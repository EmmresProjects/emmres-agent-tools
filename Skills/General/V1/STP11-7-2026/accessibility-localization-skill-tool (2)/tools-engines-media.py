"""Images, alternative text, captions, audio description and document engines.

Categories served: 8 (Images, Alt Text, Graphics, Charts), 9 (Audio, Video,
Captions, Subtitles, Audio Description), 10 (Accessible Documents, PDFs and
Office Files).

All parsing is local and dependency-free: WebVTT/SRT text parsing, ZIP+XML
inspection of OOXML and EPUB containers, and a structural scan of raw PDF bytes
for language, tagging, title, alternative text and encryption signals.
"""

import os
import re
import zipfile
import io

S = None
T = None
H = None

OPS = {}


def init(deps):
    global S, T, H
    S = deps["support"]
    T = deps["text"]
    H = deps["html"]


def op(name, doc, params=None, accepts=("html",), engine="media"):
    def deco(fn):
        OPS[name] = {"name": name, "doc": doc, "params": params or {}, "accepts": list(accepts),
                     "summary": doc.split(".")[0], "fn": fn, "engine": engine, "groups": []}
        return fn
    return deco


def _doc(ctx):
    return ctx.docs[0].data.get("document")


def _records(ctx):
    data = ctx.docs[0].data
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("records", "items", "images", "cues", "elements", "entries", "scenes"):
            if isinstance(data.get(key), list):
                return data[key]
    raise S.InputError("Expected a JSON list or an object containing a records list.",
                       hint="See knowledge-readme.md for the accepted record shapes.")


# --------------------------------------------------------------------------- #
# Images and alternative text
# --------------------------------------------------------------------------- #
ALT_REDUNDANT = re.compile(r"^\s*(image|picture|photo|graphic|icon|logo|screenshot|illustration)\s+(of|showing|shows)?\s*", re.I)


@op("alt-text-audit", "Audit alternative text for images and graphics: presence, redundancy, file "
    "names, keyword stuffing, length, decorative handling and context fit.",
    params={"max_chars": {"type": "int", "default": 150, "doc": "Longest acceptable alt text."},
            "min_chars": {"type": "int", "default": 3, "doc": "Shortest acceptable meaningful alt text."},
            "require_decorative_marker": {"type": "bool", "default": True,
                                          "doc": "Flag images whose alt is empty but which look informational."}},
    accepts=("html", "json"))
