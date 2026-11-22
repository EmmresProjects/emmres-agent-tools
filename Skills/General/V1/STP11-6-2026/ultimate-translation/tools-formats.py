#!/usr/bin/env python3
"""Structure-preserving document, resource, Office, PDF, and subtitle handlers."""
from __future__ import annotations

import copy
import csv
import fnmatch
import html
import io
import json
import mimetypes
import os
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from tools_core import (
    Document, Segment, ToolError, atomic_write, bounded_read, count_words, decode_text,
    detect_language, fingerprint, paragraph_segments, safe_output_size, sentence_spans,
)

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".xhtml", ".xml", ".resx", ".json", ".arb",
    ".yaml", ".yml", ".csv", ".tsv", ".docx", ".xlsx", ".pptx", ".srt", ".vtt", ".ass", ".ssa",
    ".ttml", ".dfxp", ".properties", ".strings", ".po", ".pdf",
}

ID_KEY_RE = re.compile(r"(?:^|[-_.])(id|ids|key|keys|uuid|guid|url|uri|href|src|locale|language|lang|path|paths|file|filename|icon|color|date|created|updated|version|hash|checksum|slug|timezone|currency|number|count)(?:$|[-_.])", re.IGNORECASE)
TRANSLATABLE_ATTRIBUTES = {"alt", "title", "placeholder", "aria-label", "aria-description", "label", "description"}
SKIP_TEXT_TAGS = {"script", "style", "code", "pre", "kbd", "samp", "noscript", "template"}


def _span_document(kind: str, text: str, pairs: Sequence[tuple[Segment, tuple[int, int]]], *, encoding: str = "utf-8", bom: bytes = b"", newline: str | None = None, metadata: dict[str, Any] | None = None) -> Document:
    ordered = sorted(pairs, key=lambda pair: pair[1][0])
    last = -1
    for segment, (start, end) in ordered:
        if start < last or start < 0 or end > len(text) or start >= end:
            raise ToolError("internal_span_error", "Overlapping or invalid extraction spans.", details={"id": segment.id, "start": start, "end": end, "previous_end": last})
        if text[start:end] != segment.text:
            if not (segment.metadata.get("entity_decoded") and html.unescape(text[start:end]) == segment.text):
                raise ToolError("internal_span_error", "Extracted segment does not match its source span.", details=segment.id)
        last = end

    def renderer(translations: dict[str, str]) -> bytes:
        output: list[str] = []
        cursor = 0
        for segment, (start, end) in ordered:
            output.append(text[cursor:start])
            output.append(translations[segment.id])
            cursor = end
        output.append(text[cursor:])
        rendered = "".join(output)
        if newline and newline != "\n":
            # Original non-segment text already retains its line endings; do not globally rewrite it.
            pass
        return bom + rendered.encode(encoding if encoding != "utf-8-sig" else "utf-8")

    return Document(kind, [pair[0] for pair in ordered], renderer, metadata or {})


def load_text_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, kind: str) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    bom = b"\xef\xbb\xbf" if used_encoding == "utf-8-sig" else (data[:2] if used_encoding == "utf-16" else b"")
    if used_encoding == "utf-16":
        # Re-encoding as UTF-8 avoids accidental mixed-endian output while preserving content.
        used_encoding = "utf-8"
        bom = b""
        warnings.append("UTF-16 input is rendered as UTF-8; content is preserved but byte encoding changes.")
    pairs = paragraph_segments(text, max_chars=int(config["limits"]["max_segment_chars"]), prefix=path.stem)
    return _span_document(kind, text, pairs, encoding=used_encoding, bom=bom, metadata={"encoding": used_encoding, "warnings": warnings, "characters": len(text)})


def markdown_pairs(text: str, max_chars: int, prefix: str) -> list[tuple[Segment, tuple[int, int]]]:
    excluded: list[tuple[int, int]] = []
    if text.startswith("---\n") or text.startswith("---\r\n"):
        match = re.search(r"\r?\n(?:---|\.\.\.)\s*(?:\r?\n|$)", text[4:])
        if match:
            excluded.append((0, match.end() + 4))
    fence_re = re.compile(r"^(?:```|~~~).*?^(?:```|~~~)\s*$", re.MULTILINE | re.DOTALL)
    excluded.extend(match.span() for match in fence_re.finditer(text))
    # Indented code blocks are conservatively excluded line by line.
    excluded.extend(match.span() for match in re.finditer(r"^(?:    |\t).+$", text, re.MULTILINE))

    def is_excluded(start: int, end: int) -> bool:
        return any(start < b and end > a for a, b in excluded)

    pairs = []
    for segment, span in paragraph_segments(text, max_chars=max_chars, prefix=prefix):
        if is_excluded(*span):
            continue
        # Protect reference definitions and HTML-only lines entirely.
        if re.match(r"^\s*(?:\[[^]]+\]:\s*\S+|<[^>]+>)\s*$", segment.text):
            continue
        # Keep heading/list/blockquote markers outside the translation span.
        local = segment.text
        marker = re.match(r"^(\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+|[-+*]\s+\[[ xX]\]\s+)*)", local)
        offset = marker.end() if marker else 0
        if offset >= len(local):
            continue
        new_span = (span[0] + offset, span[1])
        segment.text = text[new_span[0]:new_span[1]]
        segment.metadata["format"] = "markdown"
        pairs.append((segment, new_span))
    return pairs


