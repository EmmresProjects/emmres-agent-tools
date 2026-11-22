#!/usr/bin/env python3
"""android-agent-toolkit :: triage, storage, security, compatibility, preservation (module 5 of 7).

Command groups implemented here:
  triage     analyze <log...>            classify Java crash / ANR / native tombstone / installer failure blocks
             retrace --mapping M --trace T   ProGuard/R8 class + method + line retrace
  storage    sqlite <db> [--diff db2] | assets <dir> | validate <file>
  security   scan <apk> | scan --decoded <dir>      read-only static findings (no network, no execution)
  compat     check <apk> [--device-api N] [--release] [--out f.md] | levels | matrix | rules [--topic T]
  preserve   provenance | snapshot | package | report [--apk ...] [--gate results.json]
  recovery   assess <apk>                    ordered recovery plan from read-only evidence

Every write goes to a validated output path; overwriting or deleting requires --yes.
"""

import collections
import datetime
import json
import os
import re
import shutil
import sqlite3
import zipfile

import aatk_core as core

ARTIFACTS = core.get_sibling("tools-artifact-analysis.py")
ADAPTERS = core.get_sibling("tools-tool-adapters.py")

MAX_TEXT = 4 * 1024 * 1024


def _matrix():
    return core.load_json(core.resolve_asset("asset-compat-matrix.json"), "compat matrix")


def _read_text(path, cap=MAX_TEXT):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        raw = fh.read(min(size, cap))
    return raw.decode("utf-8", "replace"), size > cap


# ---------------------------------------------------------------------------
# triage analyze
# ---------------------------------------------------------------------------

BLOCK_HDR = re.compile(r"^--- BLOCK\b.*$", re.M)
INSTALL_CODE = re.compile(r"\b(INSTALL_(?:FAILED|PARSE_FAILED)_[A-Z_]+)\b")


def split_blocks(text):
    """Split on '--- BLOCK ...' headers; a plain log is one block."""
    heads = list(BLOCK_HDR.finditer(text))
    if not heads:
        return [("", text)]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((m.group(0).strip("- ").strip(), text[m.end():end]))
    return out


SIGNALS = {
    "java_crash": [(r"FATAL EXCEPTION", 3), (r"AndroidRuntime.*Process:|Process: \S+, PID", 1),
                   (r"Caused by: ", 1), (r"\tat [\w$.]+\(", 1)],
    "anr": [(r"ANR in ", 3), (r"am_anr", 3), (r"Reason: ", 1), (r"CPU usage", 1)],
    "native_tombstone": [(r"\*\*\* \*\*\* \*\*\*", 3), (r"signal \d+ \(SIG[A-Z]+\)", 3),
                         (r"backtrace:", 1), (r"#00 pc [0-9a-fA-F]+", 2)],
    "native_link_failure": [(r"CANNOT LINK EXECUTABLE", 3), (r"dlopen failed", 3),
                            (r"UnsatisfiedLinkError", 2), (r"library \"[^\"]+\" not found", 2)],
    "installer_failure": [(r"INSTALL_(?:FAILED|PARSE_FAILED)_[A-Z_]+", 3), (r"PackageManager", 1)],
}


def classify_scores(block):
    scores = {}
    for kind, sigs in SIGNALS.items():
        total = 0
        for pattern, weight in sigs:
            if re.search(pattern, block):
                total += weight
        if total:
            scores[kind] = total
    return scores


def classify(block):
    """Primary kind = highest weighted signal score (ties keep SIGNALS order)."""
    scores = classify_scores(block)
    if not scores:
        return "unknown", {}
    primary = max(SIGNALS, key=lambda k: scores.get(k, -1))
    return primary, {k: v for k, v in scores.items() if k != primary}


def _java_fields(block):
    f = {}
    m = re.search(r"Process: ([^,\s]+)(?:, PID: (\d+))?", block)
    if m:
        f["process"] = m.group(1)
        f["pid"] = m.group(2)
    exc = re.search(r"^(?:.*?AndroidRuntime:\s*)?((?:[a-z_][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error)[^\n]*)", block, re.M)
    if exc:
        f["exception"] = exc.group(1).strip()[:300]
    causes = re.findall(r"Caused by: ([^\n]+)", block)
    if causes:
        f["caused_by"] = [c.strip()[:300] for c in causes]
    frames = re.findall(r"\bat ([\w$.<>]+\([^)]*\))", block)
    f["frames"] = frames[:40]
    app = [x for x in frames if not x.startswith(("android.", "java.", "com.android.", "kotlin.", "dalvik."))]
    f["first_app_frame"] = app[0] if app else None
    return f


def _anr_fields(block):
    f = {}
    m = re.search(r"ANR in ([^\s(]+)", block)
    if m:
        f["process"] = m.group(1)
    m = re.search(r"Reason: ([^\n]+)", block)
    if m:
        f["reason"] = m.group(1).strip()[:300]
    m = re.search(r'"main"[^\n]*\n((?:\s+[^\n]+\n?){1,12})', block)
    if m:
        f["main_thread_top"] = [ln.strip() for ln in m.group(1).splitlines() if ln.strip().startswith(("at ", "- "))][:10]
    f["cpu_usage_lines"] = re.findall(r"CPU usage[^\n]*", block)[:3]
    return f


