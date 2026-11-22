"""Cognitive accessibility, plain language, inclusive content and AI output engines.

Categories served: 7 (Cognitive, Learning, Neurodiversity and Plain Language),
20 (Content Design, Inclusive Language and Cultural Adaptation),
21 (Accessibility and Localization for AI Systems).
"""

import json
import re

S = None
T = None
H = None

OPS = {}


def init(deps):
    global S, T, H
    S = deps["support"]
    T = deps["text"]
    H = deps["html"]


def op(name, doc, params=None, accepts=("text",), engine="content"):
    def deco(fn):
        OPS[name] = {"name": name, "doc": doc, "params": params or {}, "accepts": list(accepts),
                     "summary": doc.split(".")[0], "fn": fn, "engine": engine, "groups": []}
        return fn
    return deco


def _text_of(ctx):
    doc = ctx.docs[0]
    if doc.kind == "html":
        return doc.data["document"].body_text()
    if doc.kind == "strings":
        data = doc.data
        if isinstance(data, dict):
            return "\n".join(str(v) for v in data.values() if isinstance(v, str))
    if doc.kind == "json":
        data = doc.data
        if isinstance(data, dict) and isinstance(data.get("pairs"), list):
            return "\n".join(str(p.get("target") or "") for p in data["pairs"])
        if isinstance(data, dict):
            for key in ("records", "items", "outputs"):
                if isinstance(data.get(key), list):
                    return "\n".join(str(r.get("text") or r.get("output") or "") for r in data[key])
            return "\n".join(str(v) for v in data.values() if isinstance(v, str))
        if isinstance(data, list):
            return "\n".join(str(v) if isinstance(v, str) else str(v.get("text") or v.get("output") or "")
                             for v in data)
    return doc.text


# --------------------------------------------------------------------------- #
# Category 7: cognitive and plain language
# --------------------------------------------------------------------------- #
@op("plain-language-audit", "Evaluate plain-language quality: reading ease, grade level, sentence and word "
    "length, passive voice, nominalisations, jargon, all-caps and unexplained abbreviations.",
    params={"max_reading_grade": {"type": "float", "default": None,
                                  "doc": "Maximum acceptable reading grade."},
            "max_sentence_words": {"type": "int", "default": 25, "doc": "Longest acceptable sentence."},
            "locale": {"type": "str", "default": None, "doc": "Locale whose jargon list applies."}},
    accepts=("text", "html", "strings", "json"))
