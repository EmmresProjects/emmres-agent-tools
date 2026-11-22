#!/usr/bin/env python3
"""tools-rwt-collections.py - Typed local registries/collections + provenance + freshness engines.

One generic engine implements the many "maintains a local collection/registry"
catalog skills (scholarly, news, legal, standards, datasets, patents-adjacent,
plagiarism corpora, license records, language assets, publications, queries,
disclosures, feedback, corrections, acceptance evaluations). Records carry
name/kind/status/dates/rights/links/notes plus arbitrary fields.

Provenance engines: manifests from file checksums, transformation-chain
verification, gap detection, snapshot identifiers, source->claim->draft lineage,
run manifests, timestamp/version freshness controls, staleness risk scoring,
freshness review scheduling, peer-review/authority classification helpers,
primary-source prioritization, and unavailable-source disclosure marking.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_col", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_col"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

CHANGERATE = {  # relative volatility by domain (staleness model)
    "news": 0.95, "statistics": 0.8, "prices": 0.9, "law": 0.45, "regulation": 0.5,
    "policy": 0.5, "standards": 0.3, "medical": 0.35, "science": 0.25,
    "history": 0.02, "mathematics": 0.01, "biography": 0.1, "software": 0.85,
}

def cmd_records_add(args):
    ws = _ws(args)
    extra = {}
    for kv in args.field or []:
        if "=" not in kv:
            raise C.ValidationError(f"--field expects key=value, got '{kv}'")
        k, v = kv.split("=", 1)
        try:
            extra[k] = json.loads(v)
        except json.JSONDecodeError:
            extra[k] = v
    rec = ws.add_record("registry", {
        "name": args.name, "kind": args.kind, "status": args.status,
        "license": args.license, "territory": args.territory,
        "expires": args.expires, "version": args.version,
        "url_local": args.locator, "identifiers": args.identifiers.split(",") if args.identifiers else [],
        "notes": args.notes,
        **extra,
    }, actor=args.actor)
    return {"record_id": rec["id"], "kind": args.kind, "name": args.name}

def cmd_records_list(args):
    ws = _ws(args)
    recs = ws.list_records("registry")
    if args.kind:
        recs = [r for r in recs if r.get("kind") == args.kind]
    if args.status:
        recs = [r for r in recs if r.get("status") == args.status]
    if args.contains:
        q = args.contains.lower()
        recs = [r for r in recs if q in json.dumps(r, ensure_ascii=False).lower()]
    return {"records": recs[-args.limit:], "count": len(recs)}

def cmd_records_update(args):
    ws = _ws(args)
    extra = {}
    for kv in args.field or []:
        k, v = kv.split("=", 1)
        try:
            extra[k] = json.loads(v)
        except json.JSONDecodeError:
            extra[k] = v
    rec = ws.update_record("registry", args.id, extra, actor=args.actor)
    return {"updated": rec["id"]}

def cmd_import(args):
    ws = _ws(args)
    if not os.path.isfile(args.file):
        raise C.ValidationError(f"Import file not found: {args.file}")
    ext = os.path.splitext(args.file)[1].lower()
    rows = []
    with open(args.file, "r", encoding="utf-8") as fh:
        if ext in (".jsonl",):
            rows = [json.loads(l) for l in fh if l.strip()]
        elif ext == ".json":
            data = json.load(fh)
            rows = data if isinstance(data, list) else data.get("items", data.get("records", []))
        elif ext in (".csv", ".tsv"):
            import csv
            delim = "\t" if ext == ".tsv" else ","
            rows = list(csv.DictReader(fh, delimiter=delim))
        else:
            raise C.ValidationError("Import supports .json, .jsonl, .csv, .tsv")
    imported = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = {k.strip(): v for k, v in row.items()}
        name = row.get("name") or row.get("title") or row.get("id")
        if not name:
            continue
        ws.add_record("registry", {
            "name": str(name), "kind": args.kind, "status": row.get("status", "imported"),
            "license": row.get("license"), "territory": row.get("territory"),
            "expires": row.get("expires") or row.get("expiry"),
            "version": row.get("version"),
            "identifiers": [row[c] for c in ("doi", "isbn", "issn", "pmid", "arxiv", "oclc", "local_id") if row.get(c)],
            "metadata": row,
        }, actor=args.actor)
        imported += 1
    return {"imported": imported, "kind": args.kind}

def cmd_links(args):
    ws = _ws(args)
    rec = ws.add_record("lineage", {
        "from_id": args.from_id, "to_id": args.to_id,
        "relation": args.relation, "note": args.note,
    }, actor=args.actor)
    return {"edge_id": rec["id"]}

def cmd_link_list(args):
    ws = _ws(args)
    edges = ws.list_records("lineage")
    if args.node:
        edges = [e for e in edges if args.node in (e.get("from_id"), e.get("to_id"))]
    if args.relation:
        edges = [e for e in edges if e.get("relation") == args.relation]
    return {"edges": edges, "count": len(edges)}

def cmd_retraction(args):
    ws = _ws(args)
    rec = ws.add_record("registry", {
        "name": args.target, "kind": "retraction-notice",
        "status": args.notice_type, "target_id": args.target_id,
        "notice_date": args.date, "reason": args.reason,
    }, actor=args.actor)
    # mark any workspace document as flagged
    flagged = []
    for d in ws.list_records("documents"):
        if args.target_id and d.get("id") == args.target_id:
            ws.update_record("documents", d["id"], {"integrity_flag": args.notice_type,
                                                     "notice_id": rec["id"]}, actor=args.actor)
            flagged.append(d["id"])
    return {"notice_id": rec["id"], "documents_flagged": flagged,
            "note": "Flagged documents surface integrity_flag in downstream outputs; the corpus is not silently edited."}

def cmd_coverage(args):
    ws = _ws(args)
    recs = ws.list_records(args.collection)
    recs = [r for r in recs if "_corrupt_line" not in r]
    facets = {}
    for field in args.fields.split(","):
        field = field.strip()
        counts = Counter(str(r.get(field) or (r.get("metadata", {}) or {}).get(field) or "unknown") for r in recs)
        total = sum(counts.values())
        shares = {k: round(v / total, 4) for k, v in counts.most_common()}
        facets[field] = {"counts": dict(counts.most_common()), "shares": shares}
    targets = {}
    if args.targets and os.path.isfile(args.targets):
        with open(args.targets, "r", encoding="utf-8") as fh:
            targets = json.load(fh)
    gaps = []
    for field, minimums in (targets.items() if isinstance(targets, dict) else []):
        counts = facets.get(field, {}).get("counts", {})
        for value, minimum in (minimums or {}).items():
            have = counts.get(value, 0)
            if have < int(minimum):
                gaps.append({"facet": field, "value": value, "have": have, "target": int(minimum)})
    return {"collection": args.collection, "records": len(recs), "facets": facets,
            "gaps_vs_targets": gaps,
            "note": "Material gaps vs declared targets; unknown-share above 10% signals poor metadata hygiene."}

def cmd_classify_review(args):
    text = C.normalize_ws(args.text).lower()
    meta = {}
    if args.metadata and os.path.isfile(args.metadata):
        with open(args.metadata, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    label = "unknown"
    if meta.get("peer_reviewed") is True:
        label = "peer-reviewed"
    elif meta.get("type") == "preprint" or re.search(r"\bpreprint\b|\barXiv\b|\bbiorxiv\b|\bssrn\b", text, re.I):
        label = "preprint"
    elif meta.get("type") == "thesis" or re.search(r"\bthesis\b|\bdissertation\b", text, re.I):
        label = "thesis"
    elif meta.get("type") == "report" or re.search(r"\bworking paper\b|\btechnical report\b", text, re.I):
        label = "working-paper/report"
    elif meta.get("editorial_reviewed") is True or re.search(r"\bofficial gazette\b|\bgazette\b", text, re.I):
        label = "editorially-reviewed"
    elif meta.get("commentary") is True or re.search(r"\bcommentary\b|\bletter\b|\beditorial\b", text, re.I):
        label = "commentary"
    return {"label": label, "metadata_used": meta or None,
            "confidence": "metadata-driven" if meta else "cue-word-heuristic",
            "review_required": True,
            "note": "Classification from supplied metadata/cues; unknown is a valid, honest label."}

def cmd_classify_authority(args):
    meta = {}
    if args.metadata and os.path.isfile(args.metadata):
        with open(args.metadata, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    score = 0.0
    reasons = []
    if meta.get("primary"):
        score += 0.35; reasons.append("primary source flag")
    if meta.get("peer_reviewed"):
        score += 0.2; reasons.append("peer-reviewed flag")
    if meta.get("issuing_body"):
        score += 0.15; reasons.append("issuing body recorded")
    if meta.get("methodology_available") is not False:
        score += 0.15; reasons.append("method transparency")
    if meta.get("coi_disclosed") is True:
        score += 0.15; reasons.append("COI disclosed")
    label = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
    return {"authority": label, "score": round(score, 2), "reasons": reasons,
            "primary_vs_secondary": "primary" if meta.get("primary") else "secondary",
            "review_required": True,
            "note": "Scoring rubric is configurable policy, not proof of quality."}

def cmd_prioritize_primary(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = items.get("items", [])
    ranked = sorted(items, key=lambda it: (
        0 if it.get("primary") else 1,
        0 if it.get("peer_reviewed") else 1,
        str(it.get("published", "9999")),
    ))
    return {"ordered": ranked,
            "note": "Primaries first, then peer-reviewed, then recency. Secondary reporting is retained for context, not as the evidentiary base."}

def cmd_disclose(args):
    ws = _ws(args)
    rec = ws.add_record("registry", {
        "name": args.name, "kind": "unavailable-source", "status": "disclosed",
        "known_via": args.known_via, "cited_from": args.cited_from, "locator": args.locator,
    }, actor=args.actor)
    return {"disclosure_id": rec["id"],
            "required_disclosure_text": f"Source known only via {args.known_via}; original not inspected. Do not assert its contents beyond the referring source."}

def cmd_provenance_manifest(args):
    if args.file:
        targets = [args.file]
    else:
        raise C.ValidationError("Provide --file (one manifest per file; loop for sets)")
    out = []
    for path in targets:
        digest = C.sha256_file(path)
        out.append({"file": os.path.basename(path), "sha256": digest,
                    "bytes": os.path.getsize(path),
                    "captured": C.now_iso(),
                    "origin": args.origin, "creator": args.creator,
                    "publisher": args.publisher, "license": args.license,
                    "version": args.version, "transformations": args.transformations.split(",") if args.transformations else [],
                    "custody": args.custody})
    return {"manifest": out}

def cmd_provenance_verify_chain(args):
    with open(args.chain, "r", encoding="utf-8") as fh:
        steps = json.load(fh)
    if isinstance(steps, dict):
        steps = steps.get("steps", [])
    problems = []
    for i, step in enumerate(steps):
        want, got = step.get("sha256"), None
        f = step.get("file")
        if f and os.path.isfile(f):
            got = C.sha256_file(f)
        if want and got and want.lower() != got.lower():
            problems.append({"step": i, "file": f, "issue": "checksum mismatch", "expected": want, "got": got})
        if step.get("expects_transform") and not step.get("transform"):
            problems.append({"step": i, "issue": "transformation step missing (original -> extracted passage -> claim -> citation)"})
    return {"steps": len(steps), "problems": problems, "ok": not problems}

def cmd_provenance_gaps(args):
    ws = _ws(args)
    required = ["origin", "license", "version", "created", "sha256"]
    gaps = []
    for coll in ("documents", "registry", "references"):
        for rec in ws.list_records(coll):
            missing = [k for k in required if not rec.get(k) and not (coll == "registry" and k == "sha256" and not rec.get("url_local"))]
            if missing:
                gaps.append({"collection": coll, "id": rec.get("id"),
                             "name": rec.get("name") or rec.get("title"), "missing": missing})
    return {"gaps": gaps, "count": len(gaps),
            "note": "Fill gaps by editing records with collections records-update --field key=value."}

def cmd_provenance_snapshot_id(args):
    ws = _ws(args)
    entries = {}
    for coll in sorted(C.ALLOWED_COLLECTIONS):
        recs = ws.list_records(coll)
        if recs:
            entries[coll] = C.sha256_bytes(
                "".join(C.json_dump(r) + "\n" for r in recs).encode("utf-8"))
    corpus_id = C.sha256_bytes(json.dumps(entries, sort_keys=True).encode("utf-8"))
    return {"corpus_snapshot_id": corpus_id, "collection_hashes": entries,
            "created": C.now_iso(),
            "note": "Point-in-time identifier: two workspaces with the same stores produce the same id."}

def cmd_provenance_lineage(args):
    ws = _ws(args)
    edges = ws.list_records("lineage")
    if args.from_id:
        out, frontier, seen = [], {args.from_id}, {args.from_id}
        for _depth in range(args.depth):
            nxt = set()
            for e in edges:
                if e.get("from_id") in frontier:
                    out.append(e)
                    nxt.add(e.get("to_id"))
            frontier = nxt - seen
            seen |= frontier
            if not frontier:
                break
        return {"root": args.from_id, "path": out, "depth": args.depth}
    if args.to_id:
        return {"target": args.to_id,
                "incoming": [e for e in edges if e.get("to_id") == args.to_id]}
    return {"edges": edges[-args.limit:], "count": len(edges)}

def cmd_provenance_run_manifest(args):
    ws = _ws(args)
    cfg = ws.load()
    registries = {}
    for kind in ("model", "corpus", "prompt", "tool-schema"):
        recs = [r for r in ws.list_records("registry") if r.get("kind") == kind]
        registries[kind] = [{"name": r.get("name"), "version": r.get("version"), "status": r.get("status")}
                            for r in recs]
    docs_hash = C.sha256_bytes("".join(
        C.json_dump(r) + "\n" for r in ws.list_records("documents")).encode("utf-8"))
    manifest = {"tool": f"rwt {C.TOOL_VERSION}",
                "created": C.now_iso(),
                "workspace": cfg.get("name"),
                "preset": cfg.get("preset"),
                "corpus_store_hash": docs_hash,
                "registries": registries,
                "languages": cfg.get("languages"),
                "actor": args.actor,
                "notes": args.notes}
    digest = C.sha256_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    manifest["manifest_sha256"] = digest
    out = args.output
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return {"manifest": manifest, "written": bool(out)}

def cmd_freshness_check(args):
    ws = _ws(args)
    docs = ws.list_records("documents")
    flagged = []
    max_age = args.max_age_years
    for d in docs:
        pub = d.get("published")
        if not pub:
            flagged.append({"id": d.get("id"), "title": d.get("title"), "issue": "no publication date recorded"})
            continue
        age = C.years_since(pub)
        domain = (d.get("doc_type") or d.get("source_type") or "general").lower()
        vol = CHANGERATE.get(domain, 0.4)
        if age is not None and age > max_age and vol > 0.3:
            flagged.append({"id": d.get("id"), "title": d.get("title"),
                            "age_years": age, "volatility_domain": domain,
                            "issue": "potentially stale for a high-change domain"})
    return {"checked": len(docs), "flagged": flagged, "max_age_years": max_age,
            "note": "Dates missing or older than the limit in volatile domains. Supersession links outrank age."}

def cmd_freshness_score(args):
    iso, uncertain = C.parse_date(args.date)
    age = C.years_since(iso) if iso else None
    volatility = CHANGERATE.get(args.domain.lower(), 0.4)
    supersession = 1.0 if args.superseded else 0.0
    if age is None:
        risk = 0.9
    else:
        risk = min(1.0, round(0.25 + 0.35 * volatility + 0.35 * min(1.0, age / max(1.0, args.horizon_years)) + 0.4 * supersession, 3))
    return {"date": iso, "date_uncertain": uncertain, "age_years": age,
            "domain": args.domain, "volatility": volatility,
            "superseded": bool(args.superseded),
            "staleness_risk": risk,
            "verdict": "refresh-required" if risk >= 0.66 else "monitor" if risk >= 0.4 else "current"}

def cmd_freshness_schedule(args):
    ws = _ws(args)
    docs = ws.list_records("documents")
    due = []
    for d in docs:
        pub = d.get("published")
        if not pub:
            continue
        age = C.years_since(pub)
        domain = (d.get("doc_type") or d.get("source_type") or "general").lower()
        vol = CHANGERATE.get(domain, 0.4)
        interval = max(0.5, 3.0 - 2.5 * vol)  # years between reviews
        if age is not None and age >= interval:
            due.append({"id": d.get("id"), "title": d.get("title"), "domain": domain,
                        "age_years": age, "review_interval_years": round(interval, 2)})
    rec = ws.add_record("queue", {"kind": "freshness-review", "status": "open",
                                  "items": [d["id"] for d in due], "opened": C.now_iso(),
                                  "assignee": args.assignee},
                        actor=args.actor)
    return {"queued": len(due), "queue_id": rec["id"], "due": due,
            "note": "Scheduled for controlled offline update; scheduling does not fetch anything by itself."}

def cmd_readiness(args):
    checklist = C.load_asset("asset-readiness-checklist.json")
    with open(args.evidence, "r", encoding="utf-8") as fh:
        evidence = json.load(fh)
    if isinstance(evidence, dict):
        evidence_items = evidence
    else:
        evidence_items = {step: True for step in evidence}
    results, missing = [], []
    for check in checklist["checks"]:
        cid = check["id"]
        ok = bool(evidence_items.get(cid))
        results.append({"id": cid, "description": check["description"], "required": check.get("required", True), "satisfied": ok})
        if check.get("required", True) and not ok:
            missing.append(cid)
    return {"gate": "blocked" if missing else "ready",
            "missing_required": missing, "results": results,
            "note": "Blocking means 'not ready for publication'; it does not forbid internal review."}

COMMANDS = {
    "records-add": cmd_records_add, "records-list": cmd_records_list, "records-update": cmd_records_update,
    "import": cmd_import, "links": cmd_links, "link-list": cmd_link_list,
    "retraction": cmd_retraction, "coverage": cmd_coverage,
    "classify-review": cmd_classify_review, "classify-authority": cmd_classify_authority,
    "prioritize-primary": cmd_prioritize_primary, "disclose": cmd_disclose,
    "provenance-manifest": cmd_provenance_manifest, "provenance-verify-chain": cmd_provenance_verify_chain,
    "provenance-gaps": cmd_provenance_gaps, "provenance-snapshot-id": cmd_provenance_snapshot_id,
    "provenance-lineage": cmd_provenance_lineage, "provenance-run-manifest": cmd_provenance_run_manifest,
    "freshness-check": cmd_freshness_check, "freshness-score": cmd_freshness_score,
    "freshness-schedule": cmd_freshness_schedule, "readiness": cmd_readiness,
}

def build_parser(prog="rwt collections"):
    p = argparse.ArgumentParser(prog=prog, description="Typed registries, provenance manifests, freshness controls.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("records-add", help="Add a record to the shared registry store")
    sp.add_argument("--workspace", default="."); sp.add_argument("--name", required=True)
    sp.add_argument("--kind", required=True, help="e.g. scholarly|reference-work|thesis|proceedings|dataset|news|legal|standard|gazette|license|model|corpus|prompt|tool-schema|plagiarism-source|publication|feedback|correction|disclosure|coi|acceptance-eval|factcheck|evidence")
    sp.add_argument("--status", default="active"); sp.add_argument("--license")
    sp.add_argument("--territory"); sp.add_argument("--expires"); sp.add_argument("--version")
    sp.add_argument("--locator"); sp.add_argument("--identifiers"); sp.add_argument("--notes")
    sp.add_argument("--field", action="append", help="Extra field key=value (JSON value ok); repeatable")
    sp.add_argument("--actor", default="local"); sp.set_defaults(func=cmd_records_add)

    sp = sub.add_parser("records-list", help="List/filter registry records")
    sp.add_argument("--workspace", default="."); sp.add_argument("--kind"); sp.add_argument("--status")
    sp.add_argument("--contains"); sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_records_list)

    sp = sub.add_parser("records-update", help="Update fields on a registry record")
    sp.add_argument("--workspace", default="."); sp.add_argument("--id", required=True)
    sp.add_argument("--field", action="append", required=True); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_records_update)

    sp = sub.add_parser("import", help="Import approved repository exports (.json/.jsonl/.csv/.tsv) preserving metadata")
    sp.add_argument("--workspace", default="."); sp.add_argument("--file", required=True)
    sp.add_argument("--kind", required=True); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("links", help="Add a typed link (preprint->published, amendment, interest, dependency)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--from-id", required=True)
    sp.add_argument("--to-id", required=True); sp.add_argument("--relation", required=True)
    sp.add_argument("--note"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_links)

    sp = sub.add_parser("link-list", help="List typed links")
    sp.add_argument("--workspace", default="."); sp.add_argument("--node"); sp.add_argument("--relation")
    sp.set_defaults(func=cmd_link_list)

    sp = sub.add_parser("retraction", help="Record a retraction/correction notice and flag linked documents")
    sp.add_argument("--workspace", default="."); sp.add_argument("--target", required=True)
    sp.add_argument("--target-id"); sp.add_argument("--notice-type", default="retraction",
                    choices=["retraction", "expression-of-concern", "erratum", "corrigendum", "replacement"])
    sp.add_argument("--date"); sp.add_argument("--reason"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_retraction)

    sp = sub.add_parser("coverage", help="Facet coverage counts + gap analysis vs target JSON")
    sp.add_argument("--workspace", default="."); sp.add_argument("--collection", default="registry")
    sp.add_argument("--fields", default="kind,status,license")
    sp.add_argument("--targets", default=None, help="JSON {field: {value: minimum}}")
    sp.set_defaults(func=cmd_coverage)

    sp = sub.add_parser("classify-review", help="Label source by review status from metadata/cues")
    sp.add_argument("--text", default=""); sp.add_argument("--metadata")
    sp.set_defaults(func=cmd_classify_review)

    sp = sub.add_parser("classify-authority", help="Authority scoring rubric for a source (configurable policy)")
    sp.add_argument("--metadata", required=True, help="JSON {primary, peer_reviewed, issuing_body, methodology_available, coi_disclosed}")
    sp.set_defaults(func=cmd_classify_authority)

    sp = sub.add_parser("prioritize-primary", help="Order sources: primary > peer-reviewed > recent")
    sp.add_argument("--input", required=True, help="JSON list of source records")
    sp.set_defaults(func=cmd_prioritize_primary)

    sp = sub.add_parser("disclose", help="Record a source known only via secondary description")
    sp.add_argument("--workspace", default="."); sp.add_argument("--name", required=True)
    sp.add_argument("--known-via", default="secondary description"); sp.add_argument("--cited-from")
    sp.add_argument("--locator"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_disclose)

    sp = sub.add_parser("provenance-manifest", help="Create a provenance manifest for a file (checksum, custody, license, version)")
    sp.add_argument("--file", required=True); sp.add_argument("--origin"); sp.add_argument("--creator")
    sp.add_argument("--publisher"); sp.add_argument("--license"); sp.add_argument("--version")
    sp.add_argument("--transformations", default=""); sp.add_argument("--custody")
    sp.set_defaults(func=cmd_provenance_manifest)

    sp = sub.add_parser("provenance-verify-chain", help="Verify checksums/transform steps across a chain JSON")
    sp.add_argument("--chain", required=True, help="JSON list of {file?, sha256?, transform?}")
    sp.set_defaults(func=cmd_provenance_verify_chain)

    sp = sub.add_parser("provenance-gaps", help="List records missing origin/license/version/checksum/dates")
    sp.add_argument("--workspace", default="."); sp.set_defaults(func=cmd_provenance_gaps)

    sp = sub.add_parser("provenance-snapshot-id", help="Point-in-time identifier for the current workspace stores")
    sp.add_argument("--workspace", default="."); sp.set_defaults(func=cmd_provenance_snapshot_id)

    sp = sub.add_parser("provenance-lineage", help="Query source->claim->draft->translation lineage edges")
    sp.add_argument("--workspace", default="."); sp.add_argument("--from-id"); sp.add_argument("--to-id")
    sp.add_argument("--depth", type=int, default=5); sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_provenance_lineage)

    sp = sub.add_parser("provenance-run-manifest", help="Capture exact tool/corpus/registry state for a run")
    sp.add_argument("--workspace", default="."); sp.add_argument("--actor", default="local")
    sp.add_argument("--notes"); sp.add_argument("--output"); sp.set_defaults(func=cmd_provenance_run_manifest)

    sp = sub.add_parser("freshness-check", help="Flag missing/stale sources in volatile domains")
    sp.add_argument("--workspace", default="."); sp.add_argument("--max-age-years", type=float, default=5)
    sp.set_defaults(func=cmd_freshness_check)

    sp = sub.add_parser("freshness-score", help="Staleness risk score for one fact/source")
    sp.add_argument("--date", required=True); sp.add_argument("--domain", default="general")
    sp.add_argument("--superseded", action="store_true"); sp.add_argument("--horizon-years", type=float, default=5)
    sp.set_defaults(func=cmd_freshness_score)

    sp = sub.add_parser("freshness-schedule", help="Queue volatile sources for controlled offline update")
    sp.add_argument("--workspace", default="."); sp.add_argument("--assignee"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_freshness_schedule)

    sp = sub.add_parser("readiness", help="Publication readiness gate from the bundled checklist + evidence JSON")
    sp.add_argument("--evidence", required=True, help="JSON {check_id: true|false} or list of satisfied check ids")
    sp.set_defaults(func=cmd_readiness)
    return p

def run(argv):
    parser = build_parser(prog="rwt collections")
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
