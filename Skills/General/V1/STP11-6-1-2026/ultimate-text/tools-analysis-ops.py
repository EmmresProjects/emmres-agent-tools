"""Text analysis (12) and retrieval/evidence capabilities (6 local).

Extraction and scoring are deterministic: regex plus lexicon scoring with the
method and thresholds reported alongside every result so the caller can audit
why something was flagged.
"""

from __future__ import annotations

import difflib
import json
import math
import re

EXPORTS = ["extract_entities", "keyword_scores", "sentiment_scores", "day_key"]
LEX_ASSET = "asset-lexicons-language.json"
UNI_ASSET = "asset-unicode-tables.json"

PATTERNS = {
    "emails": r"[\w.+-]+@[\w-]+\.[\w.]{2,}",
    "urls": r"https?://[^\s<>\"')]+",
    "phones": r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?",
    "money": r"(?:[$€£¥₱₹₩₽]|\b(?:USD|EUR|GBP|PHP|JPY|INR)\b)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|PHP|JPY|INR)\b",
    "percentages": r"\b\d+(?:\.\d+)?\s?%",
    "hashtags": r"#\w{2,}",
    "ids": r"\b(?![0-9]{4}-[0-9]{2}-[0-9]{2})(?=[A-Z0-9-]{6,20}\b)(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]+(?:-[A-Z0-9]+)+\b",
    "dates": r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    "ordinals": r"\b\d+(?:st|nd|rd|th)\b",
    "versions": r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-z0-9.]+)?\b",
}

MONTHS = {name: idx + 1 for idx, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _lex(ctx):
    return ctx.assets.json(LEX_ASSET)


def extract_entities(text, kinds):
    found = {}
    for kind in kinds:
        pattern = PATTERNS.get(kind)
        if not pattern:
            continue
        values = []
        seen = set()
        for match in re.finditer(pattern, text, re.I if kind in ("emails", "urls") else 0):
            value = match.group(0).strip().rstrip(".,;)")
            if value in seen:
                continue
            seen.add(value)
            values.append({"value": value, "offset": match.start()})
        found[kind] = values
    return found


def keyword_scores(text, top_n=10, method="rake"):
    stop = set(_lex_global.get("english_stopwords", []))
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text.lower())
    if method == "frequency":
        counts = {}
        for word in words:
            if word not in stop and len(word) > 2:
                counts[word] = counts.get(word, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]
    if method == "bigram":
        counts = {}
        for a, b in zip(words, words[1:]):
            if a in stop and b in stop:
                continue
            if len(a) < 3 or len(b) < 3:
                continue
            phrase = "%s %s" % (a, b)
            counts[phrase] = counts.get(phrase, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]
    # RAKE-style: candidate phrases split on stopwords and punctuation, scored by degree/frequency
    candidates = re.split(r"[^A-Za-z'-]+|\b(?:%s)\b" % "|".join(sorted(stop)) if stop else r"[^A-Za-z'-]+", text.lower())
    import math as _math
    phrases, phrase_counts = [], {}
    for chunk in candidates:
        words_c = [w for w in chunk.split() if len(w) > 2]
        if not words_c:
            continue
        phrase = " ".join(words_c)
        phrases.append(phrase)
        phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()] or [text]
    n_sentences = max(1, len(sentences))
    word_freq, doc_freq = {}, {}
    for phrase in phrases:
        for word in phrase.split():
            word_freq[word] = word_freq.get(word, 0) + 1
    for sentence in sentences:
        for word in set(re.findall(r"[a-z'-]+", sentence.lower())):
            doc_freq[word] = doc_freq.get(word, 0) + 1
    scored = []
    for phrase, count in phrase_counts.items():
        words_p = phrase.split()
        if len(words_p) > 4:
            continue
        weights = []
        for word in words_p:
            rarity = _math.log(1 + n_sentences / max(1, doc_freq.get(word, 1)))
            weights.append(_math.log(1 + word_freq.get(word, 1)) * (1 + rarity))
        base = sum(weights) / len(words_p)
        scored.append((phrase, round(base * (1 + 0.5 * (len(words_p) - 1)), 2)))
    scored.sort(key=lambda kv: -kv[1])
    return scored[:top_n]


_lex_global = {}