def _tomb_fields(block):
    f = {}
    m = re.search(r"signal (\d+) \((SIG[A-Z]+)\)[^\n]*", block)
    if m:
        f["signal"] = "%s (%s)" % (m.group(1), m.group(2))
    m = re.search(r"pid: (\d+), tid: (\d+), name: ([^\s>]+)\s+>>> ([^\s<]+) <<<", block)
    if m:
        f.update({"pid": m.group(1), "tid": m.group(2), "thread": m.group(3), "process": m.group(4)})
    m = re.search(r"Abort message: '([^']{0,300})'", block)
    if m:
        f["abort_message"] = m.group(1)
    m = re.search(r"Build fingerprint: '([^']+)'", block)
    if m:
        f["build_fingerprint"] = m.group(1)
    m = re.search(r"ABI: '([^']+)'", block)
    if m:
        f["abi"] = m.group(1)
    frames = re.findall(r"#\d{2} pc [0-9a-fA-F]+\s+(\S+)", block)
    f["frames"] = frames[:30]
    return f


def _link_fields(block):
    f = {}
    m = re.search(r'library "([^"]+)" not found', block)
    if m:
        f["missing_library"] = m.group(1)
    m = re.search(r'CANNOT LINK EXECUTABLE "([^"]+)"', block)
    if m:
        f["executable"] = m.group(1)
    return f


def _installer_fields(block):
    codes = sorted(set(INSTALL_CODE.findall(block)))
    kb = _matrix().get("installer_failures") or {}
    return {"codes": codes, "kb": {c: kb[c] for c in codes if c in kb},
            "unknown_codes": [c for c in codes if c not in kb]}


SUGGEST = {
    "java_crash": ["Reproduce on the same API level and check the first app frame.",
                   "Treat the top 'Caused by' as the root cause; look for null or uninitialised views first.",
                   "Retrace with: triage retrace --mapping <mapping.txt> --trace <log> when the build is minified."],
    "anr": ["Inspect the main-thread stack for blocking I/O, locks or binder calls.",
            "Move work off the main thread; check the 'Reason' field for the triggering component."],
    "native_tombstone": ["Symbolicate with ndk-stack or addr2line using the unstripped .so for the same build.",
                         "Verify the ABI and 16 KB page alignment of the failing library (artifact elf / abi)."],
    "native_link_failure": ["Check the named library is packaged for the device ABI and that its own dependencies "
                            "are present (readelf -d / artifact elf DT_NEEDED).",
                            "Libraries built against a removed runtime (e.g. libgnustl_shared) need a rebuild."],
    "installer_failure": ["Look up each code in the installer KB (fields.kb) and fix the packaging cause.",
                          "Re-run compat check with --device-api to test install compatibility."],
    "unknown": ["No known signature matched; keep the raw log as evidence and extend the signatures."],
}


EXTRACTORS = {"java_crash": _java_fields, "anr": _anr_fields, "native_tombstone": _tomb_fields,
              "installer_failure": _installer_fields, "native_link_failure": _link_fields,
              "unknown": lambda b: {}}


def analyze_files(paths, cfg):
    blocks = []
    files = []
    for p in paths:
        path = core.check_input_path(p, kind="file", cfg=cfg, label="log-file")
        text, truncated = _read_text(path)
        files.append({"path": path, "bytes": os.path.getsize(path), "truncated": truncated})
        for idx, (title, body) in enumerate(split_blocks(text)):
            if not body.strip():
                continue
            kind, secondary = classify(body)
            kinds = [kind] + sorted(secondary, key=lambda k: -secondary[k])
            findings = {k: EXTRACTORS[k](body) for k in kinds if k != "unknown"}
            blocks.append({"source": os.path.basename(path), "index": idx + 1, "title": title or None,
                           "kind": kind, "kinds": kinds, "fields": findings.get(kind, {}),
                           "findings": findings, "suggestions": SUGGEST[kind]})
    counts = collections.Counter(k for b in blocks for k in b["kinds"] if k != "unknown")
    return {"files": files, "blocks": blocks, "counts": dict(counts)}


def cmd_triage_analyze(args, cfg):
    res = analyze_files(args.logs, cfg)
    if not res["blocks"]:
        return core.ok("triage analyze", res, ["No recognised failure blocks found."], status="UNKNOWN")
    lines = ["blocks: %s" % ", ".join("%s=%d" % kv for kv in sorted(res["counts"].items()))]
    for b in res["blocks"]:
        f = b["fields"]
        inst = b["findings"].get("installer_failure") or {}
        head = (f.get("exception") or f.get("reason") or f.get("signal") or f.get("missing_library")
                or ", ".join(inst.get("codes", [])) or "")
        others = list(b["kinds"][1:])
        lines.append("[%s #%d] %s%s  %s" % (b["source"], b["index"], b["kind"],
                                           (" (also: %s)" % ", ".join(others)) if others else "", head or ""))
        if f.get("first_app_frame"):
            lines.append("    first app frame: %s" % f["first_app_frame"])
        if b["findings"].get("installer_failure"):
            lines.append("    installer codes: %s" % ", ".join(b["findings"]["installer_failure"].get("codes", [])))
    res["_text"] = lines
    return core.ok("triage analyze", res)


PROG_CLASS = re.compile(r"^(\S+) -> (\S+):$")
PROG_MEMBER = re.compile(r"^\s+(?:(\d+):(\d+):)?[^\s(]+\s+([^\s(]+)\(([^)]*)\)(?::(\d+):(\d+))?\s+->\s+(\S+)$")
PROG_MEMBER_NOLINE = re.compile(r"^\s+\S+\s+([^\s(]+)\(([^)]*)\)\s+->\s+(\S+)$")


