#!/usr/bin/env python3
"""Self-contained validation harness for android-artifact-recovery.

Run from the Skill Tool folder (or a clean copy of it):

    python3 tools-android-artifact-recovery-tests.py
    python3 tools-android-artifact-recovery-tests.py --apk real.apk --aab real.aab \
        --build-tools-dir /path/to/build-tools/34.0.0 --apktool-jar /path/to/apktool.jar

The harness builds its own fixtures in a temporary directory, so it needs no
network and no Android SDK:

  * hand-assembled binary AXML manifests (with and without android:exported)
  * a minimal resources.arsc with a default and a locale config
  * a DEX header with a deliberately wrong Adler-32 checksum
  * ELF64 shared objects with 4 KB and 16 KB PT_LOAD alignment
  * a proto-XML manifest for the AAB decoder
  * ZIP archives with duplicate and path-traversal entries

Optional real-artifact checks (``--apk``/``--aab``) exercise the parsers against
genuine packages, and ``--build-tools-dir``/``--apktool-jar`` enable the
host-tool lane. Exit code 0 means every executed check passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "tools-android-artifact-recovery.py"

PASSED: list[str] = []
FAILED: list[str] = []


def _load_toolkit_modules():
    """Load the hyphen-prefixed runtime modules the same way the primary tool does."""
    import importlib.util

    def load(stem: str, alias: str):
        module = sys.modules.get(alias)
        if module is not None:
            return module
        path = HERE / f"{stem}.py"
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"cannot load {path.name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        spec.loader.exec_module(module)
        return module

    parsers = load("tools-ax-parsers", "ax_parsers")
    sys.modules.setdefault("tools_ax_parsers", parsers)
    formats = load("tools-ax-formats", "ax_formats")
    return formats, parsers


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def run(args: list[str], expect: int | None = 0) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, cwd=str(HERE))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    if expect is not None:
        check(f"exit {expect} for {' '.join(args[:2])}", proc.returncode == expect,
              f"got {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
    return proc.returncode, data


# --------------------------------------------------------------------------
# fixture builders
# --------------------------------------------------------------------------

AXML_STRINGS = ["versionCode", "versionName", "package", "minSdkVersion",
                "targetSdkVersion", "name", "exported", "activity", "application",
                "manifest", "uses-sdk", "android", "intent-filter", "action",
                "category", "true", "false"]


def build_axml(package: str, version_code: int, target_sdk: int,
               exported: str | None) -> bytes:
    """Assemble a structurally valid binary AndroidManifest.xml."""
    strings = AXML_STRINGS + [package, "1.0-fixture", "com.example.Fixture",
                              "android.intent.action.MAIN",
                              "android.intent.category.LAUNCHER"]
    idx = {s: i for i, s in enumerate(strings)}

    def pool() -> bytes:
        offsets, blob = [], b""
        for item in strings:
            offsets.append(len(blob))
            blob += struct.pack("<H", len(item)) + item.encode("utf-16-le") + b"\x00\x00"
        header_size = 28
        strings_at = header_size + 4 * len(strings)
        body = b"".join(struct.pack("<I", o) for o in offsets) + blob
        total = header_size + len(body)
        return (struct.pack("<HHIIIIII", 0x0001, header_size, total, len(strings), 0, 0,
                            strings_at, 0) + body)

    def resmap() -> bytes:
        ids = [0x0101021B, 0x0101021C, 0x0101000E, 0x0101020C, 0x01010270,
               0x01010003, 0x01010010]
        body = b"".join(struct.pack("<I", i) for i in ids)
        return struct.pack("<HHI", 0x0180, 8, 8 + len(body)) + body

    chunks: list[bytes] = []

    def start_tag(tag: str, attrs: list[tuple[str, int, int, int]]) -> bytes:
        payload = b""
        for name, resource_id, value_type, data in attrs:
            payload += struct.pack("<IIIHBBI", 0xFFFFFFFF, idx[name], 0xFFFFFFFF, 8, 0,
                                   value_type, data)
        ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, idx[tag], 20, 20, len(attrs), 0, 0,
                          0) + payload
        return struct.pack("<HHIII", 0x0102, 16, 16 + len(ext), 1, 0xFFFFFFFF) + ext

    def end_tag(tag: str) -> bytes:
        ext = struct.pack("<II", 0xFFFFFFFF, idx[tag])
        return struct.pack("<HHIII", 0x0103, 16, 24, 1, 0xFFFFFFFF) + ext

    def string_attr(text: str) -> tuple[int, int]:
        return (3, idx[text])

    chunks.append(struct.pack("<HHIII", 0x0100, 16, 24, 1, 0xFFFFFFFF)
                  + struct.pack("<II", 0xFFFFFFFF, idx["android"]))
    chunks.append(start_tag("manifest", [
        ("versionCode", 0x0101021B, 0x10, version_code),
        ("versionName", 0x0101021C, *string_attr("1.0-fixture")),
        ("package", 0, *string_attr(package))]))
    chunks.append(start_tag("uses-sdk", [
        ("minSdkVersion", 0x0101020C, 0x10, 21),
        ("targetSdkVersion", 0x01010270, 0x10, target_sdk)]))
    chunks.append(end_tag("uses-sdk"))
    chunks.append(start_tag("application", [
        ("name", 0x01010003, *string_attr("com.example.Fixture"))]))
    activity_attrs = [("name", 0x01010003, *string_attr("com.example.Fixture"))]
    if exported is not None:
        activity_attrs.append(("exported", 0x01010010, *string_attr(exported)))
    chunks.append(start_tag("activity", activity_attrs))
    chunks.append(start_tag("intent-filter", []))
    chunks.append(start_tag("action", [
        ("name", 0x01010003, *string_attr("android.intent.action.MAIN"))]))
    chunks.append(end_tag("action"))
    chunks.append(start_tag("category", [
        ("name", 0x01010003, *string_attr("android.intent.category.LAUNCHER"))]))
    chunks.append(end_tag("category"))
    chunks.append(end_tag("intent-filter"))
    chunks.append(end_tag("activity"))
    chunks.append(end_tag("application"))
    chunks.append(end_tag("manifest"))
    body = pool() + resmap() + b"".join(chunks)
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def build_arsc(package: str, entries: list[tuple[int, str, str]]) -> bytes:
    """Assemble a minimal resources.arsc with a default and an es-rES config."""

    def string_pool(strings: list[str]) -> bytes:
        offsets, blob = [], b""
        for item in strings:
            offsets.append(len(blob))
            blob += struct.pack("<H", len(item)) + item.encode("utf-16-le") + b"\x00\x00"
        header_size = 28
        strings_at = header_size + 4 * len(strings)
        body = b"".join(struct.pack("<I", o) for o in offsets) + blob
        return (struct.pack("<HHIIIIII", 0x0001, header_size, header_size + len(body),
                            len(strings), 0, 0, strings_at, 0) + body)

    keys = [key for _rid, key, _value in entries]
    values = [value for _rid, _key, value in entries]
    global_pool = string_pool(values)
    type_pool = string_pool(["string"])
    key_pool = string_pool(keys)

    def type_chunk(config_bytes: bytes) -> bytes:
        entry_offsets, entry_data = [], b""
        for i, (_rid, _key, value) in enumerate(entries):
            entry_offsets.append(len(entry_data))
            entry_data += struct.pack("<HHI", 8, 0, i)                       # ResTable_entry
            entry_data += struct.pack("<HBBI", 8, 0, 0x03, values.index(value))
        offsets_blob = b"".join(struct.pack("<I", o) for o in entry_offsets)
        # ResTable_type: chunk header (8) + id/flags/reserved (4) + entryCount (4)
        # + entriesStart (4) + ResTable_config
        header_size = 8 + 4 + 4 + 4 + len(config_bytes)
        entries_start = header_size + len(offsets_blob)
        total = header_size + len(offsets_blob) + len(entry_data)
        header = struct.pack("<HHIBBHII", 0x0201, header_size, total, 1, 0, len(entries),
                             entries_start, len(config_bytes))
        return header + config_bytes + offsets_blob + entry_data

    default_cfg = struct.pack("<I", 28) + b"\x00" * 24
    locale_cfg = (struct.pack("<I", 28) + b"\x00" * 4 + b"es" + b"ES" + b"\x00" * 16)
    # ResTable_typeSpec: header 16 bytes (id/flags/reserved + entryCount) + flag words
    spec = (struct.pack("<HHIBBH", 0x0202, 16, 16 + 4 * len(entries), 1, 0, 0)
            + struct.pack("<I", len(entries))
            + b"".join(struct.pack("<I", 0) for _ in entries))
    name_bytes = package.encode("utf-16-le")
    if len(name_bytes) > 254:  # ResTable_package.name is a fixed 128 UTF-16 field
        raise ValueError("fixture package name too long for a resource table")
    name_field = name_bytes + b"\x00" * (256 - len(name_bytes))
    pkg_header_size = 288
    type_pool_at = pkg_header_size
    key_pool_at = type_pool_at + len(type_pool)
    body = (type_pool + key_pool + spec + type_chunk(default_cfg)
            + type_chunk(locale_cfg))
    package_chunk = (struct.pack("<HHII", 0x0200, pkg_header_size,
                                 pkg_header_size + len(body), 0x7F)
                     + name_field
                     + struct.pack("<IIIII", type_pool_at, 0, 0, key_pool_at, 0)
                     + body)
    top = global_pool + package_chunk
    return struct.pack("<HHII", 0x0002, 12, 12 + len(top), 1) + top


def build_dex(bad_checksum: bool = False) -> bytes:
    body = bytearray(0x70 + 0x20)
    body[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", body, 32, len(body))
    struct.pack_into("<IIIIII", body, 56, 1, 0, 0, 0, 0, 0)
    body[12:32] = hashlib.sha1(bytes(body[32:])).digest()
    checksum = zlib.adler32(bytes(body[12:])) & 0xFFFFFFFF
    if bad_checksum:
        checksum ^= 0xDEADBEEF
    struct.pack_into("<I", body, 8, checksum)
    return bytes(body)


def build_elf(align: int) -> bytes:
    """ELF64 little-endian shared object with two PT_LOAD segments."""
    ehsize, phentsize = 64, 56
    phoff = ehsize
    buf = bytearray(0x400)
    buf[0:4] = b"\x7fELF"
    buf[4], buf[5], buf[6] = 2, 1, 1
    struct.pack_into("<HHI", buf, 16, 3, 183, 1)   # ET_DYN, EM_AARCH64
    struct.pack_into("<Q", buf, 24, 0)
    struct.pack_into("<Q", buf, 32, phoff)
    struct.pack_into("<Q", buf, 40, 0)
    struct.pack_into("<H", buf, 52, ehsize)
    struct.pack_into("<H", buf, 54, phentsize)
    struct.pack_into("<H", buf, 56, 2)
    for i, (offset, vaddr) in enumerate(((0x0, 0x0), (0x200, 0x200))):
        base = phoff + i * phentsize
        struct.pack_into("<I", buf, base, 1)        # PT_LOAD
        struct.pack_into("<I", buf, base + 4, 5)    # R+X
        struct.pack_into("<Q", buf, base + 8, offset)
        struct.pack_into("<Q", buf, base + 16, vaddr)
        struct.pack_into("<Q", buf, base + 24, vaddr)
        struct.pack_into("<Q", buf, base + 32, 0x100)
        struct.pack_into("<Q", buf, base + 40, 0x100)
        struct.pack_into("<Q", buf, base + 48, align)
    return bytes(buf)


def build_proto_manifest(package: str, version_code: str) -> bytes:
    """XmlNode{element=1} -> XmlElement{ns_decl=1, name=3, attr=4}."""

    def varint(n: int) -> bytes:
        out = b""
        while True:
            byte = n & 0x7F
            n >>= 7
            out += bytes([byte | (0x80 if n else 0)])
            if not n:
                return out

    def field(number: int, payload: bytes) -> bytes:
        return varint((number << 3) | 2) + varint(len(payload)) + payload

    def attribute(name: str, value: str, uri: str = "") -> bytes:
        body = b""
        if uri:
            body += field(1, uri.encode())
        return body + field(2, name.encode()) + field(3, value.encode())

    element = field(3, b"manifest")
    element += field(4, attribute("package", package))
    element += field(4, attribute("versionCode", version_code,
                                  "http://schemas.android.com/apk/res/android"))
    return field(1, element)


# --------------------------------------------------------------------------
# test groups
# --------------------------------------------------------------------------


def test_core_parsers(tmp: Path) -> None:
    print("\n[1] core parsers (pure Python)")
    F, P = _load_toolkit_modules()

    doc = P.parse_axml(build_axml("com.example.Fixture", 42, 34, None))
    check("AXML root element decoded",
          doc.manifest() is not None and doc.manifest().tag == "manifest",
          str(doc.problems))
    check("AXML package attribute",
          doc.manifest().attr_value("package") == "com.example.Fixture",
          str(doc.manifest().attr_value("package")))
    activity = [e for e in doc.all_elements() if e.tag == "activity"][0]
    check("AXML absence of android:exported is detectable", activity.attr("exported") is None)

    doc2 = P.parse_axml(build_axml("com.example.Other", 7, 31, "true"))
    activity2 = [e for e in doc2.all_elements() if e.tag == "activity"][0]
    check("AXML android:exported resolved through the resource map",
          activity2.attr_value("exported") == "true", str(activity2.attr_value("exported")))
    check("AXML change of input changes output",
          doc.manifest().attr_value("package") != doc2.manifest().attr_value("package"))

    arsc_path = tmp / "resources.arsc"
    arsc_path.write_bytes(build_arsc("com.example.Fixture",
                                     [(0x7F010000, "app_name", "Fixture App"),
                                      (0x7F010001, "greeting", "hello")]))
    table = F.parse_arsc(arsc_path)
    check("ARSC package name", table.package_name == "com.example.Fixture",
          table.package_name)
    check("ARSC entries parsed", len(table.entries) >= 2, str(len(table.entries)))
    check("ARSC config split (default + locale)", table.config_count >= 2,
          str(sorted({e.config for e in table.entries})))

    bad = F.parse_dex("classes.dex", build_dex(bad_checksum=True))
    check("DEX checksum mismatch detected", bad.checksum_ok is False)
    check("DEX problems reported", any("checksum" in p for p in bad.problems),
          str(bad.problems))
    check("DEX clean file passes checksum",
          F.parse_dex("classes.dex", build_dex()).checksum_ok is True)

    elf4k = F.parse_elf("lib4k.so", build_elf(4096))
    ready4, notes4 = elf4k.page16k_ready()
    check("ELF ABI detected as arm64-v8a", elf4k.abi == "arm64-v8a", elf4k.abi)
    check("ELF 4 KB alignment flagged as not 16 KB ready", ready4 is False, str(notes4))
    check("ELF 16 KB alignment passes",
          F.parse_elf("lib16k.so", build_elf(16384)).page16k_ready()[0] is True)

    manifest = F.decode_aab_manifest(build_proto_manifest("com.example.Bundle", "99"))
    check("AAB proto manifest package", manifest.package == "com.example.Bundle",
          manifest.package)
    check("AAB proto manifest versionCode", manifest.version_code == "99",
          manifest.version_code)
    check("AAB proto manifest has no problems", not manifest.problems,
          str(manifest.problems))


def test_zip_layout(tmp: Path) -> None:
    print("\n[2] ZIP layout, duplicate and traversal detection")
    _F, P = _load_toolkit_modules()
    good = tmp / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("AndroidManifest.xml", "x")
        zf.writestr("classes.dex", "x" * 100)
    layout = P.parse_zip_layout(good)
    check("ZIP entries parsed", len(layout.entries) == 2, str(len(layout.entries)))
    check("ZIP no problems on a clean archive", not layout.problems, str(layout.problems))

    odd = tmp / "odd.apk"
    with zipfile.ZipFile(odd, "w") as zf:
        zf.writestr("AndroidManifest.xml", "x")
        zf.writestr("classes.dex", "x")
        zf.writestr("../escape.txt", "no")
    with zipfile.ZipFile(odd, "a") as zf:
        zf.writestr("AndroidManifest.xml", "dup")
    layout2 = P.parse_zip_layout(odd)
    check("duplicate names detected", "AndroidManifest.xml" in layout2.duplicates())
    check("traversal name detected", "../escape.txt" in layout2.traversal_names())

    truncated = tmp / "truncated.apk"
    truncated.write_bytes(good.read_bytes()[:60])
    check("truncated archive flagged", bool(P.parse_zip_layout(truncated).problems))


def test_cli(tmp: Path) -> None:
    print("\n[3] CLI contract: help, capabilities, errors, approvals")
    _code, caps = run(["capabilities"])
    data = caps.get("data", {})
    check("capabilities lists commands", len(data.get("commands", [])) >= 20,
          str(len(data.get("commands", []))))
    check("capabilities reports 24 catalog categories",
          data.get("catalog", {}).get("categories") == 24,
          str(data.get("catalog", {}).get("categories")))
    check("capabilities reports 324 catalog skills",
          data.get("catalog", {}).get("skills") == 324)
    check("capabilities marks every asset present",
          all(a["present"] for a in data.get("assets", [])),
          json.dumps(data.get("assets")))

    proc = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True,
                          text=True, cwd=str(HERE))
    check("--help exits 0 with usage", proc.returncode == 0 and "usage:" in proc.stdout)

    _code, err = run(["badging", str(tmp / "missing.apk")], expect=2)
    check("missing input returns a structured error",
          "input not found" in json.dumps(err.get("errors", [])))

    not_zip = tmp / "not-a-zip.apk"
    not_zip.write_text("plain text")
    _code, err2 = run(["badging", str(not_zip)], expect=2)
    check("non-APK input explains the failure and reports the detected kind",
          "is not an APK" in json.dumps(err2.get("errors", []))
          and "detected kind" in json.dumps(err2.get("errors", [])))

    _code, err3 = run(["sign", str(not_zip), "--output", str(tmp / "o.apk"),
                       "--keystore", str(tmp / "k.p12")], expect=4)
    check("sign requires approval", "approval required" in json.dumps(err3))
    _code, err4 = run(["rebuild", str(not_zip), "--keystore", str(tmp / "k.p12")], expect=4)
    check("rebuild requires approval", "approval required" in json.dumps(err4))
    _code, err5 = run(["preserve", str(not_zip), "--outdir", str(tmp / "p")], expect=4)
    check("preserve requires approval", "approval required" in json.dumps(err5))

    proc = subprocess.run([sys.executable, str(TOOL), "frobnicate"], capture_output=True,
                          text=True, cwd=str(HERE))
    check("unknown command exits 1 (usage), not 2 (invalid input)",
          proc.returncode == 1 and "invalid choice" in proc.stderr,
          f"got {proc.returncode}")
    proc = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True,
                          cwd=str(HERE))
    check("no command exits 1 and prints usage",
          proc.returncode == 1 and "usage:" in (proc.stdout + proc.stderr))
    proc = subprocess.run([sys.executable, str(TOOL), "entry", str(tmp / "x.apk"),
                           "--limit", "-5"], capture_output=True, text=True, cwd=str(HERE))
    check("an invalid option value exits 1", proc.returncode == 1, f"got {proc.returncode}")

    output = tmp / "cap.json"
    run(["capabilities", "--out", str(output)])
    check("--out writes a file",
          output.is_file() and json.loads(output.read_text())["ok"] is True)


def test_artifact_commands(tmp: Path, apk: Path | None, aab: Path | None) -> None:
    print("\n[4] artifact commands on generated fixtures")
    _F, P = _load_toolkit_modules()

    fixture = tmp / "fixture.apk"
    with zipfile.ZipFile(fixture, "w") as zf:
        zf.writestr("AndroidManifest.xml", build_axml("com.example.Cli", 5, 31, None))
        zf.writestr("classes.dex", build_dex())
        zf.writestr("res/values/strings.xml", "<resources/>")

    _code, ident = run(["identify", str(fixture)])
    check("identify recognises a synthetic APK",
          ident["data"]["artifacts"][0]["kind"] == "apk",
          ident["data"]["artifacts"][0]["kind"])

    _code, badging = run(["badging", str(fixture)])
    facts = badging.get("data", {}).get("facts", {})
    check("badging reads package", facts.get("package") == "com.example.Cli",
          str(facts.get("package")))
    check("badging reads targetSdk", facts.get("target_sdk") == 31,
          str(facts.get("target_sdk")))
    check("badging finds the launcher activity",
          facts.get("launchable_activities") == ["com.example.Fixture"],
          str(facts.get("launchable_activities")))

    _code, audit = run(["exported-audit", str(fixture)])
    rows = audit.get("data", {}).get("components", [])
    check("exported-audit flags a missing android:exported at targetSdk 31",
          any(r["status"] == "missing-explicit-exported" for r in rows), json.dumps(rows))
    check("exported-audit raises a critical finding",
          any(f["severity"] == "critical" for f in audit.get("findings", [])))

    _code, compat = run(["compat-scan", str(fixture)])
    ids = {f["rule_id"] for f in compat.get("findings", [])}
    check("compat-scan reports the exported blocker", "COMPAT-EXPORTED" in ids, str(ids))

    _code, archive = run(["zip-health", str(fixture)])
    check("zip-health returns an entry count", archive["data"]["entry_count"] == 3,
          str(archive["data"]["entry_count"]))

    _code, dex_result = run(["dex", str(fixture)])
    check("dex reports one file", dex_result["data"]["totals"]["files"] == 1)

    _code, page = run(["page16k", str(fixture)])
    check("page16k reports NOT APPLICABLE without native libraries",
          page["data"]["verdict"] == "NOT APPLICABLE", page["data"]["verdict"])

    _code, triage_result = run(["triage", "--text", "INSTALL_FAILED_NO_MATCHING_ABIS: x"])
    check("triage routes an ABI failure",
          triage_result["data"]["earliest_stage"] == "native",
          triage_result["data"]["earliest_stage"])

    _code, report = run(["report", str(fixture), "--outdir", str(tmp / "rep")])
    markdown = Path(report["data"]["report_markdown"])
    check("report writes markdown",
          markdown.is_file() and "## Findings" in markdown.read_text())
    check("report states what was not tested",
          "untested" in markdown.read_text().lower(), "no limits section")

    if apk:
        print("\n[5] real artifact cross-checks")
        _code, real = run(["badging", str(apk)])
        real_facts = real.get("data", {}).get("facts", {})
        check("real APK yields a package name", bool(real_facts.get("package")),
              str(real_facts)[:200])
        check("real APK is detected as apk", P.detect_artifact(apk)["kind"] == "apk")
        _code, res = run(["arsc", str(apk)])
        check("real APK resource table parsed",
              res.get("data", {}).get("summary", {}).get("entry_count", 0) > 0)
        _code, abi = run(["abi-matrix", str(apk)])
        check("real APK ABI matrix computed", "abis_present" in abi.get("data", {}),
              json.dumps(abi.get("data", {}))[:200])
    if aab:
        _code, bundle = run(["aab-inspect", str(aab)])
        check("AAB modules discovered",
              len(bundle.get("data", {}).get("structure", {}).get("modules", {})) >= 2,
              json.dumps(bundle.get("data", {}))[:200])


def test_preserve_and_batch(tmp: Path) -> None:
    print("\n[6] preserve custody records and batch status honesty")
    source = tmp / "custody.apk"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("AndroidManifest.xml", build_axml("com.example.Custody", 3, 33, "true"))
        zf.writestr("classes.dex", build_dex())
    dest = tmp / "evidence"
    _code, first = run(["preserve", str(source), "--outdir", str(dest),
                        "--authorization", "test authorization", "--confirm"])
    items = first.get("data", {}).get("items", [])
    check("preserve writes a custody item", len(items) == 1, json.dumps(items)[:200])
    check("preserve records matching source and copy hashes",
          items and items[0]["sha256"] == items[0]["source_sha256"])
    check("preserve copies are read-only", items and items[0]["read_only"] is True)
    record_path = Path(first["data"]["record"])
    check("preservation record is on disk",
          record_path.is_file() and "test authorization" in record_path.read_text())
    check("preserved copy is mode 0440",
          stat.S_IMODE(Path(items[0]["copy"]).stat().st_mode) == 0o440,
          oct(stat.S_IMODE(Path(items[0]["copy"]).stat().st_mode)))
    _code, second = run(["preserve", str(source), "--outdir", str(dest),
                         "--authorization", "test authorization", "--confirm"])
    second_copy = second.get("data", {}).get("items", [{}])[0].get("copy")
    check("a repeated preserve does not silently reuse a read-only copy",
          bool(second_copy) and second_copy != items[0]["copy"], str(second_copy))

    batch_dir = tmp / "batch"
    batch_dir.mkdir()
    shutil.copy(source, batch_dir / "good.apk")
    (batch_dir / "junk.apk").write_text("not an android package")
    _code, batch = run(["batch", str(batch_dir), "--outdir", str(tmp / "batch-out")])
    rows = {r["file"]: r for r in batch.get("data", {}).get("items", [])}
    check("batch marks a real package ok", rows.get("good.apk", {}).get("status") == "ok",
          json.dumps(rows.get("good.apk", {})))
    check("batch marks junk skipped, not ok",
          rows.get("junk.apk", {}).get("status") == "skipped",
          json.dumps(rows.get("junk.apk", {})))
    check("batch reports analyzed/skipped counts",
          batch["data"]["analyzed"] == 1 and batch["data"]["skipped"] == 1,
          json.dumps(batch["data"])[:200])
    check("batch writes both index files",
          Path(batch["data"]["csv"]).is_file() and Path(batch["data"]["json"]).is_file())


def test_mutation(tmp: Path) -> None:
    print("\n[7] mutation: changing the input changes the result")
    first = tmp / "m1.apk"
    second = tmp / "m2.apk"
    with zipfile.ZipFile(first, "w") as zf:
        zf.writestr("AndroidManifest.xml", build_axml("com.example.Alpha", 1, 21, "false"))
        zf.writestr("classes.dex", build_dex())
    with zipfile.ZipFile(second, "w") as zf:
        zf.writestr("AndroidManifest.xml", build_axml("com.example.Beta", 2, 31, None))
        zf.writestr("classes.dex", build_dex())
    _code, ra = run(["badging", str(first)])
    _code, rb = run(["badging", str(second)])
    facts_a, facts_b = ra["data"]["facts"], rb["data"]["facts"]
    check("package changes with input", facts_a["package"] != facts_b["package"])
    check("targetSdk changes with input", facts_a["target_sdk"] != facts_b["target_sdk"])
    _code, audit_a = run(["exported-audit", str(first)])
    _code, audit_b = run(["exported-audit", str(second)])
    check("explicit exported produces no critical finding",
          not any(f["severity"] == "critical" for f in audit_a["findings"]),
          json.dumps(audit_a["findings"])[:300])
    check("missing exported produces a critical finding",
          any(f["severity"] == "critical" for f in audit_b["findings"]))
    check("findings differ between the two inputs",
          [f["rule_id"] for f in audit_a["findings"]]
          != [f["rule_id"] for f in audit_b["findings"]])


def test_host_lane(tmp: Path, apk: Path | None, build_tools: str | None,
                   apktool_jar: str | None) -> None:
    print("\n[8] host-tool lane (optional)")
    args: list[str] = []
    if build_tools:
        args += ["--build-tools-dir", build_tools]
    if apktool_jar:
        args += ["--apktool-jar", apktool_jar]
    _code, status = run(args + ["self-test"])
    statuses = {c["id"]: c["status"] for c in status.get("data", {}).get("checks", [])}
    print(f"  self-test statuses: {statuses}")
    check("self-test returns a status for every probe", len(statuses) >= 8)
    if not (build_tools and apk):
        print("  SKIP  host-tool execution (pass --build-tools-dir and --apk to enable)")
        return
    _code, decoded = run(args + ["apktool", "decode", str(apk),
                                 "--outdir", str(tmp / "decoded"), "--workdir", str(tmp)])
    if decoded.get("ok"):
        check("apktool decode produced a manifest",
              (tmp / "decoded" / "AndroidManifest.xml").is_file())
        check("apktool state file written", (tmp / "apktool-state.json").is_file())
        _code, built = run(args + ["apktool", "build", "--srcdir", str(tmp / "decoded"),
                                   "--output", str(tmp / "rebuilt.apk"), "--workdir",
                                   str(tmp)])
        check("apktool rebuild produced an APK",
              (tmp / "rebuilt.apk").is_file()
              and built.get("data", {}).get("returncode") == 0)
    else:
        print("  SKIP  apktool execution (jar or java unavailable)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apk", help="real APK used for cross-checks")
    parser.add_argument("--aab", help="real AAB used for cross-checks")
    parser.add_argument("--build-tools-dir", help="Android build-tools directory")
    parser.add_argument("--apktool-jar", help="apktool JAR path")
    parser.add_argument("--keep", action="store_true", help="keep temporary fixtures")
    ns = parser.parse_args()

    apk = Path(ns.apk) if ns.apk else None
    aab = Path(ns.aab) if ns.aab else None
    for label, path in (("apk", apk), ("aab", aab)):
        if path and not path.is_file():
            print(f"{label} not found: {path}")
            return 2

    tmpdir = Path(tempfile.mkdtemp(prefix="aar-tests-"))
    try:
        test_core_parsers(tmpdir)
        test_zip_layout(tmpdir)
        test_cli(tmpdir)
        test_artifact_commands(tmpdir, apk, aab)
        test_preserve_and_batch(tmpdir)
        test_mutation(tmpdir)
        test_host_lane(tmpdir, apk, ns.build_tools_dir, ns.apktool_jar)
    finally:
        if not ns.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            print(f"\nfixtures kept in {tmpdir}")

    print("\n" + "=" * 68)
    print(f"passed: {len(PASSED)}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  FAILED {failure}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
