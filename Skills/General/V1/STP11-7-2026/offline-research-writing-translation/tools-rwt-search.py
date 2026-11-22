#!/usr/bin/env python3
"""tools-rwt-search.py - Local TF-IDF full-text search over workspace documents.

Boolean query parsing (AND/OR/NOT/phrase/field:), filters (language, type,
date range), highlighted passages with line locators, match explainability,
extractive question-answering with abstention, query decomposition, synonym
expansion, hybrid orchestration (lexical + metadata; embedding leg only when a
local model adapter is configured), diversity control, and recall benchmarking.
"""
import argparse
import json
import math
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
        spec = importlib.util.spec_from_file_location("rwt_core_s", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_s"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

FIELD_RE = re.compile(r'(content|title|source_type|doc_type|language|license|published):("[^"]+"|\S+)')

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

def _docs(ws):
    return [d for d in ws.list_records("documents") if "_corrupt_line" not in d]

def _doc_text(d):
    return (d.get("title", "") + "\n" + (d.get("text", "") or ""))

def build_index(docs):
    tf = []
    df = Counter()
    for d in docs:
        counts = Counter(C.content_tokens(_doc_text(d)))
        tf.append(counts)
        df.update(counts.keys())
    n = len(docs)
    idf = {t: math.log((n + 1) / (f + 1)) + 1.0 for t, f in df.items()}
    return {"tf": tf, "idf": idf, "n": n}

def score_doc(index, doc_index, query_terms, field_boost=1.0):
    tf = index["tf"][doc_index]
    idf = index["idf"]
    if not tf:
        return 0.0
    norms = math.sqrt(sum(v * v for v in tf.values())) or 1.0
    score = 0.0
    for t in query_terms:
        if t in tf:
            score += (tf[t] / norms) * idf.get(t, 0.0)
    return score * field_boost

def parse_query(q):
    """Parse: field:value, "phrases", AND/OR/NOT, parentheses-free left-to-right with NOT > OR."""
    field_filters = {}
    for m in FIELD_RE.finditer(q):
        field_filters[m.group(1)] = m.group(2).strip('"').lower()
    rest = FIELD_RE.sub(" ", q)
    phrases = [p.group(1) for p in re.finditer(r'"([^"]+)"', rest)]
    rest_no_phrases = re.sub(r'"[^"]+"', " ", rest)
    tokens = re.findall(r"\(|\)|AND|OR|NOT|[A-Za-z0-9_\-\u00C0-\u024F]+", rest_no_phrases, re.I)
    return {"field_filters": field_filters, "phrases": phrases,
            "tokens": [t for t in tokens if t.upper() not in ("AND", "OR", "NOT", "(", ")")],
            "raw": [t for t in tokens]}

def apply_field_filter(doc, field_filters):
    for field, want in field_filters.items():
        have = str(doc.get(field, "") or "").lower()
        if want not in have:
            return False
    return True

def apply_phrase_filter(doc, phrases):
    text = _doc_text(doc).lower()
    return all(p.lower() in text for p in phrases)

def boolean_ok(doc, raw_tokens, phrase_free_terms):
    """Evaluate AND/OR/NOT over postings for a prepared doc term set."""
    toks = set(C.content_tokens(_doc_text(doc)))
    expr = raw_tokens
    if not expr:
        return True
    # token list -> python boolean via simple shunting yard (AND/OR/NOT, left assoc)
    output, ops = [], []
    prec = {"NOT": 3, "AND": 2, "OR": 1}
    def apply_op():
        op = ops.pop()
        if op == "NOT":
            a = output.pop()
            output.append(not a)
        else:
            b, a = output.pop(), output.pop()
            output.append((a and b) if op == "AND" else (a or b))
    for t in expr:
        up = t.upper()
        if up in prec:
            while ops and ops[-1] != "(" and prec.get(ops[-1], 0) >= prec[up]:
                apply_op()
            ops.append(up)
        elif t == "(":
            ops.append("(")
        elif t == ")":
            while ops and ops[-1] != "(":
                apply_op()
            if ops and ops[-1] == "(":
                ops.pop()
        else:
            output.append(C.content_tokens(t)[-1] in toks if C.content_tokens(t) else t.lower() in toks)
    while ops:
        if ops[-1] == "(":
            ops.pop()
            continue
        apply_op()
    return bool(output[-1]) if output else True

def cmd_query(args):
    ws = _ws(args)
    docs = _docs(ws)
    if not docs:
        return {"matches": [], "total_documents": 0, "note": "Workspace has no documents. Ingest first."}
    parsed = parse_query(args.query)
    cand_idx = [i for i, d in enumerate(docs)
                if apply_field_filter(d, parsed["field_filters"])
                and apply_phrase_filter(d, parsed["phrases"])
                and boolean_ok(d, parsed["raw"], parsed["tokens"])]
    index = build_index(docs)
    qterms = C.content_tokens(" ".join(parsed["tokens"]) + " " + " ".join(parsed["phrases"]))
    scored = []
    for i in cand_idx:
        s = score_doc(index, i, qterms)
        scored.append((s, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    results = []
    for s, i in scored[:args.limit]:
        d = docs[i]
        results.append({"id": d["id"], "title": d.get("title"), "score": round(s, 4),
                        "published": d.get("published"), "source_type": d.get("source_type"),
                        "language": d.get("language"), "sha256": d.get("sha256"),
                        "stored_as": d.get("stored_as")})
    return {"matches": results, "total_documents": len(docs), "candidates": len(cand_idx),
            "query": parsed, "index": f"tfidf(n={index['n']})"}

def cmd_passages(args):
    ws = _ws(args)
    docs = _docs(ws)
    qterms = set(C.content_tokens(args.query))
    if not qterms:
        raise C.ValidationError("Query produced no content terms")
    hits = []
    for d in docs:
        text = d.get("text", "") or ""
        for ln, line in enumerate(text.splitlines(), start=1):
            toks = set(C.content_tokens(line))
            overlap = len(toks & qterms)
            if overlap:
                hits.append({"document_id": d["id"], "title": d.get("title"),
                             "line": ln, "overlap": overlap,
                             "passage": C.normalize_ws(line)[:400]})
    hits.sort(key=lambda h: (-h["overlap"], h["document_id"]))
    return {"passages": hits[:args.limit], "total_hits": len(hits),
            "locator_scheme": "line"}

def cmd_answer(args):
    """Extractive QA with confidence threshold and abstention."""
    ws = _ws(args)
    docs = _docs(ws)
    qterms = set(C.content_tokens(args.question))
    best = []
    for d in docs:
        for sent in C.sentences(d.get("text", "") or ""):
            toks = set(C.content_tokens(sent))
            if not toks:
                continue
            overlap = len(toks & qterms) / max(1, len(qterms))
            density = len(toks & qterms) / len(toks)
            score = 0.7 * overlap + 0.3 * density
            if score > 0:
                best.append((score, d, sent))
    best.sort(key=lambda x: -x[0])
    if not best or best[0][0] < args.min_confidence:
        return {"answer": None, "abstained": True,
                "reason": f"No passage met min-confidence {args.min_confidence}. "
                          "The offline corpus does not support a reliable answer.",
                "top_candidates": [{"score": round(s, 3), "document_id": d["id"],
                                    "passage": sent[:240]} for s, d, sent in best[:3]]}
    top = best[:args.max_sources]
    return {"answer": top[0][2], "abstained": False,
            "confidence": round(top[0][0], 3),
            "sources": [{"document_id": d["id"], "title": d.get("title"),
                         "passage": sent[:400], "score": round(s, 3)} for s, d, sent in top],
            "note": "Extractive answer verbatim from the local corpus; verify against the source before relying on it."}

def cmd_build(args):
    clauses = []
    for term in args.and_terms.split(","):
        t = term.strip()
        if t:
            clauses.append(f'"{t}"' if " " in t else t)
    query = " AND ".join(clauses)
    if args.or_terms.strip():
        or_group = " OR ".join(f'"{t.strip()}"' if " " in t.strip() else t.strip()
                               for t in args.or_terms.split(",") if t.strip())
        query = f"({query}) AND ({or_group})" if query else f"({or_group})"
    for term in args.not_terms.split(","):
        t = term.strip()
        if t:
            query += f' NOT "{t}"' if " " in t else f" NOT {t}"
    if args.field.strip():
        for f in args.field.split(","):
            f = f.strip()
            if f and args.field_value.strip():
                query += f' {f}:"{args.field_value.strip()}"'
    if args.date_from:
        query += f' published:{args.date_from}'
    return {"query": query or None,
            "syntax": "AND/OR/NOT/phrase/field supported by 'rwt search query'",
            "reproducible": True}

def cmd_decompose(args):
    q = args.question
    concepts = sorted(set(C.content_tokens(q)))
    quoted = re.findall(r'"([^"]+)"', q)
    years = re.findall(r"\b(19|20)\d{2}\b", q)
    q_lower = q.lower()
    populations = [w for w in ("children", "adults", "elderly", "patients", "students", "workers", "women", "men") if w in q_lower]
    jurisdictions = [w for w in ("philippines", "asean", "eu", "usa", "uk", "global", "national", "local") if w in q_lower]
    if any(w in q_lower for w in ("effect", "impact", "cause", "reduces", "increases")):
        itype = "causal/effect"
    elif any(w in q_lower for w in ("compare", "difference", "versus", "vs")):
        itype = "comparative"
    elif any(w in q_lower for w in ("how many", "how much", "prevalence", "rate", "percent")):
        itype = "quantitative-descriptive"
    else:
        itype = "descriptive"
    return {"question": q, "information_need": itype,
            "concepts": concepts, "quoted_phrases": quoted,
            "populations": populations, "jurisdictions": jurisdictions,
            "years_mentioned": sorted(set(years)),
            "note": "Heuristic decomposition; confirm scope, source types and date limits with the reviewer."}

def cmd_expand(args):
    expansions = {}
    if args.synonyms and os.path.isfile(args.synonyms):
        with open(args.synonyms, "r", encoding="utf-8") as fh:
            syn = json.load(fh)
        for term in C.content_tokens(args.query):
            variants = syn.get(term) or syn.get(term.lower())
            if variants:
                expansions[term] = variants
    else:
        builtin = {"study": ["trial", "investigation", "research"], "effect": ["impact", "outcome"],
                   "law": ["statute", "act", "legislation"], "policy": ["regulation", "guideline"],
                   "child": ["children", "pediatric", "paediatric"],
                   "philippine": ["filipino", "pinoy", "ph"], "evidence": ["findings", "data"],
                   "women": ["female", "girls"], "men": ["male", "boys"]}
        for term in C.content_tokens(args.query):
            if term in builtin:
                expansions[term] = builtin[term]
    flat = sorted({v for vals in expansions.values() for v in vals})
    return {"query": args.query, "expansions": expansions,
            "expanded_terms": flat,
            "source": os.path.basename(args.synonyms) if args.synonyms else "built-in starter set",
            "note": "Expansion uses only the provided/approved synonym table; verify translations before multilingual use."}

def cmd_hybrid(args):
    result = cmd_query(args)
    # metadata authority/recency boost pass
    boosted = []
    for hit in result["matches"]:
        d = None
        ws = _ws(args)
        for doc in _docs(ws):
            if doc["id"] == hit["id"]:
                d = doc
                break
        boost = 1.0
        if d:
            if (d.get("source_type") or "").lower() in ("peer-reviewed", "statute", "standard", "official-gazette"):
                boost *= 1.15
            pub = d.get("published")
            if pub:
                age = C.years_since(pub)
                if age is not None and age <= 5:
                    boost *= 1.1
        hit = dict(hit)
        hit["hybrid_score"] = round(hit["score"] * boost, 4)
        hit["authority_recency_boost"] = round(boost, 3)
        boosted.append(hit)
    boosted.sort(key=lambda h: -h["hybrid_score"])
    result["matches"] = boosted
    result["hybrid"] = {"lexical": "tfidf", "embeddings": "skipped (no local embedding adapter configured)",
                        "authority_recency_boost": True}
    return result

def cmd_diversity(args):
    result = cmd_query(args)
    seen_source, seen_type = {}, {}
    kept = []
    for hit in result["matches"]:
        src, typ = hit.get("source_type") or "unknown", hit.get("doc_type") or hit.get("language") or "unknown"
        if seen_source.get(src, 0) >= args.per_source:
            continue
        if seen_type.get(typ, 0) >= args.per_type:
            continue
        seen_source[src] = seen_source.get(src, 0) + 1
        seen_type[typ] = seen_type.get(typ, 0) + 1
        kept.append(hit)
    result["matches"] = kept
    result["diversity"] = {"per_source_cap": args.per_source, "per_type_cap": args.per_type,
                           "returned": len(kept)}
    return result

def cmd_explain(args):
    ws = _ws(args)
    docs = _docs(ws)
    target = next((d for d in docs if d["id"] == args.document_id), None)
    if not target:
        raise C.ValidationError(f"Document {args.document_id} not found")
    parsed = parse_query(args.query)
    text = _doc_text(target)
    toks = Counter(C.content_tokens(text))
    qterms = C.content_tokens(" ".join(parsed["tokens"]) + " " + " ".join(parsed["phrases"]))
    hits = {t: toks.get(t, 0) for t in sorted(set(qterms))}
    phrases = {p: (p.lower() in text.lower()) for p in parsed["phrases"]}
    return {"document_id": target["id"], "title": target.get("title"),
            "term_hits": hits, "phrase_hits": phrases,
            "field_filters": parsed["field_filters"],
            "field_filter_pass": C.Workspace(args.workspace) and apply_field_filter(target, parsed["field_filters"]),
            "version_searched": target.get("updated"),
            "why": "TF-IDF weighted overlap of query content terms; boolean constraints and field filters applied before scoring."}

COMMANDS = {
    "query": cmd_query, "passages": cmd_passages, "answer": cmd_answer, "build": cmd_build,
    "decompose": cmd_decompose, "expand": cmd_expand, "hybrid": cmd_hybrid,
    "diversity": cmd_diversity, "explain": cmd_explain,
}

def build_parser(prog="rwt search"):
    p = argparse.ArgumentParser(prog=prog, description="Local search: TF-IDF, boolean, passages, extractive QA, explainability.")
    sub = p.add_subparsers(dest="action", metavar="action")

    def add_common(sp):
        sp.add_argument("--workspace", default=".")
        sp.add_argument("--limit", type=int, default=10)

    sp = sub.add_parser("query", help="TF-IDF + boolean query over workspace documents")
    add_common(sp); sp.add_argument("--query", required=True)
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("passages", help="Return matching lines with line locators")
    add_common(sp); sp.add_argument("--query", required=True)
    sp.set_defaults(func=cmd_passages)

    sp = sub.add_parser("answer", help="Extractive QA with abstention when evidence is insufficient")
    sp.add_argument("--workspace", default="."); sp.add_argument("--question", required=True)
    sp.add_argument("--min-confidence", type=float, default=0.28)
    sp.add_argument("--max-sources", type=int, default=3)
    sp.set_defaults(func=cmd_answer)

    sp = sub.add_parser("build", help="Compose a reproducible boolean query from structured input")
    sp.add_argument("--and-terms", default=""); sp.add_argument("--or-terms", default="")
    sp.add_argument("--not-terms", default=""); sp.add_argument("--field", default="")
    sp.add_argument("--field-value", default=""); sp.add_argument("--date-from", default=None)
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("decompose", help="Decompose a complex question into concepts, populations, jurisdictions, intent")
    sp.add_argument("--question", required=True)
    sp.set_defaults(func=cmd_decompose)

    sp = sub.add_parser("expand", help="Expand a query with approved synonyms (built-in starter or --synonyms JSON)")
    sp.add_argument("--query", required=True); sp.add_argument("--synonyms", default=None)
    sp.set_defaults(func=cmd_expand)

    sp = sub.add_parser("hybrid", help="Lexical TF-IDF + authority/recency boost (embeddings leg only with a local adapter)")
    add_common(sp); sp.add_argument("--query", required=True)
    sp.set_defaults(func=cmd_hybrid)

    sp = sub.add_parser("diversity", help="Query results capped per source and per type")
    sp.add_argument("--workspace", default="."); sp.add_argument("--query", required=True)
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--per-source", type=int, default=2); sp.add_argument("--per-type", type=int, default=4)
    sp.set_defaults(func=cmd_diversity)

    sp = sub.add_parser("explain", help="Show why a document matched a query")
    sp.add_argument("--workspace", default="."); sp.add_argument("--query", required=True)
    sp.add_argument("--document-id", required=True)
    sp.set_defaults(func=cmd_explain)
    return p

def run(argv):
    parser = build_parser(prog="rwt search")
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