def run_alt_text(ctx):
    max_chars = int(ctx.params.get("max_chars", 150))
    min_chars = int(ctx.params.get("min_chars", 3))
    items = []
    if ctx.docs[0].kind == "html":
        doc = _doc(ctx)
        for n in doc.by_tag("img", "area", "input"):
            if n.tag == "input" and (n.get("type") or "").lower() != "image":
                continue
            items.append({
                "alt": n.get("alt"),
                "src": n.get("src") or "",
                "role": H.role_of(n),
                "locator": n.locator(),
                "node": n,
                "context": T.norm_ws((n.parent.text() if n.parent is not None else "")),
            })
    else:
        for i, rec in enumerate(_records(ctx)):
            items.append({
                "alt": rec.get("alt"), "src": rec.get("src") or rec.get("file") or "",
                "role": rec.get("role") or "img", "locator": rec.get("locator") or "record %d" % (i + 1),
                "node": None, "context": rec.get("context") or "",
                "decorative": rec.get("decorative", False),
            })
    if not items:
        raise S.InputError("No images found in the artifact.", hint="Point --input at HTML containing <img> or a JSON image list.")
    informative = 0
    for item in items:
        ctx.checkpoint()
        alt = item["alt"]
        loc = item["locator"]
        evidence = {"src": item["src"][:120], "alt": alt, "context": T.truncate(item["context"], 160)}
        if alt is None:
            if item.get("decorative"):
                ctx.add(rule="alt-decorative", message="Decorative image is not marked with alt=\"\" and role=\"presentation\".",
                        severity="medium", criterion="1.1.1", locator=loc, evidence=evidence,
                        remediation="Use alt=\"\" (and no role) so assistive technology skips the image.",
                        retest="Re-run and confirm the image is treated as decorative.")
            else:
                ctx.add(rule="alt-missing", message="Image has no alt attribute.",
                        severity="critical", criterion="1.1.1", locator=loc, evidence=evidence,
                        remediation="Add concise alt text describing the purpose, or alt=\"\" if decorative.",
                        retest="Re-run and confirm the image exposes an alt attribute.")
            continue
        alt_s = T.norm_ws(alt)
        if alt_s == "":
            ctx.pass_check()
            continue
        informative += 1
        flags = []
        if ALT_REDUNDANT.match(alt_s) and len(alt_s.split()) > 2:
            flags.append(("alt-redundant", "Alt text starts with a redundant phrase such as 'image of'.", "medium"))
        if re.match(r"^[\w\-. ]+\.(png|jpe?g|gif|svg|webp|avif|bmp)$", alt_s, re.I):
            flags.append(("alt-filename", "Alt text is a file name rather than a description.", "high"))
        if len(alt_s) < min_chars:
            flags.append(("alt-too-short", "Alt text shorter than %d characters." % min_chars, "medium"))
        if len(alt_s) > max_chars:
            flags.append(("alt-too-long", "Alt text is %d characters; keep it under %d." % (len(alt_s), max_chars), "low"))
        words = [w.lower() for w in T.words(alt_s)]
        if len(words) >= 6 and len(set(words)) <= max(2, len(words) // 3):
            flags.append(("alt-keyword-stuffing", "Alt text repeats the same words (keyword stuffing).", "medium"))
        if re.search(r"\b(click here|link to|see below|read more)\b", alt_s, re.I) and item["role"] not in ("button", "link"):
            flags.append(("alt-instruction", "Alt text gives instructions that belong in surrounding text.", "low"))
        if item["role"] == "presentation" and alt_s:
            flags.append(("alt-presentation-conflict", "Image is presentation but carries alt text.", "medium"))
        if flags:
            for rule_id, message, sev in flags:
                ctx.add(rule=rule_id, message=message, severity=sev, criterion="1.1.1",
                        locator=loc, evidence=dict(evidence, alt=alt_s),
                        remediation="Rewrite the alt text to state the image's purpose in context.",
                        retest="Re-run alt-text-audit and confirm no alt-quality finding remains.")
        else:
            ctx.pass_check()
    ctx.meta("images", len(items))
    ctx.meta("informative", informative)
    ctx.meta("decorative", len(items) - informative)


@op("chart-accessibility", "Check chart and data-visualisation accessibility: accessible name, long "
    "description or data-table alternative, colour-independence cues and figure/figcaption structure.",
    params={"require_data_table": {"type": "bool", "default": True,
                                   "doc": "Whether a data-table or long description is required."}},
    accepts=("html", "json"))
def run_chart(ctx):
    require_table = bool(ctx.params.get("require_data_table", True))
    if ctx.docs[0].kind == "html":
        doc = _doc(ctx)
        charts = []
        for n in doc.nodes:
            marker = " ".join(sorted(n.classes)) + " " + (n.get("role") or "") + " " + n.tag
            if re.search(r"chart|graph|canvas|svg|figure|plot|visuali[sz]ation", marker, re.I):
                charts.append(n)
        seen = set()
        charts = [c for c in charts if not (id(c) in seen or seen.add(id(c)))]
        for n in charts:
            ctx.checkpoint()
            name, src = H.accessible_name(doc, n)
            has_table = bool(n.find_all("table")) or bool(doc.by_tag("table"))
            has_desc = bool(n.get("aria-describedby")) or bool(doc.body_text() and
                                                               re.search(r"data table|chart data|source data", doc.body_text(), re.I))
            if not T.norm_ws(name):
                ctx.add(rule="chart-name", message="Chart container has no accessible name.",
                        severity="high", criterion="1.1.1", locator=n.locator(), evidence={"tag": n.tag},
                        remediation="Add role=\"img\" with aria-label or a <figure>/<figcaption> pair naming the chart.",
                        retest="Re-run and confirm the chart exposes a name.")
            else:
                ctx.pass_check()
            if require_table and not (has_table or has_desc):
                ctx.add(rule="chart-alternative", message="Chart has no data table or long description.",
                        severity="high", criterion="1.1.1", locator=n.locator(),
                        evidence={"has_table": has_table, "has_description": has_desc},
                        remediation="Provide the underlying data as an accessible table or a detailed text description.",
                        retest="Re-run and confirm a table or description is detected.")
            else:
                ctx.pass_check()
        ctx.meta("charts", len(charts))
        if not charts:
            ctx.note("No chart-like containers detected; verify manually if visualisations are present.")
    else:
        for i, rec in enumerate(_records(ctx)):
            name = rec.get("name") or rec.get("title")
            table = rec.get("data_table") or rec.get("alternative")
            if not name:
                ctx.add(rule="chart-name", message="Chart %r has no accessible name." % (rec.get("id") or i + 1),
                        severity="high", criterion="1.1.1", locator="record %d" % (i + 1), evidence=rec,
                        remediation="Name the chart in an adjacent heading or aria-label.",
                        retest="Re-run and confirm the chart has a name.")
            else:
                ctx.pass_check()
            if require_table and not table:
                ctx.add(rule="chart-alternative", message="Chart %r has no table or long description." % (name or i + 1),
                        severity="high", criterion="1.1.1", locator="record %d" % (i + 1), evidence=rec,
                        remediation="Supply the data table or a long description.",
                        retest="Re-run and confirm an alternative is recorded.")
            else:
                ctx.pass_check()
            series = rec.get("series") or []
            if len(series) > 1 and not rec.get("patterns") and not rec.get("labels_on_series"):
                ctx.add(rule="chart-colour-only", message="Multiple series distinguished by colour alone.",
                        severity="medium", criterion="1.4.1", locator="record %d" % (i + 1),
                        evidence={"series": len(series)},
                        remediation="Add direct labels, patterns, or shapes per series.",
                        retest="Re-run and confirm a non-colour distinction is recorded.")
            else:
                ctx.pass_check()


# --------------------------------------------------------------------------- #
# Captions and subtitles
# --------------------------------------------------------------------------- #
TIMESTAMP = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})\s*-->\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2}):(?P<s2>\d{2})[.,](?P<ms2>\d{1,3})(?P<settings>.*)$")


def _secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_captions(text, source="captions"):
    cues, errors = [], []
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kind = "vtt" if (lines and lines[0].strip().upper().startswith("WEBVTT")) else "srt"
    i = 0
    n = len(lines)
    order = 0
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if kind == "vtt" and i == 0:
            i += 1
            continue
        if line.upper().startswith(("NOTE", "STYLE", "REGION")) and kind == "vtt":
            while i < n and lines[i].strip():
                i += 1
            continue
        m = TIMESTAMP.search(line)
        if not m:
            if re.match(r"^\d+$", line):
                i += 1
                continue
            errors.append({"kind": "unexpected_line", "line": i + 1, "text": T.truncate(line, 80)})
            i += 1
            continue
        start = _secs(m.group("h"), m.group("m"), m.group("s"), m.group("ms"))
        end = _secs(m.group("h2"), m.group("m2"), m.group("s2"), m.group("ms2"))
        settings = (m.group("settings") or "").strip()
        i += 1
        body = []
        while i < n and lines[i].strip():
            body.append(lines[i])
            i += 1
        order += 1
        raw = "\n".join(body)
        plain = re.sub(r"<[^>]+>", "", raw)
        cues.append({
            "index": order,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "lines": body,
            "text": T.norm_ws(plain),
            "raw": raw,
            "settings": settings,
            "identifier": None,
        })
    return {"kind": kind, "cues": cues, "errors": errors, "raw": text, "source": source}


