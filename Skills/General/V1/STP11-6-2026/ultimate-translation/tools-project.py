#!/usr/bin/env python3
"""Localization planning, locale rules, stateful governance, repositories, handoffs, and security."""
from __future__ import annotations

import collections
import csv
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools_core import Segment, ToolError, atomic_write, bounded_read, count_words, detect_language, fingerprint, read_json, utc_now, write_json
from tools_quality import defect, release_audit, sampling_plan, similarity_ratio


def plural_category(locale: str, number: int | float | str) -> str:
    lang = locale.lower().replace("_", "-").split("-")[0]
    raw = str(number)
    value = float(raw)
    absolute = abs(value)
    integer = int(absolute)
    is_integer = absolute.is_integer()
    decimals = raw.split(".", 1)[1] if "." in raw else ""
    visible_fraction = len(decimals)
    fraction_digits = int(decimals or 0)
    if lang in {"ja", "zh", "ko", "th", "vi", "id", "ms"}:
        return "other"
    if lang == "ar":
        if absolute == 0: return "zero"
        if absolute == 1: return "one"
        if absolute == 2: return "two"
        if is_integer and integer % 100 in range(3, 11): return "few"
        if is_integer and integer % 100 in range(11, 100): return "many"
        return "other"
    if lang in {"ru", "uk", "be", "sr", "hr", "bs"}:
        if is_integer and integer % 10 == 1 and integer % 100 != 11: return "one"
        if is_integer and integer % 10 in {2, 3, 4} and integer % 100 not in {12, 13, 14}: return "few"
        if is_integer and (integer % 10 == 0 or integer % 10 in {5, 6, 7, 8, 9} or integer % 100 in {11, 12, 13, 14}): return "many"
        return "other"
    if lang == "pl":
        if absolute == 1 and is_integer: return "one"
        if is_integer and integer % 10 in {2, 3, 4} and integer % 100 not in {12, 13, 14}: return "few"
        if is_integer: return "many"
        return "other"
    if lang in {"cs", "sk"}:
        if absolute == 1 and is_integer: return "one"
        if is_integer and integer in {2, 3, 4}: return "few"
        if not is_integer: return "many"
        return "other"
    if lang == "cy":
        return {0: "zero", 1: "one", 2: "two", 3: "few", 6: "many"}.get(integer, "other") if is_integer else "other"
    if lang in {"fr", "pt", "hi", "fa", "hy", "am"}:
        if absolute in {0, 1}: return "one"
        if lang == "fr" and is_integer and integer % 1000000 == 0 and integer != 0: return "many"
        return "other"
    if lang in {"es", "de", "it", "nl", "sv", "da", "no", "nb", "nn", "el", "en", "fi", "et", "bg", "sw", "tl"}:
        if lang in {"es", "it", "pt"} and is_integer and integer != 0 and integer % 1000000 == 0: return "many"
        return "one" if absolute == 1 and is_integer and visible_fraction == 0 else "other"
    if lang in {"ga"}:
        if absolute == 1: return "one"
        if absolute == 2: return "two"
        if is_integer and 3 <= integer <= 6: return "few"
        if is_integer and 7 <= integer <= 10: return "many"
        return "other"
    if lang in {"he", "iw"}:
        if absolute == 1 and is_integer: return "one"
        if absolute == 2 and is_integer: return "two"
        if is_integer and integer != 0 and integer % 10 == 0: return "many"
        return "other"
    if lang == "is":
        if is_integer and (integer % 10 == 1 and integer % 100 != 11 or integer % 10 in {2,3,4,5,6,7,8,9} and integer % 100 in {11,12,13,14}):
            return "one" if integer % 10 == 1 and integer % 100 != 11 else "other"
        return "other"
    return "one" if absolute == 1 and is_integer else "other"


def plural_test_suite(locale: str, values: Sequence[str] = ()) -> dict[str, Any]:
    samples = list(values) or ["0", "1", "2", "3", "4", "5", "6", "10", "11", "12", "14", "21", "22", "25", "100", "101", "102", "111", "112", "1000000", "0.5", "1.0", "1.5"]
    cases = [{"input": value, "category": plural_category(locale, value)} for value in samples]
    return {"locale": locale, "engine": "built-in deterministic CLDR-inspired cardinal rules for common locales", "cases": cases, "icu_example": "{count, plural, " + " ".join(f"{category} {{#{category}}}" for category in sorted({row['category'] for row in cases} | {"other"})) + "}", "requirements": ["Keep ICU plural/select/selectordinal syntax intact.", "Use locale-qualified linguistic review for gender/select and inflection agreement.", "Test decimals and actual formatted values in the runtime ICU library."], "limitations": "This is a planning/test oracle for common cardinal cases, not a complete CLDR implementation or runtime message formatter. Verify against the platform's pinned CLDR/ICU version, especially ordinal and less-common locales."}


def validate_message_format(text: str) -> dict[str, Any]:
    findings = []
    stack = []
    types = []
    for index, char in enumerate(text):
        if char == "'":
            continue
        if char == "{":
            stack.append(index)
        elif char == "}":
            if not stack:
                findings.append({"severity": "error", "type": "unmatched-close", "index": index})
            else:
                start = stack.pop()
                body = text[start+1:index]
                match = re.match(r"\s*[\w.]+\s*,\s*(plural|select|selectordinal|number|date|time)\b", body)
                if match:
                    types.append({"type": match.group(1), "start": start, "end": index})
    findings.extend({"severity": "error", "type": "unclosed-brace", "index": index} for index in stack)
    for item in types:
        if item["type"] in {"plural", "selectordinal", "select"}:
            body = text[item["start"]:item["end"]+1]
            if "other" not in body:
                findings.append({"severity": "error", "type": "missing-other", "message": f"ICU {item['type']} needs an other branch.", "start": item["start"]})
    return {"valid": not any(item["severity"] == "error" for item in findings), "message_types": types, "findings": findings, "limitations": "Structural checker plus the bounded renderer below; full ICU apostrophe, date/time skeleton, and platform-specific runtime validation still belong in the consuming framework."}


