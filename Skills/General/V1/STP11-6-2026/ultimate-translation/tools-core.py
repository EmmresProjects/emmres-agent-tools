#!/usr/bin/env python3
"""Shared models, safe I/O, segmentation, configuration, and translation engine."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import signal
import sys
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

VERSION = "1.0.0"
TOOL_NAME = "ultimate-translation"
ENV_PREFIX = "ULTIMATE_TRANSLATION_"


class ToolError(Exception):
    """A safe, structured, user-actionable failure."""

    def __init__(self, code: str, message: str, *, details: Any = None, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.hint = hint

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            result["details"] = self.details
        if self.hint:
            result["hint"] = self.hint
        return result


@dataclass
class Segment:
    id: str
    text: str
    context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Translation:
    id: str
    text: str
    confidence: float | None = None
    uncertainties: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    back_translation: str = ""
    provider: str = ""
    reused: bool = False
    review_required: bool = False


@dataclass
class Document:
    kind: str
    segments: list[Segment]
    renderer: Any
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self, translations: dict[str, str]) -> bytes:
        return self.renderer(translations)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(*parts: Any) -> str:
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:20]


def fingerprint(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()


def ensure_relative_reference(value: str) -> None:
    """Validate future-skill-root references used by configuration and manifests."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ToolError("invalid_reference", "Resource references must be non-empty forward-slash paths.", details=value)
    p = Path(value)
    if p.is_absolute() or re.match(r"^[A-Za-z]:", value) or ".." in p.parts:
        raise ToolError("invalid_reference", "Absolute paths and parent traversal are not allowed in resource references.", details=value)


def resolve_user_path(value: str, *, must_exist: bool = False, kind: str = "file") -> Path:
    if not value:
        raise ToolError("missing_path", "An input or output path is required.")
    path = Path(value).expanduser()
    if must_exist:
        if not path.exists():
            raise ToolError("input_not_found", f"{kind.capitalize()} does not exist: {value}")
        if kind == "file" and not path.is_file():
            raise ToolError("invalid_input", f"Expected a file: {value}")
        if kind == "directory" and not path.is_dir():
            raise ToolError("invalid_input", f"Expected a directory: {value}")
    return path


def bounded_read(path: Path, max_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolError("input_unreadable", f"Cannot stat input: {path}", details=str(exc)) from exc
    if size > max_bytes:
        raise ToolError("input_too_large", f"Input exceeds the configured {max_bytes}-byte limit.", details={"path": str(path), "bytes": size, "limit": max_bytes})
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError("input_unreadable", f"Cannot read input: {path}", details=str(exc)) from exc
    if len(data) > max_bytes:
        raise ToolError("input_too_large", "Input grew beyond the configured byte limit while being read.")
    return data


def decode_text(data: bytes, encoding: str = "auto") -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    if encoding != "auto":
        try:
            return data.decode(encoding), encoding, warnings
        except (UnicodeDecodeError, LookupError) as exc:
            raise ToolError("decode_failed", f"Input is not valid {encoding}.", details=str(exc), hint="Choose the correct --encoding or convert the source to UTF-8.") from exc
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig", warnings
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16", warnings
    try:
        return data.decode("utf-8"), "utf-8", warnings
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
        warnings.append("UTF-8 decoding failed; CP1252 was inferred. Verify accented characters.")
        return text, "cp1252", warnings
    except UnicodeDecodeError as exc:
        raise ToolError("decode_failed", "Could not decode text as UTF-8, UTF-16, or CP1252.", details=str(exc)) from exc


def atomic_write(path: Path, data: str | bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise ToolError("output_exists", f"Output already exists: {path}", hint="Choose another path or explicitly enable overwrite.")
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, max_bytes: int = 10_000_000) -> Any:
    data = bounded_read(path, max_bytes)
    try:
        return json.loads(decode_text(data)[0])
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_json", f"Invalid JSON in {path}.", details={"line": exc.lineno, "column": exc.colno, "message": exc.msg}) from exc


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "limits": {
        "max_input_bytes": 52428800,
        "max_output_bytes": 104857600,
        "max_segments": 50000,
        "max_segment_chars": 12000,
        "batch_segments": 12,
        "batch_chars": 18000,
        "context_chars": 3000,
        "http_timeout_seconds": 45,
        "http_retries": 3,
        "http_response_bytes": 4194304,
        "retry_backoff_seconds": 1.0,
        "checkpoint_every_batches": 1,
        "max_audio_chunks": 500,
        "max_repository_files": 5000
    },
    "provider": {
        "name": "glossary",
        "model_env": "ULTIMATE_TRANSLATION_MODEL",
        "endpoint_env": "ULTIMATE_TRANSLATION_ENDPOINT",
        "api_key_env": "ULTIMATE_TRANSLATION_API_KEY",
        "organization_env": "ULTIMATE_TRANSLATION_ORGANIZATION",
        "extra_headers": {},
        "request_options": {}
    },
    "translation": {
        "formality": "natural",
        "preserve_tokens": True,
        "allow_network": False,
        "review_confidence_below": 0.8,
        "require_human_review_domains": ["legal", "medical", "pharmaceutical", "financial", "safety", "patent"],
        "prohibited_claims": [],
        "parallel_examples": []
    },
    "subtitles": {"max_chars_per_line": 42, "max_lines": 2, "max_cps": 20, "min_duration_seconds": 0.8, "max_duration_seconds": 7.0},
    "review": {"sample_rate": 0.1, "high_risk_sample_rate": 1.0, "defect_escalation_threshold": 0.05, "seed": 417},
    "output": {"atomic": True, "report_suffix": ".report.json", "checkpoint_suffix": ".checkpoint.json"}
}


