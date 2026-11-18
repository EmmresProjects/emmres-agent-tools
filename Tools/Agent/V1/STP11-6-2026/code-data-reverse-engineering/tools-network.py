"""Network and protocol reconstruction: pcap/pcapng, HTTP, TLS, DNS, WebSocket,
identifier/token flow. Offline analysis of supplied captures only; no live traffic."""
from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, read_bytes, validate_input_file, parse_int,
                        u16, u32, u64, hash_bytes)

PCAP_MAGICS = {b"\xd4\xc3\xb2\xa1": ("le", "us"), b"\xa1\xb2\xc3\xd4": ("be", "us"),
               b"\x34\xcd\xb2\xa1": ("le", "ns"), b"\xa1\xb2\xcd\x34": ("be", "ns")}


def _mac(b):
    return ":".join(f"{x:02x}" for x in b)


def _ip4(b):
    return ".".join(str(x) for x in b)


def _ip6(b):
    return ":".join(f"{u16(b, i, 'be'):x}" for i in range(0, 16, 2))


def decode_dns_name(data: bytes, off: int, depth=0) -> tuple[str, int]:
    labels = []
    jumped = False
    orig_end = None
    while depth < 32:
        if off >= len(data):
            break
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                break
            ptr = ((ln & 0x3F) << 8) | data[off + 1]
            if not jumped:
                orig_end = off + 2
            off = ptr
            jumped = True
            depth += 1
            continue
        off += 1
        labels.append(data[off:off + ln].decode("utf-8", errors="replace"))
        off += ln
        depth += 1
    name = ".".join(labels)
    return name, (orig_end if jumped else off)


def decode_dns(data: bytes) -> dict:
    if len(data) < 12:
        return {"error": "DNS message too short"}
    tid = u16(data, 0, "be")
    flags = u16(data, 2, "be")
    qd = u16(data, 4, "be")
    an = u16(data, 6, "be")
    ns = u16(data, 8, "be")
    ar = u16(data, 10, "be")
    qr = (flags >> 15) & 1
    opcode = (flags >> 11) & 15
    rcode = flags & 15
    pos = 12
    questions = []
    for _ in range(min(qd, 64)):
        name, pos = decode_dns_name(data, pos)
        if pos + 4 > len(data):
            break
        qtype = u16(data, pos, "be")
        qclass = u16(data, pos + 2, "be")
        pos += 4
        questions.append({"name": name, "type": qtype, "class": qclass})
    answers = []
    for _ in range(min(an + ns + ar, 128)):
        name, pos = decode_dns_name(data, pos)
        if pos + 10 > len(data):
            break
        rtype = u16(data, pos, "be")
        rclass = u16(data, pos + 2, "be")
        ttl = u32(data, pos + 4, "be")
        rdlen = u16(data, pos + 8, "be")
        pos += 10
        rdata = data[pos:pos + rdlen]
        pos += rdlen
        entry = {"name": name, "type": rtype, "ttl": ttl}
        if rtype == 1 and rdlen == 4:
            entry["address"] = _ip4(rdata)
        elif rtype == 28 and rdlen == 16:
            entry["address"] = _ip6(rdata)
        elif rtype in (2, 5, 12):
            entry["target"], _ = decode_dns_name(data, pos - rdlen)
        elif rtype == 16:
            entry["txt"] = rdata.decode("utf-8", errors="replace")
        else:
            entry["rdata_hex"] = rdata[:64].hex()
        answers.append(entry)
    return {"transaction_id": hex(tid), "is_response": bool(qr), "opcode": opcode, "rcode": rcode,
            "questions": questions, "answers": answers,
            "counts": {"questions": qd, "answers": an, "authority": ns, "additional": ar}}


