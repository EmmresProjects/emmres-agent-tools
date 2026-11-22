"""tools-rwt-workflow.py - projects, review queues, approvals, change control and deliverables.

Publication, archiving and embargo actions are approval gated. Every decision is written to the
audit chain together with the reviewer identity, so an approved artifact can be traced back to a
named human decision.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile

NAME = "workflow"
ROLE = "project workspace, review queues, approvals, decisions, change control, packaging and preflight"
GROUPS = [("workflow", "Projects, reviews, approvals, change control, packaging and publication gates")]
deps: dict = {}


def _store():
    return deps["store"]


def _now():
    return _store().now_iso()


# ---------------------------------------------------------------- reviews and approvals

def add_review(ctx, kind: str, ref: str, priority: str = "normal", note: str = "",
               assignee: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    rid = f"rev-{store.slug(kind, 20)}-{store.slug(ref, 24)}"
    conn.execute("""INSERT OR REPLACE INTO reviews(id,kind,ref,assignee,status,priority,decision,note,
        created,decided) VALUES(?,?,?,?,'open',?,NULL,?,?,NULL)""",
                 (rid, kind, ref, assignee, priority, note, _now()))
    ctx.commit()
    store.audit_append(conn, ctx.actor, "workflow.review.queued", rid, {"kind": kind, "ref": ref})
    ctx.commit()
    return {"review_id": rid, "kind": kind, "ref": ref, "priority": priority}


def op_queue(ctx, action: str = "list", kind: str | None = None, ref: str | None = None,
             priority: str = "normal", note: str = "", assignee: str | None = None,
             review_id: str | None = None, decision: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    if action == "add":
        if not kind or not ref:
            raise store.ToolError(2, "--kind and --ref are required for 'add'")
        result = add_review(ctx, kind, ref, priority, note, assignee)
        return {"status": "ok", **result}
    if action == "list":
        sql, args = "SELECT * FROM reviews", []
        clauses = ["kind NOT LIKE 'screening:%'", "kind != 'risk-of-bias'"]
        if kind:
            clauses.append("kind=?"); args.append(kind)
        sql += " WHERE " + " AND ".join(clauses) + " ORDER BY created DESC LIMIT 200"
        rows = [dict(r) for r in conn.execute(sql, args)]
        return {"status": "ok", "count": len(rows),
                "open": sum(1 for r in rows if r["status"] == "open"), "items": rows}
    if action == "decide":
        if not review_id or not decision:
            raise store.ToolError(2, "--review-id and --decision are required for 'decide'")
        row = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise store.ToolError(2, f"unknown review: {review_id}")
        conn.execute("UPDATE reviews SET status='closed',decision=?,note=?,assignee=COALESCE(?,assignee),"
                     "decided=? WHERE id=?",
                     (decision, note or row["note"], assignee, _now(), review_id))
        store.audit_append(conn, assignee or ctx.actor, "workflow.review.decided", review_id,
                           {"decision": decision, "note": note})
        ctx.commit()
        return {"status": "ok", "review_id": review_id, "decision": decision, "by": assignee or ctx.actor}
    if action == "assign":
        if not review_id or not assignee:
            raise store.ToolError(2, "--review-id and --assignee are required for 'assign'")
        conn.execute("UPDATE reviews SET assignee=? WHERE id=?", (assignee, review_id))
        ctx.commit()
        return {"status": "ok", "review_id": review_id, "assignee": assignee}
    raise store.ToolError(2, "action must be add|list|decide|assign")


def op_approve(ctx, action: str = "record", target: str | None = None, role: str = "approver",
               note: str = "", action_name: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    if action == "record":
        if not target or not action_name:
            raise store.ToolError(2, "--target and --action-name are required")
        record = store.record_approval(conn, action_name, target, ctx.actor, role, note)
        ctx.commit()
        return {"status": "ok", "approval": record}
    if action == "list":
        rows = [dict(r) for r in conn.execute(
            "SELECT id,action,target,actor,role,note,created FROM approvals ORDER BY created DESC LIMIT 100")]
        return {"status": "ok", "count": len(rows), "approvals": rows}
    if action == "check":
        if not target or not action_name:
            raise store.ToolError(2, "--target and --action-name are required")
        row = conn.execute("SELECT * FROM approvals WHERE action=? AND target=? ORDER BY created DESC LIMIT 1",
                           (action_name, target)).fetchone()
        return {"status": "ok", "found": bool(row), "approval": dict(row) if row else None}
    raise store.ToolError(2, "action must be record|list|check")


def op_decision(ctx, summary: str, rationale: str, kind: str = "scope", ref: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    did = f"dec-{store.sha256_text(summary + ref if ref else summary)[:12]}"
    conn.execute("INSERT OR REPLACE INTO decisions(id,kind,summary,rationale,actor,ref,created) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (did, kind, summary, rationale, ctx.actor, ref, _now()))
    store.audit_append(conn, ctx.actor, "workflow.decision", did, {"kind": kind, "ref": ref})
    ctx.commit()
    return {"status": "ok", "decision_id": did, "kind": kind, "summary": summary}


def op_decisions(ctx, kind: str | None = None, limit: int = 100) -> dict:
    conn = ctx.conn()
    sql, args = "SELECT * FROM decisions", []
    if kind:
        sql += " WHERE kind=?"; args.append(kind)
    sql += " ORDER BY created DESC LIMIT ?"; args.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, args)]
    return {"status": "ok", "count": len(rows), "decisions": rows}


# ---------------------------------------------------------------- projects and change control

def op_project(ctx, action: str = "status", name: str = "default", question: str | None = None,
               kind: str = "research") -> dict:
    store = _store()
    conn = ctx.conn()
    key = f"project:{store.slug(name, 40)}"
    if action == "create":
        if not question:
            raise store.ToolError(2, "--question is required to create a project")
        payload = {"name": name, "question": question, "kind": kind, "created": _now(),
                   "created_by": ctx.actor, "baseline": None}
        conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)",
                     (key, json.dumps(payload, ensure_ascii=False), _now()))
        ctx.commit()
        store.audit_append(conn, ctx.actor, "workflow.project.create", name, {"kind": kind})
        ctx.commit()
        return {"status": "ok", "project": payload}
    if action == "list":
        rows = [json.loads(r["value"]) for r in conn.execute(
            "SELECT value FROM state WHERE key LIKE 'project:%'")]
        return {"status": "ok", "count": len(rows), "projects": rows}
    if action == "status":
        row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if not row:
            raise store.ToolError(2, f"unknown project: {name} (create it first)")
        project = json.loads(row["value"])
        counts = {
            "sources": conn.execute("SELECT count(*) c FROM sources").fetchone()["c"],
            "segments": conn.execute("SELECT count(*) c FROM segments").fetchone()["c"],
            "claims": conn.execute("SELECT count(*) c FROM claims").fetchone()["c"],
            "claims_approved": conn.execute("SELECT count(*) c FROM claims WHERE status='approved'").fetchone()["c"],
            "open_reviews": conn.execute("SELECT count(*) c FROM reviews WHERE status='open'").fetchone()["c"],
            "approvals": conn.execute("SELECT count(*) c FROM approvals").fetchone()["c"],
            "decisions": conn.execute("SELECT count(*) c FROM decisions").fetchone()["c"],
            "tm_units": conn.execute("SELECT count(*) c FROM tm_units").fetchone()["c"],
            "terms": conn.execute("SELECT count(*) c FROM terms").fetchone()["c"],
        }
        return {"status": "ok", "project": project, "counts": counts,
                "next_actions": [a for a, ok in (
                    ("ingest sources", counts["sources"] > 0),
                    ("extract claims", counts["claims"] > 0),
                    ("review claims", counts["claims_approved"] > 0),
                    ("close open reviews", counts["open_reviews"] == 0),
                    ("create a baseline before drafting", bool(project.get("baseline"))),
                ) if not ok][:5]}
    raise store.ToolError(2, "action must be create|status|list")


def op_baseline(ctx, name: str = "default", label: str = "v1") -> dict:
    store = _store()
    conn = ctx.conn()
    snap = store.snapshot_corpus(ctx, f"baseline {label}")
    manifests = op_run_manifest(ctx)
    baseline = {"label": label, "created": _now(), "by": ctx.actor, "snapshot": snap,
                "run_manifest": manifests["manifest"], "counts": {
                    "sources": conn.execute("SELECT count(*) c FROM sources").fetchone()["c"],
                    "claims": conn.execute("SELECT count(*) c FROM claims").fetchone()["c"]}}
    conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)",
                 (f"baseline:{store.slug(name, 40)}", json.dumps(baseline, default=str), _now()))
    project_key = f"project:{store.slug(name, 40)}"
    row = conn.execute("SELECT value FROM state WHERE key=?", (project_key,)).fetchone()
    project = json.loads(row["value"]) if row else {"name": name}
    project["baseline"] = {"label": label, "snapshot_id": snap["snapshot_id"],
                           "created": baseline["created"]}
    conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)",
                 (project_key, json.dumps(project, ensure_ascii=False), _now()))
    ctx.commit()
    return {"status": "ok", "baseline": label, "snapshot_id": snap["snapshot_id"],
            "checksum": snap["checksum"], "counts": baseline["counts"],
            "change_control": "any corpus/model/method change after this baseline needs 'workflow change'"}


def op_change(ctx, description: str, impact: str = "unspecified", approved: bool = False,
              approver: str | None = None, name: str = "default") -> dict:
    store = _store()
    change_id = f"chg-{store.sha256_text(description)[:12]}"
    conn = ctx.conn()
    record = {"id": change_id, "description": description, "impact": impact,
              "requested_by": ctx.actor, "requested": _now(), "approved": None}
    if approved:
        store.require_approval(ctx, "workflow.publish", change_id, approved, approver, impact)
        record["approved"] = {"by": approver, "at": _now()}
    conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)",
                 (f"change:{change_id}", json.dumps(record, default=str), _now()))
    ctx.commit()
    store.audit_append(conn, ctx.actor, "workflow.change", change_id,
                       {"description": description, "impact": impact, "approved": approved})
    ctx.commit()
    baseline_row = conn.execute("SELECT value FROM state WHERE key=?",
                                (f"baseline:{store.slug(name, 40)}",)).fetchone()
    baseline = json.loads(baseline_row["value"]) if baseline_row else None
    return {"status": "ok", "change_id": change_id, "approved": approved,
            "baseline_exists": bool(baseline),
            "requires_review": bool(baseline) and not approved,
            "next_step": ("re-run readiness checks against the new baseline" if approved else
                          "a reviewer must approve this change before it is applied" if baseline
                          else "no baseline yet; create one with 'workflow baseline'")}


# ---------------------------------------------------------------- readiness and preflight

def op_ready(ctx, draft_path: str | None = None, bilingual_path: str | None = None) -> dict:
    """Publication readiness gate: evidence, citations, language, provenance, approvals."""
    store = _store()
    checks, blockers = [], []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            blockers.append(name)

    provenance = deps["verify"].op_provenance(ctx)
    add("provenance", provenance["incomplete"] == 0,
        f"{provenance['complete']}/{provenance['sources']} sources complete")
    unapproved = ctx.conn().execute("SELECT count(*) c FROM claims WHERE status='extracted'").fetchone()["c"]
    total_claims = ctx.conn().execute("SELECT count(*) c FROM claims").fetchone()["c"]
    add("claim_review", total_claims == 0 or unapproved == 0,
        f"{unapproved} of {total_claims} claims are unreviewed")
    if draft_path:
        if not os.path.exists(draft_path):
            raise store.ToolError(2, f"draft not found: {draft_path}")
        attribution = deps["guard"].op_attribution(ctx, draft_path)
        add("attribution", attribution["passed"], f"{attribution['missing_count']} attribution gaps")
        proof = deps["writing"].op_proofread(ctx, input_path=draft_path)
        add("proofread", proof["ready_for_render"], f"{proof['critical']} critical, {proof['major']} major")
        boundary = deps["writing"].op_boundary(ctx, draft_path)
        add("conclusion_boundary", boundary["passed"], boundary["required_caveat"])
    if bilingual_path:
        qa = deps["translation"].op_qa(ctx, bilingual_path)
        add("translation_qa", not qa["failed_gate"], f"worst severity: {qa['worst_severity']}")
    publish = ctx.conn().execute(
        "SELECT count(*) c FROM approvals WHERE action='workflow.publish'").fetchone()["c"]
    add("publication_approval", publish > 0, f"{publish} publication approval(s) recorded")
    return {"status": "ok", "checks": checks, "blockers": blockers,
            "ready": not blockers,
            "next_step": ("request publication approval: workflow approve --action record "
                          "--action-name workflow.publish --target <artifact>" if blockers else
                          "artifact may be released with its manifest")}


def op_links(ctx, input_path: str) -> dict:
    store = _store()
    body = store.read_text(input_path)
    base = os.path.dirname(os.path.abspath(input_path))
    broken, checked = [], 0
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", body):
        target = m.group(2).split("#")[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        checked += 1
        resolved = target if os.path.isabs(target) else os.path.join(base, target)
        if not os.path.exists(resolved):
            broken.append({"link_text": m.group(1), "target": target})
    anchors = set(re.findall(r"^#{1,6}\s+(.+)$", body, re.M))
    anchor_slugs = {store.slug(a, 80) for a in anchors}
    broken_anchors = []
    for m in re.finditer(r"\[([^\]]+)\]\(#([^)]+)\)", body):
        if store.slug(m.group(2), 80) not in anchor_slugs and m.group(2) not in anchor_slugs:
            broken_anchors.append({"link_text": m.group(1), "anchor": m.group(2)})
    figures = set(re.findall(r"\b(?:Figure|Fig\.)\s+(\d+)", body))
    tables = set(re.findall(r"\bTable\s+(\d+)", body))
    captions_f = set(re.findall(r"^\s*(?:Figure|Fig\.)\s+(\d+)[:.]", body, re.M))
    captions_t = set(re.findall(r"^\s*Table\s+(\d+)[:.]", body, re.M))
    missing_captions = sorted((figures - captions_f), key=int) + sorted((tables - captions_t), key=int)
    return {"status": "ok", "relative_links_checked": checked, "broken_links": broken,
            "broken_anchors": broken_anchors,
            "figures_referenced": sorted(figures, key=int), "tables_referenced": sorted(tables, key=int),
            "references_without_caption": missing_captions,
            "passed": not broken and not broken_anchors and not missing_captions}


def op_accessible(ctx, input_path: str) -> dict:
    store = _store()
    body = store.read_text(input_path)
    required = ctx.cfg.get("delivery", {}).get("required_accessibility_checks",
                                               ["headings", "alt_text", "lang_attribute", "table_headers"])
    findings = []
    headings = [(len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#{1,6})\s+(.+)$", body, re.M)]
    if "headings" in required:
        if not headings:
            findings.append({"check": "headings", "issue": "no headings found", "severity": "major"})
        else:
            levels = [h[0] for h in headings]
            for prev, cur in zip(levels, levels[1:]):
                if cur > prev + 1:
                    findings.append({"check": "headings", "issue": f"heading level jumps {prev}->{cur}",
                                     "severity": "major"})
                    break
            if levels.count(1) > 1:
                findings.append({"check": "headings", "issue": "multiple top-level headings",
                                 "severity": "minor"})
    if "alt_text" in required:
        images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)|<img[^>]*>", body)
        for alt, src in [(m.group(1), m.group(2)) for m in
                         re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", body)]:
            if not alt.strip():
                findings.append({"check": "alt_text", "issue": f"image without alt text: {src}",
                                 "severity": "critical"})
    if "table_headers" in required:
        for block in re.findall(r"(?:^\|.*$\n?)+", body, re.M):
            lines = [ln for ln in block.strip().split("\n") if ln.strip()]
            if len(lines) > 1 and not re.match(r"^\s*\|?[\s:|-]+\|", lines[1]):
                findings.append({"check": "table_headers", "issue": "table without header separator row",
                                 "severity": "major"})
    if "link_text" in required:
        for m in re.finditer(r"\[(click here|here|link|this)\]\(([^)]+)\)", body, re.I):
            findings.append({"check": "link_text", "issue": f"non-descriptive link text '{m.group(1)}'",
                             "severity": "minor"})
    if "lang_attribute" in required and input_path.lower().endswith((".html", ".htm")):
        if not re.search(r"<html[^>]*\blang=", body, re.I):
            findings.append({"check": "lang_attribute", "issue": "html root lacks lang attribute",
                             "severity": "major"})
    critical = [f for f in findings if f["severity"] == "critical"]
    return {"status": "ok", "findings": findings, "required_checks": required,
            "passed": not critical, "note": "contrast and rendering need visual inspection of the output"}


def op_preflight(ctx, input_path: str) -> dict:
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    results = {}
    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".md", ".markdown", ".txt", ".html", ".htm"):
        results["links"] = op_links(ctx, input_path)
        results["accessibility"] = op_accessible(ctx, input_path)
        results["proofread"] = deps["writing"].op_proofread(ctx, input_path=input_path)
    results["file"] = {"path": os.path.basename(input_path), "bytes": os.path.getsize(input_path),
                       "checksum": store.sha256_file(input_path)}
    blockers = []
    for name in ("links", "accessibility", "proofread"):
        section = results.get(name)
        if section and section.get("passed") is False:
            blockers.append(name)
    results["passed"] = not blockers
    results["blockers"] = blockers
    results["note"] = "reopen the rendered artifact on a clean local tool before release"
    return {"status": "ok", "preflight": results}


# ---------------------------------------------------------------- packaging and delivery

def _zip_paths(ctx, output: str, entries: list, manifest_extra: dict | None = None) -> dict:
    store = _store()
    out = ctx.out_path(output)
    manifest = {"created": _now(), "files": [], **(manifest_extra or {})}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for entry in entries:
            if not os.path.exists(entry):
                continue
            if os.path.isdir(entry):
                for dirpath, dirnames, filenames in os.walk(entry):
                    dirnames[:] = [d for d in dirnames if d not in store.SKIP_DIRS]
                    for fn in sorted(filenames):
                        full = os.path.join(dirpath, fn)
                        rel = os.path.relpath(full, ctx.root).replace(os.sep, "/")
                        zf.write(full, rel)
                        manifest["files"].append({"path": rel, "checksum": store.sha256_file(full),
                                                  "bytes": os.path.getsize(full)})
            else:
                rel = os.path.relpath(entry, ctx.root).replace(os.sep, "/") if entry.startswith(ctx.root) \
                    else os.path.basename(entry)
                zf.write(entry, rel)
                manifest["files"].append({"path": rel, "checksum": store.sha256_file(entry),
                                          "bytes": os.path.getsize(entry)})
        zf.writestr("package-manifest.json", json.dumps(manifest, indent=2, default=str))
    if not manifest["files"]:
        os.remove(out)
        raise store.ToolError(2, "nothing to package: none of the requested paths exist")
    return {"output": out, "files": len(manifest["files"]), "checksum": store.sha256_file(out),
            "bytes": os.path.getsize(out), "manifest": manifest}


def op_handoff(ctx, output: str = "handoff.zip", include_outputs: bool = True) -> dict:
    store = _store()
    entries = [ctx.path("corpus")]
    if include_outputs:
        entries.append(ctx.path("outputs"))
    entries.append(ctx.db_path)
    decisions = [dict(r) for r in ctx.conn().execute(
        "SELECT kind,summary,rationale,actor,created FROM decisions ORDER BY created")]
    state_dump = os.path.join(ctx.path("state"), "handoff-state.json")
    store.write_json(state_dump, {"decisions": decisions,
                                  "open_reviews": [dict(r) for r in ctx.conn().execute(
                                      "SELECT id,kind,ref,status,priority FROM reviews WHERE status='open'")],
                                  "counts": {"sources": ctx.conn().execute(
                                      "SELECT count(*) c FROM sources").fetchone()["c"]}})
    entries.append(state_dump)
    result = _zip_paths(ctx, output, entries, {"kind": "research-handoff", "by": ctx.actor})
    return {"status": "ok", **result,
            "contents": "editable sources, outputs, SQLite state, decisions and open review queue"}


def op_package(ctx, output: str = "report.zip", draft_path: str | None = None,
               title: str | None = None) -> dict:
    store = _store()
    staging = ctx.path("outputs", "_package")
    os.makedirs(staging, exist_ok=True)
    entries = []
    if draft_path:
        if not os.path.exists(draft_path):
            raise store.ToolError(2, f"draft not found: {draft_path}")
        shutil.copy2(draft_path, os.path.join(staging, os.path.basename(draft_path)))
        entries.append(os.path.join(staging, os.path.basename(draft_path)))
        html = _markdown_to_html(store.read_text(draft_path), title or os.path.basename(draft_path))
        html_path = os.path.join(staging, "report.html")
        store.write_text(html_path, html)
        entries.append(html_path)
    evidence = deps["synthesis"].op_evidence_table(ctx, output="evidence-table.md")
    entries.append(evidence["output"])
    bibliography = _bibliography(ctx)
    if bibliography:
        bib = os.path.join(staging, "bibliography.md")
        store.write_text(bib, bibliography)
        entries.append(bib)
    result = _zip_paths(ctx, output, entries, {"kind": "research-report", "title": title})
    return {"status": "ok", **result,
            "note": "markdown, HTML and CSV deliverables; PDF/DOCX rendering needs an external converter"}


def _bibliography(ctx) -> str:
    conn = ctx.conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,title,doctype,issued,license,jurisdiction,publisher FROM sources WHERE status!='deleted' "
        "ORDER BY title")]
    if not rows:
        return ""
    lines = ["# Source bibliography (local corpus)", ""]
    for row in rows:
        lines.append(f"- [{row['id']}] {row['title']}. {row['doctype']}, {row['issued'] or 'undated'}. "
                     f"Publisher: {row['publisher'] or 'unspecified'}. Licence: {row['license'] or 'unspecified'}. "
                     f"Jurisdiction: {row['jurisdiction'] or 'unspecified'}.")
    return "\n".join(lines) + "\n"


def _markdown_to_html(md: str, title: str) -> str:
    import html as _html
    lines, out = md.split("\n"), []
    in_list = False
    for line in lines:
        if re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_html.escape(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_html.escape(heading.group(2))}</h{level}>")
        elif line.strip():
            out.append(f"<p>{_html.escape(line)}</p>")
    if in_list:
        out.append("</ul>")
    return (f"<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{_html.escape(title)}</title>\n</head>\n<body>\n" + "\n".join(out) +
            "\n</body>\n</html>\n")


def op_archive(ctx, output: str = "archive.zip", approved: bool = False, approver: str | None = None,
               note: str = "") -> dict:
    store = _store()
    store.require_approval(ctx, "workflow.archive-sign", output, approved, approver, note)
    entries = [ctx.path("corpus"), ctx.path("outputs"), ctx.db_path]
    result = _zip_paths(ctx, output, entries, {
        "kind": "research-archive", "premis_lite": {
            "preservation_level": "full",
            "formats": ["sqlite3", "markdown", "json", "csv", "text"],
            "at_risk_formats": ["docx", "xlsx", "pptx"],
            "migration_plan": "re-verify checksums and re-render at-risk formats every migration cycle",
        }, "signed_by": approver})
    return {"status": "ok", **result,
            "manifest_in_archive": "package-manifest.json",
            "note": "archive manifest lists checksums for every file; keep the archive bit-identical"}


def op_versions(ctx, action: str = "list", artifact: str = "report", version: str | None = None,
                notes: str = "", supersedes: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    key = f"versions:{store.slug(artifact, 40)}"
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    history = json.loads(row["value"]) if row else []
    if action == "list":
        return {"status": "ok", "artifact": artifact, "count": len(history), "versions": history}
    if action == "add":
        if not version:
            raise store.ToolError(2, "--version is required")
        entry = {"version": version, "notes": notes, "created": _now(), "by": ctx.actor,
                 "supersedes": supersedes,
                 "stable_id": f"{store.slug(artifact, 30)}@{version}"}
        history.append(entry)
        for previous in history[:-1]:
            if previous["version"] == supersedes:
                previous["status"] = "superseded"
        entry["status"] = "current"
        conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)",
                     (key, json.dumps(history, ensure_ascii=False), _now()))
        ctx.commit()
        store.audit_append(conn, ctx.actor, "workflow.version", f"{artifact}@{version}", entry)
        ctx.commit()
        return {"status": "ok", "artifact": artifact, "version": entry}
    raise store.ToolError(2, "action must be list|add")


def op_multilingual(ctx, editions_dir: str) -> dict:
    store = _store()
    if not os.path.isdir(editions_dir):
        raise store.ToolError(2, f"editions directory not found: {editions_dir}")
    manifest_path = os.path.join(editions_dir, "editions.json")
    if not os.path.exists(manifest_path):
        raise store.ToolError(2, "editions.json is required inside the editions directory")
    manifest = json.loads(store.read_text(manifest_path))
    verified = deps["translation"].op_publish_check(ctx, manifest_path)
    files = {}
    for entry in manifest.get("editions", []):
        path = os.path.join(editions_dir, entry.get("file") or "")
        files[entry.get("lang")] = {"path": entry.get("file"),
                                    "exists": bool(entry.get("file")) and os.path.exists(path),
                                    "words": store.words(store.read_text(path)) if os.path.exists(path) else 0}
    missing = [lang for lang, info in files.items() if not info["exists"]]
    return {"status": "ok", "verified": verified, "editions": files, "missing_files": missing,
            "consistent": verified["consistent"] and not missing}


def op_feedback(ctx, summary: str, source_id: str | None = None, version: str | None = None,
                decision: str = "open") -> dict:
    store = _store()
    conn = ctx.conn()
    fid = f"fb-{store.sha256_text(summary + (source_id or ''))[:12]}"
    conn.execute("INSERT OR REPLACE INTO decisions(id,kind,summary,rationale,actor,ref,created) "
                 "VALUES(?, 'reader-feedback', ?, ?, ?, ?, ?)",
                 (fid, summary, decision, ctx.actor, source_id or version, _now()))
    ctx.commit()
    return {"status": "ok", "feedback_id": fid, "decision": decision,
            "propagation": ["update the affected claim or citation",
                            "record the correction in the change log",
                            "re-run readiness checks before republishing"] if decision != "accepted" else
                           ["apply the correction", "issue a new artifact version"]}


def op_run_manifest(ctx, output: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    adapters = deps["adapters"].adapter_status(ctx)
    manifest = {
        "created": _now(), "tool_version": "1.0.0", "actor": ctx.actor,
        "workspace": os.path.basename(ctx.root),
        "config_hash": store.sha256_text(json.dumps(ctx.cfg, sort_keys=True)),
        "corpus_checksum": store.sha256_text(json.dumps(
            [list(r) for r in conn.execute("SELECT id,checksum FROM sources ORDER BY id")], sort_keys=True)),
        "database": {"path": os.path.basename(ctx.db_path),
                     "bytes": os.path.getsize(ctx.db_path) if os.path.exists(ctx.db_path) else 0},
        "counts": {table: conn.execute(f"SELECT count(*) c FROM {table}").fetchone()["c"]
                   for table in ("sources", "segments", "claims", "tm_units", "terms", "approvals")},
        "adapters": {k: {"configured": v["configured"], "available": v["available"]}
                     for k, v in adapters.items()},
        "python": os.sys.version.split()[0],
        "locale_note": "citations, locales and language pairs used are recorded per operation in the audit log",
    }
    run_id = f"run-{store.sha256_text(json.dumps(manifest, sort_keys=True))[:16]}"
    manifest["run_id"] = run_id
    conn.execute("INSERT OR REPLACE INTO runs(id,task,config_hash,inputs_hash,outputs,corpus_snapshot,created) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (run_id, "run-manifest", manifest["config_hash"], manifest["corpus_checksum"],
                  json.dumps({"outputs": output}), None, _now()))
    ctx.commit()
    payload = {"status": "ok", "manifest": manifest, "run_id": run_id}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), manifest)
    return payload


def op_embargo(ctx, action: str = "show", target: str = "artifact", until: str | None = None,
               approved: bool = False, approver: str | None = None, note: str = "") -> dict:
    store = _store()
    conn = ctx.conn()
    key = f"embargo:{store.slug(target, 40)}"
    if action == "set":
        if not until:
            raise store.ToolError(2, "--until (YYYY-MM-DD) is required")
        conn.execute("INSERT OR REPLACE INTO state(key,value,updated) VALUES(?,?,?)",
                     (key, json.dumps({"target": target, "until": until, "set_by": ctx.actor,
                                       "set_at": _now()}), _now()))
        ctx.commit()
        return {"status": "ok", "target": target, "until": until,
                "restrictions": ["no display", "no export", "no quotation outside the team"]}
    if action == "show":
        row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return {"status": "ok", "embargo": json.loads(row["value"]) if row else None}
    if action == "lift":
        store.require_approval(ctx, "workflow.embargo-lift", target, approved, approver, note)
        conn.execute("DELETE FROM state WHERE key=?", (key,))
        ctx.commit()
        store.audit_append(conn, ctx.actor, "workflow.embargo-lift", target, {"note": note})
        ctx.commit()
        return {"status": "ok", "target": target, "lifted": True}
    raise store.ToolError(2, "action must be set|show|lift")


def register(groups, common):
    g = groups["workflow"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("project", "Create, inspect or list project workspaces", op_project,
        action=(("--action",), dict(default="status", choices=["create", "status", "list"])),
        name=(("--name",), dict(default="default")), question=(("--question",), dict(default=None)),
        kind=(("--kind",), dict(default="research")))
    sub("queue", "Manage the human review queue", op_queue,
        action=(("--action",), dict(default="list", choices=["add", "list", "decide", "assign"])),
        kind=(("--kind",), dict(default=None)), ref=(("--ref",), dict(default=None)),
        priority=(("--priority",), dict(default="normal")), note=(("--note",), dict(default="")),
        assignee=(("--assignee",), dict(default=None)), review_id=(("--review-id",), dict(default=None)),
        decision=(("--decision",), dict(default=None)))
    sub("approve", "Record, list or check approvals", op_approve,
        action=(("--action",), dict(default="record", choices=["record", "list", "check"])),
        target=(("--target",), dict(default=None)), role=(("--role",), dict(default="approver")),
        note=(("--note",), dict(default="")), action_name=(("--action-name",), dict(default=None)))
    sub("decision", "Log a decision with rationale", op_decision,
        summary=(("--summary",), dict(required=True)), rationale=(("--rationale",), dict(required=True)),
        kind=(("--kind",), dict(default="scope")), ref=(("--ref",), dict(default=None)))
    sub("decisions", "List logged decisions", op_decisions,
        kind=(("--kind",), dict(default=None)), limit=(("--limit",), dict(type=int, default=100)))
    sub("baseline", "Freeze an approved baseline for change control", op_baseline,
        name=(("--name",), dict(default="default")), label=(("--label",), dict(default="v1")))
    sub("change", "Request (and optionally approve) a post-baseline change", op_change,
        description=(("--description",), dict(required=True)),
        impact=(("--impact",), dict(default="unspecified")),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        name=(("--name",), dict(default="default")))
    sub("ready", "Run the publication readiness gate", op_ready,
        draft_path=(("--draft",), dict(default=None)),
        bilingual_path=(("--bilingual",), dict(default=None)))
    sub("links", "Check link, anchor and figure integrity", op_links,
        input_path=(("--input", "-i"), dict(required=True)))
    sub("accessible", "Check accessibility requirements", op_accessible,
        input_path=(("--input", "-i"), dict(required=True)))
    sub("preflight", "Full preflight check for a deliverable", op_preflight,
        input_path=(("--input", "-i"), dict(required=True)))
    sub("handoff", "Package sources, outputs, state and decisions for handoff", op_handoff,
        output=(("--output", "-o"), dict(default="handoff.zip")),
        include_outputs=(("--no-outputs",), dict(action="store_false", default=True)))
    sub("package", "Build a report package (markdown, HTML, evidence table, bibliography)", op_package,
        output=(("--output", "-o"), dict(default="report.zip")),
        draft_path=(("--draft",), dict(default=None)), title=(("--title",), dict(default=None)))
    sub("archive", "Build a preservable archive with checksums (approval gated)", op_archive,
        output=(("--output", "-o"), dict(default="archive.zip")),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("versions", "Manage versioned publications", op_versions,
        action=(("--action",), dict(default="list", choices=["list", "add"])),
        artifact=(("--artifact",), dict(default="report")), version=(("--version",), dict(default=None)),
        notes=(("--notes",), dict(default="")), supersedes=(("--supersedes",), dict(default=None)))
    sub("multilingual", "Check multilingual editions consistency", op_multilingual,
        editions_dir=(("--editions-dir",), dict(required=True)))
    sub("feedback", "Intake a reader correction report", op_feedback,
        summary=(("--summary",), dict(required=True)), source_id=(("--source-id",), dict(default=None)),
        version=(("--version",), dict(default=None)),
        decision=(("--decision",), dict(default="open", choices=["open", "accepted", "rejected"])))
    sub("run-manifest", "Record a reproducible run manifest", op_run_manifest,
        output=(("--output", "-o"), dict(default=None)))
    sub("embargo", "Set, show or lift an embargo (lift is gated)", op_embargo,
        action=(("--action",), dict(default="show", choices=["set", "show", "lift"])),
        target=(("--target",), dict(default="artifact")), until=(("--until",), dict(default=None)),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