def load_config(path: str | None, sibling_dir: Path) -> dict[str, Any]:
    config = DEFAULT_CONFIG
    selected: Path | None = None
    if path:
        selected = resolve_user_path(path, must_exist=True)
    else:
        candidate = sibling_dir / "asset-default-config.json"
        if candidate.is_file():
            selected = candidate
    if selected:
        loaded = read_json(selected)
        if not isinstance(loaded, dict):
            raise ToolError("invalid_config", "Configuration root must be a JSON object.")
        config = deep_merge(DEFAULT_CONFIG, loaded)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 1:
        raise ToolError("invalid_config", "Only configuration version 1 is supported.")
    limits = config.get("limits")
    if not isinstance(limits, dict):
        raise ToolError("invalid_config", "limits must be an object.")
    for key, default in DEFAULT_CONFIG["limits"].items():
        value = limits.get(key, default)
        minimum = 0 if key == "http_retries" else 0.000001
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum or (key != "http_retries" and value == 0) or (key == "http_retries" and int(value) != value):
            raise ToolError("invalid_config", f"limits.{key} must be {'a non-negative integer' if key == 'http_retries' else 'a positive number'}.", details=value)
    if limits["batch_segments"] > 1000 or limits["max_segments"] > 1_000_000:
        raise ToolError("invalid_config", "Batch or segment limit exceeds the hard safety ceiling.")
    if limits["http_timeout_seconds"] > 300 or limits["http_retries"] > 10:
        raise ToolError("invalid_config", "HTTP timeout must be <=300 seconds and retries <=10.")
    provider = config.get("provider", {})
    if provider.get("name", "glossary") not in {"glossary", "http", "pseudo"}:
        raise ToolError("invalid_config", "provider.name must be glossary, http, or pseudo.")
    if not isinstance(provider.get("extra_headers", {}), dict) or not isinstance(provider.get("request_options", {}), dict):
        raise ToolError("invalid_config", "Provider headers and request options must be objects.")