def sentiment_scores(text, lexicon):
    pos = lexicon["sentiment"]["positive"]
    neg = lexicon["sentiment"]["negative"]
    negators = set(lexicon["negators"])
    intensifiers = lexicon["intensifiers"]
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    score = 0.0
    hits = []
    for idx, token in enumerate(tokens):
        value = pos.get(token, 0) or neg.get(token, 0)
        if not value:
            continue
        multiplier = 1.0
        window = tokens[max(0, idx - 3):idx]
        if any(w in negators for w in window):
            multiplier *= -1
        if idx > 0 and tokens[idx - 1] in intensifiers:
            multiplier *= intensifiers[tokens[idx - 1]]
        score += value * multiplier
        hits.append({"term": token, "value": value * multiplier})
    return score, hits


def day_key(text):
    """Best-effort sortable date key from a date expression."""
    lowered = text.lower()
    year = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", lowered)
    month = None
    for name, num in MONTHS.items():
        if name in lowered:
            month = num
            break
    day = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", lowered)
    key = [(0 if not year else int(year.group(1))), (0 if not month else month),
           (0 if not day else int(day.group(1)))]
    if re.search(r"\b(bc|bce)\b", lowered) and year:
        key[0] = -int(year.group(1))
    return tuple(key)


def key_information(ctx):
    text = ctx.require_text(minimum=5)
    kinds = [k.lower() for k in ctx.params["kinds"]]
    found = extract_entities(text, kinds)
    data = {"entities": {k: [v["value"] for v in vals] for k, vals in found.items()},
            "counts": {k: len(vals) for k, vals in found.items()},
            "total": sum(len(v) for v in found.values())}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    if ctx.params["format"] == "markdown":
        lines = ["# Extracted information", ""]
        for kind, values in found.items():
            if values:
                lines.append("## %s (%d)" % (kind, len(values)))
                lines.extend("- %s" % v["value"] for v in values[:25])
                lines.append("")
        return {"output": "\n".join(lines), "data": data}
    rows = [[kind, len(values), ", ".join(v["value"] for v in values[:3])]
            for kind, values in found.items() if values]
    if not rows:
        return {"output": "no extractable entities found", "data": data}
    return {"output": core.table(rows, ["kind", "count", "examples"]), "data": data}


def topic_analyzer(ctx):
    text = ctx.require_text(minimum=40)
    global _lex_global
    _lex_global = _lex(ctx)
    top_n = int(ctx.params["top_n"])
    method = ctx.params["method"]
    scored = keyword_scores(text, top_n=top_n, method=method)
    sentences = core.sentence_split(text)
    themes = []
    for phrase, score in scored[: min(5, len(scored))]:
        evidence = [s for s in sentences if phrase.split()[0] in s.lower()][:1]
        themes.append({"theme": phrase, "score": score, "evidence": evidence[0][:160] if evidence else ""})
    data = {"method": method, "keywords": scored, "themes": themes}
    rows = [[phrase, score] for phrase, score in scored]
    return {"output": core.table(rows, ["keyword / phrase", "score (%s)" % method]),
            "data": data}


def sentiment_analyzer(ctx):
    text = ctx.require_text(minimum=10)
    lex = _lex(ctx)
    granularity = ctx.params["granularity"]
    total, hits = sentiment_scores(text, lex)
    label = "positive" if total > 1 else "negative" if total < -1 else "neutral"
    rows = [[h["term"], round(h["value"], 2)] for h in hits[:25]]
    data = {"score": round(total, 2), "label": label, "hits": hits,
            "method": "lexicon + negation window + intensifiers (English)"}
    out = ["# Sentiment and tone", ""]
    out.append("- overall score: %.2f -> **%s**" % (total, label))
    out.append("- lexeme matches: %d" % len(hits))
    if granularity == "sentence":
        out.append("")
        out.append("| sentence | score | label |")
        out.append("| --- | --- | --- |")
        for sentence in core.sentence_split(text):
            sent_score, _ = sentiment_scores(sentence, lex)
            sent_label = "positive" if sent_score > 0.5 else "negative" if sent_score < -0.5 else "neutral"
            out.append("| %s | %.2f | %s |" % (sentence[:90].replace("|", "/"), sent_score, sent_label))
    if rows:
        out.append("")
        out.append(core.table(rows, ["term", "weighted value"]))
    return {"output": "\n".join(out), "data": data}


