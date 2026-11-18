"""tools-rwt-citation.py - citation extraction, reference formatting, graphs and bibliography QA.

Works entirely on local text and the local store. Identifier resolution is a local cache only:
nothing is looked up on a network. Unresolvable identifiers are reported as unresolved.
"""

from __future__ import annotations

import json
import os
import re

NAME = "citation"
ROLE = "citation extraction/formatting, bibliographic normalization, citation graphs, bibliography QA"
GROUPS = [("citation", "Citation extraction, reference styles, identifier cache, graphs, bibliography QA")]
deps: dict = {}

STYLES = ("apa", "mla", "chicago", "ieee", "vancouver", "harvard", "oscola")
INLINE_PATTERNS = [
    ("author-year", re.compile(r"\(([A-Z][A-Za-z'\-]+(?:\s*(?:et al\.|and|&|,)\s*[A-Z]?[A-Za-z'\-]*)*),?\s*(\d{4}[a-z]?)(?:,\s*p{1,2}\.\s*([\w\-–]+))?\)")),
    ("narrative", re.compile(r"([A-Z][A-Za-z'\-]+(?:\s*(?:et al\.|and|&)\s*[A-Z]?[A-Za-z'\-]*)*)\s*\((\d{4}[a-z]?)(?:,\s*p{1,2}\.\s*([\w\-–]+))?\)")),
    ("numeric", re.compile(r"\[(\d{1,3}(?:\s*[-,–]\s*\d{1,3})*)\]")),
    ("footnote", re.compile(r"(?<![\w])(?:sup(?:er)?\s*)?(\d{1,3})\s*(?=\s+[A-Z])")),
    ("legal", re.compile(r"\b((?:Republic Act|R\.A\.|G\.R\.|Executive Order|E\.O\.|Art\.|Section|Sec\.)\s*(?:No\.\s*)?[\dA-Z][\w.\-]*)")),
    ("standard-clause", re.compile(r"\b((?:ISO|IEC|IEEE|PNS|WCAG|RFC)\s?[\d.\-]+(?:\s*(?:§|clause|Clause|Sec\.)\s*[\d.]+)?)")),
    ("doi", re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")),
]
ROLE_CUES = {
    "contrast": r"\b(however|by contrast|in contrast|disagree\w*|challenge[sd]?|contradict\w*|unlike|"
                r"whereas|criticiz\w*|fails? to)\b",
    "method": r"\b(method|protocol|instrument|sampl\w+|measure[ds]?|procedure|we (?:used|followed)|"
              r"adapted from|following)\b",
    "support": r"\b(found|shows?|demonstrat\w*|reports?|confirms?|consistent with|supports?|evidence)\b",
    "background": r"\b(in general|broadly|historically|widely|background|overview|has been|"
                  r"reviews?|literature)\b",
}


def _store():
    return deps["store"]


def _text(ctx, source_id, text, input_path):
    store = _store()
    if text:
        return text, {"kind": "inline"}
    if input_path:
        if not os.path.exists(input_path):
            raise store.ToolError(2, f"input not found: {input_path}")
        return store.read_text(input_path), {"kind": "file", "path": input_path}
    if source_id:
        rows = ctx.conn().execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",
                                  (source_id,)).fetchall()
        if not rows:
            raise store.ToolError(2, f"no stored segments for source: {source_id}")
        return "\n\n".join(r["text"] for r in rows), {"kind": "source", "source_id": source_id}
    raise store.ToolError(2, "provide --input, --text or --source-id")


def classify_role(sentence: str) -> str:
    for role, pattern in ROLE_CUES.items():
        if re.search(pattern, sentence, re.I):
            return role
    return "background"


