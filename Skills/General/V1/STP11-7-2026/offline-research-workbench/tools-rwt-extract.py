"""tools-rwt-extract.py - entity, relation, claim, event, quantity, date and media extraction.

Everything here is deterministic, rule/gazetteer based and runs fully offline. Extraction is
assistive: records are stored with source coordinates and a status, and every record must be
approved by a human through `extract review` before it is treated as verified evidence.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import date, timedelta

NAME = "extract"
ROLE = "entity/relation/claim/event/quantity/date extraction plus transcript and table handling"
GROUPS = [("extract", "Extraction of entities, claims, events, quantities, dates, media and tables")]
deps: dict = {}

ORG_SUFFIX = r"(?:University|Universidad|College|Institute|Agency|Ministry|Department|Bureau|Commission|Council|Court|Foundation|Committee|Corporation|Company|Inc\.?|Ltd\.?|LLC|Association|Organization|Organisation|Bank|Hospital|School|Office|Authority|Board|Assembly|Parliament|Congress|Senate|Tribunal|Society|Union|Party|Center|Centre|Group|Publishing|Press)"
ORG_RE = re.compile(rf"\b((?:[A-Z][\w.&'-]+\s+){{0,4}}{ORG_SUFFIX})\b")
PERSON_RE = re.compile(r"\b((?:Dr\.|Prof\.|Atty\.|Mr\.|Ms\.|Mrs\.|Justice|Judge|Sen\.|Rep\.|Sec\.)\s+)?"
                       r"([A-Z][a-z'\-]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+)?)\b")
PLACE_GAZETTEER = {
    "philippines", "manila", "quezon city", "las pinas", "las piñas", "cebu", "davao", "mindanao",
    "luzon", "visayas", "asean", "european union", "united states", "united kingdom", "japan",
    "china", "india", "indonesia", "malaysia", "thailand", "vietnam", "singapore", "australia",
    "germany", "france", "spain", "brazil", "south africa", "kenya", "nigeria", "mexico",
}
LAW_RE = re.compile(r"\b((?:Republic Act|R\.A\.|RA|Batas Pambansa|Executive Order|E\.O\.|Administrative Order|"
                    r"Presidential Decree|P\.D\.|Ordinance|City Ordinance|Article|Art\.|Section|Sec\.)\s+"
                    r"(?:No\.\s*)?[\dA-Z][\w.\-]*)")
STANDARD_RE = re.compile(r"\b((?:ISO|IEC|IEEE|ANSI|BS|DIN|JIS|PNS|ASTM|W3C|WCAG|RFC|EN)\s?(?:/IEC\s?)?\d[\d.\-]*)")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
DATE_RES = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                r"\s+(\d{1,2}),?\s+(\d{4})\b", re.I), "month_d_y"),
    (re.compile(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|"
                r"November|December)\s+(\d{4})\b", re.I), "d_month_y"),
    (re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                r"\s+(\d{4})\b", re.I), "month_y"),
    (re.compile(r"\b((?:19|20)\d{2})s\b"), "decade"),
    (re.compile(r"\b((?:19|20)\d{2})\b"), "year"),
]
RELATIVE_RE = re.compile(r"\b(last|next|this|previous)\s+(week|month|year|quarter|decade)\b", re.I)
QUANTITY_RE = re.compile(r"(?<![\w.])([<>≈~]?\s?-?\d[\d,]*(?:\.\d+)?)\s*"
                         r"(%|percent|percentage points?|pp|PHP|USD|EUR|GBP|JPY|₱|\$|€|£|"
                         r"thousand|million|billion|trillion|kg|km|mi|m|cm|mm|ha|hectares?|"
                         r"years?|months?|days?|hours?|persons?|people|households?|respondents?|"
                         r"cases?|doses?|tonnes?|tons?|degrees?|MW|kW|GB|MB)(?![A-Za-z])", re.I)
RANGE_RE = re.compile(r"(\d[\d,.]*)\s*(?:-|–|to|and)\s*(\d[\d,.]*)")
CAUSAL_CUES = re.compile(r"\b(because|due to|caused|leads? to|results? in|as a result|therefore|"
                         r"attributable|drives?|driven by|effect of|impacts?)\b", re.I)
PREDICT_CUES = re.compile(r"\b(will|would|likely|expected to|projected|forecast|may|might|could)\b", re.I)
NORMATIVE_CUES = re.compile(r"\b(should|must|ought|required to|shall|need to|recommend)\b", re.I)
NEGATION_CUES = re.compile(r"\b(not|no|never|none|without|cannot|can't|doesn't|didn't|isn't|wasn't|"
                           r"fail(?:s|ed)? to|contrary)\b", re.I)
HEDGE_CUES = re.compile(r"\b(may|might|could|suggests?|appears?|approximately|about|possibly|"
                        r"likely|uncertain|limited evidence)\b", re.I)
METHOD_CUES = {
    "design": r"\b(randomi[sz]ed controlled trial|RCT|cohort|case-control|cross-sectional|survey|"
              r"interviews?|focus group|ethnograph\w*|case study|quasi-experimental|meta-analys\w*|"
              r"systematic review|difference-in-differences|regression discontinuity)\b",
    "participants": r"\b(participants?|respondents?|subjects?|patients?|households?|n\s*=\s*\d+)\b",
    "sampling": r"\b(random sampl\w+|purposive|stratified|cluster sampl\w+|convenience|snowball)\b",
    "instruments": r"\b(questionnaire|interview guide|scale|instrument|assay|sensor|codebook)\b",
    "analysis": r"\b(regression|thematic analysis|content analysis|ANOVA|chi-square|t-test|"
                r"difference-in-means|multilevel model|survival analysis|Bayesian)\b",
    "outcomes": r"\b(primary outcome|secondary outcome|outcome measure|endpoint|dependent variable)\b",
    "preregistration": r"\b(pre-?regist\w+|OSF|ClinicalTrials\.gov|registered report)\b",
    "limitations": r"\b(limitations?|caveat|not generali[sz]able|small sample)\b",
}
FUNDER_RE = re.compile(r"\b(funded by|supported by|grant (?:no\.?|number)|award(?:ed)? (?:no\.?|number)|"
                       r"grant from)\s+([^.;\n]{3,120})", re.I)
COI_RE = re.compile(r"\b(conflicts? of interest|competing interests?|declaration of interest|"
                    r"financial disclosure)\b[^.]{0,240}", re.I)
DEFINITION_RES = [
    re.compile(r'"([^"]{2,60})"\s+(?:means|refers to|is defined as)\s+([^.;\n]{5,240})', re.I),
    re.compile(r"\b([A-Z][\w\- ]{2,40}?)\s+(?:means|refers to|is defined as|denotes)\s+([^.;\n]{5,240})"),
    re.compile(r"\b([a-z][\w\- ]{2,40}?)\s*\(([^)]{5,160})\)"),
]


def _store():
    return deps["store"]


def _resolve_text(ctx, source_id, text, input_path):
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
            raise store.ToolError(2, f"no segments stored for source: {source_id}")
        return "\n\n".join(r["text"] for r in rows), {"kind": "source", "source_id": source_id}
    raise store.ToolError(2, "provide --input, --text or --source-id")


def sentence_offsets(text: str) -> list[dict]:
    store = _store()
    out, cursor = [], 0
    for sent in store.sentences(text):
        idx = text.find(sent, cursor)
        if idx < 0:
            idx = cursor
        out.append({"text": sent, "start": idx, "end": idx + len(sent)})
        cursor = idx + len(sent)
    return out


# ---------------------------------------------------------------- entities

def op_entities(ctx, source_id=None, text=None, input_path=None, store_records=False) -> dict:
    store = _store()
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    found = []

    def add(mention, etype, start):
        found.append({"mention": mention.strip(), "etype": etype, "start": start,
                      "end": start + len(mention.strip()),
                      "stable_id": f"{etype}:{store.slug(mention, 48)}"})

    for m in ORG_RE.finditer(body):
        add(m.group(1), "organization", m.start(1))
    for m in PERSON_RE.finditer(body):
        name = m.group(2)
        head = name.split()[0].lower()
        if head in ("the", "this", "these", "those", "a", "an"):
            continue
        if any(name.lower() == p for p in PLACE_GAZETTEER):
            continue
        add(name, "person", m.start(2))
    for place in PLACE_GAZETTEER:
        for m in re.finditer(rf"\b{re.escape(place)}\b", body, re.I):
            add(m.group(0), "place", m.start())
    for m in LAW_RE.finditer(body):
        add(m.group(1), "law", m.start(1))
    for m in STANDARD_RE.finditer(body):
        add(m.group(1), "standard", m.start(1))
    for m in DOI_RE.finditer(body):
        add(m.group(1), "identifier", m.start(1))
    for regex, dtype in DATE_RES:
        for m in regex.finditer(body):
            add(m.group(0), "date", m.start())
    for m in QUANTITY_RE.finditer(body):
        add(m.group(0), "quantity", m.start())

    seen, unique = set(), []
    for item in sorted(found, key=lambda x: (x["start"], x["etype"])):
        key = (item["etype"], item["mention"].lower(), item["start"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    if store_records and provenance["kind"] == "source":
        conn = ctx.conn()
        conn.execute("DELETE FROM entities WHERE source_id=?", (source_id,))
        for item in unique:
            conn.execute("""INSERT INTO entities(source_id,segment_id,mention,etype,start_off,end_off,
                stable_id,confidence) VALUES(?,?,?,?,?,?,?,?)""",
                         (source_id, None, item["mention"], item["etype"], item["start"],
                          item["end"], item["stable_id"], 0.7))
        ctx.commit()
    by_type = {}
    for item in unique:
        by_type.setdefault(item["etype"], 0)
        by_type[item["etype"]] += 1
    return {"status": "ok", "provenance": provenance, "entity_count": len(unique),
            "by_type": by_type, "entities": unique[:200],
            "stored": bool(store_records and provenance["kind"] == "source"),
            "note": "rule/gazetteer extraction; approve records with 'extract review' before citing"}


def op_link(ctx, mention: str, limit: int = 10) -> dict:
    conn = ctx.conn()
    key = mention.strip().lower()
    rows = [dict(r) for r in conn.execute(
        "SELECT mention, etype, stable_id, count(*) n FROM entities "
        "WHERE lower(mention)=? OR lower(mention) LIKE ? GROUP BY stable_id ORDER BY n DESC LIMIT ?",
        (key, f"%{key}%", limit))]
    variants = [dict(r) for r in conn.execute(
        "SELECT DISTINCT mention FROM entities WHERE lower(mention) LIKE ? LIMIT 20", (f"%{key[:6]}%",))]
    return {"status": "ok", "mention": mention, "candidates": rows,
            "candidate_count": len(rows), "variant_mentions": [v["mention"] for v in variants],
            "ambiguous": len(rows) > 1,
            "note": "ambiguity is preserved: a human must choose the intended identity"}


def op_relations(ctx, source_id=None, text=None, input_path=None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    patterns = [
        ("affiliation", re.compile(r"([A-Z][\w'\-]+(?:\s+[A-Z][\w'\-]+){0,3})\s*(?:,|of|at)\s*"
                                   rf"((?:[A-Z][\w.&'-]+\s+){{0,4}}{ORG_SUFFIX})")),
        ("funding", re.compile(r"(?:funded by|grant from)\s+([^.;\n]{3,90})", re.I)),
        ("authorship", re.compile(r"(?:written by|authored by|prepared by)\s+([A-Z][^.;\n]{2,80})", re.I)),
        ("amendment", re.compile(r"([A-Za-z .]{2,40}?)\s+(?:is amended by|amends|amended by)\s+"
                                 r"([A-Za-z .()\d]{2,60})", re.I)),
        ("cites", re.compile(r"([A-Z][\w'\- ]{2,60}?)\s+\((?:19|20)\d{2}\)\s+(?:found|argues?|shows?|reports?)", re.I)),
        ("comparison", re.compile(r"([\w\- ]{2,40})\s+(?:compared (?:with|to)|versus|vs\.)\s+([\w\- ]{2,40})", re.I)),
        ("causation", re.compile(r"([\w\- ]{2,60}?)\s+(?:causes?|leads? to|results? in|increases?|reduces?)\s+([\w\- ]{2,60})", re.I)),
        ("ownership", re.compile(r"([A-Z][\w'\- ]{2,40}?)\s+(?:owns|owned by|subsidiary of)\s+([\w&'\- ]{2,60})", re.I)),
    ]
    out = []
    for rtype, regex in patterns:
        for m in regex.finditer(body):
            out.append({"rtype": rtype, "subject": m.group(1).strip(), "object": m.group(2).strip(),
                        "start": m.start()})
    if source_id and out:
        conn = ctx.conn()
        for item in out:
            conn.execute("INSERT INTO relations(source_id,segment_id,subject,rtype,object,confidence) "
                         "VALUES(?,?,?,?,?,?)",
                         (source_id, None, item["subject"], item["rtype"], item["object"], 0.6))
        ctx.commit()
    counts = {}
    for item in out:
        counts[item["rtype"]] = counts.get(item["rtype"], 0) + 1
    return {"status": "ok", "provenance": provenance, "relation_count": len(out),
            "by_type": counts, "relations": out[:100]}


# ---------------------------------------------------------------- claims

def classify_claim(text: str) -> str:
    if QUANTITY_RE.search(text):
        return "quantitative"
    if CAUSAL_CUES.search(text):
        return "causal"
    if PREDICT_CUES.search(text) and re.search(r"\b(will|expected|projected|forecast)\b", text, re.I):
        return "predictive"
    if NORMATIVE_CUES.search(text):
        return "normative"
    if re.search(r"\b(means|refers to|is defined as)\b", text, re.I):
        return "definitional"
    return "factual"


def op_claims(ctx, source_id=None, text=None, input_path=None, store_records=False) -> dict:
    store = _store()
    if store_records and not any((source_id, text, input_path)):
        # no explicit target: store claims for every source in the corpus
        conn = ctx.conn()
        rows = [r["id"] for r in conn.execute("SELECT id FROM sources ORDER BY id")]
        if not rows:
            raise store.ToolError(2, "no sources in the corpus; ingest a source first")
        results, total = [], 0
        for sid in rows:
            one = op_claims(ctx, source_id=sid, store_records=True)
            results.append({"source_id": sid, "claim_count": one["claim_count"]})
            total += one["claim_count"]
        return {"status": "ok", "provenance": {"kind": "corpus", "sources": rows},
                "claim_count": total, "by_source": results, "claims": [],
                "stored": True, "note": "claims are extraction candidates, not verified facts"}
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    claims = []
    for item in sentence_offsets(body):
        sent = item["text"]
        if store.words(sent) < 5 or sent.lstrip().startswith(("#", "|", ">")):
            continue
        if sent.rstrip().endswith("?") and not QUANTITY_RE.search(sent):
            continue
        if not re.search(r"\b(?:is|are|was|were|has|have|had|does|do|did|will|would|can|must|should|"
                         r"may|finds?|found|show|shows|report|reports|indicates?|means?|includes?|"
                         r"publishes?|retracts?|requires?|recommends?)\b", sent, re.I) and not QUANTITY_RE.search(sent):
            continue
        ctype = classify_claim(sent)
        claims.append({"text": sent, "ctype": ctype, "start": item["start"], "end": item["end"],
                       "negated": bool(NEGATION_CUES.search(sent)),
                       "hedged": bool(HEDGE_CUES.search(sent)),
                       "confidence": 0.55 if (NEGATION_CUES.search(sent) or HEDGE_CUES.search(sent)) else 0.7,
                       "id": f"clm-{store.sha256_text(sent)[:12]}"})
    if store_records and provenance["kind"] == "source":
        conn = ctx.conn()
        for c in claims:
            conn.execute("""INSERT OR REPLACE INTO claims(id,source_id,segment_id,text,ctype,confidence,
                status,created) VALUES(?,?,?,?,?,?,'extracted',?)""",
                         (c["id"], source_id, None, c["text"], c["ctype"], c["confidence"],
                          store.now_iso()))
        ctx.commit()
    by_type = {}
    for c in claims:
        by_type[c["ctype"]] = by_type.get(c["ctype"], 0) + 1
    return {"status": "ok", "provenance": provenance, "claim_count": len(claims),
            "by_type": by_type, "claims": claims[:200],
            "stored": bool(store_records and provenance["kind"] == "source"),
            "note": "claims are extraction candidates, not verified facts"}


def op_evidence_link(ctx, claim_id: str, limit: int = 5) -> dict:
    store = _store()
    conn = ctx.conn()
    row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise store.ToolError(2, f"unknown claim id: {claim_id} (run 'extract claims --store' first)")
    claim = dict(row)
    retrieval = deps["retrieval"]
    result = retrieval.op_hybrid(ctx, claim["text"], limit=limit, diversify=False)
    stored = []
    for hit in result["results"]:
        seg = store.get_segment(conn, hit["segment_id"])
        relation = classify_support(claim["text"], seg["text"] if seg else "")
        score = float(hit["score"])
        conn.execute("INSERT INTO claim_evidence(claim_id,source_id,segment_id,relation,score,created) "
                     "VALUES(?,?,?,?,?,?)",
                     (claim_id, hit["source_id"], hit["segment_id"], relation, score, store.now_iso()))
        stored.append({"segment_id": hit["segment_id"], "source_id": hit["source_id"],
                       "relation": relation, "score": round(score, 6),
                       "snippet": hit.get("snippet", "")[:240]})
    ctx.commit()
    relations = {}
    for item in stored:
        relations[item["relation"]] = relations.get(item["relation"], 0) + 1
    return {"status": "ok", "claim": {"id": claim_id, "text": claim["text"], "ctype": claim["ctype"]},
            "links": stored, "relation_counts": relations,
            "advisory": ("no supporting passage found; record the claim as unsupported and abstain"
                         if not any(s["relation"] == "support" for s in stored) else
                         "support is lexical/negation-based; a reviewer must confirm the claim")}


def classify_support(claim: str, passage: str) -> str:
    store = _store()
    claim_terms = {t for t in store.tokens(claim) if t not in store.STOPWORDS["en"] and len(t) > 3}
    passage_terms = set(store.tokens(passage))
    if not claim_terms:
        return "insufficient"
    coverage = len(claim_terms & passage_terms) / len(claim_terms)
    claim_neg = bool(NEGATION_CUES.search(claim))
    passage_neg = bool(NEGATION_CUES.search(passage))
    if coverage < 0.34:
        return "context"
    if claim_neg != passage_neg and coverage >= 0.5:
        return "oppose"
    return "support" if coverage >= 0.5 else "context"


def op_events(ctx, source_id=None, text=None, input_path=None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    events = []
    for item in sentence_offsets(body):
        sent = item["text"]
        when = normalize_dates(sent)["expressions"]
        actor = ORG_RE.search(sent) or PERSON_RE.search(sent)
        action = re.search(r"\b(retracted|published|amended|repealed|approved|signed|filed|"
                           r"announced|reported|launched|suspended|convicted|ordered|found|"
                           r"released|ruled|imposed|sued|investigated)\b", sent, re.I)
        if not (action and (when or actor)):
            continue
        location = next((p for p in PLACE_GAZETTEER if re.search(rf"\b{re.escape(p)}\b", sent, re.I)), None)
        events.append({"event_id": f"evt-{_store().sha256_text(sent)[:12]}",
                       "event_type": action.group(1).lower(),
                       "actor": actor.group(0).strip() if actor else None,
                       "time": when[0]["normalized"] if when else None,
                       "time_uncertainty": when[0]["certainty"] if when else "unstated",
                       "location": location,
                       "text": sent, "start": item["start"],
                       "reported_consequences": bool(re.search(r"\b(as a result|consequently|"
                                                               r"following which|leading to)\b", sent, re.I))})
    events.sort(key=lambda e: e["start"])
    for i, event in enumerate(events, 1):
        event["sequence"] = i
    return {"status": "ok", "provenance": provenance, "event_count": len(events),
            "events": events[:100],
            "note": "event time is the event date when stated; reporting date must be tracked separately"}


def normalize_dates(text: str, reference: str | None = None) -> dict:
    out = []
    ref_date = None
    if reference:
        try:
            ref_date = date.fromisoformat(reference)
        except ValueError:
            raise _store().ToolError(2, f"reference date must be YYYY-MM-DD, got: {reference}")
    for regex, kind in DATE_RES:
        for m in regex.finditer(text):
            raw = m.group(0)
            normalized, certainty = raw, "exact"
            try:
                if kind == "iso":
                    normalized = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
                elif kind == "month_d_y":
                    normalized = date(int(m.group(3)), _month(m.group(1)), int(m.group(2))).isoformat()
                elif kind == "d_month_y":
                    normalized = date(int(m.group(3)), _month(m.group(2)), int(m.group(1))).isoformat()
                elif kind == "month_y":
                    normalized = f"{int(m.group(2)):04d}-{_month(m.group(1)):02d}"
                    certainty = "month"
                elif kind == "decade":
                    start = int(m.group(1))
                    normalized = f"{start}-{start + 9}"
                    certainty = "range"
                else:
                    normalized = m.group(1)
                    certainty = "year"
            except ValueError:
                certainty = "unparseable"
            out.append({"raw": raw, "normalized": normalized, "certainty": certainty,
                        "kind": kind, "start": m.start()})
    for m in RELATIVE_RE.finditer(text):
        base = ref_date
        normalized, certainty = None, "relative"
        if base:
            direction = -1 if m.group(1).lower() in ("last", "previous") else (0 if m.group(1).lower() == "this" else 1)
            unit = m.group(2).lower()
            days = {"week": 7, "month": 30, "quarter": 91, "year": 365, "decade": 3650}[unit]
            target = base + timedelta(days=direction * days)
            normalized = target.isoformat()
            certainty = "derived-from-reference"
        out.append({"raw": m.group(0), "normalized": normalized, "certainty": certainty,
                    "kind": "relative", "start": m.start(),
                    "requires_reference_date": normalized is None})
    out.sort(key=lambda x: x["start"])
    return {"expressions": out, "count": len(out),
            "unresolved_relative": [x["raw"] for x in out if x.get("requires_reference_date")]}


def _month(name: str) -> int:
    months = ["january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december"]
    return months.index(name.lower()) + 1


def op_dates(ctx, text: str | None = None, input_path: str | None = None,
             reference: str | None = None, source_id: str | None = None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    result = normalize_dates(body, reference)
    return {"status": "ok", "provenance": provenance, **result}


def op_quantities(ctx, text: str | None = None, input_path: str | None = None,
                  source_id: str | None = None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    items = []
    for m in QUANTITY_RE.finditer(body):
        raw_value = m.group(1).replace(",", "").replace(" ", "")
        unit = m.group(2)
        try:
            value = float(raw_value.lstrip("<>≈~"))
        except ValueError:
            value = None
        context = body[max(0, m.start() - 80):m.end() + 80].replace("\n", " ")
        denominator = re.search(r"\b(per|out of|among)\s+([\d,]+(?:\s*(?:million|thousand|billion))?)",
                                context, re.I)
        items.append({"raw": m.group(0), "value": value, "unit": unit,
                      "scale": raw_value if value is None else "",
                      "comparator": raw_value[0] if raw_value[:1] in "<>≈~" else None,
                      "denominator": denominator.group(0) if denominator else None,
                      "context": context.strip()})
    ranges = [{"raw": m.group(0), "low": m.group(1), "high": m.group(2)}
              for m in RANGE_RE.finditer(body)]
    return {"status": "ok", "provenance": provenance, "count": len(items),
            "quantities": items[:200], "ranges": ranges[:50],
            "note": "values are extracted, not recomputed; use 'verify numbers' to recheck arithmetic"}


def op_methodology(ctx, source_id: str | None = None, text: str | None = None,
                   input_path: str | None = None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    fields, missing = {}, []
    for name, pattern in METHOD_CUES.items():
        hits = sorted({m.group(0).lower() for m in re.finditer(pattern, body, re.I)})
        if hits:
            fields[name] = hits[:10]
        else:
            missing.append(name)
    return {"status": "ok", "provenance": provenance, "fields": fields,
            "missing_fields": missing,
            "completeness": round(1 - len(missing) / len(METHOD_CUES), 4),
            "note": "absent cues are not proof of absence; confirm against the full text"}


def op_funding(ctx, source_id: str | None = None, text: str | None = None,
               input_path: str | None = None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    funders = sorted({m.group(2).strip(" ,;") for m in FUNDER_RE.finditer(body)})
    grants = re.findall(r"\b(?:grant|award)\s*(?:no\.?|number|#)?\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", body, re.I)
    coi = [m.group(0).strip() for m in COI_RE.finditer(body)]
    declared_none = bool(re.search(r"\bno (?:competing|conflicts? of) interests?\b", body, re.I))
    return {"status": "ok", "provenance": provenance, "funders": funders, "grant_ids": sorted(set(grants)),
            "conflict_statements": coi[:10],
            "declared_none": declared_none,
            "disclosure_complete": bool(funders) or declared_none or bool(coi)}


def op_definitions(ctx, source_id: str | None = None, text: str | None = None,
                   input_path: str | None = None) -> dict:
    body, provenance = _resolve_text(ctx, source_id, text, input_path)
    out, seen = [], set()
    for regex in DEFINITION_RES:
        for m in regex.finditer(body):
            term, definition = m.group(1).strip(), m.group(2).strip()
            key = (term.lower(), definition[:40].lower())
            if key in seen or len(term) < 2:
                continue
            seen.add(key)
            out.append({"term": term, "definition": definition, "start": m.start(),
                        "pattern": "explicit" if "means" in m.group(0).lower() or "defined" in m.group(0).lower() else "parenthetical"})
    return {"status": "ok", "provenance": provenance, "count": len(out), "definitions": out[:100],
            "note": "explicit definitions only; implicit usage needs 'translation terms' review"}


def op_review(ctx, kind: str, ref: str, decision: str, note: str = "", actor: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    if kind not in ("entity", "claim", "relation"):
        raise store.ToolError(2, "kind must be entity|claim|relation")
    if decision not in ("approve", "reject", "merge", "split", "correct"):
        raise store.ToolError(2, "decision must be approve|reject|merge|split|correct")
    actor = actor or ctx.actor
    status = {"approve": "approved", "reject": "rejected", "merge": "merged",
              "split": "split", "correct": "corrected"}[decision]
    if kind == "claim":
        row = conn.execute("SELECT id FROM claims WHERE id=?", (ref,)).fetchone()
        if not row:
            raise store.ToolError(2, f"unknown claim: {ref}")
        conn.execute("UPDATE claims SET status=? WHERE id=?", (status, ref))
    elif kind == "entity":
        row = conn.execute("SELECT count(*) c FROM entities WHERE stable_id=?", (ref,)).fetchone()
        if not row["c"]:
            raise store.ToolError(2, f"unknown entity stable_id: {ref}")
        conn.execute("UPDATE entities SET confidence=? WHERE stable_id=?",
                     (1.0 if decision == "approve" else 0.0, ref))
    else:
        row = conn.execute("SELECT count(*) c FROM relations WHERE subject=? OR rtype=?", (ref, ref)).fetchone()
        if not row["c"]:
            raise store.ToolError(2, f"no relations matched: {ref}")
    store.audit_append(conn, actor, f"extract.review.{decision}", f"{kind}:{ref}", {"note": note})
    ctx.commit()
    return {"status": "ok", "kind": kind, "ref": ref, "decision": decision, "record_status": status,
            "reviewer": actor, "note": note}


# ---------------------------------------------------------------- transcripts and media

TIMECODE_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_subtitles(body: str) -> list[dict]:
    cues, current = [], None
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n")):
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith("WEBVTT"):
            lines = lines[1:]
            if not lines:
                continue
        match = TIMECODE_RE.search(" ".join(lines[:2]))
        if match:
            g = [int(x) for x in match.groups()]
            current = {"start_ms": ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3],
                       "end_ms": ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]}
            text_start = 1 if lines[0] == match.group(0) or TIMECODE_RE.match(lines[0]) else 2
            current["text"] = " ".join(lines[text_start:])
        elif current and not current.get("text"):
            current["text"] = " ".join(lines)
        else:
            continue
        speaker = re.match(r"^([A-Z][A-Z .'-]{1,30}):\s*", current.get("text", ""))
        current["speaker"] = speaker.group(1).strip() if speaker else None
        if speaker:
            current["text"] = current["text"][speaker.end():]
        cues.append(current)
    return [c for c in cues if c.get("text")]


def op_transcript(ctx, input_path: str, output: str | None = None, clean: bool = True) -> dict:
    store = _store()
    body = store.read_text(input_path)
    cues = parse_subtitles(body)
    if not cues:
        raise store.ToolError(2, "no subtitle cues found (expected SRT or WebVTT timecodes)")
    if clean:
        for cue in cues:
            text = re.sub(r"\[(?:inaudible|music|laughter|applause)\]", "", cue["text"], flags=re.I)
            text = re.sub(r"\b(?:um+|uh+|erm+)\b[,]?\s*", "", text, flags=re.I)
            cue["text"] = re.sub(r"\s{2,}", " ", text).strip()
    speakers = sorted({c["speaker"] for c in cues if c["speaker"]})
    out = ctx.out_path(output or f"transcript-{store.slug(os.path.basename(input_path))}.json")
    store.write_json(out, {"source_file": os.path.basename(input_path), "cues": cues,
                           "speakers": speakers, "duration_ms": cues[-1]["end_ms"]})
    return {"status": "ok", "output": out, "cues": len(cues), "speakers": speakers,
            "duration_ms": cues[-1]["end_ms"],
            "low_confidence_hint": "speaker labels absent in cues are unreviewed; route names and numbers to review"}


def op_timecode_cite(ctx, source_id: str, start_ms: int, end_ms: int | None = None,
                     output: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,start_ms,end_ms,text,locator FROM segments WHERE source_id=? AND start_ms IS NOT NULL "
        "ORDER BY start_ms", (source_id,))]
    if not rows:
        raise store.ToolError(2, f"no timecoded segments for source {source_id}; ingest a transcript first")
    end_ms = end_ms or start_ms
    selected = [r for r in rows if r["end_ms"] >= start_ms and r["start_ms"] <= end_ms]
    if not selected:
        raise store.ToolError(2, "no transcript segment overlaps the requested time range")
    quote = " ".join(r["text"] for r in selected)
    citation = f"[{source_id} @ {_fmt_ms(selected[0]['start_ms'])}-{_fmt_ms(selected[-1]['end_ms'])}]"
    payload = {"status": "ok", "source_id": source_id, "quote": quote, "citation": citation,
               "segments": [r["id"] for r in selected],
               "start_ms": selected[0]["start_ms"], "end_ms": selected[-1]["end_ms"]}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def _fmt_ms(ms: int) -> str:
    s, frac = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{frac:03d}"


def op_ocr_eval(ctx, reference_path: str, hypothesis_path: str, review_queue: bool = False) -> dict:
    """Character/word error rates for OCR or speech output against a verified reference."""
    store = _store()
    ref = store.read_text(reference_path)
    hyp = store.read_text(hypothesis_path)
    cer, cer_ops = _edit_rate(list(ref), list(hyp))
    wer, wer_ops = _edit_rate(ref.split(), hyp.split())
    payload = {"status": "ok", "cer": round(cer, 4), "wer": round(wer, 4),
               "reference_chars": len(ref), "hypothesis_chars": len(hyp),
               "operations": {"char": cer_ops, "word": wer_ops}}
    if review_queue:
        queue = deps["workflow"]
        item = queue.add_review(ctx, kind="ocr-quality", ref=os.path.basename(hypothesis_path),
                                priority="high" if wer > 0.1 else "normal",
                                note=f"WER {wer:.3f} CER {cer:.3f} exceeds automatic acceptance"
                                     if wer > 0.05 else "recorded for audit")
        payload["review_item"] = item["review_id"]
    return payload


def _edit_rate(ref: list, hyp: list) -> tuple[float, dict]:
    n, m = len(ref), len(hyp)
    if n == 0:
        return (1.0 if m else 0.0), {"substitutions": 0, "insertions": m, "deletions": 0}
    prev = list(range(m + 1))
    ops_prev = [{"substitutions": 0, "insertions": j, "deletions": 0} for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        ops_cur = [{"substitutions": 0, "insertions": i, "deletions": 0}] + [None] * m
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur[j] = prev[j - 1]
                ops_cur[j] = dict(ops_prev[j - 1])
            else:
                candidates = [(prev[j - 1] + 1, "substitutions"), (prev[j] + 1, "deletions"),
                              (cur[j - 1] + 1, "insertions")]
                cost, op = min(candidates, key=lambda c: c[0])
                cur[j] = cost
                base = ops_prev[j - 1] if op == "substitutions" else (ops_prev[j] if op == "deletions" else ops_cur[j - 1])
                ops_cur[j] = {**base, op: base[op] + 1}
        prev, ops_prev = cur, ops_cur
    return prev[m] / max(1, n), ops_prev[m]


def op_tables(ctx, input_path: str, output: str | None = None) -> dict:
    """Parse markdown pipe tables or CSV into structured rows with caption/notes detection."""
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    body = store.read_text(input_path)
    tables = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            caption = None
            for k in range(max(0, i - 3), i):
                m = re.match(r"^\s*(?:Table|Figure)\s+(\d+)[:.]\s*(.+)$", lines[k], re.I)
                if m:
                    caption = {"number": m.group(1), "text": m.group(2).strip()}
            notes = [ln.strip() for ln in lines[j:j + 4] if ln.strip().lower().startswith(("note", "*", "source"))]
            tables.append({"index": len(tables) + 1, "header": header, "rows": rows,
                           "row_count": len(rows), "column_count": len(header),
                           "caption": caption, "notes": notes,
                           "merged_cell_hint": any(c == "" for r in rows for c in r)})
            i = j
        else:
            i += 1
    if not tables and input_path.lower().endswith((".csv", ".tsv")):
        dialect = csv.Sniffer().sniff(body[:2048]) if body.strip() else csv.excel
        reader = list(csv.reader(io.StringIO(body), dialect))
        if reader:
            tables.append({"index": 1, "header": reader[0], "rows": reader[1:],
                           "row_count": len(reader) - 1, "column_count": len(reader[0]),
                           "caption": None, "notes": [], "merged_cell_hint": False})
    if not tables:
        raise store.ToolError(2, "no markdown pipe table or CSV content found")
    payload = {"status": "ok", "tables": tables, "table_count": len(tables),
               "note": "scanned-image tables need the OCR adapter; this parses text tables only"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def op_figure_alt(ctx, spec_path: str, output: str | None = None) -> dict:
    """Build accessible alt text plus a long description from a structured figure spec."""
    store = _store()
    if not os.path.exists(spec_path):
        raise store.ToolError(2, f"figure spec not found: {spec_path}")
    try:
        spec = json.loads(store.read_text(spec_path))
    except json.JSONDecodeError as exc:
        raise store.ToolError(2, f"figure spec must be JSON: {exc}")
    title = spec.get("title") or "Untitled figure"
    kind = (spec.get("type") or "chart").lower()
    series = spec.get("series") or []
    if not series:
        raise store.ToolError(2, "figure spec needs a non-empty 'series' array")
    x_axis, y_axis = spec.get("x_axis", ""), spec.get("y_axis", "")
    unit = spec.get("unit", "")
    parts, long_parts = [], [f"Figure type: {kind}. Title: {title}."]
    for entry in series:
        label = entry.get("label") or "series"
        values = [float(v) for v in entry.get("values", []) if v is not None]
        if not values:
            continue
        trend = "increased" if values[-1] > values[0] else ("decreased" if values[-1] < values[0] else "was flat")
        parts.append(f"{label} {trend} from {values[0]:g} to {values[-1]:g}{(' ' + unit) if unit else ''}")
        long_parts.append(f"{label}: {len(values)} data points, minimum {min(values):g}, maximum "
                          f"{max(values):g}, last value {values[-1]:g}{(' ' + unit) if unit else ''}.")
    if x_axis or y_axis:
        long_parts.append(f"X axis: {x_axis or 'unlabelled'}. Y axis: {y_axis or 'unlabelled'}.")
    if spec.get("note"):
        long_parts.append(f"Source note: {spec['note']}")
    if spec.get("uncertainty"):
        long_parts.append("Uncertainty is displayed in the figure; values read from the chart are approximate.")
    alt = f"{title}: " + "; ".join(parts) + "."
    payload = {"status": "ok", "alt_text": alt[:250],
               "alt_text_length": len(alt[:250]),
               "long_description": " ".join(long_parts),
               "wcag_hint": "alt text under 250 characters; long description carries the detail"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def register(groups, common):
    g = groups["extract"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    common_src = dict(
        source_id=(("--source-id", "--source"), dict(default=None)),
        text=(("--text",), dict(default=None)),
        input_path=(("--input", "-i"), dict(default=None)))

    sub("entities", "Extract people, organizations, places, laws, standards, identifiers, dates, quantities",
        op_entities, store_records=(("--store",), dict(action="store_true")), **common_src)
    sub("link", "Link an entity mention to stable local identities", op_link,
        mention=(("--mention",), dict(required=True)), limit=(("--limit",), dict(type=int, default=10)))
    sub("relations", "Extract typed relations (affiliation, funding, amendment, citation, causation)",
        op_relations, **common_src)
    sub("claims", "Extract and classify claims with source coordinates", op_claims,
        store_records=(("--store",), dict(action="store_true")), **common_src)
    sub("evidence-link", "Link a stored claim to supporting/opposing passages", op_evidence_link,
        claim_id=(("--claim-id",), dict(required=True)), limit=(("--limit",), dict(type=int, default=5)))
    sub("events", "Extract events with actors, time, place and sequence", op_events, **common_src)
    sub("dates", "Normalize absolute and relative date expressions", op_dates,
        source_id=(("--source-id", "--source"), dict(default=None)),
        text=(("--text",), dict(default=None)), input_path=(("--input", "-i"), dict(default=None)),
        reference=(("--reference",), dict(default=None)))
    sub("quantities", "Extract values, units, comparators and denominators", op_quantities, **common_src)
    sub("methodology", "Extract design, sampling, instruments, analysis and limitations cues",
        op_methodology, **common_src)
    sub("funding", "Extract funders, grant ids and conflict-of-interest statements", op_funding, **common_src)
    sub("definitions", "Extract explicit definitions with context", op_definitions, **common_src)
    sub("review", "Approve, reject, merge, split or correct extracted records", op_review,
        kind=(("--kind",), dict(required=True, choices=["entity", "claim", "relation"])),
        ref=(("--ref",), dict(required=True)),
        decision=(("--decision",), dict(required=True,
                                        choices=["approve", "reject", "merge", "split", "correct"])),
        note=(("--note",), dict(default="")), actor=(("--reviewer",), dict(default=None)))
    sub("transcript", "Parse SRT/WebVTT into cleaned, timecoded cues", op_transcript,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)),
        clean=(("--no-clean",), dict(action="store_false", default=True)))
    sub("timecode-cite", "Build a quotable transcript segment with exact timecodes", op_timecode_cite,
        source_id=(("--source-id", "--source"), dict(required=True)),
        start_ms=(("--start-ms",), dict(type=int, required=True)),
        end_ms=(("--end-ms",), dict(type=int, default=None)),
        output=(("--output", "-o"), dict(default=None)))
    sub("ocr-eval", "Compute CER/WER for OCR or ASR output and optionally queue review", op_ocr_eval,
        reference_path=(("--reference",), dict(required=True)),
        hypothesis_path=(("--hypothesis",), dict(required=True)),
        review_queue=(("--queue-review",), dict(action="store_true")))
    sub("tables", "Parse markdown/CSV tables with captions and notes", op_tables,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
    sub("figure-alt", "Generate accessible alt text and long description from a figure spec",
        op_figure_alt, spec_path=(("--spec",), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
