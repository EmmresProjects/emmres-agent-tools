#!/usr/bin/env python3
"""Subtitle timing, dubbing packaging, PCM/WAV preparation, visual planning, and font audits."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import statistics
import struct
import wave
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from tools_core import Document, Segment, ToolError, atomic_write, bounded_read, count_words, safe_output_size, utc_now
from tools_quality import defect, pair_qa


def timestamp_ms(value: str, *, kind: str = "auto") -> int:
    text = value.strip()
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?", text)
    if not match:
        # ASS permits H:MM:SS.cc (centiseconds).
        ass = re.fullmatch(r"(\d+):(\d{1,2}):(\d{2})[.](\d{1,2})", text)
        if ass:
            hours, minutes, seconds, fraction = ass.groups()
            return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 + int(fraction.ljust(2, "0")) * 10
        raise ToolError("invalid_timecode", "Invalid subtitle timestamp.", details=value)
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int((match.group(4) or "0").ljust(3, "0"))
    if minutes > 59 or seconds > 59:
        raise ToolError("invalid_timecode", "Timestamp minutes/seconds exceed 59.", details=value)
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + milliseconds


def format_timestamp(milliseconds: int, *, decimal: str = ",", ass: bool = False) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, ms = divmod(remainder, 1000)
    if ass:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{ms // 10:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal}{ms:03d}"


def parse_timed_cues(text: str, kind: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    if kind in {"srt", "vtt"}:
        timing_re = re.compile(r"(?m)^(?P<start>\d{1,3}:\d{2}:\d{2}[.,]\d{1,3})\s+-->\s+(?P<end>\d{1,3}:\d{2}:\d{2}[.,]\d{1,3})(?P<settings>[^\r\n]*)")
        for match in timing_re.finditer(text):
            line_end = text.find("\n", match.end())
            if line_end < 0:
                continue
            body_start = line_end + 1
            next_blank = re.search(r"\r?\n[ \t]*\r?\n", text[body_start:])
            body_end = body_start + next_blank.start() if next_blank else len(text)
            body = text[body_start:body_end].rstrip("\r\n")
            body_end = body_start + len(body)
            start_ms, end_ms = timestamp_ms(match.group("start")), timestamp_ms(match.group("end"))
            cues.append({"id": f"cue-{len(cues)+1:06d}", "start": start_ms, "end": end_ms, "start_span": match.span("start"), "end_span": match.span("end"), "text_span": (body_start, body_end), "text": body, "settings": match.group("settings"), "decimal": "," if kind == "srt" else "."})
    elif kind in {"ass", "ssa"}:
        for match in re.finditer(r"(?m)^Dialogue:\s*[^\r\n]*$", text):
            line = match.group(0)
            commas = [m.start() for m in re.finditer(",", line)]
            if len(commas) < 9:
                continue
            # Split into ten fields and locate start/end fields exactly.
            fields = line.split(",", 9)
            if len(fields) != 10:
                continue
            start_value, end_value = fields[1].strip(), fields[2].strip()
            start_abs = match.start() + line.find(start_value, len(fields[0]))
            end_search_start = line.find(",", start_abs - match.start()) + 1
            end_abs = match.start() + line.find(end_value, end_search_start)
            body_start = match.start() + commas[8] + 1
            cues.append({"id": f"cue-{len(cues)+1:06d}", "start": timestamp_ms(start_value, kind="ass"), "end": timestamp_ms(end_value, kind="ass"), "start_span": (start_abs, start_abs + len(start_value)), "end_span": (end_abs, end_abs + len(end_value)), "text_span": (body_start, match.end()), "text": text[body_start:match.end()], "settings": "", "decimal": ".", "ass": True})
    else:
        raise ToolError("unsupported_subtitle_timing", "Timing adjustment supports SRT, VTT, ASS, and SSA. TTML can be translated through XML but requires application-specific timing adjustment.")
    for cue in cues:
        if cue["end"] <= cue["start"]:
            raise ToolError("invalid_timecode", "Subtitle cue ends at or before its start.", details={"id": cue["id"], "start": cue["start"], "end": cue["end"]})
    return cues


def visible_caption_text(text: str) -> str:
    text = re.sub(r"\{\\[^}]*\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("\\N", " ").replace("\\n", " ").strip()


def reading_speed(text: str, duration_ms: int) -> float:
    return len(visible_caption_text(text)) / max(0.001, duration_ms / 1000)


def wrap_caption(text: str, *, max_chars: int, max_lines: int) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if "\\N" in text:
        return text, warnings
    lines = text.splitlines()
    output: list[str] = []
    for line in lines or [text]:
        if len(visible_caption_text(line)) <= max_chars:
            output.append(line)
            continue
        words = line.split()
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if len(visible_caption_text(candidate)) <= max_chars:
                current = candidate
            else:
                if current:
                    output.append(current)
                current = word
        if current:
            output.append(current)
    if len(output) > max_lines:
        warnings.append(f"Wrapping needs {len(output)} lines, exceeding max_lines={max_lines}; lines were not dropped.")
    separator = "\\N" if "{\\" in text or "\\N" in text else "\n"
    return separator.join(output), warnings


def subtitle_qa(source_cues: Sequence[dict[str, Any]], *, target_cues: Sequence[dict[str, Any]] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = (config or {}).get("subtitles", {})
    max_cps = float(settings.get("max_cps", 20))
    min_duration = float(settings.get("min_duration_seconds", 0.8)) * 1000
    max_duration = float(settings.get("max_duration_seconds", 7.0)) * 1000
    max_chars = int(settings.get("max_chars_per_line", 42))
    max_lines = int(settings.get("max_lines", 2))
    defects: list[dict[str, Any]] = []
    cues = list(target_cues if target_cues is not None else source_cues)
    for index, cue in enumerate(cues):
        duration = cue["end"] - cue["start"]
        cps = reading_speed(cue["text"], duration)
        if cps > max_cps:
            defects.append(defect("subtitle-reading-speed", "warning", "Cue exceeds configured characters-per-second limit.", segment_id=cue["id"], target=cue["text"], evidence={"cps": round(cps, 2), "max_cps": max_cps}, recommendation="Adjust timing/segmentation or adapt wording while preserving plot-critical meaning."))
        if duration < min_duration or duration > max_duration:
            defects.append(defect("subtitle-duration", "warning", "Cue duration is outside configured bounds.", segment_id=cue["id"], evidence={"duration_ms": duration, "minimum_ms": min_duration, "maximum_ms": max_duration}))
        lines = re.split(r"\r?\n|\\N", cue["text"])
        if len(lines) > max_lines or any(len(visible_caption_text(line)) > max_chars for line in lines):
            defects.append(defect("subtitle-line-length", "warning", "Cue exceeds line count or line-length limits.", segment_id=cue["id"], target=cue["text"], evidence={"lines": len(lines), "line_lengths": [len(visible_caption_text(line)) for line in lines], "max_lines": max_lines, "max_chars_per_line": max_chars}))
        if index and cue["start"] < cues[index-1]["end"]:
            defects.append(defect("subtitle-overlap", "error", "Cue overlaps the previous cue.", segment_id=cue["id"], evidence={"previous_end": cues[index-1]["end"], "start": cue["start"]}))
        if re.search(r"\[(?:music|applause|laugh|sound|noise)|♪", cue["text"], re.IGNORECASE) is None and visible_caption_text(cue["text"]).startswith(("[", "(")):
            defects.append(defect("caption-convention", "review", "Bracketed caption cue may need a consistent sound/speaker convention.", segment_id=cue["id"], target=cue["text"], objective=False))
    if target_cues is not None and len(source_cues) != len(target_cues):
        defects.append(defect("subtitle-cue-count", "critical", "Source and target cue counts differ.", evidence={"source": len(source_cues), "target": len(target_cues)}))
    if target_cues is not None:
        for source, target in zip(source_cues, target_cues):
            if source["start"] != target["start"] or source["end"] != target["end"]:
                defects.append(defect("subtitle-sync-change", "review", "Target timing differs from source; verify synchronization.", segment_id=source["id"], evidence={"source": [source["start"], source["end"]], "target": [target["start"], target["end"]]}, objective=False))
    counts = defaultdict(int)
    for item in defects:
        counts[item["severity"]] += 1
    return {"version": 1, "summary": {"cues": len(cues), "defects": len(defects), "by_severity": dict(counts), "max_cps": max((reading_speed(c["text"], c["end"]-c["start"]) for c in cues), default=0), "release_ready": not any(d["severity"] in {"critical", "error"} for d in defects) and not any(d["severity"] in {"warning", "review"} for d in defects)}, "defects": defects, "limitations": "Timing and reading-speed checks do not verify shot changes, burned-in graphics, cropping, safe areas, lip sync, audio balance, pronunciation, or final rendered video; perform media-player review."}


def adjust_subtitles(text: str, kind: str, config: dict[str, Any], *, offset_ms: int = 0, shot_changes_ms: Sequence[int] = (), snap_threshold_ms: int = 120, min_gap_ms: int = 80) -> tuple[str, dict[str, Any]]:
    if abs(offset_ms) > 24 * 3600 * 1000:
        raise ToolError("invalid_option", "Subtitle offset exceeds 24 hours.")
    cues = parse_timed_cues(text, kind)
    settings = config["subtitles"]
    max_cps = float(settings["max_cps"])
    min_duration = int(float(settings["min_duration_seconds"]) * 1000)
    max_duration = int(float(settings["max_duration_seconds"]) * 1000)
    warnings: list[dict[str, Any]] = []
    shots = sorted(int(x) for x in shot_changes_ms)
    for index, cue in enumerate(cues):
        cue["start"] = max(0, cue["start"] + offset_ms)
        cue["end"] = max(cue["start"] + 1, cue["end"] + offset_ms)
        if shots:
            nearest_start = min(shots, key=lambda x: abs(x - cue["start"]))
            if abs(nearest_start - cue["start"]) <= snap_threshold_ms and nearest_start < cue["end"]:
                cue["start"] = nearest_start
            nearest_end = min(shots, key=lambda x: abs(x - cue["end"]))
            if abs(nearest_end - cue["end"]) <= snap_threshold_ms and nearest_end > cue["start"]:
                cue["end"] = nearest_end
        desired = max(min_duration, math.ceil(len(visible_caption_text(cue["text"])) / max_cps * 1000))
        desired = min(max_duration, desired)
        next_start = cues[index + 1]["start"] + offset_ms if index + 1 < len(cues) else None
        proposed_end = cue["start"] + desired
        if next_start is not None:
            proposed_end = min(proposed_end, next_start - min_gap_ms)
        if proposed_end > cue["start"]:
            cue["end"] = max(cue["end"], proposed_end)
        if index and cue["start"] < cues[index-1]["end"] + min_gap_ms:
            previous = cues[index-1]
            new_previous_end = cue["start"] - min_gap_ms
            if new_previous_end > previous["start"]:
                previous["end"] = new_previous_end
            else:
                cue["start"] = previous["end"] + min_gap_ms
                cue["end"] = max(cue["end"], cue["start"] + 1)
                warnings.append({"cue": cue["id"], "warning": "Could not create a positive previous cue while resolving overlap."})
        wrapped, wrap_warnings = wrap_caption(cue["text"], max_chars=int(settings["max_chars_per_line"]), max_lines=int(settings["max_lines"]))
        cue["adjusted_text"] = wrapped
        warnings.extend({"cue": cue["id"], "warning": item} for item in wrap_warnings)
    replacements: list[tuple[int, int, str]] = []
    for cue in cues:
        replacements.append((*cue["start_span"], format_timestamp(cue["start"], decimal=cue["decimal"], ass=cue.get("ass", False))))
        replacements.append((*cue["end_span"], format_timestamp(cue["end"], decimal=cue["decimal"], ass=cue.get("ass", False))))
        replacements.append((*cue["text_span"], cue["adjusted_text"]))
    output = text
    for start, end, value in sorted(replacements, reverse=True):
        output = output[:start] + value + output[end:]
    adjusted_cues = parse_timed_cues(output, kind)
    report = subtitle_qa(adjusted_cues, config=config)
    report["adjustments"] = {"offset_ms": offset_ms, "shot_changes_supplied": len(shots), "snap_threshold_ms": snap_threshold_ms, "min_gap_ms": min_gap_ms, "warnings": warnings, "cues": len(cues)}
    return output, report


def voiceover_package(source_cues: Sequence[dict[str, Any]], target_cues: Sequence[dict[str, Any]], *, locale: str, title: str, character_map: dict[str, str] | None = None, pronunciation: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    if len(source_cues) != len(target_cues):
        raise ToolError("alignment_error", "Dubbing source and target cue counts differ.")
    character_map = character_map or {}
    lines = []
    speakers: dict[str, dict[str, Any]] = {}
    for index, (source, target) in enumerate(zip(source_cues, target_cues), 1):
        speaker_match = re.match(r"\s*(?:<v\s+([^>]+)>|([^:]{1,60}):)\s*", target["text"])
        speaker = (speaker_match.group(1) or speaker_match.group(2)).strip() if speaker_match else "Narrator/Unassigned"
        actor = character_map.get(speaker, "UNASSIGNED")
        duration = target["end"] - target["start"]
        line = {
            "cue_id": target["id"], "take_id": f"{index:04d}", "file_name": f"{re.sub(r'[^a-z0-9-]+', '-', locale.lower())}-{re.sub(r'[^a-z0-9-]+', '-', speaker.lower()).strip('-') or 'unassigned'}-{index:04d}.wav",
            "speaker": speaker, "actor": actor, "start_timecode": format_timestamp(target["start"], decimal="."), "end_timecode": format_timestamp(target["end"], decimal="."),
            "duration_ms": duration, "source_text": source["text"], "script": target["text"], "target_cps": round(reading_speed(target["text"], duration), 2),
            "lip_sync_review": "required", "pickup": False, "implementation_metadata": {"sequence": index, "locale": locale},
            "direction": "Preserve character identity, emotion, breath, emphasis, plot-critical meaning, and visible pauses; verify mouth movements in picture-locked review.",
        }
        if line["target_cps"] > 20:
            line["pickup"] = True
            line["review_note"] = "High reading speed; adapt or record a pickup after timing review."
        lines.append(line)
        entry = speakers.setdefault(speaker, {"speaker": speaker, "actor": actor, "lines": 0, "total_duration_ms": 0, "casting_brief": "UNASSIGNED — provide age range, vocal quality, relationship, accent/region, emotional arc, and content constraints before casting."})
        entry["lines"] += 1
        entry["total_duration_ms"] += duration
    return {
        "version": 1, "title": title, "locale": locale, "picture_lock_required": True, "approval_required_before_recording": True,
        "delivery": {"audio_format": "24-bit PCM WAV recommended; confirm production specification", "alignment": "Use cue start/end and take IDs; do not infer final mix from this script.", "pickup_workflow": "Mark revised lines as pickups and preserve take lineage."},
        "characters": list(speakers.values()), "pronunciation_lexicon": list(pronunciation), "lines": lines,
        "summary": {"lines": len(lines), "unassigned_speakers": sum(s["actor"] == "UNASSIGNED" for s in speakers.values()), "pickups_suggested": sum(l["pickup"] for l in lines)},
        "limitations": "This is an actor-ready planning package, not synthesized dubbing or proof of lip sync. Casting, pronunciation, timing, mouth movement, performance, mix, and final exports require production review."
    }


def _pcm_samples(data: bytes, sample_width: int) -> tuple[list[int], int]:
    if sample_width not in {1, 2, 3, 4}:
        raise ToolError("unsupported_audio", "Only integer PCM with 8/16/24/32-bit samples is supported.")
    count = len(data) // sample_width
    values: list[int] = []
    for index in range(count):
        chunk = data[index*sample_width:(index+1)*sample_width]
        if sample_width == 1:
            values.append(chunk[0] - 128)
        else:
            values.append(int.from_bytes(chunk, "little", signed=True))
    peak = 128 if sample_width == 1 else 2 ** (sample_width * 8 - 1)
    return values, peak


def analyze_wav(path: Path, *, silence_db: float = -45.0) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            comptype = wav.getcomptype()
            if comptype != "NONE":
                raise ToolError("unsupported_audio", "Compressed WAV is not supported; supply integer PCM WAV.")
            raw = wav.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ToolError("invalid_audio", "Input is not a readable PCM WAV file.", details=str(exc)) from exc
    if channels <= 0 or rate <= 0:
        raise ToolError("invalid_audio", "WAV has invalid channel or sample-rate metadata.")
    samples, peak = _pcm_samples(raw, width)
    if not samples:
        raise ToolError("invalid_audio", "WAV contains no audio frames.")
    absolute = [abs(x) for x in samples]
    rms = math.sqrt(sum(x*x for x in samples) / len(samples))
    peak_value = max(absolute)
    clipping_threshold = peak * 0.999
    clipped = sum(x >= clipping_threshold for x in absolute)
    threshold = peak * (10 ** (silence_db / 20))
    silent = sum(x <= threshold for x in absolute)
    channel_rms = []
    for channel in range(channels):
        values = samples[channel::channels]
        channel_rms.append(math.sqrt(sum(x*x for x in values) / max(1, len(values))))
    imbalance = (max(channel_rms) - min(channel_rms)) / max(1.0, max(channel_rms)) if len(channel_rms) > 1 else 0.0
    duration = frames / rate
    return {
        "path": str(path), "format": "PCM WAV", "channels": channels, "sample_width_bits": width * 8, "sample_rate_hz": rate,
        "frames": frames, "duration_seconds": round(duration, 6), "peak_amplitude_ratio": round(peak_value / peak, 6),
        "rms_dbfs": round(20 * math.log10(max(rms, 1e-12) / peak), 2), "clipped_sample_ratio": round(clipped / len(samples), 8),
        "silent_sample_ratio_at_threshold": round(silent / len(samples), 6), "silence_threshold_dbfs": silence_db,
        "channel_rms_dbfs": [round(20 * math.log10(max(x, 1e-12) / peak), 2) for x in channel_rms], "channel_imbalance_ratio": round(imbalance, 6),
        "dc_offset_ratio": round(statistics.fmean(samples) / peak, 8), "playback_speed_assessment": None,
        "risks": ([{"type": "clipping", "severity": "warning", "message": "Potential clipped samples detected."}] if clipped / len(samples) > 0.0001 else []) + ([{"type": "silence", "severity": "review", "message": "Large silent portion; verify capture and segmentation."}] if silent / len(samples) > 0.7 else []) + ([{"type": "channel-imbalance", "severity": "review", "message": "Channels have substantially different RMS."}] if imbalance > 0.5 else []),
        "limitations": "Sample statistics cannot diagnose every noise type, intelligibility, codec artifact, playback-speed error, overlap, or speaker identity. Listen and review before transcription."
    }


def prepare_wav(path: Path, output_dir: Path, config: dict[str, Any], *, chunk_seconds: float = 30.0, normalize: bool = False, trim_silence: bool = False, silence_db: float = -45.0) -> dict[str, Any]:
    if not 0.1 <= chunk_seconds <= 3600:
        raise ToolError("invalid_option", "chunk_seconds must be between 0.1 and 3600.")
    analysis = analyze_wav(path, silence_db=silence_db)
    output_dir.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "rb") as source:
        channels, width, rate, frames = source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()
        raw = source.readframes(frames)
    if (normalize or trim_silence) and width != 2:
        raise ToolError("unsupported_audio_cleanup", "Normalize/trim cleanup currently supports PCM16 WAV; chunking supports all integer PCM widths.")
    start_frame, end_frame = 0, frames
    normalization_gain = 1.0
    if trim_silence or normalize:
        samples, peak = _pcm_samples(raw, width)
        threshold = peak * (10 ** (silence_db / 20))
        if trim_silence:
            active = [i for i, value in enumerate(samples) if abs(value) > threshold]
            if active:
                start_frame = max(0, active[0] // channels - int(rate * 0.1))
                end_frame = min(frames, active[-1] // channels + 1 + int(rate * 0.1))
        selected = samples[start_frame*channels:end_frame*channels]
        if normalize and selected:
            current_peak = max(abs(x) for x in selected)
            normalization_gain = min(8.0, (peak * 0.89) / max(1, current_peak))
            transformed = [max(-peak, min(peak - 1, round(x * normalization_gain))) for x in selected]
            raw = b"".join(int(x).to_bytes(2, "little", signed=True) for x in transformed)
        else:
            raw = raw[start_frame*channels*width:end_frame*channels*width]
        frames = end_frame - start_frame
    chunk_frames = max(1, int(rate * chunk_seconds))
    chunk_count = math.ceil(frames / chunk_frames)
    if chunk_count > int(config["limits"]["max_audio_chunks"]):
        raise ToolError("too_many_audio_chunks", "Audio would exceed max_audio_chunks; use larger chunks.")
    chunks = []
    for index in range(chunk_count):
        begin = index * chunk_frames
        end = min(frames, begin + chunk_frames)
        payload = raw[begin*channels*width:end*channels*width]
        name = f"{path.stem}-chunk-{index+1:04d}.wav"
        destination = output_dir / name
        stream = io.BytesIO()
        with wave.open(stream, "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(width)
            out.setframerate(rate)
            out.writeframes(payload)
        atomic_write(destination, stream.getvalue(), overwrite=False)
        chunks.append({"id": f"chunk-{index+1:04d}", "path": name, "start_seconds": round((start_frame + begin) / rate, 6), "end_seconds": round((start_frame + end) / rate, 6), "frames": end - begin, "bytes": len(payload)})
    manifest = {"version": 1, "created_at": utc_now(), "source": path.name, "analysis": analysis, "cleanup": {"normalize": normalize, "trim_silence": trim_silence, "normalization_gain": round(normalization_gain, 6), "trimmed_start_frame": start_frame, "trimmed_end_frame": end_frame}, "chunk_seconds": chunk_seconds, "chunks": chunks, "recovery": "Chunks are immutable after writing; rerun to a new empty directory if cleanup parameters change. Merge transcription timestamps using each chunk's start_seconds."}
    atomic_write(output_dir / "audio-chunks.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", overwrite=False)
    return manifest


def normalize_transcript(value: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    if not isinstance(value.get("text"), str):
        raise ToolError("invalid_transcript", "Transcript needs a text string.")
    segments = value.get("segments", [])
    if not isinstance(segments, list):
        raise ToolError("invalid_transcript", "Transcript segments must be an array.")
    normalized = []
    for index, row in enumerate(segments, 1):
        if not isinstance(row, dict):
            raise ToolError("invalid_transcript", "Transcript segment must be an object.", details=index)
        text = str(row.get("text", ""))
        start = float(row.get("start", 0))
        end = float(row.get("end", start))
        if start < 0 or end < start:
            raise ToolError("invalid_transcript", "Transcript segment has invalid timestamps.", details=index)
        normalized.append({"id": str(row.get("id", f"segment-{index:06d}")), "start": start, "end": end, "speaker": str(row.get("speaker", row.get("speaker_id", "Unassigned"))), "text": text, "language": row.get("language", value.get("language", "und")), "confidence": row.get("confidence", row.get("avg_logprob")), "uncertainties": row.get("uncertainties", []), "words": row.get("words", [])})
    return {"version": 1, "source": source_name, "text": value["text"], "language": value.get("language", "und"), "duration": value.get("duration"), "segments": normalized, "speakers": sorted({row["speaker"] for row in normalized}), "review_required": True, "uncertainties": value.get("uncertainties", []), "limitations": value.get("limitations", "Review automated transcription, timestamps, speakers, names, numbers, and uncertain audio before translation or publication.")}


def transcript_srt(transcript: dict[str, Any]) -> str:
    rows = []
    for index, segment in enumerate(transcript.get("segments", []), 1):
        start = format_timestamp(round(float(segment["start"]) * 1000))
        end = format_timestamp(round(float(segment["end"]) * 1000))
        speaker = segment.get("speaker", "Unassigned")
        text = segment.get("text", "")
        label = f"{speaker}: " if speaker and speaker != "Unassigned" else ""
        rows.append(f"{index}\n{start} --> {end}\n{label}{text}\n")
    return "\n".join(rows)


def transcript_vtt(transcript: dict[str, Any]) -> str:
    rows = ["WEBVTT", ""]
    for index, segment in enumerate(transcript.get("segments", []), 1):
        start = format_timestamp(round(float(segment["start"]) * 1000), decimal=".")
        end = format_timestamp(round(float(segment["end"]) * 1000), decimal=".")
        speaker = segment.get("speaker", "Unassigned")
        text = segment.get("text", "")
        label = f"{speaker}: " if speaker and speaker != "Unassigned" else ""
        rows.extend([str(index), f"{start} --> {end}", f"{label}{text}", ""])
    return "\n".join(rows)


def transcript_markdown(transcript: dict[str, Any], *, title: str = "Transcript") -> str:
    lines = [f"# {title}", "", f"- Language: {transcript.get('language', 'und')}", f"- Review required: {str(transcript.get('review_required', True)).lower()}", ""]
    for segment in transcript.get("segments", []):
        start = format_timestamp(round(float(segment["start"]) * 1000), decimal=".")[:-4]
        speaker = segment.get("speaker", "Unassigned")
        confidence = segment.get("confidence")
        suffix = f" _(confidence: {confidence})_" if confidence is not None else ""
        lines.append(f"**[{start}] {speaker}:** {segment.get('text', '')}{suffix}")
        lines.append("")
    if not transcript.get("segments"):
        lines.append(transcript.get("text", ""))
    return "\n".join(lines).rstrip() + "\n"


def visual_plan(ocr: dict[str, Any], *, target_locale: str = "und", max_expansion_ratio: float = 1.5) -> dict[str, Any]:
    blocks = ocr.get("blocks")
    if not isinstance(blocks, list):
        raise ToolError("invalid_ocr", "Visual plan input needs an OCR blocks array.")
    planned = []
    defects = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or not isinstance(block.get("text"), str) or not isinstance(block.get("bbox"), list) or len(block["bbox"]) != 4:
            raise ToolError("invalid_ocr", "Each visual block needs text and a four-coordinate bbox.", details=index)
        translation = str(block.get("translation", ""))
        source = block["text"]
        ratio = len(translation) / max(1, len(source)) if translation else None
        bbox = block["bbox"]
        width, height = abs(float(bbox[2])-float(bbox[0])), abs(float(bbox[3])-float(bbox[1]))
        risk = ratio is not None and ratio > max_expansion_ratio
        if risk:
            defects.append(defect("visual-expansion", "warning", "Replacement text may exceed its source text box.", segment_id=str(block.get("id", index)), source=source, target=translation, evidence={"ratio": round(ratio or 0, 3), "threshold": max_expansion_ratio}))
        if block.get("confidence") is not None and float(block["confidence"]) < 0.8:
            defects.append(defect("ocr-confidence", "review", "OCR confidence is below 0.8.", segment_id=str(block.get("id", index)), source=source, evidence=block.get("confidence"), objective=False))
        kind = str(block.get("kind", "other"))
        planned.append({
            "id": str(block.get("id", f"block-{index+1}")), "reading_order": block.get("reading_order", index+1), "kind": kind,
            "source_text": source, "replacement_text": translation, "bbox_pixels": bbox, "box_width": width, "box_height": height,
            "ocr_confidence": block.get("confidence"), "uncertainties": block.get("uncertainties", []),
            "layer_plan": {"isolate_background": True, "retain_original_layer_for_review": True, "font": "SELECT_TARGET_LOCALE_FONT", "font_size": "FIT_AFTER_SHAPING", "color": "SAMPLE_OR_MATCH_ORIGINAL", "alignment": "REVIEW_BIDI_AND_VISUAL_HIERARCHY", "effects": "PRESERVE_ORIGINAL_EFFECTS_WHEN_LEGIBLE"},
            "layout_actions": (["Allow text reflow or enlarge balloon/box; do not clip or silently shrink below readable size."] if risk else []) + (["Use panel/balloon reading order and preserve sound-effect artwork; verify visual humor."] if kind in {"dialogue", "sound-effect"} else []) + (["Preserve warnings, numbers, units, certifications, ingredients, and legal lines exactly; route for market/legal review."] if kind in {"warning", "packaging", "legal"} else []),
            "review_required": True,
        })
    planned.sort(key=lambda row: (int(row["reading_order"]) if str(row["reading_order"]).isdigit() else 999999, row["bbox_pixels"][1], row["bbox_pixels"][0]))
    return {"version": 1, "target_locale": target_locale, "image": ocr.get("image", {}), "reading_order": [row["id"] for row in planned], "blocks": planned, "defects": defects, "summary": {"blocks": len(planned), "defects": len(defects), "review_required": True}, "limitations": "Coordinates and replacement plans do not render artwork or prove OCR, font shaping, layer masks, visual humor, screenshot control mapping, or label compliance. Rebuild in an editable design tool and inspect exported pixels."}


def font_audit(font_path: Path, text: str, *, locales: Sequence[str] = ()) -> dict[str, Any]:
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise ToolError("dependency_missing", "Font audit requires fonttools.", hint="Install tools-requirements.txt.") from exc
    try:
        font = TTFont(str(font_path), lazy=True, fontNumber=0)
    except Exception as exc:
        raise ToolError("invalid_font", "Could not parse font.", details=str(exc)[:300]) from exc
    cmap = font.getBestCmap() or {}
    characters = sorted({ord(char) for char in text if not char.isspace() and unicodedata_category(char) != "Cf"})
    missing = [point for point in characters if point not in cmap]
    names = {}
    if "name" in font:
        for name_id, key in ((1, "family"), (2, "subfamily"), (5, "version"), (13, "license_description"), (14, "license_url")):
            record = font["name"].getDebugName(name_id)
            if record:
                names[key] = record
    embedding = None
    if "OS/2" in font:
        embedding = int(font["OS/2"].fsType)
    tables = set(font.keys())
    rtl = bool(re.search(r"[\u0590-\u08ff]", text))
    vertical = bool(re.search(r"[\u3000-\u303f\u4e00-\u9fff]", text))
    findings = []
    if missing:
        findings.append({"severity": "error", "type": "missing-glyphs", "codepoints": [f"U+{point:04X}" for point in missing[:200]], "characters": "".join(chr(point) for point in missing[:200])})
    if rtl and not ({"GSUB", "GPOS"} & tables):
        findings.append({"severity": "error", "type": "rtl-shaping-tables", "message": "RTL text is present but GSUB/GPOS shaping tables were not found."})
    if vertical and not ({"vhea", "vmtx"} & tables):
        findings.append({"severity": "review", "type": "vertical-metrics", "message": "CJK text may need vertical writing, but vertical metric tables were not found."})
    if embedding is not None and embedding & 0x0002:
        findings.append({"severity": "error", "type": "restricted-license-embedding", "message": "OS/2 fsType indicates restricted-license embedding; confirm redistribution rights."})
    return {"font": font_path.name, "names": names, "embedding_fs_type": embedding, "tables": sorted(tables), "locales": list(locales), "tested_unique_characters": len(characters), "missing_count": len(missing), "findings": findings, "fallback_required": bool(missing), "limitations": "Cmap/table inspection does not prove complex shaping, ligature quality, diacritic positioning, UI fallback, actual glyph aesthetics, or license rights. Render representative strings in target applications and obtain legal font approval."}


def unicodedata_category(char: str) -> str:
    import unicodedata
    return unicodedata.category(char)
