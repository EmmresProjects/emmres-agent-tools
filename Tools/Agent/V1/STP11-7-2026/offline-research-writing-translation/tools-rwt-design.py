#!/usr/bin/env python3
"""tools-rwt-design.py - Research design, study analysis and writing-support engines.

Structured assistive generators: question refinement, study-design matching,
domain checklists (sampling, measurement, protocol, ethics, causal, survey,
limitations, visualization), qualitative coding support (local codebook,
excerpt coding, inter-coder kappa), and writing scaffolds that bind every
generated paragraph to evidence ids or mark it UNVERIFIED. These tools never
fabricate citations or empirical results; they organize expert judgment.
"""
import argparse
import json
import os
import re
import sys

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_des", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_des"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

QUESTION_TEMPLATES = {
    "descriptive": "What is the {attribute} of {population} in {context} as of {period}?",
    "comparative": "How does {attribute} differ between {population_a} and {population_b} in {context}?",
    "causal": "What is the effect of {intervention} on {outcome} among {population} in {context}, compared with {comparator}?",
    "interpretive": "How do {population} understand {phenomenon} in {context}?",
    "legal": "Does {instrument} authorize/require/prohibit {action} for {population} in {jurisdiction}?",
    "policy": "What are the trade-offs of {option_a} vs {option_b} for {outcome} in {context}?",
}

def cmd_question(args):
    kind = args.kind.lower()
    if kind not in QUESTION_TEMPLATES:
        raise C.ValidationError(f"kind must be one of {sorted(QUESTION_TEMPLATES)}")
    tpl = QUESTION_TEMPLATES[kind]
    filled = tpl
    missing = []
    for field in re.findall(r"\{([a-z_]+)\}", tpl):
        value = getattr(args, field, None)
        if value:
            filled = filled.replace("{" + field + "}", value)
        else:
            missing.append(field)
    return {"kind": kind, "template": tpl, "refined_question": filled,
            "missing_fields": missing,
            "quality_checks": ["answerable from available corpus/data?", "specific population?",
                               "explicit outcome and comparator?", "timebound?", "jurisdiction if legal/policy?"],
            "note": "A refined question is negotiated with the reviewer; this scaffold only structures it."}

DESIGN_MATCH = [
    ("effect of X on Y", ["randomized controlled trial", "quasi-experimental", "regression discontinuity"], "experiment"),
    ("association between", ["cohort study", "cross-sectional study", "case-control"], "observational"),
    ("how many / prevalence", ["cross-sectional survey", "census", "registry analysis"], "descriptive"),
    ("experience / perception", ["interviews", "focus groups", "ethnography", "narrative inquiry"], "qualitative"),
    ("meaning of policy in practice", ["case study", "process tracing", "document analysis"], "interpretive"),
    ("legal question", ["doctrinal analysis", "comparative law", "statutory interpretation"], "legal"),
    ("implementation", ["realist evaluation", "implementation science framework", "process evaluation"], "evaluative"),
]

def cmd_study_advisor(args):
    q = args.question.lower()
    matches = []
    for cue, designs, family in DESIGN_MATCH:
        if any(word in q for word in cue.split()):
            matches.append({"trigger": cue, "family": family, "designs": designs})
    if not matches:
        matches.append({"trigger": "(no cue matched)", "family": "descriptive",
                        "designs": ["clarify the question first (design.question)"]})
    return {"question": args.question, "candidate_designs": matches,
            "note": "Advisory matching only. Feasibility, ethics and resources decide among candidates; the researcher and IRB/ethics board decide."}