def load_markdown_document(path: Path, data: bytes, config: dict[str, Any], encoding: str) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    bom = b"\xef\xbb\xbf" if used_encoding == "utf-8-sig" else b""
    if used_encoding == "utf-16":
        used_encoding, bom = "utf-8", b""
    pairs = markdown_pairs(text, int(config["limits"]["max_segment_chars"]), path.stem)
    return _span_document("markdown", text, pairs, encoding=used_encoding, bom=bom, metadata={"encoding": used_encoding, "warnings": warnings, "protected": ["frontmatter", "code fences", "indented code", "inline tokens", "link destinations"]})


def markup_pairs(text: str, *, prefix: str, xml_mode: bool = False, max_chars: int = 4000) -> list[tuple[Segment, tuple[int, int]]]:
    pairs: list[tuple[Segment, tuple[int, int]]] = []
    token_re = re.compile(r"<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<![^>]*>|<[^>]+>")
    tokens = list(token_re.finditer(text))
    cursor = 0
    skip_stack: list[str] = []
    index = 0

    def add_text(start: int, end: int, part: str) -> None:
        nonlocal index
        if skip_stack or start >= end:
            return
        raw = text[start:end]
        if not raw.strip() or not re.search(r"[^\W\d_]", html.unescape(raw), re.UNICODE):
            return
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start, end = start + leading, end - trailing
        if start >= end:
            return
        raw = text[start:end]
        if "&" in raw:
            # Decode a whole entity-containing node while retaining its raw replacement span.
            index += 1
            pairs.append((Segment(f"{prefix}-{index:06d}", html.unescape(raw), context=part, metadata={"part": part, "node": "text", "entity_decoded": True}), (start, end)))
            return
        for s, e in sentence_spans(raw, max_chars):
            value = raw[s:e]
            if not value.strip():
                continue
            index += 1
            actual_start, actual_end = start + s, start + e
            segment_text = text[actual_start:actual_end]
            pairs.append((Segment(f"{prefix}-{index:06d}", segment_text, context=part, metadata={"part": part, "node": "text"}), (actual_start, actual_end)))

    for token in tokens:
        add_text(cursor, token.start(), "text")
        tag = token.group(0)
        if not tag.startswith(("<!--", "<![CDATA[", "<!")):
            name_match = re.match(r"<\s*(/?)\s*([A-Za-z_][\w:.-]*)", tag)
            if name_match:
                closing = bool(name_match.group(1))
                name = name_match.group(2).split(":")[-1].lower()
                self_closing = bool(re.search(r"/\s*>$", tag))
                if closing:
                    for pos in range(len(skip_stack) - 1, -1, -1):
                        if skip_stack[pos] == name:
                            del skip_stack[pos:]
                            break
                else:
                    no_translate = bool(re.search(r"(?:translatable\s*=\s*(['\"])false\1|translate\s*=\s*(['\"])(?:no|false)\2)", tag, re.IGNORECASE))
                    if (name in SKIP_TEXT_TAGS or no_translate) and not self_closing:
                        skip_stack.append(name)
                    # Translate only explicit human-facing attributes; IDs, classes, data-* and machine values stay untouched.
                    allowed = set(TRANSLATABLE_ATTRIBUTES)
                    is_description_meta = name == "meta" and bool(re.search(r"(?:name|property)\s*=\s*(['\"])(?:description|og:description|twitter:description)\1", tag, re.IGNORECASE))
                    is_button_value = name == "input" and bool(re.search(r"type\s*=\s*(['\"])(?:button|submit|reset)\1", tag, re.IGNORECASE))
                    for attr in re.finditer(r"([A-Za-z_:][\w:.-]*)\s*=\s*(['\"])(.*?)\2", tag, re.DOTALL):
                        attr_name = attr.group(1).split(":")[-1].lower()
                        selected = attr_name in allowed or (attr_name == "content" and is_description_meta) or (attr_name == "value" and is_button_value)
                        if not selected or not attr.group(3).strip():
                            continue
                        # For XML, description/label/title are human-facing; all others above are HTML accessibility attributes.
                        index += 1
                        start = token.start() + attr.start(3)
                        end = token.start() + attr.end(3)
                        raw_value = text[start:end]
                        value = html.unescape(raw_value)
                        pairs.append((Segment(f"{prefix}-{index:06d}", value, context=f"{name}@{attr_name}", metadata={"part": f"{name}@{attr_name}", "node": "attribute", "quote": attr.group(2), "entity_decoded": "&" in raw_value}), (start, end)))
        cursor = token.end()
    add_text(cursor, len(text), "text")
    # Decode entities for whole text nodes by using a separate document path would change original offsets.
    # Leaving entity-containing nodes untouched is safer than corrupting XML; surface that limitation in metadata.
    return pairs


def load_markup_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, kind: str) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    pairs = markup_pairs(text, prefix=path.stem, xml_mode=kind == "xml", max_chars=int(config["limits"]["max_segment_chars"]))
    entity_spans = len(re.findall(r"&(?!UT)[A-Za-z#][^;]{0,30};", text))
    if entity_spans:
        warnings.append("Entity-containing nodes are decoded for translation and safely re-escaped on output; exact entity spelling may be normalized.")
    doc = _span_document(kind, text, pairs, encoding="utf-8" if used_encoding == "utf-16" else used_encoding, bom=b"\xef\xbb\xbf" if used_encoding == "utf-8-sig" else b"", metadata={"encoding": used_encoding, "warnings": warnings, "entity_count": entity_spans})
    original_render = doc.renderer

    def renderer(translations: dict[str, str]) -> bytes:
        escaped = {}
        for segment in doc.segments:
            value = translations[segment.id]
            if segment.metadata.get("node") == "attribute":
                quote = segment.metadata.get("quote", '"')
                value = html.escape(value, quote=False).replace(quote, "&quot;" if quote == '"' else "&apos;")
            else:
                value = html.escape(value, quote=False)
            escaped[segment.id] = value
        return original_render(escaped)

    doc.renderer = renderer
    return doc