def parse_mapping(text):
    classes = {}
    cur = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = PROG_CLASS.match(line)
        if m and not line.startswith(" "):
            cur = {"orig": m.group(1), "obf": m.group(2), "methods": {}}
            classes[m.group(2)] = cur
            continue
        if cur is None or not line.startswith(" "):
            continue
        m = PROG_MEMBER.match(line)
        if m and m.group(1):
            name = m.group(3)
            cur["methods"].setdefault(m.group(7), []).append({
                "name": name, "obf_start": int(m.group(1)), "obf_end": int(m.group(2)),
                "orig_start": int(m.group(5)) if m.group(5) else None,
                "orig_end": int(m.group(6)) if m.group(6) else None})
            continue
        m = PROG_MEMBER_NOLINE.match(line)
        if m:
            cur["methods"].setdefault(m.group(3), []).append({"name": m.group(1), "obf_start": None,
                                                               "obf_end": None, "orig_start": None,
                                                               "orig_end": None})
    return classes


FRAME = re.compile(r"^(\s*at )([\w$.]+)\.([\w$<>]+)\(([^:)]*)(?::(\d+))?\)(.*)$")


def retrace_text(classes, trace):
    out, unresolved, resolved = [], 0, 0
    for line in trace.splitlines():
        m = FRAME.match(line)
        if not m:
            out.append(line)
            continue
        prefix, obf_cls, obf_meth, src, lno, rest = m.groups()
        c = classes.get(obf_cls)
        if not c:
            unresolved += 1
            out.append(line)
            continue
        name, new_line = obf_meth, lno
        cands = c["methods"].get(obf_meth, [])
        if cands:
            pick = cands[0]
            if lno and pick["obf_start"] is not None:
                for cnd in cands:
                    if cnd["obf_start"] is not None and cnd["obf_start"] <= int(lno) <= cnd["obf_end"]:
                        pick = cnd
                        break
                if pick["orig_start"] is not None:
                    new_line = str(pick["orig_start"] + (int(lno) - pick["obf_start"]))
            name = pick["name"]
        resolved += 1
        loc = "%s:%s" % (src or "Unknown Source", new_line) if (lno or src) else "Unknown Source"
        out.append("%s%s.%s(%s)%s" % (prefix, c["orig"], name, loc, rest))
    return "\n".join(out) + "\n", {"frames_resolved": resolved, "frames_unresolved": unresolved}


def cmd_triage_retrace(args, cfg):
    mp = core.check_input_path(args.mapping, kind="file", cfg=cfg, label="mapping")
    tr = core.check_input_path(args.trace, kind="file", cfg=cfg, label="trace")
    classes = parse_mapping(_read_text(mp)[0])
    if not classes:
        raise core.ToolkitError("E_PARSE", "No class entries found in mapping file: %s" % mp,
                                hint="Expected ProGuard/R8 mapping.txt format ('orig.Class -> a.b:').")
    text, stats = retrace_text(classes, _read_text(tr)[0])
    data = dict(stats, mapped_classes=len(classes), retraced_text=text,
                _text=text.splitlines()[:200])
    return core.ok("triage retrace", data,
                   ["Line numbers are mapped only where the mapping lists line ranges."])


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

SQLITE_MAGIC = b"SQLite format 3\x00"


def _sqlite_open(path):
    uri = "file:%s?mode=ro" % os.path.abspath(path)
    return sqlite3.connect(uri, uri=True)


def sqlite_profile(path, cfg):
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head != SQLITE_MAGIC:
        raise core.ToolkitError("E_PARSE", "Not an SQLite 3 database: %s" % path)
    con = _sqlite_open(path)
    try:
        tables = []
        for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' "
                                     "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()[:200]:
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % name.replace('"', '""'))]
            n = con.execute('SELECT COUNT(*) FROM "%s"' % name.replace('"', '""')).fetchone()[0]
            tables.append({"name": name, "columns": cols, "rows": n})
        integrity = con.execute("PRAGMA quick_check").fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        journal = con.execute("PRAGMA journal_mode").fetchone()[0]
        user_version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    sidecars = [s for s in (path + "-wal", path + "-journal", path + "-shm") if os.path.exists(s)]
    warnings = []
    if sidecars:
        warnings.append("Sidecar files present (%s): results may omit uncheckpointed WAL data." %
                        ", ".join(os.path.basename(s) for s in sidecars))
    return {"path": path, "size_bytes": os.path.getsize(path), "page_size": page_size,
            "journal_mode": journal, "user_version": user_version, "integrity": integrity,
            "tables": tables, "sidecars": sidecars}, warnings


