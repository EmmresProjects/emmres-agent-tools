"""Pure-standard-library parsers for Android package artifacts.

This module backs the ``android-artifact-recovery`` Skill Tool. Nothing here
shells out or touches the network: every parser reads bytes already supplied by
the caller. That keeps the analysis lane deterministic, offline-safe and usable
on hosts where ``aapt``/``apktool`` are absent.

Parsers provided
----------------
* ``detect_artifact``  -- magic + ZIP + container sniffing
* ``parse_zip_layout`` -- central directory, alignment, duplicates, CRC health
* ``parse_axml``       -- binary AndroidManifest.xml (AXML)
* ``format_res_value`` -- render a ``Res_value`` into JSON-friendly data

All parsers are defensive: malformed input raises ``ParseError`` with an offset
and a human-readable reason instead of crashing the caller.
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

RES_STRING_POOL_TYPE = 0x0001
RES_TABLE_TYPE = 0x0002
RES_XML_TYPE = 0x0003
RES_XML_START_NAMESPACE_TYPE = 0x0100
RES_XML_END_NAMESPACE_TYPE = 0x0101
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_END_ELEMENT_TYPE = 0x0103
RES_XML_CDATA_TYPE = 0x0104
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_TABLE_PACKAGE_TYPE = 0x0200
RES_TABLE_TYPE_TYPE = 0x0201
RES_TABLE_TYPE_SPEC_TYPE = 0x0202

TYPE_REFERENCE = 0x01
TYPE_ATTRIBUTE = 0x02
TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_DIMENSION = 0x05
TYPE_FRACTION = 0x06
TYPE_DYNAMIC_REFERENCE = 0x07
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12
TYPE_INT_COLOR_ARGB8 = 0x1C
TYPE_INT_COLOR_RGB8 = 0x1D
TYPE_INT_COLOR_ARGB4 = 0x1E
TYPE_INT_COLOR_RGB4 = 0x1F

# android: attribute IDs used when the AXML string pool has no name for an
# attribute (obfuscated or stripped resource maps). Only IDs that matter for
# compatibility work are listed; unknown IDs fall back to their hex form.
ANDROID_ATTR_IDS: dict[int, str] = {
    0x01010003: "name",
    0x01010010: "exported",
    0x01010024: "debuggable",
    0x01010026: "enabled",
    0x01010027: "process",
    0x01010028: "taskAffinity",
    0x01010029: "multiprocess",
    0x0101002A: "finishOnTaskLaunch",
    0x0101002B: "theme",
    0x0101002D: "launchMode",
    0x0101002E: "screenOrientation",
    0x0101002F: "configChanges",
    0x01010030: "permission",
    0x01010031: "handleProfiling",
    0x01010032: "functionalTest",
    0x01010035: "grantUriPermissions",
    0x01010036: "authorities",
    0x01010037: "resource",
    0x01010038: "priority",
    0x01010039: "targetActivity",
    0x01010040: "allowTaskReparenting",
    0x01010041: "alwaysRetainTaskState",
    0x01010042: "clearTaskOnLaunch",
    0x01010043: "stateNotNeeded",
    0x01010044: "excludeFromRecents",
    0x01010045: "noHistory",
    0x01010046: "value",
    0x01010049: "mimeType",
    0x0101004A: "scheme",
    0x0101004B: "host",
    0x0101004C: "port",
    0x0101004D: "path",
    0x0101004E: "pathPrefix",
    0x0101004F: "pathPattern",
    0x01010050: "permission",
    0x01010051: "readPermission",
    0x01010052: "writePermission",
    0x01010053: "protectionLevel",
    0x01010054: "permissionGroup",
    0x01010055: "sharedUserId",
    0x01010056: "hasCode",
    0x01010057: "allowBackup",
    0x01010058: "glEsVersion",
    0x01010059: "required",
    0x0101005A: "minSdkVersion",
    0x0101005B: "maxSdkVersion",
    0x0101005C: "requestLegacyExternalStorage",
    0x0101005D: "usesCleartextTraffic",
    0x0101005E: "networkSecurityConfig",
    0x0101005F: "requiredSplitTypes",
    0x01010060: "splitTypes",
    0x01010061: "versionCode",
    0x01010062: "versionName",
    0x01010063: "sharedUserLabel",
    0x01010064: "targetSdkVersion",
    0x01010065: "largeHeap",
    0x01010066: "icon",
    0x01010067: "label",
    0x0101006A: "resizeableActivity",
    0x0101006B: "supportsRtl",
    0x0101006C: "isSplitRequired",
    0x0101006D: "extractNativeLibs",
    0x0101006E: "usesNonSdkApi",
    0x01010070: "foregroundServiceType",
    0x01010071: "requestRawExternalStorageAccess",
    0x01010072: "enableOnBackInvokedCallback",
    0x01010092: "isolatedSplits",
    0x01010095: "exported",
    0x01010096: "directBootAware",
    0x01010097: "compileSdkVersion",
    0x01010098: "compileSdkVersionCodename",
    0x0101009A: "appComponentFactory",
    0x0101009B: "minSdkVersion",
    0x0101009C: "dataExtractionRules",
    0x0101009D: "fullBackupContent",
    0x0101009E: "localeConfig",
    0x010100AA: "maxAspectRatio",
    0x010100AB: "minAspectRatio",
    0x010100B3: "roundIcon",
    0x010100B4: "banner",
    0x010100B5: "use32bitAbi",
    0x01010572: "usesNonSdkApi",
    0x01010577: "attributionTagsProvider",
    0x0101057A: "zygotePreloadName",
    0x0101057B: "appCategory",
    0x0101057C: "isFeatureSplit",
    0x01010580: "configForSplit",
    0x01010581: "allowClearUserDataOnFailedRestore",
    0x01010582: "allowAudioPlaybackCapture",
    0x01010584: "enableOnBackInvokedCallback",
}

KNOWN_PERMISSION_PROTECTION = {
    "normal", "dangerous", "signature", "signature|privileged", "privileged",
    "appop", "pre23", "installer", "verifier", "preinstalled", "development",
    "instant", "ephemeral", "retailDemo", "system",
}


class ParseError(Exception):
    """Raised for malformed or unsupported binary input."""

    def __init__(self, message: str, offset: int | None = None):
        self.offset = offset
        self.message = message
        suffix = f" (offset {offset:#x})" if offset is not None else ""
        super().__init__(f"{message}{suffix}")


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------


def _u8(buf: bytes, off: int) -> int:
    if off < 0 or off + 1 > len(buf):
        raise ParseError("unexpected end of buffer", off)
    return buf[off]


def _u16(buf: bytes, off: int) -> int:
    if off < 0 or off + 2 > len(buf):
        raise ParseError("unexpected end of buffer", off)
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    if off < 0 or off + 4 > len(buf):
        raise ParseError("unexpected end of buffer", off)
    return struct.unpack_from("<I", buf, off)[0]


def _chunk_header(buf: bytes, off: int) -> tuple[int, int, int]:
    ctype = _u16(buf, off)
    hsize = _u16(buf, off + 2)
    size = _u32(buf, off + 4)
    if hsize < 8:
        raise ParseError(f"chunk header size {hsize} is smaller than 8", off)
    if size < hsize:
        raise ParseError(f"chunk size {size} smaller than header size {hsize}", off)
    return ctype, hsize, size


def _decode_pool_strings(buf: bytes, off: int) -> tuple[list[str], int]:
    """Decode a ResStringPool chunk into a list of Python strings."""
    ctype, hsize, size = _chunk_header(buf, off)
    if ctype != RES_STRING_POOL_TYPE:
        raise ParseError("expected string pool chunk", off)
    end = off + size
    string_count = _u32(buf, off + 8)
    _style_count = _u32(buf, off + 12)
    flags = _u32(buf, off + 16)
    strings_start = _u32(buf, off + 20)
    utf8 = bool(flags & 0x100)
    offsets_at = off + hsize
    strings_at = off + strings_start
    strings: list[str] = []
    for i in range(string_count):
        so = _u32(buf, offsets_at + i * 4)
        pos = strings_at + so
        try:
            if utf8:
                n = _u8(buf, pos)
                pos += 2 if (n & 0x80) else 1
                n = _u8(buf, pos)
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | _u8(buf, pos + 1)
                    pos += 2
                else:
                    pos += 1
                strings.append(buf[pos:pos + n].decode("utf-8", errors="replace"))
            else:
                n = _u16(buf, pos)
                pos += 2
                if n & 0x8000:
                    n = ((n & 0x7FFF) << 16) | _u16(buf, pos)
                    pos += 2
                strings.append(buf[pos:pos + n * 2].decode("utf-16-le", errors="replace"))
        except (IndexError, struct.error, ParseError):
            strings.append("")
    return strings, end


def format_res_value(vtype: int, data: int, strings: list[str]) -> Any:
    """Render a ``Res_value`` tuple into a JSON-friendly Python value."""
    if vtype == TYPE_STRING:
        return strings[data] if 0 <= data < len(strings) else f"@string/0x{data:08x}"
    if vtype == TYPE_INT_BOOLEAN:
        return "true" if data else "false"
    if vtype == TYPE_INT_DEC:
        return data if data < 0x80000000 else data - 0x100000000
    if vtype == TYPE_INT_HEX:
        return f"0x{data:08x}"
    if vtype == TYPE_FLOAT:
        return struct.unpack("<f", struct.pack("<I", data))[0]
    if vtype in (TYPE_REFERENCE, TYPE_DYNAMIC_REFERENCE):
        return f"@0x{data:08x}"
    if vtype == TYPE_ATTRIBUTE:
        return f"?0x{data:08x}"
    if vtype == TYPE_DIMENSION:
        return f"{data:#010x}(dimension)"
    if vtype == TYPE_FRACTION:
        return f"{data:#010x}(fraction)"
    if vtype in (TYPE_INT_COLOR_ARGB8, TYPE_INT_COLOR_RGB8,
                 TYPE_INT_COLOR_ARGB4, TYPE_INT_COLOR_RGB4):
        return f"#{data:08x}"
    return data


# --------------------------------------------------------------------------
# artifact sniffing
# --------------------------------------------------------------------------

APK_SIGNATURE_BLOCK_MAGIC = b"APK Sig Block 42"


def detect_artifact(path: Path) -> dict[str, Any]:
    """Classify a file using magic bytes plus container structure."""
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": 0,
        "kind": "missing",
        "confidence": "none",
        "extension": path.suffix.lower(),
        "magic": "",
        "notes": [],
    }
    if not path.is_file():
        return info
    size = path.stat().st_size
    info["size_bytes"] = size
    with path.open("rb") as fh:
        head = fh.read(65536)
        fh.seek(max(0, size - 65536))
        tail = fh.read()
    info["magic"] = head[:8].hex()
    if not head:
        info.update(kind="empty", confidence="certain", notes=["zero-byte file"])
        return info
    if head.startswith(b"\x7fELF"):
        kind = "elf"
        info["notes"].append(f"ELF class={head[4] if len(head) > 5 else 0} "
                             f"data={head[5] if len(head) > 5 else 0}")
    elif head.startswith(b"dex\n"):
        kind = "dex"
        info["notes"].append(f"dex magic={head[:8]!r}")
    elif head.startswith(b"\xca\xfe\xba\xbe") or head.startswith(b"\xcf\xfa\xed\xfe"):
        kind = "java-class"
    elif head.startswith(b"%PDF"):
        kind = "pdf"
    elif head.startswith(b"!<arch>"):
        kind = "android-static-lib" if path.suffix == ".a" else "ar-archive"
    elif head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        kind, notes = _classify_zip(path, head, tail)
        info["notes"].extend(notes)
    else:
        kind = _sniff_media(head, path.suffix.lower())
    info["kind"] = kind
    if info["confidence"] == "none":
        info["confidence"] = "certain" if kind != "unknown-binary" else "low"
    ext_expected = {
        ".apk": {"apk"}, ".aab": {"aab"}, ".apks": {"apks"}, ".xapk": {"xapk"},
        ".apkm": {"apkm"}, ".jar": {"jar"}, ".dex": {"dex"}, ".obb": {"obb-zip", "obb-raw"},
        ".so": {"elf"}, ".zip": {"zip", "jar", "obb-zip", "arsc-container"},
        ".pdf": {"pdf"}, ".a": {"android-static-lib"},
    }.get(path.suffix.lower())
    if ext_expected and kind not in ext_expected:
        info["notes"].append(
            f"extension/extension-like mismatch: suffix {path.suffix!r} suggests "
            f"{sorted(ext_expected)}")
        info["confidence"] = "medium"
    return info


def _sniff_media(head: bytes, suffix: str) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "mp3"
    if head.startswith(b"fLaC"):
        return "flac"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\xabKTX 20"):
        return "ktx2"
    if head.startswith(b"KTX 11"):
        return "ktx"
    if head.startswith(b"DDS "):
        return "dds"
    if head[:4] in (b"\x03\x00\x08\x00",):
        return "binary-xml"
    stripped = head.lstrip()
    if stripped[:5] == b"<?xml" or stripped[:5] == b"<mani":
        return "xml-text"
    printable = sum(1 for b in head[:2048] if 9 <= b <= 126) / max(1, len(head[:2048]))
    if printable > 0.9:
        return "text"
    return "unknown-binary"


def _classify_zip(path: Path, head: bytes, tail: bytes) -> tuple[str, list[str]]:
    notes: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            bad = zf.testzip()
            if bad:
                notes.append(f"corrupt CRC on entry {bad!r}")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        notes.append(f"unreadable ZIP container: {exc}")
        return "malformed-zip", notes
    name_set = set(names)
    lowered = {n.lower() for n in names}
    has_dex = any(n.startswith("classes") and n.endswith(".dex") for n in names)
    if "AndroidManifest.xml" in name_set and has_dex:
        return "apk", notes
    if "AndroidManifest.xml" in name_set and not has_dex:
        notes.append("manifest present without classes.dex; possible resource-only or "
                     "damaged APK")
        return "apk", notes
    if "BundleConfig.pb" in name_set or (
            any(n.startswith("base/manifest/") for n in names) and "base/resources.pb" in name_set):
        return "aab", notes
    if "manifest.json" in name_set and (
            any(n.endswith(".apk") for n in names) or "info.json" in name_set):
        return "xapk", notes
    if "info.json" in name_set and any(n.endswith(".apk") for n in names):
        return "apkm", notes
    if "toc.pb" in name_set or any(n.startswith("splits/") and n.endswith(".apk")
                                   for n in names):
        return "apks", notes
    if "META-INF/MANIFEST.MF" in name_set or "meta-inf/manifest.mf" in lowered:
        if any(n.endswith(".class") for n in names):
            return "jar", notes
        if any(n.startswith("res/") for n in names):
            return "android-library-archive", notes
        return "jar", notes
    if any(n.startswith("res/") for n in names) or "resources.arsc" in name_set:
        return "arsc-container", notes
    if any(n.startswith("assets/") for n in names) or any(
            n.lower().endswith((".obb", ".pak", ".unity3d")) for n in names):
        return "obb-zip", notes
    if path.suffix.lower() == ".obb":
        return "obb-zip", notes
    return "zip", notes


# --------------------------------------------------------------------------
# ZIP layout / health
# --------------------------------------------------------------------------


@dataclass
class ZipEntryInfo:
    name: str
    method: int
    compressed_size: int
    file_size: int
    crc: int
    local_header_offset: int
    data_offset: int
    flags: int
    date_time: tuple = ()
    extra_len: int = 0

    @property
    def alignment_4(self) -> int:
        return self.data_offset % 4

    @property
    def is_stored(self) -> bool:
        return self.method == 0


@dataclass
class ZipLayout:
    path: Path | None
    entries: list[ZipEntryInfo] = field(default_factory=list)
    comment_len: int = 0
    central_directory_offset: int = 0
    central_directory_size: int = 0
    eocd_offset: int = 0
    zip64: bool = False
    sign_block_present: bool = False
    sign_block_offset: int = -1
    trailing_bytes: int = 0
    problems: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def duplicates(self) -> dict[str, int]:
        seen: dict[str, int] = {}
        for e in self.entries:
            seen[e.name] = seen.get(e.name, 0) + 1
        return {k: v for k, v in seen.items() if v > 1}

    def traversal_names(self) -> list[str]:
        out = []
        for e in self.entries:
            n = e.name.replace("\\", "/")
            if n.startswith("/") or ".." in n.split("/") or (len(n) > 1 and n[1] == ":"):
                out.append(e.name)
        return out

    def native_libs(self) -> list[ZipEntryInfo]:
        return [e for e in self.entries if e.name.startswith("lib/") and e.name.endswith(".so")]

    def misaligned_native_libs(self, page: int = 16384) -> list[ZipEntryInfo]:
        return [e for e in self.native_libs() if e.data_offset % page != 0]

    def by_name(self, name: str) -> ZipEntryInfo | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None


def parse_zip_layout(path: Path) -> ZipLayout:
    """Parse the central directory directly so data offsets are usable.

    ``zipfile`` does not expose data offsets, and 16 KB alignment work depends on
    them, so the EOCD and central directory are walked by hand.
    """
    data = path.read_bytes()
    layout = ZipLayout(path=path)
    if len(data) < 22:
        layout.problems.append("file smaller than an end-of-central-directory record")
        return layout
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        layout.problems.append(
            "no end-of-central-directory signature found (truncated or not a ZIP?)")
        return layout
    layout.eocd_offset = eocd
    comment_len = struct.unpack_from("<H", data, eocd + 20)[0]
    layout.comment_len = comment_len
    expected_end = eocd + 22 + comment_len
    if expected_end < len(data):
        layout.trailing_bytes = len(data) - expected_end
        layout.problems.append(
            f"{layout.trailing_bytes} trailing bytes after the ZIP end record "
            "(possible appended payload or damaged rewrite)")
    entries_total = struct.unpack_from("<H", data, eocd + 10)[0]
    cd_size, cd_offset = struct.unpack_from("<II", data, eocd + 12)
    if entries_total == 0xFFFF or cd_offset == 0xFFFFFFFF:
        layout.zip64 = True
        z64 = data.rfind(b"PK\x06\x06", 0, eocd)
        if z64 >= 0:
            entries_total = struct.unpack_from("<Q", data, z64 + 32)[0]
            cd_size, cd_offset = struct.unpack_from("<QQ", data, z64 + 40)
        else:
            layout.problems.append("ZIP64 marker present but ZIP64 end record missing")
    layout.central_directory_offset = cd_offset
    layout.central_directory_size = cd_size
    if cd_offset + cd_size > len(data):
        layout.problems.append("central directory extends past end of file (truncated archive)")
        return layout
    magic_at = data.find(APK_SIGNATURE_BLOCK_MAGIC, max(0, cd_offset - 65536), cd_offset)
    if magic_at >= 0:
        layout.sign_block_present = True
        layout.sign_block_offset = magic_at
    pos = cd_offset
    for _ in range(entries_total):
        if data[pos:pos + 4] != b"PK\x01\x02":
            layout.problems.append(f"bad central directory entry signature at {pos:#x}")
            break
        (_sig, _vmade, _vneed, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen, clen, _disk, _iattr, _eattr, lho) = struct.unpack_from(
            "<IHHHHHHIIIHHHHHII", data, pos)
        name = data[pos + 46:pos + 46 + nlen].decode("utf-8", "replace")
        extra = data[pos + 46 + nlen:pos + 46 + nlen + elen]
        if csize == 0xFFFFFFFF or usize == 0xFFFFFFFF or lho == 0xFFFFFFFF:
            lho, usize64, csize64 = _zip64_extra(extra, lho, csize, usize)
            if usize64 is not None:
                usize = usize64
            if csize64 is not None:
                csize = csize64
        if data[lho:lho + 4] != b"PK\x03\x04":
            layout.problems.append(f"local header missing for {name!r} (offset {lho:#x})")
            data_offset = lho + 30
        else:
            lnlen, lelen = struct.unpack_from("<HH", data, lho + 26)
            data_offset = lho + 30 + lnlen + lelen
        layout.entries.append(ZipEntryInfo(
            name=name, method=method, compressed_size=csize, file_size=usize, crc=crc,
            local_header_offset=lho, data_offset=data_offset, flags=flags,
            date_time=(mdate, mtime), extra_len=elen))
        pos += 46 + nlen + elen + clen
    if len(layout.entries) != entries_total:
        layout.problems.append(
            f"central directory declared {entries_total} entries but "
            f"{len(layout.entries)} parsed")
    dups = layout.duplicates()
    if dups:
        layout.problems.append(f"duplicate entry names: {sorted(dups)}")
    traversal = layout.traversal_names()
    if traversal:
        layout.problems.append(f"path traversal names present: {traversal[:5]}")
    return layout


def _zip64_extra(extra: bytes, lho: int, csize: int, usize: int):
    i = 0
    lc = lu = None
    while i + 4 <= len(extra):
        hid, hlen = struct.unpack_from("<HH", extra, i)
        body = extra[i + 4:i + 4 + hlen]
        if hid == 0x0001:
            j = 0
            if usize == 0xFFFFFFFF and j + 8 <= len(body):
                lu = struct.unpack_from("<Q", body, j)[0]
                j += 8
            if csize == 0xFFFFFFFF and j + 8 <= len(body):
                lc = struct.unpack_from("<Q", body, j)[0]
                j += 8
            if lho == 0xFFFFFFFF and j + 8 <= len(body):
                lho = struct.unpack_from("<Q", body, j)[0]
                j += 8
            break
        i += 4 + hlen
    return lho, lu, lc


# --------------------------------------------------------------------------
# AXML (binary AndroidManifest.xml)
# --------------------------------------------------------------------------


@dataclass
class AxmlAttribute:
    namespace: str
    name: str
    resource_id: int
    raw_value: Any
    typed_value: Any
    value_type: int
    data: int


@dataclass
class AxmlElement:
    tag: str
    attributes: list[AxmlAttribute] = field(default_factory=list)
    children: list["AxmlElement"] = field(default_factory=list)
    parent: "AxmlElement | None" = None
    depth: int = 0
    line: int = 0

    def attr(self, name: str) -> AxmlAttribute | None:
        for a in self.attributes:
            if a.name == name:
                return a
        return None

    def attr_value(self, name: str) -> Any:
        a = self.attr(name)
        if a is None:
            return None
        return a.raw_value if a.raw_value not in (None, "") else a.typed_value

    def iter(self) -> Iterator["AxmlElement"]:
        yield self
        for c in self.children:
            yield from c.iter()

    def find_all(self, tag: str) -> list["AxmlElement"]:
        return [e for e in self.iter() if e.tag == tag]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "attributes": {
                (a.name or f"0x{a.resource_id:08x}"): {
                    "value": a.raw_value if a.raw_value not in (None, "") else a.typed_value,
                    "resource_id": f"0x{a.resource_id:08x}" if a.resource_id else None,
                }
                for a in self.attributes
            },
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class AxmlDocument:
    elements: list[AxmlElement] = field(default_factory=list)
    string_count: int = 0
    resource_map: list[int] = field(default_factory=list)
    version: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def root(self) -> AxmlElement | None:
        return self.elements[0] if self.elements else None

    def manifest(self) -> AxmlElement | None:
        for e in self.elements:
            if e.tag == "manifest":
                return e
        return None

    def all_elements(self) -> list[AxmlElement]:
        out: list[AxmlElement] = []
        for e in self.elements:
            out.extend(e.iter())
        return out


def parse_axml(buf: bytes) -> AxmlDocument:
    """Decode a binary XML document (``res/xml`` and ``AndroidManifest.xml``)."""
    doc = AxmlDocument()
    if len(buf) < 8:
        raise ParseError("AXML buffer too small", 0)
    ctype, hsize, size = _chunk_header(buf, 0)
    if ctype != RES_XML_TYPE:
        raise ParseError(f"not an AXML document (chunk type {ctype:#06x})", 0)
    doc.version = _u32(buf, 8) if hsize >= 12 and len(buf) >= 12 else 0
    end = min(size, len(buf))
    strings: list[str] = []
    resmap: list[int] = []
    off = hsize
    stack: list[AxmlElement] = []
    while off + 8 <= end:
        ctype, chsize, csize = _chunk_header(buf, off)
        if csize == 0:
            doc.problems.append(f"zero-size chunk at {off:#x}; stopping")
            break
        if off + csize > len(buf):
            doc.problems.append(f"chunk at {off:#x} overruns buffer; clipping")
            csize = len(buf) - off
        if ctype == RES_STRING_POOL_TYPE:
            strings, _ = _decode_pool_strings(buf, off)
        elif ctype == RES_XML_RESOURCE_MAP_TYPE:
            n = (csize - chsize) // 4
            resmap = [_u32(buf, off + chsize + i * 4) for i in range(n)]
        elif ctype == RES_XML_START_ELEMENT_TYPE:
            line = _u32(buf, off + 8)
            ext = off + chsize
            name_idx = _u32(buf, ext + 4)
            attr_start = _u16(buf, ext + 8)
            attr_size = _u16(buf, ext + 10)
            attr_count = _u16(buf, ext + 12)
            tag = strings[name_idx] if 0 <= name_idx < len(strings) else f"[str{name_idx}]"
            el = AxmlElement(tag=tag, parent=stack[-1] if stack else None,
                             depth=len(stack), line=line)
            base = ext + attr_start
            for i in range(attr_count):
                aoff = base + i * attr_size
                if aoff + 20 > off + csize:
                    doc.problems.append(f"attribute {i} of <{tag}> overruns chunk")
                    break
                ans = _u32(buf, aoff)
                aname = _u32(buf, aoff + 4)
                araw = _u32(buf, aoff + 8)
                vtype = _u8(buf, aoff + 15)
                vdata = _u32(buf, aoff + 16)
                namespace = strings[ans] if 0 <= ans < len(strings) else ""
                name = strings[aname] if 0 <= aname < len(strings) else f"[str{aname}]"
                rid = resmap[aname] if aname < len(resmap) else 0
                if not name and aname < len(resmap):
                    name = ANDROID_ATTR_IDS.get(resmap[aname], f"0x{resmap[aname]:08x}")
                raw = strings[araw] if (vtype == TYPE_STRING and 0 <= araw < len(strings)) else None
                el.attributes.append(AxmlAttribute(
                    namespace=namespace, name=name, resource_id=rid, raw_value=raw,
                    typed_value=format_res_value(vtype, vdata, strings),
                    value_type=vtype, data=vdata))
            if stack:
                stack[-1].children.append(el)
            else:
                doc.elements.append(el)
            stack.append(el)
        elif ctype == RES_XML_END_ELEMENT_TYPE:
            if stack:
                stack.pop()
        off += csize
    doc.string_count = len(strings)
    doc.resource_map = resmap
    return doc