def ordinal_category(locale: str, number: int | float | str) -> str:
    lang = locale.lower().replace("_", "-").split("-")[0]
    value = float(str(number))
    if not value.is_integer(): return "other"
    n = abs(int(value))
    if lang == "en":
        if n % 10 == 1 and n % 100 != 11: return "one"
        if n % 10 == 2 and n % 100 != 12: return "two"
        if n % 10 == 3 and n % 100 != 13: return "few"
        return "other"
    if lang in {"ja", "zh", "ko", "th", "vi", "id", "ms"}: return "other"
    # Unknown ordinal systems fail closed rather than borrowing cardinal rules silently.
    raise ToolError("unsupported_ordinal_locale", f"Built-in ordinal rendering supports en and other-only locales, not {locale}.", hint="Use the consuming framework's pinned ICU/CLDR formatter for this locale.")


def _matching_brace(text: str, start: int) -> int:
    depth = 0; quoted = False; index = start
    while index < len(text):
        char = text[index]
        if char == "'":
            if index + 1 < len(text) and text[index+1] == "'": index += 2; continue
            quoted = not quoted
        elif not quoted:
            if char == "{": depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0: return index
        index += 1
    raise ToolError("invalid_message_format", "Unclosed ICU brace.", details=start)


def _top_level_commas(text: str) -> list[int]:
    commas = []; depth = 0; quoted = False; index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if index + 1 < len(text) and text[index+1] == "'": index += 2; continue
            quoted = not quoted
        elif not quoted:
            if char == "{": depth += 1
            elif char == "}": depth -= 1
            elif char == "," and depth == 0: commas.append(index)
        index += 1
    return commas


def _icu_branches(text: str) -> dict[str, str]:
    branches = {}; index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace(): index += 1
        if index >= len(text): break
        label_start = index
        while index < len(text) and not text[index].isspace() and text[index] != "{": index += 1
        label = text[label_start:index].strip()
        while index < len(text) and text[index].isspace(): index += 1
        if not label or index >= len(text) or text[index] != "{":
            raise ToolError("invalid_message_format", "ICU choice branches need a label followed by a braced message.", details=text[index:index+80])
        end = _matching_brace(text, index)
        if label in branches: raise ToolError("invalid_message_format", "Duplicate ICU branch label.", details=label)
        branches[label] = text[index+1:end]
        index = end + 1
    if "other" not in branches: raise ToolError("invalid_message_format", "ICU plural/select needs an other branch.")
    return branches


def render_icu_message(pattern: str, locale: str, arguments: dict[str, Any], *, _number: Any = None) -> str:
    """Bounded ICU MessageFormat subset: variables, plural, select, selectordinal, #, nesting."""
    output = []; index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "'":
            if index + 1 < len(pattern) and pattern[index+1] == "'": output.append("'"); index += 2; continue
            end = pattern.find("'", index + 1)
            if end < 0: raise ToolError("invalid_message_format", "Unclosed ICU quoted literal.")
            output.append(pattern[index+1:end]); index = end + 1; continue
        if char == "#" and _number is not None:
            output.append(str(_number)); index += 1; continue
        if char != "{":
            output.append(char); index += 1; continue
        end = _matching_brace(pattern, index)
        body = pattern[index+1:end]
        commas = _top_level_commas(body)
        if not commas:
            key = body.strip()
            if key not in arguments: raise ToolError("missing_message_argument", f"Missing ICU argument: {key}")
            output.append(str(arguments[key])); index = end + 1; continue
        if len(commas) < 2: raise ToolError("invalid_message_format", "Complex ICU argument needs variable, type, and format/branches.", details=body)
        variable = body[:commas[0]].strip(); kind = body[commas[0]+1:commas[1]].strip(); branch_text = body[commas[1]+1:]
        if variable not in arguments: raise ToolError("missing_message_argument", f"Missing ICU argument: {variable}")
        value = arguments[variable]
        if kind in {"plural", "select", "selectordinal"}:
            branches = _icu_branches(branch_text)
            exact = f"={value}"
            if exact in branches: selected = exact
            elif kind == "select": selected = str(value) if str(value) in branches else "other"
            else:
                try:
                    selected = plural_category(locale, value) if kind == "plural" else ordinal_category(locale, value)
                except (ValueError, TypeError) as exc: raise ToolError("invalid_message_argument", f"ICU {kind} argument {variable} must be numeric.") from exc
                if selected not in branches: selected = "other"
            output.append(render_icu_message(branches[selected], locale, arguments, _number=value if kind != "select" else None))
        elif kind in {"number", "date", "time"}:
            # Platform-neutral rendering deliberately leaves locale formatting to the host ICU runtime.
            output.append(str(value))
        else: raise ToolError("unsupported_message_type", f"Unsupported ICU argument type: {kind}")
        index = end + 1
    return "".join(output)


def icu_test_suite(pattern: str, locale: str) -> dict[str, Any]:
    tests = []; index = 0; seen = set()
    while index < len(pattern):
        if pattern[index] != "{": index += 1; continue
        end = _matching_brace(pattern, index); body = pattern[index+1:end]; commas = _top_level_commas(body)
        if len(commas) >= 2:
            variable = body[:commas[0]].strip(); kind = body[commas[0]+1:commas[1]].strip()
            if (variable, kind) not in seen and kind in {"plural", "select", "selectordinal"}:
                seen.add((variable, kind)); branches = _icu_branches(body[commas[1]+1:])
                for label, message in branches.items():
                    if label.startswith("="): value = label[1:]
                    elif kind == "select": value = label
                    else:
                        candidates = [0, 1, 2, 3, 4, 5, 10, 11, 12, 13, 21, 22, 23, 101, 1.5]
                        value = next((candidate for candidate in candidates if (plural_category(locale, candidate) if kind == "plural" else ordinal_category(locale, candidate)) == label), None)
                        if value is None and label == "other": value = 1.5 if kind == "plural" else 8
                    if value is not None:
                        tests.append({"variable": variable, "type": kind, "arguments": {variable: value}, "expected_branch": label, "expected_message_template": message, "assertion": "Render in the consuming ICU runtime and compare localized output, not just branch selection."})
        index = end + 1
    return {"locale": locale, "cases": tests, "renderer": "Built-in bounded subset available through locale message --values-json", "limitations": "Generated cases cover branch selection. Add gender agreement, inflection, plurals with decimals, bidirectional text, nested placeholders, and runtime-specific formatting tests with native reviewers."}