def op_extract(ctx, source_id=None, text=None, input_path=None, store_records=False) -> dict:
    store = _store()
    body, provenance = _text(ctx, source_id, text, input_path)
    found = []
    for kind, regex in INLINE_PATTERNS:
        for m in regex.finditer(body):
            start = m.start()
            sent = _sentence_around(body, start)
            locator = None
            if kind in ("author-year", "narrative") and m.lastindex and m.lastindex >= 3:
                locator = m.group(3)
            found.append({"kind": kind, "raw": m.group(0), "ckey": canonical_key(kind, m),
                          "locator": locator, "context": sent, "role": classify_role(sent),
                          "start": start})
    found.sort(key=lambda x: x["start"])
    if store_records and provenance["kind"] == "source":
        conn = ctx.conn()
        conn.execute("DELETE FROM citations WHERE source_id=?", (source_id,))
        for item in found:
            conn.execute("INSERT INTO citations(source_id,segment_id,raw,ckey,kind,locator,context,role) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (source_id, None, item["raw"], item["ckey"], item["kind"],
                          item["locator"], item["context"], item["role"]))
        ctx.commit()
    by_kind, by_role = {}, {}
    for item in found:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_role[item["role"]] = by_role.get(item["role"], 0) + 1
    return {"status": "ok", "provenance": provenance, "citation_count": len(found),
            "by_kind": by_kind, "by_role": by_role, "citations": found[:200],
            "stored": bool(store_records and provenance["kind"] == "source")}


def canonical_key(kind: str, match) -> str:
    store = _store()
    if kind in ("author-year", "narrative"):
        author = re.sub(r"[^A-Za-z'\- ]", "", match.group(1))
        year = re.match(r"\d{4}", match.group(2)).group(0)
        return f"{store.slug(author.split()[0] if author else 'anon', 20)}-{year}"
    if kind == "numeric":
        return "num-" + re.sub(r"\s+", "", match.group(1))
    if kind == "doi":
        return "doi:" + match.group(1).lower()
    return f"{kind}-" + store.slug(match.group(0), 24)


def _sentence_around(body: str, index: int) -> str:
    store = _store()
    start = max(body.rfind(".", 0, index), body.rfind("\n\n", 0, index)) + 1
    end = body.find(".", index)
    end = len(body) if end == -1 else end + 1
    return store.normalize_text(body[start:end])[:400]


def _fmt_authors_apa(authors: list[str]) -> str:
    out = []
    for author in authors:
        author = (author or "").strip()
        if not author:
            continue
        if "," in author:  # already "Surname, Initials"
            surname, _, rest = author.partition(",")
            initials = " ".join(f"{word[0]}." for word in rest.replace(".", " ").split() if word)
            out.append(f"{surname.strip()}, {initials}".strip().rstrip(","))
            continue
        parts = author.split()
        surname = parts[-1]
        initials = " ".join(f"{word[0]}." for word in parts[:-1])
        out.append(f"{surname}, {initials}".strip().rstrip(","))
    if not out:
        return ""
    if len(out) == 1:
        return out[0]
    if len(out) <= 20:
        return ", ".join(out[:-1]) + ", & " + out[-1]
    return ", ".join(out[:19]) + ", ... " + out[-1]


