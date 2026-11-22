"""Safety, accessibility, integrity and provenance capabilities (23 handlers).

Every destructive or security-sensitive action is gated through core.gate() so
the CLI refuses to run it unless --approve or --dry-run semantics apply.
Detection is heuristic and each result carries confidence plus the method used.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

EXPORTS = ["find_pii", "find_secrets", "find_injection", "fingerprint", "normalization_report"]
LEX_ASSET = "asset-lexicons-language.json"
SEC_ASSET = "asset-patterns-security.json"
UNI_ASSET = "asset-unicode-tables.json"


def _sec(ctx):
    return ctx.assets.json(SEC_ASSET)


def _lex(ctx):
    return ctx.assets.json(LEX_ASSET)


def _scan(text, tables, group):
    hits = []
    for name, spec in tables[group].items():
        for match in re.finditer(spec["pattern"], text):
            hits.append({"type": name, "text": match.group(0), "start": match.start(),
                         "end": match.end(),
                         "confidence": spec.get("confidence", 0.6),
                         "severity": spec.get("severity", "high" if group == "secrets" else "medium"),
                         "replacement": spec.get("replacement", "[%s]" % name.upper())})
    hits.sort(key=lambda h: h["start"])
    return hits


def _luhn_ok(digits):
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def find_pii(text, tables, kinds=None):
    hits = _scan(text, tables, "pii")
    hits = [h for h in hits if h["type"] != "credit_card"
            or _luhn_ok(re.sub(r"\D", "", h["text"])) or len(re.sub(r"\D", "", h["text"])) < 13]
    if kinds:
        hits = [h for h in hits if h["type"] in kinds]
    return hits


def find_secrets(text, tables):
    return _scan(text, tables, "secrets")


def find_injection(text, tables):
    return _scan(text, tables, "injection")


def fingerprint(text, algorithm="sha256"):
    data = text.encode("utf-8")
    if algorithm == "md5":
        digest = hashlib.md5(data).hexdigest()
    elif algorithm == "sha1":
        digest = hashlib.sha1(data).hexdigest()
    else:
        digest = hashlib.sha256(data).hexdigest()
    return digest


def normalization_report(text):
    return {
        "length": len(text),
        "is_nfc": unicodedata.is_normalized("NFC", text),
        "is_nfd": unicodedata.is_normalized("NFD", text),
        "is_nfkc": unicodedata.is_normalized("NFKC", text),
        "nfc_equal": unicodedata.normalize("NFC", text) == text,
        "non_ascii": sum(1 for c in text if ord(c) > 127),
    }


# --------------------------------------------------------------------------
# safety / accessibility
# --------------------------------------------------------------------------


def accessible_language(ctx):
    text = ctx.require_text(minimum=10)
    level = ctx.params["level"]
    lex = _lex(ctx)
    target = {"plain": 8.0, "general": 10.0, "technical": 14.0}[level]
    out = text
    replaced = []
    jargon = lex["jargon"]
    for source, plain in jargon.items():
        pattern = re.compile(r"\b%s\b" % re.escape(source), re.I)
        if pattern.search(out):
            out = H.replace_preserving_case(source, plain, out)
            replaced.append({"original": source, "plain": plain})
    for source, expanded in lex["abbreviations_tts"].items():
        pattern = re.compile(r"\b%s\b" % re.escape(source))
        if pattern.search(out):
            out = pattern.sub(expanded, out, count=int(ctx.params["max_changes"]))
            replaced.append({"original": source, "plain": expanded})
    sentences = []
    for sentence in core.sentence_split(out):
        sentences.append(sentence.strip())
    split_long = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) > 25:
            midpoint = len(words) // 2
            split_long.append(" ".join(words[:midpoint]).rstrip(",") + ".")
            split_long.append(" ".join(words[midpoint:]).capitalize())
        else:
            split_long.append(sentence)
    out = " ".join(split_long)
    if ctx.params["expand_numbers"]:
        out = re.sub(r"\b(\d+)%\b", r"\1 percent", out)
    metrics = H.reading_metrics(out)
    data = {"level": level, "target_grade": target, "replacements": replaced,
            "grade_before": H.reading_metrics(text).get("fk_grade"),
            "grade_after": metrics.get("fk_grade"),
            "long_sentences_split": sum(1 for s in sentences if len(s.split()) > 25)}
    warnings = []
    if metrics.get("fk_grade") and metrics["fk_grade"] > target:
        warnings.append("grade level %.1f still above the %s target of %.1f"
                        % (metrics["fk_grade"], level, target))
    return {"output": out, "data": data, "warnings": warnings}


def alt_text_writer(ctx):
    text = ctx.require_text(minimum=1)
    style = ctx.params["style"]
    max_chars = int(ctx.params["max_chars"])
    kind = ctx.params["kind"]
    body = text.strip()
    if kind == "chart":
        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", body)
        first = H.summarize_extractive(body, max_sentences=1)
        subject = first[0] if first else body
        subject = re.sub(r"(?i)^\s*(bar|line|pie|column|scatter)?\s*(chart|graph|plot)\s*[:,-]?\s*", "", subject)
        subject = subject.strip().rstrip(".")
        summary = "Chart: %s. Key values: %s." % (
            subject if subject else "the supplied data",
            ", ".join(numbers[:5]) or "none detected")
    elif kind == "art":
        ascii_count = sum(1 for c in body if c in "#@%*+=~-_.|/\\")
        summary = "Decorative text art (%d drawing characters). %s" % (
            ascii_count, "Use it only if it is purely decorative." if ascii_count > 10 else "")
    elif kind == "ui":
        summary = "Control: %s" % body.split("\n")[0][:80]
    else:
        summary = H.summarize_extractive(body, max_sentences=2)
        summary = " ".join(summary) if summary else body[:120]
    if style == "short":
        summary = core.truncate(summary, min(max_chars, 100))
    elif style == "detailed":
        summary = summary + " Context: " + core.truncate(body, 300)
    summary = re.sub(r"\.{2,}", ".", summary).replace("..", ".").strip()
    summary = core.truncate(summary, max_chars)
    data = {"alt_text": summary, "chars": len(summary), "kind": kind, "style": style,
            "limit": max_chars}
    warnings = []
    if len(summary) > 125:
        warnings.append("alt text longer than 125 characters: consider a short alt plus a longer description")
    if re.search(r"\b(image|picture|graphic) of\b", summary, re.I):
        warnings.append("alt text starts by naming the medium; screen readers already announce it")
    return {"output": summary, "data": data, "warnings": warnings}


def inclusive_language(ctx):
    text = ctx.require_text(minimum=5)
    lex = _lex(ctx)
    severity_floor = ctx.params["severity"]
    order = {"low": 0, "medium": 1, "high": 2}
    floor = order.get(severity_floor, 0)
    findings = []
    for entry in lex["inclusive_terms"]:
        if isinstance(entry, dict):
            term = entry.get("term") or entry.get("flagged")
            preferred = entry.get("prefer") or entry.get("preferred") or []
            better = preferred[0] if isinstance(preferred, list) and preferred else (preferred or "")
            categories = entry.get("categories") or []
            severity = entry.get("severity") or ("high" if "disability" in categories else "medium")
        else:
            term, better, severity = entry, "", "medium"
        if order.get(severity, 1) < floor:
            continue
        for match in re.finditer(r"\b%s\b" % re.escape(term), text, re.I):
            findings.append({"term": term, "prefer": better, "severity": severity,
                             "offset": match.start(),
                             "context": text[max(0, match.start() - 40):match.end() + 40].replace("\n", " ")})
    for entry in lex["blaming_phrases"]:
        if isinstance(entry, dict):
            term = entry.get("term") or entry.get("flagged")
            better = entry.get("prefer") or ""
        else:
            term, better = entry, ""
        for match in re.finditer(r"\b%s\b" % re.escape(term), text, re.I):
            findings.append({"term": term, "prefer": better, "severity": "medium",
                             "offset": match.start(), "context": "blaming language"})
    findings.sort(key=lambda f: f["offset"])
    out = text
    if ctx.params["apply"] and findings:
        ctx.gate("rewrite document terminology")
        for finding in sorted(findings, key=lambda f: -f["offset"]):
            if not finding["prefer"]:
                continue
            start = finding["offset"]
            end = start + len(finding["term"])
            out = out[:start] + H.replace_preserving_case(finding["term"], finding["prefer"],
                                                          out[start:end]) + out[end:]
    rows = [[f["term"], f["prefer"] or "-", f["severity"], f["context"][:50]] for f in findings]
    output = (core.table(rows, ["term", "suggested replacement", "severity", "context"])
              if rows else "no flagged terminology found")
    if ctx.params["apply"] and findings:
        output = out + "\n\n" + output
    return {"output": output, "data": {"findings": findings, "count": len(findings),
                                       "applied": bool(ctx.params["apply"])},
            "warnings": ["%d term(s) need a human decision; suggestions are not automatic replacements"
                         % len(findings)] if findings else []}


def trauma_informed(ctx):
    text = ctx.require_text(minimum=10)
    lex = _lex(ctx)
    audience = ctx.params["audience"]
    findings = []
    charged = lex["charged_words"]
    for name, better in (charged.items() if isinstance(charged, dict) else [(c, "") for c in charged]):
        for match in re.finditer(r"\b%s\b" % re.escape(name), text, re.I):
            findings.append({"kind": "charged-word", "text": name, "offset": match.start(),
                             "advice": "prefer %r and allow the reader to skip" % better if better
                             else "offer a neutral alternative and allow the reader to skip"})
    for match in re.finditer(r"\b(you|your)\b", text, re.I):
        window = text[max(0, match.start() - 60):match.end() + 60]
        if re.search(r"\b(failed|fault|should have|why didn't|blame)\b", window, re.I):
            findings.append({"kind": "second-person-blame", "text": window.strip()[:80],
                             "offset": match.start(), "advice": "move from 'you' to 'the situation'"})
    for match in re.finditer(r"\b(must|never|always)\b", text, re.I):
        findings.append({"kind": "absolute-demand", "text": match.group(0),
                         "offset": match.start(), "advice": "absolutes raise pressure; use 'please' or describe the step"})
    out = text
    for term, better in (("victim", "person who experienced"), ("suffered from", "is living with")):
        out = re.sub(r"\b%s\b" % re.escape(term), better, out, flags=re.I)
    if audience == "minors":
        out = re.sub(r"\b(kill|die|dead)\b", "was hurt", out, flags=re.I)
    rows = [[f["kind"], f["text"][:50], f["advice"]] for f in findings[:40]]
    data = {"findings": findings, "count": len(findings), "audience": audience}
    output = out
    if rows:
        output += "\n\n### Review\n" + core.table(rows, ["kind", "text", "advice"])
    return {"output": output, "data": data,
            "warnings": ["trauma-informed review needs a human editor with subject expertise"]}


def pii_redactor(ctx):
    text = ctx.require_text(minimum=5)
    tables = _sec(ctx)
    mode = ctx.params["mode"]
    kinds = [k.strip() for k in ctx.params["types"].split(",") if k.strip() and k.strip() != "all"]
    hits = find_pii(text, tables, kinds or None)
    ctx.gate("redact detected personal data", {"entities": len(hits)})
    out = text
    if mode == "mask":
        for hit in sorted(hits, key=lambda h: -h["start"]):
            keep = max(0, len(hit["text"]) - 4)
            out = out[:hit["start"]] + hit["text"][:keep] + "*" * (len(hit["text"]) - keep) + out[hit["end"]:]
    elif mode == "hash":
        for hit in sorted(hits, key=lambda h: -h["start"]):
            token = "<%s:%s>" % (hit["type"], fingerprint(hit["text"])[:10])
            out = out[:hit["start"]] + token + out[hit["end"]:]
    else:
        for hit in sorted(hits, key=lambda h: -h["start"]):
            label = "<%s>" % hit["replacement"].strip("[]") if mode == "token" else hit["replacement"]
            out = out[:hit["start"]] + label + out[hit["end"]:]
    rows = [[h["type"], h["confidence"], h["start"]] for h in hits[:40]]
    data = {"mode": mode, "entities": len(hits), "by_type": {},
            "residual_check": find_pii(out, tables, kinds or None)}
    for hit in hits:
        data["by_type"][hit["type"]] = data["by_type"].get(hit["type"], 0) + 1
    warnings = []
    if data["residual_check"]:
        warnings.append("%d pattern(s) still match after redaction; review the output"
                        % len(data["residual_check"]))
    if not ctx.approve:
        warnings.append("approval was granted through the gate; the original text is unchanged on disk")
    if rows:
        out += "\n\n<!-- redaction summary -->\n" + core.table(rows, ["type", "confidence", "offset"])
    return {"output": out, "data": data, "warnings": warnings}


def secret_scanner(ctx):
    text = ctx.require_text(minimum=5)
    tables = _sec(ctx)
    hits = find_secrets(text, tables)
    ctx.gate("scan for credentials and report findings", {"findings": len(hits)})
    redacted = text
    for hit in sorted(hits, key=lambda h: -h["start"]):
        redacted = redacted[:hit["start"]] + "[REDACTED:%s]" % hit["type"].upper() + redacted[hit["end"]:]
    rows = [[h["type"], h["severity"], h["confidence"], h["start"]] for h in hits]
    data = {"findings": hits, "count": len(hits),
            "severity": ("critical" if any(h["severity"] == "critical" for h in hits)
                         else "high" if hits else "none"),
            "redacted_preview": redacted[:400] if hits else ""}
    if ctx.params["format"] == "json":
        return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data,
                "warnings": ["rotate any exposed credential; scanning cannot prove a secret is absent"] if hits else []}
    output = core.table(rows, ["type", "severity", "confidence", "offset"]) if rows else "no credential patterns matched"
    if hits:
        output += "\n\n" + redacted
    return {"output": output, "data": data,
            "warnings": ["rotate any exposed credential; pattern scanning cannot prove a secret is absent"] if hits else []}


def content_warning(ctx):
    text = ctx.require_text(minimum=5)
    lex = _lex(ctx)
    style = ctx.params["style"]
    topics = []
    for topic, cues in lex["sensitive_topics"].items():
        if any(re.search(r"\b%s\b" % re.escape(cue), text, re.I) for cue in cues):
            topics.append(topic)
    if not topics:
        topics = ["none detected"]
    if style == "brief":
        output = "Content note: %s." % ", ".join(topics)
    elif style == "detailed":
        output = ("Content note\n\nThis text discusses: %s.\n\n"
                  "You can skip the sections below without losing the main thread. "
                  "Support resources are available if any of this is difficult to read."
                  % ", ".join(topics))
    else:
        output = "Content note: this text discusses %s." % ", ".join(topics)
    hint = ctx.params["placement"]
    if hint == "top":
        output = output + "\n\n(Place this note before the title.)"
    data = {"topics": topics, "style": style, "placement": hint}
    return {"output": output, "data": data}


def placeholder_generator(ctx):
    text = ctx.require_text(minimum=5)
    tables = _sec(ctx)
    style = ctx.params["style"]
    kinds = [k.strip() for k in ctx.params["types"].split(",") if k.strip()]
    hits = find_pii(text, tables, kinds or None)
    counter = {}
    out = text
    mapping = []
    for hit in sorted(hits, key=lambda h: -h["start"]):
        counter[hit["type"]] = counter.get(hit["type"], 0) + 1
        if style == "numbered":
            token = "[%s_%d]" % (hit["type"].upper(), counter[hit["type"]])
        elif style == "friendly":
            token = {"email": "[contact email]", "phone_intl": "[contact number]",
                     "credit_card": "[payment card]", "ipv4": "[server address]"}.get(
                         hit["type"], "[%s]" % hit["type"].replace("_", " "))
        else:
            token = "[%s]" % hit["type"].upper()
        out = out[:hit["start"]] + token + out[hit["end"]:]
        mapping.append({"placeholder": token, "type": hit["type"], "original_offset": hit["start"]})
    data = {"placeholders": mapping, "count": len(mapping), "style": style,
            "schedule": "map kept in memory only; no original value is written to the output"}
    output = out
    if mapping:
        output += "\n\n### Placeholder map\n" + core.table([[m["placeholder"], m["type"]] for m in mapping],
                                                           ["placeholder", "type"])
    return {"output": output, "data": data,
            "warnings": ["store the placeholder map separately and delete it when no longer needed"] if mapping else []}


def high_stakes_disclaimer(ctx):
    text = ctx.require_text(minimum=5)
    domain = ctx.params["domain"]
    action = ctx.params["action"]
    placement = ctx.params["placement"]
    blocks = {
        "medical": ("This is general information, not medical advice. Speak to a qualified clinician "
                    "about your situation; do not delay care because of anything written here."),
        "legal": ("This is general information, not legal advice. Laws differ by jurisdiction and change; "
                  "have a qualified lawyer review anything you act on."),
        "financial": ("This is general information, not financial advice. Markets and tax rules change; "
                      "confirm with a licensed professional before acting."),
        "safety": ("This is general information. For an emergency, contact local emergency services "
                   "immediately rather than relying on this text."),
    }
    block = blocks.get(domain, "This text is general information and may be out of date; verify before relying on it.")
    if action == "embed":
        output = (block + "\n\n" + text) if placement == "top" else (text + "\n\n" + block)
    elif action == "insert":
        output = text + "\n\n---\n" + block
    else:
        output = block
    data = {"domain": domain, "action": action, "placement": placement,
            "verified": False, "length": len(block)}
    return {"output": output, "data": data,
            "warnings": ["a disclaimer does not make unverified content safe; sources still need checking"]}


def bias_dataset_reviewer(ctx):
    text = ctx.require_text(minimum=20)
    lex = _lex(ctx)
    dimension = ctx.params["dimension"]
    lines = [ln.strip() for ln in core.canonical_newlines(text).split("\n") if ln.strip()]
    groups = {}
    dem = lex["demographics"]
    for line in lines:
        lowered = line.lower()
        matched = [term for terms in dem.values() for term in terms if re.search(r"\b%s\b" % re.escape(term), lowered)]
        key = matched[0] if matched else "unlabelled"
        groups.setdefault(key, []).append(line)
    counts = {k: len(v) for k, v in groups.items()}
    total = max(1, len(lines))
    skew = {k: round(v / total, 3) for k, v in counts.items()}
    rare = [k for k, v in counts.items() if v / total < 0.1]
    findings = []
    charged = lex["charged_words"]
    charged_names = list(charged.keys()) if isinstance(charged, dict) else list(charged)
    for group, sample in groups.items():
        joined = " ".join(sample).lower()
        for name in charged_names:
            if re.search(r"\b%s\b" % re.escape(name), joined):
                findings.append({"group": group, "issue": "charged terminology",
                                 "term": name, "sample": core.truncate(sample[0], 80)})
    data = {"dimension": dimension, "counts": counts, "share": skew,
            "under_represented": rare, "findings": findings, "rows": len(lines)}
    out = ["# Dataset representation report (%s)" % dimension, ""]
    out.append(core.table([[k, v, "%.0f%%" % (skew[k] * 100)] for k, v in counts.items()],
                          ["group", "rows", "share"]))
    if rare:
        out.append("")
        out.append("Under-represented (below 10%): %s" % ", ".join(rare))
    if findings:
        out.append("")
        out.append(core.table([[f["group"], f["issue"], f["term"]] for f in findings[:20]],
                              ["group", "issue", "term"]))
    return {"output": "\n".join(out), "data": data,
            "warnings": ["tagging by keyword cannot establish real representation; audit the source of the data"]}


def read_aloud_optimizer(ctx):
    text = ctx.require_text(minimum=10)
    lex = _lex(ctx)
    mode = ctx.params["mode"]
    out = text
    out = re.sub(r"\b(e\.g|i\.e|etc|vs|approx)\.", lambda m: {"e.g": "for example", "i.e": "that is",
                                                             "etc": "and so on", "vs": "versus",
                                                             "approx": "about"}[m.group(1)], out)
    out = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: "%s, %s %s" % (m.group(3), m.group(2), m.group(1)), out)
    out = out.replace("&", " and ").replace("%", " percent")
    for symbol, spoken in lex["tts_units"].items():
        out = out.replace(symbol, " %s " % spoken)
    for word, spoken in lex["abbreviations_tts"].items():
        out = re.sub(r"\b%s\b" % re.escape(word), spoken, out)
    out = re.sub(r"\s{2,}", " ", out)
    sentences = [s.strip() for s in core.sentence_split(out) if s.strip()]
    if mode == "ssml-prep":
        out = "\n".join("<s>%s</s>" % s for s in sentences)
    elif mode == "phonetic-hints":
        out = "\n".join("- %s" % s for s in sentences[:20])
    numbers = len(re.findall(r"\d", text))
    data = {"mode": mode, "sentences": len(sentences), "numbers_spelled": numbers,
            "estimated_seconds": round(len(out.split()) / 2.6, 1)}
    return {"output": out, "data": data,
            "warnings": ["synthesis still needs a listening pass: homographs and acronyms may be wrong"]}


# --------------------------------------------------------------------------
# integrity / provenance
# --------------------------------------------------------------------------


def encoding_forensics(ctx):
    text = ctx.require_text(minimum=1)
    raw = text.encode("utf-8", "surrogatepass")
    report = normalization_report(text)
    mojibake = []
    for pattern, example in [("\u00e2\u20ac", "cp1252-misread UTF-8 punctuation"),
                             ("\u00c3", "cp1252-misread UTF-8 latin-1 range"),
                             ("\u00ef\u00bf\u00bd", "replacement character sequence")]:
        if pattern in text:
            mojibake.append({"pattern": pattern.encode("unicode_escape").decode(), "likely": example})
    controls = [{"code": "U+%04X" % ord(c), "count": text.count(c), "name": unicodedata.name(c, "?")}
                for c in sorted(set(text)) if unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs")
                and c not in "\n\r\t"]
    data = {"bytes_utf8": len(raw), "normalization": report, "mojibake_signals": mojibake,
            "control_characters": controls,
            "line_endings": {"crlf": text.count("\r\n"), "lf": text.count("\n"),
                             "cr_only": text.count("\r") - text.count("\r\n")},
            "ending": "utf-8-bom" if text.startswith("\ufeff") else "utf-8"}
    out = ["# Encoding forensics", ""]
    out.append("- utf-8 bytes: %d | characters: %d" % (len(raw), len(text)))
    out.append("- NFC: %s | NFD: %s | NFKC: %s" % (report["is_nfc"], report["is_nfd"], report["is_nfkc"]))
    out.append("- non-ASCII characters: %d" % report["non_ascii"])
    if mojibake:
        out.append("- mojibake signals: %s" % ", ".join(m["pattern"] for m in mojibake))
    if controls:
        out.append("")
        out.append(core.table([[c["code"], c["name"], c["count"]] for c in controls],
                              ["code point", "name", "count"]))
    return {"output": "\n".join(out), "data": data,
            "warnings": ["%d control/invisible character type(s) present" % len(controls)] if controls else []}


def corruption_repair(ctx):
    text = ctx.require_text(minimum=1)
    tables = _sec(ctx)
    dry_run = bool(ctx.params["dry_run"])
    chain = tables["recovery"]["mojibake_chain"]
    steps = []
    out = text
    if "\ufffd" in out:
        steps.append({"step": "replacement-character-report",
                      "detail": "%d U+FFFD characters: data was already lost at encode time" % out.count("\ufffd")})
    for target in chain[1:]:
        try:
            candidate = out.encode("latin-1", "strict").decode("utf-8", "strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate != out:
            steps.append({"step": "mojibake-repair", "detail": "decoded via %s -> utf-8" % target})
            try:
                candidate = candidate.encode("latin-1").decode("utf-8")
                steps.append({"step": "mojibake-repair-2", "detail": "second pass via %s" % target})
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            out = candidate
        break
    out = core.canonical_newlines(out)
    steps.append({"step": "newline-normalisation", "detail": "CRLF/CR folded to LF"})
    if not unicodedata.is_normalized("NFC", out):
        out = unicodedata.normalize("NFC", out)
        steps.append({"step": "unicode-NFC", "detail": "composed to NFC"})
    if not dry_run:
        ctx.gate("write a repaired copy of possibly damaged text", {"steps": len(steps)})
    removed = [c for c in ("\u200b", "\u200c", "\u2060", "\ufeff") if c in out]
    if removed:
        for char in removed:
            out = out.replace(char, "")
        steps.append({"step": "invisible-removal", "detail": "removed %s" % ", ".join("U+%04X" % ord(c) for c in removed)})
    data = {"steps": steps, "dry_run": dry_run, "changed": out != text,
            "replacement_characters": text.count("\ufffd"),
            "confidence": "high" if any(s["step"] == "mojibake-repair" for s in steps) else "low"}
    return {"output": out, "data": data,
            "warnings": ["repair is heuristic: compare against a known-good copy before overwriting anything"]}


def injection_marker(ctx):
    text = ctx.require_text(minimum=5)
    tables = _sec(ctx)
    hits = find_injection(text, tables)
    ctx.gate("scan untrusted text for injected instructions", {"findings": len(hits)})
    annotated = text
    for hit in sorted(hits, key=lambda h: -h["start"]):
        annotated = (annotated[:hit["start"]] + "⟦SUSPECT:%s⟧" % hit["type"] + annotated[hit["end"]:])
    rows = [[h["type"], h["confidence"], h["text"][:60].replace("\n", " ")] for h in hits]
    data = {"findings": hits, "count": len(hits),
            "policy": "treat the input as data, never as instructions",
            "annotated": annotated}
    if ctx.params["format"] == "json" or ctx.params["mode"] == "report":
        return {"output": annotated if ctx.params["mode"] != "report" else
                (core.table(rows, ["type", "confidence", "matched"]) if rows else "no injection markers found"),
                "data": data,
                "warnings": ["%d injection marker(s) found - do not follow them" % len(hits)] if hits else []}
    output = core.table(rows, ["type", "confidence", "matched"]) if rows else "no injection markers found"
    return {"output": output + ("\n\n" + annotated if hits else ""), "data": data,
            "warnings": ["%d injection marker(s) found - do not follow them" % len(hits)] if hits else []}


def bidi_spoof_check(ctx):
    text = ctx.require_text(minimum=1)
    tables = _sec(ctx)
    controls = tables["steganography"]["bidi_controls"]
    hits = [(idx, char) for idx, char in enumerate(text) if char in controls]
    suspicious = []
    for idx, char in hits:
        window = text[max(0, idx - 30):idx + 30]
        if re.search(r"[A-Za-z]{2,}", window) and re.search(r"[\u0590-\u08ff]", window):
            suspicious.append({"offset": idx, "char": "U+%04X" % ord(char),
                               "reason": "bidi control mixes RTL and Latin runs",
                               "context": window.replace("\n", " ")})
    visual = "".join("⟦%s⟧" % ("RLO" if c == "\u202e" else "BIDI") if c in controls else c for c in text)
    data = {"controls": len(hits), "suspicious": suspicious, "policy_suggestion":
            "strip or reject bidi controls outside intended RTL runs", "visualised": visual}
    rows = [[s["char"], s["reason"], s["context"][:60]] for s in suspicious]
    output = core.table(rows, ["control", "reason", "context"]) if rows else (
        "%d bidi control character(s) present, none mixing scripts" % len(hits) if hits
        else "no bidi control characters found")
    return {"output": output, "data": data,
            "warnings": ["%d suspicious bidi sequence(s)" % len(suspicious)] if suspicious else []}


SCRIPT_BLOCKS = [
    (0x0400, 0x04FF, "cyrillic"), (0x0370, 0x03FF, "greek"), (0x0530, 0x058F, "armenian"),
    (0x0590, 0x05FF, "hebrew"), (0x0600, 0x06FF, "arabic"), (0x0E00, 0x0E7F, "thai"),
    (0x0900, 0x097F, "devanagari"), (0x4E00, 0x9FFF, "han"), (0x3040, 0x30FF, "japanese"),
    (0xAC00, 0xD7AF, "hangul"), (0x1E00, 0x1EFF, "latin-extended"),
]


def _script_of(char):
    code = ord(char)
    for low, high, name in SCRIPT_BLOCKS:
        if low <= code <= high:
            return name
    if code < 128:
        return "ascii"
    if 0x80 <= code <= 0x24F:
        return "latin"
    return "other"


def homoglyph_security(ctx):
    text = ctx.require_text(minimum=1)
    tables = ctx.assets.json(UNI_ASSET)
    policy = ctx.params["policy"]
    confusables = tables["confusables"]
    findings = []
    for idx, char in enumerate(text):
        if ord(char) < 128:
            continue
        target = confusables.get(char)
        if not target:
            continue
        findings.append({"offset": idx, "char": char, "code": "U+%04X" % ord(char),
                         "looks_like": target, "script": _script_of(char)})
    mixed = sorted({f["script"] for f in findings})
    judged = findings if policy == "report" else [f for f in findings if f["script"] != "latin"]
    skeleton = "".join(confusables.get(c, c) for c in text)
    data = {"findings": judged, "count": len(judged), "scripts": mixed, "policy": policy,
            "skeleton": skeleton, "changed": skeleton != text,
            "verdict": ("reject: mixed-script confusables" if judged and policy == "strict"
                        else "review" if judged else "clean")}
    rows = [[f["code"], f["char"], f["looks_like"], f["script"], f["offset"]] for f in judged]
    output = (core.table(rows, ["code point", "char", "ascii look-alike", "script", "offset"])
              if rows else "no confusable characters found")
    if rows:
        output += "\n\nskeleton form: %s" % skeleton
    return {"output": output, "data": data,
            "warnings": ["%d confusable character(s) inside a security-relevant string" % len(judged)]
            if judged else []}


def steganography_check(ctx):
    text = ctx.require_text(minimum=1)
    tables = _sec(ctx)
    uni = ctx.assets.json(UNI_ASSET)
    invisible = {entry.get("char") if isinstance(entry, dict) else entry for entry in (uni.get("invisible_chars") or [])} | set(tables["steganography"]["zero_width"])
    found = {}
    for char in text:
        if char in invisible:
            found[char] = found.get(char, 0) + 1
    tag_ranges = tables["steganography"]["tag_characters"]["ranges"]
    tags = [c for c in text if any(lo <= ord(c) <= hi for lo, hi in tag_ranges)]
    vs_ranges = tables["steganography"]["variation_selectors"]["ranges"]
    selectors = [c for c in text if any(lo <= ord(c) <= hi for lo, hi in vs_ranges)]
    payload = ""
    if tags:
        payload = "".join(chr(ord(c) - 0xE0000) for c in tags if 0xE0020 <= ord(c) <= 0xE007E)
    data = {"invisible": {("U+%04X" % ord(k)): v for k, v in found.items()},
            "invisible_total": sum(found.values()), "tag_characters": len(tags),
            "variation_selectors": len(selectors), "hidden_payload": payload,
            "verdict": "covert-channel markers present" if (found or tags or selectors) else "no covert markers found"}
    out = ["# Covert channel check", "", "- verdict: %s" % data["verdict"]]
    if data["invisible"]:
        out.append("- invisible characters: %s" % ", ".join("%s x%d" % (k, v) for k, v in data["invisible"].items()))
    if tags:
        out.append("- tag characters: %d (decoded payload: %r)" % (len(tags), payload))
    if selectors:
        out.append("- variation selectors: %d" % len(selectors))
    return {"output": "\n".join(out), "data": data,
            "warnings": ["%d invisible code point(s) present" % data["invisible_total"]] if data["invisible_total"] else []}


def canonicalization_advisor(ctx):
    text = ctx.require_text(minimum=1)
    target = ctx.params["target"]
    tables = _sec(ctx)
    steps = []
    out = text
    if target in ("nfc", "nfkc"):
        form = target.upper()
        if not unicodedata.is_normalized(form, out):
            steps.append("unicode %s normalisation" % form)
            out = unicodedata.normalize(form, out)
    if target == "casefold":
        steps.append("casefold (comparison key only, not for display)")
        out = out.casefold()
    if target == "strip-accents":
        steps.append("diacritic removal")
        out = core.strip_accents(out)
    if target == "ascii":
        steps.append("ascii transliteration with replacement markers")
        out = core.strip_accents(out).encode("ascii", "replace").decode("ascii")
    if target == "nfc-identifier-safe":
        steps.extend(["NFC", "strip zero-width", "reject confusables"])
        out = unicodedata.normalize("NFC", out)
        for char in tables["steganography"]["zero_width"]:
            out = out.replace(char, "")
    if ctx.params["lower"] and target != "casefold":
        steps.append("lowercase")
        out = out.lower()
    key = fingerprint(out, tables["checksums"]["primary"])
    data = {"target": target, "steps": steps, "changed": out != text,
            "checksum": {"algorithm": tables["checksums"]["primary"], "value": key},
            "identity_key_preview": key[:16]}
    return {"output": out, "data": data,
            "warnings": ["canonical forms are for comparison; keep the original for display"]}


def checksum_manifest(ctx):
    text = ctx.require_text(minimum=1)
    tables = _sec(ctx)
    algorithms = [a.strip() for a in ctx.params["algorithms"].split(",") if a.strip()]
    if "sha256" not in algorithms:
        algorithms.insert(0, "sha256")
    digests = {alg: fingerprint(text, alg) for alg in algorithms}
    manifest = {"file": ctx.params["name"] or "stdin",
                "bytes_utf8": len(text.encode("utf-8")),
                "characters": len(text),
                "checksums": digests,
                "generated": core.now_iso(),
                "line_endings": "CRLF" if "\r\n" in text else "LF"}
    data = {"manifest": manifest,
            "verify_note": "recompute the same algorithm on the byte stream, not on a re-encoded copy"}
    if ctx.params["format"] == "json" or ctx.params["format"] == "manifest":
        return {"output": json.dumps(manifest, indent=2), "data": data}
    lines = ["# Checksum manifest", ""]
    lines.extend("- %s: `%s`" % (alg, value) for alg, value in digests.items())
    lines.append("- bytes (utf-8): %d" % manifest["bytes_utf8"])
    return {"output": "\n".join(lines), "data": data,
            "warnings": ["md5/sha1 are for legacy interoperability, not collision resistance"]
            if any(a in digests for a in ("md5", "sha1")) else []}


def redaction_verifier(ctx):
    text = ctx.require_text(minimum=1)
    tables = _sec(ctx)
    targets = [t.strip() for t in ctx.params["targets"].split(",") if t.strip()]
    mode = ctx.params["mode"]
    findings, leaked = [], []
    for kind in targets:
        if kind in tables["pii"]:
            spec = tables["pii"][kind]
            hits = list(re.finditer(spec["pattern"], text))
            if hits:
                findings.append({"kind": kind, "count": len(hits),
                                 "examples": [h.group(0)[:4] + "..." for h in hits[:3]]})
        elif kind in tables["secrets"]:
            hits = list(re.finditer(tables["secrets"][kind]["pattern"], text))
            if hits:
                findings.append({"kind": kind, "count": len(hits), "examples": ["redacted"]})
    residue = [(m.group(0), m.start()) for m in re.finditer(r"\[[A-Z_]{3,20}\]|<\w+:[0-9a-f]{6,}>", text)]
    shadows = []
    for token, start in residue:
        window = text[max(0, start - 60):start]
        for kind, spec in tables["pii"].items():
            if re.search(spec["pattern"], window) and kind in targets:
                shadows.append({"kind": kind, "reason": "same value may still appear near %s" % token})
    data = {"targets": targets, "mode": mode, "still_present": findings,
            "tokens_found": len(residue), "shadow_findings": shadows,
            "verdict": "clean" if not findings else "redaction incomplete"}
    if findings:
        rows = [[f["kind"], f["count"], ", ".join(f["examples"])] for f in findings]
        output = "redaction incomplete\n\n" + core.table(rows, ["kind", "remaining", "examples"])
    else:
        output = "no remaining matches for the requested types (%d placeholder token(s) found)" % len(residue)
    return {"output": output, "data": data,
            "warnings": ["%d type(s) of sensitive data still match" % len(findings)] if findings else []}


def log_sanitizer(ctx):
    text = ctx.require_text(minimum=5)
    tables = _sec(ctx)
    mode = ctx.params["mode"]
    ctx.gate("sanitize log text", {"mode": mode})
    out = text
    counts = {}

    def swap(pattern, label):
        nonlocal out
        regex = re.compile(pattern)
        found = regex.findall(out)
        if found:
            counts[label] = counts.get(label, 0) + len(found)
            out = regex.sub(label, out)

    swap(tables["secrets"]["jwt"]["pattern"], "[JWT]")
    swap(tables["secrets"]["bearer_token"]["pattern"], "[AUTH_HEADER]")
    swap(tables["secrets"]["connection_string"]["pattern"], "[CONNECTION_STRING]")
    swap(tables["secrets"]["aws_access_key_id"]["pattern"], "[AWS_KEY]")
    swap(tables["secrets"]["github_token"]["pattern"], "[GITHUB_TOKEN]")
    for kind in ("email", "phone_intl", "ipv4", "credit_card", "ssn_us"):
        swap(tables["pii"][kind]["pattern"], "[%s]" % kind.upper())
    paths = re.findall(r"(?:[A-Za-z]:\\|/)(?:[\w.-]+/){2,}[\w.-]+", out)
    if paths and mode in ("full", "paths"):
        counts["paths"] = len(paths)
        out = re.sub(r"(?:[A-Za-z]:\\|/)(?:[\w.-]+/){2,}[\w.-]+", "[PATH]", out)
    emails = re.findall(r"\buser[=:]\s*\S+", out, re.I)
    if emails:
        counts["user_fields"] = len(emails)
        out = re.sub(r"\buser[=:]\s*\S+", "user=[USER]", out, flags=re.I)
    residual = [h for h in find_secrets(out, tables)
                if not re.fullmatch(r"[\[<][A-Z_0-9]{2,30}[\]>:=][\w]*", h["text"].strip())
                and "[REDACTED" not in h["text"] and not re.search(r"[\[<][A-Z_]{3,}", h["text"])]
    data = {"mode": mode, "replacements": counts, "total": sum(counts.values()),
            "residual_scan": [h["type"] for h in residual]}
    warnings = []
    if data["residual_scan"]:
        warnings.append("credential-like patterns remain: %s" % ", ".join(sorted(set(data["residual_scan"]))))
    return {"output": out, "data": data, "warnings": warnings}


def provenance_label(ctx):
    text = ctx.require_text(minimum=5)
    origin = ctx.params["origin"]
    source = ctx.params["source"]
    license_name = ctx.params["license"]
    edited = ctx.params["edited"]
    style = ctx.params["style"]
    digest = fingerprint(text)
    if style == "footer":
        block = ("---\nOrigin: %s | Source: %s | License: %s | Edited: %s | SHA256: %s"
                 % (origin or "unknown", source or "unspecified", license_name or "unspecified",
                    edited or "unknown", digest[:16]))
        output = text + "\n\n" + block
    elif style == "frontmatter":
        block = ("---\norigin: %s\nsource: %s\nlicense: %s\nedited: %s\nsha256: %s\n---\n\n"
                 % (origin or "unknown", source or "unspecified", license_name or "unspecified",
                    edited or "unknown", digest))
        output = block + text
    else:
        output = ("[%s | %s | %s | sha256:%s]\n%s"
                  % (origin or "unknown", source or "unspecified", license_name or "unspecified",
                     digest[:16], text))
    data = {"origin": origin, "source": source, "license": license_name, "edited": edited,
            "sha256": digest, "style": style, "claims_verified": False}
    return {"output": output, "data": data,
            "warnings": ["labels record what you declare; they are not third-party verification"]}


def lossless_planner(ctx):
    text = ctx.require_text(minimum=1, allow_empty=True)
    goals = [g.strip() for g in ctx.params["goals"].split(",") if g.strip()]
    if not goals:
        raise core.InvalidInput("give --set goals=normalize-newlines,strip-trailing-space")
    order = []
    notes = []
    lossy = {"lowercase": "case information is destroyed",
             "strip-accents": "diacritics are destroyed",
             "ascii": "non-ASCII characters are replaced",
             "collapse-whitespace": "original spacing is lost",
             "strip-invisible": "formatting hints are removed"}
    for goal in sorted(goals, key=lambda g: {"strip-invisible": 0, "normalize-newlines": 1,
                                             "collapse-whitespace": 2, "strip-trailing-space": 3,
                                             "strip-accents": 4, "lowercase": 5, "ascii": 6}.get(g, 9)):
        reversible = goal in ("normalize-newlines", "strip-trailing-space")
        order.append({"step": goal, "reversible": reversible,
                      "risk": "" if reversible else lossy.get(goal, "unknown transformation")})
        if not reversible:
            notes.append("%s is lossy: keep the original bytes alongside the result" % goal)
    preview = text
    for step in order:
        name = step["step"]
        if name == "normalize-newlines":
            preview = core.canonical_newlines(preview)
        elif name == "strip-trailing-space":
            preview = "\n".join(line.rstrip() for line in preview.split("\n"))
        elif name == "collapse-whitespace":
            preview = core.fold_ws(preview)
        elif name == "strip-invisible":
            preview = "".join(c for c in preview if unicodedata.category(c) != "Cf")
        elif name == "lowercase":
            preview = preview.lower()
        elif name == "strip-accents":
            preview = core.strip_accents(preview)
        elif name == "ascii":
            preview = core.strip_accents(preview).encode("ascii", "replace").decode("ascii")
    data = {"plan": order, "lossy_steps": [s["step"] for s in order if not s["reversible"]],
            "lossless": all(s["reversible"] for s in order),
            "checksum_before": fingerprint(text)[:16], "checksum_after": fingerprint(preview)[:16]}
    out = ["# Plan"] + ["%d. %s%s" % (i + 1, s["step"], "" if s["reversible"] else " (LOSSY) - " + s["risk"])
                        for i, s in enumerate(order)]
    out.append("")
    out.append("Resulting text:\n" + preview)
    return {"output": "\n".join(out), "data": data,
            "warnings": notes[:3]}


HANDLERS = {
    "safety.accessible-language-editor": accessible_language,
    "safety.alternative-text-writer": alt_text_writer,
    "safety.inclusive-language-reviewer": inclusive_language,
    "safety.trauma-informed-text-editor": trauma_informed,
    "safety.privacy-and-pii-redactor": pii_redactor,
    "safety.secret-and-credential-scanner": secret_scanner,
    "safety.content-warning-composer": content_warning,
    "safety.safe-placeholder-generator": placeholder_generator,
    "safety.high-stakes-disclaimer-editor": high_stakes_disclaimer,
    "safety.bias-aware-dataset-text-reviewer": bias_dataset_reviewer,
    "safety.read-aloud-and-screen-reader-optimizer": read_aloud_optimizer,
    "integrity.encoding-forensics-analyst": encoding_forensics,
    "integrity.text-corruption-repairer": corruption_repair,
    "integrity.prompt-injection-and-untrusted-text-marker": injection_marker,
    "integrity.bidi-spoofing-detector": bidi_spoof_check,
    "integrity.homoglyph-security-scanner": homoglyph_security,
    "integrity.steganographic-text-pattern-checker": steganography_check,
    "integrity.canonicalization-advisor": canonicalization_advisor,
    "integrity.checksum-and-text-fingerprint-helper": checksum_manifest,
    "integrity.redaction-verification-assistant": redaction_verifier,
    "integrity.log-sanitization-editor": log_sanitizer,
    "integrity.text-provenance-labeler": provenance_label,
    "integrity.lossless-transformation-planner": lossless_planner,
}