def locale_coverage(locales: Sequence[str], locale_data: dict[str, Any]) -> dict[str, Any]:
    plans = []
    known = locale_data.get("locales", {})
    for locale in locales:
        normalized = locale.replace("_", "-")
        language = normalized.split("-")[0].lower()
        data = known.get(normalized, known.get(language, {}))
        rtl = language in {"ar", "he", "fa", "ur", "ps", "sd", "ug", "yi"}
        plans.append({
            "locale": normalized, "language": language, "region": normalized.split("-")[1].upper() if "-" in normalized else None,
            "script": data.get("script", "Arab" if rtl else "Latn-or-verify"), "direction": "rtl" if rtl else data.get("direction", "ltr"),
            "fallbacks": data.get("fallbacks", [language, "en"] if language != "en" else ["en"]),
            "plural_categories": sorted({plural_category(normalized, value) for value in [0, 1, 2, 3, 5, 11, 21, 1.5]}),
            "date_convention": data.get("date", "VERIFY_CLDR"), "number_convention": data.get("numbers", "VERIFY_CLDR"),
            "fonts": data.get("fonts", ["Choose licensed font with verified glyph/shaping coverage"]),
            "input_requirements": data.get("input", ["Verify IME, keyboard, search, normalization, collation, and bidirectional editing" if rtl else "Verify IME, keyboard, search, normalization, and collation"]),
            "market_risks": data.get("risks", ["Locale-specific legal, cultural, formatting, and terminology review required"]),
            "plural_test_suite": plural_test_suite(normalized),
        })
    return {"version": 1, "locales": plans, "release_requirements": ["Localized date/number/currency/address/phone examples", "Font glyph and shaping evidence", "RTL mirroring and mixed-script tests where applicable", "Fallback and unsupported-locale behavior", "Native-linguist approval"], "limitations": "Built-in locale metadata is a compact planning preset, not a CLDR replacement. Confirm current CLDR rules and market requirements before release."}


def route_workflow(*, risk: str = "normal", content_type: str = "text", confidence: float | None = None, deadline_hours: float | None = None, domain: str = "general") -> dict[str, Any]:
    risk = risk.lower()
    domain = domain.lower()
    reasons = []
    if domain in {"legal", "medical", "pharmaceutical", "financial", "safety", "patent"} or risk in {"critical", "high"}:
        primary = "subject-specialist"
        reviewer = "domain-expert-and-native-reviewer"
        reasons.append("High-risk domain or content requires expert handling and independent review.")
    elif content_type in {"marketing", "slogan", "humor", "campaign", "transcreation"}:
        primary = "transcreator"
        reviewer = "native-brand-reviewer"
        reasons.append("Creative intent and cultural effect require transcreation rather than literal routing.")
    elif confidence is not None and confidence < 0.65:
        primary = "native-reviewer"
        reviewer = "senior-native-reviewer"
        reasons.append("Low confidence prevents unattended machine routing.")
    else:
        primary = "configured-translation-provider"
        reviewer = "native-reviewer-sample"
        reasons.append("General content can use bounded provider translation with objective QA and risk-based review.")
    if deadline_hours is not None and deadline_hours < 4:
        reasons.append("Deadline is under four hours; do not skip expert review—reduce scope or obtain an explicit deadline exception.")
    return {"risk": risk, "content_type": content_type, "domain": domain, "primary_route": primary, "review_route": reviewer, "confidence": confidence, "deadline_hours": deadline_hours, "reasons": reasons, "release_gate": "No unattended release for critical/high-risk, unresolved errors, uncertainty, or required approvals."}


def project_plan(scope: dict[str, Any], locales: Sequence[str], *, deadline: str | None = None, domain: str = "general", content_type: str = "text", vendors: Sequence[str] = ()) -> dict[str, Any]:
    words = int(scope.get("words", scope.get("scope", {}).get("words", 0)))
    segments = int(scope.get("segments", scope.get("scope", {}).get("segments", 0)))
    subtitle_minutes = float(scope.get("subtitle_minutes", 0))
    audio_hours = float(scope.get("audio_hours", 0))
    screenshots = int(scope.get("screenshots", 0))
    risk = "critical" if domain in {"legal", "medical", "pharmaceutical", "safety", "patent"} else "normal"
    route = route_workflow(risk=risk, content_type=content_type, domain=domain)
    locale_multiplier = max(1, len(locales))
    translation_hours = round(words * locale_multiplier / (900 if risk == "critical" else 1800) + audio_hours * 8 + subtitle_minutes / 60 * 4, 2)
    review_hours = round(words * locale_multiplier / (500 if risk == "critical" else 1000) + audio_hours * 4 + screenshots * 0.15, 2)
    phases = [
        {"id": "intake", "name": "Inventory, risk classification, scope and source freeze", "dependencies": [], "deliverables": ["Asset map", "Scope estimate", "Risk register", "Source fingerprints"]},
        {"id": "preparation", "name": "Locale, terminology, style, font and accessibility preparation", "dependencies": ["intake"], "deliverables": ["Locale coverage", "Approved glossary", "Style guide", "Review plan"]},
        {"id": "translation", "name": "Resumable translation/transcreation and objective QA", "dependencies": ["preparation"], "deliverables": ["Editable localized assets", "Translation reports", "Uncertainty queue"]},
        {"id": "review", "name": "Native, domain, accessibility and rendered review", "dependencies": ["translation"], "deliverables": ["Review decisions", "Defect corrections", "Rendered evidence"]},
        {"id": "release", "name": "Regression, approvals, packaging and release gate", "dependencies": ["review"], "deliverables": ["Regression report", "Approvals", "Release manifest", "Validated handoff"]},
    ]
    return {
        "version": 1, "created_at": utc_now(), "deadline": deadline, "locales": list(locales), "domain": domain, "content_type": content_type,
        "scope": {"words_per_locale": words, "total_words": words * locale_multiplier, "segments_per_locale": segments, "subtitle_minutes": subtitle_minutes, "audio_hours": audio_hours, "interface_strings": segments if content_type in {"software", "ui", "game"} else 0, "screenshots": screenshots, "estimated_translation_hours": translation_hours, "estimated_review_hours": review_hours, "complexity": "high" if risk == "critical" or audio_hours or screenshots else "normal"},
        "routing": route, "vendors": list(vendors), "phases": phases,
        "review_design": {"stages": [{"role": "native-linguist", "scope": "meaning, fluency, terminology, locale and intent"}, {"role": "domain-expert", "scope": "required for legal/medical/financial/safety/patent", "required": risk == "critical"}, {"role": "accessibility-reviewer", "scope": "alt text, captions, reading order, plain language and announcements"}, {"role": "product-release-owner", "scope": "rendered regression, exceptions and sign-off"}], "sampling": "100% high-risk and uncertain segments; seeded risk-stratified sample for general content", "escalation": "Any critical defect or threshold breach expands the affected stratum to 100% and blocks release.", "adjudication": "Record conflicting decisions, evidence, accountable owner, effective date, affected assets, and future-use guidance."},
        "acceptance_criteria": ["Every requested locale/asset mapped", "No missing IDs or broken tokens/numbers", "Approved terminology and style decisions recorded", "No unresolved blocking defects", "Rendered layout/font/accessibility checks complete", "Required accountable approvals present", "Editable outputs and audit reports returned"],
        "limitations": "Effort estimates are planning heuristics, not vendor quotes or guaranteed turnaround. Recalibrate using actual complexity, language, tooling, and reviewer capacity."
    }


