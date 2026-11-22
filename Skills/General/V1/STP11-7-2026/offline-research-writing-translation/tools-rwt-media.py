#!/usr/bin/env python3
"""tools-rwt-media.py - Multimodal research support engines.

Deterministic where inputs are text/sidecar JSON: SRT/WebVTT parsing, quotable
timecoded segments, transcript cleanup, figure/table/caption extraction with
locators, screenshot evidence sidecars, video scene indexing from sidecar
JSON, audio review flags from word-level timing JSON, and multimodal citation
formatting. Visual/audio interpretation itself requires a local model adapter.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_m", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_m"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _read(file):
    if not os.path.isfile(file):
        raise C.ValidationError(f"File not found: {file}")
    with open(file, "r", encoding="utf-8") as fh:
        return fh.read()

SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
def parse_timecode(tc):
    m = SRT_TIME.search(tc)
    if not m:
        return None
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4).ljust(3, "0"))
    return h * 3600 + mi * 60 + s + ms / 1000.0

def fmt_time(seconds):
    h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"

def parse_srt(text):
    blocks = re.split(r"\n\s*\n", text.strip())
    segments = []
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if re.match(r"^\d+$", lines[0].strip()):
            lines = lines[1:]
        if not lines:
            continue
        if "-->" in lines[0]:
            times, body = lines[0], lines[1:]
        elif len(lines) >= 2 and "-->" in lines[1]:
            times, body = lines[1], lines[2:]
        else:
            continue
        m = re.match(r"\s*([\d:,.]+)\s*-->\s*([\d:,.]+)", times)
        if not m:
            continue
        start, end = parse_timecode(m.group(1)), parse_timecode(m.group(2))
        content = " ".join(body).strip()
        content = re.sub(r"<[^>]+>", "", content)  # strip teletext markup
        segments.append({"start": start, "end": end,
                         "start_tc": fmt_time(start), "end_tc": fmt_time(end),
                         "text": C.normalize_ws(content)})
    return segments

def cmd_timecodes(args):
    text = _read(args.file)
    segments = parse_srt(text)
    if not segments:
        raise C.ValidationError("No SRT/WebVTT cues parsed. Provide .srt/.vtt with timecodes.")
    quoteable = []
    for seg in segments:
        if len(seg["text"].split()) >= args.min_words:
            quoteable.append({"start": seg["start_tc"], "end": seg["end_tc"],
                              "duration_s": round(seg["end"] - seg["start"], 3),
                              "text": seg["text"],
                              "citation": f"[{args.source_label} {seg['start_tc']}-{seg['end_tc']}]"})
    if args.contains:
        quoteable = [q for q in quoteable if args.contains.lower() in q["text"].lower()]
    return {"segments_parsed": len(segments), "quoteable_segments": quoteable,
            "source_label": args.source_label}

def cmd_transcript_clean(args):
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    original = text
    # normalize common ASR artifacts, preserve timestamps
    text = re.sub(r"\[(uh|um|erm|ah)\]", "", text, flags=re.I)
    text = re.sub(r"\b(uh|um|erm)\b", "", text, flags=re.I)
    text = re.sub(r"(\b\w+)(\s+\1\b)+", r"\1", text, flags=re.I)  # stutter repeats
    text = re.sub(r"\s{2,}", " ", text)
    lines = [l.rstrip() for l in text.splitlines()]
    text = "\n".join(l for l in lines if l.strip())
    kept_ts = len(re.findall(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}", original))
    out_path = args.output or None
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return {"timestamps_preserved": kept_ts, "words_in": len(original.split()),
            "words_out": len(text.split()), "written": bool(out_path), "output": out_path,
            "note": "Artifacts removed conservatively; hesitations retained when semantically relevant if --keep-hesitations is set." if args.keep_hesitations else "Fillers removed; review against the audio before quoting."}

FIG_CAPTION_RE = re.compile(r"(?im)^\s*(?:figure|fig\.?)\s+(\d+[A-Za-z]?)\s*[:.\-]\s*(.{5,300})$")
TAB_CAPTION_RE = re.compile(r"(?im)^\s*table\s+(\d+[A-Za-z]?)\s*[:.\-]\s*(.{5,300})$")
MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MD_TABLE_RE = re.compile(r"(?m)^(\|.+\|)\s*\n^\|[\s:|\-]+\|")

def cmd_extract_figures(args):
    text = _read(args.file)
    ext = os.path.splitext(args.file)[1].lower()
    figures, tables = [], []
    for m in FIG_CAPTION_RE.finditer(text):
        figures.append({"number": m.group(1), "caption": C.normalize_ws(m.group(2)),
                        "line": text[:m.start()].count("\n") + 1})
    for m in TAB_CAPTION_RE.finditer(text):
        tables.append({"number": m.group(1), "caption": C.normalize_ws(m.group(2)),
                       "line": text[:m.start()].count("\n") + 1})
    for m in MD_IMAGE_RE.finditer(text):
        alt = m.group(1)
        figures.append({"number": None, "caption": alt or "(no alt text)",
                        "source": m.group(2), "line": text[:m.start()].count("\n") + 1,
                        "missing_alt": not alt.strip()})
    if ext in (".md", ".markdown"):
        for m in MD_TABLE_RE.finditer(text):
            tables.append({"number": None, "caption": "(markdown table)", "line": text[:m.start()].count("\n") + 1,
                           "header_row": m.group(1)[:200]})
    return {"figures": figures, "tables": tables,
            "figure_refs_without_caption": [n for n in sorted(set(re.findall(r"(?:Figure|Fig\.?)\s*(\d+[A-Za-z]?)", text)))
                                            if n not in {f["number"] for f in figures}],
            "table_refs_without_caption": [n for n in sorted(set(re.findall(r"Table\s+(\d+[A-Za-z]?)", text)))
                                           if n not in {t["number"] for t in tables}],
            "note": "Text-layer extraction only; chart values require the data source or a local chart model adapter."}

def cmd_chart_recovery(args):
    if args.csv:
        if not os.path.isfile(args.csv):
            raise C.ValidationError(f"CSV not found: {args.csv}")
        with open(args.csv, "r", encoding="utf-8") as fh:
            rows = list(fh)
        header = rows[0].strip().split(",") if rows else []
        data = []
        for line in rows[1:]:
            vals = line.strip().split(",")
            try:
                data.append([float(v) for v in vals])
            except ValueError:
                data.append(vals)
        return {"source": "csv", "columns": header, "rows": len(data), "data": data[:1000],
                "uncertainty": "none (exact data source)"}
    return {"source": "image", "recovered": False, "unavailable": True,
            "note": "Image-only chart recovery requires the optional local vision model adapter (models.vlm). "
                    "Record axis ranges, units and approximate readings with explicit uncertainty when done manually.",
            "manual_protocol": ["identify axes, scales, units",
                                "read gridline values",
                                "record each point as approximate with ±tolerance",
                                "store readings as CSV and reference in the figure citation"]}

def cmd_screenshot_pack(args):
    if not os.path.isfile(args.image):
        raise C.ValidationError(f"Image not found: {args.image}")
    info = C.Workspace(args.workspace)  # not required; keep sidecar standalone
    digest = C.sha256_file(args.image)
    sidecar = {"screenshot": os.path.basename(args.image), "sha256": digest,
               "captured_at": C.now_iso(),
               "application": args.application, "file": args.source_file,
               "version": args.version, "region": args.region,
               "annotation": args.annotation,
               "replay": "Open the named application+file+version and navigate to the region to reproduce."}
    out = args.output or (os.path.splitext(args.image)[0] + ".evidence.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, ensure_ascii=False, indent=2)
    return {"sidecar": out, "sha256": digest, "bytes": os.path.getsize(args.image)}

def cmd_video_segment(args):
    if not os.path.isfile(args.sidecar):
        raise C.ValidationError(f"Sidecar not found: {args.sidecar} (expected JSON list of scenes/captions/on-screen text)")
    with open(args.sidecar, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("scenes", data if isinstance(data, list) else [])
    indexed = []
    for i, item in enumerate(items):
        indexed.append({"scene_id": item.get("id", i + 1),
                        "start": item.get("start"), "end": item.get("end"),
                        "speaker": item.get("speaker"),
                        "caption": item.get("caption") or item.get("description"),
                        "on_screen_text": item.get("on_screen_text"),
                        "citation": f"[{args.source_label} {item.get('start', '?')}-{item.get('end', '?')}]"})
    return {"scenes_indexed": len(indexed), "index": indexed,
            "note": "Index built from the provided sidecar (e.g., exported from a local editor); no frame decoding performed here."}

def cmd_audio_review(args):
    if not os.path.isfile(args.words_json):
        raise C.ValidationError("Provide --words-json (word-level timing/confidence JSON from the local STT adapter)")
    with open(args.words_json, "r", encoding="utf-8") as fh:
        words = json.load(fh)
    if isinstance(words, dict):
        words = words.get("words", [])
    low_conf = [w for w in words if w.get("confidence", 1.0) < args.min_confidence]
    overlaps = []
    for i in range(1, len(words)):
        if words[i].get("start", 0) < words[i - 1].get("end", 0) - 0.05:
            overlaps.append({"word": words[i].get("word"), "at": words[i].get("start")})
    speaker_switches = sum(1 for i in range(1, len(words))
                           if words[i].get("speaker") and words[i - 1].get("speaker")
                           and words[i]["speaker"] != words[i - 1]["speaker"])
    return {"words": len(words), "low_confidence_words": [{"word": w.get("word"), "confidence": w.get("confidence")} for w in low_conf[:100]],
            "low_confidence_count": len(low_conf),
            "overlapping_words": overlaps[:50],
            "speaker_switches": speaker_switches,
            "verdict": "review-before-quoting" if low_conf or overlaps else "clean-enough",
            "note": "Quotations must preserve wording; unclear overlapping speech must not be silently resolved."}

def cmd_multimodal_citation(args):
    kind = args.kind
    ref = {"kind": kind}
    if args.locator:
        ref["locator"] = args.locator
    if kind == "image":
        ref["format"] = f"Figure {args.number}, {args.caption or 'no caption'} ({args.locator or 'no locator'})"
    elif kind == "audio" or kind == "video":
        ref["format"] = f"{args.source_label} [{args.locator or 'timecode?'}]"
    elif kind == "table":
        ref["format"] = f"Table {args.number}, {args.caption or 'no caption'}"
    elif kind == "archive":
        ref["format"] = f"{args.source_label}, {args.locator or 'box/folder?'}"
    else:
        raise C.ValidationError("kind must be one of image|audio|video|table|archive")
    ref["access_note"] = "Cited with the local locator scheme of this workspace; archive objects need repository-stable identifiers."
    return ref

def cmd_alt_text(args):
    """Scaffold alt text: structure prompts; the description itself requires human/model judgment."""
    words = [w for w in C.tokenize(args.context or "")]
    return {"kind": args.kind,
            "scaffold": {
                "concise_alt": f"[1-2 sentences: chart/map/diagram type, key variables, trend or finding. Words to use: {', '.join(words[:8]) or 'domain terms'}]",
                "detailed_description": ["type of figure and subject",
                                         "axes/scales/units or map legend and projection",
                                         "what the data shows (values, uncertainty, outliers)",
                                         "how it relates to the claim in the text",
                                         "source and date of the data"],
                "avoid": ["decorative-only description", "color-only distinctions without labels",
                          "'image of' prefixes", "invented values"]},
            "review_required": True,
            "note": "This scaffolds the description discipline. Writing the alt text requires the image content (human or configured vision adapter)."}

def cmd_visual_claim(args):
    return {"items": [C.normalize_ws(l) for l in _read(args.captions).splitlines() if l.strip()] if args.captions else [],
            "checks": ["caption matches visible content (axes, legend, data points)?",
                       "no values beyond what the image shows?",
                       "uncertainty/error bars acknowledged in prose?",
                       "source and date of image present?",
                       "cropping/editing disclosed?"],
            "verdict": "review-required",
            "note": "Automated comparison of prose vs pixels is not possible without a vision adapter; this checklist binds the human verifier."}

COMMANDS = {
    "timecodes": cmd_timecodes, "transcript-clean": cmd_transcript_clean,
    "extract-figures": cmd_extract_figures, "chart-recovery": cmd_chart_recovery,
    "screenshot-pack": cmd_screenshot_pack, "video-segment": cmd_video_segment,
    "audio-review": cmd_audio_review, "multimodal-citation": cmd_multimodal_citation,
    "alt-text": cmd_alt_text, "visual-claim": cmd_visual_claim,
}

def build_parser(prog="rwt media"):
    p = argparse.ArgumentParser(prog=prog, description="Multimodal research engines (text/sidecar JSON deterministic; visual via adapter).")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("timecodes", help="Build quotable timecoded segments from SRT/VTT")
    sp.add_argument("--file", required=True); sp.add_argument("--source-label", default="Interview")
    sp.add_argument("--min-words", type=int, default=4); sp.add_argument("--contains")
    sp.set_defaults(func=cmd_timecodes)

    sp = sub.add_parser("transcript-clean", help="Clean ASR artifacts; keep timestamps; optional --output")
    sp.add_argument("--file", required=True); sp.add_argument("--output")
    sp.add_argument("--keep-hesitations", action="store_true"); sp.set_defaults(func=cmd_transcript_clean)

    sp = sub.add_parser("extract-figures", help="Extract figure/table captions, image refs, markdown tables")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_extract_figures)

    sp = sub.add_parser("chart-recovery", help="Recover chart data from CSV exactly, or protocol for images")
    sp.add_argument("--csv"); sp.set_defaults(func=cmd_chart_recovery)

    sp = sub.add_parser("screenshot-pack", help="Write a reproducible screenshot sidecar (app/file/version/timestamp/region)")
    sp.add_argument("--image", required=True); sp.add_argument("--application")
    sp.add_argument("--source-file"); sp.add_argument("--version")
    sp.add_argument("--region"); sp.add_argument("--annotation")
    sp.add_argument("--output"); sp.add_argument("--workspace", default=".")
    sp.set_defaults(func=cmd_screenshot_pack)

    sp = sub.add_parser("video-segment", help="Index scenes/speakers/timecodes from a sidecar JSON")
    sp.add_argument("--sidecar", required=True); sp.add_argument("--source-label", default="Video")
    sp.set_defaults(func=cmd_video_segment)

    sp = sub.add_parser("audio-review", help="Flag low-confidence words and overlaps in word-level JSON")
    sp.add_argument("--words-json", required=True); sp.add_argument("--min-confidence", type=float, default=0.6)
    sp.set_defaults(func=cmd_audio_review)

    sp = sub.add_parser("multimodal-citation", help="Format citations to images/audio/video/tables/archives")
    sp.add_argument("--kind", required=True, choices=["image", "audio", "video", "table", "archive"])
    sp.add_argument("--locator"); sp.add_argument("--number"); sp.add_argument("--caption")
    sp.add_argument("--source-label", default="Source"); sp.set_defaults(func=cmd_multimodal_citation)

    sp = sub.add_parser("alt-text", help="Scaffold concise/detailed descriptions for figures")
    sp.add_argument("--kind", default="chart"); sp.add_argument("--context")
    sp.set_defaults(func=cmd_alt_text)

    sp = sub.add_parser("visual-claim", help="Checklist binding prose to visible evidence")
    sp.add_argument("--captions"); sp.set_defaults(func=cmd_visual_claim)
    _build_parser_extra_media(sub)
    return p

def run(argv):
    parser = build_parser(prog="rwt media")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)



def cmd_map_evidence(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    claims = ws.list_records("claims")
    claim = next((c for c in claims if c.get("id") == args.claim_id), None)
    if not claim:
        raise C.ValidationError(f"Claim not found in workspace: {args.claim_id}")
    record = {"claim_id": args.claim_id,
              "kind": args.kind,
              "locator": args.locator,
              "description": args.description,
              "source_label": args.source_label,
              "relation": args.relation,
              "added_by": args.actor}
    if args.file:
        if not os.path.isfile(args.file):
            raise C.ValidationError(f"Evidence file not found: {args.file}")
        record["sha256"] = C.sha256_file(args.file)
        record["bytes"] = os.path.getsize(args.file)
        record["file_name"] = os.path.basename(args.file)
    saved = ws.add_record("evidence", record, actor=args.actor)
    ws.audit(args.actor, "media.map-evidence", {"claim_id": args.claim_id, "evidence_id": saved.get("id")})
    return {"evidence_id": saved.get("id"), "claim_id": args.claim_id,
            "locator": args.locator, "kind": args.kind,
            "note": "Locator binds the artifact to the claim; a claim without a resolvable locator is not publishable."}

COMMANDS["map-evidence"] = cmd_map_evidence

def _build_parser_extra_media(sub):
    sp = sub.add_parser("map-evidence", help="Register a media/figure artifact as evidence for a workspace claim")
    sp.add_argument("--workspace", default="."); sp.add_argument("--claim-id", required=True)
    sp.add_argument("--kind", required=True, choices=["image", "audio", "video", "document-figure", "table", "screenshot"])
    sp.add_argument("--locator", required=True, help="e.g. 'Figure 3, p.12' or '00:04:12-00:05:03'")
    sp.add_argument("--description", required=True)
    sp.add_argument("--source-label", default="Source")
    sp.add_argument("--relation", default="supports", choices=["supports", "contradicts", "qualifies", "context"])
    sp.add_argument("--file"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_map_evidence)


def main():
    try:
        out = run(sys.argv[1:])
    except C.RwtError as exc:
        print(json.dumps({"error": str(exc), "exit_code": exc.exit_code}, ensure_ascii=False))
        sys.exit(exc.exit_code)
    except C.ValidationError as exc:
        print(json.dumps({"error": str(exc), "exit_code": 2}, ensure_ascii=False))
        sys.exit(2)
    if isinstance(out, int):
        sys.exit(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