def run_plain_language(ctx):
    text = _text_of(ctx)
    if not T.word_count(text):
        raise S.InputError("No readable text found in the artifact.",
                           hint="Point --input at a text, HTML or strings file with prose content.")
    lexicon = ctx.assets.get("terminology", {})
    locale = ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale", "fil-PH")
    jargon = (lexicon.get("jargon") or []) + ((lexicon.get("filipino") or {}).get("jargon") or [])
    metrics = T.lexicon_metrics(text, {"jargon": jargon})
    metrics["script_profile"] = T.script_profile(text)
    max_grade = float(ctx.params.get("max_reading_grade") or
                      ctx.config.get("thresholds", {}).get("max_reading_grade", 9.0))
    max_sentence = int(ctx.params.get("max_sentence_words", 25))
    grade = metrics["flesch_kincaid_grade"]
    if grade is not None and grade > max_grade:
        ctx.add(rule="plain-reading-grade", message="Flesch-Kincaid grade %.1f exceeds the %.1f target." % (
            grade, max_grade), severity="high", criterion="3.1.5",
            evidence={"metrics": metrics},
            remediation="Shorten sentences, prefer common words, and split long paragraphs.",
            retest="Re-run and confirm the grade level is within target.")
    else:
        ctx.pass_check()
    long_sentences = [s for s in T.sentences(text) if T.word_count(s) > max_sentence]
    for s in long_sentences[:25]:
        ctx.add(rule="plain-sentence-length", message="Sentence of %d words: %r" % (T.word_count(s), T.truncate(s, 90)),
                severity="medium", criterion="3.1.5", evidence={"words": T.word_count(s)},
                remediation="Split the sentence into shorter ones with one idea each.",
                retest="Re-run and confirm no sentence exceeds the limit.")
    if not long_sentences:
        ctx.pass_check()
    if metrics["passive_constructions"] > max(2, metrics["sentences"] // 3):
        ctx.add(rule="plain-passive-voice", message="%d passive constructions in %d sentences." % (
            metrics["passive_constructions"], metrics["sentences"]), severity="medium", criterion="3.1.5",
            evidence={"passive": metrics["passive_constructions"]},
            remediation="Rewrite in active voice with an explicit actor.",
            retest="Re-run and confirm passive constructions are reduced.")
    else:
        ctx.pass_check()
    for term in metrics["jargon_terms"][:20]:
        ctx.add(rule="plain-jargon", message="Jargon term %r is not explained." % term, severity="medium",
                criterion="3.1.3", evidence={"term": term},
                remediation="Replace with a common word or define it on first use.",
                retest="Re-run and confirm the term is absent or defined.")
    if not metrics["jargon_terms"]:
        ctx.pass_check()
    abbreviations = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", text)))
    for abbr in abbreviations[:15]:
        if not re.search(r"%s\s*\([^)]*\)|\([^)]*\)\s*%s" % (re.escape(abbr), re.escape(abbr)), text):
            ctx.add(rule="plain-abbreviation", message="Abbreviation %r is never expanded." % abbr,
                    severity="low", criterion="3.1.4", evidence={"abbreviation": abbr},
                    remediation="Expand the abbreviation on first use, or wrap it in <abbr>.",
                    retest="Re-run and confirm each abbreviation has an expansion.")
    if not abbreviations:
        ctx.pass_check()
    if metrics["all_caps_runs"] > 2:
        ctx.add(rule="plain-all-caps", message="%d all-caps runs make text harder to read." % metrics["all_caps_runs"],
                severity="low", criterion="1.4.8",
                remediation="Use sentence case; reserve capitals for short labels.",
                retest="Re-run and confirm all-caps use is reduced.")
    else:
        ctx.pass_check()
    ctx.meta("metrics", metrics)
    ctx.meta("max_reading_grade", max_grade)
    ctx.meta("long_sentences", len(long_sentences))
    ctx.note("Readability formulas are English-calibrated; treat Filipino results as advisory and confirm with a "
             "qualified Filipino reviewer.")


@op("cognitive-support-audit", "Check cognitive-load supports in an interface: consistent help placement, error "
    "prevention, timeout warnings, form length, heading structure and instruction clarity.",
    params={"max_form_fields": {"type": "int", "default": 12, "doc": "Longest acceptable single-step form."}},
    accepts=("html",))
def run_cognitive_support(ctx):
    doc = ctx.docs[0].data.get("document")
    if doc is None:
        raise S.InputError("Cognitive support audit needs an HTML artifact.",
                           hint="Pass the page markup as .html.")
    max_fields = int(ctx.params.get("max_form_fields", 12))
    form = doc.by_tag("form")
    fields = [n for n in doc.by_tag("input", "select", "textarea")
              if (n.get("type") or "text").lower() not in ("hidden", "submit", "button", "reset")]
    if form and len(fields) > max_fields:
        ctx.add(rule="coga-form-length", message="Single form exposes %d fields (> %d)." % (len(fields), max_fields),
                severity="medium", criterion="3.2.6", evidence={"fields": len(fields)},
                remediation="Split into steps with a progress indicator, or make optional fields optional.",
                retest="Re-run and confirm the form length is within the threshold.")
    else:
        ctx.pass_check()
    text = doc.body_text()
    if re.search(r"(session (?:will )?(?:expire|timeout)|auto-?logout)", text, re.I):
        if not re.search(r"(extend|stay signed in|continue session|more time)", text, re.I):
            ctx.add(rule="coga-timeout", message="Session warning without an extension control.",
                    severity="high", criterion="2.2.6",
                    remediation="Offer an extend control with a clear countdown.",
                    retest="Re-run and confirm an extension control is present.")
        else:
            ctx.pass_check()
    has_help = bool(re.search(r"(help|support|contact|faq|chat)", text, re.I))
    if not has_help:
        ctx.add(rule="coga-help", message="No help or support entry point found.", severity="medium",
                criterion="3.2.6",
                remediation="Add a consistently placed help entry point.",
                retest="Re-run and confirm a help mechanism is detected.")
    else:
        ctx.pass_check()
    labels = [H.accessible_name(doc, n)[0] for n in fields]
    vague = [l for l in labels if l and re.fullmatch(r"(?i)(field|input|data|value|enter|text)\s*\d*", l.strip())]
    for v in vague[:10]:
        ctx.add(rule="coga-vague-label", message="Vague field label %r." % v, severity="medium",
                criterion="3.3.2",
                remediation="Label the field with the information it expects, in plain words.",
                retest="Re-run and confirm labels are specific.")
    if not vague:
        ctx.pass_check()
    heads = H.headings(doc)
    if len(heads) == 0 and len(text) > 500:
        ctx.add(rule="coga-headings", message="Long content has no headings to aid navigation.",
                severity="high", criterion="2.4.6",
                remediation="Add descriptive headings so users can scan the content.",
                retest="Re-run and confirm headings exist.")
    else:
        ctx.pass_check()
    steps = len(doc.by_tag("ol")) + len(re.findall(r"(?i)step\s+\d", text))
    if len(fields) > 6 and steps == 0:
        ctx.add(rule="coga-progress", message="Multi-field process has no step or progress indication.",
                severity="low", criterion="3.2.6",
                remediation="Show step numbers and remaining steps.",
                retest="Re-run and confirm progress indication is present.")
    else:
        ctx.pass_check()
    ctx.meta("fields", len(fields))
    ctx.meta("headings", len(heads))


# --------------------------------------------------------------------------- #
# Category 20: inclusive language and cultural adaptation
# --------------------------------------------------------------------------- #
@op("inclusive-language-audit", "Audit content for inclusive language: disability and person-first wording, "
    "gendered language, age bias, idioms, violent or colonial metaphors, and unexplained acronyms.",
    params={"locale": {"type": "str", "default": None, "doc": "Locale whose policy list applies."},
            "severity_override": {"type": "enum", "values": ["critical", "high", "medium", "low", "info"],
                                  "default": None, "doc": "Force a severity for every hit."}},
    accepts=("text", "html", "strings", "json"))
def run_inclusive(ctx):
    text = _text_of(ctx)
    policy = (ctx.assets.get("terminology", {}) or {}).get("inclusive_language", {})
    hits = 0
    for group, entries in policy.items():
        if group in ("notes",):
            continue
        for entry in entries or []:
            if isinstance(entry, str):
                entry = {"avoid": entry}
            avoid = entry.get("avoid")
            prefer = entry.get("prefer") or ""
            if not avoid:
                continue
            for m in T.find_terms(text, [avoid])[:20]:
                hits += 1
                ctx.add(rule="inclusive-%s" % group,
                        message="%r is not inclusive language%s." % (m["surface"], "; prefer %r" % prefer if prefer else ""),
                        severity=ctx.params.get("severity_override") or entry.get("severity", "medium"),
                        criterion="inclusive-language", locator="offset %d" % m["start"],
                        evidence={"found": m["surface"], "preferred": prefer, "group": group,
                                  "context": text[max(0, m["start"] - 60):m["end"] + 60]},
                        remediation=entry.get("guidance") or "Rewrite with person-first, neutral wording.",
                        retest="Re-run inclusive-language-audit and confirm the term is gone.")
    if hits == 0:
        ctx.pass_check()
    for pat, rule_id, message, sev in [
        (r"\b(he|she)\b[^.]{0,40}\b(he|she)\b", "inclusive-gendered", "Gendered pronouns used as generics.", "medium"),
        (r"\b(obviously|simply|just|easy|of course)\b", "inclusive-condescending",
         "Words like 'simply' or 'obviously' dismiss users who find the task hard.", "low"),
        (r"\b(normal users?|regular users?)\b", "inclusive-normalising",
         "'Normal users' implies disabled users are abnormal.", "medium"),
    ]:
        for m in list(re.finditer(pat, text, re.I))[:10]:
            hits += 1
            ctx.add(rule=rule_id, message=message, severity=sev, criterion="inclusive-language",
                    locator="offset %d" % m.start(), evidence={"match": m.group(0)},
                    remediation="Rewrite with neutral, user-respecting wording.",
                    retest="Re-run and confirm the pattern is gone.")
    ctx.meta("locale", ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale"))
    ctx.meta("hits", hits)
    ctx.meta("policy_groups", sorted(policy.keys()))


@op("cultural-adaptation-audit", "Review content for cultural adaptation risks: idioms, humour, gestures, colour "
    "meaning, holidays, name/address/currency examples, and religious or regional sensitivity.",
    params={"target_locale": {"type": "str", "default": None, "doc": "Target locale of the adaptation."},
            "content_type": {"type": "str", "default": "general", "doc": "Marketing, legal, UI, or support."}},
    accepts=("text", "html", "strings", "json"))
def run_cultural(ctx):
    text = _text_of(ctx)
    locale = ctx.params.get("target_locale") or ctx.config.get("locale", {}).get("default_locale", "fil-PH")
    rules = (ctx.assets.get("terminology", {}) or {}).get("cultural_rules", {})
    hits = 0
    for category, entries in rules.items():
        for entry in entries or []:
            pattern = entry.get("pattern") if isinstance(entry, dict) else entry
            if not pattern:
                continue
            for m in list(re.finditer(pattern, text, re.I))[:10]:
                hits += 1
                ctx.add(rule="culture-%s" % category, message=(entry.get("message") if isinstance(entry, dict)
                        else "Potential %s dependency: %r." % (category, m.group(0))),
                        severity=(entry.get("severity", "low") if isinstance(entry, dict) else "low"),
                        criterion="cultural-adaptation", locator="offset %d" % m.start(),
                        evidence={"match": m.group(0), "category": category,
                                  "guidance": entry.get("guidance") if isinstance(entry, dict) else None},
                        remediation=(entry.get("guidance") if isinstance(entry, dict)
                                     else "Replace with locale-neutral wording or an approved local equivalent."),
                        retest="Re-run and confirm no cultural dependency remains for %s." % locale)
    sample_dates = T.format_signals(text)
    if sample_dates["month_name_date"] and locale.startswith(("fil", "en-PH")):
        ctx.add(rule="culture-date-example", message="US-style month-day dates appear in Philippine content.",
                severity="low", criterion="cultural-adaptation",
                evidence={"month_name_date": sample_dates["month_name_date"]},
                remediation="Show a D Month YYYY example so users recognise the format.",
                retest="Re-run and confirm the example matches the locale convention.")
    else:
        ctx.pass_check()
    if re.search(r"\b(John|Jane|Smith|Main St|Anytown|555-)\b", text) and not locale.startswith("en-US"):
        ctx.add(rule="culture-examples", message="Name/address examples are US-centric for locale %s." % locale,
                severity="medium", criterion="cultural-adaptation",
                remediation="Use locale-appropriate example names, streets and phone formats.",
                retest="Re-run and confirm examples are localised.")
    else:
        ctx.pass_check()
    ctx.meta("locale", locale)
    ctx.meta("hits", hits)
    ctx.note("Cultural review must be completed by in-country reviewers; automated pattern matching cannot judge context.")


# --------------------------------------------------------------------------- #
# Category 21: AI systems
# --------------------------------------------------------------------------- #
@op("ai-output-audit", "Gate AI-generated accessible content: alt text quality, plain-language level, glossary "
    "adherence, placeholder safety and locale formatting of model output.",
    params={"expected_terms": {"type": "list", "default": None,
                               "doc": "Terms that must appear in the output."},
            "forbidden_terms": {"type": "list", "default": None, "doc": "Terms that must not appear."},
            "max_reading_grade": {"type": "float", "default": 9.0, "doc": "Reading grade ceiling."},
            "asset_kind": {"type": "enum", "values": ["alt-text", "caption", "response", "summary"],
                           "default": "response", "doc": "What kind of output is being gated."}},
    accepts=("json", "text"))
def run_ai_output(ctx):
    records = []
    if ctx.docs[0].kind == "json":
        data = ctx.docs[0].data
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key in ("records", "outputs", "items"):
                if isinstance(data.get(key), list):
                    records = data[key]
                    break
            if not records and data.get("text"):
                records = [data]
    else:
        records = [{"text": ctx.docs[0].text}]
    if not records:
        raise S.InputError("No model outputs supplied.",
                           hint="Provide a JSON list of {prompt, output, locale} records or a text file.")
    kind = ctx.params.get("asset_kind", "response")
    max_grade = float(ctx.params.get("max_reading_grade", 9.0))
    expected = ctx.params.get("expected_terms") or []
    forbidden = ctx.params.get("forbidden_terms") or []
    if isinstance(expected, str):
        expected = [e.strip() for e in expected.split(",") if e.strip()]
    if isinstance(forbidden, str):
        forbidden = [f.strip() for f in forbidden.split(",") if f.strip()]
    passed = 0
    for i, rec in enumerate(records):
        ctx.checkpoint()
        output = str(rec.get("output") or rec.get("text") or "")
        locale = rec.get("locale") or ctx.config.get("locale", {}).get("default_locale")
        loc_ref = "output %d%s" % (i + 1, " (%s)" % locale if locale else "")
        if not output.strip():
            ctx.add(rule="ai-empty-output", message="Output %d is empty." % (i + 1), severity="critical",
                    criterion="ai-output-quality", locator=loc_ref,
                    remediation="Reject the generation and retry with a clearer prompt.",
                    retest="Re-run and confirm the output is non-empty.")
            continue
        if kind == "alt-text":
            words = T.word_count(output)
            if words < 3:
                ctx.add(rule="ai-alt-too-short", message="Generated alt text has only %d words." % words,
                        severity="high", criterion="1.1.1", locator=loc_ref, evidence={"output": output[:160]},
                        remediation="Regenerate with image context, or write the alt text manually.",
                        retest="Re-run and confirm alt text is descriptive.")
            elif words > 40:
                ctx.add(rule="ai-alt-too-long", message="Generated alt text is %d words." % words,
                        severity="medium", criterion="1.1.1", locator=loc_ref,
                        remediation="Condense the description to the image's purpose.",
                        retest="Re-run and confirm the length is acceptable.")
            else:
                ctx.pass_check()
            if re.match(r"(?i)^(an?|the)?\s*(image|picture|photo)\s+(of|showing)", output.strip()):
                ctx.add(rule="ai-alt-redundant", message="Generated alt text starts with a redundant phrase.",
                        severity="medium", criterion="1.1.1", locator=loc_ref, evidence={"output": output[:160]},
                        remediation="Drop the 'image of' framing and describe the content directly.",
                        retest="Re-run and confirm the redundant phrase is gone.")
            else:
                ctx.pass_check()
        metrics = T.lexicon_metrics(output)
        grade = metrics["flesch_kincaid_grade"]
        if grade is not None and grade > max_grade and kind in ("response", "summary", "alt-text"):
            ctx.add(rule="ai-reading-level", message="Generated text reads at grade %.1f (> %.1f)." % (grade, max_grade),
                    severity="medium", criterion="3.1.5", locator=loc_ref, evidence={"metrics": metrics},
                    remediation="Re-prompt with an explicit reading-level constraint and re-check.",
                    retest="Re-run and confirm the reading grade is within target.")
        else:
            ctx.pass_check()
        lowered = output.lower()
        for term in expected:
            if term.lower() not in lowered:
                ctx.add(rule="ai-glossary-missing", message="Required term %r missing from the output." % term,
                        severity="high", criterion="terminology", locator=loc_ref, evidence={"term": term},
                        remediation="Constrain the prompt with the approved termbase entry.",
                        retest="Re-run and confirm the approved term appears.")
            else:
                ctx.pass_check()
        for term in forbidden:
            if term.lower() in lowered:
                ctx.add(rule="ai-forbidden-term", message="Forbidden term %r appears in the output." % term,
                        severity="critical", criterion="terminology", locator=loc_ref, evidence={"term": term},
                        remediation="Block and regenerate; add the term to the prohibited list.",
                        retest="Re-run and confirm the forbidden term is absent.")
            else:
                ctx.pass_check()
        if locale:
            signals = T.format_signals(output)
            profile = ctx.assets.get("locale_profiles", {}).get("locales", {}).get(locale, {})
            if profile.get("grouping_separator") == "," and signals["grouping_dot"]:
                ctx.add(rule="ai-locale-number", message="Output uses dot grouping for locale %s." % locale,
                        severity="medium", criterion="locale-format", locator=loc_ref,
                        remediation="Instruct the model with the locale number format or post-format the values.",
                        retest="Re-run and confirm numbers follow the locale profile.")
            else:
                ctx.pass_check()
        if re.search(r"\{[a-z_]+\}|%s|TODO|\[insert", output) and kind != "alt-text":
            ctx.add(rule="ai-unresolved-placeholder", message="Output contains an unresolved placeholder or TODO.",
                    severity="high", criterion="ai-output-quality", locator=loc_ref,
                    remediation="Fill the placeholder server-side; never publish raw template text.",
                    retest="Re-run and confirm no placeholder survives.")
        else:
            ctx.pass_check()
        passed += 1
    ctx.meta("outputs", len(records))
    ctx.meta("checked", passed)
    ctx.meta("asset_kind", kind)
    ctx.note("Human review remains mandatory for AI-generated accessibility text and translations.")


@op("ai-translation-audit", "Gate machine-translated output: placeholder integrity, glossary compliance, "
    "untranslated segments, number and format consistency, length ratio and back-translation similarity.",
    params={"target_locale": {"type": "str", "default": None, "doc": "Target locale."},
            "min_back_translation": {"type": "float", "default": 0.55,
                                     "doc": "Minimum similarity between source and back-translation."}},
    accepts=("json",))
def run_ai_translation(ctx):
    data = ctx.docs[0].data
    pairs = data.get("pairs") if isinstance(data, dict) else data
    if not isinstance(pairs, list) or not pairs:
        raise S.InputError("No translation pairs supplied.",
                           hint="Provide [{\"source\":..,\"mt\":..,\"back_translation\":..}].")
    locale = ctx.params.get("target_locale") or ctx.config.get("locale", {}).get("default_locale")
    min_back = float(ctx.params.get("min_back_translation", 0.55))
    glossary = (ctx.assets.get("terminology", {}) or {}).get("terms", [])
    decision = "pass"
    for i, p in enumerate(pairs):
        ctx.checkpoint()
        src, mt = str(p.get("source") or ""), str(p.get("mt") or p.get("target") or "")
        loc = p.get("key") or "pair %d" % (i + 1)
        if not mt.strip() or mt.strip() == src.strip():
            decision = "fail"
            ctx.add(rule="mt-untranslated", message="%s is untranslated or identical to the source." % loc,
                    severity="critical", criterion="translation-quality", locator=loc,
                    remediation="Re-run translation for this segment or route it to a human translator.",
                    retest="Re-run and confirm the segment is translated.")
            continue
        from_src, from_mt = T.placeholder_set(src), T.placeholder_set(mt)
        if from_src != from_mt:
            decision = "fail"
            ctx.add(rule="mt-placeholder", message="%s placeholder drift (%s -> %s)." % (
                loc, sorted(from_src), sorted(from_mt)), severity="critical", criterion="translation-quality",
                locator=loc, evidence={"source": src, "mt": mt},
                remediation="Reject the segment; fix the MT pipeline's placeholder protection.",
                retest="Re-run and confirm placeholders match.")
        else:
            ctx.pass_check()
        for term in glossary:
            if not isinstance(term, dict):
                continue
            s_term, t_term = (term.get("source") or ""), (term.get("target") or "")
            if s_term and t_term and re.search(r"\b%s\b" % re.escape(s_term), src, re.I) and \
                    t_term.lower() not in mt.lower():
                decision = "fail"
                ctx.add(rule="mt-glossary", message="%s does not use approved term %r for %r." % (loc, t_term, s_term),
                        severity="high", criterion="terminology", locator=loc,
                        evidence={"mt": mt, "approved": t_term},
                        remediation="Apply term enforcement (constrained decoding or post-editing).",
                        retest="Re-run and confirm the approved term appears.")
            else:
                ctx.pass_check()
        back = p.get("back_translation")
        if back:
            sim = T.token_similarity(src, back)
            if sim < min_back:
                decision = "fail"
                ctx.add(rule="mt-back-translation", message="%s back-translation similarity %.2f below %.2f." % (
                    loc, sim, min_back), severity="high", criterion="translation-quality", locator=loc,
                    evidence={"source": src, "back_translation": back, "similarity": sim},
                    remediation="Route the segment to human post-editing and record the correction.",
                    retest="Re-run and confirm similarity meets the threshold.")
            else:
                ctx.pass_check()
        ratio = len(mt) / float(max(len(src), 1))
        if ratio > 3.5 or ratio < 0.25:
            ctx.add(rule="mt-length-ratio", message="%s length ratio %.2f is implausible." % (loc, ratio),
                    severity="medium", criterion="translation-quality", locator=loc,
                    remediation="Inspect the segment for truncation or hallucinated content.",
                    retest="Re-run and confirm the ratio is plausible.")
        else:
            ctx.pass_check()
    ctx.meta("pairs", len(pairs))
    ctx.meta("target_locale", locale)
    ctx.meta("decision", decision)


@op("conversational-interface-audit", "Audit a declared chat or agent interface: streaming announcements, focus "
    "management, keyboard support, error messaging, citations and human escalation.",
    params={"platform": {"type": "enum", "values": ["web", "mobile", "voice", "kiosk"], "default": "web",
                         "doc": "Delivery platform of the assistant."}},
    accepts=("json", "html"))
def run_conversational(ctx):
    doc = ctx.docs[0]
    if doc.kind == "html":
        d = doc.data["document"]
        checks = [
            (bool(d.by_tag("main")), "chat-landmark", "Chat transcript has no main landmark.", "medium", "1.3.1"),
            (bool([n for n in d.nodes if (n.get("aria-live") or "") in ("polite", "assertive") or H.role_of(n) in ("log", "status")]),
             "chat-live-region", "No live region or log role for streaming responses.", "critical", "4.1.3"),
            (bool(d.by_tag("form")) or bool(d.by_tag("textarea")), "chat-input", "No accessible message input found.", "high", "2.1.1"),
            (bool([n for n in d.nodes if (n.get("aria-label") or "").lower().find("stop") >= 0]),
             "chat-stop", "No stop/cancel control for a streaming response.", "medium", "2.2.2"),
        ]
        for ok, rule_id, message, sev, crit in checks:
            if ok:
                ctx.pass_check()
            else:
                ctx.add(rule=rule_id, message=message, severity=sev, criterion=crit,
                        remediation="Implement the documented conversational accessibility pattern.",
                        retest="Re-run conversational-interface-audit and confirm the check passes.")
        ctx.meta("source", "html")
        return
    spec = doc.data
    if not isinstance(spec, dict):
        raise S.InputError("Conversational audit needs an HTML page or a JSON interface spec.",
                           hint="See knowledge-readme.md for the spec shape.")
    platform = ctx.params.get("platform", "web")
    required = ["transcript_role", "input_label", "streaming_announcement", "error_message_announcement"]
    if platform in ("web", "mobile", "kiosk"):
        required += ["focus_target_after_response", "keyboard_send"]
    if platform == "voice":
        required += ["barge_in", "speech_fallback"]
    for key in required:
        value = spec.get(key)
        if not value:
            ctx.add(rule="chat-%s" % key.replace("_", "-"), message="Interface spec does not define %r." % key,
                    severity="high" if key in ("streaming_announcement", "transcript_role") else "medium",
                    criterion="4.1.3" if "announce" in key else "2.1.1",
                    locator=key, evidence={"platform": platform},
                    remediation="Define the behaviour in the component spec and implement it in the interface.",
                    retest="Re-run and confirm the spec field is present and non-empty.")
        else:
            ctx.pass_check()
    if spec.get("auto_scroll") and not spec.get("focus_preserved"):
        ctx.add(rule="chat-focus-steal", message="Auto-scrolling transcript without focus preservation moves the user's viewport.",
                severity="high", criterion="2.4.3",
                remediation="Pause auto-scroll while the user reads history and keep focus in the input.",
                retest="Re-run and confirm focus preservation is declared.")
    else:
        ctx.pass_check()
    if not spec.get("human_escalation"):
        ctx.add(rule="chat-escalation", message="No human escalation path is defined for the assistant.",
                severity="medium", criterion="ai-escalation",
                remediation="Provide an accessible route to a human, including a non-chat alternative.",
                retest="Re-run and confirm an escalation path is defined.")
    else:
        ctx.pass_check()
    ctx.meta("platform", platform)
    ctx.meta("spec_keys", sorted(spec.keys()))
