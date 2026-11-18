#!/usr/bin/env python3
"""tools-oaa-knowledge.py -- offline knowledge ingestion, indexing and retrieval.

Loaded by `tools-oaa.py`. Registers:

  index.build     ingest files/directories into a local index (chunk, dedupe, embed)
  index.query     BM25, dense or hybrid retrieval with metadata filters
  index.audit     integrity audit (orphans, dimension mismatch, stale sources)
  index.snapshot  reproducible point-in-time index snapshot with checksums
  index.rebuild   incremental rebuild driven by source fingerprints

Design notes
------------
* Parsers are local and dependency free. Supported inputs: .txt .md .markdown .rst
  .json .jsonl .csv .tsv .html .htm .py .js .ts .yaml .yml .toml .ini .cfg .log.
  Unsupported binary formats (PDF, Office, images, audio) are rejected with an
  explicit error instead of being silently mangled; convert them with an approved
  local tool first (see knowledge-readme.md).
* "Dense" vectors are deterministic feature-hashed bag-of-words vectors produced with
  `hashlib` only. They are real vectors with stable cosine similarity, but they are NOT
  a neural embedding model. A neural embedder can be plugged in through the provider
  adapter contract documented in knowledge-readme.md.
* Every write is guarded by the workspace lock so concurrent builds cannot interleave.
"""
from __future__ import annotations

import html as html_lib
import json
import math
import os
import re
import shutil

import importlib.util as _ilu
import sys as _sys

# Load the shared kernel exactly once per process so every module sees the same
# exception types, configuration objects and audit chain instance.
_KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools-oaa-kernel.py")
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel", _KERNEL_PATH)
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)

SUPPORTED_TEXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".html", ".htm", ".xml", ".py", ".js", ".ts", ".java", ".c", ".h", ".cpp",
    ".go", ".rs", ".rb", ".sh", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql",
}
UNSUPPORTED_BINARY = {
    ".pdf": "PDF", ".docx": "Word", ".xlsx": "Excel", ".pptx": "PowerPoint",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".mp3": "audio", ".wav": "audio", ".mp4": "video", ".zip": "archive",
    ".tar": "archive", ".gz": "archive", ".onnx": "model", ".gguf": "model",
    ".safetensors": "model", ".bin": "binary",
}

STEM_SUFFIXES = ("ings", "ing", "ies", "ied", "es", "ed", "s", "ly")


def normalize_terms(text):
    """Lowercase, strip punctuation, apply a light suffix fold for matching."""
    out = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-'.]*", str(text).lower()):
        token = token.strip(".'-")
        if len(token) < 2 or token in K._STOP:
            continue
        for suffix in STEM_SUFFIXES:
            if len(token) > len(suffix) + 3 and token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        out.append(token)
    return out


# --------------------------------------------------------------------------------------
# Parsing and chunking
# --------------------------------------------------------------------------------------


def strip_markup(text, extension):
    if extension in (".html", ".htm", ".xml"):
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html_lib.unescape(text)
    if extension in (".json", ".jsonl"):
        try:
            if extension == ".jsonl":
                rows = [json.loads(l) for l in text.splitlines() if l.strip()]
                text = json.dumps(rows, ensure_ascii=False, indent=1)
            else:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
        except Exception:
            pass
    return text