def argument_mapper(ctx):
    text = ctx.require_text(minimum=60)
    claims, evidence, warrants, conclusions = [], [], [], []
    for sentence in core.sentence_split(text):
        lowered = sentence.lower()
        if re.search(r"\b(therefore|thus|so,|in conclusion|we should|i argue|this shows)\b", lowered):
            conclusions.append(sentence)
        elif re.search(r"\b(because|since|as a result|due to|given that)\b", lowered):
            warrants.append(sentence)
        elif re.search(r"\b(according to|data|study|survey|report|measured|percent|\d)\b", lowered):
            evidence.append(sentence)
        elif re.search(r"\b(should|must|need to|ought)\b", lowered):
            claims.append(sentence)
        else:
            claims.append(sentence) if len(sentence.split()) < 30 else evidence.append(sentence)
    data = {"claims": claims, "evidence": evidence, "warrants": warrants,
            "conclusions": conclusions,
            "balance": {"claims": len(claims), "evidence": len(evidence)}}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Argument map", ""]
    for label, items in (("Claims", claims), ("Evidence", evidence),
                         ("Warrants (because/based on)", warrants), ("Conclusions", conclusions)):
        lines.append("## %s (%d)" % (label, len(items)))
        lines.extend("- %s" % core.truncate(i.strip(), 160) for i in items[:12])
        lines.append("")
    if claims and len(evidence) == 0:
        lines.append("> No evidence sentences were detected: every claim currently stands unsupported.")
    return {"output": "\n".join(lines), "data": data}


def document_structure(ctx):
    text = ctx.require_text(minimum=10)
    lines = core.canonical_newlines(text).split("\n")
    outline, stack = [], []
    paragraph_count = 0
    in_para = False
    for idx, line in enumerate(lines, 1):
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            outline.append({"level": level, "title": match.group(2).strip(), "line": idx})
            in_para = False
            continue
        if line.strip():
            if not in_para:
                paragraph_count += 1
                in_para = True
        else:
            in_para = False
    headings = [o for o in outline if o["level"] <= int(ctx.params["max_depth"])]
    bullets = [ln for ln in lines if re.match(r"^\s*[-*+]\s", ln)]
    tables = [ln for ln in lines if ln.strip().startswith("|")]
    data = {"headings": headings, "paragraphs": paragraph_count, "bullets": len(bullets),
            "table_rows": len(tables), "words": len(text.split()),
            "max_depth": max((h["level"] for h in headings), default=0)}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    tree = "\n".join("%s%s" % ("  " * (h["level"] - 1), h["title"]) for h in headings)
    summary = ("%d heading(s), %d paragraph(s), %d bullet(s), %d table row(s), %d words"
               % (len(headings), paragraph_count, len(bullets), len(tables), data["words"]))
    output = summary + ("\n\n" + tree if tree else "")
    return {"output": output, "data": data}


def concordance_builder(ctx):
    text = ctx.require_text(minimum=20)
    term = ctx.params["term"].strip()
    window = int(ctx.params["window"])
    if not term:
        freq = H.word_freq(text)
        term = next(iter(sorted(freq, key=freq.get, reverse=True)), "")
        if not term:
            raise core.InvalidInput("no searchable term found in the input")
    rows = []
    for match in re.finditer(r"\b%s\b" % re.escape(term), text, re.I):
        before = text[max(0, match.start() - window * 6):match.start()].replace("\n", " ")
        after = text[match.end():match.end() + window * 6].replace("\n", " ")
        rows.append([before[-window * 6:], match.group(0), after])
    counts = {}
    for word in re.findall(r"\b[A-Za-z][A-Za-z'-]+\b", text.lower()):
        counts[word] = counts.get(word, 0) + 1
    frequent = sorted(counts.items(), key=lambda kv: -kv[1])[: int(ctx.params["min_count"]) and 15]
    data = {"term": term, "hits": len(rows), "lines": rows,
            "frequency": frequent}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    out = ["# Concordance for %r (%d occurrences)" % (term, len(rows)), ""]
    out.append(core.table(rows[:40], ["left context", "match", "right context"]) if rows else "no occurrences")
    out.append("")
    out.append(core.table([list(p) for p in frequent], ["word", "count"]))
    return {"output": "\n".join(out), "data": data}