def format_reference(ref: dict, style: str = "apa") -> str:
    store = _store()
    authors = ref.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r";|, and | & |\band\b", authors) if a.strip()]
    year = str(ref.get("year") or ref.get("issued") or "n.d.")
    title = (ref.get("title") or "Untitled").strip()
    container = (ref.get("container") or ref.get("venue") or "").strip()
    publisher = (ref.get("publisher") or "").strip()
    volume = str(ref.get("volume") or "").strip()
    issue = str(ref.get("issue") or "").strip()
    pages = str(ref.get("pages") or "").strip()
    doi = (ref.get("doi") or "").strip()
    url = (ref.get("url") or "").strip()
    edition = str(ref.get("edition") or "").strip()
    style = (style or "apa").lower()
    if style not in STYLES and not style.startswith("custom:"):
        raise store.ToolError(2, f"unsupported style '{style}'", {"styles": list(STYLES)})

    if style.startswith("custom:"):
        template = style.split(":", 1)[1]
        values = {**{k: "" for k in ("authors", "year", "title", "container", "publisher", "volume",
                                     "issue", "pages", "doi", "url", "edition")}, **{k: str(v or "") for k, v in ref.items()}}
        values["authors"] = _fmt_authors_apa(authors)
        try:
            return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), template).strip()
        except re.error as exc:
            raise store.ToolError(2, f"invalid custom template: {exc}")

    if style == "apa":
        parts = [_fmt_authors_apa(authors), f"({year}).", f"{title}."]
        if edition:
            parts.append(f"({edition} ed.).")
        if publisher:
            parts.append(f"{publisher}.")
        if container:
            parts.append(f"{container}" + (f", {volume}" if volume else "") +
                         (f"({issue})" if issue else "") + (f", {pages}" if pages else "") + ".")
        if doi:
            parts.append(f"https://doi.org/{doi}")
        elif url:
            parts.append(url)
        return " ".join(p for p in parts if p).strip()
    if style == "mla":
        author = authors[0] if authors else ""
        parts = [f"{author}." if author else "", f'"{title}."' if container else f"{title}."]
        if container:
            parts.append(f"{container}," + (f" vol. {volume}," if volume else "") +
                         (f" no. {issue}," if issue else "") + (f" pp. {pages}," if pages else ""))
        if publisher:
            parts.append(f"{publisher},")
        parts.append(f"{year}.")
        if doi:
            parts.append(f"doi:{doi}.")
        elif url:
            parts.append(f"{url}.")
        return " ".join(p for p in parts if p).strip()
    if style == "chicago":
        author = authors[0] if authors else ""
        parts = [f"{author}." if author else "", f"{title}."]
        if container:
            parts.append(f"{container}" + (f" {volume}" if volume else "") +
                         (f", no. {issue}" if issue else "") + (f" ({year})" if year else "") +
                         (f": {pages}" if pages else "") + ".")
        else:
            parts.append(f"{publisher + ',' if publisher else ''} {year}.")
        if doi:
            parts.append(f"https://doi.org/{doi}.")
        return " ".join(p for p in parts if p).strip()
    if style == "ieee":
        initials_first = []
        for author in authors:
            parts_name = author.replace(",", "").split()
            if parts_name:
                initials_first.append(" ".join(f"{p[0]}." for p in parts_name[:-1]) + " " + parts_name[-1])
        head = ", ".join(initials_first)
        if head:
            head = head.replace(", ", ", ", 1)
        tail = f'"{title},"'
        if container:
            tail += f" {container}," + (f" vol. {volume}," if volume else "") + \
                    (f" no. {issue}," if issue else "") + (f" pp. {pages}," if pages else "") + f" {year}."
        else:
            tail += f" {publisher}, {year}."
        return (head + ", " if head else "") + tail + (f" doi: {doi}." if doi else "")
    if style == "vancouver":
        head = ", ".join(authors[:6]) + (", et al" if len(authors) > 6 else "")
        tail = f"{title}. {container}" if container else f"{title}."
        if year:
            tail += f". {year}"
        if volume:
            tail += f";{volume}"
        if issue:
            tail += f"({issue})"
        if pages:
            tail += f":{pages}"
        tail += "."
        return (head + ". " if head else "") + tail + (f" doi:{doi}" if doi else "")
    if style == "harvard":
        head = ", ".join(authors) + (f" ({year})" if year else "")
        tail = f"'{title}'" if container else title
        if container:
            tail += f", {container}" + (f", {volume}" if volume else "") + (f"({issue})" if issue else "") + \
                    (f", pp. {pages}" if pages else "") + "."
        else:
            tail += f". {publisher}." if publisher else "."
        return (head + " " if head else "") + tail + (f" doi: {doi}." if doi else (f" Available at: {url}." if url else ""))
    # oscola
    head = ", ".join(authors) + (f" ({year})" if year else "")
    tail = title
    if container:
        tail += f" ({year}) {volume} {container} {pages}".rstrip()
    else:
        tail += f" ({publisher} {year})".rstrip()
    return (head + ", " if head else "") + tail + (f", doi:{doi}" if doi else "") + "."