def new_project(name: str, locales: Sequence[str], *, owner: str = "UNASSIGNED", assets: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    if not name.strip() or not locales:
        raise ToolError("invalid_project", "Project name and at least one locale are required.")
    return {"version": 1, "name": name.strip(), "created_at": utc_now(), "updated_at": utc_now(), "revision": 0, "owner": owner, "locales": list(locales), "assets": list(assets), "vendors": [], "blockers": [], "defects": [], "reviews": [], "decisions": [], "feedback": [], "risks": [], "approvals": [], "inventory": [], "events": [], "status": "planned"}


EVENT_SHAPES = {
    "asset.stage": ["asset_id", "stage"],
    "asset.upsert": ["asset"],
    "blocker.add": ["id", "asset_id", "description", "owner"],
    "blocker.resolve": ["id"],
    "defect.add": ["id", "asset_id", "severity", "category", "description"],
    "defect.resolve": ["id", "resolution"],
    "review.add": ["asset_id", "reviewer", "decision", "evidence"],
    "decision.add": ["id", "question", "decision", "rationale", "owner", "effective_date", "affected_assets"],
    "feedback.add": ["id", "source", "text"],
    "risk.add": ["id", "category", "description", "severity", "mitigation", "trigger", "owner"],
    "risk.update": ["id", "status", "mitigation"],
    "approval.add": ["role", "owner", "date", "scope", "approved"],
    "vendor.add": ["id", "name", "owner", "instructions"],
    "inventory.add": ["id", "path", "locale", "owner", "version", "status"],
    "status.set": ["status"],
}


def apply_project_event(project: dict[str, Any], event: dict[str, Any], *, expected_revision: int | None = None, confirm_sensitive: bool = False) -> dict[str, Any]:
    if project.get("version") != 1 or not isinstance(project.get("revision"), int):
        raise ToolError("invalid_project", "Unsupported or corrupt project state.")
    if expected_revision is not None and project["revision"] != expected_revision:
        raise ToolError("revision_conflict", "Project changed since it was read; retry with the latest revision.", details={"expected": expected_revision, "actual": project["revision"]})
    kind = event.get("type")
    if kind not in EVENT_SHAPES:
        raise ToolError("invalid_event", "Unknown project event type.", details={"type": kind, "supported": sorted(EVENT_SHAPES)})
    missing = [key for key in EVENT_SHAPES[kind] if key not in event]
    if missing:
        raise ToolError("invalid_event", "Project event lacks required fields.", details=missing)
    sensitive = kind == "approval.add" and event.get("approved") is True or kind == "asset.stage" and event.get("stage") in {"approved", "released", "published"}
    if sensitive and not confirm_sensitive:
        raise ToolError("approval_required", "Approvals, release stages, and publishing states require --confirm-sensitive after accountable human verification.")
    updated = json.loads(json.dumps(project))
    now = utc_now()
    if kind == "asset.stage":
        asset = next((row for row in updated["assets"] if row.get("id") == event["asset_id"]), None)
        if not asset:
            raise ToolError("invalid_event", "Unknown asset_id.", details=event["asset_id"])
        asset["stage"] = event["stage"]
        asset["owner"] = event.get("owner", asset.get("owner", updated["owner"]))
        asset["updated_at"] = now
    elif kind == "asset.upsert":
        asset = event["asset"]
        if not isinstance(asset, dict) or not asset.get("id"):
            raise ToolError("invalid_event", "asset.upsert asset must be an object with id.")
        existing = next((row for row in updated["assets"] if row.get("id") == asset["id"]), None)
        if existing:
            existing.update(asset)
        else:
            updated["assets"].append({**asset, "stage": asset.get("stage", "intake")})
    elif kind == "blocker.add":
        updated["blockers"].append({**{k: v for k, v in event.items() if k != "type"}, "status": "open", "created_at": now})
    elif kind == "blocker.resolve":
        row = next((row for row in updated["blockers"] if row.get("id") == event["id"]), None)
        if not row: raise ToolError("invalid_event", "Unknown blocker id.")
        row.update({"status": "resolved", "resolved_at": now})
    elif kind == "defect.add":
        updated["defects"].append({**{k: v for k, v in event.items() if k != "type"}, "status": "open", "created_at": now, "recurrence": int(event.get("recurrence", 1))})
    elif kind == "defect.resolve":
        row = next((row for row in updated["defects"] if row.get("id") == event["id"]), None)
        if not row: raise ToolError("invalid_event", "Unknown defect id.")
        row.update({"status": "resolved", "resolved_at": now, "resolution": event["resolution"]})
    elif kind == "review.add":
        if event["decision"] not in {"accept", "reject", "changes-requested", "escalate"}:
            raise ToolError("invalid_event", "Review decision must be accept, reject, changes-requested, or escalate.")
        updated["reviews"].append({**{k: v for k, v in event.items() if k != "type"}, "created_at": now, "comments": event.get("comments", [])})
    elif kind == "decision.add":
        updated["decisions"].append({**{k: v for k, v in event.items() if k != "type"}, "created_at": now, "future_use": event.get("future_use", "Apply to affected assets and future matching contexts unless superseded.")})
    elif kind == "feedback.add":
        updated["feedback"].append({**{k: v for k, v in event.items() if k != "type"}, "created_at": now, "status": "new"})
    elif kind in {"risk.add", "risk.update"}:
        if kind == "risk.add":
            updated["risks"].append({**{k: v for k, v in event.items() if k != "type"}, "status": event.get("status", "open"), "created_at": now})
        else:
            row = next((row for row in updated["risks"] if row.get("id") == event["id"]), None)
            if not row: raise ToolError("invalid_event", "Unknown risk id.")
            row.update({"status": event["status"], "mitigation": event["mitigation"], "updated_at": now})
    elif kind == "approval.add":
        if event.get("approved") and (not str(event.get("owner", "")).strip() or not str(event.get("date", "")).strip()):
            raise ToolError("invalid_event", "Approval needs accountable owner and date.")
        updated["approvals"].append({**{k: v for k, v in event.items() if k != "type"}, "recorded_at": now, "exceptions": event.get("exceptions", [])})
    elif kind == "vendor.add":
        updated["vendors"].append({k: v for k, v in event.items() if k != "type"})
    elif kind == "inventory.add":
        updated["inventory"].append({**{k: v for k, v in event.items() if k != "type"}, "last_updated": now})
    elif kind == "status.set":
        if event["status"] in {"released", "published", "approved"} and not confirm_sensitive:
            raise ToolError("approval_required", "Released/published/approved status requires --confirm-sensitive.")
        updated["status"] = event["status"]
    updated["revision"] += 1
    updated["updated_at"] = now
    updated["events"].append({"revision": updated["revision"], "at": now, "event": event})
    if updated["events"][-1]["event"].get("api_key"):
        raise ToolError("invalid_event", "Secrets must not be stored in project events.")
    return updated


def consolidate_feedback(feedback: Sequence[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for index, row in enumerate(feedback):
        normalized = re.sub(r"\s+", " ", str(row.get("text", ""))).strip().casefold()
        key = fingerprint([row.get("asset_id", ""), row.get("segment_id", ""), normalized])
        groups[key].append({**row, "_index": index})
    consolidated = []
    conflicts = []
    for rows in groups.values():
        decisions = {str(row.get("decision", "")).lower() for row in rows if row.get("decision")}
        conflict = len(decisions - {""}) > 1
        text = rows[0].get("text", "")
        category = categorize_feedback(text)
        item = {"asset_id": rows[0].get("asset_id"), "segment_id": rows[0].get("segment_id"), "text": text, "category": category, "sources": [row.get("source", "unknown") for row in rows], "duplicate_count": len(rows), "decisions": sorted(decisions), "conflict": conflict, "priority": prioritize_feedback(category, rows), "action": "Adjudicate conflicting reviewer decisions with evidence and an accountable owner." if conflict else "Validate against source, terminology, style, and affected assets; record the correction decision.", "evidence": [{"source": row.get("source"), "comment": row.get("comment", row.get("text")), "locale": row.get("locale")} for row in rows]}
        consolidated.append(item)
        if conflict: conflicts.append(item)
    priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    consolidated.sort(key=lambda row: (priority_order.get(row["priority"], 4), -row["duplicate_count"], str(row.get("segment_id"))))
    return {"summary": {"input": len(feedback), "unique": len(consolidated), "duplicates_removed": len(feedback)-len(consolidated), "conflicts": len(conflicts)}, "items": consolidated, "conflicts": conflicts}


def categorize_feedback(text: str) -> str:
    value = text.lower()
    categories = {"safety": ["safety", "danger", "warning", "dosage"], "legal": ["legal", "contract", "consent", "privacy"], "layout": ["clip", "truncat", "overlap", "font", "layout"], "placeholder": ["variable", "placeholder", "token", "broken tag"], "terminology": ["term", "glossary", "name"], "audio": ["audio", "pronunc", "dub", "voice"], "subtitle": ["subtitle", "caption", "sync", "reading speed"], "cultural": ["offensive", "cultural", "idiom", "tone"], "untranslated": ["untranslated", "english only"], "accuracy": ["wrong", "meaning", "mistranslat", "omission"]}
    for category, words in categories.items():
        if any(word in value for word in words): return category
    return "general"


def prioritize_feedback(category: str, rows: Sequence[dict[str, Any]]) -> str:
    if category in {"safety", "legal", "placeholder"}: return "critical"
    if category in {"accuracy", "untranslated", "layout"}: return "high"
    if len(rows) >= 3: return "high"
    return "normal"


def project_metrics(project: dict[str, Any]) -> dict[str, Any]:
    assets = project.get("assets", [])
    defects = project.get("defects", [])
    reviews = project.get("reviews", [])
    open_defects = [row for row in defects if row.get("status") == "open"]
    blocking = [row for row in open_defects if row.get("severity") in {"critical", "error", "high"}]
    words = sum(int(asset.get("words", 0)) for asset in assets)
    completed = [asset for asset in assets if asset.get("stage") in {"complete", "approved", "released"}]
    approved = [asset for asset in assets if asset.get("stage") in {"approved", "released"}]
    turnaround = []
    for asset in assets:
        if asset.get("created_at") and asset.get("completed_at"):
            try:
                turnaround.append((datetime.fromisoformat(asset["completed_at"].replace("Z", "+00:00")) - datetime.fromisoformat(asset["created_at"].replace("Z", "+00:00"))).total_seconds() / 3600)
            except (ValueError, TypeError): pass
    severity = collections.Counter(row.get("severity", "unknown") for row in defects)
    return {
        "project": project.get("name"), "revision": project.get("revision"), "status": project.get("status"),
        "volume": {"assets": len(assets), "words": words, "locales": len(project.get("locales", [])), "completed_assets": len(completed), "approved_assets": len(approved), "completion_percent": round(len(completed) / max(1, len(assets)) * 100, 2)},
        "quality": {"open_defects": len(open_defects), "blocking_defects": len(blocking), "defects_by_severity": dict(severity), "defect_density_per_1000_words": round(len(defects) / max(1, words) * 1000, 3), "rework_reviews": sum(row.get("decision") in {"reject", "changes-requested"} for row in reviews), "terminology_compliance": project.get("terminology_compliance", "NOT_MEASURED"), "translation_memory_reuse": project.get("memory_reuse", "NOT_MEASURED")},
        "turnaround_hours": {"average": round(sum(turnaround)/len(turnaround), 2) if turnaround else None, "samples": len(turnaround)},
        "review": {"reviews": len(reviews), "accepted": sum(row.get("decision") == "accept" for row in reviews), "pending_or_changes": sum(row.get("decision") in {"reject", "changes-requested", "escalate"} for row in reviews)},
        "blockers": [row for row in project.get("blockers", []) if row.get("status") == "open"], "open_risks": [row for row in project.get("risks", []) if row.get("status") == "open"],
        "quality_risk": "Release blocked while blocking defects, open high risks, required reviews, or approvals remain unresolved.", "approvals": project.get("approvals", []),
    }


SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,255}\b")),
    ("generic-secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|access[_-]?token|password)\b\s*[:=]\s*(['\"]?)([^\s'\";]{8,})\1")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]


def secret_scan(text: str, *, include_email: bool = True) -> list[dict[str, Any]]:
    findings = []
    for name, pattern in SECRET_PATTERNS:
        if name == "email" and not include_email: continue
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            value = match.group(0)
            findings.append({"type": name, "line": line, "start": match.start(), "end": match.end(), "fingerprint": fingerprint(value)[:16], "redaction": f"[REDACTED:{name}]", "severity": "critical" if name not in {"email"} else "review"})
    return findings


def redact_text(text: str, *, include_email: bool = False) -> tuple[str, list[dict[str, Any]]]:
    findings = secret_scan(text, include_email=include_email)
    output = text
    for finding in sorted(findings, key=lambda row: row["start"], reverse=True):
        output = output[:finding["start"]] + finding["redaction"] + output[finding["end"]:]
    return output, findings


def scan_repository(root: Path, config: dict[str, Any], *, include_patterns: Sequence[str] = ()) -> dict[str, Any]:
    if not root.is_dir(): raise ToolError("input_not_found", f"Repository directory does not exist: {root}")
    files = []
    hardcoded = []
    secrets = []
    resource_files = []
    skipped = []
    max_files = int(config["limits"]["max_repository_files"])
    max_bytes = int(config["limits"]["max_input_bytes"])
    source_ext = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".swift", ".cs", ".cpp", ".c", ".h", ".go", ".rb", ".php"}
    resource_ext = {".json", ".arb", ".yaml", ".yml", ".po", ".properties", ".strings", ".resx", ".xml"}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in {"node_modules", "vendor", "build", "dist", "target", "__pycache__"})
        for name in sorted(names):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if include_patterns and not any(fnmatch.fnmatch(relative, pattern) for pattern in include_patterns): continue
            if path.is_symlink() or not path.is_file():
                skipped.append({"path": relative, "reason": "symlink-or-not-regular"})
                continue
            if len(files) >= max_files: raise ToolError("too_many_repository_files", "Repository exceeds max_repository_files.")
            size = path.stat().st_size
            if size > max_bytes:
                skipped.append({"path": relative, "reason": "file-too-large", "bytes": size})
                continue
            files.append({"path": relative, "bytes": size, "sha256": fingerprint(bounded_read(path, max_bytes))})
            suffix = path.suffix.lower()
            if suffix not in source_ext | resource_ext | {".md", ".txt", ".html"}: continue
            try: text = bounded_read(path, max_bytes).decode("utf-8")
            except UnicodeDecodeError:
                skipped.append({"path": relative, "reason": "not-utf8"})
                continue
            for finding in secret_scan(text, include_email=False): secrets.append({"path": relative, **{k: v for k, v in finding.items() if k not in {"start", "end"}}})
            if suffix in resource_ext and (re.search(r"(?:^|[/_.-])(?:en|en-us|source|base|messages|strings|locales?|i18n|l10n)(?:[/_.-]|$)", relative, re.IGNORECASE) or suffix in {".po", ".strings", ".resx", ".arb"}):
                resource_files.append(relative)
            if suffix in source_ext:
                for match in re.finditer(r"(?m)(?<![\w])(?:setText|setTitle|label|title|message|placeholder|tooltip|Text|Button)\s*\(\s*([\"'])([^\n\"']{3,160})\1", text):
                    value = match.group(2)
                    if re.search(r"[A-Za-z]{2}\s+[A-Za-z]{2}", value):
                        hardcoded.append({"path": relative, "line": text.count("\n", 0, match.start())+1, "text": value, "recommendation": "Move user-facing text to a reviewed resource with stable ID and context; verify before changing code."})
    duplicates = []
    missing_keys = []
    catalogs = {}
    for relative in resource_files:
        if not relative.endswith(".json"): continue
        try:
            value = json.loads((root / relative).read_text(encoding="utf-8"))
            if isinstance(value, dict) and all(not isinstance(v, (dict, list)) for v in value.values()): catalogs[relative] = set(value)
        except (OSError, json.JSONDecodeError): pass
    all_keys = collections.Counter(key for keys in catalogs.values() for key in keys)
    for relative, keys in catalogs.items():
        for key in sorted(set(all_keys) - keys): missing_keys.append({"catalog": relative, "key": key, "note": "Only flat JSON catalogs are compared; verify intentional locale differences."})
    return {"version": 1, "root_name": root.name, "summary": {"files": len(files), "resource_files": len(resource_files), "hardcoded_candidates": len(hardcoded), "secret_findings": len(secrets), "missing_key_candidates": len(missing_keys), "skipped": len(skipped)}, "files": files, "resource_files": resource_files, "hardcoded_candidates": hardcoded, "missing_key_candidates": missing_keys, "duplicate_string_candidates": duplicates, "secrets": secrets, "skipped": skipped, "limitations": "Static regex scanning does not prove every hard-coded string, secret, dynamic key, or framework resource. Never commit findings containing sensitive content; rotate exposed credentials through the appropriate security process."}