def flatten_json(value: Any, path: tuple[Any, ...] = (), *, keys: Sequence[str] = (), parent: dict[str, Any] | None = None) -> Iterable[tuple[tuple[Any, ...], str, dict[str, Any]]]:
    dotted = ".".join(str(p) for p in path)
    if isinstance(value, dict):
        for key, item in value.items():
            yield from flatten_json(item, path + (key,), keys=keys, parent=value)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from flatten_json(item, path + (index,), keys=keys, parent=value)
    elif isinstance(value, str):
        if not value.strip():
            return
        key = str(path[-1]) if path else ""
        selected = True
        if keys:
            selected = any(fnmatch.fnmatchcase(dotted, pattern) or fnmatch.fnmatchcase(key, pattern) for pattern in keys)
        elif ID_KEY_RE.search(key):
            selected = False
        if not selected:
            return
        metadata: dict[str, Any] = {"path": dotted, "format": "json"}
        if parent:
            for limit_key in ("max_length", "maxLength", "length_limit"):
                if isinstance(parent.get(limit_key), int):
                    metadata["max_length"] = parent[limit_key]
                    break
            if isinstance(parent.get("context"), str):
                metadata["context"] = parent["context"][:1000]
            if isinstance(parent.get("speaker"), str):
                metadata["speaker"] = parent["speaker"]
        yield path, value, metadata


def get_at_path(root: Any, path: Sequence[Any]) -> Any:
    value = root
    for part in path:
        value = value[part]
    return value


def set_at_path(root: Any, path: Sequence[Any], value: Any) -> None:
    target = get_at_path(root, path[:-1]) if path[:-1] else root
    target[path[-1]] = value


def load_json_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, keys: Sequence[str]) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_json", f"Invalid JSON: {path}", details={"line": exc.lineno, "column": exc.colno, "message": exc.msg}) from exc
    leaves = list(flatten_json(value, keys=keys))
    segments = [Segment(f"{path.stem}-{i+1:06d}", item, context=str(meta.get("context", meta["path"])), metadata=meta) for i, (leaf_path, item, meta) in enumerate(leaves)]

    def renderer(translations: dict[str, str]) -> bytes:
        result = copy.deepcopy(value)
        for segment, (leaf_path, _source, _meta) in zip(segments, leaves):
            set_at_path(result, leaf_path, translations[segment.id])
        indent_match = re.search(r"\n([ \t]+)\S", text)
        indent: int | str | None = 2
        if indent_match:
            whitespace = indent_match.group(1)
            indent = "\t" if "\t" in whitespace else max(1, len(whitespace))
        trailing = "\n" if text.endswith(("\n", "\r")) else ""
        return (json.dumps(result, ensure_ascii=False, indent=indent) + trailing).encode("utf-8")

    return Document("json", segments, renderer, {"encoding": "utf-8", "input_encoding": used_encoding, "warnings": warnings + (["JSON formatting is normalized; keys, values, nesting, and order are preserved."] if segments else []), "selected_keys": list(keys)})


def load_yaml_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, keys: Sequence[str]) -> Document:
    try:
        import yaml
    except ImportError as exc:
        raise ToolError("dependency_missing", "YAML requires PyYAML.", hint="Install with: python3 -m pip install -r ultimate-translation/tools-requirements.txt") from exc
    text, used_encoding, warnings = decode_text(data, encoding)
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ToolError("invalid_yaml", f"Invalid YAML: {path}", details=str(exc)) from exc
    leaves = list(flatten_json(value, keys=keys))
    segments = [Segment(f"{path.stem}-{i+1:06d}", item, context=str(meta.get("context", meta["path"])), metadata=meta) for i, (_leaf_path, item, meta) in enumerate(leaves)]

    def renderer(translations: dict[str, str]) -> bytes:
        result = copy.deepcopy(value)
        for segment, (leaf_path, _source, _meta) in zip(segments, leaves):
            set_at_path(result, leaf_path, translations[segment.id])
        return (yaml.safe_dump(result, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)).encode("utf-8")

    return Document("yaml", segments, renderer, {"encoding": "utf-8", "input_encoding": used_encoding, "warnings": warnings + ["YAML anchors, tags, comments, and exact scalar styling are not preserved by safe_load/safe_dump; review a diff.",], "selected_keys": list(keys)})