def cmd_storage_sqlite(args, cfg):
    path = core.check_input_path(args.db, kind="file", cfg=cfg, label="database")
    prof, warns = sqlite_profile(path, cfg)
    data = {"profile": prof}
    if args.diff:
        other = core.check_input_path(args.diff, kind="file", cfg=cfg, label="diff-database")
        p2, w2 = sqlite_profile(other, cfg)
        warns += w2
        a = {t["name"]: t for t in prof["tables"]}
        b = {t["name"]: t for t in p2["tables"]}
        changes = {
            "tables_added": sorted(set(b) - set(a)),
            "tables_removed": sorted(set(a) - set(b)),
            "column_changes": {n: {"added": sorted(set(b[n]["columns"]) - set(a[n]["columns"])),
                                   "removed": sorted(set(a[n]["columns"]) - set(b[n]["columns"]))}
                               for n in sorted(set(a) & set(b))
                               if set(a[n]["columns"]) != set(b[n]["columns"])},
            "row_count_changes": {n: {"before": a[n]["rows"], "after": b[n]["rows"]}
                                  for n in sorted(set(a) & set(b)) if a[n]["rows"] != b[n]["rows"]},
        }
        data["diff"] = changes
        data["_text"] = ["tables added: %s" % changes["tables_added"],
                         "tables removed: %s" % changes["tables_removed"],
                         "column changes: %s" % json.dumps(changes["column_changes"]),
                         "row count changes: %s" % json.dumps(changes["row_count_changes"])]
    else:
        data["_text"] = ["integrity: %s, page size %s, journal %s" % (prof["integrity"], prof["page_size"],
                                                                      prof["journal_mode"])] + \
                        ["  %-28s %6d rows  cols: %s" % (t["name"], t["rows"], ", ".join(t["columns"]))
                         for t in prof["tables"]]
    return core.ok("storage sqlite", data, warns)


def cmd_storage_assets(args, cfg):
    root = core.check_input_path(args.dir, kind="dir", label="assets-dir")
    cap = core.limit(cfg, "max_media_entries_scan", 5000)
    files, by_hash, total = [], collections.defaultdict(list), 0
    cats = collections.Counter()
    truncated = False
    for dp, dns, fns in os.walk(root):
        for fn in sorted(fns):
            if len(files) >= cap:
                truncated = True
                break
            full = os.path.join(dp, fn)
            size = os.path.getsize(full)
            total += size
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            ext = os.path.splitext(fn)[1].lower() or "(none)"
            cats[ext] += 1
            digest = core.sha256_file(full) if size <= 64 * 1024 * 1024 else None
            if digest:
                by_hash[digest].append(rel)
            files.append({"path": rel, "bytes": size, "sha256": digest})
        if truncated:
            break
    dups = [{"sha256": h, "paths": p} for h, p in by_hash.items() if len(p) > 1]
    largest = sorted(files, key=lambda f: f["bytes"], reverse=True)[:20]
    data = {"root": root, "file_count": len(files), "total_bytes": total, "by_extension": dict(cats),
            "duplicates": dups, "largest": largest, "truncated": truncated,
            "_text": ["%d files, %d bytes, %d duplicate groups" % (len(files), total, len(dups))]}
    return core.ok("storage assets", data, ["Scan truncated at %d files." % cap] if truncated else [])


def cmd_storage_validate(args, cfg):
    path = core.check_input_path(args.file, kind="file", cfg=cfg, label="storage-file")
    det = ARTIFACTS.detect_file(path, cfg)
    res = {"path": path, "detected": det.get("type"), "subtype": det.get("subtype")}
    with open(path, "rb") as fh:
        is_sqlite = fh.read(16) == SQLITE_MAGIC
    if is_sqlite:
        prof, warns = sqlite_profile(path, cfg)
        res.update({"valid": prof["integrity"] == "ok", "check": "sqlite quick_check: %s" % prof["integrity"]})
    elif det.get("family") == "zip":
        zh = ARTIFACTS.zip_health(path, cfg)
        res.update({"valid": zh.get("healthy"), "check": "zip health: %s" % zh.get("healthy")})
    else:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                json.load(fh)
            res.update({"valid": True, "check": "JSON parses"})
        except (UnicodeDecodeError, json.JSONDecodeError):
            res.update({"valid": None, "check": "no validator for this format"})
    status = "VALID" if res.get("valid") else ("UNKNOWN" if res.get("valid") is None else core.ST_BROKEN)
    res["status"] = status
    if status == core.ST_BROKEN:
        raise core.ToolkitError("E_FAILED", "Storage file failed validation: %s" % res["check"], data=res)
    return core.ok("storage validate", res, status=status)


# ---------------------------------------------------------------------------
# security (static, read-only)
# ---------------------------------------------------------------------------

def _manifest_from_apk(path):
    with core.open_zip(path) as zf:
        if "AndroidManifest.xml" not in zf.namelist():
            raise core.ToolkitError("E_PARSE", "No AndroidManifest.xml in %s" % path)
        return ARTIFACTS.manifest_summary(ARTIFACTS.parse_axml(zf.read("AndroidManifest.xml")))


def _scan_text(name, data, patterns, findings, cap):
    for group, pats in patterns.items():
        for p in pats:
            try:
                rx = re.compile(p["pattern"])
            except (re.error, KeyError):
                continue
            m = rx.search(data)
            if m:
                findings.append({"severity": "high" if group in ("secrets", "crypto_misuse") else "info",
                                 "code": "%s/%s" % (group, p.get("id")), "where": name,
                                 "evidence": m.group(0)[:60] + ("..." if len(m.group(0)) > 60 else "")})


def security_findings(manifest, cfg):
    f = []
    app = manifest.get("application") or {}
    perms = {p["name"] for p in manifest.get("uses_permissions") or []}
    dangerous = set((cfg.get("permissions") or {}).get("dangerous") or [])
    for p in sorted(perms & dangerous):
        f.append({"severity": "info", "code": "dangerous-permission", "where": p,
                  "detail": "Requires explicit runtime consent; review necessity."})
    if str(app.get("debuggable", "")).lower() == "true":
        f.append({"severity": "high", "code": "debuggable", "where": "application",
                  "detail": "android:debuggable=true ships debugging access."})
    if str(app.get("allowBackup", "")).lower() == "true":
        f.append({"severity": "medium", "code": "allow-backup", "where": "application",
                  "detail": "Backups are permitted; confirm no secrets are stored in app data."})
    if str(app.get("usesCleartextTraffic", "")).lower() == "true":
        f.append({"severity": "medium", "code": "cleartext-traffic", "where": "application",
                  "detail": "Cleartext HTTP allowed; check networkSecurityConfig."})
    for kind, items in (manifest.get("components") or {}).items():
        for c in items:
            if str(c.get("exported", "")).lower() == "true" and not c.get("permission") and kind in ("providers", "receivers", "services"):
                f.append({"severity": "medium", "code": "exported-without-permission", "where": "%s:%s" % (kind, c.get("name")),
                          "detail": "Exported %s has no permission guard." % kind[:-1]})
    return f