def build_handoff(output: Path, *, project: dict[str, Any] | None, files: Sequence[Path], glossary: Path | None, style_guide: Path | None, instructions: str, acceptance: Sequence[str], archive: bool = False) -> dict[str, Any]:
    if output.suffix.lower() != ".zip": raise ToolError("invalid_output", "Handoff output must use .zip.")
    if output.exists(): raise ToolError("output_exists", f"Handoff already exists: {output}")
    entries: list[tuple[str, bytes]] = []
    file_map = []
    seen = set()
    def add(name: str, data: bytes, role: str) -> None:
        if name in seen: raise ToolError("duplicate_handoff_entry", f"Duplicate handoff name: {name}")
        if name.startswith(("/", "\\")) or ".." in Path(name).parts or "\\" in name: raise ToolError("invalid_handoff_path", "Unsafe handoff entry path.")
        seen.add(name); entries.append((name, data)); file_map.append({"path": name, "role": role, "bytes": len(data), "sha256": fingerprint(data)})
    for path in files:
        if not path.is_file(): raise ToolError("input_not_found", f"Handoff file does not exist: {path}")
        if path.suffix.lower() in {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}: raise ToolError("nested_archive_forbidden", f"Nested archives are not allowed in handoffs: {path.name}")
        raw = bounded_read(path, 100_000_000)
        add("assets/" + path.name, raw, "localized-or-source-asset")
    if glossary:
        if not glossary.is_file(): raise ToolError("input_not_found", "Glossary does not exist.")
        add("terminology/" + glossary.name, bounded_read(glossary, 20_000_000), "glossary")
    if style_guide:
        if not style_guide.is_file(): raise ToolError("input_not_found", "Style guide does not exist.")
        add("guidance/" + style_guide.name, bounded_read(style_guide, 20_000_000), "style-guide")
    if project:
        add("project.json", (json.dumps(project, ensure_ascii=False, indent=2)+"\n").encode("utf-8"), "project-state")
    manifest = {"version": 1, "type": "approved-archive" if archive else "localization-handoff", "created_at": utc_now(), "instructions": instructions, "acceptance_criteria": list(acceptance), "file_map": file_map, "return_validation": ["Run CRC/integrity check", "Verify every mapped file and exact case-sensitive path", "Preserve stable IDs and return editable formats", "Include QA, decisions, exceptions and reviewer notes", "No credentials or nested archives"]}
    add("handoff.json", (json.dumps(manifest, ensure_ascii=False, indent=2)+"\n").encode("utf-8"), "operational-manifest")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive_file:
        for name, data in entries: archive_file.writestr(name, data)
        bad = archive_file.testzip()
        if bad: raise ToolError("handoff_validation_failed", f"CRC validation failed for {bad}")
    atomic_write(output, stream.getvalue(), overwrite=False)
    with zipfile.ZipFile(output) as check:
        if check.testzip(): raise ToolError("handoff_validation_failed", "Written handoff failed CRC validation.")
        names = check.namelist()
    return {"output": str(output), "type": manifest["type"], "entries": len(names), "files": file_map, "validated": True, "nested_archives": False}


