#!/usr/bin/env python3
"""Offline glossary/pseudolocalization and bounded configurable HTTP providers."""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Sequence

from tools_core import (
    Segment, ToolError, Translation, json_output, protect_tokens, restore_tokens,
)


PSEUDO_MAP = str.maketrans({
    "a": "á", "b": "ƀ", "c": "ç", "d": "ð", "e": "é", "f": "ƒ", "g": "ĝ", "h": "ĥ", "i": "í", "j": "ĵ", "k": "ķ", "l": "ĺ", "m": "ɱ", "n": "ñ", "o": "ó", "p": "þ", "q": "q", "r": "ŕ", "s": "š", "t": "ţ", "u": "ú", "v": "ṽ", "w": "ŵ", "x": "ẋ", "y": "ý", "z": "ž",
    "A": "Á", "B": "Ƀ", "C": "Ç", "D": "Ð", "E": "É", "F": "Ƒ", "G": "Ĝ", "H": "Ĥ", "I": "Í", "J": "Ĵ", "K": "Ķ", "L": "Ĺ", "M": "Ṁ", "N": "Ñ", "O": "Ó", "P": "Þ", "Q": "Q", "R": "Ŕ", "S": "Š", "T": "Ţ", "U": "Ú", "V": "Ṽ", "W": "Ŵ", "X": "Ẋ", "Y": "Ý", "Z": "Ž",
})


class GlossaryProvider:
    name = "glossary"

    def __init__(self, terms: Sequence[dict[str, Any]]):
        self.terms = sorted(terms, key=lambda t: len(str(t["source"])), reverse=True)

    def translate_batch(self, segments: Sequence[Segment], *, source_locale: str, target_locale: str, options: dict[str, Any], context: str = "") -> list[Translation]:
        results: list[Translation] = []
        for segment in segments:
            exact = None
            for term in self.terms:
                if str(term["source"]).casefold() == segment.text.strip().casefold():
                    exact = term
                    break
            if exact:
                target = str(exact["target"])
                # Restore surrounding whitespace exactly.
                leading = segment.text[:len(segment.text) - len(segment.text.lstrip())]
                trailing = segment.text[len(segment.text.rstrip()):]
                target = leading + target + trailing
                results.append(Translation(segment.id, target, confidence=1.0, provider=self.name, review_required=str(exact.get("status", "approved")) != "approved"))
                continue
            masked, replacements = protect_tokens(segment.text)
            uncertainties: list[str] = []
            for term in self.terms:
                source = str(term["source"])
                if not source or not re.search(r"[^\W\d_]", source, re.UNICODE):
                    continue
                pattern = re.compile(r"(?<![\w])" + re.escape(source) + r"(?![\w])", re.IGNORECASE if str(term.get("case_sensitive", "false")).lower() != "true" else 0)

                def glossary_replace(match: re.Match[str]) -> str:
                    token = f"⟦G{len(replacements)}⟧"
                    replacements.append((token, str(term["target"])))
                    return token

                masked = pattern.sub(glossary_replace, masked)
            # Unprotect glossary tokens and original protected tokens; unmatched text is intentionally unchanged.
            target = restore_tokens(masked, replacements, strict=True)
            coverage = sum(len(str(t["source"])) for t in self.terms if re.search(r"(?<![\w])" + re.escape(str(t["source"])) + r"(?![\w])", segment.text, re.IGNORECASE))
            ratio = min(1.0, coverage / max(1, len(re.sub(r"\s+", "", segment.text))))
            residual = re.sub(r"⟦[A-Z]+\d+⟧", "", masked).strip()
            if residual and ratio < 0.999:
                uncertainties.append("No approved glossary translation covers every span; unmatched source text was left unchanged.")
            results.append(Translation(segment.id, target, confidence=round(ratio, 3), uncertainties=uncertainties, provider=self.name, review_required=ratio < 1.0))
        return results