def cmd_security_scan(args, cfg):
    cap = core.limit(cfg, "max_media_entries_scan", 5000)
    patterns = cfg.get("scan_patterns") or {}
    findings, scanned = [], 0
    if args.decoded:
        root = core.check_input_path(args.decoded, kind="dir", label="decoded-dir")
        manifest_path = os.path.join(root, "AndroidManifest.xml")
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            dbg = re.search(r'debuggable="true"', txt)
            if dbg:
                findings.append({"severity": "high", "code": "debuggable", "where": "AndroidManifest.xml"})
            if re.search(r'usesCleartextTraffic="true"', txt):
                findings.append({"severity": "medium", "code": "cleartext-traffic", "where": "AndroidManifest.xml"})
        for dp, dns, fns in os.walk(root):
            for fn in fns:
                if scanned >= cap:
                    break
                full = os.path.join(dp, fn)
                if os.path.getsize(full) > 1024 * 1024 or not fn.endswith((".smali", ".xml", ".json", ".txt", ".properties", ".java", ".kt", ".js", ".yml", ".yaml")):
                    continue
                scanned += 1
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    _scan_text(os.path.relpath(full, root).replace(os.sep, "/"), fh.read(), patterns, findings, cap)
        target = root
    else:
        apk = core.check_input_path(args.apk, kind="file", cfg=cfg, label="apk")
        det = ARTIFACTS.detect_file(apk, cfg)
        if det.get("family") != "zip":
            raise core.ToolkitError("E_INPUT", "security scan expects an APK/ZIP container, got %s" % det.get("type"))
        manifest = _manifest_from_apk(apk)
        findings += security_findings(manifest, cfg)
        with core.open_zip(apk) as zf:
            for info in zf.infolist():
                if scanned >= cap:
                    break
                n = info.filename
                if info.file_size > 1024 * 1024 or not n.startswith(("assets/", "res/", "lib/")) or n.endswith(".arsc"):
                    continue
                if n.endswith((".png", ".jpg", ".webp", ".so", ".ogg", ".mp3", ".ttf", ".otf")):
                    continue
                scanned += 1
                try:
                    _scan_text(n, zf.read(n).decode("utf-8", "replace"), patterns, findings, cap)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    findings.append({"severity": "info", "code": "unreadable-entry", "where": n, "detail": str(exc)})
        target = apk
    counts = collections.Counter(x["severity"] for x in findings)
    data = {"target": target, "entries_scanned": scanned, "findings": findings,
            "severity_counts": dict(counts), "static_only": True,
            "_text": ["%d findings (%s); %d entries scanned" % (len(findings), dict(counts), scanned)] +
                     ["  [%s] %s  %s" % (x["severity"], x["code"], x.get("where", "")) for x in findings[:40]]}
    return core.ok("security scan", data, ["Static heuristics only; not a vulnerability assessment or proof of exploitability."])


# ---------------------------------------------------------------------------
# compat
# ---------------------------------------------------------------------------

def _apk_facts(path, cfg):
    det = ARTIFACTS.detect_file(path, cfg)
    if det.get("family") != "zip":
        raise core.ToolkitError("E_INPUT", "Expected an APK, got %s" % det.get("type"))
    m = _manifest_from_apk(path)
    app = m.get("application") or {}
    try:
        min_sdk = int(m.get("minSdkVersion") or 1)
    except (TypeError, ValueError):
        min_sdk = None
    try:
        target = int(m.get("targetSdkVersion") or min_sdk or 1)
    except (TypeError, ValueError):
        target = None
    return {"package": m.get("package"), "versionCode": m.get("versionCode"), "versionName": m.get("versionName"),
            "minSdkVersion": min_sdk, "targetSdkVersion": target, "application": app,
            "debuggable": str(app.get("debuggable", "")).lower() == "true",
            "cleartext": str(app.get("usesCleartextTraffic", "")).lower() == "true",
            "uses_permissions": [p["name"] for p in m.get("uses_permissions") or []],
            "_detect": det}


def applicable_rules(facts, device_api, release, matrix):
    out = []
    target = facts.get("targetSdkVersion") or 0
    for r in matrix.get("behavior_rules") or []:
        gate = r.get("gate")
        api = r.get("api") or 0
        hit = False
        if gate == "targetSdk" and target >= api:
            hit = True
        elif gate == "device" and device_api is not None and device_api >= api:
            hit = True
        elif gate in ("install", "play-policy") and release:
            hit = True
        if hit:
            out.append(r)
    return out