def op_format(ctx, ref: str | None = None, ref_file: str | None = None, style: str = "apa",
              output: str | None = None, sort: bool = True) -> dict:
    store = _store()
    style = (style or "apa").lower()
    if style not in STYLES:
        raise store.ToolError(2, f"unsupported style '{style}'", {"styles": list(STYLES)})
    refs = []
    if ref_file:
        if not os.path.exists(ref_file):
            raise store.ToolError(2, f"reference file not found: {ref_file}")
        try:
            loaded = json.loads(store.read_text(ref_file))
        except json.JSONDecodeError as exc:
            raise store.ToolError(2, f"reference file must be JSON: {exc}")
        refs = loaded if isinstance(loaded, list) else [loaded]
    elif ref:
        try:
            loaded = json.loads(ref)
        except json.JSONDecodeError as exc:
            raise store.ToolError(2, f"--ref must be a JSON object: {exc}")
        refs = loaded if isinstance(loaded, list) else [loaded]
    else:
        raise store.ToolError(2, "provide --ref '<json>' or --ref-file <path>")
    formatted = []
    for item in refs:
        if not isinstance(item, dict):
            raise store.ToolError(2, "each reference must be a JSON object")
        formatted.append({"style": style, "text": format_reference(item, style),
                          "key": item.get("key") or store.slug(str(item.get("title", "ref")), 30)})
    if sort:
        formatted.sort(key=lambda x: x["text"].lower())
    payload = {"status": "ok", "style": style, "count": len(formatted), "references": formatted}
    if output:
        lines = "\n\n".join(f["text"] for f in formatted)
        payload["output"] = store.write_text(ctx.out_path(output), lines + "\n")
    return payload


def op_normalize(ctx, ref: str) -> dict:
    store = _store()
    try:
        item = json.loads(ref)
    except json.JSONDecodeError as exc:
        raise store.ToolError(2, f"--ref must be JSON: {exc}")
    if not isinstance(item, dict):
        raise store.ToolError(2, "--ref must be a JSON object")
    notes = []
    title = (item.get("title") or "").strip()
    if title and title == title.upper() and len(title) > 12:
        title = title.title()
        notes.append("title converted from all caps")
    if title and not title.endswith((".", "?", "!")):
        title_sentence = title[0].upper() + title[1:] if title else title
    else:
        title_sentence = title
    authors = item.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r";|, and | & |\band\b|\|", authors) if a.strip()]
    cleaned = []
    for author in authors:
        author = re.sub(r"\s+", " ", str(author)).strip(" ,;")
        if author and author not in cleaned:
            cleaned.append(author)
    year = None
    for candidate in (item.get("year"), item.get("issued"), item.get("date")):
        if candidate:
            m = re.search(r"((?:19|20)\d{2})", str(candidate))
            if m:
                year = m.group(1)
                break
    if item.get("year") and len(str(item.get("year"))) > 4:
        notes.append("year extracted from a longer date string")
    doi = item.get("doi") or ""
    if isinstance(doi, str) and doi:
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi.strip(), flags=re.I).lower()
    venue = (item.get("venue") or item.get("container") or "").strip()
    venue = re.sub(r"^(?:the|proceedings of the)\s+", "", venue, flags=re.I).strip()
    if item.get("venue") and item["venue"] != venue:
        notes.append("venue normalized (leading article/proceedings removed)")
    normalized = {"title": title_sentence, "authors": cleaned, "year": year, "doi": doi or None,
                  "venue": venue or None, "volume": item.get("volume"), "issue": item.get("issue"),
                  "pages": (item.get("pages") or "").strip() or None,
                  "publisher": (item.get("publisher") or "").strip() or None,
                  "type": item.get("type") or "article", "key": item.get("key")}
    return {"status": "ok", "normalized": normalized, "changes": notes}