def style_guide_markdown(locale: str, *, brand: dict[str, Any] | None = None, terms: Sequence[dict[str, Any]] = ()) -> str:
    brand = brand or {}
    lines = [f"# Localization Style Guide — {locale}", "", "## Scope and decision authority", "", f"Applies to `{locale}`. Native-linguist and product owners resolve preferences; legal, medical, financial, accessibility, and safety owners control their domains. Record contested decisions with rationale, evidence, owner, affected assets, effective date, and future-use guidance.", "", "## Voice and register", "", f"- Brand voice: {brand.get('voice', 'Clear, respectful, concise, and audience-appropriate; define approved brand traits before creative work.')}", f"- Default register: {brand.get('register', 'Natural and professional unless channel or speaker context requires otherwise.')}", "- Preserve character identity, humor intent, politeness, ambiguity, inclusivity, and emotional force; document unavoidable trade-offs.", "- Do not introduce claims, stereotypes, forced standardization, or culturally unsupported assumptions.", "", "## Terminology and names", "", "- Use approved terminology without blindly replacing valid inflections. Record prohibited variants and context exceptions.", "- Preserve people, places, organizations, fictional terms, titles, honorifics, pronunciation notes, and defined terms consistently.", "", "## Mechanics", "", "- Protect variables, ICU syntax, tags, links, commands, paths, escapes, keyboard shortcuts, and cross-references.", "- Preserve numbers, negation, units, dosage, warnings, legal distinctions, citations, and claim dependencies. Convert units only with approval and validation.", "- Follow current locale CLDR conventions for dates, numbers, currencies, plural/gender agreement, capitalization, punctuation, sorting, and directionality.", "- Test UI expansion, wrapping, truncation, accessible names, alt text, captions, reading order, and target font shaping.", "", "## Channel rules", "", "- UI: concise action-led labels; respect declared character limits and accessibility announcements.", "- Marketing: preserve approved claims; supply rationale and back-translation for transcreated variants.", "- Subtitles/dubbing: preserve timing, speaker, sound cues, safe areas, readability, character voice, and production IDs.", "- Easy language: short explicit steps and common words without weakening required terminology or safety.", "", "## Approved term snapshot", "", "| Source | Approved target | Type | Notes |", "|---|---|---|---|"]
    for term in terms:
        source = str(term.get("source", "")).replace("|", "\\|")
        target = str(term.get("target", "")).replace("|", "\\|")
        notes = str(term.get("notes", "")).replace("|", "\\|")
        lines.append(f"| {source} | {target} | {term.get('term_type', 'term')} | {notes} |")
    lines += ["", "## Review evidence", "", "Retain source/target alignment, glossary hits, uncertainty, defects, rendered screenshots or media notes, reviewer decisions, exceptions, and accountable sign-off.", ""]
    return "\n".join(lines)


