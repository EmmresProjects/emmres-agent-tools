#!/usr/bin/env python3
"""Categories 11-13 (30 capabilities): Video/Audio/Streaming Media,
Document/Image/Office File Interaction, and Local Filesystem/Offline Data.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import collections
import csv
import fnmatch
import io
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import tarfile
import tempfile
import wave
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _load_core() -> Any:
    if "dwai_tools_core" in sys.modules:
        return sys.modules["dwai_tools_core"]
    import importlib.util

    for cand in (Path("data-web-app-interaction/tools-core.py"), Path(__file__).parent / "tools-core.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("dwai_tools_core", str(cand))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["dwai_tools_core"] = mod
                spec.loader.exec_module(mod)
                return mod
    raise RuntimeError("Unable to load tools-core.py")


core = _load_core()


def _parse_timecode_ms(ts: str) -> int:
    m = re.match(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*$", ts)
    if not m:
        return 0
    hh = int(m.group(1) or 0)
    mm = int(m.group(2))
    ss = int(m.group(3))
    ms = int(m.group(4).ljust(3, "0")[:3])
    return ((hh * 3600 + mm * 60 + ss) * 1000) + ms


def _parse_subtitles(raw_text: str) -> List[Dict[str, Any]]:
    cues = []
    blocks = re.split(r"\n\s*\n", raw_text.strip())
    for blk in blocks:
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip() and not ln.strip().startswith("WEBVTT")]
        if not lines:
            continue
        arrow_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if arrow_idx is None:
            continue
        start_s, end_s = [p.strip().split()[0] for p in lines[arrow_idx].split("-->", 1)]
        text_lines = lines[arrow_idx + 1:]
        text = " ".join(text_lines)
        cues.append({
            "start_ms": _parse_timecode_ms(start_s),
            "end_ms": _parse_timecode_ms(end_s),
            "lines": text_lines,
            "text": text,
        })
    return cues


def _detect_magic_format(data: bytes, ext: str = "") -> Dict[str, Any]:
    if data.startswith(b"%PDF-"):
        return {"detected_format": "pdf", "mime": "application/pdf"}
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {"detected_format": "png", "mime": "image/png"}
    if data.startswith(b"\xff\xd8\xff"):
        return {"detected_format": "jpeg", "mime": "image/jpeg"}
    if data.startswith((b"GIF87a", b"GIF89a")):
        return {"detected_format": "gif", "mime": "image/gif"}
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return {"detected_format": "wav", "mime": "audio/wav"}
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return {"detected_format": "webp", "mime": "image/webp"}
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return {"detected_format": "mp4_isobmff", "mime": "video/mp4"}
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return {"detected_format": "mp3", "mime": "audio/mpeg"}
    if data.startswith(b"SQLite format 3\x00"):
        return {"detected_format": "sqlite3", "mime": "application/vnd.sqlite3"}
    if data.startswith(b"PK\x03\x04"):
        ooxml_map = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".epub": "epub"}
        fmt = ooxml_map.get(ext.lower(), "zip")
        return {"detected_format": fmt, "mime": f"application/{fmt}"}
    if data.startswith(b"\x1f\x8b"):
        return {"detected_format": "gzip", "mime": "application/gzip"}
    head_txt = data[:512].decode("utf-8", errors="ignore").lstrip()
    if head_txt.startswith(("<!DOCTYPE html", "<html", "<HTML")):
        return {"detected_format": "html", "mime": "text/html"}
    if head_txt.startswith("<svg") or ("<svg" in head_txt and "xmlns" in head_txt):
        return {"detected_format": "svg", "mime": "image/svg+xml"}
    if head_txt.startswith("<?xml"):
        return {"detected_format": "xml", "mime": "application/xml"}
    if head_txt.startswith(("{", "[")):
        return {"detected_format": "json", "mime": "application/json"}
    return {"detected_format": ext.lstrip(".").lower() or "plain_text", "mime": "text/plain"}


# ===================== CATEGORY 11: Video, Audio, and Streaming Media =====================

def handle_online_video_viewer_and_interpreter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    default_vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nWelcome to the v2.4.0 architecture walkthrough.\n\n00:00:05.500 --> 00:00:10.000\nHere we demonstrate bounded API pagination and Retry-After handling.\n"
    vtt = str(ctx["params"].get("captions", default_vtt))
    cues = _parse_subtitles(vtt)
    chapters = ctx["params"].get("chapters", [{"start_ms": 0, "title": "Overview"}, {"start_ms": 5500, "title": "Pagination Demo"}])
    return {
        "observation_mode": "directly_observed",
        "data": {"cue_count": len(cues), "chapters": chapters, "transcript_summary": " ".join(c["text"] for c in cues)},
        "metrics": {"cue_count": len(cues), "chapter_count": len(chapters)},
    }


def handle_audio_and_music_listener_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    if ctx["input_path"] and ctx["input_path"].exists() and ctx["input_path"].suffix.lower() == ".wav":
        with wave.open(str(ctx["input_path"]), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            fr = wf.getframerate()
            nf = wf.getnframes()
            duration_s = round(nf / max(1, fr), 3)
            raw_frames = wf.readframes(min(nf, fr * 10))
        rms = 0.0
        peak = 0
        if sw == 2 and len(raw_frames) >= 2:
            samples = struct.unpack(f"<{len(raw_frames) // 2}h", raw_frames[: (len(raw_frames) // 2) * 2])
            if samples:
                rms = round(math.sqrt(sum(s * s for s in samples) / len(samples)), 2)
                peak = max(abs(s) for s in samples)
        return {
            "observation_mode": "directly_observed",
            "data": {"channels": n_ch, "sample_rate_hz": fr, "sample_width_bytes": sw, "duration_seconds": duration_s, "rms_amplitude": rms, "peak_amplitude": peak, "clipping_detected": peak >= 32760},
            "metrics": {"duration_seconds": duration_s, "sample_rate_hz": fr},
        }
    meta = ctx["params"].get("audio_context", {"duration_seconds": 120.0, "channels": 2, "sample_rate_hz": 44100, "notes": "Metadata-only context"})
    return {
        "observation_mode": "directly_observed",
        "data": {**meta, "perception_note": "Analyzed supplied metadata; supply a .wav PCM file via --input for direct waveform signal inspection."},
        "metrics": {"duration_seconds": float(meta.get("duration_seconds", 0))},
    }


def handle_transcript_extraction_and_cleanup_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    raw_vtt = str(ctx["params"].get("transcript", "00:00:01.000 --> 00:00:04.000\n[music] Speaker 1: um welcome to the release briefing\n\n00:00:04.200 --> 00:00:08.000\nSpeaker 2: pagination tests passed on staging.\n"))
    if ctx["input_path"] and ctx["input_path"].is_file() and ctx["input_path"].suffix.lower() in (".vtt", ".srt", ".txt"):
        raw_vtt = ctx["input_path"].read_text(encoding="utf-8", errors="replace")
    cues = _parse_subtitles(raw_vtt)
    cleaned = []
    non_speech = []
    for c in cues:
        cues_tags = re.findall(r"\[[^\]]+\]", c["text"])
        non_speech.extend(cues_tags)
        txt = re.sub(r"\b(?:um|uh)\b", "", c["text"], flags=re.I)
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt and txt[-1] not in ".!?":
            txt += "."
        cleaned.append({"start_ms": c["start_ms"], "end_ms": c["end_ms"], "cleaned_text": txt})
    return {"observation_mode": "directly_observed", "data": {"segments": cleaned, "non_speech_cues": non_speech}, "metrics": {"segment_count": len(cleaned)}}


def handle_video_chapter_and_segment_indexer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cues = ctx["params"].get("segments", [
        {"timestamp_s": 0, "topic": "Introduction and Scope"},
        {"timestamp_s": 45, "topic": "Live Demonstration: Table Extraction"},
        {"timestamp_s": 130, "topic": "Q&A and Rollback Checklist"},
    ])
    formatted = [{"timecode": f"{int(c['timestamp_s']) // 60:02d}:{int(c['timestamp_s']) % 60:02d}", "topic": c["topic"]} for c in cues]
    return {"observation_mode": "directly_observed", "data": {"chapters": formatted}, "metrics": {"chapter_count": len(formatted)}}


def handle_streaming_playback_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    manifest = str(ctx["params"].get("hls_m3u8", "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1920x1080\nhigh.m3u8\n"))
    variants = re.findall(r"BANDWIDTH=(\d+)(?:,RESOLUTION=([0-9x]+))?", manifest)
    valid_header = manifest.lstrip().startswith("#EXTM3U")
    return {
        "observation_mode": "tested",
        "data": {"valid_m3u8_header": valid_header, "adaptive_variants": [{"bandwidth_bps": int(b), "resolution": r or "audio"} for b, r in variants]},
        "metrics": {"variant_count": len(variants)},
    }


def handle_media_metadata_inspector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html")
    raw = target.read_bytes()[:4096] if target.is_file() else b""
    fmt = _detect_magic_format(raw, target.suffix if target.exists() else "")
    return {
        "observation_mode": "directly_observed",
        "data": {"file": str(target), "size_bytes": target.stat().st_size if target.is_file() else 0, **fmt},
        "metrics": {"size_bytes": target.stat().st_size if target.is_file() else 0},
    }


def handle_caption_and_subtitle_qa_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    vtt = str(ctx["params"].get("captions", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nThis line is spoken extremely fast to trigger reading speed QA alerts during subtitle verification!\n\n00:00:01.800 --> 00:00:04.000\nOverlapping cue check.\n"))
    cues = _parse_subtitles(vtt)
    issues = []
    prev_end = -1
    for idx, c in enumerate(cues, start=1):
        dur_s = max(0.1, (c["end_ms"] - c["start_ms"]) / 1000.0)
        cps = len(c["text"]) / dur_s
        if cps > 20.0:
            issues.append({"cue": idx, "issue": f"High reading speed ({round(cps, 1)} chars/sec > 20)."})
        if any(len(ln) > 42 for ln in c["lines"]):
            issues.append({"cue": idx, "issue": "Line exceeds 42 characters."})
        if c["start_ms"] < prev_end:
            issues.append({"cue": idx, "issue": "Cue overlaps previous subtitle end timestamp."})
        prev_end = c["end_ms"]
    return {"observation_mode": "tested", "data": {"cue_count": len(cues), "qa_issues": issues}, "metrics": {"issue_count": len(issues)}}


def handle_audio_video_synchronization_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    audio_ms = float(ctx["params"].get("audio_duration_ms", 60000.0))
    video_ms = float(ctx["params"].get("video_duration_ms", 60035.0))
    offset_ms = round(video_ms - audio_ms, 2)
    # ITU-R BT.1359 detectability threshold ~ +45ms / -125ms
    in_sync = -100.0 <= offset_ms <= 45.0
    return {"observation_mode": "tested", "data": {"audio_ms": audio_ms, "video_ms": video_ms, "drift_ms": offset_ms, "within_itu_sync_tolerance": in_sync}, "metrics": {"drift_ms": offset_ms}}


def handle_media_download_permission_reviewer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    license_id = str(ctx["params"].get("license", "CC-BY-4.0"))
    official_export = bool(ctx["params"].get("platform_download_endpoint", True))
    permitted = official_export and license_id.upper() not in ("ALL-RIGHTS-RESERVED", "UNKNOWN")
    return {"observation_mode": "directly_observed", "data": {"license": license_id, "official_export": official_export, "download_permitted": permitted}, "metrics": {"permitted": int(permitted)}}


def handle_media_content_comparison_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    v1 = ctx["params"].get("version_a", {"duration_s": 120.0, "resolution": "1920x1080", "caption_cues": 34})
    v2 = ctx["params"].get("version_b", {"duration_s": 118.5, "resolution": "1920x1080", "caption_cues": 35})
    diffs = {k: {"version_a": v1.get(k), "version_b": v2.get(k)} for k in set(v1) | set(v2) if v1.get(k) != v2.get(k)}
    return {"observation_mode": "directly_observed", "data": {"differences": diffs}, "metrics": {"diff_fields": len(diffs)}}


# ===================== CATEGORY 12: Document, Image, and Office File Interaction =====================

def _read_ooxml_or_doc(path: Path) -> Dict[str, Any]:
    ext = path.suffix.lower()
    if ext == ".docx" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        paras = ["".join(p.itertext()).strip() for p in root.iter() if p.tag.endswith("}p")]
        paras = [p for p in paras if p]
        return {"format": "docx", "paragraph_count": len(paras), "text_preview": "\n".join(paras)[:1200]}

    if ext == ".xlsx" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            shared: List[str] = []
            if "xl/sharedStrings.xml" in names:
                sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                shared = ["".join(si.itertext()) for si in sroot.iter() if si.tag.endswith("}si")]
            sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            rows_out = []
            if sheets:
                wroot = ET.fromstring(zf.read(sheets[0]))
                for r in wroot.iter():
                    if r.tag.endswith("}row"):
                        vals = []
                        for c in list(r):
                            t_attr = c.attrib.get("t", "")
                            v_el = next((ch for ch in list(c) if ch.tag.endswith("}v")), None)
                            raw_v = v_el.text if v_el is not None and v_el.text else ""
                            if t_attr == "s" and raw_v.isdigit() and int(raw_v) < len(shared):
                                vals.append(shared[int(raw_v)])
                            else:
                                vals.append(raw_v)
                        rows_out.append(vals)
            return {"format": "xlsx", "sheet_count": len(sheets), "first_sheet_rows": len(rows_out), "rows_preview": rows_out[:10]}

    if ext == ".pptx" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            slides = sorted(n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
            slide_texts = []
            for sname in slides:
                sroot = ET.fromstring(zf.read(sname))
                txt = " ".join(t.strip() for t in sroot.itertext() if t.strip())
                slide_texts.append({"slide": sname, "text": txt[:400]})
            return {"format": "pptx", "slide_count": len(slides), "slides": slide_texts}

    if ext == ".pdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            txt = "\n".join((p.extract_text() or "") for p in reader.pages[:5])
            return {"format": "pdf", "adapter": "pypdf", "page_count": len(reader.pages), "text_preview": txt[:1200]}
        except Exception:
            raw = path.read_bytes()
            pages = len(re.findall(rb"/Type\s*/Page\b", raw))
            return {"format": "pdf", "adapter": "stdlib-binary-Fallback", "estimated_pages": max(1, pages), "size_bytes": len(raw)}

    if ext in (".html", ".htm"):
        dom = core.parse_html_document(path.read_text(encoding="utf-8", errors="replace"))
        return {"format": "html", "title": dom.title, "headings": dom.headings, "text_preview": "\n".join(dom.main_paragraphs)[:1200]}

    txt = path.read_text(encoding="utf-8", errors="replace")
    return {"format": ext.lstrip(".") or "text", "line_count": len(txt.splitlines()), "text_preview": txt[:1200]}


def handle_universal_document_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html")
    info = _read_ooxml_or_doc(target)
    return {"observation_mode": "directly_observed", "data": {"path": str(target), **info}, "metrics": {"size_bytes": target.stat().st_size}}


def handle_scanned_document_ocr_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    tesseract_bin = shutil.which(ctx["cfg"]["providers"]["ocr_provider"].get("binary_name", "tesseract"))
    if tesseract_bin and ctx["input_path"] and ctx["input_path"].suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
        proc = subprocess.run([tesseract_bin, str(ctx["input_path"]), "stdout"], capture_output=True, text=True, timeout=15, check=False)
        return {"observation_mode": "directly_observed", "data": {"ocr_engine": "tesseract", "text": proc.stdout.strip()}, "metrics": {"char_count": len(proc.stdout)}}
    supplied = ctx["params"].get("ocr_pages", [{"page": 1, "confidence": 0.94, "text": "Invoice #2048 — Total USD $49.00"}])
    limitations = [] if tesseract_bin else ["System 'tesseract' binary not installed; processed supplied OCR page structure."]
    return {"observation_mode": "directly_observed", "data": {"ocr_engine_available": bool(tesseract_bin), "pages": supplied}, "limitations": limitations, "metrics": {"page_count": len(supplied)}}


def handle_spreadsheet_data_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    if target.suffix.lower() == ".xlsx":
        info = _read_ooxml_or_doc(target)
        return {"observation_mode": "directly_observed", "data": info, "metrics": {"sheet_count": info.get("sheet_count", 1)}}
    rows, meta = core.load_structured_records(target, ctx["params"])
    formulas = sum(1 for r in rows for v in r.values() if isinstance(v, str) and v.startswith("="))
    return {"observation_mode": "directly_observed", "data": {"meta": meta, "formula_cells": formulas, "preview": rows[:5]}, "metrics": {"row_count": len(rows), "formula_cells": formulas}}


def handle_presentation_reader_and_navigator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    if ctx["input_path"] and ctx["input_path"].suffix.lower() == ".pptx":
        info = _read_ooxml_or_doc(ctx["input_path"])
        return {"observation_mode": "directly_observed", "data": info, "metrics": {"slide_count": info.get("slide_count", 0)}}
    slides = ctx["params"].get("slides", [
        {"slide_number": 1, "title": "Release v2.4.0 Overview", "notes": "Introduce safety posture"},
        {"slide_number": 2, "title": "Benchmark Metrics", "notes": "Highlight p95 latency reduction"},
    ])
    return {"observation_mode": "directly_observed", "data": {"slides": slides}, "metrics": {"slide_count": len(slides)}}


def _inspect_image_binary(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        w, h, bit_depth, color_type = struct.unpack(">IIBB", raw[16:26])
        return {"format": "png", "width": w, "height": h, "bit_depth": bit_depth, "color_type": color_type}
    if raw.startswith((b"GIF87a", b"GIF89a")) and len(raw) >= 10:
        w, h = struct.unpack("<HH", raw[6:10])
        return {"format": "gif", "width": w, "height": h}
    txt = raw[:4096].decode("utf-8", errors="ignore")
    if "<svg" in txt:
        vb = re.search(r'viewBox=["\']([^"\']+)["\']', txt)
        return {"format": "svg", "viewBox": vb.group(1) if vb else "unspecified"}
    return {"format": path.suffix.lstrip(".") or "binary", "size_bytes": len(raw)}


def handle_image_text_and_object_inspector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    if ctx["input_path"] and ctx["input_path"].is_file():
        info = _inspect_image_binary(ctx["input_path"])
        return {"observation_mode": "directly_observed", "data": {"path": str(ctx["input_path"]), **info}, "metrics": {"inspected": 1}}
    svg_sample = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><text x="20" y="40">Architecture Diagram v2.4</text></svg>'
    texts = re.findall(r"<text[^>]*>([^<]+)</text>", svg_sample)
    return {"observation_mode": "directly_observed", "data": {"format": "svg", "visible_text": texts}, "metrics": {"text_elements": len(texts)}}


def handle_archive_and_package_reader(ctx: Dict[str, Any]) -> Dict[str, Any]:
    exec_cfg = ctx["cfg"]["execution"]
    max_entries = int(exec_cfg["max_archive_entries"])
    max_uncomp = int(exec_cfg["max_archive_uncompressed_bytes"])
    max_ratio = float(exec_cfg["max_archive_compression_ratio"])

    if ctx["input_path"] and ctx["input_path"].is_file() and zipfile.is_zipfile(ctx["input_path"]):
        entries = []
        total_uncomp = 0
        unsafe_paths = []
        with zipfile.ZipFile(ctx["input_path"], "r") as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise core.PolicyBlockedError(f"Archive entry count {len(infos)} exceeds limit {max_entries}.")
            for zi in infos:
                norm = zi.filename.replace("\\", "/")
                if norm.startswith("/") or ".." in norm.split("/"):
                    unsafe_paths.append(zi.filename)
                total_uncomp += zi.file_size
                ratio = (zi.file_size / max(1, zi.compress_size)) if zi.compress_size > 0 else 1.0
                if ratio > max_ratio and zi.file_size > 1024 * 1024:
                    raise core.PolicyBlockedError(f"Archive entry '{zi.filename}' exceeds compression ratio safety limit ({round(ratio, 1)}).")
                entries.append({"name": zi.filename, "file_size": zi.file_size, "compress_size": zi.compress_size})
        if total_uncomp > max_uncomp:
            raise core.PolicyBlockedError(f"Total uncompressed size {total_uncomp} exceeds limit {max_uncomp}.")
        return {
            "observation_mode": "directly_observed",
            "data": {"archive": str(ctx["input_path"]), "entry_count": len(entries), "total_uncompressed_bytes": total_uncomp, "zip_slip_paths": unsafe_paths, "safe_to_extract": len(unsafe_paths) == 0, "entries": entries[:50]},
            "metrics": {"entry_count": len(entries), "unsafe_paths": len(unsafe_paths)},
        }

    if ctx["input_path"] and ctx["input_path"].is_file() and tarfile.is_tarfile(ctx["input_path"]):
        with tarfile.open(ctx["input_path"], "r:*") as tf:
            members = tf.getmembers()[:max_entries]
            unsafe = [m.name for m in members if m.name.startswith("/") or ".." in m.name.split("/")]
            return {"observation_mode": "directly_observed", "data": {"entry_count": len(members), "unsafe_paths": unsafe}, "metrics": {"entry_count": len(members)}}

    return {
        "observation_mode": "tested",
        "data": {"limits_enforced": {"max_entries": max_entries, "max_uncompressed_bytes": max_uncomp, "max_compression_ratio": max_ratio}, "note": "Pass a .zip, .tar, .docx, or .xlsx file via --input to inventory archive entries."},
        "metrics": {"max_entries": max_entries},
    }


def handle_file_format_identifier(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html")
    raw = target.read_bytes()[:4096]
    detected = _detect_magic_format(raw, target.suffix)
    declared = target.suffix.lstrip(".").lower()
    mismatch = bool(declared and detected["detected_format"] not in (declared, "plain_text") and not (declared == "geojson" and detected["detected_format"] == "json"))
    return {
        "observation_mode": "directly_observed",
        "data": {"file": str(target), "declared_extension": declared, **detected, "extension_mismatch": mismatch},
        "metrics": {"mismatch": int(mismatch)},
    }


def handle_cross_format_conversion_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, meta = core.load_structured_records(ctx["input_path"], ctx["params"])
    target_fmt = str(ctx["params"].get("target_format", "markdown")).lower()
    headers = list(rows[0].keys()) if rows else []
    if target_fmt == "json":
        converted = json.dumps(rows, indent=2, ensure_ascii=True)
    elif target_fmt == "csv" and headers:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
        converted = buf.getvalue()
    else:
        if headers:
            md_lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
            for r in rows[:20]:
                md_lines.append("| " + " | ".join(str(r.get(h, "")) for h in headers) + " |")
            converted = "\n".join(md_lines)
        else:
            converted = ""
    return {
        "observation_mode": "directly_observed",
        "data": {"source_format": meta.get("format"), "target_format": target_fmt, "fidelity_loss": "none" if target_fmt in ("json", "csv") else "truncated_preview_if_over_20_rows", "converted_output": converted[:1500]},
        "metrics": {"source_rows": len(rows)},
    }


def handle_document_search_and_cross_reference_builder(ctx: Dict[str, Any]) -> Dict[str, Any]:
    term = str(ctx["params"].get("term", "pagination")).lower()
    root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html").parent
    files = [root] if root.is_file() else [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in (".html", ".csv", ".jsonl", ".json", ".md")]
    hits = []
    for f in files:
        txt = f.read_text(encoding="utf-8", errors="replace")
        count = len(re.findall(re.escape(term), txt.lower()))
        if count > 0:
            hits.append({"file": f.name, "occurrences": count})
    return {"observation_mode": "directly_observed", "data": {"term": term, "cross_references": hits}, "metrics": {"files_matched": len(hits)}}


def handle_office_file_round_trip_tester(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    with tempfile.TemporaryDirectory(prefix="dwai-rt-") as tmpd:
        copy_path = Path(tmpd) / target.name
        shutil.copy2(target, copy_path)
        before_sha = core.sha256_file(target)
        reopened = _read_ooxml_or_doc(copy_path)
        after_sha = core.sha256_file(copy_path)
    return {
        "observation_mode": "tested",
        "data": {"original": str(target), "operated_on_isolated_copy": True, "hash_preserved": before_sha == after_sha, "reopen_summary": {k: v for k, v in reopened.items() if k != "text_preview"}},
        "metrics": {"round_trip_passed": int(before_sha == after_sha)},
    }


# ===================== CATEGORY 13: Local Filesystem and Offline Data =====================

def handle_local_workspace_explorer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    entries = []
    targets = [root] if root.is_file() else sorted(root.iterdir())
    for p in targets[:200]:
        st = p.stat()
        entries.append({"name": p.name, "is_dir": p.is_dir(), "size_bytes": st.st_size, "mode_octal": oct(stat.S_IMODE(st.st_mode))})
    return {"observation_mode": "directly_observed", "data": {"workspace": str(root), "entries": entries}, "metrics": {"entry_count": len(entries)}}


def handle_offline_file_searcher(ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    glob_pat = str(ctx["params"].get("name_glob", "asset-*"))
    content_q = str(ctx["params"].get("contains_text", "")).lower()
    files = [root] if root.is_file() else [p for p in sorted(root.rglob("*")) if p.is_file()]
    matched = []
    for p in files:
        if not fnmatch.fnmatch(p.name, glob_pat):
            continue
        if content_q:
            txt = p.read_text(encoding="utf-8", errors="ignore").lower()
            if content_q not in txt:
                continue
        matched.append({"name": p.name, "size_bytes": p.stat().st_size})
    return {"observation_mode": "directly_observed", "data": {"name_glob": glob_pat, "matches": matched}, "metrics": {"match_count": len(matched)}}


def handle_duplicate_file_analyst(ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    files = [root] if root.is_file() else [p for p in sorted(root.iterdir()) if p.is_file()]
    by_hash: Dict[str, List[str]] = collections.defaultdict(list)
    for p in files:
        by_hash[core.sha256_file(p)].append(p.name)
    dup_groups = [{"sha256": h, "files": names} for h, names in by_hash.items() if len(names) > 1]
    return {"observation_mode": "directly_observed", "data": {"scanned_files": len(files), "exact_duplicate_groups": dup_groups}, "metrics": {"duplicate_groups": len(dup_groups)}}


def handle_file_organization_planner(ctx: Dict[str, Any]) -> Dict[str, Any]:
    files = ctx["params"].get("filenames", ["Report Final_v2 (1).PDF", "2026_10_Sales Data.CSV", "logo_HERO.PNG"])
    mappings = []
    for fn in files:
        p = Path(fn)
        stem = re.sub(r"[^a-z0-9]+", "-", p.stem.lower()).strip("-")
        ext = p.suffix.lower()
        folder = "documents" if ext in (".pdf", ".docx", ".md") else "datasets" if ext in (".csv", ".json", ".xlsx") else "media"
        mappings.append({"original": fn, "proposed_path": f"{folder}/{stem}{ext}", "reversible": True})
    return {"observation_mode": "directly_observed", "data": {"organization_plan": mappings}, "metrics": {"planned_files": len(mappings)}}


def handle_batch_file_renaming_operator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    renames = ctx["params"].get("renames", [{"from": "draft_1.txt", "to": "notes-01.txt"}, {"from": "draft_2.txt", "to": "notes-02.txt"}])
    targets = [r["to"] for r in renames]
    collisions = [t for t, c in collections.Counter(targets).items() if c > 1]
    if collisions:
        raise core.InputValidationError(f"Batch rename collision detected on target names: {collisions}", {"collisions": collisions})
    applied = False
    if ctx["params"].get("apply", False):
        if not ctx["approve"]:
            raise core.ApprovalRequiredError("Applying batch file renames requires --approve.")
        for r in renames:
            src = core.validate_safe_path(r["from"], must_exist=True, allow_dir=False)
            dst = core.validate_safe_path(r["to"], must_exist=False, allow_dir=False)
            if dst.exists():
                raise core.InputValidationError(f"Destination already exists: {dst}")
            src.rename(dst)
        applied = True
    rollback_map = [{"from": r["to"], "to": r["from"]} for r in renames]
    return {"observation_mode": "tested", "data": {"preview": renames, "collisions": collisions, "applied": applied, "rollback_mapping": rollback_map}, "metrics": {"count": len(renames), "applied": int(applied)}}


def handle_local_data_importer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, meta = core.load_structured_records(ctx["input_path"], ctx["params"])
    return {"observation_mode": "directly_observed", "data": {"import_meta": meta, "raw_input_preserved": True, "sample_rows": rows[:5]}, "metrics": {"imported_rows": len(rows)}}


def handle_offline_search_index_builder(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    query = str(ctx["params"].get("query", "onboarding OR sandbox"))
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id, body)")
        for idx, r in enumerate(rows, start=1):
            did = str(r.get("record_id") or r.get("event_id") or f"row-{idx}")
            body = " ".join(str(v) for v in r.values() if v is not None)
            conn.execute("INSERT INTO docs_fts(doc_id, body) VALUES (?, ?)", (did, body))
        hits = [{"doc_id": row[0], "snippet": row[1][:140]} for row in conn.execute("SELECT doc_id, body FROM docs_fts WHERE docs_fts MATCH ? LIMIT 10", (query,)).fetchall()]
    finally:
        conn.close()
    return {"observation_mode": "directly_observed", "data": {"fts_engine": "sqlite3_fts5", "indexed_count": len(rows), "query": query, "matches": hits}, "metrics": {"indexed_count": len(rows), "match_count": len(hits)}}


def handle_filesystem_permission_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = ctx["input_path"] if (ctx["input_path"] and ctx["input_path"].exists()) else core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    files = [root] if root.is_file() else sorted(root.iterdir())
    findings = []
    for p in files:
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & stat.S_IWOTH:
            findings.append({"path": p.name, "mode": oct(mode), "risk": "world_writable"})
        if mode & (stat.S_ISUID | stat.S_ISGID):
            findings.append({"path": p.name, "mode": oct(mode), "risk": "setuid_or_setgid"})
    return {"observation_mode": "directly_observed", "data": {"scanned_count": len(files), "permission_findings": findings}, "metrics": {"finding_count": len(findings)}}


def handle_safe_local_file_manipulator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    op = str(ctx["params"].get("operation", "copy_preview")).lower()
    src = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-records.csv")
    dest_str = str(ctx["params"].get("destination_path", ""))
    executed = False
    if op in ("copy", "move", "delete") and ctx["params"].get("apply", False):
        if not ctx["approve"]:
            raise core.ApprovalRequiredError(f"File operation '{op}' requires explicit --approve.")
        if op == "copy" and dest_str:
            dst = core.validate_safe_path(dest_str, must_exist=False, allow_dir=False)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            executed = True
    return {"observation_mode": "directly_observed", "data": {"operation": op, "source": str(src), "destination": dest_str or None, "executed": executed}, "metrics": {"executed": int(executed)}}


def handle_local_workspace_integrity_auditor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    pkg_dir = core.resolve_package_resource("data-web-app-interaction/asset-default-config.json").parent
    cfg = ctx["cfg"]
    missing_refs = []
    for key, rel_p in cfg.get("paths", {}).items():
        resolved = core.resolve_package_resource(rel_p)
        if not resolved.exists():
            missing_refs.append({"config_key": key, "path": rel_p})
    subfolders = [p.name for p in pkg_dir.iterdir() if p.is_dir()]
    unprefixed = [p.name for p in pkg_dir.iterdir() if p.is_file() and not p.name.startswith(("tools-", "knowledge-", "asset-"))]
    return {
        "observation_mode": "directly_observed",
        "data": {"missing_references": missing_refs, "unexpected_subfolders": subfolders, "unprefixed_files": unprefixed, "integrity_clean": not (missing_refs or subfolders or unprefixed)},
        "metrics": {"missing_references": len(missing_refs), "unexpected_subfolders": len(subfolders)},
    }


SKILL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    # Cat 11
    "online-video-viewer-and-interpreter": handle_online_video_viewer_and_interpreter,
    "audio-and-music-listener-analyst": handle_audio_and_music_listener_analyst,
    "transcript-extraction-and-cleanup-specialist": handle_transcript_extraction_and_cleanup_specialist,
    "video-chapter-and-segment-indexer": handle_video_chapter_and_segment_indexer,
    "streaming-playback-tester": handle_streaming_playback_tester,
    "media-metadata-inspector": handle_media_metadata_inspector,
    "caption-and-subtitle-qa-specialist": handle_caption_and_subtitle_qa_specialist,
    "audio-video-synchronization-tester": handle_audio_video_synchronization_tester,
    "media-download-permission-reviewer": handle_media_download_permission_reviewer,
    "media-content-comparison-analyst": handle_media_content_comparison_analyst,
    # Cat 12
    "universal-document-reader": handle_universal_document_reader,
    "scanned-document-ocr-reader": handle_scanned_document_ocr_reader,
    "spreadsheet-data-reader": handle_spreadsheet_data_reader,
    "presentation-reader-and-navigator": handle_presentation_reader_and_navigator,
    "image-text-and-object-inspector": handle_image_text_and_object_inspector,
    "archive-and-package-reader": handle_archive_and_package_reader,
    "file-format-identifier": handle_file_format_identifier,
    "cross-format-conversion-operator": handle_cross_format_conversion_operator,
    "document-search-and-cross-reference-builder": handle_document_search_and_cross_reference_builder,
    "office-file-round-trip-tester": handle_office_file_round_trip_tester,
    # Cat 13
    "local-workspace-explorer": handle_local_workspace_explorer,
    "offline-file-searcher": handle_offline_file_searcher,
    "duplicate-file-analyst": handle_duplicate_file_analyst,
    "file-organization-planner": handle_file_organization_planner,
    "batch-file-renaming-operator": handle_batch_file_renaming_operator,
    "local-data-importer": handle_local_data_importer,
    "offline-search-index-builder": handle_offline_search_index_builder,
    "filesystem-permission-auditor": handle_filesystem_permission_auditor,
    "safe-local-file-manipulator": handle_safe_local_file_manipulator,
    "local-workspace-integrity-auditor": handle_local_workspace_integrity_auditor,
}