def op_cache(ctx, action: str = "get", identifier: str | None = None, payload: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    if action == "get":
        if not identifier:
            raise store.ToolError(2, "--identifier is required for 'get'")
        key = identifier.strip().lower()
        row = conn.execute("SELECT value,created FROM cache WHERE kind='identifier' AND key=?",
                           (key,)).fetchone()
        if not row:
            return {"status": "ok", "identifier": identifier, "resolved": False,
                    "hint": "identifier is not in the local cache; resolve it on a connected host and "
                            "import it with 'citation cache --action put', never by network lookup here"}
        return {"status": "ok", "identifier": identifier, "resolved": True,
                "record": json.loads(row["value"]), "cached_at": row["created"]}
    if action == "put":
        if not identifier or not payload:
            raise store.ToolError(2, "--identifier and --payload are required for 'put'")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise store.ToolError(2, f"--payload must be JSON: {exc}")
        conn.execute("INSERT OR REPLACE INTO cache(kind,key,value,created) VALUES('identifier',?,?,?)",
                     (identifier.strip().lower(), json.dumps(data, ensure_ascii=False), store.now_iso()))
        ctx.commit()
        return {"status": "ok", "identifier": identifier, "stored": True}
    if action == "list":
        rows = [{"identifier": r["key"], "cached_at": r["created"]}
                for r in conn.execute("SELECT key,created FROM cache WHERE kind='identifier' ORDER BY key")]
        return {"status": "ok", "count": len(rows), "entries": rows[:200]}
    raise store.ToolError(2, "action must be get|put|list")


def op_validate(ctx, source_id: str | None = None, text_path: str | None = None) -> dict:
    """Bibliography completeness: every in-text citation resolves, every reference is cited."""
    store = _store()
    conn = ctx.conn()
    if text_path:
        body = store.read_text(text_path)
        citations = op_extract(ctx, text=body)["citations"]
        refs = parse_reference_list(body)
    else:
        if not source_id:
            raise store.ToolError(2, "provide --source-id or --text")
        rows = conn.execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",
                            (source_id,)).fetchall()
        if not rows:
            raise store.ToolError(2, f"no stored segments for source: {source_id}")
        body = "\n\n".join(r["text"] for r in rows)
        citations = op_extract(ctx, text=body)["citations"]
        refs = [dict(r) for r in conn.execute(
            "SELECT ckey,raw,title,authors,year FROM refs WHERE source_id=?", (source_id,))]
        if not refs:
            refs = parse_reference_list(body)
    ref_keys = {r.get("ckey") or r.get("key") for r in refs}
    cited_keys = {c["ckey"] for c in citations if c["kind"] in ("author-year", "narrative", "doi")}
    missing_refs = sorted(k for k in cited_keys if k and k not in ref_keys)
    uncited = sorted(k for k in ref_keys if k and k not in cited_keys)
    duplicates = {}
    for r in refs:
        key = r.get("ckey") or r.get("key")
        duplicates[key] = duplicates.get(key, 0) + 1
    dupes = sorted(k for k, n in duplicates.items() if n > 1 and k)
    malformed = [r.get("raw") or r.get("title") for r in refs
                 if not (r.get("year") or r.get("authors"))]
    ok = not missing_refs and not malformed and bool(citations)
    return {"status": "ok", "in_text_citations": len(citations), "references": len(refs),
            "missing_references": missing_refs, "uncited_references": uncited,
            "duplicate_references": dupes, "malformed_references": malformed[:10],
            "complete": ok,
            "note": "uncited references may be intentionally retained; record that decision in the decision log"}


