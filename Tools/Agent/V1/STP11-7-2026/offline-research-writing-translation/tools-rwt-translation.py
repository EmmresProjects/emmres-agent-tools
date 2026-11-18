#!/usr/bin/env python3
"""tools-rwt-translation.py - Terminology, translation memory, language ID, MT QA engines.

Capabilities: terminology database (CRUD, forbidden variants, acronym rules,
entity policy, definition consistency audit, glossary build), translation
memory (CRUD, fuzzy match, TMX import/export, cleaning: duplicates,
misalignment, wrong language, corrupted chars), language identification
(stopword/script based), severity-weighted QA scoring against the bundled
error taxonomy, acceptance test runner, model drift monitor, document-level
translation preparation, pivot-translation guard, back-translation comparison,
low-resource workflow envelope, domain packager, extractive multilingual
summarization hook, and a local dictionary lookup.
"""
import argparse
import difflib
import json
import math
import os
import re
import sys
from collections import Counter
from xml.etree import ElementTree

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_tr", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_tr"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

# ------------------------------------------------------------ language id
LANGID_PROFILES = {
    "en": set("the of and to in is are was for on with as by at from that this it be have has not you we they".split()),
    "es": set("el la los las de y en que es un una por con no su para como más pero sus le ha me si son al lo".split()),
    "fr": set("le la les des de un une et est en que pour dans pas sur au aux ce il nous vous ne se du à".split()),
    "de": set("der die das und in den von zu mit sich des auf für ist im dem nicht ein eine auch es an werden".split()),
    "it": set("il la di che e un una per non si in del sono da con le ha lo gli ma come più".split()),
    "pt": set("de a o que e do da em um uma para com não uma os no se na por mais as dos como mas foi".split()),
    "tl": set("ng mga sa na ay ito iyon siya sila kami kayo ko mo niya kanya namin natin ang at o".split()),
    "id": set("yang dan di ke dari untuk dengan tidak adalah ini itu pada dalam juga saya kami".split()),
    "nl": set("de het een en van in is op te voor met als aan niet er ook die deze zijn".split()),
}
SCRIPT_RANGES = [
    ("Han", re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")),
    ("Hiragana-Katakana", re.compile(r"[\u3040-\u30FF]")),
    ("Hangul", re.compile(r"[\uAC00-\uD7AF]")),
    ("Cyrillic", re.compile(r"[\u0400-\u04FF]")),
    ("Arabic", re.compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")),
    ("Hebrew", re.compile(r"[\u0590-\u05FF]")),
    ("Devanagari", re.compile(r"[\u0900-\u097F]")),
    ("Thai", re.compile(r"[\u0E00-\u0E7F]")),
]

def detect_language(text):
    scripts = {name: len(rx.findall(text)) for name, rx in SCRIPT_RANGES}
    top_script = max(scripts, key=scripts.get)
    if scripts[top_script] > 8:
        mapping = {"Han": "zh", "Hiragana-Katakana": "ja", "Hangul": "ko", "Cyrillic": "ru",
                   "Arabic": "ar", "Hebrew": "he", "Devanagari": "hi", "Thai": "th"}
        return {"language": mapping.get(top_script, "und"), "script": top_script,
                "confidence": round(min(0.99, scripts[top_script] / max(1, len(text)) * 3), 3),
                "method": "script-range"}
    toks = C.tokenize(text)
    if not toks:
        return {"language": "und", "confidence": 0.0, "method": "empty"}
    counts = {lang: sum(1 for t in toks if t in prof) for lang, prof in LANGID_PROFILES.items()}
    best = max(counts, key=counts.get)
    total_hits = counts[best]
    confidence = min(0.98, total_hits / max(1, len(toks)) * 2.5) if total_hits else 0.0
    if total_hits == 0:
        return {"language": "und", "confidence": 0.0, "method": "stopword", "candidates": counts}
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    return {"language": best, "confidence": round(confidence, 3), "method": "stopword",
            "candidates": [{"lang": l, "hits": h} for l, h in ranked]}

def cmd_langid(args):
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    if not text:
        raise C.ValidationError("Provide --text or --file")
    paras = [p for p in text.splitlines() if p.strip()]
    overall = detect_language(text)
    spans = []
    if args.spans and len(paras) > 1:
        for i, p in enumerate(paras, start=1):
            d = detect_language(p)
            d["line"] = i
            d["excerpt"] = p[:120]
            spans.append(d)
    return {"document": overall, "spans": spans or None,
            "note": "Stopword/script identification; dialects and code-switching reduce confidence — verify flagged spans by hand."}

# ------------------------------------------------------------ terminology
def cmd_terms_add(args):
    ws = _ws(args)
    ws.add_record("terms", {
        "term": args.term, "target": args.target, "domain": args.domain,
        "definition": args.definition, "forbidden_variants": [v.strip() for v in args.forbidden_variants.split(",") if v.strip()],
        "grammar": args.grammar, "example": args.example, "owner": args.owner,
        "approval": "approved" if args.approve else "draft",
        "source_lang": args.source_lang, "target_lang": args.target_lang,
        "do_not_translate": args.do_not_translate,
    }, actor=args.actor)
    return {"term": args.term, "approval": "approved" if args.approve else "draft"}

def cmd_terms_lookup(args):
    ws = _ws(args)
    terms = ws.list_records("terms")
    q = args.query.lower()
    exact = [t for t in terms if str(t.get("term", "")).lower() == q]
    fuzzy = [t for t in terms if str(t.get("term", "")).lower() != q
             and C.sim_ratio(str(t.get("term", "")).lower(), q) >= args.fuzzy]
    return {"exact": exact, "fuzzy_matches": sorted(fuzzy, key=lambda t: -C.sim_ratio(str(t.get("term", "")).lower(), q))[:10]}

def cmd_terms_import(args):
    ws = _ws(args)
    imported = 0
    with open(args.file, "r", encoding="utf-8") as fh:
        if args.file.lower().endswith(".jsonl"):
            rows = [json.loads(l) for l in fh if l.strip()]
        else:
            rows = json.load(fh)
            if isinstance(rows, dict):
                rows = rows.get("terms", [])
    for t in rows:
        if not t.get("term"):
            continue
        ws.add_record("terms", {"term": t["term"], "target": t.get("target"),
                                "domain": t.get("domain"), "definition": t.get("definition"),
                                "forbidden_variants": t.get("forbidden_variants", []),
                                "grammar": t.get("grammar"), "example": t.get("example"),
                                "owner": t.get("owner", args.owner),
                                "approval": t.get("approval", "approved" if args.approve else "draft"),
                                "source_lang": t.get("source_lang", "en"),
                                "target_lang": t.get("target_lang", args.target_lang),
                                "do_not_translate": bool(t.get("do_not_translate", False))},
                     actor=args.actor)
        imported += 1
    return {"imported": imported, "approval": "approved" if args.approve else "as-in-file"}

def cmd_terms_consistency(args):
    ws = _ws(args)
    terms = ws.list_records("terms")
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    low = text.lower()
    violations = []
    for t in terms:
        for variant in t.get("forbidden_variants", []):
            if variant and variant.lower() in low:
                ctx = low.find(variant.lower())
                violations.append({"term_id": t.get("id"), "approved_term": t.get("term"),
                                   "forbidden_variant_found": variant,
                                   "context": C.normalize_ws(text[max(0, ctx - 60):ctx + len(variant) + 60]),
                                   "domain": t.get("domain")})
    # case-inconsistent usage of approved terms
    case_issues = []
    for t in terms:
        term = str(t.get("term", ""))
        if not term:
            continue
        variants = set(re.findall(re.escape(term), text, re.I))
        if len(variants) > 1:
            case_issues.append({"term": term, "variants_found": sorted(variants)})
    return {"violations": violations, "case_inconsistencies": case_issues,
            "terms_checked": len(terms)}

def cmd_terms_acronyms(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    found = {}
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,6})\b", text):
        found.setdefault(m.group(1), []).append(m.start())
    ws_acronyms_path = args.donottranslate
    dnt = set()
    if ws_acronyms_path and os.path.isfile(ws_acronyms_path):
        dnt = set(json.load(open(ws_acronyms_path, encoding="utf-8")))
    out = []
    for abbr, positions in sorted(found.items()):
        expanded = bool(re.search(r"[^.]*\(" + re.escape(abbr) + r"\)", text))
        out.append({"acronym": abbr, "occurrences": len(positions),
                    "first_use_offset": positions[0],
                    "expanded_at_first_use": expanded,
                    "do_not_translate": abbr in dnt})
    undefined = [a["acronym"] for a in out if not a["expanded_at_first_use"] and a["acronym"] not in dnt]
    return {"acronyms": out, "undefined_at_first_use": undefined,
            "dnt_list_used": bool(dnt)}

def cmd_terms_inflect(args):
    """Limited rule-based inflection hints for en/es; always flag for verification."""
    term = args.term
    lang = args.lang
    forms = {"base": term}
    if lang == "en":
        plural = term + ("es" if re.search(r"(s|x|z|ch|sh)$", term, re.I) else ("ies" if re.search(r"[^aeiou]y$", term, re.I) else "s"))
        if term.lower().endswith("ies"):
            plural = term[:-3] + "ies"
        forms["plural"] = plural if not re.search(r"[^aeiou]y$", term, re.I) else term[:-1] + "ies"
        forms["possessive"] = term + ("'" if term.endswith("s") else "'s")
    elif lang == "es":
        if term.lower().endswith(("a", "o", "e")):
            forms["plural"] = term + "s"
        elif re.search(r"[áéíóú]$|[bcdfglmnñpr]$", term.lower()):
            forms["plural"] = term + "es"
        else:
            forms["plural"] = term + "s"
        forms["feminine"] = term[:-1] + "a" if term.lower().endswith("o") else term
    else:
        raise C.ValidationError(f"Inflection rules implemented only for en and es (got '{lang}'). Use the morphology service/adapter for other languages.")
    return {"term": term, "lang": lang, "forms": forms,
            "verify_with": "local morphology service or qualified reviewer for domain terms"}

def cmd_terms_entity_policy(args):
    policy = {
        "person_names": "retain original spelling; add established translation only in parentheses at first use",
        "organizations": "use official short name; translate only when an official translated name exists",
        "places": "use established exonym if one exists in target language; else transliterate per approved standard; keep original in parentheses",
        "laws_statutes": "cite in original language with bracketed translation; never silently translate titles",
        "standards": "retain standard identifier (e.g., ISO 9001); translate descriptive name only",
        "measurements": "retain value + unit; convert units only with explicit marker [value] (unit converted)",
        "legal_citations": "retain original reporter citation; add translation of proposition, not of the citation",
    }
    if args.entity_class:
        key = args.entity_class.lower().replace("-", "_").replace(" ", "_")
        match = next((k for k in policy if key.startswith(k.rstrip("s")) or k.startswith(key)), None)
        return {"entity_class": args.entity_class, "policy": policy.get(match or key, "unknown class; default: retain original + glossary note"),
                "all_classes": policy}
    return {"policy": policy}

def cmd_terms_definition_audit(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    defs = []
    for m in re.finditer(r"(?im)^\s*[\"'“]?([A-Za-z][A-Za-z0-9 \-/]{1,60})[\"'”]?\s*(?:is defined as|refers to|means|denotes)\s*(.{10,300})$", text):
        defs.append({"term": C.normalize_ws(m.group(1)), "definition": C.normalize_ws(m.group(2))})
    # group by normalized term and compare definitions
    groups = {}
    for d in defs:
        groups.setdefault(d["term"].lower(), []).append(d["definition"])
    divergent = []
    for term, dlist in groups.items():
        if len(dlist) > 1:
            max_sim = max(C.sim_ratio(dlist[i].lower(), dlist[j].lower())
                          for i in range(len(dlist)) for j in range(i + 1, len(dlist)))
            if max_sim < 0.75:
                divergent.append({"term": term, "definitions": dlist, "max_similarity": round(max_sim, 3)})
    return {"definitions_found": len(defs), "terms_repeated": sum(1 for v in groups.values() if len(v) > 1),
            "divergent_definitions": divergent,
            "note": "Divergent definitions need subject-matter adjudication; defined terms must keep one meaning per jurisdiction/version."}

def cmd_terms_glossary_build(args):
    ws = _ws(args)
    docs = ws.list_records("documents")
    if not docs:
        raise C.ValidationError("No documents in workspace; ingest the approved corpus first")
    freq = Counter()
    first_seen = {}
    definition_sentences = {}
    for d in docs:
        for sent in C.sentences(d.get("text", "") or ""):
            low = sent.lower()
            m = re.search(r"([a-z][a-z\- ]{2,40})\s+(?:is defined as|refers to|means|denotes)\b", low)
            if m:
                term = C.normalize_ws(m.group(1))
                definition_sentences.setdefault(term, C.normalize_ws(sent))
    for d in docs:
        for tok in C.content_tokens(d.get("text", "")):
            if len(tok) >= 6 and tok.isalpha():
                freq[tok] += 1
    candidates = [t for t, n in freq.most_common(200) if n >= args.min_freq]
    glossary = []
    for t in candidates:
        entry = {"term": t, "frequency": freq[t],
                 "definition": definition_sentences.get(t),
                 "needs_definition": t not in definition_sentences,
                 "citations": [d["id"] for d in docs[:3]]}
        glossary.append(entry)
    return {"candidates": glossary, "min_frequency": args.min_freq,
            "review_required": True,
            "note": "Candidates are frequency-based; subject-matter owners approve entries and translations."}

# ------------------------------------------------------------ translation memory
def _seg_similarity(a, b):
    seq = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    ta, tb = set(C.tokenize(a)), set(C.tokenize(b))
    tok = C.jaccard(ta, tb)
    return 0.6 * seq + 0.4 * tok

def cmd_tm_add(args):
    ws = _ws(args)
    rec = ws.add_record("segments", {
        "source": args.source, "target": args.target,
        "source_lang": args.source_lang, "target_lang": args.target_lang,
        "domain": args.domain, "project": args.project, "quality": args.quality,
        "approval": "approved" if args.approve else "draft",
    }, actor=args.actor)
    return {"segment_id": rec["id"]}

def cmd_tm_match(args):
    ws = _ws(args)
    segments = ws.list_records("segments")
    if args.source_lang:
        segments = [s for s in segments if s.get("source_lang") == args.source_lang]
    if args.target_lang:
        segments = [s for s in segments if s.get("target_lang") == args.target_lang]
    scored = [(round(_seg_similarity(args.source, s.get("source", "")), 4), s) for s in segments]
    scored = [x for x in scored if x[0] >= args.threshold]
    scored.sort(key=lambda x: -x[0])
    return {"query": args.source[:120],
            "matches": [{"similarity": sim, "source": s.get("source"), "target": s.get("target"),
                         "id": s.get("id"), "approval": s.get("approval"),
                         "domain": s.get("domain"), "quality": s.get("quality")} for sim, s in scored[:args.limit]]}

def cmd_tm_import_tmx(args):
    ws = _ws(args)
    if not os.path.isfile(args.file):
        raise C.ValidationError(f"TMX file not found: {args.file}")
    try:
        tree = ElementTree.parse(args.file)
    except ElementTree.ParseError as exc:
        raise C.ValidationError(f"TMX is not well-formed XML: {exc}")
    imported = 0
    for tu in tree.iter():
        if not tu.tag.endswith("tu"):
            continue
        tuv = {}
        for unit in tu:
            if not unit.tag.endswith("tuv"):
                continue
            lang = unit.get("{http://www.w3.org/XML/1998/namespace}lang") or unit.get("xml:lang") or unit.get("lang")
            seg = "".join(unit.itertext()).strip()
            if lang and seg:
                tuv[lang.split("-")[0].lower()] = seg
        if len(tuv) >= 2:
            pair = list(tuv.items())
            src_lang, src = pair[0]
            tgt_lang, tgt = pair[1]
            ws.add_record("segments", {"source": src, "target": tgt,
                                       "source_lang": src_lang, "target_lang": tgt_lang,
                                       "domain": args.domain, "approval": "draft"},
                          actor=args.actor)
            imported += 1
    return {"imported": imported, "note": "Imported as drafts; approve after review."}

def cmd_tm_export_tmx(args):
    ws = _ws(args)
    segments = [s for s in ws.list_records("segments") if s.get("approval") in ("approved",) or args.include_drafts]
    tmx = ElementTree.Element("tmx", {"version": "1.4"})
    header = ElementTree.SubElement(tmx, "header", {"creationtool": "rwt", "datatype": "PlainText",
                                                    "segtype": "sentence", "adminlang": "en",
                                                    "srclang": args.source_lang, "o-tmf": "rwt-1.0"})
    body = ElementTree.SubElement(tmx, "body")
    for s in segments:
        if s.get("source_lang", "en") != args.source_lang:
            continue
        tu = ElementTree.SubElement(body, "tu")
        for lang, text in ((s.get("source_lang", "en"), s.get("source", "")),
                           (s.get("target_lang", "es"), s.get("target", ""))):
            tuv = ElementTree.SubElement(tu, "tuv", {"xml:lang": lang})
            seg = ElementTree.SubElement(tuv, "seg")
            seg.text = text
    out_path = args.output or os.path.join(ws.root, C.EXPORTS_DIR, "translation-memory.tmx")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tree = ElementTree.ElementTree(tmx)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    C.atomic_write(out_path, open(out_path, "rb").read().decode("utf-8"))
    return {"exported": len(segments), "path": out_path}

def cmd_tm_clean(args):
    ws = _ws(args)
    segments = ws.list_records("segments")
    seen = {}
    drops, flags = [], []
    for s in segments:
        src_norm = C.normalize_ws(s.get("source", "")).lower()
        tgt_norm = C.normalize_ws(s.get("target", "")).lower()
        key = (src_norm, tgt_norm)
        if key in seen:
            drops.append({"id": s.get("id"), "reason": f"duplicate of {seen[key]}"})
            continue
        seen[key] = s.get("id")
        if "\ufffd" in (s.get("source", "") + s.get("target", "")):
            flags.append({"id": s.get("id"), "reason": "corrupted characters (U+FFFD)"})
        srclen, tgtlen = len(src_norm), len(tgt_norm)
        if srclen and tgtlen and (srclen / tgtlen > 3 or tgtlen / srclen > 3):
            flags.append({"id": s.get("id"), "reason": f"misaligned length ratio {max(srclen,tgtlen)/min(srclen,tgtlen):.1f}"})
        det = detect_language(s.get("source", ""))
        if args.source_lang and det["language"] not in (args.source_lang, "und") and det["confidence"] > 0.4:
            flags.append({"id": s.get("id"), "reason": f"source detected as {det['language']} not {args.source_lang}"})
    if args.apply:
        drop_ids = {d["id"] for d in drops}
        kept = [s for s in segments if s.get("id") not in drop_ids]
        ws._rewrite_collection("segments", kept)
        ws.audit(args.actor, "segments.clean", {"dropped": len(drop_ids)})
    return {"segments": len(segments), "duplicates_dropped": drops,
            "flagged_for_review": flags, "applied": bool(args.apply),
            "note": "Misalignment and wrong-language flags need human review; only exact duplicates are auto-droppable."}

def cmd_tm_align(args):
    with open(args.source_file, "r", encoding="utf-8") as fh:
        src_text = fh.read()
    with open(args.target_file, "r", encoding="utf-8") as fh:
        tgt_text = fh.read()
    src_sents = C.sentences(src_text)
    tgt_sents = C.sentences(tgt_text)
    pairs, si, ti = [], 0, 0
    while si < len(src_sents) and ti < len(tgt_sents):
        best_j, best_sim = ti, 0.0
        for j in range(max(ti, ti), min(len(tgt_sents), ti + args.window + 1)):
            sim = C.sim_ratio(src_sents[si].lower(), tgt_sents[j].lower())
            # penalize length mismatch
            len_ratio = min(len(src_sents[si]), len(tgt_sents[j])) / max(1, max(len(src_sents[si]), len(tgt_sents[j])))
            sim = 0.7 * sim + 0.3 * len_ratio
            if sim > best_sim:
                best_sim, best_j = sim, j
        pairs.append({"source_sentence": si + 1, "target_sentence": best_j + 1,
                      "similarity": round(best_sim, 3),
                      "source": src_sents[si][:200], "target": tgt_sents[best_j][:200],
                      "low_confidence": best_sim < 0.45})
        si += 1
        ti = best_j + 1
    remaining = abs(len(src_sents) - len(tgt_sents))
    return {"aligned_pairs": pairs, "unparsed_alignment": remaining,
            "review_low_confidence": [p for p in pairs if p["low_confidence"]],
            "note": "Greedy monotone alignment by similarity+length; tables/footnotes need manual alignment."}

# ------------------------------------------------------------ QA scoring
def cmd_qa_taxonomy(args):
    taxonomy = C.load_asset("asset-error-taxonomy.json")
    return taxonomy

def cmd_qa_score(args):
    taxonomy = C.load_asset("asset-error-taxonomy.json")
    weights = taxonomy["severity_weights"]
    thresholds = taxonomy["acceptance"]
    with open(args.errors, "r", encoding="utf-8") as fh:
        errors = json.load(fh)
    if isinstance(errors, dict):
        errors = errors.get("errors", [])
    if not isinstance(errors, list):
        raise C.ValidationError("Errors file must be a JSON list of {category, severity, note?}")
    totals = Counter()
    by_category = Counter()
    unknown = []
    for e in errors:
        cat, sev = str(e.get("category", "")).lower(), str(e.get("severity", "minor")).lower()
        if sev not in weights:
            unknown.append({"error": e, "issue": f"unknown severity '{sev}'"})
            continue
        totals[sev] += 1
        by_category[f"{cat}:{sev}"] += 1
    words = args.words or 1000
    per_1000 = {k: v / words * 1000 for k, v in totals.items()}
    penalty = sum(weights[sev] * cnt for sev, cnt in per_1000.items())
    score = max(0.0, 100.0 - penalty)
    gate = "pass" if score >= thresholds["pass_score"] and totals.get("critical", 0) == 0 else (
        "conditional" if score >= thresholds.get("conditional_score", 90) else "fail")
    return {"words": words, "error_counts": dict(totals),
            "errors_per_1000_words": {k: round(v, 3) for k, v in per_1000.items()},
            "by_category": dict(by_category),
            "severity_weights": weights, "penalty": round(penalty, 2),
            "score": round(score, 2), "gate": gate,
            "critical_errors": totals.get("critical", 0),
            "unknown_entries": unknown,
            "note": taxonomy["notes"]["scoring_note"]}

def cmd_acceptance(args):
    cases = C.load_asset("asset-benchmark-cases.json").get("acceptance_tests", [])
    with open(args.output_file, "r", encoding="utf-8") as fh:
        candidate = fh.read()
    results = []
    for case in cases:
        cid, ctype = case.get("id"), case.get("type")
        if ctype == "glossary":
            term = case["term"].lower()
            ok = term in candidate.lower()
            results.append({"id": cid, "type": ctype, "pass": ok,
                            "detail": f"approved term '{case['term']}' {'present' if ok else 'MISSING'}"})
        elif ctype == "placeholder":
            kept = all(ph in candidate for ph in case["placeholders"])
            results.append({"id": cid, "type": ctype, "pass": kept,
                            "detail": "placeholders preserved" if kept else f"missing: {[p for p in case['placeholders'] if p not in candidate]}"})
        elif ctype == "number":
            nums = re.findall(r"-?\d+(?:\.\d+)?", candidate)
            ok = all(n in nums for n in case["numbers"])
            results.append({"id": cid, "type": ctype, "pass": ok,
                            "detail": "numbers preserved" if ok else f"missing numbers: {[n for n in case['numbers'] if n not in nums]}"})
        elif ctype == "forbidden":
            bad = [w for w in case["words"] if re.search(re.escape(w), candidate, re.I)]
            results.append({"id": cid, "type": ctype, "pass": not bad,
                            "detail": f"forbidden terms used: {bad}" if bad else "no forbidden terms"})
        elif ctype == "negation":
            ok = bool(re.search(r"\b(no|not|never|without|sin|non)\b", candidate, re.I)) == case.get("expects_negation", True)
            results.append({"id": cid, "type": ctype, "pass": ok, "detail": "negation polarity checked"})
    passed = sum(1 for r in results if r["pass"])
    return {"tests": results, "passed": passed, "total": len(results),
            "verdict": "pass" if passed == len(results) else "fail",
            "note": "Acceptance tests check format/terminology fidelity; adequacy still needs bilingual review."}

def cmd_drift(args):
    with open(args.baseline, "r", encoding="utf-8") as fh:
        baseline = json.load(fh)
    with open(args.current, "r", encoding="utf-8") as fh:
        current = json.load(fh)
    b_scores, c_scores = {}, {}
    for run in (baseline if isinstance(baseline, list) else [baseline]):
        for k, v in (run.get("scores") or {}).items():
            b_scores[k] = v
    for run in (current if isinstance(current, list) else [current]):
        for k, v in (run.get("scores") or {}).items():
            c_scores[k] = v
    deltas = {k: round(c_scores.get(k, 0) - v, 3) for k, v in b_scores.items()}
    regressions = {k: d for k, d in deltas.items() if d < -args.tolerance}
    return {"pairs_evaluated": sorted(deltas), "deltas": deltas,
            "regressions": regressions, "tolerance": args.tolerance,
            "verdict": "block-release" if regressions else "no-material-regression"}

# ------------------------------------------------------------ translation workflow helpers
def cmd_translate_document(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    placeholders = sorted(set(re.findall(r"(?:\{\{[^}]+\}\}|\{[A-Za-z0-9_]+\}|%%[A-Za-z0-9_]+%%|%\([a-z0-9_]+\)[sd]|\$\{[^}]+\})", text)))
    tags = sorted(set(re.findall(r"<[^>]{1,80}>", text)))
    segments = C.sentences(text)
    return {"segments": len(segments), "placeholders": placeholders, "protected_tags": tags,
            "preparation": ["placeholders locked (must not be translated)",
                            "inline tags locked",
                            "terminology to enforce: run terms.consistency against the domain glossary",
                            "translation memory matches suggested via tm.match"],
            "note": "Preparation envelope only. Translation itself is performed by the approved offline model adapter or a human translator."}

def cmd_pivot_guard(args):
    return {"pivot_language_detected": bool(args.pivot_lang), "pivot_language": args.pivot_lang,
            "additional_validation_required": bool(args.pivot_lang),
            "required_checks": (["back-translation of high-risk segments",
                                 "terminology re-verified against source language glossary",
                                 "number/negation/modality audit",
                                 "no round-trip-only confidence claims"] if args.pivot_lang else []),
            "note": "Pivot translation compounds meaning loss; do not report mechanical back-translation as validation."}

def cmd_backtranslation(args):
    with open(args.original, "r", encoding="utf-8") as fh:
        original = fh.read()
    with open(args.backtranslation, "r", encoding="utf-8") as fh:
        back = fh.read()
    ratio = C.sim_ratio(C.normalize_ws(original).lower(), C.normalize_ws(back).lower())
    orig_nums = re.findall(r"-?\d+(?:\.\d+)?", original)
    back_nums = re.findall(r"-?\d+(?:\.\d+)?", back)
    number_loss = [n for n in orig_nums if n not in back_nums]
    neg_orig = bool(re.search(r"\b(no|not|never|without|sin|non)\b", original, re.I))
    neg_back = bool(re.search(r"\b(no|not|never|without|sin|non)\b", back, re.I))
    return {"similarity": round(ratio, 3), "number_loss": number_loss,
            "negation_flipped": neg_orig != neg_back,
            "verdict": ("high-divergence-review" if ratio < 0.6 or number_loss or neg_orig != neg_back
                        else "terminology-review"),
            "note": "Low similarity can be legitimate stylistic distance; it flags segments for bilingual review, it does not condemn them."}

def cmd_source_edit(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    issues = []
    for i, s in enumerate(C.sentences(text), start=1):
        words = len(C.tokenize(s))
        if words > 45:
            issues.append({"sentence": i, "words": words, "issue": "overlong; split before translating"})
        if len(re.findall(r"\b(not|no|never|without)\b", s, re.I)) > 1:
            issues.append({"sentence": i, "issue": "multiple negations; ambiguity risk"})
    placeholders = sorted(set(re.findall(r"(?:\{\{[^}]+\}\}|\{[A-Za-z0-9_]+\}|%%[A-Za-z0-9_]+%%|%\([a-z0-9_]+\)[sd]|\$\{[^}]+\})", text)))
    undefined_refs = re.findall(r"\[(?:ref|source|cite)\s*\d*\]", text, re.I)
    return {"issues": issues, "placeholders_to_protect": placeholders,
            "undefined_reference_markers": undefined_refs,
            "readability": C.flesch_kincaid(text),
            "note": "Fix source issues before translation; translation cost grows with source ambiguity."}

def cmd_feedback_loop(args):
    ws = _ws(args)
    corrections = ws.list_records("corrections")
    if not corrections:
        return {"processed": 0,
                "note": "No corrections recorded. Add via collections add --collection corrections, or via review decisions."}
    tm_added, terms_added = 0, 0
    proposals = []
    for c in corrections:
        if c.get("correction_type") == "terminology" and c.get("source") and c.get("target"):
            ws.add_record("terms", {"term": c["source"], "target": c["target"],
                                    "domain": c.get("domain"), "approval": "draft",
                                    "owner": "feedback-loop", "example": c.get("note")},
                          actor=args.actor)
            terms_added += 1
        elif c.get("correction_type") == "segment" and c.get("source") and c.get("target"):
            ws.add_record("segments", {"source": c["source"], "target": c["target"],
                                       "source_lang": c.get("source_lang", "en"),
                                       "target_lang": c.get("target_lang", "und"),
                                       "approval": "draft", "domain": c.get("domain")},
                          actor=args.actor)
            tm_added += 1
        else:
            proposals.append(c.get("id"))
    return {"corrections_processed": len(corrections), "terms_added_draft": terms_added,
            "tm_segments_added_draft": tm_added, "manual_triage": proposals,
            "note": "Feedback becomes DRAFT assets; a language lead approves before production use."}

def cmd_dictionary(args):
    """Local dictionary lookup from user-provided dictionary files (no network)."""
    if not args.dictionary:
        return {"term": args.term, "definitions": [], "unavailable": True,
                "note": "No local dictionary configured. Provide --dictionary path to a JSON {word: [defs]} or a plain dict-like file."}
    with open(args.dictionary, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    hits = data.get(args.term.lower()) or data.get(args.term)
    return {"term": args.term, "definitions": hits or [], "dictionary": os.path.basename(args.dictionary)}

def cmd_translate_plan(args):
    return {"project": args.name, "languages": [l.strip() for l in args.languages.split(",") if l.strip()],
            "domains": args.domains, "volume_words": args.volume_words,
            "assets": ["terminology database (terms)", "translation memory (segments)",
                       "style guide (keep with project assets)", "acceptance test set (benchmark.acceptance)"],
            "roles": ["project-manager", "translator", "bilingual-reviewer", "subject-reviewer", "proofreader", "qa"],
            "workflow": "pre-translation source edit -> MT (optional local adapter) -> human post-edit -> bilingual review -> proofread -> QA score -> approval gate",
            "acceptance_criteria": ["critical errors = 0", "QA score >= 95 (see qa.score / asset-error-taxonomy.json)",
                                    "terminology conformance 100% on approved terms",
                                    "publication gate approved (workflow.gate)"],
            "risks": ["low-resource language pair" if any(l.strip() in ("tl", "ceb", "ilo", "hil", "war") for l in args.languages.split(",")) else "none flagged",
                      "legal/medical domain requires subject-matter sign-off"]}

COMMANDS = {
    "langid": cmd_langid,
    "terms-add": cmd_terms_add, "terms-lookup": cmd_terms_lookup, "terms-import": cmd_terms_import,
    "terms-consistency": cmd_terms_consistency, "terms-acronyms": cmd_terms_acronyms,
    "terms-inflect": cmd_terms_inflect, "terms-entity-policy": cmd_terms_entity_policy,
    "terms-definition-audit": cmd_terms_definition_audit, "terms-glossary-build": cmd_terms_glossary_build,
    "tm-add": cmd_tm_add, "tm-match": cmd_tm_match, "tm-import-tmx": cmd_tm_import_tmx,
    "tm-export-tmx": cmd_tm_export_tmx, "tm-clean": cmd_tm_clean, "tm-align": cmd_tm_align,
    "qa-taxonomy": cmd_qa_taxonomy, "qa-score": cmd_qa_score, "acceptance": cmd_acceptance,
    "drift": cmd_drift,
    "translate-document": cmd_translate_document, "pivot-guard": cmd_pivot_guard,
    "backtranslation": cmd_backtranslation, "source-edit": cmd_source_edit,
    "feedback-loop": cmd_feedback_loop, "dictionary": cmd_dictionary, "translate-plan": cmd_translate_plan,
}

def build_parser(prog="rwt translate"):
    p = argparse.ArgumentParser(prog=prog, description="Translation engines: terms, TM, language ID, QA scoring, MT helpers.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("langid", help="Identify language (stopword/script based), whole document or per span")
    sp.add_argument("--text"); sp.add_argument("--file"); sp.add_argument("--spans", action="store_true")
    sp.set_defaults(func=cmd_langid)

    sp = sub.add_parser("terms-add", help="Add a term to the terminology database")
    sp.add_argument("--workspace", default="."); sp.add_argument("--term", required=True)
    sp.add_argument("--target"); sp.add_argument("--domain"); sp.add_argument("--definition")
    sp.add_argument("--forbidden-variants", default=""); sp.add_argument("--grammar")
    sp.add_argument("--example"); sp.add_argument("--owner"); sp.add_argument("--approve", action="store_true")
    sp.add_argument("--source-lang", default="en"); sp.add_argument("--target-lang", default="und")
    sp.add_argument("--do-not-translate", action="store_true"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_terms_add)

    sp = sub.add_parser("terms-lookup", help="Exact + fuzzy terminology lookup")
    sp.add_argument("--workspace", default="."); sp.add_argument("--query", required=True)
    sp.add_argument("--fuzzy", type=float, default=0.75); sp.set_defaults(func=cmd_terms_lookup)

    sp = sub.add_parser("terms-import", help="Import terms from JSON/JSONL")
    sp.add_argument("--workspace", default="."); sp.add_argument("--file", required=True)
    sp.add_argument("--approve", action="store_true"); sp.add_argument("--owner")
    sp.add_argument("--target-lang", default="und"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_terms_import)

    sp = sub.add_parser("terms-consistency", help="Find forbidden variants and case drift in a document")
    sp.add_argument("--workspace", default="."); sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_terms_consistency)

    sp = sub.add_parser("terms-acronyms", help="Check acronym first-use expansion and do-not-translate adherence")
    sp.add_argument("--file", required=True); sp.add_argument("--donottranslate", default=None,
                    help="Path to JSON list of DNT acronyms")
    sp.set_defaults(func=cmd_terms_acronyms)

    sp = sub.add_parser("terms-inflect", help="Rule-based inflection hints (en/es only)")
    sp.add_argument("--term", required=True); sp.add_argument("--lang", default="en")
    sp.set_defaults(func=cmd_terms_inflect)

    sp = sub.add_parser("terms-entity-policy", help="Named-entity translation policy")
    sp.add_argument("--entity-class", default=None)
    sp.set_defaults(func=cmd_terms_entity_policy)

    sp = sub.add_parser("terms-definition-audit", help="Find conflicting definitions of the same defined term")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_terms_definition_audit)

    sp = sub.add_parser("terms-glossary-build", help="Candidate glossary from the approved corpus (needs approval)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--min-freq", type=int, default=4)
    sp.set_defaults(func=cmd_terms_glossary_build)

    sp = sub.add_parser("tm-add", help="Add a translation unit")
    sp.add_argument("--workspace", default="."); sp.add_argument("--source", required=True)
    sp.add_argument("--target", required=True); sp.add_argument("--source-lang", default="en")
    sp.add_argument("--target-lang", default="es"); sp.add_argument("--domain"); sp.add_argument("--project")
    sp.add_argument("--quality", default="reviewed"); sp.add_argument("--approve", action="store_true")
    sp.add_argument("--actor", default="local"); sp.set_defaults(func=cmd_tm_add)

    sp = sub.add_parser("tm-match", help="Fuzzy-match translation memory")
    sp.add_argument("--workspace", default="."); sp.add_argument("--source", required=True)
    sp.add_argument("--source-lang"); sp.add_argument("--target-lang")
    sp.add_argument("--threshold", type=float, default=0.55); sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(func=cmd_tm_match)

    sp = sub.add_parser("tm-import-tmx", help="Import TMX (XML) translation memory as drafts")
    sp.add_argument("--workspace", default="."); sp.add_argument("--file", required=True)
    sp.add_argument("--domain"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_tm_import_tmx)

    sp = sub.add_parser("tm-export-tmx", help="Export approved TM to TMX")
    sp.add_argument("--workspace", default="."); sp.add_argument("--source-lang", default="en")
    sp.add_argument("--output"); sp.add_argument("--include-drafts", action="store_true")
    sp.set_defaults(func=cmd_tm_export_tmx)

    sp = sub.add_parser("tm-clean", help="Remove exact duplicates; flag misalignment/wrong-language/corruption")
    sp.add_argument("--workspace", default="."); sp.add_argument("--source-lang")
    sp.add_argument("--apply", action="store_true"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_tm_clean)

    sp = sub.add_parser("tm-align", help="Align source/target texts into TM candidate pairs")
    sp.add_argument("--source-file", required=True); sp.add_argument("--target-file", required=True)
    sp.add_argument("--window", type=int, default=3); sp.set_defaults(func=cmd_tm_align)

    sp = sub.add_parser("qa-taxonomy", help="Print the bundled translation error taxonomy and weights")
    sp.set_defaults(func=cmd_qa_taxonomy)

    sp = sub.add_parser("qa-score", help="Severity-weighted quality score from an errors JSON list")
    sp.add_argument("--errors", required=True, help="JSON list of {category, severity, note?}")
    sp.add_argument("--words", type=int, default=1000); sp.set_defaults(func=cmd_qa_score)

    sp = sub.add_parser("acceptance", help="Run the fixed acceptance test set against MT output")
    sp.add_argument("--output-file", required=True); sp.set_defaults(func=cmd_acceptance)

    sp = sub.add_parser("drift", help="Compare benchmark scores before/after a model or asset change")
    sp.add_argument("--baseline", required=True); sp.add_argument("--current", required=True)
    sp.add_argument("--tolerance", type=float, default=0.02); sp.set_defaults(func=cmd_drift)

    sp = sub.add_parser("translate-document", help="Analyze a document for document-level MT preparation (placeholders, tags, segments)")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_translate_document)

    sp = sub.add_parser("pivot-guard", help="Record pivot translation and require extra validation")
    sp.add_argument("--pivot-lang", default=None); sp.set_defaults(func=cmd_pivot_guard)

    sp = sub.add_parser("backtranslation", help="Compare original vs back-translated text; flag meaning-loss cues")
    sp.add_argument("--original", required=True); sp.add_argument("--backtranslation", required=True)
    sp.set_defaults(func=cmd_backtranslation)

    sp = sub.add_parser("source-edit", help="Pre-translation source check: length, negations, placeholders, readability")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_source_edit)

    sp = sub.add_parser("feedback-loop", help="Convert approved corrections into draft TM/terms assets")
    sp.add_argument("--workspace", default="."); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_feedback_loop)

    sp = sub.add_parser("dictionary", help="Look up a local dictionary JSON file (offline)")
    sp.add_argument("--term", required=True); sp.add_argument("--dictionary", default=None)
    sp.set_defaults(func=cmd_dictionary)

    sp = sub.add_parser("translate-plan", help="Skeleton a translation project plan")
    sp.add_argument("--name", required=True); sp.add_argument("--languages", required=True, help="comma-separated")
    sp.add_argument("--domains", default="general"); sp.add_argument("--volume-words", type=int, default=1000)
    sp.set_defaults(func=cmd_translate_plan)
    _build_parser_extra_tr(sub)
    return p

def run(argv):
    parser = build_parser(prog="rwt translate")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)

