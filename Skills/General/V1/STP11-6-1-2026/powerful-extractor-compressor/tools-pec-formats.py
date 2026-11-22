#!/usr/bin/env python3
"""Format adapters for the powerful-extractor-compressor Skill Tool.

Loaded by ``tools-pec.py``; not a standalone entry point. Provides:

* magic-byte / structural detection for archives and containers
* a normalized :class:`Entry` model shared by every backend
* listing and streaming extraction for the formats the local runtime supports
* archive creation for ZIP / TAR / compressed TAR / raw single-stream codecs
* multi-volume detection, diagnosis and assembly
* honest capability reporting for formats that need an absent backend

Nothing here executes archive content, and no backend is invoked through a
shell. External helpers (``7z``, ``unrar``) are only used when explicitly
configured and available, always with a fixed argv list.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import os
import shutil
import struct
import subprocess
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterator

from importlib import import_module

import importlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _runtime():
    return sys.modules.get("pec_runtime")


def rt():
    mod = _runtime()
    if mod is None:  # pragma: no cover - defensive direct import
        import importlib.util
        spec = importlib.util.spec_from_file_location("pec_runtime",
                                                      os.path.join(HERE, "tools-pec-runtime.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pec_runtime"] = mod
        spec.loader.exec_module(mod)
    return mod


R = rt()
PecError = R.PecError
EXIT_USAGE = R.EXIT_USAGE
EXIT_DEPENDENCY = R.EXIT_DEPENDENCY
EXIT_INTEGRITY = R.EXIT_INTEGRITY

# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------
# (name, offset, magic bytes, kind, notes)
SIGNATURES: list[tuple[str, int, bytes, str, str]] = [
    ("zip", 0, b"PK\x03\x04", "archive", "standard ZIP / ZIP64 local header"),
    ("zip-empty", 0, b"PK\x05\x06", "archive", "empty ZIP end-of-central-directory"),
    ("zip-spanned", 0, b"PK\x07\x08", "archive", "spanned/split ZIP segment"),
    ("zip64-eocd", 0, b"PK\x06\x06", "archive", "ZIP64 end-of-central-directory"),
    ("7z", 0, b"7z\xbc\xaf\x27\x1c", "archive", "7-Zip container"),
    ("rar5", 0, b"Rar!\x1a\x07\x01\x00", "archive", "RAR 5.x"),
    ("rar4", 0, b"Rar!\x1a\x07\x00", "archive", "RAR 4.x"),
    ("gzip", 0, b"\x1f\x8b", "compressed-stream", "GZIP (single stream)"),
    ("bzip2", 0, b"BZh", "compressed-stream", "BZIP2 (single stream)"),
    ("xz", 0, b"\xfd7zXZ\x00", "compressed-stream", "XZ / LZMA2 (single stream)"),
    ("zstd", 0, b"\x28\xb5\x2f\xfd", "compressed-stream", "Zstandard frame"),
    ("lz4", 0, b"\x04\x22\x4d\x18", "compressed-stream", "LZ4 frame"),
    ("lzip", 0, b"LZIP", "compressed-stream", "lzip"),
    ("cab", 0, b"MSCF", "container", "Microsoft Cabinet"),
    ("wim", 0, b"MSWIM\x00\x00\x00", "container", "Windows Imaging Format"),
    ("ar", 0, b"!<arch>\n", "archive", "ar / .deb member archive"),
    ("iso9660", 32769, b"CD001", "container", "ISO-9660 / UDF optical image"),
    ("dmg-koly", 512, b"koly", "container", "Apple UDIF disk image"),
    ("sqlite", 0, b"SQLite format 3\x00", "database", "SQLite database (not an archive)"),
    ("pdf", 0, b"%PDF-", "document", "PDF document (not an archive)"),
    ("ole2", 0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "container", "OLE2 compound file"),
]

MAGIC_EXT_HINT = {
    "zip": [".zip", ".docx", ".xlsx", ".pptx", ".jar", ".apk", ".epub", ".whl"],
    "7z": [".7z"],
    "rar4": [".rar"], "rar5": [".rar"],
    "gzip": [".gz", ".tgz"],
    "bzip2": [".bz2", ".tbz2"],
    "xz": [".xz", ".txz"],
    "zstd": [".zst", ".tzst"],
    "lz4": [".lz4"],
    "cab": [".cab"],
    "wim": [".wim", ".esd"],
    "iso9660": [".iso"],
    "dmg-koly": [".dmg"],
    "ar": [".deb", ".a"],
}

# Extensions that are ZIP containers in disguise (OOXML, EPUB, wheels...)
ZIP_FAMILY_EXTENSIONS = {
    ".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub",
    ".jar", ".war", ".apk", ".whl", ".xpi", ".vsix", ".ipa",
}

EXTERNAL_BACKENDS = ("7z", "7zz", "7za", "unrar", "rar")


def read_head(path: str, size: int = 512) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(size)
    except OSError as exc:
        raise PecError(f"cannot read {path}: {exc}", R.EXIT_IO) from exc


def detect_bytes(head: bytes, path: str = "") -> dict | None:
    for name, offset, magic, kind, notes in SIGNATURES:
        if len(head) >= offset + len(magic) and head[offset:offset + len(magic)] == magic:
            return {"id": name, "kind": kind, "notes": notes, "offset": offset}
    # tar: ustar marker at 257, or checksum-valid header
    if len(head) >= 512:
        block = head[:512]
        if block[257:262] == b"ustar":
            return {"id": "tar", "kind": "archive", "notes": "POSIX ustar TAR", "offset": 257}
        if block[257:263] in (b"ustar\x00", b"ustar "):
            return {"id": "tar", "kind": "archive", "notes": "GNU/ustar TAR", "offset": 257}
        if _tar_checksum_ok(block):
            return {"id": "tar", "kind": "archive", "notes": "TAR (checksum-verified header)", "offset": 0}
    if head[:2] == b"MZ":
        return {"id": "pe-executable", "kind": "executable",
                "notes": "PE executable; may be a self-extracting archive", "offset": 0}
    if head[:4] == b"\x7fELF":
        return {"id": "elf-executable", "kind": "executable",
                "notes": "ELF executable; may be a self-extracting archive", "offset": 0}
    return None


def _tar_checksum_ok(block: bytes) -> bool:
    try:
        stored = int(block[148:156].replace(b"\x00", b" ").strip() or b"0", 8)
    except ValueError:
        return False
    if stored == 0:
        return False
    unsigned = sum(block[:148]) + 8 * 32 + sum(block[156:])
    signed = sum(struct.unpack("148b", block[:148])) + 8 * 32 + sum(struct.unpack("356b", block[156:512]))
    return stored in (unsigned, signed)


TAR_SUFFIXES = {".tgz": "tar.gz", ".tbz2": "tar.bz2", ".tbz": "tar.bz2",
                ".txz": "tar.xz", ".tzst": "tar.zst"}


def tar_suffix_for(path: str) -> str | None:
    """Return the compressed-TAR format implied by the filename, else None.

    Recognizes both short suffixes (.tgz, .tbz2, .txz, .tzst) and explicit
    double suffixes (.tar.gz, .tar.bz2, .tar.xz, .tar.zst).
    """
    name = os.path.basename(path).lower()
    for suffix, fmt in TAR_SUFFIXES.items():
        if name.endswith(suffix):
            return fmt
    for suffix, fmt in ((".tar.gz", "tar.gz"), (".tar.bz2", "tar.bz2"),
                        (".tar.xz", "tar.xz"), (".tar.zst", "tar.zst")):
        if name.endswith(suffix):
            return fmt
    return None


def detect_file(path: str) -> dict:
    """Detect the real type of ``path`` and reconcile it with the extension."""
    if not os.path.exists(path):
        raise PecError(f"path not found: {path}", EXIT_USAGE)
    if os.path.isdir(path):
        return {"path": path, "id": "directory", "kind": "directory",
                "notes": "directory input", "extension_match": True}
    head = read_head(path, 525_000)
    guess = detect_bytes(head, path)
    ext = os.path.splitext(path)[1].lower()
    result = guess or {"id": "unknown", "kind": "unknown", "notes": "no known signature", "offset": 0}
    hints = MAGIC_EXT_HINT.get(result["id"], [])
    match = (not ext) or (ext in hints) or (result["id"] in {"zip", "zip-empty", "zip-spanned"} and ext in ZIP_FAMILY_EXTENSIONS)
    result.update({
        "path": path,
        "size": os.path.getsize(path),
        "extension": ext,
        "extension_match": bool(match),
        "zip_family": result["id"].startswith("zip") and ext in ZIP_FAMILY_EXTENSIONS,
    })
    compressed_tar = tar_suffix_for(path)
    if compressed_tar and result["id"] in {"gzip", "bzip2", "xz", "zstd"}:
        result["container"] = "tar"
        result["compressed_tar"] = compressed_tar
        result["notes"] = f"{result['notes']}; TAR archive inside ({compressed_tar} container)"
    if not match:
        result["notes"] = f"{result['notes']}; extension {ext!r} does not match the detected format"
    if result["id"] == "pe-executable":
        embedded = _probe_embedded(head)
        if embedded:
            result["embedded"] = embedded
            result["notes"] = ("self-extracting executable exposing an embedded "
                               f"{embedded['id']} payload at offset {embedded['offset']}")
    return result


def _probe_embedded(head: bytes) -> dict | None:
    for name, magic in (("zip", b"PK\x03\x04"), ("7z", b"7z\xbc\xaf\x27\x1c"), ("rar4", b"Rar!\x1a\x07\x00")):
        idx = head.find(magic, 2)
        if idx > 0:
            return {"id": name, "offset": idx}
    return None


# --------------------------------------------------------------------------
# Normalized entry model
# --------------------------------------------------------------------------
@dataclass
class Entry:
    name: str
    size: int = 0
    compressed_size: int = 0
    is_dir: bool = False
    mtime: float | None = None
    mode: int | None = None
    link_target: str | None = None
    crc: int | None = None
    encrypted: bool = False
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {
            "name": self.name,
            "size": self.size,
            "compressed_size": self.compressed_size,
            "is_dir": self.is_dir,
            "mtime": self.mtime,
            "mode": oct(self.mode) if self.mode is not None else None,
            "link_target": self.link_target,
            "encrypted": self.encrypted,
        }
        if self.extra:
            out["extra"] = self.extra
        return out


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _entry_kwargs(entry: Entry) -> dict:
    return {
        "name": entry.name, "size": entry.size, "compressed_size": entry.compressed_size,
        "is_dir": entry.is_dir, "mtime": entry.mtime, "mode": entry.mode,
        "link_target": entry.link_target, "crc": entry.crc, "encrypted": entry.encrypted,
        "extra": entry.extra,
    }


def _zip_entries(zf: zipfile.ZipFile) -> Iterator[Entry]:
    for info in zf.infolist():
        mode = (info.external_attr >> 16) or None
        is_dir = info.is_dir() or info.filename.endswith("/")
        link_target = None
        if mode and (mode & 0o170000) == 0o120000:
            link_target = "<symlink>"
        yield Entry(
            name=info.filename,
            size=info.file_size,
            compressed_size=info.compress_size,
            is_dir=is_dir,
            mtime=time.mktime(info.date_time + (0, 0, -1)) if info.date_time else None,
            mode=mode,
            link_target=link_target,
            crc=info.CRC,
            encrypted=bool(info.flag_bits & 0x1),
            extra={"method": info.compress_type, "comment": info.comment.decode("utf-8", "replace")[:200] if info.comment else ""},
        )


def _tar_entries(tf: tarfile.TarFile) -> Iterator[Entry]:
    for info in tf.getmembers():
        link = info.linkname or None
        if info.issym():
            link = f"symlink->{info.linkname}"
        elif info.islnk():
            link = f"hardlink->{info.linkname}"
        yield Entry(
            name=info.name,
            size=info.size,
            compressed_size=0,
            is_dir=info.isdir(),
            mtime=_safe_float(info.mtime),
            mode=info.mode,
            link_target=link,
            extra={"type": info.type.decode() if isinstance(info.type, bytes) else str(info.type)},
        )


def _sevenzip_entries(path: str, config: Any) -> list[Entry]:
    module_name = str(config.get("backends.sevenzip_module", "py7zr"))
    try:
        py7zr = import_module(module_name)
    except ImportError:
        py7zr = None
    if py7zr is not None:
        with py7zr.SevenZipFile(path, mode="r") as archive:
            out = []
            for item in archive.list():
                created = getattr(item, "creationtime", None) or getattr(item, "lastwritetime", None)
                out.append(Entry(
                    name=item.filename,
                    size=int(getattr(item, "uncompressed", 0) or 0),
                    compressed_size=int(getattr(item, "compressed", 0) or 0),
                    is_dir=bool(getattr(item, "is_directory", False)),
                    mtime=created.timestamp() if created else None,
                    crc=getattr(item, "crc32", None),
                    encrypted=bool(getattr(item, "is_encrypted", False)),
                ))
            return out
    binary = _external_binary("7z", config)
    if not binary:
        raise PecError(
            "7z backend unavailable: install the 'py7zr' Python package or a 7z binary, "
            "then re-run", EXIT_DEPENDENCY)
    proc = run_backend([binary, "l", "-slt", "-p", path], config, timeout=120)
    entries: list[Entry] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            if current.get("Path"):
                entries.append(Entry(
                    name=current.get("Path", ""),
                    size=int(current.get("Size", "0") or 0),
                    compressed_size=int(current.get("Packed Size", "0") or 0),
                    is_dir=current.get("Folder") == "+",
                    mode=_mode_from_7z(current.get("Attributes", "")),
                    encrypted=current.get("Encrypted") == "+",
                ))
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key.strip()] = value.strip()
    return [e for e in entries if e.name and e.name != path]


def _mode_from_7z(attributes: str) -> int | None:
    try:
        return int(attributes.split()[0], 16) if attributes else None
    except (ValueError, IndexError):
        return None


def _rar_entries(path: str, config: Any) -> list[Entry]:
    try:
        rarfile = import_module(str(config.get("backends.rar_module", "rarfile")))
    except ImportError as exc:
        raise PecError("RAR backend unavailable: install the 'rarfile' Python package and a "
                       "system 'unrar' tool, then re-run", EXIT_DEPENDENCY) from exc
    out: list[Entry] = []
    with rarfile.RarFile(path) as rf:
        for info in rf.infolist():
            out.append(Entry(
                name=info.filename,
                size=info.file_size,
                compressed_size=info.compress_size,
                is_dir=info.is_dir(),
                mtime=info.mtime.timestamp() if info.mtime else None,
                mode=info.mode,
                link_target=("symlink->" + info.file_redir) if getattr(info, "file_redir", None) else None,
                crc=info.CRC,
                encrypted=bool(info.needs_password()),
            ))
    return out


# --------------------------------------------------------------------------
# Backend availability
# --------------------------------------------------------------------------
def _external_binary(name: str, config: Any) -> str | None:
    configured = str(config.get(f"backends.{'unrar' if name == 'unrar' else 'seven_zip'}", "") or "")
    if configured and os.path.exists(configured):
        return configured
    return shutil.which(name)


def backend_status(config: Any) -> dict:
    status = {
        "python": True,
        "zip": True, "tar": True, "gzip": True, "bzip2": True, "xz": True, "lzma": True,
    }
    for module, label in (
        ("py7zr", "7z"), ("rarfile", "rar"), ("zstandard", "zstd"), ("lz4.frame", "lz4"),
        ("blake3", "blake3"),
    ):
        try:
            import_module(module)
            status[label] = True
        except Exception:  # noqa: BLE001 - optional dependency probe
            status[label] = False
    for binary in EXTERNAL_BACKENDS:
        status[f"bin:{binary}"] = bool(shutil.which(binary))
    return status


# --------------------------------------------------------------------------
# Listing / opening
# --------------------------------------------------------------------------
ARCHIVE_KINDS = {"zip", "zip-empty", "zip-spanned", "tar", "7z", "rar4", "rar5"}


def open_archive(path: str, config: Any, password: str | None = None):
    """Return (kind, handle) for a readable archive container."""
    info = detect_file(path)
    kind = info["id"]
    if kind in {"zip", "zip-empty", "zip-spanned"}:
        try:
            return "zip", zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise PecError(f"invalid ZIP in {path}: {exc}", EXIT_INTEGRITY, "corrupt") from exc
    if kind == "tar":
        try:
            return "tar", tarfile.open(path, "r:*")
        except tarfile.TarError as exc:
            raise PecError(f"invalid TAR in {path}: {exc}", EXIT_INTEGRITY, "corrupt") from exc
    if kind == "7z":
        return "7z", None
    if kind in {"rar4", "rar5"}:
        return "rar", None
    if kind in {"gzip", "bzip2", "xz", "zstd", "lz4"}:
        if kind != "zstd" and _is_tar_stream(path):
            return "tar", tarfile.open(path, "r:*")
        return kind, None
    if kind == "pe-executable":
        embedded = info.get("embedded")
        if embedded:
            raise PecError(
                f"{path} is a self-extracting executable with an embedded {embedded['id']} payload at "
                f"offset {embedded['offset']}; run `python3 tools-pec.py detect {path}` then extract the "
                "payload with an approved tool rather than executing the file", R.EXIT_POLICY, "sfe")
        raise PecError(f"{path} is an executable, not an archive", R.EXIT_POLICY, "executable")
    raise PecError(f"unsupported container type {kind!r} for {path}", EXIT_DEPENDENCY, "unsupported")


def _is_tar_stream(path: str) -> bool:
    """True when a gzip/bzip2/xz stream actually contains a TAR archive."""
    try:
        with tarfile.open(path, "r:*") as tf:
            return bool(tf.next() is not None)
    except Exception:  # noqa: BLE001 - non-tar streams are expected here
        return False


def list_entries(path: str, config: Any, password: str | None = None) -> tuple[list[Entry], dict]:
    kind, handle = open_archive(path, config, password)
    meta = detect_file(path)
    meta["opened_as"] = kind
    if kind == "zip":
        with handle as zf:
            entries = list(_zip_entries(zf))
            if any(e.encrypted for e in entries) and password and _is_aes_zip(path):
                try:
                    pz = import_module("pyzipper")
                    with pz.AESZipFile(path) as azf:
                        azf.setpassword(password.encode("utf-8"))
                        entries = [Entry(**{**_entry_kwargs(e), "encrypted": True}) for e in entries]
                except Exception:  # noqa: BLE001 - listing already succeeded via stdlib
                    pass
            meta["encrypted_entries"] = sum(1 for e in entries if e.encrypted)
            meta["comment"] = (zf.comment or b"").decode("utf-8", "replace")[:500]
        return entries, meta
    if kind == "tar":
        with handle as tf:
            return list(_tar_entries(tf)), meta
    if kind == "7z":
        return _sevenzip_entries(path, config), meta
    if kind == "rar":
        return _rar_entries(path, config), meta
    # single-stream codecs present exactly one synthetic entry
    name = stream_output_name(path, kind)
    entries = [Entry(name=name, size=stream_size(path, kind, config), compressed_size=os.path.getsize(path))]
    return entries, meta


def stream_output_name(path: str, kind: str) -> str:
    base = os.path.basename(path)
    stripped = {
        "gzip": (".gz", ".tgz", ".gzip"),
        "bzip2": (".bz2", ".tbz2", ".tbz"),
        "xz": (".xz", ".txz", ".lzma"),
        "zstd": (".zst", ".tzst"),
        "lz4": (".lz4",),
    }[kind]
    lower = base.lower()
    for ext in stripped:
        if lower.endswith(ext):
            stem = base[: -len(ext)]
            if ext in {".tgz", ".tbz2", ".tbz", ".txz", ".tzst"}:
                return stem + ".tar"
            return stem or base + ".out"
    return base + ".out"


def stream_size(path: str, kind: str, config: Any) -> int:
    """Uncompressed size for single-stream codecs (decompresses with a cap)."""
    cap = int(config.get("limits.max_entry_bytes", 8 << 30))
    total = 0
    try:
        opener = _stream_opener(path, kind, config)
        with opener() as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    return cap
    except Exception:  # noqa: BLE001 - size probe is advisory
        return 0
    return total


class _PlainFile:
    def __init__(self, fh):
        self.fh = fh

    def __enter__(self):
        return self.fh

    def __exit__(self, *exc):
        self.fh.close()


def _stream_opener(path: str, kind: str, config: Any):
    if kind == "gzip":
        return lambda: gzip.open(path, "rb")
    if kind == "bzip2":
        return lambda: bz2.open(path, "rb")
    if kind == "xz":
        return lambda: lzma.open(path, "rb")
    if kind == "zstd":
        try:
            zstd = import_module("zstandard")
        except ImportError as exc:
            raise PecError("Zstandard backend unavailable: pip install zstandard", EXIT_DEPENDENCY) from exc
        return lambda: zstd.ZstdDecompressor().stream_reader(open(path, "rb"), closefd=True)
    if kind == "lz4":
        try:
            lz4frame = import_module("lz4.frame")
        except ImportError as exc:
            raise PecError("LZ4 backend unavailable: pip install lz4", EXIT_DEPENDENCY) from exc
        return lambda: lz4frame.open(path, "rb")
    raise PecError(f"no stream reader for {kind}", EXIT_DEPENDENCY)


def iter_entry_bytes(path: str, kind: str, entry: Entry, config: Any,
                     password: str | None = None, chunk: int = 1 << 20) -> Iterator[bytes]:
    """Yield decompressed bytes for one entry, chunk by chunk."""
    if kind == "zip":
        zip_reader = zipfile.ZipFile
        if entry.encrypted and password and _is_aes_zip(path):
            try:
                zip_reader = import_module("pyzipper").AESZipFile
            except ImportError as exc:
                raise PecError("AES-encrypted ZIP needs: pip install pyzipper", EXIT_DEPENDENCY) from exc
        with zip_reader(path) as zf:
            pwd = password.encode("utf-8") if password else None
            if entry.encrypted and password and zip_reader is not zipfile.ZipFile:
                zf.setpassword(pwd)
            try:
                with zf.open(entry.name, pwd=pwd) as fh:
                    while True:
                        data = fh.read(min(chunk, int(config.get("limits.io_chunk_bytes", chunk))))
                        if not data:
                            break
                        yield data
            except RuntimeError as exc:
                raise PecError(f"cannot read {entry.name!r}: {exc} (encrypted or unsupported)",
                               R.EXIT_POLICY, "encryption") from exc
        return
    if kind == "tar":
        with tarfile.open(path, "r:*") as tf:
            member = tf.getmember(entry.name)
            fh = tf.extractfile(member)
            if fh is None:
                return
            with fh:
                while True:
                    data = fh.read(chunk)
                    if not data:
                        break
                    yield data
        return
    if kind == "7z":
        yield from _sevenzip_stream(path, entry, config, password, chunk)
        return
    if kind == "rar":
        try:
            rarfile = import_module(str(config.get("backends.rar_module", "rarfile")))
        except ImportError as exc:  # pragma: no cover - guarded earlier
            raise PecError("rarfile backend unavailable", EXIT_DEPENDENCY) from exc
        with rarfile.RarFile(path) as rf:
            if password:
                rf.setpassword(password)
            try:
                with rf.open(entry.name) as fh:
                    while True:
                        data = fh.read(chunk)
                        if not data:
                            break
                        yield data
            except Exception as exc:  # noqa: BLE001 - rarfile raises many types
                raise PecError(f"cannot read RAR entry {entry.name!r}: {exc}",
                               R.EXIT_INTEGRITY, "corrupt") from exc
        return
    # single stream codecs
    opener = _stream_opener(path, kind, config)
    with opener() as fh:
        while True:
            data = fh.read(chunk)
            if not data:
                break
            yield data


def _sevenzip_stream(path: str, entry: Entry, config: Any, password: str | None, chunk: int) -> Iterator[bytes]:
    try:
        py7zr = import_module(str(config.get("backends.sevenzip_module", "py7zr")))
    except ImportError:
        py7zr = None
    if py7zr is not None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with py7zr.SevenZipFile(path, mode="r", password=password) as archive:
                archive.extract(path=tmp, targets=[entry.name])
            target = os.path.join(tmp, entry.name)
            if os.path.exists(target):
                with open(target, "rb") as fh:
                    while True:
                        data = fh.read(chunk)
                        if not data:
                            break
                        yield data
        return
    raise PecError("7z streaming requires py7zr", EXIT_DEPENDENCY)


def run_backend(argv: list[str], config: Any, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run an allow-listed external backend with a bounded timeout (no shell)."""
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env["LC_ALL"] = "C"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              check=False, env=env)
    except FileNotFoundError as exc:
        raise PecError(f"backend not found: {argv[0]}", EXIT_DEPENDENCY) from exc
    except subprocess.TimeoutExpired as exc:
        raise PecError(f"backend timed out after {timeout}s: {argv[0]}", R.EXIT_CANCELLED, "timeout") from exc
    return proc