def parse_reference_list(body: str) -> list[dict]:
    store = _store()
    refs = []
    section = re.search(r"\n#{0,6}\s*(References|Bibliography|Works Cited|Literature Cited)\s*\n(.*)$",
                        body, re.I | re.S)
    block = section.group(2) if section else ""
    if not block:
        return []
    entries = [e.strip() for e in re.split(r"\n(?=\s*(?:[A-Z][A-Za-z'\-]+,|[A-Z][A-Za-z'\-]+\s+[A-Z]\.|\[\d+\]))",
                                           block) if e.strip()]
    for raw in entries[:500]:
        flat = re.sub(r"\s+", " ", raw)
        year = re.search(r"\((\d{4}[a-z]?)\)|\b((?:19|20)\d{2})\b", flat)
        doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", flat)
        authors = flat.split("(")[0].split(".")[0] if "(" in flat else " ".join(flat.split()[:4])
        title = None
        quote = re.search(r'"([^"]{5,200})"', flat)
        if quote:
            title = quote.group(1)
        else:
            after_year = None
            match_year = re.search(r"\(\d{4}[a-z]?\)\.?\s*", flat)
            if match_year:
                remainder = flat[match_year.end():]
                after_year = re.split(r"\.\s", remainder)[0].strip()
            if after_year and len(after_year) > 8:
                title = after_year
            else:
                chunks = [c.strip() for c in flat.split(".") if len(c.strip()) > 12]
                title = chunks[0] if chunks else None
        year_val = (year.group(1) or (year.group(2) if year.lastindex and year.lastindex > 1 else None)) if year else None
        ckey = None
        first_author = re.match(r"([A-Z][A-Za-z'\-]+)", authors or "")
        if first_author and year_val:
            ckey = f"{store.slug(first_author.group(1), 20)}-{year_val[:4]}"
        refs.append({"raw": flat[:400], "authors": authors, "year": year_val, "title": title,
                     "doi": doi.group(0).lower() if doi else None, "ckey": ckey})
    return refs


def op_references(ctx, input_path: str, output: str | None = None) -> dict:
    store = _store()
    body = store.read_text(input_path)
    refs = parse_reference_list(body)
    payload = {"status": "ok", "count": len(refs), "references": refs,
               "note": "heuristic parse; confirm author/title boundaries before use"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def op_graph(ctx, source_id: str | None = None, output: str | None = None) -> dict:
    conn = ctx.conn()
    rows = [dict(r) for r in conn.execute("SELECT source_id,ckey,kind FROM citations")]
    if source_id:
        rows = [r for r in rows if r["source_id"] == source_id] or rows
    nodes = {}
    for r in rows:
        nodes.setdefault(r["source_id"], {"id": r["source_id"], "out": 0, "in": 0})
        if r["ckey"]:
            nodes.setdefault(r["ckey"], {"id": r["ckey"], "out": 0, "in": 0})
            nodes[r["ckey"]]["in"] += 1
            nodes[r["source_id"]]["out"] += 1
    edges = sorted({(r["source_id"], r["ckey"]) for r in rows if r["ckey"]})
    indeg = {k: v["in"] for k, v in nodes.items()}
    hubs = sorted((k for k, v in indeg.items() if v > 1), key=lambda k: -indeg[k])[:10]
    isolated = sorted(k for k, v in nodes.items() if v["in"] == 0 and v["out"] == 0)
    bridging = sorted(k for k, v in nodes.items() if v["in"] >= 1 and v["out"] >= 1)
    circular = sorted({k for k, ck in edges if ck and ck in nodes and
                       any(e[1] == k for e in edges if e[0] == ck)})
    payload = {"status": "ok", "nodes": len(nodes), "edges": len(edges),
               "heavily_cited": hubs, "bridging": bridging[:20], "isolated": isolated[:20],
               "potential_circular": circular,
               "note": "citation counts here are local-corpus counts, not global citation counts"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output),
                                             {"nodes": list(nodes.values()), "edges": edges})
    return payload


def op_selfcitation(ctx, author: str | None = None, venue: str | None = None) -> dict:
    """Flag concentrated self/in-group citation patterns for human review (not an accusation)."""
    conn = ctx.conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT c.source_id, c.ckey, s.title FROM citations c LEFT JOIN sources s ON s.id=c.source_id")]
    if not rows:
        return {"status": "ok", "flags": [], "note": "no stored citations; run 'citation extract --store'"}
    by_source = {}
    for r in rows:
        by_source.setdefault(r["source_id"], []).append(r["ckey"])
    flags = []
    for sid, keys in by_source.items():
        if author:
            hits = [k for k in keys if author.lower() in (k or "").lower()]
            share = len(hits) / (len(keys) or 1)
            if share >= 0.3:
                flags.append({"source_id": sid, "pattern": "author-self-citation", "share": round(share, 3),
                              "matches": len(hits), "total": len(keys)})
        if venue:
            hits = [k for k in keys if venue.lower() in (k or "").lower()]
            share = len(hits) / (len(keys) or 1)
            if share >= 0.3:
                flags.append({"source_id": sid, "pattern": "venue-concentration", "share": round(share, 3),
                              "matches": len(hits), "total": len(keys)})
    return {"status": "ok", "flags": flags,
            "review_required": bool(flags),
            "note": "concentration can be legitimate; a human reviewer decides"}


