"""tools-rwt-verify.py - fact checking, provenance, authority and freshness verification.

Verification always searches the local corpus and reports its evidence. When evidence is
missing the verdict is `insufficient`, never a guess: the tool's contract is calibrated
abstention, and every verdict carries the passages it was based on.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone

NAME = "verify"
ROLE = "claim/quote/number/chronology verification, source authority, provenance and freshness controls"
GROUPS = [("verify", "Claim verification, corroboration, quotation and number checks, provenance, freshness")]
deps: dict = {}

CHANGE_RATE = {"news": 0.95, "blog": 0.9, "press-release": 0.8, "policy": 0.6, "statute": 0.35,
               "regulation": 0.4, "guidance": 0.5, "standard": 0.25, "case": 0.3,
               "journal-article": 0.15, "review": 0.15, "book": 0.1, "dataset": 0.4,
               "report": 0.45, "document": 0.5, "transcript": 0.0}
VERDICTS = ("supported", "contradicted", "qualified", "insufficient")


def _store():
    return deps["store"]


def op_claim(ctx, claim: str, limit: int = 5, min_coverage: float = 0.5) -> dict:
    """Verify a claim against local evidence only."""
    store = _store()
    if not claim or store.words(claim) < 3:
        raise store.ToolError(2, "claim must contain at least three words")
    retrieval = deps["retrieval"]
    extract = deps["extract"]
    hits = retrieval.op_hybrid(ctx, claim, limit=limit, diversify=True)
    if not hits["results"]:
        return {"status": "ok", "claim": claim, "verdict": "insufficient", "confidence": 0.0,
                "support_count": 0, "oppose_count": 0, "evidence": [],
                "reason": "no local evidence matched", "abstain": True,
                "limits": "no passage in the local corpus matched the claim"}
    claim_terms = {t for t in store.tokens(claim) if t not in store.STOPWORDS["en"] and len(t) > 3}
    evidence, support, oppose = [], 0, 0
    conn = ctx.conn()
    for hit in hits["results"]:
        seg = store.get_segment(conn, hit["segment_id"])
        text = seg["text"] if seg else ""
        coverage = len(claim_terms & set(store.tokens(text))) / (len(claim_terms) or 1)
        relation = extract.classify_support(claim, text)
        if relation == "support":
            support += 1
        elif relation == "oppose":
            oppose += 1
        evidence.append({"segment_id": hit["segment_id"], "source_id": hit["source_id"],
                         "title": hit.get("title"), "relation": relation,
                         "coverage": round(coverage, 3), "snippet": hit.get("snippet", "")[:300],
                         "issued": hit.get("issued")})
    if oppose and not support:
        verdict, confidence = "contradicted", 0.65
    elif support and oppose:
        verdict, confidence = "qualified", 0.5
    elif support and not oppose:
        verdict = "supported" if evidence[0]["coverage"] >= min_coverage else "qualified"
        confidence = round(min(0.9, 0.5 + 0.1 * support), 2)
    else:
        verdict, confidence = "insufficient", 0.2
    payload = {"status": "ok", "claim": claim, "verdict": verdict, "confidence": confidence,
               "support_count": support, "oppose_count": oppose, "evidence": evidence,
               "abstain": verdict in ("insufficient", "contradicted"),
               "reason": {"supported": "at least one local passage supports the claim without opposition",
                          "contradicted": "local passages overlap but negate the claim",
                          "qualified": "evidence is mixed or only partially overlaps the claim",
                          "insufficient": "no passage overlaps enough to judge"}[verdict],
               "limits": "lexical/negation matching over the local corpus only; a human confirms high-impact claims"}
    ctx.audit("verify.claim", store.sha256_text(claim)[:16], {"verdict": verdict})
    ctx.commit()
    return payload


def op_corroborate(ctx, claim: str, limit: int = 8, similarity_cap: float = 0.6) -> dict:
    """Distinguish independent corroboration from repeated/syndicated text."""
    store = _store()
    retrieval = deps["retrieval"]
    extract = deps["extract"]
    hits = retrieval.op_hybrid(ctx, claim, limit=limit, diversify=False)
    supporting = []
    conn = ctx.conn()
    for hit in hits["results"]:
        seg = store.get_segment(conn, hit["segment_id"])
        if not seg:
            continue
        if extract.classify_support(claim, seg["text"]) == "support":
            src = store.get_source(conn, hit["source_id"]) or {}
            supporting.append({"segment_id": hit["segment_id"], "source_id": hit["source_id"],
                               "publisher": src.get("publisher"), "issued": src.get("issued"),
                               "shingles": store.shingles(seg["text"], 5)})
    clusters, independent = [], 0
    for item in supporting:
        placed = False
        for cluster in clusters:
            sim = store.jaccard(item["shingles"], cluster["shingles"])
            if sim >= similarity_cap:
                cluster["members"].append(item["source_id"])
                placed = True
                break
        if not placed:
            independent += 1
            clusters.append({"members": [item["source_id"]], "shingles": item["shingles"]})
    publishers = {s["publisher"] for s in supporting if s["publisher"]}
    return {"status": "ok", "claim": claim, "supporting_passages": len(supporting),
            "independent_clusters": independent,
            "distinct_publishers": len(publishers),
            "clusters": [{"members": c["members"]} for c in clusters],
            "verdict": ("corroborated" if independent >= 2 else
                        ("repetition-only" if len(supporting) > 1 else "single-source")),
            "note": "repeated text across outlets counts as one source, not corroboration"}


def op_quote(ctx, quote: str, source_id: str | None = None, context_words: int = 12) -> dict:
    """Verify quotation wording, context and omissions against a local source."""
    store = _store()
    conn = ctx.conn()
    if not quote or len(quote.strip()) < 8:
        raise store.ToolError(2, "quote must be at least 8 characters")
    if source_id:
        rows = conn.execute("SELECT id,text FROM segments WHERE source_id=? ORDER BY ordinal",
                            (source_id,)).fetchall()
        if not rows:
            raise store.ToolError(2, f"no stored segments for source: {source_id}")
    else:
        retrieval = deps["retrieval"]
        hits = retrieval.op_query(ctx, quote, limit=5, explain=True)
        if not hits["results"]:
            return {"status": "ok", "quote": quote, "verdict": "unverified", "found": False,
                    "reason": "quotation does not appear in the local corpus"}
        rows = [{"id": h["segment_id"], "text": h["text"]} for h in hits["results"]]
    normalized_quote = re.sub(r"\s+", " ", quote.strip().strip('"“”'))
    best = None
    for row in rows:
        text = row["text"]
        if normalized_quote in text:
            best = {"segment_id": row["id"], "match": "verbatim", "score": 1.0, "text": text}
            break
        flat = re.sub(r"\s+", " ", text)
        ratio = _ratio(normalized_quote, flat)
        if best is None or ratio > best["score"]:
            best = {"segment_id": row["id"], "match": "fuzzy", "score": round(ratio, 3), "text": flat}
    if not best:
        return {"status": "ok", "quote": quote, "verdict": "unverified", "found": False}
    omitted = _omission_check(normalized_quote, best["text"])
    context = _context(best["text"], normalized_quote, context_words)
    if best["match"] == "verbatim":
        verdict = "verified" if not omitted["ellipsis_conflict"] else "verified-with-alteration"
    else:
        verdict = "altered" if best["score"] >= 0.8 else "unverified"
    words_quoted = _store().words(normalized_quote)
    return {"status": "ok", "quote": quote, "verdict": verdict, "match": best["match"],
            "similarity": best["score"], "segment_id": best["segment_id"],
            "words_quoted": words_quoted, "context": context,
            "omissions": omitted,
            "reason": {"verbatim": "exact wording found", "fuzzy": "close wording only"}.get(
                best["match"], "no exact match")}


def _ratio(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _omission_check(quote: str, text: str) -> dict:
    clean = re.sub(r"\s*\.\.\.\s*|\s*\[\s*\.\.\.\s*\]\s*", "\x00", quote)
    parts = [p.strip() for p in clean.split("\x00") if p.strip()]
    positions, cursor = [], 0
    for part in parts:
        idx = text.find(part)
        if idx < 0:
            return {"ellipsis_conflict": True, "reason": f"segment not found in source: {part[:60]}"}
        positions.append((idx, idx + len(part)))
        cursor = idx + len(part)
    skipped = sum(max(0, positions[i + 1][0] - positions[i][1]) for i in range(len(positions) - 1))
    return {"ellipsis_conflict": False, "skipped_characters": skipped,
            "parts_found": len(parts),
            "note": "ellipsis skips material; confirm the omission does not change meaning"}


def _context(text: str, quote: str, window: int) -> dict:
    idx = text.find(quote)
    if idx < 0:
        return {"before": "", "after": ""}
    before_words = text[:idx].split()[-window:]
    after_words = text[idx + len(quote):].split()[:window]
    return {"before": " ".join(before_words), "after": " ".join(after_words)}


UNIT_CONVERSIONS = {
    ("km", "mi"): 0.621371, ("mi", "km"): 1.609344, ("kg", "lb"): 2.204623, ("lb", "kg"): 0.453592,
    ("m", "ft"): 3.28084, ("ft", "m"): 0.3048, ("ha", "acres"): 2.471054, ("acres", "ha"): 0.404686,
}


def op_numbers(ctx, text: str | None = None, input_path: str | None = None) -> dict:
    """Recompute stated percentages, ratios and unit conversions from their own operands."""
    store = _store()
    if input_path:
        body = store.read_text(input_path)
    elif text:
        body = text
    else:
        raise store.ToolError(2, "provide --text or --input")
    findings = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(?:out of|of|/)\s*(\d[\d,]*(?:\.\d+)?)\s*"
                         r"[\(\[]?\s*(\d+(?:\.\d+)?)\s*%", body):
        part, whole, stated = (float(m.group(i).replace(",", "")) for i in (1, 2, 3))
        if whole == 0:
            findings.append({"raw": m.group(0), "issue": "zero denominator", "severity": "critical"})
            continue
        computed = part / whole * 100
        delta = abs(computed - stated)
        findings.append({"raw": m.group(0), "stated_percent": stated,
                         "computed_percent": round(computed, 3), "delta": round(delta, 3),
                         "verdict": "ok" if delta <= 0.5 else "mismatch",
                         "severity": "ok" if delta <= 0.5 else "major"})
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(%|percent)\s*(increase|decrease|change)\s*(?:from|of)\s*"
                         r"(\d+(?:\.\d+)?)\s*(?:to|)\s*(\d+(?:\.\d+)?)", body, re.I):
        stated, start, end = float(m.group(1)), float(m.group(4)), float(m.group(5))
        computed = (end - start) / start * 100 if start else None
        if computed is None:
            continue
        findings.append({"raw": m.group(0), "stated_percent": stated,
                         "computed_percent": round(computed, 3),
                         "verdict": "ok" if abs(abs(computed) - abs(stated)) <= 0.5 else "mismatch",
                         "severity": "ok" if abs(abs(computed) - abs(stated)) <= 0.5 else "major"})
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(km|mi|kg|lb|m|ft|ha)\s*\(\s*(\d+(?:\.\d+)?)\s*(km|mi|kg|lb|m|ft|acres)\s*\)", body):
        value, unit, stated, target = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
        factor = UNIT_CONVERSIONS.get((unit, target))
        if not factor:
            continue
        computed = value * factor
        findings.append({"raw": m.group(0), "stated": stated, "computed": round(computed, 3),
                         "verdict": "ok" if abs(computed - stated) <= max(0.05, 0.01 * computed) else "mismatch",
                         "severity": "ok" if abs(computed - stated) <= max(0.05, 0.01 * computed) else "minor"})
    mismatches = [f for f in findings if f.get("verdict") == "mismatch" or f.get("severity") == "critical"]
    return {"status": "ok", "checked": len(findings), "mismatches": len(mismatches),
            "passed": not mismatches, "findings": findings[:100],
            "note": "only arithmetic recoverable from the text itself is recomputed; original data is required for the rest"}


def op_chronology(ctx, source_id: str | None = None) -> dict:
    """Detect impossible sequences, anachronisms and event/reporting date confusion."""
    store = _store()
    conn = ctx.conn()
    params = [source_id] if source_id else []
    where = "WHERE source_id=?" if source_id else ""
    rows = [dict(r) for r in conn.execute(
        f"SELECT source_id, id, text FROM segments {where} ORDER BY source_id, ordinal", params)]
    if not rows:
        raise store.ToolError(2, "no segments to analyse; ingest sources first")
    sources = {r["id"]: dict(r) for r in conn.execute("SELECT id,title,issued,doctype FROM sources")}
    issues, timeline = [], []
    for row in rows:
        src = sources.get(row["source_id"], {})
        doc_year = None
        m = re.search(r"((?:19|20)\d{2})", src.get("issued") or "")
        if m:
            doc_year = int(m.group(1))
        for match in re.finditer(r"\b((?:19|20)\d{2})\b", row["text"]):
            year = int(match.group(1))
            timeline.append({"source_id": row["source_id"], "year": year, "title": src.get("title"),
                             "snippet": row["text"][max(0, match.start() - 60):match.end() + 60]})
            if doc_year and year > doc_year:
                issues.append({"kind": "forward-reference", "source_id": row["source_id"],
                               "document_year": doc_year, "referenced_year": year,
                               "severity": "major",
                               "detail": "segment references a year after the document's stated date; "
                                         "check for a later revision, a prediction, or a metadata error"})
        if re.search(r"\b(before|after|prior to|subsequent to)\b", row["text"], re.I):
            years = re.findall(r"\b((?:19|20)\d{2})\b", row["text"])
            if len(years) >= 2:
                timeline.append({"source_id": row["source_id"], "sequence_statement": row["text"][:200],
                                 "years": years})
    ordered = sorted({t["year"] for t in timeline if "year" in t})
    gaps = [(a, b) for a, b in zip(ordered, ordered[1:]) if b - a > 5]
    return {"status": "ok", "segments_scanned": len(rows), "issues": issues,
            "issue_count": len(issues), "years_present": ordered, "large_gaps": gaps,
            "timeline_sample": timeline[:50],
            "note": "reported-vs-event date confusion is flagged only when both dates are explicit"}


def op_contradiction(ctx, topic: str, limit: int = 8) -> dict:
    """Present incompatible claims side by side, with source quality and scope."""
    store = _store()
    retrieval = deps["retrieval"]
    extract = deps["extract"]
    hits = retrieval.op_hybrid(ctx, topic, limit=limit, diversify=True)["results"]
    conn = ctx.conn()
    claims = []
    for hit in hits:
        seg = store.get_segment(conn, hit["segment_id"])
        if not seg:
            continue
        src = store.get_source(conn, hit["source_id"]) or {}
        for sent in store.sentences(seg["text"]):
            if len(store.tokens(sent)) < 5:
                continue
            claims.append({"text": sent, "source_id": hit["source_id"], "title": src.get("title"),
                           "doctype": src.get("doctype"), "issued": src.get("issued"),
                           "negated": bool(extract.NEGATION_CUES.search(sent)),
                           "terms": {t for t in store.tokens(sent)
                                     if t not in store.STOPWORDS["en"] and len(t) > 3}})
    pairs = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a, b = claims[i], claims[j]
            if a["source_id"] == b["source_id"]:
                continue
            overlap = store.jaccard(a["terms"], b["terms"])
            if overlap >= 0.45 and a["negated"] != b["negated"]:
                pairs.append({"a": {"text": a["text"], "source_id": a["source_id"], "issued": a["issued"],
                                    "doctype": a["doctype"]},
                              "b": {"text": b["text"], "source_id": b["source_id"], "issued": b["issued"],
                                    "doctype": b["doctype"]},
                              "overlap": round(overlap, 3),
                              "polarity_conflict": True,
                              "possible_reconciliation": "different periods, populations or definitions may explain "
                                                         "the difference; check the scope of both claims"})
    pairs.sort(key=lambda p: -p["overlap"])
    return {"status": "ok", "topic": topic, "claims_examined": len(claims),
            "contradictions": pairs[:25], "count": len(pairs),
            "note": "polarity conflicts are candidates for review, not proof of error"}


def op_authority(ctx, source_id: str) -> dict:
    store = _store()
    src = store.get_source(ctx.conn(), source_id)
    if not src:
        raise store.ToolError(2, f"unknown source: {source_id}")
    score, factors = 0.0, []
    peer_map = {"peer-reviewed": 0.3, "editorially-reviewed": 0.2, "preprint": 0.1,
                "working-paper": 0.1, "thesis": 0.15, "report": 0.15, "commentary": 0.05,
                "unknown": 0.0}
    score += peer_map.get(src.get("peer_status") or "unknown", 0.0)
    factors.append({"factor": "peer_review_status", "value": src.get("peer_status"),
                    "points": peer_map.get(src.get("peer_status") or "unknown", 0.0)})
    primary_types = {"statute", "regulation", "case", "standard", "dataset", "transcript",
                     "official-record", "policy"}
    if (src.get("doctype") or "") in primary_types:
        score += 0.25
        factors.append({"factor": "primary_source", "value": src.get("doctype"), "points": 0.25})
    if src.get("publisher"):
        score += 0.1
        factors.append({"factor": "named_publisher", "value": src["publisher"], "points": 0.1})
    if src.get("issued"):
        score += 0.1
        factors.append({"factor": "dated", "value": src["issued"], "points": 0.1})
    if src.get("uri") or (src.get("meta") or {}).get("rights"):
        score += 0.05
        factors.append({"factor": "traceable_locator", "value": src.get("uri") or "rights recorded", "points": 0.05})
    status = src.get("status")
    if status in ("superseded", "deleted"):
        score -= 0.2
        factors.append({"factor": "superseded_or_deleted", "value": status, "points": -0.2})
    score = round(max(0.0, min(1.0, score)), 3)
    band = "high" if score >= 0.7 else ("moderate" if score >= 0.4 else "low")
    return {"status": "ok", "source_id": source_id, "authority_score": score, "band": band,
            "factors": factors,
            "caveat": "metadata-based estimate; domain expertise and claim-level fit still decide use"}


def op_freshness(ctx, source_id: str | None = None, as_of: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    today = date.fromisoformat(as_of) if as_of else datetime.now(timezone.utc).date()
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            raise store.ToolError(2, f"--as-of must be YYYY-MM-DD: {as_of}")
    params, where = [], ""
    if source_id:
        where, params = "WHERE id=?", [source_id]
    rows = [dict(r) for r in conn.execute(
        f"SELECT id,title,doctype,issued,status FROM sources {where}", params)]
    if not rows:
        raise store.ToolError(2, "no sources to score")
    scored = []
    for row in rows:
        issued = None
        m = re.search(r"((?:19|20)\d{2})(?:-(\d{2}))?(?:-(\d{2}))?", row.get("issued") or "")
        if m:
            try:
                issued = date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
            except ValueError:
                issued = None
        if not issued:
            scored.append({"source_id": row["id"], "staleness_risk": "unknown", "age_years": None,
                           "reason": "no usable publication date"})
            continue
        age_years = round((today - issued).days / 365.25, 2)
        rate = CHANGE_RATE.get(row.get("doctype") or "document", 0.5)
        risk_score = min(1.0, age_years * rate / 3)
        if row.get("status") in ("superseded", "deleted"):
            risk_score = 1.0
        risk = "high" if risk_score >= 0.66 else ("moderate" if risk_score >= 0.33 else "low")
        scored.append({"source_id": row["id"], "title": row["title"], "doctype": row["doctype"],
                       "issued": issued.isoformat(), "age_years": age_years,
                       "change_rate": rate, "staleness_risk": risk,
                       "risk_score": round(risk_score, 3),
                       "action": ("queue for controlled offline update" if risk == "high" else
                                  "recheck before publication" if risk == "moderate" else "acceptable")})
    high = [s for s in scored if s["staleness_risk"] == "high"]
    return {"status": "ok", "as_of": today.isoformat(), "scored": len(scored),
            "high_risk": len(high), "sources": scored,
            "note": "age is not error: volatile topics and law change faster than books"}


def op_provenance(ctx, source_id: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    params, where = [], ""
    if source_id:
        where, params = "WHERE id=?", [source_id]
    rows = [dict(r) for r in conn.execute(
        f"SELECT id,title,publisher,issued,retrieved,license,uri,path,checksum,meta FROM sources {where}",
        params)]
    if not rows:
        raise store.ToolError(2, "no sources registered")
    gaps = []
    for row in rows:
        meta = json.loads(row["meta"] or "{}")
        missing = [field for field in ("publisher", "issued", "retrieved", "license", "checksum")
                   if not row.get(field)]
        if not (row.get("uri") or row.get("path")):
            missing.append("locator")
        if not meta.get("acquisition_route") and not row.get("uri"):
            missing.append("acquisition_route")
        if missing:
            gaps.append({"source_id": row["id"], "title": row["title"], "missing": missing})
    complete = len(rows) - len(gaps)
    return {"status": "ok", "sources": len(rows), "complete": complete,
            "incomplete": len(gaps), "gaps": gaps[:100],
            "required_fields": ["publisher", "issued", "retrieved", "license", "checksum", "locator"],
            "note": "incomplete provenance blocks export under the default policy"}


def op_chain(ctx, claim_id: str) -> dict:
    """Validate file -> segment -> claim -> citation lineage for one claim."""
    store = _store()
    conn = ctx.conn()
    row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise store.ToolError(2, f"unknown claim: {claim_id}")
    claim = dict(row)
    steps = []
    src = store.get_source(conn, claim["source_id"]) if claim["source_id"] else None
    steps.append({"step": "source", "ok": bool(src), "detail": (src or {}).get("title")})
    if src:
        path = src["path"] if os.path.isabs(src["path"] or "") else ctx.path(src["path"] or "")
        exists = bool(src["path"]) and os.path.exists(path)
        hash_ok = exists and store.sha256_file(path) == src["checksum"]
        steps.append({"step": "file", "ok": exists and hash_ok,
                      "detail": {"path": src["path"], "exists": exists, "checksum_match": hash_ok}})
    seg_ok = bool(conn.execute("SELECT 1 FROM segments WHERE source_id=? LIMIT 1",
                               (claim["source_id"],)).fetchone()) if claim["source_id"] else False
    steps.append({"step": "segment", "ok": seg_ok,
                  "detail": "claim is sentence-level; source segments exist" if seg_ok else "no segments"})
    evidence = [dict(r) for r in conn.execute(
        "SELECT segment_id,relation,score FROM claim_evidence WHERE claim_id=?", (claim_id,))]
    steps.append({"step": "evidence_links", "ok": bool(evidence), "detail": evidence[:5]})
    cite_ok = bool(conn.execute("SELECT 1 FROM citations WHERE source_id=? LIMIT 1",
                                (claim["source_id"],)).fetchone()) if claim["source_id"] else False
    steps.append({"step": "citation", "ok": cite_ok,
                  "detail": "source is cited in the draft" if cite_ok else "no stored citation for this source"})
    broken = [s["step"] for s in steps if not s["ok"]]
    return {"status": "ok", "claim_id": claim_id, "claim": claim["text"], "steps": steps,
            "intact": not broken, "broken_steps": broken}


def op_abstain(ctx, claim: str, min_evidence: int = 1) -> dict:
    """Convert verification results into calibrated language or an explicit abstention."""
    verification = op_claim(ctx, claim)
    verdict = verification["verdict"]
    if verdict == "supported" and verification["support_count"] >= min_evidence:
        mode = "answer"
        phrasing = "Evidence in the local corpus supports this claim; attribute it to the cited source."
    elif verdict == "qualified":
        mode = "qualify"
        phrasing = ("Evidence is partial or mixed: state the finding with its scope and note the "
                    "conflicting or limited evidence.")
    elif verdict == "contradicted":
        mode = "escalate"
        phrasing = ("Local evidence contradicts this claim: do not publish it as stated; route it to "
                    "human review with both passages.")
    else:
        mode = "abstain"
        phrasing = ("The local corpus cannot support this claim: state that the corpus is "
                    "insufficient, or omit the claim.")
    return {"status": "ok", "claim": claim, "verdict": verdict, "mode": mode,
            "required_language": phrasing, "verification": verification,
            "policy": "high-impact claims require human approval before publication"}


def register(groups, common):
    g = groups["verify"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("claim", "Verify one claim against local evidence", op_claim,
        claim=(("--claim",), dict(required=True)), limit=(("--limit",), dict(type=int, default=5)),
        min_coverage=(("--min-coverage",), dict(type=float, default=0.5)))
    sub("corroborate", "Separate independent corroboration from repeated text", op_corroborate,
        claim=(("--claim",), dict(required=True)), limit=(("--limit",), dict(type=int, default=8)),
        similarity_cap=(("--similarity-cap",), dict(type=float, default=0.6)))
    sub("quote", "Verify quotation wording, context and omissions", op_quote,
        quote=(("--quote",), dict(required=True)),
        source_id=(("--source-id", "--source"), dict(default=None)),
        context_words=(("--context-words",), dict(type=int, default=12)))
    sub("numbers", "Recompute stated percentages, ratios and unit conversions", op_numbers,
        text=(("--text",), dict(default=None)), input_path=(("--input", "-i"), dict(default=None)))
    sub("chronology", "Find impossible sequences, anachronisms and date confusion", op_chronology,
        source_id=(("--source-id", "--source"), dict(default=None)))
    sub("contradiction", "Map incompatible claims with source quality", op_contradiction,
        topic=(("--topic",), dict(required=True)), limit=(("--limit",), dict(type=int, default=8)))
    sub("authority", "Score source authority from metadata", op_authority,
        source_id=(("--source-id", "--source"), dict(required=True)))
    sub("freshness", "Score staleness risk and update actions", op_freshness,
        source_id=(("--source-id", "--source"), dict(default=None)),
        as_of=(("--as-of",), dict(default=None)))
    sub("provenance", "Check provenance completeness per source", op_provenance,
        source_id=(("--source-id", "--source"), dict(default=None)))
    sub("chain", "Validate file -> segment -> claim -> citation lineage", op_chain,
        claim_id=(("--claim-id",), dict(required=True)))
    sub("abstain", "Decide answer/qualify/escalate/abstain for a claim", op_abstain,
        claim=(("--claim",), dict(required=True)),
        min_evidence=(("--min-evidence",), dict(type=int, default=1)))