class PseudoProvider:
    name = "pseudo"

    def translate_batch(self, segments: Sequence[Segment], *, source_locale: str, target_locale: str, options: dict[str, Any], context: str = "") -> list[Translation]:
        expansion = float(options.get("pseudo_expansion", 0.35))
        if not 0 <= expansion <= 3:
            raise ToolError("invalid_option", "pseudo_expansion must be between 0 and 3.")
        accented = bool(options.get("pseudo_accents", True))
        bracketed = bool(options.get("pseudo_brackets", True))
        bidi = bool(options.get("pseudo_bidi", False))
        results = []
        for segment in segments:
            masked, replacements = protect_tokens(segment.text)
            transformed = masked.translate(PSEUDO_MAP) if accented else masked
            if expansion:
                count = max(1, math.ceil(len(segment.text) * expansion))
                transformed += " " + "ẋ" * count
            if bracketed:
                transformed = "⟦" + transformed + "⟧"
            if bidi:
                transformed = "\u202e" + transformed + "\u202c"
            transformed = restore_tokens(transformed, replacements)
            results.append(Translation(segment.id, transformed, confidence=1.0, provider=self.name, review_required=False))
        return results


SYSTEM_PROMPT = """You are a professional multilingual translation and localization engine.
Return only valid JSON matching the requested schema. Never invent missing text, credentials, facts, or certainty.
Preserve meaning, implied intent, emotional force, politeness, ambiguity, humor where possible, speaker and character identity, names, numbers, units, cross-references, markup, placeholders, escape sequences, and formatting.
Use the requested target locale, regional variant, register, audience, domain, style guide, brand voice, and approved terminology. Do not standardize vernacular identity unless explicitly asked.
Treat protected markers such as ⟦UT0⟧ as immutable atomic tokens: each must occur exactly once in the corresponding output. Keep IDs unchanged.
For low-resource or uncertain content, preserve the safest literal interpretation, provide a confidence from 0 to 1, and put limitations in uncertainties. Do not hallucinate dialect or cultural knowledge.
Legal, medical, pharmaceutical, financial, patent, and safety-critical material always requires human expert review; preserve claims, dosage, warnings, negation, dependencies, and legal distinctions exactly.
"""


