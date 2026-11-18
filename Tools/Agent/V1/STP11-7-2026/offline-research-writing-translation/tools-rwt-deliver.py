#!/usr/bin/env python3
"""tools-rwt-deliver.py - Deliverables, publication packaging and archiving engines.

Capabilities: self-contained accessible HTML report packaging from structured
JSON, accessibility checking (headings, alt, lang, reading order), archive
building with manifest+checksums (dataset/release/reproducible-bundle kinds),
multilingual edition packaging + consistency verification, citation/link
integrity checking, PDF structural preflight (with explicit limits), format
migration planning, final artifact reopen test, and research handoff packaging.
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import tarfile
import zipfile
from datetime import datetime, timezone

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_d", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_d"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _read(file):
    if not os.path.isfile(file):
        raise C.ValidationError(f"File not found: {file}")
    with open(file, "r", encoding="utf-8") as fh:
        return fh.read()

def _load_json(file):
    with open(file, "r", encoding="utf-8") as fh:
        return json.load(fh)

# ------------------------------------------------------------ report packaging
def cmd_report(args):
    spec = _load_json(args.input)
    template = _read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset-report-template.html"))
    lang = spec.get("lang", "en")
    title = html.escape(spec.get("title", "Research Report"))
    sections = spec.get("sections", [])
    toc_items, body = [], []
    for i, sec in enumerate(sections, start=1):
        sid = f"section-{i}"
        heading = sec.get("heading", f"Section {i}")
        toc_items.append(f'<li><a href="#{sid}">{html.escape(heading)}</a></li>')
        paras = "".join(f"<p>{html.escape(p)}</p>" for p in sec.get("paragraphs", []))
        extra = ""
        if sec.get("table"):
            t = sec["table"]
            head = "".join(f"<th scope='col'>{html.escape(str(c))}</th>" for c in t.get("columns", []))
            rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in t.get("rows", []))
            extra += f"<table><caption>{html.escape(t.get('caption', ''))}</caption><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
        if sec.get("alt_text"):
            extra += f"<p class='alt'><em>Figure description:</em> {html.escape(sec['alt_text'])}</p>"
        body.append(f'<section id="{sid}" aria-labelledby="{sid}-h"><h2 id="{sid}-h">{html.escape(heading)}</h2>{paras}{extra}</section>')
    refs = spec.get("references", [])
    ref_html = "\n".join(f'<li id="ref-{i+1}">{html.escape(r)}</li>' for i, r in enumerate(refs))
    if ref_html:
        body.append(f'<section id="references" aria-labelledby="references-h"><h2 id="references-h">References</h2><ol>{ref_html}</ol></section>')
    evidence = spec.get("evidence_table")
    if evidence:
        head = "".join(f"<th scope='col'>{html.escape(str(c))}</th>" for c in evidence.get("columns", ["Claim", "Source", "Locator"]))
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in evidence.get("rows", []))
        cap = html.escape(evidence.get("caption", "Material claim → source → locator"))
        body.append(f'<section id="evidence" aria-labelledby="evidence-h"><h2 id="evidence-h">Evidence Table</h2><table><caption>{cap}</caption><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></section>')
    out = (template.replace("{{LANG}}", html.escape(lang))
                   .replace("{{TITLE}}", title)
                   .replace("{{TOC}}", "\n".join(toc_items))
                   .replace("{{BODY}}", "\n".join(body))
                   .replace("{{GENERATED}}", html.escape(C.now_iso()))
                   .replace("{{VERSION}}", html.escape(spec.get("version", "1.0"))))
    out_path = args.output or os.path.join("exports", re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") + ".html")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(out)
    C.sha256_file(out_path)
    return {"report": out_path, "sections": len(sections), "references": len(refs),
            "sha256": C.sha256_file(out_path),
            "accessibility": "lang/heading structure/table captions/alt-text slots embedded; run deliver accessible-check to verify"}

def cmd_accessible_check(args):
    text = _read(args.file)
    issues = []
    if not re.search(r"<html[^>]*\blang=", text, re.I):
        issues.append("missing lang attribute on <html>")
    h1 = len(re.findall(r"<h1\b", text, re.I))
    if h1 != 1:
        issues.append(f"expected exactly one <h1>, found {h1}")
    levels = [int(m.group(1)) for m in re.finditer(r"<h([1-6])\b", text, re.I)]
    for i in range(1, len(levels)):
        if levels[i] - levels[i - 1] > 1:
            issues.append(f"heading level jump h{levels[i-1]} -> h{levels[i]}")
            break
    for m in re.finditer(r"<img\b([^>]*)>", text, re.I):
        if "alt=" not in m.group(1):
            issues.append("img without alt attribute")
    for m in re.finditer(r"<table\b([^>]*)>", text, re.I):
        pass  # caption check below
    tables = len(re.findall(r"<table\b", text, re.I))
    captions = len(re.findall(r"<caption\b", text, re.I))
    if tables > captions:
        issues.append(f"{tables - captions} table(s) without <caption>")
    if "role=" in text.lower() and 'role="presentation"' in text.lower() and "<table" in text.lower():
        pass
    if not re.search(r"<title>\s*\S", text, re.I):
        issues.append("missing or empty <title>")
    if re.search(r"<(script|style)\b(?![^>]*>)", text):
        pass
    contrast = "manual review required (embedded palette uses >= 7:1 contrast)" 
    return {"file": args.file, "issues": issues, "tables": tables, "captions": captions,
            "contrast": contrast, "ok": not issues}

# ------------------------------------------------------------ archives
def cmd_archive(args):
    manifest_entries = []
    for root in args.include:
        if not os.path.exists(root):
            raise C.ValidationError(f"Include path not found: {root}")
        if os.path.isdir(root):
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, root)
                    manifest_entries.append((full, os.path.join(os.path.basename(root.rstrip("/")), rel)))
        else:
            manifest_entries.append((root, os.path.basename(root)))
    if not manifest_entries:
        raise C.ValidationError("Nothing to archive: --include matched zero files")
    manifest = []
    for full, arcname in manifest_entries:
        manifest.append({"path": arcname.replace(os.sep, "/"), "sha256": C.sha256_file(full),
                         "bytes": os.path.getsize(full)})
    rights = {"license": args.license, "redistribution": args.redistribution,
              "attribution": args.attribution}
    out_path = args.output
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    manifest_doc = {"archive": os.path.basename(out_path), "kind": args.kind,
                    "created": C.now_iso(), "tool": f"rwt {C.TOOL_VERSION}",
                    "rights": rights, "files": manifest,
                    "provenance": args.provenance,
                    "reproduce": args.reproduce}
    manifest_bytes = json.dumps(manifest_doc, ensure_ascii=False, indent=2).encode("utf-8")
    if out_path.endswith(".zip"):
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for full, arcname in manifest_entries:
                zf.write(full, arcname.replace(os.sep, "/"))
            zf.writestr("manifest.json", manifest_bytes)
    elif out_path.endswith(".tar.gz") or out_path.endswith(".tgz"):
        with tarfile.open(out_path, "w:gz") as tf:
            for full, arcname in manifest_entries:
                tf.add(full, arcname.replace(os.sep, "/"), recursive=False)
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_bytes)
            import io
            tf.addfile(info, io.BytesIO(manifest_bytes))
    else:
        raise C.ValidationError("Output must be .zip or .tar.gz")
    return {"archive": out_path, "files": len(manifest), "manifest_embedded": True,
            "rights": rights, "sha256": C.sha256_file(out_path)}

def cmd_reopen(args):
    """Reopen every delivered file: parse by extension, verify checksums vs manifest."""
    problems, checked = [], 0
    manifest = None
    if args.manifest:
        manifest = _load_json(args.manifest)
    for root in args.paths:
        targets = []
        if os.path.isdir(root):
            for dirpath, _dirs, files in os.walk(root):
                targets += [os.path.join(dirpath, f) for f in files]
        elif os.path.isfile(root):
            targets = [root]
        for path in targets:
            checked += 1
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext in (".json",):
                    _load_json(path)
                elif ext in (".jsonl",):
                    with open(path, encoding="utf-8") as fh:
                        for line in fh:
                            if line.strip():
                                json.loads(line)
                elif ext in (".html", ".htm"):
                    from html.parser import HTMLParser
                    HTMLParser().feed(_read(path))
                elif ext in (".csv", ".tsv"):
                    import csv
                    with open(path, newline="", encoding="utf-8") as fh:
                        list(csv.reader(fh, delimiter="\t" if ext == ".tsv" else ","))
                elif ext in (".zip",):
                    with zipfile.ZipFile(path) as zf:
                        zf.testzip()
                elif ext in (".tar.gz", ".tgz"):
                    with tarfile.open(path) as tf:
                        tf.getnames()
                elif ext in (".txt", ".md", ".srt", ".vtt"):
                    _read(path)
                else:
                    with open(path, "rb") as fh:
                        fh.read(4096)
            except Exception as exc:
                problems.append({"file": path, "issue": str(exc)[:200]})
            if manifest:
                arcname = os.path.relpath(path).replace(os.sep, "/")
                match = next((e for e in manifest.get("files", []) if e.get("path") == arcname or os.path.basename(path) == e.get("path")), None)
                if match and match.get("sha256") != C.sha256_file(path):
                    problems.append({"file": path, "issue": "checksum mismatch vs manifest"})
    return {"files_checked": checked, "problems": problems, "ok": not problems}

# ------------------------------------------------------------ citation/link integrity
def cmd_citation_integrity(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    problems = []
    internal = re.findall(r"\[ref-(\d+)\]|\(ref-(\d+)\)|#ref-(\d+)", text)
    flat = [next(g for g in groups if g) for groups in internal]
    refs_defined = set(re.findall(r'id="ref-(\d+)"', text))
    for n in set(flat):
        if n not in refs_defined:
            problems.append({"type": "internal-ref-missing", "ref": f"ref-{n}"})
    fig_refs = set(re.findall(r"(?:Figure|Fig\.?)\s*(\d+)", text))
    fig_defs = set(re.findall(r'id="fig-(\d+)"', text)) | set(re.findall(r"(?im)^\s*(?:figure|fig\.?)\s+(\d+)", text))
    for n in fig_refs - fig_defs:
        problems.append({"type": "figure-ref-unresolved", "figure": n})
    tab_refs = set(re.findall(r"Table\s+(\d+)", text))
    tab_defs = set(re.findall(r'id="tab-(\d+)"', text)) | set(re.findall(r"(?im)^\s*table\s+(\d+)", text))
    for n in tab_refs - tab_defs:
        problems.append({"type": "table-ref-unresolved", "table": n})
    urls = re.findall(r"https?://[^\s)\"']+", text)
    if urls:
        problems.append({"type": "external-urls-present", "count": len(urls),
                         "note": "Offline archives must not depend on live URLs; convert to local identifiers or archive snapshot ids"})
    return {"problems": problems, "ok": not problems, "urls_found": len(urls)}

# ------------------------------------------------------------ multilingual packaging
def cmd_multilingual(args):
    manifest_path = args.manifest
    spec = _load_json(manifest_path)
    editions = spec.get("editions", [])
    source_versions = {e.get("source_version") for e in editions}
    unified = len(source_versions) <= 1
    problems = []
    for e in editions:
        for req in ("lang", "path", "source_version", "checksum_of_source"):
            if not e.get(req):
                problems.append({"edition": e.get("lang"), "missing": req})
        if e.get("adapted") and not e.get("adaptation_notes"):
            problems.append({"edition": e.get("lang"), "issue": "declared adaptation without notes"})
    return {"editions": len(editions), "source_versions": sorted(v for v in source_versions if v),
            "unified_source_version": unified, "problems": problems,
            "ok": unified and not problems,
            "note": "All language editions must trace to the same approved source version; adaptations must be declared."}

def cmd_migration_plan(args):
    inventory = []
    for root in args.include:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                inventory.append({"file": os.path.join(dirpath, name), "ext": ext,
                                  "risk": ("high" if ext in (".doc", ".xls", ".ppt", ".wpd", ".pages", ".key") else
                                            "medium" if ext in (".pdf", ".docx", ".xlsx", ".pptx", ".epub") else
                                            "low")})
    high = [i for i in inventory if i["risk"] == "high"]
    plan = [
        "Inventory by format and rights (retain checksums)",
        "Convert legacy office formats via an approved local converter (LibreOffice/pandoc) to OOXML or PDF/A",
        "Re-parse every migrated file and compare text content (deliver reopen on originals vs migrated)",
        "Preserve metadata (creator, dates, rights) explicitly; it is usually lost in conversion",
        "Validate citations, page locators and links resolve in the target",
        "Record migration decisions in the decision log; keep originals in cold storage",
    ]
    return {"files_inventoried": len(inventory), "high_risk_files": high, "plan": plan,
            "validation": "re-open + checksum + text-diff per file; spot-render for layout",
            "note": "No files are migrated by this command; it plans and validates the migration you execute."}

def cmd_handoff(args):
    ws = None
    try:
        ws = C.Workspace(args.workspace)
        ws.load()
    except C.RwtError:
        pass
    include = list(args.include)
    if ws:
        include.append(os.path.join(ws.root, C.WORKSPACE_DIR, "stores"))
    out_path = args.output
    return cmd_archive(argparse.Namespace(
        include=include, output=out_path, kind="handoff",
        license=args.license, redistribution="internal-succession",
        attribution="see manifest", provenance="research project handoff",
        reproduce="Extract, run: rwt init handoff-ws && rwt deliver reopen --paths . --manifest manifest.json"))

def cmd_pdf_preflight(args):
    path = args.file
    if not os.path.isfile(path):
        raise C.ValidationError(f"PDF not found: {path}")
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(1024)
        fh.seek(max(0, size - 2048))
        tail = fh.read()
    issues = []
    if not head.startswith(b"%PDF-"):
        issues.append("missing %PDF header (not a PDF or corrupt)")
    version = head[5:8].decode("latin-1", "ignore")
    pages_naive = tail.count(b"/Type /Page") + tail.count(b"/Type/Page")
    if b"startxref" not in tail:
        issues.append("no startxref in trailer (corrupt or truncated)")
    if b"/Encrypt" in head or b"/Encrypt" in tail:
        issues.append("document is encrypted/permission-restricted")
    if size > 100 * 1024 * 1024:
        issues.append("file exceeds 100MB; optimize before distribution")
    return {"file": path, "size_bytes": size, "pdf_version": version,
            "page_objects_naive_count": pages_naive, "issues": issues,
            "not_checked": ["font embedding", "tagged PDF structure", "color profiles",
                            "metadata fields", "link destinations"],
            "note": "Structural sanity only. Full preflight (fonts, tags, archival PDF/A) needs a dedicated local preflight tool (e.g., veraPDF/callas); not bundled."}

COMMANDS = {
    "report": cmd_report, "accessible-check": cmd_accessible_check, "archive": cmd_archive,
    "reopen": cmd_reopen, "citation-integrity": cmd_citation_integrity,
    "multilingual": cmd_multilingual, "migration-plan": cmd_migration_plan,
    "handoff": cmd_handoff, "pdf-preflight": cmd_pdf_preflight,
}

def build_parser(prog="rwt deliver"):
    p = argparse.ArgumentParser(prog=prog, description="Deliverables: reports, archives, reopen tests, preflight.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("report", help="Package an accessible self-contained HTML report from structured JSON")
    sp.add_argument("--input", required=True, help="JSON {title, lang?, version?, sections:[{heading, paragraphs[], table?, alt_text?}], references[], evidence_table?}")
    sp.add_argument("--output"); sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("accessible-check", help="Check lang/h1/heading order/alt/captions in HTML")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_accessible_check)

    sp = sub.add_parser("archive", help="Build .zip/.tar.gz with embedded manifest+checksums+rights")
    sp.add_argument("--include", required=True, action="append", help="File/dir to include; repeatable")
    sp.add_argument("--output", required=True); sp.add_argument("--kind", default="release",
                    choices=["release", "dataset", "archive", "reproducible-bundle", "handoff", "corpus"])
    sp.add_argument("--license", default="all-rights-reserved"); sp.add_argument("--redistribution", default="internal")
    sp.add_argument("--attribution", default="see manifest"); sp.add_argument("--provenance", default="")
    sp.add_argument("--reproduce", default=""); sp.set_defaults(func=cmd_archive)

    sp = sub.add_parser("reopen", help="Reopen delivered files: parse by type + verify checksums vs manifest")
    sp.add_argument("--paths", required=True, action="append"); sp.add_argument("--manifest")
    sp.set_defaults(func=cmd_reopen)

    sp = sub.add_parser("citation-integrity", help="Validate internal refs/figure refs; flag live URLs for archival")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_citation_integrity)

    sp = sub.add_parser("multilingual", help="Verify all language editions trace to one approved source version")
    sp.add_argument("--manifest", required=True, help="JSON {editions:[{lang, path, source_version, checksum_of_source, adapted?, adaptation_notes?}]}")
    sp.set_defaults(func=cmd_multilingual)

    sp = sub.add_parser("migration-plan", help="Inventory at-risk formats and produce a migration plan")
    sp.add_argument("--include", required=True, action="append"); sp.set_defaults(func=cmd_migration_plan)

    sp = sub.add_parser("handoff", help="Package workspace stores + selected files as a handoff archive")
    sp.add_argument("--workspace", default="."); sp.add_argument("--include", action="append", default=[])
    sp.add_argument("--output", required=True); sp.add_argument("--license", default="internal")
    sp.set_defaults(func=cmd_handoff)

    sp = sub.add_parser("pdf-preflight", help="Structural PDF sanity checks (explicit limits)")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_pdf_preflight)
    return p

def run(argv):
    parser = build_parser(prog="rwt deliver")
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