def load_csv_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, columns: Sequence[str]) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    try:
        sample = text[:8192]
        if path.suffix.lower() == ".tsv":
            dialect = csv.excel_tab
            delimiter = "\t"
        else:
            try:
                sniffed = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                dialect = sniffed
                delimiter = sniffed.delimiter
            except csv.Error:
                dialect = csv.excel
                delimiter = ","
        reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect, strict=True)
        rows = list(reader)
        fields = reader.fieldnames
    except csv.Error as exc:
        raise ToolError("invalid_csv", f"Invalid delimited input: {path}", details=str(exc)) from exc
    if not fields or any(field is None or field == "" for field in fields):
        raise ToolError("invalid_csv", "CSV/TSV needs a non-empty header row.")
    if len(set(fields)) != len(fields):
        raise ToolError("invalid_csv", "Duplicate CSV column names are ambiguous and not supported.")
    if None in fields:
        raise ToolError("invalid_csv", "CSV contains more values than headers.")
    selected: list[str] = []
    if columns:
        for spec in columns:
            if spec.isdigit():
                index = int(spec)
                if index >= len(fields):
                    raise ToolError("invalid_column", f"CSV column index out of range: {spec}")
                selected.append(fields[index])
            elif spec in fields:
                selected.append(spec)
            else:
                raise ToolError("invalid_column", f"Unknown CSV column: {spec}", details=fields)
    else:
        selected = [field for field in fields if not ID_KEY_RE.search(field)]
    segments: list[Segment] = []
    positions: list[tuple[str, int, str]] = []
    for row_index, row in enumerate(rows, 2):
        if None in row:
            raise ToolError("invalid_csv", "CSV row has extra fields.", details={"row": row_index})
        for field in selected:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            sid = f"{path.stem}-{len(segments)+1:06d}"
            segments.append(Segment(sid, value, context=f"row {row_index}, column {field}", metadata={"row": row_index, "column": field, "path": f"row[{row_index}].{field}"}))
            positions.append((sid, row_index - 2, field))

    def renderer(translations: dict[str, str]) -> bytes:
        output_rows = copy.deepcopy(rows)
        for sid, row_index, field in positions:
            output_rows[row_index][field] = translations[sid]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, dialect=dialect, lineterminator="\r\n" if "\r\n" in text else "\n", extrasaction="raise")
        writer.writeheader()
        writer.writerows(output_rows)
        return stream.getvalue().encode("utf-8")

    return Document("csv", segments, renderer, {"encoding": "utf-8", "input_encoding": used_encoding, "delimiter": delimiter, "columns": fields, "translated_columns": selected, "rows": len(rows), "warnings": warnings})


def resource_pairs(text: str, kind: str, prefix: str) -> list[tuple[Segment, tuple[int, int]]]:
    pairs: list[tuple[Segment, tuple[int, int]]] = []
    if kind == "properties":
        pattern = re.compile(r"^(?P<prefix>\s*(?:[^:=\\\s]|\\.)+\s*[:=]\s*)(?P<value>(?:\\.|[^\r\n])*)(?P<continuation>\\?)\s*$", re.MULTILINE)
        for match in pattern.finditer(text):
            if match.group("continuation"):
                continue
            value = match.group("value")
            if not value.strip():
                continue
            pairs.append((Segment(f"{prefix}-{len(pairs)+1:06d}", value, metadata={"format": kind}), match.span("value")))
    elif kind == "strings":
        pattern = re.compile(r'(?P<prefix>"((?:\\.|[^"\\])*)"\s*=\s*")(?P<value>(?:\\.|[^"\\])*)(?P<suffix>"\s*;)')
        for match in pattern.finditer(text):
            value = match.group("value")
            if value.strip():
                pairs.append((Segment(f"{prefix}-{len(pairs)+1:06d}", value, metadata={"format": kind}), match.span("value")))
    return pairs


def load_resource_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, kind: str) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    pairs = resource_pairs(text, kind, path.stem)
    if not pairs and text.strip():
        warnings.append("No single-line translatable values were found; multiline escaped resources require preprocessing.")
    return _span_document(kind, text, pairs, encoding="utf-8" if used_encoding == "utf-16" else used_encoding, bom=b"\xef\xbb\xbf" if used_encoding == "utf-8-sig" else b"", metadata={"encoding": used_encoding, "warnings": warnings})


def load_po_document(path: Path, data: bytes, config: dict[str, Any], encoding: str) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    # One-line msgid/msgstr entries are handled without rewriting comments, flags, or references.
    entry_re = re.compile(r'(?P<msgid>^msgid\s+"(?P<source>(?:\\.|[^"\\])*)")\r?\n(?P<msgstr>^msgstr\s+")(?P<target>(?:\\.|[^"\\])*)"(?P<tail>\s*$)', re.MULTILINE)
    pairs: list[tuple[Segment, tuple[int, int]]] = []
    mapping: dict[str, str] = {}
    for match in entry_re.finditer(text):
        source = match.group("source")
        if not source or source.startswith("msgctxt"):
            continue
        sid = f"{path.stem}-{len(pairs)+1:06d}"
        target = match.group("target")
        source_value = bytes(source, "utf-8").decode("unicode_escape") if "\\" in source else source
        if target:
            # Existing translations are not overwritten unless explicitly treated as source; use source msgid for new work.
            continue
        segment = Segment(sid, source, metadata={"format": "po", "unescaped_source": source_value})
        # Custom renderer below uses source span for validation but writes into msgstr.
        pairs.append((segment, match.span("source")))
        mapping[sid] = match.group("source")
    # Use a dedicated renderer because output span differs from source span.
    entries = list(entry_re.finditer(text))
    selected = [(match, segments) for match, segments in zip([m for m in entries if m.group("source") and not m.group("target")], pairs)]

    def renderer(translations: dict[str, str]) -> bytes:
        replacements: list[tuple[int, int, str]] = []
        for match, (segment, _span) in selected:
            value = translations[segment.id].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            replacements.append((match.start("target"), match.end("target"), value))
        output = text
        for start, end, value in reversed(replacements):
            output = output[:start] + value + output[end:]
        return output.encode("utf-8")

    return Document("po", [pair[0] for pair in pairs], renderer, {"encoding": "utf-8", "warnings": warnings + ["Only single-line PO entries with empty msgstr are supported; plural and multiline entries are left unchanged.",]})


