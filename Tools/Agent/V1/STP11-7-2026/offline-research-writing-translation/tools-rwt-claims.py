#!/usr/bin/env python3
"""tools-rwt-claims.py - Claim extraction, verification and fact-check support.

Capabilities: heuristic claim extraction, claim records in the workspace,
evidence linking, cross-source corroboration analysis, quotation verification
against local sources (wording, omissions, speaker, page/timecode), claim
coverage of a document against the evidence table, contradiction mapping,
identity/attribution cues, claim verification search with abstention.
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
        spec = importlib.util.spec_from_file_location("rwt_core_cl", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_cl"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

CLAIM_CUES = re.compile(
    r"\b(causes?|caused|leads? to|results? in|increases?|decreases?|reduces?|raises?|lowers?"
    r"|is associated with|prevent|cure|treat|predicts?|will|must|always|never|all|none"
    r"|\d+(?:\.\d+)?\s*%|\$?\d[\d,]*(?:\.\d+)?\s*(million|billion|thousand|m|bn)?\b)", re.I)
NEGATION = re.compile(r"\b(not|no|never|neither|without|cannot|n't)\b", re.I)
ATTRIBUTION = re.compile(r"\b(said|says|according to|reported|stated|argued|claimed|warned|announced)\b", re.I)

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

def cmd_extract(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for i, sent in enumerate(C.sentences(text), start=1):
        m = CLAIM_CUES.search(sent)
        if not m:
            continue
        if NEGATION.search(sent):
            kind_hint = "negated"
        elif ATTRIBUTION.search(sent):
            kind_hint = "attributed"
        else:
            kind_hint = "unattributed"
        kind = "quantitative" if re.search(r"\d", sent) else (
            "causal" if re.search(r"(cause|lead|result|prevent|cure|treat)", sent, re.I) else "factual")
        out.append({"sentence_no": i, "text": C.normalize_ws(sent),
                    "kind": kind, "attribution": kind_hint,
                    "review_required": True})
    return {"file": args.file, "claims": out, "count": len(out),
            "note": "Candidate claims only. A reviewer decides materiality and required evidence class."}

def cmd_add(args):
    ws = _ws(args)
    rec = ws.add_record("claims", {
        "text": args.text, "kind": args.kind, "source_id": args.source_id,
        "locator": args.locator, "verdict": args.verdict,
        "confidence": args.confidence, "review_status": "pending",
        "scope": {"population": args.population, "period": args.period,
                  "jurisdiction": args.jurisdiction},
    }, actor=args.actor)
    return {"claim_id": rec["id"], "record": {k: v for k, v in rec.items() if k != "text" or True}}

def cmd_list(args):
    ws = _ws(args)
    claims = ws.list_records("claims", limit=args.limit)
    return {"claims": claims, "count": len(claims)}

def cmd_link(args):
    ws = _ws(args)
    rec = ws.add_record("evidence", {
        "claim_id": args.claim_id, "document_id": args.document_id,
        "relation": args.relation, "locator": args.locator,
        "excerpt": args.excerpt[:2000] if args.excerpt else None,
        "review_status": "pending",
    }, actor=args.actor)
    return {"evidence_id": rec["id"]}

def cmd_verify(args):
    """Search local evidence for support/contradiction; abstain if insufficient."""
    ws = _ws(args)
    claims = ws.list_records("claims")
    claim = next((c for c in claims if c.get("id") == args.claim_id), None)
    question = args.question or (claim.get("text") if claim else None)
    if not question:
        raise C.ValidationError("Provide --question or a valid --claim-id")
    qterms = set(C.content_tokens(question))
    hits = []
    for d in ws.list_records("documents"):
        if "_corrupt_line" in d:
            continue
        for sent in C.sentences(d.get("text", "") or ""):
            toks = set(C.content_tokens(sent))
            if not toks:
                continue
            score = len(toks & qterms) / max(1, len(qterms))
            if score >= args.min_overlap:
                hits.append({"document_id": d["id"], "title": d.get("title"),
                             "score": round(score, 3), "passage": sent[:400]})
    hits.sort(key=lambda h: -h["score"])
    hits = hits[:args.max_hits]
    if not hits:
        verdict = "insufficient-evidence"
        note = "No local passage met the overlap threshold. Do not publish a verdict from absence."
    else:
        verdict = "review-required"
        note = ("Local passages found. A reviewer assigns support/contradict/qualify; "
                "repetition of one outlet is not corroboration.")
    ws.add_record("evidence", {"claim_id": args.claim_id, "relation": "verification-search",
                               "locator": "search", "excerpt": question[:400],
                               "hits": len(hits), "review_status": "pending"},
                  actor=args.actor)
    return {"claim": question[:300], "passages": hits, "verdict": verdict, "note": note}

def cmd_corroborate(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    sources = data.get("sources", [])
    min_independent = args.min_independent
    independent = []
    for s in sources:
        independent.append({"source": s.get("source") or s.get("id"),
                            "independent": s.get("independent", True),
                            "primary": s.get("primary", False)})
    n_indep = sum(1 for s in independent if s["independent"])
    n_primary = sum(1 for s in independent if s["primary"])
    verdict = "corroborated" if n_indep >= min_independent else (
        "single-source" if n_indep <= 1 else "weakly-corroborated")
    return {"sources_considered": len(sources), "independent_sources": n_indep,
            "primary_sources": n_primary, "required": min_independent,
            "verdict": verdict,
            "note": "Independence flags come from the independence analyzer (shared data/funders/releases). "
                    "Verbatim repetition is not corroboration."}

def cmd_quote_verify(args):
    with open(args.quote_file, "r", encoding="utf-8") as fh:
        quote = C.normalize_ws(fh.read())
    with open(args.source_file, "r", encoding="utf-8") as fh:
        source = fh.read()
    norm_source = C.normalize_ws(source)
    exact = quote in norm_source
    ratio = C.sim_ratio(quote.lower(), norm_source.lower())
    # locate best window via difflib on words
    q_words = quote.lower().split()
    s_words = norm_source.lower().split()
    best_ratio, best_window = 0.0, ""
    if s_words and q_words:
        qlen = len(q_words)
        step = max(1, qlen // 4)
        for i in range(0, max(1, len(s_words) - qlen + 1), step):
            window = " ".join(s_words[i:i + qlen])
            r = C.sim_ratio(" ".join(q_words), window)
            if r > best_ratio:
                best_ratio, best_window = r, window
    ellided = "[…]" in quote or "[...]" in quote
    return {"quote_words": len(q_words), "exact_match": exact,
            "best_window_similarity": round(best_ratio, 4),
            "best_window": best_window[:400] if not exact else None,
            "contains_ellipsis": ellided,
            "speaker": args.speaker, "locator": args.locator,
            "verdict": ("verified" if exact else
                        "minor-drift-review" if best_ratio > 0.85 else
                        "not-found-or-altered"),
            "note": "Wording drift, omissions (via ellipsis), speaker and context require human confirmation before publication."}

def cmd_coverage(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    ws = _ws(args)
    evidence = ws.list_records("evidence")
    claim_ids = {e.get("claim_id") for e in evidence}
    sentences = C.sentences(text)
    material, unlinked = [], []
    for i, s in enumerate(sentences):
        if CLAIM_CUES.search(s):
            material.append(i + 1)
    # weak linkage signal: evidence excerpts appearing in the document
    linked = 0
    for e in evidence:
        ex = (e.get("excerpt") or "").lower()
        if ex and ex[:120] in text.lower():
            linked += 1
    return {"material_claim_sentences": len(material),
            "sentence_indexes": material,
            "evidence_links_recorded": len(claim_ids),
            "evidence_excerpts_found_in_document": linked,
            "unlinked_estimate": max(0, len(material) - linked),
            "note": "Deterministic lower bound; pair with Substantive Evidence Editor review for claim-strength matching."}

def cmd_contradictions(args):
    ws = _ws(args)
    claims = ws.list_records("claims")
    groups = {}
    for c in claims:
        key_tokens = C.content_tokens(c.get("text", ""))
        key = " ".join(sorted(set(key_tokens))[:6])
        groups.setdefault(key, []).append(c)
    conflicts = []
    for key, members in groups.items():
        verdicts = Counter(str(m.get("verdict") or "unreviewed") for m in members)
        if len(verdicts) > 1:
            conflicts.append({"cluster_terms": key[:120],
                              "claims": [{"id": m["id"], "verdict": m.get("verdict"),
                                          "text": m.get("text", "")[:200]} for m in members]})
    return {"conflict_clusters": conflicts, "claims_checked": len(claims),
            "note": "Clusters share topic terms; a reviewer adjudicates using source quality, scope, date and method."}

def cmd_identity(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    issues = []
    for m in re.finditer(r"\b(said|stated|announced|warned|revealed)\s+([A-Z][a-z]+(?: [A-Z][a-z.]+){0,3})", text):
        name = m.group(2)
        issues.append({"type": "attributed_statement", "name": name, "quote_found": True,
                       "check": "Confirm role, affiliation and exact title against the source of record."})
    for m in re.finditer(r"\b(reportedly|allegedly|sources? say|it is believed)\b", text, re.I):
        issues.append({"type": "indirect_attribution", "text": m.group(0),
                       "check": "Indirect attribution: name or deliberately withhold source; do not imply firsthand confirmation."})
    for m in re.finditer(r"\b(Dr\.|Prof\.|Atty\.|Engr\.|Gov\.|Sen\.|Rep\.|Sec\.)\s*[A-Z]", text):
        pass  # title usage is fine; titles verified against record by reviewer
    return {"attribution_mentions": len(issues), "items": issues,
            "note": "Check every name against the source; misattributed quotations are high-severity errors."}

COMMANDS = {
    "extract": cmd_extract, "add": cmd_add, "list": cmd_list, "link": cmd_link,
    "verify": cmd_verify, "corroborate": cmd_corroborate, "quote-verify": cmd_quote_verify,
    "coverage": cmd_coverage, "contradictions": cmd_contradictions, "identity": cmd_identity,
}

def build_parser(prog="rwt claims"):
    p = argparse.ArgumentParser(prog=prog, description="Claim engines: extract, verify, corroborate, quote-verify, contradictions.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("extract", help="Heuristically extract candidate claims with source coordinates")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("add", help="Record a claim in the workspace")
    sp.add_argument("--workspace", default="."); sp.add_argument("--text", required=True)
    sp.add_argument("--kind", default="factual"); sp.add_argument("--source-id")
    sp.add_argument("--locator"); sp.add_argument("--verdict", default="unreviewed")
    sp.add_argument("--confidence", type=float, default=None); sp.add_argument("--population")
    sp.add_argument("--period"); sp.add_argument("--jurisdiction"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("list", help="List recorded claims")
    sp.add_argument("--workspace", default="."); sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("link", help="Link a claim to supporting/opposing/contextual evidence")
    sp.add_argument("--workspace", default="."); sp.add_argument("--claim-id", required=True)
    sp.add_argument("--document-id", required=True)
    sp.add_argument("--relation", required=True, choices=["supporting", "opposing", "contextual", "insufficient"])
    sp.add_argument("--locator"); sp.add_argument("--excerpt"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("verify", help="Search local evidence for support; abstains when insufficient")
    sp.add_argument("--workspace", default="."); sp.add_argument("--claim-id")
    sp.add_argument("--question"); sp.add_argument("--min-overlap", type=float, default=0.34)
    sp.add_argument("--max-hits", type=int, default=5); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("corroborate", help="Require independent sources for high-impact claims")
    sp.add_argument("--input", required=True, help="JSON {sources:[{source, independent?, primary?}]}")
    sp.add_argument("--min-independent", type=int, default=2); sp.set_defaults(func=cmd_corroborate)

    sp = sub.add_parser("quote-verify", help="Verify a quotation against the local source text")
    sp.add_argument("--quote-file", required=True); sp.add_argument("--source-file", required=True)
    sp.add_argument("--speaker"); sp.add_argument("--locator"); sp.set_defaults(func=cmd_quote_verify)

    sp = sub.add_parser("coverage", help="Estimate material claims lacking linked evidence")
    sp.add_argument("--file", required=True); sp.add_argument("--workspace", default=".")
    sp.set_defaults(func=cmd_coverage)

    sp = sub.add_parser("contradictions", help="Map conflicting verdicts across recorded claims")
    sp.add_argument("--workspace", default="."); sp.set_defaults(func=cmd_contradictions)

    sp = sub.add_parser("identity", help="Flag attribution statements and indirect-attribution cues")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_identity)
    return p

def run(argv):
    parser = build_parser(prog="rwt claims")
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