def _is_aes_zip(path: str) -> bool:
    """True when any ZIP entry uses WinZip AES (method 99) rather than ZipCrypto."""
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.compress_type == 99:
                    return True
    except Exception:  # noqa: BLE001 - detection is best-effort
        return False
    return False


def create_encrypted_archive(entries: list[tuple[str, str]], output: str, password: str,
                             config: Any, level: int = 6) -> dict:
    """Create an AES-256 encrypted ZIP from ``(filesystem_path, archive_name)`` pairs."""
    if not password:
        raise PecError("an archive password is required", EXIT_USAGE)
    try:
        pyzipper = import_module("pyzipper")
    except ImportError as exc:
        raise PecError("encrypted ZIP creation requires: pip install pyzipper", EXIT_DEPENDENCY) from exc
    if os.path.exists(output):
        raise PecError(f"refusing to overwrite existing archive: {output}", R.EXIT_POLICY, "overwrite")
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    tmp = output + ".partial"
    try:
        with pyzipper.AESZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=max(0, min(9, level)),
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(password.encode("utf-8"))
            zf.setencryption(pyzipper.WZ_AES, nbits=256)
            for fs_path, arc in entries:
                zf.write(fs_path, arc)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.replace(tmp, output)
    digest = hashlib.sha256()
    with open(output, "rb") as fh:
        while True:
            data = fh.read(1 << 20)
            if not data:
                break
            digest.update(data)
    input_bytes = sum(os.path.getsize(fs) for fs, _ in entries if os.path.exists(fs))
    out_bytes = os.path.getsize(output)
    return {"output": output, "format": "zip-aes256", "members": len(entries), "directories": 0,
            "input_bytes": input_bytes, "output_bytes": out_bytes,
            "ratio": round(input_bytes / out_bytes, 3) if out_bytes else 0.0,
            "sha256": digest.hexdigest(), "encryption": "AES-256",
            "warning": "AES-256 ZIP requires a modern extractor; test recipients before delivery"}