def similarity_detector(ctx):
    text = ctx.require_text(minimum=20)
    unit = ctx.params["unit"]
    threshold = float(ctx.params["threshold"])
    ignore_case = bool(ctx.params["ignore_case"])

    def units(blob):
        if unit == "sentence":
            return [s.strip() for s in core.sentence_split(blob) if s.strip()]
        if unit == "document":
            return [blob.strip()]
        return [ln.strip() for ln in core.canonical_newlines(blob).split("\n") if ln.strip()]

    if ctx.text2.strip():
        left, right = units(text), units(ctx.text2)
        rows = []
        for a in left:
            best, score = None, 0.0
            for b in right:
                ratio = difflib.SequenceMatcher(None, a.lower() if ignore_case else a,
                                                b.lower() if ignore_case else b).ratio()
                if ratio > score:
                    best, score = b, ratio
            if best is not None:
                rows.append({"source": a[:120], "match": best[:120], "score": round(score, 3),
                             "duplicate": score >= threshold})
        duplicates = [r for r in rows if r["duplicate"]]
        data = {"mode": "two-text", "pairs": len(rows), "duplicates": len(duplicates),
                "threshold": threshold, "results": rows}
        output = core.table([[r["score"], r["duplicate"], r["source"][:50], r["match"][:50]] for r in rows[:40]],
                            ["score", "dup", "source", "closest match"])
        return {"output": output, "data": data,
                "warnings": ["%d pair(s) at or above the duplicate threshold" % len(duplicates)] if duplicates else []}
    items = units(text)
    flagged = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
            if ratio >= threshold:
                flagged.append({"a": i + 1, "b": j + 1, "score": round(ratio, 3),
                                "text_a": a[:100], "text_b": b[:100]})
    data = {"mode": "single-text", "units": len(items), "duplicates": len(flagged),
            "threshold": threshold, "results": flagged}
    rows = [[f["a"], f["b"], f["score"], f["text_a"][:40], f["text_b"][:40]] for f in flagged]
    output = core.table(rows, ["unit a", "unit b", "score", "text a", "text b"]) if rows else "no near-duplicates above threshold"
    return {"output": output, "data": data,
            "warnings": ["%d near-duplicate pair(s) found" % len(flagged)] if flagged else []}


def classification_designer(ctx):
    text = ctx.require_text(minimum=20)
    labels_raw = ctx.params["labels"].strip()
    if not labels_raw:
        raise core.InvalidInput("give labels with --set labels='billing,support,other'")
    labels = [l.strip() for l in re.split(r"[,;]", labels_raw) if l.strip()]
    method = ctx.params["method"]
    lines = [ln.strip() for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    rules = {}
    for label in labels:
        if method == "regex":
            rules[label] = {"pattern": r"\b%s\b" % re.escape(label)}
        else:
            keywords = []
            for line in lines:
                if re.search(r"\b%s\b" % re.escape(label), line, re.I):
                    keywords.extend([w for w in core.lower_words(line) if len(w) > 4])
            rules[label] = {"keywords": sorted(set(keywords))[:10]}
    assignments, unmatched = [], []
    for line in lines:
        scores = {}
        for label, rule in rules.items():
            if method == "regex":
                scores[label] = 1 if re.search(rule["pattern"], line, re.I) else 0
            else:
                scores[label] = sum(1 for k in rule["keywords"] if k in line.lower())
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            unmatched.append(line[:80])
            assignments.append({"text": line[:120], "label": "UNMATCHED", "scores": scores})
        else:
            assignments.append({"text": line[:120], "label": best, "scores": scores})
    coverage = 1 - len(unmatched) / max(1, len(lines))
    data = {"labels": labels, "method": method, "rules": rules,
            "assignments": assignments, "coverage": round(coverage, 3),
            "unmatched": unmatched[:20]}
    out = ["# Classifier design (%s)" % method, ""]
    out.append("rules:")
    for label, rule in rules.items():
        out.append("- **%s**: %s" % (label, rule.get("keywords") or rule.get("pattern")))
    out.append("")
    out.append("coverage: %.0f%% of %d line(s)" % (coverage * 100, len(lines)))
    out.append("")
    out.append(core.table([[a["label"], a["text"][:70]] for a in assignments[:25]], ["label", "text"]))
    if coverage < 0.8:
        out.append("")
        out.append("> Coverage below 80%%: add keywords or a fallback label before relying on this rule set.")
    return {"output": "\n".join(out), "data": data,
            "warnings": ["%d line(s) unmatched" % len(unmatched)] if unmatched else []}


def requirement_extractor(ctx):
    text = ctx.require_text(minimum=20)
    lex = _lex(ctx)
    wanted = [k.lower() for k in ctx.params["kinds"]]
    rows = []
    for sentence in core.sentence_split(text):
        for kind, cues in lex["obligation_cues"].items():
            if kind not in wanted:
                continue
            if any(re.search(r"\b%s\b" % re.escape(cue), sentence, re.I) for cue in cues):
                actor = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:must|shall|should|may)\b", sentence)
                rows.append({"kind": kind, "text": sentence.strip(),
                             "actor": actor.group(1) if actor else "",
                             "id": "R%d" % (len(rows) + 1)})
    data = {"requirements": rows, "count": len(rows),
            "by_kind": {k: sum(1 for r in rows if r["kind"] == k) for k in wanted}}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    table_rows = [[r["id"], r["kind"], r["actor"] or "-", r["text"][:90]] for r in rows]
    output = core.table(table_rows, ["id", "kind", "actor", "requirement"]) if rows else "no obligations detected"
    return {"output": output, "data": data}


