"""Text operations: Universal Creation (12) and Editing/Proofreading (12).

Deterministic, reviewable implementations. Generation-heavy capabilities of the
creation category (composer, rewriter, expansion, continuation, variants, tone,
audience) are deliberately NOT faked here: they run through task contracts in
``tools-contract-ops.py``. Everything below is a real local transformation.
"""

from __future__ import annotations

import difflib
import json
import math
import re

EXPORTS = [
    "paragraphs", "join_paragraphs", "count_syllables", "reading_metrics",
    "content_words", "word_freq", "summarize_extractive", "replace_preserving_case",
    "sentence_case", "title_case", "parse_items", "render_items", "md_table_plain",
    "split_sentences", "norm_ws", "dedupe_lines", "find_phrase_hits", "STYLE_PROFILES",
]

LEXICON_ASSET = "asset-lexicons-language.json"

STYLE_PROFILES = {
    "plain-en": {"quotes": "straight", "dash": "en", "serial_comma": True, "spell": "us"},
    "neutral-us": {"quotes": "curly", "dash": "em", "serial_comma": True, "spell": "us"},
    "neutral-gb": {"quotes": "curly", "dash": "en", "serial_comma": False, "spell": "gb"},
    "technical": {"quotes": "straight", "dash": "hyphen", "serial_comma": True, "spell": "us"},
}


# --------------------------------------------------------------------------
# shared helpers (exported)
# --------------------------------------------------------------------------


def norm_ws(text):
    return re.sub(r"[ \t]+", " ", text)


def paragraphs(text):
    blocks = re.split(r"\n\s*\n", core.canonical_newlines(text))
    return [b.strip("\n") for b in blocks if b.strip()]


def join_paragraphs(blocks):
    return "\n\n".join(b.strip("\n") for b in blocks if b.strip())


def split_sentences(text):
    return core.sentence_split(text)


def count_syllables(text):
    total = 0
    for word in re.findall(r"[A-Za-z]+", text.lower()):
        if not word:
            continue
        word = re.sub(r"(?:[^aeiouy]e|[^aeiouy]es)$", "", word)
        groups = re.findall(r"[aeiouy]+", word)
        total += max(1, len(groups))
    return total


def content_words(text, stopwords=None):
    stop = set(stopwords) if stopwords else set(DEFAULT_STOPWORDS)
    return [w for w in core.lower_words(text) if w not in stop and len(w) > 2]


def word_freq(text, stopwords=None):
    freq = {}
    for word in content_words(text, stopwords):
        freq[word] = freq.get(word, 0) + 1
    return freq


def reading_metrics(text):
    sentences = [s for s in core.sentence_split(text) if re.search(r"[A-Za-z]", s)]
    words = re.findall(r"[A-Za-z']+", text)
    n_words = max(1, len(words))
    n_sent = max(1, len(sentences))
    syllables = max(1, count_syllables(text))
    letters = sum(ch.isalpha() for ch in text)
    complex_words = sum(1 for w in words if count_syllables(w) >= 3)
    asl = n_words / n_sent
    asw = syllables / n_words
    poly = complex_words / n_words
    flesch = 206.835 - 1.015 * asl - 84.6 * asw
    fk = 0.39 * asl + 11.8 * asw - 15.59
    fog = 0.4 * (asl + 100 * poly)
    smog = 1.0430 * math.sqrt(max(0, complex_words) * (30.0 / n_sent)) + 3.1291
    ari = 4.71 * (letters / n_words) + 0.5 * asl - 21.43
    coleman = 0.0588 * (100.0 * letters / n_words) - 0.296 * (100.0 * n_sent / n_words) - 15.8
    lengths = [len(re.findall(r"[A-Za-z']+", s)) for s in sentences] or [0]
    mean = sum(lengths) / len(lengths)
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    return {
        "words": len(words), "sentences": len(sentences), "syllables": syllables,
        "letters": letters, "complex_words": complex_words,
        "avg_sentence_length": round(asl, 2),
        "avg_syllables_per_word": round(asw, 2),
        "flesch_reading_ease": round(flesch, 1),
        "flesch_kincaid_grade": round(fk, 1),
        "gunning_fog": round(fog, 1),
        "smog_index": round(smog, 1),
        "automated_readability_index": round(ari, 1),
        "coleman_liau": round(coleman, 1),
        "sentence_length_stdev": round(math.sqrt(variance), 2),
        "longest_sentence_words": max(lengths),
    }


def summarize_extractive(text, max_sentences=6, max_words=0):
    """Select the highest-value sentences and return them in document order."""
    sents = [s for s in core.sentence_split(text) if len(s.split()) > 2]
    if not sents:
        return []
    freq = word_freq(text)
    if freq:
        top = max(freq.values())
        freq = {k: v / top for k, v in freq.items()}
    scored = []
    for idx, sent in enumerate(sents):
        words = core.lower_words(sent)
        if not words:
            continue
        score = sum(freq.get(w, 0.0) for w in words) / math.sqrt(len(words))
        score += 0.35 if idx == 0 else 0.0
        score += 0.2 if idx == len(sents) - 1 else 0.0
        if re.search(r"\b(should|must|recommend|conclusion|therefore|result)\b", sent, re.I):
            score += 0.25
        if re.search(r"\d", sent):
            score += 0.1
        scored.append((score, idx, sent))
    scored.sort(key=lambda item: -item[0])
    chosen, used = [], 0
    for score, idx, sent in scored:
        n = len(sent.split())
        if max_words and used + n > max_words and chosen:
            continue
        chosen.append(idx)
        used += n
        if len(chosen) >= max_sentences:
            break
    return [sents[i] for i in sorted(chosen)]


def replace_preserving_case(pattern, replacement, text, whole_word=True):
    """Case-preserving literal replacement (Sentence/sentence/SENTENCE)."""
    flags = 0 if whole_word else 0
    if whole_word:
        regex = re.compile(r"\b%s\b" % re.escape(pattern), flags | re.IGNORECASE)
    else:
        regex = re.compile(re.escape(pattern), flags | re.IGNORECASE)

    def _sub(match):
        original = match.group(0)
        if not replacement:
            return ""
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return regex.sub(_sub, text)


def sentence_case(text):
    text = text.strip()
    if not text:
        return text
    out = text[0].upper() + text[1:]
    return re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


TITLE_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
               "nor", "of", "on", "onto", "or", "over", "per", "the", "to", "up",
               "via", "with", "yet", "so"}