def op_disambiguate(ctx, kind: str = "author", name: str = "") -> dict:
    """Group name variants into candidate identities with evidence, never silently merging."""
    conn = ctx.conn()
    if kind == "author":
        rows = [dict(r) for r in conn.execute(
            "SELECT key, value FROM registry WHERE kind='models' UNION ALL "
            "SELECT ckey, authors FROM refs")]
        values = [r for r in rows if r.get("value")]
    else:
        rows = [dict(r) for r in conn.execute("SELECT ckey, authors FROM refs")]
        values = rows
    needle = (name or "").strip().lower()
    variants = {}
    for row in values:
        text = str(row.get("value") or row.get("authors") or "")
        for candidate in re.split(r";|\band\b|&", text):
            candidate = candidate.strip(" ,")
            if not candidate or (needle and needle not in candidate.lower()):
                continue
            surname = candidate.split(",")[0].split()[-1].lower() if candidate else ""
            variants.setdefault(surname, set()).add(candidate)
    groups = [{"surname": s, "variants": sorted(v), "needs_review": len(v) > 1,
               "orcid_present": any("orcid" in x.lower() for x in v)}
              for s, v in sorted(variants.items())]
    return {"status": "ok", "kind": kind, "filter": name, "groups": groups[:50],
            "note": "variants are grouped by surname only; ORCID/affiliation evidence is required "
                    "before merging identities"}


def register(groups, common):
    g = groups["citation"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    src = dict(source_id=(("--source-id", "--source"), dict(default=None)),
               text=(("--text",), dict(default=None)),
               input_path=(("--input", "-i"), dict(default=None)))
    sub("extract", "Extract inline, numeric, footnote, legal and standard citations with context",
        op_extract, store_records=(("--store",), dict(action="store_true")), **src)
    sub("format", "Format and validate references in a supported style", op_format,
        ref=(("--ref",), dict(default=None)), ref_file=(("--ref-file",), dict(default=None)),
        style=(("--style",), dict(default="apa")), output=(("--output", "-o"), dict(default=None)),
        sort=(("--no-sort",), dict(action="store_false", default=True)))
    sub("normalize", "Normalize bibliographic metadata", op_normalize,
        ref=(("--ref",), dict(required=True)))
    sub("cache", "Local identifier resolution cache (get|put|list)", op_cache,
        action=(("--action",), dict(default="get", choices=["get", "put", "list"])),
        identifier=(("--identifier",), dict(default=None)),
        payload=(("--payload",), dict(default=None)))
    sub("validate", "Bibliography completeness and consistency check", op_validate,
        source_id=(("--source-id", "--source"), dict(default=None)),
        text_path=(("--text-path",), dict(default=None)))
    sub("references", "Parse a reference list into structured records", op_references,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
    sub("graph", "Build the local citation graph", op_graph,
        source_id=(("--source-id", "--source"), dict(default=None)),
        output=(("--output", "-o"), dict(default=None)))
    sub("selfcitation", "Flag concentrated self/venue citation behaviour", op_selfcitation,
        author=(("--author",), dict(default=None)), venue=(("--venue",), dict(default=None)))
    sub("disambiguate", "Group author/venue name variants for human resolution", op_disambiguate,
        kind=(("--kind",), dict(default="author", choices=["author", "venue"])),
        name=(("--name",), dict(default="")))
