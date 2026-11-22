#!/usr/bin/env python3
"""tools-rwt-text.py - Text quality, style, structure and writing-support engines.

Capabilities: readability/stats, redundancy detection, structure check,
plain-language support, technical consistency, copy-edit heuristics, line-edit
heuristics, tone/register heuristics, logical-flow audit, conclusion-boundary
check, requirements (shall/should/may) extraction with clause references,
policy comparison matrix, extractive summarization, version comparison,
final-proof checks, publication-calibration lexicon scan.
"""
import argparse
import difflib
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
        spec = importlib.util.spec_from_file_location("rwt_core_t", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_t"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _read(file):
    if not os.path.isfile(file):
        raise C.ValidationError(f"File not found: {file}")
    with open(file, "r", encoding="utf-8") as fh:
        return fh.read()

def cmd_readability(args):
    text = _read(args.file)
    fk = C.flesch_kincaid(text)
    words = C.tokenize(text)
    long_words = [w for w in words if len(w) >= 13]
    passive = len(re.findall(r"\b(is|are|was|were|be|been|being)\s+\w+(ed|en)\b", text, re.I))
    return {**fk, "long_words_13plus": len(long_words),
            "long_word_share": round(len(long_words) / max(1, len(words)), 4),
            "passive_voice_constructions_estimate": passive,
            "note": "Syllable counts use a vowel-group heuristic; treat grade level as approximate."}

def cmd_stats(args):
    text = _read(args.file)
    words = C.tokenize(text)
    sents = C.sentences(text)
    return {"characters": len(text), "words": len(words),
            "unique_words": len(set(w.lower() for w in words)),
            "sentences": len(sents),
            "paragraphs": len([p for p in text.splitlines() if p.strip()]),
            "type_token_ratio": round(len(set(words)) / max(1, len(words)), 4)}

def cmd_redundancy(args):
    text = _read(args.file)
    sents = C.sentences(text)
    repeated_sentences = []
    seen = {}
    for i, s in enumerate(sents):
        key = C.normalize_ws(s).lower()
        if len(key) > 30:
            if key in seen:
                repeated_sentences.append({"first_sentence": seen[key] + 1,
                                           "repeat_sentence": i + 1,
                                           "text": s[:160]})
            else:
                seen[key] = i
    toks = C.content_tokens(text)
    n_in_range = args.ngram
    ngram_counts = Counter(tuple(toks[i:i + n_in_range]) for i in range(len(toks) - n_in_range + 1))
    repeated_ngrams = [{"ngram": " ".join(g), "count": c}
                       for g, c in ngram_counts.most_common(20) if c >= args.min_count]
    # near-duplicate paragraphs
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    near_paras = []
    for i in range(len(paras)):
        for j in range(i + 1, len(paras)):
            sim = C.shingle_jaccard(paras[i], paras[j], n=4)
            if sim >= 0.6:
                near_paras.append({"para_a": i + 1, "para_b": j + 1,
                                   "similarity": round(sim, 3)})
    return {"repeated_sentences": repeated_sentences,
            "repeated_content_ngrams": repeated_ngrams,
            "near_duplicate_paragraphs": near_paras[:20]}

def cmd_structure(args):
    text = _read(args.file)
    headings = re.findall(r"^(#{1,6}\s+.+|[A-Z][A-Z ]{4,}:|\d+(\.\d+)*\s+[A-Z][^\n]{3,80})$", text, re.M)
    sections = [h if isinstance(h, str) else h[0] for h in headings]
    required = [s.strip().lower() for s in args.required.split(",") if s.strip()]
    found = {r: any(r in (s or "").lower() for s in sections) for r in required}
    words = len(C.tokenize(text))
    return {"headings_detected": len(sections), "sections": sections[:60],
            "required_sections": found,
            "missing_sections": [r for r, ok in found.items() if not ok],
            "word_count": words,
            "balance": ("short" if words < args.min_words else "ok")}

PLAIN_SWAPS = {
    "utilize": "use", "approximately": "about", "commence": "start",
    "terminate": "end", "demonstrate": "show", "sufficient": "enough",
    "assistance": "help", "regarding": "about", "subsequent": "later",
    "prior to": "before", "in order to": "to", "due to the fact that": "because",
    "a large number of": "many", "at this point in time": "now",
    "notwithstanding": "despite", "aforementioned": "mentioned",
}

def cmd_plain_language(args):
    text = _read(args.file)
    low = text.lower()
    hits = [{"phrase": k, "suggestion": v} for k, v in PLAIN_SWAPS.items() if k in low]
    fk = C.flesch_kincaid(text)
    return {**fk, "simplification_candidates": hits,
            "note": "Suggestions preserve meaning; a domain reviewer approves changes to technical terms."}

def cmd_tech_consistency(args):
    text = _read(args.file)
    issues = []
    # abbreviation first-use
    for m in re.finditer(r"\b([A-Z]{2,6})\b", text):
        abbr = m.group(1)
        if abbr in ("I", "A"):
            continue
        expanded = re.search(r"\(" + re.escape(abbr) + r"\)", text) or re.search(r"[A-Za-z][A-Za-z ]+?\(" + re.escape(abbr) + r"\)", text)
        first = text.find(abbr)
        head = text[:first]
        if abbr not in head and not re.search(r"\([^)]*\)", head[-200:]):
            if text.count(abbr) > 0 and abbr not in ("DOI", "ISBN", "URL", "PDF", "XML", "JSON", "CSV", "API", "OK"):
                issues.append({"type": "abbreviation_first_use", "abbreviation": abbr,
                               "occurrences": text.count(abbr),
                               "note": "Define at first use or add to the do-not-translate list"})
    # units formatting: number directly followed by unit without space or with wrong case
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)(kg|mg|mL|ml|km|cm|mm|g|Hz|kHz|MHz|GHz)\b", text):
        if m.group(2) in ("ml",):
            issues.append({"type": "unit_case", "found": m.group(0), "suggested": m.group(1) + " mL"})
    # double space / stray markers
    for m in re.finditer(r"[ \t]{2,}", text):
        issues.append({"type": "double_space", "offset": m.start()})
        if len(issues) > 50:
            break
    # figure/table references vs definitions
    refs = set(re.findall(r"(?:Figure|Fig\.|Table)\s+(\d+)", text))
    defs = set(re.findall(r"^(?:Figure|Fig\.|Table)\s+(\d+)", text, re.M))
    missing_defs = sorted(refs - defs)
    if missing_defs:
        issues.append({"type": "figure_table_ref_without_definition", "numbers": missing_defs})
    return {"issues": issues[:120], "issue_count": len(issues)}