def decode_packet(payload: bytes, linktype: int) -> dict:
    """Decode Ethernet/raw-IP/SLL link layer down to L4 + app hints."""
    out = {}
    pos = 0
    if linktype == 1:  # Ethernet
        if len(payload) < 14:
            return {"error": "short ethernet"}
        out["eth"] = {"dst": _mac(payload[0:6]), "src": _mac(payload[6:12]),
                      "ethertype": hex(u16(payload, 12, "be"))}
        pos = 14
        et = u16(payload, 12, "be")
        if et == 0x8100:  # VLAN
            et = u16(payload, 16, "be")
            pos = 18
    elif linktype == 101:  # raw IP
        et = 0x0800 if (payload[0] >> 4) == 4 else 0x86DD
    elif linktype == 113:  # Linux SLL
        if len(payload) < 16:
            return {"error": "short sll"}
        et = u16(payload, 14, "be")
        pos = 16
    else:
        return {"linktype": linktype, "note": "unsupported link layer; payload kept raw"}
    if et == 0x0800 and pos + 20 <= len(payload):
        ihl = (payload[pos] & 0x0F) * 4
        proto = payload[pos + 9]
        src = _ip4(payload[pos + 12:pos + 16])
        dst = _ip4(payload[pos + 16:pos + 20])
        total = u16(payload, pos + 2, "be")
        out["ipv4"] = {"src": src, "dst": dst, "protocol": proto, "ttl": payload[pos + 8],
                       "total_length": total, "ihl": ihl}
        l4 = pos + ihl
        _decode_l4(payload, l4, proto, out)
    elif et == 0x86DD and pos + 40 <= len(payload):
        proto = payload[pos + 6]
        src = _ip6(payload[pos + 8:pos + 24])
        dst = _ip6(payload[pos + 24:pos + 40])
        out["ipv6"] = {"src": src, "dst": dst, "next_header": proto, "payload_length": u16(payload, pos + 4, "be")}
        _decode_l4(payload, pos + 40, proto, out)
    elif et == 0x0806:
        out["arp"] = {"opcode": u16(payload, pos + 6, "be")}
    return out


def _decode_l4(payload: bytes, l4: int, proto: int, out: dict):
    if proto == 6 and l4 + 20 <= len(payload):  # TCP
        sport, dport = u16(payload, l4, "be"), u16(payload, l4 + 2, "be")
        seq = u32(payload, l4 + 4, "be")
        ack = u32(payload, l4 + 8, "be")
        off = (payload[l4 + 12] >> 4) * 4
        flags = payload[l4 + 13]
        out["tcp"] = {"src_port": sport, "dst_port": dport, "seq": seq, "ack": ack,
                      "flags": _tcp_flags(flags), "header_len": off, "window": u16(payload, l4 + 14, "be")}
        app = payload[l4 + off:]
        out["payload_len"] = len(app)
        _app_hints(app, sport, dport, out)
    elif proto == 17 and l4 + 8 <= len(payload):  # UDP
        sport, dport = u16(payload, l4, "be"), u16(payload, l4 + 2, "be")
        ulen = u16(payload, l4 + 4, "be")
        out["udp"] = {"src_port": sport, "dst_port": dport, "length": ulen}
        app = payload[l4 + 8:]
        out["payload_len"] = len(app)
        _app_hints(app, sport, dport, out)
    elif proto == 1:
        out["icmp"] = {"type": payload[l4], "code": payload[l4 + 1]}


def _tcp_flags(f):
    names = [(0x01, "FIN"), (0x02, "SYN"), (0x04, "RST"), (0x08, "PSH"), (0x10, "ACK"), (0x20, "URG"), (0x40, "ECE"), (0x80, "CWR")]
    return "|".join(n for b, n in names if f & b) or "none"


