"""Text, requirement, context, combination, and evidence handlers.

Families: W (worksheet), R (requirements), A (text audits), C (context control),
X (combinatorial variation), E (evidence and sources).
Each handler takes a Context and returns {"summary": {...}, "items": [...], ...}.
Loaded by tools-engine.py; do not run directly.
"""
import importlib.util
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from itertools import product

TOOL_DIR = Path(__file__).resolve().parent
HANDLERS = {}


def _load_core():
    name = "tools_core_shared"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL_DIR / "tools-core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def handler(key):
    def register(fn):
        HANDLERS[key] = fn
        return fn
    return register


def _term_regex(terms):
    """Whole-term regex that works for terms ending in punctuation such as 100%."""
    ordered = sorted(set(terms), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in ordered) + r")(?![A-Za-z0-9])", re.I)


def _scan(lines, rules, ctx):
    """rules: list of (rule_name, compiled_regex, severity, note). One finding per rule per line."""
    findings = []
    for lineno, line in enumerate(lines, start=1):
        ctx.tick()
        for name, rx, severity, note in rules:
            m = rx.search(line)
            if m:
                findings.append({"line": lineno, "rule": name, "severity": severity,
                                 "match": core.excerpt(m.group(0), 60), "note": note,
                                 "excerpt": core.excerpt(line, 160)})
    return findings


def _severity_summary(findings):
    counts = Counter(f["severity"] for f in findings)
    return {"findings": len(findings), "high": counts.get("high", 0),
            "medium": counts.get("medium", 0), "low": counts.get("low", 0)}


def _limit(items, ctx):
    if len(items) > ctx.policy["limits"]["max_items"]:
        raise core.InputError(f"input has {len(items)} items; the limit is {ctx.policy['limits']['max_items']}")
    return items


def _lines(ctx):
    text = ctx.read_text()
    return core.split_lines(text)


# ====================================================================== W
def split_fields(description):
    parts = re.split(r",\s*|;\s*", description.rstrip("."))
    fields = []
    for part in parts:
        part = re.sub(r"^(and|or)\s+", "", part.strip())
        if len(part) > 1:
            fields.append(part)
    return fields


@handler("W:worksheet")
def worksheet(ctx):
    cap = ctx.capability
    fields = split_fields(cap["description"])
    source_text = ""
    if ctx.input_path is not None:
        source_text = ctx.read_text()
    excerpt_block = ("```text\n" + source_text[:1500] + ("\n..." if len(source_text) > 1500 else "") + "\n```") if source_text else "_No source material was supplied._"
    template = (TOOL_DIR / "asset-worksheet-template.md").read_text(encoding="utf-8")
    body = (template.replace("{{title}}", cap["name"])
            .replace("{{id}}", cap["id"])
            .replace("{{category}}", f"{cap['category_no']}. {cap['category']}")
            .replace("{{description}}", cap["description"])
            .replace("{{fields}}", "\n".join(f"- [ ] {f}" for f in fields))
            .replace("{{input_excerpt}}", excerpt_block)
            .replace("{{generated}}", core.now_iso())
            .replace("{{support}}", cap["support_level"]))
    path = ctx.write_artifact(f"{ctx.slug}.worksheet.md", body)
    return {
        "summary": {"fields": len(fields), "source_supplied": bool(source_text)},
        "items": [{"field": f, "answer": ""} for f in fields],
        "artifact": path,
        "completion": "structure-only; a person or agent must answer each field with evidence",
    }


# ====================================================================== R
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]\])\s+(.*\S)")


def _cue_regex(cues):
    ordered = sorted(set(cues), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(c) for c in ordered) + r")\b", re.I)


