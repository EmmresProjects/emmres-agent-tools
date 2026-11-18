#!/usr/bin/env python3
"""SSML-lite parser, validator and synthesis-plan builder.

Supported tags (all others are reported and skipped, inner text kept):
  <speak>, <break time="200ms"|strength="weak|medium|strong|x-strong|none"/>,
  <emphasis level="strong|moderate|reduced">, <prosody rate="..." pitch="..." volume="...">,
  <say-as interpret-as="cardinal|ordinal|characters|date|time|currency">,
  <sub alias="...">, <phoneme alphabet="ipa|x-sampa" ph="..."> (recorded, not injected),
  <voice name="..."> (recorded; actual voice chosen by --preset/--model)

Security rules: DOCTYPE/ENTITY rejected; scripts, event handlers, URLs and
data: URIs rejected; unknown tags dropped (text kept); attributes clamped.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

MAX_INPUT_CHARS = 20000

_BREAK_STRENGTH_MS = {"none": 0, "x-weak": 60, "weak": 120, "medium": 220,
                      "strong": 350, "x-strong": 500}
_RATE_KEYWORDS = {"x-slow": 0.62, "slow": 0.78, "medium": 1.0, "fast": 1.18, "x-fast": 1.38}
_PITCH_KEYWORDS = {"x-low": -8, "low": -4, "medium": 0, "high": 4, "x-high": 8}
_VOLUME_KEYWORDS = {"silent": -60, "x-soft": -12, "soft": -6, "medium": 0, "loud": 6, "x-loud": 12}

_SECURITY_REJECT = [
    (r"<!DOCTYPE", "DOCTYPE declarations are not allowed"),
    (r"<!ENTITY", "ENTITY declarations are not allowed"),
    (r"<\s*script", "script tags are not allowed"),
    (r"on[a-z]+\s*=", "event-handler attributes are not allowed"),
    (r"javascript\s*:", "javascript: URIs are not allowed"),
    (r"data\s*:", "data: URIs are not allowed"),
    (r"https?://", "external URLs are not allowed inside markup"),
    (r"<\?", "processing instructions are not allowed"),
]


class SsmlError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


def security_scan(source: str):
    problems = []
    for pattern, msg in _SECURITY_REJECT:
        if re.search(pattern, source, re.IGNORECASE):
            problems.append(msg)
    return problems


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_time_ms(value: str) -> int:
    v = (value or "").strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s)?", v)
    if not m:
        raise SsmlError(f"Invalid break time: {value!r}")
    num = float(m.group(1))
    unit = m.group(2) or "ms"
    ms = num * 1000 if unit == "s" else num
    return int(max(0, min(5000, ms)))


def _parse_rate(value: str) -> float:
    v = (value or "medium").strip().lower()
    if v in _RATE_KEYWORDS:
        return _RATE_KEYWORDS[v]
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)%", v)
    if m:
        pct = float(m.group(1))
        pct = max(-60.0, min(80.0, pct))
        return round(1.0 + pct / 100.0, 4) if abs(pct) <= 100 else pct / 100.0
    raise SsmlError(f"Invalid prosody rate: {value!r} (use x-slow|slow|medium|fast|x-fast or a percentage)")


def _parse_pitch(value: str) -> float:
    v = (value or "medium").strip().lower()
    if v in _PITCH_KEYWORDS:
        return float(_PITCH_KEYWORDS[v])
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*st", v)
    if m:
        return max(-12.0, min(12.0, float(m.group(1))))
    raise SsmlError(f"Invalid prosody pitch: {value!r} (use x-low|low|medium|high|x-high or semitones like +2st)")


def _parse_volume(value: str) -> float:
    v = (value or "medium").strip().lower()
    if v in _VOLUME_KEYWORDS:
        return float(_VOLUME_KEYWORDS[v])
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*db", v)
    if m:
        return max(-30.0, min(12.0, float(m.group(1))))
    raise SsmlError(f"Invalid prosody volume: {value!r} (use x-soft|soft|medium|loud|x-loud or dB like -3dB)")


_SAY_AS_INTERPRET = {"cardinal", "ordinal", "characters", "date", "time", "currency", "digits"}


def _say_as_words(interpret_as: str, text: str, text_module) -> str:
    inner = text.strip()
    if interpret_as == "characters":
        return " ".join(list(inner.replace(" ", "")))
    if interpret_as == "digits":
        return " ".join(ch for ch in inner if ch.isdigit())
    if interpret_as == "cardinal":
        return text_module.int_to_words(int(inner.replace(",", ""))) if inner.replace(",", "").isdigit() else inner
    if interpret_as == "ordinal":
        return text_module.int_to_ordinal(int(inner.replace(",", ""))) if inner.replace(",", "").isdigit() else inner
    if interpret_as == "date":
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", inner)
        if m:
            return text_module._format_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return inner
    if interpret_as == "time":
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", inner)
        if m:
            return text_module._time_sub(re.match(r"([01]?\d|2[0-3]):([0-5]\d)", inner)) if False else inner
        return inner
    if interpret_as == "currency":
        return text_module.verbalize_numbers(inner)
    return inner


def parse_ssml(source: str, text_module=None):
    """Parse SSML-lite. Returns (segments, warnings).

    segments: list of dicts {text, rate, pitch, volume_db, emphasis, pause_before_ms}
    """
    warnings = []
    if len(source) > MAX_INPUT_CHARS:
        raise SsmlError(f"Input exceeds {MAX_INPUT_CHARS} characters",
                        {"length": len(source), "max": MAX_INPUT_CHARS})
    problems = security_scan(source)
    if problems:
        raise SsmlError("Markup failed the security filter", {"problems": problems})

    stripped = source.strip()
    is_markup = stripped.startswith("<")
    if not is_markup:
        return ([_segment(stripped, 1.0, 0.0, 0.0, False, 0)], warnings)

    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        raise SsmlError(f"Markup is not well-formed XML: {exc}") from exc
    if _strip_ns(root.tag) != "speak":
        warnings.append(f"Root element is <{_strip_ns(root.tag)}>, expected <speak>; continuing")

    segments: list[dict] = []
    state = {"pending_pause_ms": 0}

    def emit_text(text, rate, pitch, volume_db, emphasis):
        text = re.sub(r"\s+", " ", text)
        if not text.strip():
            return
        seg = _segment(text, rate, pitch, volume_db, emphasis, state["pending_pause_ms"])
        state["pending_pause_ms"] = 0
        segments.append(seg)

    def walk(node, rate, pitch, volume_db, emphasis):
        tag = _strip_ns(node.tag)
        if tag == "break":
            ms = _parse_time_ms(node.get("time")) if node.get("time") is not None else \
                _BREAK_STRENGTH_MS.get((node.get("strength") or "medium").lower(), 220)
            state["pending_pause_ms"] += ms
            return
        if tag == "prosody":
            rate = _parse_rate(node.get("rate")) if node.get("rate") else rate
            pitch = _parse_pitch(node.get("pitch")) if node.get("pitch") else pitch
            volume_db = _parse_volume(node.get("volume")) if node.get("volume") else volume_db
        elif tag == "emphasis":
            level = (node.get("level") or "moderate").lower()
            emphasis = level != "reduced"
            if level == "strong":
                rate = rate * 0.94
            elif level == "reduced":
                rate = rate * 1.05
        elif tag == "say-as":
            interpret = (node.get("interpret-as") or "").lower()
            inner = "".join(node.itertext())
            if interpret in _SAY_AS_INTERPRET and text_module is not None:
                emit_text(_say_as_words(interpret, inner, text_module), rate, pitch, volume_db, emphasis)
            else:
                if interpret and interpret not in _SAY_AS_INTERPRET:
                    warnings.append(f"say-as interpret-as={interpret!r} not supported; text spoken literally")
                emit_text(inner, rate, pitch, volume_db, emphasis)
            return
        elif tag == "sub":
            alias = node.get("alias")
            if alias:
                emit_text(alias, rate, pitch, volume_db, emphasis)
            else:
                emit_text("".join(node.itertext()), rate, pitch, volume_db, emphasis)
                warnings.append("<sub> without alias; original text spoken")
            return
        elif tag == "phoneme":
            warnings.append("<phoneme> is recorded but this engine cannot inject raw IPA; "
                            "use the pronunciation dictionary for overrides")
            emit_text("".join(node.itertext()), rate, pitch, volume_db, emphasis)
            return
        elif tag == "voice":
            if node.get("name"):
                warnings.append(f"<voice name={node.get('name')!r}> ignored; voice comes from --preset/--model")
        elif tag in ("speak", "p", "s"):
            pass
        else:
            warnings.append(f"Unsupported tag <{tag}> skipped; its text is kept")

        if node.text and node.text.strip():
            emit_text(node.text, rate, pitch, volume_db, emphasis)
        for child in node:
            walk(child, rate, pitch, volume_db, emphasis)
            if child.tail and child.tail.strip():
                emit_text(child.tail, rate, pitch, volume_db, emphasis)

    walk(root, 1.0, 0.0, 0.0, False)
    if not segments:
        raise SsmlError("Markup produced no speakable text")
    return segments, warnings


def _segment(text, rate, pitch, volume_db, emphasis, pause_before_ms):
    return {"text": text.strip(), "rate": round(rate, 4), "pitch_st": round(pitch, 2),
            "volume_db": round(volume_db, 2), "emphasis": bool(emphasis),
            "pause_before_ms": int(pause_before_ms)}


def validate_ssml(source: str, text_module=None) -> dict:
    errors, warnings = [], []
    try:
        segments, warn = parse_ssml(source, text_module)
        warnings.extend(warn)
    except SsmlError as exc:
        return {"ok": False, "errors": [exc.message], "details": exc.details,
                "warnings": warnings, "segments": []}
    total_chars = sum(len(s["text"]) for s in segments)
    return {"ok": True, "errors": errors, "warnings": warnings,
            "segments": segments, "segment_count": len(segments),
            "speakable_chars": total_chars,
            "estimated_seconds": round(total_chars / 14.0, 2)}