def narrative_audit(script: dict[str, Any]) -> dict[str, Any]:
    """Validate branching narrative references, variables, continuity metadata, and reachability."""
    nodes = script.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ToolError("invalid_narrative", "Narrative script needs a non-empty nodes array.")
    by_id: dict[str, dict[str, Any]] = {}
    defects: list[dict[str, Any]] = []
    edges: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            defects.append(defect("narrative-id", "critical", "Every narrative node needs a non-empty string id.", evidence={"index": index}))
            continue
        node_id = node["id"]
        if node_id in by_id:
            defects.append(defect("narrative-duplicate-id", "critical", "Duplicate narrative node id.", segment_id=node_id))
            continue
        by_id[node_id] = node
        targets = []
        if isinstance(node.get("next"), str): targets.append(node["next"])
        choices = node.get("choices", [])
        if not isinstance(choices, list):
            defects.append(defect("narrative-choices", "critical", "choices must be an array.", segment_id=node_id))
            choices = []
        for choice_index, choice in enumerate(choices):
            if not isinstance(choice, dict) or not isinstance(choice.get("next"), str):
                defects.append(defect("narrative-choice", "critical", "Each choice needs a next node id.", segment_id=node_id, evidence={"choice": choice_index}))
                continue
            targets.append(choice["next"])
            if not str(choice.get("label", "")).strip():
                defects.append(defect("narrative-choice-label", "error", "Choice has no localizable label.", segment_id=node_id, evidence={"choice": choice_index}))
        edges[node_id] = targets
        combined = json.dumps(node, ensure_ascii=False)
        declared = set(node.get("variables_declared", script.get("variables", [])))
        used = set(re.findall(r"\{\{?([A-Za-z_][A-Za-z0-9_.-]*)\}?\}?", combined))
        undeclared = used - declared
        if undeclared and node.get("strict_variables", script.get("strict_variables", True)):
            defects.append(defect("narrative-variable", "error", "Narrative node uses undeclared variables.", segment_id=node_id, evidence=sorted(undeclared)))
        for field in ("speaker", "objective", "chronology", "pronouns", "consequences"):
            if node.get("requires_continuity", False) and field not in node:
                defects.append(defect("narrative-continuity-metadata", "review", "Continuity-critical node lacks metadata needed for review.", segment_id=node_id, evidence=field, objective=False))
    for node_id, targets in edges.items():
        for target in targets:
            if target not in by_id:
                defects.append(defect("narrative-broken-reference", "critical", "Narrative edge points to an unknown node.", segment_id=node_id, evidence=target))
    start = script.get("start", nodes[0].get("id") if nodes and isinstance(nodes[0], dict) else None)
    reachable: set[str] = set()
    pending = [start] if isinstance(start, str) else []
    while pending:
        current = pending.pop()
        if current in reachable or current not in by_id: continue
        reachable.add(current)
        pending.extend(edges.get(current, []))
    unreachable = sorted(set(by_id) - reachable)
    if unreachable:
        defects.append(defect("narrative-unreachable", "warning", "Nodes are unreachable from the declared start; verify alternate entry points or progression blockers.", evidence=unreachable))
    names = script.get("canonical_names", {})
    if isinstance(names, dict):
        for node_id, node in by_id.items():
            text = str(node.get("text", "")) + " " + " ".join(str(c.get("label", "")) for c in node.get("choices", []) if isinstance(c, dict))
            for name, approved in names.items():
                if name in text and isinstance(approved, str) and approved not in text:
                    defects.append(defect("narrative-name-variant", "review", "Canonical name is present without its approved localized form; inspect possible variant.", segment_id=node_id, evidence={"source_name": name, "approved": approved}, objective=False))
    return {"version": 1, "summary": {"nodes": len(by_id), "edges": sum(len(v) for v in edges.values()), "reachable": len(reachable), "unreachable": len(unreachable), "defects": len(defects), "release_ready": not any(d["severity"] in {"critical", "error"} for d in defects)}, "defects": defects, "reachable_nodes": sorted(reachable), "limitations": "Graph checks verify references and declared metadata, not story logic, pronoun correctness, chronology, emotional continuity, or consequence semantics. Use narrative-design and native-language review, optionally through adapt narrative-review."}