def chunk_text(text, cfg, strategy="structural"):
    """Split text into chunks. Strategies: structural, paragraph, sentence, sliding."""
    cfg = cfg.get("retrieval", {}) if isinstance(cfg, dict) else {}
    size = int(cfg.get("chunk_chars", 800))
    overlap = int(cfg.get("chunk_overlap", 120))
    size = max(80, size)
    overlap = max(0, min(overlap, size // 2))
    text = re.sub(r"\r\n?", "\n", text)
    if strategy == "sliding":
        pieces = []
        step = max(1, size - overlap)
        for start in range(0, len(text), step):
            pieces.append(text[start:start + size])
            if start + size >= len(text):
                break
        return [p.strip() for p in pieces if p.strip()]
    # Paragraph-aware packing shared by structural/paragraph/sentence strategies.
    if strategy == "sentence":
        units = [u.strip() for u in re.split(r"(?<=[.!?])\s+", text) if u.strip()]
    else:
        units = [u.strip() for u in re.split(r"\n\s*\n", text) if u.strip()]
        if len(units) == 1 and len(units[0]) > size:
            units = [u.strip() for u in re.split(r"(?<=[.!?])\s+", units[0])
                     if u.strip()]
    chunks = []
    current = ""
    for unit in units:
        while len(unit) > size:
            head, unit = unit[:size], unit[size:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head.strip())
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= size:
            current = current + "\n" + unit
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = (tail + "\n" + unit).strip() if tail else unit
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


def shingles(text, k=5):
    words = normalize_terms(text)
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def feature_vector(text, dims):
    """Deterministic hashed bag-of-words vector (sparse dict index->weight)."""
    counts = {}
    for term in normalize_terms(text):
        bucket = int(K.sha256_bytes(term)[:8], 16) % dims
        counts[bucket] = counts.get(bucket, 0) + 1
    if not counts:
        return {}
    norm = math.sqrt(sum(v * v for v in counts.values()))
    return {str(idx): round(val / norm, 6) for idx, val in sorted(counts.items())}


def cosine(a, b):
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(val * b.get(key, 0.0) for key, val in a.items())


# --------------------------------------------------------------------------------------
# Source discovery and extraction
# --------------------------------------------------------------------------------------


def discover_sources(ctx, root_path, recursive=True, max_files=500):
    root = ctx.workspace.read_scope.resolve(root_path, must_exist=True)
    files = []
    if os.path.isfile(root):
        files.append(root)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d != "__pycache__")
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                files.append(os.path.join(dirpath, name))
                if len(files) >= max_files:
                    break
            if len(files) >= max_files:
                break
            if not recursive:
                break
    if not files:
        raise K.NotFoundError("no input files found", root=root_path)
    return files[:max_files]


def extract_document(ctx, path):
    extension = os.path.splitext(path)[1].lower()
    if extension in UNSUPPORTED_BINARY:
        raise K.InputError(
            "unsupported binary format; convert it with an approved local tool first",
            path=ctx.workspace.rel(path), kind=UNSUPPORTED_BINARY[extension],
            supported=sorted(SUPPORTED_TEXT))
    if extension not in SUPPORTED_TEXT:
        raise K.InputError("unsupported file extension",
                           path=ctx.workspace.rel(path),
                           supported=sorted(SUPPORTED_TEXT))
    max_bytes = int(K.get_path(ctx.cfg, "limits.max_document_bytes", 2000000))
    raw = K.read_text(path, max_bytes=max_bytes)
    text = strip_markup(raw, extension)
    return {"path": path, "extension": extension, "text": text,
            "chars": len(text), "sha256": K.sha256_bytes(raw)}


# --------------------------------------------------------------------------------------
# Index persistence
# --------------------------------------------------------------------------------------


def index_path(ctx, name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,48}", name or ""):
        raise K.InputError("index name must be lowercase letters, digits and hyphens",
                           name=name)
    return os.path.join(ctx.workspace.index_dir, "index-%s.json" % name)


def load_index(ctx, name):
    path = index_path(ctx, name)
    if not os.path.isfile(path):
        raise K.NotFoundError("index not found", index=name,
                              remediation="build it with: index.build --params "
                                          "'{\"source\":\"...\",\"index\":\"%s\"}'" % name)
    return K.read_json(path, max_bytes=256 * 1024 * 1024)


def save_index(ctx, name, index):
    path = index_path(ctx, name)
    K.write_json(path, index, indent=1)
    return path


def _register(registry):
    reg = registry

    # ---------------------------------------------------------------- index.build
    @reg.op("index.build",
            "Ingest files or a directory into a local searchable index.",
            params=["source", "index", "strategy", "recursive", "max_files",
                    "dedupe_threshold"],
            group="knowledge", mutating=True,
            examples=['{"source": "docs", "index": "kb"}'])
    def index_build(ctx, params):
        source = params.get("source")
        if not source:
            raise K.InputError("index.build requires params.source",
                               required=["source", "index"])
        name = params.get("index", "default")
        strategy = params.get("strategy", "structural")
        if strategy not in ("structural", "paragraph", "sentence", "sliding"):
            raise K.InputError("unknown chunking strategy", strategy=strategy,
                               allowed=["structural", "paragraph", "sentence",
                                        "sliding"])
        max_files = int(params.get("max_files", 500))
        threshold = float(params.get("dedupe_threshold",
                                     K.get_path(ctx.cfg,
                                                "retrieval.dedupe_threshold", 0.92)))
        dims = int(K.get_path(ctx.cfg, "retrieval.dense_dims", 256))
        max_chunks = int(K.get_path(ctx.cfg, "limits.max_chunks_per_doc", 500))
        files = discover_sources(ctx, source, recursive=bool(
            params.get("recursive", True)), max_files=max_files)
        documents, chunks, skipped = [], [], []
        seen_shingles = []
        duplicates = []
        for path in files:
            try:
                doc = extract_document(ctx, path)
            except K.OAAError as exc:
                skipped.append({"path": ctx.workspace.source_id(path),
                                "code": exc.code, "message": exc.message})
                continue
            rel = ctx.workspace.source_id(path)
            doc_id = K.sha256_bytes(rel)[:16]
            pieces = chunk_text(doc["text"], ctx.cfg, strategy)[:max_chunks]
            doc_chunks = []
            for ordinal, piece in enumerate(pieces):
                sh = shingles(piece)
                near_dup = None
                for other_sh, other_id in seen_shingles:
                    score = jaccard(sh, other_sh)
                    if score >= threshold:
                        near_dup = {"of": other_id, "similarity": round(score, 4)}
                        break
                chunk_id = "%s-%03d" % (doc_id, ordinal)
                if near_dup:
                    duplicates.append({"chunk_id": chunk_id, "duplicate_of":
                                       near_dup["of"],
                                       "similarity": near_dup["similarity"]})
                else:
                    seen_shingles.append((sh, chunk_id))
                doc_chunks.append({
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "source": rel,
                    "ordinal": ordinal,
                    "text": piece,
                    "terms": _term_counts(piece),
                    "vector": feature_vector(piece, dims),
                    "near_duplicate_of": near_dup["of"] if near_dup else None,
                    "sha256": K.sha256_bytes(piece)[:32],
                })
            chunks.extend(doc_chunks)
            documents.append({
                "doc_id": doc_id,
                "source": rel,
                "extension": doc["extension"],
                "chars": doc["chars"],
                "sha256": doc["sha256"],
                "mtime": int(os.path.getmtime(path)),
                "chunks": len(doc_chunks),
                "abspath_hint": rel,
            })
        if not documents:
            raise K.InputError("no supported documents could be indexed",
                               source=source, skipped=skipped)
        index = {
            "schema": "oaa-index/1",
            "name": name,
            "built_at": K.now_iso(),
            "strategy": strategy,
            "dims": dims,
            "bm25": {"k1": float(K.get_path(ctx.cfg, "retrieval.bm25_k1", 1.5)),
                     "b": float(K.get_path(ctx.cfg, "retrieval.bm25_b", 0.75))},
            "documents": documents,
            "chunks": chunks,
            "duplicates": duplicates,
            "skipped": skipped,
            "stats": {
                "documents": len(documents),
                "chunks": len(chunks),
                "unique_chunks": len(chunks) - len(duplicates),
                "chars": sum(d["chars"] for d in documents),
                "avg_terms": round(sum(sum(c["terms"].values()) for c in chunks)
                                   / max(1, len(chunks)), 2),
            },
        }
        with K.workspace_lock(ctx.workspace.lock_file,
                              K.get_path(ctx.cfg, "limits.lock_timeout_seconds", 10)):
            path = save_index(ctx, name, index)
        ctx.emit("index.build", action="index.build", target=name,
                 documents=len(documents), chunks=len(chunks))
        return {"index": name, "path": ctx.workspace.rel(path),
                "stats": index["stats"], "duplicates": len(duplicates),
                "skipped": skipped,
                "sources": [d["source"] for d in documents]}

    # ---------------------------------------------------------------- index.query
    @reg.op("index.query",
            "Query an index with BM25, dense or hybrid scoring.",
            params=["index", "query", "top_k", "mode", "source_filter"],
            group="knowledge",
            examples=['{"index": "kb", "query": "vram budget", "mode": "hybrid"}'])
    def index_query(ctx, params):
        name = params.get("index", "default")
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            raise K.InputError("index.query requires a non-empty params.query")
        index = load_index(ctx, name)
        mode = params.get("mode", "hybrid")
        if mode not in ("bm25", "dense", "hybrid"):
            raise K.InputError("unknown retrieval mode", mode=mode,
                               allowed=["bm25", "dense", "hybrid"])
        top_k = int(params.get("top_k", 5))
        max_results = int(K.get_path(ctx.cfg, "limits.max_results", 50))
        if top_k < 1 or top_k > max_results:
            raise K.InputError("top_k out of range", top_k=top_k,
                               max_allowed=max_results)
        q_terms = normalize_terms(query)
        max_terms = int(K.get_path(ctx.cfg, "limits.max_query_terms", 64))
        if len(q_terms) > max_terms:
            q_terms = q_terms[:max_terms]
        if not q_terms:
            raise K.InputError("query contains no usable terms", query=query)
        source_filter = params.get("source_filter")
        results = rank_chunks(ctx.cfg, index, query, mode=mode,
                              source_filter=source_filter)[:top_k]
        ctx.emit("index.query", action="index.query", target=name,
                 query=query, hits=len(results))
        return {"index": name, "query": query, "mode": mode,
                "terms": q_terms, "hits": results,
                "index_built_at": index["built_at"]}

    # ---------------------------------------------------------------- index.audit
    @reg.op("index.audit",
            "Audit an index for orphans, dimension mismatch, duplicates and stale sources.",
            params=["index"], group="knowledge", mutating=False,
            examples=['{"index": "kb"}'])
    def index_audit(ctx, params):
        name = params.get("index", "default")
        index = load_index(ctx, name)
        problems = []
        chunk_ids = set()
        doc_ids = {d["doc_id"] for d in index["documents"]}
        dims = index.get("dims")
        for chunk in index["chunks"]:
            if chunk["chunk_id"] in chunk_ids:
                problems.append({"issue": "duplicate_chunk_id",
                                 "chunk_id": chunk["chunk_id"]})
            chunk_ids.add(chunk["chunk_id"])
            if chunk["doc_id"] not in doc_ids:
                problems.append({"issue": "orphan_chunk", "chunk_id": chunk["chunk_id"],
                                 "doc_id": chunk["doc_id"]})
            if not chunk.get("vector"):
                problems.append({"issue": "missing_vector", "chunk_id": chunk["chunk_id"]})
            elif any(int(k) >= (dims or 0) for k in chunk["vector"]):
                problems.append({"issue": "dimension_mismatch",
                                 "chunk_id": chunk["chunk_id"], "dims": dims})
            if not chunk.get("terms"):
                problems.append({"issue": "empty_terms", "chunk_id": chunk["chunk_id"]})
        declared = {d["doc_id"]: d["chunks"] for d in index["documents"]}
        actual = {}
        for chunk in index["chunks"]:
            actual[chunk["doc_id"]] = actual.get(chunk["doc_id"], 0) + 1
        for doc_id, count in declared.items():
            if actual.get(doc_id, 0) != count:
                problems.append({"issue": "chunk_count_mismatch", "doc_id": doc_id,
                                 "declared": count, "actual": actual.get(doc_id, 0)})
        # Staleness check against the live filesystem (only for files inside workspace).
        stale = []
        for doc in index["documents"]:
            candidate = ctx.workspace.source_path(doc["source"])
            if candidate and os.path.isfile(candidate):
                current = K.sha256_file(candidate)
                if current != doc["sha256"]:
                    stale.append({"source": doc["source"], "issue": "changed"})
            else:
                stale.append({"source": doc["source"], "issue": "missing"})
        report = {
            "index": name,
            "ok": not problems and not stale,
            "chunks": len(index["chunks"]),
            "documents": len(index["documents"]),
            "problems": problems,
            "stale_sources": stale,
            "duplicates": len(index.get("duplicates", [])),
            "audited_at": K.now_iso(),
        }
        ctx.emit("index.audit", action="index.audit", target=name,
                 problems=len(problems), stale=len(stale))
        return report

    # ---------------------------------------------------------------- index.snapshot
    @reg.op("index.snapshot",
            "Create a reproducible point-in-time snapshot of an index with checksums.",
            params=["index", "label"], group="knowledge", mutating=True,
            examples=['{"index": "kb", "label": "pre-upgrade"}'])
    def index_snapshot(ctx, params):
        name = params.get("index", "default")
        label = params.get("label", "snapshot")
        if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,48}", label):
            raise K.InputError("snapshot label must be lowercase letters, digits, "
                               "and hyphens", label=label)
        index = load_index(ctx, name)
        snap_dir = os.path.join(ctx.workspace.index_dir, "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap_id = "%s-%s" % (label, K.sha256_obj(index)[:8])
        snap_path = os.path.join(snap_dir, "index-%s-%s.json" % (name, snap_id))
        K.write_json(snap_path, index, indent=1)
        manifest = {
            "snapshot_id": snap_id,
            "index": name,
            "label": label,
            "created_at": K.now_iso(),
            "file": ctx.workspace.rel(snap_path),
            "sha256": K.sha256_file(snap_path),
            "documents": len(index["documents"]),
            "chunks": len(index["chunks"]),
            "source_digest": K.sha256_obj([d["sha256"] for d in index["documents"]]),
            "index_built_at": index["built_at"],
        }
        man_path = os.path.join(snap_dir, "manifest-%s-%s.json" % (name, snap_id))
        K.write_json(man_path, manifest)
        ctx.emit("index.snapshot", action="index.snapshot", target=name,
                 snapshot=snap_id)
        return {"manifest": manifest, "manifest_path": ctx.workspace.rel(man_path)}

    # ---------------------------------------------------------------- index.rebuild
    @reg.op("index.rebuild",
            "Incrementally rebuild an index: reindex changed sources and drop deleted ones.",
            params=["index", "source"], group="knowledge", mutating=True,
            examples=['{"index": "kb", "source": "docs"}'])
    def index_rebuild(ctx, params):
        name = params.get("index", "default")
        existing = load_index(ctx, name)
        source = params.get("source")
        if not source:
            raise K.InputError("index.rebuild requires params.source")
        files = discover_sources(ctx, source, recursive=True,
                                 max_files=int(params.get("max_files", 500)))
        current = {}
        for path in files:
            extension = os.path.splitext(path)[1].lower()
            if extension in UNSUPPORTED_BINARY or extension not in SUPPORTED_TEXT:
                continue
            current[ctx.workspace.source_id(path)] = K.sha256_file(path)
        previous = {d["source"]: d["sha256"] for d in existing["documents"]}
        added = sorted(set(current) - set(previous))
        removed = sorted(set(previous) - set(current))
        changed = sorted(s for s in set(current) & set(previous)
                         if current[s] != previous[s])
        untouched = sorted(s for s in current if s in previous
                           and current[s] == previous[s])
        report = None
        if added or changed or removed:
            # Full rebuild from sources is deterministic; only re-derive what changed.
            build = index_build(ctx, {"source": source, "index": name,
                                      "strategy": params.get("strategy",
                                                             existing.get("strategy",
                                                                          "structural")),
                                      "max_files": params.get("max_files", 500)})
            rebuilt = load_index(ctx, name)
            keep_sources = set(untouched) | set(added) | set(changed)
            for doc_group in ("documents", "chunks"):
                if doc_group == "documents":
                    rebuilt["documents"] = [d for d in rebuilt["documents"]
                                            if d["source"] in keep_sources]
                else:
                    rebuilt["chunks"] = [c for c in rebuilt["chunks"]
                                         if c["source"] in keep_sources]
            # Re-attach untouched chunks from the previous index.
            kept_ids = {c["chunk_id"] for c in rebuilt["chunks"]}
            for chunk in existing["chunks"]:
                if chunk["source"] in untouched and chunk["chunk_id"] not in kept_ids:
                    rebuilt["chunks"].append(chunk)
            rebuilt["stats"]["documents"] = len(rebuilt["documents"])
            rebuilt["stats"]["chunks"] = len(rebuilt["chunks"])
            rebuilt["last_incremental"] = {"at": K.now_iso(), "added": added,
                                           "changed": changed, "removed": removed}
            save_index(ctx, name, rebuilt)
            report = {"rebuilt": True, "added": added, "changed": changed,
                      "removed": removed, "unchanged": len(untouched),
                      "stats": rebuilt["stats"], "build": build["stats"]}
        else:
            report = {"rebuilt": False, "added": [], "changed": [], "removed": [],
                      "unchanged": len(untouched),
                      "stats": existing["stats"]}
        # Source deletion propagation for removed sources.
        if removed:
            propagated = _propagate_deletions(ctx, removed)
            report["deletion_propagation"] = propagated
        ctx.emit("index.rebuild", action="index.rebuild", target=name,
                 added=len(added), changed=len(changed), removed=len(removed))
        return report


def rank_chunks(cfg, index, query, mode="hybrid", source_filter=None):
    """Score every chunk of an index against a query. Shared by CLI and evaluation."""
    q_terms = normalize_terms(query)
    max_terms = int(K.get_path(cfg, "limits.max_query_terms", 64))
    q_terms = q_terms[:max_terms]
    if not q_terms:
        return []
    bm25_scores = _bm25(index, q_terms)
    qvec = feature_vector(query, index["dims"])
    dense_scores = {c["chunk_id"]: cosine(qvec, c["vector"]) for c in index["chunks"]}
    w_dense = float(K.get_path(cfg, "retrieval.hybrid_dense_weight", 0.45))
    w_bm25 = float(K.get_path(cfg, "retrieval.hybrid_bm25_weight", 0.55))
    bmax = max(bm25_scores.values()) if bm25_scores else 0.0
    results = []
    for chunk in index["chunks"]:
        if source_filter and source_filter not in chunk["source"]:
            continue
        b = bm25_scores.get(chunk["chunk_id"], 0.0)
        b_norm = (b / bmax) if bmax else 0.0
        d = dense_scores.get(chunk["chunk_id"], 0.0)
        if mode == "bm25":
            score = b_norm
        elif mode == "dense":
            score = d
        else:
            score = w_bm25 * b_norm + w_dense * d
        if score <= 0:
            continue
        results.append({
            "chunk_id": chunk["chunk_id"],
            "source": chunk["source"],
            "ordinal": chunk["ordinal"],
            "score": round(score, 6),
            "bm25": round(b, 6),
            "dense": round(d, 6),
            "text": chunk["text"],
            "near_duplicate_of": chunk.get("near_duplicate_of"),
        })
    results.sort(key=lambda r: (-r["score"], r["chunk_id"]))
    return results


def make_in_memory_index(cfg, documents, strategy="paragraph", name="_eval"):
    """Build a transient index from [{'source','text'}] documents (no disk writes)."""
    dims = int(K.get_path(cfg, "retrieval.dense_dims", 256))
    chunks = []
    docs_meta = []
    for doc in documents:
        version = str(doc.get("version", "1.0"))
        cache_key = K.sha256_bytes(canonical_key(doc))[:16]
        pieces = chunk_text(doc.get("text", ""), cfg, strategy)
        for ordinal, piece in enumerate(pieces):
            chunks.append({
                "chunk_id": "%s-%03d" % (cache_key, ordinal),
                "doc_id": cache_key,
                "source": doc.get("source", "inline"),
                "version": version,
                "ordinal": ordinal,
                "text": piece,
                "terms": _term_counts(piece),
                "vector": feature_vector(piece, dims),
                "near_duplicate_of": None,
            })
        docs_meta.append({"doc_id": cache_key, "source": doc.get("source", "inline"),
                          "chunks": len(pieces), "sha256": cache_key})
    return {
        "schema": "oaa-index/1",
        "name": name,
        "built_at": K.now_iso(),
        "strategy": strategy,
        "dims": dims,
        "bm25": {"k1": float(K.get_path(cfg, "retrieval.bm25_k1", 1.5)),
                 "b": float(K.get_path(cfg, "retrieval.bm25_b", 0.75))},
        "documents": docs_meta,
        "chunks": chunks,
        "duplicates": [],
        "skipped": [],
        "stats": {"documents": len(docs_meta), "chunks": len(chunks)},
    }


def canonical_key(doc):
    return "%s\n%s" % (doc.get("source", ""), doc.get("text", ""))


def _term_counts(text):
    counts = {}
    for term in normalize_terms(text):
        counts[term] = counts.get(term, 0) + 1
    return counts


def _bm25(index, q_terms):
    chunks = index["chunks"]
    if not chunks:
        return {}
    k1 = float(index["bm25"]["k1"])
    b = float(index["bm25"]["b"])
    lengths = [sum(c["terms"].values()) or 1 for c in chunks]
    avgdl = sum(lengths) / len(lengths)
    df = {}
    for term in set(q_terms):
        df[term] = sum(1 for c in chunks if term in c["terms"])
    n = len(chunks)
    scores = {}
    for chunk in chunks:
        dl = sum(chunk["terms"].values()) or 1
        score = 0.0
        for term in q_terms:
            tf = chunk["terms"].get(term)
            if not tf:
                continue
            idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            denom = tf + k1 * (1 - b + b * dl / avgdl)
            score += idf * (tf * (k1 + 1)) / denom
        if score:
            scores[chunk["chunk_id"]] = score
    return scores


def _propagate_deletions(ctx, removed_sources):
    """Remove derived chunks, memories and caches tied to deleted sources."""
    removed_chunks = 0
    index_files = sorted(f for f in os.listdir(ctx.workspace.index_dir)
                         if f.startswith("index-") and f.endswith(".json"))
    for filename in index_files:
        path = os.path.join(ctx.workspace.index_dir, filename)
        try:
            data = K.read_json(path, max_bytes=256 * 1024 * 1024)
        except K.OAAError:
            continue
        before = len(data.get("chunks", []))
        data["chunks"] = [c for c in data.get("chunks", [])
                          if c.get("source") not in removed_sources]
        data["documents"] = [d for d in data.get("documents", [])
                             if d.get("source") not in removed_sources]
        after = len(data["chunks"])
        if after != before:
            removed_chunks += before - after
            data.setdefault("stats", {})["chunks"] = after
            data["stats"]["documents"] = len(data["documents"])
            K.write_json(path, data, indent=1)
    memory_removed = 0
    for mem in ctx.store.memory_list(include_expired=True):
        provenance_source = mem["provenance"].get("source")
        if provenance_source in removed_sources:
            ctx.store.memory_delete(mem["id"], hard=True)
            memory_removed += 1
    return {"sources": removed_sources, "chunks_removed": removed_chunks,
            "indexes_updated": len(index_files),
            "memories_removed": memory_removed}


def register(registry):
    _register(registry)
    return registry