def parse_key_values(items: Sequence[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ToolError("invalid_option", f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        if not key.strip():
            raise ToolError("invalid_option", "Option key cannot be empty.")
        result[key.strip()] = value
    return result


# Token guardian: printf, interpolation, ICU-ish blocks, tags, links, paths, escapes, and accelerators.
TOKEN_PATTERNS = [
    r"`[^`\r\n]{1,4000}`",
    r"\{\{[^{}\r\n]{1,1000}\}\}",
    r"\{%[^{}\r\n]{1,1000}%\}",
    r"\$\{[^{}\r\n]{1,1000}\}",
    r"%\([A-Za-z_][A-Za-z0-9_]*\)[#0 +\-]?\d*(?:\.\d+)?[diouxXeEfFgGcrs%]",
    r"%(?:\d+\$)?[#0 +\-]?\d*(?:\.\d+)?[diouxXeEfFgGcrs%]",
    r"\{[A-Za-z_][A-Za-z0-9_.:-]*(?:\s*,\s*(?:plural|select|selectordinal|number|date|time)[^{}]*(?:\{[^{}]*\}[^{}]*)*)?\}",
    r"</?[A-Za-z_][^<>\r\n]{0,4000}>",
    r"(?:https?|ftp)://[^\s<>]{1,4000}",
    r"(?:[A-Za-z]:\\|/|\\\\)[^\s<>{}\r\n]{1,4000}",
   r"\\(?:N|n|t|r|{|}|h|<|>)",
   r"(?<!&)&[A-Za-z](?=[A-Za-z0-9_ ]|$)",
]
TOKEN_RE = re.compile("|".join(f"(?:{p})" for p in TOKEN_PATTERNS), re.MULTILINE)


def extract_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def protect_tokens(text: str) -> tuple[str, list[tuple[str, str]]]:
    replacements: list[tuple[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        marker = f"⟦UT{len(replacements)}⟧"
        replacements.append((marker, match.group(0)))
        return marker

    return TOKEN_RE.sub(replace, text), replacements


def restore_tokens(text: str, replacements: Sequence[tuple[str, str]], *, strict: bool = True) -> str:
    result = text
    for marker, original in replacements:
        count = result.count(marker)
        if strict and count != 1:
            raise ToolError("token_mismatch", "A translation provider damaged or duplicated a protected token marker.", details={"marker": marker, "expected": 1, "found": count}, hint="Retry with a smaller batch or use an approved translation memory/glossary.")
        result = result.replace(marker, original)
    leftover = re.findall(r"⟦UT\d+⟧", result)
    if strict and leftover:
        raise ToolError("token_mismatch", "Unexpected protected token markers remain in output.", details=leftover)
    return result


SENTENCE_RE = re.compile(r"[^.!?\u3002\uff01\uff1f]+(?:[.!?\u3002\uff01\uff1f]+[\"'\u2019\u201d)\]]*|$)", re.MULTILINE)


def sentence_spans(text: str, max_chars: int = 4000) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in SENTENCE_RE.finditer(text):
        start, end = match.span()
        if not text[start:end].strip():
            continue
        while end - start > max_chars:
            cut = text.rfind(" ", start + 1, start + max_chars)
            if cut <= start:
                cut = start + max_chars
            spans.append((start, cut))
            start = cut
        if text[start:end].strip():
            spans.append((start, end))
    return spans


def paragraph_segments(text: str, *, max_chars: int = 4000, prefix: str = "seg") -> list[tuple[Segment, tuple[int, int]]]:
    result: list[tuple[Segment, tuple[int, int]]] = []
    index = 0
    for para in re.finditer(r"[^\n]+", text):
        pstart, pend = para.span()
        for start, end in sentence_spans(text[pstart:pend], max_chars):
            absolute = (pstart + start, pstart + end)
            raw = text[absolute[0]:absolute[1]]
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            actual = (absolute[0] + leading, absolute[1] - trailing)
            if actual[0] >= actual[1]:
                continue
            index += 1
            value = text[actual[0]:actual[1]]
            result.append((Segment(f"{prefix}-{index:06d}", value, context=text[max(0, actual[0]-160):min(len(text), actual[1]+160)]), actual))
    return result


SCRIPT_RANGES: list[tuple[str, str, int, int]] = [
    ("Latin", "latn", 0x0040, 0x024F), ("Greek", "grek", 0x0370, 0x03FF),
    ("Cyrillic", "cyrl", 0x0400, 0x052F), ("Armenian", "armn", 0x0530, 0x058F),
    ("Hebrew", "hebr", 0x0590, 0x05FF), ("Arabic", "arab", 0x0600, 0x06FF),
    ("Devanagari", "deva", 0x0900, 0x097F), ("Thai", "thai", 0x0E00, 0x0E7F),
    ("Georgian", "geor", 0x10A0, 0x10FF), ("Hiragana", "kana", 0x3040, 0x309F),
    ("Katakana", "kana", 0x30A0, 0x30FF), ("CJK", "hani", 0x4E00, 0x9FFF),
    ("Hangul", "hang", 0xAC00, 0xD7AF),
]

LOCALE_PATTERNS = {
    "en": {"the", "and", "is", "are", "you", "hello", "with", "for", "this", "that", "not", "from"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "es", "hola", "usted", "para", "con", "no"},
    "fr": {"le", "les", "des", "une", "est", "et", "bonjour", "vous", "pour", "avec", "pas", "dans"},
    "de": {"der", "die", "das", "und", "ist", "sie", "nicht", "mit", "für", "ein", "eine", "zu"},
    "it": {"il", "gli", "che", "di", "e", "è", "ciao", "per", "con", "non", "della", "una"},
    "pt": {"você", "não", "uma", "para", "com", "olá", "está", "que", "os", "as", "de", "e"},
    "nl": {"de", "het", "een", "en", "is", "niet", "met", "voor", "hallo", "dat", "van", "je"},
    "tl": {"ang", "ng", "mga", "ay", "at", "sa", "ako", "ikaw", "hindi", "ito", "para", "kumusta"},
    "id": {"yang", "dan", "di", "itu", "ini", "tidak", "untuk", "dengan", "anda", "saya", "adalah"},
    "vi": {"và", "củа", "củа", "không", "cho", "với", "bạn", "tôi", "là", "này", "có", "trong"},
}
# Correct accidental confusable characters while keeping detection data readable.
LOCALE_PATTERNS["vi"] = {"và", "củа".replace("а", "a"), "không", "cho", "với", "bạn", "tôi", "là", "này", "có", "trong", "củа".replace("а", "a")}


def detect_language(text: str) -> dict[str, Any]:
    sample = text[:20000]
    scripts: dict[str, int] = {}
    for char in sample:
        point = ord(char)
        for name, _code, low, high in SCRIPT_RANGES:
            if low <= point <= high:
                scripts[name] = scripts.get(name, 0) + 1
                break
    words = re.findall(r"[^\W\d_]+", sample.lower(), re.UNICODE)
    scores = {locale: sum(1 for word in words if word in lexicon) for locale, lexicon in LOCALE_PATTERNS.items()}
    best_locale = max(scores, key=scores.get) if scores else "und"
    best_score = scores.get(best_locale, 0)
    primary_script = max(scripts, key=scripts.get) if scripts else "Unknown"
    script_locale = {"Greek": "el", "Cyrillic": "ru", "Hebrew": "he", "Arabic": "ar", "Devanagari": "hi", "Thai": "th", "Armenian": "hy", "Georgian": "ka", "Hiragana": "ja", "Katakana": "ja", "CJK": "zh", "Hangul": "ko"}.get(primary_script)
    if best_score < 2 and script_locale:
        best_locale = script_locale
    total = max(1, len(words))
    confidence = min(0.95, 0.25 + best_score / max(4, min(total, 30))) if best_locale != "und" else 0.1
    if script_locale and best_locale == script_locale:
        confidence = max(confidence, min(0.9, 0.45 + scripts.get(primary_script, 0) / max(100, len(sample))))
    return {
        "language": best_locale,
        "confidence": round(confidence, 3),
        "method": "local-script-and-stopword-heuristic",
        "scripts": [{"name": k, "characters": v} for k, v in sorted(scripts.items(), key=lambda kv: -kv[1])],
        "review_required": confidence < 0.65 or best_locale == "und",
        "limitations": "Heuristic detection is not dialect identification; verify short, mixed, or low-resource text."
    }


def detect_segments(text: str, max_chars: int = 4000) -> list[dict[str, Any]]:
    results = []
    for segment, span in paragraph_segments(text, max_chars=max_chars):
        detected = detect_language(segment.text)
        tokens = re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)?", segment.text, re.UNICODE)
        results.append({"id": segment.id, "start": span[0], "end": span[1], "text": segment.text, "token_count": len(tokens), **detected})
    return results


def batches(segments: Sequence[Segment], max_segments: int, max_chars: int) -> Iterator[list[Segment]]:
    current: list[Segment] = []
    chars = 0
    for segment in segments:
        size = len(segment.text)
        if size > max_chars and len(segment.text) > 50000:
            raise ToolError("segment_too_large", "A segment exceeds the hard 50,000-character provider ceiling.", details={"id": segment.id, "characters": size})
        if current and (len(current) >= max_segments or chars + size > max_chars):
            yield current
            current, chars = [], 0
        current.append(segment)
        chars += size
    if current:
        yield current


def load_glossary(path: str | Path | None, *, source_locale: str | None = None, target_locale: str | None = None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = resolve_user_path(str(path), must_exist=True)
    if p.suffix.lower() == ".json":
        data = read_json(p)
        rows = data.get("terms") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ToolError("invalid_glossary", "JSON glossary must be an array or an object with a terms array.")
    else:
        raw = decode_text(bounded_read(p, 20_000_000))[0]
        try:
            rows = list(csv.DictReader(raw.splitlines()))
        except csv.Error as exc:
            raise ToolError("invalid_glossary", "Could not parse glossary CSV.", details=str(exc)) from exc
    required = {"source", "target"}
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 2):
        if not isinstance(row, dict):
            errors.append({"row": index, "error": "not an object"})
            continue
        item = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None}
        missing = required - item.keys()
        if missing or not item.get("source") or not item.get("target"):
            errors.append({"row": index, "error": "source and target are required"})
            continue
        item.setdefault("status", "approved")
        item.setdefault("term_type", "term")
        item.setdefault("case_sensitive", "false")
        item.setdefault("forbidden", "")
        item.setdefault("definition", "")
        item.setdefault("part_of_speech", "")
        item.setdefault("notes", "")
        item.setdefault("source_locale", source_locale or "")
        item.setdefault("target_locale", target_locale or "")
        if source_locale and item["source_locale"] and not locale_matches(item["source_locale"], source_locale):
            continue
        if target_locale and item["target_locale"] and not locale_matches(item["target_locale"], target_locale):
            continue
        key = (item["source"].casefold(), item["target"], item["source_locale"], item["target_locale"])
        if key in seen:
            errors.append({"row": index, "error": "duplicate source-target-locale term"})
            continue
        seen.add(key)
        item["row"] = index
        normalized.append(item)
    if errors:
        raise ToolError("invalid_glossary", "Glossary validation failed.", details=errors[:100])
    if not normalized and path:
        raise ToolError("empty_glossary", "No glossary terms match the requested locales.")
    return normalized


def locale_matches(configured: str, requested: str) -> bool:
    a = configured.lower().replace("_", "-")
    b = requested.lower().replace("_", "-")
    return a == b or a.split("-")[0] == b.split("-")[0] or a == "*" or b == "*"


def load_memory(path: str | Path | None, *, source_locale: str, target_locale: str) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    p = resolve_user_path(str(path), must_exist=True)
    data = read_json(p)
    records = data.get("segments", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ToolError("invalid_memory", "Translation memory must contain a segments array.")
    memory: dict[str, dict[str, Any]] = {}
    for i, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("source"), str) or not isinstance(record.get("target"), str):
            raise ToolError("invalid_memory", "Every memory segment needs source and target strings.", details={"index": i})
        sl = str(record.get("source_locale", source_locale))
        tl = str(record.get("target_locale", target_locale))
        if not locale_matches(sl, source_locale) or not locale_matches(tl, target_locale):
            continue
        if record.get("status", "approved") not in {"approved", "reviewed"}:
            continue
        key = fingerprint([sl.split("-")[0], tl, re.sub(r"\s+", " ", record["source"]).strip().casefold()])
        # Newer approved records win deterministically by order in the file.
        memory[key] = record
    return memory


def memory_key(source: str, source_locale: str, target_locale: str) -> str:
    return fingerprint([source_locale.split("-")[0], target_locale, re.sub(r"\s+", " ", source).strip().casefold()])


class Cancellation:
    def __init__(self) -> None:
        self.cancelled = False
        self.previous: Any = None

    def __enter__(self) -> "Cancellation":
        def handler(signum: int, frame: Any) -> None:
            self.cancelled = True
        try:
            self.previous = signal.signal(signal.SIGTERM, handler)
        except (ValueError, OSError):
            self.previous = None
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.previous is not None:
            try:
                signal.signal(signal.SIGTERM, self.previous)
            except (ValueError, OSError):
                pass


def translation_report_segment(segment: Segment, translated: Translation) -> dict[str, Any]:
    return {
        "id": segment.id,
        "source": segment.text,
        "target": translated.text,
        "confidence": translated.confidence,
        "uncertainties": translated.uncertainties,
        "alternatives": translated.alternatives,
        "back_translation": translated.back_translation,
        "provider": translated.provider,
        "reused": translated.reused,
        "review_required": translated.review_required,
        "metadata": segment.metadata,
    }


class TranslationEngine:
    def __init__(self, provider: Any, config: dict[str, Any], *, source_locale: str, target_locale: str, options: dict[str, Any], memory: dict[str, dict[str, Any]] | None = None, checkpoint_path: Path | None = None, resume: bool = False):
        self.provider = provider
        self.config = config
        self.source_locale = source_locale
        self.target_locale = target_locale
        self.options = options
        self.memory = memory or {}
        self.checkpoint_path = checkpoint_path
        self.resume = resume
        self.results: dict[str, Translation] = {}
        self.context = ""
        self.source_fp = ""

    def _checkpoint(self, segments: Sequence[Segment], status: str, completed_batches: int, error: Any = None) -> None:
        if not self.checkpoint_path:
            return
        payload = {
            "version": 1,
            "status": status,
            "updated_at": utc_now(),
            "source_fingerprint": self.source_fp,
            "source_locale": self.source_locale,
            "target_locale": self.target_locale,
            "options_fingerprint": fingerprint(self.options),
            "provider": getattr(self.provider, "name", "unknown"),
            "segment_order": [s.id for s in segments],
            "completed_batches": completed_batches,
            "context": self.context[-int(self.config["limits"]["context_chars"]):],
            "translations": [translation_report_segment(next(s for s in segments if s.id == key), value) for key, value in self.results.items()],
            "error": error,
        }
        write_json(self.checkpoint_path, payload)

    def _restore(self, segments: Sequence[Segment]) -> int:
        if not self.resume or not self.checkpoint_path or not self.checkpoint_path.exists():
            return 0
        saved = read_json(self.checkpoint_path)
        expected = {
            "source_fingerprint": self.source_fp,
            "source_locale": self.source_locale,
            "target_locale": self.target_locale,
            "options_fingerprint": fingerprint(self.options),
        }
        for key, value in expected.items():
            if saved.get(key) != value:
                raise ToolError("checkpoint_mismatch", f"Checkpoint {key} does not match this job; refusing unsafe resume.")
        if saved.get("segment_order") != [s.id for s in segments]:
            raise ToolError("checkpoint_mismatch", "Checkpoint segment order differs; refusing unsafe resume.")
        by_id = {s.id: s for s in segments}
        for item in saved.get("translations", []):
            sid = item.get("id")
            if sid not in by_id or item.get("source") != by_id[sid].text:
                raise ToolError("checkpoint_mismatch", "Checkpoint contains an unknown or changed segment.")
            self.results[sid] = Translation(
                id=sid, text=item["target"], confidence=item.get("confidence"), uncertainties=list(item.get("uncertainties", [])),
                alternatives=list(item.get("alternatives", [])), back_translation=item.get("back_translation", ""),
                provider=item.get("provider", ""), reused=bool(item.get("reused")), review_required=bool(item.get("review_required")),
            )
        self.context = str(saved.get("context", ""))
        return int(saved.get("completed_batches", 0))

    def run(self, segments: Sequence[Segment], source_data: bytes | str) -> dict[str, Translation]:
        if len(segments) > self.config["limits"]["max_segments"]:
            raise ToolError("too_many_segments", "Input exceeds max_segments.", details={"segments": len(segments)})
        self.source_fp = fingerprint(source_data)
        limits = self.config["limits"]
        batch_list = list(batches(segments, int(limits["batch_segments"]), int(limits["batch_chars"])))
        completed = self._restore(segments)
        if completed > len(batch_list):
            raise ToolError("checkpoint_mismatch", "Checkpoint completed-batch count is invalid.")
        try:
            with Cancellation() as cancellation:
                for batch_index in range(completed, len(batch_list)):
                    if cancellation.cancelled:
                        raise ToolError("cancelled", "Cancellation requested; progress was checkpointed.", hint="Rerun the same command with --resume.")
                    pending: list[Segment] = []
                    for segment in batch_list[batch_index]:
                        if segment.id in self.results:
                            continue
                        hit = self.memory.get(memory_key(segment.text, self.source_locale, self.target_locale))
                        if hit:
                            self.results[segment.id] = Translation(segment.id, hit["target"], confidence=1.0, provider="memory", reused=True, review_required=False)
                        else:
                            pending.append(segment)
                    if pending:
                        values = self.provider.translate_batch(pending, source_locale=self.source_locale, target_locale=self.target_locale, options=self.options, context=self.context)
                        by_id = {s.id: s for s in pending}
                        if len(values) != len(pending):
                            raise ToolError("provider_response_invalid", "Provider returned the wrong number of translations.", details={"expected": len(pending), "received": len(values)})
                        for value in values:
                            if value.id not in by_id:
                                raise ToolError("provider_response_invalid", "Provider returned an unknown segment ID.", details=value.id)
                            if not isinstance(value.text, str) or not value.text.strip():
                                raise ToolError("provider_response_invalid", "Provider returned an empty translation.", details=value.id)
                            original_tokens = sorted(extract_tokens(by_id[value.id].text))
                            target_tokens = sorted(extract_tokens(value.text))
                            if original_tokens != target_tokens:
                                raise ToolError("token_mismatch", "Protected tokens differ between source and translation.", details={"id": value.id, "source": original_tokens, "target": target_tokens})
                            threshold = float(self.config["translation"].get("review_confidence_below", 0.8))
                            if value.confidence is not None and value.confidence < threshold:
                                value.review_required = True
                            if value.uncertainties:
                                value.review_required = True
                            self.results[value.id] = value
                        self.context = (self.context + "\n" + "\n".join(f"Source: {s.text}\nTarget: {self.results[s.id].text}" for s in pending))[-int(limits["context_chars"]):]
                    completed = batch_index + 1
                    if completed % int(limits["checkpoint_every_batches"]) == 0:
                        self._checkpoint(segments, "in-progress", completed)
        except KeyboardInterrupt as exc:
            self._checkpoint(segments, "cancelled", completed, {"code": "keyboard_interrupt"})
            raise ToolError("cancelled", "Interrupted; progress was checkpointed.", hint="Rerun the same command with --resume.") from exc
        except ToolError as exc:
            self._checkpoint(segments, "failed", completed, exc.as_dict())
            raise
        except Exception as exc:
            self._checkpoint(segments, "failed", completed, {"code": "unexpected_error", "message": str(exc)})
            raise
        missing = [s.id for s in segments if s.id not in self.results]
        if missing:
            raise ToolError("incomplete_translation", "Not all segments were translated.", details=missing[:20])
        self._checkpoint(segments, "complete", completed)
        return self.results


def validate_json_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, str]]:
    """Small deterministic JSON Schema subset: type, required, properties, items, enum, min/max, pattern."""
    errors: list[dict[str, str]] = []
    types = schema.get("type")
    if types:
        allowed = types if isinstance(types, list) else [types]
        checks = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
        valid = False
        for kind in allowed:
            if kind in checks and isinstance(instance, checks[kind]) and not (kind != "boolean" and isinstance(instance, bool) and kind in {"object", "array", "string"}):
                valid = True
            elif kind == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
                valid = True
            elif kind == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
                valid = True
        if not valid:
            errors.append({"path": path, "message": f"expected type {allowed}"})
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append({"path": path, "message": "value is not in enum"})
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append({"path": path, "message": f"missing required property {key}"})
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(validate_json_schema(instance[key], subschema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            extras = set(instance) - set(schema.get("properties", {}))
            for key in sorted(extras):
                errors.append({"path": f"{path}.{key}", "message": "additional property is not allowed"})
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append({"path": path, "message": "too few items"})
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append({"path": path, "message": "too many items"})
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(instance):
                errors.extend(validate_json_schema(item, schema["items"], f"{path}[{i}]"))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append({"path": path, "message": "string is too short"})
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append({"path": path, "message": "string is too long"})
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append({"path": path, "message": "string does not match pattern"})
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append({"path": path, "message": "number is below minimum"})
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append({"path": path, "message": "number is above maximum"})
    return errors


def json_output(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def print_result(value: Any, output: str | None = None, *, human: bool = False) -> None:
    text = json_output(value)
    if output:
        atomic_write(resolve_user_path(output), text)
    if not output or human:
        sys.stdout.write(text)


def error_result(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ToolError):
        return {"ok": False, "error": exc.as_dict(), "timestamp": utc_now()}
    return {"ok": False, "error": {"code": "unexpected_error", "message": str(exc), "hint": "Rerun with --debug for a traceback; verify input and configuration."}, "timestamp": utc_now()}


def safe_output_size(data: bytes, config: dict[str, Any]) -> bytes:
    if len(data) > int(config["limits"]["max_output_bytes"]):
        raise ToolError("output_too_large", "Rendered output exceeds max_output_bytes.", details={"bytes": len(data)})
    return data


def count_words(text: str) -> int:
    # CJK characters count individually; other scripts use Unicode word boundaries.
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    without_cjk = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", " ", text)
    return cjk + len(re.findall(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d+(?:[.,]\d+)*", without_cjk, re.UNICODE))


def readability(text: str) -> dict[str, Any]:
    words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", text)
    sentences = max(1, len(re.findall(r"[.!?]+", text)) or 1)
    syllables = 0
    for word in words:
        groups = re.findall(r"[aeiouy]+", word.lower())
        syllables += max(1, len(groups) - (1 if word.lower().endswith("e") and len(groups) > 1 else 0))
    if not words:
        return {"supported": False, "reason": "Flesch metrics are calculated only for Latin-script English-like text."}
    words_n = len(words)
    grade = 0.39 * (words_n / sentences) + 11.8 * (syllables / words_n) - 15.59
    ease = 206.835 - 1.015 * (words_n / sentences) - 84.6 * (syllables / words_n)
    return {"supported": True, "words": words_n, "sentences": sentences, "estimated_syllables": syllables, "flesch_kincaid_grade": round(grade, 2), "flesch_reading_ease": round(ease, 2), "caution": "Estimate only; not validated for non-English languages."}