def caption_metrics(parsed, thresholds):
    cues = parsed["cues"]
    cps_limit = float(thresholds.get("max_caption_cps", 17))
    min_dur = float(thresholds.get("min_caption_duration", 1.0))
    max_line = int(thresholds.get("max_caption_line_chars", 42))
    max_lines = int(thresholds.get("max_caption_lines", 2))
    rows = []
    for c in cues:
        dur = max(c["duration"], 0.001)
        chars = len(re.sub(r"\s+", " ", c["text"]))
        cps = round(chars / dur, 2)
        rows.append({
            "index": c["index"], "start": c["start"], "end": c["end"], "duration": c["duration"],
            "chars": chars, "cps": cps, "lines": len(c["lines"]),
            "max_line_chars": max((len(l) for l in c["lines"]), default=0),
            "reading_rate_ok": cps <= cps_limit,
            "min_duration_ok": c["duration"] >= min_dur,
            "has_speaker": bool(re.match(r"^(-|\w[\w .'\-]{0,24}:)", c["text"])),
        })
    overlaps = []
    for a, b in zip(cues, cues[1:]):
        if b["start"] < a["end"] - 0.001:
            overlaps.append({"first": a["index"], "second": b["index"],
                             "overlap_seconds": round(a["end"] - b["start"], 3)})
    gaps = []
    for a, b in zip(cues, cues[1:]):
        gap = round(b["start"] - a["end"], 3)
        if gap >= 2.0:
            gaps.append({"after": a["index"], "seconds": gap})
    return {
        "cues": rows,
        "overlaps": overlaps,
        "long_gaps": gaps,
        "thresholds": {"cps": cps_limit, "min_duration": min_dur, "max_line_chars": max_line,
                       "max_lines": max_lines},
        "spoken_characters": sum(r["chars"] for r in rows),
        "duration_covered": round(sum(c["duration"] for c in cues), 2),
    }


@op("captions-audit", "Parse and validate WebVTT/SRT captions: timing, overlaps, reading speed (CPS), "
    "line length, line count, minimum duration, speaker identification and structural errors.",
    params={"media_duration_seconds": {"type": "float", "default": None,
                                       "doc": "Media duration, enabling caption coverage analysis."},
            "min_coverage_pct": {"type": "float", "default": 60.0,
                                 "doc": "Minimum percentage of media duration that must be captioned."},
            "require_speakers": {"type": "bool", "default": False,
                                 "doc": "Require speaker labels for multi-speaker content."}},
    accepts=("captions",))
def run_captions(ctx):
    text = ctx.docs[0].text
    parsed = parse_captions(text, ctx.docs[0].path)
    th = ctx.config.get("thresholds", {})
    metrics = caption_metrics(parsed, th)
    if not parsed["cues"]:
        raise S.InputError("No caption cues were parsed.",
                          hint="Check the file uses WebVTT or SRT cue timing such as 00:00:01.000 --> 00:00:03.000.")
    for err in parsed["errors"]:
        ctx.add(rule="caption-structure", message="Unexpected caption line %d: %r." % (err["line"], err["text"]),
                severity="medium", criterion="1.2.2", locator="line %d" % err["line"], evidence=err,
                remediation="Remove stray text or fix the cue timestamp.",
                retest="Re-run captions-audit and confirm no structural error is reported.")
    if parsed["kind"] == "srt":
        ctx.note("File parsed as SRT; WebVTT is preferred for web delivery.")
    for row in metrics["cues"]:
        loc = "cue %d (%s-%s)" % (row["index"], row["start"], row["end"])
        if not row["reading_rate_ok"]:
            ctx.add(rule="caption-reading-rate", message="Cue %d reads at %.1f characters/second (> %.1f)." % (
                row["index"], row["cps"], metrics["thresholds"]["cps"]), severity="high", criterion="1.2.2",
                locator=loc, evidence=row,
                remediation="Split the cue or shorten the text so reading speed stays within the limit.",
                retest="Re-run and confirm cps is within the configured limit for every cue.")
        else:
            ctx.pass_check()
        if not row["min_duration_ok"]:
            ctx.add(rule="caption-min-duration", message="Cue %d lasts %.2fs, below the %.1fs minimum." % (
                row["index"], row["duration"], metrics["thresholds"]["min_duration"]), severity="medium",
                criterion="1.2.2", locator=loc, evidence=row,
                remediation="Extend the cue or merge it with an adjacent cue.",
                retest="Re-run and confirm every cue meets the minimum duration.")
        else:
            ctx.pass_check()
        if row["max_line_chars"] > metrics["thresholds"]["max_line_chars"]:
            ctx.add(rule="caption-line-length", message="Cue %d has a %d character line (> %d)." % (
                row["index"], row["max_line_chars"], metrics["thresholds"]["max_line_chars"]), severity="low",
                criterion="1.2.2", locator=loc, evidence=row,
                remediation="Wrap the cue to at most 42 characters per line.",
                retest="Re-run and confirm line lengths pass.")
        else:
            ctx.pass_check()
        if row["lines"] > metrics["thresholds"]["max_lines"]:
            ctx.add(rule="caption-line-count", message="Cue %d uses %d lines (> %d)." % (
                row["index"], row["lines"], metrics["thresholds"]["max_lines"]), severity="low",
                criterion="1.2.2", locator=loc, evidence=row,
                remediation="Reduce to two lines per cue.",
                retest="Re-run and confirm cue line counts pass.")
        else:
            ctx.pass_check()
        if ctx.params.get("require_speakers") and not row["has_speaker"]:
            ctx.add(rule="caption-speaker", message="Cue %d has no speaker identification." % row["index"],
                    severity="medium", criterion="1.2.2", locator=loc, evidence=row,
                    remediation="Prefix cues with the speaker name or use WebVTT voice spans.",
                    retest="Re-run and confirm every cue identifies its speaker.")
        else:
            ctx.pass_check()
    for ov in metrics["overlaps"]:
        ctx.add(rule="caption-overlap", message="Cues %d and %d overlap by %.2fs." % (
            ov["first"], ov["second"], ov["overlap_seconds"]), severity="medium", criterion="1.2.2",
            locator="cues %d-%d" % (ov["first"], ov["second"]), evidence=ov,
            remediation="Adjust cue end times so captions do not overlap unless intentionally stacked.",
            retest="Re-run and confirm no overlapping cues remain.")
    media_dur = ctx.params.get("media_duration_seconds")
    coverage = None
    if media_dur:
        media_dur = float(media_dur)
        captioned_ranges = []
        for c in parsed["cues"]:
            if captioned_ranges and c["start"] <= captioned_ranges[-1][1]:
                captioned_ranges[-1][1] = max(captioned_ranges[-1][1], c["end"])
            else:
                captioned_ranges.append([c["start"], c["end"]])
        covered = sum(e - s for s, e in captioned_ranges)
        coverage = round(100.0 * covered / media_dur, 2)
        min_cov = float(ctx.params.get("min_coverage_pct", 60.0))
        if coverage < min_cov:
            ctx.add(rule="caption-coverage", message="Captions cover %.1f%% of the %.1fs media (< %.1f%%)." % (
                coverage, media_dur, min_cov), severity="high", criterion="1.2.2",
                locator="whole file", evidence={"coverage_pct": coverage, "covered_seconds": round(covered, 2)},
                remediation="Caption the uncaptioned segments, including meaningful non-speech sound.",
                retest="Re-run with the same media duration and confirm coverage meets the target.")
        else:
            ctx.pass_check()
    ctx.meta("caption_file_kind", parsed["kind"])
    ctx.meta("cue_count", len(parsed["cues"]))
    ctx.meta("spoken_characters", metrics["spoken_characters"])
    ctx.meta("duration_covered_seconds", metrics["duration_covered"])
    ctx.meta("coverage_pct", coverage)
    ctx.meta("thresholds", metrics["thresholds"])
    ctx.meta("worst_cps", max((r["cps"] for r in metrics["cues"]), default=0))
    ctx.note("Accuracy, sync and speaker identification still require human caption review.")


