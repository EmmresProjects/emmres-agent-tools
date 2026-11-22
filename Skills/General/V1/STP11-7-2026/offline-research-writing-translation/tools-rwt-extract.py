#!/usr/bin/env python3
"""tools-rwt-extract.py - Entity, claim-adjacent, event and relationship extraction engines.

Regex/gazetteer heuristics with explicit review flags. These extract candidate
records; they do not assert identity or truth. Time normalization handles
ISO, common ambiguous formats, years, and relative expressions anchored to a
reference date.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_e", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_e"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _read(file):
    if not os.path.isfile(file):
        raise C.ValidationError(f"File not found: {file}")
    with open(file, "r", encoding="utf-8") as fh:
        return fh.read()

PROPER = r"[A-Z][a-z]+(?:[- ][A-Z][a-z]+)*"
ORG_SUFFIX = r"(?:Inc\.?|Ltd\.?|LLC|Corp\.?|Corporation|University|Institute|Agency|Commission|Department|Ministry|Office|Bureau|Authority|Council|Association|Federation|Group|Holdings|S\.A\.|AG|GmbH|N\.V\.)"

def cmd_entities(args):
    text = _read(args.file)
    entities = []
    for m in re.finditer(rf"\b({PROPER})\s+({ORG_SUFFIX})\b", text):
        entities.append({"text": C.normalize_ws(m.group(0)), "type": "ORG", "confidence": 0.7})
    for m in re.finditer(r"\b(19|20)\d{2}\b", text):
        entities.append({"text": m.group(0), "type": "YEAR", "confidence": 0.95})
    for m in re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}", text):
        entities.append({"text": m.group(0), "type": "DATE", "confidence": 0.95})
    for m in re.finditer(r"\b(Republic Act|RA|Batas Pambansa|Executive Order|EO|Administrative Order|AO)\s+(?:No\.?\s*)?\d[\d\-]*\b", text):
        entities.append({"text": C.normalize_ws(m.group(0)), "type": "LAW", "confidence": 0.85})
    for m in re.finditer(r"\b(ISO|IEC|IEEE|ASTM|ANSI|ASME|EN|NIST|PNS)\s*[A-Z]?[\-\s]?\d[\d\.\-]*", text):
        entities.append({"text": C.normalize_ws(m.group(0)), "type": "STANDARD", "confidence": 0.85})
    for m in re.finditer(rf"\b({PROPER})\b", text):
        if not any(m.group(0) in e["text"] for e in entities):
            entities.append({"text": m.group(0), "type": "PROPER_NOUN_CANDIDATE", "confidence": 0.35})
    # dedup identical texts, keep highest confidence
    best = {}
    for e in entities:
        key = (e["text"], e["type"])
        if key not in best or e["confidence"] > best[key]["confidence"]:
            best[key] = e
    out = sorted(best.values(), key=lambda e: (-e["confidence"], e["text"]))
    return {"entities": out[:args.limit], "count": len(out),
            "note": "Gazetteer/regex candidates. ORG/LAW/STANDARD matches are strong; PROPER_NOUN_CANDIDATE needs linking/validation."}

def cmd_link_entities(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    with open(args.file, "r", encoding="utf-8") as fh:
        mentions = json.load(fh)
    if isinstance(mentions, dict):
        mentions = mentions.get("mentions", [])
    registry = ws.list_records("registry")
    by_name = {}
    for rec in registry:
        nm = str(rec.get("name", "")).lower()
        by_name.setdefault(nm, []).append(rec)
    out = []
    for m in mentions:
        key = C.normalize_ws(str(m.get("text", "")).lower())
        candidates = by_name.get(key, [])
        if not candidates:
            # fuzzy
            candidates = [rec for nm, recs in by_name.items() for rec in recs
                          if C.sim_ratio(nm, key) >= args.threshold]
        out.append({"mention": m.get("text"), "candidates": [
            {"id": rec.get("id"), "name": rec.get("name"), "kind": rec.get("kind"),
             "status": rec.get("status")} for rec in candidates][:5],
            "disambiguation": "ambiguous" if len(candidates) > 1 else ("linked" if candidates else "no-candidate")})
    return {"links": out, "review_required": True,
            "note": "Candidate links only. Ambiguity is preserved; a reviewer selects the identity."}

def cmd_relations(args):
    text = _read(args.file)
    relations = []
    patterns = [
        ("funding", r"(?:funded|supported|financed)\s+(?:by|with)\s+(?:a grant from\s+)?([A-Z][^.,;]{2,80})"),
        ("affiliation", r"([A-Z][^,.;]{2,80}?)\s+(?:is a professor|is affiliated|works at|of the)\s+([A-Z][^.,;]{2,80})"),
        ("ownership", r"([A-Z][^.,;]{2,80}?)\s+(?:owns|owned by|acquired|controlled by)\s+([A-Z][^.,;]{2,80})"),
        ("causation", r"([A-Za-z ]{3,60})\s+(?:causes?|leads? to|results? in)\s+([A-Za-z ]{3,60})"),
        ("comparison", r"([A-Za-z0-9 ]{3,60})\s+(?:compared with|versus|vs\.?)\s+([A-Za-z0-9 ]{3,60})"),
        ("amendment", r"([A-Za-z0-9 .]{3,60})\s+(?:amends|amended by|repeals|supersedes)\s+([A-Za-z0-9 .]{3,60})"),
        ("citation", r"\[(\d{1,3})\]"),
    ]
    for kind, pattern in patterns:
        for m in re.finditer(pattern, text):
            relations.append({"type": kind, "match": C.normalize_ws(m.group(0))[:160]})
    return {"relations": relations[:args.limit], "count": len(relations),
            "note": "Syntactic candidates; type and direction need human confirmation."}

def cmd_events(args):
    text = _read(args.file)
    events = []
    event_verbs = r"(?:announced|signed|launched|passed|enacted|held|opened|closed|resigned|appointed|elected|published|released|reported|declared|approved|rejected|filed|convicted|acquitted)"
    for sent in C.sentences(text):
        m = re.search(rf"\b(?:\w+\s+)?({event_verbs})\b", sent, re.I)
        if m:
            dates = re.findall(r"(January|February|March|April|May|June|July|August|September|October|November|December)?\s*\d{1,2},\s*\d{4}|\b(19|20)\d{2}\b", sent)
            flat_dates = [" ".join(d).strip() for d in dates]
            actors = re.findall(rf"\b{PROPER}(?:\s+{PROPER})*\b", sent)[:4]
            events.append({"verb": m.group(1).lower(), "actors": actors,
                           "dates": flat_dates, "sentence": C.normalize_ws(sent)[:200]})
    return {"events": events[:args.limit], "count": len(events),
            "note": "Event candidates with actors/dates as found; sequence and consequence fields need reviewer completion."}

def cmd_methodology(args):
    text = _read(args.file)
    low = text.lower()
    hits = {
        "design": [w for w in ("randomized", "randomised", "cohort", "case-control", "cross-sectional",
                                "quasi-experimental", "qualitative", "ethnograph", "survey", "interview",
                                "focus group", "archival", "mixed methods", "systematic review") if w in low],
        "sampling": [w for w in ("random sample", "stratified", "cluster sampling", "convenience",
                                  "purposive", "snowball", "quota", "census") if w in low],
        "instruments": [w for w in ("questionnaire", "survey instrument", "scale", "interview guide",
                                     "codebook", "rubric", "checklist") if w in low],
        "analysis": [w for w in ("regression", "anova", "chi-square", "t-test", "thematic analysis",
                                  "grounded theory", "content analysis", "odds ratio", "confidence interval") if w in low],
        "preregistration": [w for w in ("preregistered", "pre-registered", "registered report", "osf.io") if w in low],
        "limitations": [w for w in ("limitation", "generaliz", "external validity", "selection bias", "attrition") if w in low],
    }
    n_match = re.findall(r"\bn\s*=\s*\d+", low)
    return {"methodology": hits, "sample_sizes_found": n_match,
            "missing_sections": [k for k, v in hits.items() if not v and k != "limitations"],
            "review_required": True,
            "note": "Keyword-based profile of the methods section; confirm against the full text."}

def cmd_funding(args):
    text = _read(args.file)
    funders = [C.normalize_ws(m.group(1)) for m in re.finditer(
        r"(?:funded|supported|financed)\s+(?:by|with|through)\s+(?:a grant from\s+)?([A-Z][^.,;]{2,100})", text)]
    grants = re.findall(r"\b(?:grant|award|contract)\s*(?:no\.?|number|#)?\s*[:#]?\s*[A-Za-z0-9\-/]{4,20}\b", text, re.I)
    disclosures = [C.normalize_ws(m.group(0)) for m in re.finditer(
        r"(?:conflict of interest|competing interest|declaration of interest)[s]?[^.]{0,200}\.", text, re.I)]
    coi = bool(re.search(r"(no (competing|conflict)|nothing to declare|declares? no)", text, re.I))
    return {"funders": funders, "grant_identifiers": grants,
            "disclosure_statements": disclosures, "explicit_no_conflict": coi,
            "review_required": True}

def cmd_definitions(args):
    text = _read(args.file)
    defs = []
    for m in re.finditer(r"(?i)(?:^|\n)\s*[\"'“]?([A-Za-z][A-Za-z0-9 \-/]{1,60})[\"'”]?\s*(?:is defined as|refers to|means|denotes)\s*(.{10,300})", text):
        defs.append({"term": C.normalize_ws(m.group(1)), "definition": C.normalize_ws(m.group(2)),
                     "line": text[:m.start()].count("\n") + 1})
    return {"definitions": defs, "count": len(defs),
            "note": "Explicit definitional statements with line locators; jurisdiction/version context attached by the caller."}

RELATIVE_RE = re.compile(r"\b(\d+)\s+(day|week|month|year)s?\s+(ago|from now|later)\b", re.I)
WEEKDAY_RE = re.compile(r"\b(yesterday|today|tomorrow|last (week|month|year)|next (week|month|year))\b", re.I)

def cmd_time_normalize(args):
    text = _read(args.file) if args.file else args.text
    if not text:
        raise C.ValidationError("Provide --file or --text")
    ref = args.reference_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ref_dt = datetime.strptime(ref, "%Y-%m-%d")
    out = []
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", text):
        out.append({"raw": m.group(0), "iso": m.group(0), "precision": "day", "uncertain": False})
    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text):
        iso, uncertain = C.parse_date(m.group(0))
        out.append({"raw": m.group(0), "iso": iso, "precision": "day", "uncertain": uncertain,
                    "note": "Ambiguous numeric order; confirm locale" if uncertain else None})
    for m in re.finditer(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b", text):
        iso, _ = C.parse_date(m.group(1))
        out.append({"raw": m.group(1), "iso": iso, "precision": "day", "uncertain": False})
    for m in re.finditer(r"\b((?:19|20)\d{2})\b", text):
        out.append({"raw": m.group(0), "iso": f"{m.group(1)}-01-01", "precision": "year", "uncertain": True})
    for m in RELATIVE_RE.finditer(text):
        n, unit, direction = int(m.group(1)), m.group(2).lower(), m.group(3).lower()
        delta = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        if direction == "ago":
            anchor = ref_dt - timedelta(days=delta)
        else:
            anchor = ref_dt + timedelta(days=delta)
        out.append({"raw": m.group(0), "iso": anchor.strftime("%Y-%m-%d"),
                    "precision": f"approx-{unit}", "uncertain": True,
                    "anchored_to": ref})
    for m in WEEKDAY_RE.finditer(text):
        word = m.group(1).lower()
        delta = {"yesterday": -1, "today": 0, "tomorrow": 1}.get(word)
        if delta is not None:
            out.append({"raw": m.group(1), "iso": (ref_dt + timedelta(days=delta)).strftime("%Y-%m-%d"),
                        "precision": "day", "uncertain": False, "anchored_to": ref})
        else:
            out.append({"raw": m.group(1), "iso": None, "precision": "week/month/year",
                        "uncertain": True, "note": "Relative period; resolve with the reference date"})
    return {"reference_date": ref, "expressions": out,
            "note": "Relative expressions are anchored to the reference date (default today, UTC). Historical/regnal dates need manual mapping."}

COMMANDS = {
    "entities": cmd_entities, "link-entities": cmd_link_entities, "relations": cmd_relations,
    "events": cmd_events, "methodology": cmd_methodology, "funding": cmd_funding,
    "definitions": cmd_definitions, "time-normalize": cmd_time_normalize,
}

def build_parser(prog="rwt extract"):
    p = argparse.ArgumentParser(prog=prog, description="Extraction engines: entities, relations, events, methodology, timing.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("entities", help="Extract people/ORG/law/standard/year/date candidates")
    sp.add_argument("--file", required=True); sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_entities)

    sp = sub.add_parser("link-entities", help="Link mentions to registry records (candidates only)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--file", required=True,
                    help="JSON list of {text} mentions")
    sp.add_argument("--threshold", type=float, default=0.8); sp.set_defaults(func=cmd_link_entities)

    sp = sub.add_parser("relations", help="Extract funding/affiliation/ownership/causation/comparison/amendment/citation candidates")
    sp.add_argument("--file", required=True); sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_relations)

    sp = sub.add_parser("events", help="Extract event candidates (verb, actors, dates)")
    sp.add_argument("--file", required=True); sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("methodology", help="Profile study design/sampling/instruments/analysis from text")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_methodology)

    sp = sub.add_parser("funding", help="Extract funders, grant identifiers, conflict disclosures")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_funding)

    sp = sub.add_parser("definitions", help="Extract explicit definition statements with line locators")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_definitions)

    sp = sub.add_parser("time-normalize", help="Normalize dates and relative expressions to explicit ranges")
    sp.add_argument("--file"); sp.add_argument("--text")
    sp.add_argument("--reference-date", default=None, help="YYYY-MM-DD; defaults to today (UTC)")
    sp.set_defaults(func=cmd_time_normalize)
    return p

def run(argv):
    parser = build_parser(prog="rwt extract")
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
