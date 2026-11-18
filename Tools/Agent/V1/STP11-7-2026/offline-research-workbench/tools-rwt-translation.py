"""tools-rwt-translation.py - terminology, translation memory, translation QA and production workflow.

Machine translation is delegated to a configured local adapter (tools-rwt-adapters.py). When no
adapter exists the translation commands fail with exit code 4 or, with --tm-only, produce an
explicitly untranslated TM-prefill deliverable. Nothing is sent over a network, and no
translation is presented as human-reviewed unless a reviewer recorded that decision.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

NAME = "translation"
ROLE = "terminology base, translation memory, alignment, QA scoring, MT adapter orchestration, locale checks"
GROUPS = [("translate", "Terminology, translation memory, alignment, QA, MT orchestration and locale checks")]
deps: dict = {}

STOPWORD_SETS = {
    "en": set("the a an and or but of to in for on with is are was were be been this that these those "
              "as by from at it its we you they he she not no".split()),
    "fil": set("ang ng mga sa na at ay si ni kay para kung hindi may mayroon ito iyon dito sila kami "
               "tayo ako ikaw siya ngunit subalit dahil upang rin din lamang lang po".split()),
    "es": set("el la los las un una de del y o pero en con por para es son que no se su sus al".split()),
    "fr": set("le la les un une de des et ou mais en avec pour est sont que ne se son sa ses au aux".split()),
    "de": set("der die das ein eine und oder aber in mit für ist sind dass nicht sich von zu den dem".split()),
    "pt": set("o a os as um uma de do da e ou mas em com por para é são que não se seu sua ao".split()),
    "id": set("yang dan di ke dari untuk dengan ini itu adalah tidak pada akan juga dalam".split()),
}
SCRIPT_RANGES = [
    ("Latin", [(0x0041, 0x024F), (0x1E00, 0x1EFF)]),
    ("Cyrillic", [(0x0400, 0x04FF)]),
    ("Greek", [(0x0370, 0x03FF)]),
    ("Arabic", [(0x0600, 0x06FF), (0x0750, 0x077F)]),
    ("Hebrew", [(0x0590, 0x05FF)]),
    ("Devanagari", [(0x0900, 0x097F)]),
    ("Thai", [(0x0E00, 0x0E7F)]),
    ("Han", [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)]),
    ("Hiragana", [(0x3040, 0x309F)]),
    ("Katakana", [(0x30A0, 0x30FF)]),
    ("Hangul", [(0xAC00, 0xD7AF), (0x1100, 0x11FF)]),
]
FILLERS = {"fil": ["po", "ho", "opo"], "en": []}
CYRILLIC_MAP = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
                "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
                "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
                "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
                "я": "ya"}
GREEK_MAP = {"α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
             "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
             "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o"}


def _store():
    return deps["store"]


def _conn(ctx):
    return ctx.conn()


# ---------------------------------------------------------------- terminology

def op_terms(ctx, action: str = "list", term: str | None = None, lang: str = "en",
             definition: str | None = None, equivalents: str | None = None,
             forbidden: str | None = None, domain: str | None = None, owner: str | None = None) -> dict:
    store = _store()
    conn = _conn(ctx)
    if action == "add":
        if not term:
            raise store.ToolError(2, "--term is required for 'add'")
        try:
            eqv = json.loads(equivalents) if equivalents else {}
        except json.JSONDecodeError:
            raise store.ToolError(2, "--equivalents must be JSON, e.g. {\"fil\": [\"pagpapawalang-bisa\"]}")
        tid = f"term-{store.slug(term, 40)}-{lang}"
        conn.execute("""INSERT OR REPLACE INTO terms(id,term,lang,definition,forbidden,equivalents,
            domain,owner,status,created) VALUES(?,?,?,?,?,?,?,?,'approved',?)""",
                     (tid, term, lang, definition or "", forbidden or "",
                      json.dumps(eqv, ensure_ascii=False), domain, owner or ctx.actor, store.now_iso()))
        ctx.commit()
        store.audit_append(conn, ctx.actor, "translate.term.add", tid, {"term": term, "lang": lang})
        ctx.commit()
        return {"status": "ok", "term_id": tid, "term": term, "lang": lang, "equivalents": eqv}
    if action == "list":
        rows = [{"term": r["term"], "lang": r["lang"], "definition": r["definition"],
                 "forbidden": r["forbidden"], "equivalents": json.loads(r["equivalents"] or "{}"),
                 "domain": r["domain"], "owner": r["owner"], "status": r["status"]}
                for r in conn.execute("SELECT * FROM terms ORDER BY term")]
        if domain:
            rows = [r for r in rows if r["domain"] == domain]
        return {"status": "ok", "count": len(rows), "terms": rows[:300]}
    if action == "get":
        row = conn.execute("SELECT * FROM terms WHERE term=? AND lang=?", (term, lang)).fetchone()
        if not row:
            raise store.ToolError(2, f"term not found: {term} ({lang})")
        return {"status": "ok", "term": dict(row)}
    raise store.ToolError(2, "action must be add|list|get")


def op_term_extract(ctx, input_path: str, language: str = "en", min_freq: int = 2,
                    max_terms: int = 50) -> dict:
    store = _store()
    body = store.read_text(input_path)
    stop = STOPWORD_SETS.get(language, set())
    tokens = re.findall(r"[^\W\d_][\w'\-]+", body, re.UNICODE)
    candidates = {}
    for n in (1, 2, 3):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if any(t.lower() in stop for t in gram):
                continue
            if n > 1 and gram[0][0].islower():
                continue
            phrase = " ".join(gram)
            if len(phrase) < 4:
                continue
            candidates[phrase] = candidates.get(phrase, 0) + 1
    scored = [{"term": term, "frequency": freq,
               "score": round(freq * (1 + 0.5 * (len(term.split()) - 1)), 3),
               "status": "candidate"}
              for term, freq in candidates.items() if freq >= min_freq]
    scored.sort(key=lambda x: (-x["score"], x["term"]))
    existing = {r["term"].lower() for r in _conn(ctx).execute("SELECT term FROM terms")}
    for item in scored:
        item["already_in_terminology"] = item["term"].lower() in existing
    return {"status": "ok", "language": language, "candidates": scored[:max_terms],
            "candidate_count": len(scored),
            "note": "candidates require expert validation before they become approved terminology"}


def op_term_check(ctx, source: str, target: str, src_lang: str = "en", tgt_lang: str = "fil") -> dict:
    """Terminology consistency: required equivalents used, forbidden variants absent."""
    conn = _conn(ctx)
    rows = [dict(r) for r in conn.execute("SELECT * FROM terms")]
    violations, applied = [], []
    target_lower = target.lower()
    for row in rows:
        eqv = json.loads(row["equivalents"] or "{}")
        approved = eqv.get(tgt_lang) or []
        approved = [approved] if isinstance(approved, str) else approved
        if not approved:
            continue
        if re.search(rf"\b{re.escape(row['term'])}\b", source, re.I):
            if not any(a.lower() in target_lower for a in approved):
                violations.append({"type": "missing-approved-term", "term": row["term"],
                                   "expected_equivalent": approved, "severity": "major",
                                   "source": row["term"]})
            else:
                applied.append({"term": row["term"], "equivalent": approved})
        if row["forbidden"]:
            for variant in [v.strip() for v in row["forbidden"].split(";") if v.strip()]:
                if variant.lower() in target_lower:
                    violations.append({"type": "forbidden-variant", "term": row["term"],
                                       "variant": variant, "severity": "major"})
    return {"status": "ok", "terms_checked": len(rows), "violations": violations,
            "applied_terms": applied, "passed": not violations}


# ---------------------------------------------------------------- translation memory

def op_tm(ctx, action: str = "list", source: str | None = None, target: str | None = None,
          src_lang: str = "en", tgt_lang: str = "fil", input_path: str | None = None,
          output: str | None = None, domain: str | None = None, threshold: float = 0.7,
          limit: int = 5, project: str | None = None, apply_changes: bool = False,
          approved: bool = False, approver: str | None = None, note: str = "") -> dict:
    store = _store()
    conn = _conn(ctx)
    if action == "add":
        if not source or not target:
            raise store.ToolError(2, "--source and --target are required for 'add'")
        unit = _insert_tm(ctx, source, target, src_lang, tgt_lang, domain, project, "imported")
        return {"status": "ok", **unit}
    if action == "list":
        rows = [dict(r) for r in conn.execute(
            "SELECT id,src_lang,tgt_lang,source,target,domain,project,status,provenance FROM tm_units "
            "WHERE src_lang=? AND tgt_lang=? ORDER BY created DESC LIMIT ?",
            (src_lang, tgt_lang, int(limit)))]
        total = conn.execute("SELECT count(*) c FROM tm_units").fetchone()["c"]
        return {"status": "ok", "count": len(rows), "total_units": total, "units": rows}
    if action == "import":
        if not input_path or not os.path.exists(input_path):
            raise store.ToolError(2, f"import file not found: {input_path}")
        raw = open(input_path, "rb").read()
        imported, skipped = 0, []
        if input_path.lower().endswith((".tmx", ".xml")):
            try:
                tree = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise store.ToolError(2, f"invalid TMX: {exc}")
            for tu in tree.iter("tu"):
                langs = {}
                for tuv in tu.findall("tuv"):
                    lang = tuv.get("{http://www.w3.org/XML/1998/namespace}lang") or tuv.get("lang")
                    seg = tuv.find("seg")
                    if lang and seg is not None and seg.text:
                        langs[lang.split("-")[0].lower()] = seg.text.strip()
                if src_lang in langs and tgt_lang in langs:
                    _insert_tm(ctx, langs[src_lang], langs[tgt_lang], src_lang, tgt_lang,
                               domain, project, "tmx")
                    imported += 1
                else:
                    skipped.append({"reason": "missing language pair", "languages": list(langs)})
        else:
            text = store.read_text(input_path)
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or not {"source", "target"} <= set(reader.fieldnames):
                raise store.ToolError(2, "CSV import needs 'source' and 'target' columns "
                                        "(optional: src_lang,tgt_lang,domain,project)")
            for row in reader:
                if not row.get("source") or not row.get("target"):
                    skipped.append({"reason": "empty source/target", "row": row})
                    continue
                _insert_tm(ctx, row["source"], row["target"], row.get("src_lang") or src_lang,
                           row.get("tgt_lang") or tgt_lang, row.get("domain") or domain,
                           row.get("project") or project, "csv")
                imported += 1
        ctx.commit()
        return {"status": "ok", "imported": imported, "skipped": skipped[:20],
                "total_units": conn.execute("SELECT count(*) c FROM tm_units").fetchone()["c"]}
    if action == "export":
        if not output:
            raise store.ToolError(2, "--output is required for 'export'")
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM tm_units WHERE src_lang=? AND tgt_lang=?", (src_lang, tgt_lang))]
        if not rows:
            raise store.ToolError(2, "no translation units for that language pair")
        out = ctx.out_path(output)
        if out.lower().endswith(".tmx"):
            root = ET.Element("tmx", version="1.4")
            header = ET.SubElement(root, "header", creationtool="offline-research-workbench",
                                   creationtoolversion="1.0.0", segtype="sentence",
                                   o_tmf="C", adminlang="en", srclang=src_lang,
                                   datatype="plaintext")
            ET.SubElement(header, "note").text = "Exported offline; provenance kept in the workbench database"
            body_el = ET.SubElement(root, "body")
            for row in rows:
                tu = ET.SubElement(body_el, "tu", tuid=row["id"])
                ET.SubElement(tu, "prop", type="status").text = row["status"]
                for lang, text in ((row["src_lang"], row["source"]), (row["tgt_lang"], row["target"])):
                    tuv = ET.SubElement(tu, "tuv")
                    tuv.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
                    ET.SubElement(tuv, "seg").text = text
            store.write_text(out, ET.tostring(root, encoding="unicode"))
        else:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "src_lang", "tgt_lang", "source", "target", "domain", "status",
                             "provenance", "checksum"])
            for row in rows:
                writer.writerow([row["id"], row["src_lang"], row["tgt_lang"], row["source"],
                                 row["target"], row["domain"], row["status"], row["provenance"],
                                 row["checksum"]])
            store.write_text(out, buf.getvalue())
        return {"status": "ok", "output": out, "units": len(rows), "format": "tmx" if out.endswith(".tmx") else "csv"}
    if action == "match":
        if not source:
            raise store.ToolError(2, "--source is required for 'match'")
        return {"status": "ok", **match_tm(ctx, source, src_lang, tgt_lang, threshold, limit)}
    if action == "clean":
        return clean_tm(ctx, src_lang, tgt_lang, apply_changes=apply_changes,
                        approved=approved, approver=approver, note=note)
    raise store.ToolError(2, "action must be add|list|import|export|match|clean")


def clean_tm(ctx, src_lang: str = "en", tgt_lang: str = "fil", apply_changes: bool = False,
             approved: bool = False, approver: str | None = None, note: str = "") -> dict:
    """Find duplicate, misaligned, empty, wrong-language and forbidden-term TM units."""
    store = _store()
    conn = _conn(ctx)
    rows = [dict(r) for r in conn.execute(
        "SELECT id,source,target,src_lang,tgt_lang,status FROM tm_units WHERE src_lang=? AND tgt_lang=?",
        (src_lang, tgt_lang))]
    forbidden = {}
    for term in conn.execute("SELECT term,forbidden,equivalents FROM terms"):
        for variant in [v.strip() for v in (term["forbidden"] or "").split(";") if v.strip()]:
            forbidden[variant.lower()] = term["term"]
    flagged, seen = [], {}
    for row in rows:
        key = re.sub(r"\s+", " ", row["source"].strip().lower())
        problems = []
        if key in seen:
            problems.append({"problem": "duplicate-source", "other_unit": seen[key]})
        else:
            seen[key] = row["id"]
        if not row["target"].strip():
            problems.append({"problem": "empty-target"})
        elif row["target"].strip().lower() == row["source"].strip().lower():
            problems.append({"problem": "target-equals-source"})
        detected = op_langid(ctx, text=row["target"])["language"]
        if detected != tgt_lang:
            problems.append({"problem": "wrong-target-language", "detected": detected})
        for variant, term in forbidden.items():
            if variant in row["target"].lower():
                problems.append({"problem": "forbidden-term", "variant": variant, "term": term})
        if problems:
            flagged.append({"tm_id": row["id"], "source": row["source"][:160], "problems": problems})
    removed = 0
    if apply_changes and flagged:
        store.require_approval(ctx, "corpus.delete", f"tm-clean:{src_lang}-{tgt_lang}",
                               approved, approver, note)
        for item in flagged:
            conn.execute("DELETE FROM tm_units WHERE id=?", (item["tm_id"],))
            removed += 1
        ctx.commit()
    return {"status": "ok", "units_scanned": len(rows), "flagged": flagged[:100],
            "flagged_count": len(flagged), "applied": apply_changes, "removed": removed,
            "note": "cleaning is advisory until --apply plus an approval is supplied"}


def op_script_check(ctx, input_path: str | None = None, text: str | None = None,
                    lang: str = "ar") -> dict:
    """RTL, CJK and complex-script typography checks with an explicit repertoire limit."""
    store = _store()
    if input_path:
        body = store.read_text(input_path)
    elif text:
        body = text
    else:
        raise store.ToolError(2, "provide --input or --text")
    rtl = lang.split("-")[0].lower() in ("ar", "he", "fa", "ur")
    cjk = lang.split("-")[0].lower() in ("zh", "ja", "ko")
    findings = []
    if rtl:
        bidi_controls = re.findall(r"[\u202a-\u202e\u2066-\u2069]", body)
        if bidi_controls:
            findings.append({"check": "bidi-controls", "severity": "major",
                             "count": len(bidi_controls),
                             "message": "explicit bidi controls must be balanced and intentional"})
        latin_runs = re.findall(r"[A-Za-z]{2,}", body)
        if latin_runs and not re.search(r"[\u2066-\u2069]", body):
            findings.append({"check": "mixed-direction", "severity": "minor",
                             "samples": latin_runs[:5],
                             "message": "isolate LTR runs (numbers, citations, URLs) from RTL text"})
        if re.search(r"[\u0660-\u0669]", body) and re.search(r"\d", body):
            findings.append({"check": "digit-script", "severity": "minor",
                             "message": "Arabic-Indic and ASCII digits are mixed"})
        if re.search(r"[,;?]", body):
            findings.append({"check": "rtl-punctuation", "severity": "minor",
                             "message": "check punctuation direction and mirroring in the renderer"})
    if cjk:
        if re.search(r"[\u3001\u3002]", body) and re.search(r"[,.]", body):
            findings.append({"check": "punctuation-width", "severity": "major",
                             "message": "full-width and half-width punctuation are mixed"})
        if re.search(r"[\u4e00-\u9fff][A-Za-z0-9]", body) or re.search(r"[A-Za-z0-9][\u4e00-\u9fff]", body):
            findings.append({"check": "cjk-latin-spacing", "severity": "minor",
                             "message": "add spacing between CJK and Latin runs for readability"})
        prohibited_line_start = re.findall(r"\n\s*([\u3001\u3002\uff0c\uff01\uff1f\u30fb])", body)
        if prohibited_line_start:
            findings.append({"check": "line-break-rules", "severity": "major",
                             "count": len(prohibited_line_start),
                             "message": "closing punctuation must not start a line"})
    combining = [c for c in body if unicodedata.combining(c)]
    if combining:
        findings.append({"check": "combining-marks", "severity": "minor",
                         "count": len(combining),
                         "message": "verify shaping and font coverage for combining sequences"})
    if re.search(r"[\u200b-\u200f\u2028\u2029]", body):
        findings.append({"check": "invisible-characters", "severity": "major",
                         "message": "zero-width and separator characters are present; remove or document them"})
    major = [f for f in findings if f["severity"] == "major"]
    return {"status": "ok", "language": lang, "script_class": "rtl" if rtl else ("cjk" if cjk else "ltr"),
            "findings": findings, "passed": not major,
            "repertoire_note": "font glyph coverage can only be confirmed by rendering the document; "
                               "this check covers direction, punctuation, spacing and invisible characters"}


def _insert_tm(ctx, source, target, src_lang, tgt_lang, domain, project, provenance) -> dict:
    store = _store()
    conn = _conn(ctx)
    if len(source) > int(ctx.cfg.get("translation", {}).get("max_segment_chars", 5000)):
        raise store.ToolError(2, "source segment exceeds translation.max_segment_chars")
    uid = f"tm-{store.sha256_text(source + '||' + tgt_lang)[:16]}"
    conn.execute("""INSERT OR REPLACE INTO tm_units(id,src_lang,tgt_lang,source,target,domain,project,
        provenance,status,checksum,created) VALUES(?,?,?,?,?,?,?,?,'imported',?,?)""",
                 (uid, src_lang, tgt_lang, source, target, domain, project, provenance,
                  store.sha256_text(source), store.now_iso()))
    ctx.commit()
    return {"tm_id": uid, "src_lang": src_lang, "tgt_lang": tgt_lang, "status": "imported"}


def match_tm(ctx, source: str, src_lang: str, tgt_lang: str, threshold: float = 0.7,
             limit: int = 5) -> dict:
    import difflib
    store = _store()
    rows = [dict(r) for r in _conn(ctx).execute(
        "SELECT id,source,target,domain,status,provenance FROM tm_units WHERE src_lang=? AND tgt_lang=?",
        (src_lang, tgt_lang))]
    scored = []
    norm_source = re.sub(r"\s+", " ", source.strip().lower())
    for row in rows:
        candidate = re.sub(r"\s+", " ", row["source"].strip().lower())
        ratio = difflib.SequenceMatcher(None, norm_source, candidate).ratio()
        if ratio >= threshold:
            scored.append({"tm_id": row["id"], "match_percent": round(ratio * 100, 2),
                           "source": row["source"], "target": row["target"],
                           "domain": row["domain"], "status": row["status"],
                           "provenance": row["provenance"]})
    scored.sort(key=lambda x: -x["match_percent"])
    exact = [s for s in scored if s["match_percent"] >= 99.99]
    return {"source": source, "src_lang": src_lang, "tgt_lang": tgt_lang,
            "matches": scored[:limit], "match_count": len(scored),
            "best_match_percent": scored[0]["match_percent"] if scored else 0,
            "exact_match": bool(exact),
            "threshold": threshold}


# ---------------------------------------------------------------- alignment

def op_align(ctx, source_path: str, target_path: str, src_lang: str = "en", tgt_lang: str = "fil",
             output: str | None = None, add_to_tm: bool = False) -> dict:
    """Heuristic sentence alignment: length ratio, numeric anchors and shared tokens."""
    store = _store()
    for path in (source_path, target_path):
        if not os.path.exists(path):
            raise store.ToolError(2, f"file not found: {path}")
    src_sents = store.sentences(store.read_text(source_path))
    tgt_sents = store.sentences(store.read_text(target_path))
    if not src_sents or not tgt_sents:
        raise store.ToolError(2, "both files need sentences")
    pairs = align_sentences(src_sents, tgt_sents)
    if add_to_tm:
        for pair in pairs:
            if pair["source"] and pair["target"]:
                _insert_tm(ctx, pair["source"], pair["target"], src_lang, tgt_lang, None, None,
                           "alignment")
    payload = {"status": "ok", "pairs": pairs[:1000], "pair_count": len(pairs),
               "source_sentences": len(src_sents), "target_sentences": len(tgt_sents),
               "added_to_tm": add_to_tm,
               "note": "alignment is heuristic; every pair must be confirmed by a bilingual reviewer"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), pairs)
    return payload


def align_sentences(src_sents: list[str], tgt_sents: list[str]) -> list[dict]:
    store = _store()
    n, m = len(src_sents), len(tgt_sents)
    if n > 800 or m > 800:
        raise store.ToolError(2, "alignment is limited to 800 sentences per document; split the file")
    def score(i, j):
        a, b = src_sents[i], tgt_sents[j]
        wa, wb = max(1, store.words(a)), max(1, store.words(b))
        length_ratio = min(wa, wb) / max(wa, wb)
        numbers_a = set(re.findall(r"\d+(?:[.,]\d+)?", a))
        numbers_b = set(re.findall(r"\d+(?:[.,]\d+)?", b))
        anchor = 1.0 if numbers_a and numbers_a & numbers_b else (0.0 if numbers_a else 0.5)
        shared = len(set(store.tokens(a)) & set(store.tokens(b)))
        cognate = min(1.0, shared / 6.0)
        punct_a = len(re.findall(r"[;:!?]", a))
        punct_b = len(re.findall(r"[;:!?]", b))
        punct = 1.0 if punct_a == punct_b else 0.5
        return 0.4 * length_ratio + 0.3 * anchor + 0.2 * cognate + 0.1 * punct
    inf = float("-inf")
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if dp[i][j] == inf:
                continue
            if i < n and j < m:
                cand = dp[i][j] + score(i, j)
                if cand > dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = cand
                    back[i + 1][j + 1] = (i, j, "1-1")
            if i < n:
                cand = dp[i][j] - 0.35
                if cand > dp[i + 1][j]:
                    dp[i + 1][j] = cand
                    back[i + 1][j] = (i, j, "1-0")
            if j < m:
                cand = dp[i][j] - 0.35
                if cand > dp[i][j + 1]:
                    dp[i][j + 1] = cand
                    back[i][j + 1] = (i, j, "0-1")
    pairs, i, j = [], n, m
    while (i, j) != (0, 0):
        prev = back[i][j]
        if prev is None:
            break
        pi, pj, label = prev
        pairs.append({"source": src_sents[pi] if label != "0-1" else "",
                      "target": tgt_sents[pj] if label != "1-0" else "",
                      "alignment": label,
                      "confidence": round(max(0.0, min(1.0, (dp[i][j] - dp[pi][pj] + 0.35) / 1.0)), 3)})
        i, j = pi, pj
    pairs.reverse()
    return pairs


# ---------------------------------------------------------------- QA and scoring

def _segments_from(ctx, input_path: str) -> list[dict]:
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"bilingual file not found: {input_path}")
    raw = open(input_path, "rb").read()
    if input_path.lower().endswith((".tmx", ".xml")):
        try:
            tree = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise store.ToolError(2, f"invalid TMX: {exc}")
        units = []
        for index, tu in enumerate(tree.iter("tu"), 1):
            langs = {}
            for tuv in tu.findall("tuv"):
                lang = tuv.get("{http://www.w3.org/XML/1998/namespace}lang") or tuv.get("lang")
                seg = tuv.find("seg")
                if lang and seg is not None and seg.text:
                    langs[lang.split("-")[0].lower()] = seg.text
            if len(langs) >= 2:
                keys = list(langs)
                units.append({"id": f"tu-{index}", "src_lang": keys[0], "tgt_lang": keys[1],
                              "source": langs[keys[0]], "target": langs[keys[1]]})
        return units
    text = store.read_text(input_path)
    if input_path.lower().endswith((".json",)):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise store.ToolError(2, f"JSON bilingual file invalid: {exc}")
        data = data if isinstance(data, list) else data.get("segments", [])
        out = []
        for index, item in enumerate(data, 1):
            if not isinstance(item, dict) or "source" not in item or "target" not in item:
                raise store.ToolError(2, "each JSON segment needs 'source' and 'target'")
            out.append({"id": item.get("id") or f"seg-{index}",
                        "src_lang": item.get("src_lang", "en"), "tgt_lang": item.get("tgt_lang", "fil"),
                        "source": item["source"], "target": item["target"]})
        return out
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"source", "target"} <= set(reader.fieldnames):
        raise store.ToolError(2, "CSV bilingual file needs 'source' and 'target' columns")
    return [{"id": row.get("id") or f"seg-{i}", "src_lang": row.get("src_lang", "en"),
             "tgt_lang": row.get("tgt_lang", "fil"), "source": row["source"], "target": row["target"]}
            for i, row in enumerate(reader, 1)]


PLACEHOLDER_DEFAULT = [r"\{[^{}]+\}", r"%[sdif]", r"\$\{[^{}]+\}", r"<[^<>\s]+>", r"&[a-z]+;"]


def qa_segment(ctx, seg: dict, placeholders: list | None = None) -> list[dict]:
    translation_cfg = ctx.cfg.get("translation", {})
    patterns = placeholders or [p for p in translation_cfg.get("placeholder_patterns", PLACEHOLDER_DEFAULT)]
    findings = []
    source, target = seg["source"], seg["target"]
    src_nums = re.findall(r"\d+(?:[.,]\d+)*", source)
    tgt_nums = re.findall(r"\d+(?:[.,]\d+)*", target)
    if sorted(src_nums) != sorted(tgt_nums):
        findings.append({"category": "accuracy", "type": "number-mismatch", "severity": "critical",
                         "source_numbers": src_nums, "target_numbers": tgt_nums,
                         "message": "numbers must survive translation exactly"})
    for pattern in patterns:
        src_ph = sorted(re.findall(pattern, source))
        tgt_ph = sorted(re.findall(pattern, target))
        if src_ph != tgt_ph:
            findings.append({"category": "functional", "type": "placeholder-mismatch", "severity": "critical",
                             "pattern": pattern, "source": src_ph, "target": tgt_ph,
                             "message": "placeholders/tags must be preserved"})
    if not target.strip():
        findings.append({"category": "accuracy", "type": "omission", "severity": "critical",
                         "message": "target segment is empty"})
        return findings
    if source.strip().lower() == target.strip().lower():
        findings.append({"category": "accuracy", "type": "untranslated", "severity": "major",
                         "message": "target is identical to source"})
    ratio = len(target) / max(1, len(source))
    low = float(translation_cfg.get("length_ratio_min", 0.5))
    high = float(translation_cfg.get("length_ratio_max", 2.5))
    if not (low <= ratio <= high):
        findings.append({"category": "accuracy", "type": "length-ratio", "severity": "major",
                         "ratio": round(ratio, 3), "allowed": [low, high],
                         "message": "target length is far from source length; check for omission or addition"})
    src_neg = len(re.findall(r"\b(not|no|never|none|without)\b", source, re.I))
    tgt_neg = len(re.findall(r"\b(hindi|wala|hindi|never|not|no|não|non|nicht|tidak)\b", target, re.I))
    if src_neg and not tgt_neg:
        findings.append({"category": "accuracy", "type": "negation-dropped", "severity": "critical",
                         "message": "source negation has no counterpart in the target"})
    if re.search(r"\b(shall|must|should|may)\b", source, re.I) and \
            not re.search(r"\b(dapat|kailangan|maaaring|must|shall|should|deberá|deve|doit)\b", target, re.I):
        findings.append({"category": "accuracy", "type": "modality-shift", "severity": "major",
                         "message": "modal strength may have been lost"})
    src_terms = deps["translation"].op_term_check(ctx, source, target, seg.get("src_lang", "en"),
                                                 seg.get("tgt_lang", "fil"))
    findings.extend(src_terms["violations"])
    if re.search(r"\s{2,}", target):
        findings.append({"category": "style", "type": "double-space", "severity": "minor"})
    if re.search(r"[.!?]\s*$", source) and not re.search(r"[.!?]\s*$", target):
        findings.append({"category": "grammar", "type": "missing-final-punctuation", "severity": "minor"})
    if re.search(r"[A-Z]{4,}", source) and not re.search(r"[A-Z]{4,}", target):
        findings.append({"category": "style", "type": "acronym-expansion-missing", "severity": "minor",
                         "message": "an acronym in the source has no obvious counterpart"})
    return findings


def op_qa(ctx, input_path: str, output: str | None = None, fail_on: str = "critical") -> dict:
    store = _store()
    segments = _segments_from(ctx, input_path)
    if not segments:
        raise store.ToolError(2, "no bilingual segments found")
    all_findings, per_segment = [], []
    for seg in segments:
        findings = qa_segment(ctx, seg)
        for finding in findings:
            finding["segment_id"] = seg["id"]
        all_findings.extend(findings)
        if findings:
            per_segment.append({"segment_id": seg["id"], "source": seg["source"][:160],
                                "target": seg["target"][:160],
                                "severity": _worst(findings), "findings": findings})
    counts = {}
    for finding in all_findings:
        severity = finding.get("severity", "minor")
        counts[severity] = counts.get(severity, 0) + 1
    worst = _worst(all_findings) if all_findings else "none"
    order = {"none": 0, "minor": 1, "major": 2, "critical": 3}
    failed = order.get(worst, 0) >= order.get(fail_on, 3)
    payload = {"status": "ok", "segments": len(segments), "findings": len(all_findings),
               "by_severity": counts, "worst_severity": worst,
               "failed_gate": failed, "fail_on": fail_on,
               "segment_reports": per_segment[:200],
               "note": "automated QA catches mechanical errors; bilingual accuracy still needs a reviewer"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def _worst(findings) -> str:
    order = {"minor": 1, "major": 2, "critical": 3}
    worst = "none"
    for finding in findings:
        severity = finding.get("severity", "minor")
        if order.get(severity, 0) > order.get(worst, 0):
            worst = severity
    return worst


def op_score(ctx, input_path: str, output: str | None = None, human_errors: str | None = None) -> dict:
    """Severity-weighted language quality score (LISA-style penalty per 1000 words)."""
    store = _store()
    segments = _segments_from(ctx, input_path)
    findings = []
    for seg in segments:
        findings.extend(qa_segment(ctx, seg))
    if human_errors:
        if not os.path.exists(human_errors):
            raise store.ToolError(2, f"human error file not found: {human_errors}")
        try:
            extra = json.loads(store.read_text(human_errors))
        except json.JSONDecodeError as exc:
            raise store.ToolError(2, f"human error file must be JSON: {exc}")
        for error in extra:
            if error.get("severity") not in ("critical", "major", "minor"):
                raise store.ToolError(2, "human errors need severity critical|major|minor")
            findings.append({**error, "source": "human"})
    weights = ctx.cfg.get("translation", {}).get("severity_weights",
                                                 {"critical": 10, "major": 5, "minor": 1})
    words = sum(store.words(seg["target"]) for seg in segments) or 1
    penalty = sum(weights.get(f.get("severity", "minor"), 1) for f in findings)
    score = max(0.0, 100 - penalty * 1000 / words)
    threshold = float(ctx.cfg.get("translation", {}).get("acceptance_threshold", 95))
    payload = {"status": "ok", "segments": len(segments), "target_words": words,
               "penalty_points": penalty,
               "error_counts": {sev: sum(1 for f in findings if f.get("severity") == sev)
                                for sev in ("critical", "major", "minor")},
               "language_score": round(score, 2), "threshold": threshold,
               "accepted": score >= threshold,
               "decision": "accept" if score >= threshold else "return to reviewer",
               "formula": "score = 100 - (weighted_error_points * 1000 / target_words)"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


# ---------------------------------------------------------------- MT orchestration

def op_mt(ctx, input_path: str, src_lang: str = "en", tgt_lang: str = "fil",
          output: str | None = None, tm_only: bool = False, domain: str | None = None,
          max_segments: int = 500) -> dict:
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    body = store.read_text(input_path)
    chunks = store.chunk_text(body, int(ctx.cfg.get("limits", {}).get("max_words_per_segment", 220)))
    chunks = chunks[:max(1, min(int(max_segments), 2000))]
    adapter = deps["adapters"]
    available = bool(adapter.resolve_command(ctx, "mt"))
    if not available and not tm_only:
        raise store.ToolError(
            4,
            "no local 'mt' adapter is configured; configure adapters.kinds.mt.command with an approved "
            "offline MT model, or run with --tm-only to produce a translation-memory prefill without MT",
            {"kind": "mt", "configure": "adapters.kinds.mt.command in a --config file",
             "tm_units_available": _conn(ctx).execute("SELECT count(*) c FROM tm_units").fetchone()["c"]})
    results, untranslated = [], []
    for index, chunk in enumerate(chunks, 1):
        match = match_tm(ctx, chunk, src_lang, tgt_lang, threshold=99.99, limit=1)
        if match["exact_match"]:
            results.append({"index": index, "source": chunk, "target": match["matches"][0]["target"],
                            "provenance": "tm-exact", "tm_id": match["matches"][0]["tm_id"]})
            continue
        if not available:
            untranslated.append({"index": index, "source": chunk})
            continue
        response = adapter.invoke(ctx, "mt", {"text": chunk, "src_lang": src_lang, "tgt_lang": tgt_lang,
                                              "domain": domain})
        target = str(response["result"].get("translation", "")).strip()
        findings = qa_segment(ctx, {"source": chunk, "target": target, "src_lang": src_lang,
                                    "tgt_lang": tgt_lang})
        results.append({"index": index, "source": chunk, "target": target,
                        "provenance": "mt-adapter", "qa_findings": findings})
    out = ctx.out_path(output or f"translation-{tgt_lang}.json")
    payload = {"status": "ok" if not untranslated else "partial", "output": out,
               "engine": "mt-adapter" if available else "tm-prefill-only",
               "src_lang": src_lang, "tgt_lang": tgt_lang, "segments": len(results),
               "machine_translated": sum(1 for r in results if r["provenance"] == "mt-adapter"),
               "tm_reused": sum(1 for r in results if r["provenance"] == "tm-exact"),
               "untranslated": len(untranslated), "untranslated_segments": untranslated[:20],
               "status_flag": "draft-machine-translation" if available else "draft-untranslated",
               "review_required": True,
               "note": "output is a draft; human review is mandatory before publication"}
    store.write_json(out, {"segments": results, "meta": {k: v for k, v in payload.items() if k != "output"}})
    return payload


def op_backtranslate(ctx, input_path: str, src_lang: str = "en", tgt_lang: str = "fil") -> dict:
    """Selective back-translation of high-risk segments, compared mechanically."""
    store = _store()
    segments = _segments_from(ctx, input_path)
    adapter = deps["adapters"]
    if not adapter.resolve_command(ctx, "mt"):
        raise store.ToolError(4, "back-translation needs a configured local 'mt' adapter for both directions",
                              {"configure": "adapters.kinds.mt.command"})
    risky = [s for s in segments
             if re.search(r"\d", s["source"]) or re.search(r"\b(not|no|never|shall|must|may)\b",
                                                            s["source"], re.I)]
    risky = risky[:50]
    if not risky:
        return {"status": "ok", "checked": 0, "note": "no high-risk segments (numbers, negation, modality)"}
    details = []
    for seg in risky:
        back = adapter.invoke(ctx, "mt", {"text": seg["target"], "src_lang": tgt_lang,
                                          "tgt_lang": src_lang})["result"].get("translation", "")
        similarity = _similarity(seg["source"], back)
        numbers_match = sorted(re.findall(r"\d+", seg["source"])) == sorted(re.findall(r"\d+", back))
        details.append({"segment_id": seg["id"], "source": seg["source"][:200],
                        "forward": seg["target"][:200], "back_translation": back[:200],
                        "similarity": round(similarity, 3),
                        "numbers_preserved": numbers_match,
                        "flag": not numbers_match or similarity < 0.45})
    flagged = [d for d in details if d["flag"]]
    return {"status": "ok", "checked": len(details), "flagged": len(flagged), "details": details,
            "caveat": "mechanical back-translation can hide errors; it supplements, never replaces, review"}


def _similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ---------------------------------------------------------------- language and locale

def op_langid(ctx, text: str | None = None, input_path: str | None = None) -> dict:
    store = _store()
    if input_path:
        body = store.read_text(input_path)
    elif text:
        body = text
    else:
        raise store.ToolError(2, "provide --text or --input")
    tokens = [t.lower() for t in re.findall(r"[^\W\d_]+", body)]
    if not tokens:
        raise store.ToolError(2, "no words to identify")
    scores = {}
    for lang, words in STOPWORD_SETS.items():
        hits = sum(1 for t in tokens if t in words)
        scores[lang] = hits / len(tokens)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    scripts = {}
    for char in body:
        code = ord(char)
        for name, ranges in SCRIPT_RANGES:
            if any(lo <= code <= hi for lo, hi in ranges):
                scripts[name] = scripts.get(name, 0) + 1
                break
    dominant_script = max(scripts, key=scripts.get) if scripts else "unknown"
    segments = []
    for sent in store.sentences(body):
        stoks = [t.lower() for t in re.findall(r"[^\W\d_]+", sent)]
        if not stoks:
            continue
        local = max(STOPWORD_SETS, key=lambda l: sum(1 for t in stoks if t in STOPWORD_SETS[l]) / len(stoks))
        segments.append({"language": local, "text": sent[:120]})
    mixed = len({s["language"] for s in segments}) > 1
    return {"status": "ok", "language": best, "confidence": round(best_score, 3),
            "margin": round(best_score - second, 3),
            "scores": {k: round(v, 3) for k, v in ranked},
            "script": dominant_script, "script_distribution": scripts,
            "mixed_language": mixed, "segment_languages": segments[:20],
            "note": "function-word/script heuristic; short or mixed text needs human confirmation"}


def op_locale(ctx, text: str | None = None, input_path: str | None = None,
              locale: str = "en-PH") -> dict:
    store = _store()
    if input_path:
        body = store.read_text(input_path)
    elif text:
        body = text
    else:
        raise store.ToolError(2, "provide --text or --input")
    family = locale.split("-")[0].lower()
    decimal_comma = family in ("de", "fr", "es", "pt", "id", "it", "nl", "tr")
    findings = []
    dates_us = re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", body)
    if dates_us:
        findings.append({"type": "ambiguous-numeric-date", "samples": dates_us[:5], "severity": "major",
                         "message": "use an unambiguous format such as 2024-03-05 or 5 March 2024"})
    for m in re.finditer(r"\b(\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d{3})+)\b", body):
        thousand_sep = "," if "," in m.group(1) else "."
        expected = "." if decimal_comma else ","
        if thousand_sep != expected:
            findings.append({"type": "thousand-separator", "sample": m.group(1), "expected": expected,
                             "severity": "minor"})
            break
    for m in re.finditer(r"\b(\d+),(\d+)\b", body):
        if decimal_comma:
            findings.append({"type": "decimal-separator", "sample": m.group(0), "expected": "comma decimal",
                             "severity": "minor"})
            break
        else:
            findings.append({"type": "decimal-separator", "sample": m.group(0), "expected": "period decimal",
                             "severity": "minor"})
            break
    currencies = set(re.findall(r"(?:₱|PHP|USD|\$|€|£|¥)", body))
    if len(currencies) > 1:
        findings.append({"type": "mixed-currency", "currencies": sorted(currencies), "severity": "major",
                         "message": "state the currency and conversion basis once"})
    times = re.findall(r"\b\d{1,2}:\d{2}\s?(?:AM|PM|am|pm)?\b", body)
    if any("AM" in t.upper() or "PM" in t.upper() for t in times):
        findings.append({"type": "12-hour-clock", "samples": times[:3], "severity": "minor",
                         "message": "state the time zone; 24-hour format avoids ambiguity"})
    return {"status": "ok", "locale": locale, "findings": findings,
            "decimal_comma_locale": decimal_comma,
            "passed": not [f for f in findings if f["severity"] == "major"]}


def op_transliterate(ctx, text: str | None = None, input_path: str | None = None) -> dict:
    store = _store()
    if input_path:
        body = store.read_text(input_path)
    elif text:
        body = text
    else:
        raise store.ToolError(2, "provide --text or --input")
    out_chars, mapped, unmapped = [], 0, set()
    for char in body:
        lower = char.lower()
        if lower in CYRILLIC_MAP:
            replacement = CYRILLIC_MAP[lower]
            out_chars.append(replacement.upper() if char.isupper() and replacement else replacement)
            mapped += 1
        elif lower in GREEK_MAP:
            replacement = GREEK_MAP[lower]
            out_chars.append(replacement.upper() if char.isupper() and replacement else replacement)
            mapped += 1
        else:
            out_chars.append(char)
            if ord(char) > 0x2000 and not unicodedata.category(char).startswith(("P", "Z")):
                unmapped.add(char)
    romanized = "".join(out_chars)
    return {"status": "ok", "romanized": romanized, "mapped_characters": mapped,
            "unmapped_scripts": sorted(unmapped)[:20],
            "original_preserved": body,
            "limitations": "Cyrillic and Greek tables only; Han, Arabic, Devanagari, Thai and Hangul "
                           "need an approved local romanization standard (not bundled)",
            "reversible": False}


# ---------------------------------------------------------------- production workflow

def op_plan(ctx, target_lang: str, src_lang: str = "en", domain: str | None = None,
            files: str | None = None, risk: str = "standard") -> dict:
    store = _store()
    conn = _conn(ctx)
    ids = [f.strip() for f in files.split(",")] if files else None
    sources = [dict(r) for r in conn.execute(
        "SELECT id,title,word_count,language FROM sources WHERE status!='deleted'")]
    if ids:
        sources = [s for s in sources if s["id"] in ids]
    words = sum(s["word_count"] or 0 for s in sources)
    tm_units = conn.execute("SELECT count(*) c FROM tm_units WHERE tgt_lang=?", (target_lang,)).fetchone()["c"]
    terms = conn.execute("SELECT count(*) c FROM terms").fetchone()["c"]
    roles = ["translator", "bilingual reviewer", "subject-matter reviewer", "proofreader",
             "functional QA", "final approver"]
    acceptance = {"language_score_min": float(ctx.cfg.get("translation", {}).get("acceptance_threshold", 95)),
                  "critical_errors_max": 0, "terminology_compliance": "100% of approved terms",
                  "qa_gate": "no critical findings"}
    return {"status": "ok",
            "project": {"src_lang": src_lang, "target_lang": target_lang, "domain": domain,
                        "risk": risk, "files": [s["id"] for s in sources], "words": words},
            "assets": {"tm_units": tm_units, "terms": terms,
                       "style_guide": "asset-style-guide.json"},
            "roles": roles, "acceptance_criteria": acceptance,
            "schedule_hint": {"words_per_day_estimate": 2000,
                              "days": round(words / 2000, 1) if words else 0},
            "risks": (["high-risk content: mandatory subject-matter review"] if risk == "high" else
                      ["terminology gaps: validate extracted terms before translation"]),
            "next_commands": ["translate pretranslate --input <file>",
                              "translate mt --input <file> --tm-only",
                              "translate qa --input <bilingual.json>"]}


def op_pretranslate(ctx, input_path: str, src_lang: str = "en") -> dict:
    """Source-side pre-translation editing checks."""
    store = _store()
    body = store.read_text(input_path)
    issues = []
    for sent in store.sentences(body):
        if store.words(sent) > 45:
            issues.append({"type": "overlong-sentence", "severity": "major", "sentence": sent[:160],
                           "message": "split long sentences before translation"})
        if re.search(r"\b(it|this|that|they)\b", sent, re.I) and not re.search(r"[A-Z][a-z]{2,}", sent):
            issues.append({"type": "unclear-reference", "severity": "minor", "sentence": sent[:160]})
        if re.search(r"\b(and/or|etc\.|i\.e\.|e\.g\.)", sent):
            issues.append({"type": "ambiguous-source", "severity": "minor", "sentence": sent[:160]})
    acronyms = set(re.findall(r"\b[A-Z]{2,6}\b", body))
    undefined = [a for a in acronyms if not re.search(rf"\b[A-Z][\w\- ]{{4,60}}\s\({a}\)", body)]
    variables = sorted(set(re.findall(r"\{[^{}]+\}|%[sdif]|\$\{[^{}]+\}", body)))
    if undefined:
        issues.append({"type": "undefined-acronym", "severity": "major", "acronyms": sorted(undefined)[:10]})
    return {"status": "ok", "issues": issues[:100], "issue_count": len(issues),
            "protected_variables": variables, "source_language": src_lang,
            "note": "fix the source before translating; ambiguous source produces ambiguous target"}


def op_query(ctx, action: str = "add", question: str | None = None, resolution: str | None = None,
             owner: str | None = None) -> dict:
    store = _store()
    conn = _conn(ctx)
    if action == "add":
        if not question:
            raise store.ToolError(2, "--question is required")
        qid = f"tq-{store.sha256_text(question)[:12]}"
        conn.execute("""INSERT OR REPLACE INTO decisions(id,kind,summary,rationale,actor,ref,created)
            VALUES(?,?,?,?,?,?,?)""",
                     (qid, "translation-query", question, resolution or "", owner or ctx.actor, None,
                      store.now_iso()))
        ctx.commit()
        return {"status": "ok", "query_id": qid, "status_flag": "open" if not resolution else "resolved"}
    if action == "list":
        rows = [dict(r) for r in conn.execute(
            "SELECT id,summary,rationale,actor,created FROM decisions WHERE kind='translation-query' "
            "ORDER BY created DESC LIMIT 100")]
        return {"status": "ok", "count": len(rows), "queries": rows,
                "open": sum(1 for r in rows if not r["rationale"])}
    if action == "resolve":
        if not question or not resolution:
            raise store.ToolError(2, "--question and --resolution are required")
        qid = f"tq-{store.sha256_text(question)[:12]}"
        conn.execute("UPDATE decisions SET rationale=? WHERE id=?", (resolution, qid))
        ctx.commit()
        return {"status": "ok", "query_id": qid, "resolution": resolution}
    raise store.ToolError(2, "action must be add|list|resolve")


def op_publish_check(ctx, project_path: str) -> dict:
    """Confirm every language edition reflects the same approved source version."""
    store = _store()
    if not os.path.exists(project_path):
        raise store.ToolError(2, f"project file not found: {project_path}")
    project = json.loads(store.read_text(project_path))
    conn = _conn(ctx)
    issues, editions = [], []
    source_id = project.get("source_id")
    recorded = project.get("source_checksum")
    if source_id:
        src = store.get_source(conn, source_id)
        if not src:
            issues.append({"type": "unknown-source", "source_id": source_id})
        else:
            if recorded and src["checksum"] != recorded:
                issues.append({"type": "source-version-drift", "recorded": recorded,
                               "current": src["checksum"],
                               "message": "the source changed after translation started"})
            if src.get("status") in ("superseded", "replaced"):
                issues.append({"type": "superseded-source", "status": src["status"]})
    for edition in project.get("editions", []):
        if isinstance(edition, str):
            edition = {"lang": edition}
        lang = edition.get("lang")
        if not lang:
            issues.append({"type": "edition-without-language"})
            continue
        if not edition.get("reviewed_by"):
            issues.append({"type": "unreviewed-edition", "lang": lang})
        if edition.get("source_checksum") and recorded and edition["source_checksum"] != recorded:
            issues.append({"type": "edition-mismatch", "lang": lang,
                           "edition_checksum": edition["source_checksum"], "expected": recorded})
        editions.append({"lang": lang, "adapted": bool(edition.get("adaptations")),
                         "adaptations": edition.get("adaptations", []),
                         "reviewed_by": edition.get("reviewed_by")})
    return {"status": "ok", "editions": editions, "issues": issues,
            "consistent": not issues,
            "note": "declared adaptations (e.g. legal examples localised) must be recorded per edition"}


def register(groups, common):
    g = groups["translate"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("terms", "Manage the terminology base", op_terms,
        action=(("--action",), dict(default="list", choices=["add", "list", "get"])),
        term=(("--term",), dict(default=None)), lang=(("--lang",), dict(default="en")),
        definition=(("--definition",), dict(default=None)),
        equivalents=(("--equivalents",), dict(default=None)),
        forbidden=(("--forbidden",), dict(default=None)),
        domain=(("--domain",), dict(default=None)), owner=(("--owner",), dict(default=None)))
    sub("term-extract", "Extract candidate terms for expert validation", op_term_extract,
        input_path=(("--input", "-i"), dict(required=True)),
        language=(("--language", "--lang"), dict(default="en")),
        min_freq=(("--min-freq",), dict(type=int, default=2)),
        max_terms=(("--max-terms",), dict(type=int, default=50)))
    sub("term-check", "Check terminology compliance between source and target", op_term_check,
        source=(("--source",), dict(required=True)), target=(("--target",), dict(required=True)),
        src_lang=(("--src-lang",), dict(default="en")), tgt_lang=(("--tgt-lang",), dict(default="fil")))
    sub("tm", "Manage the translation memory (add|list|import|export|match)", op_tm,
        action=(("--action",), dict(default="list",
                                    choices=["add", "list", "import", "export", "match", "clean"])),
        source=(("--source",), dict(default=None)), target=(("--target",), dict(default=None)),
        src_lang=(("--src-lang",), dict(default="en")), tgt_lang=(("--tgt-lang",), dict(default="fil")),
        input_path=(("--input", "-i"), dict(default=None)), output=(("--output", "-o"), dict(default=None)),
        domain=(("--domain",), dict(default=None)), threshold=(("--threshold",), dict(type=float, default=0.7)),
        limit=(("--limit",), dict(type=int, default=5)), project=(("--project",), dict(default=None)),
        apply_changes=(("--apply",), dict(action="store_true")),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("align", "Align bilingual sentences into translation units", op_align,
        source_path=(("--source-file",), dict(required=True)),
        target_path=(("--target-file",), dict(required=True)),
        src_lang=(("--src-lang",), dict(default="en")), tgt_lang=(("--tgt-lang",), dict(default="fil")),
        output=(("--output", "-o"), dict(default=None)),
        add_to_tm=(("--add-to-tm",), dict(action="store_true")))
    sub("qa", "Run mechanical translation QA over a bilingual file", op_qa,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)),
        fail_on=(("--fail-on",), dict(default="critical", choices=["minor", "major", "critical"])))
    sub("score", "Compute the severity-weighted language score", op_score,
        input_path=(("--input", "-i"), dict(required=True)),
        output=(("--output", "-o"), dict(default=None)),
        human_errors=(("--human-errors",), dict(default=None)))
    sub("mt", "Translate locally through the configured MT adapter (or TM prefill)", op_mt,
        input_path=(("--input", "-i"), dict(required=True)),
        src_lang=(("--src-lang",), dict(default="en")), tgt_lang=(("--tgt-lang",), dict(default="fil")),
        output=(("--output", "-o"), dict(default=None)),
        tm_only=(("--tm-only",), dict(action="store_true")),
        domain=(("--domain",), dict(default=None)),
        max_segments=(("--max-segments",), dict(type=int, default=500)))
    sub("backtranslate", "Back-translate high-risk segments and compare", op_backtranslate,
        input_path=(("--input", "-i"), dict(required=True)),
        src_lang=(("--src-lang",), dict(default="en")), tgt_lang=(("--tgt-lang",), dict(default="fil")))
    sub("langid", "Identify language, script and mixed-language spans", op_langid,
        text=(("--text",), dict(default=None)), input_path=(("--input", "-i"), dict(default=None)))
    sub("locale", "Check locale-specific numbers, dates and currency", op_locale,
        text=(("--text",), dict(default=None)), input_path=(("--input", "-i"), dict(default=None)),
        locale=(("--locale",), dict(default="en-PH")))
    sub("transliterate", "Romanize Cyrillic/Greek text (other scripts need an approved standard)",
        op_transliterate, text=(("--text",), dict(default=None)),
        input_path=(("--input", "-i"), dict(default=None)))
    sub("plan", "Create a translation project plan", op_plan,
        target_lang=(("--target-lang",), dict(required=True)),
        src_lang=(("--src-lang",), dict(default="en")), domain=(("--domain",), dict(default=None)),
        files=(("--files",), dict(default=None)),
        risk=(("--risk",), dict(default="standard", choices=["standard", "high"])))
    sub("pretranslate", "Source-side pre-translation editing checks", op_pretranslate,
        input_path=(("--input", "-i"), dict(required=True)),
        src_lang=(("--src-lang",), dict(default="en")))
    sub("query", "Record, list or resolve translation queries", op_query,
        action=(("--action",), dict(default="add", choices=["add", "list", "resolve"])),
        question=(("--question",), dict(default=None)), resolution=(("--resolution",), dict(default=None)),
        owner=(("--owner",), dict(default=None)))
    sub("script-check", "RTL, CJK and complex-script typography checks", op_script_check,
        input_path=(("--input", "-i"), dict(default=None)), text=(("--text",), dict(default=None)),
        lang=(("--lang",), dict(default="ar")))
    sub("publish-check", "Verify language editions match the approved source version", op_publish_check,
        project_path=(("--project",), dict(required=True)))
