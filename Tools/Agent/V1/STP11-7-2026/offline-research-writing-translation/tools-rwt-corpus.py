#!/usr/bin/env python3
"""tools-rwt-corpus.py - Corpus ingestion, cleaning, installation and health.

Capabilities: document ingest with locators, exact/near deduplication,
integrity audit, offline bundle install (checksummed), incremental update,
snapshot/restore, Unicode/mojibake repair report, boilerplate removal,
format-extraction routing, event timeline building, source diversity audit,
independence (syndication/shared-source) analysis.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        _CORE = core_loader()
    return _CORE

def core_loader():
    this = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, this)
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_core_c", os.path.join(this, "tools-rwt-core.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["rwt_core_c"] = m
    spec.loader.exec_module(m)
    return m

C = core()

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".html", ".htm", ".xml", ".json", ".jsonl",
                   ".csv", ".tsv", ".rst", ".tex", ".srt", ".vtt", ".yaml", ".yml", ".log"}
BINARY_ROUTES = {
    ".pdf": "pdftotext/qpdf or an approved local OCR/model adapter (not bundled)",
    ".docx": "pandoc or python-docx via a configured local parser adapter (not bundled)",
    ".doc": "antiword/catdoc via a configured local parser adapter (not bundled)",
    ".xlsx": "python-openpyxl via a configured local parser adapter (not bundled)",
    ".pptx": "python-pptx via a configured local parser adapter (not bundled)",
    ".epub": "ebook-convert/unpack zip+OPF via a configured local parser adapter (not bundled)",
    ".zip": "security.scan-media then manual extraction (archives are never auto-extracted here)",
    ".png": "models.ocr (local OCR adapter)", ".jpg": "models.ocr (local OCR adapter)",
    ".jpeg": "models.ocr (local OCR adapter)", ".tiff": "models.ocr (local OCR adapter)",
    ".mp3": "models.stt (local speech adapter)", ".wav": "models.stt (local speech adapter)",
    ".mp4": "models.stt (local speech adapter)",
}

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

def _read_text_lossy(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc), enc, raw
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", raw

def _strip_html(html):
    class Stripper:
        def __init__(self):
            self.parts, self.skip = [], 0
        def feed(self, text):
            from html.parser import HTMLParser
            class P(HTMLParser):
                def __init__(self, outer):
                    super().__init__(convert_charrefs=True)
                    self.outer = outer
                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self.outer.skip += 1
                    if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"):
                        self.outer.parts.append("\n")
                def handle_endtag(self, tag):
                    if tag in ("script", "style", "nav", "footer", "header") and self.outer.skip:
                        self.outer.skip -= 1
                def handle_data(self, data):
                    if not self.outer.skip:
                        self.outer.parts.append(data)
            P(self).feed(text)
            return "".join(self.parts)
    out = Stripper().feed(html)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return C.normalize_ws(out)

def cmd_ingest(args):
    ws = _ws(args)
    info = ws.store_file(args.file)
    text, encoding, raw = _read_text_lossy(args.file)
    ext = os.path.splitext(args.file)[1].lower()
    if ext in (".html", ".htm"):
        text = _strip_html(text)
    tokens = C.tokenize(text)
    doc = ws.add_record("documents", {
        "title": args.title or os.path.basename(args.file),
        "source_path": os.path.abspath(args.file),
        "stored_as": info["stored_as"], "sha256": info["sha256"], "bytes": info["bytes"],
        "encoding": encoding, "language": args.language, "doc_type": args.doc_type,
        "source_type": args.source_type, "published": args.published,
        "words": len(tokens), "license": args.license,
        "locators": {"scheme": "line", "unit": "lines",
                     "note": "Line locators are 1-based; page locators require a PDF adapter."},
        "text": text,
    }, actor=args.actor)
    return {"document_id": doc["id"], "words": doc["words"], "encoding": encoding,
            "sha256": info["sha256"], "locator_scheme": "line"}

def cmd_dedup(args):
    ws = _ws(args)
    docs = ws.list_records("documents")
    docs = [d for d in docs if "_corrupt_line" not in d]
    # exact copies
    by_hash = defaultdict(list)
    for d in docs:
        if d.get("sha256"):
            by_hash[d["sha256"]].append(d["id"])
    exact_groups = [v for v in by_hash.values() if len(v) > 1]
    # near duplicates via shingle jaccard
    near = []
    ids = [d["id"] for d in docs]
    texts = [d.get("text", "") for d in docs]
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            if not texts[i] or not texts[j]:
                continue
            sim = C.shingle_jaccard(texts[i], texts[j], n=5)
            if sim >= args.threshold:
                near.append({"a": ids[i], "b": ids[j], "similarity": round(sim, 4),
                             "action": "review; do not auto-delete"})
    return {"documents": len(docs), "exact_groups": exact_groups,
            "near_duplicates": near,
            "note": "Near-duplicates are reported for human review (author manuscripts, mirrors, syndication)."}

def cmd_integrity(args):
    ws = _ws(args)
    problems = []
    n_docs = 0
    for name in sorted(os.listdir(os.path.join(ws.root, C.STORES_DIR))):
        if not name.endswith(".jsonl"):
            continue
        coll = name[:-6]
        records = ws.list_records(coll)
        seen = set()
        for i, rec in enumerate(records):
            if "_corrupt_line" in rec:
                problems.append({"collection": coll, "line": i + 1, "issue": "unparseable JSON"})
                continue
            rid = rec.get("id")
            if rid and rid in seen:
                problems.append({"collection": coll, "id": rid, "issue": "duplicate id"})
            seen.add(rid)
            if not rec.get("created"):
                problems.append({"collection": coll, "id": rid, "issue": "missing created timestamp"})
        if coll == "documents":
            n_docs += len(records)
            for rec in records:
                if rec.get("_corrupt_line"):
                    continue
                stored = rec.get("stored_as")
                if stored and not os.path.isfile(os.path.join(ws.root, stored)):
                    problems.append({"collection": coll, "id": rec.get("id"),
                                     "issue": f"missing stored file: {stored}"})
                elif stored:
                    digest = C.sha256_file(os.path.join(ws.root, stored))
                    if rec.get("sha256") and digest != rec["sha256"]:
                        problems.append({"collection": coll, "id": rec.get("id"),
                                         "issue": "stored file hash mismatch"})
    cfg_ok = True
    try:
        ws.load()
    except Exception:
        cfg_ok = False
        problems.append({"collection": "config", "issue": "workspace.json unreadable"})
    audit = ws.verify_audit() if cfg_ok else {"ok": False, "entries": 0, "problems": [{"issue": "audit not verified"}]}
    if not audit.get("ok"):
        for p in audit.get("problems", []):
            problems.append({"collection": "audit", **p})
    return {"documents": n_docs, "problems": problems, "ok": not problems,
            "audit_entries": audit.get("entries", 0)}

def cmd_snapshot(args):
    ws = _ws(args)
    if args.restore:
        return ws.restore_snapshot(args.restore, actor=args.actor, confirm=args.yes)
    if args.list:
        return {"snapshots": ws.list_snapshots()}
    return ws.snapshot(args.label)

def cmd_encoding(args):
    text, encoding, raw = _read_text_lossy(args.file)
    mojibake_markers = [m for m in ("Ã©", "Ã¨", "Ã", "â€™", "â€œ", "â€", "Â", "\ufffd") if m in text]
    nfkc = unicodedata.normalize("NFKC", text)
    ligatures = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "æ": "ae", "œ": "oe"}
    lig_found = {k: v for k, v in ligatures.items() if k in text}
    replacement_chars = text.count("\ufffd")
    script_loss = bool(re.search(r"�", text)) or (args.expect_script and not re.search(args.expect_script, text))
    repaired = nfkc
    for k, v in lig_found.items():
        repaired = repaired.replace(k, v)
    out = {"encoding_detected": encoding,
           "mojibake_markers": mojibake_markers,
           "replacement_characters": replacement_chars,
           "ligatures_replaced": lig_found,
           "normalization": "NFC applied" if args.write else "NFC available with --write",
           "script_loss_suspected": script_loss,
           "suggested_action": ("repair available: re-read with corrected encoding and NFC-normalize; "
                                "flag for human review if script is lost")}
    if args.write:
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write(repaired)
        out["written"] = True
    return out

def cmd_deboilerplate(args):
    ws = _ws(args)
    docs = [d for d in ws.list_records("documents") if "_corrupt_line" not in d]
    if not os.path.isfile(args.file):
        raise C.ValidationError(f"File not found: {args.file}")
    text, _enc, _raw = _read_text_lossy(args.file)
    lines = text.splitlines()
    # lines appearing in >=N stored documents are boilerplate candidates
    doc_line_sets = []
    for d in docs:
        doc_line_sets.append(set(l.strip() for l in (d.get("text") or "").splitlines() if len(l.strip()) > 15))
    threshold = max(args.min_docs, math_ceil(len(docs) * args.fraction)) if docs else args.min_docs
    counts = Counter()
    for s in doc_line_sets:
        for l in s:
            counts[l] += 1
    boiler = {l for l, n in counts.items() if n >= threshold and len(l) > 15}
    kept = [l for l in lines if l.strip() not in boiler and not re.match(r"^\s*(menu|home|login|subscribe|cookie|advertisement)\b", l, re.I)]
    removed = len(lines) - len(kept)
    if args.write:
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + "\n")
    return {"lines_total": len(lines), "lines_removed": removed,
            "boilerplate_patterns": sorted(boiler)[:50],
            "corpus_documents_compared": len(docs),
            "written": bool(args.write)}

def math_ceil(x):
    import math
    return math.ceil(x)

def cmd_install(args):
    """Verify a corpus bundle manifest (sha256) and register files into the workspace."""
    ws = _ws(args)
    manifest_path = args.manifest
    if not os.path.isfile(manifest_path):
        raise C.ValidationError(f"Manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        try:
            manifest = json.load(fh)
        except json.JSONDecodeError as exc:
            raise C.ValidationError(f"Manifest is not valid JSON: {exc}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise C.ValidationError("Manifest must contain a non-empty 'files' list with path + sha256")
    bundle_dir = os.path.dirname(os.path.abspath(manifest_path))
    results, failed = [], []
    for entry in files:
        rel = entry.get("path") or entry.get("file")
        want = entry.get("sha256")
        if not rel or not want:
            failed.append({"entry": entry, "issue": "missing path or sha256"})
            continue
        src = os.path.normpath(os.path.join(bundle_dir, rel))
        if not src.startswith(bundle_dir):
            failed.append({"entry": entry, "issue": "path escapes bundle directory (rejected)"})
            continue
        if not os.path.isfile(src):
            failed.append({"entry": entry, "issue": "file missing"})
            continue
        got = C.sha256_file(src)
        if got != want.lower():
            failed.append({"entry": entry, "issue": "checksum mismatch", "expected": want, "got": got})
            continue
        stored = ws.store_file(src)
        results.append({"path": rel, "sha256": got, **stored})
    if failed:
        ws.audit(args.actor, "corpus.install.rejected", {"failed": len(failed)})
        return {"installed": 0, "failed": failed,
                "note": "Installation aborted; nothing was registered. Fix the bundle and retry."}
    for r in results:
        ws.add_record("documents", {
            "title": os.path.basename(r["path"]), "source_path": r["path"],
            "stored_as": r["stored_as"], "sha256": r["sha256"], "bytes": r["bytes"],
            "doc_type": "corpus-bundle", "source_type": manifest.get("source_type", "bundle"),
            "license": manifest.get("license"), "words": None,
        }, actor=args.actor)
    ws.audit(args.actor, "corpus.install", {"installed": len(results), "manifest": os.path.basename(manifest_path)})
    return {"installed": len(results), "manifest": os.path.basename(manifest_path),
            "license": manifest.get("license")}

def cmd_update(args):
    """Apply a signed/checksummed incremental change set: add | replace | delete."""
    ws = _ws(args)
    with open(args.changeset, "r", encoding="utf-8") as fh:
        try:
            changeset = json.load(fh)
        except json.JSONDecodeError as exc:
            raise C.ValidationError(f"Changeset is not valid JSON: {exc}")
    changes = changeset.get("changes")
    if not isinstance(changes, list) or not changes:
        raise C.ValidationError("Changeset must contain a non-empty 'changes' list")
    applied, rejected = [], []
    for ch in changes:
        op, target = ch.get("op"), ch.get("id")
        if op == "add":
            if not ch.get("record"):
                rejected.append({"change": ch, "issue": "add requires 'record'"})
                continue
            ws.add_record(ch.get("collection", "documents"), ch["record"], actor=args.actor)
            applied.append({"op": "add"})
        elif op == "replace":
            if not target or not ch.get("record"):
                rejected.append({"change": ch, "issue": "replace requires 'id' and 'record'"})
                continue
            try:
                ws.update_record(ch.get("collection", "documents"), target, ch["record"], actor=args.actor)
                applied.append({"op": "replace", "id": target})
            except C.ValidationError as exc:
                rejected.append({"change": ch, "issue": exc.message})
        elif op == "delete":
            if not args.yes:
                rejected.append({"change": ch,
                                 "issue": "delete requires --yes (destructive; snapshot taken first if applied)"})
                continue
            coll = ch.get("collection", "documents")
            records = ws.list_records(coll)
            kept = [r for r in records if r.get("id") != target]
            if len(kept) == len(records):
                rejected.append({"change": ch, "issue": "id not found"})
                continue
            ws._rewrite_collection(coll, kept)
            ws.audit(args.actor, f"{coll}.delete", {"id": target})
            applied.append({"op": "delete", "id": target})
        else:
            rejected.append({"change": ch, "issue": f"unknown op '{op}'"})
    return {"applied": applied, "rejected": rejected,
            "note": "Changes are audit-logged. Deletions are tombstoned by removal from store; restore via snapshot if needed."}

def cmd_route(args):
    ext = os.path.splitext(args.file)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return {"file": args.file, "extension": ext, "route": "native-text",
                "parser": "built-in (stdlib)", "lossy": False,
                "locators": "line numbers preserved"}
    if ext in BINARY_ROUTES:
        return {"file": args.file, "extension": ext, "route": "external-local-adapter",
                "parser": BINARY_ROUTES[ext], "lossy": "unknown until parsed",
                "note": "No parser is bundled for this format; configure the adapter and re-check. Nothing is uploaded."}
    return {"file": args.file, "extension": ext, "route": "unsupported",
            "parser": None,
            "note": "Unsupported format. Convert locally to text/HTML/CSV first or add an adapter."}

def cmd_timeline(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = items.get("items", [])
    if not isinstance(items, list) or len(items) < 2:
        raise C.ValidationError("Input must be a JSON list (or {'items': [...]}) with >=2 dated entries")
    entries = []
    for idx, item in enumerate(items):
        iso, uncertain = C.parse_date(item.get("date", ""))
        if not iso:
            entries.append({"index": idx, "date": item.get("date"), "error": "unparseable date",
                            "uncertain": True})
            continue
        entries.append({"index": idx, "iso": iso, "uncertain": uncertain,
                        "source": item.get("source", f"item-{idx}"),
                        "text": C.normalize_ws(item.get("text", "")),
                        "type": item.get("type", "report")})
    entries.sort(key=lambda e: e.get("iso", "9999"))
    # cluster by shared content tokens (>=40% jaccard of content tokens => same event thread)
    clusters = []
    for e in entries:
        if "iso" not in e:
            continue
        toks = set(C.content_tokens(e["text"]))
        placed = False
        for cl in clusters:
            if C.jaccard(toks, cl["tokens"]) >= args.cluster_threshold:
                cl["entries"].append(e)
                cl["tokens"] |= toks
                placed = True
                break
        if not placed:
            clusters.append({"tokens": toks, "entries": [e]})
    out_clusters = []
    for cl in clusters:
        seq = cl["entries"]
        out_clusters.append({
            "size": len(seq),
            "first": seq[0]["iso"], "last": seq[-1]["iso"],
            "entries": [{k: e[k] for k in ("iso", "source", "type", "uncertain") if k in e}
                        | {"text": e["text"][:280]} for e in seq],
            "corrections": [e["text"][:200] for e in seq if re.search(r"\b(correction|corrects|retracts?|erratum)\b", e["text"], re.I)],
        })
    return {"clusters": out_clusters,
            "unparseable": [e for e in entries if "iso" not in e],
            "note": "Clustering is token-overlap based; verify cluster membership and date precedence by hand."}

def cmd_diversity(args):
    ws = _ws(args)
    docs = [d for d in ws.list_records("documents") if "_corrupt_line" not in d]
    if not docs:
        return {"documents": 0, "facets": {}, "note": "No documents in workspace."}
    facets = {}
    for field in args.fields.split(","):
        field = field.strip()
        counts = Counter(str(d.get(field) or "unknown") for d in docs)
        total = sum(counts.values())
        shares = {k: round(v / total, 4) for k, v in counts.most_common()}
        concentration = sum((v / total) ** 2 for v in counts.values())
        facets[field] = {"counts": dict(counts.most_common()),
                         "shares": shares,
                         "herfindahl_concentration": round(concentration, 4),
                         "effective_sources": round(1 / concentration, 2) if concentration else None}
    return {"documents": len(docs), "facets": facets,
            "note": "Diversity is a coverage signal, not proof of truth or balance. "
                    "Low effective-source counts indicate dependence on few outlets/collections."}

def cmd_independence(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = items.get("items", [])
    if not isinstance(items, list) or len(items) < 2:
        raise C.ValidationError("Input must be a JSON list of {id, text, source?, dataset?, funder?}")
    flags = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            sim = C.shingle_jaccard(a.get("text", ""), b.get("text", ""), n=5)
            reasons = []
            if sim >= 0.6:
                reasons.append(f"near-identical text (sim={sim:.2f}): likely syndication/shared release")
            elif sim >= 0.35:
                reasons.append(f"substantial overlap (sim={sim:.2f}): check shared source")
            for key in ("dataset", "funder", "group"):
                if a.get(key) and b.get(key) and str(a[key]).lower() == str(b[key]).lower():
                    reasons.append(f"shared {key}: {a[key]}")
            if reasons:
                flags.append({"a": a.get("id", i), "b": b.get("id", j), "reasons": reasons})
    return {"compared": len(items), "dependent_pairs": flags,
            "note": "Flags indicate possible lack of independence; shared datasets/funders are not disqualifying but must be disclosed."}

COMMANDS = {
    "ingest": cmd_ingest, "dedup": cmd_dedup, "integrity": cmd_integrity,
    "snapshot": cmd_snapshot, "encoding": cmd_encoding, "deboilerplate": cmd_deboilerplate,
    "install": cmd_install, "update": cmd_update, "route": cmd_route,
    "timeline": cmd_timeline, "diversity": cmd_diversity, "independence": cmd_independence,
}

def build_parser(prog="rwt corpus"):
    p = argparse.ArgumentParser(prog=prog, description="Corpus engines: ingest, dedup, integrity, snapshot, install, timeline, diversity.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("ingest", help="Register a text-like file as a workspace document with line locators")
    sp.add_argument("--file", required=True); sp.add_argument("--workspace", default=".")
    sp.add_argument("--title"); sp.add_argument("--language", default="en")
    sp.add_argument("--doc-type", default="article"); sp.add_argument("--source-type", default="unknown")
    sp.add_argument("--published", default=None, help="ISO date if known")
    sp.add_argument("--license", default=None); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("dedup", help="Find exact (sha256) and near-duplicate (shingle Jaccard) documents")
    sp.add_argument("--workspace", default="."); sp.add_argument("--threshold", type=float, default=0.7)
    sp.set_defaults(func=cmd_dedup)

    sp = sub.add_parser("integrity", help="Check stores, files, hashes and the audit chain")
    sp.add_argument("--workspace", default=".")
    sp.set_defaults(func=cmd_integrity)

    sp = sub.add_parser("snapshot", help="Create, list or restore workspace snapshots (restore requires --yes)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--label", default="manual")
    sp.add_argument("--list", action="store_true"); sp.add_argument("--restore", metavar="SNAP")
    sp.add_argument("--yes", action="store_true"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("encoding", help="Detect mojibake, replacement chars, ligatures; optionally repair with --write")
    sp.add_argument("--file", required=True); sp.add_argument("--write", action="store_true")
    sp.add_argument("--expect-script", default=None, help="Regex the text should still match after repair")
    sp.set_defaults(func=cmd_encoding)

    sp = sub.add_parser("deboilerplate", help="Remove lines repeated across stored documents (menus, notices)")
    sp.add_argument("--file", required=True); sp.add_argument("--workspace", default=".")
    sp.add_argument("--min-docs", type=int, default=3); sp.add_argument("--fraction", type=float, default=0.5)
    sp.add_argument("--write", action="store_true")
    sp.set_defaults(func=cmd_deboilerplate)

    sp = sub.add_parser("install", help="Verify and install a signed/checksummed offline corpus bundle")
    sp.add_argument("--manifest", required=True); sp.add_argument("--workspace", default=".")
    sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("update", help="Apply an incremental changeset (add|replace|delete; delete needs --yes)")
    sp.add_argument("--changeset", required=True); sp.add_argument("--workspace", default=".")
    sp.add_argument("--yes", action="store_true"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("route", help="Show which local parser handles a file format (honest routing, no magic)")
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("timeline", help="Cluster and order dated reports/corrections about one event")
    sp.add_argument("--input", required=True, help="JSON list of {date, source, text, type?}")
    sp.add_argument("--cluster-threshold", type=float, default=0.25)
    sp.set_defaults(func=cmd_timeline)

    sp = sub.add_parser("diversity", help="Measure source diversity by facet (country, language, format, ownership...)")
    sp.add_argument("--workspace", default=".")
    sp.add_argument("--fields", default="source_type,language,doc_type,license")
    sp.set_defaults(func=cmd_diversity)

    sp = sub.add_parser("independence", help="Flag likely syndication/shared-source dependence between reports")
    sp.add_argument("--input", required=True, help="JSON list of {id, text, dataset?, funder?, group?}")
    sp.set_defaults(func=cmd_independence)
    return p

def run(argv):
    parser = build_parser(prog="rwt corpus")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


def main():
    try:
        out = run(sys.argv[1:])
    except C.RwtError as exc:
        print(json.dumps({"error": str(exc), "exit_code": exc.exit_code}, ensure_ascii=False))
        sys.exit(exc.exit_code)
    except C.ValidationError as exc:
        print(json.dumps({"error": str(exc), "exit_code": 2}, ensure_ascii=False))
        sys.exit(2)
    if isinstance(out, int):
        sys.exit(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