# ------------------------------------------------------------ additions
def cmd_terms_extract(args):
    text = open(args.file, encoding="utf-8").read()
    # candidate multiword terms: repeated TitleCase / lower-hyphen phrases >= min_freq
    phrases = re.findall(r"\b([A-Za-z][A-Za-z\-]{2,}(?:\s+[A-Za-z][A-Za-z\-]{2,}){0,3})\b", text)
    freq = Counter()
    stop_start = {"the", "and", "for", "with", "from", "that", "this", "these", "those", "into", "over", "under"}
    for ph in phrases:
        words = ph.split()
        if words[0].lower() in stop_start:
            continue
        if len(words) == 1 and words[0].lower() in C.STOPWORDS:
            continue
        freq[" ".join(words).lower()] += 1
    single = Counter(t for t in C.content_tokens(text) if len(t) >= args.min_length)
    candidates = ([{"term": t, "frequency": n, "type": "multiword"} for t, n in freq.most_common(60) if n >= args.min_freq and " " in t]
                  + [{"term": t, "frequency": n, "type": "single"} for t, n in single.most_common(60) if n >= args.min_freq])
    return {"candidates": candidates[:80], "min_frequency": args.min_freq,
            "review_required": True,
            "note": "Statistical candidates only; subject-matter experts validate domain terms and register them in terms."}