@op("caption-quality-gate", "Score caption quality against configurable accuracy, reading-speed, "
    "coverage and completeness rules and return a pass/fail gate with the failing cues listed.",
    params={"accuracy_target_pct": {"type": "float", "default": 99.0,
                                    "doc": "Target word-accuracy borrowed from the human review result."},
            "human_accuracy_pct": {"type": "float", "default": None,
                                   "doc": "Measured accuracy from human review, when available."}},
    accepts=("captions", "json"))
def run_caption_gate(ctx):
    if ctx.docs[0].kind == "captions":
        parsed = parse_captions(ctx.docs[0].text, ctx.docs[0].path)
        metrics = caption_metrics(parsed, ctx.config.get("thresholds", {}))
        bad = [r for r in metrics["cues"] if not r["reading_rate_ok"] or not r["min_duration_ok"]]
    else:
        data = ctx.docs[0].data
        items = data.get("cues") if isinstance(data, dict) else data
        bad = [c for c in items or [] if not c.get("accuracy_ok", True) or not c.get("timing_ok", True)]
        metrics = {"cues": items or []}
    target = float(ctx.params.get("accuracy_target_pct", 99.0))
    measured = ctx.params.get("human_accuracy_pct")
    decision = "pass"
    if bad:
        decision = "fail"
        ctx.add(rule="caption-gate", message="%d caption cue(s) fail the quality gate." % len(bad),
                severity="high", criterion="1.2.2", evidence={"cues": bad[:20]},
                remediation="Correct the failing cues, then re-run the gate.",
                retest="Re-run caption-quality-gate and confirm decision=pass.")
    else:
        ctx.pass_check()
    if measured is not None and float(measured) < target:
        decision = "fail"
        ctx.add(rule="caption-accuracy", message="Measured accuracy %.2f%% is below the %.2f%% target." % (
            float(measured), target), severity="high", criterion="1.2.2",
            remediation="Send the file for caption re-editing with the error log attached.",
            retest="Repeat human accuracy measurement on the corrected file.")
    ctx.meta("decision", decision)
    ctx.meta("cues_checked", len(metrics["cues"]))
    ctx.meta("accuracy_target_pct", target)


@op("audio-description-script", "Build an audio-description script from a scene list and check that each "
    "description fits the available gap at a sustainable speech rate.",
    params={"words_per_second": {"type": "float", "default": 2.6,
                                  "doc": "Speech rate used to test whether descriptions fit."},
            "min_gap_seconds": {"type": "float", "default": 1.5,
                                "doc": "Shortest gap worth describing."}},
    accepts=("json",))