def timeline_extractor(ctx):
    text = ctx.require_text(minimum=20)
    events = []
    for sentence in core.sentence_split(text):
        for match in re.finditer(PATTERNS["dates"], sentence):
            events.append({"when": match.group(0), "event": sentence.strip()[:180],
                           "key": day_key(match.group(0))})
    if ctx.params["sort"]:
        events.sort(key=lambda e: e["key"])
    data = {"events": events, "count": len(events)}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    rows = [[e["when"], e["event"][:90]] for e in events]
    output = core.table(rows, ["when", "event"]) if rows else "no dated events found"
    return {"output": output, "data": data,
            "warnings": [] if events else ["no recognisable date expressions in the input"]}


def qa_miner(ctx):
    text = ctx.require_text(minimum=20)
    pairs = []
    lines = [ln.strip() for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    pending = None
    for line in lines:
        q = re.match(r"^(?:Q|Question)\s*[:.\-]\s*(.+)$", line, re.I)
        a = re.match(r"^(?:A|Answer)\s*[:.\-]\s*(.+)$", line, re.I)
        if q:
            pending = q.group(1)
        elif a and pending:
            pairs.append({"question": pending, "answer": a.group(1)})
            pending = None
        elif line.endswith("?"):
            pending = line.rstrip("?") + "?"
        elif pending:
            pairs.append({"question": pending, "answer": line})
            pending = None
    limit = int(ctx.params["max_pairs"])
    pairs = pairs[:limit]
    data = {"pairs": pairs, "count": len(pairs)}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    if ctx.params["format"] == "table":
        return {"output": core.table([[p["question"][:60], p["answer"][:80]] for p in pairs],
                                     ["question", "answer"]), "data": data}
    output = "\n\n".join("### %s\n%s" % (p["question"], p["answer"]) for p in pairs)
    return {"output": output or "no question/answer pairs found", "data": data}


def annotation_assistant(ctx):
    text = ctx.require_text(minimum=10)
    kinds = [k.lower() for k in ctx.params["kinds"]]
    mapping = {"emails": "EMAIL", "urls": "URL", "phone": "PHONE", "dates": "DATE",
               "money": "MONEY", "people": "PERSON", "orgs": "ORG", "ids": "ID"}
    spans = []
    for kind in kinds:
        pattern_key = {"phone": "phones", "people": None, "orgs": None}.get(kind, kind)
        if pattern_key:
            for match in re.finditer(PATTERNS.get(pattern_key, r""), text):
                spans.append({"start": match.start(), "end": match.end(),
                              "label": mapping.get(kind, kind.upper()),
                              "text": match.group(0), "confidence": 0.9})
    for match in re.finditer(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b", text):
        if "people" in kinds:
            spans.append({"start": match.start(), "end": match.end(), "label": "PERSON",
                          "text": match.group(0), "confidence": 0.55})
    for match in re.finditer(r"\b([A-Z][A-Za-z&.-]+(?:\s+[A-Z][A-Za-z&.-]+){0,3})\s+(Inc|Ltd|LLC|Corp|GmbH|Company|Group|Bank|University)\b", text):
        if "orgs" in kinds:
            spans.append({"start": match.start(), "end": match.end(), "label": "ORG",
                          "text": match.group(0), "confidence": 0.6})
    spans.sort(key=lambda s: s["start"])
    records = []
    for span in spans:
        records.append({"text": text[span["start"]:span["end"]], "label": span["label"],
                        "start": span["start"], "end": span["end"],
                        "confidence": span["confidence"]})
    if ctx.params["format"] == "jsonl":
        output = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    else:
        output = core.table([[r["label"], r["text"], r["start"], r["confidence"]] for r in records],
                            ["label", "text", "offset", "confidence"])
    data = {"annotations": records, "count": len(records),
            "review_queue": [r for r in records if r["confidence"] < 0.7]}
    return {"output": output or "no annotations produced", "data": data,
            "warnings": ["%d annotation(s) below the 0.7 confidence threshold need review"
                         % len(data["review_queue"])] if data["review_queue"] else []}


# --------------------------------------------------------------------------
# retrieval (local reference capabilities)
# --------------------------------------------------------------------------


def standard_navigator(ctx):
    tables = ctx.assets.json(UNI_ASSET)
    standards = tables["standards"]
    query = ctx.params["query"].strip() or ctx.text.strip()
    area = ctx.params["area"]
    area_map = {"unicode": ["unicode", "uax15", "uax9", "uax29", "uax31", "uax39", "uax44", "utn11", "tr51", "tr35"],
                "encoding": ["rfc3629", "ecma404", "rfc8259", "rfc4180", "rfc2047"],
                "web": ["commonmark", "gfm", "webvtt", "aria"],
                "idna": ["idna", "bpc47", "iso639", "iso15924"],
                "i18n": ["cldr", "icu", "iso8601", "bpc47", "uax29"],
                "accessibility": ["wcag", "aria", "srt", "webvtt"]}.get(area)
    candidates = [s for s in standards if not area_map or s["id"] in area_map]
    if query:
        lowered = query.lower()
        scored = []
        for entry in candidates:
            haystack = " ".join([entry["name"], entry["scope"], entry["org"]]).lower()
            score = haystack.count(lowered) * 2 + sum(1 for word in lowered.split() if word in haystack)
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda pair: -pair[0])
        candidates = [entry for _, entry in scored]
    if not candidates:
        raise core.InvalidInput("no standard matched %r" % query,
                                hint="Try 'normalization', 'bidi', 'json', 'captions' or 'accessibility'.")
    data = {"query": query, "area": area, "results": candidates}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    if ctx.params["format"] == "table":
        return {"output": core.table([[c["id"], c["name"], c["url"]] for c in candidates],
                                     ["id", "standard", "url"]), "data": data}
    lines = ["# Standards matching %r" % (query or area), ""]
    for entry in candidates:
        lines.append("## %s" % entry["name"])
        lines.append("- id: `%s` | org: %s" % (entry["id"], entry["org"]))
        lines.append("- scope: %s" % entry["scope"])
        lines.append("- source: %s" % entry["url"])
        lines.append("")
    lines.append("These are canonical references; the Skill Tool never fetches them itself.")
    return {"output": "\n".join(lines), "data": data}


def shortcut_retriever(ctx):
    tables = ctx.assets.json(UNI_ASSET)
    query = ctx.params["query"].strip() or ctx.text.strip()
    platform = ctx.params["platform"]
    entries = tables["keyboard_shortcuts"]
    if query:
        def norm(value):
            return re.sub(r"[^a-z0-9]", "", value.lower())
        needle = norm(query)
        entries = [e for e in entries if needle in norm(e["symbol"]) or needle in norm(e["name"])]
    if not entries:
        raise core.InvalidInput("no shortcut matched %r" % query)
    platforms = ["windows", "macos", "linux", "emacs", "vim"] if platform == "any" else [platform]
    rows = [[e["symbol"], e["name"]] + [e.get(p, "") for p in platforms] for e in entries]
    if ctx.params["format"] == "json":
        return {"output": json.dumps(entries, indent=2, ensure_ascii=False),
                "data": {"entries": entries, "platforms": platforms}}
    if ctx.params["format"] == "markdown":
        header = "| symbol | name | " + " | ".join(platforms) + " |"
        sep = "| --- | --- | " + " | ".join("---" for _ in platforms) + " |"
        body = "\n".join("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
        return {"output": "\n".join([header, sep, body]), "data": {"entries": entries}}
    return {"output": core.table(rows, ["symbol", "name"] + platforms),
            "data": {"entries": entries, "platforms": platforms}}


def corpus_finder(ctx):
    query = ctx.params["query"].strip()
    corpus_path = ctx.params["corpus_file"].strip()
    if not corpus_path:
        raise core.InvalidInput("give --set corpus_file=path/to/corpus.txt")
    path = ctx.resolve(corpus_path, must_exist=True)
    corpus = core.read_text_file(path, max_bytes=int(ctx.cfg.get("max_input_bytes", 4 * 1024 * 1024)))
    context = int(ctx.params["context"])
    use_regex = bool(ctx.params["regex"])
    limit = int(ctx.params["max_hits"])
    lines = core.canonical_newlines(corpus).split("\n")
    hits = []
    if use_regex:
        try:
            pattern = re.compile(query)
        except re.error as exc:
            raise core.InvalidParam("invalid regex: %s" % exc)
    else:
        pattern = re.compile(re.escape(query), re.I)
    for idx, line in enumerate(lines):
        if query and pattern.search(line):
            start = max(0, idx - context)
            end = min(len(lines), idx + context + 1)
            hits.append({"line": idx + 1, "text": line.strip(),
                         "context": [l.strip() for l in lines[start:end]]})
            if len(hits) >= limit:
                break
    data = {"query": query, "corpus": corpus_path, "hits": hits, "count": len(hits),
            "corpus_lines": len(lines)}
    output = "\n\n".join("%d: %s" % (h["line"], "\n".join(h["context"])) for h in hits) or "no matches"
    return {"output": output, "data": data}


def source_conflict_resolver(ctx):
    left = ctx.require_text(minimum=5)
    right = ctx.require_second_text()
    claims_left = _claim_set(left)
    claims_right = _claim_set(right)
    only_left = claims_left - claims_right
    only_right = claims_right - claims_left
    conflicts = _numeric_conflicts(left, right)
    agreement = (len(claims_left & claims_right) / max(1, len(claims_left | claims_right)))
    data = {"agreement": round(agreement, 3), "only_in_source_a": sorted(only_left)[:20],
            "only_in_source_b": sorted(only_right)[:20], "numeric_conflicts": conflicts,
            "verdict": ("sources largely agree" if agreement > 0.6 and not conflicts else
                        "sources diverge; resolve with a primary source")}
    if ctx.params["mode"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Source conflict report", ""]
    lines.append("- overlap: %.0f%%" % (agreement * 100))
    lines.append("- verdict: %s" % data["verdict"])
    if conflicts:
        lines.append("")
        lines.append("## Numeric conflicts")
        lines.append(core.table([[c["value_a"], c["value_b"], c["context"]] for c in conflicts],
                                ["source A", "source B", "context"]))
    if only_left:
        lines.append("")
        lines.append("## Only in source A")
        lines.extend("- %s" % c for c in sorted(only_left)[:10])
    if only_right:
        lines.append("")
        lines.append("## Only in source B")
        lines.extend("- %s" % c for c in sorted(only_right)[:10])
    return {"output": "\n".join(lines), "data": data,
            "warnings": ["%d numeric conflict(s) found" % len(conflicts)] if conflicts else []}


def _claim_set(text):
    claims = set()
    for sentence in core.sentence_split(text):
        normalized = re.sub(r"\s+", " ", sentence.strip().lower())
        if len(normalized.split()) >= 4:
            claims.add(normalized[:160])
    return claims


def _numeric_conflicts(a, b):
    values_a = set(re.findall(r"\b\d[\d,.]*\b", a))
    values_b = set(re.findall(r"\b\d[\d,.]*\b", b))
    conflicts = []
    for value in sorted(values_a - values_b)[:10]:
        conflicts.append({"value_a": value, "value_b": "absent",
                          "context": "number present in source A only"})
    return conflicts


def research_note(ctx):
    title = ctx.params["title"].strip() or "Research note"
    claim = ctx.params["claim"].strip() or (ctx.text.strip()[:200] if ctx.text.strip() else "")
    source = ctx.params["source"].strip()
    retrieved = ctx.params["retrieved"].strip()
    style = ctx.params["style"]
    if not claim:
        raise core.InvalidInput("give --set claim=... or input text")
    if not source:
        raise core.InvalidInput("give --set source=... (citation or URL)",
                                hint="A research note without a source cannot be verified.")
    lines = ["# %s" % title, ""]
    lines.append("**Claim.** %s" % claim)
    lines.append("")
    lines.append("**Source.** %s" % source)
    if retrieved:
        lines.append("**Retrieved.** %s" % retrieved)
    lines.append("**Citation style.** %s" % style)
    lines.append("")
    lines.append("**Status.** unverified - the Skill Tool records what you supply; it does not fetch or check the source.")
    lines.append("")
    lines.append("## Evidence to attach")
    lines.append("- direct quotation supporting the claim")
    lines.append("- version or date of the source")
    lines.append("- what would falsify the claim")
    return {"output": "\n".join(lines),
            "data": {"title": title, "claim": claim, "source": source,
                     "retrieved": retrieved, "style": style, "verified": False}}


def reference_table(ctx):
    text = ctx.require_text(minimum=3)
    fmt = ctx.params["format"]
    sort_by = ctx.params["sort_by"].strip()
    caption = ctx.params["caption"].strip()
    rows = []
    for line in core.canonical_newlines(text).split("\n"):
        if not line.strip():
            continue
        if "," in line:
            rows.append([c.strip() for c in line.split(",")])
        elif "|" in line:
            rows.append([c.strip() for c in line.strip("|").split("|")])
        elif "\t" in line:
            rows.append([c.strip() for c in line.split("\t")])
        else:
            rows.append([line.strip()])
    if not rows:
        raise core.InvalidInput("no rows found")
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else rows
    if sort_by and sort_by in header:
        idx = header.index(sort_by)
        body.sort(key=lambda r: r[idx] if idx < len(r) else "")
    width = max(len(r) for r in body)
    body = [r + [""] * (width - len(r)) for r in body]
    if fmt == "latex":
        cols = " | ".join("l" for _ in range(len(header)))
        lines = ["\\begin{table}[h]", "\\centering"]
        if caption:
            lines.append("\\caption{%s}" % caption)
        lines.append("\\begin{tabular}{%s}" % cols)
        lines.append(" \\hline")
        lines.append(" & ".join(header) + " \\\\")
        lines.append(" \\hline")
        for row in body:
            lines.append(" & ".join(row) + " \\\\")
        lines.append(" \\hline")
        lines.append("\\end{tabular}")
        lines.append("\\end{table}")
        return {"output": "\n".join(lines), "data": {"rows": len(body), "columns": len(header)}}
    if fmt == "csv":
        output = "\n".join(",".join('"%s"' % c.replace('"', '""') for c in row) for row in [header] + body)
        return {"output": output, "data": {"rows": len(body), "columns": len(header)}}
    if fmt == "ascii":
        return {"output": core.table(body, header), "data": {"rows": len(body), "columns": len(header)}}
    lines = []
    if caption:
        lines.append("**%s**" % caption)
        lines.append("")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return {"output": "\n".join(lines), "data": {"rows": len(body), "columns": len(header)}}


HANDLERS = {
    "analysis.key-information-extractor": key_information,
    "analysis.topic-and-theme-analyzer": topic_analyzer,
    "analysis.sentiment-and-tone-analyzer": sentiment_analyzer,
    "analysis.argument-mapper": argument_mapper,
    "analysis.document-structure-analyzer": document_structure,
    "analysis.terminology-and-concordance-builder": concordance_builder,
    "analysis.similarity-and-duplicate-detector": similarity_detector,
    "analysis.text-classification-designer": classification_designer,
    "analysis.requirement-and-obligation-extractor": requirement_extractor,
    "analysis.timeline-and-event-extractor": timeline_extractor,
    "analysis.question-and-answer-miner": qa_miner,
    "analysis.text-dataset-annotation-assistant": annotation_assistant,
    "retrieval.text-standard-navigator": standard_navigator,
    "retrieval.shortcut-documentation-retriever": shortcut_retriever,
    "retrieval.corpus-example-finder": corpus_finder,
    "retrieval.source-conflict-resolver": source_conflict_resolver,
    "retrieval.citation-ready-research-note-maker": research_note,
    "retrieval.reference-table-builder": reference_table,
}
