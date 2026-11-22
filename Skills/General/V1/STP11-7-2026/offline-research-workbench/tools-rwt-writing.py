"""tools-rwt-writing.py - drafting scaffolds, editing, style, readability and conclusion boundaries.

Drafting is evidence-bound: generated prose only reuses stored, source-linked claims and marks
every gap with an explicit placeholder instead of inventing support. Editing tools report
measurable findings with locations so a human editor keeps authority over the text.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import statistics

NAME = "writing"
ROLE = "outline/draft scaffolds, readability, style lint, redundancy, flow, revision and boundary checks"
GROUPS = [("writing", "Drafting scaffolds, editing, style, readability, revisions and boundary checks")]
deps: dict = {}

WEASEL = ["some say", "many believe", "it is said", "critics argue", "experts agree",
          "studies show", "research shows", "it is well known", "arguably", "clearly",
          "obviously", "everyone knows"]
INTENSIFIERS = ["very", "really", "extremely", "highly", "quite", "rather", "totally", "absolutely",
                "definitely", "completely"]
OVERCLAIM = ["proves", "proven", "conclusively", "undeniably", "irrefutably", "always", "never",
             "all studies", "no evidence at all", "settled science", "guarantees"]
NOMINALIZATION_RE = re.compile(r"\b\w{4,}(?:tion|ment|ness|ity|ance|ence|ization|isation)\b", re.I)
PASSIVE_RE = re.compile(r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w+ed|\w+en|"
                        r"made|done|shown|found|given|taken|seen|known)\b", re.I)
HEDGE_RE = re.compile(r"\b(may|might|could|possibly|likely|probably|appears?|suggests?|"
                      r"indicates?|tends? to|approximately|about|roughly)\b", re.I)
TRANSITIONS = ["however", "therefore", "moreover", "furthermore", "in contrast", "consequently",
               "similarly", "nevertheless", "for example", "in addition", "first", "second",
               "finally", "in summary", "overall"]
TONE_PROFILES = {
    "formal": {"contractions": 0, "first_person": 0, "hedging_max": 0.35, "exclamations": 0},
    "neutral": {"contractions": 3, "first_person": 0, "hedging_max": 0.5, "exclamations": 0},
    "cautious": {"contractions": 0, "first_person": 0, "hedging_min": 0.15, "exclamations": 0},
    "persuasive": {"contractions": 6, "first_person": 6, "hedging_max": 0.6, "exclamations": 0},
    "instructional": {"contractions": 4, "first_person": 4, "hedging_max": 0.4, "exclamations": 0},
    "conversational": {"contractions": 20, "first_person": 20, "hedging_max": 0.8, "exclamations": 2},
}
SECTIONS = {
    "academic": ["Abstract", "1 Introduction", "2 Methods", "3 Results", "4 Discussion",
                 "5 Limitations", "6 Conclusion", "References"],
    "technical": ["Summary", "1 Context", "2 Requirements", "3 Method", "4 Findings",
                  "5 Risks", "6 Recommendations", "Appendix A Evidence"],
    "policy": ["Executive summary", "1 Problem", "2 Evidence", "3 Options", "4 Trade-offs",
               "5 Implementation", "6 Monitoring", "References"],
    "legal": ["Issue", "Governing authority", "Analysis", "Counterarguments", "Conclusion"],
    "plain": ["What we asked", "What we found", "What it means", "What we still do not know"],
}


def _store():
    return deps["store"]


def _body(ctx, text=None, input_path=None, require=True):
    store = _store()
    if input_path:
        if not os.path.exists(input_path):
            raise store.ToolError(2, f"input not found: {input_path}")
        return store.read_text(input_path)
    if text:
        return text
    if require:
        raise store.ToolError(2, "provide --input or --text")
    return ""


def syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and count > 1 and not word.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


def readability_metrics(body: str) -> dict:
    store = _store()
    sents = store.sentences(body)
    toks = [t for t in re.findall(r"[A-Za-z']+", body)]
    if not sents or not toks:
        raise store.ToolError(2, "text has no analyzable sentences")
    words_per_sentence = len(toks) / len(sents)
    syl = sum(syllables(t) for t in toks)
    syllables_per_word = syl / len(toks)
    flesch = 206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word
    fk_grade = 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59
    long_sents = [s for s in sents if _store().words(s) > 30]
    passive = len(PASSIVE_RE.findall(body))
    return {
        "words": len(toks), "sentences": len(sents),
        "avg_sentence_words": round(words_per_sentence, 2),
        "max_sentence_words": max(_store().words(s) for s in sents),
        "long_sentence_share": round(len(long_sents) / len(sents), 3),
        "flesch_reading_ease": round(flesch, 2),
        "flesch_kincaid_grade": round(fk_grade, 2),
        "avg_syllables_per_word": round(syllables_per_word, 3),
        "passive_voice_hits": passive,
        "passive_share": round(passive / len(sents), 3),
        "nominalizations": len(NOMINALIZATION_RE.findall(body)),
        "hedge_share": round(len(HEDGE_RE.findall(body)) / len(sents), 3),
        "longest_sentences": [s[:180] for s in sorted(long_sents, key=lambda s: -_store().words(s))[:5]],
        "language_note": "readability formulas are calibrated for English; for other languages only "
                         "structural metrics (sentence length, passive/hedge counts) are meaningful",
    }


def op_readability(ctx, text=None, input_path=None, threshold_grade: float = 14.0) -> dict:
    metrics = readability_metrics(_body(ctx, text, input_path))
    exceed = metrics["flesch_kincaid_grade"] > threshold_grade
    return {"status": "ok", "metrics": metrics, "grade_threshold": threshold_grade,
            "passes_threshold": not exceed,
            "recommendation": ("shorten sentences and unpack long noun phrases" if exceed else
                               "readability is within the configured threshold")}


def op_outline(ctx, question: str, kind: str = "academic", source_ids: str | None = None) -> dict:
    store = _store()
    if kind not in SECTIONS:
        raise store.ToolError(2, f"kind must be one of {list(SECTIONS)}")
    conn = ctx.conn()
    ids = [s.strip() for s in source_ids.split(",")] if source_ids else None
    claims = []
    for row in conn.execute("SELECT id,source_id,text,ctype,status FROM claims ORDER BY created"):
        if ids and row["source_id"] not in ids:
            continue
        claims.append(dict(row))
    concepts = deps["retrieval"].op_decompose(ctx, question)["concepts"]
    relevant = [c for c in claims
                if set(concepts) & {t for t in store.tokens(c["text"])}][:20] or claims[:10]
    outline = []
    for index, section in enumerate(SECTIONS[kind], 1):
        mapped = relevant[(index - 1) * 2:(index - 1) * 2 + 2]
        outline.append({"number": index, "section": section,
                        "purpose": _section_purpose(section),
                        "evidence": [{"claim_id": c["id"], "text": c["text"][:160],
                                      "source_id": c["source_id"]} for c in mapped],
                        "evidence_gap": not mapped})
    return {"status": "ok", "question": question, "kind": kind, "outline": outline,
            "claims_available": len(claims),
            "gap_sections": [o["section"] for o in outline if o["evidence_gap"]],
            "rule": "every factual section must map to stored claims before drafting"}


def _section_purpose(section: str) -> str:
    lookup = {
        "abstract": "state question, method, principal findings and limits without new claims",
        "introduction": "frame the question and what evidence exists",
        "methods": "document corpus, search, sampling, tools and exclusions reproducibly",
        "results": "report observations and values separately from interpretation",
        "discussion": "interpret against prior evidence, alternatives and limits",
        "limitations": "state design limits, uncertainty and transferability",
        "conclusion": "answer only what the evidence supports",
        "references": "list every cited source in the chosen style",
        "problem": "define the problem, population and jurisdiction",
        "evidence": "summarise what the local evidence shows, with citations",
        "options": "compare options against criteria",
        "trade-offs": "state costs, risks and distributional effects",
        "implementation": "state who does what, when, with what constraint",
        "monitoring": "state indicators, data sources and review frequency",
        "issue": "state the legal question precisely",
        "governing authority": "list binding authority with jurisdiction and effective dates",
        "analysis": "apply authority to facts, with counterarguments",
        "counterarguments": "state the strongest opposing reading",
    }
    for key, value in lookup.items():
        if key in section.lower():
            return value
    return "develop the section from stored evidence only"


def op_draft(ctx, section: str, source_ids: str | None = None, max_claims: int = 8,
             style: str = "academic", output: str | None = None) -> dict:
    """Assemble an evidence-bound section skeleton from stored claims (never invents support)."""
    store = _store()
    conn = ctx.conn()
    params, where = [], ""
    if source_ids:
        ids = [s.strip() for s in source_ids.split(",") if s.strip()]
        where = " AND source_id IN (" + ",".join("?" * len(ids)) + ")"
        params = ids
    rows = [dict(r) for r in conn.execute(
        f"SELECT id,source_id,text,ctype,status FROM claims WHERE status IN ('extracted','approved'){where} "
        f"ORDER BY created LIMIT ?", params + [max(1, min(int(max_claims), 50))])]
    if not rows:
        raise store.ToolError(2, "no stored claims to draft from; run 'extract claims --store' first")
    lines = [f"## {section}", ""]
    for index, claim in enumerate(rows, 1):
        marker = "" if claim["status"] == "approved" else " *(unreviewed extraction)*"
        cite = f"[{claim['source_id']}]"
        lines.append(f"{index}. {claim['text']} {cite}{marker}")
    gaps = []
    for claim in rows:
        evidence = conn.execute("SELECT count(*) c FROM claim_evidence WHERE claim_id=?",
                                (claim["id"],)).fetchone()["c"]
        if not evidence:
            gaps.append(claim["id"])
    if gaps:
        lines += ["", "> [EVIDENCE NEEDED] link or verify these claims before publication: "
                      + ", ".join(gaps[:10])]
    lines += ["", f"> Draft assembled from {len(rows)} stored claims; style profile: {style}. "
                  "Every sentence carries a source id and must be verified by a reviewer."]
    body = "\n".join(lines) + "\n"
    out = ctx.out_path(output or f"draft-{store.slug(section, 30)}.md")
    store.write_text(out, body)
    return {"status": "ok", "output": out, "section": section, "claims_used": len(rows),
            "unverified_claims": gaps, "words": store.words(body),
            "next_commands": ["verify claim --claim \"<text>\"", "writing proofread --input " + out]}


def op_style(ctx, text=None, input_path=None, profile: str = "academic",
             max_findings: int = 100) -> dict:
    store = _store()
    body = _body(ctx, text, input_path)
    style_guide = {}
    guide_path = os.path.join(store.base_dir(), "asset-style-guide.json")
    if os.path.exists(guide_path):
        with open(guide_path, encoding="utf-8") as fh:
            style_guide = json.load(fh)
    banned = style_guide.get("banned_terms", {}).get(profile, [])
    findings = []

    def add(rule, severity, message, match):
        findings.append({"rule": rule, "severity": severity, "message": message,
                         "excerpt": (match or "")[:160]})

    for term in WEASEL:
        for m in re.finditer(rf"\b{re.escape(term)}\b", body, re.I):
            add("weasel-attribution", "major", f"'{term}' attributes a claim to unnamed sources", m.group(0))
    for m in re.finditer(r"\b(" + "|".join(map(re.escape, OVERCLAIM)) + r")\b", body, re.I):
        add("overclaim", "critical", f"'{m.group(0)}' exceeds typical evidence strength", m.group(0))
    for m in re.finditer(r"\b(" + "|".join(map(re.escape, INTENSIFIERS)) + r")\b", body, re.I):
        add("intensifier", "minor", f"'{m.group(0)}' weakens precise prose", m.group(0))
    for m in NOMINALIZATION_RE.finditer(body):
        add("nominalization", "minor", f"'{m.group(0)}' may hide the actor; consider a verb", m.group(0))
    for m in PASSIVE_RE.finditer(body):
        add("passive-voice", "minor", "passive construction hides the actor", m.group(0))
    for term in banned:
        for m in re.finditer(rf"\b{re.escape(term)}\b", body, re.I):
            add("house-style-banned", "major", f"'{term}' is banned by the {profile} style guide", m.group(0))
    for m in re.finditer(r"\b(\w+)\s+\1\b", body, re.I):
        add("duplicated-word", "major", "duplicated word", m.group(0))
    for m in re.finditer(r"[ \t]{2,}", body):
        add("double-space", "minor", "double space", "(whitespace)")
    if re.search(r"[\u2018\u2019\u201c\u201d]", body) and style_guide.get("quotes") == "straight":
        add("quote-style", "minor", "curly quotes conflict with configured style", "typography")
    counts = {}
    for item in findings:
        counts[item["rule"]] = counts.get(item["rule"], 0) + 1
    critical = [f for f in findings if f["severity"] == "critical"]
    return {"status": "ok", "profile": profile, "findings": findings[:max_findings],
            "finding_count": len(findings), "by_rule": counts,
            "critical_count": len(critical),
            "passes": not critical and len(findings) <= max(3, store.words(body) // 200),
            "note": "style findings are suggestions for a human editor, not automatic rewrites"}


def op_redundancy(ctx, text=None, input_path=None, threshold: float = 0.8) -> dict:
    store = _store()
    body = _body(ctx, text, input_path)
    sents = store.sentences(body)
    duplicates = []
    for i in range(len(sents)):
        for j in range(i + 1, len(sents)):
            a, b = store.shingles(sents[i], 4), store.shingles(sents[j], 4)
            if not a or not b:
                continue
            score = store.jaccard(a, b)
            if score >= threshold:
                duplicates.append({"a_index": i, "b_index": j, "similarity": round(score, 3),
                                   "a": sents[i][:180], "b": sents[j][:180]})
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if store.words(p) > 25]
    para_dupes = []
    for i in range(len(paragraphs)):
        for j in range(i + 1, len(paragraphs)):
            score = store.jaccard(store.shingles(paragraphs[i], 5), store.shingles(paragraphs[j], 5))
            if score >= 0.6:
                para_dupes.append({"a_index": i, "b_index": j, "similarity": round(score, 3)})
    return {"status": "ok", "sentences": len(sents), "duplicate_sentences": duplicates[:50],
            "duplicate_sentence_count": len(duplicates),
            "repeated_paragraphs": para_dupes[:20],
            "repetition_score": round(len(duplicates) / (len(sents) or 1), 3),
            "note": "repetition can be deliberate emphasis; check each pair before deleting"}


def op_flow(ctx, text=None, input_path=None) -> dict:
    store = _store()
    body = _body(ctx, text, input_path)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    issues = []
    if len(paragraphs) > 1:
        for index in range(1, len(paragraphs)):
            start = " ".join(store.tokens(paragraphs[index])[:6])
            if not any(t in start for t in TRANSITIONS) and index % 2 == 0:
                issues.append({"kind": "missing-transition", "paragraph_index": index,
                               "excerpt": paragraphs[index][:120],
                               "severity": "minor"})
    causal = []
    for sent in store.sentences(body):
        if re.search(r"\b(because|due to|caused by|therefore|thus|as a result)\b", sent, re.I) and \
                not re.search(r"\b(randomi[sz]ed|experiment|trial|natural experiment|"
                              r"difference-in-differences|instrumental|regression discontinuity|"
                              r"counterfactual|identification strategy)\b", body, re.I):
            causal.append({"sentence": sent[:200], "issue": "causal language without an identified design",
                           "severity": "major"})
    uncited = [s[:200] for s in store.sentences(body)
               if re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent)\b", s)
               and not re.search(r"\[[^\]]+\]|\([A-Z][A-Za-z\-]+,\s*\d{4}", s)]
    conclusion = paragraphs[-1] if paragraphs else ""
    unreferenced_conclusion = bool(conclusion) and not re.search(r"\[[^\]]+\]|\([A-Z][A-Za-z\-]+,\s*\d{4}",
                                                                 conclusion)
    premises = [p[:160] for p in paragraphs[:-1]][:10]
    return {"status": "ok", "paragraphs": len(paragraphs), "issues": issues[:20],
            "causal_language_flags": causal[:20], "uncited_quantities": uncited[:20],
            "conclusion_lacks_citation": unreferenced_conclusion, "premises": premises,
            "checks": ["premise ordering", "transitions", "causal language", "quantities without support",
                       "conclusion dependence on premises"]}


def op_revise(ctx, original_path: str, revised_path: str, output: str | None = None) -> dict:
    store = _store()
    for path in (original_path, revised_path):
        if not os.path.exists(path):
            raise store.ToolError(2, f"file not found: {path}")
    before = store.read_text(original_path).splitlines()
    after = store.read_text(revised_path).splitlines()
    edits = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes():
        if tag == "equal":
            continue
        removed = " ".join(before[i1:i2]).strip()
        added = " ".join(after[j1:j2]).strip()
        reason = "clarity"
        if re.search(r"\d", removed + added):
            reason = "accuracy"
        elif re.search(r"\b(however|therefore|furthermore|in contrast)\b", added, re.I):
            reason = "structure"
        elif re.search(r"\b(shall|must|should|may)\b", removed + added, re.I):
            reason = "accuracy"
        elif len(added.split()) < len(removed.split()):
            reason = "clarity"
        elif re.search(r"[A-Z]{2,}\b", added) and not re.search(r"[A-Z]{2,}\b", removed):
            reason = "consistency"
        edits.append({"category": reason, "line": i1 + 1, "removed": removed[:300], "added": added[:300]})
    summary = {}
    for edit in edits:
        summary[edit["category"]] = summary.get(edit["category"], 0) + 1
    payload = {"status": "ok", "edits": edits[:200], "edit_count": len(edits),
               "by_category": summary,
               "unified_diff": "".join(difflib.unified_diff(before, after, "original", "revised",
                                                            lineterm="\n"))[:20000]}
    if output:
        lines = [f"## Tracked revision ({len(edits)} edits)", ""]
        for edit in edits:
            lines.append(f"- **{edit['category']}** (line {edit['line']}): `{edit['removed'][:120]}` -> "
                         f"`{edit['added'][:120]}`")
        payload["output"] = store.write_text(ctx.out_path(output), "\n".join(lines) + "\n")
    return payload


def op_boundary(ctx, draft_path: str, evidence_max_year: str | None = None) -> dict:
    """Prevent conclusions and recommendations from exceeding the evidence scope."""
    store = _store()
    body = _body(ctx, input_path=draft_path)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    conclusion = paragraphs[-1] if paragraphs else body
    overreach_terms = re.findall(r"\b(all|every|always|never|any|no one|none|entire|universal|"
                                 r"proves?|establishes? conclusively)\b", conclusion, re.I)
    years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", body)]
    draft_year = max(years) if years else None
    jurisdictions = sorted({j for j in ("philippines", "eu", "us", "uk", "asean", "international")
                            if re.search(rf"\b{j}\b", conclusion, re.I)})
    citations = re.findall(r"\[([^\]]+)\]", conclusion)
    issues = []
    if overreach_terms:
        issues.append({"kind": "scope-overreach", "severity": "major",
                       "terms": sorted({t.lower() for t in overreach_terms}),
                       "message": "absolute language in the conclusion is rarely supported by local evidence"})
    if not citations:
        issues.append({"kind": "unsupported-conclusion", "severity": "critical",
                       "message": "conclusion carries no citation back to evidence"})
    if evidence_max_year and draft_year and draft_year > int(evidence_max_year):
        issues.append({"kind": "period-overreach", "severity": "major",
                       "message": f"draft refers to {draft_year} but evidence ends at {evidence_max_year}"})
    if len(jurisdictions) > 1:
        issues.append({"kind": "jurisdiction-spread", "severity": "minor",
                       "message": f"conclusion spans {jurisdictions}; state that findings are jurisdiction-specific"})
    return {"status": "ok", "conclusion_excerpt": conclusion[:400],
            "absolute_terms": sorted({t.lower() for t in overreach_terms}),
            "citations": citations[:10], "jurisdictions": jurisdictions,
            "issues": issues, "passed": not [i for i in issues if i["severity"] == "critical"],
            "required_caveat": ("State the population, period and jurisdiction the evidence covers, and "
                                "what it does not cover." if issues else "No boundary issue detected.")}


def op_summary(ctx, input_path: str, words: int = 180, output: str | None = None) -> dict:
    """Extractive, source-linked summary (no new claims are generated)."""
    store = _store()
    body = _body(ctx, input_path=input_path)
    sents = store.sentences(body)
    if len(sents) < 3:
        raise store.ToolError(2, "text is too short to summarise")
    freq = {}
    for token in store.tokens(body):
        if token in store.STOPWORDS["en"] or len(token) < 4:
            continue
        freq[token] = freq.get(token, 0) + 1
    scored = []
    for index, sent in enumerate(sents):
        toks = [t for t in store.tokens(sent) if t in freq]
        if not toks:
            continue
        density = sum(freq[t] for t in toks) / len(toks)
        position_bonus = 1.2 if index < 2 else (1.1 if index == len(sents) - 1 else 1.0)
        numeric_bonus = 1.15 if re.search(r"\d", sent) else 1.0
        cite_bonus = 1.1 if re.search(r"\[[^\]]+\]", sent) else 1.0
        scored.append((density * position_bonus * numeric_bonus * cite_bonus, index, sent))
    scored.sort(key=lambda x: -x[0])
    chosen, total = [], 0
    for score, index, sent in scored:
        if total + store.words(sent) > words:
            continue
        chosen.append((index, sent))
        total += store.words(sent)
    chosen.sort()
    summary = " ".join(s for _, s in chosen)
    out = ctx.out_path(output or "summary.md")
    store.write_text(out, summary + "\n")
    return {"status": "ok", "output": out, "summary": summary, "words": store.words(summary),
            "sentences_used": [i for i, _ in chosen],
            "method": "extractive frequency/position scoring; no paraphrase, no new claims"}


def op_tone(ctx, text=None, input_path=None, target: str = "formal") -> dict:
    store = _store()
    body = _body(ctx, text, input_path)
    if target not in TONE_PROFILES:
        raise store.ToolError(2, f"target must be one of {list(TONE_PROFILES)}")
    sents = max(1, len(store.sentences(body)))
    metrics = {
        "contractions": len(re.findall(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", body)),
        "first_person": len(re.findall(r"\b(I|we|our|us|my)\b", body)),
        "hedges": len(HEDGE_RE.findall(body)),
        "hedge_share": round(len(HEDGE_RE.findall(body)) / sents, 3),
        "exclamations": body.count("!"),
        "questions": body.count("?"),
        "imperatives": len(re.findall(r"^(?:Use|Do|Check|Record|Verify|Ensure)\b", body, re.M)),
    }
    profile = TONE_PROFILES[target]
    violations = []
    if metrics["contractions"] > profile.get("contractions", 999):
        violations.append(f"{metrics['contractions']} contractions exceed the {target} budget of "
                          f"{profile['contractions']}")
    if metrics["first_person"] > profile.get("first_person", 999):
        violations.append(f"{metrics['first_person']} first-person markers exceed the {target} budget")
    if "hedging_max" in profile and metrics["hedge_share"] > profile["hedging_max"]:
        violations.append(f"hedge share {metrics['hedge_share']} exceeds {profile['hedging_max']}")
    if "hedging_min" in profile and metrics["hedge_share"] < profile["hedging_min"]:
        violations.append(f"hedge share {metrics['hedge_share']} is below the cautious minimum "
                          f"{profile['hedging_min']}")
    if metrics["exclamations"] > profile.get("exclamations", 0):
        violations.append("exclamation marks are not allowed in this register")
    return {"status": "ok", "target": target, "metrics": metrics, "violations": violations,
            "in_register": not violations}


def op_consistency(ctx, text=None, input_path=None) -> dict:
    store = _store()
    body = _body(ctx, text, input_path)
    issues = []
    abbreviations = {}
    for m in re.finditer(r"\(([A-Z]{2,6})\)", body):
        abbreviations.setdefault(m.group(1), 0)
        abbreviations[m.group(1)] += 1
    for abbr in abbreviations:
        if not re.search(rf"\b{abbr}\b[^(]{{0,40}}\(", body) and not re.search(
                rf"\b[A-Z][\w\- ]{{4,60}}\s\({abbr}\)", body):
            issues.append({"kind": "undefined-abbreviation", "value": abbr, "severity": "major"})
    for abbr in abbreviations:
        used = len(re.findall(rf"\b{abbr}\b", body))
        if used > 1 and not re.search(rf"\({abbr}\)", body):
            issues.append({"kind": "abbreviation-not-expanded", "value": abbr, "severity": "major"})
    units = set(re.findall(r"\b(kg|lb|lbs|km|mi|miles|kilometers|metres|meters|celsius|fahrenheit|"
                           r"%|percent)\b", body, re.I))
    groups = {"metric": {"kg", "km", "kilometers", "metres", "meters", "celsius"},
              "imperial": {"lb", "lbs", "mi", "miles", "fahrenheit"},
              "percent": {"%", "percent"}}
    present = {name for name, members in groups.items() if {u.lower() for u in units} & members}
    if len(present) > 1:
        issues.append({"kind": "mixed-unit-systems", "systems": sorted(present),
                       "message": "state values in one system and convert the other explicitly",
                       "severity": "minor"})
    headings = [(len(m.group(1)), m.group(2).strip()) for m in
                re.finditer(r"^(#{1,6})\s+(.+)$", body, re.M)]
    numbered = [h for h in headings if re.match(r"^\d+(\.\d+)*\s", h[1])]
    numbers = [h[1].split()[0].rstrip(".") for h in numbered]
    if numbers and numbers != [str(i + 1) for i in range(len(numbers))] and \
            numbers != [f"{i + 1}." for i in range(len(numbers))]:
        issues.append({"kind": "heading-numbering", "found": numbers[:12], "severity": "major"})
    figures = set(re.findall(r"\b(?:Figure|Fig\.)\s+(\d+)", body))
    refs = set(re.findall(r"\b(?:Table)\s+(\d+)", body))
    return {"status": "ok", "abbreviations": abbreviations, "units": sorted(units),
            "headings": len(headings), "issues": issues, "issue_count": len(issues),
            "figures_referenced": sorted(figures, key=int), "tables_referenced": sorted(refs, key=int),
            "passed": not [i for i in issues if i["severity"] == "major"]}


def op_proofread(ctx, text=None, input_path=None) -> dict:
    store = _store()
    body = _body(ctx, text, input_path)
    issues = []
    patterns = [
        ("unresolved-placeholder", r"\b(TODO|TBD|XXX|FIXME|TK|\[insert[^\]]*\]|\{\{[^}]*\}\})", "critical"),
        ("double-space", r"[ \t]{2,}", "minor"),
        ("space-before-punctuation", r"\s+[,.;:](?!\d)", "minor"),
        ("missing-space-after-period", r"\.[A-Z][a-z]", "major"),
        ("doubled-word", r"\b(\w+)\s+\1\b", "major"),
        ("straight-quote-mix", r"[\u201c\u201d][^\u201c\u201d]{0,200}\"", "minor"),
        ("unbalanced-brackets", r"(?<!\w)\([^)]*$", "major"),
        ("repeated-punctuation", r"[!?]{2,}|\.{4,}", "minor"),
    ]
    for rule, pattern, severity in patterns:
        for m in re.finditer(pattern, body):
            if rule == "doubled-word" and m.group(1).lower() in ("that", "had"):
                pass
            issues.append({"rule": rule, "severity": severity, "excerpt": m.group(0)[:80]})
    unresolved_refs = []
    for marker in re.findall(r"\[([A-Za-z][^\]]{2,40})\]", body):
        if not re.match(r"[A-Z][A-Za-z\-]+(?:,\s*\d{4}| \d{4})", marker) and \
                not re.match(r"^(?:EVIDENCE NEEDED|ref|REF)", marker):
            unresolved_refs.append(marker)
    if unresolved_refs:
        issues.append({"rule": "non-citation-bracket", "severity": "major",
                       "excerpt": ", ".join(sorted(set(unresolved_refs))[:10])})
    critical = [i for i in issues if i["severity"] == "critical"]
    major = [i for i in issues if i["severity"] == "major"]
    return {"status": "ok", "issues": issues[:200], "issue_count": len(issues),
            "critical": len(critical), "major": len(major),
            "ready_for_render": not critical and not major,
            "note": "run after rendering too; layout and cross-reference errors only appear in output"}


def register(groups, common):
    g = groups["writing"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    txt = dict(text=(("--text",), dict(default=None)),
               input_path=(("--input", "-i"), dict(default=None)))
    sub("outline", "Build an evidence-mapped outline", op_outline,
        question=(("--question", "-q"), dict(required=True)),
        kind=(("--kind",), dict(default="academic", choices=list(SECTIONS))),
        source_ids=(("--source-ids",), dict(default=None)))
    sub("draft", "Assemble an evidence-bound section skeleton from stored claims", op_draft,
        section=(("--section",), dict(required=True)),
        source_ids=(("--source-ids",), dict(default=None)),
        max_claims=(("--max-claims",), dict(type=int, default=8)),
        style=(("--style",), dict(default="academic")),
        output=(("--output", "-o"), dict(default=None)))
    sub("readability", "Measure readability and structure", op_readability,
        threshold_grade=(("--threshold-grade",), dict(type=float, default=14.0)), **txt)
    sub("style", "Lint for weasel words, overclaims, passives and house style", op_style,
        profile=(("--profile",), dict(default="academic")),
        max_findings=(("--max-findings",), dict(type=int, default=100)), **txt)
    sub("redundancy", "Find repeated sentences and paragraphs", op_redundancy,
        threshold=(("--threshold",), dict(type=float, default=0.8)), **txt)
    sub("flow", "Audit premises, transitions and causal language", op_flow, **txt)
    sub("revise", "Produce a tracked revision with per-edit reasons", op_revise,
        original_path=(("--original",), dict(required=True)),
        revised_path=(("--revised",), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)))
    sub("boundary", "Check conclusion scope against the evidence", op_boundary,
        draft_path=(("--draft",), dict(required=True)),
        evidence_max_year=(("--evidence-max-year",), dict(default=None)))
    sub("summary", "Create a source-linked extractive summary", op_summary,
        input_path=(("--input", "-i"), dict(required=True)),
        words=(("--words",), dict(type=int, default=180)),
        output=(("--output", "-o"), dict(default=None)))
    sub("tone", "Check register against a target tone", op_tone,
        target=(("--target",), dict(default="formal", choices=list(TONE_PROFILES))), **txt)
    sub("consistency", "Check abbreviations, units, numbering and references", op_consistency, **txt)
    sub("proofread", "Final proofreading pass for placeholders and typography", op_proofread, **txt)