OFFICE_XML_RE = re.compile(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?(?P<tag>t)(?:\s[^>]*)?>(?P<text>[^<]*)</(?P=prefix)?t\s*>", re.DOTALL)
OFFICE_CVALUE_RE = re.compile(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?v(?:\s[^>]*)?>(?P<text>[^<]*)</(?P=prefix)?v\s*>", re.DOTALL)


def office_text_spans(xml: str, part: str, extension: str, prefix: str, start_index: int) -> list[tuple[Segment, tuple[int, int]]]:
    pairs: list[tuple[Segment, tuple[int, int]]] = []
    matches: list[re.Match[str]] = []
    if extension == ".xlsx" and re.match(r"xl/worksheets/[^/]+\.xml$", part):
        for inline in re.finditer(r"<(?:[A-Za-z_][\w.-]*:)?is(?:\s[^>]*)?>[\s\S]*?</(?:[A-Za-z_][\w.-]*:)?is\s*>", xml):
            matches.extend(match for match in OFFICE_XML_RE.finditer(xml, inline.start(), inline.end()))
    else:
        matches.extend(OFFICE_XML_RE.finditer(xml))
    if extension in {".xlsx", ".pptx"} and re.match(r"(?:xl|ppt)/charts/[^/]+\.xml$", part):
        matches.extend(match for match in OFFICE_CVALUE_RE.finditer(xml) if match.group("text").strip() and not re.fullmatch(r"[-+0-9.,Ee%]+", match.group("text").strip()))
    matches = sorted(matches, key=lambda m: m.start())
    last_end = -1
    for match in matches:
        if match.start() < last_end:
            continue
        raw = match.group("text")
        if not raw.strip() or "<" in raw or not re.search(r"[^\W\d_]", html.unescape(raw), re.UNICODE):
            continue
        # Decode a whole entity-containing node and safely re-escape the translated replacement.
        value = html.unescape(raw)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = match.start("text") + leading
        end = match.end("text") - trailing
        if start >= end:
            continue
        start_index += 1
        raw_span = xml[start:end]
        decoded_span = html.unescape(raw_span)
        pairs.append((Segment(f"{prefix}-{start_index:06d}", decoded_span, context=part, metadata={"part": part, "node": "office-text", "entity_decoded": "&" in raw_span}), (start, end)))
        last_end = match.end()
    return pairs


def load_office_document(path: Path, data: bytes, config: dict[str, Any]) -> Document:
    extension = path.suffix.lower()
    try:
        source_zip = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ToolError("invalid_office", f"{extension} is not a valid OOXML ZIP package.") from exc
    infos = source_zip.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ToolError("invalid_office", "OOXML package contains duplicate entry names.")
    if any(info.flag_bits & 0x1 for info in infos):
        raise ToolError("encrypted_office", "Encrypted OOXML is not supported; supply an authorized decrypted copy.")
    total = sum(info.file_size for info in infos)
    if total > int(config["limits"]["max_output_bytes"]):
        raise ToolError("input_too_large", "Uncompressed OOXML content exceeds the configured output safety limit.", details={"bytes": total})
    entries: dict[str, bytes] = {}
    decoded: dict[str, str] = {}
    all_pairs: list[tuple[str, Segment, tuple[int, int]]] = []
    index = 0
    for info in infos:
        raw = source_zip.read(info)
        entries[info.filename] = raw
        part = info.filename
        relevant = False
        if extension == ".docx":
            relevant = bool(re.match(r"word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments|glossary/document)\.xml$", part))
        elif extension == ".pptx":
            relevant = bool(re.match(r"ppt/(?:slides/slide\d+|notesSlides/notesSlide\d+|charts/[^/]+|diagrams/[^/]+)\.xml$", part))
        elif extension == ".xlsx":
            relevant = part == "xl/sharedStrings.xml" or bool(re.match(r"xl/(?:worksheets/sheet\d+|comments\d*|charts/[^/]+)\.xml$", part))
        if not relevant or not raw:
            continue
        try:
            xml = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("unsupported_office_encoding", f"OOXML part is not UTF-8: {part}", hint="Save a fresh OOXML copy with the source Office application.") from exc
        decoded[part] = xml
        pairs = office_text_spans(xml, part, extension, path.stem, index)
        index += len(pairs)
        all_pairs.extend((part, segment, span) for segment, span in pairs)
    source_zip.close()
    if extension == ".docx" and "word/document.xml" not in entries:
        raise ToolError("invalid_office", "DOCX lacks word/document.xml.")
    if extension == ".xlsx" and "xl/workbook.xml" not in entries:
        raise ToolError("invalid_office", "XLSX lacks xl/workbook.xml.")
    if extension == ".pptx" and "ppt/presentation.xml" not in entries:
        raise ToolError("invalid_office", "PPTX lacks ppt/presentation.xml.")
    segments = [segment for _part, segment, _span in all_pairs]

    def renderer(translations: dict[str, str]) -> bytes:
        per_part: dict[str, list[tuple[Segment, tuple[int, int]]]] = {}
        for part, segment, span in all_pairs:
            per_part.setdefault(part, []).append((segment, span))
        output_entries = dict(entries)
        for part, pairs in per_part.items():
            xml = decoded[part]
            output: list[str] = []
            cursor = 0
            for segment, (start, end) in pairs:
                output.append(xml[cursor:start])
                output.append(html.escape(translations[segment.id], quote=False))
                cursor = end
            output.append(xml[cursor:])
            output_entries[part] = "".join(output).encode("utf-8")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as target_zip:
            for info in infos:
                # Reusing ZipInfo preserves timestamps, attributes, comments, compression, and entry order.
                target_zip.writestr(info, output_entries[info.filename])
        return stream.getvalue()

    metadata = {
        "package_parts": len(infos), "uncompressed_bytes": total,
        "embedded_assets": [name for name in names if re.search(r"/(?:media|embeddings|fonts)/", name)],
        "preserved": ["ZIP entry order and metadata", "unmodified package parts", "styles", "relationships", "formulas", "fields", "comments outside translated text"],
        "warnings": ["Only known OOXML text containers are selected. Text inside raster images, unsupported shapes, OLE objects, and third-party extensions needs visual/OCR review."],
    }
    if extension == ".xlsx":
        metadata["warnings"].append("Formulas, cell types, named ranges, validations, filters, and references are not rewritten. Shared-string translations affect every cell using that string.")
    if extension == ".pptx":
        metadata["warnings"].append("Text expansion is reported by QA but cannot be fully validated without rendering the presentation in its target application.")
    return Document(extension[1:], segments, renderer, metadata)


def load_subtitle_document(path: Path, data: bytes, config: dict[str, Any], encoding: str, kind: str) -> Document:
    text, used_encoding, warnings = decode_text(data, encoding)
    pairs: list[tuple[Segment, tuple[int, int]]] = []
    if kind == "srt":
        pattern = re.compile(r"(?ms)^(?P<index>\d+)[ \t]*\r?\n(?P<time>[^\r\n]+-->[^\r\n]+)\r?\n(?P<text>.*?)(?=\r?\n[ \t]*\r?\n|\Z)")
        for match in pattern.finditer(text):
            if "-->" not in match.group("time"):
                raise ToolError("invalid_subtitle", "Invalid SRT timestamp line.", details=match.group("time"))
            raw = match.group("text").rstrip("\r\n")
            if not raw.strip():
                continue
            start, end = match.span("text")
            end -= match.group("text")[len(raw):].__len__()
            value = text[start:end]
            pairs.append((Segment(f"{path.stem}-{len(pairs)+1:06d}", value, context=match.group("time"), metadata={"cue": match.group("index"), "timecode": match.group("time"), "format": kind}), (start, end)))
    elif kind == "vtt":
        if not text.lstrip("\ufeff").startswith("WEBVTT"):
            raise ToolError("invalid_subtitle", "VTT input must start with WEBVTT.")
        pattern = re.compile(r"(?ms)^(?P<head>(?:[^\r\n]+\r?\n)?[^\r\n]*-->[^\r\n]+)\r?\n(?P<text>.*?)(?=\r?\n[ \t]*\r?\n|\Z)")
        for match in pattern.finditer(text):
            if match.start() < text.find("WEBVTT") + 6 or match.group("head").lstrip().startswith(("NOTE", "STYLE", "REGION")):
                continue
            raw = match.group("text").rstrip("\r\n")
            if not raw.strip():
                continue
            start = match.start("text")
            end = start + len(raw)
            value = text[start:end]
            pairs.append((Segment(f"{path.stem}-{len(pairs)+1:06d}", value, context=match.group("head"), metadata={"timecode": match.group("head").splitlines()[-1], "format": kind}), (start, end)))
    elif kind in {"ass", "ssa"}:
        for match in re.finditer(r"(?m)^(?P<head>Dialogue:\s*[^\r\n]*)$", text):
            line = match.group(0)
            # ASS dialogue has nine commas before the final text field.
            positions = [m.start() for m in re.finditer(",", line)]
            if len(positions) < 9:
                raise ToolError("invalid_subtitle", "ASS/SSA Dialogue line lacks nine format fields.", details=line[:200])
            start = match.start() + positions[8] + 1
            end = match.end()
            value = text[start:end]
            if value.strip():
                timing = line.split(",")[1:3]
                pairs.append((Segment(f"{path.stem}-{len(pairs)+1:06d}", value, context=" to ".join(timing), metadata={"timecode": " --> ".join(timing), "format": kind}), (start, end)))
    if not pairs and text.strip():
        warnings.append("No subtitle dialogue was extracted; verify cue syntax and encoding.")
    return _span_document(kind, text, pairs, encoding="utf-8" if used_encoding == "utf-16" else used_encoding, bom=b"\xef\xbb\xbf" if used_encoding == "utf-8-sig" else b"", metadata={"encoding": used_encoding, "warnings": warnings, "cue_count": len(pairs), "format": kind})


def load_pdf_document(path: Path, data: bytes, config: dict[str, Any]) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ToolError("dependency_missing", "PDF extraction requires pypdf.", hint="Install the declared optional dependencies from tools-requirements.txt.") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise ToolError("invalid_pdf", "Could not parse PDF.", details=str(exc)[:500]) from exc
    if reader.is_encrypted:
        try:
            result = reader.decrypt("")
        except Exception:
            result = False
        if not result:
            raise ToolError("encrypted_pdf", "Encrypted PDF requires an authorized decrypted copy; passwords are not requested or stored.")
    pages: list[dict[str, Any]] = []
    segments: list[Segment] = []
    warnings: list[str] = []
    for page_index, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            warnings.append(f"Page {page_index}: text extraction failed: {str(exc)[:200]}")
            text = ""
        width = float(page.mediabox.width or 595.28)
        height = float(page.mediabox.height or 841.89)
        lines: list[tuple[str, str]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            sid = f"{path.stem}-{len(segments)+1:06d}"
            segment = Segment(sid, line.strip(), context=f"PDF page {page_index}", metadata={"page": page_index, "part": "pdf-line"})
            segments.append(segment)
            lines.append((sid, line.strip()))
        pages.append({"page": page_index, "width": width, "height": height, "lines": lines, "source_characters": len(text)})
    if not segments:
        warnings.append("No extractable PDF text was found. Rasterize authorized pages to PNG/JPEG with an approved external renderer, run ocr on those images, then use visual plan. No PDF rasterizer is bundled and scanned-page reconstruction is not automatic.")

    def renderer(translations: dict[str, str]) -> bytes:
        try:
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError as exc:
            raise ToolError("dependency_missing", "PDF reconstruction requires reportlab.", hint="Install the declared optional dependencies from tools-requirements.txt.") from exc
        stream = io.BytesIO()
        pdf = canvas.Canvas(stream, pagesize=(pages[0]["width"], pages[0]["height"]) if pages else (595.28, 841.89), pageCompression=1)
        font_name = "Helvetica"
        font_path = config.get("pdf", {}).get("font_path")
        font_size = float(config.get("pdf", {}).get("font_size", 11))
        if font_path:
            font_file = Path(font_path)
            if not font_file.is_file():
                raise ToolError("invalid_config", "Configured PDF font_path does not exist.", details=font_path)
            font_name = "UltimateTranslationEmbeddedFont"
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(font_file)))
            except Exception as exc:
                raise ToolError("invalid_font", "Could not load configured PDF TrueType font.", details=str(exc)[:300]) from exc
        pdf.setTitle(f"Translated text reconstruction: {path.name}")
        for page_info in pages:
            width, height = page_info["width"], page_info["height"]
            margin = 42.0
            y = height - margin
            pdf.setPageSize((width, height))
            pdf.setFont(font_name, font_size)
            for sid, _source in page_info["lines"]:
                target = translations[sid]
                words = target.split()
                lines_out: list[str] = []
                current = ""
                for word in words:
                    candidate = word if not current else current + " " + word
                    try:
                        fits = pdfmetrics.stringWidth(candidate, font_name, font_size) <= width - 2 * margin
                    except Exception as exc:
                        raise ToolError("font_coverage", "PDF font cannot render translated text. Configure a Unicode TrueType font with pdf.font_path.", details=str(exc)[:300]) from exc
                    if fits:
                        current = candidate
                    else:
                        if current:
                            lines_out.append(current)
                        current = word
                if current:
                    lines_out.append(current)
                for line in lines_out or [""]:
                    if y < margin + font_size:
                        pdf.showPage()
                        pdf.setPageSize((width, height))
                        pdf.setFont(font_name, font_size)
                        y = height - margin
                    try:
                        pdf.drawString(margin, y, line)
                    except Exception as exc:
                        raise ToolError("font_coverage", "PDF reconstruction failed while drawing text; configure a suitable Unicode font.", details=str(exc)[:300]) from exc
                    y -= font_size * 1.35
                y -= font_size * 0.35
            pdf.showPage()
        if not pages:
            pdf.setFont(font_name, font_size)
            pdf.drawString(42, 800, "No extractable source text")
            pdf.showPage()
        pdf.save()
        return stream.getvalue()

    return Document("pdf", segments, renderer, {"pages": len(pages), "page_details": pages, "warnings": warnings + ["Reconstruction is text-first and reflows lines; original fonts, figures, exact coordinates, annotations, forms, links, and redaction appearance are not guaranteed. Use visual/legal review for production publication.",]})


def load_document(path_value: str | Path, config: dict[str, Any], *, encoding: str = "auto", keys: Sequence[str] = (), columns: Sequence[str] = (), kind: str | None = None) -> Document:
    path = Path(path_value)
    if not path.is_file():
        raise ToolError("input_not_found", f"Input file does not exist: {path}")
    data = bounded_read(path, int(config["limits"]["max_input_bytes"]))
    extension = kind.lower() if kind else path.suffix.lower()
    if extension and not extension.startswith("."):
        extension = "." + extension
    if extension not in SUPPORTED_EXTENSIONS:
        raise ToolError("unsupported_format", f"Unsupported translation input format: {extension or '(no extension)'}", details={"supported": sorted(SUPPORTED_EXTENSIONS)}, hint="Use inspect for unknown files or convert to a supported translation-safe format.")
    if extension == ".txt":
        return load_text_document(path, data, config, encoding, "text")
    if extension in {".md", ".markdown"}:
        return load_markdown_document(path, data, config, encoding)
    if extension in {".html", ".htm", ".xhtml"}:
        return load_markup_document(path, data, config, encoding, "html")
    if extension in {".xml", ".resx", ".ttml", ".dfxp"}:
        return load_markup_document(path, data, config, encoding, "xml")
    if extension in {".json", ".arb"}:
        return load_json_document(path, data, config, encoding, keys)
    if extension in {".yaml", ".yml"}:
        return load_yaml_document(path, data, config, encoding, keys)
    if extension in {".csv", ".tsv"}:
        return load_csv_document(path, data, config, encoding, columns)
    if extension in {".docx", ".xlsx", ".pptx"}:
        return load_office_document(path, data, config)
    if extension in {".srt", ".vtt", ".ass", ".ssa"}:
        return load_subtitle_document(path, data, config, encoding, extension[1:])
    if extension in {".properties", ".strings"}:
        return load_resource_document(path, data, config, encoding, "properties" if extension == ".properties" else "strings")
    if extension == ".po":
        return load_po_document(path, data, config, encoding)
    if extension == ".pdf":
        return load_pdf_document(path, data, config)
    raise ToolError("unsupported_format", f"No handler for {extension}.")


def probe_file(path_value: str | Path, config: dict[str, Any], *, encoding: str = "auto", keys: Sequence[str] = (), columns: Sequence[str] = ()) -> dict[str, Any]:
    path = Path(path_value)
    data = bounded_read(path, int(config["limits"]["max_input_bytes"]))
    extension = path.suffix.lower()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    magic = data[:16]
    detected = "unknown"
    if magic.startswith(b"%PDF"):
        detected = "pdf"
    elif magic.startswith(b"PK\x03\x04"):
        detected = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}.get(extension, "zip-ooxml-or-other")
    elif extension in SUPPORTED_EXTENSIONS:
        detected = extension.lstrip(".")
    elif mime.startswith("image/"):
        detected = "image"
    elif mime.startswith("audio/"):
        detected = "audio"
    elif mime.startswith("video/"):
        detected = "video"
    report: dict[str, Any] = {
        "path": str(path), "name": path.name, "extension": extension, "mime_guess": mime, "detected_family": detected,
        "bytes": len(data), "sha256": fingerprint(data), "translation_supported_directly": extension in SUPPORTED_EXTENSIONS,
        "risks": [], "warnings": [], "metadata": {}, "segments": [], "scope": {},
    }
    if not data:
        report["risks"].append({"severity": "error", "type": "empty-file", "message": "Input is empty."})
        return report
    if extension in SUPPORTED_EXTENSIONS:
        try:
            document = load_document(path, config, encoding=encoding, keys=keys, columns=columns)
            report["metadata"] = document.metadata
            report["warnings"].extend(document.metadata.get("warnings", []))
            report["segments"] = [{"id": s.id, "characters": len(s.text), "words": count_words(s.text), "language_heuristic": detect_language(s.text)["language"], "path": s.metadata.get("path"), "part": s.metadata.get("part"), "page": s.metadata.get("page"), "row": s.metadata.get("row"), "column": s.metadata.get("column")} for s in document.segments]
            report["scope"] = {
                "segments": len(document.segments), "words": sum(count_words(s.text) for s in document.segments),
                "characters": sum(len(s.text) for s in document.segments), "subtitle_cues": len(document.segments) if document.kind in {"srt", "vtt", "ass", "ssa"} else 0,
                "estimated_translation_hours": round(sum(count_words(s.text) for s in document.segments) / 1800, 2),
                "estimated_review_hours": round(sum(count_words(s.text) for s in document.segments) / 1000, 2),
            }
            if not document.segments:
                report["risks"].append({"severity": "warning", "type": "no-translatable-text", "message": "No translatable segments were selected."})
            if len(document.segments) >= int(config["limits"]["max_segments"]):
                report["risks"].append({"severity": "warning", "type": "scale", "message": "Input is near the configured segment limit; use resumable batches."})
        except ToolError as exc:
            report["translation_supported_directly"] = False
            report["risks"].append({"severity": "error", "type": exc.code, "message": exc.message, "details": exc.details})
    else:
        report["risks"].append({"severity": "review", "type": "sidecar-required", "message": "Use OCR/transcription or a reviewed text sidecar before translation; direct binary reconstruction is unavailable for this format."})
    if extension in {".pdf", ".docx", ".xlsx", ".pptx"}:
        report["risks"].append({"severity": "review", "type": "layout-and-hidden-content", "message": "Review hidden text, comments, metadata, embedded assets, reading order, links, and redaction boundaries before external processing or release."})
    if extension in {".html", ".htm", ".xml", ".json", ".yaml", ".yml"}:
        report["risks"].append({"severity": "info", "type": "machine-values", "message": "Only selected human-facing values are translated; verify application-specific translatability rules."})
    return report