def cmd_compat_check(args, cfg):
    apk = core.check_input_path(args.apk, kind="file", cfg=cfg, label="apk")
    facts = _apk_facts(apk, cfg)
    matrix = _matrix()
    device_api = args.device_api
    findings = []
    if device_api is not None and facts["minSdkVersion"] and facts["minSdkVersion"] > device_api:
        findings.append({"severity": "error", "code": "INSTALL_FAILED_OLDER_SDK",
                         "detail": "minSdkVersion %d > device API %d" % (facts["minSdkVersion"], device_api)})
    if facts["targetSdkVersion"] and facts["targetSdkVersion"] < 23 and device_api and device_api >= 23:
        findings.append({"severity": "warning", "code": "legacy-target",
                         "detail": "targetSdk < 23 runs with the legacy permission model on modern devices"})
    if args.release and facts["debuggable"]:
        findings.append({"severity": "error", "code": "release-debuggable", "detail": "android:debuggable=true in a release check"})
    if args.release and facts["cleartext"]:
        findings.append({"severity": "warning", "code": "release-cleartext", "detail": "usesCleartextTraffic=true"})
    rules = applicable_rules(facts, device_api, args.release, matrix)
    if not facts["targetSdkVersion"]:
        findings.append({"severity": "warning", "code": "no-target-sdk", "detail": "targetSdkVersion not declared"})
    elf = None
    try:
        abi = ARTIFACTS.abi_report(apk, cfg)
        libs = abi.get("abis") or {}
        elf = {"abis": sorted(libs), "page16k_ready": {k: v.get("page16k_ready") for k, v in libs.items()}}
        if libs and not all(v.get("page16k_ready") for v in libs.values()):
            findings.append({"severity": "warning" if not args.release else "error", "code": "page-size-16kb",
                             "detail": "Some native libraries are not 16 KB page aligned."})
    except core.ToolkitError:
        elf = None
    data = {"package": facts["package"], "versionCode": facts["versionCode"],
            "minSdkVersion": facts["minSdkVersion"], "targetSdkVersion": facts["targetSdkVersion"],
            "device_api": device_api, "release": bool(args.release), "findings": findings,
            "applicable_rules": [{"id": r["id"], "gate": r["gate"], "api": r["api"], "title": r["title"],
                                  "action": r["action"]} for r in rules],
            "native": elf}
    lines = ["package %s  minSdk %s  targetSdk %s" % (facts["package"], facts["minSdkVersion"], facts["targetSdkVersion"]),
             "applicable rules: %d" % len(rules)]
    lines += ["  [%s] %s: %s" % (f["severity"], f["code"], f["detail"]) for f in findings]
    data["_text"] = lines
    if args.out:
        md = _compat_markdown(data)
        core.write_text(args.out, md)
        data["report"] = args.out
    return core.ok("compat check", data)


def _compat_markdown(d):
    rows = ["# Compatibility check: %s" % d["package"], "",
            "- minSdkVersion: %s" % d["minSdkVersion"], "- targetSdkVersion: %s" % d["targetSdkVersion"],
            "- device API: %s" % d["device_api"], "- release check: %s" % d["release"], "", "## Findings", ""]
    rows += ["- **%s** `%s`: %s" % (f["severity"], f["code"], f["detail"]) for f in d["findings"]] or ["- none"]
    rows += ["", "## Applicable behaviour rules", ""]
    rows += ["- `%s` (%s >= %s): %s. Action: %s" % (r["id"], r["gate"], r["api"], r["title"], r["action"])
             for r in d["applicable_rules"]] or ["- none"]
    return "\n".join(rows) + "\n"


def cmd_compat_levels(args, cfg):
    m = _matrix()
    return core.ok("compat levels", {"api_levels": m.get("api_levels")})


def cmd_compat_matrix(args, cfg):
    m = _matrix()
    data = {"test_matrix_lanes": m.get("test_matrix_lanes"), "agp_gradle_jdk": m.get("agp_gradle_jdk"),
            "gradle_jdk": m.get("gradle_jdk")}
    return core.ok("compat matrix", data)


def cmd_compat_rules(args, cfg):
    m = _matrix()
    rules = m.get("behavior_rules") or []
    topic = (args.topic or "").lower()
    if topic:
        rules = [r for r in rules if topic in (r.get("topic") or "").lower() or topic in (r.get("id") or "").lower()]
    data = {"topic": args.topic, "rules": rules, "count": len(rules)}
    if topic == "installer":
        kb = m.get("installer_failures") or {}
        data["installer_failures"] = kb
        data["installer_codes"] = sorted(kb)
    if not rules and topic != "installer":
        raise core.ToolkitError("E_NOT_FOUND", "No behaviour rules for topic '%s'" % args.topic,
                                hint="Available topics: %s" % ", ".join(sorted({r.get("topic") for r in m.get("behavior_rules") or []})))
    return core.ok("compat rules", data)


# ---------------------------------------------------------------------------
# preserve
# ---------------------------------------------------------------------------

def _file_records(files, cfg):
    recs = []
    for f in files:
        p = core.check_input_path(f, kind="file", cfg=cfg, label="file")
        st = os.stat(p)
        mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        recs.append({"path": f, "name": os.path.basename(p), "sha256": core.sha256_file(p),
                     "size_bytes": st.st_size, "mtime_utc": mtime})
    return recs


def _provenance_doc(recs, note, with_tools, cfg):
    doc = {"kind": "provenance", "created": core.now_iso(), "toolkit": core.TOOLKIT_NAME,
           "toolkit_version": core.TOOLKIT_VERSION, "note": note or None, "files": recs}
    if with_tools:
        tools = {}
        for row in ADAPTERS.list_tools(cfg)["tools"]:
            if row["installed"] and row["kind"] != "python-module":
                probe = ADAPTERS.version_probe(row["name"], cfg)
                tools[row["name"]] = {"status": probe["status"], "version": probe.get("version")}
        doc["tool_versions"] = tools
    return doc