def run_ad_script(ctx):
    scenes = _records(ctx)
    wps = float(ctx.params.get("words_per_second", 2.6))
    min_gap = float(ctx.params.get("min_gap_seconds", 1.5))
    script = []
    for i, sc in enumerate(scenes):
        ctx.checkpoint()
        try:
            start = float(sc.get("start", 0))
            end = float(sc.get("end", start))
        except (TypeError, ValueError):
            ctx.add(rule="ad-timing", message="Scene %d has non-numeric timings." % (i + 1), severity="low",
                    criterion="1.2.3", evidence=sc,
                    remediation="Provide start/end in seconds.",
                    retest="Re-run and confirm timings parse.")
            continue
        gap = round(max(0.0, end - start), 2)
        dialogue = bool(sc.get("dialogue"))
        visual = T.norm_ws(sc.get("visual") or sc.get("description") or "")
        if dialogue or gap < min_gap or not visual:
            script.append({**{k: sc.get(k) for k in ("start", "end", "visual", "dialogue")},
                           "action": "skip", "reason": "dialogue" if dialogue else
                           ("insufficient gap" if gap < min_gap else "no visual information"),
                           "gap_seconds": gap})
            ctx.pass_check()
            continue
        budget_words = int(gap * wps)
        words = T.word_count(visual)
        action = "describe" if words <= budget_words else "condense"
        script.append({"start": start, "end": end, "gap_seconds": gap, "visual": visual,
                       "action": action, "word_budget": budget_words, "words": words,
                       "estimated_seconds": round(words / wps, 2)})
        if action == "condense":
            ctx.add(rule="ad-overrun", message="Description for %.1fs-%.1fs needs %.1fs of speech." % (
                start, end, words / wps), severity="medium", criterion="1.2.3",
                locator="scene %d" % (i + 1), evidence={"words": words, "budget": budget_words},
                remediation="Condense the description so it fits the gap, or extend the pause.",
                retest="Re-run and confirm every description fits its gap.")
        else:
            ctx.pass_check()
    ctx.meta("script", script)
    ctx.meta("descriptions", sum(1 for s in script if s.get("action") in ("describe", "condense")))
    ctx.note("Description content must be written by a human describer who has viewed the media.")


@op("media-player-audit", "Audit HTML media embeds: captions and description tracks, controls, "
    "autoplay, keyboard access, poster text and language metadata.",
    params={"expected_caption_lang": {"type": "str", "default": None,
                                      "doc": "BCP-47 tag required on the caption track."}},
    accepts=("html",))
def run_media_player(ctx):
    doc = _doc(ctx)
    players = doc.by_tag("video", "audio", "iframe", "object", "embed")
    if not players:
        raise S.InputError("No media elements found in the HTML.",
                           hint="Point --input at a page containing <video> or <audio>.")
    expected = ctx.params.get("expected_caption_lang")
    for n in players:
        ctx.checkpoint()
        tracks = [c for c in n.child_elements() if c.tag == "track"]
        kinds = {(t.get("kind") or "subtitles").lower() for t in tracks}
        if n.tag == "video":
            if not ({"captions", "subtitles"} & kinds):
                ctx.add(rule="media-captions", message="Video has no captions or subtitles track.",
                        severity="critical", criterion="1.2.2", locator=n.locator(),
                        remediation="Add <track kind=\"captions\" srclang=\"fil\" src=\"...vtt\" default>.",
                        retest="Re-run and confirm a captions track is listed.")
            else:
                ctx.pass_check()
            if "descriptions" not in kinds:
                ctx.add(rule="media-descriptions", message="Video has no audio-description track.",
                        severity="high", criterion="1.2.3", locator=n.locator(),
                        remediation="Add kind=\"descriptions\" or link a described version/transcript.",
                        retest="Re-run and confirm a descriptions track or transcript is present.")
            else:
                ctx.pass_check()
        if not n.has("controls") and not n.has("data-custom-controls"):
            ctx.add(rule="media-controls", message="Media element exposes no controls.",
                    severity="high", criterion="2.1.1", locator=n.locator(),
                    remediation="Add the controls attribute or accessible custom controls.",
                    retest="Re-run and confirm controls are exposed.")
        else:
            ctx.pass_check()
        if expected:
            langs = [t.get("srclang") for t in tracks if (t.get("kind") or "").lower() in ("captions", "subtitles")]
            if langs and expected not in langs:
                ctx.add(rule="media-caption-lang", message="Caption language %r not among %r." % (expected, langs),
                        severity="medium", criterion="1.2.2", locator=n.locator(),
                        remediation="Add a caption track in the page language and set srclang.",
                        retest="Re-run and confirm the expected language is present.")
            else:
                ctx.pass_check()
        if n.tag == "iframe" and not T.norm_ws(n.get("title")):
            ctx.add(rule="media-iframe-title", message="Media iframe has no title.", severity="high",
                    criterion="4.1.2", locator=n.locator(),
                    remediation="Give the iframe a title describing the embedded media.",
                    retest="Re-run and confirm the iframe has a title.")
        else:
            ctx.pass_check()
    ctx.meta("players", len(players))


# --------------------------------------------------------------------------- #
# Documents: PDF, OOXML, EPUB
# --------------------------------------------------------------------------- #
def _pdf_scan(data):
    text = data.decode("latin-1", "replace")
    facts = {
        "is_pdf": text.lstrip()[:5] == "%PDF-" or "%PDF-" in text[:1024],
        "encrypted": "/Encrypt" in text,
        "tagged": bool(re.search(r"/MarkInfo\s*<<[^>]*?/Marked\s+true", text)) or "/StructTreeRoot" in text,
        "has_lang": bool(re.search(r"/Lang\s*\(([^)]{2,20})\)", text)),
        "lang_value": (re.search(r"/Lang\s*\(([^)]{2,20})\)", text).group(1) if re.search(r"/Lang\s*\(([^)]{2,20})\)", text) else None),
        "has_title": bool(re.search(r"/Title\s*[\(<]", text)),
        "has_alt": bool(re.search(r"/Alt\s*[\(<]", text)),
        "has_outline": "/Outlines" in text,
        "form_fields": len(re.findall(r"/Subtype\s*/Widget", text)),
        "fields_without_tooltip": len(re.findall(r"/Subtype\s*/Widget(?:(?!/TU).){0,400}?/Type\s*/Annot", text, re.S)),
        "pages": max(len(re.findall(r"/Type\s*/Page[^s]", text)), 1),
        "images": len(re.findall(r"/Subtype\s*/Image", text)),
        "s_images_with_alt": len(re.findall(r"/Alt\s*[\(<]", text)),
        "has_display_doc_title": "/DisplayDocTitle" in text,
        "producer_xmp_lang": "dc:language" in text,
    }
    return facts