def title_case(text, keep=(), style="title-ap"):
    keep_set = {k.lower() for k in keep}
    words = re.split(r"(\s+)", text.strip())
    out, first_done = [], False
    for token in words:
        if token.isspace() or not token:
            out.append(token)
            continue
        core_word = token.strip("\"'()[]:;,.!?").lower()
        if core_word in keep_set:
            out.append(token)
            continue
        if not first_done or core_word not in TITLE_SMALL or style == "title-chicago":
            out.append(token[:1].upper() + token[1:] if token[:1].isalpha() else token)
        else:
            out.append(token.lower())
        first_done = True
    result = "".join(out)
    # last word always capitalised
    result = re.sub(r"(\b[a-z][\w'-]*)(\s*)$", lambda m: m.group(1)[:1].upper() + m.group(1)[1:] + m.group(2), result)
    return result


def find_phrase_hits(text, phrases):
    hits = []
    for phrase in phrases:
        for match in re.finditer(r"\b%s\b" % re.escape(phrase), text, re.I):
            hits.append({"match": match.group(0), "span": [match.start(), match.end()],
                         "phrase": phrase})
    hits.sort(key=lambda h: h["span"][0])
    return hits


def md_table_plain(rows, headers):
    """Markdown table with padded columns (monospace friendly)."""
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], max((len(x) for x in str(cell).split("\n")), default=0))
    def fmt(cells):
        return "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)) + " |"
    out = [fmt(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out.extend(fmt(r) for r in rows)
    return "\n".join(out)


def dedupe_lines(text):
    seen, out = set(), []
    for line in text.split("\n"):
        key = line.strip()
        if key and key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out)


def parse_items(text):
    """Detect the structure of a block and return (kind, items, meta)."""
    lines = [ln.rstrip() for ln in core.canonical_newlines(text).split("\n")]
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return "empty", [], {}
    bullet_re = re.compile(r"^\s*(?:[-*\u2022\u2023\u25aa\u2026]|\(?\d+[.)])\s+(.*)$")
    if all(bullet_re.match(ln) or not ln.strip() for ln in lines):
        return "bullets", [bullet_re.match(ln).group(1).strip() for ln in non_empty], {}
    if any("|" in ln for ln in non_empty) and len(non_empty) >= 2:
        rows = []
        for ln in non_empty:
            if re.match(r"^\s*\|?[\s:|-]+\|?\s*$", ln):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows.append(cells)
        if len(rows) >= 2:
            return "table", rows[1:], {"headers": rows[0]}
    if re.search(r"^\s*(Q|Question)\s*[:\-]", text, re.I | re.M):
        pairs, q = [], None
        for ln in non_empty:
            m = re.match(r"^\s*(?:Q|Question)\s*[:\-]\s*(.*)$", ln, re.I)
            a = re.match(r"^\s*(?:A|Answer)\s*[:\-]\s*(.*)$", ln, re.I)
            if m:
                q = m.group(1).strip()
            elif a and q is not None:
                pairs.append((q, a.group(1).strip()))
                q = None
        if pairs:
            return "faq", pairs, {}
    colon_rows = []
    for ln in non_empty:
        m = re.match(r"^([^:]{1,60}):\s+(.+)$", ln)
        if m:
            colon_rows.append([m.group(1).strip(), m.group(2).strip()])
    if len(colon_rows) == len(non_empty) and len(colon_rows) >= 2:
        return "keyvalue", colon_rows, {}
    blocks = paragraphs(text)
    if len(blocks) > 1:
        return "paragraphs", blocks, {}
    sents = [s for s in core.sentence_split(text) if s.strip()]
    return "sentences", sents, {}


def render_items(kind, items, meta, params):
    to = params.get("to", "list")
    key_name = params.get("key_name") or "item"
    value_name = params.get("value_name") or "detail"
    if to == "list":
        if kind == "table":
            return "\n".join("- " + " | ".join(str(c) for c in row) for row in items)
        if kind == "keyvalue":
            return "\n".join("- %s: %s" % (k, v) for k, v in items)
        if kind == "faq":
            return "\n".join("- **%s** %s" % (q, a) for q, a in items)
        return "\n".join("- " + str(i).strip() for i in items)
    if to == "table":
        if kind == "table" and meta.get("headers"):
            return md_table_plain(items, meta["headers"])
        if kind == "keyvalue":
            return md_table_plain(items, [key_name, value_name])
        if kind == "faq":
            return md_table_plain([[q, a] for q, a in items], ["question", "answer"])
        rows = []
        for item in items:
            text = str(item)
            if ":" in text and len(text.split(":", 1)[0]) < 60:
                left, right = text.split(":", 1)
                rows.append([left.strip(), right.strip()])
            else:
                rows.append([text.strip(), ""])
        return md_table_plain(rows, [key_name, value_name])
    if to == "faq":
        pairs = []
        if kind == "faq":
            pairs = list(items)
        elif kind == "keyvalue":
            pairs = [(k, v) for k, v in items]
        else:
            pairs = [("What is item %d?" % (i + 1), str(item))
                     for i, item in enumerate(items)]
        return "\n\n".join("Q: %s\nA: %s" % (q, a) for q, a in pairs)
    if to == "checklist":
        base = [str(x).strip() for x in (items if kind != "table" else [" | ".join(r) for r in items])]
        return "\n".join("- [ ] " + b for b in base)
    if to == "flashcards":
        cards = []
        for item in items:
            text = str(item)
            if kind == "faq":
                cards.append(item)
            elif ":" in text:
                left, right = text.split(":", 1)
                cards.append((left.strip(), right.strip()))
            else:
                cards.append((text.strip(), ""))
        if kind == "keyvalue":
            cards = list(items)
        return "\n\n".join("Front: %s\nBack: %s" % (q, a) for q, a in cards)
    if to == "json-records":
        records = []
        if kind == "table" and meta.get("headers"):
            for row in items:
                records.append({meta["headers"][i] if i < len(meta["headers"]) else "col%d" % i: row[i]
                                for i in range(len(row))})
        elif kind == "keyvalue":
            records = [{key_name: k, value_name: v} for k, v in items]
        elif kind == "faq":
            records = [{"question": q, "answer": a} for q, a in items]
        else:
            records = [{key_name: str(x).strip()} for x in items]
        return json.dumps(records, indent=2, ensure_ascii=False)
    if to == "script":
        lines = []
        for item in items:
            text = str(item)
            m = re.match(r"^([A-Z][A-Za-z .'\-]{0,30}):\s*(.+)$", text)
            if m:
                lines.append("%s\n%s" % (m.group(1).upper(), m.group(2)))
            else:
                lines.append("NARRATOR\n%s" % text)
        return "\n\n".join(lines)
    # prose
    if kind == "paragraphs":
        return join_paragraphs(items)
    if kind == "faq":
        return " ".join("%s %s" % (q, a) for q, a in items)
    if kind == "keyvalue":
        return " ".join("%s: %s." % (k, v.rstrip(".")) for k, v in items)
    return " ".join(str(x).strip().rstrip(".") + "." for x in items)


def _lexicon(ctx):
    return ctx.assets.json(LEXICON_ASSET)


DEFAULT_STOPWORDS = ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that",
                     "this", "these", "those", "of", "in", "on", "at", "to", "for", "with",
                     "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
                     "it", "its", "we", "you", "they", "he", "she", "i", "not", "no", "do",
                     "does", "did", "have", "has", "had", "will", "would", "can", "could",
                     "should", "may", "might", "must", "so", "such", "which", "who", "when",
                     "where", "why", "how", "all", "any", "some", "there", "here", "about"]


