#!/usr/bin/env python3
"""tools-rwt-locale.py - Language-specific accuracy and localization QA engines.

Capabilities: locale variant control (spelling/date/number presets), RTL and
mixed-script structural testing, CJK typography checks, complex-script font
coverage testing (parses the sfnt cmap of .ttf/.otf fonts with stdlib only),
locale number/date/currency review, register/politeness markers, inclusive
language flags, code-switching detection, and a Filipino/Philippine-English
review pass. No rendering is performed; checks are structural.
"""
import argparse
import json
import os
import re
import struct
import sys
import unicodedata

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_l", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_l"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _read(file):
    if not os.path.isfile(file):
        raise C.ValidationError(f"File not found: {file}")
    with open(file, "r", encoding="utf-8") as fh:
        return fh.read()

LOCALE_PRESETS = {
    "en-US": {"decimal": ".", "thousands": ",", "date_order": "MDY", "currency": "USD", "spelling": "US"},
    "en-GB": {"decimal": ".", "thousands": ",", "date_order": "DMY", "currency": "GBP", "spelling": "UK"},
    "en-PH": {"decimal": ".", "thousands": ",", "date_order": "MDY", "currency": "PHP", "spelling": "PH"},
    "fil":   {"decimal": ".", "thousands": ",", "date_order": "MDY", "currency": "PHP", "spelling": "PH"},
    "es-ES": {"decimal": ",", "thousands": ".", "date_order": "DMY", "currency": "EUR", "spelling": "ES"},
    "es-PH": {"decimal": ".", "thousands": ",", "date_order": "MDY", "currency": "PHP", "spelling": "PH"},
    "fr-FR": {"decimal": ",", "thousands": " ", "date_order": "DMY", "currency": "EUR", "spelling": "FR"},
    "de-DE": {"decimal": ",", "thousands": ".", "date_order": "DMY", "currency": "EUR", "spelling": "DE"},
    "ja-JP": {"decimal": ".", "thousands": ",", "date_order": "YMD", "currency": "JPY", "spelling": "JP"},
    "zh-CN": {"decimal": ".", "thousands": ",", "date_order": "YMD", "currency": "CNY", "spelling": "CN"},
    "ar":    {"decimal": ".", "thousands": ",", "date_order": "YMD", "currency": "SAR", "spelling": "AR"},
}
EN_US_UK = {
    "color": "colour", "honor": "honour", "organize": "organise", "analyze": "analyse",
    "center": "centre", "defense": "defence", "license": "licence", "practice": "practise",
    "program": "programme", "traveled": "travelled", "canceled": "cancelled",
    "dialog": "dialogue", "catalog": "catalogue", "modeling": "modelling",
}

def cmd_variant(args):
    text = _read(args.file)
    locale = args.locale
    preset = LOCALE_PRESETS.get(locale)
    if not preset:
        raise C.ValidationError(f"Unknown locale '{locale}'. Known: {sorted(LOCALE_PRESETS)}")
    issues = []
    if preset["spelling"] == "UK":
        for us, uk in EN_US_UK.items():
            if re.search(r"\b" + us + r"\b", text, re.I):
                issues.append({"type": "spelling-variant", "found": us, "expected_uk": uk})
    elif preset["spelling"] == "US":
        for us, uk in EN_US_UK.items():
            if re.search(r"\b" + uk + r"\b", text, re.I):
                issues.append({"type": "spelling-variant", "found": uk, "expected_us": us})
    return {"locale": locale, "preset": preset, "issues": issues,
            "date_note": f"Expected date order {preset['date_order']} for {locale}; ambiguous numeric dates flagged for review.",
            "note": "Variant checks cover common pairs only; a human editor confirms house style."}

