"""Localization engineering, language assets, Filipino/English review, bidi,
fonts/typography and pseudolocalization engines.

Categories served: 13 (Localization Architecture and i18n Engineering),
14 (Translation Memory, Terminology and Language Assets), 15 (Translation,
Transcreation and Human Review), 16 (Filipino, English and Philippine Locale),
17 (RTL, Bidirectional and Complex Scripts), 18 (Fonts, Typography, Glyph
Coverage), 19 (Pseudolocalization and Localization Testing).
"""

import io
import json
import os
import re
import struct
import unicodedata
import zipfile

S = None
T = None
H = None

OPS = {}


def init(deps):
    global S, T, H
    S = deps["support"]
    T = deps["text"]
    H = deps["html"]


def op(name, doc, params=None, accepts=("strings",), engine="l10n"):
    def deco(fn):
        OPS[name] = {"name": name, "doc": doc, "params": params or {}, "accepts": list(accepts),
                     "summary": doc.split(".")[0], "fn": fn, "engine": engine, "groups": []}
        return fn
    return deco


# --------------------------------------------------------------------------- #
# String resource loading
# --------------------------------------------------------------------------- #
def load_strings(path, text):
    """Parse JSON, .properties, .po or a flat key=value list into a flat dict."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise S.InputError("Invalid JSON resource file: %s" % exc,
                              hint="Fix the JSON syntax; line %s column %s." % (exc.lineno, exc.colno))
        return _flatten(data), "json"
    if ext in (".properties", ".props", ".ini", ".env"):
        out = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "!", ";")):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
            elif ":" in line:
                k, v = line.split(":", 1)
            else:
                continue
            out[k.strip()] = v.strip()
        return out, "properties"
    if ext == ".po":
        return _parse_po(text), "po"
    raise S.InputError("Unsupported string resource type %r." % ext,
                       hint="Supported: .json, .properties, .ini, .po, or pass a filtered JSON export.")


def _flatten(data, prefix=""):
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = ("%s.%s" % (prefix, k)) if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.update(_flatten(v, key))
            elif isinstance(v, str):
                out[key] = v
    elif isinstance(data, list):
        for i, v in enumerate(data):
            key = ("%s[%d]" % (prefix, i)) if prefix else "[%d]" % i
            if isinstance(v, (dict, list)):
                out.update(_flatten(v, key))
            elif isinstance(v, str):
                out[key] = v
    return out


def _parse_po(text):
    out = {}
    msgid = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            msgid = line[6:].strip().strip('"')
        elif line.startswith("msgstr ") and msgid is not None:
            out[msgid] = line[7:].strip().strip('"')
            msgid = None
    return out


def _strings(ctx):
    doc = ctx.docs[0]
    strings, kind = load_strings(doc.path, doc.text)
    if not strings:
        raise S.InputError("No strings parsed from %s." % doc.path,
                          hint="Check the file contains key/value resources in a supported format.")
    return strings, kind


# --------------------------------------------------------------------------- #
# Category 13: i18n architecture
# --------------------------------------------------------------------------- #
@op("i18n-audit", "Audit a string resource bundle: ICU syntax, placeholder integrity across locales, "
    "untranslated values, empty strings, duplicate values, key naming and locale coverage.",
    params={"base_path": {"type": "str", "default": None,
                          "doc": "Second resource file (usually the source locale) used for comparison."},
            "min_coverage_pct": {"type": "float", "default": 95.0,
                                 "doc": "Minimum translated coverage required."},
            "key_pattern": {"type": "str", "default": r"^[A-Za-z0-9_.\-\[\]]+$",
                            "doc": "Regex every key must satisfy."}},
    accepts=("strings",))
def run_i18n_audit(ctx):
    strings, kind = _strings(ctx)
    key_re = re.compile(ctx.params.get("key_pattern") or r"^[A-Za-z0-9_.\-\[\]]+$")
    empty = untranslated = 0
    values = {}
    for key in sorted(strings):
        ctx.checkpoint()
        value = strings[key]
        if not key_re.match(key):
            ctx.add(rule="i18n-key-format", message="Key %r does not match %r." % (key, key_re.pattern),
                    severity="low", criterion="locale-architecture", locator=key,
                    remediation="Rename the key to the documented convention.",
                    retest="Re-run and confirm all keys match the pattern.")
        else:
            ctx.pass_check()
        if not value.strip():
            empty += 1
            ctx.add(rule="i18n-empty-value", message="Key %r has an empty value." % key, severity="high",
                    criterion="locale-architecture", locator=key,
                    remediation="Translate the value or remove the unused key.",
                    retest="Re-run and confirm the key is non-empty or absent.")
            continue
        if value == key or (re.match(r"^[a-z0-9_.\-]+$", value) and value.replace(".", "_") == key.replace(".", "_")):
            untranslated += 1
            ctx.add(rule="i18n-untranslated", message="Key %r still contains the source key as its value." % key,
                    severity="high", criterion="locale-architecture", locator=key,
                    remediation="Translate the value for the target locale.",
                    retest="Re-run and confirm the value differs from the key.")
        values.setdefault(value, []).append(key)
        try:
            T.parse_icu(value)
            ctx.pass_check()
        except T.ICUError as exc:
            ctx.add(rule="i18n-icu-syntax", message="ICUI syntax error in %r: %s" % (key, exc),
                    severity="critical", criterion="locale-architecture", locator=key,
                    evidence={"value": value},
                    remediation="Fix the message syntax; run icu-validate for a focused report.",
                    retest="Re-run and confirm the message parses.")
        for other in (ctx.params.get("base_path"),):
            if not other:
                continue
            base_strings, _ = load_strings(other, S.read_text(other, limits=ctx.limits))
            if key in base_strings:
                missing, extra = _placeholder_diff(base_strings[key], value)
                if missing or extra:
                    ctx.add(rule="i18n-placeholder-drift",
                            message="Placeholders drift for %r (missing %s, extra %s)." % (
                                key, missing or "-", extra or "-"),
                            severity="critical", criterion="locale-architecture", locator=key,
                            evidence={"source": base_strings[key], "target": value},
                            remediation="Restore the exact placeholder set from the source string.",
                            retest="Re-run and confirm no placeholder drift remains.")
                else:
                    ctx.pass_check()
    for value, keys in values.items():
        if len(keys) > 3:
            ctx.add(rule="i18n-duplicate-values", message="%d keys share the same value (%s)." % (len(keys), value[:40]),
                    severity="info", criterion="locale-architecture",
                    evidence={"keys": keys[:10]},
                    remediation="Confirm the duplication is intentional; otherwise split the keys.",
                    retest="Re-run and confirm duplicates are resolved or accepted.")
        else:
            ctx.pass_check()
    coverage = round(100.0 * (len(strings) - empty - untranslated) / max(len(strings), 1), 2)
    min_cov = float(ctx.params.get("min_coverage_pct", 95.0))
    if coverage < min_cov:
        ctx.add(rule="i18n-coverage", message="Translated coverage %.2f%% is below the %.2f%% target." % (
            coverage, min_cov), severity="high", criterion="locale-architecture",
            evidence={"keys": len(strings), "empty": empty, "untranslated": untranslated},
            remediation="Complete the missing translations before release.",
            retest="Re-run and confirm coverage meets the target.")
    else:
        ctx.pass_check()
    ctx.meta("resource_kind", kind)
    ctx.meta("keys", len(strings))
    ctx.meta("empty", empty)
    ctx.meta("untranslated", untranslated)
    ctx.meta("coverage_pct", coverage)


def _placeholder_diff(source, target):
    a = T.placeholder_set(source)
    b = T.placeholder_set(target)
    return sorted(a - b), sorted(b - a)


@op("icu-validate", "Validate ICU MessageFormat syntax: balanced braces, argument grammar, plural/select "
    "options, mandatory 'other' branches and orphaned arguments.",
    params={"declared_args": {"type": "list", "default": None,
                              "doc": "Arguments the runtime passes; flags references to unknown arguments."}},
    accepts=("strings",))
def run_icu(ctx):
    strings, _ = _strings(ctx)
    declared = ctx.params.get("declared_args")
    if isinstance(declared, str):
        declared = [a.strip() for a in declared.split(",") if a.strip()]
    declared = set(declared or [])
    ok = 0
    for key, value in sorted(strings.items()):
        ctx.checkpoint()
        if "{" not in value and "}" not in value:
            ctx.pass_check()
            continue
        try:
            args = T.parse_icu(value)
        except T.ICUError as exc:
            ctx.add(rule="icu-syntax", message="%s: %s" % (key, exc), severity="critical",
                    criterion="locale-architecture", locator=key, evidence={"value": value},
                    remediation="Correct the message syntax; nested options need a closing brace per branch.",
                    retest="Re-run icu-validate and confirm the message parses.")
            continue
        ok += 1
        if declared:
            used = T.icu_argument_names(value)
            unknown = sorted(used - declared)
            if unknown:
                ctx.add(rule="icu-unknown-argument", message="%s references undeclared arguments %s." % (key, unknown),
                        severity="high", criterion="locale-architecture", locator=key,
                        evidence={"used": sorted(used), "declared": sorted(declared)},
                        remediation="Declare the argument in the runtime contract or fix the message.",
                        retest="Re-run and confirm no undeclared arguments remain.")
            else:
                ctx.pass_check()
        for a in _walk_args(args):
            if a["type"] in ("plural", "select", "selectordinal") and "other" not in (a.get("options") or {}):
                ctx.add(rule="icu-missing-other", message="%s: %s argument %r has no 'other' branch." % (
                    key, a["type"], a["name"]), severity="high", criterion="locale-architecture", locator=key,
                    remediation="Add an 'other' branch; it is required by ICU.",
                    retest="Re-run and confirm no missing 'other' branch.")
            else:
                ctx.pass_check()
        ctx.meta("messages_with_arguments", ok)
    ctx.meta("messages", len(strings))


def _walk_args(args):
    for a in args:
        yield a
        for sub in (a.get("options") or {}).values():
            for child in _walk_args(sub):
                yield child


@op("hardcoded-scan", "Detect user-facing strings hard-coded in markup or code instead of externalised "
    "resources, and strings that are concatenated rather than parameterised.",
    params={"min_length": {"type": "int", "default": 4, "doc": "Shortest text run reported."},
            "ignore_pattern": {"type": "str", "default": r"^\s*$",
                               "doc": "Regex of text to ignore."}},
    accepts=("html", "text"))
def run_hardcoded(ctx):
    doc_input = ctx.docs[0]
    text = doc_input.text
    min_len = int(ctx.params.get("min_length", 4))
    ignore = re.compile(ctx.params.get("ignore_pattern") or r"^\s*$")
    findings = []
    if doc_input.kind == "html":
        doc = doc_input.data["document"]
        for n in doc.nodes:
            if n.tag in ("script", "style", "title", "head", "meta", "link"):
                continue
            for child in n.children:
                if not isinstance(child, str):
                    continue
                raw = T.norm_ws(child)
                if len(raw) < min_len or ignore.match(raw):
                    continue
                if re.match(r"^[\d\s.,:;\-\u2013\u2014/()%]+$", raw):
                    continue
                findings.append({"text": raw, "locator": "line %d" % n.line, "element": n.tag})
        for n in doc.nodes:
            for attr in ("placeholder", "title", "aria-label", "alt", "value"):
                val = n.get(attr)
                if val and len(T.norm_ws(val)) >= min_len and not ignore.match(val):
                    if attr == "value" and (n.get("type") or "").lower() not in ("submit", "button", "reset"):
                        continue
                    findings.append({"text": T.norm_ws(val), "locator": "line %d" % n.line,
                                     "element": n.tag, "attribute": attr})
    else:
        for i, line in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"""['"]([^'"\n]{%d,})['"]""" % min_len, line):
                val = m.group(1)
                if ignore.match(val) or re.match(r"^[\w./:-]+$", val) and not " " in val:
                    continue
                findings.append({"text": val, "locator": "line %d" % i})
            for m in re.finditer(r">([^<>{}]{4,})<", line):
                findings.append({"text": T.norm_ws(m.group(1)), "locator": "line %d" % i})
    for f in findings:
        ctx.add(rule="i18n-hardcoded", message="Hard-coded user-facing string %r." % T.truncate(f["text"], 80),
                severity="medium", criterion="locale-architecture", locator=f["locator"], evidence=f,
                remediation="Move the string into the resource bundle and reference it by key.",
                retest="Re-run hardcoded-scan and confirm the string is gone.")
    if not findings:
        ctx.pass_check()
    concat = re.findall(r"""['"][^'"]*['"]\s*\+\s*(?:[A-Za-z_$][\w$.]*|[A-Za-z_$][\w$.]*\s*\+\s*['"])""", text)
    for c in concat[:20]:
        ctx.add(rule="i18n-concatenation", message="String concatenation builds a message: %r." % T.truncate(c, 60),
                severity="medium", criterion="locale-architecture",
                remediation="Use a single ICU message with placeholders instead of concatenation.",
                retest="Re-run and confirm concatenated messages are gone.")
    if not concat:
        ctx.pass_check()
    ctx.meta("hardcoded_strings", len(findings))
    ctx.meta("concatenations", len(concat))
    ctx.meta("sample", findings[:25])


@op("locale-format-audit", "Check that dates, times, numbers, currencies, percentages, telephone numbers "
    "and addresses follow the target locale profile, using CLDR-style conventions.",
    params={"locale": {"type": "str", "default": None, "doc": "Locale profile to validate against."},
            "text": {"type": "str", "default": None, "doc": "Inline text to audit instead of a file."}},
    accepts=("text", "html", "strings", "json"))
def run_locale_format(ctx):
    locale = (ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale") or "fil-PH")
    profiles = ctx.assets.get("locale_profiles", {}).get("locales", {})
    profile = profiles.get(locale)
    if profile is None:
        raise S.InputError("Unknown locale profile %r." % locale,
                          hint="Add it to asset-locale-profiles.json or choose one of: %s." % ", ".join(sorted(profiles)))
    if ctx.params.get("text"):
        text = ctx.params["text"]
    elif ctx.docs[0].kind == "html":
        text = ctx.docs[0].data["document"].body_text()
    elif ctx.docs[0].kind == "strings":
        text = "\n".join(load_strings(ctx.docs[0].path, ctx.docs[0].text)[0].values())
    elif ctx.docs[0].kind == "json":
        vals = ctx.docs[0].data
        text = "\n".join(str(v) for v in (vals.values() if isinstance(vals, dict) else vals))
    else:
        text = ctx.docs[0].text
    signals = T.format_signals(text)
    expected_group = profile.get("grouping_separator", ",")
    expected_decimal = profile.get("decimal_separator", ".")
    ctx.meta("signals", signals)
    ctx.meta("profile", profile)
    if expected_group == ",":
        if signals["grouping_dot"] and not profile.get("allow_dot_grouping"):
            ctx.add(rule="locale-number-grouping", message="Dot thousands grouping appears in a locale that uses commas.",
                    severity="high", criterion="locale-format",
                    evidence={"grouping_dot": signals["grouping_dot"]},
                    remediation="Use %r for thousands and %r for decimals." % (expected_group, expected_decimal),
                    retest="Re-run and confirm no dot-grouped numbers remain.")
        else:
            ctx.pass_check()
    if expected_decimal == "." and signals["decimal_comma"]:
        ctx.add(rule="locale-decimal", message="Decimal comma appears where a decimal point is expected.",
                severity="medium", criterion="locale-format",
                remediation="Use '.' as the decimal separator for this locale.",
                retest="Re-run and confirm decimal separators match the profile.")
    else:
        ctx.pass_check()
    if profile.get("date_order") == "MMMM D, YYYY" and signals["dmy_order"]:
        ctx.add(rule="locale-date-order", message="Day-month-year date order found; profile expects %r." % profile["date_order"],
                severity="medium", criterion="locale-format",
                remediation="Render dates through the locale formatter instead of hard-coded patterns.",
                retest="Re-run and confirm dates use the profile order.")
    else:
        ctx.pass_check()
    currency = profile.get("currency_code")
    if currency and not signals["currency_code"]:
        ctx.add(rule="locale-currency-code", message="No ISO currency code found for %s content." % locale,
                severity="low", criterion="locale-format",
                remediation="Show the ISO code with the amount so ambiguity is removed.",
                retest="Re-run and confirm the currency code appears with amounts.")
    else:
        ctx.pass_check()
    if profile.get("time_format") == "h:mm AM/PM" and signals["time_24h"] and not signals["time_12h"]:
        ctx.add(rule="locale-time", message="24-hour time appears; the profile expects %r." % profile["time_format"],
                severity="low", criterion="locale-format",
                remediation="Use the locale time format from CLDR data.",
                retest="Re-run and confirm the time format matches the profile.")
    else:
        ctx.pass_check()
    ctx.meta("locale", locale)
    ctx.meta("expected", {"grouping": expected_group, "decimal": expected_decimal,
                          "date_order": profile.get("date_order"), "time_format": profile.get("time_format"),
                          "currency": currency})


@op("expansion-budget", "Compare source and target string lengths against the locale text-expansion budget and "
    "flag strings likely to truncate or overflow the interface.",
    params={"budget_pct": {"type": "float", "default": None,
                           "doc": "Override the locale's expansion budget percentage."}},
    accepts=("json", "strings"))
def run_expansion(ctx):
    data = ctx.docs[0].data if ctx.docs[0].kind == "json" else None
    pairs = []
    if isinstance(data, dict) and isinstance(data.get("pairs"), list):
        pairs = data["pairs"]
    elif isinstance(data, list):
        pairs = data
    if not pairs:
        raise S.InputError("Expansion budget needs source/target pairs.",
                           hint="Provide [{\"key\":\"..\",\"source\":\"..\",\"target\":\"..\"}] or --reference a source bundle.")
    locale = ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale", "fil-PH")
    profile = ctx.assets.get("locale_profiles", {}).get("locales", {}).get(locale, {})
    budget = float(ctx.params.get("budget_pct") or profile.get("expansion_budget_pct", 35))
    rows = []
    for p in pairs:
        ctx.checkpoint()
        src = p.get("source") or ""
        tgt = p.get("target") or ""
        if not src:
            ctx.pass_check()
            continue
        ratio = round(100.0 * (len(tgt) - len(src)) / max(len(src), 1), 1)
        room = p.get("available_chars")
        rows.append({"key": p.get("key"), "source_chars": len(src), "target_chars": len(tgt),
                     "expansion_pct": ratio, "budget_pct": budget,
                     "available_chars": room})
        if ratio > budget:
            ctx.add(rule="i18n-expansion", message="%r expands %.1f%% (> %.1f%% budget)." % (
                p.get("key") or T.truncate(src, 30), ratio, budget), severity="medium",
                criterion="locale-architecture", locator=p.get("key"),
                evidence={"source": src[:120], "target": tgt[:120], "expansion_pct": ratio},
                remediation="Shorten the target string or give the container flexible width.",
                retest="Re-run and confirm the expansion falls within budget.")
        else:
            ctx.pass_check()
        if room and len(tgt) > int(room):
            ctx.add(rule="i18n-truncation-risk", message="%r needs %d chars but only %s are available." % (
                p.get("key"), len(tgt), room), severity="critical", criterion="locale-architecture",
                locator=p.get("key"), evidence={"target_chars": len(tgt), "available_chars": room},
                remediation="Shorten the string or widen the container before release.",
                retest="Re-run and confirm no string exceeds its available width.")
    worst = max((r["expansion_pct"] for r in rows), default=0)
    ctx.meta("locale", locale)
    ctx.meta("budget_pct", budget)
    ctx.meta("strings", len(rows))
    ctx.meta("worst_expansion_pct", worst)
    ctx.meta("rows", rows[:200])


# --------------------------------------------------------------------------- #
# Category 14: TM and terminology
# --------------------------------------------------------------------------- #
@op("tm-audit", "Validate and analyse a Translation Memory (TMX 1.4): segment counts, language pairs, empty "
    "segments, duplicates, source-target leverage and governance metadata.",
    params={"source_lang": {"type": "str", "default": None, "doc": "Source language to analyse."},
            "target_lang": {"type": "str", "default": None, "doc": "Target language to analyse."}},
    accepts=("tmx",))
def run_tm_audit(ctx):
    text = ctx.docs[0].text
    if "<tmx" not in text.lower():
        raise S.InputError("Input is not a TMX file.", hint="Export the memory as TMX 1.4 (XML).")
    units = re.findall(r"<tu\b.*?</tu>", text, re.S | re.I)
    src_lang = ctx.params.get("source_lang")
    tgt_lang = ctx.params.get("target_lang")
    pairs = []
    empty = 0
    seen = {}
    for i, tu in enumerate(units):
        ctx.checkpoint()
        tuid = re.search(r'tuid="([^"]+)"', tu)
        segs = {}
        for m in re.finditer(r'<tuv\b[^>]*xml:lang="([^"]+)"[^>]*>\s*<seg>(.*?)</seg>', tu, re.S | re.I):
            segs[m.group(1).lower()] = _xml_unescape(m.group(2))
        if len(segs) < 2:
            ctx.add(rule="tm-incomplete-unit", message="TU %d has fewer than two language variants." % (i + 1),
                    severity="medium", criterion="language-asset", locator="tu %d" % (i + 1), evidence={"segs": segs},
                    remediation="Remove or complete the incomplete translation unit.",
                    retest="Re-run and confirm every TU has source and target.")
            continue
        src_l = (src_lang or sorted(segs)[0]).lower()
        tgt_candidates = [l for l in segs if l != src_l]
        tgt_l = (tgt_lang or tgt_candidates[0]).lower()
        if src_l not in segs or tgt_l not in segs:
            ctx.add(rule="tm-language-missing", message="TU %d lacks %r or %r." % (i + 1, src_l, tgt_l),
                    severity="medium", criterion="language-asset", locator="tu %d" % (i + 1),
                    remediation="Re-import the memory with the required language pair.",
                    retest="Re-run and confirm the pair exists.")
            continue
        src, tgt = segs[src_l], segs[tgt_l]
        if not src.strip() or not tgt.strip():
            empty += 1
            ctx.add(rule="tm-empty-segment", message="TU %d has an empty source or target segment." % (i + 1),
                    severity="medium", criterion="language-asset", locator="tu %d" % (i + 1),
                    remediation="Delete or fill the empty segment.",
                    retest="Re-run and confirm no empty segments remain.")
            continue
        key = (src.strip().lower(), tgt.strip().lower())
        if key in seen:
            ctx.add(rule="tm-duplicate", message="TU %d duplicates TU %d." % (i + 1, seen[key]),
                    severity="low", criterion="language-asset", locator="tu %d" % (i + 1),
                    evidence={"source": T.truncate(src, 80)},
                    remediation="Merge duplicates and keep the highest-quality variant.",
                    retest="Re-run and confirm duplicates are merged.")
        else:
            seen[key] = i + 1
            ctx.pass_check()
        if src.strip().lower() == tgt.strip().lower() and src_l.split("-")[0] != tgt_l.split("-")[0]:
            ctx.add(rule="tm-copy-source", message="TU %d target equals the source (possible untranslated copy)." % (i + 1),
                    severity="high", criterion="language-asset", locator="tu %d" % (i + 1),
                    evidence={"source": src[:80], "target": tgt[:80]},
                    remediation="Translate the segment or confirm it is a legitimate identical term.",
                    retest="Re-run and confirm the segment is legitimately identical.")
        else:
            ctx.pass_check()
        pairs.append({"src": src, "tgt": tgt})
    header = re.search(r"<header\b[^>]*>", text, re.I)
    ctx.meta("tmx_version", (re.search(r'version="([^"]+)"', header.group(0)).group(1) if header and re.search(r'version="([^"]+)"', header.group(0)) else None))
    ctx.meta("header_attrs", (re.search(r"<header\b([^>]*)>", text, re.I).group(1).strip() if header else None))
    ctx.meta("translation_units", len(units))
    ctx.meta("empty_units", empty)
    ctx.meta("duplicates", len(units) - len(seen) - empty)
    ctx.meta("source_lang", src_lang)
    ctx.meta("target_lang", tgt_lang)
    ctx.note("TMX 1.4 attributes such as creationtool, creationtoolversion, segtype, o-tmf, adminlang, "
             "srclang and datatype should be preserved when importing.")


def _xml_unescape(text):
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


@op("term-audit", "Validate a termbase or glossary: missing translations, forbidden terms in targets, "
    "inconsistent approved terms, duplicate entries and do-not-translate handling.",
    params={"target_lang": {"type": "str", "default": None, "doc": "Target language to check."},
            "check_forbidden": {"type": "bool", "default": True,
                                "doc": "Flag forbidden/deprecated terms found in targets."}},
    accepts=("json", "tbx"))
def run_term_audit(ctx):
    entries, kind = _load_terms(ctx)
    if not entries:
        raise S.InputError("No terminology entries found.",
                           hint="Provide JSON [{source,target,status,forbidden:[...]}] or TBX with glossEntry elements.")
    missing = inconsistent = forbidden_hits = 0
    target_lang = ctx.params.get("target_lang") or ctx.config.get("locale", {}).get("default_locale")
    seen = {}
    for e in entries:
        ctx.checkpoint()
        src = T.norm_ws(e.get("source") or e.get("term") or "")
        tgt = T.norm_ws(e.get("target") or "")
        status = (e.get("status") or "approved").lower()
        if not src:
            ctx.add(rule="term-no-source", message="Term entry without a source term: %r." % e,
                    severity="low", criterion="terminology",
                    remediation="Add the source term or remove the entry.",
                    retest="Re-run and confirm every entry has a source term.")
            continue
        if not tgt and status != "deprecated":
            missing += 1
            ctx.add(rule="term-missing-target", message="Term %r has no %r translation." % (src, target_lang),
                    severity="medium", criterion="terminology", locator=src, evidence=e,
                    remediation="Supply the approved target term or mark the entry as deprecated.",
                    retest="Re-run and confirm the entry is translated or deprecated.")
        else:
            ctx.pass_check()
        if tgt:
            key = src.lower()
            if key in seen and seen[key].lower() != tgt.lower():
                inconsistent += 1
                ctx.add(rule="term-inconsistent", message="Term %r is translated as both %r and %r." % (
                    src, seen[key], tgt), severity="high", criterion="terminology", locator=src,
                    evidence={"first": seen[key], "second": tgt},
                    remediation="Choose one approved translation and deprecate the other.",
                    retest="Re-run and confirm only one approved translation remains.")
            else:
                seen[key] = tgt
                ctx.pass_check()
        if ctx.params.get("check_forbidden", True):
            for bad in e.get("forbidden") or []:
                if tgt and bad.lower() in tgt.lower():
                    forbidden_hits += 1
                    ctx.add(rule="term-forbidden", message="Target %r contains forbidden term %r." % (tgt, bad),
                            severity="high", criterion="terminology", locator=src, evidence={"forbidden": bad},
                            remediation="Replace the forbidden term with the approved equivalent.",
                            retest="Re-run and confirm no forbidden term appears.")
                else:
                    ctx.pass_check()
        for field in ("context", "definition", "part_of_speech", "domain"):
            if not e.get(field) and status == "approved":
                ctx.note("Entry %r lacks %s; translators may need it for disambiguation." % (src, field))
                break
    ctx.meta("termbase_kind", kind)
    ctx.meta("entries", len(entries))
    ctx.meta("missing_targets", missing)
    ctx.meta("inconsistent", inconsistent)
    ctx.meta("forbidden_hits", forbidden_hits)
    ctx.meta("target_lang", target_lang)


def _load_terms(ctx):
    doc = ctx.docs[0]
    if doc.kind == "tbx":
        entries = []
        for block in re.findall(r"<termEntry\b.*?</termEntry>", doc.text, re.S | re.I):
            langs = {}
            for m in re.finditer(r'<langSet\b[^>]*xml:lang="([^"]+)"[^>]*>(.*?)</langSet>', block, re.S | re.I):
                terms = [_xml_unescape(t) for t in re.findall(r"<term>(.*?)</term>", m.group(2), re.S | re.I)]
                if terms:
                    langs[m.group(1)] = terms[0]
            if langs:
                src_lang = sorted(langs)[0]
                tgt_lang = next((l for l in sorted(langs) if l != src_lang), None)
                entries.append({"source": langs[src_lang], "target": langs.get(tgt_lang) if tgt_lang else None,
                                "status": "approved"})
        return entries, "tbx"
    data = doc.data
    if isinstance(data, list):
        return data, "json"
    if isinstance(data, dict):
        for key in ("terms", "entries", "glossary", "termbase"):
            if isinstance(data.get(key), list):
                return data[key], "json"
    raise S.InputError("Unrecognised termbase JSON shape.",
                       hint="Use {'terms':[{source,target,status,forbidden,context}]} or a TBX file.")


@op("tm-match", "Leverage source segments against a translation memory: exact, fuzzy and no-match analysis "
    "with match bands and per-segment recommendations.",
    params={"threshold": {"type": "float", "default": 0.75, "doc": "Minimum similarity to report a fuzzy match."},
            "leverage_target_pct": {"type": "float", "default": 40.0, "doc": "Target leverage to report against."}},
    accepts=("json",))
def run_tm_match(ctx):
    data = ctx.docs[0].data
    segments = data.get("segments") if isinstance(data, dict) else data
    if not isinstance(segments, list) or not segments:
        raise S.InputError("No source segments supplied.",
                           hint="Provide {'segments':[\"text\", ...], 'tm_path': \"memory.tmx\"} or a list of segments.")
    tm_path = (data.get("tm_path") if isinstance(data, dict) else None) or ctx.params.get("tm_path")
    tm_text = None
    if len(ctx.docs) > 1 and ctx.docs[1].kind == "tmx":
        tm_text, tm_path = ctx.docs[1].text, ctx.docs[1].path
    if tm_text is None:
        if not tm_path:
            raise S.ParamError(
                "A translation memory is required for leverage analysis.",
                hint="Pass --reference <memory.tmx>, or set tm_path in the JSON input.")
        tm_text = S.read_text(tm_path, limits=ctx.limits)
    units = []
    for tu in re.findall(r"<tu\b.*?</tu>", tm_text, re.S | re.I):
        segs = re.findall(r"<seg>(.*?)</seg>", tu, re.S | re.I)
        if len(segs) >= 2:
            units.append((_xml_unescape(segs[0]), _xml_unescape(segs[1])))
    if not units:
        raise S.InputError("The translation memory contains no bilingual units.")
    threshold = float(ctx.params.get("threshold", 0.75))
    bands = {"exact": 0, "fuzzy_high": 0, "fuzzy_low": 0, "no_match": 0}
    words_total = words_matched = 0
    rows = []
    for i, seg in enumerate(segments):
        ctx.checkpoint()
        src = seg if isinstance(seg, str) else (seg.get("source") or "")
        words_total += max(1, T.word_count(src))
        best = (0.0, None, None)
        norm_src = T.normalize_for_match(src)
        for tsrc, ttgt in units:
            if T.normalize_for_match(tsrc) == norm_src:
                best = (1.0, tsrc, ttgt)
                break
            score = T.similarity(norm_src, T.normalize_for_match(tsrc))
            if score > best[0]:
                best = (score, tsrc, ttgt)
        band = ("exact" if best[0] == 1.0 else "fuzzy_high" if best[0] >= 0.85 else
                "fuzzy_low" if best[0] >= threshold else "no_match")
        bands[band] += 1
        if band != "no_match":
            words_matched += max(1, T.word_count(src))
        rows.append({"segment": T.truncate(src, 90), "match": band, "score": best[0],
                     "tm_source": T.truncate(best[1], 90) if best[1] else None,
                     "tm_target": T.truncate(best[2], 90) if best[2] else None})
        if band == "no_match":
            ctx.add(rule="tm-no-match", message="Segment %d has no usable match (best %.2f)." % (i + 1, best[0]),
                    severity="info", criterion="language-asset", locator="segment %d" % (i + 1),
                    evidence=rows[-1],
                    remediation="Route to human translation and then add the validated pair to the memory.",
                    retest="Re-run tm-match and confirm the new segment reaches fuzzy or exact.")
        else:
            ctx.pass_check()
    leverage = round(100.0 * words_matched / max(words_total, 1), 2)
    target = float(ctx.params.get("leverage_target_pct", 40.0))
    if leverage < target:
        ctx.add(rule="tm-low-leverage", message="Leverage %.2f%% is below the %.2f%% target." % (leverage, target),
                severity="low", criterion="language-asset",
                remediation="Improve memory alignment or pre-translate with approved terminology.",
                retest="Re-run and confirm leverage meets the target.")
    else:
        ctx.pass_check()
    ctx.meta("segments", len(segments))
    ctx.meta("bands", bands)
    ctx.meta("leverage_pct", leverage)
    ctx.meta("matches", rows[:300])


# --------------------------------------------------------------------------- #
# Category 15: translation and human review
# --------------------------------------------------------------------------- #
@op("translation-qa", "Bilingual QA of source/target segment pairs: placeholders, tags, numbers, untranslated "
    "segments, punctuation, whitespace, terminology and length anomalies.",
    params={"target_lang": {"type": "str", "default": None, "doc": "Target locale of the content."},
            "check_numbers": {"type": "bool", "default": True, "doc": "Require numeric values to match."}},
    accepts=("json",))
def run_translation_qa(ctx):
    pairs = _pairs(ctx)
    target_lang = ctx.params.get("target_lang") or ctx.config.get("locale", {}).get("default_locale")
    glossary = ctx.assets.get("terminology", {}).get("terms", [])
    issues = 0
    for i, p in enumerate(pairs):
        ctx.checkpoint()
        src, tgt = p["source"], p["target"]
        key = p.get("key") or "segment %d" % (i + 1)
        if not src.strip():
            continue
        if not tgt.strip():
            issues += 1
            ctx.add(rule="qa-empty-target", message="%s has an empty target." % key, severity="critical",
                    criterion="translation-quality", locator=key, evidence=p,
                    remediation="Translate the segment.",
                    retest="Re-run and confirm the target is non-empty.")
            continue
        missing, extra = _placeholder_diff(src, tgt)
        if missing or extra:
            issues += 1
            ctx.add(rule="qa-placeholder", message="%s has placeholder drift (missing %s / extra %s)." % (
                key, missing or "-", extra or "-"), severity="critical", criterion="translation-quality",
                locator=key, evidence={"source": src, "target": tgt},
                remediation="Restore the exact placeholder and markup set from the source.",
                retest="Re-run and confirm placeholders match.")
        else:
            ctx.pass_check()
        if T.norm_ws(src).lower() == T.norm_ws(tgt).lower():
            issues += 1
            ctx.add(rule="qa-untranslated", message="%s target is identical to the source." % key,
                    severity="high", criterion="translation-quality", locator=key,
                    evidence={"text": T.truncate(src, 90)},
                    remediation="Translate, or mark the segment as a do-not-translate term.",
                    retest="Re-run and confirm the segment is translated or exempted.")
        else:
            ctx.pass_check()
        if ctx.params.get("check_numbers", True):
            ns = re.findall(r"\d+(?:[.,]\d+)*", src)
            nt = re.findall(r"\d+(?:[.,]\d+)*", tgt)
            if sorted(ns) != sorted(nt):
                issues += 1
                ctx.add(rule="qa-number-mismatch", message="%s numbers differ (source %s, target %s)." % (
                    key, ns[:4], nt[:4]), severity="high", criterion="translation-quality", locator=key,
                    evidence={"source": src, "target": tgt},
                    remediation="Correct the numeric values or localise the formats deliberately.",
                    retest="Re-run and confirm numbers match.")
            else:
                ctx.pass_check()
        if (src.rstrip().endswith((".", "!", "?")) != tgt.rstrip().endswith((".", "!", "?", "\u3002"))):
            ctx.add(rule="qa-punctuation", message="%s terminal punctuation differs between source and target." % key,
                    severity="low", criterion="translation-quality", locator=key,
                    remediation="Match the sentence-final punctuation of the target language convention.",
                    retest="Re-run and confirm punctuation policy is satisfied.")
        else:
            ctx.pass_check()
        if src != src.strip() or tgt != tgt.strip():
            ctx.add(rule="qa-whitespace", message="%s has leading or trailing whitespace." % key,
                    severity="low", criterion="translation-quality", locator=key,
                    remediation="Trim whitespace; padding belongs to the layout, not the string.",
                    retest="Re-run and confirm strings are trimmed.")
        else:
            ctx.pass_check()
        for term in glossary:
            if not isinstance(term, dict):
                continue
            s_term = (term.get("source") or "").strip()
            t_term = (term.get("target") or "").strip()
            if not s_term or not t_term:
                continue
            if re.search(r"\b%s\b" % re.escape(s_term), src, re.I) and not re.search(re.escape(t_term), tgt, re.I):
                bad = term.get("forbidden") or []
                used_bad = [b for b in bad if re.search(re.escape(b), tgt, re.I)]
                ctx.add(rule="qa-terminology", message="%s uses %r instead of approved %r%s." % (
                    key, used_bad[0] if used_bad else "(variant)", t_term,
                    " [forbidden term]" if used_bad else ""),
                    severity="high", criterion="terminology", locator=key,
                    evidence={"source": src, "target": tgt, "approved": t_term},
                    remediation="Apply the approved term from the termbase.",
                    retest="Re-run and confirm the approved term is present.")
                issues += 1
            else:
                ctx.pass_check()
        ratio = len(tgt) / float(max(len(src), 1))
        if ratio > 3.0 or ratio < 0.3:
            ctx.add(rule="qa-length-ratio", message="%s length ratio %.2f is anomalous." % (key, ratio),
                    severity="low", criterion="translation-quality", locator=key,
                    evidence={"source_chars": len(src), "target_chars": len(tgt)},
                    remediation="Confirm the translation is complete and not summarised or padded.",
                    retest="Re-run and confirm the ratio is plausible.")
        else:
            ctx.pass_check()
    ctx.meta("pairs", len(pairs))
    ctx.meta("issues", issues)
    ctx.meta("target_lang", target_lang)
    ctx.note("Automated bilingual checks cannot judge meaning, register or cultural fit; qualified review is required.")


def _pairs(ctx):
    data = ctx.docs[0].data
    pairs = None
    if isinstance(data, dict):
        for key in ("pairs", "segments", "records"):
            if isinstance(data.get(key), list):
                pairs = data[key]
                break
    elif isinstance(data, list):
        pairs = data
    if not pairs:
        raise S.InputError("No source/target pairs found.",
                           hint="Provide [{\"key\":\"..\",\"source\":\"..\",\"target\":\"..\"}].")
    out = []
    for i, p in enumerate(pairs):
        if not isinstance(p, dict):
            raise S.InputError("Pair %d is not an object." % (i + 1),
                              hint="Each pair needs at least source and target strings.")
        if "source" not in p or "target" not in p:
            raise S.InputError("Pair %d is missing 'source' or 'target'." % (i + 1))
        out.append(p)
    return out


@op("mqm-score", "Score translated content using an MQM-style error typology: severity-weighted penalties, "
    "normalised quality score and a pass/fail verdict against the configured threshold.",
    params={"threshold": {"type": "float", "default": 90.0, "doc": "Minimum passing score (0-100)."},
            "words": {"type": "int", "default": None, "doc": "Word count of the sample, for normalisation."}},
    accepts=("json",))
def run_mqm(ctx):
    data = ctx.docs[0].data
    errors = data.get("errors") if isinstance(data, dict) else data
    if not isinstance(errors, list):
        raise S.InputError("MQM scoring needs an error list.",
                           hint="Provide {'errors':[{category,severity,count,note}]} or a list of errors.")
    typology = ctx.assets.get("mqm", {})
    weights = typology.get("severity_weights", {"critical": 10, "major": 5, "minor": 1})
    categories = {c["id"] for c in typology.get("categories", [])} or set()
    total_penalty = 0.0
    by_cat = {}
    for i, err in enumerate(errors):
        ctx.checkpoint()
        cat = (err.get("category") or "other").lower()
        sev = (err.get("severity") or "minor").lower()
        count = int(err.get("count", 1))
        weight = float(weights.get(sev, 1))
        penalty = weight * count
        total_penalty += penalty
        by_cat[cat] = by_cat.get(cat, 0) + count
        if categories and cat not in categories:
            ctx.add(rule="mqm-unknown-category", message="Error %d uses unknown category %r." % (i + 1, cat),
                    severity="low", criterion="translation-quality",
                    remediation="Map the error to a category in asset-mqm-typology.json.",
                    retest="Re-run and confirm the category is recognised.")
        else:
            ctx.pass_check()
        if sev in ("critical", "major"):
            ctx.add(rule="mqm-%s" % sev, message="%s error (%s): %s" % (sev.upper(), cat,
                    T.truncate(err.get("note") or "no note", 120)),
                    severity="critical" if sev == "critical" else "high",
                    criterion="translation-quality", evidence=err,
                    remediation=err.get("remediation") or "Correct the error per the MQM category definition.",
                    retest="Re-run mqm-score on the corrected sample and confirm the score passes.")
    words = int(ctx.params.get("words") or data.get("words") or 0) if isinstance(data, dict) else int(ctx.params.get("words") or 0)
    if not words:
        words = sum(max(1, T.word_count(e.get("note") or "")) for e in errors) or 100
        ctx.note("Word count was not supplied; normalised against %d sampled words." % words)
    penalty_per_1000 = round(1000.0 * total_penalty / max(words, 1), 2)
    score = round(max(0.0, 100.0 - penalty_per_1000 / 2.0), 2)
    threshold = float(ctx.params.get("threshold", ctx.config.get("thresholds", {}).get("mqm_pass_score", 90.0)))
    verdict = "pass" if score >= threshold else "fail"
    if verdict == "fail":
        ctx.add(rule="mqm-threshold", message="MQM score %.2f is below the %.2f threshold." % (score, threshold),
                severity="high", criterion="translation-quality",
                evidence={"score": score, "penalty_per_1000_words": penalty_per_1000},
                remediation="Complete a rework pass on the highest-weight categories, then re-score a fresh sample.",
                retest="Re-run mqm-score on a new sample and confirm the score passes.")
    else:
        ctx.pass_check()
    ctx.meta("score", score)
    ctx.meta("verdict", verdict)
    ctx.meta("penalty_points", total_penalty)
    ctx.meta("penalty_per_1000_words", penalty_per_1000)
    ctx.meta("words", words)
    ctx.meta("by_category", by_cat)
    ctx.meta("threshold", threshold)


@op("review-plan", "Plan human translation and accessibility review: sampling size, reviewer roles, "
    "rounds, checklists and the evidence each reviewer must return.",
    params={"words": {"type": "int", "default": 2000, "doc": "Total word count to sample from."},
            "confidence": {"type": "float", "default": 0.95, "doc": "Confidence level for the sample."},
            "error_rate": {"type": "float", "default": 0.05, "doc": "Expected proportion of defective segments."},
            "margin": {"type": "float", "default": 0.05, "doc": "Acceptable margin of error."}},
    accepts=("json", "text"))
def run_review_plan(ctx):
    words = int(ctx.params.get("words", 2000))
    confidence = float(ctx.params.get("confidence", 0.95))
    p = float(ctx.params.get("error_rate", 0.05))
    margin = float(ctx.params.get("margin", 0.05))
    z = {0.8: 1.2816, 0.85: 1.4395, 0.9: 1.6449, 0.95: 1.96, 0.99: 2.5758}.get(round(confidence, 2), 1.96)
    segments = max(1, int(round(words / 12.0)))
    n = (z * z * p * (1 - p)) / (margin * margin) if p > 0 else 0
    n = int(round(n)) if n else segments
    sample = min(segments, max(1, n))
    roles = [
        {"role": "bilingual reviewer", "task": "Compare source and target for accuracy, completeness, fluency and terminology.",
         "returns": ["MQM error list with category and severity", "edited target strings", "query log"]},
        {"role": "monolingual reviewer", "task": "Read the target only for naturalness, tone and register.",
         "returns": ["fluency findings", "style deviations from the guide"]},
        {"role": "domain expert", "task": "Validate subject-matter accuracy and jurisdiction-specific legal meaning.",
         "returns": ["accuracy sign-off or corrections"]},
        {"role": "in-country reviewer", "task": "Validate cultural fit, formats, names and regulatory wording.",
         "returns": ["locale acceptance decision"]},
        {"role": "accessibility reviewer", "task": "Verify accessible names, reading order and captions in the target locale.",
         "returns": ["AT session notes", "criteria pass/fail"]},
    ]
    plan = {"words": words, "segments_estimate": segments, "sample_segments": sample,
            "sampling": {"confidence": confidence, "expected_error_rate": p, "margin": margin,
                         "method": "random sample with 100% review of high-risk categories (legal, safety, consent)"},
            "roles": roles, "rounds": 2,
            "round_detail": ["Round 1: full sample by bilingual reviewer, MQM scoring",
                             "Round 2: re-review of corrected segments plus 10% fresh sample"],
            "evidence_to_return": ["completed MQM error list", "edited files", "query resolutions",
                                   "sign-off record with reviewer name and date"]}
    ctx.meta("plan", plan)
    ctx.meta("sample_segments", sample)
    ctx.note("Sample size is derived from a proportion formula; high-risk content must be reviewed in full.")


@op("translation-brief", "Generate a translation brief with audience, purpose, register, terminology, "
    "format constraints, references and acceptance criteria for the assigned reviewer.",
    params={"locale": {"type": "str", "default": None, "doc": "Target locale."},
            "audience": {"type": "str", "default": "general public", "doc": "Intended audience."},
            "register": {"type": "enum", "values": ["formal", "neutral", "conversational"], "default": "neutral",
                         "doc": "Required register."},
            "content_type": {"type": "str", "default": "user interface", "doc": "Content type being localised."}},
    accepts=("strings", "json", "text"))
def run_brief(ctx):
    locale = ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale", "fil-PH")
    profile = ctx.assets.get("locale_profiles", {}).get("locales", {}).get(locale, {})
    terms = ctx.assets.get("terminology", {})
    brief = {
        "target_locale": locale,
        "locale_notes": profile.get("review_notes", ""),
        "audience": ctx.params.get("audience"),
        "register": ctx.params.get("register"),
        "content_type": ctx.params.get("content_type"),
        "formats": {"date_order": profile.get("date_order"), "time_format": profile.get("time_format"),
                    "currency": profile.get("currency_code"), "decimal_separator": profile.get("decimal_separator"),
                    "grouping_separator": profile.get("grouping_separator")},
        "terminology": {
            "approved_sample": [{"source": t.get("source"), "target": t.get("target")}
                                for t in terms.get("terms", [])[:12] if isinstance(t, dict)][:12],
            "do_not_translate": terms.get("do_not_translate", []),
            "forbidden_sample": [{"avoid": t.get("source"), "forbidden": t.get("forbidden")}
                                 for t in terms.get("terms", []) if isinstance(t, dict) and t.get("forbidden")][:8],
        },
        "accessibility_requirements": [
            "Keep accessible names identical to visible labels (WCAG 2.5.3).",
            "Do not translate translatable attributes into markup that breaks at runtime.",
            "Captions and alt text are part of the deliverable, not a follow-up.",
        ],
        "acceptance_criteria": [
            "No placeholder, tag or number drift.",
            "Terminology matches the termbase.",
            "MQM score at or above the configured threshold.",
            "Passes expansion budget without truncation.",
        ],
    }
    ctx.meta("brief", brief)
    ctx.note("The brief is generated for assignment; a human owner must confirm audience and register.")


# --------------------------------------------------------------------------- #
# Category 16: Filipino, English, Philippine locale
# --------------------------------------------------------------------------- #
@op("filipino-review", "Review Filipino/Tagalog text: orthography, function words (ng/nang, mga), loanword "
    "handling, code-switching, formality markers, inclusive language and accessibility terminology.",
    params={"register": {"type": "enum", "values": ["formal", "neutral", "conversational"], "default": "neutral",
                         "doc": "Expected register, which governs po/opo expectations."},
            "allow_taglish": {"type": "bool", "default": False, "doc": "Permit code-switched English words."}},
    accepts=("text", "strings", "json", "html"))
def run_filipino_review(ctx):
    text = _text_of(ctx)
    terms = ctx.assets.get("terminology", {})
    fil = terms.get("filipino", {})
    style = fil.get("style_rules", {})
    register = ctx.params.get("register", "neutral")
    metrics = T.lexicon_metrics(text, fil)
    metrics["script_profile"] = T.script_profile(text)
    issues = 0

    for rule in fil.get("spelling_rules", []):
        pattern = rule.get("pattern")
        if not pattern:
            continue
        for m in re.finditer(pattern, text, re.I):
            issues += 1
            ctx.add(rule="fil-orthography", message="%s (%r)" % (rule.get("message", "Orthography issue"),
                                                                  m.group(0)),
                    severity=rule.get("severity", "medium"), criterion="fil-PH-language",
                    locator="offset %d" % m.start(),
                    evidence={"match": m.group(0), "suggestion": rule.get("suggestion")},
                    remediation=rule.get("suggestion") or "Apply the standard Filipino orthography.",
                    retest="Re-run filipino-review and confirm the pattern no longer matches.")
    if not fil.get("spelling_rules"):
        ctx.pass_check()

    markers = [m for m in T.find_terms(text, fil.get("taglish_markers", []))]
    if markers and not ctx.params.get("allow_taglish"):
        for m in markers[:40]:
            issues += 1
            ctx.add(rule="fil-code-switching", message="English word %r inside Filipino text." % m["surface"],
                    severity="medium", criterion="fil-PH-language", locator="offset %d" % m["start"],
                    evidence={"word": m["surface"]},
                    remediation="Use the approved Filipino equivalent, or confirm the term is an accepted loanword.",
                    retest="Re-run and confirm code-switching is intentional or removed.")
    else:
        ctx.pass_check()

    formal_markers = style.get("formality_markers", ["po", "opo", "ho"])
    has_formal = bool(T.find_terms(text, formal_markers))
    if register == "formal" and not has_formal and T.word_count(text) > 20:
        issues += 1
        ctx.add(rule="fil-formality", message="Formal register requested but no politeness marker (po/opo) found.",
                severity="low", criterion="fil-PH-language",
                remediation="Add po/opo where the sentence addresses the reader directly.",
                retest="Re-run and confirm the register is detected as formal.")
    else:
        ctx.pass_check()

    for term in fil.get("preferred_terms", []):
        avoid = term.get("avoid")
        prefer = term.get("prefer")
        if not avoid or not prefer:
            continue
        hits = T.find_terms(text, [avoid] if isinstance(avoid, str) else avoid)
        for h in hits[:20]:
            issues += 1
            ctx.add(rule="fil-terminology", message="%r should be %r in Filipino content." % (h["surface"], prefer),
                    severity="medium", criterion="fil-PH-language", locator="offset %d" % h["start"],
                    evidence={"found": h["surface"], "preferred": prefer, "context": term.get("context")},
                    remediation="Replace with the preferred Filipino term.",
                    retest="Re-run and confirm the deprecated term is gone.")

    for bad, prefer in (fil.get("inclusive_language", {}) or {}).items():
        for h in T.find_terms(text, [bad])[:10]:
            issues += 1
            ctx.add(rule="fil-inclusive-language", message="Non-inclusive term %r; prefer %r." % (h["surface"], prefer),
                    severity="medium", criterion="inclusive-language", locator="offset %d" % h["start"],
                    evidence={"found": h["surface"], "preferred": prefer},
                    remediation="Rewrite with person-first, neutral wording.",
                    retest="Re-run and confirm the flagged term is gone.")

    max_grade = float(ctx.config.get("thresholds", {}).get("max_filipino_reading_grade", 10))
    if metrics["avg_sentence_words"] > float(style.get("max_sentence_words", 22)):
        issues += 1
        ctx.add(rule="fil-sentence-length", message="Average sentence length %.1f words exceeds %s." % (
            metrics["avg_sentence_words"], style.get("max_sentence_words", 22)),
            severity="medium", criterion="plain-language",
            evidence=metrics,
            remediation="Split long sentences; plain Filipino favours short clauses.",
            retest="Re-run and confirm average sentence length is within the style rule.")
    else:
        ctx.pass_check()

    ctx.meta("metrics", metrics)
    ctx.meta("issues", issues)
    ctx.meta("register", register)
    ctx.note("Filipino review requires a qualified bilingual reviewer; these checks are advisory lint, not a language verdict.")


def _text_of(ctx):
    doc = ctx.docs[0]
    if doc.kind == "html":
        return doc.data["document"].body_text()
    if doc.kind == "strings":
        strings, _ = load_strings(doc.path, doc.text)
        return "\n".join(strings.values())
    if doc.kind == "json":
        data = doc.data
        if isinstance(data, dict) and isinstance(data.get("pairs"), list):
            return "\n".join(str(p.get("target") or "") for p in data["pairs"])
        if isinstance(data, dict):
            return "\n".join(str(v) for v in data.values() if isinstance(v, str))
        if isinstance(data, list):
            return "\n".join(str(v) for v in data if isinstance(v, str))
    return doc.text


@op("philippine-locale-check", "Validate Philippine locale conventions: date and time order, peso currency "
    "formatting, telephone numbering, address order, name order and government terminology.",
    params={"locale": {"type": "enum", "values": ["fil-PH", "en-PH", "tl-PH"], "default": "fil-PH",
                       "doc": "Philippine locale variant to validate against."}},
    accepts=("text", "strings", "json", "html"))
def run_ph_locale(ctx):
    locale = ctx.params.get("locale", "fil-PH")
    profile = ctx.assets.get("locale_profiles", {}).get("locales", {}).get(locale) or {}
    text = _text_of(ctx)
    signals = T.format_signals(text)
    checks = [
        (bool(signals["currency_code"] or signals["currency_symbol"]), "ph-currency",
         "No peso amount with a currency marker was found; confirm amounts are labelled.",
         "low", "Show amounts as PHP 1,234.56 or \u20b11,234.56 so the currency is unambiguous."),
        (signals["grouping_dot"] == 0, "ph-number-grouping",
         "Dot grouping found; Philippine English/Filipino convention uses 1,234.56.",
         "medium", "Use comma thousands separators and a decimal point."),
        (signals["dmy_order"] == 0 or profile.get("date_order") == "D MMMM YYYY", "ph-date-order",
         "Month-day-year ordering found; en-PH/fil-PH documents conventionally use D Month YYYY.",
         "medium", "Render dates through the locale formatter (e.g. 7 Nobyembre 2026 / 7 November 2026)."),
        (not re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", text) or profile.get("allow_slash_dates"), "ph-date-ambiguity",
         "Ambiguous slash dates found (7/11/2026 could be 7 November or 11 July).",
         "high", "Use an unambiguous month name or ISO 8601 where space allows."),
        (bool(re.search(r"(?:\+63|0)9\d{2}[\s-]?\d{3}[\s-]?\d{4}", text)) or "phone" not in text.lower(), "ph-telephone",
         "Philippine mobile numbers should appear as +63 9XX XXX XXXX or 09XX XXX XXXX.",
         "low", "Normalise telephone numbers to the Philippine numbering plan."),
    ]
    for ok, rule_id, message, sev, remediation in checks:
        if ok:
            ctx.pass_check()
        else:
            ctx.add(rule=rule_id, message=message, severity=sev, criterion="fil-PH-locale",
                    evidence={"signals": signals},
                    remediation=remediation,
                    retest="Re-run and confirm the convention check passes.")
    gov_terms = ctx.assets.get("terminology", {}).get("filipino", {}).get("government_terms", [])
    for gt in gov_terms:
        if not isinstance(gt, dict):
            continue
        found = T.find_terms(text, [gt.get("en") or ""])
        for f in found[:5]:
            ctx.note("Government term %r should be rendered %r in Filipino content." % (f["surface"], gt.get("fil")))
    ctx.meta("locale", locale)
    ctx.meta("profile", profile)
    ctx.meta("signals", signals)


@op("language-readiness", "Assess readiness for a target language: resource completeness, font coverage, "
    "reviewer availability, locale profile and outstanding blockers, with a go/no-go recommendation.",
    params={"languages": {"type": "list", "default": None, "doc": "Languages to assess."},
            "strings_path": {"type": "str", "default": None, "doc": "Resource bundle to measure coverage."}},
    accepts=("json", "strings"))
def run_language_readiness(ctx):
    langs = ctx.params.get("languages")
    if isinstance(langs, str):
        langs = [l.strip() for l in langs.split(",")]
    data = ctx.docs[0].data if ctx.docs[0].kind == "json" else {}
    langs = langs or (data.get("languages") if isinstance(data, dict) else None)
    if not langs:
        raise S.ParamError("No target languages supplied.",
                           hint="Pass --set languages=fil-PH,ceb-PH or provide {\"languages\": [...]}.")
    profiles = ctx.assets.get("locale_profiles", {}).get("locales", {})
    fonts = data.get("fonts", []) if isinstance(data, dict) else []
    coverage = data.get("coverage", {}) if isinstance(data, dict) else {}
    readiness = []
    for lang in langs:
        profile = profiles.get(lang)
        blockers = []
        if not profile:
            blockers.append("no locale profile")
        else:
            if profile.get("direction") == "rtl" and not data.get("rtl_supported"):
                blockers.append("RTL layout not implemented")
            required = profile.get("required_charset", "")
            if fonts and required and not data.get("font_covers_" + lang):
                blockers.append("font coverage not proven for %s" % required)
            if profile.get("review_notes") and not data.get("reviewer_" + lang):
                blockers.append("no qualified reviewer assigned")
        cov = coverage.get(lang)
        if cov is not None and float(cov) < float(ctx.params.get("min_coverage_pct", 95)):
            blockers.append("coverage %.1f%% below target" % float(cov))
        verdict = "go" if not blockers else ("conditional" if len(blockers) == 1 else "no-go")
        readiness.append({"locale": lang, "verdict": verdict, "blockers": blockers,
                          "profile_present": bool(profile), "coverage_pct": cov})
        for b in blockers:
            ctx.add(rule="lang-readiness", message="%s blocked: %s." % (lang, b), severity="high",
                    criterion="locale-readiness", locator=lang,
                    remediation="Resolve the blocker or defer the locale from release scope with owner approval.",
                    retest="Re-run language-readiness and confirm the blocker clears.")
        if not blockers:
            ctx.pass_check()
    ctx.meta("readiness", readiness)
    ctx.meta("go", [r["locale"] for r in readiness if r["verdict"] == "go"])


# --------------------------------------------------------------------------- #
# Category 17: bidi and complex scripts
# --------------------------------------------------------------------------- #
@op("bidi-audit", "Audit bidirectional text: base direction, unbalanced control characters, isolate usage, "
    "mirroring of punctuation and icons, and embedded number formatting.",
    params={"expected_direction": {"type": "enum", "values": ["ltr", "rtl", "auto"], "default": "auto",
                                   "doc": "Direction the artifact should declare."}},
    accepts=("text", "strings", "json", "html"))
def run_bidi(ctx):
    text = _text_of(ctx)
    profile = T.bidi_profile(text)
    scripts = T.script_profile(text)
    expected = ctx.params.get("expected_direction", "auto")
    if expected != "auto" and profile["base_direction_guess"] != expected:
        ctx.add(rule="bidi-direction", message="Content reads as %s but %s was expected." % (
            profile["base_direction_guess"], expected), severity="high", criterion="1.3.1",
            evidence=profile, remediation="Set dir on the root element and the embedded runs.",
            retest="Re-run and confirm the base direction matches.")
    else:
        ctx.pass_check()
    if profile["unbalanced_isolates"]:
        ctx.add(rule="bidi-isolate", message="%d unbalanced isolate initiator(s)." % profile["unbalanced_isolates"],
                severity="high", criterion="1.3.1", evidence=profile["controls"],
                remediation="Close every LRI/RLI/FSI with PDI; prefer isolates over legacy embeddings.",
                retest="Re-run and confirm isolates balance.")
    else:
        ctx.pass_check()
    if profile["unbalanced_embeddings"]:
        ctx.add(rule="bidi-embedding", message="%d unbalanced legacy embedding control(s)." % profile["unbalanced_embeddings"],
                severity="medium", criterion="1.3.1", evidence=profile["controls"],
                remediation="Replace LRE/RLE/PDF sequences with isolates.",
                retest="Re-run and confirm embeddings balance.")
    else:
        ctx.pass_check()
    if profile["strong_rtl_chars"] and profile["strong_ltr_chars"]:
        ratio = profile["strong_rtl_chars"] / float(max(profile["strong_rtl_chars"] + profile["strong_ltr_chars"], 1))
        ctx.note("Mixed-direction content detected (%.0f%% RTL characters); verify each embedded run is isolated." % (ratio * 100))
    if re.search(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}\b", text) and profile["base_direction_guess"] == "rtl":
        ctx.note("Numbers and times in RTL text should be wrapped in isolates or LRM to keep digit order.")
    mirrored = [m for m in profile["mirror_candidates"] if m in "()[]{}<>"]
    ctx.meta("bidi", profile)
    ctx.meta("scripts", scripts)
    ctx.meta("mirrored_punctuation_present", bool(mirrored))


@op("rtl-layout-audit", "Audit CSS and markup for RTL readiness: logical properties, physical directions, "
    "direction declarations, icon mirroring and text alignment.",
    params={"icon_allowlist": {"type": "list", "default": None,
                               "doc": "Icon names that must not be mirrored (logos, media controls)."}},
    accepts=("html", "css"))
def run_rtl_layout(ctx):
    doc = ctx.docs[0].data.get("document")
    if doc is None:
        raise S.InputError("RTL layout audit needs HTML or CSS input.",
                           hint="Pass an HTML page with <style> or a CSS file saved as .html.")
    css = "\n".join(H.style_blocks(doc)) + "\n" + doc.text
    physical = {
        "margin-left": "margin-inline-start", "margin-right": "margin-inline-end",
        "padding-left": "padding-inline-start", "padding-right": "padding-inline-end",
        "border-left": "border-inline-start", "border-right": "border-inline-end",
        "left:": "inset-inline-start", "right:": "inset-inline-end",
    }
    hits = 0
    for prop, logical in physical.items():
        for m in re.finditer(r"(?<![\w-])%s\s*:\s*[^;{}]+;" % re.escape(prop.rstrip(":")), css):
            hits += 1
            if hits <= 40:
                ctx.add(rule="rtl-physical-property", message="Physical property %r used; prefer %r." % (
                    prop.rstrip(":"), logical), severity="medium", criterion="rtl-layout",
                    evidence={"snippet": T.truncate(m.group(0), 120)},
                    remediation="Use logical CSS properties so the layout mirrors automatically.",
                    retest="Re-run and confirm no physical directional properties remain.")
            else:
                break
    if hits == 0:
        ctx.pass_check()
    for pattern, message, sev in [
        (r"text-align\s*:\s*(left|right)", "Physical text-align used; use start/end.", "medium"),
        (r"float\s*:\s*(left|right)", "Physical float used; use inline-start/inline-end or flex.", "medium"),
        (r"transform\s*:\s*translateX\(\s*-?\d", "Directional transform may not mirror in RTL.", "low"),
    ]:
        for m in re.finditer(pattern, css, re.I):
            ctx.add(rule="rtl-physical-value", message=message, severity=sev, criterion="rtl-layout",
                    evidence={"snippet": T.truncate(m.group(0), 120)},
                    remediation="Replace with logical equivalents or direction-aware transforms.",
                    retest="Re-run and confirm the pattern is gone.")
    for n in doc.by_tag("img", "svg", "i"):
        name = " ".join(sorted(n.classes)) + " " + (n.get("src") or "") + " " + (n.get("aria-label") or "")
        if re.search(r"(arrow|chevron|back|next|caret)", name, re.I) and not n.get("data-no-mirror"):
            ctx.add(rule="rtl-icon-mirror", message="Directional icon %r should mirror in RTL." % T.truncate(name.strip(), 60),
                    severity="low", criterion="rtl-layout", locator=n.locator(),
                    remediation="Mirror the icon with transform: scaleX(-1) under [dir=\"rtl\"] or supply a mirrored asset.",
                    retest="Re-run and confirm mirroring is recorded.")
    html = doc.by_tag("html")
    if html and not (html[0].get("dir")):
        ctx.add(rule="rtl-root-dir", message="Root element declares no dir attribute.", severity="medium",
                criterion="rtl-layout", locator=html[0].locator(),
                remediation="Declare dir=\"ltr\" or dir=\"rtl\" from the resolved locale.",
                retest="Re-run and confirm the root direction is declared.")
    else:
        ctx.pass_check()
    ctx.meta("physical_property_hits", hits)


@op("script-rendering", "Check complex-script and glyph risks in text: unassigned or private-use characters, "
    "combining marks, Arabic/Hebrew presentation forms, CJK compatibility forms and normalisation state.",
    params={"allow_private_use": {"type": "bool", "default": False, "doc": "Permit private-use characters."},
            "require_nfc": {"type": "bool", "default": True, "doc": "Require NFC normalisation."}},
    accepts=("text", "strings", "json", "html"))
def run_script_rendering(ctx):
    text = _text_of(ctx)
    scripts = T.script_profile(text)
    bad = T.unmapped_chars(text)
    if not ctx.params.get("allow_private_use", False):
        for ch in bad:
            ctx.add(rule="script-private-use", message="Character %s (%s) may render as tofu." % (
                ch["codepoint"], ch["category"]), severity="high", criterion="1.1.1",
                evidence=ch, remediation="Replace the character with a standard codepoint or supply a font that covers it.",
                retest="Re-run and confirm the character is gone or whitelisted.")
        if not bad:
            ctx.pass_check()
    if ctx.params.get("require_nfc", True):
        import unicodedata
        nfc = unicodedata.normalize("NFC", text)
        if nfc != text:
            diffs = sum(1 for a, b in zip(text, nfc) if a != b)
            ctx.add(rule="script-normalisation", message="Text is not NFC-normalised (%d differing positions)." % diffs,
                    severity="medium", criterion="1.1.1", evidence={"codepoints_checked": len(text)},
                    remediation="Normalise content to NFC before publishing.",
                    retest="Re-run and confirm normalisation is stable.")
        else:
            ctx.pass_check()
    marks = T.combining_marks(text)
    if marks:
        ctx.note("%d combining marks present; confirm the font shapes them correctly (conjuncts, vowel signs)." % len(marks))
    if scripts.get("arabic", 0) > 0.05:
        ctx.note("Arabic script present: verify joining forms and that no letters are split across spans.")
    if scripts.get("hebrew", 0) > 0.05:
        ctx.note("Hebrew script present: verify niqqud handling and mixed-direction numbers.")
    ctx.meta("scripts", scripts)
    ctx.meta("combining_marks", len(marks))
    ctx.meta("suspicious_characters", bad[:20])


# --------------------------------------------------------------------------- #
# Category 18: fonts and typography
# --------------------------------------------------------------------------- #
def read_cmap(data):
    """Parse an sfnt/ttf/otf cmap table into a set of codepoints (formats 4 and 12)."""
    if len(data) < 12:
        raise S.InputError("Font file is too small to be a valid sfnt.")
    tag = data[:4]
    if tag == b"wOFF":
        raise S.InputError("WOFF/WOFF2 fonts must be converted to TTF/OTF before coverage analysis.",
                           hint="Convert with an offline tool, or analyse the original TTF/OTF.")
    if tag == b"ttcf":
        raise S.InputError("TrueType Collections are not supported.",
                           hint="Extract a single font from the .ttc first.")
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(num_tables):
        off = 12 + i * 16
        if off + 16 > len(data):
            break
        tag_name = data[off:off + 4]
        table_off, table_len = struct.unpack(">II", data[off + 8:off + 16])
        tables[tag_name] = (table_off, table_len)
    if b"cmap" not in tables:
        raise S.InputError("Font has no cmap table, so glyph coverage cannot be determined.")
    cmap_off = tables[b"cmap"][0]
    num_sub = struct.unpack(">H", data[cmap_off + 2:cmap_off + 4])[0]
    codepoints = set()
    best_format = None
    for i in range(num_sub):
        rec = cmap_off + 4 + i * 8
        platform, encoding, sub_off = struct.unpack(">HHI", data[rec:rec + 8])
        sub = cmap_off + sub_off
        if sub + 2 > len(data):
            continue
        fmt = struct.unpack(">H", data[sub:sub + 2])[0]
        if fmt == 4 and best_format != 12:
            try:
                seq_count = struct.unpack(">H", data[sub + 6:sub + 8])[0] // 2
                seg_x2 = sub + 14
                end_codes = struct.unpack(">%dH" % seq_count, data[seg_x2:seg_x2 + seq_count * 2])
                start_codes = struct.unpack(">%dH" % seq_count,
                                            data[seg_x2 + seq_count * 2 + 2:seg_x2 + seq_count * 4 + 2])
                for start, end in zip(start_codes, end_codes):
                    if start == 0xFFFF:
                        continue
                    for cp in range(start, min(end, 0xFFFE) + 1):
                        codepoints.add(cp)
                best_format = 4
            except struct.error:
                continue
        elif fmt == 12:
            try:
                n_groups = struct.unpack(">I", data[sub + 12:sub + 16])[0]
                base = sub + 16
                codepoints = set()
                for g in range(min(n_groups, 20000)):
                    off = base + g * 12
                    start, end, _gid = struct.unpack(">III", data[off:off + 12])
                    if end - start > 100000:
                        continue
                    for cp in range(start, end + 1):
                        codepoints.add(cp)
                best_format = 12
            except struct.error:
                continue
    if not codepoints:
        raise S.InputError("No codepoints could be read from the font cmap table.")
    return codepoints


@op("font-coverage", "Measure font glyph coverage for target languages and scripts by parsing the font cmap "
    "table, and report missing characters, tofu risk and fallback requirements.",
    params={"locale": {"type": "str", "default": None, "doc": "Locale whose required charset is checked."},
            "text": {"type": "str", "default": None, "doc": "Additional text whose characters must be covered."},
            "charset": {"type": "str", "default": None, "doc": "Extra characters to require."}},
    accepts=("font",))
def run_font_coverage(ctx):
    doc = ctx.docs[0]
    codepoints = read_cmap(doc.data["raw"])
    locale = (ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale") or "fil-PH")
    profile = ctx.assets.get("locale_profiles", {}).get("locales", {}).get(locale, {})
    required_text = (profile.get("required_charset") or "") + (ctx.params.get("charset") or "") + \
                    (profile.get("sample_text") or "")
    if ctx.params.get("text"):
        required_text += ctx.params["text"]
    if len(ctx.docs) > 1:
        required_text += ctx.docs[1].text or ""
    missing = []
    for ch in sorted(set(required_text)):
        if ch in "\n\r\t ":
            continue
        if ord(ch) not in codepoints:
            missing.append({"char": ch, "codepoint": "U+%04X" % ord(ch), "name": unicodedata.name(ch, "?")})
    coverage = round(100.0 * (len(set(required_text)) - len(missing)) / max(len(set(required_text)), 1), 2)
    if missing:
        ctx.add(rule="font-missing-glyphs", message="%d required character(s) are missing from the font for %s." % (
            len(missing), locale), severity="critical", criterion="1.1.1",
            evidence={"missing": missing[:40], "coverage_pct": coverage},
            remediation="Choose a font with the required coverage or add a fallback with the missing ranges.",
            retest="Re-run font-coverage on the updated font stack and confirm coverage is 100%.")
    else:
        ctx.pass_check()
    ctx.meta("glyph_total", len(codepoints))
    ctx.meta("coverage_pct", coverage)
    ctx.meta("missing", missing[:80])
    ctx.meta("locale", locale)
    ctx.meta("font_file", doc.path)


@op("typography-audit", "Audit typography against accessibility thresholds: line height, letter and word spacing, "
    "line length, justification, all-caps runs, minimum size and font-weight contrast.",
    params={"min_line_height": {"type": "float", "default": 1.5,
                                "doc": "Minimum line-height for body text."},
            "max_line_chars": {"type": "int", "default": 80, "doc": "Maximum characters per line."}},
    accepts=("html", "css", "text"))
def run_typography(ctx):
    min_lh = float(ctx.params.get("min_line_height", 1.5))
    max_chars = int(ctx.params.get("max_line_chars", 80))
    doc = ctx.docs[0].data.get("document") if ctx.docs[0].kind != "text" else None
    checked = 0
    if doc is not None:
        for block in H.style_blocks(doc):
            for selectors, decls in H.declarations(block):
                lh = decls.get("line-height")
                if lh:
                    try:
                        val = float(lh.replace("px", "")) / 16.0 if lh.endswith("px") else float(lh.rstrip("%")) / 100.0 if lh.endswith("%") else float(lh)
                    except ValueError:
                        val = None
                    checked += 1
                    if val is not None and val < min_lh and not re.search(r"heading|h[1-6]", selectors, re.I):
                        ctx.add(rule="type-line-height", message="Selector %r sets line-height %.2f (< %.2f)." % (
                            selectors[:60], val, min_lh), severity="medium", criterion="1.4.12",
                            evidence={"selector": selectors[:200], "line_height": val},
                            remediation="Use at least 1.5 line height for body text.",
                            retest="Re-run and confirm the line height passes.")
                    else:
                        ctx.pass_check()
                if "letter-spacing" in decls:
                    try:
                        ls = float(re.sub(r"[^\d.\-]", "", decls["letter-spacing"]))
                    except ValueError:
                        ls = None
                    if ls is not None and ls < 0.12 and "px" in decls["letter-spacing"]:
                        ctx.add(rule="type-letter-spacing", message="Negative or tight letter-spacing in %r may break spacing overrides." % selectors[:60],
                                severity="low", criterion="1.4.12",
                                remediation="Allow at least 0.12em letter spacing without clipping.",
                                retest="Re-run and confirm letter spacing is tolerated.")
                    else:
                        ctx.pass_check()
                if re.search(r"text-align\s*:\s*justify", "text-align: %s" % decls.get("text-align", ""), re.I):
                    ctx.add(rule="type-justified", message="Justified text in %r can create uneven word spacing." % selectors[:60],
                            severity="low", criterion="1.4.8",
                            remediation="Use left/start alignment for body text, or enable hyphenation.",
                            retest="Re-run and confirm justification is removed or hyphenated.")
        text = doc.body_text()
    else:
        text = ctx.docs[0].text
    metrics = T.lexicon_metrics(text)
    long_lines = [l for l in text.splitlines() if len(l) > max_chars]
    if long_lines:
        ctx.add(rule="type-line-length", message="%d line(s) exceed %d characters." % (len(long_lines), max_chars),
                severity="low", criterion="1.4.8",
                evidence={"sample": T.truncate(long_lines[0], 120)},
                remediation="Constrain measure to 45-80 characters for comfortable reading.",
                retest="Re-run and confirm line lengths are within the measure.")
    else:
        ctx.pass_check()
    caps = re.findall(r"\b[A-Z]{6,}\b", text)
    if len(caps) > 2:
        ctx.add(rule="type-all-caps", message="%d all-caps runs found, which slow reading and may be announced letter by letter." % len(caps),
                severity="low", criterion="1.4.8", evidence={"examples": caps[:8]},
                remediation="Use sentence case for body text; reserve all caps for short labels.",
                retest="Re-run and confirm all-caps runs are removed or justified.")
    else:
        ctx.pass_check()
    ctx.meta("metrics", metrics)
    ctx.meta("declarations_checked", checked)


# --------------------------------------------------------------------------- #
# Category 19: pseudolocalization
# --------------------------------------------------------------------------- #
ACCENT_MAP = str.maketrans({
    "a": "\u00e1", "b": "\u0180", "c": "\u00e7", "d": "\u00f0", "e": "\u00e9", "f": "\u0192",
    "g": "\u011d", "h": "\u0125", "i": "\u00ee", "j": "\u0135", "k": "\u0137", "l": "\u013c",
    "m": "\u1e3f", "n": "\u00f1", "o": "\u00f4", "p": "\u00fe", "q": "\u01eb", "r": "\u0155",
    "s": "\u0161", "t": "\u0163", "u": "\u00fb", "v": "\u1e7d", "w": "\u0175", "x": "\u1e8b",
    "y": "\u00fd", "z": "\u017e",
    "A": "\u00c1", "B": "\u0181", "C": "\u00c7", "D": "\u00d0", "E": "\u00c9", "F": "\u0191",
    "G": "\u011c", "H": "\u0124", "I": "\u00ce", "J": "\u0134", "K": "\u0136", "L": "\u013b",
    "M": "\u1e3e", "N": "\u00d1", "O": "\u00d4", "P": "\u00de", "Q": "\u01ea", "R": "\u0154",
    "S": "\u0160", "T": "\u0162", "U": "\u00db", "V": "\u1e7c", "W": "\u0174", "X": "\u1e8a",
    "Y": "\u00dd", "Z": "\u017d",
})
PROTECT = re.compile(r"(\{[^{}]*\}|%(?:\d+\$)?[sdifg@]|\{\d+\}|</?[A-Za-z][^>]*>|<[^>]+>|\$\{[^}]+\}|\\n)")


def pseudolocalize(text, mode="accented", expansion=1.35):
    text = text if isinstance(text, str) else ""
    parts = PROTECT.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
            continue
        seg = part.translate(ACCENT_MAP) if mode in ("accented", "expanded", "bidi") else part
        if mode in ("expanded", "accented"):
            pad = max(0, int(round(len(seg) * (expansion - 1.0))))
            if pad and seg.strip():
                seg = seg + "\u00b7" * pad
        if mode in ("bidi", "mirrored"):
            seg = "\u202b" + seg + "\u202c"
        if mode == "mirrored":
            seg = seg[::-1] if seg.strip() else seg
        out.append(seg)
    body = "".join(out)
    if mode in ("accented", "expanded", "bidi", "mirrored"):
        body = "[\u00df" + body + "\u00df]"
    return body


@op("pseudolocalize", "Generate pseudolocalized resource bundles (accented, expanded, bidi, mirrored) to expose "
    "hard-coded strings, truncation and layout failures before real translation.",
    params={"mode": {"type": "enum", "values": ["accented", "expanded", "bidi", "mirrored"], "default": "accented",
                     "doc": "Pseudolocale variant to generate."},
            "expansion": {"type": "float", "default": 1.35, "doc": "Target length multiplier (1.0-3.0)."},
            "output_path": {"type": "str", "default": None,
                            "doc": "Optional path to write the generated bundle (JSON)."},
            "require_approval_to_write": {"type": "bool", "default": True, "doc": "Internal guard flag."}},
    accepts=("strings",))
def run_pseudolocalize(ctx):
    strings, kind = _strings(ctx)
    mode = ctx.params.get("mode", "accented")
    expansion = float(ctx.params.get("expansion", 1.35))
    if not 1.0 <= expansion <= 3.0:
        raise S.ParamError("expansion must be between 1.0 and 3.0.", hint="Typical budgets are 1.3-1.5.")
    generated = {}
    changed = 0
    for key, value in strings.items():
        new = pseudolocalize(value, mode, expansion)
        generated[key] = new
        if new != value:
            changed += 1
    out_path = ctx.params.get("output_path")
    written = None
    if out_path:
        if ctx.params.get("require_approval_to_write", True) and not ctx.params.get("_approved"):
            ctx.note("output_path provided; writing requires --approve (state-changing action).", "warn")
        else:
            written = S.atomic_write(out_path, json.dumps(generated, indent=2, ensure_ascii=False) + "\n")
    ctx.meta("mode", mode)
    ctx.meta("expansion", expansion)
    ctx.meta("keys", len(strings))
    ctx.meta("changed", changed)
    ctx.meta("output_path", written)
    ctx.meta("sample", {k: generated[k] for k in list(generated)[:8]})
    if changed == 0:
        ctx.add(rule="pseudo-no-change", message="No string changed under pseudolocalization.", severity="high",
                criterion="pseudolocalization", remediation="Verify the bundle contains translatable strings and that the filter is not excluding them.",
                retest="Re-run and confirm generated strings differ from source.")
    else:
        ctx.pass_check()
    ctx.note("Pseudolocale text passes through placeholders and markup unchanged by design.")


@op("pseudo-audit", "Audit pseudolocalized output for untranslated strings, placeholder corruption, markup damage, "
    "unbracketed strings and expansion that exceeds the configured budget.",
    params={"budget_pct": {"type": "float", "default": 40.0, "doc": "Allowed expansion over source length."},
            "source_path": {"type": "str", "default": None, "doc": "Source bundle for comparison."}},
    accepts=("strings", "json"))
def run_pseudo_audit(ctx):
    if ctx.docs[0].kind == "strings":
        pseudo, _ = load_strings(ctx.docs[0].path, ctx.docs[0].text)
    else:
        data = ctx.docs[0].data
        pseudo = _flatten(data) if isinstance(data, (dict, list)) else {}
    source_path = ctx.params.get("source_path")
    if not source_path:
        raise S.ParamError("A source bundle is required to compare against.",
                           hint="Pass --reference <source.json> or --set source_path=<file>.")
    source, _ = load_strings(source_path, S.read_text(source_path, limits=ctx.limits))
    budget = float(ctx.params.get("budget_pct", 40.0))
    missing = corrupted = unbracketed = 0
    for key, src in source.items():
        ctx.checkpoint()
        tgt = pseudo.get(key)
        if tgt is None:
            missing += 1
            ctx.add(rule="pseudo-missing-key", message="Key %r is absent from the pseudolocale bundle." % key,
                    severity="high", criterion="pseudolocalization", locator=key,
                    remediation="Regenerate the pseudolocale from the current source bundle.",
                    retest="Re-run and confirm the key exists.")
            continue
        if tgt == src and src.strip():
            ctx.add(rule="pseudo-untranslated", message="Key %r is identical to the source, suggesting a hard-coded string." % key,
                    severity="high", criterion="pseudolocalization", locator=key,
                    evidence={"source": src, "pseudo": tgt},
                    remediation="Check whether the value bypasses the resource bundle.",
                    retest="Re-run and confirm the string is pseudolocalized.")
            continue
        m_src, x_src = T.placeholder_set(src), T.placeholder_set(tgt)
        if m_src != x_src:
            corrupted += 1
            ctx.add(rule="pseudo-placeholder-corrupt", message="Key %r placeholder set changed (%s -> %s)." % (
                key, sorted(m_src), sorted(x_src)), severity="critical", criterion="pseudolocalization",
                locator=key, evidence={"source": src, "pseudo": tgt},
                remediation="Fix the pseudolocalizer protection list; placeholders and tags must survive.",
                retest="Re-run and confirm placeholder sets match.")
            continue
        if src.strip() and "\u00df" not in tgt:
            unbracketed += 1
            ctx.add(rule="pseudo-unbracketed", message="Key %r is not bracketed, so clipping may go unnoticed." % key,
                    severity="low", criterion="pseudolocalization", locator=key,
                    remediation="Ensure the pseudolocale wraps every visible string in boundary markers.",
                    retest="Re-run and confirm the string is bracketed.")
            continue
        growth = 100.0 * (len(tgt) - len(src)) / max(len(src), 1)
        if growth > budget:
            ctx.add(rule="pseudo-over-budget", message="Key %r grew %.1f%% (> %.1f%% budget)." % (key, growth, budget),
                    severity="medium", criterion="pseudolocalization", locator=key,
                    evidence={"growth_pct": round(growth, 1)},
                    remediation="Shorten the source or make the container flexible.",
                    retest="Re-run and confirm growth is within budget.")
        else:
            ctx.pass_check()
    ctx.meta("source_keys", len(source))
    ctx.meta("pseudo_keys", len(pseudo))
    ctx.meta("missing_keys", missing)
    ctx.meta("placeholder_corruption", corrupted)
    ctx.meta("unbracketed", unbracketed)


# --------------------------------------------------------------------------- #
# Category 18 (continued): font licensing and embedding permissions
# --------------------------------------------------------------------------- #
def read_font_metadata(data):
    """Read the sfnt name table and OS/2 embedding permissions."""
    if len(data) < 12:
        raise S.InputError("Font file is too small to be a valid sfnt.")
    if data[:4] == b"wOFF":
        raise S.InputError("WOFF/WOFF2 fonts must be converted to TTF/OTF before licence analysis.",
                           hint="Convert with an offline tool, then re-run.")
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(num_tables):
        off = 12 + i * 16
        if off + 16 > len(data):
            break
        tag = data[off:off + 4]
        table_off, table_len = struct.unpack(">II", data[off + 8:off + 16])
        tables[tag] = (table_off, table_len)
    meta = {"tables": sorted(t.decode("latin-1", "replace") for t in tables), "names": {},
            "fs_type": None, "embedding": None, "license_url": None, "license_text": None,
            "copyright": None, "family": None, "subfamily": None}
    if b"name" in tables:
        off = tables[b"name"][0]
        try:
            fmt, count, str_off = struct.unpack(">HHH", data[off:off + 6])
            for i in range(count):
                rec = off + 6 + i * 12
                pid, eid, lid, nid, length, so = struct.unpack(">HHHHHH", data[rec:rec + 12])
                raw = data[off + str_off + so: off + str_off + so + length]
                if pid == 3:
                    text = raw.decode("utf-16-be", "replace")
                elif pid in (1, 0):
                    text = raw.decode("latin-1", "replace")
                else:
                    continue
                if nid not in meta["names"]:
                    meta["names"][nid] = text.strip("\x00").strip()
        except struct.error:
            pass
        meta["copyright"] = meta["names"].get(0)
        meta["family"] = meta["names"].get(1)
        meta["subfamily"] = meta["names"].get(2)
        meta["license_text"] = meta["names"].get(13)
        meta["license_url"] = meta["names"].get(14)
    if b"OS/2" in tables:
        off = tables[b"OS/2"][0]
        try:
            meta["fs_type"] = struct.unpack(">H", data[off + 8:off + 10])[0]
        except struct.error:
            meta["fs_type"] = None
    fs = meta["fs_type"]
    if fs is None:
        meta["embedding"] = "unknown"
    elif fs & 0x0002:
        meta["embedding"] = "restricted"
    elif fs & 0x0008:
        meta["embedding"] = "editable"
    elif fs & 0x0004:
        meta["embedding"] = "preview-and-print"
    elif fs & 0x0100:
        meta["embedding"] = "no-subsetting"
    else:
        meta["embedding"] = "installable"
    return meta


@op("font-license", "Check font licence and embedding permissions from the font binary (name table and OS/2 "
    "fsType), so webfont, PDF and Office embedding decisions are evidence-based.",
    params={"intended_use": {"type": "enum", "values": ["web", "pdf", "office", "app", "print"],
                             "default": "web", "doc": "Where the font will be embedded."},
            "allow_restricted": {"type": "bool", "default": False,
                                 "doc": "Permit fonts flagged as restricted embedding."}},
    accepts=("font",))
def run_font_license(ctx):
    data = ctx.docs[0].data["raw"]
    meta = read_font_metadata(data)
    use = ctx.params.get("intended_use", "web")
    if not meta.get("license_text") and not meta.get("license_url"):
        ctx.add(rule="font-license-missing", message="Font carries no licence description (nameID 13/14).",
                severity="medium", criterion="font-licensing", evidence=meta,
                remediation="Obtain the licence terms from the foundry and record them in the font inventory.",
                retest="Re-run font-license once the licence metadata or inventory record exists.")
    else:
        ctx.pass_check()
    if meta["embedding"] == "restricted" and not ctx.params.get("allow_restricted"):
        ctx.add(rule="font-embedding-restricted", message="Font forbids embedding (fsType bit 1 set) for %s use." % use,
                severity="critical", criterion="font-licensing",
                evidence={"fs_type": meta["fs_type"], "embedding": meta["embedding"]},
                remediation="Choose a font whose licence permits %s embedding, or obtain a licence upgrade." % use,
                retest="Re-run font-license on the replacement font and confirm embedding is permitted.")
    else:
        ctx.pass_check()
    if meta["embedding"] == "no-subsetting" and use in ("web", "pdf"):
        ctx.add(rule="font-no-subsetting", message="Font forbids subsetting, which blocks efficient %s embedding." % use,
                severity="high", criterion="font-licensing",
                remediation="Use the full font file or pick a licence that allows subsetting.",
                retest="Re-run and confirm subsetting is permitted.")
    else:
        ctx.pass_check()
    if meta["embedding"] == "preview-and-print" and use in ("app", "office"):
        ctx.add(rule="font-preview-only", message="Font permits preview-and-print embedding only; %s use may breach it." % use,
                severity="high", criterion="font-licensing",
                remediation="Confirm the licence in writing before shipping in an application or Office document.",
                retest="Re-run with the licence decision recorded.")
    else:
        ctx.pass_check()
    ctx.meta("font_metadata", meta)
    ctx.meta("intended_use", use)
    if not meta.get("family"):
        ctx.note("Font family name could not be read from the name table; the file may be damaged or obfuscated.")