def translation_user_prompt(segments: Sequence[Segment], source_locale: str, target_locale: str, options: dict[str, Any], context: str) -> str:
    safe_options = {k: v for k, v in options.items() if k in {
        "tone", "mode", "audience", "domain", "brand_voice", "style_rules", "reading_level", "channel",
        "character_voice", "max_length", "literal", "cultural_adaptation", "vernacular", "glossary", "parallel_examples",
        "pronunciation_standard", "purpose", "locale_notes", "accessibility", "dubbing", "lip_sync",
    } and v not in (None, "", [], {})}
    payload = {
        "task": "Translate each segment and return structured results.",
        "source_locale": source_locale,
        "target_locale": target_locale,
        "options": safe_options,
        "rolling_context_do_not_translate": context[-6000:],
        "segments": [{"id": s.id, "text": s.text, "context": s.context[-1000:], "metadata": {k: v for k, v in s.metadata.items() if k in {"path", "part", "row", "column", "speaker", "start", "end", "max_length", "term_type"}}} for s in segments],
        "response_schema": {"translations": [{"id": "same input id", "text": "translated text", "confidence": 0.0, "uncertainties": ["..."], "alternatives": ["optional"], "back_translation": "optional literal back-translation"}]},
        "rules": ["Return every input ID exactly once and no extra IDs.", "Do not merge or split segments.", "Keep leading/trailing whitespace when meaningful.", "Do not translate URLs, code, variables, file paths, or protected markers.", "Use alternatives only for real ambiguity, not decoration."],
    }
    return json.dumps(payload, ensure_ascii=False)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward potentially sensitive content to a different, unapproved endpoint."""
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler())


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or "invalid-host"
    if parsed.port:
        host += f":{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def validate_endpoint(url: str, *, allow_http_loopback: bool = True) -> str:
    if not url or len(url) > 2048:
        raise ToolError("invalid_endpoint", "A provider endpoint of at most 2048 characters is required.")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ToolError("invalid_endpoint", "Provider endpoint is malformed.", details=str(exc)) from exc
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ToolError("invalid_endpoint", "Endpoint must be an HTTPS URL without credentials, query, or fragment. HTTP is allowed only for loopback testing.")
    if parsed.scheme == "http":
        host = parsed.hostname.lower().strip("[]")
        # Do not resolve arbitrary hostnames merely to approve plain HTTP: DNS rebinding must not bypass this guard.
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if not allow_http_loopback or not loopback:
            raise ToolError("insecure_endpoint", "Plain HTTP is permitted only for a literal loopback test host (localhost, 127.0.0.1, or ::1).")
    return url


class HTTPProvider:
    name = "http"

    def __init__(self, config: dict[str, Any], *, allow_network: bool = False):
        self.config = config
        self.system_prompt = config.get("prompts", {}).get("translation_system", SYSTEM_PROMPT)
        provider = config.get("provider", {})
        self.model = os.environ.get(provider.get("model_env", "ULTIMATE_TRANSLATION_MODEL"), "")
        self.endpoint = os.environ.get(provider.get("endpoint_env", "ULTIMATE_TRANSLATION_ENDPOINT"), "")
        self.api_key = os.environ.get(provider.get("api_key_env", "ULTIMATE_TRANSLATION_API_KEY"), "")
        self.organization = os.environ.get(provider.get("organization_env", "ULTIMATE_TRANSLATION_ORGANIZATION"), "")
        if not self.model:
            raise ToolError("provider_not_configured", "Translation model is missing.", hint="Set ULTIMATE_TRANSLATION_MODEL (or provider.model_env).")
        validate_endpoint(self.endpoint)
        if not allow_network or not config.get("translation", {}).get("allow_network", False):
            raise ToolError("network_approval_required", "Network translation requires both --allow-network and translation.allow_network=true.", hint="Confirm that content may leave this device and configure the approved endpoint.")
        self.timeout = float(config["limits"]["http_timeout_seconds"])
        self.retries = int(config["limits"]["http_retries"])
        self.backoff = float(config["limits"]["retry_backoff_seconds"])
        self.max_response = int(config["limits"]["http_response_bytes"])
        self.extra_headers = provider.get("extra_headers", {})
        for header in self.extra_headers:
            if header.lower() in {"authorization", "proxy-authorization", "host", "content-length", "cookie"}:
                raise ToolError("invalid_config", f"Sensitive or transport header is not allowed in extra_headers: {header}")
        self.request_options = provider.get("request_options", {})
        allowed = {"temperature", "top_p", "max_tokens", "max_completion_tokens", "seed", "response_format", "reasoning_effort"}
        unknown = set(self.request_options) - allowed
        if unknown:
            raise ToolError("invalid_config", "Unsupported provider request options.", details=sorted(unknown))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ultimate-translation/1.0", **{str(k): str(v) for k, v in self.extra_headers.items()}}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        return headers

    def request_json(self, payload: dict[str, Any], *, endpoint: str | None = None, operation: str = "chat") -> dict[str, Any]:
        url = validate_endpoint(endpoint or self.endpoint)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 4_000_000:
            raise ToolError("request_too_large", "Provider request exceeds the 4 MB safety limit.")
        last_error: ToolError | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
            try:
                with NO_REDIRECT_OPENER.open(request, timeout=self.timeout) as response:
                    raw = response.read(self.max_response + 1)
                    if len(raw) > self.max_response:
                        raise ToolError("provider_response_too_large", "Provider response exceeds http_response_bytes.")
                    try:
                        value = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ToolError("provider_response_invalid", "Provider did not return valid UTF-8 JSON.", details=str(exc)) from exc
                    if not isinstance(value, dict):
                        raise ToolError("provider_response_invalid", "Provider response root must be an object.")
                    return value
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                detail = {"status": exc.code, "endpoint": _redact_url(url), "attempt": attempt + 1}
                try:
                    raw = exc.read(4096)
                    # Do not persist or echo provider error bodies: they can echo sensitive source text or tokens.
                    detail["provider_response_bytes_inspected"] = len(raw)
                except Exception:
                    pass
                last_error = ToolError("provider_http_error", f"Provider returned HTTP {exc.code}.", details=detail, hint="Check the model, endpoint, quota, and request schema.")
                if not retryable or attempt >= self.retries:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
                last_error = ToolError("provider_unavailable", "Provider request failed or timed out.", details={"endpoint": _redact_url(url), "attempt": attempt + 1, "reason": str(getattr(exc, "reason", exc))[:500]}, hint="Check connectivity and retry; completed batches are resumable.")
                if attempt >= self.retries:
                    raise last_error from exc
            if attempt < self.retries:
                time.sleep(min(30.0, self.backoff * (2 ** attempt)) + random.uniform(0, min(0.25, self.backoff / 4)))
        raise last_error or ToolError("provider_unavailable", "Provider request failed.")

    def chat_json(self, system: str, user: str, *, operation: str = "structured-generation") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            **self.request_options,
        }
        response = self.request_json(payload, operation=operation)
        content = self.extract_content(response)
        try:
            value = parse_model_json(content)
        except ToolError:
            raise
        if not isinstance(value, dict):
            raise ToolError("provider_response_invalid", "Model JSON response must be an object.")
        return value

    @staticmethod
    def extract_content(response: dict[str, Any]) -> str:
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            if isinstance(response.get("error"), dict):
                safe_error = {key: response["error"].get(key) for key in ("type", "code") if response["error"].get(key) is not None}
                raise ToolError("provider_error", "Provider returned an error object; its body was not echoed or persisted.", details=safe_error) from exc
            raise ToolError("provider_response_invalid", "Response lacks choices[0].message.content or output_text.") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") in {"text", "output_text"}]
            if parts:
                return "".join(parts)
        raise ToolError("provider_response_invalid", "Model content is not text.")

    def translate_batch(self, segments: Sequence[Segment], *, source_locale: str, target_locale: str, options: dict[str, Any], context: str = "") -> list[Translation]:
        protected: dict[str, list[tuple[str, str]]] = {}
        outgoing = []
        for segment in segments:
            masked, replacements = protect_tokens(segment.text)
            protected[segment.id] = replacements
            outgoing.append(Segment(segment.id, masked, segment.context, segment.metadata))
        value = self.chat_json(self.system_prompt, translation_user_prompt(outgoing, source_locale, target_locale, options, context), operation="translation")
        rows = value.get("translations")
        if not isinstance(rows, list):
            raise ToolError("provider_response_invalid", "Translation response needs a translations array.")
        results: list[Translation] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("text"), str):
                raise ToolError("provider_response_invalid", "Each translation needs string id and text fields.")
            sid = row["id"]
            if sid in seen or sid not in protected:
                raise ToolError("provider_response_invalid", "Duplicate or unexpected translation ID.", details=sid)
            seen.add(sid)
            text = restore_tokens(row["text"], protected[sid])
            confidence = row.get("confidence")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                except (ValueError, TypeError) as exc:
                    raise ToolError("provider_response_invalid", "confidence must be numeric or null.", details=sid) from exc
                if not 0 <= confidence <= 1:
                    raise ToolError("provider_response_invalid", "confidence must be between 0 and 1.", details=sid)
            uncertainties = row.get("uncertainties", [])
            alternatives = row.get("alternatives", [])
            if not isinstance(uncertainties, list) or not all(isinstance(x, str) for x in uncertainties):
                raise ToolError("provider_response_invalid", "uncertainties must be a string array.", details=sid)
            if not isinstance(alternatives, list) or not all(isinstance(x, str) for x in alternatives):
                raise ToolError("provider_response_invalid", "alternatives must be a string array.", details=sid)
            results.append(Translation(sid, text, confidence=confidence, uncertainties=uncertainties, alternatives=alternatives, back_translation=str(row.get("back_translation", "")), provider=self.name))
        missing = set(protected) - seen
        if missing:
            raise ToolError("provider_response_invalid", "Translation response omitted segment IDs.", details=sorted(missing))
        return results

    def transcribe(self, audio_path: Path, *, language: str | None = None, diarize: bool = False, verbatim: str = "clean", endpoint: str | None = None, model: str | None = None) -> dict[str, Any]:
        """Call a configurable multipart transcription endpoint with bounded PCM/audio data."""
        url = validate_endpoint(endpoint or self.endpoint)
        selected_model = model or self.model
        raw = audio_path.read_bytes()
        if len(raw) > int(self.config["limits"]["max_input_bytes"]):
            raise ToolError("input_too_large", "Audio exceeds max_input_bytes.")
        boundary = "----ultimate-translation-" + uuid.uuid4().hex
        fields = {"model": selected_model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
        if language and language != "auto":
            fields["language"] = language.split("-")[0]
        if diarize:
            fields["diarize"] = "true"
        fields["prompt"] = f"Transcribe with {verbatim} verbatim detail. Preserve names, numbers, code-switching, uncertain audio, and speaker turns. Do not invent speech."
        chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.extend([f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")])
        filename = audio_path.name.replace('"', "")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"))
        chunks.append(raw)
        chunks.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
        body = b"".join(chunks)
        if len(body) > int(self.config["limits"]["max_input_bytes"]) + 1_000_000:
            raise ToolError("request_too_large", "Multipart audio request exceeds the configured limit.")
        headers = self._headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["Accept"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        # One bounded request: media uploads are not automatically replayed, avoiding duplicate billable jobs.
        try:
            with NO_REDIRECT_OPENER.open(request, timeout=self.timeout) as response:
                data = response.read(self.max_response + 1)
                if len(data) > self.max_response:
                    raise ToolError("provider_response_too_large", "Transcription response is too large.")
                value = json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ToolError("provider_http_error", f"Transcription endpoint returned HTTP {exc.code}.", details={"status": exc.code, "endpoint": _redact_url(url)}) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise ToolError("provider_unavailable", "Transcription upload failed. It was not automatically replayed.", details=str(getattr(exc, "reason", exc))[:500], hint="Check whether the provider received the request before retrying.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError("provider_response_invalid", "Transcription endpoint did not return valid JSON.") from exc
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ToolError("provider_response_invalid", "Transcription response needs a text string.")
        value.setdefault("segments", [])
        value["provider"] = "http"
        value["diarization_requested"] = diarize
        value["review_required"] = True
        value["limitations"] = "Provider-supplied timestamps, speakers, language, and confidence require human verification; overlap diarization support depends on the configured endpoint."
        return value

    def ocr_image(self, image_path: Path, *, target_locale: str | None = None, endpoint: str | None = None) -> dict[str, Any]:
        """Request OCR with coordinates through a vision-capable chat endpoint."""
        mime = mimetypes.guess_type(image_path.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ToolError("unsupported_image", "OCR supports PNG, JPEG, WebP, and GIF through the configured vision provider.")
        raw = image_path.read_bytes()
        if len(raw) > int(self.config["limits"]["max_input_bytes"]):
            raise ToolError("input_too_large", "Image exceeds max_input_bytes.")
        encoded = base64.b64encode(raw).decode("ascii")
        instruction = {
            "task": "Detect every visible text span, reading order, uncertainty, and bounding box. Do not invent text.",
            "image_width_height": "Use pixel coordinates in the original image.",
            "target_locale_for_optional_translation": target_locale,
            "response_schema": {"image": {"width": 0, "height": 0}, "blocks": [{"id": "block-1", "text": "recognized text", "translation": "optional", "confidence": 0.0, "bbox": [0, 0, 0, 0], "reading_order": 1, "kind": "label|dialogue|sound-effect|warning|other", "uncertainties": []}]},
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": "You are a careful OCR and visual-text localization engine. Return JSON only. Coordinates are [left,top,right,bottom] in original-image pixels."}, {"role": "user", "content": [{"type": "text", "text": json.dumps(instruction)}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}]}],
            "response_format": {"type": "json_object"},
            **self.request_options,
        }
        response = self.request_json(payload, endpoint=endpoint, operation="ocr")
        value = parse_model_json(self.extract_content(response))
        blocks = value.get("blocks") if isinstance(value, dict) else None
        if not isinstance(blocks, list):
            raise ToolError("provider_response_invalid", "OCR response needs a blocks array.")
        for i, block in enumerate(blocks):
            if not isinstance(block, dict) or not isinstance(block.get("text"), str) or not isinstance(block.get("bbox"), list) or len(block["bbox"]) != 4:
                raise ToolError("provider_response_invalid", "Every OCR block needs text and four bbox coordinates.", details=i)
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in block["bbox"]):
                raise ToolError("provider_response_invalid", "OCR bbox coordinates must be numeric.", details=i)
            block.setdefault("id", f"block-{i+1}")
            block.setdefault("reading_order", i + 1)
            block.setdefault("confidence", None)
            block.setdefault("uncertainties", [])
        value["provider"] = "http"
        value["review_required"] = True
        return value


def parse_model_json(content: str) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ToolError("provider_response_invalid", "Model returned empty content.")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ToolError("provider_response_invalid", "Model content is not valid JSON; content was not echoed or persisted.")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ToolError("provider_response_invalid", "Model content is not valid JSON.", details=str(exc)) from exc
    if not isinstance(value, dict):
        raise ToolError("provider_response_invalid", "Model JSON root must be an object.")
    return value


def make_provider(config: dict[str, Any], *, glossary: Sequence[dict[str, Any]] = (), allow_network: bool = False, force: str | None = None) -> Any:
    name = force or config.get("provider", {}).get("name", "glossary")
    if name == "glossary":
        if not glossary:
            raise ToolError("glossary_required", "The offline glossary provider needs at least one matching approved term.", hint="Supply --glossary, use --provider pseudo for layout testing, or configure --provider http with network approval.")
        return GlossaryProvider(glossary)
    if name == "pseudo":
        return PseudoProvider()
    if name == "http":
        return HTTPProvider(config, allow_network=allow_network)
    raise ToolError("invalid_provider", f"Unknown provider: {name}")