def cmd_number_date(args):
    text = _read(args.file)
    preset = LOCALE_PRESETS.get(args.locale)
    if not preset:
        raise C.ValidationError(f"Unknown locale '{args.locale}'. Known: {sorted(LOCALE_PRESETS)}")
    issues = []
    # thousands separator mismatch
    if preset["thousands"] == ",":
        for m in re.finditer(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b", text):
            issues.append({"type": "possible-dot-thousands", "text": m.group(0),
                           "note": f"{args.locale} uses ',' as thousands separator"})
    else:
        for m in re.finditer(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b", text):
            issues.append({"type": "possible-comma-thousands", "text": m.group(0),
                           "note": f"{args.locale} uses '{preset['thousands']}' as thousands separator"})
    # decimal separator sanity: find digit-sep-digit patterns with the "wrong" separator
    dec, thr = preset["decimal"], preset["thousands"]
    for m in re.finditer(r"\d" + re.escape(thr if thr != " " else " ") + r"\d{3}", text):
        if thr != ",":
            issues.append({"type": "thousands-separator", "text": m.group(0)})
    # ambiguous dates like 03/04/2026
    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= 12 and b <= 12:
            issues.append({"type": "ambiguous-date", "text": m.group(0),
                           "note": f"Could be {preset['date_order']} order; write ISO YYYY-MM-DD or month name"})
    currencies = re.findall(r"\b(USD|PHP|EUR|GBP|JPY|CNY|SAR|₱|\$|€|£|¥)\s?\d|\d\s?(USD|PHP|EUR|GBP|JPY)\b", text)
    iso = re.findall(r"\b(USD|PHP|EUR|GBP|JPY|CNY|SAR)\b", text)
    return {"locale": args.locale, "issues": issues[:60],
            "currency_mentions": len(currencies), "iso_currency_codes": sorted(set(iso)),
            "missing_iso_currency": bool(currencies) and not iso,
            "recommendation": "Use ISO 8601 dates and ISO 4217 currency codes in research text."}

RTL_RANGES = re.compile(r"[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\uFB1D-\uFDFF\uFE70-\uFEFF]")
LTR_RANGES = re.compile(r"[A-Za-z\u00C0-\u024F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
BIDI_CONTROLS = re.compile(r"[\u202A-\u202E\u2066-\u2069\u200F\u200E]")

def cmd_rtl(args):
    text = _read(args.file)
    rtl_chars = RTL_RANGES.findall(text)
    ltr_chars = LTR_RANGES.findall(text)
    bidi = BIDI_CONTROLS.findall(text)
    mixed_lines = []
    for i, line in enumerate(text.splitlines(), start=1):
        if RTL_RANGES.search(line) and LTR_RANGES.search(line):
            digits = re.findall(r"[0-9\u0660-\u0669\u06F0-\u06F9]+", line)
            mixed_lines.append({"line": i, "digits": digits, "text": line[:160]})
    arabic_indic = re.findall(r"[\u0660-\u0669\u06F0-\u06F9]", text)
    ascii_digits = re.findall(r"[0-9]", text)
    return {"rtl_characters": len(rtl_chars), "ltr_characters": len(ltr_chars),
            "bidi_control_characters": len(bidi),
            "mixed_direction_lines": mixed_lines[:40],
            "arabic_indic_digits": len(arabic_indic), "ascii_digits": len(ascii_digits),
            "digit_style_consistent": not (arabic_indic and ascii_digits),
            "note": "Structural check only. Rendering, mirroring and export behavior must be verified in the target viewer/editor."}

def cmd_cjk(args):
    text = _read(args.file)
    cjk = bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text))
    fullwidth = sorted(set(re.findall(r"[\uFF01-\uFF5E]", text)))
    cjk_punct = sorted(set(re.findall(r"[\u3000-\u303F]", text)))
    ascii_punct_in_cjk = []
    if cjk:
        for ch in re.findall(r"[,.;:!?()]", text):
            if ch in ",.;:!?()":
                ascii_punct_in_cjk.append(ch)
    ruby = re.findall(r"<ruby>.*?</ruby>", text, re.S | re.I)
    vertical = bool(re.search(r"(writing-mode:\s*vertical|text-orientation|vertical-text)", text, re.I))
    return {"cjk_present": cjk, "fullwidth_forms": fullwidth[:20],
            "cjk_punctuation": cjk_punct[:20],
            "ascii_punctuation_occurrences_in_cjk_text": len(ascii_punct_in_cjk) if cjk else 0,
            "ruby_elements": len(ruby), "vertical_text_indicators": vertical,
            "note": "Check line breaking and font embedding in the rendered file; mixed half/fullwidth punctuation is a common defect."}

# ---- sfnt cmap parsing (format 4 and 12) ----
def _cmap_subtables(font_bytes):
    if len(font_bytes) < 12:
        raise C.ValidationError("File too small to be an sfnt font")
    sfnt_version, num_tables = struct.unpack_from(">HH", font_bytes, 0)
    if num_tables > 100:
        raise C.ValidationError("Implausible table count; not an sfnt font?")
    tables = {}
    for i in range(num_tables):
        off = 12 + 16 * i
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", font_bytes, off)
        tables[tag.decode("latin-1")] = (offset, length)
    if "cmap" not in tables:
        raise C.ValidationError("No cmap table found (not a TrueType/OpenType font?)")
    cmap_off, _cmap_len = tables["cmap"]
    version, n = struct.unpack_from(">HH", font_bytes, cmap_off)
    subtables = []
    for i in range(n):
        rec = cmap_off + 4 + 8 * i
        platform_id, encoding_id, sub_offset = struct.unpack_from(">HHI", font_bytes, rec)
        subtables.append((platform_id, encoding_id, cmap_off + sub_offset))
    return subtables

def _cmap_format4_covers(font_bytes, base):
    (length,) = struct.unpack_from(">H", font_bytes, base + 2)
    seg_x2, = struct.unpack_from(">H", font_bytes, base + 6)
    seg_count = seg_x2 // 2
    end_codes = base + 14
    start_codes = end_codes + seg_x2 + 2
    id_deltas = start_codes + seg_x2
    id_range_offsets = id_deltas + seg_x2
    covered = set()
    for i in range(seg_count):
        end = struct.unpack_from(">H", font_bytes, end_codes + 2 * i)[0]
        start = struct.unpack_from(">H", font_bytes, start_codes + 2 * i)[0]
        if start == 0xFFFF and end == 0xFFFF:
            continue
        for cp in range(start, min(end, start + 300) + 1):  # cap range expansion for pathological fonts
            covered.add(cp)
        if end - start > 300:
            covered.add(("range-cap-skipped", start, end))
    return covered

def _cmap_format12_covers(font_bytes, base):
    length, n_groups = struct.unpack_from(">IxxxxI", font_bytes, base + 4)[0], None
    # layout: format(2) reserved(2) length(4) language(4) nGroups(4)
    n_groups, = struct.unpack_from(">I", font_bytes, base + 12)
    covered = set()
    pos = base + 16
    for _ in range(min(n_groups, 10000)):
        start_cp, end_cp, _start_glyph = struct.unpack_from(">III", font_bytes, pos)
        pos += 12
        if end_cp - start_cp > 1000:
            covered.add(("range-cap-skipped", start_cp, end_cp))
            continue
        for cp in range(start_cp, min(end_cp, 0x10FFFF) + 1):
            covered.add(cp)
    return covered

def font_cmap_codepoints(path):
    with open(path, "rb") as fh:
        font_bytes = fh.read()
    subtables = _cmap_subtables(font_bytes)
    covered = set()
    formats_seen = []
    for platform_id, encoding_id, base in subtables:
        (fmt,) = struct.unpack_from(">H", font_bytes, base)
        formats_seen.append(fmt)
        if fmt == 4:
            covered |= _cmap_format4_covers(font_bytes, base)
        elif fmt == 12:
            covered |= _cmap_format12_covers(font_bytes, base)
    capped = sorted(x for x in covered if isinstance(x, tuple))
    cps = {x for x in covered if isinstance(x, int)}
    return cps, formats_seen, capped

def cmd_font_coverage(args):
    if not os.path.isfile(args.font):
        raise C.ValidationError(f"Font not found: {args.font}")
    cps, formats_seen, capped = font_cmap_codepoints(args.font)
    sample_texts = []
    if args.text_file:
        sample_texts = [_read(args.text_file)]
    elif args.text:
        sample_texts = [args.text]
    missing = {}
    total = 0
    for text in sample_texts:
        for ch in text:
            cp = ord(ch)
            if ch.isspace() or cp < 0x20:
                continue
            total += 1
            if cp not in cps:
                missing[cp] = missing.get(cp, 0) + 1
    report = {"font": os.path.basename(args.font), "cmap_formats": formats_seen,
              "codepoints_covered": len(cps), "ranges_capped": capped[:10],
              "sample_codepoints_tested": total,
              "missing_codepoints": [{"codepoint": f"U+{cp:04X}", "char": chr(cp),
                                      "occurrences": n} for cp, n in sorted(missing.items(), key=lambda kv: -kv[1])[:50]],
              "coverage_ok": not missing}
    if args.expect_ranges:  # e.g. "0590-05FF,0600-06FF" for Hebrew+Arabic
        missing_ranges = []
        for part in args.expect_ranges.split(","):
            part = part.strip()
            if not part:
                continue
            lo_s, hi_s = part.split("-")
            lo, hi = int(lo_s, 16), int(hi_s, 16)
            present = sum(1 for cp in range(lo, hi + 1) if cp in cps)
            missing_ranges.append({"range": f"U+{lo:04X}-U+{hi:04X}", "covered": present, "total": hi - lo + 1})
        report["expected_ranges"] = missing_ranges
    return report

INCLUSIVE_FLAGS = [
    (r"\b(chairman|chairwoman)\b", "chairperson / chair"),
    (r"\b(mankind)\b", "humankind / humanity"),
    (r"\b(man-made)\b", "synthetic / human-made"),
    (r"\b(he|she)\b(?=[^.]{0,60}\b(?:everyone|someone|anybody|nobody)\b)", "they (singular)"),
    (r"\bcrazy\b", "wild / intense / confusing"),
    (r"\binsane\b", "extreme / remarkable"),
    (r"\b(blind spot|tone deaf)\b", "gap / unaware"),
    (r"\b(lame)\b", "weak / unconvincing"),
    (r"\b(grandfathered)\b", "legacy / pre-existing"),
    (r"\b(tribe of|savage|primitive)\b", "community / early / prehistoric context-dependent"),
    (r"\b(maiden name)\b", "family name / birth name"),
    (r"\b(crippled)\b", "limited / disabled"),
]
REGISTER_MARKERS = {
    "formal-en": {"contractions_forbidden": True, "markers": [r"\b(I'm|you're|don't|can't|won't|isn't)\b"]},
    "formal-es": {"markers": [r"\btú\b", r"\bvosotros\b"], "prefer": ["usted", "ustedes"]},
    "formal-fil": {"markers": [r"\bikaw\b", r"\bsiya\b(?!.*\bpo\b)"], "prefer": ["po", "opo", "kayo", "sila"]},
    "informal-fil": {"markers": [r"\bpo\b", r"\bopo\b"], "prefer": ["ka", "siya"]},
}

def cmd_register(args):
    text = _read(args.file)
    out = {"file": args.file, "checks": []}
    profile = REGISTER_MARKERS.get(args.register)
    if not profile:
        raise C.ValidationError(f"Unknown register '{args.register}'. Known: {sorted(REGISTER_MARKERS)}")
    hits = []
    for pattern in profile.get("markers", []):
        for m in re.finditer(pattern, text, re.I):
            hits.append({"pattern": pattern, "found": m.group(0), "offset": m.start()})
    out["register"] = args.register
    out["markers_found"] = hits
    out["preferred_forms"] = profile.get("prefer", [])
    informal_en = len(re.findall(r"\b(yeah|gonna|wanna|kinda|sorta|cool|awesome)\b", text, re.I))
    out["informal_en_markers"] = informal_en
    return out

def cmd_inclusive(args):
    text = _read(args.file)
    hits = []
    for pattern, suggestion in INCLUSIVE_FLAGS:
        for m in re.finditer(pattern, text, re.I):
            hits.append({"found": m.group(0), "suggestion": suggestion, "offset": m.start()})
    return {"flags": hits, "count": len(hits),
            "note": "Flags common exclusive terms; community preference lists for the target locale take precedence."}

def cmd_codeswitch(args):
    text = _read(args.file)
    try:
        tr = core().load_sibling if False else None  # not needed; replicate tiny detector
    except Exception:
        pass
    # use translation sibling for language detection per line
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rwt_tr_l", os.path.join(this, "tools-rwt-translation.py"))
    trmod = importlib.util.module_from_spec(spec)
    sys.modules["rwt_tr_l"] = trmod
    spec.loader.exec_module(trmod)
    spans = []
    switches = 0
    prev = None
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        d = trmod.detect_language(line)
        if prev and d["language"] != prev and d["language"] != "und" and prev != "und":
            switches += 1
        prev = d["language"]
        spans.append({"line": i, "language": d["language"], "confidence": d["confidence"], "text": line[:100]})
    return {"spans": spans, "switches": switches,
            "note": "Code-switching between Filipino and English is normal in Philippine discourse; check that borrowed terms are consistent and comprehensible for the audience."}

def cmd_filipino(args):
    text = _read(args.file)
    checks = []
    # particle usage in formal Filipino
    po = len(re.findall(r"\bpo\b", text, re.I))
    opo = len(re.findall(r"\bopo\b", text, re.I))
    checks.append({"check": "politeness_particles_po_opo", "po": po, "opo": opo,
                   "note": "Formal/public-address Filipino typically requires 'po/opo'; their absence is a register flag, not an error."})
    # common phE markers
    phe = re.findall(r"\b(comfort room|CR|ref|condo|barangay|tricycle|jeepney|OFW|POGO|sari-sari)\b", text, re.I)
    checks.append({"check": "philippine_english_terms", "found": sorted(set(x.lower() for x in phe))})
    # tagalog function words for untranslated spans
    tl_hits = len(re.findall(r"\b(ang|nang|mga|yung|kaya|kasi|naman|talaga|ba)\b", text, re.I))
    en_hits = len(re.findall(r"\b(the|and|of|to|in|is|are|was)\b", text, re.I))
    mix = min(tl_hits, en_hits)
    checks.append({"check": "code_mix_estimate", "tagalog_function_words": tl_hits,
                   "english_function_words": en_hits, "mixed": mix > 3})
    # diacritics
    checks.append({"check": "spanish_derived_diacritics",
                   "found": sorted(set(re.findall(r"[áéíóúñÁÉÍÓÚÑ]", text)))})
    return {"checks": checks,
            "recommendation": "Route flagged register/terminology issues to a Filipino language reviewer; Philippine English and Filipino are both legitimate target standards depending on the audience."}

COMMANDS = {
    "variant": cmd_variant, "number-date": cmd_number_date, "rtl": cmd_rtl, "cjk": cmd_cjk,
    "font-coverage": cmd_font_coverage, "register": cmd_register, "inclusive": cmd_inclusive,
    "codeswitch": cmd_codeswitch, "filipino": cmd_filipino,
}

def build_parser(prog="rwt locale"):
    p = argparse.ArgumentParser(prog=prog, description="Localization QA: locale variants, RTL/CJK/fonts, register, inclusive language.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("variant", help="Locale spelling-variant checks (en-US/en-GB/en-PH families)")
    sp.add_argument("--file", required=True); sp.add_argument("--locale", default="en-PH")
    sp.set_defaults(func=cmd_variant)

    sp = sub.add_parser("number-date", help="Number/date/currency separator and ambiguity checks by locale")
    sp.add_argument("--file", required=True); sp.add_argument("--locale", default="en-PH")
    sp.set_defaults(func=cmd_number_date)

    sp = sub.add_parser("rtl", help="Structural RTL checks: ranges, bidi controls, mixed-direction lines, digit style")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_rtl)

    sp = sub.add_parser("cjk", help="CJK checks: punctuation width mixing, ruby elements, vertical text indicators")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_cjk)

    sp = sub.add_parser("font-coverage", help="Parse an sfnt font's cmap; report coverage of a sample text or ranges")
    sp.add_argument("--font", required=True, help="Path to .ttf/.otf")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--expect-ranges", default=None, help="Comma-separated hex ranges, e.g. 0590-05FF,0600-06FF")
    sp.set_defaults(func=cmd_font_coverage)

    sp = sub.add_parser("register", help="Register/politeness marker checks for a declared register")
    sp.add_argument("--file", required=True)
    sp.add_argument("--register", default="formal-en",
                    choices=sorted(REGISTER_MARKERS))
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("inclusive", help="Flag common non-inclusive terms with alternatives")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_inclusive)

    sp = sub.add_parser("codeswitch", help="Detect per-line language and count code switches")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_codeswitch)

    sp = sub.add_parser("filipino", help="Filipino/Philippine-English review pass (po/opo, PhE terms, code-mix, diacritics)")
    sp.add_argument("--file", required=True); sp.set_defaults(func=cmd_filipino)
    return p

def run(argv):
    parser = build_parser(prog="rwt locale")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


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
