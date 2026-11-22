#!/usr/bin/env python3
"""Objective bilingual QA, terminology, translation memory, accessibility, and change analysis."""
from __future__ import annotations

import collections
import csv
import html
import io
import json
import math
import random
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools_core import Segment, ToolError, count_words, detect_language, extract_tokens, locale_matches, memory_key, readability, utc_now


def defect(category: str, severity: str, message: str, *, segment_id: str | None = None, source: str = "", target: str = "", evidence: Any = None, recommendation: str = "Review and correct the flagged segment.", objective: bool = True, preference: bool = False) -> dict[str, Any]:
    return {
        "id": f"defect-{segment_id or 'file'}-{category}",
        "segment_id": segment_id, "category": category, "severity": severity, "message": message,
        "evidence": evidence, "source_excerpt": source[:500], "target_excerpt": target[:500],
        "recommendation": recommendation, "objective_defect": objective, "preference": preference,
        "status": "open",
    }


NUMBER_RE = re.compile(r"(?<![\w])(?P<sign>[-+−])?(?P<currency>[$€£¥₱₹₩])?\s*(?P<number>(?:\d{1,3}(?:[ .,'’]\d{3})+|\d+)(?:[.,]\d+)?)(?P<percent>\s*%)?(?:\s*(?P<unit>kg|g|mg|µg|lb|lbs|oz|km|cm|mm|m|mi|ft|in|km/h|mph|l|ml|gal|°C|°F|C|F|hours?|hrs?|minutes?|mins?|seconds?|secs?|days?|months?|years?))?(?![\w])", re.IGNORECASE)
DATE_RE = re.compile(r"\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b")
TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:[.,]\d{1,3})?)?\s?(?:AM|PM|am|pm)?\b")


def canonical_number(raw: str) -> str | None:
    value = raw.strip().replace("−", "-").replace(" ", "").replace("’", "").replace("'", "")
    sign = ""
    if value[:1] in "+-":
        sign, value = value[0], value[1:]
    if not re.search(r"\d", value):
        return None
    # Determine decimal separator from the last punctuation and digit grouping.
    dot, comma = value.rfind("."), value.rfind(",")
    decimal_sep = None
    if dot >= 0 and comma >= 0:
        decimal_sep = "." if dot > comma else ","
    elif dot >= 0:
        decimals = len(value) - dot - 1
        decimal_sep = "." if decimals != 3 or value.count(".") > 1 and decimals != 3 else None
        if decimals == 3 and value.count(".") == 1:
            decimal_sep = None
    elif comma >= 0:
        decimals = len(value) - comma - 1
        decimal_sep = "," if decimals != 3 else None
    if decimal_sep:
        other = "," if decimal_sep == "." else "."
        value = value.replace(other, "")
        # If the selected separator occurs more than once, only the last is decimal.
        parts = value.split(decimal_sep)
        value = "".join(parts[:-1]) + "." + parts[-1]
    else:
        value = re.sub(r"[.,]", "", value)
    try:
        number = Decimal(sign + value)
        normalized = format(number.normalize(), "f")
        return normalized
    except InvalidOperation:
        return None


def quantities(text: str) -> list[dict[str, Any]]:
    result = []
    occupied: list[tuple[int, int]] = []
    for match in NUMBER_RE.finditer(text):
        raw = match.group(0)
        if any(match.start() < b and match.end() > a for a, b in occupied):
            continue
        canonical = canonical_number((match.group("sign") or "") + match.group("number"))
        if canonical is None:
            continue
        occupied.append(match.span())
        result.append({"raw": raw.strip(), "value": canonical, "unit": (match.group("unit") or "").lower(), "currency": match.group("currency") or "", "percent": bool(match.group("percent")), "start": match.start()})
    return result


UNIT_CONVERSIONS = {
    ("km", "mi"): lambda x: x * Decimal("0.621371"), ("mi", "km"): lambda x: x * Decimal("1.609344"),
    ("kg", "lb"): lambda x: x * Decimal("2.20462262"), ("lb", "kg"): lambda x: x / Decimal("2.20462262"),
    ("cm", "in"): lambda x: x / Decimal("2.54"), ("in", "cm"): lambda x: x * Decimal("2.54"),
    ("l", "gal"): lambda x: x / Decimal("3.785411784"), ("gal", "l"): lambda x: x * Decimal("3.785411784"),
}
UNIT_ALIASES = {"lbs": "lb", "hrs": "hour", "hr": "hour", "hours": "hour", "mins": "minute", "min": "minute", "minutes": "minute", "secs": "second", "sec": "second", "seconds": "second", "days": "day", "months": "month", "years": "year"}