def extract_requirements(text, policy, ctx):
    cue_rx = _cue_regex(policy["requirement_cues"])
    hard_rx = _cue_regex(policy["hard_cues"])
    pref_rx = _cue_regex(policy["preference_cues"])
    stop = policy["stopwords"]
    found = []
    for lineno, raw in enumerate(core.split_lines(text), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        bullet = _BULLET.match(raw)
        body = bullet.group(1) if bullet else raw.strip()
        if bullet or cue_rx.search(body):
            if hard_rx.search(body):
                kind = "hard"
            elif pref_rx.search(body):
                kind = "preference"
            else:
                kind = "open"
            found.append({"line": lineno, "type": kind, "text": body,
                          "keywords": sorted(core.content_words(body, stop))})
    return _limit(found, ctx)


@handler("R:extract")
def r_extract(ctx):
    text = ctx.read_text()
    reqs = extract_requirements(text, ctx.policy, ctx)
    items = []
    for i, req in enumerate(reqs, start=1):
        items.append({"id": f"REQ-{i:03d}", "line": req["line"], "type": req["type"],
                      "text": core.excerpt(req["text"], 300), "keywords": ", ".join(req["keywords"][:8])})
    kinds = Counter(r["type"] for r in reqs)
    if not reqs:
        ctx.warnings.append("no requirement-like lines were found; use bullets or cue words such as must, should, or only")
    return {"summary": {"requirements": len(items), "hard": kinds.get("hard", 0),
                        "preference": kinds.get("preference", 0), "open": kinds.get("open", 0)},
            "items": items}


def _doc_index(ctx, root, exclude_paths):
    policy = ctx.policy
    docs = {}
    skip = {ctx.state_dir.name, ctx.output_dir.name}
    for path in core.walk_files(root, policy, extensions=core.TEXT_EXTENSIONS, skip_dirs=skip):
        resolved = path.resolve()
        if resolved in exclude_paths or resolved.is_relative_to(ctx.state_dir.resolve()) or resolved.is_relative_to(ctx.output_dir.resolve()):
            continue
        if path.stat().st_size > policy["limits"]["max_scan_file_bytes"]:
            ctx.warnings.append(f"skipped large file {path.name}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        docs[str(path.relative_to(root))] = core.content_words(text, policy["stopwords"])
        ctx.tick()
    return docs


@handler("R:coverage")
def r_coverage(ctx):
    policy = ctx.policy
    text = ctx.read_text()
    reqs = extract_requirements(text, policy, ctx)
    root = Path(ctx.opt("check_dir")).resolve() if ctx.opt("check_dir") else (ctx.input_path.parent if ctx.input_path.is_file() else ctx.input_path)
    if not root.is_dir():
        raise core.InputError(f"check_dir is not a folder: {root}")
    docs = _doc_index(ctx, root, {ctx.input_path.resolve()})
    if not docs:
        ctx.warnings.append("no text files were found to check against; every requirement is reported as missing")
    cov_min = policy["defaults"]["coverage_covered"]
    part_min = policy["defaults"]["coverage_partial"]
    items = []
    for i, req in enumerate(reqs, start=1):
        ctx.tick()
        kw = set(req["keywords"])
        best_doc, best = "", 0.0
        if kw:
            for name, tokens in docs.items():
                ratio = len(kw & tokens) / float(len(kw))
                if ratio > best:
                    best, best_doc = ratio, name
        else:
            ctx.warnings.append(f"REQ-{i:03d} has no content words to match; review it manually")
        status = "covered" if best >= cov_min else ("partial" if best >= part_min else "missing")
        items.append({"id": f"REQ-{i:03d}", "type": req["type"], "status": status,
                      "match": round(best, 2), "best_file": best_doc, "text": core.excerpt(req["text"], 200)})
    counts = Counter(i["status"] for i in items)
    total = len(items)
    score = round((counts.get("covered", 0) + 0.5 * counts.get("partial", 0)) / total, 3) if total else 0.0
    return {"summary": {"requirements": total, "covered": counts.get("covered", 0),
                        "partial": counts.get("partial", 0), "missing": counts.get("missing", 0),
                        "coverage_score": score, "files_checked": len(docs), "method": "keyword overlap (assisted)"},
            "items": items}


@handler("R:checks")
def r_checks(ctx):
    text = ctx.read_text()
    reqs = extract_requirements(text, ctx.policy, ctx)
    items = []
    for i, req in enumerate(reqs, start=1):
        method = "must-pass" if req["type"] == "hard" else "report"
        items.append({"id": f"REQ-{i:03d}", "method": method, "keywords": ", ".join(req["keywords"][:8]),
                      "check": f"Confirm the deliverable satisfies: {core.excerpt(req['text'], 180)}"})
    return {"summary": {"checks": len(items), "must_pass": sum(1 for i in items if i["method"] == "must-pass")},
            "items": items}


# ====================================================================== A
@handler("A:claims")
def a_claims(ctx):
    p = ctx.policy
    lines = _lines(ctx)
    absolute = _term_regex(p["absolute_terms"])
    markers = [m.lower() for m in p["source_markers"]]
    figure = re.compile(r"(?<![A-Za-z])\d[\d,.]*\s?%?")
    findings = _scan(lines, [("absolute-claim", absolute, "medium",
                              "absolute wording needs evidence or a qualifier")], ctx)
    for lineno, line in enumerate(lines, start=1):
        body = re.sub(r"^\s*(\d+[.)]|[-*])\s+", "", line)
        if figure.search(body) and not any(mk in line.lower() for mk in markers):
            findings.append({"line": lineno, "rule": "unsourced-figure", "severity": "low",
                             "match": figure.search(body).group(0).strip(), "note": "number without a visible source marker",
                             "excerpt": core.excerpt(line, 160)})
    return {"summary": _severity_summary(findings), "items": findings}


@handler("A:fallacy")
def a_fallacy(ctx):
    rules = []
    for pat in ctx.policy["fallacy_patterns"]:
        rules.append((pat["name"], re.compile(pat["pattern"], re.I), pat["severity"],
                      "reasoning pattern that often hides an unsupported step; check the argument"))
    findings = _scan(_lines(ctx), rules, ctx)
    return {"summary": _severity_summary(findings), "items": findings}


@handler("A:ambiguity")
def a_ambiguity(ctx):
    p = ctx.policy
    text = ctx.read_text()
    lines = core.split_lines(text)
    findings = _scan(lines, [("vague-term", _term_regex(p["vague_terms"]), "low",
                              "vague quantity or instruction; replace with a measurable or named value")], ctx)
    for lineno, sentence in core.sentences_with_lines(text):
        if re.match(r"(This|It|That|These|Those|They)\b", sentence):
            findings.append({"line": lineno, "rule": "unclear-reference", "severity": "low",
                             "match": sentence.split()[0], "note": "sentence opens with a pronoun; name the referent",
                             "excerpt": core.excerpt(sentence, 160)})
    allowed = set(p["allowed_acronyms"])
    seen = set()
    for lineno, line in enumerate(lines, start=1):
        for acr in re.findall(r"\b[A-Z]{2,6}\b", line):
            if acr in allowed or acr in seen:
                continue
            seen.add(acr)
            defined = ("(" + acr + ")") in text or re.search(r"\b" + acr + r"\s*\(", text)
            if not defined:
                findings.append({"line": lineno, "rule": "undefined-acronym", "severity": "low",
                                 "match": acr, "note": "acronym is not defined in this text",
                                 "excerpt": core.excerpt(line, 160)})
    return {"summary": _severity_summary(findings), "items": findings}


_LABEL_EXCLUDE = {"step", "item", "phase", "section", "option", "page", "line", "version", "req",
                  "figure", "table", "rule", "point", "day", "week", "month", "year", "example"}


@handler("A:consistency")
def a_consistency(ctx):
    rx = re.compile(r"(?P<label>[A-Za-z][A-Za-z /_-]{2,48}?)\s*(?::|=|\bis\b|\bare\b|\bwas\b)\s*(?P<value>\$?\d[\d,]*(?:\.\d+)?%?)")
    seen = defaultdict(list)
    lines = _lines(ctx)
    for lineno, line in enumerate(lines, start=1):
        ctx.tick()
        for m in rx.finditer(line):
            label = re.sub(r"\s+", " ", m.group("label").strip().lower())
            label = re.sub(r"^(the|a|an)\s+", "", label)
            if not label or label.split()[0] in _LABEL_EXCLUDE:
                continue
            value = re.sub(r"[$,%]", "", m.group("value"))
            seen[label].append((lineno, value, m.group("value")))
    findings = []
    for label, occ in seen.items():
        values = {v for _, v, _ in occ}
        if len(values) > 1:
            first_line, first_value, _ = occ[0]
            for lineno, value, raw in occ[1:]:
                if value != first_value:
                    findings.append({"line": lineno, "rule": "conflicting-value", "severity": "high",
                                     "match": f"{label}: {raw}",
                                     "note": f"'{label}' was {first_value} on line {first_line}",
                                     "excerpt": core.excerpt(lines[lineno - 1], 160)})
    return {"summary": _severity_summary(findings), "items": findings}


def _syllables(word):
    groups = re.findall(r"[aeiouy]+", word.lower())
    count = len(groups)
    if word.lower().endswith("e") and count > 1:
        count -= 1
    return max(1, count)


@handler("A:clarity")
def a_clarity(ctx):
    limit = ctx.policy["defaults"]["long_sentence_words"]
    sents = core.sentences_with_lines(ctx.read_text())
    if not sents:
        raise core.InputError("no sentences found in input")
    findings, word_total, syll_total = [], 0, 0
    for lineno, sentence in sents:
        ctx.tick()
        ws = re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)
        word_total += len(ws)
        syll_total += sum(_syllables(w) for w in ws)
        if len(ws) > limit:
            findings.append({"line": lineno, "rule": "long-sentence", "severity": "low",
                             "match": f"{len(ws)} words", "note": f"more than {limit} words; split or shorten",
                             "excerpt": core.excerpt(sentence, 160)})
    avg = word_total / float(len(sents))
    flesch = 206.835 - 1.015 * (word_total / float(len(sents))) - 84.6 * (syll_total / float(max(1, word_total)))
    band = "easy" if flesch >= 60 else ("moderate" if flesch >= 30 else "difficult")
    summary = {"sentences": len(sents), "avg_words_per_sentence": round(avg, 1),
               "flesch_reading_ease": round(flesch, 1), "readability": band}
    summary.update(_severity_summary(findings))
    return {"summary": summary, "items": findings}


@handler("A:pii")
def a_pii(ctx):
    patterns = [(p["name"], re.compile(p["pattern"]), p.get("luhn", False)) for p in ctx.policy["pii_patterns"]]
    lines = _lines(ctx)
    findings, counts = [], Counter()
    for lineno, line in enumerate(lines, start=1):
        ctx.tick()
        redacted = line
        hit_names = []
        for name, rx, luhn in patterns:
            for m in rx.finditer(line):
                if luhn and not _luhn_ok(m.group(0)):
                    continue
                hit_names.append(name)
                redacted = redacted.replace(m.group(0), f"[REDACTED:{name}]")
        for name in dict.fromkeys(hit_names):
            counts[name] += 1
            findings.append({"line": lineno, "rule": name, "severity": "high",
                             "match": "[redacted]", "note": "possible sensitive data; minimize, redact, or keep out of memory",
                             "excerpt": core.excerpt(redacted, 160)})
    summary = _severity_summary(findings)
    summary["by_type"] = dict(counts)
    return {"summary": summary, "items": findings}


def _luhn_ok(raw):
    digits = re.sub(r"\D", "", raw)
    if not 13 <= len(digits) <= 19:
        return False
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@handler("A:causal")
def a_causal(ctx):
    rx = _term_regex(ctx.policy["causal_terms"])
    findings = _scan(_lines(ctx), [("causal-language", rx, "medium",
                                    "causal wording needs experiment or strong quasi-experimental design; otherwise use association language")], ctx)
    return {"summary": _severity_summary(findings), "items": findings}


@handler("A:bias-cues")
def a_bias(ctx):
    rx = _term_regex(ctx.policy["bias_cues"])
    findings = _scan(_lines(ctx), [("bias-cue", rx, "low",
                                    "wording that can signal anchoring, sunk cost, or confirmation bias; test the claim against alternatives")], ctx)
    return {"summary": _severity_summary(findings), "items": findings}


@handler("A:questions")
def a_questions(ctx):
    findings = []
    for lineno, sentence in core.sentences_with_lines(ctx.read_text()):
        if "?" not in sentence:
            continue
        low = sentence.lower()
        leading = [c for c in ctx.policy["leading_cues"] if c in low]
        if leading:
            findings.append({"line": lineno, "rule": "leading-question", "severity": "medium",
                             "match": leading[0], "note": "question presupposes its answer; ask it neutrally",
                             "excerpt": core.excerpt(sentence, 160)})
        if re.search(r"\b(and|or)\b", low):
            findings.append({"line": lineno, "rule": "possible-double-barreled", "severity": "low",
                             "match": "and/or", "note": "may ask two questions at once; split it",
                             "excerpt": core.excerpt(sentence, 160)})
    return {"summary": _severity_summary(findings), "items": findings}


@handler("A:terminology")
def a_terminology(ctx):
    lines = _lines(ctx)
    forms = defaultdict(set)
    mid_forms = defaultdict(Counter)
    stop = set(ctx.policy["stopwords"])
    token_rx = re.compile(r"\b[A-Za-z]{3,}(?:[-_][A-Za-z]+)*\b")
    for lineno, line in enumerate(lines, start=1):
        ctx.tick()
        for m in token_rx.finditer(line):
            raw = m.group(0)
            key = re.sub(r"[-_]", "", raw.lower())
            if len(key) < 4 or key in stop:
                continue
            forms[key].add(raw)
            if m.start() > 0 and raw[0].isupper():
                mid_forms[key][raw] += 1
    findings = []
    for key, raws in sorted(forms.items()):
        lowers = {r.lower() for r in raws}
        if len(lowers) > 1:
            findings.append({"term": key, "variants": ", ".join(sorted(raws)), "rule": "spelling-variant",
                             "severity": "medium", "note": "different spellings of one term; choose one and define it"})
        elif len(raws) > 1 and key in mid_forms:
            # same letters differ only in case; headings often explain it, so this is a review prompt
            findings.append({"term": key, "variants": ", ".join(sorted(raws)), "rule": "case-variant",
                             "severity": "low", "note": "capitalization differs, including mid-sentence; review (headings are often the cause)"})
    return {"summary": _severity_summary(findings), "items": findings}


@handler("A:tone")
def a_tone(ctx):
    text = ctx.read_text()
    low = text.lower()
    counts = {
        "exclamations": text.count("!"),
        "politeness": len(re.findall(r"\b(please|thank you|thanks|kindly|appreciate)\b", low)),
        "hedges": len(re.findall(r"\b(maybe|perhaps|might|could|possibly|seems)\b", low)),
        "first_person": len(re.findall(r"\b(i|we|our|my)\b", low)),
        "second_person": len(re.findall(r"\b(you|your)\b", low)),
    }
    if counts["exclamations"] > 2:
        label = "enthusiastic; consider a calmer register for formal recipients"
    elif counts["politeness"] and counts["hedges"]:
        label = "courteous and tentative"
    elif counts["second_person"] > counts["first_person"]:
        label = "direct, recipient-focused"
    else:
        label = "neutral, informational"
    return {"summary": {**counts, "tone": label}, "items": []}


@handler("A:actions")
def a_actions(ctx):
    items = []
    for lineno, line in enumerate(_lines(ctx), start=1):
        if not re.search(r"(?i)\b(todo|action|next step|owner|due|deadline)\b|^\s*-\s*\[\s\]", line):
            continue
        owner_m = re.search(r"(?i)owner\s*[:=]\s*([A-Za-z][\w .'-]{0,40})", line) or re.search(r"@([A-Za-z][\w.-]{0,40})", line)
        due_m = re.search(r"\d{4}-\d{2}-\d{2}", line) or re.search(r"(?i)due\s*[:=]?\s*([^,;|]{3,30})", line)
        owner = owner_m.group(1).strip() if owner_m else ""
        due = due_m.group(0).strip() if due_m else ""
        status = "complete" if owner and due else ("missing owner" if not owner and due else ("missing due date" if owner else "missing owner and due date"))
        task = re.sub(r"(?i)^\s*(-\s*\[\s\]\s*|todo:?\s*|action:?\s*|next step:?\s*)", "", line).strip()
        items.append({"line": lineno, "task": core.excerpt(task, 160), "owner": owner, "due": due, "status": status})
    summary = {"action_items": len(items), "complete": sum(1 for i in items if i["status"] == "complete")}
    return {"summary": summary, "items": items}


@handler("A:classify")
def a_classify(ctx):
    classes = ctx.policy["feedback_classes"]
    order = ["defect", "misunderstanding", "new_requirement", "preference"]
    items, counts = [], Counter()
    for lineno, line in enumerate(_lines(ctx), start=1):
        if not line.strip():
            continue
        low = line.lower()
        label = "unclassified"
        for cls in order:
            if any(cue in low for cue in classes.get(cls, [])):
                label = cls
                break
        counts[label] += 1
        items.append({"line": lineno, "class": label, "text": core.excerpt(line.strip(), 200)})
    return {"summary": dict(counts), "items": _limit(items, ctx)}


@handler("A:query-sanitize")
def a_query_sanitize(ctx):
    text = " ".join(ctx.read_text().split())
    removed = Counter()
    for pat in ctx.policy["pii_patterns"]:
        rx = re.compile(pat["pattern"])
        def _sub(m, name=pat["name"]):
            if pat.get("luhn") and not _luhn_ok(m.group(0)):
                return m.group(0)
            removed[name] += 1
            return "[removed]"
        text = rx.sub(_sub, text)
    text = text[:300]
    return {"summary": {"characters": len(text), "removed_items": sum(removed.values())},
            "sanitized_query": text, "removed": dict(removed)}


# ====================================================================== C
def _budget_items(ctx):
    if ctx.input_path is not None and ctx.input_path.suffix.lower() == ".json":
        data = ctx.read_json()
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        if not isinstance(data, list):
            raise core.InputError("budget input JSON must be a list or an object with an 'items' list")
        items = []
        for idx, raw in enumerate(data, start=1):
            if isinstance(raw, str):
                raw = {"text": raw}
            if not isinstance(raw, dict) or "text" not in raw:
                raise core.InputError(f"item {idx} must be an object with a 'text' field")
            items.append(raw)
        return _limit(items, ctx)
    lines = [l for l in _lines(ctx) if l.strip()]
    return _limit([{"id": f"L{n}", "text": l.strip()} for n, l in enumerate(lines, start=1)], ctx)


@handler("C:budget")
def c_budget(ctx):
    factor = ctx.policy["defaults"]["token_factor"]
    budget = ctx.opt_int("budget_tokens", ctx.policy["defaults"]["budget_tokens"], 1)
    items = _budget_items(ctx)
    prepared = []
    for n, item in enumerate(items, start=1):
        priority = item.get("priority", 3)
        try:
            priority = int(priority)
        except (TypeError, ValueError) as exc:
            raise core.InputError(f"priority must be an integer 1-5 for item {n}") from exc
        if not 1 <= priority <= 5:
            raise core.InputError(f"priority must be between 1 and 5 for item {n}")
        tokens = int(item["tokens"]) if "tokens" in item else core.estimate_tokens(str(item["text"]), factor)
        prepared.append({"id": str(item.get("id", n)), "priority": priority, "tokens": tokens,
                         "text": core.excerpt(str(item["text"]), 200)})
    prepared.sort(key=lambda x: (-x["priority"], x["tokens"], x["id"]))
    used, items_out = 0, []
    for p in prepared:
        ctx.tick()
        if used + p["tokens"] <= budget:
            used += p["tokens"]
            items_out.append({**p, "decision": "kept"})
        else:
            items_out.append({**p, "decision": "dropped: over budget"})
    kept = sum(1 for i in items_out if i["decision"] == "kept")
    return {"summary": {"budget_tokens": budget, "used_tokens": used, "kept": kept,
                        "dropped": len(items_out) - kept},
            "items": items_out}


def _dedupe_lines(lines, ctx):
    stop = ctx.policy["stopwords"]
    threshold = ctx.policy["defaults"]["dedupe_jaccard"]
    max_near = ctx.policy["limits"]["max_near_duplicate_lines"]
    kept_index, kept_sets, seen_exact = [], [], {}
    actions = []
    for lineno, line in enumerate(lines, start=1):
        ctx.tick()
        if not line.strip():
            actions.append({"line": lineno, "action": "blank", "ref": ""})
            kept_index.append(line)
            continue
        key = " ".join(line.lower().split()).strip(" .;:")
        if key in seen_exact:
            actions.append({"line": lineno, "action": "removed: exact duplicate", "ref": seen_exact[key]})
            continue
        words_set = core.content_words(line, stop)
        dup_ref = None
        if words_set and len(kept_sets) <= max_near:
            for ref, prev_set in kept_sets:
                if core.jaccard(words_set, prev_set) >= threshold:
                    dup_ref = ref
                    break
        if dup_ref is not None:
            actions.append({"line": lineno, "action": "removed: near duplicate", "ref": dup_ref})
            continue
        seen_exact[key] = lineno
        if words_set:
            kept_sets.append((lineno, words_set))
        kept_index.append(line)
        actions.append({"line": lineno, "action": "kept", "ref": ""})
    return kept_index, actions


@handler("C:dedupe")
def c_dedupe(ctx):
    lines = _lines(ctx)
    kept, actions = _dedupe_lines(lines, ctx)
    cleaned = "\n".join(kept).strip() + "\n"
    artifact = ctx.write_artifact(f"{ctx.slug}.cleaned.txt", cleaned)
    removed = [a for a in actions if a["action"].startswith("removed")]
    return {"summary": {"input_lines": len(lines), "removed": len(removed),
                        "kept": len(lines) - len(removed), "artifact": artifact},
            "items": removed}


@handler("C:compress")
def c_compress(ctx):
    p = ctx.policy
    budget = ctx.opt_int("budget_tokens", p["defaults"]["budget_tokens"], 1)
    lines = _lines(ctx)
    kept_lines, _ = _dedupe_lines(lines, ctx)
    cues = [c.lower() for c in p["priority_cues"]]
    filler = [f.lower() for f in p["filler_phrases"]]
    detail_rx = re.compile(r"\d|https?://|/|`|\"")
    scored = []
    for idx, line in enumerate(kept_lines):
        ctx.tick()
        if not line.strip():
            continue
        low = line.lower()
        score = sum(2 for c in cues if re.search(r"\b" + re.escape(c) + r"\b", low))
        score += 1 if detail_rx.search(line) else 0
        score -= 2 * sum(1 for f in filler if f in low)
        scored.append((score, idx, line))
    chosen, used = set(), 0
    for score, idx, line in sorted(scored, key=lambda x: (-x[0], x[1])):
        tokens = core.estimate_tokens(line, p["defaults"]["token_factor"])
        if used + tokens <= budget and score >= 0:
            chosen.add(idx)
            used += tokens
    out_lines = [kept_lines[i] for i in sorted(chosen)]
    compressed = "\n".join(out_lines).strip() + "\n"
    artifact = ctx.write_artifact(f"{ctx.slug}.compressed.txt", compressed)
    # measure input and output the same way so the two figures are comparable
    in_tokens = core.estimate_tokens("\n".join(lines), p["defaults"]["token_factor"])
    out_tokens = core.estimate_tokens(compressed, p["defaults"]["token_factor"])
    return {"summary": {"input_tokens_est": in_tokens, "output_tokens_est": out_tokens,
                        "retained_lines": len(out_lines), "budget_tokens": budget, "artifact": artifact},
            "items": [{"kept_line": core.excerpt(l, 160)} for l in out_lines[:200]],
            "note": "Filler lines are removed and high-priority lines are kept first; check the artifact before relying on it."}


@handler("C:drift")
def c_drift(ctx):
    req_path = ctx.opt("request")
    if not req_path:
        raise core.InputError("option request=<path to the original request text> is required")
    request_text = core.read_text_file(Path(req_path), ctx.policy)
    work_text = ctx.read_text()
    stop = ctx.policy["stopwords"]
    req_set = core.content_words(request_text, stop)
    work_counter = Counter(w for w in core.words(work_text) if len(w) >= 4 and w not in stop)
    work_set = set(work_counter)
    if not req_set:
        raise core.InputError("request text has no content words to compare")
    recall = len(req_set & work_set) / float(len(req_set))
    precision = len(req_set & work_set) / float(len(work_set)) if work_set else 0.0
    minimum = ctx.opt_float("min_recall", ctx.policy["defaults"]["drift_recall_min"], 0, 1)
    missing = sorted(req_set - work_set)[:25]
    off = sorted(work_set - req_set, key=lambda w: -work_counter[w])[:25]
    items = [{"kind": "request term missing from work", "term": t} for t in missing] + \
            [{"kind": "work term not in request", "term": t} for t in off]
    return {"summary": {"request_terms": len(req_set), "recall": round(recall, 3),
                        "share_of_work_on_request": round(precision, 3),
                        "verdict": "drift-detected" if recall < minimum else "aligned",
                        "method": "term overlap (assisted)"},
            "items": items}


_PIN_PATTERNS = [
    ("url", re.compile(r"https?://\S+")),
    ("file-path", re.compile(r"(?:[\w.-]+/)+[\w.-]+|\b[\w.-]+\.(?:py|md|json|txt|csv|zip|yaml|yml|pdf|docx|xlsx|pptx|svg|png|jpg)\b")),
    ("version", re.compile(r"(?<![\w.])v?\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?(?![\w.])")),
    ("quantity", re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:%|ms|sec|min|MB|GB|KB|kg|km|USD|PHP|usd|php)(?!\w)|\$\s?\d+(?:\.\d+)?")),
    ("iso-date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("code-span", re.compile(r"`([^`]{1,120})`")),
    ("quoted", re.compile(r'"([^"]{1,120})"')),
    ("constant-identifier", re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b")),
]


@handler("C:pin")
def c_pin(ctx):
    seen, items = set(), []
    counts = Counter()
    for lineno, line in enumerate(_lines(ctx), start=1):
        ctx.tick()
        for kind, rx in _PIN_PATTERNS:
            for m in rx.finditer(line):
                value = m.group(1) if rx.groups else m.group(0)
                value = value.strip().rstrip(".,;:")
                if not value or (kind, value) in seen:
                    continue
                seen.add((kind, value))
                counts[kind] += 1
                items.append({"line": lineno, "kind": kind, "value": value})
    return {"summary": {"pinned_details": len(items), **dict(counts)}, "items": _limit(items, ctx),
            "note": "Copy these exact values into any summary or handoff; do not paraphrase them."}


@handler("C:prune")
def c_prune(ctx):
    data = ctx.read_json()
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise core.InputError("prune input must be a JSON list or an object with an 'items' list")
    per_class = ctx.opt_int("per_class", 3, 1)
    classed = defaultdict(list)
    for n, raw in enumerate(_limit(data, ctx), start=1):
        if not isinstance(raw, dict):
            raise core.InputError(f"item {n} must be an object")
        try:
            score = float(raw.get("score", 0))
        except (TypeError, ValueError) as exc:
            raise core.InputError(f"item {n} score must be numeric") from exc
        classed[str(raw.get("class", "unclassified"))].append((score, str(raw.get("id", n)), raw))
    items, representatives = [], {}
    for cls, members in sorted(classed.items()):
        members.sort(key=lambda x: (-x[0], x[1]))
        for rank, (score, ident, raw) in enumerate(members, start=1):
            decision = "kept" if rank <= per_class else "pruned: class already represented"
            items.append({"class": cls, "id": ident, "score": score, "decision": decision})
        representatives[cls] = [m[1] for m in members[:per_class]]
    kept = sum(1 for i in items if i["decision"] == "kept")
    return {"summary": {"classes": len(classed), "kept": kept, "pruned": len(items) - kept, "per_class": per_class},
            "items": items, "representatives": representatives}


# ====================================================================== X
@handler("X:combine")
def x_combine(ctx):
    data = ctx.read_json()
    elements = data.get("elements") if isinstance(data, dict) else None
    if not isinstance(elements, dict) or not elements:
        raise core.InputError("combine input needs an 'elements' object mapping each role to a non-empty list")
    names = list(elements)
    for n in names:
        if not isinstance(elements[n], list) or not elements[n]:
            raise core.InputError(f"role {n!r} must be a non-empty list")
    total = 1
    for n in names:
        total *= len(elements[n])
    cap = ctx.opt_int("limit", ctx.policy["limits"]["max_combinations"], 1, ctx.policy["limits"]["max_combinations"])
    sep = data.get("separator", " | ")
    items = []
    for n, combo in enumerate(product(*[elements[k] for k in names]), start=1):
        if n > cap:
            break
        ctx.tick()
        items.append({"id": n, "text": sep.join(str(v) for v in combo),
                      **{k: v for k, v in zip(names, combo)}})
    return {"summary": {"roles": len(names), "possible": total, "returned": len(items),
                        "truncated": total > len(items)}, "items": items}


@handler("X:vary")
def x_vary(ctx):
    data = ctx.read_json()
    base = data.get("base") if isinstance(data, dict) else None
    variants = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(base, dict) or not isinstance(variants, dict):
        raise core.InputError("vary input needs 'base' (object) and 'variants' (attribute -> list of alternatives)")
    items = []
    for attr, alternatives in variants.items():
        if not isinstance(alternatives, list):
            raise core.InputError(f"variants for {attr!r} must be a list")
        for alt in alternatives:
            ctx.tick()
            if base.get(attr) == alt:
                continue  # identical to the base; not a variant
            variant = dict(base)
            variant[attr] = alt
            items.append({"attribute": attr, "from": base.get(attr), "to": alt, "variant": core.canonical(variant)})
    cap = ctx.policy["limits"]["max_combinations"]
    if len(items) > cap:
        raise core.InputError(f"too many variants ({len(items)}); the limit is {cap}")
    return {"summary": {"attributes": len(variants), "variants": len(items)}, "items": items}


# ====================================================================== E
def _load_evidence(ctx):
    data = ctx.read_json()
    if not isinstance(data, dict):
        raise core.InputError("evidence input must be a JSON object with 'claims' and 'sources' lists")
    claims = data.get("claims", [])
    sources = data.get("sources", [])
    if not isinstance(claims, list) or not isinstance(sources, list):
        raise core.InputError("'claims' and 'sources' must be lists")
    _limit(claims, ctx)
    _limit(sources, ctx)
    source_map = {}
    for s in sources:
        if not isinstance(s, dict) or "id" not in s:
            raise core.InputError("every source needs an 'id'")
        if str(s.get("type")) not in ctx.policy["source_tiers"]:
            raise core.InputError(f"source {s['id']!r} has type {s.get('type')!r}; use one of: "
                                  + ", ".join(sorted(ctx.policy["source_tiers"])))
        if s["id"] in source_map:
            raise core.InputError(f"duplicate source id {s['id']!r}")
        source_map[s["id"]] = s
    claim_ids = set()
    for c in claims:
        if not isinstance(c, dict) or "id" not in c or "text" not in c:
            raise core.InputError("every claim needs 'id' and 'text'")
        if c["id"] in claim_ids:
            raise core.InputError(f"duplicate claim id {c['id']!r}")
        claim_ids.add(c["id"])
    return claims, sources, source_map


def _tier(source_type, policy):
    return policy["source_tiers"].get(str(source_type), 5)


_GRADES = {1: "strong", 2: "strong", 3: "moderate", 4: "weak", 5: "unsupported"}


@handler("E:map")
def e_map(ctx):
    claims, _, smap = _load_evidence(ctx)
    items = []
    for c in claims:
        refs = c.get("evidence", [])
        if not isinstance(refs, list):
            raise core.InputError(f"evidence for claim {c['id']} must be a list of source ids")
        resolved = [r for r in refs if r in smap]
        dangling = [r for r in refs if r not in smap]
        if not refs:
            status = "gap: no evidence"
        elif resolved:
            status = "supported" + (" (dangling refs)" if dangling else "")
        else:
            status = "dangling: references unknown sources"
        items.append({"claim": c["id"], "status": status, "sources": ", ".join(resolved), "dangling": ", ".join(dangling)})
    gaps = sum(1 for i in items if i["status"].startswith("gap"))
    return {"summary": {"claims": len(items), "gaps": gaps,
                        "supported": sum(1 for i in items if i["status"].startswith("supported")),
                        "dangling": sum(1 for i in items if i["status"].startswith("dangling"))},
            "items": items}


@handler("E:quality")
def e_quality(ctx):
    claims, _, smap = _load_evidence(ctx)
    items = []
    for c in claims:
        refs = [r for r in c.get("evidence", []) if r in smap]
        tiers = [(_tier(smap[r].get("type"), ctx.policy), r) for r in refs]
        best = min(tiers) if tiers else (5, "")
        grade = _GRADES[best[0]] if tiers else "unsupported"
        items.append({"claim": c["id"], "best_tier": best[0], "best_source": best[1], "grade": grade})
    counts = Counter(i["grade"] for i in items)
    return {"summary": {"claims": len(items), **dict(counts),
                        "tier_scale": "1 primary/measured, 2 official/peer-reviewed, 3 secondary, 4 news/opinion, 5 unsupported"},
            "items": items}


@handler("E:freshness")
def e_freshness(ctx):
    claims, sources, _ = _load_evidence(ctx)
    as_of = ctx.as_of()
    default_max = ctx.policy["defaults"]["source_max_age_days"]
    volatile_max = ctx.policy["defaults"]["volatile_max_age_days"]
    volatile_sources = {r for c in claims if c.get("volatile") for r in c.get("evidence", [])}
    items = []
    for s in sources:
        limit = volatile_max if s["id"] in volatile_sources else default_max
        if "max_age_days" in s:
            limit = int(s["max_age_days"])
        if not s.get("date"):
            items.append({"source": s["id"], "date": "", "age_days": "", "limit_days": limit, "status": "undated"})
            continue
        dated = core.parse_date(s["date"], f"date of source {s['id']}")
        age = (as_of - dated).days
        status = "future-dated" if age < 0 else ("stale" if age > limit else "fresh")
        items.append({"source": s["id"], "date": str(dated), "age_days": age, "limit_days": limit, "status": status})
    counts = Counter(i["status"] for i in items)
    return {"summary": {"as_of": str(as_of), **dict(counts)}, "items": items}


def _topics(claims):
    grouped = defaultdict(list)
    for c in claims:
        if c.get("topic") is not None and "value" in c:
            grouped[str(c["topic"])].append(c)
    return grouped


@handler("E:conflicts")
def e_conflicts(ctx):
    claims, _, smap = _load_evidence(ctx)
    items = []
    for topic, members in _topics(claims).items():
        values = {str(m["value"]).strip().lower() for m in members}
        if len(values) < 2:
            continue
        tiers = []
        for m in members:
            best = min([_tier(smap[r].get("type"), ctx.policy) for r in m.get("evidence", []) if r in smap] or [5])
            tiers.append((best, m["id"]))
        tiers.sort()
        if len(tiers) > 1 and tiers[0][0] < tiers[1][0]:
            resolution = f"authority suggests {tiers[0][1]}; disagreement is preserved until a person confirms"
        else:
            resolution = "unresolved: equal authority; keep both and gather evidence"
        items.append({"topic": topic, "claims": ", ".join(f"{m['id']}={m['value']}" for m in members),
                      "resolution": resolution})
    return {"summary": {"topics_checked": len(_topics(claims)), "conflicts": len(items)}, "items": items}


@handler("E:compare")
def e_compare(ctx):
    claims, _, _ = _load_evidence(ctx)
    items = []
    for topic, members in _topics(claims).items():
        values = [str(m["value"]) for m in members]
        agree = len({v.strip().lower() for v in values}) == 1
        items.append({"topic": topic, "values": "; ".join(f"{m['id']}: {m['value']}" for m in members),
                      "agreement": "agree" if agree else "disagree"})
    return {"summary": {"topics": len(items), "agree": sum(1 for i in items if i["agreement"] == "agree")},
            "items": items}


@handler("E:rank")
def e_rank(ctx):
    _, sources, _ = _load_evidence(ctx)
    by_date = sorted(sources, key=lambda s: str(s.get("date") or ""), reverse=True)
    ranked = sorted(by_date, key=lambda s: _tier(s.get("type"), ctx.policy))
    items = [{"rank": i, "source": s["id"], "type": s.get("type", ""), "tier": _tier(s.get("type"), ctx.policy),
              "date": s.get("date", "")} for i, s in enumerate(ranked, start=1)]
    return {"summary": {"sources": len(items)}, "items": items,
            "note": "Lower tier is stronger. Within a tier the newer source ranks first."}


@handler("E:cite")
def e_cite(ctx):
    _, sources, _ = _load_evidence(ctx)
    items = []
    for s in sources:
        title = s.get("title", "")
        date = s.get("date", "")
        location = s.get("url") or s.get("location") or s.get("path") or ""
        missing = [n for n, v in (("title", title), ("date", date), ("location", location)) if not v]
        ref = f"[{s['id']}] {title or '(no title)'}. {s.get('type', 'unknown type')}. {date or 'undated'}. {location or 'no location'}."
        items.append({"reference": ref, "missing": ", ".join(missing)})
    return {"summary": {"references": len(items), "incomplete": sum(1 for i in items if i["missing"])}, "items": items}


@handler("E:saturation")
def e_saturation(ctx):
    claims, sources, _ = _load_evidence(ctx)
    window = ctx.opt_int("window", 2, 1)
    threshold = ctx.policy["defaults"]["saturation_new_share"]
    by_source = defaultdict(set)
    for c in claims:
        for r in c.get("evidence", []):
            by_source[r].add(c["id"])
    total = len({c["id"] for c in claims if c.get("evidence")})
    seen, items = set(), []
    for s in sources:
        new = by_source.get(s["id"], set()) - seen
        seen |= new
        share = len(new) / float(total) if total else 0.0
        items.append({"source": s["id"], "new_claims": len(new), "cumulative": len(seen), "new_share": round(share, 3)})
    last = items[-window:]
    saturated = bool(items) and len(last) == window and all(i["new_share"] <= threshold for i in last)
    return {"summary": {"sources": len(items), "claims_with_evidence": total, "saturated": saturated,
                        "rule": f"last {window} sources each add <= {threshold:.0%} new claims"}, "items": items}


@handler("E:bias")
def e_bias(ctx):
    _, sources, _ = _load_evidence(ctx)
    dominant = ctx.policy["defaults"]["dominant_share"]
    items = []
    n = len(sources)
    for field in ("region", "funding", "method"):
        counter = Counter(str(s.get(field) or "(missing)") for s in sources)
        for value, count in counter.most_common():
            share = count / float(n) if n else 0.0
            flag = "dominant" if share >= dominant and value != "(missing)" else ("missing" if value == "(missing)" else "")
            items.append({"field": field, "value": value, "count": count, "share": round(share, 2), "flag": flag})
    flags = sum(1 for i in items if i["flag"] == "dominant")
    return {"summary": {"sources": n, "dominant_categories": flags}, "items": items}


@handler("E:ground")
def e_ground(ctx):
    claims, _, _ = _load_evidence(ctx)
    docs_opt = ctx.opt("docs")
    if docs_opt:
        root = Path(docs_opt).resolve()
    else:
        root = ctx.input_path.parent if ctx.input_path.is_file() else ctx.input_path
    docs = _doc_index(ctx, root, {ctx.input_path.resolve()})
    supported_min = ctx.policy["defaults"]["ground_supported"]
    partial_min = ctx.policy["defaults"]["ground_partial"]
    items = []
    for c in claims:
        ctx.tick()
        kw = core.content_words(str(c["text"]), ctx.policy["stopwords"])
        best_doc, best = "", 0.0
        for name, tokens in docs.items():
            ratio = len(kw & tokens) / float(len(kw)) if kw else 0.0
            if ratio > best:
                best, best_doc = ratio, name
        status = "supported" if best >= supported_min else ("partial" if best >= partial_min else "ungrounded")
        items.append({"claim": c["id"], "status": status, "match": round(best, 2), "best_file": best_doc})
    counts = Counter(i["status"] for i in items)
    return {"summary": {"claims": len(items), "files_checked": len(docs), **dict(counts),
                        "method": "keyword overlap (assisted)"}, "items": items}