CHECKLISTS = {
    "sampling": ["population and frame defined?", "inclusion/exclusion criteria explicit?",
                 "recruitment method avoids selection bias?", "target size justified (power/saturation)?",
                 "clustering accounted for (design effect)?", "attrition/missing data plan?",
                 "representativeness assessed (not assumed)?"],
    "measurement": ["construct defined and operationalized?", "instrument validity evidence (content/construct/criterion)?",
                    "reliability reported (alpha/kappa/ICC)?", "sensitivity/specificity where diagnostic?",
                    "cultural adaptation/translation validated?", "measurement invariance across groups?"],
    "protocol": ["randomization method described?", "allocation concealment?", "blinding and who was blinded?",
                 "control condition adequate?", "intervention fidelity monitored?",
                 "outcome timing prespecified?", "stopping rules / interim analyses?",
                 "preregistration id recorded?"],
    "ethics": ["consent process appropriate (voluntary, informed)?", "vulnerable groups safeguards?",
               "data minimization and retention limits?", "compensation non-coercive?",
               "risks and benefits balanced?", "review board approval obtained/planned?",
               "conflict of interest disclosed?"],
    "causal": ["exchangeability plausible (confounders measured)?", "positivity/overlap assessed?",
               "consistency (well-defined intervention)?", "no interference/spillover or addressed?",
               "no unmeasured confounding sensitivity analysis?", "model assumptions stated and checked?",
               "reverse causation addressed?"],
    "survey": ["wording neutral and single-barreled?", "no leading/loaded items?",
               "ordering effects considered?", "response options exhaustive/mutually exclusive?",
               "skip logic tested?", "accessibility (screen readers, low literacy)?",
               "translation validated for each language version?", "respondent burden estimated?"],
}

def cmd_checklist(args):
    kind = args.kind
    if kind not in CHECKLISTS:
        raise C.ValidationError(f"kind must be one of {sorted(CHECKLISTS)}")
    items = CHECKLISTS[kind]
    answers = {}
    if args.answers and os.path.isfile(args.answers):
        with open(args.answers, "r", encoding="utf-8") as fh:
            answers = json.load(fh)
    results = []
    for i, item in enumerate(items, start=1):
        key = str(i)
        results.append({"n": i, "item": item,
                        "answer": answers.get(key),
                        "status": "pass" if answers.get(key) is True else ("fail" if answers.get(key) is False else "unrecorded")})
    fails = sum(1 for r in results if r["status"] == "fail")
    unrec = sum(1 for r in results if r["status"] == "unrecorded")
    return {"checklist": kind, "items": results, "fails": fails, "unrecorded": unrec,
            "verdict": "gaps-found" if fails else ("incomplete" if unrec else "checklist-satisfied"),
            "note": "Answers are yours; the tool only structures and scores them. Ethics determinations belong to the review board."}

def cmd_coding(args):
    if not os.path.isfile(args.codebook):
        raise C.ValidationError(f"Codebook not found: {args.codebook}")
    with open(args.codebook, "r", encoding="utf-8") as fh:
        codebook = json.load(fh)  # {"CODE": "definition", ...}
    if not os.path.isfile(args.excerpts):
        raise C.ValidationError(f"Excerpts not found: {args.excerpts}")
    with open(args.excerpts, "r", encoding="utf-8") as fh:
        excerpts = json.load(fh)
    if isinstance(excerpts, dict):
        excerpts = excerpts.get("excerpts", [])
    results = []
    for i, ex in enumerate(excerpts):
        text = str(ex.get("text", "")).lower()
        matched = []
        for code, definition in codebook.items():
            cue = str(definition).lower()
            keywords = [w for w in re.findall(r"[a-z]{5,}", cue)][:6]
            hits = sum(1 for k in keywords if k in text)
            if hits >= 2:
                matched.append({"code": code, "keyword_hits": hits})
        results.append({"excerpt_id": ex.get("id", i + 1), "matched_codes": matched,
                        "reviewer_decision": "pending"})
    return {"excerpts_coded": len(results), "results": results,
            "note": "Keyword pre-coding only. Memos, disagreements and final coding are the researchers'; use numbers kappa for inter-coder agreement."}