def cmd_preserve_provenance(args, cfg):
    if not args.files:
        raise core.ToolkitError("E_USAGE", "preserve provenance needs --files <path...>")
    recs = _file_records(args.files, cfg)
    doc = _provenance_doc(recs, args.note, args.tools, cfg)
    store = os.path.join(args.out_dir, (cfg.get("paths") or {}).get("provenance_store", "provenance-manifest.json"))
    if os.path.exists(store):
        core.ensure_approval("overwrite provenance record %s" % store, args.yes, cfg)
    core.write_text(store, core.json_dumps(doc))
    return core.ok("preserve provenance", {"record": store, "files": len(recs), "tool_versions": doc.get("tool_versions")})


def cmd_preserve_snapshot(args, cfg):
    if not args.files:
        raise core.ToolkitError("E_USAGE", "preserve snapshot needs --files <path...>")
    dest = core.check_output_path(args.out or args.out_dir, kind="dir", overwrite=True, label="snapshot-dir")
    os.makedirs(dest, exist_ok=True)
    if os.listdir(dest):
        core.ensure_approval("write a snapshot into non-empty directory %s" % dest, args.yes, cfg)
    originals = os.path.join(dest, "originals")
    os.makedirs(originals, exist_ok=True)
    recs = _file_records(args.files, cfg)
    for r in recs:
        target = os.path.join(originals, r["name"])
        if os.path.exists(target):
            raise core.ToolkitError("E_INPUT", "Duplicate file name in snapshot: %s" % r["name"],
                                    hint="Snapshot files with unique names.")
        shutil.copy2(r["path"], target)
        os.chmod(target, 0o444)
        r["snapshot_path"] = os.path.relpath(target, dest).replace(os.sep, "/")
        r["snapshot_sha256"] = core.sha256_file(target)
        if r["snapshot_sha256"] != r["sha256"]:
            raise core.ToolkitError("E_FAILED", "Snapshot copy does not match the original: %s" % r["name"])
    doc = _provenance_doc(recs, "snapshot of originals before modification", False, cfg)
    core.write_text(os.path.join(dest, (cfg.get("paths") or {}).get("provenance_store", "provenance-manifest.json")),
                    core.json_dumps(doc))
    return core.ok("preserve snapshot", {"snapshot_dir": dest, "files": len(recs),
                                         "all_copies_verified": True, "record": recs})


def cmd_preserve_package(args, cfg):
    if not args.files or not args.out:
        raise core.ToolkitError("E_USAGE", "preserve package needs --files <path...> --out <release.zip>")
    if not args.out.lower().endswith(".zip"):
        raise core.ToolkitError("E_INPUT", "--out must end with .zip")
    out = core.check_output_path(args.out, kind="file", overwrite=True, label="release-zip")
    if os.path.exists(out):
        core.ensure_approval("overwrite release archive %s" % out, args.yes, cfg)
    recs = _file_records(args.files, cfg)
    prov_name = (cfg.get("paths") or {}).get("provenance_store", "provenance-manifest.json")
    names = [r["name"] for r in recs]
    if len(set(names)) != len(names):
        raise core.ToolkitError("E_INPUT", "Duplicate file names cannot be packaged together")
    if prov_name in names:
        raise core.ToolkitError("E_INPUT", "'%s' is reserved for the provenance record; rename the input" % prov_name)
    doc = _provenance_doc(recs, "release package", False, cfg)
    tmp = out + ".partial"
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for r in recs:
                zf.write(r["path"], arcname=r["name"])
            zf.writestr("provenance-manifest.json", core.json_dumps(doc))
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return core.ok("preserve package", {"archive": out, "entries": len(recs) + 1,
                                        "bytes": os.path.getsize(out), "sha256": core.sha256_file(out)})


def _apk_summary_row(path, cfg):
    facts = _apk_facts(path, cfg)
    abi = ARTIFACTS.abi_report(path, cfg)
    sig = ARTIFACTS.signing_report(path, cfg)
    certs = sig.get("certificates") or []
    return {"file": os.path.basename(path), "package": facts["package"], "versionCode": facts["versionCode"],
            "minSdk": facts["minSdkVersion"], "targetSdk": facts["targetSdkVersion"],
            "abis": sorted((abi.get("abis") or {}).keys()),
            "page16k_ready": all(v.get("page16k_ready") for v in (abi.get("abis") or {}).values()) if abi.get("abis") else None,
            "schemes": sig.get("schemes", {}),
            "cert_sha256": certs[0]["sha256_fingerprint"] if certs else None,
            "debuggable": facts["debuggable"]}


def _gate_check(path, cfg):
    doc = core.load_json(path, "results file")
    rows = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise core.ToolkitError("E_PARSE", "Results file has no 'results' list: %s" % path)
    bad = []
    for r in rows:
        if r.get("critical") and r.get("status") not in (core.ST_FOUND, "PASS", "OK"):
            bad.append({"id": r.get("id"), "status": r.get("status")})
        elif r.get("status") == "FAIL":
            bad.append({"id": r.get("id"), "status": "FAIL"})
    return {"results_file": path, "rows": len(rows), "blocking": bad, "pass": not bad}