def compare_quantities(source: str, target: str, *, allow_conversion: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_q = quantities(source)
    target_q = quantities(target)
    unmatched_target = list(target_q)
    missing: list[dict[str, Any]] = []
    for sq in source_q:
        match_index = next((i for i, tq in enumerate(unmatched_target) if tq["value"] == sq["value"] and tq["percent"] == sq["percent"] and tq["currency"] == sq["currency"]), None)
        if match_index is not None:
            tq = unmatched_target.pop(match_index)
            su = UNIT_ALIASES.get(sq["unit"], sq["unit"])
            tu = UNIT_ALIASES.get(tq["unit"], tq["unit"])
            if su and tu and su != tu and not allow_conversion:
                missing.append({**sq, "reason": f"unit changed to {tq['unit']} without conversion approval"})
            continue
        converted = False
        if allow_conversion and sq["unit"]:
            for i, tq in enumerate(unmatched_target):
                key = (UNIT_ALIASES.get(sq["unit"], sq["unit"]), UNIT_ALIASES.get(tq["unit"], tq["unit"]))
                converter = UNIT_CONVERSIONS.get(key)
                if converter:
                    try:
                        expected = converter(Decimal(sq["value"]))
                        actual = Decimal(tq["value"])
                        tolerance = max(Decimal("0.02") * abs(expected), Decimal("0.02"))
                        if abs(expected - actual) <= tolerance:
                            unmatched_target.pop(i)
                            converted = True
                            break
                    except (InvalidOperation, ZeroDivisionError):
                        pass
        if not converted:
            missing.append({**sq, "reason": "quantity not found in target"})
    return missing, unmatched_target


def bracket_issues(text: str) -> list[str]:
    pairs = {")": "(", "]": "[", "}": "{", "）": "（", "】": "【"}
    openings = set(pairs.values())
    stack: list[str] = []
    issues: list[str] = []
    # Ignore brackets inside protected URLs/tokens.
    cleaned = re.sub(r"https?://\S+|\{[^{}]+\}|\[[^\]]*\]\([^)]*\)", "", text)
    for char in cleaned:
        if char in openings:
            stack.append(char)
        elif char in pairs:
            if not stack or stack[-1] != pairs[char]:
                issues.append(f"unmatched closing {char}")
            else:
                stack.pop()
    issues.extend(f"unclosed {char}" for char in stack)
    if text.count('"') % 2:
        issues.append("odd number of straight double quotes")
    return issues


def pair_qa(source: Segment, target_text: str, *, source_locale: str = "auto", target_locale: str = "und", terms: Sequence[dict[str, Any]] = (), allow_conversion: bool = False, ui_limit: int | None = None) -> list[dict[str, Any]]:
    target = target_text
    defects: list[dict[str, Any]] = []
    sid = source.id
    if source.text.strip() and not target.strip():
        return [defect("completeness", "critical", "Target segment is empty.", segment_id=sid, source=source.text, target=target, recommendation="Supply the missing translation before release.")]
    source_tokens = collections.Counter(extract_tokens(source.text))
    target_tokens = collections.Counter(extract_tokens(target))
    if source_tokens != target_tokens:
        defects.append(defect("placeholder", "critical", "Protected tags, tokens, links, paths, or escapes differ.", segment_id=sid, source=source.text, target=target, evidence={"missing": list((source_tokens - target_tokens).elements()), "added": list((target_tokens - source_tokens).elements())}, recommendation="Restore every source token exactly once; do not translate variables or runtime markup."))
    source_tag_order = re.findall(r"</?([A-Za-z_][\w:.-]*)\b[^>]*>", source.text)
    target_tag_order = re.findall(r"</?([A-Za-z_][\w:.-]*)\b[^>]*>", target)
    if source_tag_order != target_tag_order:
        defects.append(defect("tag-order", "error", "Markup tag sequence differs.", segment_id=sid, source=source.text, target=target, evidence={"source": source_tag_order, "target": target_tag_order}))
    missing_numbers, added_numbers = compare_quantities(source.text, target, allow_conversion=allow_conversion)
    if missing_numbers:
        defects.append(defect("number-unit", "critical" if any(q.get("unit") or q.get("percent") or q.get("currency") for q in missing_numbers) else "error", "Source quantity, currency, percentage, or unit is missing/changed.", segment_id=sid, source=source.text, target=target, evidence=missing_numbers, recommendation="Preserve exact values and units, or document an approved conversion."))
    if added_numbers:
        defects.append(defect("added-number", "error", "Target introduces a number not found in the source.", segment_id=sid, source=source.text, target=target, evidence=added_numbers, recommendation="Remove unsupported quantities or document an approved locale conversion."))
    source_dates = DATE_RE.findall(source.text)
    target_dates = DATE_RE.findall(target.text if hasattr(target, "text") else target)
    if len(source_dates) != len(target_dates):
        defects.append(defect("date", "error", "Date count differs between source and target.", segment_id=sid, source=source.text, target=target, evidence={"source": source_dates, "target": target_dates}))
    elif source_dates != target_dates:
        defects.append(defect("date-format", "review", "Date representation changed; verify that locale formatting preserves the actual dates.", segment_id=sid, source=source.text, target=target, evidence={"source": source_dates, "target": target_dates}, objective=False))
    if len(TIME_RE.findall(source.text)) != len(TIME_RE.findall(target)):
        defects.append(defect("time", "error", "Time expression count differs.", segment_id=sid, source=source.text, target=target, evidence={"source": TIME_RE.findall(source.text), "target": TIME_RE.findall(target)}))
    normalized_source = re.sub(r"\s+", " ", source.text).strip().casefold()
    normalized_target = re.sub(r"\s+", " ", target).strip().casefold()
    if normalized_source == normalized_target and re.search(r"[^\W\d_]", source.text, re.UNICODE):
        defects.append(defect("untranslated", "warning", "Target is identical to alphabetic source text.", segment_id=sid, source=source.text, target=target, recommendation="Confirm intentional borrowing or translate the segment.", objective=False))
    for term in terms:
        source_term = str(term.get("source", ""))
        target_term = str(term.get("target", ""))
        flags = 0 if str(term.get("case_sensitive", "false")).lower() == "true" else re.IGNORECASE
        try:
            hit = re.search(r"(?<![\w])" + re.escape(source_term) + r"(?![\w])", source.text, flags)
            target_hit = re.search(r"(?<![\w])" + re.escape(target_term) + r"(?![\w])", target, flags)
        except re.error:
            continue
        if hit and not target_hit:
            severity = "error" if term.get("term_type") in {"name", "proper-noun", "product", "command"} else "warning"
            defects.append(defect("terminology", severity, "Approved target term was not found literally; inflection or valid context may explain the difference.", segment_id=sid, source=source.text, target=target, evidence={"source_term": source_term, "expected": target_term, "glossary_row": term.get("row")}, recommendation="Confirm the approved term or document a contextually valid inflection; do not blindly replace.", objective=False))
        forbidden = term.get("forbidden", "")
        forbidden_values = [x.strip() for x in re.split(r"[|;]", forbidden) if x.strip()]
        for bad in forbidden_values:
            if re.search(r"(?<![\w])" + re.escape(bad) + r"(?![\w])", target, re.IGNORECASE):
                defects.append(defect("forbidden-term", "error", "Target contains a prohibited variant.", segment_id=sid, source=source.text, target=target, evidence={"term": source_term, "forbidden": bad}, recommendation=f"Use the approved target: {target_term}"))
    issues = bracket_issues(target)
    if issues:
        defects.append(defect("punctuation", "warning", "Target has unbalanced punctuation.", segment_id=sid, source=source.text, target=target, evidence=issues))
    limit = ui_limit or source.metadata.get("max_length")
    if isinstance(limit, int) and len(target) > limit:
        defects.append(defect("ui-length", "error", "Target exceeds the declared UI/character limit.", segment_id=sid, source=source.text, target=target, evidence={"characters": len(target), "limit": limit}, recommendation="Shorten without dropping required meaning or obtain a layout change."))
    if len(source.text) >= 8 and len(target) > max(30, len(source.text) * 2.5):
        defects.append(defect("expansion", "review", "Target is substantially longer than the source; test clipping, wrapping, and safe areas.", segment_id=sid, source=source.text, target=target, evidence={"source_characters": len(source.text), "target_characters": len(target)}, objective=False))
    language = target_locale.split("-")[0].lower()
    rtl_languages = {"ar", "he", "fa", "ur", "ps", "sd", "ug", "yi"}
    if language in rtl_languages and re.search(r"[A-Za-z]", source.text) and not re.search(r"[\u0590-\u08ff]", target):
        defects.append(defect("directionality", "warning", "RTL target locale has no detected RTL-script text.", segment_id=sid, source=source.text, target=target, recommendation="Verify target language and bidirectional rendering; numbers and tokens alone may be intentional.", objective=False))
    return defects


def align_segments(source_segments: Sequence[Segment], target_segments: Sequence[Segment]) -> list[tuple[Segment, Segment | None, str]]:
    if len(source_segments) != len(target_segments):
        # Path-based alignment recovers reordered structured content before falling back to omissions.
        source_by_path = {s.metadata.get("path") or s.metadata.get("part"): s for s in source_segments if s.metadata.get("path") or s.metadata.get("part")}
        target_by_path = {s.metadata.get("path") or s.metadata.get("part"): s for s in target_segments if s.metadata.get("path") or s.metadata.get("part")}
        if source_by_path and len(source_by_path) == len(source_segments) and len(target_by_path) == len(target_segments):
            rows = []
            for key, source in source_by_path.items():
                target = target_by_path.get(key)
                rows.append((source, target, target.text if target else ""))
            for key, target in target_by_path.items():
                if key not in source_by_path:
                    rows.append((Segment(f"added-{key}", "", metadata={"path": key}), target, target.text))
            return rows
        rows = []
        for index in range(max(len(source_segments), len(target_segments))):
            source = source_segments[index] if index < len(source_segments) else Segment(f"missing-source-{index}", "")
            target = target_segments[index] if index < len(target_segments) else None
            rows.append((source, target, target.text if target else ""))
        return rows
    return [(source, target, target.text) for source, target in zip(source_segments, target_segments)]


def run_qa(source_segments: Sequence[Segment], target_segments: Sequence[Segment], *, source_locale: str = "auto", target_locale: str = "und", terms: Sequence[dict[str, Any]] = (), allow_conversion: bool = False, domain: str = "general", provider_report: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    rows = align_segments(source_segments, target_segments)
    defects: list[dict[str, Any]] = []
    aligned: list[dict[str, Any]] = []
    for source, target_segment, target in rows:
        aligned.append({"id": source.id, "source": source.text, "target": target, "target_id": target_segment.id if target_segment else None, "metadata": source.metadata})
        if not source.text and target:
            defects.append(defect("added-content", "review", "Target contains a segment not present in source alignment.", segment_id=target_segment.id if target_segment else None, target=target, objective=False))
            continue
        defects.extend(pair_qa(source, target, source_locale=source_locale, target_locale=target_locale, terms=terms, allow_conversion=allow_conversion))
    if len(source_segments) != len(target_segments):
        defects.append(defect("segment-count", "critical", "Source and target segment counts differ.", evidence={"source": len(source_segments), "target": len(target_segments)}, recommendation="Restore omitted/duplicated segments or use path-aligned review."))
    source_groups: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for source, _target, target in rows:
        normalized = re.sub(r"\s+", " ", source.text).strip().casefold()
        if normalized:
            source_groups[normalized].append((source.id, target))
    for _key, values in source_groups.items():
        variants = {re.sub(r"\s+", " ", target).strip() for _sid, target in values}
        if len(values) > 1 and len(variants) > 1:
            defects.append(defect("cross-asset-consistency", "review", "Repeated source segments have inconsistent targets.", evidence={"segment_ids": [sid for sid, _ in values], "variants": sorted(variants)[:20]}, recommendation="Confirm intentional context differences or harmonize recurring text.", objective=False))
    for report in provider_report:
        if report.get("review_required") or report.get("uncertainties"):
            defects.append(defect("provider-uncertainty", "review", "Translation provider flagged uncertainty or low confidence.", segment_id=report.get("id"), source=report.get("source", ""), target=report.get("target", ""), evidence={"confidence": report.get("confidence"), "uncertainties": report.get("uncertainties", [])}, recommendation="Route to a qualified bilingual reviewer.", objective=False))
    counts = collections.Counter(item["severity"] for item in defects)
    categories = collections.Counter(item["category"] for item in defects)
    high_risk = domain.lower() in {"legal", "medical", "pharmaceutical", "financial", "safety", "patent"}
    blocking = counts["critical"] + counts["error"]
    review = counts["review"] + counts["warning"]
    source_words = sum(count_words(s.text) for s in source_segments)
    return {
        "version": 1, "timestamp": utc_now(), "source_locale": source_locale, "target_locale": target_locale, "domain": domain,
        "summary": {
            "source_segments": len(source_segments), "target_segments": len(target_segments), "aligned_segments": len(rows), "source_words": source_words,
            "defects": len(defects), "by_severity": dict(sorted(counts.items())), "by_category": dict(sorted(categories.items())),
            "blocking_defects": blocking, "review_findings": review, "defect_density_per_1000_words": round(len(defects) / max(1, source_words) * 1000, 3),
            "human_review_required": high_risk or review > 0 or blocking > 0, "release_ready": blocking == 0 and not high_risk and review == 0,
        },
        "defects": defects, "aligned_segments": aligned,
        "limitations": "Deterministic QA verifies objective structure, quantities, tokens, terminology literals, and heuristics. It does not prove semantic accuracy, fluency, grammar, cultural safety, pronunciation, rendered layout, or legal/clinical correctness. Use qualified human or configured bilingual model review for those dimensions."
    }


def extract_terms(segments: Sequence[Segment], *, max_terms: int = 100) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    stop = {"the", "and", "for", "with", "this", "that", "from", "are", "was", "were", "you", "your", "our", "their", "have", "has", "not", "but", "can", "will", "shall", "into", "about"}

    def add(term: str, kind: str, segment: Segment) -> None:
        cleaned = term.strip(" \t\r\n.,;:!?()[]{}<>\"'")
        if len(cleaned) < 2 or len(cleaned) > 160:
            return
        key = cleaned.casefold()
        entry = candidates.setdefault(key, {"source": cleaned, "term_type": kind, "occurrences": 0, "contexts": [], "review_status": "candidate"})
        entry["occurrences"] += 1
        if len(entry["contexts"]) < 3:
            entry["contexts"].append({"segment_id": segment.id, "text": segment.text[:500]})

    for segment in segments:
        for match in re.finditer(r"\b[A-Z][A-Z0-9&.-]{1,29}\b", segment.text):
            add(match.group(0), "abbreviation-or-product", segment)
        for match in re.finditer(r"\b(?:[A-Z][\w'’&.-]+(?:\s+|$)){2,6}", segment.text):
            add(match.group(0), "name-or-domain-phrase", segment)
        for match in re.finditer(r"`([^`\n]{2,100})`|\b(?:/[a-z][\w/-]{1,80})\b", segment.text):
            add(match.group(1) or match.group(0), "command-or-identifier", segment)
        words = re.findall(r"\b[a-zA-Z][a-zA-Z-]{3,}\b", segment.text.lower())
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                phrase_words = words[i:i+n]
                if phrase_words[0] in stop or phrase_words[-1] in stop:
                    continue
                if n == 1 and phrase_words[0] in stop:
                    continue
                add(" ".join(phrase_words), "domain-phrase-candidate", segment)
    ranked = sorted(candidates.values(), key=lambda item: (-item["occurrences"], -len(item["source"]), item["source"].casefold()))
    # Repetitive unigram frequency is useful, but cap it so phrases and names remain visible.
    result = ranked[:max_terms]
    for item in result:
        item["confidence"] = round(min(0.95, 0.25 + math.log2(item["occurrences"] + 1) * 0.2), 3)
        item["notes"] = "Candidate only; approve definition, part of speech, locale, capitalization, gender, usage, forbidden variants, and target with a qualified reviewer."
    return result


def build_memory(rows: Sequence[dict[str, Any]], *, source_locale: str, target_locale: str, status: str = "draft", origin: str = "aligned") -> dict[str, Any]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        source = row.get("source", "")
        target = row.get("target", "")
        if not isinstance(source, str) or not isinstance(target, str) or not source.strip() or not target.strip():
            continue
        normalized_source = re.sub(r"\s+", " ", source).strip()
        normalized_target = re.sub(r"\s+", " ", target).strip()
        key = (normalized_source.casefold(), normalized_target)
        if key in unique:
            unique[key]["reuse_count"] += 1
            continue
        existing_targets = [r for r in unique.values() if r["source"].casefold() == normalized_source.casefold() and r["target"] != normalized_target]
        if existing_targets:
            conflicts.append({"source": normalized_source, "existing_targets": [r["target"] for r in existing_targets], "new_target": normalized_target, "row": index})
        unique[key] = {
            "id": row.get("id") or f"tm-{len(unique)+1:06d}", "source": normalized_source, "target": normalized_target,
            "source_locale": row.get("source_locale", source_locale), "target_locale": row.get("target_locale", target_locale),
            "context": row.get("context", ""), "metadata": row.get("metadata", {}), "status": row.get("status", status),
            "origin": origin, "created_at": row.get("created_at", utc_now()), "reuse_count": int(row.get("reuse_count", 1)),
        }
    records = list(unique.values())
    return {"version": 1, "source_locale": source_locale, "target_locale": target_locale, "segments": records, "summary": {"input_pairs": len(rows), "unique_pairs": len(records), "duplicates_removed": len(rows) - len(records), "conflicting_sources": len(conflicts)}, "conflicts": conflicts}


def clean_memory(memory: dict[str, Any], *, source_locale: str = "auto", target_locale: str = "und") -> dict[str, Any]:
    records = memory.get("segments", []) if isinstance(memory, dict) else memory
    if not isinstance(records, list):
        raise ToolError("invalid_memory", "Memory needs a segments array.")
    clean: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}
    for index, row in enumerate(records):
        if not isinstance(row, dict) or not isinstance(row.get("source"), str) or not isinstance(row.get("target"), str):
            quarantined.append({"index": index, "record": row, "reason": "invalid source/target structure"})
            continue
        source, target = row["source"].strip(), row["target"].strip()
        sid = str(row.get("id", f"row-{index+1}"))
        key = (re.sub(r"\s+", " ", source).casefold(), re.sub(r"\s+", " ", target))
        if key in seen:
            quarantined.append({"index": index, "id": sid, "reason": f"duplicate of {seen[key]}"})
            continue
        seen[key] = sid
        pair_defects = pair_qa(Segment(sid, source), target, source_locale=source_locale, target_locale=target_locale)
        wrong_language = False
        detected_source = detect_language(source)
        detected_target = detect_language(target)
        if source_locale != "auto" and detected_source["confidence"] >= 0.75 and not locale_matches(detected_source["language"], source_locale):
            wrong_language = True
        if target_locale != "und" and detected_target["confidence"] >= 0.75 and not locale_matches(detected_target["language"], target_locale):
            wrong_language = True
        if wrong_language:
            pair_defects.append(defect("wrong-language", "review", "Heuristic language detection disagrees with memory locale metadata.", segment_id=sid, source=source, target=target, evidence={"source_detection": detected_source, "target_detection": detected_target}, objective=False))
        stale = row.get("status") in {"stale", "rejected", "obsolete"} or bool(row.get("expires_at") and str(row["expires_at"]) <= utc_now())
        if stale:
            pair_defects.append(defect("stale", "warning", "Memory record is stale, rejected, obsolete, or expired.", segment_id=sid, source=source, target=target))
        blocking = [d for d in pair_defects if d["severity"] in {"critical", "error"} or d["category"] in {"wrong-language", "stale"}]
        if blocking or not source or not target:
            quarantined.append({"index": index, "id": sid, "record": row, "defects": pair_defects})
            defects.extend(pair_defects)
        else:
            clean.append({**row, "source": source, "target": target, "qa_status": "review" if pair_defects else "checked"})
            defects.extend(pair_defects)
    return {"version": 1, "source_locale": source_locale, "target_locale": target_locale, "segments": clean, "summary": {"input": len(records), "clean": len(clean), "quarantined": len(quarantined), "findings": len(defects)}, "quarantined": quarantined, "defects": defects}


def to_tmx(memory: dict[str, Any]) -> str:
    root = ET.Element("tmx", {"version": "1.4"})
    header = ET.SubElement(root, "header", {"creationtool": "ultimate-translation", "creationtoolversion": "1.0.0", "segtype": "sentence", "o-tmf": "ultimate-translation-json", "adminlang": "en", "srclang": str(memory.get("source_locale", "und")), "datatype": "PlainText"})
    body = ET.SubElement(root, "body")
    for row in memory.get("segments", []):
        tu = ET.SubElement(body, "tu", {"tuid": str(row.get("id", ""))})
        for lang_key, text_key in (("source_locale", "source"), ("target_locale", "target")):
            lang = str(row.get(lang_key, memory.get(lang_key, "und")))
            tuv = ET.SubElement(tu, "tuv", {"{http://www.w3.org/XML/1998/namespace}lang": lang})
            seg = ET.SubElement(tuv, "seg")
            seg.text = str(row.get(text_key, ""))
        if row.get("context"):
            note = ET.SubElement(tu, "note")
            note.text = str(row["context"])
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def to_xliff(package: dict[str, Any]) -> str:
    root = ET.Element("xliff", {"version": "1.2", "xmlns": "urn:oasis:names:tc:xliff:document:1.2"})
    file_node = ET.SubElement(root, "file", {"original": "source", "source-language": str(package.get("source_locale", "und")), "target-language": str(package.get("target_locale", "und")), "datatype": "plaintext"})
    body = ET.SubElement(file_node, "body")
    for row in package.get("segments", []):
        unit = ET.SubElement(body, "trans-unit", {"id": str(row.get("id", ""))})
        source = ET.SubElement(unit, "source")
        source.text = str(row.get("source", ""))
        target = ET.SubElement(unit, "target")
        target.text = str(row.get("target", ""))
        if row.get("context"):
            note = ET.SubElement(unit, "note", {"from": "context"})
            note.text = str(row["context"])
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def change_key(row: dict[str, Any]) -> str:
    """Stable delta key independent of source/target filename prefixes."""
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata", {}), dict) else {}
    if metadata.get("path"):
        return "path:" + str(metadata["path"])
    row_id = str(row.get("id", ""))
    sequence = re.search(r"-(\d{6})$", row_id)
    if metadata.get("part") and sequence:
        return "part:" + str(metadata["part"]) + "#" + sequence.group(1)
    if sequence:
        return "sequence:" + sequence.group(1)
    if row_id:
        return "id:" + row_id
    return "source:" + str(row.get("source", row.get("text", "")))


def change_delta(previous: Sequence[dict[str, Any]], current: Sequence[dict[str, Any]], memory: dict[str, Any] | None = None) -> dict[str, Any]:
    def key(row: dict[str, Any]) -> str:
        return change_key(row)
    old = {key(row): row for row in previous}
    new = {key(row): row for row in current}
    changes = []
    counts = collections.Counter()
    reusable = 0
    memory_rows = (memory or {}).get("segments", []) if isinstance(memory, dict) else []
    exact_memory = {re.sub(r"\s+", " ", row.get("source", "")).strip().casefold(): row for row in memory_rows if row.get("status", "approved") in {"approved", "reviewed"}}
    for item_key, row in new.items():
        old_row = old.get(item_key)
        source = str(row.get("source", row.get("text", "")))
        if old_row is None:
            status, impact = "new", "retranslate"
        elif str(old_row.get("source", old_row.get("text", ""))) == source:
            status, impact = "unchanged", "reuse"
        else:
            old_source = str(old_row.get("source", old_row.get("text", "")))
            ratio = similarity_ratio(old_source, source)
            status = "modified"
            impact = "review-existing" if ratio >= 0.85 else "retranslate"
        if impact == "reuse":
            reusable += 1
            target = old_row.get("target", "")
            reuse_source = "previous"
        elif source.strip().casefold() in exact_memory:
            target = exact_memory[source.strip().casefold()].get("target", "")
            impact = "memory-reuse"
            reuse_source = "translation-memory"
            reusable += 1
        else:
            target = ""
            reuse_source = None
        counts[status] += 1
        changes.append({"key": item_key, "status": status, "impact": impact, "previous_source": old_row.get("source", old_row.get("text", "")) if old_row else None, "source": source, "reused_target": target, "reuse_source": reuse_source})
    for item_key, row in old.items():
        if item_key not in new:
            counts["removed"] += 1
            changes.append({"key": item_key, "status": "removed", "impact": "remove-or-retire-translation", "previous_source": row.get("source", row.get("text", "")), "source": None})
    return {"version": 1, "summary": {**dict(counts), "current": len(new), "previous": len(previous), "reusable": reusable, "needs_provider_or_review": sum(1 for row in changes if row["impact"] in {"retranslate", "review-existing"})}, "changes": changes}


def similarity_ratio(a: str, b: str) -> float:
    # difflib is deterministic and appropriate for short localization segments.
    import difflib
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def accessibility_audit(text: str, *, kind: str = "text", target_locale: str = "und") -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if kind == "html":
        images = re.findall(r"<img\b[^>]*>", text, re.IGNORECASE)
        for image in images:
            alt = re.search(r"\balt\s*=\s*(['\"])(.*?)\1", image, re.IGNORECASE | re.DOTALL)
            if not alt:
                findings.append(defect("accessibility-alt", "error", "Image lacks an alt attribute.", evidence=image[:300], recommendation="Add concise alt text that communicates purpose; use alt=\"\" only for decorative images."))
        for link in re.finditer(r"<a\b[^>]*>(.*?)</a>", text, re.IGNORECASE | re.DOTALL):
            label = re.sub(r"<[^>]+>", "", link.group(1)).strip()
            if not label or label.lower() in {"click here", "here", "read more", "learn more"}:
                findings.append(defect("accessibility-link-purpose", "warning", "Link purpose is empty or ambiguous out of context.", evidence=label, recommendation="Use a concise destination- or action-specific label."))
        headings = [(int(m.group(1)), m.start()) for m in re.finditer(r"<h([1-6])\b[^>]*>", text, re.IGNORECASE)]
        for (previous, _p), (current, _c) in zip(headings, headings[1:]):
            if current > previous + 1:
                findings.append(defect("accessibility-heading-order", "warning", "Heading level skips downward.", evidence={"from": previous, "to": current}, recommendation="Use a logical heading hierarchy and reading order."))
        for element in re.finditer(r"<(?:input|select|textarea)\b[^>]*>", text, re.IGNORECASE):
            tag = element.group(0)
            if not re.search(r"(?:aria-label|aria-labelledby|id)\s*=", tag, re.IGNORECASE):
                findings.append(defect("accessibility-label", "warning", "Form control may lack a programmatic label.", evidence=tag[:300], recommendation="Associate a visible label or suitable accessible name."))
    metrics = readability(text)
    if metrics.get("supported") and metrics.get("flesch_kincaid_grade", 0) > 12:
        findings.append(defect("plain-language", "review", "Estimated reading grade exceeds 12; assess easy-language needs.", evidence=metrics, recommendation="Use shorter sentences and explicit steps without weakening required terms or safety.", objective=False))
    return {"version": 1, "locale": target_locale, "kind": kind, "findings": findings, "readability": metrics, "summary": {"findings": len(findings), "blocking": sum(f["severity"] in {"critical", "error"} for f in findings)}, "limitations": "Static checks do not replace screen-reader, keyboard, caption, sign-language, cognitive-accessibility, or rendered reading-order testing."}


def release_audit(manifest: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    required = ["locales", "assets", "approvals"]
    defects = []
    for key in required:
        if key not in manifest:
            defects.append(defect("release-manifest", "critical", f"Manifest lacks {key}."))
    locales = set(manifest.get("locales", []))
    if not locales:
        defects.append(defect("release-locales", "critical", "No requested locales are listed."))
    for asset in manifest.get("assets", []):
        if not isinstance(asset, dict):
            defects.append(defect("release-asset", "error", "Asset entry must be an object.", evidence=asset))
            continue
        path = asset.get("localized_path")
        if not path:
            defects.append(defect("release-missing-path", "critical", "Localized asset path is missing.", evidence=asset))
            continue
        candidate = (base_dir / path).resolve()
        try:
            candidate.relative_to(base_dir.resolve())
        except ValueError:
            defects.append(defect("release-path", "critical", "Asset path escapes the release root.", evidence=path))
            continue
        if not candidate.is_file():
            defects.append(defect("release-missing-file", "critical", "Localized asset does not exist.", evidence=path))
        asset_locales = set(asset.get("locales", locales))
        missing = locales - asset_locales
        if missing:
            defects.append(defect("release-locale-coverage", "error", "Asset does not declare every requested locale.", evidence={"path": path, "missing": sorted(missing)}))
        if asset.get("open_blocking_defects", 0):
            defects.append(defect("release-open-defects", "critical", "Asset has open blocking defects.", evidence={"path": path, "count": asset["open_blocking_defects"]}))
        if asset.get("accessibility_required", False) and not asset.get("accessibility_approved", False):
            defects.append(defect("release-accessibility", "critical", "Accessibility approval is missing.", evidence=path))
        if asset.get("fonts_required", False) and not asset.get("fonts_verified", False):
            defects.append(defect("release-fonts", "error", "Font coverage/licensing verification is missing.", evidence=path))
    approvals = manifest.get("approvals", {})
    for role in manifest.get("required_approvals", ["linguistic", "release"]):
        entry = approvals.get(role) if isinstance(approvals, dict) else None
        if not isinstance(entry, dict) or not entry.get("approved") or not entry.get("owner") or not entry.get("date"):
            defects.append(defect("release-approval", "critical", "Required approval is missing or incomplete.", evidence={"role": role, "entry": entry}))
    return {"version": 1, "timestamp": utc_now(), "summary": {"release_ready": not defects, "defects": len(defects), "locales": sorted(locales), "assets": len(manifest.get("assets", []))}, "defects": defects, "limitations": "Manifest checks establish evidence presence, not independent truth. Verify signatures, actual rendered builds, exports, and approval authority in the release system."}


def sampling_plan(records: Sequence[dict[str, Any]], *, sample_rate: float = 0.1, high_risk_rate: float = 1.0, seed: int = 417) -> dict[str, Any]:
    if not 0 < sample_rate <= 1 or not 0 < high_risk_rate <= 1:
        raise ToolError("invalid_option", "Sampling rates must be >0 and <=1.")
    rng = random.Random(seed)
    selected = []
    for row in records:
        risk = str(row.get("risk", "normal")).lower()
        rate = high_risk_rate if risk in {"high", "critical", "legal", "medical", "safety"} else sample_rate
        chosen = rng.random() < rate
        if chosen:
            selected.append({"id": row.get("id"), "risk": risk, "locale": row.get("locale"), "content_type": row.get("content_type"), "change_level": row.get("change_level"), "reviewer": row.get("reviewer"), "reason": "high-risk census" if rate == 1 else "seeded representative sample"})
    return {"version": 1, "seed": seed, "population": len(records), "sample_size": len(selected), "sample_rate": sample_rate, "high_risk_rate": high_risk_rate, "selected": selected, "escalation": "Expand to 100% of the affected locale/content stratum when critical defects occur or defect rate exceeds the configured threshold.", "limitations": "Seeded sampling is reproducible but representativeness depends on complete, accurately stratified input records."}
