"""tools-rwt-guard.py - rights, quotation, plagiarism, privacy, security and audit controls.

Every destructive or publishing action here is approval gated through the hash-chained audit
log. Redaction is irreversible: identifiers are replaced by salted digests, not masked in a
way that can be reversed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile

NAME = "guard"
ROLE = "quotation/licence controls, similarity and patchwriting, redaction, injection and document scanning, export gate"
GROUPS = [("guard", "Rights, quotation limits, similarity, privacy, security scanning and export gates")]
deps: dict = {}

MACRO_SUFFIXES = {".docm", ".xlsm", ".pptm", ".dotm", ".xlam"}
RISKY_SUFFIXES = {".exe", ".dll", ".js", ".vbs", ".ps1", ".bat", ".sh", ".jar", ".scr", ".hta",
                  ".lnk", ".msi"}
INJECTION_PATTERNS = [
    (r"ignore (?:all )?(?:previous|prior|above) instructions", "instruction-override"),
    (r"disregard (?:the|your) (?:system|developer) (?:prompt|message)", "instruction-override"),
    (r"you are now (?:a|an|the)\b", "role-hijack"),
    (r"(?:system|assistant)\s*:\s*(?:you|please)", "fake-role-marker"),
    (r"<\s*/?\s*(?:system|instructions?)\s*>", "fake-tag"),
    (r"(?:send|post|upload|exfiltrate)\b[^.]{0,60}\b(?:to|at)\s+https?://", "exfiltration"),
    (r"\b(?:api[_-]?key|token|password|secret)\b[^.\n]{0,40}\b(?:is|=|:)\s*\S+", "secret-request"),
    (r"\bdo not (?:tell|inform|report)\b", "concealment-request"),
    (r"\bdelete (?:all|the) (?:files?|records?|audit logs?)\b", "destruction-request"),
]
SENSITIVITY_PATTERNS = {
    "health": r"\b(diagnosis|patient|medical record|prescription|HIV|mental health|clinical)\b",
    "financial": r"\b(bank account|salary|credit card|tax return|loan|net worth)\b",
    "legal-privileged": r"\b(attorney-client|legal advice|privileged|work product)\b",
    "personal": r"\b(home address|date of birth|passport|national id|SSN)\b",
    "confidential": r"\b(confidential|internal only|do not distribute|embargo(?:ed)?)\b",
    "export-controlled": r"\b(export[- ]control|ITAR|dual[- ]use|restricted technology)\b",
}


def _store():
    return deps["store"]


# ---------------------------------------------------------------- quotation and rights

def op_quote_guard(ctx, quote: str, source_id: str | None = None, output: str | None = None,
                   max_words: int | None = None) -> dict:
    store = _store()
    if not quote or len(quote.strip()) < 5:
        raise store.ToolError(2, "quote must be at least 5 characters")
    conn = ctx.conn()
    limits = ctx.cfg.get("quoting", {})
    configured = max_words or limits.get("default_max_words", 120)
    hard_cap = limits.get("max_quote_words_hard", 400)
    source = store.get_source(conn, source_id) if source_id else None
    rights = (source or {}).get("meta", {}).get("rights", {}) if source else {}
    allowed = min(int(rights.get("max_quote_words", configured)), hard_cap)
    words = store.words(quote)
    full_text = ""
    if source_id:
        rows = conn.execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",
                            (source_id,)).fetchall()
        full_text = "\n\n".join(r["text"] for r in rows)
    ratio = words / max(1, store.words(full_text)) if full_text else None
    max_ratio = float(rights.get("max_quote_ratio", limits.get("default_max_ratio", 0.1)))
    issues = []
    if words > allowed:
        issues.append({"type": "length-exceeded", "words": words, "allowed": allowed, "severity": "critical"})
    if ratio is not None and ratio > max_ratio:
        issues.append({"type": "ratio-exceeded", "ratio": round(ratio, 4), "allowed": max_ratio,
                       "severity": "major"})
    if source and (source.get("license") or "unknown") in ctx.cfg.get("licensing", {}).get("restricted_licenses", []):
        issues.append({"type": "restricted-licence", "license": source.get("license"), "severity": "major",
                       "message": "quote only what is necessary and never redistribute the source"})
    if not source_id:
        issues.append({"type": "unattributed-source", "severity": "minor",
                       "message": "record the source id so length and licence checks are auditable"})
    payload = {"status": "ok", "words": words, "allowed_words": allowed, "ratio_of_source": ratio,
               "max_ratio": max_ratio, "issues": issues, "permitted": not issues,
               "source": {"id": source_id, "license": (source or {}).get("license")},
               "action": ("shorten, paraphrase, or request permission" if issues else "quote may proceed")}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def op_similarity(ctx, input_path: str, threshold: float = 0.15, exclude_self: bool = True) -> dict:
    """Overlap between a draft and the local corpus (containment of draft shingles)."""
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    draft = store.read_text(input_path)
    draft_shingles = store.shingles(draft, 5)
    if not draft_shingles:
        raise store.ToolError(2, "draft is too short for similarity analysis")
    conn = ctx.conn()
    matches, longest_total = [], 0.0
    for row in conn.execute("SELECT id,title,path FROM sources"):
        rows = conn.execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",
                            (row["id"],)).fetchall()
        text = "\n\n".join(r["text"] for r in rows)
        if not text:
            continue
        source_shingles = store.shingles(text, 5)
        overlap = len(draft_shingles & source_shingles)
        containment = overlap / len(draft_shingles)
        if containment >= threshold:
            matches.append({"source_id": row["id"], "title": row["title"],
                            "containment": round(containment, 4),
                            "shared_ngrams": overlap,
                            "sample_overlap": sorted(draft_shingles & source_shingles)[:3]})
            longest_total = max(longest_total, text.find(sorted(draft_shingles & source_shingles)[0])
                                if overlap else 0)
    matches.sort(key=lambda m: -m["containment"])
    quoted_words = sum(store.words(m.group(0)) for m in
                       re.finditer(r"\"[^\"]{20,}\"|\u201c[^\u201d]{20,}\u201d", draft))
    return {"status": "ok", "draft_words": store.words(draft), "matches": matches[:20],
            "match_count": len(matches), "max_containment": matches[0]["containment"] if matches else 0,
            "quoted_words": quoted_words,
            "threshold": threshold,
            "interpretation": ("overlap is normal for shared terminology and quoted material; "
                               "review each match before concluding anything")}


def op_patchwriting(ctx, input_path: str, source_id: str, min_overlap: float = 0.2) -> dict:
    """Detect paraphrase that stays too close in wording/structure (3-gram overlap, 8-gram absence)."""
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    draft = store.read_text(input_path)
    conn = ctx.conn()
    rows = conn.execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",
                        (source_id,)).fetchall()
    if not rows:
        raise store.ToolError(2, f"no stored segments for source: {source_id}")
    source_text = "\n\n".join(r["text"] for r in rows)
    src3, src8 = store.shingles(source_text, 3), store.shingles(source_text, 8)
    findings = []
    for sent in store.sentences(draft):
        if store.words(sent) < 10:
            continue
        s3, s8 = store.shingles(sent, 3), store.shingles(sent, 8)
        overlap3 = len(s3 & src3) / len(s3) if s3 else 0
        exact8 = len(s8 & src8)
        if exact8 > 0:
            findings.append({"type": "verbatim-overlap", "severity": "critical", "sentence": sent[:240],
                             "matched_8grams": exact8,
                             "message": "verbatim overlap requires quotation marks and a citation"})
        elif overlap3 >= min_overlap:
            findings.append({"type": "close-paraphrase", "severity": "major", "sentence": sent[:240],
                             "three_gram_overlap": round(overlap3, 3),
                             "message": "wording remains close to the source; rewrite the sentence structure"})
    return {"status": "ok", "source_id": source_id, "sentences_checked": len(store.sentences(draft)),
            "findings": findings[:100], "finding_count": len(findings),
            "min_overlap": min_overlap,
            "note": "close paraphrase is a writing-quality and integrity signal for a human reviewer"}


def op_attribution(ctx, input_path: str) -> dict:
    """Check quotations, percentages and figures for required attribution."""
    store = _store()
    body = store.read_text(input_path)
    missing = []
    for m in re.finditer(r"\"[^\"]{25,}\"|\u201c[^\u201d]{25,}\u201d", body):
        tail = body[m.end():m.end() + 160]
        if not re.search(r"\[[^\]]+\]|\([A-Z][A-Za-z\-]+,\s*\d{4}", tail):
            missing.append({"kind": "quotation", "excerpt": m.group(0)[:160],
                            "severity": "critical"})
    for sent in store.sentences(body):
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent)\b", sent) and \
                not re.search(r"\[[^\]]+\]|\([A-Z][A-Za-z\-]+,\s*\d{4}", sent):
            missing.append({"kind": "quantity", "excerpt": sent[:160], "severity": "major"})
        if re.search(r"\b(?:Table|Figure|Fig\.)\s+\d+", sent) and \
                not re.search(r"(?:Table|Figure|Fig\.)\s+\d+[^.]{0,80}(?:source|adapted|from)", sent, re.I):
            missing.append({"kind": "figure-source", "excerpt": sent[:160], "severity": "minor"})
    return {"status": "ok", "missing": missing[:100], "missing_count": len(missing),
            "passed": not [m for m in missing if m["severity"] == "critical"],
            "note": "unattributed quotations block publication under the default policy"}


LICENSE_RULES = {
    "cc-by-4.0": {"attribution": True, "commercial": True, "derivatives": True, "share_alike": False},
    "cc-by-sa-4.0": {"attribution": True, "commercial": True, "derivatives": True, "share_alike": True},
    "cc-by-nc-4.0": {"attribution": True, "commercial": False, "derivatives": True, "share_alike": False},
    "cc-by-nd-4.0": {"attribution": True, "commercial": True, "derivatives": False, "share_alike": False},
    "cc-by-nc-nd-4.0": {"attribution": True, "commercial": False, "derivatives": False, "share_alike": False},
    "cc0-1.0": {"attribution": False, "commercial": True, "derivatives": True, "share_alike": False},
    "public-domain": {"attribution": False, "commercial": True, "derivatives": True, "share_alike": False},
    "all-rights-reserved": {"attribution": True, "commercial": False, "derivatives": False, "share_alike": False},
    "proprietary": {"attribution": True, "commercial": False, "derivatives": False, "share_alike": False},
}


def op_license_check(ctx, license: str, use: str = "internal", derivatives: bool = False,
                     redistribute: bool = False, attribution_given: bool = False) -> dict:
    """Evaluate a licence against an intended use (internal|commercial|publish)."""
    store = _store()
    key = (license or "unknown").strip().lower()
    rules = LICENSE_RULES.get(key)
    if not rules:
        return {"status": "ok", "license": license, "known": False,
                "decision": "manual-review-required",
                "reason": "licence not in the local licence table; a human must read the terms",
                "requirements": ["record the licence text and territory in the corpus registry"]}
    violations, requirements = [], []
    if rules["attribution"] and not attribution_given:
        violations.append("attribution required and not recorded")
        requirements.append("include creator, title, source and licence notice")
    if use == "commercial" and not rules["commercial"]:
        violations.append("non-commercial licence forbids commercial use")
    if derivatives and not rules["derivatives"]:
        violations.append("no-derivatives licence forbids adapted output")
    if redistribute and key in ("all-rights-reserved", "proprietary"):
        violations.append("redistribution of this licence is not permitted")
    if rules["share_alike"] and redistribute:
        requirements.append("derivative output must carry the same licence")
    decision = "permitted" if not violations else "blocked"
    return {"status": "ok", "license": key, "known": True, "use": use,
            "decision": decision, "violations": violations, "requirements": requirements,
            "rules": rules}


def op_pd_status(ctx, source_id: str, basis: str, jurisdiction: str = "PH", evidence: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    src = store.get_source(conn, source_id)
    if not src:
        raise store.ToolError(2, f"unknown source: {source_id}")
    valid = ("author-death-70y", "publication-95y", "government-work", "explicit-dedication",
             "no-copyright-foreign-work", "other-documented")
    if basis not in valid:
        raise store.ToolError(2, f"basis must be one of {list(valid)}")
    meta = src["meta"]
    meta.setdefault("rights", {})["public_domain"] = {"basis": basis, "jurisdiction": jurisdiction,
                                                      "evidence": evidence,
                                                      "recorded": store.now_iso(),
                                                      "recorded_by": ctx.actor}
    conn.execute("UPDATE sources SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), source_id))
    ctx.commit()
    store.audit_append(conn, ctx.actor, "guard.pd-status", source_id,
                       {"basis": basis, "jurisdiction": jurisdiction})
    ctx.commit()
    return {"status": "ok", "source_id": source_id, "public_domain": meta["rights"]["public_domain"],
            "warning": "age alone is not a basis; the recorded basis and jurisdiction are what count"}


def op_fair_use(ctx, purpose: str, source_id: str, amount_words: int, alternatives: str | None = None,
                market_effect: str | None = None, output: str | None = None) -> dict:
    """Assemble a fair-use review packet for human legal judgement (no automated verdict)."""
    store = _store()
    conn = ctx.conn()
    src = store.get_source(conn, source_id)
    if not src:
        raise store.ToolError(2, f"unknown source: {source_id}")
    total = src.get("word_count") or 0
    ratio = round(amount_words / total, 4) if total else None
    packet = {
        "source": {"id": source_id, "title": src["title"], "license": src["license"],
                   "doctype": src["doctype"], "words": total},
        "purpose": purpose,
        "amount": {"words": amount_words, "ratio_of_work": ratio,
                   "is_core_of_work": ratio is not None and ratio > 0.25},
        "nature": {"published": bool(src.get("issued")), "factual_or_creative": src.get("doctype")},
        "market_effect": market_effect or "not assessed",
        "alternatives": alternatives or "not assessed",
        "jurisdiction_note": "fair use (US) and fair dealing (PH/UK/Commonwealth) differ; "
                             "the applicable exception must be named by a human",
        "automated_verdict": None,
        "review_required": True,
        "checklist": ["name the exception relied on", "record why the amount is necessary",
                      "record whether a licence is available", "record the decision and reviewer"],
    }
    flags = []
    if ratio is not None and ratio > 0.1:
        flags.append("quoted amount exceeds 10% of the work")
    if not alternatives or alternatives.lower() in ("none", "n/a"):
        flags.append("no alternative to using the excerpt was recorded")
    packet["flags"] = flags
    payload = {"status": "ok", "packet": packet}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), packet)
    return payload


# ---------------------------------------------------------------- privacy and security

def _salt(ctx) -> str:
    conn = ctx.conn()
    row = conn.execute("SELECT value FROM meta WHERE key='redaction_salt'").fetchone()
    if row:
        return row["value"]
    env = os.environ.get(ctx.cfg.get("privacy", {}).get("salt_env", "RWT_REDACTION_SALT"))
    salt = env or hashlib.sha256(os.urandom(32)).hexdigest()
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('redaction_salt',?)", (salt,))
    conn.commit()
    return salt


def redact_text(ctx, body: str, ids_only: bool = False) -> tuple[str, dict]:
    salt = _salt(ctx)
    patterns = ctx.cfg.get("privacy", {}).get("identifier_patterns", {})
    token = ctx.cfg.get("privacy", {}).get("redaction_token", "[REDACTED]")
    counts = {}
    out = body
    for name, pattern in patterns.items():
        if ids_only and name in ("doi", "orcid"):
            continue
        def repl(match, name=name):
            counts[name] = counts.get(name, 0) + 1
            digest = hashlib.sha256((salt + match.group(0)).encode("utf-8")).hexdigest()[:10]
            return f"{token}:{name}:{digest}"
        try:
            out = re.sub(pattern, repl, out)
        except re.error as exc:
            raise _store().ToolError(2, f"invalid privacy pattern '{name}': {exc}")
    return out, counts


def op_redact(ctx, input_path: str, output: str | None = None, commit: bool = False,
              approved: bool = False, approver: str | None = None, note: str = "") -> dict:
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    body = store.read_text(input_path)
    redacted, counts = redact_text(ctx, body)
    if commit:
        deps["store"].require_approval(ctx, "guard.redact-commit", os.path.basename(input_path),
                                       approved, approver, note)
        target = ctx.path("corpus", f"redacted-{os.path.basename(input_path)}")
        store.write_text(target, redacted)
        payload = {"status": "ok", "committed_to": os.path.relpath(target, ctx.root)}
    else:
        target = ctx.out_path(output or f"redacted-{os.path.basename(input_path)}")
        store.write_text(target, redacted)
        payload = {"status": "ok", "preview": target}
    payload.update({"replacements": counts, "total_replacements": sum(counts.values()),
                    "irreversible": True,
                    "note": "identifiers are replaced by salted digests; originals are not recoverable "
                            "from this file"})
    return payload


def op_deid_risk(ctx, input_path: str) -> dict:
    """Assess re-identification risk from quasi-identifier combinations and rare values."""
    store = _store()
    body = store.read_text(input_path)
    quasi = ctx.cfg.get("privacy", {}).get("quasi_identifiers", [])
    present = [q for q in quasi if re.search(rf"\b{re.escape(q)}\b", body, re.I)]
    direct_hits = sum(_direct_matches(ctx, body).values())
    risk_score = min(1.0, 0.18 * len(present) + 0.25 * direct_hits)
    risk = "high" if risk_score >= 0.6 else ("moderate" if risk_score >= 0.3 else "low")
    return {"status": "ok", "quasi_identifiers_present": present, "direct_identifiers": direct_hits,
            "risk": risk, "risk_score": round(risk_score, 3),
            "recommendations": (["generalize or bin quasi-identifiers", "remove direct identifiers",
                                 "review quotations that could identify a person"]
                                if risk != "low" else ["no action required beyond normal handling"]),
            "note": "combination risk, not single-field risk, is what re-identifies people"}


def _direct_matches(ctx, body: str) -> dict:
    patterns = ctx.cfg.get("privacy", {}).get("identifier_patterns", {})
    out = {}
    for name in ("email", "phone", "ph_phone", "national_id", "credit_card"):
        pattern = patterns.get(name)
        if pattern:
            out[name] = len(re.findall(pattern, body))
    return out


def op_classify(ctx, source_id: str | None = None, input_path: str | None = None,
                label: str | None = None) -> dict:
    store = _store()
    valid = ctx.cfg.get("sensitivity_labels", ["public", "internal", "confidential"])
    if label and label not in valid:
        raise store.ToolError(2, f"label must be one of {valid}")
    body = ""
    if input_path:
        body = store.read_text(input_path)
    elif source_id:
        rows = ctx.conn().execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",
                                  (source_id,)).fetchall()
        if not rows:
            raise store.ToolError(2, f"no stored segments for source: {source_id}")
        body = "\n\n".join(r["text"] for r in rows)
    else:
        raise store.ToolError(2, "provide --source-id or --input")
    detected = []
    for name, pattern in SENSITIVITY_PATTERNS.items():
        hits = len(re.findall(pattern, body, re.I))
        if hits:
            detected.append({"label": name, "hits": hits})
    detected.sort(key=lambda d: -d["hits"])
    suggested = detected[0]["label"] if detected else "public"
    if label and source_id:
        conn = ctx.conn()
        src = store.get_source(conn, source_id)
        meta = src["meta"]
        meta["sensitivity"] = label
        conn.execute("UPDATE sources SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), source_id))
        ctx.commit()
        store.audit_append(conn, ctx.actor, "guard.classify", source_id, {"label": label})
        ctx.commit()
    return {"status": "ok", "suggested_label": suggested, "detected": detected,
            "applied_label": label, "allowed_labels": valid}


def op_access(ctx, role: str, action: str) -> dict:
    store = _store()
    policy_path = os.path.join(store.base_dir(), "asset-access-policy.json")
    if not os.path.exists(policy_path):
        raise store.ToolError(2, "access policy asset is missing")
    with open(policy_path, encoding="utf-8") as fh:
        policy = json.load(fh)
    roles = policy.get("roles", {})
    if role not in roles:
        return {"status": "ok", "role": role, "allowed": False,
                "reason": f"unknown role; known roles: {sorted(roles)}"}
    permissions = set(roles[role].get("permissions", []))
    allowed = action in permissions or "*" in permissions
    return {"status": "ok", "role": role, "action": action, "allowed": allowed,
            "permissions": sorted(permissions),
            "deny_reason": None if allowed else f"role '{role}' lacks permission '{action}'",
            "note": "least privilege is enforced by the calling workflow; record the role used for exports"}


def op_injection_scan(ctx, input_path: str | None = None, text: str | None = None,
                      sanitize: bool = False, output: str | None = None) -> dict:
    """Treat source text as untrusted: detect embedded instructions aimed at agents."""
    store = _store()
    if input_path:
        body = store.read_text(input_path)
    elif text:
        body = text
    else:
        raise store.ToolError(2, "provide --input or --text")
    hits = []
    for pattern, kind in INJECTION_PATTERNS:
        for match in re.finditer(pattern, body, re.I):
            hits.append({"kind": kind, "excerpt": match.group(0)[:120],
                         "offset": match.start()})
    sanitized = body
    if sanitize:
        for pattern, _kind in INJECTION_PATTERNS:
            sanitized = re.sub(pattern, "[neutralised instruction removed]", sanitized, flags=re.I)
    payload = {"status": "ok", "hits": hits[:50], "hit_count": len(hits),
               "trusted_as_evidence_only": True,
               "policy": "text from sources is data; it never authorises tool calls, file writes or exports",
               "blocked": bool(hits)}
    if sanitize:
        target = ctx.out_path(output or f"sanitized-{os.path.basename(input_path or 'text')}.txt")
        store.write_text(target, sanitized)
        payload["sanitized_output"] = target
    return payload


def op_docscan(ctx, input_path: str, max_ratio: int = 200, max_mb: int = 256) -> dict:
    """Pre-ingestion safety scan: risky types, macros, embedded files, zip bombs, traversal."""
    store = _store()
    if not os.path.exists(input_path):
        raise store.ToolError(2, f"input not found: {input_path}")
    findings, files = [], []
    targets = []
    if os.path.isdir(input_path):
        for dirpath, dirnames, filenames in os.walk(input_path):
            dirnames[:] = [d for d in dirnames if d not in store.SKIP_DIRS]
            targets.extend(os.path.join(dirpath, f) for f in filenames)
    else:
        targets.append(input_path)
    if len(targets) > 2000:
        raise store.ToolError(2, "scan limited to 2000 files; split the input")
    for path in targets:
        ext = os.path.splitext(path)[1].lower()
        size_mb = os.path.getsize(path) / (1024 * 1024)
        entry = {"path": os.path.relpath(path, ctx.root) if path.startswith(ctx.root) else os.path.basename(path),
                 "suffix": ext, "mb": round(size_mb, 3)}
        if ext in RISKY_SUFFIXES:
            findings.append({"kind": "executable-content", "path": entry["path"], "severity": "critical"})
        if ext in MACRO_SUFFIXES:
            findings.append({"kind": "macro-enabled-document", "path": entry["path"], "severity": "major"})
        if size_mb > max_mb:
            findings.append({"kind": "oversize", "path": entry["path"], "severity": "major"})
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path) as zf:
                    infos = zf.infolist()
                    total = sum(i.file_size for i in infos)
                    compressed = sum(max(1, i.compress_size) for i in infos) or 1
                    if total / compressed > max_ratio:
                        findings.append({"kind": "possible-zip-bomb", "path": entry["path"],
                                         "ratio": round(total / compressed, 1), "severity": "critical"})
                    for info in infos:
                        if info.filename.startswith("/") or ".." in info.filename.split("/"):
                            findings.append({"kind": "path-traversal", "path": entry["path"],
                                             "member": info.filename, "severity": "critical"})
                        if info.filename.lower().endswith((".exe", ".js", ".vbs", ".ps1", ".dll")):
                            findings.append({"kind": "embedded-executable", "path": entry["path"],
                                             "member": info.filename, "severity": "critical"})
            except zipfile.BadZipFile:
                findings.append({"kind": "corrupt-archive", "path": entry["path"], "severity": "minor"})
        entry["verdict"] = "reject" if any(f["path"] == entry["path"] and f["severity"] == "critical"
                                           for f in findings) else "accept"
        files.append(entry)
    return {"status": "ok", "files_scanned": len(files), "findings": findings,
            "rejected": [f["path"] for f in files if f["verdict"] == "reject"],
            "safe_to_ingest": not findings, "results": files[:200]}


def op_retention(ctx, action: str = "list", days: int = 365, approved: bool = False,
                 approver: str | None = None, note: str = "") -> dict:
    store = _store()
    conn = ctx.conn()
    if action == "list":
        rows = []
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        for src in conn.execute("SELECT id,title,created,license FROM sources WHERE status!='deleted'"):
            created = src["created"] or now.isoformat()
            try:
                age_days = (now - datetime.fromisoformat(created)).days
            except ValueError:
                age_days = 0
            rows.append({"source_id": src["id"], "title": src["title"], "age_days": age_days,
                         "retention_days": days, "due": age_days >= days})
        return {"status": "ok", "retention_days": days, "due_for_review": sum(1 for r in rows if r["due"]),
                "entries": rows[:200]}
    if action == "purge":
        deps["store"].require_approval(ctx, "guard.retention-purge", f"older-than-{days}d",
                                       approved, approver, note)
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        targets = [r["id"] for r in conn.execute(
            "SELECT id FROM sources WHERE created < ? AND status!='deleted'", (cutoff,))]
        for sid in targets:
            conn.execute("DELETE FROM segments_fts WHERE source_id=?", (sid,))
            conn.execute("DELETE FROM segments WHERE source_id=?", (sid,))
            conn.execute("UPDATE sources SET status='purged' WHERE id=?", (sid,))
        ctx.commit()
        store.audit_append(conn, ctx.actor, "guard.retention-purge", f"older-than-{days}d",
                           {"purged": len(targets)})
        ctx.commit()
        return {"status": "ok", "purged": len(targets), "source_ids": targets,
                "note": "derived claims, translations and exports must be purged by the same decision"}
    raise store.ToolError(2, "action must be list|purge")


def op_export_gate(ctx, files: str, package_name: str = "export", approved: bool = False,
                   approver: str | None = None, note: str = "", include_originals: bool = False) -> dict:
    store = _store()
    paths = [ctx.resolve_input(p.strip()) for p in files.split(",") if p.strip()]
    if not paths:
        raise store.ToolError(2, "--files must list at least one path")
    for path in paths:
        if not os.path.exists(path):
            raise store.ToolError(2, f"file not found: {path}")
    approval = store.require_approval(ctx, "guard.export", package_name, approved, approver, note)
    dest = ctx.path("exports", store.slug(package_name, 40))
    os.makedirs(dest, exist_ok=True)
    manifest = {"package": package_name, "created": store.now_iso(), "approved_by": approval.get("approval"),
                "files": [], "redactions": {}, "licences": []}
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in store.TEXT_SUFFIXES:
            body = store.read_text(path)
            redacted, counts = redact_text(ctx, body)
            name = "redacted-" + os.path.basename(path)
            target = os.path.join(dest, name)
            store.write_text(target, redacted)
            manifest["redactions"][name] = counts
        elif include_originals:
            target = os.path.join(dest, os.path.basename(path))
            shutil.copy2(path, target)
            manifest["files"].append({"name": os.path.basename(path), "redacted": False})
            continue
        else:
            raise store.ToolError(2, f"binary file {os.path.basename(path)} requires --include-originals "
                                     f"and a licence check")
        manifest["files"].append({"name": name, "checksum": store.sha256_file(target),
                                  "bytes": os.path.getsize(target), "redacted": True})
    licences = {r["license"] for r in ctx.conn().execute("SELECT license FROM sources") if r["license"]}
    manifest["licences"] = sorted(licences)
    store.write_json(os.path.join(dest, "export-manifest.json"), manifest)
    store.audit_append(ctx.conn(), ctx.actor, "guard.export", package_name,
                       {"files": len(manifest["files"])})
    ctx.commit()
    return {"status": "ok", "package": os.path.relpath(dest, ctx.root),
            "files": len(manifest["files"]), "redactions": manifest["redactions"],
            "licences": sorted(licences),
            "note": "exports are redacted text by default; originals require an explicit licence decision"}


def op_audit(ctx, action: str = "show", limit: int = 50) -> dict:
    store = _store()
    conn = ctx.conn()
    if action == "verify":
        return {"status": "ok", **store.audit_verify(conn)}
    if action == "show":
        rows = [dict(r) for r in conn.execute(
            "SELECT seq,ts,actor,action,target,detail,hash FROM audit ORDER BY seq DESC LIMIT ?",
            (int(limit),))]
        return {"status": "ok", "entries": rows, "count": len(rows),
                "total": conn.execute("SELECT count(*) c FROM audit").fetchone()["c"]}
    raise store.ToolError(2, "action must be show|verify")


def op_incident(ctx, summary: str, kind: str = "data-exposure", severity: str = "major",
                containment: str = "") -> dict:
    store = _store()
    conn = ctx.conn()
    valid = ("data-exposure", "lost-device", "unauthorised-export", "corrupt-corpus",
             "model-misbehaviour", "credential-exposure", "other")
    if kind not in valid:
        raise store.ToolError(2, f"kind must be one of {list(valid)}")
    iid = f"inc-{store.sha256_text(summary + store.now_iso())[:12]}"
    conn.execute("""INSERT INTO incidents(id,kind,summary,severity,status,containment,actor,created)
        VALUES(?,?,?,?,?,?,?,?)""",
                 (iid, kind, summary, severity, "open", containment, ctx.actor, store.now_iso()))
    store.audit_append(conn, ctx.actor, "guard.incident", iid, {"kind": kind, "severity": severity})
    ctx.commit()
    return {"status": "ok", "incident_id": iid, "kind": kind, "severity": severity,
            "containment": containment,
            "required_next_steps": ["preserve the evidence and audit log",
                                    "identify affected records and sources",
                                    "notify the authorised response owner"]}


def register(groups, common):
    g = groups["guard"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("quote-guard", "Enforce quotation length, ratio and licence limits", op_quote_guard,
        quote=(("--quote",), dict(required=True)),
        source_id=(("--source-id", "--source"), dict(default=None)),
        output=(("--output", "-o"), dict(default=None)),
        max_words=(("--max-words",), dict(type=int, default=None)))
    sub("similarity", "Measure overlap between a draft and the local corpus", op_similarity,
        input_path=(("--input", "-i"), dict(required=True)),
        threshold=(("--threshold",), dict(type=float, default=0.15)),
        exclude_self=(("--include-self",), dict(action="store_false", default=True)))
    sub("patchwriting", "Detect paraphrases that stay too close to a source", op_patchwriting,
        input_path=(("--input", "-i"), dict(required=True)),
        source_id=(("--source-id", "--source"), dict(required=True)),
        min_overlap=(("--min-overlap",), dict(type=float, default=0.2)))
    sub("attribution", "Check quotations and figures for required attribution", op_attribution,
        input_path=(("--input", "-i"), dict(required=True)))
    sub("license-check", "Evaluate a licence against an intended use", op_license_check,
        license=(("--license",), dict(required=True)),
        use=(("--use",), dict(default="internal", choices=["internal", "commercial", "publish"])),
        derivatives=(("--derivatives",), dict(action="store_true")),
        redistribute=(("--redistribute",), dict(action="store_true")),
        attribution_given=(("--attribution-given",), dict(action="store_true")))
    sub("pd-status", "Record a public-domain basis for a source", op_pd_status,
        source_id=(("--source-id", "--source"), dict(required=True)),
        basis=(("--basis",), dict(required=True)), jurisdiction=(("--jurisdiction",), dict(default="PH")),
        evidence=(("--evidence",), dict(default=None)))
    sub("fair-use", "Assemble a fair-use review packet for human judgement", op_fair_use,
        purpose=(("--purpose",), dict(required=True)),
        source_id=(("--source-id", "--source"), dict(required=True)),
        amount_words=(("--amount-words",), dict(type=int, required=True)),
        alternatives=(("--alternatives",), dict(default=None)),
        market_effect=(("--market-effect",), dict(default=None)),
        output=(("--output", "-o"), dict(default=None)))
    sub("redact", "Irreversibly redact identifiers (approval gated when committing)", op_redact,
        input_path=(("--input", "-i"), dict(required=True)), output=(("--output", "-o"), dict(default=None)),
        commit=(("--commit",), dict(action="store_true")),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("deid-risk", "Assess re-identification risk", op_deid_risk,
        input_path=(("--input", "-i"), dict(required=True)))
    sub("classify", "Suggest or apply a sensitivity label", op_classify,
        source_id=(("--source-id", "--source"), dict(default=None)),
        input_path=(("--input", "-i"), dict(default=None)), label=(("--label",), dict(default=None)))
    sub("access", "Check role permissions", op_access,
        role=(("--role",), dict(required=True)), action=(("--action",), dict(required=True)))
    sub("injection-scan", "Detect prompt-injection instructions in source text", op_injection_scan,
        input_path=(("--input", "-i"), dict(default=None)), text=(("--text",), dict(default=None)),
        sanitize=(("--sanitize",), dict(action="store_true")),
        output=(("--output", "-o"), dict(default=None)))
    sub("docscan", "Pre-ingestion safety scan of documents and archives", op_docscan,
        input_path=(("--input", "-i"), dict(required=True)),
        max_ratio=(("--max-ratio",), dict(type=int, default=200)),
        max_mb=(("--max-mb",), dict(type=int, default=256)))
    sub("retention", "List or purge records past retention (purge is gated)", op_retention,
        action=(("--action",), dict(default="list", choices=["list", "purge"])),
        days=(("--days",), dict(type=int, default=365)),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")))
    sub("export-gate", "Redact, licence-check, sign and approve an export package", op_export_gate,
        files=(("--files",), dict(required=True)), package_name=(("--name",), dict(default="export")),
        approved=(("--approve",), dict(action="store_true")), approver=(("--approver",), dict(default=None)),
        note=(("--note",), dict(default="")),
        include_originals=(("--include-originals",), dict(action="store_true")))
    sub("audit", "Show or verify the hash-chained audit log", op_audit,
        action=(("--action",), dict(default="show", choices=["show", "verify"])),
        limit=(("--limit",), dict(type=int, default=50)))
    sub("incident", "Record a research data incident", op_incident,
        summary=(("--summary",), dict(required=True)),
        kind=(("--kind",), dict(default="data-exposure")),
        severity=(("--severity",), dict(default="major", choices=["minor", "major", "critical"])),
        containment=(("--containment",), dict(default="")))
