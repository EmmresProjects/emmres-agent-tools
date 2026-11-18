#!/usr/bin/env python3
"""Domain engines for Categories 1-15: Research strategy, discovery, files, documents, media, APIs, and scraping."""
import sys

sys.dont_write_bytecode = True

import csv
import html
import html.parser
import io
import json
import os
import re
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import wave
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from _rwc_core_runtime import (  # type: ignore
    PolicyViolationError,
    RuntimeConfig,
    SafetyGovernor,
    TruthfulnessTracker,
    ValidationError,
    retry_bounded,
    sha256_bytes,
    sha256_file,
    sha256_text,
    to_portable_path,
)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9][a-z0-9_\-]{1,}", text.lower())


class _HTMLIntelligenceParser(html.parser.HTMLParser):
    """Extracts title, meta tags, canonical link, links, headings, tables, and body text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.in_script_or_style = 0
        self.current_heading: Optional[str] = None
        self.heading_buf: List[str] = []
        self.current_link_href: Optional[str] = None
        self.link_text_buf: List[str] = []
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cell_buf: List[str] = []
        self.current_row: List[str] = []

        self.title = ""
        self.meta: Dict[str, str] = {}
        self.canonical_url: Optional[str] = None
        self.links: List[Dict[str, str]] = []
        self.headings: List[Dict[str, str]] = []
        self.tables: List[List[List[str]]] = []
        self.current_table: List[List[str]] = []
        self.text_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        amap = {k.lower(): (v or "") for k, v in attrs}
        if t in ("script", "style", "noscript"):
            self.in_script_or_style += 1
            return
        if t == "title":
            self.in_title = True
        elif t == "meta":
            key = amap.get("property") or amap.get("name")
            val = amap.get("content", "")
            if key and val:
                self.meta[key] = val
        elif t == "link":
            rel = amap.get("rel", "").lower()
            if "canonical" in rel and amap.get("href"):
                self.canonical_url = amap["href"]
        elif t == "a":
            self.current_link_href = amap.get("href", "")
            self.link_text_buf = []
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.current_heading = t
            self.heading_buf = []
        elif t == "table":
            self.in_table = True
            self.current_table = []
        elif t == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif t in ("td", "th") and self.in_row:
            self.in_cell = True
            self.cell_buf = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript") and self.in_script_or_style > 0:
            self.in_script_or_style -= 1
            return
        if t == "title":
            self.in_title = False
        elif t == "a" and self.current_link_href is not None:
            self.links.append(
                {
                    "href": self.current_link_href.strip(),
                    "anchor_text": " ".join("".join(self.link_text_buf).split()),
                }
            )
            self.current_link_href = None
        elif self.current_heading == t:
            self.headings.append(
                {"level": t, "text": " ".join("".join(self.heading_buf).split())}
            )
            self.current_heading = None
        elif t in ("td", "th") and self.in_cell:
            self.current_row.append(" ".join("".join(self.cell_buf).split()))
            self.in_cell = False
        elif t == "tr" and self.in_row:
            if self.current_row:
                self.current_table.append(self.current_row)
            self.in_row = False
        elif t == "table" and self.in_table:
            if self.current_table:
                self.tables.append(self.current_table)
            self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.in_script_or_style > 0:
            return
        if self.in_title:
            self.title += data
        if self.current_link_href is not None:
            self.link_text_buf.append(data)
        if self.current_heading is not None:
            self.heading_buf.append(data)
        if self.in_cell:
            self.cell_buf.append(data)
        stripped = data.strip()
        if stripped:
            self.text_parts.append(stripped)


def extract_document_file(path: Path, max_bytes: int = 26214400) -> Dict[str, Any]:
    """Native multi-format document extractor (.txt, .md, .html, .csv, .json, .xml, .docx, .xlsx, .pptx, .pdf)."""
    size = path.stat().st_size
    if size > max_bytes:
        raise ValidationError(
            f"File exceeds max_input_bytes ({size} > {max_bytes})",
            {"file": path.name, "bytes": size},
        )
    ext = path.suffix.lower()
    digest = sha256_file(path)

    if ext in (".txt", ".md", ".rst", ".log", ".py", ".js", ".ts", ".sql", ".sh", ".yaml", ".yml"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        headings = re.findall(r"^(#{1,6}\s+.+)$", raw, flags=re.M)
        return {
            "format": ext.lstrip(".") or "text",
            "extractor": "stdlib-utf8-reader",
            "bytes": size,
            "sha256": digest,
            "text": raw,
            "headings": headings[:50],
            "tables": [],
            "metadata": {"line_count": len(raw.splitlines()), "word_count": len(raw.split())},
        }

    if ext in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        p = _HTMLIntelligenceParser()
        p.feed(raw)
        p.close()
        body_text = "\n".join(p.text_parts)
        return {
            "format": "html",
            "extractor": "stdlib-html-parser",
            "bytes": size,
            "sha256": digest,
            "text": body_text,
            "headings": [h["text"] for h in p.headings],
            "tables": p.tables,
            "metadata": {"title": p.title.strip(), "meta": p.meta, "link_count": len(p.links)},
        }

    if ext in (".csv", ".tsv"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        delim = "\t" if ext == ".tsv" else ","
        rows = list(csv.reader(io.StringIO(raw), delimiter=delim))
        preview_lines = [" | ".join(r) for r in rows[:100]]
        return {
            "format": ext.lstrip("."),
            "extractor": "stdlib-csv-reader",
            "bytes": size,
            "sha256": digest,
            "text": "\n".join(preview_lines),
            "headings": rows[0] if rows else [],
            "tables": [rows[:200]] if rows else [],
            "metadata": {"row_count": len(rows), "column_count": len(rows[0]) if rows else 0},
        }

    if ext == ".json":
        raw = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(raw)
        top_keys = list(parsed.keys()) if isinstance(parsed, dict) else []
        return {
            "format": "json",
            "extractor": "stdlib-json-reader",
            "bytes": size,
            "sha256": digest,
            "text": raw[:20000],
            "headings": top_keys,
            "tables": [],
            "metadata": {"root_type": type(parsed).__name__, "top_keys": top_keys[:30]},
        }

    if ext == ".xml":
        raw = path.read_text(encoding="utf-8", errors="replace")
        root = ET.fromstring(raw)
        texts = [t.strip() for t in root.itertext() if t and t.strip()]
        return {
            "format": "xml",
            "extractor": "stdlib-xml-elementtree",
            "bytes": size,
            "sha256": digest,
            "text": "\n".join(texts),
            "headings": [root.tag],
            "tables": [],
            "metadata": {"root_tag": root.tag, "text_segments": len(texts)},
        }

    if ext == ".docx":
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        paragraphs: List[str] = []
        for elem in root.iter():
            if elem.tag.endswith("}p"):
                p_text = "".join(t.text or "" for t in elem.iter() if t.tag.endswith("}t")).strip()
                if p_text:
                    paragraphs.append(p_text)
        joined = "\n".join(paragraphs)
        return {
            "format": "docx",
            "extractor": "stdlib-ooxml-docx-reader",
            "bytes": size,
            "sha256": digest,
            "text": joined,
            "headings": paragraphs[:10],
            "tables": [],
            "metadata": {"paragraph_count": len(paragraphs), "word_count": len(joined.split())},
        }

    if ext == ".xlsx":
        with zipfile.ZipFile(path, "r") as zf:
            shared: List[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sroot.iter():
                    if si.tag.endswith("}si"):
                        val = "".join(t.text or "" for t in si.iter() if t.tag.endswith("}t"))
                        shared.append(val)
            sheet_names = sorted(
                n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            )
            tables: List[List[List[str]]] = []
            for sname in sheet_names:
                wroot = ET.fromstring(zf.read(sname))
                rows_out: List[List[str]] = []
                for row_el in wroot.iter():
                    if row_el.tag.endswith("}row"):
                        vals: List[str] = []
                        for c_el in row_el:
                            if not c_el.tag.endswith("}c"):
                                continue
                            ctype = c_el.attrib.get("t", "")
                            raw_v = ""
                            for sub in c_el.iter():
                                if sub.tag.endswith("}v") and sub.text:
                                    raw_v = sub.text
                                elif sub.tag.endswith("}t") and sub.text and ctype == "inlineStr":
                                    raw_v += sub.text
                            if ctype == "s" and raw_v.isdigit() and int(raw_v) < len(shared):
                                vals.append(shared[int(raw_v)])
                            else:
                                vals.append(raw_v)
                        if any(vals):
                            rows_out.append(vals)
                if rows_out:
                    tables.append(rows_out)
        flat_lines = [" | ".join(r) for tbl in tables for r in tbl[:100]]
        return {
            "format": "xlsx",
            "extractor": "stdlib-ooxml-xlsx-reader",
            "bytes": size,
            "sha256": digest,
            "text": "\n".join(flat_lines),
            "headings": tables[0][0] if tables and tables[0] else [],
            "tables": tables,
            "metadata": {"sheet_count": len(sheet_names), "extracted_tables": len(tables)},
        }

    if ext == ".pptx":
        slides_text: List[str] = []
        with zipfile.ZipFile(path, "r") as zf:
            slide_files = sorted(
                n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
            for sf in slide_files:
                sroot = ET.fromstring(zf.read(sf))
                tokens = [el.text.strip() for el in sroot.iter() if el.tag.endswith("}t") and el.text and el.text.strip()]
                if tokens:
                    slides_text.append(" ".join(tokens))
        return {
            "format": "pptx",
            "extractor": "stdlib-ooxml-pptx-reader",
            "bytes": size,
            "sha256": digest,
            "text": "\n\n".join(slides_text),
            "headings": [s[:80] for s in slides_text[:10]],
            "tables": [],
            "metadata": {"slide_count": len(slides_text)},
        }

    if ext == ".pdf":
        # Prefer pypdf if installed; otherwise use stdlib stream/FlateDecode extractor
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(path))
            pages_txt = [pg.extract_text() or "" for pg in reader.pages]
            joined = "\n\n".join(pages_txt)
            return {
                "format": "pdf",
                "extractor": "optional-pypdf",
                "bytes": size,
                "sha256": digest,
                "text": joined,
                "headings": [ln.strip() for ln in joined.splitlines() if ln.strip()][:15],
                "tables": [],
                "metadata": {"page_count": len(reader.pages), "word_count": len(joined.split())},
            }
        except ImportError:
            raw_pdf = path.read_bytes()
            extracted_chunks: List[str] = []
            for stream_match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw_pdf, flags=re.S):
                chunk = stream_match.group(1)
                for candidate in (chunk, _safe_zlib_decompress(chunk)):
                    if not candidate:
                        continue
                    for lit in re.findall(rb"\(([^\(\)\\]{2,})\)", candidate):
                        extracted_chunks.append(lit.decode("latin-1", errors="ignore"))
            joined = " ".join(extracted_chunks)
            return {
                "format": "pdf",
                "extractor": "stdlib-pdf-stream-fallback",
                "bytes": size,
                "sha256": digest,
                "text": joined,
                "headings": [],
                "tables": [],
                "metadata": {"word_count": len(joined.split()), "optional_pypdf_installed": False},
            }

    # Generic fallback text/binary inspection
    raw_bytes = path.read_bytes()
    is_text = b"\x00" not in raw_bytes[:4096]
    text_val = raw_bytes.decode("utf-8", errors="replace") if is_text else ""
    return {
        "format": ext.lstrip(".") or "binary",
        "extractor": "stdlib-generic-inspector",
        "bytes": size,
        "sha256": digest,
        "text": text_val[:10000],
        "headings": [],
        "tables": [],
        "metadata": {"is_probable_text": is_text},
    }


def _safe_zlib_decompress(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        return b""


def inspect_visual_evidence_bytes(data: bytes, filename: str = "inline") -> Dict[str, Any]:
    """Native binary header and diagram structure analyzer for PNG, JPEG, GIF, BMP, WEBP, SVG, and Mermaid/DOT."""
    digest = sha256_bytes(data)
    size = len(data)
    ext = Path(filename).suffix.lower()

    # PNG
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
        text_chunks: List[str] = []
        pos = 8
        while pos + 8 <= len(data):
            clen = struct.unpack(">I", data[pos:pos + 4])[0]
            ctype = data[pos + 4:pos + 8]
            cdata = data[pos + 8:pos + 8 + clen]
            if ctype == b"tEXt":
                text_chunks.append(cdata.decode("latin-1", errors="ignore").replace("\x00", ": "))
            pos += 12 + clen
            if ctype == b"IEND":
                break
        return {
            "media_type": "image/png",
            "width": width,
            "height": height,
            "aspect_ratio": round(width / height, 4) if height else None,
            "bit_depth": bit_depth,
            "color_type_code": color_type,
            "embedded_text_chunks": text_chunks,
            "bytes": size,
            "sha256": digest,
        }

    # GIF
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return {
            "media_type": "image/gif",
            "width": width,
            "height": height,
            "aspect_ratio": round(width / height, 4) if height else None,
            "bytes": size,
            "sha256": digest,
        }

    # BMP
    if data.startswith(b"BM") and len(data) >= 26:
        width, height = struct.unpack("<ii", data[18:26])
        return {
            "media_type": "image/bmp",
            "width": abs(width),
            "height": abs(height),
            "bytes": size,
            "sha256": digest,
        }

    # JPEG SOF marker scan
    if data.startswith(b"\xff\xd8"):
        idx = 2
        width = height = 0
        while idx + 9 < len(data):
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height, width = struct.unpack(">HH", data[idx + 5:idx + 9])
                break
            if marker in (0xD8, 0xD9):
                idx += 2
                continue
            seg_len = struct.unpack(">H", data[idx + 2:idx + 4])[0]
            idx += 2 + seg_len
        return {
            "media_type": "image/jpeg",
            "width": width,
            "height": height,
            "aspect_ratio": round(width / height, 4) if height else None,
            "bytes": size,
            "sha256": digest,
        }

    # SVG or diagram text
    text_str = data.decode("utf-8", errors="ignore").strip()
    if "<svg" in text_str[:500].lower() or ext == ".svg":
        root = ET.fromstring(text_str)
        labels = [el.text.strip() for el in root.iter() if el.tag.endswith("text") and el.text and el.text.strip()]
        return {
            "media_type": "image/svg+xml",
            "width": root.attrib.get("width"),
            "height": root.attrib.get("height"),
            "viewBox": root.attrib.get("viewBox"),
            "text_labels": labels,
            "element_count": sum(1 for _ in root.iter()),
            "bytes": size,
            "sha256": digest,
        }

    # Mermaid or DOT diagram syntax
    edges = re.findall(
        r"([A-Za-z0-9_]+)(?:\[[^\]]*\])?\s*(?:-->|---|->|==>)\s*([A-Za-z0-9_]+)(?:\[[^\]]*\])?",
        text_str,
    )
    node_defs = re.findall(r"([A-Za-z0-9_]+)\[([^\]]+)\]", text_str)
    nodes = sorted(set([u for u, _ in edges] + [v for _, v in edges] + [n for n, _ in node_defs]))
    return {
        "media_type": "diagram/text",
        "diagram_nodes": nodes,
        "node_labels": {n: lbl for n, lbl in node_defs},
        "diagram_edges": [{"source": u, "target": v} for u, v in edges],
        "bytes": size,
        "sha256": digest,
    }


def parse_multimedia_or_transcript(data: bytes, filename: str = "sample.srt") -> Dict[str, Any]:
    """Native WAV/MP3/MP4 binary header & SRT/VTT time-coded transcript analyzer."""
    digest = sha256_bytes(data)
    ext = Path(filename).suffix.lower()

    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        with wave.open(io.BytesIO(data), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = round(frames / float(rate), 3) if rate else 0.0
            return {
                "media_type": "audio/wav",
                "channels": wf.getnchannels(),
                "sample_width_bytes": wf.getsampwidth(),
                "sample_rate_hz": rate,
                "frame_count": frames,
                "duration_seconds": duration,
                "sha256": digest,
            }

    if data.startswith(b"ID3") or ext == ".mp3":
        id3_ver = f"2.{data[3]}.{data[4]}" if data.startswith(b"ID3") and len(data) > 5 else "none"
        return {
            "media_type": "audio/mpeg",
            "id3_version": id3_ver,
            "bytes": len(data),
            "sha256": digest,
        }

    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12].decode("ascii", errors="replace")
        return {
            "media_type": "video/mp4",
            "major_brand": brand,
            "bytes": len(data),
            "sha256": digest,
        }

    # Parse SRT or WebVTT transcript
    text = data.decode("utf-8", errors="replace")
    segments: List[Dict[str, Any]] = []
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    for blk in blocks:
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip() and ln.strip() != "WEBVTT"]
        if not lines:
            continue
        ts_line_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ts_line_idx is not None:
            times = [p.strip() for p in lines[ts_line_idx].split("-->")]
            utterance = " ".join(lines[ts_line_idx + 1:])
            segments.append(
                {
                    "segment_index": len(segments) + 1,
                    "start": times[0],
                    "end": times[1] if len(times) > 1 else times[0],
                    "text": utterance,
                }
            )
    return {
        "media_type": "transcript/timecoded",
        "segment_count": len(segments),
        "segments": segments,
        "full_transcript": " ".join(s["text"] for s in segments) if segments else text,
        "sha256": digest,
    }


def execute_category_1_to_15(
    category_id: int,
    context: Dict[str, Any],
    config: RuntimeConfig,
    governor: SafetyGovernor,
    tracker: TruthfulnessTracker,
) -> Dict[str, Any]:
    """Execute concrete domain logic for Categories 1 through 15."""
    bundle: Dict[str, Any] = context["sample_bundle"]
    query: str = context.get("query") or bundle.get("research_question", "Reproducible research workspace evaluation")
    input_path: Optional[Path] = context.get("input_path")
    input_text: Optional[str] = context.get("input_text")
    params: Dict[str, Any] = context.get("params", {})

    # Category 1: Research Strategy and Investigation Design
    if category_id == 1:
        terms = [t for t in _tokenize(query) if len(t) > 3][:8]
        primary_terms = terms[:4] or ["research", "evidence", "workspace"]
        sub_questions = [
            f"What empirical metrics and benchmarks govern {' '.join(primary_terms[:2])}?",
            f"Which architectural controls preserve provenance and safety for {' '.join(primary_terms[-2:])}?",
            "What contradictory claims, edge cases, or reproducibility limits appear across independent sources?",
        ]
        boolean_queries = [
            f"({' AND '.join(primary_terms[:3])}) AND (benchmark OR evaluation OR empirical)",
            f"\"{' '.join(primary_terms[:2])}\" AND (provenance OR reproducibility OR audit)",
        ]
        return {
            "research_question": query,
            "key_concepts": terms,
            "sub_questions": sub_questions,
            "search_query_matrix": boolean_queries,
            "evidence_requirements": {
                "minimum_independent_sources": 2,
                "require_sha256_provenance": True,
                "falsification_criteria": "Flag claims where independent sources diverge on numeric metrics by >5%.",
            },
            "investigation_stages": [
                {"stage": 1, "name": "scoping-and-query-plan", "owner_role": "planner"},
                {"stage": 2, "name": "source-ingestion-and-cleaning", "owner_role": "operator"},
                {"stage": 3, "name": "hybrid-indexing-and-verification", "owner_role": "verifier"},
                {"stage": 4, "name": "synthesis-and-deliverable-packaging", "owner_role": "packager"},
            ],
        }

    # Category 2: Web Search and Discovery
    if category_id == 2:
        q_tokens = set(_tokenize(query))
        sources = params.get("sources") or bundle.get("sources", [])
        ranked_hits: List[Dict[str, Any]] = []
        for src in sources:
            hay = f"{src.get('title', '')} {src.get('content', '')}"
            s_tokens = _tokenize(hay)
            overlap = sorted(q_tokens.intersection(s_tokens))
            score = round(len(overlap) / max(1, len(q_tokens)), 4)
            if score > 0 or not q_tokens:
                ranked_hits.append(
                    {
                        "source_id": src.get("source_id"),
                        "title": src.get("title"),
                        "url": src.get("url"),
                        "matched_terms": overlap,
                        "relevance_score": score,
                        "discovery_mode": "offline-verified-corpus",
                    }
                )
        ranked_hits.sort(key=lambda x: x["relevance_score"], reverse=True)

        live_endpoint = os.environ.get(
            config.providers.get("search_provider", {}).get("endpoint_env", "RWC_SEARCH_API_URL"), ""
        ).strip()
        live_results: List[Dict[str, Any]] = []
        if tracker.network_enabled and live_endpoint:
            governor.validate_url(live_endpoint)
            sep = "&" if "?" in live_endpoint else "?"
            req_url = f"{live_endpoint}{sep}q={urllib.parse.quote_plus(query)}"
            req = urllib.request.Request(
                req_url, headers={"User-Agent": config.providers.get("user_agent", "rwc/1.0")}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read(1048576).decode("utf-8", errors="replace"))
                tracker.network_used = True
                tracker.searches_executed += 1
                if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                    live_results = payload["results"][:10]
        else:
            tracker.record_skip(
                "live_web_search",
                "Network disabled by default or RWC_SEARCH_API_URL unconfigured; executed deterministic local corpus discovery.",
            )

        return {
            "query": query,
            "query_variants": [
                query,
                f"{query} empirical benchmark",
                f"{query} specification documentation",
            ],
            "local_corpus_hits": ranked_hits,
            "live_endpoint_hits": live_results,
            "live_search_configured": bool(live_endpoint),
        }

    # Category 3: Website and Link Intelligence
    if category_id == 3:
        raw_html = (
            input_path.read_text(encoding="utf-8", errors="replace")
            if input_path and input_path.is_file()
            else (input_text or bundle.get("html_page_sample", ""))
        )
        base_url = params.get("base_url", "https://example.org/portal")
        base_host = urllib.parse.urlsplit(base_url).hostname or "example.org"
        parser = _HTMLIntelligenceParser()
        parser.feed(raw_html)
        parser.close()

        internal_links: List[Dict[str, str]] = []
        external_links: List[Dict[str, str]] = []
        malformed_links: List[str] = []
        domain_counts: Dict[str, int] = {}

        for lk in parser.links:
            href = lk["href"]
            if not href or href.startswith("javascript:"):
                malformed_links.append(href)
                continue
            resolved = urllib.parse.urljoin(base_url, href)
            sp = urllib.parse.urlsplit(resolved)
            host = (sp.hostname or "").lower()
            if host:
                domain_counts[host] = domain_counts.get(host, 0) + 1
            entry = {"href": href, "resolved_url": resolved, "anchor_text": lk["anchor_text"]}
            if host == base_host:
                internal_links.append(entry)
            else:
                external_links.append(entry)

        return {
            "page_title": parser.title.strip(),
            "canonical_url": parser.canonical_url,
            "open_graph_and_meta": parser.meta,
            "internal_link_count": len(internal_links),
            "external_link_count": len(external_links),
            "internal_links": internal_links,
            "external_links": external_links,
            "domain_distribution": domain_counts,
            "malformed_or_script_links": malformed_links,
        }

    # Category 4: Documentation and Technical Reference Research
    if category_id == 4:
        target = input_path
        if target and target.is_file():
            doc_text = target.read_text(encoding="utf-8", errors="replace")
            doc_name = to_portable_path(target)
        else:
            doc_text = input_text or (
                "# Workspace API Reference\n\nUse `--config <path>` and `--output <dir>`.\n"
                "```python\ndef run_pipeline(config_path: str, timeout: int = 30) -> dict:\n    pass\n```\n"
            )
            doc_name = "inline-technical-reference"

        cli_flags = sorted(set(re.findall(r"--[a-z0-9][a-z0-9\-]+", doc_text)))
        py_defs = re.findall(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*\s*(?:\([^)]*\))?)", doc_text, flags=re.M)
        code_blocks = re.findall(r"```([a-zA-Z0-9_\-]*)\n(.*?)```", doc_text, flags=re.S)
        headings = re.findall(r"^(#{1,4}\s+.+)$", doc_text, flags=re.M)
        return {
            "source_reference": doc_name,
            "heading_outline": headings[:40],
            "cli_flags_detected": cli_flags,
            "symbol_signatures": py_defs[:50],
            "code_example_count": len(code_blocks),
            "code_languages": sorted(set(lang or "plain" for lang, _ in code_blocks)),
        }

    # Category 5: Scholarly and Scientific Research
    if category_id == 5:
        bib_raw = (
            input_path.read_text(encoding="utf-8", errors="replace")
            if input_path and input_path.is_file()
            else (input_text or bundle.get("bibtex_sample", ""))
        )
        entries: List[Dict[str, Any]] = []
        for match in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.+?)\n\}", bib_raw, flags=re.S):
            etype, cite_key, body = match.group(1).lower(), match.group(2).strip(), match.group(3)
            fields: Dict[str, str] = {}
            for fm in re.finditer(r"(\w+)\s*=\s*[\{\"]([^\}\"]+)[\}\"]", body):
                fields[fm.group(1).lower()] = " ".join(fm.group(2).split())
            entries.append(
                {
                    "entry_type": etype,
                    "citation_key": cite_key,
                    "title": fields.get("title", ""),
                    "authors": [a.strip() for a in fields.get("author", "").split(" and ") if a.strip()],
                    "year": fields.get("year"),
                    "venue": fields.get("journal") or fields.get("booktitle", ""),
                    "doi": fields.get("doi"),
                }
            )
        dois = sorted(set(re.findall(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", bib_raw)))
        return {
            "parsed_entry_count": len(entries),
            "scholarly_entries": entries,
            "dois_detected": dois,
            "years_covered": sorted(set(e["year"] for e in entries if e.get("year"))),
        }

    # Category 6: News, Public Records, and Current Intelligence
    if category_id == 6:
        xml_raw = (
            input_path.read_text(encoding="utf-8", errors="replace")
            if input_path and input_path.is_file()
            else (input_text or bundle.get("rss_xml_sample", ""))
        )
        items: List[Dict[str, Any]] = []
        root = ET.fromstring(xml_raw)
        for item_el in root.iter():
            tag_low = item_el.tag.lower()
            if tag_low.endswith("item") or tag_low.endswith("entry"):
                title = ""
                link = ""
                pub_date = ""
                summary = ""
                for child in item_el:
                    ct = child.tag.lower().split("}")[-1]
                    if ct == "title":
                        title = (child.text or "").strip()
                    elif ct == "link":
                        link = (child.text or child.attrib.get("href", "")).strip()
                    elif ct in ("pubdate", "published", "updated"):
                        pub_date = (child.text or "").strip()
                    elif ct in ("description", "summary", "content"):
                        summary = (child.text or "").strip()
                items.append({"title": title, "link": link, "published": pub_date, "summary": summary})

        # Detect near-duplicate syndication using word 3-gram Jaccard similarity
        duplicates: List[Dict[str, Any]] = []
        shingle_sets: List[Set[Tuple[str, ...]]] = []
        for it in items:
            toks = _tokenize(f"{it['title']} {it['summary']}")
            shingles = set(tuple(toks[i:i + 3]) for i in range(max(0, len(toks) - 2))) or {tuple(toks)}
            shingle_sets.append(shingles)
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                u = shingle_sets[i].union(shingle_sets[j])
                sim = len(shingle_sets[i].intersection(shingle_sets[j])) / max(1, len(u))
                if sim >= 0.6:
                    duplicates.append({"item_a": i, "item_b": j, "similarity": round(sim, 3)})

        return {
            "feed_item_count": len(items),
            "timeline_items": items,
            "near_duplicate_pairs": duplicates,
        }

    # Category 7: Enterprise and Connected Workspace Search
    if category_id == 7:
        connectors_env = os.environ.get("RWC_CONNECTORS_JSON", "").strip()
        configured_connectors: List[str] = []
        if connectors_env:
            try:
                configured_connectors = list(json.loads(connectors_env).keys())
            except json.JSONDecodeError:
                configured_connectors = []
        local_sources = bundle.get("sources", [])
        q_terms = set(_tokenize(query))
        matches = [
            {"source_id": s["source_id"], "title": s["title"], "license": s.get("license", "unspecified")}
            for s in local_sources
            if q_terms.intersection(_tokenize(f"{s['title']} {s['content']}"))
        ]
        return {
            "query": query,
            "external_enterprise_connectors_configured": configured_connectors,
            "unavailable_external_connectors_note": (
                "No external Slack/Notion/Jira credentials configured in RWC_CONNECTORS_JSON; searched mounted local workspace records only."
                if not configured_connectors
                else "Configured connectors loaded."
            ),
            "mounted_workspace_matches": matches,
        }

    # Category 8: Local File and Folder Research
    if category_id == 8:
        scan_root = input_path if (input_path and input_path.exists()) else Path(__file__).resolve().parent
        max_files = int(config.limits.get("max_files_scanned", 2000))
        max_depth = int(config.limits.get("max_directory_depth", 8))
        pattern = params.get("pattern", "")
        rx = re.compile(pattern, re.I) if pattern else None

        files_info: List[Dict[str, Any]] = []
        by_hash: Dict[str, List[str]] = {}
        pattern_hits: List[Dict[str, Any]] = []

        if scan_root.is_file():
            candidates = [scan_root]
            base = scan_root.parent
        else:
            base = scan_root
            candidates = []
            for root_str, dirs, filenames in os.walk(scan_root, followlinks=False):
                rel_depth = len(Path(root_str).relative_to(scan_root).parts)
                if rel_depth >= max_depth:
                    dirs[:] = []
                dirs[:] = [d for d in sorted(dirs) if d != "__pycache__"]
                for fn in sorted(filenames):
                    candidates.append(Path(root_str) / fn)
                    if len(candidates) >= max_files:
                        break
                if len(candidates) >= max_files:
                    break

        for fp in candidates:
            if not fp.is_file():
                continue
            rel = to_portable_path(fp, base_dir=base)
            h = sha256_file(fp)
            sz = fp.stat().st_size
            by_hash.setdefault(h, []).append(rel)
            files_info.append({"path": rel, "bytes": sz, "sha256": h, "extension": fp.suffix.lower()})
            if rx and sz <= 2097152:
                txt = fp.read_text(encoding="utf-8", errors="ignore")
                for ln_no, ln in enumerate(txt.splitlines(), start=1):
                    if rx.search(ln):
                        pattern_hits.append({"path": rel, "line": ln_no, "snippet": ln.strip()[:140]})
                        if len(pattern_hits) >= 50:
                            break

        duplicate_clusters = [paths for paths in by_hash.values() if len(paths) > 1]
        return {
            "scanned_file_count": len(files_info),
            "total_bytes": sum(f["bytes"] for f in files_info),
            "files": files_info[:100],
            "duplicate_hash_clusters": duplicate_clusters,
            "pattern_matches": pattern_hits,
        }

    # Category 9: Document Reading and Extraction
    if category_id == 9:
        if input_path and input_path.is_file():
            extracted = extract_document_file(input_path, max_bytes=int(config.limits.get("max_input_bytes", 26214400)))
            extracted["source_path"] = to_portable_path(input_path)
            return extracted
        raw_txt = input_text or bundle["sources"][0]["content"]
        return {
            "format": "inline-text",
            "extractor": "stdlib-inline-reader",
            "bytes": len(raw_txt.encode("utf-8")),
            "sha256": sha256_text(raw_txt),
            "text": raw_txt,
            "headings": [],
            "tables": [],
            "metadata": {"word_count": len(raw_txt.split())},
        }

    # Category 10: OCR, Scans, and Visual Text Recovery
    if category_id == 10:
        tesseract_bin = shutil.which("tesseract")
        raw_ocr_text = (
            input_path.read_text(encoding="utf-8", errors="ignore")
            if (input_path and input_path.is_file() and input_path.suffix.lower() in (".txt", ".md", ".svg"))
            else (input_text or "Repro-\nducible   work\ufb01lows   preserve  SHA-256   prove-\nnance  across   scanned   records.")
        )
        # Apply deterministic OCR text recovery: ligatures, hyphenated line wraps, spacing normalization
        repaired = raw_ocr_text.replace("\ufb01", "fi").replace("\ufb02", "fl").replace("\u2014", "--")
        repaired = re.sub(r"([A-Za-z])-\s*\n\s*([a-z])", r"\1\2", repaired)
        repaired = re.sub(r"[ \t]{2,}", " ", repaired).strip()

        svg_labels: List[str] = []
        if input_path and input_path.is_file() and input_path.suffix.lower() in (".png", ".svg"):
            vis = inspect_visual_evidence_bytes(input_path.read_bytes(), input_path.name)
            svg_labels = vis.get("text_labels") or vis.get("embedded_text_chunks") or []

        return {
            "tesseract_cli_available": bool(tesseract_bin),
            "ocr_engine_used": "post-ocr-text-normalizer-and-embedded-chunk-reader",
            "recovered_text": repaired,
            "embedded_visual_labels": svg_labels,
            "limitations_note": (
                "Raster OCR binary (tesseract) is not installed in this environment; embedded text chunks and post-OCR normalization executed."
                if not tesseract_bin
                else "Tesseract binary detected on PATH."
            ),
        }

    # Category 11: Image, Diagram, and Visual Evidence Analysis
    if category_id == 11:
        if input_path and input_path.is_file():
            return inspect_visual_evidence_bytes(input_path.read_bytes(), input_path.name)
        sample_diag = (input_text or bundle.get("mermaid_diagram_sample", "")).encode("utf-8")
        return inspect_visual_evidence_bytes(sample_diag, "diagram.mmd")

    # Category 12: Audio, Video, and Multimedia Research
    if category_id == 12:
        if input_path and input_path.is_file():
            res = parse_multimedia_or_transcript(input_path.read_bytes(), input_path.name)
        else:
            srt_bytes = (input_text or bundle.get("srt_transcript_sample", "")).encode("utf-8")
            res = parse_multimedia_or_transcript(srt_bytes, "sample.srt")
        res["ffprobe_available"] = bool(shutil.which("ffprobe"))
        return res

    # Category 13: Data Acquisition and Download Management
    if category_id == 13:
        urls: List[str] = params.get("urls") or [s["url"] for s in bundle.get("sources", []) if s.get("url")]
        max_dl = int(config.limits.get("max_download_bytes", 10485760))
        manifest_entries: List[Dict[str, Any]] = []
        for u in urls:
            parsed_u = governor.validate_url(u, resolve_dns=tracker.network_enabled)
            if tracker.network_enabled and params.get("execute_download"):
                governor.require_approval("network_download_when_enabled", f"Downloading external URL {u}")

                def _fetch() -> Tuple[bytes, Dict[str, str]]:
                    req = urllib.request.Request(
                        u, headers={"User-Agent": config.providers.get("user_agent", "rwc/1.0")}
                    )
                    with urllib.request.urlopen(req, timeout=15) as r:
                        body = r.read(max_dl + 1)
                        if len(body) > max_dl:
                            raise ValidationError(f"Download exceeded max_download_bytes ({max_dl})")
                        hdrs = {k.lower(): v for k, v in r.headers.items()}
                        return body, hdrs

                (body, hdrs), attempts = retry_bounded(_fetch, max_retries=int(config.limits.get("max_retries", 2)))
                tracker.network_used = True
                tracker.downloads_completed += 1
                manifest_entries.append(
                    {
                        "url": u,
                        "host": parsed_u.hostname,
                        "status": "downloaded",
                        "bytes": len(body),
                        "sha256": sha256_bytes(body),
                        "etag": hdrs.get("etag"),
                        "attempts": attempts,
                    }
                )
            else:
                tracker.record_skip("remote_download", f"Validated URL policy only (offline default): {u}")
                manifest_entries.append(
                    {
                        "url": u,
                        "host": parsed_u.hostname,
                        "status": "validated_dry_run",
                        "max_bytes_limit": max_dl,
                        "ssrf_check": "passed",
                    }
                )
        return {"acquisition_count": len(manifest_entries), "entries": manifest_entries}

    # Category 14: Web APIs and Structured Data Retrieval
    if category_id == 14:
        endpoint = params.get("endpoint", "https://api.example.org/v1/benchmarks")
        governor.validate_url(endpoint, resolve_dns=False)
        records = params.get("fixture_records") or bundle.get("tabular_records", [])
        extract_field = params.get("select_field", "engine")
        projected = [r.get(extract_field) for r in records if isinstance(r, dict)]
        tracker.record_skip("external_api_request", "Used deterministic structured fixture payload (network disabled by default).")
        return {
            "endpoint_validated": endpoint,
            "mode": "offline-structured-fixture",
            "record_count": len(records),
            "selected_field": extract_field,
            "projected_values": projected,
            "schema_fields": sorted({k for r in records if isinstance(r, dict) for k in r.keys()}),
        }

    # Category 15: Web Crawling and Responsible Scraping
    if category_id == 15:
        robots_txt = params.get(
            "robots_txt",
            "User-agent: *\nDisallow: /private/\nDisallow: /admin/\nCrawl-delay: 1\n",
        )
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots_txt.splitlines())
        ua = config.providers.get("user_agent", "research-workspace-creator/1.0")
        candidate_paths = params.get("paths", ["/portal", "/docs/indexing", "/private/keys"])
        evaluations = [
            {"path": p, "allowed_by_robots": rp.can_fetch(ua, p)} for p in candidate_paths
        ]
        html_doc = input_text or bundle.get("html_page_sample", "")
        hp = _HTMLIntelligenceParser()
        hp.feed(html_doc)
        hp.close()
        return {
            "user_agent": ua,
            "crawl_delay_enforced_seconds": max(
                float(config.limits.get("crawl_delay_seconds", 0.5)),
                float(rp.crawl_delay(ua) or 0.0),
            ),
            "robots_evaluations": evaluations,
            "scraped_tables": hp.tables,
            "scraped_headings": hp.headings,
        }

    raise ValidationError(f"Unsupported category_id {category_id} in ingestion module.")