def _ooxml_scan(path, data):
    facts = {"kind": "ooxml", "exists": True, "parts": [], "language": None, "title": None}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise S.InputError("File is not a valid OOXML/ZIP container: %s" % path,
                           hint="Confirm the file is .docx/.pptx/.xlsx and not an older .doc/.ppt/.xls.")
    facts["parts"] = zf.namelist()
    names = set(zf.namelist())
    core = None
    for candidate in ("docProps/core.xml", "docProps/app.xml"):
        if candidate in names:
            core = zf.read(candidate).decode("utf-8", "replace")
            break
    if core:
        m = re.search(r"<dc:language>(.*?)</dc:language>", core)
        facts["language"] = m.group(1).strip() if m else None
        m = re.search(r"<dc:title>(.*?)</dc:title>", core)
        facts["title"] = m.group(1).strip() if m else None
    body = None
    for candidate in ("word/document.xml", "ppt/presentation.xml", "xl/workbook.xml"):
        if candidate in names:
            body = zf.read(candidate).decode("utf-8", "replace")
            facts["kind"] = candidate.split("/")[0]
            break
    facts["body"] = body or ""
    if "word/document.xml" in names:
        doc_xml = zf.read("word/document.xml").decode("utf-8", "replace")
        facts["headings"] = len(re.findall(r'w:pStyle\s+w:val="Heading\d"', doc_xml))
        facts["title_style"] = len(re.findall(r'w:pStyle\s+w:val="Title"', doc_xml))
        facts["tables"] = len(re.findall(r"<w:tbl>", doc_xml))
        facts["images"] = len(re.findall(r"<wp:docPr\b", doc_xml))
        facts["images_with_alt"] = len(re.findall(r'<wp:docPr\b[^>]*\bdescr="[^"]+"', doc_xml))
        facts["empty_alt"] = len(re.findall(r'<wp:docPr\b[^>]*\bdescr=""', doc_xml))
        facts["header_rows"] = len(re.findall(r"<w:tblHeader", doc_xml))
        facts["lists"] = len(re.findall(r'w:numPr', doc_xml))
        facts["links"] = len(re.findall(r"<w:hyperlink\b", doc_xml))
        facts["text_runs"] = len(re.findall(r"<w:t[ >]", doc_xml))
        facts["lang_runs"] = len(re.findall(r'w:lang\s+w:val="([^"]+)"', doc_xml))
    if "ppt/presentation.xml" in names:
        slides = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        facts["slides"] = len(slides)
        titles = alts = 0
        for s in slides:
            xml = zf.read(s).decode("utf-8", "replace")
            titles += 1 if re.search(r"<p:ph[^>]*type=\"(title|ctrTitle)\"", xml) else 0
            for m in re.finditer(r"<p:cNvPr\b[^>]*>", xml):
                tag = m.group(0)
                if 'descr="' in tag and 'descr=""' not in tag:
                    alts += 1
                elif 'name="' in tag and not re.search(r'name="(Title|Text Placeholder|Content Placeholder)', tag):
                    pass
        facts["slides_with_titles"] = titles
        facts["shapes_with_alt"] = alts
    if "xl/workbook.xml" in names:
        wb = zf.read("xl/workbook.xml").decode("utf-8", "replace")
        facts["sheets"] = len(re.findall(r"<sheet\b", wb))
        facts["defined_names"] = len(re.findall(r"<definedName\b", wb))
        facts["tables"] = len([n for n in names if n.startswith("xl/tables/")])
    return facts


def _epub_scan(path, data):
    facts = {"kind": "epub", "exists": True}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise S.InputError("File is not a valid EPUB container: %s" % path)
    names = zf.namelist()
    facts["mimetype_ok"] = "mimetype" in names
    opf = next((n for n in names if n.endswith(".opf")), None)
    facts["opf"] = opf
    if opf:
        xml = zf.read(opf).decode("utf-8", "replace")
        m = re.search(r"<dc:language[^>]*>(.*?)</dc:language>", xml)
        facts["language"] = m.group(1).strip() if m else None
        m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", xml)
        facts["title"] = m.group(1).strip() if m else None
    facts["nav_doc"] = any(re.search(r"nav", n) and n.endswith(".xhtml") for n in names)
    facts["ncx"] = any(n.endswith(".ncx") for n in names)
    html_parts = [n for n in names if n.endswith((".xhtml", ".html", ".htm"))]
    facts["content_docs"] = len(html_parts)
    imgs = alts = 0
    for part in html_parts[: max(1, min(len(html_parts), 40))]:
        body = zf.read(part).decode("utf-8", "replace")
        imgs += len(re.findall(r"<img\b", body))
        alts += len(re.findall(r'<img\b[^>]*\balt="[^"]+"', body))
        if re.search(r"<img\b(?![^>]*\balt=)", body):
            facts.setdefault("img_without_alt", 0)
            facts["img_without_alt"] += len(re.findall(r"<img\b(?![^>]*\balt=)", body))
    facts["images"] = imgs
    facts["images_with_alt"] = alts
    return facts


