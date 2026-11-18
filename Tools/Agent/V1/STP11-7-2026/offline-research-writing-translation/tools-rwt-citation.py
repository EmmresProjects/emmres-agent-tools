#!/usr/bin/env python3
"""tools-rwt-citation.py - Citation extraction, resolution cache, formatting, checking.

Implements: inline citation extraction, local DOI/ISBN/ISSN/PMID/arXiv resolution
cache (no network), bibliography normalization, reference formatting in APA, MLA,
Chicago, IEEE, Vancouver, Harvard, OSCOLA, Bluebook-like and custom styles (from
asset-citation-styles.json), citation context extraction with cue-based use
classification, broken-reference detection (with ISBN/ISSN checksums), author
disambiguation scoring, venue normalization, citation-graph metrics (seminal,
isolated, cycles, bridges), self-citation/ring flags, and bibliography
completeness checking.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_ci", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_ci"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ISBN_RE = re.compile(r"ISBN(?:-1[03])?:?\s*([0-9Xx\- ]{10,17})")
ISSN_RE = re.compile(r"ISSN[: ]?\s*(\d{4}-\d{3}[\dXx])", re.I)
PMID_RE = re.compile(r"PMID:?\s*(\d{6,9})", re.I)
ARXIV_RE = re.compile(r"arXiv[: ]?\s*(\d{4}\.\d{4,5}(v\d+)?)", re.I)
PAREN_CITE_RE = re.compile(r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et al\.?|&|and)\s+[A-Za-z\-]+)*),\s*(\d{4}[a-z]?)(?:[,:]\s*p+\.?\s*\d+(?:-\d+)?)?\)")
BRACKET_NUM_RE = re.compile(r"\[(\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)\]")
FOOTNOTE_CITE_RE = re.compile(r"(?:^|\n)\s*(\d{1,3})\.\s+[A-Z]")

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

def isbn_normalize(raw):
    return re.sub(r"[^0-9Xx]", "", raw).upper()

def isbn10_check(isbn):
    if len(isbn) != 10:
        return None
    total = 0
    for i, ch in enumerate(isbn):
        if ch == "X":
            if i != 9:
                return False
            v = 10
        elif ch.isdigit():
            v = int(ch)
        else:
            return False
        total += v * (10 - i)
    return total % 11 == 0

def isbn13_check(isbn):
    if len(isbn) != 13 or not isbn.isdigit():
        return None
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn))
    return total % 10 == 0

def issn_check(issn):
    s = issn.replace("-", "").upper()
    if len(s) != 8:
        return False
    total = 0
    for i, ch in enumerate(s):
        if ch == "X" and i == 7:
            v = 10
        elif ch.isdigit():
            v = int(ch)
        else:
            return False
        total += v * (8 - i)
    return total % 11 == 0

def cmd_extract(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    found = []
    for m in PAREN_CITE_RE.finditer(text):
        found.append({"type": "author-year", "authors": m.group(1), "year": m.group(2),
                      "raw": m.group(0), "context": context_of(text, m.start())})
    for m in BRACKET_NUM_RE.finditer(text):
        found.append({"type": "numeric", "numbers": re.split(r"[,\s–-]+", m.group(1)),
                      "raw": m.group(0), "context": context_of(text, m.start())})
    for m in DOI_RE.finditer(text):
        found.append({"type": "doi", "value": m.group(0).lower(), "raw": m.group(0)})
    for m in ISBN_RE.finditer(text):
        found.append({"type": "isbn", "value": isbn_normalize(m.group(1)),
                      "valid": (isbn10_check(isbn_normalize(m.group(1))) if len(isbn_normalize(m.group(1))) == 10
                                else isbn13_check(isbn_normalize(m.group(1))))})
    for m in ISSN_RE.finditer(text):
        found.append({"type": "issn", "value": m.group(1).upper(), "valid": issn_check(m.group(1))})
    for m in PMID_RE.finditer(text):
        found.append({"type": "pmid", "value": m.group(1)})
    for m in ARXIV_RE.finditer(text):
        found.append({"type": "arxiv", "value": m.group(1)})
    for m in FOOTNOTE_CITE_RE.finditer(text):
        found.append({"type": "footnote-start", "marker": m.group(1), "context": context_of(text, m.start())})
    return {"file": args.file, "citations": found, "count": len(found)}

def context_of(text, pos, width=160):
    start = text.rfind(".", 0, pos) + 1
    end = text.find(".", pos)
    end = len(text) if end == -1 else end + 1
    sent = C.normalize_ws(text[start:end])
    return sent[:width]

def cmd_resolve(args):
    ws = _ws(args)
    recs = ws.list_records("references")
    index = {}
    for r in recs:
        for key in ("doi", "isbn", "issn", "pmid", "arxiv", "local_id"):
            v = r.get(key)
            if v:
                index[f"{key}:{str(v).lower()}"] = r
        for iid in r.get("identifiers", []) or []:
            index[f"local_id:{str(iid).lower()}"] = r
    key = f"{args.kind}:{args.identifier.strip().lower()}"
    hit = index.get(key)
    if hit:
        return {"identifier": args.identifier, "kind": args.kind, "resolved": True,
                "record": {k: v for k, v in hit.items() if k != "id"} | {"id": hit.get("id")}}
    return {"identifier": args.identifier, "kind": args.kind, "resolved": False,
            "record": None,
            "note": "Not in the local resolver cache. Offline resolution never reaches the network; "
                    "import the source's metadata to resolve it.",
            "unavailable": True}

def cmd_normalize(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = [items]
    out = []
    for it in items:
        title = C.normalize_ws(it.get("title", "")).rstrip(".")
        authors = it.get("authors", [])
        norm_authors = []
        for a in authors:
            if isinstance(a, str):
                a = a.strip()
                if "," in a:
                    parts = [p.strip() for p in a.split(",", 1)]
                    family, given = parts[0], parts[1] if len(parts) > 1 else ""
                else:
                    toks = a.split()
                    family, given = (toks[-1] if toks else ""), " ".join(toks[:-1])
                norm_authors.append({"family": family, "given": given})
            else:
                norm_authors.append({"family": a.get("family", "").strip(),
                                     "given": a.get("given", "").strip()})
        iso, uncertain = C.parse_date(it.get("issued") or it.get("year") or "")
        ptype = (it.get("type") or "article").lower()
        if ptype not in ("article", "book", "chapter", "preprint", "report", "thesis",
                         "webpage", "legal-case", "standard", "dataset", "proceedings"):
            ptype = "article"
        out.append({"title": title,
                    "authors": norm_authors,
                    "issued": iso, "issued_uncertain": uncertain,
                    "container": C.normalize_ws(it.get("container", "")),
                    "publisher": C.normalize_ws(it.get("publisher", "")),
                    "volume": it.get("volume"), "issue": it.get("issue"),
                    "pages": it.get("pages"), "edition": it.get("edition"),
                    "doi": (it.get("doi") or "").lower() or None,
                    "isbn": isbn_normalize(it["isbn"]) if it.get("isbn") else None,
                    "pmid": it.get("pmid"), "arxiv": it.get("arxiv"),
                    "type": ptype})
    if args.write:
        with open(args.file, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    return {"normalized": len(out), "records": out, "written": bool(args.write)}

def _author_apa(a):
    initials = " ".join(f"{p[0].upper()}." for p in a["given"].split() if p)
    return f"{a['family']}, {initials}".rstrip(", ")

def _author_mla(a):
    return f"{a['family']}, {a['given']}".rstrip(", ")

def _author_chicago(a):
    return f"{a['family']}, {a['given']}".rstrip(", ")

def _author_ieee(a):
    initials = " ".join(f"{p[0].upper()}." for p in a["given"].split() if p)
    return f"{initials} {a['family']}".strip()

def _author_vancouver(a):
    initials = "".join(p[0].upper() for p in a["given"].split() if p)
    return f"{a['family']} {initials}".strip()

def _author_oscola(a):
    return f"{a['given']} {a['family']}".strip()

def _author_bluebook(a):
    return f"{a['family']}, {a['given']}".rstrip(", ")

_AUTHOR_FORMAT = {"apa": _author_apa, "mla": _author_mla, "chicago": _author_chicago,
                  "ieee": _author_ieee, "vancouver": _author_vancouver, "harvard": _author_apa,
                  "oscola": _author_oscola, "bluebook": _author_bluebook, "custom": _author_apa}

def _format_authors(authors, style):
    fn = _AUTHOR_FORMAT[style]
    names = [fn(a) for a in authors if a.get("family") or a.get("given")]
    max_names = {"apa": 20, "harvard": 4, "mla": 2, "ieee": 6, "vancouver": 6,
                 "chicago": 10, "oscola": 5, "bluebook": 3, "custom": 6}[style]
    if len(names) > max_names:
        if style in ("apa", "harvard", "vancouver", "ieee", "chicago"):
            names = names[:max_names - 1] + ["et al."]
        else:
            names = [names[0], "et al."]
    if style == "apa" and len(names) == 2:
        return ", & ".join(names)
    if style == "chicago" and len(names) <= 3:
        return ", ".join(names[:-1]) + (", and " if len(names) > 1 else "") + names[-1]
    if style == "ieee":
        return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else (names[0] if names else "")
    if style == "vancouver":
        return ", ".join(names)
    return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + (names[-1] if names else "")

def cmd_format(args):
    styles = C.load_asset("asset-citation-styles.json")["styles"]
    style = args.style.lower()
    if style not in styles and style != "custom":
        raise C.ValidationError(f"Unknown style '{args.style}'. Available: {sorted(styles)} + 'custom'")
    with open(args.file, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = [items]
    out = []
    for it in items:
        authors = it.get("authors") or []
        a_str = _format_authors(authors, style)
        year = (it.get("issued") or "")[:4]
        title = it.get("title", "Untitled")
        container = it.get("container", "")
        vol_issue = ""
        if it.get("volume"):
            vol_issue = str(it["volume"])
            if it.get("issue"):
                vol_issue += f"({it['issue']})"
        pages = it.get("pages", "")
        doi = f" https://doi.org/{it['doi']}" if it.get("doi") else ""
        if style == "apa":
            s = f"{a_str} ({year}). {title}. {container + ', ' if container else ''}{vol_issue + ', ' if vol_issue else ''}{pages + '.' if pages else ''}{doi}"
        elif style == "mla":
            s = f"{a_str}. “{title}.” {container}{', vol. ' + vol_issue if vol_issue else ''}{', ' + pages if pages else ''}, {year}.{doi}"
        elif style == "chicago":
            s = f"{a_str}. “{title}.” {container} {vol_issue}{', no. ' + str(it['issue']) if it.get('issue') else ''} ({year}): {pages}.{doi}"
        elif style == "ieee":
            s = f"{a_str}, “{title},” {container}, vol. {vol_issue or '–'}, pp. {pages or '–'}, {year}.{doi}"
        elif style == "vancouver":
            s = f"{a_str}. {title}. {container}. {year};{vol_issue}:{pages}.{doi}"
        elif style == "harvard":
            s = f"{a_str} ({year}) {title}. {container}{', ' + vol_issue if vol_issue else ''}{', pp. ' + pages if pages else ''}.{doi}"
        elif style == "oscola":
            s = f"{a_str}, ‘{title}’ ({container + ' ' if container else ''}{year}){(' ' + pages) if pages else ''}.{doi}"
        elif style == "bluebook":
            s = f"{a_str}, {title}, {vol_issue + ' ' if vol_issue else ''}{container} ({year}){(', ' + pages) if pages else ''}.{doi}"
        else:  # custom: template-driven from asset
            tpl = styles.get("custom", {}).get("template", "{authors} ({year}). {title}. {container} {pages}. {doi}")
            s = tpl.format(authors=a_str, year=year, title=title, container=container,
                           pages=pages, doi=("https://doi.org/" + it["doi"]) if it.get("doi") else "")
        s = re.sub(r"\s+", " ", s).replace(" .", ".").strip()
        out.append({"id": it.get("id"), "style": style, "reference": s})
    return {"style": style, "formatted": out}

def cmd_check(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = [items]
    problems = []
    seen = {}
    for idx, it in enumerate(items):
        label = it.get("title") or it.get("doi") or f"entry {idx}"
        for field in ("title", "authors", "issued", "type"):
            if not it.get(field):
                problems.append({"entry": idx, "issue": f"missing required field: {field}", "entry_label": label})
        if it.get("doi") and not DOI_RE.fullmatch(str(it["doi"]).strip()):
            problems.append({"entry": idx, "issue": "malformed DOI", "value": it["doi"]})
        if it.get("isbn"):
            n = isbn_normalize(it["isbn"])
            ok = isbn10_check(n) if len(n) == 10 else isbn13_check(n) if len(n) == 13 else False
            if ok is False:
                problems.append({"entry": idx, "issue": "ISBN checksum failed", "value": it["isbn"]})
            elif ok is None:
                problems.append({"entry": idx, "issue": "ISBN must be 10 or 13 digits", "value": it["isbn"]})
        if it.get("issn") and not issn_check(it["issn"]):
            problems.append({"entry": idx, "issue": "ISSN checksum failed", "value": it["issn"]})
        key = (C.normalize_ws(str(it.get("title", "")).lower()), (it.get("issued") or "")[:4])
        if key[0]:
            if key in seen:
                problems.append({"entry": idx, "issue": "duplicate entry", "duplicates_with": seen[key]})
            else:
                seen[key] = idx
    return {"entries": len(items), "problems": problems, "ok": not problems}

def cmd_context(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    results = []
    for m in PAREN_CITE_RE.finditer(text):
        sent = context_of(text, m.start(), 400)
        low = sent.lower()
        if any(w in low for w in ("contrary", "however", "whereas", "dispute", "contradicts", "against")):
            use = "contrast"
        elif any(w in low for w in ("method", "protocol", "procedure", "sample was", "using the")):
            use = "method"
        elif any(w in low for w in ("found", "show", "demonstrated", "results indicate", "reported that")):
            use = "support"
        else:
            use = "background"
        results.append({"raw": m.group(0), "context": sent, "use_classification": use,
                        "review_required": True})
    for m in BRACKET_NUM_RE.finditer(text):
        sent = context_of(text, m.start(), 400)
        results.append({"raw": m.group(0), "context": sent, "use_classification": "unclassified",
                        "review_required": True})
    return {"citations": results, "count": len(results),
            "note": "Use classifications are cue-word heuristics; a human reviewer confirms support vs contrast."}

def _load_graph(args):
    with open(args.graph, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    edges = data.get("edges") or []
    nodes = set(data.get("nodes") or [])
    for e in edges:
        nodes.add(e.get("from")); nodes.add(e.get("to"))
    cites = defaultdict(set)
    reverse = defaultdict(set)
    meta = {}
    for rec in data.get("records", []) or []:
        meta[rec.get("id")] = rec
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a and b:
            cites[a].add(b)
            reverse[b].add(a)
    return nodes, cites, reverse, meta

def cmd_graph(args):
    nodes, cites, reverse, meta = _load_graph(args)
    out_deg = {n: len(cites.get(n, ())) for n in nodes}
    in_deg = {n: len(reverse.get(n, ())) for n in nodes}
    return {"nodes": len(nodes), "edges": sum(out_deg.values()),
            "top_cited": sorted(({"id": n, "cited_by": d} for n, d in in_deg.items()),
                                key=lambda x: -x["cited_by"])[:10],
            "isolated": sorted([n for n in nodes if in_deg[n] == 0 and out_deg[n] == 0]),
            "in_degree": in_deg, "out_degree": out_deg}

def cmd_analyze(args):
    nodes, cites, reverse, _meta = _load_graph(args)
    in_deg = {n: len(reverse.get(n, ())) for n in nodes}
    max_in = max(in_deg.values()) if in_deg else 0
    seminal = [n for n, d in in_deg.items() if d >= max(3, max_in * 0.6)]
    isolated = [n for n in nodes if in_deg[n] == 0 and not cites.get(n)]
    recent_cut = args.recent_after
    recent = [n for n in nodes if (n or "")[:4] >= str(recent_cut)] if recent_cut else []
    # simple bridge detection: nodes whose removal increases components (sampled for small graphs)
    bridges = []
    if len(nodes) <= 300:
        def components(exclude=None):
            seen, count = set(), 0
            for start in nodes:
                if start == exclude or start in seen:
                    continue
                stack, count = [start], count + 1
                while stack:
                    n = stack.pop()
                    for m in cites.get(n, ()):
                        if m != exclude and m not in seen:
                            seen.add(m); stack.append(m)
            return count
        base = components()
        for n in list(nodes):
            if components(exclude=n) > base:
                bridges.append(n)
    # cycles (directed)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    cycles = []
    def dfs(u):
        color[u] = GRAY
        for v in cites.get(u, ()):
            if color.get(v) == WHITE:
                dfs(v)
            elif color.get(v) == GRAY:
                cycles.append([v, u])
        color[u] = BLACK
    for n in list(nodes):
        if color[n] == WHITE:
            dfs(n)
    return {"seminal": sorted(seminal), "isolated": sorted(isolated),
            "bridges": sorted(bridges),
            "directed_cycles": cycles[:25],
            "heavily_cited": sorted(({"id": n, "cited_by": d} for n, d in in_deg.items()),
                                    key=lambda x: -x["cited_by"])[:10],
            "note": "Cycle detection sampled on graphs up to 300 nodes; review flagged cycles manually (they may be legitimate)."}

def cmd_selfcite(args):
    nodes, cites, reverse, meta = _load_graph(args)
    author_of = {n: (meta.get(n, {}).get("first_author") or meta.get(n, {}).get("author")) for n in nodes}
    journal_of = {n: meta.get(n, {}).get("container") or meta.get(n, {}).get("journal") for n in nodes}
    total, self_links = 0, 0
    ring = Counter()
    for a, outs in cites.items():
        for b in outs:
            total += 1
            if author_of.get(a) and author_of.get(a) == author_of.get(b):
                self_links += 1
                ring[author_of[a]] += 1
            elif journal_of.get(a) and journal_of.get(a) == journal_of.get(b):
                ring[f"journal:{journal_of[a]}"] += 1
    rate = self_links / total if total else 0.0
    flagged = [{"entity": k, "self_citations": v} for k, v in ring.most_common() if v >= args.min_self_cites]
    return {"total_citations": total, "self_citations": self_links,
            "self_citation_rate": round(rate, 4),
            "flagged_entities": flagged,
            "threshold": args.min_self_cites,
            "note": "Concentration flags route to human review; legitimate self-citation exists. This is not an accusation."}

def cmd_completeness(args):
    with open(args.text, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(args.references, "r", encoding="utf-8") as fh:
        refs = json.load(fh)
    if isinstance(refs, dict):
        refs = refs.get("references", [])
    cited = []
    for m in PAREN_CITE_RE.finditer(text):
        yr = m.group(2)[:4]
        cited.append({"family": m.group(1).split(",")[0].split(" and ")[0].strip(), "year": yr, "raw": m.group(0)})
    ref_keys = []
    for r in refs:
        authors = r.get("authors") or []
        fam = ""
        if authors:
            a0 = authors[0]
            fam = (a0.get("family") if isinstance(a0, dict) else str(a0)).split(",")[0].strip()
        ref_keys.append({"family": (fam or "").lower(), "year": str(r.get("issued", ""))[:4],
                         "title": r.get("title", "")})
    unmatched = []
    for c in cited:
        ok = any(c["family"].lower().startswith(rk["family"][:6]) and c["year"] == rk["year"]
                 for rk in ref_keys if rk["family"])
        if not ok:
            unmatched.append(c)
    used_years = {c["year"] for c in cited}
    listed_uncited = [rk for rk in ref_keys
                      if not any(c["family"].lower().startswith(rk["family"][:6]) and c["year"] == rk["year"]
                                 for c in cited) if rk["family"]]
    return {"in_text_citations": len(cited), "reference_entries": len(ref_keys),
            "cited_but_missing_from_bibliography": unmatched,
            "listed_but_never_cited": listed_uncited,
            "ok": not unmatched,
            "note": "Author-year matching is heuristic; retain intentionally-uncited entries via --keep-listed."}

VENUES = {
    "plos one": "PLoS ONE", "plos neglected tropical diseases": "PLoS NTD",
    "nature": "Nature", "science": "Science", "lancet": "The Lancet",
    "new england journal of medicine": "N Engl J Med", "nejm": "N Engl J Med",
    "jama": "JAMA", "bmj": "BMJ", "annals of internal medicine": "Ann Intern Med",
    "ieee": "IEEE", "acm": "ACM",
    "supreme court rapporteur": "SCR", "philippine reports": "Phil. Rep.",
    "official gazette": "Official Gazette", "iso": "ISO", "iec": "IEC", "ansi": "ANSI",
}

def cmd_venue(args):
    name = C.normalize_ws(args.name).lower()
    hit = VENUES.get(name)
    entry = {"input": args.name, "normalized": hit or C.normalize_ws(args.name),
             "matched": bool(hit)}
    if args.map and os.path.isfile(args.map):
        with open(args.map, "r", encoding="utf-8") as fh:
            extra = json.load(fh)
        low = {k.lower(): v for k, v in extra.items()}
        if name in low:
            entry = {"input": args.name, "normalized": low[name], "matched": True, "source": "user-map"}
    return entry

def cmd_disambiguate(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        people = json.load(fh)
    if isinstance(people, dict):
        people = people.get("authors", [])
    clusters = []
    for p in people:
        placed = False
        for cl in clusters:
            rep = cl["members"][0]
            name_sim = C.sim_ratio((p.get("family", "") + p.get("given", "")).lower(),
                                   (rep.get("family", "") + rep.get("given", "")).lower())
            same_orcid = p.get("orcid") and rep.get("orcid") and p["orcid"] == rep["orcid"]
            same_aff = (p.get("affiliation") and rep.get("affiliation") and
                        C.sim_ratio(str(p["affiliation"]).lower(), str(rep["affiliation"]).lower()) > 0.7)
            shared_co = set(p.get("coauthors", [])) & set(rep.get("coauthors", []))
            score = (0.6 if same_orcid else 0) + 0.25 * name_sim + (0.1 if same_aff else 0) + min(0.05, 0.02 * len(shared_co))
            if same_orcid or score >= args.threshold:
                cl["members"].append(p); cl["score"] = round(score, 3); placed = True; break
        if not placed:
            clusters.append({"representative": p, "members": [p], "score": 1.0})
    out = [{"cluster_id": f"A{i+1}", "size": len(cl["members"]),
             "names": sorted({f"{m.get('given','')} {m.get('family','')}".strip() for m in cl["members"]}),
             "orcids": sorted({m.get("orcid") for m in cl["members"] if m.get("orcid")}),
             "merge_confidence": cl["score"]} for i, cl in enumerate(clusters)]
    return {"clusters": out, "review_required": True,
            "note": "Candidate merges only; a human confirms identity merges. ORCID match is decisive when present."}

COMMANDS = {
    "extract": cmd_extract, "resolve": cmd_resolve, "normalize": cmd_normalize,
    "format": cmd_format, "check": cmd_check, "context": cmd_context,
    "graph": cmd_graph, "analyze": cmd_analyze, "selfcite": cmd_selfcite,
    "completeness": cmd_completeness, "venue": cmd_venue, "disambiguate": cmd_disambiguate,
}

def build_parser(prog="rwt citation"):
    p = argparse.ArgumentParser(prog=prog, description="Citation engines: extract, resolve-cache, format, check, graph, completeness.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("extract", help="Extract author-year, numeric, DOI/ISBN/ISSN/PMID/arXiv citations from a text file")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("resolve", help="Resolve identifiers against the LOCAL cache only (offline)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--kind", required=True,
                    choices=["doi", "isbn", "issn", "pmid", "arxiv", "local_id", "report", "patent"])
    sp.add_argument("--identifier", required=True); sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("normalize", help="Normalize reference records (authors, dates ISO, identifiers)")
    sp.add_argument("--file", required=True); sp.add_argument("--write", action="store_true")
    sp.set_defaults(func=cmd_normalize)

    sp = sub.add_parser("format", help="Format references (APA, MLA, Chicago, IEEE, Vancouver, Harvard, OSCOLA, Bluebook, custom)")
    sp.add_argument("--file", required=True); sp.add_argument("--style", default="apa")
    sp.set_defaults(func=cmd_format)

    sp = sub.add_parser("check", help="Detect broken/duplicate/malformed references (incl. ISBN/ISSN checksums)")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("context", help="Capture citing sentence + heuristic use classification")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_context)

    for name, fn, helptext in [("graph", cmd_graph, "Build in/out degree maps from an edge list JSON"),
                               ("analyze", cmd_analyze, "Find seminal, isolated, bridge and circular citation patterns"),
                               ("selfcite", cmd_selfcite, "Flag self-citation concentration for human review")]:
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--graph", required=True, help="JSON: {nodes?, edges:[{from,to}], records?}")
        if name == "analyze":
            sp.add_argument("--recent-after", type=int, default=0)
        if name == "selfcite":
            sp.add_argument("--min-self-cites", type=int, default=3)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("completeness", help="Cross-check in-text citations against the bibliography")
    sp.add_argument("--text", required=True); sp.add_argument("--references", required=True)
    sp.set_defaults(func=cmd_completeness)

    sp = sub.add_parser("venue", help="Normalize venue names/abbreviations (built-in table + optional --map JSON)")
    sp.add_argument("--name", required=True); sp.add_argument("--map", default=None)
    sp.set_defaults(func=cmd_venue)

    sp = sub.add_parser("disambiguate", help="Cluster same/similar authors (ORCID decisive) for human confirmation")
    sp.add_argument("--file", required=True, help="JSON list of {family, given, orcid?, affiliation?, coauthors?[]}")
    sp.add_argument("--threshold", type=float, default=0.8)
    sp.set_defaults(func=cmd_disambiguate)
    return p

def run(argv):
    parser = build_parser(prog="rwt citation")
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