# --------------------------------------------------------------------------
# creation handlers
# --------------------------------------------------------------------------


def compression(ctx):
    text = ctx.require_text(minimum=40)
    mode = ctx.params["mode"]
    max_words = int(ctx.params["max_words"])
    max_chars = int(ctx.params["max_chars"])
    n_sent = int(ctx.params["sentences"])
    if mode == "one-liner":
        n_sent = 1
    selected = summarize_extractive(text, max_sentences=n_sent, max_words=max_words)
    if not selected:
        raise core.InvalidInput("input has no usable sentences to compress")
    if mode == "one-liner":
        output = selected[0].rstrip(" .") + "."
        if max_chars and len(output) > max_chars:
            output = core.truncate(output, max_chars)
    elif mode == "key-points":
        lines = []
        for sent in selected:
            short = " ".join(sent.split()[:24]).rstrip(".,;:")
            lines.append("- " + short)
        output = "\n".join(lines)
    elif mode == "precis":
        output = " ".join(selected)
        budget = max(20, int(len(text.split()) * 0.25))
        if len(output.split()) > budget:
            output = " ".join(output.split()[:budget]).rstrip(",;:") + "."
    else:  # executive, abstract
        body = " ".join(selected)
        if mode == "executive":
            heads = [ln for ln in core.canonical_newlines(text).split("\n")
                     if ctx.params["keep_headings"] and re.match(r"^#{1,6}\s", ln)]
            output = body if not heads else "\n".join(heads[:3]) + "\n\n" + body
        else:
            output = body
    if max_words and len(output.split()) > max_words:
        output = " ".join(output.split()[:max_words]).rstrip(",;:") + "."
    if max_chars and len(output) > max_chars:
        output = core.truncate(output, max_chars)
    data = {
        "mode": mode,
        "source_words": len(text.split()),
        "output_words": len(output.split()),
        "compression_ratio": round(len(output.split()) / max(1, len(text.split())), 3),
        "sentences_selected": len(selected),
    }
    return {"output": output, "data": data}


def format_converter(ctx):
    text = ctx.require_text(minimum=3)
    kind, items, meta = parse_items(text)
    if kind == "empty":
        raise core.InvalidInput("input is empty or only whitespace")
    output = render_items(kind, items, meta, ctx.params)
    if not output.strip():
        raise core.InvalidInput("could not extract any items to convert",
                               hint="Provide a list, table, FAQ block or paragraphs.")
    data = {"detected_input": kind, "target": ctx.params["to"], "items": len(items)}
    return {"output": output, "data": data}


def constraint_solver(ctx):
    text = ctx.require_text(minimum=1)
    params = ctx.params
    out = text
    report, violations = [], []

    if not params["check_only"]:
        if params["case"] != "keep":
            if params["case"] == "lower":
                out = out.lower()
            elif params["case"] == "upper":
                out = out.upper()
            elif params["case"] == "title":
                out = title_case(out)
            elif params["case"] == "sentence":
                out = sentence_case(out)
        if params["max_words"]:
            words = out.split()
            if len(words) > params["max_words"]:
                out = " ".join(words[: params["max_words"]])
        if params["max_chars"] and len(out) > params["max_chars"]:
            cut = out[: params["max_chars"]]
            if " " in cut.strip():
                cut = cut[: cut.rfind(" ")]
            out = cut.rstrip()

    chars, words = len(out), len(out.split())
    syllables = count_syllables(out)
    checks = {
        "chars": chars, "words": words, "syllables": syllables,
    }
    if params["exact_chars"] and chars != params["exact_chars"]:
        violations.append({"constraint": "exact_chars", "expected": params["exact_chars"], "actual": chars})
    if params["max_chars"] and chars > params["max_chars"]:
        violations.append({"constraint": "max_chars", "limit": params["max_chars"], "actual": chars})
    if params["min_words"] and words < params["min_words"]:
        violations.append({"constraint": "min_words", "minimum": params["min_words"], "actual": words})
    if params["max_words"] and words > params["max_words"]:
        violations.append({"constraint": "max_words", "limit": params["max_words"], "actual": words})
    if params["max_syllables"] and syllables > params["max_syllables"]:
        violations.append({"constraint": "max_syllables", "limit": params["max_syllables"], "actual": syllables})
    forbidden = params["forbidden_letters"].strip()
    if forbidden:
        found = sorted({ch for ch in out.lower() if ch in forbidden.lower()})
        if found:
            violations.append({"constraint": "forbidden_letters", "found": "".join(found)})
    acrostic = params["required_acrostic"].strip()
    if acrostic:
        initials = "".join(ln.strip()[:1].upper() for ln in out.split("\n") if ln.strip())
        if initials != acrostic.upper().replace(" ", ""):
            violations.append({"constraint": "acrostic", "expected": acrostic.upper(),
                               "actual": initials})
    data = {"measurements": checks, "violations": violations,
            "applied": [] if params["check_only"] else ["case", "length"]}
    warnings = ["%d constraint(s) could not be satisfied automatically" % len(violations)] if violations else []
    output = out
    if params["check_only"]:
        output = json.dumps({"measurements": checks, "violations": violations}, indent=2)
    return {"output": output, "data": data, "warnings": warnings}


