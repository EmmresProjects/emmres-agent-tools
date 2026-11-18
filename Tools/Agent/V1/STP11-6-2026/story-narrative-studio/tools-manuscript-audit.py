#!/usr/bin/env python3
"""tools-manuscript-audit.py - measurable manuscript, continuity and verse diagnostics.

Loaded by ``tools-story-narrative.py`` (``audit``, ``continuity``, ``verse``
operations) and by ``tools-revision-planner.py`` (which reuses the findings).

All analysis is local and deterministic: regular expressions, counters and the
packed rubric asset. Nothing is sent anywhere, nothing is executed, and no model
is called. The findings are *evidence for a human or an agent to weigh*, not
publishing verdicts.
"""

from __future__ import annotations

import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import importlib.util as _ilu

_SPEC = _ilu.spec_from_file_location("sns_core", os.path.join(_HERE, "tools-narrative-core.py"))
core = _ilu.module_from_spec(_SPEC)
_SPEC.loader.exec_module(core)

HEDGES = [
    "very", "really", "quite", "somewhat", "rather", "fairly", "pretty much",
    "a bit", "kind of", "sort of", "just", "actually", "basically", "literally",
    "definitely", "certainly", "perhaps", "maybe", "somehow", "seemed to",
]
FILTER_WORDS = [
    "felt", "saw", "heard", "noticed", "realized", "realised", "watched",
    "seemed", "wondered", "thought", "knew", "decided", "began to", "started to",
    "could feel", "could see", "could hear",
]
ABSTRACT_SUFFIX = ("tion", "sion", "ness", "ity", "ment", "ance", "ence", "ism", "ization")
CLICHES = [
    "at the end of the day", "in the nick of time", "a shiver ran down",
    "let out a breath he didn't know", "let out a breath she didn't know",
    "heart pounded", "heart hammered", "time stood still", "only time will tell",
    "little did they know", "all hell broke loose", "eyes widened",
    "breath hitched", "pool of blood", "dead as a doornail", "calm before the storm",
    "avoid like the plague", "needle in a haystack", "tip of the iceberg",
]
EXPOSITION_MARKERS = ["as you know", "of course you know", "as everyone knows", "remember that", "as i told you"]
STRONG_VERBS = [
    "seize", "refuse", "abandon", "confess", "strike", "break", "pay", "surrender",
    "betray", "leave", "sign", "burn", "bury", "trade", "steal", "return",
]
PRONOUN_GROUPS = {
    "first": ["i", "me", "my", "mine", "we", "us", "our"],
    "third": ["he", "she", "him", "her", "his", "they", "them", "their"],
    "second": ["you", "your", "yours"],
}
VIOLENCE_TERMS = ["blood", "corpse", "stab", "gunshot", "dismember", "torture", "gore",
                  "beheading", "strangle", "massacre"]
SEXUAL_TERMS = ["genital", "orgasm", "aroused", "intercourse", "nude", "naked", "erotic"]
SUBSTANCE_TERMS = ["heroin", "cocaine", "methamphetamine", "overdose", "withdrawal", "fentanyl"]
SELF_HARM_TERMS = ["suicide", "self-harm", "cut myself", "razor blade", "overdose on"]
METHOD_TERMS = ["instructions for", "how to make a bomb", "how to build a", "recipe for explosives",
                "step-by-step guide to", "synthesise the", "synthesize the compound"]
PROTECTED_GROUPS = ["those people", "all of them are", "naturally lazy", "naturally violent",
                    "genetically", "savage", "primitive tribe"]