def cmd_copy_edit(args):
    text = _read(args.file)
    issues = []
    for m in re.finditer(r"\b([A-Za-z]+)\s+\1\b", text, re.I):
        issues.append({"type": "doubled_word", "text": m.group(0), "offset": m.start()})
    for m in re.finditer(r"\s+[,.;:]", text):
        issues.append({"type": "space_before_punctuation", "offset": m.start()})
    for m in re.finditer(r"\(\s*\)|\[\s*\]", text):
        issues.append({"type": "empty_brackets", "offset": m.start()})
    for m in re.finditer(r"\b(TODO|TBD|XXX|LOREM|FIX ME)\b", text, re.I):
        issues.append({"type": "placeholder_marker", "text": m.group(0), "offset": m.start()})
    for m in re.finditer(r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)*)\.\s*(\d{1,3})\b", text):
        issues.append({"type": "possible_number_format_mix", "text": m.group(0)[:40]})
    nums = re.findall(r"\b\d+\b", text)
    spelled_out = len(re.findall(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", text, re.I))
    return {"issues": issues[:120], "issue_count": len(issues),
            "digits_found": len(nums), "spelled_out_numbers": spelled_out,
            "note": "Mechanical checks only; grammar and house style need a copy editor."}

def cmd_line_edit(args):
    text = _read(args.file)
    sents = C.sentences(text)
    lengths = [len(C.tokenize(s)) for s in sents]
    overlong = [{"sentence": i + 1, "words": n, "text": sents[i][:160]}
                for i, n in enumerate(lengths) if n > args.max_words]
    starts = Counter(s.split()[0].lower() for s in sents if s.split())
    repeat_starts = {k: v for k, v in starts.items() if v >= 3}
    nominalizations = re.findall(r"\b\w+(?:tion|sion|ment|ance|ence|ity|ness)\b", text, re.I)
    return {"sentences": len(sents),
            "mean_sentence_words": round(sum(lengths) / max(1, len(lengths)), 2),
            "overlong_sentences": overlong[:30],
            "repeated_sentence_openers": repeat_starts,
            "nominalization_count": len(nominalizations),
            "note": "Flags for a line editor; meaning preservation is mandatory."}

def cmd_tone(args):
    text = _read(args.file)
    contractions = len(re.findall(r"\b\w+'(t|s|re|ve|ll|d|m)\b", text, re.I))
    first_person = len(re.findall(r"\b(I|me|my|mine|we|our|us)\b", text))
    hedges = len(re.findall(r"\b(may|might|could|perhaps|possibly|appears|suggests|seems)\b", text, re.I))
    boosters = len(re.findall(r"\b(clearly|obviously|undoubtedly|certainly|always|never|proves|guarantee)\b", text, re.I))
    imperative = len(re.findall(r"(?m)^(?:[A-Z][a-z]+ )?(?:[A-Z][a-z]+ )*[A-Z][a-z]+\.", text))
    if contractions > 5:
        register = "conversational/informal"
    elif first_person > 10:
        register = "first-person/engaged"
    elif hedges > boosters * 2:
        register = "cautious/qualified"
    elif boosters > 0:
        register = "assertive (verify evidence strength)"
    else:
        register = "neutral/formal"
    return {"contractions": contractions, "first_person_markers": first_person,
            "hedges": hedges, "boosters": boosters,
            "estimated_register": register,
            "note": "Heuristic estimate; match the declared register in the style guide."}

def cmd_logic_flow(args):
    text = _read(args.file)
    sents = C.sentences(text)
    transition_cues = len(re.findall(r"\b(however|therefore|thus|moreover|furthermore|consequently|nevertheless|meanwhile|in contrast|similarly|first|second|finally|in addition)\b", text, re.I))
    causal_cues = len(re.findall(r"\b(causes?|leads? to|results? in|because|due to|thereby|consequently)\b", text, re.I))
    counter_cues = len(re.findall(r"\b(however|although|despite|contrary|conversely|alternatively|on the other hand)\b", text, re.I))
    unsupported = re.findall(r"\b(clearly|proves?|undoubtedly|obviously|always|never|everyone knows)\b", text, re.I)
    return {"sentences": len(sents), "transition_cues": transition_cues,
            "causal_language_markers": causal_cues,
            "counterargument_markers": counter_cues,
            "overclaiming_markers": sorted(set(unsupported)),
            "ratio_transitions_per_sentence": round(transition_cues / max(1, len(sents)), 3),
            "note": "Causal language must match study design; correlation wording flagged for review."}

def cmd_conclusion_check(args):
    text = _read(args.file)
    # crude sectioning: assume last 'Conclusion' section or last 15% of text
    m = re.search(r"(?is)(conclusion[s]?|concluding remarks)\s*\n(.*)", text)
    body = m.group(2) if m else text[-max(400, len(text) // 6):]
    scope_markers = re.findall(r"\b(all|always|never|every|any|nothing|everyone|no one|worldwide|universally)\b", body, re.I)
    causal_claims = re.findall(r"\b(causes?|caused|leads? to|prevents?|cures?|eliminates?)\b", body, re.I)
    recommendations = re.findall(r"\b(should|must|recommend|policymakers should|therefore we)\b", body, re.I)
    limits_present = bool(re.search(r"\b(limitation|within this (study|scope)|caution|generaliz|external validity|further research)\b", body, re.I))
    return {"conclusion_excerpt": C.normalize_ws(body)[:500],
            "universal_scope_words": sorted(set(w.lower() for w in scope_markers)),
            "causal_claims": sorted(set(w.lower() for w in causal_claims)),
            "recommendation_sentences": len(recommendations),
            "limitations_acknowledged": limits_present,
            "verdict": "overclaim-risk" if (scope_markers or causal_claims) and not limits_present else "within-limits-plausible",
            "action": "Route to human approver if verdict is overclaim-risk; narrow population/period/jurisdiction wording."}

def cmd_requirements(args):
    text = _read(args.file)
    clause_re = re.compile(r"(?i)\b(shall|should|may|must|can)\b")
    # capture clause id if line starts like "4.2.1 " or "[R-12]"
    clause_marker = re.compile(r"^\s*(?:\[(?P<bid>[A-Za-z0-9\-\.]+)\]|(?P<num>\d+(?:\.\d+)+))\s+")
    current_clause = None
    results = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        cm = clause_marker.match(line)
        if cm:
            current_clause = cm.group("bid") or cm.group("num")
        for m in clause_re.finditer(line):
            kw = m.group(1).lower()
            kind = {"shall": "mandatory", "must": "mandatory", "should": "recommended",
                    "may": "permitted", "can": "capability"}[kw]
            results.append({"clause": current_clause, "modal": kw, "kind": kind,
                            "statement": C.normalize_ws(line)[:300]})
    definitions = re.findall(r"(?im)^\s*([A-Za-z][A-Za-z0-9 \-/]{1,40})\s*(?:is defined as|refers to|denotes)\b.*$", text)
    exceptions = [C.normalize_ws(l)[:200] for l in text.splitlines()
                  if re.search(r"(?i)\b(exception|except when|unless|provided that)\b", l)]
    verification = [C.normalize_ws(l)[:200] for l in text.splitlines()
                    if re.search(r"(?i)\b(verif(y|ication)|conformance|tested by|audit(ed)? by|shall be met)\b", l)]
    return {"requirements": results, "requirement_count": len(results),
            "definitions_detected": [d.strip() for d in definitions][:40],
            "exceptions": exceptions[:40], "verification_statements": verification[:40],
            "note": "Modal extraction is lexical; legal effect requires qualified review. Clause ids come from line numbering of the source text."}

def cmd_matrix(args):
    files = args.files.split(",")
    if len(files) < 2:
        raise C.ValidationError("Provide at least two policy files via --files a.txt,b.txt")
    COLUMNS = {"obligation": r"\b(shall|must|required)\b",
               "permission": r"\b(may|permitted|allowed)\b",
               "prohibition": r"\b(shall not|must not|prohibited)\b",
               "exception": r"\b(except|unless|provided that|excluding)\b",
               "deadline": r"\b(within \d+|no later than|by \d{4}|effective \d{4})\b",
               "enforcement": r"\b(penalt|fine|sanction|enforce|liabilit|offence|offense)\b"}
    matrix = []
    for f in files:
        text = _read(f.strip())
        row = {"file": f.strip()}
        for col, pattern in COLUMNS.items():
            row[col] = len(re.findall(pattern, text, re.I))
        row["effective_dates"] = sorted(set(re.findall(r"\b(?:effective|from)\s+(?:on\s+)?(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{4})\b", text, re.I)))
        matrix.append(row)
    return {"policy_files": len(files), "matrix": matrix,
            "note": "Counts are lexical signals; obligation scope, definitions and enforcement require legal/policy review."}

def cmd_summarize(args):
    text = _read(args.file)
    sents = C.sentences(text)
    if not sents:
        return {"summary": "", "note": "No sentences detected."}
    q = set(C.content_tokens(args.query)) if args.query else None
    freq = Counter(C.content_tokens(text))
    scored = []
    for i, s in enumerate(sents):
        toks = C.content_tokens(s)
        if not toks:
            continue
        base = sum(freq[t] for t in toks) / math.sqrt(len(toks))
        if q:
            base *= 1.0 + 2.0 * (len(set(toks) & q) / max(1, len(q)))
        position_boost = 1.2 if i < 2 else 1.0
        scored.append((base * position_boost, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = sorted(scored[:args.sentences], key=lambda x: x[1])
    return {"summary": " ".join(s for _score, _i, s in chosen),
            "sentences_returned": len(chosen),
            "method": "extractive frequency + optional query salience (no generative model)",
            "abstract_vs_translation": "extractive summary; not a translation layer"}

import math

def cmd_compare_versions(args):
    a = _read(args.file_a)
    b = _read(args.file_b)
    sent_a = C.sentences(a)
    sent_b = C.sentences(b)
    sm = difflib.SequenceMatcher(None, sent_a, sent_b)
    changes = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            changes.append({"op": tag,
                            "a_sentences": sent_a[i1:i2][:5],
                            "b_sentences": sent_b[j1:j2][:5]})
    return {"changes": changes, "change_blocks": len(changes),
            "similarity": round(sm.ratio(), 4),
            "claim_level_review_note": "Sentence-level alignment is a starting point; claim-level comparison requires reviewer judgment."}

def cmd_final_proof(args):
    text = _read(args.file)
    issues = []
    for m in re.finditer(r"\b(TODO|TBD|XXX|LOREM|PLACEHOLDER|FIGURE ?X|TABLE ?X|\?\?\?)\b", text, re.I):
        issues.append({"type": "residual_placeholder", "text": m.group(0), "offset": m.start()})
    double_spaces = [m.start() for m in re.finditer(r"[ \t]{2,}", text)]
    if double_spaces:
        issues.append({"type": "double_spaces", "count": len(double_spaces)})
    fig_refs = [int(n) for n in re.findall(r"(?:Figure|Fig\.)\s*(\d+)", text)]
    tab_refs = [int(n) for n in re.findall(r"Table\s+(\d+)", text)]
    for label, seq in (("figure", fig_refs), ("table", tab_refs)):
        if seq:
            missing = [n for n in range(1, max(seq) + 1) if n not in seq]
            if missing:
                issues.append({"type": f"{label}_numbering_gap", "missing": missing})
    cit = len(C.content_tokens(text))
    return {"issues": issues, "word_count": cit,
            "ok": not issues,
            "note": "Layout/rendering of the final file needs the deliverable toolchain (see deliver.reopen / deliver.pdf-preflight)."}

def cmd_calibration(args):
    text = _read(args.file)
    over = re.findall(r"\b(proves?|undoubtedly|obviously|clearly|always|never|all|none|guarantee[sd]?|certain(?:ly)?)\b", text, re.I)
    calibrated = re.findall(r"\b(may|might|could|appears?|suggests?|likely|probably|uncertain|limited evidence|cannot exclude|insufficient)\b", text, re.I)
    hard = len(over)
    soft = len(calibrated)
    ratio = soft / (hard + soft) if (hard + soft) else 1.0
    return {"overconfident_markers": len(over), "calibrated_markers": soft,
            "calibration_ratio": round(ratio, 3),
            "high_risk_passages": [C.normalize_ws(s)[:200] for s in C.sentences(text)
                                   if re.search(r"\b(proves?|undoubtedly|always|never|all|none|guarantee)\b", s, re.I)][:10],
            "recommendation": "Replace absolute claims with calibrated language or remove the claim; abstain if the corpus cannot support it."}

COMMANDS = {
    "readability": cmd_readability, "stats": cmd_stats, "redundancy": cmd_redundancy,
    "structure": cmd_structure, "plain-language": cmd_plain_language,
    "tech-consistency": cmd_tech_consistency, "copy-edit": cmd_copy_edit,
    "line-edit": cmd_line_edit, "tone": cmd_tone, "logic-flow": cmd_logic_flow,
    "conclusion-check": cmd_conclusion_check, "requirements": cmd_requirements,
    "matrix": cmd_matrix, "summarize": cmd_summarize, "compare-versions": cmd_compare_versions,
    "final-proof": cmd_final_proof, "calibration": cmd_calibration,
}

def build_parser(prog="rwt text"):
    p = argparse.ArgumentParser(prog=prog, description="Writing and editing engines.")
    sub = p.add_subparsers(dest="action", metavar="action")

    def add_file_cmd(name, fn, helptext):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--file", required=True)
        sp.set_defaults(func=fn)
        return sp

    add_file_cmd("readability", cmd_readability, "Flesch-Kincaid grade/ease + long-word and passive-voice estimates")
    add_file_cmd("stats", cmd_stats, "Character/word/sentence/paragraph counts, type-token ratio")

    sp = sub.add_parser("redundancy", help="Repeated sentences, content n-grams and near-duplicate paragraphs")
    sp.add_argument("--file", required=True); sp.add_argument("--ngram", type=int, default=6)
    sp.add_argument("--min-count", type=int, default=3); sp.set_defaults(func=cmd_redundancy)

    sp = sub.add_parser("structure", help="Detect headings; check required sections present")
    sp.add_argument("--file", required=True)
    sp.add_argument("--required", default="introduction,methods,results,discussion,conclusion")
    sp.add_argument("--min-words", type=int, default=300)
    sp.set_defaults(func=cmd_structure)

    add_file_cmd("plain-language", cmd_plain_language, "Flag bureaucratic phrases with plain alternatives + readability")
    add_file_cmd("tech-consistency", cmd_tech_consistency, "Abbreviation first-use, unit case, double spaces, figure/table refs")
    add_file_cmd("copy-edit", cmd_copy_edit, "Mechanical checks: doubled words, punctuation spacing, placeholders")

    sp = sub.add_parser("line-edit", help="Overlong sentences, repeated openers, nominalizations")
    sp.add_argument("--file", required=True); sp.add_argument("--max-words", type=int, default=40)
    sp.set_defaults(func=cmd_line_edit)

    add_file_cmd("tone", cmd_tone, "Estimate register (formal/cautious/assertive/conversational) from markers")
    add_file_cmd("logic-flow", cmd_logic_flow, "Transition/causal/counterargument marker audit")
    add_file_cmd("conclusion-check", cmd_conclusion_check, "Detect conclusions exceeding scope (universal words, causal claims)")
    add_file_cmd("requirements", cmd_requirements, "Extract shall/should/may statements with clause references")

    sp = sub.add_parser("matrix", help="Compare obligation/exception/deadline/enforcement signals across policy files")
    sp.add_argument("--files", required=True, help="Comma-separated list of >=2 text files")
    sp.set_defaults(func=cmd_matrix)

    sp = sub.add_parser("summarize", help="Extractive summary (no generative model)")
    sp.add_argument("--file", required=True); sp.add_argument("--query", default=None)
    sp.add_argument("--sentences", type=int, default=4); sp.set_defaults(func=cmd_summarize)

    sp = sub.add_parser("compare-versions", help="Sentence-level alignment diff of two versions")
    sp.add_argument("--file-a", required=True); sp.add_argument("--file-b", required=True)
    sp.set_defaults(func=cmd_compare_versions)

    add_file_cmd("final-proof", cmd_final_proof, "Residual placeholders, numbering gaps, double spaces")
    add_file_cmd("calibration", cmd_calibration, "Overconfident vs calibrated language scan with high-risk passages")
    _build_parser_extra(sub, add_file_cmd)
    return p

def run(argv):
    parser = build_parser(prog="rwt text")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)

# ------------------------------------------------------------ additions
def cmd_dev_edit(args):
    text = _read(args.file)
    sents = C.sentences(text)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    words = len(C.tokenize(text))
    headings = re.findall(r"^(#{1,6}\s+.+|\d+(\.\d+)*\s+[A-Z][^\n]{3,80})$", text, re.M)
    issues = []
    if words < 200:
        issues.append({"issue": "very short document", "hint": "scaffold under-developed for the declared deliverable"})
    if len(headings) < 2 and words > 800:
        issues.append({"issue": "long document without section structure"})
    mean_para = (sum(len(p.split()) for p in paras) / len(paras)) if paras else 0
    if mean_para > 220:
        issues.append({"issue": "paragraphs overlong on average", "mean_paragraph_words": round(mean_para, 1)})
    counterarg = bool(re.search(r"\b(however|although|critics|counterargument|limitations?|alternative(ly)? explanation)\b", text, re.I))
    if not counterarg:
        issues.append({"issue": "no counterargument/limitations signal detected"})
    transitions = len(re.findall(r"\b(however|therefore|moreover|furthermore|consequently|in contrast|similarly)\b", text, re.I))
    return {"words": words, "paragraphs": len(paras), "headings": len(headings),
            "mean_paragraph_words": round(mean_para, 1),
            "transition_cues": transitions,
            "counterargument_present": counterarg,
            "issues": issues,
            "action_order": ["fix missing sections/scope first", "reorder evidence flow",
                             "strengthen claims-evidence match (claims coverage)",
                             "then do sentence-level editing (line-edit)"],
            "note": "Developmental passes happen BEFORE sentence editing."}

def cmd_audience(args):
    text = _read(args.file)
    fk = C.flesch_kincaid(text)
    profiles = {
        "expert": {"grade_target": (12, 20), "note": "keep technical density; define only non-standard terms"},
        "executive": {"grade_target": (8, 12), "note": "lead with decision-relevant finding; cut method detail to appendix"},
        "public": {"grade_target": (6, 9), "note": "concrete examples; avoid jargon; active voice"},
        "student": {"grade_target": (9, 12), "note": "explain terms; worked examples; check definitions"},
        "operational": {"grade_target": (8, 12), "note": "imperative structure, numbered steps, checklists"},
    }
    if args.audience not in profiles:
        raise C.ValidationError(f"audience must be one of {sorted(profiles)}")
    prof = profiles[args.audience]
    grade = fk["grade_level"]
    lo, hi = prof["grade_target"]
    fit = "within-target" if (grade is not None and lo <= grade <= hi) else ("simplify" if (grade or 0) > hi else "deepen-or-extend")
    return {"audience": args.audience, "grade_level": grade, "reading_ease": fk["reading_ease"],
            "target_grade_range": [lo, hi], "fit": fit, "guidance": prof["note"],
            "note": "Re-voice from the same approved evidence; never add new claims for the audience version."}

def cmd_tracked_revision(args):
    a = _read(args.file_a)
    b = _read(args.file_b)
    sent_a = C.sentences(a)
    sent_b = C.sentences(b)
    sm = difflib.SequenceMatcher(None, sent_a, sent_b)
    revisions = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        removed = sent_a[i1:i2]
        added = sent_b[j1:j2]
        joined = " ".join(added)
        if tag == "replace":
            reason = "wording/precision" if any(x in joined.lower() for x in ("may", "might", "suggests")) else "content change"
            category = "clarity" if any(x in joined.lower() for x in ("for example", "that is", "in other words")) else ("accuracy" if re.search(r"\d", joined) else "style")
        elif tag == "insert":
            reason = "added material"
            category = "structure"
        else:
            reason = "removed material"
            category = "redundancy" if not added else "accuracy"
        revisions.append({"op": tag, "category": category, "reason": reason,
                          "removed": removed[:3], "added": added[:3]})
    groups = {}
    for r in revisions:
        groups.setdefault(r["category"], []).append(r)
    return {"categories": {k: len(v) for k, v in groups.items()},
            "revisions": revisions,
            "categories_order": ["accuracy", "structure", "clarity", "style", "consistency"],
            "note": "Category heuristics assist explanation; the editor signs off each accepted change."}

def cmd_similarity_scan(args):
    ws = _ws(args)
    text = _read(args.file)
    docs = ws.list_records("documents")
    matches = []
    shingles_q = set(C.ngrams(C.tokenize(text), 5))
    for d in docs:
        if not d.get("text"):
            continue
        sim = C.shingle_jaccard(text, d["text"], n=5)
        if sim >= args.threshold:
            sim_tokens = set(C.ngrams(C.tokenize(d["text"]), 5))
            shared = len(shingles_q & sim_tokens)
            matches.append({"document_id": d.get("id"), "title": d.get("title"),
                            "overlap": round(sim, 4), "shared_shingles": shared,
                            "license": d.get("license")})
    matches.sort(key=lambda m: -m["overlap"])
    return {"file": args.file, "documents_compared": len(docs),
            "matches_above_threshold": matches,
            "verdict": "overlap-review-needed" if matches else "no-overlap-detected",
            "note": "Overlap ≠ plagiarism. Common phrases, methods sections and boilerplate legitimately overlap; a human interprets."}

def cmd_similarity_patchwrite(args):
    def _read_file(f):
        return _read(f)
    source = _read_file(args.source)
    suspect = _read_file(args.suspect)
    src_sents = C.sentences(source)
    sus_sents = C.sentences(suspect)
    flags = []
    for i, s in enumerate(sus_sents):
        for j, t in enumerate(src_sents):
            sim = C.sim_ratio(s.lower(), t.lower())
            if 0.55 <= sim < 0.92:  # too close to be independent, too different to be verbatim
                flags.append({"suspect_sentence": i + 1, "source_sentence": j + 1,
                              "similarity": round(sim, 3),
                              "suspect_excerpt": s[:180], "source_excerpt": t[:180]})
    return {"flags": flags[:50], "count": len(flags),
            "band": "0.55-0.92 similarity = patchwriting band",
            "note": "Flags paraphrase that stays structurally close to the source; patchwriting still requires attribution and quotation marks for close wording."}

def cmd_attribution_check(args):
    text = _read(args.file)
    quotes = re.findall(r"[“\"]([^”\"]{25,})[”\"]", text)
    cited_near_quote = 0
    for q in quotes:
        idx = text.find(q)
        window = text[max(0, idx - 400):idx + len(q) + 400]
        if PAREN_CITE_RE.search(window) or DOI_RE.search(window) or re.search(r"\[\d+\]", window):
            cited_near_quote += 1
    numbers = len(re.findall(r"\b\d+(?:\.\d+)?\s*%", text))
    chart_numbers = numbers  # heuristic: percents typically data-derived
    return {"quotations_found": len(quotes),
            "quotations_with_nearby_citation": cited_near_quote,
            "quotations_missing_nearby_citation": len(quotes) - cited_near_quote,
            "percent_datapoints": chart_numbers,
            "note": "Data points, images and translated material also require attribution; this scan covers quotations only. Numbers need a source-linked evidence path (claims coverage)."}

PAREN_CITE_RE = re.compile(r"\([A-Z][^()]{0,80}\d{4}[a-z]?[^()]*\)")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)

# register new commands
COMMANDS.update({
    "dev-edit": cmd_dev_edit, "audience": cmd_audience, "tracked-revision": cmd_tracked_revision,
    "similarity-scan": cmd_similarity_scan, "similarity-patchwrite": cmd_similarity_patchwrite,
    "attribution-check": cmd_attribution_check,
})

def _build_parser_extra(sub, add_file_cmd):
    add_file_cmd("dev-edit", cmd_dev_edit, "Developmental edit: structure, scope, paragraph balance, counterarguments")
    sp = sub.add_parser("audience", help="Audience-fit estimate (expert/executive/public/student/operational)")
    sp.add_argument("--file", required=True); sp.add_argument("--audience", default="public")
    sp.set_defaults(func=cmd_audience)
    sp = sub.add_parser("tracked-revision", help="Diff two versions; categorize reasons for changes")
    sp.add_argument("--file-a", required=True); sp.add_argument("--file-b", required=True)
    sp.set_defaults(func=cmd_tracked_revision)
    sp = sub.add_parser("similarity-scan", help="Shingle-overlap of a file against workspace documents (review, not verdict)")
    sp.add_argument("--file", required=True); sp.add_argument("--workspace", default=".")
    sp.add_argument("--threshold", type=float, default=0.15); sp.set_defaults(func=cmd_similarity_scan)
    sp = sub.add_parser("similarity-patchwrite", help="Detect paraphrases structurally too close to the source")
    sp.add_argument("--source", required=True); sp.add_argument("--suspect", required=True)
    sp.set_defaults(func=cmd_similarity_patchwrite)
    add_file_cmd("attribution-check", cmd_attribution_check, "Check quotations carry nearby citations")


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