def plain_language(ctx):
    text = ctx.require_text(minimum=20)
    lex = _lexicon(ctx)
    strength = ctx.params["strength"]
    keep = {t.lower() for t in ctx.params["keep_terms"]}
    max_words = int(ctx.params["max_sentence_words"])
    out = text
    changes = []

    phrase_maps = [("fluff", lex["fluff_phrases"])]
    if strength in ("standard", "aggressive"):
        phrase_maps.append(("jargon", lex["jargon"]))
    if strength == "aggressive":
        phrase_maps.append(("nominalization", lex["nominalizations"]))
    for label, mapping in phrase_maps:
        for phrase in sorted(mapping, key=len, reverse=True):
            if keep and phrase.lower() in keep:
                continue
            replacement = mapping[phrase]
            regex = re.compile(r"\b%s\b" % re.escape(phrase), re.I)
            hits = len(regex.findall(out))
            if not hits:
                continue
            out = replace_preserving_case(phrase, replacement, out)
            changes.append({"kind": label, "from": phrase, "to": replacement or "(removed)",
                            "count": hits})

    # split long sentences at safe clause boundaries
    if strength in ("standard", "aggressive"):
        rebuilt = []
        splits = 0
        for sent in core.sentence_split(out):
            if len(sent.split()) <= max_words:
                rebuilt.append(sent)
                continue
            parts = re.split(r"(?<=,)\s+(?=(?:and|but|which|while|whereas|so)\b)", sent)
            buffer = ""
            for part in parts:
                if len((buffer + " " + part).split()) <= max_words:
                    buffer = (buffer + " " + part).strip()
                else:
                    if buffer:
                        rebuilt.append(buffer)
                    buffer = part.strip()
                    splits += 1
            if buffer:
                rebuilt.append(buffer)
        if splits:
            out = " ".join(rebuilt)
            changes.append({"kind": "sentence-split", "from": "%d long sentences" % splits,
                            "to": "shorter sentences", "count": splits})

    passive = re.findall(r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w+(?:ed|en|wn|ne|t))\b", out)
    long_sentences = [s for s in core.sentence_split(out) if len(s.split()) > max_words]
    data = {
        "strength": strength,
        "changes": changes,
        "change_count": sum(c["count"] for c in changes),
        "passive_voice_candidates": len(passive),
        "remaining_long_sentences": len(long_sentences),
        "reading_before": reading_metrics(text)["flesch_reading_ease"],
        "reading_after": reading_metrics(out)["flesch_reading_ease"],
    }
    if not ctx.params["list_changes"]:
        data.pop("changes", None)
    warnings = []
    if long_sentences:
        warnings.append("%d sentence(s) still exceed %d words after splitting"
                        % (len(long_sentences), max_words))
    if passive:
        warnings.append("%d passive-voice candidate(s) need human review" % len(passive))
    return {"output": out, "data": data, "warnings": warnings}


def quality_finisher(ctx):
    text = ctx.require_text(minimum=2)
    lex = _lexicon(ctx)
    mode = ctx.params["mode"]
    profile = STYLE_PROFILES.get(ctx.params["profile"], STYLE_PROFILES["neutral-us"])
    max_words = int(ctx.params["max_sentence_words"])
    out = text
    fixes = []

    def apply(name, pattern, repl, flags=0):
        nonlocal out
        new = re.sub(pattern, repl, out, flags=flags)
        if new != out:
            fixes.append({"fix": name, "count": len(re.findall(pattern, out, flags=flags))})
            out = new

    if mode in ("mechanical", "full"):
        apply("collapse-double-space", r"[ \t]{2,}", " ")
        apply("space-before-punctuation", r"\s+([,.;:!?])", r"\1")
        apply("space-after-comma", r",(?=[A-Za-z])", ", ")
        apply("collapse-blank-lines", r"\n{3,}", "\n\n")
        apply("trailing-whitespace", r"[ \t]+\n", "\n")
        apply("repeated-word", r"\b(\w+)(\s+\1\b)+", r"\1", flags=re.I)
        apply("multiple-punctuation", r"!{2,}", "!")
        apply("space-after-period", r"(?<=[a-z])\.(?=[A-Z][a-z])", ". ")
        for typo, correct in lex["typos"].items():
            if re.search(r"\b%s\b" % re.escape(typo), out, re.I):
                new = replace_preserving_case(typo, correct, out)
                if new != out:
                    fixes.append({"fix": "typo:%s" % typo, "count": 1})
                    out = new
        out = out.strip() + "\n" if out.strip() else out

    metrics = reading_metrics(out)
    long_sentences = [s for s in core.sentence_split(out) if len(s.split()) > max_words]
    issues = []
    if long_sentences:
        issues.append({"kind": "long-sentences", "count": len(long_sentences),
                       "sample": long_sentences[0][:120]})
    if out.count("(") != out.count(")"):
        issues.append({"kind": "unbalanced-parentheses", "count": abs(out.count("(") - out.count(")"))})
    if out.count('"') % 2:
        issues.append({"kind": "unbalanced-quotes", "count": 1})
    if re.search(r"  +", out):
        issues.append({"kind": "double-space", "count": len(re.findall(r"  +", out))})
    if not re.search(r"[.:!?]\s*$", out.strip()):
        issues.append({"kind": "missing-terminal-punctuation", "count": 1})
    data = {"mode": mode, "fixes": fixes, "issues": issues, "metrics": metrics,
            "profile": ctx.params["profile"]}
    output = json.dumps({"fixes": fixes, "issues": issues, "metrics": metrics}, indent=2) if mode == "lint" else out
    warnings = []
    if issues:
        warnings.append("%d issue(s) remain for human review" % len(issues))
    return {"output": output, "data": data, "warnings": warnings}


# --------------------------------------------------------------------------
# editing handlers
# --------------------------------------------------------------------------


def grammar_mechanics(ctx):
    text = ctx.require_text(minimum=2)
    lex = _lexicon(ctx)
    dialect = ctx.params["dialect"]
    out = text
    findings = []

    def record(rule, count, example=""):
        if count:
            findings.append({"rule": rule, "count": count, "example": example})

    rules = [
        ("double-space", r"[ \t]{2,}", " ", True),
        ("space-before-punctuation", r"\s+([,.;:!?])", r"\1", True),
        ("space-after-comma", r",(?=[A-Za-z])", ", ", True),
        ("repeated-word", r"\b(\w+)(\s+\1\b)+", r"\1", True),
        ("lowercase-pronoun-i", r"\bi\b", "I", True),
        ("multiple-exclamation", r"!{2,}", "!", True),
        ("missing-space-after-sentence", r"(?<=[a-z])\.(?=[A-Z][a-z])", ". ", True),
    ]
    for name, pattern, repl, _ in rules:
        matches = re.findall(pattern, out, flags=re.I if name in ("repeated-word", "lowercase-pronoun-i") else 0)
        count = len(matches)
        if count:
            record(name, count, str(matches[0])[:40] if matches else "")
            if ctx.params["apply"]:
                out = re.sub(pattern, repl, out, flags=re.I if name in ("repeated-word", "lowercase-pronoun-i") else 0)

    spell_map = {}
    if dialect == "en-GB":
        spell_map = dict(lex["us_gb"])
    else:
        spell_map = {gb: us for us, gb in lex["us_gb"].items()}
    spell_hits = []
    if dialect in ("en-US", "en-GB"):
        for wrong, right in list(spell_map.items()):
            if re.search(r"\b%s\b" % re.escape(wrong), out, re.I):
                spell_hits.append((wrong, right))
        if spell_hits:
            record("dialect-spelling", len(spell_hits),
                   ", ".join("%s->%s" % pair for pair in spell_hits[:4]))
            if ctx.params["apply"]:
                for wrong, right in spell_hits:
                    out = replace_preserving_case(wrong, right, out)

    typo_hits = [(t, c) for t, c in lex["typos"].items()
                 if re.search(r"\b%s\b" % re.escape(t), out, re.I)]
    if typo_hits:
        record("common-typo", len(typo_hits), ", ".join(t for t, _ in typo_hits[:5]))
        if ctx.params["apply"]:
            for typo, correct in typo_hits:
                out = replace_preserving_case(typo, correct, out)

    if out.count("(") != out.count(")"):
        record("unbalanced-parentheses", abs(out.count("(") - out.count(")")), "report only")
    if out.count('"') % 2:
        record("odd-quote-count", 1, "report only")

    output = out if ctx.params["apply"] else text
    if not ctx.params["report"] and ctx.params["apply"]:
        data = {"findings": findings}
    else:
        data = {"findings": findings, "dialect": dialect,
                "applied": bool(ctx.params["apply"])}
    return {"output": output, "data": data,
            "warnings": [] if findings else ["no mechanical issues found"]}