def _app_hints(app: bytes, sport: int, dport: int, out: dict):
    if not app:
        return
    if dport == 53 or sport == 53:
        out["dns"] = decode_dns(app)
        return
    if app[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD", b"DELE", b"OPTI", b"PATC") or app[:5] == b"HTTP/":
        text = app[:2048].decode("utf-8", errors="replace")
        first = text.split("\r\n")[0]
        out["http"] = {"start_line": first, "preview": text[:512]}
        return
    if app[0] == 0x16 and len(app) > 5:  # TLS handshake record
        out["tls"] = decode_tls_record(app)
        return
    if dport in (80, 8080, 443, 8443) or sport in (80, 8080, 443, 8443):
        out["app_guess"] = "http/tls on common port (payload not recognized)"


def decode_tls_record(data: bytes) -> dict:
    if len(data) < 5 or data[0] != 0x16:
        return {"error": "not a TLS handshake record"}
    ctype = data[0]
    version = u16(data, 1, "be")
    length = u16(data, 3, "be")
    out = {"content_type": ctype, "record_version": hex(version), "record_length": length}
    body = data[5:5 + length]
    if not body or body[0] != 0x01:  # ClientHello
        out["handshake_type"] = body[0] if body else None
        return out
    hs_len = int.from_bytes(body[1:4], "big")
    pos = 4
    client_version = u16(body, pos, "be")
    pos += 2
    random = body[pos:pos + 32].hex()
    pos += 32
    sid_len = body[pos]
    pos += 1 + sid_len
    cs_len = u16(body, pos, "be")
    pos += 2
    ciphers = [hex(u16(body, pos + i, "be")) for i in range(0, min(cs_len, 256), 2)]
    pos += cs_len
    comp_len = body[pos]
    pos += 1 + comp_len
    sni = []
    alpn = []
    supported_versions = []
    if pos + 2 <= len(body):
        ext_len = u16(body, pos, "be")
        pos += 2
        end = pos + ext_len
        while pos + 4 <= min(end, len(body)):
            etype = u16(body, pos, "be")
            elen = u16(body, pos + 2, "be")
            edata = body[pos + 4:pos + 4 + elen]
            pos += 4 + elen
            if etype == 0:  # SNI
                if len(edata) >= 5:
                    name_len = u16(edata, 3, "be")
                    sni.append(edata[5:5 + name_len].decode("utf-8", errors="replace"))
            elif etype == 16:  # ALPN
                p = 0
                while p < len(edata):
                    l = edata[p]
                    p += 1
                    alpn.append(edata[p:p + l].decode("utf-8", errors="replace"))
                    p += l
            elif etype == 43:  # supported_versions
                if edata:
                    vl = edata[0]
                    supported_versions = [hex(u16(edata, 1 + i, "be")) for i in range(0, min(vl, 16), 2)]
    out.update({"handshake": "ClientHello", "client_version": hex(client_version), "random": random,
                "cipher_suites": ciphers, "sni": sni, "alpn": alpn, "supported_versions": supported_versions})
    return out


def parse_pcap(data: bytes, max_packets: int = 5000) -> dict:
    if data[:4] not in PCAP_MAGICS:
        raise RetoolError("not-pcap", "Missing pcap magic")
    endian, unit = PCAP_MAGICS[data[:4]]
    eb = "little" if endian == "le" else "big"
    vmaj = u16(data, 4, endian)
    vmin = u16(data, 6, endian)
    snaplen = u32(data, 16, endian)
    linktype = u32(data, 20, endian)
    pos = 24
    packets = []
    flows = {}
    dns_msgs = []
    http_msgs = []
    tls_msgs = []
    while pos + 16 <= len(data) and len(packets) < max_packets:
        ts_sec = int.from_bytes(data[pos:pos + 4], eb)
        ts_frac = int.from_bytes(data[pos + 4:pos + 8], eb)
        incl = int.from_bytes(data[pos + 8:pos + 12], eb)
        orig = int.from_bytes(data[pos + 12:pos + 16], eb)
        pos += 16
        if incl > len(data) - pos:
            break
        payload = data[pos:pos + incl]
        pos += incl
        ts = ts_sec + ts_frac / (1e9 if unit == "ns" else 1e6)
        dec = decode_packet(payload, linktype)
        pkt = {"index": len(packets), "timestamp": round(ts, 6), "captured_len": incl, "original_len": orig,
               "decoded": dec}
        packets.append(pkt)
        # flow key
        ip = dec.get("ipv4") or dec.get("ipv6")
        l4 = dec.get("tcp") or dec.get("udp")
        if ip and l4:
            proto = "tcp" if "tcp" in dec else "udp"
            key = f"{ip['src']}:{l4['src_port']}->{ip['dst']}:{l4['dst_port']}/{proto}"
            f = flows.setdefault(key, {"packets": 0, "bytes": 0, "first_ts": ts, "last_ts": ts})
            f["packets"] += 1
            f["bytes"] += orig
            f["last_ts"] = ts
        if "dns" in dec and "error" not in dec["dns"]:
            dns_msgs.append({"packet": pkt["index"], **dec["dns"]})
        if "http" in dec:
            http_msgs.append({"packet": pkt["index"], **dec["http"]})
        if "tls" in dec and "handshake" in dec["tls"]:
            tls_msgs.append({"packet": pkt["index"], **dec["tls"]})
    return {"format": "pcap", "version": f"{vmaj}.{vmin}", "timestamp_unit": unit, "snaplen": snaplen,
            "linktype": linktype, "packet_count": len(packets), "packets": packets[:1000],
            "flows": [{"flow": k, **v} for k, v in list(flows.items())[:500]],
            "dns_messages": dns_msgs[:200], "http_messages": http_msgs[:200], "tls_client_hellos": tls_msgs[:100],
            "limitation": "Per-packet decode; no full TCP stream reassembly. HTTP/TLS shown per packet."}


def parse_pcapng(data: bytes, max_packets: int = 5000) -> dict:
    if data[:4] != b"\x0a\x0d\x0d\x0a":
        raise RetoolError("not-pcapng", "Missing pcapng SHB")
    pos = 0
    interfaces = []
    packets = []
    endian = "le"
    while pos + 12 <= len(data) and len(packets) < max_packets:
        btype = u32(data, pos, endian)
        blen = u32(data, pos + 4, endian)
        if blen < 12 or pos + blen > len(data):
            break
        body = data[pos + 8:pos + blen - 4]
        if btype == 0x0A0D0D0A:  # SHB
            bom = body[:4]
            endian = "le" if bom == b"\x4d\x3c\x2b\x1a" else "be"
        elif btype == 0x00000001:  # IDB
            linktype = u16(body, 0, endian)
            snaplen = u32(body, 4, endian)
            interfaces.append({"linktype": linktype, "snaplen": snaplen})
        elif btype == 0x00000006:  # EPB
            iface = u32(body, 0, endian)
            ts_high = u32(body, 4, endian)
            ts_low = u32(body, 8, endian)
            caplen = u32(body, 12, endian)
            origlen = u32(body, 16, endian)
            payload = body[20:20 + caplen]
            lt = interfaces[iface]["linktype"] if iface < len(interfaces) else 1
            dec = decode_packet(payload, lt)
            ts = ((ts_high << 32) | ts_low) / 1e6
            packets.append({"index": len(packets), "interface": iface, "timestamp": round(ts, 6),
                            "captured_len": caplen, "original_len": origlen, "decoded": dec})
        elif btype == 0x00000003:  # SPB
            origlen = u32(body, 0, endian)
            payload = body[4:4 + origlen]
            lt = interfaces[0]["linktype"] if interfaces else 1
            packets.append({"index": len(packets), "simple": True, "decoded": decode_packet(payload, lt)})
        pos += blen
    return {"format": "pcapng", "interfaces": interfaces, "packet_count": len(packets),
            "packets": packets[:1000],
            "limitation": "SHB/IDB/EPB/SPB blocks decoded; name resolution and custom blocks are skipped."}


def parse_http_text(data: bytes) -> dict:
    text = data.decode("utf-8", errors="replace")
    head, _, body = text.partition("\r\n\r\n")
    if "\r\n\r\n" not in text:
        head, _, body = text.partition("\n\n")
    lines = head.splitlines()
    if not lines:
        raise RetoolError("bad-http", "Empty HTTP message")
    start = lines[0]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    is_request = not start.startswith("HTTP/")
    out = {"start_line": start, "headers": headers, "body_preview": body[:1024], "body_bytes": len(body.encode())}
    if is_request:
        parts = start.split()
        if len(parts) >= 2:
            out["method"] = parts[0]
            out["target"] = parts[1]
            out["version"] = parts[2] if len(parts) > 2 else None
            from urllib.parse import urlsplit, parse_qs
            sp = urlsplit(parts[1])
            out["path"] = sp.path
            out["query"] = {k: v for k, v in parse_qs(sp.query).items()}
    else:
        parts = start.split(None, 2)
        if len(parts) >= 2:
            out["version"] = parts[0]
            out["status"] = int(parts[1]) if parts[1].isdigit() else None
            out["reason"] = parts[2] if len(parts) > 2 else ""
    ct = headers.get("content-type", "")
    if "json" in ct and body.strip():
        try:
            out["json_body"] = json.loads(body)
        except json.JSONDecodeError:
            pass
    return {"format": "http", "kind": "request" if is_request else "response", **out}


def decode_websocket_frame(data: bytes) -> dict:
    if len(data) < 2:
        raise RetoolError("bad-websocket", "Frame too short")
    b0, b1 = data[0], data[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    ln = b1 & 0x7F
    pos = 2
    if ln == 126:
        ln = u16(data, pos, "be")
        pos += 2
    elif ln == 127:
        ln = u64(data, pos, "be")
        pos += 8
    mask = data[pos:pos + 4] if masked else None
    if masked:
        pos += 4
    payload = data[pos:pos + ln]
    if masked and mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    opnames = {0: "continuation", 1: "text", 2: "binary", 8: "close", 9: "ping", 10: "pong"}
    out = {"fin": fin, "opcode": opnames.get(opcode, opcode), "masked": masked, "payload_length": ln,
           "payload_preview": payload[:512].decode("utf-8", errors="replace") if opcode == 1 else payload[:256].hex()}
    if opcode == 8 and len(payload) >= 2:
        out["close_code"] = u16(payload, 0, "be")
        out["close_reason"] = payload[2:].decode("utf-8", errors="replace")
    return {"format": "websocket-frame", **out}


def analyze_tokens(data: bytes) -> dict:
    text = data.decode("utf-8", errors="replace")
    findings = {"jwt": [], "uuid": [], "urls": [], "api_key_patterns": []}
    for m in re.finditer(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", text):
        token = m.group(0)
        parts = token.split(".")
        entry = {"offset": m.start(), "fingerprint": hash_bytes(token.encode())["sha256"][:12]}
        for i, label in ((0, "header"), (1, "payload")):
            try:
                pad = parts[i] + "=" * (-len(parts[i]) % 4)
                decoded = base64.urlsafe_b64decode(pad)
                entry[label] = json.loads(decoded)
            except Exception:
                entry[label] = None
        entry["verified"] = False
        entry["note"] = "Decoded without signature verification; never trust claims without verifying."
        findings["jwt"].append(entry)
    for m in re.finditer(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", text):
        findings["uuid"].append({"offset": m.start(), "value": m.group(0)})
    for m in re.finditer(r"\bhttps?://[^\s\"'<>]+", text):
        findings["urls"].append({"offset": m.start(), "value": m.group(0)[:200]})
    for m in re.finditer(r"\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b", text):
        findings["api_key_patterns"].append({"offset": m.start(), "fingerprint": hash_bytes(m.group(0).encode())["sha256"][:12]})
    return {"format": "token-flow", "counts": {k: len(v) for k, v in findings.items()}, "findings": findings,
            "note": "Identifiers are extracted for flow analysis; secret values are fingerprinted, not printed."}


def cmd_network(args, env, cfg):
    mode = args.mode or "auto"
    if args.text is not None and mode in ("http", "websocket", "tokens"):
        data = args.text.encode()
        source = "<text>"
    else:
        path = validate_input_file(args.path, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        source = str(path)
    if mode == "auto":
        if data[:4] in PCAP_MAGICS:
            mode = "pcap"
        elif data[:4] == b"\x0a\x0d\x0d\x0a":
            mode = "pcapng"
        elif data[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD") or data[:5] == b"HTTP/":
            mode = "http"
        elif data[:1] == b"\x16":
            mode = "tls"
        else:
            mode = "tokens"
    env["inputs"] = {"source": source, "mode": mode}
    if mode == "pcap":
        env["result"] = parse_pcap(data, cfg["limits"]["max_inventory_files"])
    elif mode == "pcapng":
        env["result"] = parse_pcapng(data, cfg["limits"]["max_inventory_files"])
    elif mode == "http":
        env["result"] = parse_http_text(data)
    elif mode == "tls":
        env["result"] = {"format": "tls", "record": decode_tls_record(data)}
    elif mode == "dns":
        env["result"] = {"format": "dns", "message": decode_dns(data)}
    elif mode == "websocket":
        env["result"] = decode_websocket_frame(data)
    elif mode == "tokens":
        env["result"] = analyze_tokens(data)
    else:
        raise RetoolError("bad-mode", f"Unknown network mode: {mode}", exit_code=EXIT_USAGE)
    if "limitation" in env["result"]:
        env["limitations"].append(env["result"]["limitation"])
    return env