def cmd_preserve_report(args, cfg):
    if args.gate:
        path = core.check_input_path(args.gate, kind="file", cfg=cfg, label="results")
        g = _gate_check(path, cfg)
        data = dict(g, _text=["gate: %s (%d rows, %d blocking)" % ("PASS" if g["pass"] else "FAIL", g["rows"], len(g["blocking"]))]
                            + ["  blocking: %s %s" % (b["id"], b["status"]) for b in g["blocking"]])
        if not g["pass"]:
            raise core.ToolkitError("E_FAILED", "Release gate failed: %d blocking item(s)" % len(g["blocking"]), data=data)
        return core.ok("preserve report", data, status="PASS")
    if not args.apk:
        raise core.ToolkitError("E_USAGE", "preserve report needs --apk <a...> or --gate <results.json>")
    seen, paths = set(), []
    for a in args.apk:
        real = os.path.realpath(core.check_input_path(a, kind="file", cfg=cfg, label="apk"))
        if real not in seen:  # the same file listed twice is compared once
            seen.add(real)
            paths.append(real)
    rows = [_apk_summary_row(p, cfg) for p in paths]
    lines = ["# APK comparison", "", "| file | package | versionCode | minSdk | targetSdk | ABIs | 16KB | cert sha256 (prefix) | debuggable |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["file"], r["package"], r["versionCode"], r["minSdk"], r["targetSdk"], ",".join(r["abis"]) or "-",
            r["page16k_ready"], (r["cert_sha256"] or "-")[:16], r["debuggable"]))
    md = "\n".join(lines) + "\n"
    data = {"rows": rows, "_text": lines[2:]}
    if args.out:
        out = core.check_output_path(args.out, kind="file", overwrite=True, label="report")
        if os.path.exists(out):
            core.ensure_approval("overwrite report %s" % out, args.yes, cfg)
        core.write_text(out, md)
        data["report"] = out
    return core.ok("preserve report", data)


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------

def cmd_recovery_assess(args, cfg):
    apk = core.check_input_path(args.apk, kind="file", cfg=cfg, label="apk")
    det = ARTIFACTS.detect_file(apk, cfg)
    steps, evidence = [], {}
    if det.get("family") != "zip":
        raise core.ToolkitError("E_INPUT", "recovery assess expects an APK/ZIP container")
    zh = ARTIFACTS.zip_health(apk, cfg)
    evidence["zip_healthy"] = zh.get("healthy")
    codes = [i["code"] for i in zh.get("issues", [])]
    if not zh.get("healthy") or any(c in codes for c in ("truncated", "bad-eocd", "crc-mismatch", "path-traversal")):
        steps.append("Container is damaged or unsafe: keep the original untouched, obtain a fresh copy, and do not install it.")
    sig = ARTIFACTS.signing_report(apk, cfg)
    sch = sig.get("schemes", {})
    evidence["signing_schemes"] = {k: sch.get(k) for k in ("v1", "v2", "v3")}
    if not sch.get("v2") and not sch.get("v3"):
        steps.append("No v2/v3 signature block: modern installers may reject it. Re-sign only with the original key and with approval.")
    abi = ARTIFACTS.abi_report(apk, cfg)
    evidence["abis"] = sorted((abi.get("abis") or {}).keys())
    if abi.get("abis") and not any("64" in str(v.get("classes")) for v in abi["abis"].values()):
        steps.append("Only 32-bit native code is present: do not claim 64-bit support; require a tested arm64-v8a build.")
    if abi.get("abis") and not all(v.get("page16k_ready") for v in abi["abis"].values()):
        steps.append("Native libraries are not 16 KB page aligned: rebuild with an aligned NDK/linker, then re-test on a 16 KB device or emulator.")
    tr = [a for a, v in (abi.get("abis") or {}).items() if v.get("textrel")]
    if tr:
        steps.append("Text relocations (TEXTREL) in %s: rebuild PIC libraries." % ", ".join(tr))
    facts = _apk_facts(apk, cfg)
    evidence["targetSdkVersion"] = facts["targetSdkVersion"]
    if facts["targetSdkVersion"] and facts["targetSdkVersion"] < 26:
        steps.append("targetSdk %s is below current store minimums: plan a migration (see compat rules)." % facts["targetSdkVersion"])
    if not steps:
        steps.append("No blocking recovery issues found in the static evidence; proceed to install and runtime tests on a real device.")
    steps.append("Keep the original file in preservation storage (preserve snapshot) before any modification.")
    data = {"apk": apk, "evidence": evidence, "ordered_plan": steps,
            "_text": ["%d step(s)" % len(steps)] + ["%d. %s" % (i + 1, s) for i, s in enumerate(steps)],
            "execution": "none - assessment only"}
    return core.ok("recovery assess", data)


COMMANDS = {
    ("triage", "analyze"): cmd_triage_analyze,
    ("triage", "retrace"): cmd_triage_retrace,
    ("storage", "sqlite"): cmd_storage_sqlite,
    ("storage", "assets"): cmd_storage_assets,
    ("storage", "validate"): cmd_storage_validate,
    ("security", "scan"): cmd_security_scan,
    ("compat", "check"): cmd_compat_check,
    ("compat", "levels"): cmd_compat_levels,
    ("compat", "matrix"): cmd_compat_matrix,
    ("compat", "rules"): cmd_compat_rules,
    ("preserve", "provenance"): cmd_preserve_provenance,
    ("preserve", "snapshot"): cmd_preserve_snapshot,
    ("preserve", "package"): cmd_preserve_package,
    ("preserve", "report"): cmd_preserve_report,
    ("recovery", "assess"): cmd_recovery_assess,
}
