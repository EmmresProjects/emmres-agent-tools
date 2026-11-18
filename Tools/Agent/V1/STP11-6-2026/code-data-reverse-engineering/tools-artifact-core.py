#!/usr/bin/env python3
"""Safe, local, static artifact analysis primitives for the Skill Tool.

This module deliberately avoids executing inspected content, contacting networks, or
writing to an input path. It is loaded by tools-artifact-inspector.py.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import bz2
import configparser
import copy
import datetime as dt
import difflib
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import math
import os
import re
import shutil
import signal
import sqlite3
import stat
import struct
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable
from xml.etree import ElementTree

TOOL_NAME = "code-data-reverse-engineering"
TOOL_VERSION = "1.0.0"
PACKAGE_DIR = Path(__file__).resolve().parent


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class OperationCancelled(ToolError):
    def __init__(self, message: str = "Operation canceled by signal"):
        super().__init__("canceled", message)


class StructuredArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - parser plumbing
        raise ToolError("invalid_arguments", message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def nonnegative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return value


def json_load(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ToolError("missing_resource", f"Required {label} is missing", {"path": path.name}) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError("invalid_resource", f"Cannot read {label}", {"path": path.name, "reason": str(exc)}) from exc
    if not isinstance(data, dict):
        raise ToolError("invalid_resource", f"{label} must contain a JSON object", {"path": path.name})
    return data


def _validate_limit_map(limits: Any) -> dict[str, int]:
    if not isinstance(limits, dict):
        raise ToolError("invalid_resource", "Policy defaults must be an object")
    result: dict[str, int] = {}
    for key, value in limits.items():
        if not isinstance(key, str) or not isinstance(value, int) or value <= 0:
            raise ToolError("invalid_resource", "Policy limits must be positive integers", {"key": key})
        result[key] = value
    required = {
        "max_file_bytes", "max_read_bytes", "max_scan_bytes", "max_strings",
        "min_string_length", "entropy_block_bytes", "max_entropy_blocks", "max_embedded_hits",
        "max_archive_entries", "max_archive_uncompressed_bytes", "max_archive_ratio",
        "max_archive_depth", "max_nested_entry_bytes", "max_source_files",
        "max_source_file_bytes", "max_compare_bytes", "max_diff_ranges", "timeout_seconds", "chunk_bytes",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise ToolError("invalid_resource", "Policy is missing required limits", {"missing": missing})
    return result


def load_policy(custom_policy: str | None = None) -> dict[str, Any]:
    """Load the built-in policy, optionally accepting a conservative limits override.

    An external policy may lower or raise numeric defaults and may replace source-extension
    mappings. Detection regexes intentionally remain package-controlled so a hostile custom
    regex cannot turn a static scan into an unbounded regular-expression workload.
    """
    policy_path = PACKAGE_DIR / "asset-default-policy.json"
    policy = json_load(policy_path, "default policy")
    policy["defaults"] = _validate_limit_map(policy.get("defaults"))
    if not isinstance(policy.get("secret_patterns"), list) or not isinstance(policy.get("risk_rules"), list):
        raise ToolError("invalid_resource", "Default policy has invalid detection rules")
    if custom_policy:
        custom_path = require_existing_file(custom_policy, "policy")
        override = json_load(custom_path, "custom policy")
        allowed = {"defaults", "source_extensions", "source_filenames"}
        unknown = sorted(set(override) - allowed)
        if unknown:
            raise ToolError(
                "invalid_policy",
                "Custom policy may only override defaults, source_extensions, and source_filenames",
                {"unsupported_keys": unknown},
            )
        if "defaults" in override:
            if not isinstance(override["defaults"], dict):
                raise ToolError("invalid_policy", "Custom defaults must be an object")
            unknown_limits = sorted(set(override["defaults"]) - set(policy["defaults"]))
            if unknown_limits:
                raise ToolError("invalid_policy", "Custom policy contains unknown limit names", {"unknown_limits": unknown_limits})
            override_limits = _validate_limit_map({**policy["defaults"], **override["defaults"]})
            policy["defaults"] = override_limits
        for key in ("source_extensions", "source_filenames"):
            if key in override:
                if not isinstance(override[key], dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in override[key].items()
                ):
                    raise ToolError("invalid_policy", f"Custom {key} must map strings to strings")
                policy[key] = dict(override[key])
    return policy


def load_signatures() -> list[dict[str, Any]]:
    data = json_load(PACKAGE_DIR / "asset-magic-signatures.json", "magic signature catalog")
    signatures = data.get("signatures")
    if not isinstance(signatures, list):
        raise ToolError("invalid_resource", "Magic signature catalog has no signatures list")
    result: list[dict[str, Any]] = []
    for item in signatures:
        if not isinstance(item, dict):
            raise ToolError("invalid_resource", "Magic signature entry must be an object")
        try:
            raw = bytes.fromhex(str(item["hex"]))
            offset = int(item["offset"])
            name = str(item["name"])
            fmt = str(item["format"])
        except (KeyError, ValueError) as exc:
            raise ToolError("invalid_resource", "Invalid magic signature entry") from exc
        if not raw or offset < 0:
            raise ToolError("invalid_resource", "Magic signature has invalid bytes or offset", {"name": name})
        entry = dict(item)
        entry["_raw"] = raw
        entry["_offset"] = offset
        entry["name"] = name
        entry["format"] = fmt
        result.append(entry)
    return result


def require_existing_path(value: str | Path, label: str = "input") -> Path:
    path = Path(value).expanduser()
    try:
        path.stat()
    except FileNotFoundError as exc:
        raise ToolError("missing_input", f"{label.capitalize()} path does not exist", {"path": str(path)}) from exc
    except OSError as exc:
        raise ToolError("unreadable_input", f"Cannot access {label} path", {"path": str(path), "reason": str(exc)}) from exc
    return path


def require_existing_file(value: str | Path, label: str = "input") -> Path:
    path = require_existing_path(value, label)
    if not path.is_file():
        raise ToolError("invalid_input", f"{label.capitalize()} must be a regular file", {"path": str(path)})
    if path.is_symlink():
        raise ToolError("symlink_input", f"{label.capitalize()} must not be a symlink", {"path": str(path)})
    return path


def require_file_within_limit(path: Path, limits: dict[str, int], label: str = "input") -> os.stat_result:
    try:
        info = path.stat()
    except OSError as exc:
        raise ToolError("unreadable_input", f"Cannot stat {label}", {"path": str(path), "reason": str(exc)}) from exc
    if info.st_size > limits["max_file_bytes"]:
        raise ToolError(
            "input_too_large",
            f"{label.capitalize()} exceeds max_file_bytes",
            {"path": str(path), "size": info.st_size, "max_file_bytes": limits["max_file_bytes"]},
        )
    return info


class Context:
    def __init__(self, policy: dict[str, Any], args: argparse.Namespace):
        self.policy = policy
        self.args = args
        self.limits = dict(policy["defaults"])
        overrides = {
            "max_file_bytes": getattr(args, "max_file_bytes", None),
            "max_read_bytes": getattr(args, "max_read_bytes", None),
            "max_scan_bytes": getattr(args, "max_scan_bytes", None),
            "max_archive_entries": getattr(args, "max_archive_entries", None),
            "max_archive_depth": getattr(args, "max_archive_depth", None),
            "max_source_files": getattr(args, "max_source_files", None),
            "max_compare_bytes": getattr(args, "max_compare_bytes", None),
        }
        for key, value in overrides.items():
            if value is not None:
                if value > self.limits[key]:
                    raise ToolError(
                        "limit_exceeds_policy",
                        f"{key} may not exceed the active policy limit",
                        {"requested": value, "policy_limit": self.limits[key]},
                    )
                self.limits[key] = value
        timeout = getattr(args, "timeout", None)
        if timeout is None:
            timeout = self.limits["timeout_seconds"]
        if timeout > self.limits["timeout_seconds"]:
            raise ToolError(
                "limit_exceeds_policy",
                "timeout may not exceed the active policy limit",
                {"requested": timeout, "policy_limit": self.limits["timeout_seconds"]},
            )
        self.deadline = time.monotonic() + timeout if timeout else None
        self.cancel_requested = False
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        def request_cancel(_signum: int, _frame: Any) -> None:
            self.cancel_requested = True

        # Signal installation is best effort: this module can also be embedded in a non-main thread.
        try:
            signal.signal(signal.SIGINT, request_cancel)
            signal.signal(signal.SIGTERM, request_cancel)
        except (ValueError, AttributeError):
            pass

    def check(self) -> None:
        if self.cancel_requested:
            raise OperationCancelled()
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ToolError("timeout", "Operation exceeded configured timeout", {"timeout_seconds": self.timeout_seconds})

    @property
    def timeout_seconds(self) -> int:
        return int(self.limits["timeout_seconds"] if getattr(self.args, "timeout", None) is None else self.args.timeout)

    def public_limits(self) -> dict[str, int]:
        return dict(self.limits)


def file_sha256(path: Path, ctx: Context) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            ctx.check()
            block = handle.read(ctx.limits["chunk_bytes"])
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def file_hashes(path: Path, ctx: Context) -> dict[str, str]:
    hashers = {
        "md5": hashlib.md5(),
        "sha1": hashlib.sha1(),
        "sha256": hashlib.sha256(),
        "sha3_256": hashlib.sha3_256(),
        "blake2b_256": hashlib.blake2b(digest_size=32),
    }
    with path.open("rb") as handle:
        while True:
            ctx.check()
            block = handle.read(ctx.limits["chunk_bytes"])
            if not block:
                break
            for hasher in hashers.values():
                hasher.update(block)
    return {name: hasher.hexdigest() for name, hasher in hashers.items()}


def bytes_hashes(data: bytes) -> dict[str, str]:
    return {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha3_256": hashlib.sha3_256(data).hexdigest(),
        "blake2b_256": hashlib.blake2b(data, digest_size=32).hexdigest(),
    }


def read_limited(path: Path, max_bytes: int, ctx: Context) -> bytes:
    info = path.stat()
    if info.st_size > max_bytes:
        raise ToolError(
            "read_limit_exceeded",
            "Operation requires more bytes than max_read_bytes permits",
            {"path": str(path), "size": info.st_size, "max_read_bytes": max_bytes},
        )
    chunks: list[bytes] = []
    remaining = max_bytes
    with path.open("rb") as handle:
        while remaining:
            ctx.check()
            block = handle.read(min(ctx.limits["chunk_bytes"], remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
    return b"".join(chunks)


def read_prefix(path: Path, count: int, ctx: Context) -> bytes:
    with path.open("rb") as handle:
        ctx.check()
        return handle.read(count)


def safe_text_preview(data: bytes, patterns: list[dict[str, Any]], maximum: int = 160) -> str:
    text = data.decode("utf-8", errors="replace")
    text = text.replace("\x00", "\\0").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    for rule in patterns:
        try:
            expression = re.compile(str(rule["pattern"]))
            label = str(rule.get("id", "sensitive"))
            text = expression.sub(f"<redacted:{label}>", text)
        except (re.error, KeyError):
            continue
    text = "".join(char if char.isprintable() else "�" for char in text)
    return text[:maximum] + ("…" if len(text) > maximum else "")


def detect_text_encoding(data: bytes) -> dict[str, Any]:
    if data.startswith(b"\xef\xbb\xbf"):
        return {"encoding": "utf-8-sig", "confidence": "high", "reason": "UTF-8 BOM"}
    if data.startswith(b"\xff\xfe\x00\x00"):
        return {"encoding": "utf-32le", "confidence": "high", "reason": "UTF-32 little-endian BOM"}
    if data.startswith(b"\x00\x00\xfe\xff"):
        return {"encoding": "utf-32be", "confidence": "high", "reason": "UTF-32 big-endian BOM"}
    if data.startswith(b"\xff\xfe"):
        return {"encoding": "utf-16le", "confidence": "high", "reason": "UTF-16 little-endian BOM"}
    if data.startswith(b"\xfe\xff"):
        return {"encoding": "utf-16be", "confidence": "high", "reason": "UTF-16 big-endian BOM"}
    if not data:
        return {"encoding": "utf-8", "confidence": "high", "reason": "empty input"}
    try:
        data.decode("utf-8")
        nul_ratio = data.count(0) / len(data)
        if nul_ratio < 0.02:
            return {"encoding": "utf-8", "confidence": "medium", "reason": "valid UTF-8 with few NUL bytes"}
    except UnicodeDecodeError:
        pass
    even_nuls = data[1::2].count(0)
    odd_nuls = data[::2].count(0)
    half = max(1, len(data) // 2)
    if even_nuls / half > 0.3:
        return {"encoding": "utf-16le", "confidence": "low", "reason": "NUL distribution suggests UTF-16 little-endian"}
    if odd_nuls / half > 0.3:
        return {"encoding": "utf-16be", "confidence": "low", "reason": "NUL distribution suggests UTF-16 big-endian"}
    printable = sum(1 for byte in data if byte in (9, 10, 13) or 32 <= byte <= 126)
    if printable / len(data) > 0.9:
        return {"encoding": "ascii-or-single-byte", "confidence": "low", "reason": "mostly printable 7-bit bytes"}
    return {"encoding": "binary-or-unknown", "confidence": "low", "reason": "not valid UTF-8 and no strong Unicode BOM/pattern"}


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def entropy_profile(path: Path, ctx: Context) -> dict[str, Any]:
    size = path.stat().st_size
    block_size = min(ctx.limits["entropy_block_bytes"], max(1, size))
    max_blocks = ctx.limits["max_entropy_blocks"]
    if size == 0:
        return {"sampled_bytes": 0, "overall_sample_entropy_bits_per_byte": 0.0, "blocks": []}
    count = min(max_blocks, max(1, math.ceil(size / block_size)))
    if count == 1:
        offsets = [0]
    else:
        offsets = [int((size - block_size) * index / (count - 1)) for index in range(count)]
    samples: list[bytes] = []
    blocks: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for offset in offsets:
            ctx.check()
            handle.seek(offset)
            block = handle.read(block_size)
            samples.append(block)
            blocks.append({"offset": offset, "length": len(block), "entropy_bits_per_byte": round(entropy(block), 4)})
    combined = b"".join(samples)
    return {
        "sampled_bytes": len(combined),
        "sampled_regions": len(blocks),
        "overall_sample_entropy_bits_per_byte": round(entropy(combined), 4),
        "blocks": blocks,
        "interpretation": "Entropy is a compression/encryption/structure indicator only; it is not proof of encryption or packing.",
    }


def signatures_at(data: bytes, signatures: list[dict[str, Any]], base_offset: int = 0, include_weak: bool = False) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in signatures:
        if item.get("weak") and not include_weak:
            continue
        offset = int(item["_offset"])
        raw = item["_raw"]
        if offset + len(raw) > len(data) or data[offset:offset + len(raw)] != raw:
            continue
        requirement = item.get("requires_ascii_at")
        if requirement:
            required_offset = int(requirement.get("offset", -1))
            required_text = str(requirement.get("text", "")).encode("ascii", errors="strict")
            if required_offset < 0 or data[required_offset:required_offset + len(required_text)] != required_text:
                continue
        matches.append({
            "name": item["name"], "format": item["format"], "category": item.get("category", "unknown"),
            "offset": base_offset + offset,
        })
    return matches


def embedded_signatures(path: Path, signatures: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    scan_size = min(path.stat().st_size, ctx.limits["max_scan_bytes"])
    data = read_prefix(path, scan_size, ctx)
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in signatures:
        if item.get("weak") or len(item["_raw"]) < 3:
            continue
        raw = item["_raw"]
        start = 0
        while True:
            ctx.check()
            found = data.find(raw, start)
            if found < 0:
                break
            start = found + 1
            if found == int(item["_offset"]):
                continue
            requirement = item.get("requires_ascii_at")
            if requirement:
                requirement_offset = found + int(requirement.get("offset", -1))
                wanted = str(requirement.get("text", "")).encode("ascii", errors="strict")
                if requirement_offset < 0 or data[requirement_offset:requirement_offset + len(wanted)] != wanted:
                    continue
            key = (str(item["format"]), found)
            if key in seen:
                continue
            seen.add(key)
            hits.append({"offset": found, "name": item["name"], "format": item["format"], "category": item.get("category", "unknown")})
            if len(hits) >= ctx.limits["max_embedded_hits"]:
                return hits
    return sorted(hits, key=lambda entry: (entry["offset"], entry["format"]))


def extract_strings(data: bytes, ctx: Context, policy: dict[str, Any]) -> dict[str, Any]:
    minimum = ctx.limits["min_string_length"]
    maximum = ctx.limits["max_strings"]
    patterns = policy["secret_patterns"]
    results: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    def add(offset: int, encoding: str, raw: bytes) -> None:
        if len(results) >= maximum:
            return
        try:
            value = raw.decode(encoding, errors="replace")
        except LookupError:
            return
        value = safe_text_preview(value.encode("utf-8", errors="replace"), patterns, 240)
        key = (offset, value)
        if key not in seen:
            seen.add(key)
            results.append({"offset": offset, "encoding": encoding, "value": value})

    ascii_pattern = re.compile(rb"[\x20-\x7e]{" + str(minimum).encode("ascii") + rb",}")
    for match in ascii_pattern.finditer(data):
        add(match.start(), "ascii", match.group())
        if len(results) >= maximum:
            break
    if len(results) < maximum:
        le_pattern = re.compile(rb"(?:[\x20-\x7e]\x00){" + str(minimum).encode("ascii") + rb",}")
        for match in le_pattern.finditer(data):
            add(match.start(), "utf-16le", match.group())
            if len(results) >= maximum:
                break
    if len(results) < maximum:
        be_pattern = re.compile(rb"(?:\x00[\x20-\x7e]){" + str(minimum).encode("ascii") + rb",}")
        for match in be_pattern.finditer(data):
            add(match.start(), "utf-16be", match.group())
            if len(results) >= maximum:
                break
    return {"scanned_bytes": len(data), "minimum_length": minimum, "count": len(results), "truncated": len(results) >= maximum, "items": results}


def metadata_for(path: Path) -> dict[str, Any]:
    info = path.stat()
    mode = stat.filemode(info.st_mode)
    return {
        "name": path.name,
        "suffix": path.suffix.lower(),
        "size": info.st_size,
        "mode": mode,
        "modified_utc": dt.datetime.fromtimestamp(info.st_mtime, tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def read_c_string(data: bytes, offset: int, maximum: int = 4096) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset, min(len(data), offset + maximum))
    if end < 0:
        end = min(len(data), offset + maximum)
    try:
        return data[offset:end].decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return None


def require_range(data: bytes, offset: int, length: int, label: str) -> None:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise ToolError("malformed_structure", f"{label} extends beyond available data", {"offset": offset, "length": length, "available": len(data)})


def unpack_at(fmt: str, data: bytes, offset: int, label: str) -> tuple[Any, ...]:
    size = struct.calcsize(fmt)
    require_range(data, offset, size, label)
    return struct.unpack_from(fmt, data, offset)


def parse_elf(data: bytes, ctx: Context) -> dict[str, Any]:
    require_range(data, 0, 16, "ELF identification")
    if data[:4] != b"\x7fELF":
        raise ToolError("format_mismatch", "Input is not ELF")
    klass = data[4]
    byte_order = data[5]
    if klass not in (1, 2) or byte_order not in (1, 2):
        raise ToolError("malformed_structure", "ELF has unsupported class or byte order", {"class": klass, "data": byte_order})
    endian = "<" if byte_order == 1 else ">"
    bits = 32 if klass == 1 else 64
    header_fmt = endian + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH")
    fields = unpack_at(header_fmt, data, 16, "ELF header")
    (etype, machine, version, entry, phoff, shoff, flags, ehsize, phentsize, phnum, shentsize, shnum, shstrndx) = fields
    expected_ehsize = 52 if bits == 32 else 64
    expected_phentsize = 32 if bits == 32 else 56
    expected_shentsize = 40 if bits == 32 else 64
    warnings: list[str] = []
    if ehsize != expected_ehsize:
        warnings.append(f"ELF header size is {ehsize}, expected {expected_ehsize} for class")
    if phnum and phentsize < expected_phentsize:
        raise ToolError("malformed_structure", "ELF program header entry is too small", {"phentsize": phentsize})
    if shnum and shentsize < expected_shentsize:
        raise ToolError("malformed_structure", "ELF section header entry is too small", {"shentsize": shentsize})
    if phnum:
        require_range(data, phoff, phentsize * phnum, "ELF program header table")
    if shnum:
        require_range(data, shoff, shentsize * shnum, "ELF section header table")
    program_headers: list[dict[str, Any]] = []
    ph_fmt = endian + ("IIIIIIII" if bits == 32 else "IIQQQQQQ")
    for index in range(min(phnum, 512)):
        ctx.check()
        raw = unpack_at(ph_fmt, data, phoff + index * phentsize, "ELF program header")
        if bits == 32:
            ptype, offset, vaddr, paddr, filesz, memsz, pflags, align = raw
        else:
            ptype, pflags, offset, vaddr, paddr, filesz, memsz, align = raw
        program_headers.append({"index": index, "type": ptype, "offset": offset, "virtual_address": vaddr, "file_size": filesz, "memory_size": memsz, "flags": pflags, "alignment": align})
    section_headers: list[dict[str, Any]] = []
    sh_fmt = endian + ("IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ")
    for index in range(min(shnum, 4096)):
        ctx.check()
        raw = unpack_at(sh_fmt, data, shoff + index * shentsize, "ELF section header")
        if bits == 32:
            name_offset, stype, sflags, address, offset, size, link, info, addralign, entsize = raw
        else:
            name_offset, stype, sflags, address, offset, size, link, info, addralign, entsize = raw
        if offset + size > len(data) and stype != 8:  # SHT_NOBITS occupies memory but not the file.
            warnings.append(f"section {index} file range exceeds input")
        section_headers.append({"index": index, "name_offset": name_offset, "type": stype, "flags": sflags, "address": address, "offset": offset, "size": size, "link": link, "info": info, "alignment": addralign, "entry_size": entsize})
    if shnum > len(section_headers):
        warnings.append(f"section list truncated at {len(section_headers)} entries")
    if phnum > len(program_headers):
        warnings.append(f"program-header list truncated at {len(program_headers)} entries")
    string_table = b""
    if 0 <= shstrndx < len(section_headers):
        shstr = section_headers[shstrndx]
        if shstr["offset"] + shstr["size"] <= len(data):
            string_table = data[shstr["offset"]:shstr["offset"] + shstr["size"]]
    for item in section_headers:
        name = read_c_string(string_table, int(item["name_offset"])) if string_table else None
        item["name"] = name or ""
        item.pop("name_offset", None)
    dynamic_symbols: list[dict[str, Any]] = []
    imports: list[str] = []
    sym_fmt = endian + ("IIIBBH" if bits == 32 else "IBBHQQ")
    sym_size = struct.calcsize(sym_fmt)
    for section in section_headers:
        if section["type"] not in (2, 11) or not section["entry_size"]:
            continue
        if section["entry_size"] < sym_size or section["link"] >= len(section_headers):
            warnings.append(f"symbol section {section['index']} has invalid entry size or string-table link")
            continue
        linked = section_headers[section["link"]]
        if linked["offset"] + linked["size"] > len(data) or section["offset"] + section["size"] > len(data):
            warnings.append(f"symbol section {section['index']} extends beyond input")
            continue
        names = data[linked["offset"]:linked["offset"] + linked["size"]]
        limit = min(section["size"] // section["entry_size"], 1000)
        for index in range(limit):
            ctx.check()
            raw = unpack_at(sym_fmt, data, section["offset"] + index * section["entry_size"], "ELF symbol")
            if bits == 32:
                name_offset, value, size, info, other, section_index = raw
            else:
                name_offset, info, other, section_index, value, size = raw
            name = read_c_string(names, name_offset) or ""
            item = {"name": name, "value": value, "size": size, "binding": info >> 4, "type": info & 0x0F, "section_index": section_index, "table": section.get("name", "")}
            dynamic_symbols.append(item)
            if section["type"] == 11 and section_index == 0 and name:
                imports.append(name)
    return {
        "format": "ELF",
        "class_bits": bits,
        "endianness": "little" if endian == "<" else "big",
        "type": etype,
        "machine": machine,
        "version": version,
        "entry_point": entry,
        "flags": flags,
        "program_headers": program_headers,
        "sections": section_headers,
        "symbols_sample": dynamic_symbols[:1000],
        "dynamic_undefined_symbols_sample": sorted(set(imports))[:1000],
        "warnings": warnings,
    }


def rva_to_file_offset(rva: int, sections: list[dict[str, Any]], headers_size: int) -> int | None:
    if 0 <= rva < headers_size:
        return rva
    for section in sections:
        start = section["virtual_address"]
        extent = max(section["virtual_size"], section["raw_size"])
        if start <= rva < start + extent:
            return section["raw_pointer"] + (rva - start)
    return None


def parse_pe(data: bytes, ctx: Context) -> dict[str, Any]:
    require_range(data, 0, 64, "DOS header")
    if data[:2] != b"MZ":
        raise ToolError("format_mismatch", "Input is not a DOS MZ/PE file")
    pe_offset = unpack_at("<I", data, 0x3C, "e_lfanew")[0]
    require_range(data, pe_offset, 24, "PE header")
    if data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        return {"format": "DOS MZ", "pe_header_found": False, "dos_pe_offset": pe_offset, "warnings": ["MZ header found but PE signature is absent at e_lfanew"]}
    machine, section_count, timestamp, symbol_pointer, symbol_count, optional_size, characteristics = unpack_at("<HHIIIHH", data, pe_offset + 4, "COFF file header")
    optional_offset = pe_offset + 24
    require_range(data, optional_offset, optional_size, "PE optional header")
    if optional_size < 2:
        raise ToolError("malformed_structure", "PE optional header is too small")
    magic = unpack_at("<H", data, optional_offset, "PE optional magic")[0]
    if magic == 0x10B:
        bits, image_base_offset, image_base_fmt, directory_base = 32, 28, "<I", 96
    elif magic == 0x20B:
        bits, image_base_offset, image_base_fmt, directory_base = 64, 24, "<Q", 112
    else:
        raise ToolError("malformed_structure", "PE optional header has unknown magic", {"magic": hex(magic)})
    entry_point = unpack_at("<I", data, optional_offset + 16, "PE entry point")[0]
    image_base = unpack_at(image_base_fmt, data, optional_offset + image_base_offset, "PE image base")[0]
    size_of_headers = unpack_at("<I", data, optional_offset + 60, "PE SizeOfHeaders")[0]
    number_of_directories_offset = optional_offset + (92 if bits == 32 else 108)
    number_of_directories = 0
    if number_of_directories_offset + 4 <= optional_offset + optional_size:
        number_of_directories = unpack_at("<I", data, number_of_directories_offset, "PE directory count")[0]
    sections_offset = optional_offset + optional_size
    require_range(data, sections_offset, section_count * 40, "PE section table")
    sections: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index in range(min(section_count, 1024)):
        ctx.check()
        offset = sections_offset + index * 40
        name_raw = data[offset:offset + 8].split(b"\x00", 1)[0]
        name = name_raw.decode("ascii", errors="replace")
        virtual_size, virtual_address, raw_size, raw_pointer, reloc_ptr, line_ptr, reloc_count, line_count, flags = unpack_at("<IIIIIIHHI", data, offset + 8, "PE section")
        if raw_size and raw_pointer + raw_size > len(data):
            warnings.append(f"section {name or index} raw range exceeds input")
        sections.append({"index": index, "name": name, "virtual_size": virtual_size, "virtual_address": virtual_address, "raw_size": raw_size, "raw_pointer": raw_pointer, "characteristics": flags})
    directories: list[dict[str, Any]] = []
    directory_names = ["export", "import", "resource", "exception", "certificate", "base_relocation", "debug", "architecture", "global_pointer", "tls", "load_config", "bound_import", "iat", "delay_import", "com_descriptor", "reserved"]
    directory_count = min(number_of_directories, 16, max(0, (optional_size - directory_base) // 8))
    for index in range(directory_count):
        rva, size = unpack_at("<II", data, optional_offset + directory_base + index * 8, "PE data directory")
        directories.append({"index": index, "name": directory_names[index], "rva_or_file_offset": rva, "size": size})
    imports: list[dict[str, Any]] = []
    exports: list[str] = []
    if len(directories) > 1 and directories[1]["rva_or_file_offset"] and directories[1]["size"]:
        import_offset = rva_to_file_offset(directories[1]["rva_or_file_offset"], sections, size_of_headers)
        if import_offset is None:
            warnings.append("Import directory RVA does not map to a section")
        else:
            for index in range(1024):
                ctx.check()
                entry_offset = import_offset + index * 20
                if entry_offset + 20 > len(data):
                    warnings.append("Import descriptor extends beyond input")
                    break
                original_thunk, _, _, name_rva, first_thunk = unpack_at("<IIIII", data, entry_offset, "import descriptor")
                if not any((original_thunk, name_rva, first_thunk)):
                    break
                name_offset = rva_to_file_offset(name_rva, sections, size_of_headers)
                dll = read_c_string(data, name_offset) if name_offset is not None else None
                imports.append({"dll": dll or "<unresolved>", "original_first_thunk_rva": original_thunk, "first_thunk_rva": first_thunk})
    if directories and directories[0]["rva_or_file_offset"] and directories[0]["size"]:
        export_offset = rva_to_file_offset(directories[0]["rva_or_file_offset"], sections, size_of_headers)
        if export_offset is None or export_offset + 40 > len(data):
            warnings.append("Export directory RVA does not map to complete data")
        else:
            (_, _, _, _, name_rva, ordinal_base, function_count, name_count, function_rva, names_rva, ordinals_rva) = unpack_at("<IIHHIIIIIII", data, export_offset, "export directory")
            names_offset = rva_to_file_offset(names_rva, sections, size_of_headers)
            if names_offset is not None:
                for index in range(min(name_count, 1000)):
                    ctx.check()
                    ptr_offset = names_offset + index * 4
                    if ptr_offset + 4 > len(data):
                        break
                    export_name_rva = unpack_at("<I", data, ptr_offset, "export name pointer")[0]
                    export_name_offset = rva_to_file_offset(export_name_rva, sections, size_of_headers)
                    name = read_c_string(data, export_name_offset) if export_name_offset is not None else None
                    if name:
                        exports.append(name)
            if name_count > 1000:
                warnings.append("export name list truncated at 1000 entries")
    return {
        "format": "PE",
        "bits": bits,
        "machine": machine,
        "timestamp_utc": dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if timestamp else None,
        "characteristics": characteristics,
        "entry_point_rva": entry_point,
        "image_base": image_base,
        "size_of_headers": size_of_headers,
        "coff_symbol_pointer": symbol_pointer,
        "coff_symbol_count": symbol_count,
        "sections": sections,
        "data_directories": directories,
        "imports": imports,
        "exports_sample": exports,
        "warnings": warnings,
    }


def parse_macho(data: bytes, ctx: Context) -> dict[str, Any]:
    require_range(data, 0, 4, "Mach-O magic")
    raw_magic = data[:4]
    fat_magic = {
        b"\xca\xfe\xba\xbe": (">", False),
        b"\xbe\xba\xfe\xca": ("<", False),
        b"\xca\xfe\xba\xbf": (">", True),
        b"\xbf\xba\xfe\xca": ("<", True),
    }
    if raw_magic in fat_magic:
        endian, fat64 = fat_magic[raw_magic]
        nfat = unpack_at(endian + "I", data, 4, "fat architecture count")[0]
        entry_size = 32 if fat64 else 20
        require_range(data, 8, nfat * entry_size, "fat architecture table")
        architectures: list[dict[str, Any]] = []
        for index in range(min(nfat, 128)):
            ctx.check()
            offset = 8 + index * entry_size
            if fat64:
                cputype, cpusubtype, arch_offset, arch_size, align, reserved = unpack_at(endian + "IIQQII", data, offset, "fat64 architecture")
            else:
                cputype, cpusubtype, arch_offset, arch_size, align = unpack_at(endian + "IIIII", data, offset, "fat architecture")
            architectures.append({"index": index, "cpu_type": cputype, "cpu_subtype": cpusubtype, "offset": arch_offset, "size": arch_size, "alignment_power": align})
        return {"format": "Mach-O universal binary", "endianness": "big" if endian == ">" else "little", "fat64": fat64, "architectures": architectures, "warnings": ["architecture list truncated at 128 entries"] if nfat > 128 else []}
    magic_map = {
        b"\xce\xfa\xed\xfe": ("<", 32),
        b"\xcf\xfa\xed\xfe": ("<", 64),
        b"\xfe\xed\xfa\xce": (">", 32),
        b"\xfe\xed\xfa\xcf": (">", 64),
    }
    if raw_magic not in magic_map:
        raise ToolError("format_mismatch", "Input is not recognized Mach-O")
    endian, bits = magic_map[raw_magic]
    header_fmt = endian + ("IiiIIII" if bits == 32 else "IiiIIIII")
    raw = unpack_at(header_fmt, data, 0, "Mach-O header")
    if bits == 32:
        magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags = raw
        reserved = None
        header_size = 28
    else:
        magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = raw
        header_size = 32
    require_range(data, header_size, sizeofcmds, "Mach-O load-command region")
    commands: list[dict[str, Any]] = []
    labels = {
        0x1: "LC_SEGMENT", 0x2: "LC_SYMTAB", 0xC: "LC_LOAD_DYLIB", 0xD: "LC_ID_DYLIB", 0x19: "LC_SEGMENT_64",
        0x1B: "LC_UUID", 0x1D: "LC_CODE_SIGNATURE", 0x1F: "LC_REEXPORT_DYLIB", 0x22: "LC_DYLD_INFO",
        0x80000022: "LC_DYLD_INFO_ONLY", 0x24: "LC_VERSION_MIN_MACOSX", 0x25: "LC_VERSION_MIN_IPHONEOS",
        0x2A: "LC_SOURCE_VERSION", 0x32: "LC_BUILD_VERSION", 0x80000028: "LC_MAIN",
    }
    cursor = header_size
    end = header_size + sizeofcmds
    for index in range(min(ncmds, 2048)):
        ctx.check()
        if cursor + 8 > end:
            raise ToolError("malformed_structure", "Mach-O load command header is truncated", {"command_index": index})
        command, command_size = unpack_at(endian + "II", data, cursor, "Mach-O load command")
        if command_size < 8 or cursor + command_size > end:
            raise ToolError("malformed_structure", "Mach-O load command has invalid size", {"command_index": index, "command_size": command_size})
        entry: dict[str, Any] = {"index": index, "command": hex(command), "name": labels.get(command, "unknown"), "size": command_size}
        if command in (0x1, 0x19) and command_size >= (56 if bits == 32 else 72):
            segment_name = data[cursor + 8:cursor + 24].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            entry["segment_name"] = segment_name
        if command == 0x1B and command_size >= 24:
            entry["uuid"] = data[cursor + 8:cursor + 24].hex()
        commands.append(entry)
        cursor += command_size
    warnings: list[str] = []
    if ncmds > len(commands):
        warnings.append(f"load-command list truncated at {len(commands)} entries")
    return {"format": "Mach-O", "bits": bits, "endianness": "little" if endian == "<" else "big", "cpu_type": cputype, "cpu_subtype": cpusubtype, "file_type": filetype, "flags": flags, "reserved": reserved, "load_commands": commands, "warnings": warnings}


def read_uleb128(data: bytes, offset: int, limit: int = 10) -> tuple[int, int]:
    value = 0
    shift = 0
    for count in range(limit):
        require_range(data, offset + count, 1, "LEB128 value")
        byte = data[offset + count]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset + count + 1
        shift += 7
    raise ToolError("malformed_structure", "LEB128 value is too long")


def parse_wasm(data: bytes, ctx: Context) -> dict[str, Any]:
    require_range(data, 0, 8, "WebAssembly header")
    if data[:4] != b"\x00asm":
        raise ToolError("format_mismatch", "Input is not a WebAssembly module")
    version = struct.unpack_from("<I", data, 4)[0]
    names = {0: "custom", 1: "type", 2: "import", 3: "function", 4: "table", 5: "memory", 6: "global", 7: "export", 8: "start", 9: "element", 10: "code", 11: "data", 12: "data_count", 13: "tag"}
    sections: list[dict[str, Any]] = []
    cursor = 8
    while cursor < len(data) and len(sections) < 4096:
        ctx.check()
        section_offset = cursor
        section_id = data[cursor]
        cursor += 1
        length, cursor = read_uleb128(data, cursor)
        require_range(data, cursor, length, "WebAssembly section")
        entry: dict[str, Any] = {"offset": section_offset, "id": section_id, "name": names.get(section_id, "unknown"), "payload_length": length}
        if section_id == 0 and length:
            custom_name_length, custom_start = read_uleb128(data, cursor)
            if custom_start + custom_name_length <= cursor + length:
                entry["custom_name"] = data[custom_start:custom_start + custom_name_length].decode("utf-8", errors="replace")
        sections.append(entry)
        cursor += length
    warnings = ["section list truncated at 4096 entries"] if cursor < len(data) else []
    return {"format": "WebAssembly", "version": version, "sections": sections, "warnings": warnings}


def parse_java_class(data: bytes) -> dict[str, Any]:
    require_range(data, 0, 10, "Java class header")
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ToolError("format_mismatch", "Input is not a Java class")
    minor, major, constant_pool_count = unpack_at(">HHH", data, 4, "Java class header")
    return {"format": "Java class", "minor_version": minor, "major_version": major, "constant_pool_count": constant_pool_count, "warnings": ["The standard-library parser reports the header only; it does not execute or decompile bytecode."]}


def parse_dex(data: bytes) -> dict[str, Any]:
    require_range(data, 0, 112, "DEX header")
    if not data.startswith(b"dex\n"):
        raise ToolError("format_mismatch", "Input is not a DEX file")
    magic = data[:8].rstrip(b"\x00").decode("ascii", errors="replace")
    checksum = unpack_at("<I", data, 8, "DEX checksum")[0]
    file_size, header_size, endian_tag, link_size, link_off, map_off = unpack_at("<IIIIII", data, 32, "DEX header fields")
    string_ids_size, string_ids_off, type_ids_size, type_ids_off = unpack_at("<IIII", data, 56, "DEX ID fields")
    proto_ids_size, proto_ids_off, field_ids_size, field_ids_off = unpack_at("<IIII", data, 72, "DEX proto/field IDs")
    method_ids_size, method_ids_off, class_defs_size, class_defs_off = unpack_at("<IIII", data, 88, "DEX method/class IDs")
    data_size, data_off = unpack_at("<II", data, 104, "DEX data section")
    warnings: list[str] = []
    if file_size != len(data):
        warnings.append(f"header file_size is {file_size}, available bytes are {len(data)}")
    if header_size != 112:
        warnings.append(f"header_size is {header_size}, expected 112")
    return {"format": "DEX", "magic": magic, "checksum_adler32": f"{checksum:08x}", "signature_sha1": data[12:32].hex(), "file_size": file_size, "header_size": header_size, "endian_tag": hex(endian_tag), "link": {"size": link_size, "offset": link_off}, "map_offset": map_off, "string_ids": {"count": string_ids_size, "offset": string_ids_off}, "type_ids": {"count": type_ids_size, "offset": type_ids_off}, "proto_ids": {"count": proto_ids_size, "offset": proto_ids_off}, "field_ids": {"count": field_ids_size, "offset": field_ids_off}, "method_ids": {"count": method_ids_size, "offset": method_ids_off}, "class_defs": {"count": class_defs_size, "offset": class_defs_off}, "data": {"size": data_size, "offset": data_off}, "warnings": warnings}


def parse_sqlite_header(data: bytes) -> dict[str, Any]:
    require_range(data, 0, 100, "SQLite header")
    if not data.startswith(b"SQLite format 3\x00"):
        raise ToolError("format_mismatch", "Input is not a SQLite database")
    page_size = struct.unpack_from(">H", data, 16)[0]
    if page_size == 1:
        page_size = 65536
    encoding_code = struct.unpack_from(">I", data, 56)[0]
    encodings = {1: "UTF-8", 2: "UTF-16le", 3: "UTF-16be"}
    return {
        "format": "SQLite 3",
        "page_size": page_size,
        "write_version": data[18],
        "read_version": data[19],
        "reserved_bytes_per_page": data[20],
        "file_change_counter": struct.unpack_from(">I", data, 24)[0],
        "page_count": struct.unpack_from(">I", data, 28)[0],
        "freelist": {"first_trunk_page": struct.unpack_from(">I", data, 32)[0], "page_count": struct.unpack_from(">I", data, 36)[0]},
        "schema_format": struct.unpack_from(">I", data, 44)[0],
        "text_encoding": encodings.get(encoding_code, f"unknown ({encoding_code})"),
        "user_version": struct.unpack_from(">I", data, 60)[0],
        "application_id": struct.unpack_from(">I", data, 68)[0],
        "sqlite_version_number": struct.unpack_from(">I", data, 96)[0],
    }


def parse_ar(data: bytes, ctx: Context) -> dict[str, Any]:
    if not data.startswith(b"!<arch>\n"):
        raise ToolError("format_mismatch", "Input is not an ar archive")
    cursor = 8
    members: list[dict[str, Any]] = []
    warnings: list[str] = []
    while cursor < len(data) and len(members) < 4096:
        ctx.check()
        if cursor + 60 > len(data):
            warnings.append("truncated ar member header")
            break
        header = data[cursor:cursor + 60]
        if header[58:60] != b"`\n":
            warnings.append(f"invalid ar member trailer at offset {cursor}")
            break
        name = header[0:16].decode("ascii", errors="replace").strip()
        size_text = header[48:58].decode("ascii", errors="replace").strip()
        try:
            member_size = int(size_text)
        except ValueError:
            warnings.append(f"invalid member size at offset {cursor}")
            break
        data_offset = cursor + 60
        if data_offset + member_size > len(data):
            warnings.append(f"member {name} extends beyond input")
            break
        members.append({"offset": cursor, "name": name, "size": member_size})
        cursor = data_offset + member_size + (member_size % 2)
    if cursor < len(data) and len(members) >= 4096:
        warnings.append("member list truncated at 4096 entries")
    return {"format": "ar archive", "members": members, "warnings": warnings}


def parse_known_format(data: bytes, declared_format: str | None, ctx: Context) -> dict[str, Any] | None:
    fmt = declared_format
    if fmt is None:
        if data.startswith(b"\x7fELF"):
            fmt = "elf"
        elif data.startswith(b"MZ"):
            fmt = "pe"
        elif data[:4] in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
            # CAFEBABE ambiguously denotes a Java class. A class header has a plausible minor/major version.
            fmt = "java-class" if data.startswith(b"\xca\xfe\xba\xbe") and len(data) >= 10 and struct.unpack_from(">H", data, 6)[0] < 1000 else "macho"
        elif data.startswith(b"\x00asm"):
            fmt = "wasm"
        elif data.startswith(b"dex\n"):
            fmt = "dex"
        elif data.startswith(b"SQLite format 3\x00"):
            fmt = "sqlite"
        elif data.startswith(b"!<arch>\n"):
            fmt = "ar"
        else:
            return None
    parsers = {
        "elf": parse_elf,
        "pe": parse_pe,
        "macho": parse_macho,
        "wasm": parse_wasm,
        "dex": parse_dex,
        "ar": parse_ar,
    }
    if fmt == "java-class":
        return parse_java_class(data)
    if fmt == "sqlite":
        return parse_sqlite_header(data)
    parser = parsers.get(fmt)
    return parser(data, ctx) if parser else None


def normalize_archive_path(name: str) -> tuple[str | None, str | None]:
    """Return a safe relative POSIX path or an explanation of why it is unsafe."""
    if not isinstance(name, str) or not name:
        return None, "empty archive path"
    candidate = name.replace("\\", "/")
    if candidate.startswith("/") or candidate.startswith("//"):
        return None, "absolute archive path"
    if re.match(r"^[A-Za-z]:", candidate):
        return None, "drive-qualified archive path"
    path = PurePosixPath(candidate)
    if any(part in ("", ".", "..") for part in path.parts):
        return None, "path traversal or empty component"
    return str(path), None


def zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def zip_entry_record(info: zipfile.ZipInfo, duplicate: bool) -> dict[str, Any]:
    normalized, path_problem = normalize_archive_path(info.filename.rstrip("/")) if not info.is_dir() else (info.filename.rstrip("/"), None)
    compressed = info.compress_size
    uncompressed = info.file_size
    ratio = (uncompressed / compressed) if compressed else (float("inf") if uncompressed else 1.0)
    hazards: list[str] = []
    if path_problem:
        hazards.append(path_problem)
    if zip_entry_is_symlink(info):
        hazards.append("symlink entry")
    if info.flag_bits & 0x1:
        hazards.append("encrypted entry")
    if duplicate:
        hazards.append("duplicate normalized path")
    return {"path": info.filename, "normalized_path": normalized, "type": "directory" if info.is_dir() else "file", "compressed_size": compressed, "uncompressed_size": uncompressed, "compression_method": info.compress_type, "crc32": f"{info.CRC:08x}", "compression_ratio": round(ratio, 3) if math.isfinite(ratio) else "infinite", "hazards": hazards}


def archive_limits_check(entries: int, uncompressed: int, ctx: Context) -> None:
    if entries > ctx.limits["max_archive_entries"]:
        raise ToolError("archive_limit_exceeded", "Archive entry count exceeds max_archive_entries", {"entries": entries, "max_archive_entries": ctx.limits["max_archive_entries"]})
    if uncompressed > ctx.limits["max_archive_uncompressed_bytes"]:
        raise ToolError("archive_limit_exceeded", "Archive declared uncompressed size exceeds policy", {"uncompressed_bytes": uncompressed, "max_archive_uncompressed_bytes": ctx.limits["max_archive_uncompressed_bytes"]})


def inspect_zip(source: Path | BinaryIO, ctx: Context, recursive: bool = False, depth: int = 0) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ToolError("invalid_archive", "Cannot open ZIP archive", {"reason": str(exc)}) from exc
    with archive:
        infos = archive.infolist()
        total = sum(item.file_size for item in infos)
        archive_limits_check(len(infos), total, ctx)
        normalized_seen: set[str] = set()
        records: list[dict[str, Any]] = []
        unsafe_count = 0
        ratio_count = 0
        for info in infos:
            ctx.check()
            normalized, _ = normalize_archive_path(info.filename.rstrip("/")) if not info.is_dir() else (info.filename.rstrip("/"), None)
            duplicate = bool(normalized and normalized in normalized_seen)
            if normalized:
                normalized_seen.add(normalized)
            record = zip_entry_record(info, duplicate)
            ratio = info.file_size / info.compress_size if info.compress_size else (float("inf") if info.file_size else 1.0)
            if ratio > ctx.limits["max_archive_ratio"]:
                record["hazards"].append("compression ratio exceeds policy")
                ratio_count += 1
            if record["hazards"]:
                unsafe_count += 1
            records.append(record)
        nested: list[dict[str, Any]] = []
        if recursive and depth < ctx.limits["max_archive_depth"]:
            for info in infos:
                ctx.check()
                if info.is_dir() or info.file_size > ctx.limits["max_nested_entry_bytes"] or info.flag_bits & 0x1:
                    continue
                try:
                    with archive.open(info, "r") as handle:
                        content = handle.read(ctx.limits["max_nested_entry_bytes"] + 1)
                except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                    nested.append({"path": info.filename, "status": "unreadable", "reason": str(exc)})
                    continue
                if len(content) > ctx.limits["max_nested_entry_bytes"]:
                    nested.append({"path": info.filename, "status": "skipped", "reason": "nested entry exceeds max_nested_entry_bytes"})
                    continue
                child = inspect_archive_bytes(content, ctx, recursive=True, depth=depth + 1)
                if child:
                    nested.append({"path": info.filename, "status": "recognized", "archive": child})
        elif recursive and depth >= ctx.limits["max_archive_depth"]:
            nested.append({"status": "limit", "reason": "max_archive_depth reached"})
        return {"format": "ZIP", "entry_count": len(infos), "declared_uncompressed_bytes": total, "unsafe_or_suspicious_entry_count": unsafe_count, "high_ratio_entry_count": ratio_count, "entries": records, "nested_archives": nested}


def tar_entry_record(member: tarfile.TarInfo, duplicate: bool) -> dict[str, Any]:
    normalized, path_problem = normalize_archive_path(member.name.rstrip("/")) if member.name else (None, "empty archive path")
    hazards: list[str] = []
    if path_problem:
        hazards.append(path_problem)
    if member.issym() or member.islnk():
        hazards.append("link entry")
    if member.isdev() or member.isfifo():
        hazards.append("device or FIFO entry")
    if duplicate:
        hazards.append("duplicate normalized path")
    entry_type = "directory" if member.isdir() else "file" if member.isfile() else "other"
    return {"path": member.name, "normalized_path": normalized, "type": entry_type, "size": member.size, "mode": oct(member.mode), "link_target": member.linkname or None, "hazards": hazards}


def inspect_tar(source: Path | BinaryIO, ctx: Context, recursive: bool = False, depth: int = 0) -> dict[str, Any]:
    try:
        archive = tarfile.open(name=str(source) if isinstance(source, Path) else None, fileobj=None if isinstance(source, Path) else source, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ToolError("invalid_archive", "Cannot open TAR archive", {"reason": str(exc)}) from exc
    with archive:
        members = archive.getmembers()
        total = sum(member.size for member in members if member.isfile())
        archive_limits_check(len(members), total, ctx)
        seen: set[str] = set()
        records: list[dict[str, Any]] = []
        unsafe_count = 0
        for member in members:
            ctx.check()
            normalized, _ = normalize_archive_path(member.name.rstrip("/")) if member.name else (None, "empty archive path")
            duplicate = bool(normalized and normalized in seen)
            if normalized:
                seen.add(normalized)
            record = tar_entry_record(member, duplicate)
            if record["hazards"]:
                unsafe_count += 1
            records.append(record)
        nested: list[dict[str, Any]] = []
        if recursive and depth < ctx.limits["max_archive_depth"]:
            for member in members:
                ctx.check()
                if not member.isfile() or member.size > ctx.limits["max_nested_entry_bytes"]:
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                with handle:
                    content = handle.read(ctx.limits["max_nested_entry_bytes"] + 1)
                if len(content) > ctx.limits["max_nested_entry_bytes"]:
                    nested.append({"path": member.name, "status": "skipped", "reason": "nested entry exceeds max_nested_entry_bytes"})
                    continue
                child = inspect_archive_bytes(content, ctx, recursive=True, depth=depth + 1)
                if child:
                    nested.append({"path": member.name, "status": "recognized", "archive": child})
        elif recursive and depth >= ctx.limits["max_archive_depth"]:
            nested.append({"status": "limit", "reason": "max_archive_depth reached"})
        return {"format": "TAR", "entry_count": len(members), "declared_uncompressed_bytes": total, "unsafe_or_suspicious_entry_count": unsafe_count, "entries": records, "nested_archives": nested}


def inspect_archive_bytes(data: bytes, ctx: Context, recursive: bool = False, depth: int = 0) -> dict[str, Any] | None:
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        stream.seek(0)
        return inspect_zip(stream, ctx, recursive=recursive, depth=depth)
    # tarfile.is_tarfile accepts only a name, not a file-like object. Probe the bounded
    # in-memory data by opening it directly, then rewind for the actual inventory.
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
            pass
    except (OSError, tarfile.TarError, EOFError):
        return None
    return inspect_tar(io.BytesIO(data), ctx, recursive=recursive, depth=depth)


def identify_archive(path: Path) -> str | None:
    if zipfile.is_zipfile(path):
        return "zip"
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except (OSError, tarfile.TarError):
        return None
    return None


def decompress_validate(path: Path, kind: str, ctx: Context) -> dict[str, Any]:
    openers = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}
    opener = openers.get(kind)
    if opener is None:
        raise ToolError("unsupported_operation", "Unsupported compression validator", {"kind": kind})
    produced = 0
    digest = hashlib.sha256()
    try:
        with opener(path, "rb") as handle:
            while True:
                ctx.check()
                block = handle.read(ctx.limits["chunk_bytes"])
                if not block:
                    break
                produced += len(block)
                if produced > ctx.limits["max_archive_uncompressed_bytes"]:
                    raise ToolError("archive_limit_exceeded", "Decompressed output exceeds policy", {"max_archive_uncompressed_bytes": ctx.limits["max_archive_uncompressed_bytes"]})
                digest.update(block)
    except (OSError, EOFError, lzma.LZMAError) as exc:
        raise ToolError("invalid_compression", f"{kind} stream failed validation", {"reason": str(exc)}) from exc
    return {"format": kind, "decompressed_bytes": produced, "decompressed_sha256": digest.hexdigest(), "validated": True}


def stream_copy_and_hash(source: BinaryIO, destination: BinaryIO, ctx: Context, expected_size: int | None = None) -> tuple[int, str]:
    hasher = hashlib.sha256()
    copied = 0
    while True:
        ctx.check()
        block = source.read(ctx.limits["chunk_bytes"])
        if not block:
            break
        copied += len(block)
        if copied > ctx.limits["max_archive_uncompressed_bytes"]:
            raise ToolError("archive_limit_exceeded", "Extracted data exceeds policy")
        destination.write(block)
        hasher.update(block)
    if expected_size is not None and copied != expected_size:
        raise ToolError("invalid_archive", "Extracted entry length differs from archive metadata", {"expected": expected_size, "actual": copied})
    return copied, hasher.hexdigest()


def write_file_from_bytes(destination: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def safe_destination(root: Path, normalized: str) -> Path:
    destination = root.joinpath(*PurePosixPath(normalized).parts)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ToolError("unsafe_archive_path", "Archive entry escapes extraction directory", {"path": normalized}) from exc
    return destination


def extract_archive(path: Path, destination: Path, ctx: Context) -> dict[str, Any]:
    if destination.exists():
        raise ToolError("output_exists", "Extraction destination must not already exist", {"path": str(destination)})
    parent = destination.parent
    if not parent.exists() or not parent.is_dir():
        raise ToolError("invalid_output", "Extraction destination parent does not exist", {"path": str(parent)})
    archive_kind = identify_archive(path)
    if archive_kind is None:
        raise ToolError("unsupported_archive", "Safe extraction supports ZIP and TAR-family archives only", {"path": str(path)})
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.extract-", dir=parent))
    records: list[dict[str, Any]] = []
    try:
        if archive_kind == "zip":
            with zipfile.ZipFile(path, "r") as archive:
                infos = archive.infolist()
                archive_limits_check(len(infos), sum(item.file_size for item in infos), ctx)
                seen: set[str] = set()
                for info in infos:
                    ctx.check()
                    normalized, problem = normalize_archive_path(info.filename.rstrip("/"))
                    if info.is_dir():
                        if problem:
                            raise ToolError("unsafe_archive_path", "Archive directory path is unsafe", {"path": info.filename, "reason": problem})
                        safe_destination(staging, normalized).mkdir(parents=True, exist_ok=True)
                        continue
                    if problem or normalized is None:
                        raise ToolError("unsafe_archive_path", "Archive file path is unsafe", {"path": info.filename, "reason": problem})
                    if normalized in seen:
                        raise ToolError("unsafe_archive_path", "Archive contains duplicate output path", {"path": info.filename})
                    seen.add(normalized)
                    if zip_entry_is_symlink(info) or (info.flag_bits & 0x1):
                        raise ToolError("unsafe_archive_entry", "Archive contains a symlink or encrypted entry", {"path": info.filename})
                    ratio = info.file_size / info.compress_size if info.compress_size else (float("inf") if info.file_size else 1.0)
                    if ratio > ctx.limits["max_archive_ratio"]:
                        raise ToolError("archive_limit_exceeded", "Archive entry compression ratio exceeds policy", {"path": info.filename, "ratio": ratio, "max_archive_ratio": ctx.limits["max_archive_ratio"]})
                    target = safe_destination(staging, normalized)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source:
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        if hasattr(os, "O_NOFOLLOW"):
                            flags |= os.O_NOFOLLOW
                        descriptor = os.open(target, flags, 0o600)
                        try:
                            with os.fdopen(descriptor, "wb") as output:
                                length, digest = stream_copy_and_hash(source, output, ctx, info.file_size)
                                output.flush()
                                os.fsync(output.fileno())
                        except BaseException:
                            try:
                                target.unlink(missing_ok=True)
                            except OSError:
                                pass
                            raise
                    records.append({"path": normalized, "size": length, "sha256": digest})
        else:
            with tarfile.open(path, mode="r:*") as archive:
                members = archive.getmembers()
                archive_limits_check(len(members), sum(member.size for member in members if member.isfile()), ctx)
                seen = set()
                for member in members:
                    ctx.check()
                    normalized, problem = normalize_archive_path(member.name.rstrip("/")) if member.name else (None, "empty archive path")
                    if problem or normalized is None:
                        raise ToolError("unsafe_archive_path", "Archive member path is unsafe", {"path": member.name, "reason": problem})
                    if member.isdir():
                        safe_destination(staging, normalized).mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        raise ToolError("unsafe_archive_entry", "Archive contains unsupported link, device, or special entry", {"path": member.name, "type": member.type.decode("ascii", errors="replace")})
                    if normalized in seen:
                        raise ToolError("unsafe_archive_path", "Archive contains duplicate output path", {"path": member.name})
                    seen.add(normalized)
                    if member.size > ctx.limits["max_archive_uncompressed_bytes"]:
                        raise ToolError("archive_limit_exceeded", "Archive member size exceeds policy", {"path": member.name})
                    source = archive.extractfile(member)
                    if source is None:
                        raise ToolError("invalid_archive", "Unable to read archive member", {"path": member.name})
                    target = safe_destination(staging, normalized)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source:
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        if hasattr(os, "O_NOFOLLOW"):
                            flags |= os.O_NOFOLLOW
                        descriptor = os.open(target, flags, 0o600)
                        try:
                            with os.fdopen(descriptor, "wb") as output:
                                length, digest = stream_copy_and_hash(source, output, ctx, member.size)
                                output.flush()
                                os.fsync(output.fileno())
                        except BaseException:
                            try:
                                target.unlink(missing_ok=True)
                            except OSError:
                                pass
                            raise
                    records.append({"path": normalized, "size": length, "sha256": digest})
        manifest = {"tool": TOOL_NAME, "version": TOOL_VERSION, "source_archive": str(path), "created_utc": utc_now(), "entries": records}
        write_file_from_bytes(staging / "extraction-manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"destination": str(destination), "entry_count": len(records), "entries": records, "manifest": str(destination / "extraction-manifest.json")}


def source_language(path: Path, policy: dict[str, Any]) -> str | None:
    suffix = path.suffix.lower()
    if suffix in policy.get("source_extensions", {}):
        return policy["source_extensions"][suffix]
    return policy.get("source_filenames", {}).get(path.name.lower())


def read_source_text(path: Path, ctx: Context, policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    size = path.stat().st_size
    if size > ctx.limits["max_source_file_bytes"]:
        raise ToolError("source_file_too_large", "Source file exceeds max_source_file_bytes", {"path": str(path), "size": size, "max_source_file_bytes": ctx.limits["max_source_file_bytes"]})
    raw = read_limited(path, ctx.limits["max_source_file_bytes"], ctx)
    encoding = detect_text_encoding(raw)
    selected = encoding["encoding"]
    codec = selected if selected not in ("binary-or-unknown", "ascii-or-single-byte") else "utf-8"
    try:
        return raw.decode(codec, errors="replace"), encoding
    except LookupError:
        return raw.decode("utf-8", errors="replace"), {"encoding": "utf-8", "confidence": "low", "reason": "fallback decoder"}


def find_line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def source_imports(text: str, language: str | None) -> list[str]:
    patterns: list[re.Pattern[str]] = []
    if language == "python":
        patterns = [re.compile(r"(?m)^\s*import\s+([^#\n]+)"), re.compile(r"(?m)^\s*from\s+([A-Za-z_][\w.]*)\s+import\b")]
    elif language in ("javascript", "typescript", "vue"):
        patterns = [re.compile(r"(?m)\bimport\s+(?:[^;\n]*?\s+from\s+)?['\"]([^'\"]+)['\"]"), re.compile(r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")]
    elif language in ("c", "cpp", "objective-c"):
        patterns = [re.compile(r"(?m)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]")]
    elif language in ("java", "kotlin", "scala"):
        patterns = [re.compile(r"(?m)^\s*import\s+([\w.*]+)")]
    elif language == "go":
        patterns = [re.compile(r"(?m)^\s*import\s*(?:\(\s*)?['\"]([^'\"]+)['\"]"), re.compile(r"(?m)^\s*['\"]([^'\"]+)['\"]\s*(?://.*)?$")]
    elif language == "rust":
        patterns = [re.compile(r"(?m)^\s*use\s+([^;\n]+)")]
    elif language == "csharp":
        patterns = [re.compile(r"(?m)^\s*using\s+([\w.]+)")]
    elif language == "shell":
        patterns = [re.compile(r"(?m)^\s*(?:source|\.)\s+([^\s#;]+)")]
    elif language == "sql":
        patterns = [re.compile(r"(?i)\b(?:from|join|into|update)\s+([A-Za-z_][\w.$]*)")]
    found: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = next((group for group in match.groups() if group), "").strip()
            if value:
                found.add(value)
    return sorted(found)[:1000]


def source_definitions(text: str, language: str | None) -> list[dict[str, Any]]:
    if language == "python":
        pattern = re.compile(r"(?m)^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
    elif language in ("javascript", "typescript", "vue"):
        pattern = re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(")
    elif language in ("c", "cpp", "objective-c", "java", "kotlin", "csharp", "swift", "rust", "go"):
        pattern = re.compile(r"(?m)^\s*(?:public|private|protected|static|virtual|inline|async|extern|unsafe|final|override|mut|pub|func|fn|fun|class|struct|interface|enum|[A-Za-z_][\w:<>\[\],*&\s]+)*\s+([A-Za-z_]\w*)\s*\([^;\n{}]*\)\s*(?:\{|=>)")
    else:
        return []
    definitions: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        groups = [group for group in match.groups() if group]
        if not groups:
            continue
        name = groups[-1]
        kind = groups[0] if language == "python" and groups[0] in ("def", "class") else "definition"
        definitions.append({"name": name, "kind": kind, "line": find_line_number(text, match.start())})
        if len(definitions) >= 1000:
            break
    return definitions


def scan_source_text(text: str, language: str | None, path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for rule in policy["secret_patterns"]:
        try:
            pattern = re.compile(str(rule["pattern"]))
        except re.error:
            continue
        for match in pattern.finditer(text):
            findings.append({"kind": "possible_sensitive_material", "rule": rule.get("id"), "severity": rule.get("severity", "medium"), "path": str(path), "line": find_line_number(text, match.start()), "evidence": "Value redacted by policy."})
            if len(findings) >= 200:
                break
    for rule in policy["risk_rules"]:
        languages = rule.get("languages", [])
        if language and languages and language not in languages:
            continue
        try:
            pattern = re.compile(str(rule["pattern"]))
        except re.error:
            continue
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            evidence = safe_text_preview(text[line_start:line_end].encode("utf-8", errors="replace"), policy["secret_patterns"], 220)
            findings.append({"kind": "static_risk_indicator", "rule": rule.get("id"), "severity": rule.get("severity", "medium"), "path": str(path), "line": find_line_number(text, match.start()), "message": rule.get("message"), "evidence": evidence})
            if len(findings) >= 400:
                break
    return {"language": language, "imports": source_imports(text, language), "definitions": source_definitions(text, language), "findings": findings}


def source_paths(input_path: Path, ctx: Context, policy: dict[str, Any], include_hidden: bool) -> tuple[list[Path], list[dict[str, Any]]]:
    skipped: list[dict[str, Any]] = []
    if input_path.is_file():
        if not source_language(input_path, policy):
            raise ToolError("unsupported_source", "Input is not a recognized source or build/configuration file", {"path": str(input_path)})
        return [input_path], skipped
    if not input_path.is_dir():
        raise ToolError("invalid_input", "Source scan input must be a file or directory", {"path": str(input_path)})
    paths: list[Path] = []
    for root, dirs, files in os.walk(input_path, followlinks=False):
        ctx.check()
        root_path = Path(root)
        dirs[:] = sorted(directory for directory in dirs if include_hidden or not directory.startswith("."))
        for filename in sorted(files):
            ctx.check()
            if not include_hidden and filename.startswith("."):
                continue
            candidate = root_path / filename
            if candidate.is_symlink():
                skipped.append({"path": str(candidate), "reason": "symlink skipped"})
                continue
            if not candidate.is_file() or not source_language(candidate, policy):
                continue
            if len(paths) >= ctx.limits["max_source_files"]:
                skipped.append({"path": str(input_path), "reason": "max_source_files reached"})
                return paths, skipped
            paths.append(candidate)
    return paths, skipped


def analyze_source(input_path: Path, ctx: Context, policy: dict[str, Any], include_hidden: bool = False) -> dict[str, Any]:
    paths, skipped = source_paths(input_path, ctx, policy, include_hidden)
    files: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    dependency_edges: list[dict[str, Any]] = []
    language_counts: Counter[str] = Counter()
    for path in paths:
        ctx.check()
        language = source_language(path, policy)
        try:
            text, encoding = read_source_text(path, ctx, policy)
        except ToolError as exc:
            skipped.append({"path": str(path), "reason": exc.code})
            continue
        scan = scan_source_text(text, language, path, policy)
        language_counts[language or "unknown"] += 1
        file_record = {"path": str(path), "encoding": encoding, "language": language, "size": path.stat().st_size, "imports": scan["imports"], "definitions": scan["definitions"], "finding_count": len(scan["findings"])}
        files.append(file_record)
        all_findings.extend(scan["findings"])
        dependency_edges.extend({"from": str(path), "to": dependency, "relation": "import"} for dependency in scan["imports"])
    return {"file_count": len(files), "language_counts": dict(sorted(language_counts.items())), "files": files, "dependency_edges": dependency_edges[:5000], "findings": all_findings[:1000], "skipped": skipped, "warnings": ["Static findings are indicators, not proof of exploitability or a complete audit."]}


def inspect_json(path: Path, ctx: Context) -> dict[str, Any]:
    raw = read_limited(path, ctx.limits["max_read_bytes"], ctx)
    encoding = detect_text_encoding(raw)
    codec = encoding["encoding"] if encoding["encoding"] not in ("binary-or-unknown", "ascii-or-single-byte") else "utf-8"
    duplicates: list[str] = []

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                duplicates.append(key)
            output[key] = value
        return output

    try:
        value = json.loads(raw.decode(codec), object_pairs_hook=object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError("invalid_json", "JSON parsing failed", {"reason": str(exc)}) from exc
    summary: dict[str, Any] = {"root_type": type(value).__name__, "duplicate_keys": sorted(set(duplicates))[:1000], "valid": True}
    if isinstance(value, dict):
        summary["root_key_count"] = len(value)
        summary["root_keys_sample"] = sorted(map(str, value.keys()))[:100]
    elif isinstance(value, list):
        summary["root_length"] = len(value)
    return summary


def inspect_toml(path: Path, ctx: Context) -> dict[str, Any]:
    raw = read_limited(path, ctx.limits["max_read_bytes"], ctx)
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ToolError("invalid_toml", "TOML parsing failed", {"reason": str(exc)}) from exc
    return {"valid": True, "root_key_count": len(value), "root_keys_sample": sorted(value.keys())[:100]}


def inspect_ini(path: Path, ctx: Context) -> dict[str, Any]:
    raw = read_limited(path, ctx.limits["max_read_bytes"], ctx)
    parser = configparser.ConfigParser(strict=True, interpolation=None)
    try:
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise ToolError("invalid_ini", "INI parsing failed", {"reason": str(exc)}) from exc
    return {"valid": True, "sections": parser.sections(), "section_count": len(parser.sections()), "defaults": sorted(parser.defaults().keys())}


def inspect_xml(path: Path, ctx: Context) -> dict[str, Any]:
    raw = read_limited(path, ctx.limits["max_read_bytes"], ctx)
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ToolError("unsafe_xml", "XML with DTD or entity declarations is not parsed", {"reason": "Avoids entity-expansion and external-entity risk"})
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ToolError("invalid_xml", "XML parsing failed", {"reason": str(exc)}) from exc
    count = sum(1 for _ in root.iter())
    return {"valid": True, "root_tag": root.tag, "element_count": count, "root_attributes": sorted(root.attrib.keys())}


def inspect_sqlite_database(path: Path, ctx: Context) -> dict[str, Any]:
    # Path.as_uri avoids string interpolation into a SQLite URI; mode=ro denies writes.
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ToolError("invalid_sqlite", "Cannot open SQLite database read-only", {"reason": str(exc)}) from exc
    try:
        connection.execute("PRAGMA query_only = ON")
        quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
        tables = [{"name": row[0], "type": row[1], "sql": row[2]} for row in connection.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','index','view','trigger') ORDER BY type, name LIMIT 1000")]
        return {"valid": all(value == "ok" for value in quick_check), "quick_check": quick_check, "schema_objects": tables, "schema_object_count": len(tables), "read_only": True}
    except sqlite3.Error as exc:
        raise ToolError("invalid_sqlite", "SQLite inspection failed", {"reason": str(exc)}) from exc
    finally:
        connection.close()


def validate_artifact(path: Path, kind: str, ctx: Context, signatures: list[dict[str, Any]]) -> dict[str, Any]:
    require_file_within_limit(path, ctx.limits)
    inferred = kind
    if kind == "auto":
        head = read_prefix(path, min(8192, path.stat().st_size), ctx)
        matched = signatures_at(head, signatures)
        formats = [item["format"] for item in matched]
        suffix = path.suffix.lower()
        if "zip" in formats:
            inferred = "zip"
        elif "tar" in formats or suffix in (".tar", ".tgz", ".tbz", ".tbz2", ".txz"):
            inferred = "tar"
        elif "gzip" in formats:
            inferred = "gzip"
        elif "bzip2" in formats:
            inferred = "bzip2"
        elif "xz" in formats:
            inferred = "xz"
        elif "sqlite" in formats:
            inferred = "sqlite"
        elif "elf" in formats:
            inferred = "elf"
        elif "pe" in formats:
            inferred = "pe"
        elif "macho" in formats or "macho-fat" in formats:
            inferred = "macho"
        elif "wasm" in formats:
            inferred = "wasm"
        elif "dex" in formats:
            inferred = "dex"
        elif "java-class" in formats:
            inferred = "java-class"
        elif suffix == ".json":
            inferred = "json"
        elif suffix == ".toml":
            inferred = "toml"
        elif suffix in (".ini", ".cfg", ".conf"):
            inferred = "ini"
        elif suffix in (".xml", ".xsd", ".plist"):
            inferred = "xml"
        else:
            inferred = "text"
    if inferred == "json":
        return {"kind": inferred, "result": inspect_json(path, ctx)}
    if inferred == "toml":
        return {"kind": inferred, "result": inspect_toml(path, ctx)}
    if inferred == "ini":
        return {"kind": inferred, "result": inspect_ini(path, ctx)}
    if inferred == "xml":
        return {"kind": inferred, "result": inspect_xml(path, ctx)}
    if inferred == "sqlite":
        header = parse_sqlite_header(read_prefix(path, min(100, path.stat().st_size), ctx))
        return {"kind": inferred, "header": header, "database": inspect_sqlite_database(path, ctx)}
    if inferred in ("zip", "tar"):
        actual = identify_archive(path)
        if actual != inferred and not (inferred == "tar" and actual == "tar"):
            raise ToolError("format_mismatch", f"Input is not a {inferred.upper()} archive")
        result = inspect_zip(path, ctx, recursive=False) if actual == "zip" else inspect_tar(path, ctx, recursive=False)
        return {"kind": actual, "result": result}
    if inferred in ("gzip", "bzip2", "xz"):
        return {"kind": inferred, "result": decompress_validate(path, inferred, ctx)}
    if inferred in ("elf", "pe", "macho", "wasm", "java-class", "dex", "ar"):
        data = read_limited(path, ctx.limits["max_read_bytes"], ctx)
        parser_kind = "java-class" if inferred == "java-class" else inferred
        return {"kind": inferred, "result": parse_known_format(data, parser_kind, ctx)}
    if inferred == "text":
        data = read_limited(path, ctx.limits["max_read_bytes"], ctx)
        return {"kind": "text", "result": {"encoding": detect_text_encoding(data), "bytes_checked": len(data), "valid": True, "note": "Text mode validates bounded readability and encoding heuristics; it does not infer a document grammar."}}
    raise ToolError("unsupported_kind", "Validation kind is unsupported", {"kind": inferred})


def triage_artifact(path: Path, ctx: Context, policy: dict[str, Any], signatures: list[dict[str, Any],]) -> dict[str, Any]:
    info = require_file_within_limit(path, ctx.limits)
    head = read_prefix(path, min(max(8192, 1024), info.st_size), ctx)
    scan_size = min(info.st_size, ctx.limits["max_scan_bytes"])
    scan_data = read_prefix(path, scan_size, ctx)
    matched = signatures_at(head, signatures)
    formats = [match["format"] for match in matched]
    parser_data: dict[str, Any] | None = None
    parser_error: dict[str, Any] | None = None
    preferred = None
    for candidate in ("elf", "pe", "macho", "macho-fat", "wasm", "java-class", "dex", "sqlite", "ar"):
        if candidate in formats:
            preferred = "macho" if candidate == "macho-fat" else candidate
            break
    if preferred:
        try:
            data = read_limited(path, ctx.limits["max_read_bytes"], ctx)
            parser_data = parse_known_format(data, preferred, ctx)
        except ToolError as exc:
            parser_error = {"code": exc.code, "message": exc.message, "details": exc.details}
    extension_hint = path.suffix.lower().lstrip(".") or None
    mismatch = bool(matched and extension_hint and all(match["format"] != extension_hint for match in matched))
    return {
        "artifact": metadata_for(path),
        "hashes": file_hashes(path, ctx),
        "top_level_signatures": matched,
        "extension_hint": extension_hint,
        "extension_signature_mismatch": mismatch,
        "encoding": detect_text_encoding(scan_data),
        "entropy": entropy_profile(path, ctx),
        "strings": extract_strings(scan_data, ctx, policy),
        "embedded_signature_hits": embedded_signatures(path, signatures, ctx),
        "structure": parser_data,
        "structure_error": parser_error,
        "scan_coverage": {"bytes_scanned_for_strings_and_embedded_signatures": scan_size, "file_size": info.st_size, "truncated": scan_size < info.st_size},
    }


def compare_artifacts(left: Path, right: Path, ctx: Context) -> dict[str, Any]:
    left_info = require_file_within_limit(left, ctx.limits, "left input")
    right_info = require_file_within_limit(right, ctx.limits, "right input")
    left_hashes = file_hashes(left, ctx)
    right_hashes = file_hashes(right, ctx)
    exact = left_hashes["sha256"] == right_hashes["sha256"] and left_info.st_size == right_info.st_size
    result: dict[str, Any] = {
        "left": {"path": str(left), "size": left_info.st_size, "hashes": left_hashes},
        "right": {"path": str(right), "size": right_info.st_size, "hashes": right_hashes},
        "byte_for_byte_equal": exact,
        "diff_ranges": [],
        "coverage": "hashes cover full files",
    }
    if exact:
        return result
    if max(left_info.st_size, right_info.st_size) > ctx.limits["max_compare_bytes"]:
        result["coverage"] = "hashes cover full files; positional diff omitted because one input exceeds max_compare_bytes"
        result["warning"] = "Lower input size or change the active policy to obtain insertion/deletion/replace ranges."
        return result
    left_data = read_limited(left, ctx.limits["max_compare_bytes"], ctx)
    right_data = read_limited(right, ctx.limits["max_compare_bytes"], ctx)
    matcher = difflib.SequenceMatcher(a=left_data, b=right_data, autojunk=False)
    ranges: list[dict[str, Any]] = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        ctx.check()
        if tag == "equal":
            continue
        ranges.append({"operation": tag, "left_offset": a0, "left_length": a1 - a0, "right_offset": b0, "right_length": b1 - b0})
        if len(ranges) >= ctx.limits["max_diff_ranges"]:
            break
    result["diff_ranges"] = ranges
    result["coverage"] = "full positional diff for both bounded files"
    result["diff_ranges_truncated"] = len(ranges) >= ctx.limits["max_diff_ranges"]
    return result


def decode_bytes(path: Path, encoding: str, ctx: Context, policy: dict[str, Any]) -> dict[str, Any]:
    data = read_limited(path, ctx.limits["max_read_bytes"], ctx)
    compact = b"".join(data.split())
    selected = encoding
    if encoding == "auto":
        if compact and len(compact) % 2 == 0 and re.fullmatch(rb"[0-9a-fA-F]+", compact):
            selected = "base16"
        elif b"%" in data:
            selected = "percent"
        else:
            try:
                base64.b64decode(compact, validate=True)
                selected = "base64"
            except (binascii.Error, ValueError):
                selected = "utf8"
    try:
        if selected == "base16":
            decoded = base64.b16decode(compact.upper(), casefold=True)
        elif selected == "base32":
            decoded = base64.b32decode(compact.upper(), casefold=True)
        elif selected == "base64":
            decoded = base64.b64decode(compact, validate=True)
        elif selected == "base85":
            decoded = base64.b85decode(compact)
        elif selected == "ascii85":
            decoded = base64.a85decode(compact, adobe=False)
        elif selected == "percent":
            decoded = urllib.parse.unquote_to_bytes(data.decode("ascii"))
        elif selected == "utf8":
            data.decode("utf-8")
            decoded = data
        elif selected == "utf16le":
            data.decode("utf-16le")
            decoded = data
        elif selected == "utf16be":
            data.decode("utf-16be")
            decoded = data
        else:
            raise ToolError("unsupported_encoding", "Unsupported decoder", {"encoding": selected})
    except (UnicodeDecodeError, binascii.Error, ValueError) as exc:
        raise ToolError("invalid_encoding", "Input cannot be decoded using selected encoding", {"encoding": selected, "reason": str(exc)}) from exc
    if len(decoded) > ctx.limits["max_archive_uncompressed_bytes"]:
        raise ToolError("decode_limit_exceeded", "Decoded output exceeds policy limit", {"decoded_bytes": len(decoded), "max_bytes": ctx.limits["max_archive_uncompressed_bytes"]})
    return {"encoding": selected, "source_bytes": len(data), "decoded_bytes": len(decoded), "decoded_hashes": bytes_hashes(decoded), "decoded_encoding_guess": detect_text_encoding(decoded), "decoded_hex_preview": decoded[:80].hex(), "decoded_text_preview": safe_text_preview(decoded[:512], policy["secret_patterns"], 240), "_decoded": decoded}


def analyze_regex(pattern: str) -> dict[str, Any]:
    if len(pattern) > 4096:
        raise ToolError("pattern_too_long", "Pattern exceeds 4096-character safety limit")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return {"valid": False, "error": str(exc), "length": len(pattern), "risk_indicators": []}
    indicators: list[dict[str, str]] = []
    if re.search(r"\((?:[^()\\]|\\.)*[*+](?:[^()\\]|\\.)*\)[*+{]", pattern):
        indicators.append({"kind": "nested_quantifier", "severity": "high", "message": "A quantified group contains another quantifier; review for catastrophic backtracking."})
    if re.search(r"\([^)]*\|[^)]*\)[+*]", pattern):
        indicators.append({"kind": "quantified_alternation", "severity": "medium", "message": "A quantified alternation can backtrack heavily when alternatives overlap."})
    if re.search(r"\\[1-9]", pattern):
        indicators.append({"kind": "backreference", "severity": "medium", "message": "Backreferences can make matching non-linear and engine-dependent."})
    if re.search(r"\.\*[+*]|\.\+[*+]", pattern):
        indicators.append({"kind": "stacked_wildcard_quantifier", "severity": "high", "message": "Stacked wildcard quantifiers are likely invalid or expensive."})
    anchors = {"start": pattern.startswith("^") or pattern.startswith("\\A"), "end": pattern.endswith("$") or pattern.endswith("\\Z")}
    return {"valid": True, "python_flags": compiled.flags, "length": len(pattern), "anchoring": anchors, "risk_indicators": indicators, "note": "This command compiles but never executes the supplied pattern against untrusted text."}


def collect_case_input_files(source: Path, ctx: Context) -> tuple[list[tuple[Path, Path]], list[dict[str, Any]]]:
    """Return source file / safe-relative pairs. Symlinks and special files are skipped."""
    records: list[tuple[Path, Path]] = []
    skipped: list[dict[str, Any]] = []
    if source.is_file():
        if source.is_symlink():
            raise ToolError("symlink_input", "Case input must not be a symlink", {"path": str(source)})
        return [(source, Path(source.name))], skipped
    if not source.is_dir():
        raise ToolError("invalid_input", "Case input must be a regular file or directory", {"path": str(source)})
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(dirs)
        for filename in sorted(files):
            ctx.check()
            path = Path(root) / filename
            if path.is_symlink() or not path.is_file():
                skipped.append({"path": str(path), "reason": "symlink or non-regular file skipped"})
                continue
            relative = path.relative_to(source)
            records.append((path, relative))
            if len(records) > ctx.limits["max_source_files"]:
                raise ToolError("case_limit_exceeded", "Case input exceeds max_source_files", {"max_source_files": ctx.limits["max_source_files"]})
    return records, skipped


def create_case(inputs: list[Path], destination: Path, case_id: str, source_note: str | None, ctx: Context) -> dict[str, Any]:
    if destination.exists():
        raise ToolError("output_exists", "Case destination must not already exist", {"path": str(destination)})
    if not destination.parent.exists() or not destination.parent.is_dir():
        raise ToolError("invalid_output", "Case destination parent does not exist", {"path": str(destination.parent)})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", case_id):
        raise ToolError("invalid_case_id", "case-id must be 1-64 ASCII letters, digits, dots, underscores, or hyphens and begin alphanumeric")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.case-", dir=destination.parent))
    originals = staging / "originals"
    originals.mkdir(mode=0o700)
    copied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used: set[str] = set()
    try:
        for source in inputs:
            source = require_existing_path(source, "case input")
            source_records, source_skipped = collect_case_input_files(source, ctx)
            skipped.extend(source_skipped)
            root_prefix = source.name if source.is_dir() else ""
            for file_path, relative in source_records:
                ctx.check()
                source_info = require_file_within_limit(file_path, ctx.limits, "case input")
                proposed = Path(root_prefix) / relative if root_prefix else relative
                key = proposed.as_posix()
                if key in used:
                    stem = proposed.stem
                    suffix = proposed.suffix
                    counter = 2
                    while f"{proposed.parent.as_posix()}/{stem}-{counter}{suffix}" in used:
                        counter += 1
                    proposed = proposed.parent / f"{stem}-{counter}{suffix}"
                    key = proposed.as_posix()
                used.add(key)
                target = originals / proposed
                target.parent.mkdir(parents=True, exist_ok=True)
                pre_hash = file_sha256(file_path, ctx)
                with file_path.open("rb") as source_handle:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    descriptor = os.open(target, flags, 0o600)
                    try:
                        with os.fdopen(descriptor, "wb") as target_handle:
                            copied_length, copied_hash = stream_copy_and_hash(source_handle, target_handle, ctx, source_info.st_size)
                            target_handle.flush()
                            os.fsync(target_handle.fileno())
                    except BaseException:
                        target.unlink(missing_ok=True)
                        raise
                if pre_hash != copied_hash:
                    raise ToolError("copy_verification_failed", "Hash mismatch after case copy", {"path": str(file_path)})
                # Best-effort read-only mode; the manifest is the portable immutability evidence.
                try:
                    target.chmod(0o444)
                except OSError:
                    pass
                copied.append({"source_name": file_path.name, "case_path": f"originals/{proposed.as_posix()}", "size": copied_length, "sha256": copied_hash})
        manifest = {"tool": TOOL_NAME, "version": TOOL_VERSION, "case_id": case_id, "created_utc": utc_now(), "source_note": source_note, "copy_mode": "verified SHA-256, best-effort read-only permissions", "artifacts": copied, "skipped": skipped}
        write_file_from_bytes(staging / "case-manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"case_id": case_id, "destination": str(destination), "manifest": str(destination / "case-manifest.json"), "artifact_count": len(copied), "artifacts": copied, "skipped": skipped}


def base_report(operation: str, ctx: Context) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "operation": operation,
        "status": "ok",
        "created_utc": utc_now(),
        "safety": {
            "analysis_mode": "local static inspection",
            "input_mutation": "never",
            "content_execution": "disabled",
            "network": "disabled",
            "automatic_retries": 0,
            "write_approvals": ["--approve-copy", "--approve-extract", "--approve-write", "--approve-overwrite"],
        },
        "limits": ctx.public_limits(),
        "evidence": {},
        "findings": [],
        "interpretations": [],
        "limitations": [],
    }


def error_report(operation: str | None, error: ToolError) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "operation": operation or "unknown",
        "status": "error",
        "created_utc": utc_now(),
        "error": {"code": error.code, "message": error.message, "details": error.details},
    }


def capabilities() -> list[dict[str, Any]]:
    return [
        {"command": "case-init", "capabilities": ["verified case copies", "SHA-256 custody manifest", "best-effort read-only copied originals", "source-note provenance"], "catalog_alignment": ["Foundational Intake, Triage, and Evidence", "Legacy Software and Digital Preservation"]},
        {"command": "triage", "capabilities": ["hashes", "magic signatures", "encoding heuristics", "sampled entropy", "redacted string extraction", "embedded signature locations", "ELF/PE/Mach-O/Wasm/Dex/SQLite/ar structure when recognized"], "catalog_alignment": ["Binary Identification and Architecture", "Executable and Object Format Inspection", "Compression, Packing, and Entropy", "Encoding, Text, and Binary Representation", "File and Format Forensics"]},
        {"command": "inspect", "capabilities": ["triage plus safe archive inventory or source scan and format validation"], "catalog_alignment": ["Static Analysis Workflows", "Archives, Containers, and Resource Packages", "Structured and Serialized Data"]},
        {"command": "validate", "capabilities": ["JSON duplicate-key-aware parse", "TOML", "INI", "XML without DTD/entities", "SQLite read-only quick_check", "ZIP/TAR metadata checks", "GZIP/BZIP2/XZ bounded decompression", "ELF/PE/Mach-O/Wasm/Java/DEX/ar bounds parsing"], "catalog_alignment": ["Structured and Serialized Data", "Databases, Registries, and Persistent State", "Testing, Validation, and Quality Assurance"]},
        {"command": "archive-list", "capabilities": ["ZIP and TAR-family inventories", "path traversal/link/duplicate/encryption/compression-ratio flags", "bounded recursive nested ZIP/TAR inspection"], "catalog_alignment": ["Archives, Containers, and Resource Packages", "File and Format Forensics"]},
        {"command": "extract", "capabilities": ["approval-gated ZIP/TAR extraction", "path/link/device/duplicate rejection", "bounded writes", "hash manifest", "staging cleanup"], "catalog_alignment": ["Archive Integrity and Path Safety Validator", "Resource Extraction and Repackaging Engineer"]},
        {"command": "compare", "capabilities": ["full-file multi-hash comparison", "bounded positional replace/insert/delete ranges"], "catalog_alignment": ["Binary Similarity Comparator", "Byte-for-Byte Integrity Comparator"]},
        {"command": "source-scan", "capabilities": ["polyglot source/build file discovery", "import dependency edges", "definition candidates", "redacted sensitive-material indicators", "static risk indicators"], "catalog_alignment": ["Source Code Understanding and Repair", "Build Systems, CI, and Automation", "Security-Focused Defensive Analysis"]},
        {"command": "decode", "capabilities": ["Base16/Base32/Base64/Base85/Ascii85/percent validation and decoding", "UTF-8/UTF-16 validation", "approval-gated decoded output"], "catalog_alignment": ["Encoding, Text, and Binary Representation"]},
        {"command": "regex-check", "capabilities": ["Python syntax compilation without target execution", "nested quantifier, alternation, and backreference advisories"], "catalog_alignment": ["Regular Expression Investigator"]},
    ]


def markdown_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_markdown(report: dict[str, Any]) -> str:
    title = f"{TOOL_NAME}: {report.get('operation', 'report')}"
    lines = [f"# {title}", "", f"**Status:** {report.get('status', 'unknown')}", f"**Created (UTC):** {report.get('created_utc', 'unknown')}", ""]
    if report.get("status") == "error":
        error = report.get("error", {})
        lines.extend(["## Error", "", f"- **Code:** `{error.get('code', 'unknown')}`", f"- **Message:** {error.get('message', '')}"])
        if error.get("details"):
            lines.extend(["", "```json", json.dumps(error["details"], indent=2, sort_keys=True), "```"])
        return "\n".join(lines) + "\n"
    for section in ("safety", "limits", "evidence", "findings", "interpretations", "limitations"):
        content = report.get(section)
        if content in (None, {}, []):
            continue
        lines.extend([f"## {section.replace('_', ' ').title()}", ""])
        if isinstance(content, dict):
            simple = {key: value for key, value in content.items() if not isinstance(value, (dict, list))}
            complex_values = {key: value for key, value in content.items() if isinstance(value, (dict, list))}
            if simple:
                lines.extend(["| Field | Value |", "|---|---|"])
                for key, value in simple.items():
                    lines.append(f"| `{key}` | {markdown_scalar(value)} |")
                lines.append("")
            if complex_values:
                lines.extend(["```json", json.dumps(complex_values, indent=2, sort_keys=True, ensure_ascii=False), "```", ""])
        else:
            lines.extend(["```json", json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def ensure_safe_output_path(value: str, input_paths: Iterable[Path], overwrite: bool, approve_overwrite: bool) -> Path:
    output = Path(value).expanduser()
    if not output.parent.exists() or not output.parent.is_dir():
        raise ToolError("invalid_output", "Output parent directory does not exist", {"path": str(output.parent)})
    try:
        resolved_output = output.resolve(strict=False)
        input_resolved = {path.resolve(strict=False) for path in input_paths}
        if resolved_output in input_resolved:
            raise ToolError("unsafe_output", "Output path must not replace an input", {"path": str(output)})
    except OSError:
        pass
    if output.exists() and not (overwrite and approve_overwrite):
        raise ToolError("output_exists", "Output exists; use both --overwrite and --approve-overwrite to replace it", {"path": str(output)})
    if output.exists() and output.is_dir():
        raise ToolError("invalid_output", "Report output path is a directory", {"path": str(output)})
    return output


def atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def emit_report(report: dict[str, Any], args: argparse.Namespace, input_paths: Iterable[Path] = ()) -> None:
    output_format = getattr(args, "format", "json")
    if output_format == "markdown":
        rendered = render_markdown(report)
    else:
        rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    output = getattr(args, "output", None)
    if output:
        target = ensure_safe_output_path(output, input_paths, bool(getattr(args, "overwrite", False)), bool(getattr(args, "approve_overwrite", False)))
        atomic_write(target, rendered.encode("utf-8"))
        if not getattr(args, "quiet", False):
            print(json.dumps({"status": report.get("status"), "report_path": str(target), "operation": report.get("operation")}, sort_keys=True))
    else:
        print(rendered, end="")


def write_decoded_output(path_value: str, data: bytes, args: argparse.Namespace, input_path: Path) -> Path:
    if not getattr(args, "approve_write", False):
        raise ToolError("approval_required", "Writing decoded bytes requires --approve-write", {"action": "write decoded output"})
    path = ensure_safe_output_path(path_value, [input_path], bool(getattr(args, "overwrite", False)), bool(getattr(args, "approve_overwrite", False)))
    atomic_write(path, data)
    return path


def handle_operation(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[dict[str, Any] | None, list[Path]]:
    command = args.command
    if command == "help":
        if args.topic:
            choice = parser._subparsers._group_actions[0].choices.get(args.topic)  # type: ignore[attr-defined]
            if choice is None:
                raise ToolError("unknown_command", "Unknown help topic", {"topic": args.topic})
            choice.print_help()
        else:
            parser.print_help()
        return None, []
    if command is None:
        parser.print_help()
        return None, []
    if args.retries != 0:
        raise ToolError("unsupported_retry", "Automatic retries are intentionally disabled for local deterministic operations", {"requested_retries": args.retries})
    policy = load_policy(args.policy)
    ctx = Context(policy, args)
    signatures = load_signatures()
    if command in ("capability-list", "capabilities"):
        report = base_report("capability-list", ctx)
        report["evidence"] = {"capabilities": capabilities(), "asset_references": [f"{TOOL_NAME}/asset-default-policy.json", f"{TOOL_NAME}/asset-magic-signatures.json"]}
        report["limitations"] = ["This is a static, local inspection tool. It does not execute artifacts, instrument processes, contact services, decrypt unknown material, bypass licensing or security controls, or replace specialized proprietary-format parsers."]
        return report, []
    if command == "triage":
        path = require_existing_file(args.input)
        report = base_report("triage", ctx)
        report["evidence"] = triage_artifact(path, ctx, policy, signatures)
        report["interpretations"] = ["Top-level and embedded signatures are leads. Signature collision, nesting, padding, and malformed data can produce false positives."]
        return report, [path]
    if command == "inspect":
        path = require_existing_file(args.input)
        report = base_report("inspect", ctx)
        evidence = {"triage": triage_artifact(path, ctx, policy, signatures)}
        archive_kind = identify_archive(path)
        if archive_kind == "zip":
            evidence["archive_inventory"] = inspect_zip(path, ctx, recursive=args.recursive_archives)
        elif archive_kind == "tar":
            evidence["archive_inventory"] = inspect_tar(path, ctx, recursive=args.recursive_archives)
        elif source_language(path, policy):
            evidence["source_scan"] = analyze_source(path, ctx, policy, include_hidden=args.include_hidden)
            report["findings"] = evidence["source_scan"]["findings"]
        try:
            evidence["validation"] = validate_artifact(path, "auto", ctx, signatures)
        except ToolError as exc:
            evidence["validation"] = {"status": "not_completed", "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        report["evidence"] = evidence
        report["interpretations"] = ["Inspect combines independent bounded static checks. A parser disagreement is recorded rather than silently treated as a conclusive format claim."]
        return report, [path]
    if command == "validate":
        path = require_existing_file(args.input)
        report = base_report("validate", ctx)
        report["evidence"] = {"input": metadata_for(path), "validation": validate_artifact(path, args.kind, ctx, signatures)}
        return report, [path]
    if command == "archive-list":
        path = require_existing_file(args.input)
        archive_kind = identify_archive(path)
        if archive_kind == "zip":
            inventory = inspect_zip(path, ctx, recursive=args.recursive)
        elif archive_kind == "tar":
            inventory = inspect_tar(path, ctx, recursive=args.recursive)
        else:
            raise ToolError("unsupported_archive", "Archive inventory supports ZIP and TAR-family archives only", {"path": str(path)})
        report = base_report("archive-list", ctx)
        report["evidence"] = {"input": metadata_for(path), "archive": inventory}
        report["limitations"] = ["RAR, 7-Zip, CAB, ISO, and proprietary archive extraction need a dedicated audited parser or separately approved local tool; this package does not invoke external tools."]
        return report, [path]
    if command == "extract":
        path = require_existing_file(args.input)
        if not args.approve_extract:
            raise ToolError("approval_required", "Extraction writes a new directory and requires --approve-extract", {"action": "safe archive extraction"})
        destination = Path(args.output_dir).expanduser()
        report = base_report("extract", ctx)
        report["evidence"] = {"input": metadata_for(path), "extraction": extract_archive(path, destination, ctx)}
        return report, [path]
    if command == "compare":
        left = require_existing_file(args.left, "left input")
        right = require_existing_file(args.right, "right input")
        report = base_report("compare", ctx)
        report["evidence"] = compare_artifacts(left, right, ctx)
        report["limitations"] = ["Positional differences are only generated when both files are within max_compare_bytes; full hashes are still calculated for larger inputs."]
        return report, [left, right]
    if command == "source-scan":
        input_path = require_existing_path(args.input)
        report = base_report("source-scan", ctx)
        evidence = analyze_source(input_path, ctx, policy, include_hidden=args.include_hidden)
        report["evidence"] = evidence
        report["findings"] = evidence["findings"]
        report["limitations"] = ["Dependency and definition extraction use language-aware regular-expression heuristics, not a compiler or full semantic parser. Findings are not proof of vulnerability or secret validity."]
        return report, [input_path]
    if command == "decode":
        path = require_existing_file(args.input)
        decoded = decode_bytes(path, args.encoding, ctx, policy)
        raw = decoded.pop("_decoded")
        if args.decoded_output:
            written = write_decoded_output(args.decoded_output, raw, args, path)
            decoded["decoded_output"] = str(written)
        report = base_report("decode", ctx)
        report["evidence"] = {"input": metadata_for(path), "decode": decoded}
        report["limitations"] = ["The report does not expose complete decoded content. It does not attempt decryption, XOR guessing, or key recovery."]
        return report, [path]
    if command == "regex-check":
        report = base_report("regex-check", ctx)
        report["evidence"] = {"regex": analyze_regex(args.pattern)}
        report["limitations"] = ["Risk indicators are conservative heuristics. The supplied pattern is compiled but never run against a subject string."]
        return report, []
    if command == "case-init":
        if not args.approve_copy:
            raise ToolError("approval_required", "Creating verified case copies requires --approve-copy", {"action": "copy supplied artifacts"})
        inputs = [require_existing_path(item, "case input") for item in args.input]
        report = base_report("case-init", ctx)
        report["evidence"] = {"case": create_case(inputs, Path(args.output_dir).expanduser(), args.case_id, args.source_note, ctx)}
        return report, inputs
    raise ToolError("unknown_command", "Unknown command", {"command": command})


def build_parser() -> StructuredArgumentParser:
    parser = StructuredArgumentParser(
        prog="tools-artifact-inspector.py",
        description="Safe local static triage, validation, source review, archive inspection, comparison, and preservation intake.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Safety: inspected content is never executed and no network connection is made. "
            "case-init, extract, decoded writes, and report overwrite require explicit approval flags.\n\n"
            "Examples:\n"
            "  python3 code-data-reverse-engineering/tools-artifact-inspector.py triage sample.bin\n"
            "  python3 code-data-reverse-engineering/tools-artifact-inspector.py --output report.json inspect sample.zip --recursive-archives\n"
            "  python3 code-data-reverse-engineering/tools-artifact-inspector.py source-scan ./project\n"
            "  python3 code-data-reverse-engineering/tools-artifact-inspector.py --output comparison.json compare old.bin new.bin"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    parser.add_argument("--policy", metavar="PATH", help="Optional conservative JSON override for numeric limits and source mappings.")
    parser.add_argument("--output", "-o", metavar="PATH", help="Write the JSON or Markdown report to this path instead of stdout.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Report format (default: json).")
    parser.add_argument("--overwrite", action="store_true", help="Request report/decoded-output replacement; also requires --approve-overwrite.")
    parser.add_argument("--approve-overwrite", action="store_true", help="Explicitly approve replacement of an existing report or decoded-output file.")
    parser.add_argument("--quiet", action="store_true", help="When --output is used, suppress the one-line completion object on stdout.")
    parser.add_argument("--timeout", type=positive_int, metavar="SECONDS", help="Bound total operation time; cannot exceed active policy.")
    parser.add_argument("--retries", type=nonnegative_int, default=0, metavar="COUNT", help="Must remain 0: local operations do not auto-retry.")
    parser.add_argument("--max-file-bytes", type=positive_int, metavar="BYTES", help="Lower the accepted input-file limit.")
    parser.add_argument("--max-read-bytes", type=positive_int, metavar="BYTES", help="Lower the in-memory parser read limit.")
    parser.add_argument("--max-scan-bytes", type=positive_int, metavar="BYTES", help="Lower bounded string/signature scan coverage.")
    parser.add_argument("--max-archive-entries", type=positive_int, metavar="COUNT", help="Lower archive entry-count limit.")
    parser.add_argument("--max-archive-depth", type=positive_int, metavar="COUNT", help="Lower recursive archive inventory depth.")
    parser.add_argument("--max-source-files", type=positive_int, metavar="COUNT", help="Lower recursive source scan file limit.")
    parser.add_argument("--max-compare-bytes", type=positive_int, metavar="BYTES", help="Lower maximum size for positional binary diffing.")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    help_parser = subparsers.add_parser("help", help="Show built-in help; optionally for one command.")
    help_parser.add_argument("topic", nargs="?", help="Command name to describe.")
    subparsers.add_parser("capability-list", aliases=["capabilities"], help="List all implemented capabilities and boundaries.")

    triage = subparsers.add_parser("triage", help="Perform bounded read-only file identification and evidence collection.")
    triage.add_argument("input", metavar="INPUT", help="Regular artifact file to inspect.")

    inspect = subparsers.add_parser("inspect", help="Run triage plus appropriate archive/source/validation checks.")
    inspect.add_argument("input", metavar="INPUT", help="Regular artifact file to inspect.")
    inspect.add_argument("--recursive-archives", action="store_true", help="Inventory recognized nested ZIP/TAR entries within policy limits.")
    inspect.add_argument("--include-hidden", action="store_true", help="Include hidden source files when input itself is a recognized source file.")

    validate = subparsers.add_parser("validate", help="Validate a supported data, archive, executable, or compression format.")
    validate.add_argument("input", metavar="INPUT", help="Regular artifact file to validate.")
    validate.add_argument("--kind", choices=("auto", "json", "toml", "ini", "xml", "sqlite", "zip", "tar", "gzip", "bzip2", "xz", "elf", "pe", "macho", "wasm", "java-class", "dex", "ar", "text"), default="auto", help="Format to validate (default: auto).")

    archive_list = subparsers.add_parser("archive-list", help="Safely inventory a ZIP or TAR-family archive without extracting it.")
    archive_list.add_argument("input", metavar="INPUT", help="ZIP or TAR-family archive.")
    archive_list.add_argument("--recursive", action="store_true", help="Inspect bounded recognized nested archives.")

    extract = subparsers.add_parser("extract", help="Approval-gated safe ZIP/TAR extraction into a new directory.")
    extract.add_argument("input", metavar="INPUT", help="ZIP or TAR-family archive.")
    extract.add_argument("--output-dir", required=True, metavar="DIR", help="New destination directory; it must not exist.")
    extract.add_argument("--approve-extract", action="store_true", help="Explicitly approve writing extracted files.")

    compare = subparsers.add_parser("compare", help="Compare two files using full hashes and bounded exact diff ranges.")
    compare.add_argument("left", metavar="LEFT", help="First regular file.")
    compare.add_argument("right", metavar="RIGHT", help="Second regular file.")

    source = subparsers.add_parser("source-scan", help="Statically map recognized source/build/configuration files.")
    source.add_argument("input", metavar="INPUT", help="Recognized source/build file or directory tree.")
    source.add_argument("--include-hidden", action="store_true", help="Include hidden files/directories in a directory scan.")

    decode = subparsers.add_parser("decode", help="Validate/decode common textual byte encodings without exposing complete content in reports.")
    decode.add_argument("input", metavar="INPUT", help="Encoded input file.")
    decode.add_argument("--encoding", choices=("auto", "base16", "base32", "base64", "base85", "ascii85", "percent", "utf8", "utf16le", "utf16be"), default="auto", help="Encoding to validate/decode (default: auto).")
    decode.add_argument("--decoded-output", metavar="PATH", help="Optional path for decoded bytes; requires --approve-write.")
    decode.add_argument("--approve-write", action="store_true", help="Explicitly approve writing decoded bytes.")

    regex = subparsers.add_parser("regex-check", help="Compile and inspect a Python regular expression without running it against a subject.")
    regex.add_argument("--pattern", required=True, help="Regular expression to compile and inspect (max 4096 characters).")

    case_init = subparsers.add_parser("case-init", help="Approval-gated verified copies and custody manifest for files/directories.")
    case_init.add_argument("--input", action="append", required=True, metavar="PATH", help="Input file or directory; repeat for multiple inputs.")
    case_init.add_argument("--output-dir", required=True, metavar="DIR", help="New case directory; it must not exist.")
    case_init.add_argument("--case-id", required=True, help="Stable case identifier (1-64 ASCII letters/digits/dot/underscore/hyphen).")
    case_init.add_argument("--source-note", help="Optional provenance note recorded verbatim in the case manifest.")
    case_init.add_argument("--approve-copy", action="store_true", help="Explicitly approve creation of verified working copies.")
    return parser


def normalize_global_arguments(argv: list[str]) -> list[str]:
    """Permit global report/safety options before or after a subcommand.

    argparse subparsers conventionally require parent options before the command. This
    small, fixed allowlist moves only this tool's global options; it never interprets
    arbitrary user strings or subcommand options such as --output-dir.
    """
    options_with_value = {
        "--policy", "--output", "-o", "--format", "--timeout", "--retries",
        "--max-file-bytes", "--max-read-bytes", "--max-scan-bytes", "--max-archive-entries",
        "--max-archive-depth", "--max-source-files", "--max-compare-bytes",
    }
    flags = {"--overwrite", "--approve-overwrite", "--quiet", "--version"}
    prefix: list[str] = []
    remaining: list[str] = []
    index = 0
    after_separator = False
    while index < len(argv):
        token = argv[index]
        if token == "--":
            after_separator = True
            remaining.append(token)
            index += 1
            continue
        if not after_separator and token in flags:
            prefix.append(token)
            index += 1
            continue
        if not after_separator and token in options_with_value:
            if index + 1 >= len(argv):
                # Let argparse render its standard structured argument error.
                remaining.append(token)
                index += 1
            else:
                prefix.extend((token, argv[index + 1]))
                index += 2
            continue
        if not after_separator and any(token.startswith(option + "=") for option in options_with_value if option.startswith("--")):
            prefix.append(token)
            index += 1
            continue
        remaining.append(token)
        index += 1
    return prefix + remaining


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed_args: argparse.Namespace | None = None
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        parsed_args = parser.parse_args(normalize_global_arguments(arguments))
        report, inputs = handle_operation(parsed_args, parser)
        if report is not None:
            emit_report(report, parsed_args, inputs)
        return 0
    except ToolError as exc:
        report = error_report(getattr(parsed_args, "command", None), exc)
        if parsed_args is not None:
            try:
                emit_report(report, parsed_args)
            except ToolError:
                print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        report = error_report(getattr(parsed_args, "command", None), OperationCancelled())
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        return 130
    except Exception as exc:  # Defensive boundary: never hide a failed local operation as success.
        report = error_report(getattr(parsed_args, "command", None), ToolError("internal_error", "Unexpected internal failure", {"type": type(exc).__name__, "message": str(exc)}))
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