def developmental_editor(ctx):
    text = ctx.require_text(minimum=120)
    blocks = paragraphs(text)
    sents = core.sentence_split(text)
    metrics = reading_metrics(text)
    para_words = [len(b.split()) for b in blocks]
    headings = [ln for ln in core.canonical_newlines(text).split("\n") if re.match(r"^#{1,6}\s", ln)]
    hook = sents[0] if sents else ""
    thesis_candidates = sorted(
        [s for s in sents if re.search(r"\b(argue|this (report|essay|article)|thesis|we show|i argue|purpose)\b", s, re.I)],
        key=len, reverse=True)[:3]
    transitions = [s[:80] for s in sents if re.match(r"^(However|Moreover|Therefore|Thus|In addition|Nevertheless|Consequently|Finally|First|Second)\b", s.strip())]
    findings = []
    if para_words and max(para_words) > 180:
        findings.append({"issue": "overlong-paragraph", "detail": "longest paragraph is %d words" % max(para_words)})
    if len(para_words) > 3 and metrics["sentence_length_stdev"] > 12:
        findings.append({"issue": "uneven-pacing",
                         "detail": "sentence length varies by %.1f words (stdev)" % metrics["sentence_length_stdev"]})
    if len(sents) > 4 and len(thesis_candidates) == 0:
        findings.append({"issue": "no-explicit-thesis", "detail": "no sentence states the main claim"})
    if len(headings) == 0 and len(blocks) > 4:
        findings.append({"issue": "no-headings", "detail": "%d paragraphs without section headings" % len(blocks)})
    if len(transitions) < max(1, len(sents) // 8):
        findings.append({"issue": "few-transitions", "detail": "%d explicit transition(s)" % len(transitions)})
    data = {
        "paragraphs": len(blocks), "sentences": len(sents), "headings": len(headings),
        "paragraph_word_counts": para_words[:40],
        "opening": hook[:160], "thesis_candidates": thesis_candidates,
        "transitions": transitions, "metrics": metrics, "findings": findings,
    }
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Developmental review", ""]
    lines.append("- paragraphs: %d | sentences: %d | headings: %d" % (len(blocks), len(sents), len(headings)))
    lines.append("- reading ease %.1f | FK grade %.1f | avg sentence %.1f words"
                 % (metrics["flesch_reading_ease"], metrics["flesch_kincaid_grade"], metrics["avg_sentence_length"]))
    lines.append("- opening sentence: %s" % (hook[:200] or "(none)"))
    lines.append("")
    lines.append("## Findings")
    if findings:
        lines.extend("- **%s**: %s" % (f["issue"], f["detail"]) for f in findings)
    else:
        lines.append("- no structural problems detected by the rule set")
    if thesis_candidates:
        lines.append("")
        lines.append("## Thesis candidates")
        lines.extend("- %s" % t for t in thesis_candidates)
    lines.append("")
    lines.append("## Recommended order of work")
    lines.append("1. Fix the highest-severity finding above before line editing.")
    lines.append("2. Confirm every section advances the stated claim.")
    lines.append("3. Only then run line-and-copy-editor for surface polish.")
    return {"output": "\n".join(lines), "data": data}


def line_copy_editor(ctx):
    text = ctx.require_text(minimum=40)
    lex = _lexicon(ctx)
    intensity = int(ctx.params["intensity"])
    flags = []
    for sent in core.sentence_split(text):
        words = sent.split()
        if len(words) > 30:
            flags.append({"kind": "long-sentence", "detail": "%d words" % len(words), "span": sent[:120]})
        adverbs = [w for w in words if w.lower().endswith("ly") and len(w) > 5]
        if adverbs:
            flags.append({"kind": "adverb", "detail": ", ".join(adverbs[:4]), "span": sent[:120]})
        hedges = [h for h in lex["hedges"] if re.search(r"\b%s\b" % re.escape(h), sent, re.I)]
        if hedges:
            flags.append({"kind": "hedge", "detail": ", ".join(hedges[:4]), "span": sent[:120]})
        if re.search(r"\b(?:is|are|was|were|be|been|being)\s+\w+(?:ed|en)\b", sent, re.I):
            flags.append({"kind": "passive", "detail": "passive construction", "span": sent[:120]})
        if re.search(r"\bthere (?:is|are|was|were)\b", sent, re.I):
            flags.append({"kind": "expletive", "detail": "there is/are opening", "span": sent[:120]})
        cliches = [c for c in lex["cliches"] if c in sent.lower()]
        if cliches:
            flags.append({"kind": "cliche", "detail": ", ".join(cliches), "span": sent[:120]})
    out = text
    if ctx.params["apply"] and intensity >= 3:
        for hedge in sorted(lex["hedges"], key=len, reverse=True):
            out = re.sub(r"\b%s\s+" % re.escape(hedge), "", out, flags=re.I)
        out = re.sub(r"[ \t]{2,}", " ", out)
    data = {"flags": flags[:120], "flag_count": len(flags), "intensity": intensity,
            "applied": bool(ctx.params["apply"] and intensity >= 3)}
    lines = ["# Line and copy edit report", ""]
    if flags:
        counts = {}
        for flag in flags:
            counts[flag["kind"]] = counts.get(flag["kind"], 0) + 1
        lines.append(md_table_plain([[k, v] for k, v in sorted(counts.items())], ["flag", "count"]))
        lines.append("")
        for flag in flags[:40]:
            lines.append("- **%s** (%s): %s" % (flag["kind"], flag["detail"], flag["span"]))
    else:
        lines.append("No line-level flags from the current rule set.")
    return {"output": out if ctx.params["apply"] else "\n".join(lines), "data": data}


def structural_reorg(ctx):
    text = ctx.require_text(minimum=60)
    mode = ctx.params["mode"]
    target = int(ctx.params["target_paragraph_words"])
    blocks = paragraphs(text)
    if not blocks:
        raise core.InvalidInput("no paragraphs detected")
    if mode == "lead-first":
        freq = word_freq(text)
        scored = []
        for idx, block in enumerate(blocks):
            words = core.lower_words(block)
            score = sum(freq.get(w, 0) for w in words) / math.sqrt(max(1, len(words)))
            scored.append((score, idx, block))
        best = max(scored, key=lambda item: item[0])[1]
        if best != 0:
            blocks = [blocks[best]] + blocks[:best] + blocks[best + 1:]
        note = "moved paragraph %d to the lead position" % (best + 1)
    elif mode == "by-heading":
        sections, current = [], []
        for block in blocks:
            if re.match(r"^#{1,6}\s", block):
                if current:
                    sections.append(current)
                current = [block]
            else:
                current.append(block)
        if current:
            sections.append(current)
        sections.sort(key=lambda sec: (re.sub(r"[^0-9]", "", sec[0][:20]) or "99"))
        blocks = [block for sec in sections for block in sec]
        note = "grouped %d section(s) by heading" % len(sections)
    elif mode == "merge-paragraphs":
        merged, buffer = [], ""
        for block in blocks:
            if re.match(r"^#{1,6}\s", block):
                if buffer:
                    merged.append(buffer)
                    buffer = ""
                merged.append(block)
                continue
            if len(block.split()) < 40 and buffer:
                buffer = buffer.rstrip() + " " + block.strip()
            else:
                if buffer:
                    merged.append(buffer)
                buffer = block
        if buffer:
            merged.append(buffer)
        blocks = merged
        note = "merged short paragraphs (target %d words)" % target
    else:  # split-long
        out = []
        for block in blocks:
            if len(block.split()) <= target:
                out.append(block)
                continue
            chunk = []
            for sent in core.sentence_split(block):
                chunk.append(sent)
                if len(" ".join(chunk).split()) >= target:
                    out.append(" ".join(chunk))
                    chunk = []
            if chunk:
                out.append(" ".join(chunk))
        blocks = out
        note = "split paragraphs longer than %d words" % target
    return {"output": join_paragraphs(blocks), "data": {"mode": mode, "note": note,
                                                        "paragraphs": len(blocks)}}


def consistency_auditor(ctx):
    text = ctx.require_text(minimum=20)
    term = ctx.params["term"].strip()
    ignore_case = ctx.params["ignore_case"]
    forms = {}
    for match in re.finditer(r"[A-Za-z][A-Za-z'\- ]{1,40}", text):
        raw = match.group(0).strip()
        words = raw.split()
        if not words:
            continue
        for i in range(len(words)):
            for j in range(i + 1, min(len(words), i + 4) + 1):
                phrase = " ".join(words[i:j])
                key = phrase.lower() if ignore_case else phrase
                if term and term.lower() not in key:
                    continue
                forms.setdefault(key, {})
                surface = forms[key]
                surface[phrase] = surface.get(phrase, 0) + 1
    rows = []
    for key, variants in forms.items():
        if len(variants) < 2:
            continue
        canonical = max(variants.items(), key=lambda kv: kv[1])[0]
        rows.append([key, canonical, ", ".join("%s(%d)" % (v, c) for v, c in sorted(variants.items()))])
    rows.sort(key=lambda r: -sum(len(x) for x in [r[2]]))
    rows = rows[:60]
    us_gb = _lexicon(ctx)["us_gb"]
    mixed = []
    lower_text = text.lower()
    for us, gb in us_gb.items():
        if re.search(r"\b%s\b" % us, lower_text) and re.search(r"\b%s\b" % gb, lower_text):
            mixed.append([us, gb])
    data = {"variants": [{"term": r[0], "canonical": r[1], "surface_forms": r[2]} for r in rows],
            "mixed_spelling": mixed}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Consistency and terminology audit", ""]
    if rows:
        lines.append(md_table_plain([[r[0], r[1], r[2]] for r in rows],
                                    ["term", "suggested", "surface forms (count)"]))
    else:
        lines.append("No inconsistent term variants detected.")
    if mixed:
        lines.append("")
        lines.append("Mixed US/GB spellings for the same word: %s"
                     % ", ".join("%s/%s" % (a, b) for a, b in mixed[:10]))
    return {"output": "\n".join(lines), "data": data}


def ambiguity_detector(ctx):
    text = ctx.require_text(minimum=30)
    scope = ctx.params["scope"]
    find = []
    nouns = r"(?:[A-Z][a-z]+|\b(?:team|manager|system|report|client|server|policy|editor|author)\b)"
    for sent in core.sentence_split(text):
        if scope in ("all", "pronouns"):
            if re.search(r"\b(it|they|this|that|these|those)\b", sent, re.I) and \
                    len(re.findall(nouns, sent)) >= 2 and len(sent.split()) > 8:
                find.append({"kind": "unclear-pronoun", "span": sent[:140],
                             "note": "pronoun may refer to more than one noun"})
        if scope in ("all", "quantifiers"):
            for word in _lexicon(ctx)["vague_quantifiers"]:
                if re.search(r"\b%s\b" % re.escape(word), sent, re.I):
                    find.append({"kind": "vague-quantifier", "span": sent[:140],
                                 "note": "'%s' has no defined threshold" % word})
                    break
        if scope in ("all", "negation"):
            if re.search(r"\b(not|no)\b.{0,40}\b(not|never|none)\b", sent, re.I):
                find.append({"kind": "double-negative", "span": sent[:140],
                             "note": "double negation is hard to parse"})
        if re.search(r"\b(?:and/or|only|either)\b", sent, re.I) and len(sent.split()) > 10:
            find.append({"kind": "scope-ambiguity", "span": sent[:140],
                         "note": "modifier scope is ambiguous"})
    data = {"findings": find, "count": len(find), "scope": scope}
    rows = [[f["kind"], f["note"], f["span"]] for f in find[:40]]
    output = json.dumps(data, indent=2) if ctx.params["format"] == "json" else (
        md_table_plain(rows, ["kind", "note", "span"]) if rows else "No ambiguity flags found.")
    return {"output": output, "data": data}


def redundancy_trimmer(ctx):
    text = ctx.require_text(minimum=20)
    lex = _lexicon(ctx)
    strength = int(ctx.params["strength"])
    out = text
    removed = []
    for phrase in sorted(lex["fluff_phrases"], key=len, reverse=True):
        regex = re.compile(r"\b%s\b" % re.escape(phrase), re.I)
        count = len(regex.findall(out))
        if not count:
            continue
        replacement = lex["fluff_phrases"][phrase]
        if strength == 1 and not replacement:
            continue
        out = replace_preserving_case(phrase, replacement, out)
        removed.append({"phrase": phrase, "replacement": replacement or "(deleted)", "count": count})
    if ctx.params["remove_repeated_words"]:
        new = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", out, flags=re.I)
        if new != out:
            removed.append({"phrase": "immediate word repetition", "replacement": "single word", "count": 1})
            out = new
    if strength >= 2:
        hedges = [h for h in lex["hedges"] if h not in ("just", "clearly")]
        hits = 0
        for hedge in hedges:
            pattern = re.compile(r"\b%s\s+" % re.escape(hedge), re.I)
            hits += len(pattern.findall(out))
            out = pattern.sub("", out)
        if hits:
            removed.append({"phrase": "hedges", "replacement": "(deleted)", "count": hits})
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    data = {"removed": removed, "chars_before": len(text), "chars_after": len(out),
            "saved_pct": round(100 * (len(text) - len(out)) / max(1, len(text)), 1)}
    warnings = [] if removed else ["no redundancy from the configured list was found"]
    return {"output": out, "data": data, "warnings": warnings}


def fact_claim_marker(ctx):
    text = ctx.require_text(minimum=30)
    lex = _lexicon(ctx)
    kinds = [k.lower() for k in ctx.params["kinds"]]
    claims = []
    for sent in core.sentence_split(text):
        detected = []
        if "numbers" in kinds and re.search(r"\b\d[\d,.]*\b", sent):
            detected.append("number")
        if "dates" in kinds and re.search(r"\b(19|20)\d{2}\b|\b(January|February|March|April|May|June|July|August|September|October|November|December)\b", sent):
            detected.append("date")
        if "superlatives" in kinds:
            for cue in lex["claim_cues"]["superlatives"]:
                if re.search(r"\b%s\b" % re.escape(cue), sent, re.I):
                    detected.append("superlative:%s" % cue)
                    break
        if "attribution" in kinds:
            for cue in lex["claim_cues"]["attribution"]:
                if cue in sent.lower():
                    detected.append("attribution")
                    break
        if "predictions" in kinds:
            for cue in lex["claim_cues"]["prediction"]:
                if re.search(r"\b%s\b" % re.escape(cue), sent, re.I):
                    detected.append("prediction")
                    break
        if "universals" in kinds:
            for cue in lex["claim_cues"]["universal"]:
                if re.search(r"\b%s\b" % re.escape(cue), sent, re.I):
                    detected.append("universal")
                    break
        if detected:
            claims.append({"sentence": sent.strip(), "kinds": detected})
    data = {"claims": claims, "count": len(claims)}
    if ctx.params["style"] == "json":
        output = json.dumps(data, indent=2, ensure_ascii=False)
    elif ctx.params["style"] == "inline":
        marked = text
        for claim in claims:
            marked = marked.replace(claim["sentence"], claim["sentence"].rstrip(".") + " [CHECK: %s]." % ", ".join(claim["kinds"]), 1)
        output = marked
    else:
        rows = [[c["sentence"][:120], ", ".join(c["kinds"])] for c in claims]
        output = md_table_plain(rows, ["sentence", "needs verification"]) if rows else "No verifiable claims detected."
    return {"output": output, "data": data}


def sensitive_tone_reviewer(ctx):
    text = ctx.require_text(minimum=10)
    lex = _lexicon(ctx)
    domain = ctx.params["domain"]
    flags = []
    for term, alternative in lex["charged_words"].items():
        for match in re.finditer(r"\b%s\b" % re.escape(term), text, re.I):
            flags.append({"kind": "charged-term", "term": match.group(0),
                          "alternative": alternative, "offset": match.start(), "domain": domain})
    for phrase in lex["blaming_phrases"]:
        for match in re.finditer(re.escape(phrase), text, re.I):
            flags.append({"kind": "blaming", "term": match.group(0),
                          "alternative": "neutral description of the behaviour",
                          "offset": match.start(), "domain": domain})
    if domain in ("health", "trauma"):
        for topic, words in lex["sensitive_topics"].items():
            for word in words:
                if re.search(r"\b%s\b" % re.escape(word), text, re.I):
                    flags.append({"kind": "sensitive-topic", "term": word,
                                  "alternative": "consider a content note and plain framing",
                                  "offset": 0, "domain": domain})
                    break
    data = {"flags": flags, "count": len(flags), "domain": domain}
    rows = [[f["kind"], f["term"], f["alternative"]] for f in flags[:40]]
    output = json.dumps(data, indent=2) if ctx.params["format"] == "json" else (
        md_table_plain(rows, ["kind", "term", "suggested"]) if rows else "No sensitive-tone flags found.")
    return {"output": output, "data": data}


def change_explanation(ctx):
    original = ctx.require_text(minimum=5)
    revised = ctx.require_second_text()
    a = core.canonical_newlines(original).split("\n")
    b = core.canonical_newlines(revised).split("\n")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    changes = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        before = "\n".join(a[i1:i2]).strip()
        after = "\n".join(b[j1:j2]).strip()
        reasons = {"replace": "revised wording", "delete": "removed redundant material",
                   "insert": "added missing information"}
        changes.append({"op": tag, "line_range": [i1 + 1, i2], "before": before[:400],
                        "after": after[:400], "reason": reasons.get(tag, "edit")})
    added = sum(1 for c in changes if c["op"] == "insert")
    deleted = sum(1 for c in changes if c["op"] == "delete")
    modified = sum(1 for c in changes if c["op"] == "replace")
    data = {"changes": changes, "added": added, "deleted": deleted, "modified": modified,
            "similarity": round(sm.ratio(), 3)}
    if ctx.params["mode"] == "summary":
        output = ("%d change(s): %d added, %d removed, %d modified; similarity %.2f"
                  % (len(changes), added, deleted, modified, data["similarity"]))
    elif ctx.params["mode"] == "table":
        rows = [[c["op"], c["line_range"][0], (c["before"] or "-")[:60], (c["after"] or "-")[:60], c["reason"]]
                for c in changes]
        output = md_table_plain(rows, ["op", "line", "before", "after", "reason"])
    else:
        lines = ["# Edit explanation", ""]
        for idx, change in enumerate(changes, 1):
            lines.append("%d. **%s** (line %s)" % (idx, change["op"], change["line_range"][0]))
            if change["before"]:
                lines.append("   - before: %s" % change["before"].replace("\n", " / ")[:200])
            if change["after"]:
                lines.append("   - after: %s" % change["after"].replace("\n", " / ")[:200])
            lines.append("   - reason: %s" % change["reason"])
        output = "\n".join(lines) if changes else "No differences found."
    return {"output": output, "data": data}


def style_sheet_builder(ctx):
    text = ctx.require_text(minimum=80)
    name = ctx.params["name"]
    curly = text.count("\u201c") + text.count("\u2019")
    straight = text.count('"') + text.count("'")
    em, en = text.count("\u2014"), text.count("\u2013")
    serial = len(re.findall(r",\s+[a-z]+,?\s+and\s", text))
    nonserial = len(re.findall(r",\s+[a-z]+\s+and\s", text))
    digits = len(re.findall(r"\b\d+\b", text))
    spelled = len(re.findall(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", text, re.I))
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\w+ \d{1,2}, \d{4}\b", text)
    heads = re.findall(r"^(#{1,6})\s+(.*)$", text, re.M)
    iso = len(re.findall(r"\b\w+ise\b|\b\w+isation\b", text, re.I))
    ize = len(re.findall(r"\b\w+ize\b|\b\w+ization\b", text, re.I))
    sheet = {
        "name": name,
        "quotes": {"curly": curly, "straight": straight,
                   "decision": "curly" if curly >= straight else "straight"},
        "dashes": {"em": em, "en": en, "hyphen_minus": text.count("-"),
                   "decision": "em" if em >= en else ("en" if en else "hyphen")},
        "serial_comma": {"with_serial_comma": serial, "without": nonserial,
                         "decision": serial >= nonserial},
        "numerals": {"digits": digits, "spelled": spelled,
                     "decision": "numerals" if digits >= spelled else "spelled out"},
        "dates": {"pattern": dates[0] if dates else None, "count": len(dates)},
        "headings": {"count": len(heads),
                     "style": ("title-case" if heads and sum(1 for _, h in heads if h[:1].isupper() and sum(w[:1].isupper() for w in h.split()) > 1) > len(heads) / 2 else "sentence-case") if heads else "none"},
        "spelling": {"ise": iso, "ize": ize, "decision": "GB (-ise)" if iso > ize else "US (-ize)"},
        "contractions": len(re.findall(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", text)),
        "terms": sorted({m.group(0) for m in re.finditer(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\b", text)})[:20],
    }
    data = {"style_sheet": sheet}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(sheet, indent=2, ensure_ascii=False), "data": data}
    lines = ["# Style sheet: %s" % name, ""]
    lines.append("| decision | observation | choice |")
    lines.append("| --- | --- | --- |")
    lines.append("| Quotation marks | %d curly / %d straight | %s |" % (curly, straight, sheet["quotes"]["decision"]))
    lines.append("| Dashes | %d em / %d en | %s |" % (em, en, sheet["dashes"]["decision"]))
    lines.append("| Serial comma | %d with / %d without | %s |"
                 % (serial, nonserial, "use" if sheet["serial_comma"]["decision"] else "omit"))
    lines.append("| Numerals | %d digits / %d spelled | %s |"
                 % (digits, spelled, sheet["numerals"]["decision"]))
    lines.append("| Spelling | %d -ise / %d -ize | %s |" % (iso, ize, sheet["spelling"]["decision"]))
    lines.append("| Headings | %d | %s |" % (len(heads), sheet["headings"]["style"]))
    if dates:
        lines.append("| Dates | %d | %s |" % (len(dates), dates[0]))
    lines.append("")
    lines.append("## Terminology to keep consistent")
    lines.extend("- %s" % term for term in sheet["terms"]) if sheet["terms"] else lines.append("- (none detected)")
    return {"output": "\n".join(lines), "data": data}


def _hunks(base, other):
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def version_merge(ctx):
    mine = ctx.require_text(minimum=1)
    other = ctx.require_second_text()
    base = ctx.text3 or ""
    if not base.strip():
        raise core.InvalidInput(
            "three-way merge needs a base version",
            hint="Pass the common ancestor with --text3 or --text3-file.",
        )
    base_l = core.canonical_newlines(base).split("\n")
    mine_l = core.canonical_newlines(mine).split("\n")
    other_l = core.canonical_newlines(other).split("\n")
    hunks = []
    for start, end, repl in _hunks(base_l, mine_l):
        hunks.append({"start": start, "end": end, "text": repl, "side": "current"})
    for start, end, repl in _hunks(base_l, other_l):
        hunks.append({"start": start, "end": end, "text": repl, "side": "other"})
    hunks.sort(key=lambda h: (h["start"], h["end"]))
    strategy = ctx.params["strategy"]
    out, conflicts = [], []
    i, h = 0, 0
    n = len(base_l)
    while i < n:
        group = [x for x in hunks if x["start"] == i]
        if not group:
            out.append(base_l[i])
            i += 1
            continue
        span_end = max(x["end"] for x in group)
        current = [x for x in group if x["side"] == "current"]
        others = [x for x in group if x["side"] == "other"]
        if current and others and [x["text"] for x in current] != [x["text"] for x in others]:
            if strategy == "prefer-current":
                out.extend(current[-1]["text"])
            elif strategy == "prefer-other":
                out.extend(others[-1]["text"])
            else:
                conflicts.append({"start": i + 1, "end": span_end})
                out.append("<<<<<<< current")
                out.extend(current[-1]["text"])
                out.append("=======")
                out.extend(others[-1]["text"])
                out.append(">>>>>>> other")
        else:
            out.extend((current or others)[-1]["text"])
        i = span_end if span_end > i else i + 1
    data = {"conflicts": conflicts, "conflict_count": len(conflicts), "strategy": strategy,
            "base_lines": n}
    warnings = ["%d conflict(s) marked for manual resolution" % len(conflicts)] if conflicts else []
    return {"output": "\n".join(out), "data": data, "warnings": warnings}


# --------------------------------------------------------------------------
# handler table
# --------------------------------------------------------------------------

HANDLERS = {
    "creation.compression-and-condensation-engine": compression,
    "creation.format-to-format-text-converter": format_converter,
    "creation.constraint-based-writing-solver": constraint_solver,
    "creation.plain-language-converter": plain_language,
    "creation.text-quality-finisher": quality_finisher,
    "editing.grammar-and-mechanics-editor": grammar_mechanics,
    "editing.developmental-editor": developmental_editor,
    "editing.line-and-copy-editor": line_copy_editor,
    "editing.structural-reorganization-editor": structural_reorg,
    "editing.consistency-and-terminology-auditor": consistency_auditor,
    "editing.ambiguity-and-logic-detector": ambiguity_detector,
    "editing.redundancy-and-fluff-trimmer": redundancy_trimmer,
    "editing.fact-claim-marker": fact_claim_marker,
    "editing.sensitive-tone-reviewer": sensitive_tone_reviewer,
    "editing.change-explanation-editor": change_explanation,
    "editing.style-sheet-builder": style_sheet_builder,
    "editing.version-merge-editor": version_merge,
}