def build_static_site_package(source_dir: Path, localized: Sequence[tuple[str, Path]], output_dir: Path, *, default_locale: str) -> dict[str, Any]:
    if not source_dir.is_dir(): raise ToolError("input_not_found", "Static-site source directory does not exist.")
    if output_dir.exists() and any(output_dir.iterdir()): raise ToolError("output_exists", "Static-site output directory must be empty or absent.")
    if not localized: raise ToolError("invalid_option", "At least one locale=path mapping is required.")
    locales = [default_locale] + [locale for locale, _path in localized if locale != default_locale]
    if len(set(locales)) != len(locales): raise ToolError("invalid_option", "Duplicate locale in static-site package.")
    output_dir.mkdir(parents=True, exist_ok=True)
    route_map = []
    hreflang = []
    copied = []

    def safe_files(root: Path) -> list[Path]:
        result = []
        for base, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "build", "dist"}]
            for name in names:
                path = Path(base) / name
                if path.is_file() and not path.is_symlink() and path.suffix.lower() not in {".zip", ".tar", ".gz", ".7z", ".rar"}: result.append(path)
        return result

    sources = safe_files(source_dir)
    if not sources: raise ToolError("invalid_input", "No regular site files found in source directory.")
    for locale, root in [(default_locale, source_dir), *localized]:
        if not root.is_dir(): raise ToolError("input_not_found", f"Localized site directory does not exist: {root}")
        for path in safe_files(root):
            relative = path.relative_to(root)
            if ".." in relative.parts: raise ToolError("invalid_path", "Unsafe static-site path.")
            destination = output_dir / locale / relative
            atomic_write(destination, path.read_bytes(), overwrite=False)
            copied.append({"locale": locale, "path": f"{locale}/{relative.as_posix()}", "sha256": fingerprint(path.read_bytes())})
            if path.suffix.lower() in {".html", ".htm"}:
                route = f"/{locale}/{relative.as_posix()}"
                route_map.append({"locale": locale, "source": relative.as_posix(), "route": route, "fallback_locale": default_locale if locale != default_locale else None})
                hreflang.append({"route": route, "links": [{"locale": other, "href": f"/{other}/{relative.as_posix()}", "hreflang": other} for other in locales] + [{"locale": "x-default", "href": f"/{default_locale}/{relative.as_posix()}", "hreflang": "x-default"}]})
    plan = {"version": 1, "default_locale": default_locale, "locales": locales, "fallback_rules": [{"from": locale, "to": default_locale, "condition": "Only after verifying an equivalent route exists; never silently mix locales within a component."} for locale in locales if locale != default_locale], "routes": route_map, "hreflang": hreflang, "assets": copied, "deployment_requires_approval": True, "limitations": "This creates a reviewable static handoff only. It does not crawl a CMS, publish, verify runtime routing, or prove all locale counterparts exist. Add generated hreflang tags through the site template and validate deployment in staging."}
    atomic_write(output_dir / "localization-routes.json", json.dumps(plan, ensure_ascii=False, indent=2)+"\n", overwrite=False)
    return plan


def secure_cleanup(paths: Sequence[Path], *, approve_delete: bool) -> dict[str, Any]:
    if not approve_delete: raise ToolError("approval_required", "Secure deletion requires --approve-delete. Review the exact file list first.")
    removed, errors = [], []
    for path in paths:
        try:
            if not path.exists() or not path.is_file() or path.is_symlink(): raise ToolError("invalid_cleanup_path", "Cleanup accepts only existing regular non-symlink files.", details=str(path))
            # Ordinary deletion is explicit; do not claim guaranteed secure erasure on modern storage.
            size = path.stat().st_size
            path.unlink()
            removed.append({"path": str(path), "bytes": size})
        except Exception as exc: errors.append({"path": str(path), "error": str(exc)})
    return {"removed": removed, "errors": errors, "count": len(removed), "limitations": "Files were unlinked after explicit approval. This is not guaranteed forensic secure erasure on SSDs, copy-on-write filesystems, backups, or synchronized storage; use platform-managed retention and cryptographic erasure where required."}