# --------------------------------------------------------------------------
# Text preparation
# --------------------------------------------------------------------------
def strip_markup(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`>#]+", " ", text)
    return text


def sentences(text):
    clean = strip_markup(text)
    parts = re.split(r"(?<=[.!?\u201d\"'])\s+|\n{2,}", clean)
    return [p.strip() for p in parts if len(p.strip()) > 1]


def words(text):
    return re.findall(r"[A-Za-z0-9'\u2019-]+", text)


def syllables_word(word):
    w = word.lower()
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy])es$", "", w)
    w = re.sub(r"ed$", "", w) if not re.search(r"(ted|ded|eed)$", w) else w
    groups = re.findall(r"[aeiouy]{1,3}", w)
    count = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1
    if w.endswith("le") and len(w) > 2 and w[-3] not in "aeiouy":
        count += 1
    return max(1, count)


def readability(sentence_list):
    w = []
    for sentence in sentence_list:
        w.extend(words(sentence))
    if not w or not sentence_list:
        return {"grade": 0.0, "avg_sentence": 0.0, "words": len(w)}
    syl = sum(syllables_word(x) for x in w)
    asl = len(w) / len(sentence_list)
    asw = syl / len(w)
    grade = 0.39 * asl + 11.8 * asw - 15.59
    return {"grade": round(max(0.0, grade), 1), "avg_sentence": round(asl, 1), "words": len(w)}


def guess_title(text, fallback="Untitled manuscript"):
    """Use the first markdown heading, else the first short line, else the fallback."""
    heading = re.search(r"^\s{0,3}#\s+(.+?)\s*$", text, flags=re.M)
    if heading:
        return heading.group(1).strip()[:120]
    for line in text.strip().split("\n"):
        clean = line.strip()
        if 3 <= len(clean) <= 90 and not clean.endswith((".", ",", ";")):
            return clean.strip('\"\'\u2018\u2019\u201c\u201d')[:120]
    return fallback


def split_sections(text, min_words=40):
    """Split on markdown headings, then 'Chapter'/'Scene' markers, then blank lines."""
    raw = []
    if re.search(r"^#{1,6}\s+", text, flags=re.M):
        chunks = re.split(r"^#{1,6}\s+.*$", text, flags=re.M)
        raw = chunks[1:] if len(chunks) > 1 else chunks
    elif re.search(r"^\s*(chapter|scene|part)\s+[\divxlc]+", text, flags=re.M | re.I):
        raw = re.split(r"^\s*(?:chapter|scene|part)\s+[\divxlc]+\b.*$", text, flags=re.M | re.I)
        raw = raw[1:]
    else:
        blocks = re.split(r"\n\s*\n", text)
        buffer = []
        for block in blocks:
            buffer.append(block)
            if len(words(" ".join(buffer))) >= min_words:
                raw.append("\n\n".join(buffer))
                buffer = []
        if buffer:
            raw.append("\n\n".join(buffer))
    sections = []
    for index, chunk in enumerate(raw, 1):
        wc = len(words(strip_markup(chunk)))
        if wc == 0:
            continue
        sections.append({"index": index, "text": chunk.strip(), "words": wc})
    if not sections and text.strip():
        sections = [{"index": 1, "text": text, "words": len(words(strip_markup(text)))}]
    return sections


# --------------------------------------------------------------------------
# Metric helpers
# --------------------------------------------------------------------------
def _density(count, total_words, per=1000):
    if not total_words:
        return 0.0
    return round(count * per / total_words, 2)


def _phrase_hits(text_lower, phrases):
    hits = []
    for phrase in phrases:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        for match in re.finditer(pattern, text_lower):
            start = max(0, match.start() - 50)
            hits.append({"phrase": phrase, "context": text_lower[start:match.end() + 50].strip()})
    return hits


def dialogue_stats(text):
    quotes = re.findall(r"[\u201c\"]([^\u201d\"]{1,600})[\u201d\"]", text)
    quoted_words = sum(len(words(q)) for q in quotes)
    total = len(words(strip_markup(text))) or 1
    tags = re.findall(r"\b(said|asked|replied|answered|shouted|whispered|muttered|told|added)\b",
                      text, flags=re.I)
    return {
        "dialogue_lines": len(quotes),
        "dialogue_words": quoted_words,
        "dialogue_ratio": round(quoted_words / total, 3),
        "tags": len(tags),
        "tag_density": _density(len(tags), total),
        "avg_line_words": round(quoted_words / len(quotes), 1) if quotes else 0.0,
    }


def passive_ratio(text):
    matches = re.findall(r"\b(?:was|were|is|are|been|being|be)\s+\w+(?:ed|en)\b", text, flags=re.I)
    total = len(sentences(text)) or 1
    return round(len(matches) / total, 3), matches[:8]


NOT_ADVERBS = {
    "tally", "assembly", "family", "supply", "reply", "ally", "rally", "belly", "jelly", "rely",
    "apply", "imply", "multiply", "monopoly", "melancholy", "italy", "july", "fly", "ply", "supply",
    "ugly", "friendly", "lovely", "lively", "lonely", "costly", "deadly", "likely", "daily", "weekly",
    "monthly", "yearly", "hourly", "quarterly", "elderly", "orderly", "scholarly", "ghastly", "chilly",
    "curly", "burly", "surly", "unruly", "stately", "smelly", "sparkly", "steely", "wobbly", "holy",
    "silly", "only", "early", "kindly", "bodily", "homely", "seemly", "timely", "solely", "alley",
    "valley", "trolley", "chimney", "journey", "money", "honey", "pulley", "medley", "parsley",
}


def adverb_ratio(text):
    """Adverbs per 1000 words, excluding common -ly nouns/adjectives that fool the pattern."""
    adverbs = [w for w in re.findall(r"\b[A-Za-z]+ly\b", text, flags=re.I) if w.lower() not in NOT_ADVERBS]
    total = len(words(text)) or 1
    return _density(len(adverbs), total), adverbs[:12]


def abstract_ratio(text):
    tokens = [w.lower() for w in words(text)]
    abstract = [w for w in tokens if len(w) > 5 and w.endswith(ABSTRACT_SUFFIX)]
    return _density(len(abstract), len(tokens) or 1), abstract[:12]


def sentence_stats(sentence_list):
    lengths = [len(words(s)) for s in sentence_list if s.strip()]
    if not lengths:
        return {"count": 0, "avg": 0.0, "stdev": 0.0, "long_ratio": 0.0, "short_ratio": 0.0, "monotony": 0.0}
    avg = sum(lengths) / len(lengths)
    variance = sum((x - avg) ** 2 for x in lengths) / len(lengths)
    openers = {}
    for sentence in sentence_list:
        toks = words(sentence)
        if toks:
            key = toks[0].lower()
            openers[key] = openers.get(key, 0) + 1
    top_opener_share = max(openers.values()) / len(sentence_list) if openers else 0
    return {
        "count": len(lengths),
        "avg": round(avg, 1),
        "stdev": round(math.sqrt(variance), 1),
        "long_ratio": round(sum(1 for x in lengths if x > 40) / len(lengths), 3),
        "short_ratio": round(sum(1 for x in lengths if x <= 8) / len(lengths), 3),
        "monotony": round(top_opener_share, 3),
    }


def pov_stats(text):
    tokens = [w.lower() for w in words(text)]
    total = len(tokens) or 1
    return {key: round(sum(1 for t in tokens if t in group) / total, 3)
            for key, group in PRONOUN_GROUPS.items()}


# --------------------------------------------------------------------------
# Rubric scoring
# --------------------------------------------------------------------------
def _score_from(value, good, bad, invert=False):
    """Map a metric to 1-5. good = publishable threshold, bad = worst case."""
    if good == bad:
        return 3
    span = (value - bad) / (good - bad)
    span = core.clamp(span, 0.0, 1.0) if not invert else core.clamp(1 - span, 0.0, 1.0)
    return round(1 + span * 4, 2)


def score_manuscript(text, spec, outline=None, sections=None):
    """Return per-dimension {score, evidence, issues, confidence} for one manuscript."""
    clean = strip_markup(text)
    sents = sentences(text)
    total_words = len(words(clean))
    sections = sections or split_sections(text)
    issues = []

    def issue(dim, severity, message, evidence):
        issues.append({"dimension": dim, "severity": severity, "message": message, "evidence": evidence})

    d_stats = dialogue_stats(text)
    s_stats = sentence_stats(sents)
    adv, adv_sample = adverb_ratio(clean)
    pas, pas_sample = passive_ratio(text)
    absn, abs_sample = abstract_ratio(clean)
    hedges = _phrase_hits(clean.lower(), HEDGES)
    filters = _phrase_hits(clean.lower(), FILTER_WORDS)
    cliches = _phrase_hits(clean.lower(), CLICHES)
    expo = _phrase_hits(clean.lower(), EXPOSITION_MARKERS)
    povs = pov_stats(clean)
    readability_data = readability(sents)
    fmt = spec.get("format_preset", {}) if spec else {}

    results = {}

    # coherence / causality / structure / continuity come from continuity + outline checks
    if outline:
        beats = outline.get("beats") or []
        covered = 0
        for beat in beats:
            name_tokens = [t for t in words(beat.get("name", "").lower()) if len(t) > 3]
            if any(t in clean.lower() for t in name_tokens):
                covered += 1
        ratio = covered / len(beats) if beats else 0
        results["structure"] = {
            "score": round(1 + ratio * 4, 2),
            "confidence": "low",
            "evidence": ["%s of %s beat names appear in the manuscript; beat names are only a proxy for actual turning points." % (covered, len(beats))],
        }
        act_words = outline.get("acts") or {}
        if act_words:
            shares = {str(k): round(v["words"] / max(1, outline.get("target_words", 1)), 3) for k, v in act_words.items()}
            results["structure"]["evidence"].append("Outline act shares: %s" % shares)
    else:
        results["structure"] = {
            "score": None,
            "confidence": "manual",
            "evidence": ["No outline supplied; pass --outline with an outline JSON to check beat coverage."],
        }

    # thematic question presence
    theme_terms = []
    if spec and spec.get("themes"):
        theme_terms = [t.lower() for t in spec["themes"]]
    if theme_terms:
        hits = sum(clean.lower().count(t) for t in theme_terms)
        results["theme_integration"] = {
            "score": round(core.clamp(1 + min(hits, 8) * 0.5, 1, 5), 2),
            "confidence": "low",
            "evidence": ["Theme terms from the brief appear %s times; frequency is not integration." % hits],
        }
    else:
        results["theme_integration"] = {
            "score": None, "confidence": "manual",
            "evidence": ["No --themes supplied; theme integration requires a human or model read."],
        }

    results["prose_clarity"] = {
        "score": round(
            (_score_from(adv, 8, 30, invert=True) * 0.2
             + _score_from(pas, 0.15, 0.55, invert=True) * 0.2
             + _score_from(absn, 12, 45, invert=True) * 0.2
             + _score_from(hedges and len(hedges) or 0, 1, 25, invert=True) * 0.2
             + _score_from(cliches and len(cliches) or 0, 0, 8, invert=True) * 0.2), 2),
        "confidence": "medium",
        "evidence": [
            "Adverbs: %s per 1000 words (%s)" % (adv, ", ".join(adv_sample[:6]) or "none"),
            "Passive constructions: %.1f%% of sentences" % (pas * 100),
            "Abstract nouns: %s per 1000 words" % absn,
            "Hedges: %s hits; filter words: %s hits; cliches: %s hits" % (len(hedges), len(filters), len(cliches)),
            "Sentence length: mean %s, stdev %s, long-sentence share %.1f%%" % (
                s_stats["avg"], s_stats["stdev"], s_stats["long_ratio"] * 100),
            "Flesch-Kincaid grade estimate: %s" % readability_data["grade"],
        ],
    }
    if adv > 18:
        issue("prose_clarity", "medium", "Adverb density is high; replace intensifiers with stronger verbs.",
              ", ".join(adv_sample[:6]))
    if len(hedges) > 12:
        issue("prose_clarity", "medium", "Hedging language weakens statement.", ", ".join(h["phrase"] for h in hedges[:6]))
    if len(filters) > 10:
        issue("prose_clarity", "low", "Filter words distance the reader from the POV experience.",
              ", ".join(h["phrase"] for h in filters[:6]))
    if len(cliches) > 0:
        issue("originality", "low" if len(cliches) < 4 else "medium",
              "%s cliche phrase(s) detected." % len(cliches), ", ".join(h["phrase"] for h in cliches[:6]))
    if s_stats["stdev"] < 4 and s_stats["count"] > 20:
        issue("prose_clarity", "medium", "Sentence rhythm is monotone; vary length and structure.",
              "mean %.1f words, stdev %.1f" % (s_stats["avg"], s_stats["stdev"]))
    if s_stats["monotony"] > 0.3:
        issue("prose_clarity", "low", "One opening word dominates sentence starts.",
              "most common opener covers %.0f%% of sentences" % (s_stats["monotony"] * 100))

    target_ratio = fmt.get("dialogue_ratio") or 0.3
    delta = abs(d_stats["dialogue_ratio"] - target_ratio)
    results["dialogue_economy"] = {
        "score": round(_score_from(delta, 0.02, 0.30, invert=True), 2),
        "confidence": "low",
        "evidence": [
            "Dialogue share %.0f%% against a %.0f%% target for this format." % (
                d_stats["dialogue_ratio"] * 100, target_ratio * 100),
            "Tag density %s per 1000 words; %s dialogue lines; average %s words per line." % (
                d_stats["tag_density"], d_stats["dialogue_lines"], d_stats["avg_line_words"]),
        ],
    }
    if d_stats["tag_density"] > 12:
        issue("dialogue_economy", "low", "Dialogue tag density is high; use action beats instead of tags.",
              "%s tags per 1000 words" % d_stats["tag_density"])
    if expo:
        issue("dialogue_economy", "high", "Exposition markers detected - characters explaining what they both know.",
              ", ".join(h["phrase"] for h in expo[:4]))

    results["audience_fit"] = {
        "score": _audience_score(spec, clean, readability_data, issues),
        "confidence": "low",
        "evidence": ["Readability grade %s, declared audience %s." % (
            readability_data["grade"], spec.get("audience_label", "unspecified") if spec else "unspecified")],
    }

    results["pacing"] = _pacing_result(sections, s_stats, outline, issues)
    results["causality"] = _causality_result(clean, sections, issues)
    results["character_agency"] = _agency_result(clean, pas, povs, issues)
    results["originality"] = {
        "score": round(_score_from(len(cliches) + len(expo), 0, 10, invert=True), 2),
        "confidence": "low",
        "evidence": ["Cliché and exposition hits are the only automated originality signal; read the work for premise-level similarity."],
    }
    results["emotional_impact"] = {
        "score": None,
        "confidence": "manual",
        "evidence": ["Emotional impact cannot be measured mechanically; check whether each act pays a debt it opened."],
    }
    results["character_depth"] = {
        "score": None,
        "confidence": "manual",
        "evidence": ["Requires reading: verify every principal wants two incompatible things."],
    }
    results["continuity"] = {
        "score": None,
        "confidence": "manual",
        "evidence": ["Run the `continuity` operation against a bible to score this dimension."],
    }
    results["format_readiness"] = _readiness_result(text, total_words, spec, issues)
    results["representation_ethics"] = _ethics_result(clean, spec, issues)

    metrics = {
        "words": total_words,
        "sentences": s_stats["count"],
        "sections": len(sections),
        "readability": readability_data,
        "sentence_stats": s_stats,
        "dialogue": d_stats,
        "passive_ratio": pas,
        "adverbs_per_1000": adv,
        "abstract_per_1000": absn,
        "hedge_hits": len(hedges),
        "filter_hits": len(filters),
        "cliche_hits": len(cliches),
        "exposition_hits": len(expo),
        "pov_share": povs,
        "section_words": [s["words"] for s in sections],
    }
    return results, issues, metrics


def _audience_score(spec, clean, readability_data, issues):
    if not spec:
        return None
    audience = spec.get("audience", "general")
    grade = readability_data["grade"]
    ceilings = {"children": 4.0, "middle-grade": 7.0, "ya": 10.0, "general": 13.0, "adult": 20.0}
    ceiling = ceilings.get(audience, 13.0)
    over = max(0.0, grade - ceiling)
    score = round(core.clamp(5 - over * 1.4, 1, 5), 2)
    lower = {"children": 1.0, "middle-grade": 2.0, "ya": 3.0}.get(audience)
    if lower is not None and grade < lower:
        score = max(1.0, round(score - 0.6, 2))
    if over > 1.5:
        issues.append({"dimension": "audience_fit", "severity": "high",
                       "message": "Readability grade %.1f exceeds the %s ceiling of %.1f." % (grade, audience, ceiling),
                       "evidence": "Flesch-Kincaid estimate from sentence length and syllable count"})
    lower_terms = VIOLENCE_TERMS + SEXUAL_TERMS + SUBSTANCE_TERMS + SELF_HARM_TERMS
    hits = sorted({t for t in lower_terms if re.search(r"\b" + re.escape(t) + r"\b", clean.lower())})
    if hits and audience in ("children", "middle-grade"):
        issues.append({"dimension": "audience_fit", "severity": "high",
                       "message": "Content vocabulary flagged against a %s envelope." % audience,
                       "evidence": ", ".join(hits)})
        score = max(1.0, round(score - 1.2, 2))
    return score


def _pacing_result(sections, s_stats, outline, issues):
    if len(sections) < 2:
        return {"score": None, "confidence": "manual",
                "evidence": ["Fewer than two sections detected; pacing needs chapter or scene segmentation."]}
    lengths = [s["words"] for s in sections]
    mean = sum(lengths) / len(lengths)
    spread = (max(lengths) - min(lengths)) / mean if mean else 0
    ratios = []
    for section in sections:
        d = dialogue_stats(section["text"])
        st = sentence_stats(sentences(section["text"]))
        proxy = core.clamp(d["dialogue_ratio"] * 1.4 + (1 - min(st["avg"], 30) / 30) * 0.6, 0, 1)
        ratios.append(round(proxy, 3))
    rises = sum(1 for a, b in zip(ratios, ratios[1:]) if b >= a - 0.15)
    trend = rises / max(1, len(ratios) - 1)
    score = round(core.clamp(1 + trend * 2 + min(spread, 1.2) * 1.6, 1, 5), 2)
    evidence = [
        "Section word counts: %s" % ", ".join(str(x) for x in lengths[:12]),
        "Length spread %.0f%% of mean; %s sections." % (spread * 100, len(sections)),
        "Pressure proxy per section (dialogue share + short sentences): %s" % ratios[:12],
    ]
    if max(lengths) > mean * 2.2:
        issues.append({"dimension": "pacing", "severity": "medium",
                       "message": "One section is more than twice the mean length.",
                       "evidence": "max %s vs mean %s" % (max(lengths), round(mean))})
    if any(r < 0.15 for r in ratios[len(ratios) // 2:]):
        issues.append({"dimension": "pacing", "severity": "low",
                       "message": "A late section has very low pressure by proxy; check for a sag.",
                       "evidence": "proxy values %s" % ratios})
    if outline:
        evidence.append("Compare explicitly with the outline's tension targets before acting on this proxy.")
    return {"score": score, "confidence": "low", "evidence": evidence}


def _causality_result(clean, sections, issues):
    connectors = re.findall(r"\b(because|therefore|so that|which forced|as a result|until|unless|in order to)\b",
                            clean, flags=re.I)
    turns = re.findall(r"\b(but|however|instead|except|although|yet)\b", clean, flags=re.I)
    causal_density = _density(len(connectors), max(1, len(words(clean))))
    turn_density = _density(len(turns), max(1, len(words(clean))))
    score = round(core.clamp(1 + min(causal_density, 6) * 0.45 + min(turn_density, 8) * 0.15, 1, 5), 2)
    evidence = [
        "Causal connectors: %s (%s per 1000 words)." % (len(connectors), causal_density),
        "Contrast turns: %s (%s per 1000 words)." % (len(turns), turn_density),
        "Connectives are a proxy only: verify that each scene is caused by a choice, not a coincidence.",
    ]
    if causal_density < 1.0:
        issues.append({"dimension": "causality", "severity": "medium",
                       "message": "Very few causal connectives; scenes may be sequential rather than caused.",
                       "evidence": "causal density %s per 1000 words" % causal_density})
    return {"score": score, "confidence": "low", "evidence": evidence}


def _agency_result(clean, pas, povs, issues):
    strong = sum(len(re.findall(r"\b" + re.escape(v) + r"\b", clean, flags=re.I)) for v in STRONG_VERBS)
    decisions = len(re.findall(r"\b(decided|chose|refused|agreed|promised|lied|admitted)\b", clean, flags=re.I))
    score = round(core.clamp(
        1 + min(strong / 12, 2.0) + min(decisions / 4, 1.4) + (1 - min(pas, 0.5)), 1, 5), 2)
    evidence = [
        "Strong choice verbs: %s; explicit decisions: %s." % (strong, decisions),
        "Passive constructions: %.0f%% of sentences." % (pas * 100),
        "POV pronoun share: first %.0f%%, third %.0f%%, second %.0f%%." % (
            povs["first"] * 100, povs["third"] * 100, povs["second"] * 100),
    ]
    if decisions == 0:
        issues.append({"dimension": "character_agency", "severity": "high",
                       "message": "No explicit decisions found in the manuscript.",
                       "evidence": "zero matches for decided/chose/refused/agreed/promised/lied/admitted"})
    if pas > 0.35:
        issues.append({"dimension": "character_agency", "severity": "medium",
                       "message": "Passive voice dominates; events may appear to happen without an actor.",
                       "evidence": "%.0f%% of sentences" % (pas * 100)})
    return {"score": score, "confidence": "low", "evidence": evidence}


def _readiness_result(text, total_words, spec, issues):
    if not spec:
        return {"score": None, "confidence": "manual", "evidence": ["No format supplied."]}
    target = spec.get("target_words") or 0
    fmt = spec.get("format", "unknown")
    if not target:
        return {"score": None, "confidence": "manual", "evidence": ["No target length supplied."]}
    deviation = (total_words - target) / target
    score = round(core.clamp(5 - abs(deviation) * 5, 1, 5), 2)
    evidence = ["Manuscript %s words against a %s target (%+.0f%%)." % (
        format(total_words, ","), format(target, ","), deviation * 100)]
    if abs(deviation) > 0.15:
        issues.append({"dimension": "format_readiness", "severity": "medium",
                       "message": "Length is more than 15%% away from the declared budget.",
                       "evidence": "%s words vs %s target" % (total_words, target)})
    if fmt in ("comic", "manga"):
        pages = len(re.findall(r"^\s*(?:page|p)\s*\d+", text, flags=re.M | re.I))
        evidence.append("Page markers detected: %s." % pages)
        if pages == 0:
            issues.append({"dimension": "format_readiness", "severity": "high",
                           "message": "No page markers found in a page-based format script.",
                           "evidence": "expected lines beginning with PAGE n"})
    if fmt in ("screenplay", "tv", "stage", "audio-drama"):
        scenes = len(re.findall(r"^\s*(?:INT|EXT|SCENE|CUE)\b", text, flags=re.M | re.I))
        evidence.append("Scene slugs detected: %s." % scenes)
    return {"score": score, "confidence": "medium", "evidence": evidence}


def _ethics_result(clean, spec, issues):
    found = []
    for term in METHOD_TERMS:
        if term in clean.lower():
            found.append(("critical", term))
    for term in PROTECTED_GROUPS:
        if term in clean.lower():
            found.append(("medium", term))
    for term in SELF_HARM_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", clean.lower()):
            found.append(("low", term + " (depiction vs method detail must be checked)"))
    honorifics = re.findall(r"\b(?:President|Senator|Dr\.|Mr\.|Ms\.|Mrs\.|Minister|General)\s+[A-Z][a-z]+",
                            clean)
    if honorifics:
        found.append(("low", "possible real-person reference: " + ", ".join(sorted(set(honorifics))[:4])))
    claims = len(re.findall(r"\b(in 1[6-9]\d\d|in 20\d\d|percent|according to|a study)\b", clean, flags=re.I))
    for severity, term in found:
        issues.append({"dimension": "representation_ethics", "severity": severity,
                       "message": "Ethics scan flagged: %s" % term,
                       "evidence": "heuristic lexicon match - review manually"})
    score = round(core.clamp(5 - sum({"critical": 2.5, "medium": 1.0, "low": 0.4}[s] for s, _ in found), 1, 5), 2)
    evidence = [
        "Heuristic ethics hits: %s." % (len(found) or "none"),
        "Factual claim markers: %s (each needs an entry in the bible's sources ledger)." % claims,
        "This scan is a triage aid, not a rights review.",
    ]
    return {"score": score, "confidence": "low", "evidence": evidence}


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------
def verdict_from_scores(results, issues, spec):
    rub = core.load_asset("rubric")
    fmt = (spec or {}).get("format", "default")
    thresholds = rub["format_thresholds"].get(fmt) or rub["format_thresholds"]["default"]
    audience_thresholds = rub["format_thresholds"].get((spec or {}).get("audience", ""), None)
    if audience_thresholds:
        thresholds = audience_thresholds
    scored = {k: v["score"] for k, v in results.items() if v.get("score") is not None}
    total = round(sum(scored.values()) / len(scored), 2) if scored else 0.0
    blockers = []
    for dim, floor in (thresholds.get("block_below") or {}).items():
        value = scored.get(dim)
        if value is not None and value < floor:
            blockers.append("%s scored %s (floor %s)" % (dim, value, floor))
    weights = rub["severity_weights"]
    penalty = sum(weights.get(i["severity"], 0.5) for i in issues)
    weighted = round(total - min(penalty / 10.0, 1.2), 2)
    if weighted < thresholds.get("min_total", 3.4) or blockers:
        verdict = "blocked"
    elif weighted < thresholds.get("min_total", 3.4) + 0.6:
        verdict = "revise"
    else:
        verdict = "ready"
    return {
        "verdict": verdict,
        "mean_score": total,
        "weighted_score": weighted,
        "min_total": thresholds.get("min_total", 3.4),
        "blockers": blockers,
        "issue_penalty": round(penalty, 2),
        "dimensions_scored": len(scored),
    }


def audit_manuscript(text, spec, outline=None, timeout=None):
    deadline = core.Deadline(timeout or 30)
    sections = split_sections(text)
    deadline.check("sectioning")
    results, issues, metrics = score_manuscript(text, spec, outline=outline, sections=sections)
    deadline.check("scoring")
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: order.get(i["severity"], 4))
    report = {
        "title": (spec or {}).get("title") or guess_title(text),
        "format": (spec or {}).get("format_label") or "unspecified",
        "genre": (spec or {}).get("genre_label") or "unspecified",
        "audience": (spec or {}).get("audience_label") or "unspecified",
        "target_words": (spec or {}).get("target_words"),
        "metrics": metrics,
        "dimensions": results,
        "issues": issues,
        "issue_counts": {sev: sum(1 for i in issues if i["severity"] == sev)
                         for sev in ("critical", "high", "medium", "low")},
        "generated_at": core.now_iso(),
    }
    report["verdict"] = verdict_from_scores(results, issues, spec)
    report["next_actions"] = next_actions(report)
    return report, render_audit_md(report)


def next_actions(report):
    actions = []
    counts = report["issue_counts"]
    if counts["critical"]:
        actions.append("Resolve every critical issue before any other work; they block production.")
    if counts["high"]:
        actions.append("Fix high-severity issues in the dimensions that scored below 3.")
    low = sorted([(v["score"], k) for k, v in report["dimensions"].items() if v.get("score") is not None])
    for score, dim in low[:3]:
        if score < 3.5:
            actions.append("Target %s next (scored %s): %s" % (
                dim, score, report["dimensions"][dim].get("evidence", [""])[0][:160]))
    if not actions:
        actions.append("No automated blockers. Complete the manual dimensions: character depth, theme integration, emotional impact, continuity.")
    return actions


def render_audit_md(report):
    v = report["verdict"]
    out = ["# Manuscript Audit - %s" % report["title"], ""]
    out.append("*%s | %s | %s | generated %s*" % (
        report["format"], report["genre"], report["audience"], report["generated_at"]))
    out.append("")
    out.append("## Verdict: **%s**" % v["verdict"].upper())
    out.append("")
    out.append("| Metric | Value |")
    out.append("| --- | --- |")
    out.append("| Mean dimension score | %s / 5 |" % v["mean_score"])
    out.append("| Weighted score (after issue penalty) | %s |" % v["weighted_score"])
    out.append("| Required minimum | %s |" % v["min_total"])
    out.append("| Dimensions scored automatically | %s / %s |" % (
        v["dimensions_scored"], len(report["dimensions"])))
    out.append("| Issues (critical/high/medium/low) | %s/%s/%s/%s |" % (
        report["issue_counts"]["critical"], report["issue_counts"]["high"],
        report["issue_counts"]["medium"], report["issue_counts"]["low"]))
    out.append("")
    if v["blockers"]:
        out.append("**Blockers:**")
        out.append("")
        for blocker in v["blockers"]:
            out.append("- %s" % blocker)
        out.append("")
    out.append("## Dimensions")
    out.append("")
    out.append("| Dimension | Score | Confidence | Evidence |")
    out.append("| --- | --- | --- | --- |")
    for dim, data in report["dimensions"].items():
        score = data.get("score")
        out.append("| %s | %s | %s | %s |" % (
            dim, "manual" if score is None else score, data.get("confidence", "manual"),
            (data.get("evidence") or [""])[0].replace("|", "/")))
    out.append("")
    out.append("## Measurements")
    out.append("")
    m = report["metrics"]
    out.append("- Words: **%s** in %s sentences across %s sections" % (
        format(m["words"], ","), m["sentences"], m["sections"]))
    out.append("- Readability (Flesch-Kincaid estimate): grade %s; mean sentence %s words" % (
        m["readability"]["grade"], m["readability"]["avg_sentence"]))
    out.append("- Sentence rhythm: mean %s, stdev %s, long %s%%, short %s%%" % (
        m["sentence_stats"]["avg"], m["sentence_stats"]["stdev"],
        round(m["sentence_stats"]["long_ratio"] * 100), round(m["sentence_stats"]["short_ratio"] * 100)))
    out.append("- Dialogue: %.0f%% of words in %s lines; %s tags per 1000 words" % (
        m["dialogue"]["dialogue_ratio"] * 100, m["dialogue"]["dialogue_lines"], m["dialogue"]["tag_density"]))
    out.append("- Lexical: adverbs %s/1000, abstract nouns %s/1000, passive %.0f%% of sentences" % (
        m["adverbs_per_1000"], m["abstract_per_1000"], m["passive_ratio"] * 100))
    out.append("- Signals: %s hedges, %s filter words, %s cliches, %s exposition markers" % (
        m["hedge_hits"], m["filter_hits"], m["cliche_hits"], m["exposition_hits"]))
    out.append("")
    out.append("## Issues")
    out.append("")
    if not report["issues"]:
        out.append("No automated issues detected. Manual dimensions still require a read.")
    for issue in report["issues"]:
        out.append("- **[%s]** %s _(%s)_ - evidence: %s" % (
            issue["severity"], issue["message"], issue["dimension"], issue["evidence"]))
    out.append("")
    out.append("## Next actions")
    out.append("")
    for action in report["next_actions"]:
        out.append("1. %s" % action)
    out.append("")
    out.append("## Limits of this audit")
    out.append("")
    out.append("Scores are computed from regular-expression and counting heuristics. They detect patterns, not quality. "
               "Manual dimensions are left unscored on purpose. Nothing here replaces reading the manuscript, "
               "an editorial letter, or a rights review.")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Continuity
# --------------------------------------------------------------------------
def continuity_check(bible, manuscript_text=None, timeout=None):
    deadline = core.Deadline(timeout or 30)
    schema = core.load_asset("continuity_schema")
    findings = []

    problems = core.validate_schema(bible, schema)
    for problem in problems:
        findings.append({
            "severity": "critical",
            "code": "schema",
            "message": problem["message"],
            "location": problem["path"],
            "fix": "Correct the bible so it validates against asset-continuity-schema.json.",
        })
    deadline.check("schema")
    if not isinstance(bible, dict):
        return _continuity_report(findings, bible, manuscript_text, {})

    chars = {c.get("id"): c for c in bible.get("characters") or [] if isinstance(c, dict)}
    location_entries = {l.get("id"): (l.get("name") or l.get("id"))
                        for l in bible.get("locations") or [] if isinstance(l, dict)}
    locations = set(location_entries)
    factions = {f.get("id") for f in bible.get("factions") or [] if isinstance(f, dict)}
    items = {i.get("id") for i in bible.get("items") or [] if isinstance(i, dict)}
    timeline = bible.get("timeline") or []
    threads = bible.get("threads") or []
    rules = bible.get("rules") or []

    def add(severity, code, message, location, fix):
        findings.append({"severity": severity, "code": code, "message": message,
                         "location": location, "fix": fix})

    # referential integrity
    for char in chars.values():
        for rel in char.get("relationships") or []:
            target = rel.get("to")
            if target not in chars:
                add("high", "dangling-relationship",
                    "Relationship target %r is not a defined character." % target,
                    "characters.%s.relationships" % char.get("id"),
                    "Define the character or correct the id.")
            if target == char.get("id"):
                add("medium", "self-relationship", "Character is in a relationship with themselves.",
                    "characters.%s" % char.get("id"), "Remove the entry.")
        for fact in char.get("knowledge") or []:
            learned = fact.get("learned_at") or ""
            timeline_ids = {t.get("id") for t in timeline if isinstance(t, dict)}
            if learned and learned not in timeline_ids and not re.search(r"\d", learned):
                add("low", "unpinned-knowledge",
                    "Knowledge entry %r is not pinned to a timeline id or a dated event." % fact.get("fact"),
                    "characters.%s.knowledge" % char.get("id"),
                    "Use a timeline id or a concrete date so knowledge order can be audited.")

    for item in bible.get("items") or []:
        if item.get("owner") and item["owner"] not in chars:
            add("high", "dangling-owner", "Item %r has an unknown owner %r." % (item.get("id"), item.get("owner")),
                "items.%s" % item.get("id"), "Define the owner or correct the id.")
    for event in timeline:
        if event.get("location") and event["location"] not in locations:
            add("high", "dangling-location", "Event %r references unknown location %r." % (
                event.get("id"), event.get("location")), "timeline.%s" % event.get("id"),
                "Define the location or correct the id.")
        for participant in event.get("participants") or []:
            if participant not in chars:
                add("high", "dangling-participant", "Event %r references unknown participant %r." % (
                    event.get("id"), participant), "timeline.%s" % event.get("id"),
                    "Define the character or remove the participant.")
    for faction in bible.get("factions") or []:
        for target in faction.get("opposes") or []:
            if target not in factions:
                add("high", "dangling-opposition", "Faction %r opposes unknown faction %r." % (
                    faction.get("id"), target), "factions.%s" % faction.get("id"),
                    "Define the faction or correct the id.")

    # timeline ordering
    orders = [e.get("order") for e in timeline if isinstance(e.get("order"), int)]
    if len(orders) != len(set(orders)):
        duplicates = sorted({o for o in orders if orders.count(o) > 1})
        add("medium", "timeline-collision", "Timeline positions %s are used more than once." % duplicates,
            "timeline", "Give each event a unique order value.")
    if orders and orders != sorted(orders):
        add("low", "timeline-unsorted", "Timeline array is not sorted by order.",
            "timeline", "Sort events by order for readability; the audit uses the order value.")

    # threads
    open_threads = [t for t in threads if t.get("status") == "open"]
    for thread in threads:
        if thread.get("status") == "resolved" and not thread.get("payoff"):
            add("high", "unpaid-thread", "Thread %r is resolved without a recorded payoff." % thread.get("id"),
                "threads.%s" % thread.get("id"), "Record the payoff or reopen the thread.")
        if thread.get("status") == "open" and not thread.get("opened_at"):
            add("low", "unlocated-thread", "Open thread %r has no opening location." % thread.get("id"),
                "threads.%s" % thread.get("id"), "Record where the question is raised.")
        if thread.get("status") == "open" and thread.get("resolved_at"):
            add("medium", "stale-thread", "Thread %r is marked open but has a resolution point." % thread.get("id"),
                "threads.%s" % thread.get("id"), "Set status to resolved and record the payoff.")

    # rules
    for rule in rules:
        if not rule.get("cost"):
            add("high", "costless-rule", "Rule %r has no cost, so it cannot constrain choices." % rule.get("id"),
                "rules.%s" % rule.get("id"), "State the price of using the rule.")

    # sources
    for source in bible.get("sources") or []:
        if source.get("confidence") in ("verified", "probable") and not source.get("source"):
            add("medium", "unsourced-claim", "Claim %r is marked %r without a source." % (
                source.get("claim"), source.get("confidence")), "sources.%s" % source.get("id"),
                "Add a citation or downgrade confidence to uncertain/invented.")
        if source.get("confidence") == "invented":
            add("low", "invented-claim-logged", "Invented claim logged (keep it out of factual framing): %r"
                % source.get("claim"), "sources.%s" % source.get("id"), "Label the invention in the text.")
    invented_without_marking = [s for s in bible.get("sources") or [] if s.get("confidence") == "invented"]
    if invented_without_marking:
        add("low", "invention-register",
            "%s invented claim(s) recorded; confirm each is presented as fiction." % len(invented_without_marking),
            "sources", "Keep the fact/invention separation visible to the reader.")

    # glossary duplicates
    seen_terms = {}
    for entry in bible.get("glossary") or []:
        term = (entry.get("term") or "").strip().lower()
        if term in seen_terms:
            add("medium", "glossary-duplicate", "Glossary term %r appears twice." % entry.get("term"),
                "glossary", "Merge the entries.")
        seen_terms[term] = True

    # manuscript cross-check
    manuscript_stats = {}
    if manuscript_text:
        clean = strip_markup(manuscript_text)
        deadline.check("manuscript cross-check")
        missing = []
        for char in chars.values():
            first = (char.get("name") or "").split(" ")[0]
            count = len(re.findall(r"\b" + re.escape(first) + r"\b", clean)) if first else 0
            manuscript_stats[char.get("id")] = count
            if count == 0:
                missing.append(char.get("name"))
        if missing:
            add("medium", "unused-character",
                "%s bible character(s) never appear in the manuscript: %s" % (len(missing), ", ".join(missing[:8])),
                "characters", "Cut them from the bible or place them in the manuscript.")
        stop = {"the", "a", "an", "of", "and", "in", "on", "at"}
        for loc_id, loc_name in location_entries.items():
            tokens = [t for t in re.findall(r"[a-z]+", (loc_name or loc_id).lower()) if t not in stop]
            found = [t for t in tokens if t in clean.lower()]
            if tokens and len(found) < max(1, len(tokens) - 1):
                add("low", "unused-location",
                    "Location %r (id %r) is not referenced in the manuscript." % (loc_name, loc_id),
                    "locations.%s" % loc_id,
                    "Place the location in the manuscript, or remove it from the bible.")
        unresolved = [t.get("id") for t in open_threads]
        if unresolved:
            add("medium", "open-threads-at-end",
                "%s thread(s) are still open: %s" % (len(unresolved), ", ".join(unresolved[:10])),
                "threads", "Resolve them, mark them abandoned, or confirm they are series-level promises.")
    else:
        add("low", "no-manuscript",
            "No manuscript supplied; only internal bible consistency was checked.",
            "manuscript", "Pass --input with the manuscript to cross-check names and usage.")

    coverage = {
        "characters": len(chars),
        "locations": len(locations),
        "factions": len(factions),
        "items": len(items),
        "rules": len(rules),
        "timeline_events": len(timeline),
        "threads_open": len(open_threads),
        "threads_total": len(threads),
        "sources": len(bible.get("sources") or []),
        "glossary": len(bible.get("glossary") or []),
        "manuscript_mentions": manuscript_stats,
    }
    return _continuity_report(findings, bible, manuscript_text, coverage)


def _continuity_report(findings, bible, manuscript_text, coverage):
    weights = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    findings.sort(key=lambda f: weights.get(f["severity"], 5), reverse=True)
    counts = {sev: sum(1 for f in findings if f["severity"] == sev)
              for sev in ("critical", "high", "medium", "low")}
    score = core.clamp(5 - (counts["critical"] * 1.5 + counts["high"] * 0.6 + counts["medium"] * 0.25 + counts["low"] * 0.1), 1, 5)
    report = {
        "title": (bible or {}).get("title") or "Untitled bible",
        "status": "invalid" if counts["critical"] else ("issues" if findings else "clean"),
        "score": round(score, 2),
        "counts": counts,
        "coverage": coverage,
        "manuscript_checked": bool(manuscript_text),
        "findings": findings,
        "generated_at": core.now_iso(),
    }
    return report, render_continuity_md(report)


def render_continuity_md(report):
    out = ["# Continuity Audit - %s" % report["title"], ""]
    out.append("*Status: **%s** | continuity score %s / 5 | manuscript cross-check: %s*" % (
        report["status"], report["score"], "yes" if report["manuscript_checked"] else "no"))
    out.append("")
    out.append("| Severity | Count |")
    out.append("| --- | --- |")
    for sev in ("critical", "high", "medium", "low"):
        out.append("| %s | %s |" % (sev, report["counts"][sev]))
    out.append("")
    if report["coverage"]:
        out.append("## Coverage")
        out.append("")
        for key, value in report["coverage"].items():
            if isinstance(value, dict):
                used = sum(1 for v in value.values() if v > 0)
                out.append("- %s: %s tracked, %s mentioned in manuscript" % (key, len(value), used))
            else:
                out.append("- %s: %s" % (key, value))
        out.append("")
    out.append("## Findings")
    out.append("")
    if not report["findings"]:
        out.append("No findings. The bible validates and no internal contradiction was detected by these checks.")
    for finding in report["findings"]:
        out.append("- **[%s]** `%s` %s  \n  Location: `%s`  \n  Fix: %s" % (
            finding["severity"], finding["code"], finding["message"],
            finding["location"], finding["fix"]))
    out.append("")
    out.append("## What this check does not do")
    out.append("")
    out.append("It does not read the manuscript for meaning, prose continuity, or timeline plausibility. "
               "It verifies the bible's internal referential integrity and, when a manuscript is supplied, "
               "whether named entities actually appear.")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Verse / lyrics prosody
# --------------------------------------------------------------------------
def verse_analyse(text, timeout=None):
    core.Deadline(timeout or 20).check("start")
    lines_raw = [l.rstrip() for l in text.split("\n")]
    stanzas, current = [], []
    for line in lines_raw:
        if line.strip():
            current.append(line)
        elif current:
            stanzas.append(current)
            current = []
    if current:
        stanzas.append(current)
    if not stanzas:
        raise core.StudioError("No verse lines found in the input.", field="input",
                               hint="Pass plain text with one line per verse line.")

    flat = [(s_index, line) for s_index, stanza in enumerate(stanzas, 1) for line in stanza]
    analysed = []
    for index, (stanza_index, line) in enumerate(flat, 1):
        clean = re.sub(r"[^A-Za-z' ]", " ", line).strip().lower()
        tokens = [t for t in clean.split() if t]
        syl = sum(syllables_word(t) for t in tokens)
        stresses = "".join("S" if i % 2 == 1 else "u" for i in range(max(0, syl)))
        analysed.append({
            "line": index,
            "stanza": stanza_index,
            "text": line.strip(),
            "words": len(tokens),
            "syllables": syl,
            "ending": tokens[-1] if tokens else "",
            "skeleton": stresses,
        })
    # rhyme scheme by suffix overlap on final word
    def rhyme_key(word):
        w = re.sub(r"[^a-z]", "", word.lower())
        return w[-3:] if len(w) >= 3 else w

    labels, seen = [], {}
    scheme = []
    next_label = ord("A")
    for item in analysed:
        key = rhyme_key(item["ending"])
        if key and key in seen:
            label = seen[key]
        else:
            label = chr(next_label)
            next_label += 1
            if key:
                seen[key] = label
        scheme.append(label)
        labels.append(label)
    rhyme_scheme = "".join(scheme)
    stanza_schemes = []
    cursor = 0
    for stanza in stanzas:
        chunk = rhyme_scheme[cursor:cursor + len(stanza)]
        stanza_schemes.append(chunk)
        cursor += len(stanza)

    syll_counts = [a["syllables"] for a in analysed]
    mean = sum(syll_counts) / len(syll_counts)
    variance = sum((x - mean) ** 2 for x in syll_counts) / len(syll_counts)
    refrain_lines = [text for text, count in
                     {a["text"]: sum(1 for b in analysed if b["text"] == a["text"]) for a in analysed}.items()
                     if count > 1]
    rhythm_words = {"iambic": 0, "trochaic": 0}
    for syllable in syll_counts:
        if syllable % 2 == 0:
            rhythm_words["iambic"] += 1
        else:
            rhythm_words["trochaic"] += 1
    meter_guess = "free verse or irregular"
    common = [c for c in set(syll_counts) if syll_counts.count(c) >= max(2, len(syll_counts) // 3)]
    if common:
        candidate = max(common)
        names = {8: "tetrameter (4 feet)", 10: "pentameter (5 feet)", 12: "hexameter (6 feet)",
                 6: "trimeter (3 feet)", 14: "heptameter (7 feet)"}
        meter_guess = names.get(candidate, "%s syllables per line" % candidate)
    assonance = len(re.findall(r"[aeiou]", text.lower()))
    report = {
        "lines": len(analysed),
        "stanzas": len(stanzas),
        "stanza_lengths": [len(s) for s in stanzas],
        "total_syllables": sum(syll_counts),
        "mean_syllables": round(mean, 2),
        "syllable_stdev": round(math.sqrt(variance), 2),
        "syllable_range": [min(syll_counts), max(syll_counts)],
        "rhyme_scheme": rhyme_scheme,
        "stanza_schemes": stanza_schemes,
        "rhymed_line_share": round(sum(1 for l in scheme if scheme.count(l) > 1) / len(scheme), 2),
        "refrains": refrain_lines,
        "meter_guess": meter_guess,
        "parity_split": rhythm_words,
        "vowel_share": round(assonance / max(1, len(re.findall(r"[a-z]", text.lower()))), 3),
        "lines_detail": analysed,
        "generated_at": core.now_iso(),
        "warnings": ["Syllable and stress values are heuristic estimates from spelling, not phonology. "
                     "Confirm against a dictionary before quoting metre as fact."],
    }
    findings = []
    if report["lines"] < 2:
        findings.append({"severity": "medium",
                         "message": "Only one line supplied; a single-line piece cannot be measured for scheme, "
                                    "stanza shape, or refrain. Pass the full text to analyse form."})
    if report["syllable_stdev"] > 4.5:
        findings.append({"severity": "medium", "message": "Line lengths vary widely (stdev %s); if the form is metrical, "
                         "the pattern will read as accidental." % report["syllable_stdev"]})
    if 0.2 < report["rhymed_line_share"] < 0.7:
        findings.append({"severity": "low", "message": "Partial rhyme: some lines rhyme and others do not. Either commit "
                         "to the scheme or move fully to free verse."})
    if not refrain_lines and len(stanzas) > 3:
        findings.append({"severity": "low", "message": "No refrain or repeated line; consider whether the form needs a "
                         "returning anchor for a song or spoken-word piece."})
    report["findings"] = findings
    return report, render_verse_md(report)


def render_verse_md(report):
    out = ["# Prosody Analysis", ""]
    out.append("*Generated %s | heuristic estimates*" % report["generated_at"])
    out.append("")
    out.append("| Metric | Value |")
    out.append("| --- | --- |")
    out.append("| Lines / stanzas | %s / %s |" % (report["lines"], report["stanzas"]))
    out.append("| Stanza shape | %s |" % report["stanza_lengths"])
    out.append("| Syllables per line (mean / stdev) | %s / %s |" % (
        report["mean_syllables"], report["syllable_stdev"]))
    out.append("| Syllable range | %s |" % report["syllable_range"])
    out.append("| Rhyme scheme | %s |" % report["rhyme_scheme"])
    out.append("| Rhymed line share | %s |" % report["rhymed_line_share"])
    out.append("| Meter guess | %s |" % report["meter_guess"])
    out.append("| Refrains | %s |" % (report["refrains"] or "none"))
    out.append("")
    out.append("## Line table")
    out.append("")
    out.append("| # | Line | Words | Syllables | Rhyme | Skeleton |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for item in report["lines_detail"]:
        out.append("| %s | %s | %s | %s | %s | `%s` |" % (
            item["line"], item["text"][:60].replace("|", "/"), item["words"],
            item["syllables"], report["rhyme_scheme"][item["line"] - 1], item["skeleton"]))
    out.append("")
    out.append("## Findings")
    out.append("")
    if not report["findings"]:
        out.append("No automated findings.")
    for finding in report["findings"]:
        out.append("- **[%s]** %s" % (finding["severity"], finding["message"]))
    out.append("")
    return "\n".join(out)
