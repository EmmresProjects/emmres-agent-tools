"""Media and spoken-word text capabilities (12 handlers).

Timing maths is explicit: when the input carries no timestamps, captions are
distributed by a characters-per-second budget and the assumed timing is
reported so a human can correct it.
"""

from __future__ import annotations

import json
import re

EXPORTS = ["parse_timestamp", "format_timestamp", "wrap_caption", "clean_transcript"]
LEX_ASSET = "asset-lexicons-language.json"

TS = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
SPEAKER_LINE = re.compile(r"^(?:\[?%s\]?\s*)?([A-Z][A-Za-z .'-]{0,24}):\s*(.*)$" % TS)


def parse_timestamp(value):
    match = re.match(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?\s*$", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    ms = int((match.group(4) or "0").ljust(3, "0")[:3])
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + ms


def format_timestamp(ms, style="srt"):
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    if style == "vtt":
        return "%02d:%02d:%02d.%03d" % (hours, minutes, seconds, millis)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, seconds, millis)


def clean_transcript(text, fillers, lex):
    out = core.canonical_newlines(text)
    removed = []
    for filler in lex["fillers"]:
        pattern = re.compile(r"(?<![\w])\s?\b%s\b[,]?\s?" % re.escape(filler), re.I)
        hits = pattern.findall(out)
        if hits:
            removed.append((filler, len(hits)))
            out = pattern.sub(" ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out, removed


def wrap_caption(text, max_chars, max_lines):
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        merged = []
        per = max(1, len(lines) // max_lines + (1 if len(lines) % max_lines else 0))
        for idx in range(0, len(lines), per):
            merged.append(" ".join(lines[idx:idx + per]))
        lines = merged[:max_lines]
    return lines


def transcript_cleaner(ctx):
    text = ctx.require_text(minimum=10)
    tables = ctx.assets.json(LEX_ASSET)
    out = core.canonical_newlines(text)
    notes = []
    removed = []
    if ctx.params["fillers"]:
        out, removed = clean_transcript(out, True, tables)
        if removed:
            notes.append("removed fillers: %s" % ", ".join("%s x%d" % (f, c) for f, c in removed[:8]))
    if ctx.params["timestamps"]:
        before = out
        out = re.sub(r"\[?%s\]?\s*" % TS, "", out)
        if out != before:
            notes.append("stripped inline timestamps")
    if not ctx.params["keep_speakers"]:
        before = out
        out = re.sub(r"(?m)^([A-Z][A-Za-z .'-]{0,24}):\s*", "", out)
        if out != before:
            notes.append("removed speaker labels")
    if ctx.params["punctuation"]:
        before = out
        out = re.sub(r"\s+([,.!?;:])", r"\1", out)
        out = re.sub(r"([,.!?;:])(?=[A-Za-z])", r"\1 ", out)
        out = re.sub(r"\b([a-z])", lambda m: m.group(1), out)
        sentences = []
        for line in out.split("\n"):
            stripped = line.strip()
            if stripped and stripped[0].islower():
                stripped = stripped[0].upper() + stripped[1:]
            if stripped and stripped[-1] not in ".!?":
                stripped += "."
            sentences.append(stripped)
        out = "\n".join(sentences)
        if out != before:
            notes.append("restored sentence punctuation and capitalisation")
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    max_line = int(ctx.params["max_line_chars"])
    if max_line:
        rewrapped = []
        for line in out.split("\n"):
            if not line.strip():
                rewrapped.append("")
                continue
            rewrapped.extend(wrap_caption(line, max_line, 0))
        out = "\n".join(rewrapped)
        notes.append("re-wrapped to %d characters per line" % max_line)
    data = {"notes": notes, "fillers_removed": removed,
            "words_before": len(text.split()), "words_after": len(out.split())}
    return {"output": out, "data": data}


def diarization_editor(ctx):
    text = ctx.require_text(minimum=5)
    action = ctx.params["action"]
    label_style = ctx.params["label_style"]
    lines = core.canonical_newlines(text).split("\n")
    entries, current_speaker, buffer = [], None, []
    for line in lines:
        match = SPEAKER_LINE.match(line.strip())
        if match:
            if current_speaker:
                entries.append({"speaker": current_speaker, "text": " ".join(buffer).strip()})
            current_speaker = match.group(4)
            buffer = [match.group(5)] if match.lastindex and match.lastindex >= 5 else []
        elif line.strip():
            buffer.append(line.strip())
    if current_speaker:
        entries.append({"speaker": current_speaker, "text": " ".join(buffer).strip()})
    if not entries:
        return {"output": "no speaker labels detected (expected 'Name: text' lines)",
                "data": {"entries": 0, "speakers": []},
                "warnings": ["input did not match the 'Speaker: text' convention"]}
    speakers = []
    for entry in entries:
        if entry["speaker"] not in speakers:
            speakers.append(entry["speaker"])
    if action == "merge":
        merged = []
        for entry in entries:
            if merged and merged[-1]["speaker"] == entry["speaker"]:
                merged[-1]["text"] = (merged[-1]["text"] + " " + entry["text"]).strip()
            else:
                merged.append(dict(entry))
        entries = merged
    elif action == "split":
        split_entries = []
        for entry in entries:
            parts = [p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", entry["text"]) if p]
            for part in parts or [entry["text"]]:
                split_entries.append({"speaker": entry["speaker"], "text": part})
        entries = split_entries
    labels = {}
    if label_style == "numbered":
        labels = {name: "Speaker %d" % (idx + 1) for idx, name in enumerate(speakers)}
    elif label_style == "initials":
        labels = {name: "".join(part[0] for part in re.split(r"\s+", name) if part)[:3].upper()
                  for name in speakers}
    out_lines = []
    for entry in entries:
        label = labels.get(entry["speaker"], entry["speaker"])
        out_lines.append("%s: %s" % (label, entry["text"]))
    rows = [[labels.get(s, s), sum(1 for e in entries if e["speaker"] == s),
             round(100.0 * sum(len(e["text"].split()) for e in entries if e["speaker"] == s)
                   / max(1, sum(len(e["text"].split()) for e in entries)), 1)] for s in speakers]
    data = {"action": action, "label_style": label_style, "entries": len(entries),
            "speakers": speakers, "labels": labels,
            "share_of_words": {r[0]: r[2] for r in rows}}
    output = "\n".join(out_lines)
    if ctx.params["action"] == "normalize":
        output += "\n\n" + core.table(rows, ["speaker", "turns", "share of words (%)"])
    return {"output": output, "data": data}


def caption_writer(ctx):
    text = ctx.require_text(minimum=1)
    fmt = ctx.params["format"]
    max_chars = int(ctx.params["max_chars"])
    max_lines = int(ctx.params["max_lines"])
    cps = float(ctx.params["cps"])
    start_ms = int(ctx.params["start_ms"])
    blocks = []
    pattern = re.compile(r"\[?%s\]?\s*(.*)" % TS)
    supplied = [ln for ln in core.canonical_newlines(text).split("\n") if pattern.match(ln.strip())]
    if supplied:
        for line in supplied:
            match = pattern.match(line.strip())
            start = parse_timestamp("%s:%s:%s.%s" % (match.group(1) or "0", match.group(2),
                                                     match.group(3), (match.group(4) or "0").ljust(3, "0")[:3]))
            blocks.append({"start": start, "text": match.group(5).strip()})
        for idx, block in enumerate(blocks):
            end = blocks[idx + 1]["start"] if idx + 1 < len(blocks) else block["start"] + int(
                len(block["text"]) / max(1.0, cps) * 1000)
            block["end"] = end
        timing_source = "timestamps from input"
    else:
        sentences = [s for s in core.sentence_split(text) if s.strip()]
        clock = start_ms
        for sentence in sentences:
            duration = max(700, int(len(sentence) / max(1.0, cps) * 1000))
            blocks.append({"start": clock, "end": clock + duration, "text": sentence.strip()})
            clock += duration
        timing_source = "estimated from %s characters per second" % cps
    rendered = []
    for block in blocks:
        lines = wrap_caption(block["text"], max_chars, max_lines)
        rendered.append({"start": block["start"], "end": block["end"], "lines": lines})
    if fmt == "srt":
        parts = []
        for idx, cue in enumerate(rendered, 1):
            parts.append("%d\n%s --> %s\n%s\n" % (idx, format_timestamp(cue["start"]),
                                                 format_timestamp(cue["end"]), "\n".join(cue["lines"])))
        output = "\n".join(parts)
    elif fmt == "vtt":
        parts = ["WEBVTT", ""]
        for cue in rendered:
            parts.append("%s --> %s\n%s\n" % (format_timestamp(cue["start"], "vtt"),
                                             format_timestamp(cue["end"], "vtt"),
                                             "\n".join(cue["lines"])))
        output = "\n".join(parts)
    else:
        output = "\n".join(" ".join(cue["lines"]) for cue in rendered)
    data = {"cues": len(rendered), "format": fmt, "timing_source": timing_source,
            "max_chars": max_chars, "max_lines": max_lines, "cps": cps,
            "over_limit_cues": sum(1 for cue in rendered for line in cue["lines"] if len(line) > max_chars)}
    warnings = []
    if data["over_limit_cues"]:
        warnings.append("%d line(s) exceed %d characters (single long words cannot be split)"
                        % (data["over_limit_cues"], max_chars))
    total_seconds = rendered[-1]["end"] / 1000.0 if rendered else 0
    return {"output": output, "data": data,
            "warnings": warnings + ["total runtime %.1f s - correct the timings against the real media"
                                    % total_seconds]}


def subtitle_localization(ctx):
    text = ctx.require_text(minimum=10)
    offset = int(ctx.params["offset_ms"])
    max_chars = int(ctx.params["max_chars"])
    cps = float(ctx.params["cps"])
    fmt = ctx.params["format"]
    cues = []
    for block in re.split(r"\n\s*\n", core.canonical_newlines(text).strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        idx = 0
        if re.fullmatch(r"\d+", lines[0].strip()):
            idx = 1
        if idx < len(lines) and "-->" in lines[idx]:
            left, _, right = lines[idx].partition("-->")
            body = " ".join(lines[idx + 1:]).strip()
            cues.append({"index": lines[0].strip() if idx else str(len(cues) + 1),
                         "start": parse_timestamp(left.strip()) or 0,
                         "end": parse_timestamp(right.strip().split()[0]) or 0,
                         "text": body})
    if not cues:
        raise core.InvalidInput("input is not a timestamped subtitle file",
                                hint="Expected SRT/VTT blocks with '00:00:01,000 --> 00:00:03,000' lines.")
    problems = []
    for cue in cues:
        cue["start"] = max(0, cue["start"] + offset)
        cue["end"] = max(cue["start"] + 500, cue["end"] + offset)
        duration = (cue["end"] - cue["start"]) / 1000.0
        limit = int(duration * cps)
        if len(cue["text"]) > limit:
            problems.append({"index": cue["index"], "chars": len(cue["text"]), "limit": limit,
                             "advice": "shorten the translation or extend the cue"})
        cue["lines"] = wrap_caption(cue["text"], max_chars, 2)
    parts = []
    for cue in cues:
        parts.append("%s\n%s --> %s\n%s\n" % (cue["index"], format_timestamp(cue["start"],
                     "vtt" if fmt == "vtt" else "srt"),
                     format_timestamp(cue["end"], "vtt" if fmt == "vtt" else "srt"),
                     "\n".join(cue["lines"])))
    data = {"cues": len(cues), "offset_ms": offset, "cps": cps, "problems": problems,
            "overloaded": len(problems)}
    return {"output": ("WEBVTT\n\n" if fmt == "vtt" else "") + "\n".join(parts), "data": data,
            "warnings": ["%d cue(s) exceed the reading-rate budget" % len(problems)] if problems else []}


def ocr_cleanup(ctx):
    text = ctx.require_text(minimum=1)
    level = int(ctx.params["level"])
    out = core.canonical_newlines(text)
    changes = []
    if ctx.params["fix_ligatures"]:
        for ligature, replacement in (("\ufb01", "fi"), ("\ufb02", "fl"), ("\ufb00", "ff"),
                                      ("\ufb03", "ffi"), ("\ufb04", "ffl"), ("\u00e6", "ae"),
                                      ("\u0153", "oe"), ("\u00c6", "AE"), ("\u0152", "OE")):
            if ligature in out:
                out = out.replace(ligature, replacement)
                changes.append("expanded ligature %r" % ligature)
    if level >= 1:
        out, merged = re.subn(r"(\w)-\n(\w)", r"\1\2", out)
        if merged:
            changes.append("joined %d line-break hyphenation(s)" % merged)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
    if level >= 2:
        out = re.sub(r"(?<=[a-z])[|l](?=[a-z])", "l", out)
        out = re.sub(r"(?<=\d)[Oo](?=\d)", "0", out)
        out = re.sub(r"(?<=\d)[Il](?=\d)", "1", out)
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
        changes.append("applied character-confusion and spacing fixes")
    if level >= 3:
        out = re.sub(r"\b([A-Z]{1,3})\s+\.\s*([A-Z])", r"\1. \2", out)
        out = re.sub(r"\b([a-z])\s+([a-z])\b", r"\1\2", out)
        changes.append("applied aggressive letter-spacing fixes (review manually)")
    suspected = sum(1 for line in out.split("\n") if re.search(r"[^\x00-\x7F]{6,}", line))
    data = {"level": level, "changes": changes, "suspected_garbage_lines": suspected,
            "words": len(out.split())}
    return {"output": out, "data": data,
            "warnings": ["level 3 rewrites spacing aggressively; diff against the original"] if level >= 3 else []}


def table_reconstructor(ctx):
    text = ctx.require_text(minimum=5)
    delimiter = ctx.params["delimiter"]
    fmt = ctx.params["format"]
    lines = [ln for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    if delimiter == "auto":
        candidates = {"\t": 0, ",": 0, ";": 0, "|": 0}
        for line in lines[:50]:
            for char in candidates:
                candidates[char] += line.count(char)
        delimiter = max(candidates, key=candidates.get) if max(candidates.values()) else " "
    rows = []
    for line in lines:
        if delimiter == " ":
            cells = [c for c in re.split(r"\s{2,}|\t", line) if c.strip()]
        else:
            cells = [c.strip() for c in line.strip().strip("|").split(delimiter)]
        rows.append(cells)
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    body = rows[1:]
    if fmt == "csv":
        output = "\n".join(",".join('"%s"' % c.replace('"', '""') for c in row) for row in rows)
    elif fmt == "markdown":
        lines_out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
        lines_out += ["| " + " | ".join(row) + " |" for row in body]
        output = "\n".join(lines_out)
    else:
        output = core.table(body, header)
    data = {"delimiter": delimiter, "rows": len(body), "columns": width,
            "ragged_rows": sum(1 for r in rows if len([c for c in r if c]) != width)}
    return {"output": output, "data": data,
            "warnings": ["check merged cells: %d row(s) had a different cell count" % data["ragged_rows"]]
            if data["ragged_rows"] else []}


def audio_script(ctx):
    text = ctx.require_text(minimum=10)
    lex = ctx.assets.json(LEX_ASSET)
    out = text
    notes = []
    if ctx.params["respell"]:
        for source, spoken in lex["tts_units"].items():
            if source in out:
                out = out.replace(source, " %s " % spoken)
                notes.append("expanded unit %s" % source)
        for source, spoken in lex["abbreviations_tts"].items():
            out = re.sub(r"\b%s\b" % re.escape(source), spoken, out)
    pace = {"slow": 1.15, "normal": 1.0, "fast": 0.9}[ctx.params["pace"]]
    if pace != 1.0:
        out = re.sub(r"(?<=[.!?])\s+", "\n\n", out)
        notes.append("added breath breaks for %s pace" % ctx.params["pace"])
    sentences = [s for s in core.sentence_split(out) if s.strip()]
    if ctx.params["ssml"]:
        out = "<speak>\n" + "\n".join(
            '  <s><prosody rate="%s">%s</prosody></s>' % (ctx.params["pace"], s.strip())
            for s in sentences) + "\n</speak>"
        notes.append("wrapped in SSML with prosody rate")
    data = {"notes": notes, "words": len(text.split()),
            "estimated_seconds": round(len(text.split()) / (2.6 * pace), 1),
            "sentences": len(sentences)}
    return {"output": out, "data": data}


def teleprompter(ctx):
    text = ctx.require_text(minimum=5)
    width = int(ctx.params["width"])
    chunk = int(ctx.params["chunk"])
    markers = bool(ctx.params["markers"])
    sentences = [s.strip() for s in core.sentence_split(text) if s.strip()]
    groups = [sentences[i:i + chunk] for i in range(0, len(sentences), chunk)]
    blocks = []
    for block in groups:
        for sentence in block:
            blocks.extend(wrap_caption(sentence, width, 0))
        blocks.append("")
    output = "\n".join(blocks).strip()
    if markers:
        marked = []
        for line in output.split("\n"):
            marked.append(line)
        output = "\n".join(marked)
        output = "[BEGIN]\n" + output + "\n[END]"
    data = {"width": width, "chunk": chunk, "lines": len(output.split("\n")),
            "sentences": len(sentences), "blocks": len(groups),
            "estimated_seconds": round(len(text.split()) / 2.4, 1)}
    return {"output": output, "data": data}


def tts_prep(ctx):
    text = ctx.require_text(minimum=5)
    lex = ctx.assets.json(LEX_ASSET)
    expand = ctx.params["expand"]
    out = text
    notes = []
    if expand in ("all", "numbers"):
        def spell_number(match):
            return H.number_to_words(match.group(0)) if hasattr(H, "number_to_words") else match.group(0)
        out = re.sub(r"\b\d{1,4}\b", spell_number, out)
        notes.append("numbers spelled out where a word form exists")
    if expand in ("all", "abbrev"):
        for source, spoken in lex["abbreviations_tts"].items():
            out = re.sub(r"\b%s\b" % re.escape(source), spoken, out)
        for symbol, spoken in lex["tts_units"].items():
            out = out.replace(symbol, " %s " % spoken)
        out = out.replace("&", " and ").replace("%", " percent").replace("+", " plus ")
        notes.append("abbreviations and symbols expanded")
    if expand == "dates":
        out = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: "%s %s %s" % (m.group(3), m.group(2), m.group(1)), out)
        notes.append("ISO dates reordered for reading")
    out = re.sub(r"\s{2,}", " ", out).strip()
    sentences = [s for s in core.sentence_split(out) if s.strip()]
    if ctx.params["ssml"]:
        out = "<speak>\n" + "\n".join("  <s>%s</s>" % s.strip() for s in sentences) + "\n</speak>"
    data = {"expand": expand, "notes": notes, "sentences": len(sentences),
            "estimated_seconds": round(len(out.split()) / 2.6, 1)}
    return {"output": out, "data": data,
            "warnings": ["listen to the render: homographs and acronyms remain ambiguous"]}


def video_chapters(ctx):
    text = ctx.require_text(minimum=10)
    raw = ctx.params["timestamps"].strip()
    minimum = int(ctx.params["min_chapter_s"])
    fmt = ctx.params["format"]
    chapters = []
    if raw:
        chunks = [c.strip() for c in re.split(r"[;\n]+", raw) if c.strip()]
        for chunk in chunks:
            parts = chunk.split("=", 1)
            if len(parts) != 2:
                raise core.InvalidInput("timestamps must look like 00:01:30=Chapter title")
            ms = parse_timestamp(parts[0])
            if ms is None:
                raise core.InvalidInput("unparseable timestamp %r" % parts[0])
            chapters.append({"start": ms, "title": parts[1].strip()})
    else:
        lines = [ln.strip() for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
        clock = 0
        for line in lines:
            stamp = re.match(r"^\[?%s\]?\s*(.*)$" % TS, line)
            if stamp:
                clock = parse_timestamp("%s:%s:%s" % (stamp.group(1) or "0", stamp.group(2), stamp.group(3))) or clock
                title = stamp.group(4).strip() or "Chapter"
            else:
                title = line
            chapters.append({"start": clock, "title": title})
            clock += minimum * 1000
    chapters.sort(key=lambda c: c["start"])
    kept, dropped = [], []
    for chapter in chapters:
        if kept and chapter["start"] - kept[-1]["start"] < minimum * 1000:
            dropped.append(chapter["title"])
            continue
        kept.append(chapter)
    summary = H.summarize_extractive(text, max_sentences=3)
    if fmt == "youtube":
        output = "\n".join("%s %s" % (format_timestamp(c["start"])[3:8], c["title"]) for c in kept)
    elif fmt == "json":
        output = json.dumps({"chapters": kept, "summary": summary}, indent=2, ensure_ascii=False)
    else:
        output = "\n".join("%s - %s" % (format_timestamp(c["start"])[3:8], c["title"]) for c in kept)
        if summary:
            output += "\n\nSummary: " + " ".join(summary)
    data = {"chapters": len(kept), "dropped": dropped, "min_chapter_s": minimum,
            "summary": summary, "format": fmt}
    return {"output": output, "data": data,
            "warnings": ["%d chapter(s) shorter than %ds were merged away" % (len(dropped), minimum)]
            if dropped else []}


def onsite_text_planner(ctx):
    platform = ctx.params["platform"]
    duration = int(ctx.params["duration_s"])
    count = int(ctx.params["count"])
    text = ctx.text.strip()
    limits = {"youtube": {"title": 100, "description": 5000, "hook_s": 5},
              "tiktok": {"title": 150, "description": 2200, "hook_s": 2},
              "instagram": {"title": 125, "description": 2200, "hook_s": 3},
              "linkedin": {"title": 120, "description": 3000, "hook_s": 4},
              "webinar": {"title": 120, "description": 2000, "hook_s": 10}}.get(platform,
              {"title": 100, "description": 2000, "hook_s": 5})
    key_points = H.summarize_extractive(text, max_sentences=count) if text else []
    if not key_points:
        key_points = ["state the problem", "show the proof", "name the next step"]
    slot = max(1.0, (duration - limits["hook_s"]) / max(1, len(key_points)))
    rows = []
    clock = limits["hook_s"]
    for idx, point in enumerate(key_points, 1):
        rows.append(["%d" % idx, "%d-%ds" % (clock, clock + slot), core.truncate(point, 90)])
        clock += slot
    output = ["# On-screen text plan (%s)" % platform, ""]
    output.append("- title limit: %d characters" % limits["title"])
    output.append("- hook window: first %ds" % limits["hook_s"])
    output.append("")
    output.append(core.table(rows, ["#", "time", "on-screen text"]))
    data = {"platform": platform, "duration_s": duration, "limits": limits,
            "cards": len(rows), "hook_seconds": limits["hook_s"]}
    return {"output": "\n".join(output), "data": data,
            "warnings": ["shake-out review needed: on-screen text must stay readable at %ds per card"
                         % slot] if slot < 2 else []}


def multimodal_consistency(ctx):
    text = ctx.require_text(minimum=5)
    other = ctx.require_second_text()
    checks = [c.lower() for c in ctx.params["checks"]]
    issues = []
    if "numbers" in checks:
        left = re.findall(r"\b\d[\d,.]*\b", text)
        right = re.findall(r"\b\d[\d,.]*\b", other)
        only_left = sorted(set(left) - set(right))
        only_right = sorted(set(right) - set(left))
        if only_left or only_right:
            issues.append({"kind": "numbers", "detail": "text-only: %s | second-text-only: %s"
                           % (", ".join(only_left[:6]) or "-", ", ".join(only_right[:6]) or "-")})
    if "names" in checks:
        names_a = {n for n in re.findall(r"\b[A-Z][a-z]{2,}\b", text)}
        names_b = {n for n in re.findall(r"\b[A-Z][a-z]{2,}\b", other)}
        missing = sorted(names_a - names_b)
        if missing:
            issues.append({"kind": "names", "detail": "missing from second text: %s" % ", ".join(missing[:8])})
    if "timing" in checks:
        stamps_a = [parse_timestamp(m.group(0)) for m in re.finditer(TS, text)]
        stamps_b = [parse_timestamp(m.group(0)) for m in re.finditer(TS, other)]
        if stamps_a and stamps_b and (max(stamps_a) > max(stamps_b) * 1.05
                                      or min(stamps_a) < min(stamps_b) * 0.95):
            issues.append({"kind": "timing", "detail": "timelines differ: %.1fs vs %.1fs"
                           % (max(stamps_a) / 1000.0, max(stamps_b) / 1000.0)})
    if "titles" in checks:
        title_a = first_line(text)
        title_b = first_line(other)
        ratio = difflib_ratio(title_a, title_b)
        if ratio < 0.6:
            issues.append({"kind": "titles", "detail": "titles diverge (similarity %.2f): %r vs %r"
                           % (ratio, title_a[:40], title_b[:40])})
    if ctx.params["format"] == "json":
        output = json.dumps({"issues": issues, "checks": checks}, indent=2, ensure_ascii=False)
    else:
        output = (core.table([[i["kind"], i["detail"]] for i in issues], ["check", "detail"])
                  if issues else "texts are consistent on the requested checks (%s)" % ", ".join(checks))
    return {"output": output, "data": {"issues": issues, "count": len(issues), "checks": checks},
            "warnings": ["%d consistency issue(s) between the two texts" % len(issues)] if issues else []}


def first_line(text):
    for line in core.canonical_newlines(text).split("\n"):
        if line.strip():
            return line.strip()
    return ""


def difflib_ratio(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


HANDLERS = {
    "media-text.transcript-cleaner": transcript_cleaner,
    "media-text.speaker-diarization-text-editor": diarization_editor,
    "media-text.caption-and-subtitle-writer": caption_writer,
    "media-text.subtitle-localization-editor": subtitle_localization,
    "media-text.ocr-cleanup-specialist": ocr_cleanup,
    "media-text.scanned-table-text-reconstructor": table_reconstructor,
    "media-text.audio-script-and-pronunciation-writer": audio_script,
    "media-text.teleprompter-text-formatter": teleprompter,
    "media-text.text-to-speech-preparation-editor": tts_prep,
    "media-text.video-chapter-and-summary-writer": video_chapters,
    "media-text.on-screen-text-planner": onsite_text_planner,
    "media-text.multimodal-text-consistency-checker": multimodal_consistency,
}