def exchange_package(document: Document, source_data: bytes | str, *, source_locale: str, target_locale: str, translations: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "source_fingerprint": fingerprint(source_data),
        "source_locale": source_locale,
        "target_locale": target_locale,
        "kind": document.kind,
        "segments": [{
            "id": s.id, "source": s.text, "target": (translations or {}).get(s.id, ""), "context": s.context, "metadata": s.metadata,
        } for s in document.segments],
    }


def apply_exchange(document: Document, package: dict[str, Any], source_data: bytes | str) -> dict[str, str]:
    if package.get("version") != 1 or not isinstance(package.get("segments"), list):
        raise ToolError("invalid_exchange", "Exchange package needs version 1 and a segments array.")
    if package.get("source_fingerprint") != fingerprint(source_data):
        raise ToolError("exchange_mismatch", "Exchange source fingerprint differs from the input file.")
    expected = {s.id: s.text for s in document.segments}
    translations: dict[str, str] = {}
    for row in package["segments"]:
        if not isinstance(row, dict) or row.get("id") not in expected:
            raise ToolError("invalid_exchange", "Exchange contains an unknown segment ID.")
        sid = row["id"]
        if row.get("source") != expected[sid] or not isinstance(row.get("target"), str):
            raise ToolError("exchange_mismatch", "Exchange source text or target type differs from input.", details=sid)
        translations[sid] = row["target"]
    missing = set(expected) - set(translations)
    if missing:
        raise ToolError("invalid_exchange", "Exchange omitted segment IDs.", details=sorted(missing)[:20])
    return translations
