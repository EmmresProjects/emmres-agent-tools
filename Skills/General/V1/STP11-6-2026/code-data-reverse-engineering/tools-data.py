"""Structured and serialized data analysis: validation, decoding, schema
inference, and read-only database/registry inspection. Standard library only."""
from __future__ import annotations

import configparser
import csv
import io
import json
import re
import sqlite3
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, EXIT_LIMIT, read_bytes, validate_input_file,
                        u16, u32, u64, i32, cstring, hash_bytes, parse_int)

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None


# ---------------------------------------------------------------------------
# JSON / TOML / INI / YAML / XML / CSV
# ---------------------------------------------------------------------------

def analyze_json(data: bytes, cfg: dict) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    dup_keys = []
    max_depth = [0]

    def hook(pairs):
        seen = {}
        for k, v in pairs:
            if k in seen:
                dup_keys.append(k)
            seen[k] = v
        return seen

    def depth_check(obj, d=0):
        max_depth[0] = max(max_depth[0], d)
        if d > cfg["limits"]["max_json_depth"]:
            raise RetoolError("depth-limit", "JSON nesting exceeds max_json_depth", exit_code=EXIT_LIMIT)
        if isinstance(obj, dict):
            for v in obj.values():
                depth_check(v, d + 1)
        elif isinstance(obj, list):
            for v in obj:
                depth_check(v, d + 1)

    try:
        obj = json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as e:
        return {"format": "json", "valid": False, "error": {"message": e.msg, "line": e.lineno, "col": e.colno, "pos": e.pos}}
    depth_check(obj)
    types = {}

    def census(o):
        t = type(o).__name__
        types[t] = types.get(t, 0) + 1
        if isinstance(o, dict):
            for v in o.values():
                census(v)
        elif isinstance(o, list):
            for v in o:
                census(v)
    census(obj)
    return {"format": "json", "valid": True, "duplicate_keys": sorted(set(dup_keys)),
            "max_depth": max_depth[0], "type_census": types,
            "top_level_type": type(obj).__name__,
            "top_level_keys": list(obj.keys())[:100] if isinstance(obj, dict) else None,
            "preview": _preview(obj)}


def _preview(obj, depth=0):
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return {k: _preview(v, depth + 1) for k, v in list(obj.items())[:20]}
    if isinstance(obj, list):
        return [_preview(v, depth + 1) for v in obj[:10]] + (["..."] if len(obj) > 10 else [])
    if isinstance(obj, str) and len(obj) > 120:
        return obj[:120] + "..."
    return obj


def analyze_toml(data: bytes) -> dict:
    if tomllib is None:
        return {"format": "toml", "valid": None, "note": "tomllib requires Python 3.11+; running on older Python. Use --format json after conversion or upgrade."}
    try:
        obj = tomllib.loads(data.decode("utf-8"))
        return {"format": "toml", "valid": True, "top_level_keys": list(obj.keys()), "preview": _preview(obj)}
    except tomllib.TOMLDecodeError as e:
        return {"format": "toml", "valid": False, "error": str(e)}


def analyze_ini(data: bytes) -> dict:
    cp = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        cp.read_string(data.decode("utf-8-sig", errors="replace"))
    except configparser.Error as e:
        return {"format": "ini", "valid": False, "error": str(e)}
    sections = {}
    for sec in cp.sections():
        sections[sec] = dict(cp.items(sec))
    return {"format": "ini", "valid": True, "sections": sections, "defaults": dict(cp.defaults())}


def analyze_yaml(data: bytes) -> dict:
    """Conservative YAML lint + flat-subset parse. Full YAML needs PyYAML (not bundled)."""
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    issues = []
    keys_by_indent = {}
    docs = 0
    for i, line in enumerate(lines, 1):
        if "\t" in line[:len(line) - len(line.lstrip())]:
            issues.append({"line": i, "issue": "tab-indentation", "severity": "high"})
        stripped = line.strip()
        if stripped in ("---", "..."):
            docs += 1
        if stripped.endswith(" "):
            issues.append({"line": i, "issue": "trailing-space", "severity": "low"})
        m = re.match(r"^(\s*)([A-Za-z0-9_.\-]+)\s*:", line)
        if m:
            indent, key = len(m.group(1)), m.group(2)
            bucket = keys_by_indent.setdefault(indent, {})
            if key in bucket:
                issues.append({"line": i, "issue": "duplicate-key-same-indent", "key": key,
                               "first_line": bucket[key], "severity": "medium"})
            else:
                bucket[key] = i
    # flat subset parse (top-level scalar mapping)
    flat = {}
    for line in lines:
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(.+)$", line)
        if m and not m.group(2).startswith(("[", "{", "|", ">")):
            flat[m.group(1)] = m.group(2).strip().strip("'\"")
    return {"format": "yaml", "lint_only": True, "document_markers": docs, "issues": issues,
            "flat_mapping_subset": flat,
            "limitation": "Structural lint + flat mapping subset only; full YAML parsing requires PyYAML (optional, not bundled)."}