# --------------------------------------------------------------------------
# Multi-volume support
# --------------------------------------------------------------------------
def volume_family(path: str) -> dict | None:
    """Detect whether ``path`` belongs to a multi-volume set."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    lower = ext.lower()
    if lower == ".zip" and _has_z01(directory, stem):
        return {"kind": "zip-spanned", "stem": stem, "dir": directory}
    if lower in {".z01", ".z02", ".z03"} or (lower.startswith(".z") and lower[1:].isdigit()):
        return {"kind": "zip-spanned", "stem": stem, "dir": directory}
    if lower in {".001", ".002", ".003"} or (lower[1:].isdigit() and len(lower) >= 4):
        return {"kind": "numeric-chunks", "stem": stem, "dir": directory}
    if lower in {".r00", ".r01"} or lower.startswith(".r") and lower[1:].isdigit():
        return {"kind": "rar-volumes", "stem": stem, "dir": directory}
    if ".part" in name.lower() and lower in {".rar", ".001"}:
        return {"kind": "rar-parts", "stem": name.split(".part")[0], "dir": directory}
    return None


def _has_z01(directory: str, stem: str) -> bool:
    return os.path.exists(os.path.join(directory, f"{stem}.z01"))


def collect_volumes(path: str) -> dict:
    """Return the ordered volume list and a diagnosis for a multi-volume set."""
    family = volume_family(path)
    if not family:
        return {"multipart": False, "volumes": [os.path.abspath(path)]}
    directory = family["dir"]
    kind = family["kind"]
    files = sorted(os.listdir(directory))
    volumes: list[str] = []
    if kind == "zip-spanned":
        stem = family["stem"]
        z = sorted(f for f in files if f.lower().startswith(stem.lower() + ".z") and f.lower()[len(stem) + 2:].isdigit())
        final = [f for f in files if f.lower() == stem.lower() + ".zip"]
        volumes = [os.path.join(directory, f) for f in z + final]
    elif kind == "numeric-chunks":
        stem = family["stem"]
        volumes = [os.path.join(directory, f) for f in files
                   if f.startswith(stem + ".") and f[len(stem) + 1:].isdigit()]
        volumes.sort(key=lambda p: int(os.path.splitext(p)[1][1:]))
    elif kind == "rar-volumes":
        stem = family["stem"]
        volumes = [os.path.join(directory, f) for f in files if f.startswith(stem + ".")]
        volumes.sort()
    elif kind == "rar-parts":
        stem = family["stem"]
        volumes = [os.path.join(directory, f) for f in files if f.lower().startswith(stem.lower() + ".part")]
        volumes.sort()
    numbers: list[int] = []
    for vol in volumes:
        tail = os.path.splitext(vol)[1].lstrip(".")
        if tail.isdigit():
            numbers.append(int(tail))
        elif tail.lower().startswith("z") and tail[1:].isdigit():
            numbers.append(int(tail[1:]))
        elif tail.lower() == "zip":
            numbers.append(9999)
        elif tail.lower().startswith("r") and tail[1:].isdigit():
            numbers.append(int(tail[1:]) + 1)
    gaps: list[int] = []
    if numbers:
        ordered = [n for n in numbers if n != 9999]
        for expected, actual in zip(range(ordered[0], ordered[-1] + 1), ordered):
            if expected != actual:
                gaps.append(expected)
    complete = bool(volumes) and not gaps
    if kind == "zip-spanned":
        complete = complete and any(v.lower().endswith(".zip") for v in volumes)
    return {
        "multipart": True,
        "kind": kind,
        "stem": family["stem"],
        "volumes": volumes,
        "count": len(volumes),
        "total_bytes": sum(os.path.getsize(v) for v in volumes),
        "missing": gaps,
        "complete": complete,
    }


def assemble_volumes(volumes: list[str], output: str, chunk: int = 1 << 20) -> dict:
    """Concatenate byte-chunk volumes into one file (never for zip-spanned sets)."""
    if not volumes:
        raise PecError("no volumes to assemble", EXIT_USAGE)
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    tmp = output + ".partial"
    with open(tmp, "wb") as out:
        for vol in volumes:
            with open(vol, "rb") as fh:
                while True:
                    data = fh.read(chunk)
                    if not data:
                        break
                    out.write(data)
                    digest.update(data)
                    written += len(data)
        out.flush()
        os.fsync(out.fileno())
    head = read_head(tmp, 600_000)
    guess = detect_bytes(head) or {"id": "unknown"}
    if guess["id"] in {"zip", "zip-spanned"}:
        # A spanned zip cannot simply be concatenated; validate before publishing.
        try:
            with zipfile.ZipFile(tmp) as zf:
                zf.infolist()
        except Exception as exc:  # noqa: BLE001 - validation probe
            os.unlink(tmp)
            raise PecError(
                "concatenated volumes do not form a readable ZIP; this set is likely a true "
                f"spanned archive that must be joined with `zip -s 0` ({exc})",
                EXIT_INTEGRITY, "spanned-zip") from exc
    os.replace(tmp, output)
    return {"output": output, "merged_from": volumes, "bytes": written,
            "sha256": digest.hexdigest(), "detected": guess["id"]}


# --------------------------------------------------------------------------
# Extraction primitives (single entry → file object)
# --------------------------------------------------------------------------
def extract_entry_to_path(path: str, kind: str, entry: Entry, dest: str, config: Any,
                          password: str | None = None, hasher=None,
                          max_bytes: int | None = None) -> dict:
    """Stream one entry to ``dest`` (parent must already exist and be safe)."""
    chunk = int(config.get("limits.io_chunk_bytes", 1 << 20))
    written = 0
    digest = hashlib.sha256()
    started = time.monotonic()
    limit = max_bytes if max_bytes is not None else int(config.get("limits.max_entry_bytes", 8 << 30))
    with open(dest, "wb") as out:
        try:
            for data in iter_entry_bytes(path, kind, entry, config, password, chunk):
                written += len(data)
                if written > limit:
                    raise PecError(
                        f"entry {entry.name!r} exceeded the per-entry byte limit ({limit} bytes)",
                        R.EXIT_POLICY, "bomb")
                out.write(data)
                digest.update(data)
        except PecError:
            out.close()
            try:
                os.unlink(dest)
            except OSError:
                pass
            raise
        out.flush()
        os.fsync(out.fileno())
    return {
        "path": dest,
        "bytes": written,
        "sha256": digest.hexdigest(),
        "seconds": round(time.monotonic() - started, 4),
        "throughput_mbps": round((written / (1 << 20)) / max(1e-6, time.monotonic() - started), 2),
    }


def native_test(path: str, config: Any) -> dict:
    """Run a format-native integrity test when the backend offers one."""
    info = detect_file(path)
    kind = info["id"]
    started = time.monotonic()
    if kind in {"zip", "zip-empty", "zip-spanned"}:
        try:
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
            return {"tested": True, "backend": "zipfile", "ok": bad is None,
                    "bad_entry": bad, "seconds": round(time.monotonic() - started, 3)}
        except Exception as exc:  # noqa: BLE001
            return {"tested": True, "backend": "zipfile", "ok": False, "error": str(exc),
                    "seconds": round(time.monotonic() - started, 3)}
    if kind == "tar":
        try:
            with tarfile.open(path, "r:*") as tf:
                for member in tf:
                    if member.isfile():
                        fh = tf.extractfile(member)
                        if fh:
                            while fh.read(1 << 20):
                                pass
            return {"tested": True, "backend": "tarfile", "ok": True,
                    "seconds": round(time.monotonic() - started, 3)}
        except Exception as exc:  # noqa: BLE001
            return {"tested": True, "backend": "tarfile", "ok": False, "error": str(exc),
                    "seconds": round(time.monotonic() - started, 3)}
    binary = _external_binary("7z", config)
    if kind == "7z" and binary:
        proc = run_backend([binary, "t", path], config, timeout=600)
        return {"tested": True, "backend": os.path.basename(binary), "ok": proc.returncode == 0,
                "stdout_tail": proc.stdout[-500:], "seconds": round(time.monotonic() - started, 3)}
    if kind in {"rar4", "rar5"}:
        unrar = _external_binary("unrar", config)
        if unrar:
            proc = run_backend([unrar, "t", "-idq", path], config, timeout=600)
            return {"tested": True, "backend": "unrar", "ok": proc.returncode == 0,
                    "seconds": round(time.monotonic() - started, 3)}
    if kind in {"gzip", "bzip2", "xz"}:
        try:
            with _stream_opener(path, kind, config)() as fh:
                while fh.read(1 << 20):
                    pass
            return {"tested": True, "backend": kind, "ok": True,
                    "seconds": round(time.monotonic() - started, 3)}
        except Exception as exc:  # noqa: BLE001
            return {"tested": True, "backend": kind, "ok": False, "error": str(exc),
                    "seconds": round(time.monotonic() - started, 3)}
    return {"tested": False, "ok": None, "reason": f"no native test backend for {kind}"}


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------
CREATE_FORMATS = {
    "zip": {"kind": "zip", "extension": ".zip"},
    "zip64": {"kind": "zip", "extension": ".zip", "force_zip64": True},
    "tar": {"kind": "tar", "extension": ".tar", "mode": "w"},
    "tar.gz": {"kind": "tar", "extension": ".tar.gz", "mode": "w:gz"},
    "tgz": {"kind": "tar", "extension": ".tgz", "mode": "w:gz"},
    "tar.bz2": {"kind": "tar", "extension": ".tar.bz2", "mode": "w:bz2"},
    "tar.xz": {"kind": "tar", "extension": ".tar.xz", "mode": "w:xz"},
    "gzip": {"kind": "stream", "codec": "gzip", "extension": ".gz"},
    "bz2": {"kind": "stream", "codec": "bzip2", "extension": ".bz2"},
    "xz": {"kind": "stream", "codec": "xz", "extension": ".xz"},
    "zstd": {"kind": "stream", "codec": "zstd", "extension": ".zst"},
    "7z": {"kind": "7z", "extension": ".7z"},
}


def _collect_members(sources: list[str], root_folder: str | None, deterministic: bool) -> list[dict]:
    members: list[dict] = []
    for src in sources:
        src = os.path.abspath(src)
        if not os.path.exists(src):
            raise PecError(f"input not found: {src}", EXIT_USAGE)
        base = os.path.dirname(src)
        if os.path.isfile(src):
            arcname = os.path.join(root_folder, os.path.basename(src)) if root_folder else os.path.basename(src)
            members.append({"fs": src, "arc": arcname.replace(os.sep, "/"), "size": os.path.getsize(src)})
        else:
            for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
                dirnames.sort()
                filenames.sort()
                rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
                if root_folder:
                    rel_dir = f"{root_folder.rstrip('/')}/{rel_dir}"
                members.append({"fs": dirpath, "arc": rel_dir.rstrip("/") + "/", "size": 0, "dir": True})
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    if os.path.islink(full):
                        continue
                    arc = f"{rel_dir.rstrip('/')}/{name}" if rel_dir else name
                    members.append({"fs": full, "arc": arc, "size": os.path.getsize(full)})
    members.sort(key=lambda m: m["arc"])
    return members


def _deterministic_time() -> tuple[int, int, int, int, int, int]:
    return (1980, 1, 1, 0, 0, 0)


def create_archive(sources: list[str], output: str, config: Any, fmt: str = "zip",
                   level: int = 6, root_folder: str | None = None,
                   deterministic: bool = True, store_extensions: list[str] | None = None,
                   cancellation=None, progress=None, password: str | None = None) -> dict:
    spec = CREATE_FORMATS.get(fmt)
    if spec is None:
        raise PecError(f"unsupported output format {fmt!r}; choose from {', '.join(sorted(CREATE_FORMATS))}",
                       EXIT_USAGE)
    if os.path.exists(output):
        raise PecError(f"refusing to overwrite existing archive: {output}", R.EXIT_POLICY, "overwrite")
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    members = _collect_members(sources, root_folder, deterministic)
    if not members:
        raise PecError("no input files matched the sources", EXIT_USAGE)
    store_ext = {e.lower() for e in (store_extensions or [])}
    started = time.monotonic()
    tmp_out = output + ".partial"
    total_in = 0
    written = 0
    kind = spec["kind"]
    try:
        if kind == "zip":
            zip_factory = zipfile.ZipFile
            if password:
                try:
                    zip_factory = import_module("pyzipper").AESZipFile
                except ImportError as exc:
                    raise PecError(
                        "encrypted ZIP creation requires AES support: pip install pyzipper "
                        "(never uses legacy ZipCrypto)", EXIT_DEPENDENCY) from exc
            zip_kwargs = {"compression": zipfile.ZIP_DEFLATED,
                          "compresslevel": max(0, min(9, level)),
                          "allowZip64": True,
                          "strict_timestamps": False}
            if password:
                zip_kwargs["encryption"] = import_module("pyzipper").WZ_AES
                zip_kwargs["encryption_kwargs"] = {"nbits": 256}
            with zip_factory(tmp_out, "w", **zip_kwargs) as zf:
                if password:
                    zf.setpassword(password.encode("utf-8"))
                for member in members:
                    if cancellation:
                        cancellation.check()
                    if member.get("dir"):
                        info = zipfile.ZipInfo(member["arc"])
                        info.external_attr = (0o40755 << 16) | 0x10
                        info.date_time = _deterministic_time() if deterministic else time.localtime()[:6]
                        zf.writestr(info, b"")
                        continue
                    ext = os.path.splitext(member["fs"])[1].lower()
                    method = zipfile.ZIP_STORED if ext in store_ext else zipfile.ZIP_DEFLATED
                    info = zipfile.ZipInfo(member["arc"], date_time=_deterministic_time() if deterministic else time.localtime(member["fs"])[:6])
                    info.compress_type = method
                    info.external_attr = 0o644 << 16
                    with open(member["fs"], "rb") as src, zf.open(info, "w", force_zip64=spec.get("force_zip64", False)) as dst:
                        while True:
                            chunk = src.read(int(config.get("limits.io_chunk_bytes", 1 << 20)))
                            if not chunk:
                                break
                            dst.write(chunk)
                            written += len(chunk)
                    total_in += member["size"]
                    if progress:
                        progress(member["arc"], written, total_in)
            tmp_out_check = tmp_out
        elif kind == "tar":
            mode = spec["mode"]
            tar_kwargs = {} if mode == "w" else {"compresslevel": max(1, min(9, level))} if mode != "w:xz" else {"preset": max(0, min(9, level))}
            with tarfile.open(tmp_out, mode, **tar_kwargs) as tf:
                for member in members:
                    if cancellation:
                        cancellation.check()
                    info = tarfile.TarInfo(member["arc"].rstrip("/") or ".")
                    if member.get("dir"):
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        info.mtime = 0 if deterministic else int(time.time())
                        tf.addfile(info)
                        continue
                    info.size = member["size"]
                    info.mode = 0o644
                    info.mtime = 0 if deterministic else int(os.path.getmtime(member["fs"]))
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with open(member["fs"], "rb") as src:
                        tf.addfile(info, src)
                    written += member["size"]
                    total_in += member["size"]
                    if progress:
                        progress(member["arc"], written, total_in)
        elif kind == "stream":
            codec = spec["codec"]
            if codec == "gzip":
                opener = lambda: gzip.open(tmp_out, "wb", compresslevel=max(0, min(9, level)))
            elif codec == "bzip2":
                opener = lambda: bz2.open(tmp_out, "wb", compresslevel=max(1, min(9, level)))
            elif codec == "xz":
                opener = lambda: lzma.open(tmp_out, "wb", preset=max(0, min(9, level)))
            else:  # zstd
                try:
                    zstd = import_module("zstandard")
                except ImportError as exc:
                    raise PecError("Zstandard creation requires: pip install zstandard", EXIT_DEPENDENCY) from exc
                opener = lambda: zstd.ZstdCompressor(level=max(1, min(22, level + 10))).stream_writer(
                    open(tmp_out, "wb"), closefd=True)
            if len(members) != 1 or members[0].get("dir"):
                raise PecError(f"format {fmt!r} compresses exactly one file; pass a single file input",
                               EXIT_USAGE)
            with open(members[0]["fs"], "rb") as src, opener() as dst:
                while True:
                    if cancellation:
                        cancellation.check()
                    chunk = src.read(int(config.get("limits.io_chunk_bytes", 1 << 20)))
                    if not chunk:
                        break
                    dst.write(chunk)
                    written += len(chunk)
            total_in = members[0]["size"]
            tmp_out_check = tmp_out
        else:  # 7z
            try:
                py7zr = import_module("py7zr")
            except ImportError as exc:
                raise PecError("7z creation requires: pip install py7zr", EXIT_DEPENDENCY) from exc
            with py7zr.SevenZipFile(tmp_out, "w") as archive:
                seen = set()
                for member in members:
                    if cancellation:
                        cancellation.check()
                    if member["fs"] in seen:
                        continue
                    seen.add(member["fs"])
                    archive.write(member["fs"], member["arc"].rstrip("/") or ".")
                    written += member.get("size", 0)
            total_in = sum(m.get("size", 0) for m in members)
        os.replace(tmp_out, output)
    except BaseException:
        for leftover in (tmp_out,):
            if os.path.exists(leftover):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
        raise
    out_size = os.path.getsize(output)
    digest = hashlib.sha256()
    with open(output, "rb") as fh:
        while True:
            data = fh.read(1 << 20)
            if not data:
                break
            digest.update(data)
    elapsed = max(1e-6, time.monotonic() - started)
    return {
        "output": output,
        "format": fmt,
        "level": level,
        "deterministic": deterministic,
        "members": len([m for m in members if not m.get("dir")]),
        "directories": len([m for m in members if m.get("dir")]),
        "input_bytes": total_in,
        "output_bytes": out_size,
        "ratio": round(total_in / out_size, 3) if out_size else 0.0,
        "sha256": digest.hexdigest(),
        "seconds": round(elapsed, 3),
        "throughput_mbps": round((total_in / (1 << 20)) / elapsed, 2),
    }


def split_volumes(path: str, volume_size: int, chunk: int = 8 << 20) -> dict:
    """Split a finished archive into deterministic numeric chunks."""
    if volume_size < 65_536:
        raise PecError("volume size must be at least 65536 bytes", EXIT_USAGE)
    base_size = os.path.getsize(path)
    volumes: list[dict] = []
    with open(path, "rb") as src:
        index = 1
        while True:
            remaining = volume_size
            if remaining <= 0:
                break
            target = f"{path}.{index:03d}"
            with open(target, "wb") as dst:
                while remaining > 0:
                    data = src.read(min(chunk, remaining))
                    if not data:
                        break
                    dst.write(data)
                    remaining -= len(data)
            size = os.path.getsize(target)
            if size == 0:
                os.unlink(target)
                break
            digest = hashlib.sha256()
            with open(target, "rb") as fh:
                while True:
                    data = fh.read(1 << 20)
                    if not data:
                        break
                    digest.update(data)
            volumes.append({"path": target, "bytes": size, "sha256": digest.hexdigest()})
            if src.tell() >= base_size:
                break
            index += 1
    return {"source": path, "source_bytes": base_size, "volume_size": volume_size,
            "volumes": volumes, "count": len(volumes)}