@op("document-audit", "Audit a document for accessibility metadata and structure: PDF tagging, "
    "language, title, alt text and encryption; OOXML headings, alt text, tables and language; EPUB "
    "package, navigation and image alternatives.",
    params={"require_language": {"type": "bool", "default": True, "doc": "Fail when no language is declared."},
            "require_title": {"type": "bool", "default": True, "doc": "Fail when no document title exists."},
            "required_lang": {"type": "str", "default": None, "doc": "Exact language expected, e.g. fil-PH."}},
    accepts=("document",))
def run_document_audit(ctx):
    doc = ctx.docs[0]
    ext = os.path.splitext(doc.path)[1].lower()
    if ext == ".pdf":
        facts = _pdf_scan(doc.data["raw"])
        facts["format"] = "pdf"
        if not facts["is_pdf"]:
            raise S.InputError("File does not start with a PDF header.", hint="Confirm the file is a real PDF.")
        checks = [
            (facts["tagged"], "pdf-tagged", "PDF is not tagged, so reading order cannot be determined.",
             "critical", "1.3.1", "Re-export as a tagged PDF (PDF/UA) from the authoring tool."),
            (facts["has_lang"], "pdf-language", "PDF has no /Lang entry.",
             "critical", "3.1.1", "Set the document language in the PDF properties and re-export."),
            (facts["has_title"], "pdf-title", "PDF has no document title metadata.",
             "high", "2.4.2", "Set the document title in document properties and enable DisplayDocTitle."),
            (not facts["encrypted"], "pdf-encryption", "PDF is encrypted; assistive technology may be blocked.",
             "high", "1.3.1", "Remove encryption or enable accessibility permissions, then re-export."),
            (facts["images"] == 0 or facts["s_images_with_alt"] > 0, "pdf-alt",
             "PDF contains images but no /Alt alternative text.",
             "high", "1.1.1", "Add alt text to images in the authoring tool before exporting."),
            (facts["has_outline"] or facts["tagged"], "pdf-outline", "PDF has no bookmarks/outline or tag structure.",
             "medium", "1.3.1", "Generate bookmarks from heading styles and keep tag structure."),
        ]
        if facts["form_fields"]:
            checks.append((facts["fields_without_tooltip"] == 0, "pdf-form-tooltip",
                           "Form fields are missing tooltips (TU entries).",
                           "high", "3.3.2", "Add tooltips/labels to every form field."))
        if facts["has_title"] and not facts["has_display_doc_title"]:
            ctx.add(rule="pdf-display-title", message="DisplayDocTitle is not set, so the window shows the file name.",
                    severity="low", criterion="2.4.2", evidence=facts,
                    remediation="Enable 'Display document title' in the PDF viewer preferences.",
                    retest="Re-run and confirm DisplayDocTitle is present.")
        for ok, rule_id, message, sev, crit, remediation in checks:
            if ok:
                ctx.pass_check()
            else:
                ctx.add(rule=rule_id, message=message + " [%s]" % doc.path, severity=sev, criterion=crit,
                        evidence={k: v for k, v in facts.items() if k != "raw"},
                        remediation=remediation,
                        retest="Re-run document-audit on the re-exported file and confirm the check passes.")
        ctx.meta("pdf", {k: v for k, v in facts.items() if k != "raw"})
    elif ext in (".docx", ".pptx", ".xlsx"):
        facts = _ooxml_scan(doc.path, doc.data["raw"])
        facts["format"] = ext.lstrip(".")
        checks = []
        if ctx.params.get("require_title", True):
            checks.append((bool(facts.get("title")), "doc-title", "Document core properties have no title.",
                           "medium", "2.4.2", "Set the document title in core properties."))
        if ctx.params.get("require_language", True):
            checks.append((bool(facts.get("language")), "doc-language", "Document does not declare a language.",
                           "high", "3.1.1", "Set the document language (Review > Language) and re-save."))
        if ext == ".docx":
            checks += [
                (facts.get("headings", 0) > 0 or facts.get("title_style", 0) > 0, "doc-headings",
                 "No heading styles detected; the document is visually, not structurally, organised.",
                 "high", "1.3.1", "Apply real Heading 1-6 styles instead of manual bold/large text."),
                (facts.get("images", 0) == 0 or facts.get("images_with_alt", 0) >= facts.get("images", 0),
                 "doc-alt", "%d of %d images have no alternative text." % (
                     facts.get("images", 0) - facts.get("images_with_alt", 0), facts.get("images", 0)),
                 "critical", "1.1.1", "Add alt text to every informative image and mark decorative ones."),
                (facts.get("tables", 0) == 0 or facts.get("header_rows", 0) > 0, "doc-table-header",
                 "Tables do not repeat header rows.",
                 "medium", "1.3.1", "Mark the header row as a repeating header row."),
            ]
        elif ext == ".pptx":
            checks += [
                (facts.get("slides", 0) == 0 or facts.get("slides_with_titles", 0) >= max(1, facts.get("slides", 1) // 2),
                 "ppt-titles", "Only %d of %d slides use a title placeholder." % (
                     facts.get("slides_with_titles", 0), facts.get("slides", 0)),
                 "high", "1.3.1", "Use slide layouts with title placeholders so each slide has a heading."),
                (facts.get("shapes_with_alt", 0) > 0 or "media" not in " ".join(facts.get("parts", [])),
                 "ppt-alt", "Slides contain media without alternative text.",
                 "high", "1.1.1", "Add alt text to every image, chart and shape carrying meaning."),
            ]
        else:
            checks += [
                (facts.get("sheets", 0) > 0, "xls-sheets", "Workbook has no worksheets.", "medium", "1.3.1",
                 "Add at least one worksheet."),
                (facts.get("tables", 0) > 0 or facts.get("defined_names", 0) > 0, "xls-tables",
                 "No defined tables or named ranges; data regions are unstructured.", "low", "1.3.1",
                 "Format data ranges as tables and give each a table name."),
            ]
        required_lang = ctx.params.get("required_lang")
        if required_lang and facts.get("language") and facts["language"] != required_lang:
            checks.append((False, "doc-language-match",
                           "Document language %r does not match required %r." % (facts["language"], required_lang),
                           "medium", "3.1.1", "Set the document language to %s." % required_lang))
        for ok, rule_id, message, sev, crit, remediation in checks:
            if ok:
                ctx.pass_check()
            else:
                ctx.add(rule=rule_id, message=message, severity=sev, criterion=crit,
                        evidence={k: v for k, v in facts.items() if k not in ("body", "parts")},
                        remediation=remediation,
                        retest="Re-run document-audit on the updated file and confirm the check passes.")
        ctx.meta("office", {k: v for k, v in facts.items() if k not in ("body", "parts")})
    elif ext == ".epub":
        facts = _epub_scan(doc.path, doc.data["raw"])
        facts["format"] = "epub"
        checks = [
            (facts.get("mimetype_ok"), "epub-mimetype", "EPUB is missing the mimetype entry.", "medium", "1.3.1",
             "Repackage the EPUB with an uncompressed mimetype entry first."),
            (bool(facts.get("language")), "epub-language", "EPUB package declares no language.", "high", "3.1.1",
             "Add dc:language to the OPF metadata."),
            (bool(facts.get("title")), "epub-title", "EPUB package declares no title.", "medium", "2.4.2",
             "Add dc:title to the OPF metadata."),
            (facts.get("nav_doc") or facts.get("ncx"), "epub-nav", "EPUB has no navigation document or NCX.",
             "high", "1.3.1", "Add an EPUB 3 navigation document with the table of contents."),
            (facts.get("images", 0) == 0 or facts.get("images_with_alt", 0) >= facts.get("images", 0),
             "epub-alt", "%d image(s) lack alt text." % (facts.get("images", 0) - facts.get("images_with_alt", 0)),
             "critical", "1.1.1", "Add alt text to every informative image."),
        ]
        for ok, rule_id, message, sev, crit, remediation in checks:
            if ok:
                ctx.pass_check()
            else:
                ctx.add(rule=rule_id, message=message, severity=sev, criterion=crit, evidence=facts,
                        remediation=remediation,
                        retest="Re-run document-audit on the rebuilt EPUB and confirm the check passes.")
        ctx.meta("epub", facts)
    else:
        raise S.InputError("Unsupported document extension %r." % ext,
                           hint="Supported: .pdf, .docx, .pptx, .xlsx, .epub.")
    ctx.meta("format", ext.lstrip("."))
    ctx.note("Structural scans prove metadata and tagging signals only; manual reading-order, table and "
             "screen-reader verification of the rendered document is still required.")


@op("document-remediation-plan", "Turn document findings into an ordered remediation plan grouped by "
    "authoring tool step, with owner roles and retest instructions.",
    params={"tool": {"type": "enum", "values": ["word", "powerpoint", "excel", "acrobat", "epub"],
                     "default": "word", "doc": "Authoring tool the plan is written for."}},
    accepts=("json",))
def run_doc_plan(ctx):
    findings = []
    data = ctx.docs[0].data
    if isinstance(data, dict) and "findings" in data:
        findings = data["findings"]
    elif isinstance(data, list):
        findings = data
    if not findings:
        raise S.InputError("No findings supplied for planning.",
                           hint="Pass a result document produced by document-audit.")
    tool = ctx.params.get("tool", "word")
    recipes = {
        "pdf-tagged": ("Re-export from the source document with 'Tagged PDF' and 'Document structure tags' enabled.", "author"),
        "pdf-language": ("File > Properties > Advanced > Language, then re-export.", "author"),
        "pdf-title": ("File > Properties > Description > Title, then re-export with DisplayDocTitle.", "author"),
        "pdf-alt": ("Add alt text to each image in the source document, then re-export.", "author"),
        "doc-headings": ("Replace manual formatting with the Heading styles; verify the navigation pane.", "author"),
        "doc-alt": ("Right-click each image > Edit Alt Text, or mark it decorative.", "author"),
        "doc-language": ("Review > Language > Set Proofing Language; save and re-check core properties.", "author"),
        "doc-table-header": ("Table Design > Header Row, and set 'Repeat Header Rows' for long tables.", "author"),
        "ppt-titles": ("Switch slides to layouts with title placeholders; one heading per slide.", "designer"),
        "ppt-alt": ("Select each object > Alt Text pane > add a description or mark as decorative.", "designer"),
        "epub-alt": ("Edit the XHTML source and add alt attributes to every img element.", "developer"),
        "epub-nav": ("Add a nav.xhtml and reference it from the OPF spine/manifest.", "developer"),
    }
    plan = []
    for f in findings:
        rule_id = f.get("rule") or f.get("check")
        step, role = recipes.get(rule_id, ("Apply the documented fix for %s." % rule_id, "author"))
        plan.append({"order": len(plan) + 1, "rule": rule_id, "severity": f.get("severity"),
                     "wcag": f.get("wcag") or f.get("criterion"), "tool_step": step,
                     "owner_role": role, "retest": f.get("retest") or "Re-run document-audit."})
    plan.sort(key=lambda p: (-S.SEVERITY_RANK.get(p.get("severity") or "low", 1), p["order"]))
    for i, p in enumerate(plan, 1):
        p["order"] = i
    ctx.meta("tool", tool)
    ctx.meta("plan", plan)
    ctx.meta("steps", len(plan))