def analyze_xml(data: bytes) -> dict:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return {"format": "xml", "valid": False, "error": str(e)}
    namespaces = set()
    tags = {}
    count = [0]

    def walk(el, depth=0):
        count[0] += 1
        if count[0] > 100000:
            raise RetoolError("element-limit", "XML element count exceeds safety limit", exit_code=EXIT_LIMIT)
        tag = el.tag
        if isinstance(tag, str) and tag.startswith("{"):
            ns, _, local = tag[1:].partition("}")
            namespaces.add(ns)
            tag = local
        tags[tag] = tags.get(tag, 0) + 1
        if depth > 200:
            raise RetoolError("depth-limit", "XML depth exceeds safety limit", exit_code=EXIT_LIMIT)
        for child in el:
            walk(child, depth + 1)
    walk(root)
    return {"format": "xml", "valid": True, "root": root.tag, "element_count": count[0],
            "tag_histogram": dict(sorted(tags.items(), key=lambda x: -x[1])[:50]),
            "namespaces": sorted(namespaces), "attributes_root": dict(root.attrib),
            "note": "ElementTree does not expand external entities; DTD/XXE surfaces are not executed."}


def analyze_csv(data: bytes) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return {"format": "csv", "valid": True, "rows": 0}
    header = rows[0]
    widths = {}
    for r in rows[1:]:
        widths[len(r)] = widths.get(len(r), 0) + 1
    return {"format": "csv", "valid": True, "delimiter": delim, "rows": len(rows) - 1,
            "columns": len(header), "header": header[:50], "row_width_histogram": widths,
            "sample": rows[1:6]}


# ---------------------------------------------------------------------------
# ASN.1 DER / BER
# ---------------------------------------------------------------------------

ASN1_TAGS = {0x01: "BOOLEAN", 0x02: "INTEGER", 0x03: "BIT STRING", 0x04: "OCTET STRING", 0x05: "NULL",
             0x06: "OBJECT IDENTIFIER", 0x0C: "UTF8String", 0x10: "SEQUENCE", 0x11: "SET",
             0x13: "PrintableString", 0x16: "IA5String", 0x17: "UTCTime", 0x18: "GeneralizedTime",
             0x30: "SEQUENCE", 0x31: "SET", 0xA0: "context[0]", 0xA3: "context[3]"}