def cmd_survey(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    issues = []
    for m in re.finditer(r"\b(and|or)\b", text, re.I):
        pass  # double-barreled needs sentence context; flag candidates below
    for sent in C.sentences(text):
        low = sent.lower()
        if re.search(r"\b(and|or)\b", low) and re.search(r"\b(happy|satisfied|confident|comfortable)\b", low):
            issues.append({"type": "possible-double-barreled", "sentence": sent[:160]})
        if re.search(r"\b(always|never|everyone|no one|obviously|of course)\b", low):
            issues.append({"type": "leading-or-absolute", "sentence": sent[:160]})
        if len(C.tokenize(sent)) > 30:
            issues.append({"type": "overlong-item", "words": len(C.tokenize(sent)), "sentence": sent[:160]})
    likert = re.findall(r"\b(strongly agree|agree|neutral|disagree|strongly disagree)\b", text, re.I)
    return {"items_flagged": issues, "likert_anchors_found": len(likert),
            "checks": ["double-barreled", "leading/absolute wording", "overlong items", "likert anchors"],
            "note": "Heuristic flags; the survey methodologist owns final wording and order."}

def cmd_viz(args):
    with open(args.csv, "r", encoding="utf-8") as fh:
        lines = [l for l in fh if l.strip()]
    header = lines[0].strip().split(",") if lines else []
    rows = []
    for line in lines[1:]:
        try:
            rows.append([float(x) for x in line.strip().split(",")])
        except ValueError:
            rows.append(line.strip().split(","))
    issues = []
    if len(header) < 2:
        issues.append("fewer than 2 columns — not chartable")
    numeric_cols = 0
    if rows and all(isinstance(c, list) for c in rows):
        numeric_cols = sum(1 for i in range(len(header)) if all(isinstance(r[i], float) for r in rows))
    if numeric_cols == 0:
        issues.append("no fully-numeric columns found")
    max_val = max((max(r) for r in rows if all(isinstance(v, float) for v in r)), default=None)
    return {"columns": header, "rows": len(rows), "numeric_columns": numeric_cols,
            "max_value": max_val,
            "guidance": ["bar charts for category comparison (zero baseline)",
                         "line charts for time series",
                         "scatter with labeled axes for relationships",
                         "always show uncertainty (error bars/CIs) when estimates",
                         "direct labels > legends; colorblind-safe palette",
                         "never truncate a bar axis"],
            "issues": issues}

def cmd_limitations(args):
    prompts = {
        "design": "What design constraints (observational, self-report, cross-sectional) limit causal/temporal claims?",
        "sample": "Who is missing or underrepresented; is attrition differential?",
        "measurement": "What measurement error or proxy use could bias estimates, and in which direction?",
        "scope": "Which populations, settings, periods and jurisdictions do the findings NOT cover?",
        "analysis": "What assumptions underlie the analysis; which are untestable here?",
        "generalizability": "Where should readers not transport these findings?",
    }
    answers = {}
    if args.answers and os.path.isfile(args.answers):
        with open(args.answers, "r", encoding="utf-8") as fh:
            answers = json.load(fh)
    out = [{"dimension": k, "prompt": v, "stated": bool(answers.get(k)), "content": answers.get(k)}
           for k, v in prompts.items()]
    missing = [o["dimension"] for o in out if not o["stated"]]
    return {"dimensions": out, "unstated_dimensions": missing,
            "note": "Limitations must be stated in the paper itself; this ensures none were skipped in drafting."}

OUTLINE_SECTIONS = {
    "article": ["Title", "Abstract", "Introduction (question + gap)", "Methods (corpus, search, screening)",
                "Results (findings, not interpretation)", "Discussion (interpretation vs prior evidence)",
                "Limitations", "Conclusion (within evidence boundary)", "References", "Appendices"],
    "report": ["Title page", "Executive summary", "Background & objectives", "Methodology",
               "Findings", "Risks & limitations", "Recommendations", "Appendices", "References"],
    "policy": ["Problem statement", "Evidence summary", "Options", "Trade-offs",
               "Implementation path", "Monitoring & evaluation", "References"],
    "legal-memo": ["Issue", "Short answer", "Governing authority", "Analysis",
                   "Counterarguments", "Uncertainty", "Conclusion"],
}

def cmd_outline(args):
    kind = args.kind
    if kind not in OUTLINE_SECTIONS:
        raise C.ValidationError(f"kind must be one of {sorted(OUTLINE_SECTIONS)}")
    return {"kind": kind,
            "sections": [{"n": i + 1, "heading": s, "evidence_slots": "bind each material claim to evidence ids [E#]"}
                         for i, s in enumerate(OUTLINE_SECTIONS[kind])],
            "note": "Every section: question it answers → evidence it needs → claim it supports. No orphan sections."}

def cmd_draft(args):
    sections = []
    evidence = []
    if args.evidence and os.path.isfile(args.evidence):
        with open(args.evidence, "r", encoding="utf-8") as fh:
            evidence = json.load(fh)
        if isinstance(evidence, dict):
            evidence = evidence.get("evidence", [])
    brief = args.brief or ""
    body = [f"# {args.title}", "\n> DRAFT — every material claim is bound to an evidence id or marked UNVERIFIED. Human review required.\n"]
    if brief:
        body.append(f"**Working brief:** {brief}\n")
    if evidence:
        body.append("## Evidence map\n")
        for i, e in enumerate(evidence, start=1):
            eid = e.get("id", f"E{i}")
            body.append(f"- [{eid}] {e.get('claim', e.get('text', ''))[:160]} (locator: {e.get('locator', '?')}, relation: {e.get('relation', '?')})")
        body.append("\n## Draft sections\n")
        body.append("(Write discipline: each sentence with a factual claim carries its [E#]; inference sentences are marked [INFERENCE]; unknowns are UNVERIFIED.)\n")
        for i, e in enumerate(evidence, start=1):
            eid = e.get("id", f"E{i}")
            body.append(f"[Section using {eid}]: Evidence states: “{str(e.get('text', e.get('claim', '')))[:300]}” [{eid}].")
    else:
        body.append("## No evidence supplied\nAll claims would be UNVERIFIED. Ingest sources and link evidence first (corpus ingest; claims link).")
    body.append("\n## Integrity\n- No citation is invented.\n- UNVERIFIED claims block publication at the readiness gate.\n- Human reviewer approves before any external use.")
    out = "\n".join(body)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    return {"draft_markdown": out if not args.output else None,
            "output": args.output, "evidence_items": len(evidence),
            "unverified_claims": max(0, len([e for e in evidence if not e.get("text") and not e.get("claim")])),
            "note": "This scaffold binds structure to evidence; it does not write prose for you, and it never invents sources."}

def cmd_methods(args):
    ws = None
    try:
        ws = C.Workspace(args.workspace)
        ws.load()
    except Exception:
        pass
    stats = {}
    if ws:
        stats = {"documents": len(ws.list_records("documents")),
                 "claims": len(ws.list_records("claims")),
                 "evidence_links": len(ws.list_records("evidence")),
                 "registry_entries": len(ws.list_records("registry")),
                 "decisions": len(ws.list_records("decisions")),
                 "snapshot_id": C.sha256_bytes("".join(
                     C.json_dump(r) + "\n" for r in ws.list_records("documents")).encode())}
    body = f"""# Methods (auto-assembled from workspace records)

## Corpus
The review corpus comprises {stats.get('documents', 0)} documents registered in the workspace (snapshot identifier {stats.get('snapshot_id', 'n/a')}).
Ingestion, cleaning and deduplication followed the corpus pipeline (see knowledge-readme.md).

## Search & screening
Searches were executed with the local TF-IDF/boolean engine; screening decisions and conflicts are recorded in the review queue and decision log ({stats.get('decisions', 0)} decisions recorded).

## Evidence extraction
{stats.get('evidence_links', 0)} evidence links were recorded against {stats.get('claims', 0)} material claims.

## Models & tools
All models used are recorded in the registry ({stats.get('registry_entries', 0)} entries, kind=model). Tool version: rwt {C.TOOL_VERSION}.

## Reproducibility
A reproducible run manifest (provenance run-manifest) and corpus snapshot identifier accompany this report. Restore and re-verification instructions are provided in the handoff archive.
"""
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(body)
    return {"methods_markdown": None if args.output else body, "output": args.output, "stats": stats,
            "note": "Numbers come from the workspace itself; edit wording, never the recorded counts."}

COMMANDS = {
    "question": cmd_question, "study-advisor": cmd_study_advisor, "checklist": cmd_checklist,
    "coding": cmd_coding, "survey": cmd_survey, "viz": cmd_viz, "limitations": cmd_limitations,
    "outline": cmd_outline, "draft": cmd_draft, "methods": cmd_methods,
}

def build_parser(prog="rwt design"):
    p = argparse.ArgumentParser(prog=prog, description="Research design + writing-support engines.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("question", help="Refine a broad topic into an answerable question")
    sp.add_argument("--kind", required=True, choices=sorted(QUESTION_TEMPLATES))
    for f in ("attribute", "population", "population_a", "population_b", "context", "period",
              "intervention", "comparator", "outcome", "phenomenon", "instrument", "action",
              "jurisdiction", "option_a", "option_b"):
        sp.add_argument(f"--{f.replace('_', '-')}", default=None)
    sp.set_defaults(func=cmd_question)

    sp = sub.add_parser("study-advisor", help="Match a question to candidate study designs (advisory)")
    sp.add_argument("--question", required=True); sp.set_defaults(func=cmd_study_advisor)

    sp = sub.add_parser("checklist", help="Domain checklist (sampling|measurement|protocol|ethics|causal|survey)")
    sp.add_argument("--kind", required=True, choices=sorted(CHECKLISTS))
    sp.add_argument("--answers", default=None, help="JSON {item_number: true|false}")
    sp.set_defaults(func=cmd_checklist)

    sp = sub.add_parser("coding", help="Qualitative pre-coding against a local codebook (human decides)")
    sp.add_argument("--codebook", required=True); sp.add_argument("--excerpts", required=True)
    sp.set_defaults(func=cmd_coding)

    sp = sub.add_parser("survey", help="Heuristic survey-item review (double-barreled, leading, overlong)")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_survey)

    sp = sub.add_parser("viz", help="CSV chartability + visualization guidance")
    sp.add_argument("--csv", required=True); sp.set_defaults(func=cmd_viz)

    sp = sub.add_parser("limitations", help="Ensure design/sample/measurement/scope/analysis limits are stated")
    sp.add_argument("--answers", default=None); sp.set_defaults(func=cmd_limitations)

    sp = sub.add_parser("outline", help="Evidence-bound outline for article|report|policy|legal-memo")
    sp.add_argument("--kind", required=True, choices=sorted(OUTLINE_SECTIONS))
    sp.set_defaults(func=cmd_outline)

    sp = sub.add_parser("draft", help="Evidence-bound draft scaffold (never invents citations)")
    sp.add_argument("--title", required=True); sp.add_argument("--brief", default="")
    sp.add_argument("--evidence", default=None, help="JSON list of evidence records {id?, claim?, text?, locator?, relation?}")
    sp.add_argument("--output"); sp.set_defaults(func=cmd_draft)

    sp = sub.add_parser("methods", help="Auto-assemble a Methods section skeleton from workspace records")
    sp.add_argument("--workspace", default="."); sp.add_argument("--output")
    sp.set_defaults(func=cmd_methods)
    return p

def run(argv):
    parser = build_parser(prog="rwt design")
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
