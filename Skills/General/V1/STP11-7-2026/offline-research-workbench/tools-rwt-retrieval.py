"""tools-rwt-retrieval.py - local full-text search, embeddings, reranking and retrieval orchestration.

All retrieval runs against the local SQLite corpus (FTS5/BM25). The deterministic vector
baseline is a hashed sublinear-TF cosine index computed from local text, so hybrid retrieval
works with no model download. A configured `embed`/`rerank` adapter replaces the baseline
when one exists; the response always records which engine produced the ranking.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re

NAME = "retrieval"
ROLE = "local search, embeddings, reranking, hybrid retrieval, query planning, recall benchmarking"
GROUPS = [("search", "Local full-text search, hybrid retrieval, reranking and query planning")]
deps: dict = {}

BOOLEAN_OPS = ("AND", "OR", "NOT", "NEAR")


def _store():
    return deps["store"]


# ---------------------------------------------------------------- embedding baseline

def hash_embed(text: str, dims: int = 512) -> list[float]:
    """Deterministic local embedding: hashed unigram+bigram sublinear TF, L2 normalized."""
    toks = _store().tokens(text)
    if not toks:
        return [0.0] * dims
    vec = [0.0] * dims
    grams = list(toks) + [f"{toks[i]}~{toks[i + 1]}" for i in range(len(toks) - 1)]
    counts: dict = {}
    for gram in grams:
        digest = hashlib.md5(gram.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % dims
        sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
        counts[bucket] = counts.get(bucket, 0.0) + sign
    for bucket, value in counts.items():
        magnitude = abs(value)
        weight = 1.0 + math.log(magnitude) if magnitude > 1.0 else magnitude
        vec[bucket] = weight * (1.0 if value >= 0 else -1.0)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def embed_texts(ctx, texts: list[str]) -> tuple[list[list[float]], str]:
    """Use the configured embed adapter when available, else the deterministic baseline."""
    dims = int(ctx.cfg.get("search", {}).get("embedding_dims", 512))
    adapter = deps["adapters"]
    if adapter.resolve_command(ctx, "embed"):
        result = adapter.invoke(ctx, "embed", {"texts": texts})
        vectors = result["result"]["vectors"]
        if len(vectors) != len(texts):
            raise _store().ToolError(4, "embed adapter returned a different number of vectors "
                                        "than requested texts")
        return [[float(x) for x in vec] for vec in vectors], "embed-adapter"
    return [hash_embed(t, dims) for t in texts], "hash-tf-baseline"


# ---------------------------------------------------------------- query handling

_FTS_UNSAFE = re.compile(r'[()\[\]{}^:~\\]')


def build_match_query(query: str, max_terms: int = 32) -> dict:
    """Turn loose user input into a safe, reproducible FTS5 MATCH expression."""
    raw = (query or "").strip()
    if not raw:
        raise _store().ToolError(2, "query must not be empty")
    has_boolean = bool(re.search(r"\b(AND|OR|NOT|NEAR)\b", raw)) or '"' in raw
    if has_boolean:
        expr = raw
        terms = [t for t in re.findall(r"[^\s()\"]+", expr)
                 if t.upper() not in BOOLEAN_OPS and t.upper() != "NEAR"]
        terms = [re.sub(r"\*$", "", t) for t in terms][:max_terms]
        sanitized = expr
    else:
        terms = [t for t in re.findall(r"[^\W_]+(?:['\u2019-][^\W_]+)*", raw)][:max_terms]
        if not terms:
            raise _store().ToolError(2, f"query has no searchable terms: {query!r}")
        sanitized = " AND ".join(f'"{t}"' for t in terms)
    return {"expr": sanitized, "terms": terms, "mode": "boolean" if has_boolean else "all-terms"}


def _filters_sql(filters: dict, alias: str = "s") -> tuple[str, list]:
    clauses, args = [], []
    if filters.get("source_id"):
        clauses.append(f"{alias}.id = ?"); args.append(filters["source_id"])
    if filters.get("doctype"):
        clauses.append(f"{alias}.doctype = ?"); args.append(filters["doctype"])
    if filters.get("language"):
        clauses.append(f"{alias}.language = ?"); args.append(filters["language"])
    if filters.get("license"):
        clauses.append(f"{alias}.license = ?"); args.append(filters["license"])
    if filters.get("year_from"):
        clauses.append(f"substr(COALESCE({alias}.issued,''),1,4) >= ?"); args.append(str(filters["year_from"]))
    if filters.get("year_to"):
        clauses.append(f"substr(COALESCE({alias}.issued,''),1,4) <= ?"); args.append(str(filters["year_to"]))
    if filters.get("exclude_superseded"):
        clauses.append(f"{alias}.status IN ('active','current')")
    return (" AND " + " AND ".join(clauses) if clauses else ""), args


def _snippet(text: str, terms: list[str], width: int = 28) -> dict:
    toks = text.split()
    lowered = [t.lower().strip(".,;:()[]\"'") for t in toks]
    hits = [i for i, t in enumerate(lowered) if any(term.lower() in t for term in terms)]
    if not hits:
        return {"snippet": " ".join(toks[:width * 2]), "matched_terms": []}
    start = max(0, hits[0] - width // 2)
    end = min(len(toks), hits[-1] + width)
    matched = sorted({term for term in terms for t in lowered if term.lower() in t})
    return {"snippet": ("..." if start else "") + " ".join(toks[start:end]) + ("..." if end < len(toks) else ""),
            "matched_terms": matched}


def _facets(rows: list[dict]) -> dict:
    facets = {"doctype": {}, "language": {}, "year": {}, "source": {}}
    for row in rows:
        facets["doctype"][row["doctype"] or "unspecified"] = facets["doctype"].get(row["doctype"] or "unspecified", 0) + 1
        facets["language"][row["language"] or "unspecified"] = facets["language"].get(row["language"] or "unspecified", 0) + 1
        year = (row["issued"] or "")[:4] or "unspecified"
        facets["year"][year] = facets["year"].get(year, 0) + 1
        facets["source"][row["source_id"]] = facets["source"].get(row["source_id"], 0) + 1
    return {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:12]) for k, v in facets.items()}


def op_index(ctx, rebuild: bool = True) -> dict:
    conn = ctx.conn()
    count = _store().reindex_fts(conn) if rebuild else 0
    ctx.commit()
    fts = _store().meta(conn, "fts5", "0") == "1"
    return {"status": "ok", "engine": "sqlite-fts5" if fts else "no-fts5",
            "segments_indexed": count,
            "sources": conn.execute("SELECT count(*) c FROM sources").fetchone()["c"],
            "note": "vector baselines are computed on demand; no index rebuild needed"}


def op_query(ctx, query: str, limit: int = 10, doctype=None, language=None, source_id=None,
             year_from=None, year_to=None, facet: bool = False, explain: bool = False) -> dict:
    store = _store()
    conn = ctx.conn()
    built = build_match_query(query, int(ctx.cfg.get("limits", {}).get("max_query_terms", 32)))
    limit = max(1, min(int(limit), 200))
    filters = {"doctype": doctype, "language": language, "source_id": source_id,
               "year_from": year_from, "year_to": year_to, "exclude_superseded": True}
    where, where_args = _filters_sql(filters)
    sql = f"""SELECT f.segment_id, f.source_id, -bm25(segments_fts, ?, ?) AS score,
                     s.title, s.doctype, s.language, s.issued, s.license, seg.text, seg.locator,
                     seg.page, seg.section, seg.start_ms, seg.end_ms
              FROM segments_fts f
              JOIN segments seg ON seg.id = f.segment_id
              JOIN sources s ON s.id = f.source_id
              WHERE segments_fts MATCH ? {where}
              ORDER BY score DESC LIMIT ?"""
    args = [float(ctx.cfg.get("search", {}).get("bm25_k1", 1.2)),
            float(ctx.cfg.get("search", {}).get("bm25_b", 0.75)),
            built["expr"], *where_args, limit]
    try:
        rows = [dict(r) for r in conn.execute(sql, args)]
    except Exception as exc:  # sqlite3.OperationalError on malformed FTS syntax
        raise store.ToolError(2, f"query could not be parsed as an FTS5 expression: {exc}",
                              {"expression": built["expr"],
                               "hint": "quote multi-word phrases or use space separated terms"})
    for row in rows:
        row["score"] = round(row["score"], 12)
        row.update(_snippet(row["text"], built["terms"]))
        if not explain:
            row.pop("text", None)
    payload = {"status": "ok", "engine": "sqlite-fts5-bm25", "query": query,
               "expression": built["expr"], "terms": built["terms"], "count": len(rows),
               "results": rows}
    if facet:
        payload["facets"] = _facets(rows)
    if explain:
        payload["explain"] = {
            "matched_expression": built["expr"],
            "scoring": "sqlite FTS5 bm25 with configured k1/b",
            "filters": {k: v for k, v in filters.items() if v},
            "ranking_note": "higher score = stronger lexical match; identical scores mean identical term statistics",
        }
    if not rows:
        payload["advisory"] = "no local evidence matched; abstain or widen the query (honest abstention is required)"
    return payload


def op_hybrid(ctx, query: str, limit: int = 10, alpha: float = 0.5, diversify: bool = True,
              doctype=None, language=None) -> dict:
    """Blend BM25 with dense similarity, then apply a source-diversity penalty."""
    store = _store()
    conn = ctx.conn()
    built = build_match_query(query)
    pool = max(limit * 5, 25)
    where, where_args = _filters_sql({"doctype": doctype, "language": language,
                                      "exclude_superseded": True})
    try:
        rows = [dict(r) for r in conn.execute(
            f"""SELECT f.segment_id, f.source_id, -bm25(segments_fts, ?, ?) AS bm25,
                       s.title, s.doctype, s.language, s.issued, seg.text, seg.locator, seg.page
                FROM segments_fts f JOIN segments seg ON seg.id=f.segment_id
                JOIN sources s ON s.id=f.source_id
                WHERE segments_fts MATCH ? {where} ORDER BY bm25 DESC LIMIT ?""",
            [1.2, 0.75, built["expr"], *where_args, pool])]
    except Exception as exc:
        raise store.ToolError(2, f"query could not be parsed: {exc}", {"expression": built["expr"]})
    if not rows:
        return {"status": "ok", "results": [], "count": 0,
                "advisory": "no local evidence matched; abstain rather than answer from memory"}
    qvecs, qengine = embed_texts(ctx, [query])
    dvecs, dengine = embed_texts(ctx, [r["text"] for r in rows])
    lex_max = max(r["bm25"] for r in rows) or 1.0
    for row, dvec in zip(rows, dvecs):
        lexical = row["bm25"] / lex_max
        dense = cosine(qvecs[0], dvec)
        row["lexical"] = round(lexical, 4)
        row["dense"] = round(dense, 4)
        row["score"] = round(alpha * lexical + (1 - alpha) * dense, 6)
    rows.sort(key=lambda r: -r["score"])
    if diversify:
        penalty = float(ctx.cfg.get("search", {}).get("diversity_penalty", 0.25))
        seen: dict = {}
        for row in rows:
            hits = seen.get(row["source_id"], 0)
            row["score"] = round(row["score"] * (1 - penalty * min(hits, 3)), 6)
            seen[row["source_id"]] = hits + 1
        rows.sort(key=lambda r: -r["score"])
    selected = rows[:limit]
    for row in selected:
        row.update(_snippet(row["text"], built["terms"]))
        row.pop("text", None)
    return {"status": "ok", "engine": f"hybrid(bm25+{dengine})", "alpha": alpha,
            "query": query, "candidates": len(rows), "count": len(selected),
            "diversified": diversify, "results": selected}


def op_embed(ctx, text: str | None = None, input_path: str | None = None) -> dict:
    store = _store()
    if input_path:
        if not os.path.exists(input_path):
            raise store.ToolError(2, f"input not found: {input_path}")
        text = store.read_text(input_path)
    if not text:
        raise store.ToolError(2, "provide --text or --input")
    vectors, engine = embed_texts(ctx, [text])
    norm = math.sqrt(sum(v * v for v in vectors[0])) or 1.0
    return {"status": "ok", "engine": engine, "dimensions": len(vectors[0]),
            "l2_norm": round(norm, 6), "vector_head": vectors[0][:12]}


def op_rerank(ctx, query: str, passages: list | None = None, input_path: str | None = None,
              top_k: int = 10) -> dict:
    """Rerank candidate passages with a cross-encoder adapter or the lexical+dense fallback."""
    store = _store()
    if input_path:
        if not os.path.exists(input_path):
            raise store.ToolError(2, f"input not found: {input_path}")
        try:
            rows = json.loads(store.read_text(input_path))
        except json.JSONDecodeError as exc:
            raise store.ToolError(2, f"input file must be a JSON array of strings or objects: {exc}")
        passages = [r["text"] if isinstance(r, dict) else r for r in rows]
    if not passages:
        raise store.ToolError(2, "provide --passages (repeatable) or --input with a JSON array")
    adapter = deps["adapters"]
    if adapter.resolve_command(ctx, "rerank"):
        result = adapter.invoke(ctx, "rerank", {"query": query, "passages": passages})
        scores = [float(s) for s in result["result"]["scores"]]
        if len(scores) != len(passages):
            raise store.ToolError(4, "rerank adapter returned a different number of scores")
        engine = "rerank-adapter"
    else:
        qvec, _ = embed_texts(ctx, [query])
        dvecs, _ = embed_texts(ctx, passages)
        qterms = set(store.tokens(query))
        scores = []
        for passage, dvec in zip(passages, dvecs):
            overlap = len(qterms & set(store.tokens(passage))) / (len(qterms) or 1)
            scores.append(round(0.5 * cosine(qvec[0], dvec) + 0.5 * overlap, 6))
        engine = "lexical-dense-fallback"
    ranked = sorted(({"rank": i + 1, "score": scores[idx], "passage": passages[idx][:400]}
                     for i, idx in enumerate(sorted(range(len(passages)), key=lambda i: -scores[i]))),
                    key=lambda r: r["rank"])
    return {"status": "ok", "engine": engine, "query": query, "count": len(ranked),
            "results": ranked[:max(1, int(top_k))]}


def op_decompose(ctx, question: str) -> dict:
    """Deterministic research query decomposition (concept + PICO + source-type facets)."""
    store = _store()
    q = (question or "").strip()
    if not q:
        raise store.ToolError(2, "question must not be empty")
    toks = [t for t in store.tokens(q) if t not in store.STOPWORDS["en"] and len(t) > 2]
    seen, concepts = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t); concepts.append(t)
    lowered = q.lower()
    pico = {}
    for label, patterns in {
        "population": (r"\b(?:in|among|for)\s+([a-z0-9 ,'-]{3,40}?)(?:\s+(?:in|during|from|between)\b|[?.]|$)",),
        "intervention": (r"\b(?:effect|impact|effectiveness)\s+of\s+([a-z0-9 ,'-]{3,40}?)(?:\s+on\b|[?.]|$)",),
        "outcome": (r"\b(?:on|upon)\s+([a-z0-9 ,'-]{3,40}?)(?:\s+in\b|[?.]|$)",),
        "jurisdiction": (r"\b(?:philippines|filipino|asean|eu|us|uk|ph)\b",),
        "period": (r"\b(?:19|20)\d{2}\b(?:\s*(?:-|to|and)\s*(?:19|20)\d{2})?",),
    }.items():
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                pico[label] = match.group(0).strip()
                break
    source_types = []
    for keyword, doctype in [("law", "statute"), ("policy", "policy"), ("standard", "standard"),
                             ("news", "news"), ("study", "journal-article"), ("review", "review"),
                             ("dataset", "dataset"), ("case", "case")]:
        if keyword in lowered:
            source_types.append(doctype)
    boolean = " AND ".join('"{}"'.format(c) for c in concepts[:8])
    return {"status": "ok", "question": q, "concepts": concepts,
            "facets": pico, "suggested_source_types": source_types,
            "suggested_boolean": boolean,
            "alternate_queries": [" OR ".join('"{}"'.format(c) for c in concepts[:4]) + " AND review",
                                  " AND ".join('"{}"'.format(c) for c in concepts[:3]) + " AND (statute OR policy)"]}


def op_boolean(ctx, concepts: str = None, mode: str = "AND", synonyms: str | None = None,
               year_from: str | None = None, year_to: str | None = None,
               language: str | None = None) -> dict:
    """Build a reproducible boolean strategy string (recorded verbatim in run manifests)."""
    store = _store()
    if not concepts:
        raise store.ToolError(2, "provide --concepts (comma or semicolon separated concept groups)")
    groups = [g.strip() for g in re.split(r"[;\n]", concepts) if g.strip()]
    rendered = []
    for group in groups:
        alts = [a.strip() for a in re.split(r"\s*\|\s*|,", group) if a.strip()]
        rendered.append("(" + " OR ".join(f'"{a}"' for a in alts) + ")")
    expr = f" {mode.upper()} ".join(rendered)
    if synonyms:
        extra = [s.strip() for s in synonyms.split(",") if s.strip()]
        if extra:
            expr += " AND (" + " OR ".join(f'"{s}"' for s in extra) + ")"
    if year_from or year_to:
        expr += f" AND year:[{year_from or '*'} TO {year_to or '*'}]"
    if language:
        expr += f" AND lang:{language}"
    return {"status": "ok", "boolean": expr, "concept_groups": len(groups),
            "mode": mode.upper(),
            "notes": ["run with: search query --query \"<boolean>\"",
                      "year and lang clauses are applied as metadata filters by search query"]}


def op_expand(ctx, query: str, target_lang: str | None = None) -> dict:
    """Expand a query with approved terminology equivalents plus deterministic variants."""
    store = _store()
    conn = ctx.conn()
    terms = store.tokens(query)
    if not terms:
        raise store.ToolError(2, "query must contain searchable terms")
    expansions, sources = {}, []
    for row in conn.execute("SELECT term,lang,equivalents,forbidden FROM terms"):
        equivalents = json.loads(row["equivalents"] or "{}")
        for token in terms:
            if token == row["term"].lower():
                alts = []
                if target_lang:
                    value = equivalents.get(target_lang)
                    if isinstance(value, list):
                        alts.extend(value)
                    elif value:
                        alts.append(value)
                else:
                    for lang, value in equivalents.items():
                        alts.extend(value if isinstance(value, list) else [value])
                if alts:
                    expansions[token] = sorted(set(alts))
                    sources.append("terminology")
    for token in terms:
        variants = {token}
        if token.endswith("ies"):
            variants.add(token[:-3] + "y")
        if token.endswith("s") and len(token) > 4:
            variants.add(token[:-1])
        variants.add(token.replace("-", " "))
        extra = sorted(v for v in variants if v != token)
        if extra:
            expansions.setdefault(token, [])
            expansions[token] = sorted(set(expansions[token]) | set(extra))
    expanded_query = " AND ".join(
        "(" + " OR ".join(f'"{v}"' for v in [t] + expansions.get(t, [])) + ")" for t in terms)
    return {"status": "ok", "query": query, "target_lang": target_lang,
            "expansions": expansions, "expansion_sources": sorted(set(sources)) or ["morphological"],
            "expanded_query": expanded_query,
            "note": "only approved terminology equivalents are added; nothing is machine-translated here"}


def op_recall(ctx, cases_path: str, limit: int = 10) -> dict:
    """Retrieval recall benchmark: known relevant segment/source ids per query."""
    store = _store()
    if not os.path.exists(cases_path):
        raise store.ToolError(2, f"cases file not found: {cases_path}")
    try:
        cases = json.loads(store.read_text(cases_path))
    except json.JSONDecodeError as exc:
        raise store.ToolError(2, f"cases file must be JSON: {exc}")
    if not isinstance(cases, list) or not cases:
        raise store.ToolError(2, "cases file must be a non-empty JSON array")
    max_items = int(ctx.cfg.get("evaluation", {}).get("max_benchmark_items", 500))
    cases = cases[:max_items]
    details, hits = [], 0
    for case in cases:
        query = case.get("query")
        relevant = set(case.get("relevant", []))
        if not query or not relevant:
            raise store.ToolError(2, "each case needs 'query' and non-empty 'relevant'")
        result = op_query(ctx, query, limit=limit)
        found = {r["segment_id"] for r in result["results"]} | {r["source_id"] for r in result["results"]}
        matched = sorted(relevant & found)
        hit = bool(matched)
        hits += 1 if hit else 0
        details.append({"query": query, "hit": hit, "matched": matched,
                        "missed": sorted(relevant - found)})
    recall = round(hits / len(cases), 4)
    threshold = float(ctx.cfg.get("evaluation", {}).get("thresholds", {}).get("retrieval_recall", 0.8))
    return {"status": "ok", "cases": len(cases), "recall_at_k": recall, "k": limit,
            "threshold": threshold, "passed": recall >= threshold, "details": details}


def op_explain(ctx, query: str, segment_id: str) -> dict:
    conn = ctx.conn()
    store = _store()
    seg = store.get_segment(conn, segment_id)
    if not seg:
        raise store.ToolError(2, f"unknown segment: {segment_id}")
    built = build_match_query(query)
    seg_terms = set(store.tokens(seg["text"]))
    query_terms = set(t.lower() for t in built["terms"])
    matched = sorted(query_terms & seg_terms)
    phrase_hits = [t for t in built["terms"] if " " in t and t.lower() in seg["text"].lower()]
    src = store.get_source(conn, seg["source_id"]) or {}
    return {"status": "ok", "query": query, "segment_id": segment_id,
            "matched_terms": matched, "missing_terms": sorted(query_terms - seg_terms),
            "phrase_hits": phrase_hits,
            "term_coverage": round(len(matched) / (len(query_terms) or 1), 4),
            "source": {"title": src.get("title"), "doctype": src.get("doctype"),
                       "language": src.get("language"), "issued": src.get("issued"),
                       "status": src.get("status"), "license": src.get("license")},
            "version_searched": seg["version_label"],
            "explanation": ("segment shares the query terms listed in matched_terms; "
                            "unmatched query terms explain why it ranked below a full match")}


def register(groups, common):
    g = groups["search"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("index", "Rebuild the FTS5 index over local segments", op_index,
        rebuild=(("--no-rebuild",), dict(action="store_false", default=True)))
    sub("query", "Full-text search with filters, facets and highlighting", op_query,
        query=(("--query", "-q"), dict(required=True)),
        limit=(("--limit",), dict(type=int, default=10)),
        doctype=(("--doctype",), dict(default=None)),
        language=(("--language", "--lang"), dict(default=None)),
        source_id=(("--source-id", "--source"), dict(default=None)),
        year_from=(("--year-from",), dict(default=None)),
        year_to=(("--year-to",), dict(default=None)),
        facet=(("--facet",), dict(action="store_true")),
        explain=(("--explain",), dict(action="store_true")))
    sub("hybrid", "Blend BM25 and dense similarity with source diversification", op_hybrid,
        query=(("--query", "-q"), dict(required=True)),
        limit=(("--limit",), dict(type=int, default=10)),
        alpha=(("--alpha",), dict(type=float, default=0.5)),
        diversify=(("--no-diversify",), dict(action="store_false", default=True)),
        doctype=(("--doctype",), dict(default=None)),
        language=(("--language", "--lang"), dict(default=None)))
    sub("embed", "Embed text locally (adapter or hashed-TF baseline)", op_embed,
        text=(("--text",), dict(default=None)),
        input_path=(("--input", "-i"), dict(default=None)))
    sub("rerank", "Rerank candidate passages for a query", op_rerank,
        query=(("--query", "-q"), dict(required=True)),
        passages=(("--passages",), dict(action="append", default=None)),
        input_path=(("--input", "-i"), dict(default=None)),
        top_k=(("--top-k",), dict(type=int, default=10)))
    sub("decompose", "Decompose a research question into concepts and facets", op_decompose,
        question=(("--question", "-q"), dict(required=True)))
    sub("boolean", "Build a reproducible boolean search strategy", op_boolean,
        concepts=(("--concepts",), dict(required=True)),
        mode=(("--mode",), dict(default="AND", choices=["AND", "OR"])),
        synonyms=(("--synonyms",), dict(default=None)),
        year_from=(("--year-from",), dict(default=None)),
        year_to=(("--year-to",), dict(default=None)),
        language=(("--language", "--lang"), dict(default=None)))
    sub("expand", "Expand a query with approved terminology and variants", op_expand,
        query=(("--query", "-q"), dict(required=True)),
        target_lang=(("--target-lang",), dict(default=None)))
    sub("recall", "Run a retrieval recall benchmark from a JSON case file", op_recall,
        cases_path=(("--cases",), dict(required=True)),
        limit=(("--limit",), dict(type=int, default=10)))
    sub("explain", "Explain why a segment matched a query", op_explain,
        query=(("--query", "-q"), dict(required=True)),
        segment_id=(("--segment-id", "--segment"), dict(required=True)))