def _oid_decode(b: bytes) -> str:
    if not b:
        return ""
    parts = [str(b[0] // 40), str(b[0] % 40)]
    val = 0
    for x in b[1:]:
        val = (val << 7) | (x & 0x7F)
        if not x & 0x80:
            parts.append(str(val))
            val = 0
    return ".".join(parts)


OID_NAMES = {"2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.10": "O", "2.5.4.11": "OU",
             "1.2.840.113549.1.1.1": "rsaEncryption", "1.2.840.113549.1.1.5": "sha1WithRSAEncryption",
             "1.2.840.113549.1.1.11": "sha256WithRSAEncryption", "1.2.840.113549.1.1.12": "sha384WithRSAEncryption",
             "1.2.840.113549.1.7.1": "data", "1.2.840.113549.1.7.2": "signedData",
             "1.2.840.10045.2.1": "ecPublicKey", "1.2.840.10045.4.3.2": "ecdsa-with-SHA256",
             "2.5.29.19": "basicConstraints", "2.5.29.15": "keyUsage", "2.5.29.37": "extKeyUsage",
             "1.3.6.1.5.5.7.3.3": "codeSigning"}


def parse_der(data: bytes, max_depth: int = 12, max_nodes: int = 5000) -> dict:
    count = [0]

    def walk(blob, off, end, depth):
        out = []
        while off < end and count[0] <= max_nodes:
            start = off
            if off + 2 > end:
                break
            tag = blob[off]
            ln = blob[off + 1]
            hdr = 2
            if ln & 0x80:
                n = ln & 0x7F
                if n == 0 or off + 2 + n > end:
                    break
                ln = int.from_bytes(blob[off + 2:off + 2 + n], "big")
                hdr = 2 + n
            off += hdr
            if off + ln > end:
                out.append({"offset": start, "tag": hex(tag), "error": "length beyond buffer"})
                break
            body = blob[off:off + ln]
            node = {"offset": start, "tag": ASN1_TAGS.get(tag, f"tag(0x{tag:02x})"), "tag_raw": hex(tag),
                    "length": ln, "depth": depth}
            count[0] += 1
            if tag & 0x20 and depth < max_depth:
                node["children"] = walk(blob, off, off + ln, depth + 1)
            elif tag == 0x02:
                node["value"] = int.from_bytes(body, "big") if body else 0
            elif tag == 0x06:
                oid = _oid_decode(body)
                node["value"] = oid
                node["name"] = OID_NAMES.get(oid, "")
            elif tag in (0x0C, 0x13, 0x16, 0x17, 0x18):
                node["value"] = body.decode("utf-8", errors="replace")
            elif tag == 0x01:
                node["value"] = bool(body[0]) if body else False
            elif tag == 0x05:
                node["value"] = None
            elif tag in (0x03, 0x04):
                node["value_hex"] = body[:64].hex()
                node["value_bytes"] = len(body)
            else:
                node["value_hex"] = body[:32].hex()
            out.append(node)
            off += ln
        return out

    tree = walk(data, 0, len(data), 0)
    return {"format": "der-asn1", "node_count": count[0], "tree": tree[:2000],
            "note": "TLV structure decoded; cryptographic verification is not performed."}


# ---------------------------------------------------------------------------
# Protocol Buffers (schema-less wire decode)
# ---------------------------------------------------------------------------

def decode_protobuf(data: bytes, max_depth: int = 8) -> dict:
    fields = []

    def walk(blob, depth):
        pos = 0
        out = []
        while pos < len(blob):
            start = pos
            key, pos = _varint(blob, pos)
            fnum, wtype = key >> 3, key & 7
            entry = {"field": fnum, "wire_type": wtype, "offset": start}
            if wtype == 0:
                val, pos = _varint(blob, pos)
                entry["value"] = val
                entry["zigzag"] = (val >> 1) ^ -(val & 1)
            elif wtype == 1:
                entry["value_hex"] = blob[pos:pos + 8].hex()
                entry["as_double"] = struct.unpack_from("<d", blob, pos)[0] if pos + 8 <= len(blob) else None
                pos += 8
            elif wtype == 2:
                ln, pos = _varint(blob, pos)
                body = blob[pos:pos + ln]
                pos += ln
                entry["length"] = ln
                # Prefer printable UTF-8 string; otherwise try nested message; else hex.
                txt = None
                try:
                    cand = body.decode("utf-8")
                    if cand and all(32 <= ord(c) < 127 or c in "\n\r\t" for c in cand):
                        txt = cand
                except UnicodeDecodeError:
                    pass
                if txt is not None:
                    entry["value_string"] = txt
                else:
                    nested = None
                    if depth < max_depth and ln > 0:
                        try:
                            nested = walk(body, depth + 1)
                            if nested:
                                entry["nested_message"] = nested
                        except Exception:
                            nested = None
                    if nested is None:
                        entry["value_hex"] = body[:64].hex()
            elif wtype == 5:
                entry["value_hex"] = blob[pos:pos + 4].hex()
                entry["as_float"] = struct.unpack_from("<f", blob, pos)[0] if pos + 4 <= len(blob) else None
                pos += 4
            else:
                entry["error"] = f"unsupported wire type {wtype}"
                break
            out.append(entry)
            if len(out) > 2000:
                break
        return out

    fields = walk(data, 0)
    schema = {}
    for f in fields:
        k = f["field"]
        schema.setdefault(k, {"wire_types": set(), "count": 0, "nested": False})
        schema[k]["wire_types"].add(f["wire_type"])
        schema[k]["count"] += 1
        if "nested_message" in f:
            schema[k]["nested"] = True
    schema_out = {str(k): {"wire_types": sorted(v["wire_types"]), "occurrences": v["count"],
                           "possibly_repeated": v["count"] > 1, "possibly_message": v["nested"]}
                  for k, v in schema.items()}
    return {"format": "protobuf-wire", "fields": fields[:1000], "inferred_schema": schema_out,
            "note": "Schema-less decode; field names/types are inferred from wire format only. Provide a .proto for named decoding."}


def _varint(blob, pos):
    val = 0
    shift = 0
    while True:
        if pos >= len(blob):
            raise RetoolError("truncated-varint", "protobuf varint truncated")
        b = blob[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
        if shift > 70:
            raise RetoolError("varint-too-long", "protobuf varint too long")
    return val, pos


# ---------------------------------------------------------------------------
# MessagePack
# ---------------------------------------------------------------------------

def decode_msgpack(data: bytes, max_items: int = 10000) -> dict:
    pos = [0]
    count = [0]

    def rd(n):
        if pos[0] + n > len(data):
            raise RetoolError("truncated-msgpack", "MessagePack truncated")
        b = data[pos[0]:pos[0] + n]
        pos[0] += n
        return b

    def val():
        count[0] += 1
        if count[0] > max_items:
            raise RetoolError("item-limit", "MessagePack item limit exceeded", exit_code=EXIT_LIMIT)
        b = rd(1)[0]
        if b <= 0x7F:
            return b
        if b >= 0xE0:
            return b - 256
        if 0x80 <= b <= 0x8F:
            return {str(val()): val() for _ in range(b & 0x0F)}
        if 0x90 <= b <= 0x9F:
            return [val() for _ in range(b & 0x0F)]
        if 0xA0 <= b <= 0xBF:
            return rd(b & 0x1F).decode("utf-8", errors="replace")
        if b == 0xC0:
            return None
        if b == 0xC2:
            return False
        if b == 0xC3:
            return True
        if b == 0xC4:
            return rd(rd(1)[0]).hex()
        if b == 0xC5:
            return rd(u16(rd(2), 0, "be")).hex()
        if b == 0xC6:
            return rd(u32(rd(4), 0, "be")).hex()
        if b == 0xCA:
            return struct.unpack(">f", rd(4))[0]
        if b == 0xCB:
            return struct.unpack(">d", rd(8))[0]
        if b == 0xCC:
            return rd(1)[0]
        if b == 0xCD:
            return u16(rd(2), 0, "be")
        if b == 0xCE:
            return u32(rd(4), 0, "be")
        if b == 0xCF:
            return u64(rd(8), 0, "be")
        if b == 0xD0:
            return int.from_bytes(rd(1), "big", signed=True)
        if b == 0xD1:
            return int.from_bytes(rd(2), "big", signed=True)
        if b == 0xD2:
            return int.from_bytes(rd(4), "big", signed=True)
        if b == 0xD3:
            return int.from_bytes(rd(8), "big", signed=True)
        if b == 0xD9:
            return rd(rd(1)[0]).decode("utf-8", errors="replace")
        if b == 0xDA:
            return rd(u16(rd(2), 0, "be")).decode("utf-8", errors="replace")
        if b == 0xDB:
            return rd(u32(rd(4), 0, "be")).decode("utf-8", errors="replace")
        if b == 0xDC:
            return [val() for _ in range(u16(rd(2), 0, "be"))]
        if b == 0xDD:
            return [val() for _ in range(u32(rd(4), 0, "be"))]
        if b == 0xDE:
            return {str(val()): val() for _ in range(u16(rd(2), 0, "be"))}
        if b == 0xDF:
            return {str(val()): val() for _ in range(u32(rd(4), 0, "be"))}
        if b in (0xD4, 0xD5, 0xD6, 0xD7, 0xD8):
            n = 1 << (b - 0xD4)
            t = int.from_bytes(rd(1), "big", signed=True)
            return {"__ext__": t, "data": rd(n).hex()}
        if b == 0xC7:
            n = rd(1)[0]
            t = int.from_bytes(rd(1), "big", signed=True)
            return {"__ext__": t, "data": rd(n).hex()}
        if b == 0xC8:
            n = u16(rd(2), 0, "be")
            t = int.from_bytes(rd(1), "big", signed=True)
            return {"__ext__": t, "data": rd(n).hex()}
        if b == 0xC9:
            n = u32(rd(4), 0, "be")
            t = int.from_bytes(rd(1), "big", signed=True)
            return {"__ext__": t, "data": rd(n).hex()}
        raise RetoolError("bad-msgpack", f"Unknown MessagePack byte 0x{b:02x} at {pos[0] - 1}")

    obj = val()
    return {"format": "msgpack", "value": _preview(obj), "consumed_bytes": pos[0], "total_bytes": len(data),
            "items_decoded": count[0]}


# ---------------------------------------------------------------------------
# CBOR
# ---------------------------------------------------------------------------

def decode_cbor(data: bytes, max_items: int = 10000) -> dict:
    pos = [0]
    count = [0]

    def rd(n):
        if pos[0] + n > len(data):
            raise RetoolError("truncated-cbor", "CBOR truncated")
        b = data[pos[0]:pos[0] + n]
        pos[0] += n
        return b

    def arg(ai):
        if ai < 24:
            return ai
        if ai == 24:
            return rd(1)[0]
        if ai == 25:
            return u16(rd(2), 0, "be")
        if ai == 26:
            return u32(rd(4), 0, "be")
        if ai == 27:
            return u64(rd(8), 0, "be")
        if ai == 31:
            return None  # indefinite
        raise RetoolError("bad-cbor", f"Bad additional info {ai}")

    def val():
        count[0] += 1
        if count[0] > max_items:
            raise RetoolError("item-limit", "CBOR item limit exceeded", exit_code=EXIT_LIMIT)
        ib = rd(1)[0]
        mt, ai = ib >> 5, ib & 31
        if mt == 0:
            return arg(ai)
        if mt == 1:
            return -1 - arg(ai)
        if mt == 2:
            if ai == 31:
                chunks = []
                while True:
                    if data[pos[0]] == 0xFF:
                        pos[0] += 1
                        break
                    chunks.append(val())
                return b"".join(chunks).hex()
            return rd(arg(ai)).hex()
        if mt == 3:
            if ai == 31:
                chunks = []
                while True:
                    if data[pos[0]] == 0xFF:
                        pos[0] += 1
                        break
                    chunks.append(val())
                return "".join(chunks)
            return rd(arg(ai)).decode("utf-8", errors="replace")
        if mt == 4:
            if ai == 31:
                out = []
                while data[pos[0]] != 0xFF:
                    out.append(val())
                pos[0] += 1
                return out
            return [val() for _ in range(arg(ai))]
        if mt == 5:
            if ai == 31:
                out = {}
                while data[pos[0]] != 0xFF:
                    k = val()
                    out[str(k)] = val()
                pos[0] += 1
                return out
            return {str(val()): val() for _ in range(arg(ai))}
        if mt == 6:
            return {"__tag__": arg(ai), "value": val()}
        if mt == 7:
            if ai == 20:
                return False
            if ai == 21:
                return True
            if ai == 22:
                return None
            if ai == 25:
                import struct as _s
                return _s.unpack(">e", rd(2))[0]
            if ai == 26:
                return struct.unpack(">f", rd(4))[0]
            if ai == 27:
                return struct.unpack(">d", rd(8))[0]
            if ai == 31:
                return "__break__"
            return {"__simple__": ai}
        raise RetoolError("bad-cbor", f"Bad major type {mt}")

    obj = val()
    return {"format": "cbor", "value": _preview(obj), "consumed_bytes": pos[0], "total_bytes": len(data),
            "items_decoded": count[0]}


# ---------------------------------------------------------------------------
# BSON
# ---------------------------------------------------------------------------

def decode_bson(data: bytes, max_items: int = 10000) -> dict:
    pos = [0]
    count = [0]

    def rd(n):
        if pos[0] + n > len(data):
            raise RetoolError("truncated-bson", "BSON truncated")
        b = data[pos[0]:pos[0] + n]
        pos[0] += n
        return b

    def cstr():
        end = data.find(b"\x00", pos[0])
        if end < 0:
            raise RetoolError("truncated-bson", "BSON cstring unterminated")
        s = data[pos[0]:end].decode("utf-8", errors="replace")
        pos[0] = end + 1
        return s

    def doc():
        count[0] += 1
        if count[0] > max_items:
            raise RetoolError("item-limit", "BSON item limit", exit_code=EXIT_LIMIT)
        size = u32(rd(4), 0, "le")
        end = pos[0] + size - 5
        out = {}
        while pos[0] < end:
            t = rd(1)[0]
            key = cstr()
            if t == 0x01:
                out[key] = struct.unpack("<d", rd(8))[0]
            elif t == 0x02:
                ln = u32(rd(4), 0, "le")
                out[key] = rd(ln - 1).decode("utf-8", errors="replace")
                rd(1)
            elif t == 0x03:
                out[key] = doc()
            elif t == 0x04:
                out[key] = list(doc().values())
            elif t == 0x05:
                ln = u32(rd(4), 0, "le")
                sub = rd(1)[0]
                out[key] = {"subtype": sub, "data": rd(ln).hex()}
            elif t == 0x06:
                out[key] = None
            elif t == 0x07:
                out[key] = rd(12).hex()
            elif t == 0x08:
                out[key] = bool(rd(1)[0])
            elif t == 0x09:
                out[key] = u64(rd(8), 0, "le")
            elif t == 0x0A:
                out[key] = None
            elif t == 0x10:
                out[key] = i32(rd(4), 0, "le")
            elif t == 0x11:
                out[key] = u64(rd(8), 0, "le")
            elif t == 0x12:
                out[key] = int.from_bytes(rd(8), "little", signed=True)
            elif t == 0x13:
                out[key] = rd(16).hex()
            else:
                out[key] = f"<unsupported bson type 0x{t:02x}>"
                break
        rd(1)  # trailing zero
        return out

    obj = doc()
    return {"format": "bson", "value": _preview(obj), "consumed_bytes": pos[0], "total_bytes": len(data)}


# ---------------------------------------------------------------------------
# Java serialization stream walk (no instantiation)
# ---------------------------------------------------------------------------

TC = {0x70: "NULL", 0x71: "REFERENCE", 0x72: "CLASSDESC", 0x73: "OBJECT", 0x74: "STRING",
      0x75: "ARRAY", 0x76: "CLASS", 0x77: "BLOCKDATA", 0x78: "ENDBLOCKDATA", 0x79: "RESET",
      0x7A: "BLOCKDATALONG", 0x7B: "EXCEPTION", 0x7C: "LONGSTRING", 0x7D: "PROXYCLASSDESC",
      0x7E: "ENUM"}


def analyze_java_serialization(data: bytes) -> dict:
    if data[:2] != b"\xac\xed":
        raise RetoolError("not-java-serialization", "Missing 0xACED magic")
    version = u16(data, 2, "be")
    tokens = []
    strings = []
    classes = []
    pos = 4
    depth_guard = 0
    while pos < len(data) and depth_guard < 5000:
        depth_guard += 1
        t = data[pos]
        pos += 1
        tokens.append(TC.get(t, hex(t)))
        if t == 0x74:  # STRING
            ln = u16(data, pos, "be")
            pos += 2
            strings.append(data[pos:pos + ln].decode("utf-8", errors="replace"))
            pos += ln
        elif t == 0x7C:  # LONGSTRING
            ln = u64(data, pos, "be")
            pos += 8
            strings.append(data[pos:pos + min(ln, 4096)].decode("utf-8", errors="replace"))
            pos += ln
        elif t == 0x72:  # CLASSDESC
            ln = u16(data, pos, "be")
            pos += 2
            cname = data[pos:pos + ln].decode("utf-8", errors="replace")
            pos += ln
            classes.append(cname)
            pos += 8  # serialVersionUID
            if pos < len(data):
                pos += 1  # flags
            # skip field descriptors conservatively
            if pos + 2 <= len(data):
                nfields = u16(data, pos, "be")
                pos += 2
                for _ in range(min(nfields, 256)):
                    if pos >= len(data):
                        break
                    pos += 1  # typecode
                    if pos + 2 <= len(data):
                        nl = u16(data, pos, "be")
                        pos += 2 + nl
        elif t == 0x77:  # BLOCKDATA
            if pos < len(data):
                ln = data[pos]
                pos += 1 + ln
        elif t == 0x7A:
            if pos + 4 <= len(data):
                ln = u32(data, pos, "be")
                pos += 4 + ln
        elif t in (0x78, 0x79, 0x70):
            continue
        elif t == 0x71:
            pos += 4
        else:
            # unknown token; stop conservative walk
            break
        if len(tokens) > 4000:
            break
    risky = [c for c in classes if any(x in c for x in ("Invoker", "Transformer", "TemplatesImpl", "AnnotationInvocationHandler", "BadAttributeValueExpException", "JdbcRowSet", "ProcessBuilder", "Runtime"))]
    return {"format": "java-serialization", "version": version, "token_count": len(tokens),
            "tokens_sample": tokens[:200], "strings_sample": strings[:100], "classes": classes[:100],
            "gadget_risk_classes": risky, "executed": False,
            "note": "Stream walked structurally without instantiating objects. Gadget-class names are indicators, not proof of exploitability."}


# ---------------------------------------------------------------------------
# SQLite (read-only)
# ---------------------------------------------------------------------------

def analyze_sqlite(path: Path, cfg: dict) -> dict:
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA query_only = ON")
    out = {"format": "sqlite", "read_only": True}
    try:
        out["integrity_check"] = [r[0] for r in con.execute("PRAGMA integrity_check")]
        out["page_size"] = con.execute("PRAGMA page_size").fetchone()[0]
        out["page_count"] = con.execute("PRAGMA page_count").fetchone()[0]
        out["freelist_count"] = con.execute("PRAGMA freelist_count").fetchone()[0]
        out["encoding"] = con.execute("PRAGMA encoding").fetchone()[0]
        out["user_version"] = con.execute("PRAGMA user_version").fetchone()[0]
        out["application_id"] = con.execute("PRAGMA application_id").fetchone()[0]
        schema = list(con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"))
        out["schema"] = [{"type": t, "name": n, "table": tn, "sql": s} for t, n, tn, s in schema[:200]]
        tables = [r[1] for r in schema if r[0] == "table"]
        counts = {}
        for t in tables[:50]:
            try:
                counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error as e:
                counts[t] = f"error: {e}"
        out["table_row_counts"] = counts
        out["wal_file_present"] = path.with_suffix(path.suffix + "-wal").exists()
        out["journal_file_present"] = path.with_suffix(path.suffix + "-journal").exists()
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# Windows registry hive (structure-level)
# ---------------------------------------------------------------------------

def analyze_regf(data: bytes) -> dict:
    if data[:4] != b"regf":
        raise RetoolError("not-regf", "Missing regf signature")
    seq1 = u32(data, 4)
    seq2 = u32(data, 8)
    timestamp = u64(data, 12)
    major = u32(data, 20)
    minor = u32(data, 24)
    root_offset = u32(data, 36)
    hive_size = u32(data, 40)
    name = data[48:48 + 64].decode("utf-16le", errors="replace").rstrip("\x00")
    # hbin census
    pos = 4096
    hbins = 0
    nk_count = 0
    vk_count = 0
    sk_count = 0
    while pos + 32 <= len(data) and hbins < 100000:
        if data[pos:pos + 4] != b"hbin":
            break
        size = u32(data, pos + 8)
        if size < 32 or size % 4096:
            break
        end = min(pos + size, len(data))
        p = pos + 32
        while p + 4 <= end:
            cell_size = i32(data, p)
            if cell_size == 0:
                break
            alloc = cell_size < 0
            csize = abs(cell_size)
            if csize < 8 or p + csize > end:
                break
            sig = data[p + 4:p + 6]
            if sig == b"nk":
                nk_count += 1
            elif sig == b"vk":
                vk_count += 1
            elif sig == b"sk":
                sk_count += 1
            p += csize
        pos += size
        hbins += 1
    return {"format": "windows-registry", "sequence_match": seq1 == seq2, "timestamp_filetime": timestamp,
            "version": f"{major}.{minor}", "root_cell_offset": root_offset, "hive_size": hive_size,
            "hive_name_hint": name, "hbin_blocks": hbins, "nk_cells": nk_count, "vk_cells": vk_count,
            "sk_cells": sk_count,
            "limitation": "Header + cell census only; full key/value tree reconstruction requires a dedicated hive parser."}


# ---------------------------------------------------------------------------
# FlatBuffers / Cap'n Proto framing
# ---------------------------------------------------------------------------

def analyze_flatbuffers(data: bytes) -> dict:
    if len(data) < 8:
        raise RetoolError("too-short", "FlatBuffers buffer too short")
    root_off = u32(data, 0)
    ident = data[4:8]
    issues = []
    if root_off >= len(data):
        issues.append("root offset beyond buffer")
    if ident == b"TFL3":
        issues.append("TensorFlow Lite identifier present (use parse --format for model detail)")
    return {"format": "flatbuffers", "root_offset": root_off, "file_identifier": ident.decode("ascii", errors="replace"),
            "issues": issues, "note": "Framing check only; table/field decode requires the schema (.fbs)."}


def analyze_capnp(data: bytes) -> dict:
    if len(data) < 8:
        raise RetoolError("too-short", "Cap'n Proto segment too short")
    seg_count = u32(data, 0) + 1
    if seg_count > 512:
        raise RetoolError("bad-capnp", "Implausible segment count")
    seg_sizes = [u32(data, 4 + i * 4) for i in range(min(seg_count, 64))]
    return {"format": "capnp", "segment_count": seg_count, "segment_sizes_words": seg_sizes,
            "note": "Framing check only; struct decode requires the schema (.capnp)."}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

DATA_FORMATS = ["json", "toml", "ini", "yaml", "xml", "csv", "der", "protobuf", "msgpack", "cbor",
                "bson", "java-serialization", "sqlite", "regf", "flatbuffers", "capnp"]


def detect_data_format(data: bytes, name: str) -> str:
    ext = Path(name).suffix.lower()
    extmap = {".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".yaml": "yaml", ".yml": "yaml",
              ".xml": "xml", ".csv": "csv", ".tsv": "csv", ".der": "der", ".cer": "der", ".crt": "der",
              ".pb": "protobuf", ".proto": "protobuf", ".bin": "protobuf", ".msgpack": "msgpack",
              ".mpack": "msgpack", ".cbor": "cbor", ".bson": "bson", ".ser": "java-serialization",
              ".sqlite": "sqlite", ".db": "sqlite", ".sqlite3": "sqlite", ".reg": "regf", ".hive": "regf"}
    if data[:16] == b"SQLite format 3\x00":
        return "sqlite"
    if data[:4] == b"regf":
        return "regf"
    if data[:2] == b"\xac\xed":
        return "java-serialization"
    if data[:1] == b"\x30" and len(data) > 4:
        return "der"
    if data[:1] in (b"{", b"["):
        return "json"
    if data[:5] == b"<?xml" or data[:1] == b"<":
        return "xml"
    if ext in extmap:
        return extmap[ext]
    if data[:1] == b"\x82" or (data and data[0] in range(0x80, 0x90)):
        return "msgpack"
    return "json"


def cmd_data(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    if args.format and args.format != "auto":
        fmt = args.format
    else:
        head = read_bytes(path, cfg, 0, min(path.stat().st_size, 8192))
        fmt = detect_data_format(head, path.name)
    env["inputs"] = {"path": str(path), "format": fmt}
    if fmt == "sqlite":
        env["result"] = analyze_sqlite(path, cfg)
        return env
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    analyzers = {
        "json": lambda d: analyze_json(d, cfg), "toml": analyze_toml, "ini": analyze_ini,
        "yaml": analyze_yaml, "xml": analyze_xml, "csv": analyze_csv, "der": parse_der,
        "protobuf": decode_protobuf, "msgpack": decode_msgpack, "cbor": decode_cbor, "bson": decode_bson,
        "java-serialization": analyze_java_serialization, "regf": analyze_regf,
        "flatbuffers": analyze_flatbuffers, "capnp": analyze_capnp,
    }
    if fmt not in analyzers:
        raise RetoolError("unsupported-format", f"No data analyzer for: {fmt}. Supported: {', '.join(DATA_FORMATS)}", exit_code=EXIT_USAGE)
    env["result"] = analyzers[fmt](data)
    if "limitation" in env["result"]:
        env["limitations"].append(env["result"]["limitation"])
    return env