def cmd_low_resource(args):
    return {"language_pair": args.pair,
            "strategy": [
                "1. Seed the terminology database manually for the domain (terms-add with --approve after expert check)",
                "2. Grow a translation memory from approved human translations (tm-add / tm-import-tmx)",
                "3. Use the closest related-language model if any approved model exists, else human translation only",
                "4. Back-translate high-risk passages (translate backtranslation)",
                "5. Route 100% of segments through bilingual human review; MT confidence must NOT waive review",
                "6. Record explicit uncertainty per segment in the review queue",
                "7. Benchmark on a fixed acceptance set before release (benchmark.acceptance)"],
            "hard_limits": ["no fluent offline model may exist for this pair; do not fabricate",
                            "borrowings and code-switching must be documented, not silently normalized",
                            "untranslatable cultural terms: keep source + glossary note"],
            "acceptable_workflow": "human-first with tool assistance",
            "note": "Low-resource translation is a human workflow assisted by tooling; unreviewed MT output is not releasable."}

def cmd_domain_pack(args):
    ws = _ws(args)
    terms = ws.list_records("terms")
    segments = ws.list_records("segments")
    if args.domain:
        terms = [t for t in terms if t.get("domain") == args.domain]
        segments = [s for s in segments if s.get("domain") == args.domain]
    pack = {"domain": args.domain, "created": C.now_iso(),
            "terms": terms, "tm_segments": segments[:5000],
            "acceptance_tests_ref": "asset-benchmark-cases.json#acceptance_tests",
            "instructions": ["load into the target project via terms-import / tm-import-tmx",
                             "run benchmark.acceptance on the domain MT output",
                             "assign a domain reviewer before production use"]}
    out = args.output or os.path.join(ws.root, C.EXPORTS_DIR, f"domain-pack-{args.domain or 'all'}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(pack, fh, ensure_ascii=False, indent=2)
    ws.audit(args.actor, "domain-pack.build", {"domain": args.domain, "terms": len(terms), "segments": len(segments)})
    return {"pack": out, "terms": len(terms), "tm_segments": len(segments)}

def cmd_transliterate(args):
    text = args.text
    scripts = [name for name, rx in SCRIPT_RANGES if rx.search(text)]
    return {"input_scripts": scripts,
            "romanized": None,
            "reversibility": "original retained alongside any conversion; transliteration is lossful",
            "supported_now": "script detection + original retention only",
            "unavailable": True,
            "note": "Full transliteration requires an approved local engine with the specific standard (ISO 9, BGN/PCGN, Pinyin, Hepburn...). "
                    "Configure a local transliteration adapter; do not improvise letter substitutions. "
                    "The TM/terms stores always keep the original text for reversibility."}

def cmd_bilingual_review(args):
    with open(args.source, "r", encoding="utf-8") as fh:
        src = fh.read()
    with open(args.target, "r", encoding="utf-8") as fh:
        tgt = fh.read()
    src_sents = C.sentences(src)
    tgt_sents = C.sentences(tgt)
    issues = []
    src_nums = re.findall(r"-?\d+(?:[.,]\d+)*", src)
    tgt_flat = tgt.replace(",", ".")
    for n in src_nums:
        if n.replace(",", ".") not in tgt_flat and n.replace(".", ",") not in tgt:
            issues.append({"type": "number-drift", "source_number": n})
    neg = lambda t: bool(re.search(r"\b(no|not|never|without|sin|non|hindi)\b", t, re.I))
    if neg(src) != neg(tgt):
        issues.append({"type": "negation-changed", "source_negation": neg(src), "target_negation": neg(tgt)})
    modals_src = re.findall(r"\b(must|may|should|shall|can|might|could)\b", src, re.I)
    modals_tgt = re.findall(r"\b(must|may|should|shall|can|might|could|debe|puede|deve|peut|darf|muss)\b", tgt, re.I)
    if len(modals_src) != len(modals_tgt):
        issues.append({"type": "modality-count-mismatch", "source_modals": modals_src,
                       "target_modals": modals_tgt,
                       "note": "Modal verbs (must/may/should...) carry legal/obligation weight; count must survive translation"})
    len_ratio = len(tgt_sents) / max(1, len(src_sents))
    if len_ratio < 0.6 or len_ratio > 1.6:
        issues.append({"type": "sentence-count-mismatch", "source_sentences": len(src_sents),
                       "target_sentences": len(tgt_sents)})
    return {"issues": issues, "issue_count": len(issues),
            "verdict": "review-required" if issues else "mechanical-checks-clean",
            "note": "Omissions/additions/meaning shift need the human bilingual reviewer; this covers mechanical fidelity only."}

def cmd_proofread(args):
    text = open(args.file, encoding="utf-8").read()
    issues = []
    for m in re.finditer(r"\b(\w+)\s+\1\b", text, re.I):
        issues.append({"type": "doubled-word", "text": m.group(0)})
    for m in re.finditer(r"\s[,.;:!?]", text):
        issues.append({"type": "space-before-punctuation"})
    for m in re.finditer(r"[.!?][A-Za-z]", text):
        issues.append({"type": "missing-space-after-sentence", "context": m.group(0)})
    for m in re.finditer(r"\(\s*\)", text):
        issues.append({"type": "empty-parentheses"})
    fk = C.flesch_kincaid(text)
    return {"issues": issues[:80], "issue_count": len(issues),
            "readability": fk,
            "note": "Mechanical pass only; register and naturalness need the target-language reviewer."}

def cmd_domain_review(args):
    ws = _ws(args)
    text = open(args.file, encoding="utf-8").read()
    terms = ws.list_records("terms")
    if args.domain:
        terms = [t for t in terms if t.get("domain") == args.domain]
    low = text.lower()
    missing = [t["term"] for t in terms if t.get("target") and str(t["target"]).lower() not in low]
    return {"domain": args.domain, "domain_terms": len(terms),
            "approved_translations_missing": missing[:30],
            "verdict": "domain-review-required",
            "checklist": ["every approved domain term appears in its approved translation",
                          "calculations/figures identical to source",
                          "legal effect / clinical meaning preserved",
                          "standard identifiers retained (ISO, RA numbers...)"],
            "note": "The domain reviewer validates; the tool only checks term presence mechanically."}

COMMANDS.update({
    "terms-extract": cmd_terms_extract, "low-resource": cmd_low_resource,
    "domain-pack": cmd_domain_pack, "transliterate": cmd_transliterate,
    "bilingual-review": cmd_bilingual_review, "proofread": cmd_proofread,
    "domain-review": cmd_domain_review,
})

def _build_parser_extra_tr(sub):
    sp = sub.add_parser("terms-extract", help="Extract candidate domain terms for expert validation")
    sp.add_argument("--file", required=True); sp.add_argument("--min-freq", type=int, default=3)
    sp.add_argument("--min-length", type=int, default=5); sp.set_defaults(func=cmd_terms_extract)

    sp = sub.add_parser("low-resource", help="Workflow envelope for low-resource language pairs (human-first)")
    sp.add_argument("--pair", required=True, help="e.g. en-fil"); sp.set_defaults(func=cmd_low_resource)

    sp = sub.add_parser("domain-pack", help="Bundle domain TM + terminology + tests into a pack file")
    sp.add_argument("--workspace", default="."); sp.add_argument("--domain")
    sp.add_argument("--output"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_domain_pack)

    sp = sub.add_parser("transliterate", help="Script detection + original retention (full engine: configure adapter)")
    sp.add_argument("--text", required=True); sp.set_defaults(func=cmd_transliterate)

    sp = sub.add_parser("bilingual-review", help="Mechanical fidelity checks source vs translation (numbers, negation, modality)")
    sp.add_argument("--source", required=True); sp.add_argument("--target", required=True)
    sp.set_defaults(func=cmd_bilingual_review)

    sp = sub.add_parser("proofread", help="Target-language mechanical proofreading pass")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_proofread)

    sp = sub.add_parser("domain-review", help="Domain review checklist + approved-term presence check")
    sp.add_argument("--workspace", default="."); sp.add_argument("--file", required=True)
    sp.add_argument("--domain"); sp.set_defaults(func=cmd_domain_review)


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
