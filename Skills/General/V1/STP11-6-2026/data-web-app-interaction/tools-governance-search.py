#!/usr/bin/env python3
"""Categories 1-2 (20 capabilities): Authorization, Safe Operation, and Web Search/Discovery."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import collections
import datetime as dt
import difflib
import json
import math
import re
import unicodedata
import urllib.parse
import urllib.request
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


def _read_text_or_default(input_path: Optional[Path], params: Dict[str, Any], default_rel: str = "data-web-app-interaction/asset-sample-page.html") -> Tuple[str, str]:
    if isinstance(params.get("text"), str) and params["text"]:
        return params["text"], "inline_text"
    target = input_path if input_path is not None else core.resolve_package_resource(default_rel)
    if target.is_dir():
        files = sorted(p for p in target.iterdir() if p.is_file())[:10]
        combined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
        return combined, str(target)
    return target.read_text(encoding="utf-8", errors="replace"), str(target)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = collections.Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


# --- Category 1 Handlers ---

def handle_action_authorization_gatekeeper(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    policy = ctx["policy"]
    target = str(params.get("target", ctx.get("input_path") or "data-web-app-interaction/asset-sample-page.html"))
    environment = str(params.get("environment", "sandbox")).lower()
    owner_confirmed = bool(params.get("owner_confirmed", True if environment in ("sandbox", "local", "test") else False))
    account = str(params.get("account", "anonymous-readonly"))
    requested_actions = params.get("actions", ["read", "inspect"])
    if isinstance(requested_actions, str):
        requested_actions = [requested_actions]

    ro_ops = set(policy.get("read_only_operations", []))
    cons_ops = set(policy.get("consequential_operations", []))
    allowed_actions = []
    blocked_actions = []
    for act in requested_actions:
        act_l = str(act).lower().strip()
        if act_l in ro_ops:
            allowed_actions.append(act_l)
        elif act_l in cons_ops and owner_confirmed and (ctx["approve"] or params.get("explicit_permission", False)):
            allowed_actions.append(act_l)
        else:
            blocked_actions.append(act_l)

    authorized = len(blocked_actions) == 0 and (owner_confirmed or all(a in ro_ops for a in allowed_actions))
    return {
        "observation_mode": "directly_observed",
        "data": {
            "authorized": authorized,
            "target": target,
            "account": account,
            "environment": environment,
            "owner_confirmed": owner_confirmed,
            "allowed_actions": allowed_actions,
            "blocked_actions": blocked_actions,
            "decision_reason": (
                "All requested actions fall within authorized scope."
                if authorized
                else "Blocked mutating or unconfirmed actions pending explicit owner permission."
            ),
        },
        "metrics": {"allowed_count": len(allowed_actions), "blocked_count": len(blocked_actions)},
    }


def handle_read_only_exploration_controller(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    policy = ctx["policy"]
    steps = params.get("steps", [
        {"step": "open_page", "verb": "read"},
        {"step": "scroll_sections", "verb": "scroll"},
        {"step": "inspect_table", "verb": "inspect"},
    ])
    allow_modify = bool(params.get("allow_modification", False) and ctx["approve"])
    ro_verbs = set(policy.get("read_only_operations", [])) | {"get", "head", "navigate", "observe"}

    evaluated = []
    downgraded = 0
    for idx, st in enumerate(steps, start=1):
        verb = str(st.get("verb", "inspect")).lower()
        permitted = verb in ro_verbs or allow_modify
        effective_verb = verb if permitted else "preview"
        if not permitted:
            downgraded += 1
        evaluated.append({
            "index": idx,
            "step": st.get("step", f"step_{idx}"),
            "requested_verb": verb,
            "effective_verb": effective_verb,
            "read_only_compliant": verb in ro_verbs,
            "permitted": permitted,
        })
    return {
        "observation_mode": "tested",
        "data": {
            "default_posture": "read-only",
            "modification_unlocked": allow_modify,
            "evaluated_steps": evaluated,
            "downgraded_to_preview_count": downgraded,
        },
        "metrics": {"total_steps": len(evaluated), "downgraded_steps": downgraded},
    }


def handle_consequential_action_confirmation_manager(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    action_type = str(params.get("action_type", "publish_draft")).lower()
    target = str(params.get("target", "staging-target"))
    reversible = bool(params.get("reversible", False))
    affected_items = int(params.get("affected_items", 1))
    summary_payload = {
        "action_type": action_type,
        "target": target,
        "reversible": reversible,
        "affected_items": affected_items,
    }
    confirmation_digest = core.sha256_bytes(json.dumps(summary_payload, sort_keys=True).encode("utf-8"))[:16]
    confirmed = bool(ctx["approve"] and (not params.get("expected_digest") or params.get("expected_digest") == confirmation_digest))
    return {
        "observation_mode": "directly_observed",
        "data": {
            "confirmation_status": "CONFIRMED" if confirmed else "PENDING_HUMAN_CONFIRMATION",
            "action_summary": summary_payload,
            "confirmation_digest": confirmation_digest,
            "pre_action_checklist": [
                "Verify target account and environment match intended destination.",
                "Confirm recipient visibility, irreversible side effects, and backup availability.",
                "Supply --approve with scope note before executing consequential step.",
            ],
            "may_proceed": confirmed,
        },
        "metrics": {"affected_items": affected_items, "reversible": int(reversible)},
    }


def handle_credential_and_secret_handling_guardian(ctx: Dict[str, Any]) -> Dict[str, Any]:
    text, src = _read_text_or_default(ctx["input_path"], ctx["params"])
    redactor = core.SecretRedactor(ctx["policy"], redact_pii=False)
    scrubbed = redactor.scrub_text(text)

    # Also scan high-entropy candidate tokens (>=20 chars alphanumeric/symbol) without exposing values
    token_candidates = re.findall(r"\b[A-Za-z0-9_\-+/=]{24,}\b", scrubbed)
    high_entropy_count = 0
    for cand in token_candidates:
        if "REDACTED" not in cand and _shannon_entropy(cand) >= 4.1:
            high_entropy_count += 1
            scrubbed = scrubbed.replace(cand, "[REDACTED-HIGH-ENTROPY-SECRET]")

    env_vars_checked = []
    for env_name in ctx["params"].get("auth_env_vars", ["DWAI_SEARCH_API_KEY", "DWAI_MODEL_API_KEY"]):
        import os
        present = bool(os.environ.get(str(env_name)))
        env_vars_checked.append({"env_var": str(env_name), "configured": present, "exposed_value": "[NEVER-EXPOSED]"})

    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "secrets_redacted": redactor.redaction_count + high_entropy_count,
            "pattern_redactions": redactor.redaction_count,
            "high_entropy_redactions": high_entropy_count,
            "auth_references": env_vars_checked,
            "sanitized_preview": scrubbed[:600],
        },
        "metrics": {"total_redactions": redactor.redaction_count + high_entropy_count},
    }


def handle_target_boundary_enforcer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    candidates = params.get("targets", ["https://docs.example.org/catalog/releases", "http://127.0.0.1:8080/admin"])
    allowed_domains = params.get("allowed_domains", ["example.org", "docs.example.org"])
    max_rps = float(params.get("max_rps", ctx["cfg"]["execution"]["rate_limit_requests_per_second"]))
    requested_rps = float(params.get("requested_rps", 1.0))

    results = []
    permitted_count = 0
    for tgt in candidates:
        tgt_s = str(tgt)
        if tgt_s.startswith(("http://", "https://")):
            try:
                info = core.enforce_url_boundary(tgt_s, ctx["cfg"], ctx["policy"], extra_allowed_domains=allowed_domains)
                results.append({"target": tgt_s, "type": "url", "allowed": True, "parsed": info})
                permitted_count += 1
            except core.SkillToolError as exc:
                results.append({"target": tgt_s, "type": "url", "allowed": False, "reason": exc.message})
        else:
            try:
                core.validate_safe_path(tgt_s, must_exist=False)
                results.append({"target": tgt_s, "type": "path", "allowed": True})
                permitted_count += 1
            except core.SkillToolError as exc:
                results.append({"target": tgt_s, "type": "path", "allowed": False, "reason": exc.message})

    rate_compliant = requested_rps <= max_rps
    return {
        "observation_mode": "tested",
        "data": {
            "allowed_domains": allowed_domains,
            "rate_limit_check": {"requested_rps": requested_rps, "max_rps": max_rps, "compliant": rate_compliant},
            "target_evaluations": results,
        },
        "metrics": {"permitted_targets": permitted_count, "blocked_targets": len(results) - permitted_count},
    }


def _parse_robots_txt(robots_text: str, user_agent: str) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Any]] = {}
    current_agents: List[str] = []
    sitemaps: List[str] = []
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, val = [x.strip() for x in line.split(":", 1)]
        fl = field.lower()
        if fl == "user-agent":
            ua = val.lower()
            current_agents = [ua]
            groups.setdefault(ua, {"allow": [], "disallow": [], "crawl_delay": None})
        elif fl in ("allow", "disallow") and current_agents:
            for ua in current_agents:
                groups[ua][fl].append(val)
        elif fl == "crawl-delay" and current_agents:
            try:
                delay = float(val)
                for ua in current_agents:
                    groups[ua]["crawl_delay"] = delay
            except ValueError:
                pass
        elif fl == "sitemap":
            sitemaps.append(val)

    ua_l = user_agent.lower()
    matched_group = groups.get(ua_l) or groups.get("*") or {"allow": [], "disallow": [], "crawl_delay": None}
    return {"matched_rules": matched_group, "all_agents": list(groups.keys()), "sitemaps": sitemaps}


def _robots_allows_path(rules: Dict[str, Any], path: str) -> bool:
    best_len = -1
    decision = True
    for rule_path in rules.get("disallow", []):
        if not rule_path:
            continue
        if path.startswith(rule_path) and len(rule_path) > best_len:
            best_len = len(rule_path)
            decision = False
    for rule_path in rules.get("allow", []):
        if not rule_path:
            continue
        if path.startswith(rule_path) and len(rule_path) >= best_len:
            best_len = len(rule_path)
            decision = True
    return decision


def handle_terms_robots_and_access_policy_reviewer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    robots_txt = params.get("robots_txt", "User-agent: *\nAllow: /catalog/\nAllow: /docs/\nDisallow: /admin/\nDisallow: /private/\nCrawl-delay: 2\nSitemap: https://docs.example.org/sitemap.xml\n")
    if ctx["input_path"] and ctx["input_path"].is_file() and ctx["input_path"].name.lower().endswith(".txt"):
        robots_txt = ctx["input_path"].read_text(encoding="utf-8", errors="replace")
    ua = params.get("user_agent", ctx["cfg"]["network"]["user_agent"])
    candidate_paths = params.get("paths", ["/catalog/releases", "/docs/v2/getting-started", "/admin/export"])
    parsed = _parse_robots_txt(robots_txt, ua)

    path_checks = [
        {"path": p, "allowed_by_robots": _robots_allows_path(parsed["matched_rules"], p)}
        for p in candidate_paths
    ]
    declared_license = str(params.get("declared_license", "CC-BY-4.0"))
    return {
        "observation_mode": "directly_observed",
        "data": {
            "user_agent_evaluated": ua,
            "crawl_delay_seconds": parsed["matched_rules"].get("crawl_delay"),
            "sitemaps": parsed["sitemaps"],
            "declared_license": declared_license,
            "path_permissions": path_checks,
            "recommendation": "Use official API or respect Crawl-delay and Disallow prefixes.",
        },
        "metrics": {
            "allowed_paths": sum(1 for c in path_checks if c["allowed_by_robots"]),
            "disallowed_paths": sum(1 for c in path_checks if not c["allowed_by_robots"]),
        },
    }


def handle_privacy_and_personal_data_minimizer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, meta = core.load_structured_records(ctx["input_path"], ctx["params"])
    purpose_fields = ctx["params"].get("allowed_fields")
    pii_re = {k: re.compile(v) for k, v in ctx["policy"].get("pii_patterns", {}).items()}

    detected_pii_fields: Dict[str, List[str]] = collections.defaultdict(list)
    minimized_rows: List[Dict[str, Any]] = []
    redacted_cells = 0
    for row in rows:
        new_row: Dict[str, Any] = {}
        for k, v in row.items():
            if purpose_fields and k not in purpose_fields:
                continue
            val_str = "" if v is None else str(v)
            matched_types = [ptype for ptype, rx in pii_re.items() if rx.search(val_str)]
            if matched_types or any(tok in k.lower() for tok in ("email", "phone", "ssn", "dob", "address")):
                for mt in (matched_types or ["sensitive_field_name"]):
                    if mt not in detected_pii_fields[k]:
                        detected_pii_fields[k].append(mt)
                digest = core.sha256_bytes(val_str.encode("utf-8"))[:10]
                new_row[k] = f"REDACTED_{digest}" if val_str else ""
                redacted_cells += 1
            else:
                new_row[k] = v
        minimized_rows.append(new_row)

    return {
        "observation_mode": "directly_observed",
        "data": {
            "source_meta": meta,
            "detected_pii_columns": dict(detected_pii_fields),
            "retention_days_limit": int(ctx["params"].get("retention_days", 30)),
            "redacted_cell_count": redacted_cells,
            "minimized_preview": minimized_rows[:5],
        },
        "metrics": {"input_rows": len(rows), "redacted_cells": redacted_cells, "pii_columns": len(detected_pii_fields)},
    }


def handle_safe_sandbox_execution_planner(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx["input_path"] or core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html")
    ext = target.suffix.lower() if target.exists() else str(ctx["params"].get("extension", ".bin")).lower()
    size_bytes = target.stat().st_size if target.exists() and target.is_file() else int(ctx["params"].get("size_bytes", 0))
    exec_exts = {".sh", ".py", ".js", ".exe", ".msi", ".apk", ".dmg", ".bin", ".elf", ".bat", ".ps1"}
    risk_tier = "high" if ext in exec_exts else "medium" if ext in {".zip", ".tar", ".docm", ".xlsm"} else "low"
    defaults = ctx["policy"].get("sandbox_defaults", {})

    plan = {
        "artifact": str(target),
        "extension": ext,
        "size_bytes": size_bytes,
        "risk_tier": risk_tier,
        "isolation_profile": {
            "filesystem_mode": defaults.get("filesystem", "read-only-root-with-isolated-scratch"),
            "network_access": "none" if not ctx["params"].get("require_isolated_test_net", False) else "loopback-only",
            "cpu_quota_seconds": int(ctx["params"].get("max_cpu_seconds", defaults.get("max_cpu_seconds", 30))),
            "memory_limit_mb": int(ctx["params"].get("max_memory_mb", defaults.get("max_memory_mb", 512))),
            "drop_all_capabilities": True,
            "pre_execution_snapshot": True,
            "post_run_scratch_wipe": True,
        },
    }
    return {"observation_mode": "directly_observed", "data": plan, "metrics": {"size_bytes": size_bytes}}


def handle_human_handoff_and_escalation_controller(ctx: Dict[str, Any]) -> Dict[str, Any]:
    text, src = _read_text_or_default(ctx["input_path"], ctx["params"])
    lower = text.lower()
    marker_map = {
        "captcha_challenge": ["g-recaptcha", "hcaptcha", "cf-turnstile", "verify you are human"],
        "identity_or_mfa": ["two-factor", "6-digit code", "authenticator app", "verify identity", "biometric"],
        "legal_acceptance": ["i agree to the terms", "binding arbitration", "accept license agreement"],
        "financial_approval": ["confirm payment", "place order and pay", "authorize charge", "wire transfer"],
    }
    detected = []
    for category, needles in marker_map.items():
        hits = [n for n in needles if n in lower]
        if hits:
            detected.append({"category": category, "matched_indicators": hits})
    if ctx["params"].get("ambiguous_permission", False):
        detected.append({"category": "ambiguous_authorization", "matched_indicators": ["explicit_flag"]})

    must_escalate = len(detected) > 0
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "handoff_required": must_escalate,
            "automation_state": "PAUSED_FOR_HUMAN" if must_escalate else "CLEAR_TO_OBSERVE",
            "detected_triggers": detected,
            "escalation_instructions": (
                "Stop automated interaction immediately and present exact step context to authorized human operator."
                if must_escalate
                else "No CAPTCHA, MFA, legal acceptance, or payment challenge detected in inspected state."
            ),
        },
        "metrics": {"trigger_count": len(detected)},
    }


def handle_capability_and_limitation_reporter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    items = params.get("items", [
        {"claim": "Parsed local HTML DOM headings and tables", "evidence_type": "local_execution"},
        {"claim": "Simulated 1000-trial Monte Carlo latency model", "evidence_type": "simulation"},
        {"claim": "Live external production database query", "evidence_type": "missing_credentials"},
    ])
    mapping = {
        "local_execution": "directly_observed",
        "test_suite": "tested",
        "simulation": "simulated",
        "heuristic": "inferred",
        "missing_credentials": "blocked",
        "policy_denied": "blocked",
        "not_run": "unverified",
    }
    classified = []
    counts: Dict[str, int] = collections.Counter()
    for it in items:
        ev = str(it.get("evidence_type", "not_run")).lower()
        epistemic = mapping.get(ev, "unverified")
        counts[epistemic] += 1
        classified.append({
            "claim": it.get("claim", ""),
            "evidence_type": ev,
            "status_classification": epistemic,
        })
    return {
        "observation_mode": "directly_observed",
        "data": {"epistemic_report": classified, "summary_counts": dict(counts)},
        "metrics": dict(counts),
    }


# --- Category 2 Handlers ---

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]{2,}", text.lower())


def _load_search_corpus(input_path: Optional[Path], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(params.get("documents"), list):
        return [dict(d) for d in params["documents"]]
    if input_path and input_path.exists():
        rows, _ = core.load_structured_records(input_path, params)
        docs = []
        for idx, r in enumerate(rows, start=1):
            docs.append({
                "doc_id": r.get("id") or r.get("record_id") or r.get("event_id") or f"doc-{idx}",
                "url": r.get("url", f"https://docs.example.org/item/{idx}"),
                "title": r.get("title") or r.get("name") or r.get("component") or f"Document {idx}",
                "date": r.get("date") or r.get("timestamp") or r.get("timestamp_utc") or "2026-10-15",
                "text": " ".join(str(v) for v in r.values() if v is not None),
            })
        return docs
    html_path = core.resolve_package_resource("data-web-app-interaction/asset-sample-page.html")
    dom = core.parse_html_document(html_path.read_text(encoding="utf-8"))
    return [
        {
            "doc_id": "sample-portal",
            "url": "https://docs.example.org/catalog/releases",
            "title": dom.title,
            "date": "2026-10-15",
            "text": " ".join(dom.main_paragraphs),
        },
        {
            "doc_id": "sample-changelog",
            "url": "https://docs.example.org/docs/v2/changelog",
            "title": "Documentation v2.4.0 Changelog",
            "date": "2026-10-15",
            "text": "Version 2.4.0 introduces bounded API pagination, Retry-After handling, and WCAG keyboard navigation fixes.",
        },
    ]


def handle_general_web_search_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    query = str(params.get("query", "bounded API pagination keyboard navigation")).strip()
    if not query:
        raise core.InputValidationError("Search query cannot be empty.")
    q_tokens = _tokenize(query)
    corpus = _load_search_corpus(ctx["input_path"], params)

    scored = []
    N = max(1, len(corpus))
    df = {qt: sum(1 for d in corpus if qt in _tokenize(d.get("title", "") + " " + d.get("text", ""))) for qt in q_tokens}
    for doc in corpus:
        body = f"{doc.get('title', '')} {doc.get('text', '')}"
        toks = _tokenize(body)
        tf = collections.Counter(toks)
        score = 0.0
        matched = []
        for qt in q_tokens:
            if tf[qt] > 0:
                matched.append(qt)
                idf = math.log(1.0 + (N - df[qt] + 0.5) / (df[qt] + 0.5))
                score += (tf[qt] / max(1, len(toks))) * 10.0 * idf
        if score > 0 or not q_tokens:
            scored.append({
                "doc_id": doc.get("doc_id"),
                "url": doc.get("url"),
                "title": doc.get("title"),
                "date": doc.get("date"),
                "relevance_score": round(score, 4),
                "matched_terms": matched,
                "direct_evidence_excerpt": doc.get("text", "")[:240],
            })
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)

    limitations = []
    if not ctx["cfg"]["network"].get("allow_live_network", False) and not params.get("endpoint"):
        limitations.append("Evaluated against supplied/local corpus; set network.allow_live_network=true and providers.search_provider.endpoint for live web API search.")

    return {
        "observation_mode": "directly_observed",
        "data": {
            "built_query": f'({" AND ".join(dict.fromkeys(q_tokens))})' if q_tokens else query,
            "results": scored[: int(params.get("top_k", 10))],
            "interpretation_note": "Evidence excerpts reflect verbatim source text; ranking score is lexical BM25-like relevance.",
        },
        "limitations": limitations,
        "metrics": {"corpus_size": len(corpus), "matched_results": len(scored)},
    }


def handle_deep_web_research_planner(ctx: Dict[str, Any]) -> Dict[str, Any]:
    question = str(ctx["params"].get("question", "How do API pagination standards compare across v2 releases and what are the migration risks?"))
    focus_terms = [w for w in _tokenize(question) if w not in {"how", "what", "are", "the", "and", "across", "for", "with"}]
    core_topic = " ".join(focus_terms[:5]) or question
    sub_queries = [
        {"stage": 1, "purpose": "official_specification", "query": f'"{core_topic}" official documentation specification'},
        {"stage": 2, "purpose": "version_and_freshness", "query": f"{core_topic} changelog release notes breaking changes"},
        {"stage": 3, "purpose": "empirical_benchmarks_or_issues", "query": f"{core_topic} known issues migration guide"},
        {"stage": 4, "purpose": "independent_corroboration", "query": f"{core_topic} comparison review audit"},
    ]
    return {
        "observation_mode": "directly_observed",
        "data": {
            "research_question": question,
            "freshness_requirement_days": int(ctx["params"].get("max_age_days", 180)),
            "authoritative_source_tiers": ["official_docs", "standards_bodies", "release_changelogs", "peer_reviewed"],
            "non_redundant_sub_queries": sub_queries,
            "verification_criteria": [
                "At least one primary/official specification confirms exact behavior.",
                "Publication or release timestamp verified against freshness cutoff.",
                "Conflicting secondary claims explicitly reconciled or flagged.",
            ],
        },
        "metrics": {"sub_query_count": len(sub_queries)},
    }


def handle_domain_restricted_searcher(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    domains = [d.lower().lstrip(".") for d in params.get("domains", ["docs.example.org"])]
    query = str(params.get("query", "release pagination"))
    corpus = _load_search_corpus(ctx["input_path"], params)
    q_tokens = set(_tokenize(query))

    filtered = []
    rejected_domains = 0
    for doc in corpus:
        host = (urllib.parse.urlparse(str(doc.get("url", ""))).hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in domains):
            rejected_domains += 1
            continue
        toks = set(_tokenize(f"{doc.get('title', '')} {doc.get('text', '')}"))
        overlap = sorted(q_tokens & toks)
        if overlap or not q_tokens:
            filtered.append({**doc, "host": host, "matched_terms": overlap})

    site_clause = " OR ".join(f"site:{d}" for d in domains)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "domain_scoped_query": f"({site_clause}) {query}".strip(),
            "allowed_domains": domains,
            "results": filtered,
            "rejected_outside_domain_count": rejected_domains,
        },
        "metrics": {"in_domain_matches": len(filtered), "rejected_off_domain": rejected_domains},
    }


def handle_current_information_verifier(ctx: Dict[str, Any]) -> Dict[str, Any]:
    text, src = _read_text_or_default(ctx["input_path"], ctx["params"])
    ref_date_str = str(ctx["params"].get("reference_date", "2026-11-05"))
    ref_date = dt.date.fromisoformat(ref_date_str[:10])
    max_age_days = int(ctx["params"].get("max_age_days", 90))

    iso_dates = sorted(set(re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)))
    versions = sorted(set(re.findall(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9.]+)?\b", text)))
    evaluated_dates = []
    for ds in iso_dates:
        try:
            d_obj = dt.date.fromisoformat(ds)
            age = (ref_date - d_obj).days
            evaluated_dates.append({"date": ds, "age_days": age, "within_freshness_window": 0 <= age <= max_age_days})
        except ValueError:
            pass

    fresh = any(item["within_freshness_window"] for item in evaluated_dates)
    return {
        "observation_mode": "directly_observed",
        "data": {
            "source": src,
            "reference_date": ref_date.isoformat(),
            "max_age_days": max_age_days,
            "observed_dates": evaluated_dates,
            "observed_versions": versions,
            "freshness_verified": fresh,
        },
        "metrics": {"date_count": len(evaluated_dates), "version_count": len(versions)},
    }


def handle_multilingual_web_searcher(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    base_query = str(params.get("query", "service status release notes"))
    translations: Dict[str, str] = params.get("translations", {
        "en": base_query,
        "es": "notas de version y estado del servicio",
        "ja": "サービス稼働状況 リリースノート",
        "de": "dienststatus versionshinweise",
    })
    normalized_queries = []
    for lang, phrase in translations.items():
        nfkc = unicodedata.normalize("NFKC", phrase)
        ascii_fold = "".join(ch for ch in unicodedata.normalize("NFD", nfkc) if unicodedata.category(ch) != "Mn")
        scripts = sorted({unicodedata.name(ch, "UNKNOWN").split()[0] for ch in nfkc if ch.isalpha()})
        normalized_queries.append({
            "language": lang,
            "query_nfkc": nfkc,
            "diacritic_folded": ascii_fold,
            "detected_scripts": scripts,
        })
    return {
        "observation_mode": "directly_observed",
        "data": {"base_query": base_query, "multilingual_query_plan": normalized_queries},
        "metrics": {"language_count": len(normalized_queries)},
    }


def handle_entity_disambiguation_researcher(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows, _ = core.load_structured_records(ctx["input_path"], ctx["params"])
    target_name = str(ctx["params"].get("entity_name", "")).lower().strip()
    clusters: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)

    for idx, r in enumerate(rows, start=1):
        label = str(r.get("entity") or r.get("name") or r.get("user_id") or r.get("tier") or "unknown")
        if target_name and target_name not in label.lower():
            continue
        discriminator = "|".join([
            label.lower(),
            str(r.get("type") or r.get("category") or r.get("region") or "unspecified").lower(),
            str(r.get("domain") or r.get("cohort_month") or "").lower(),
        ])
        clusters[discriminator].append({"row_index": idx, "record": r})

    disambiguated = [
        {"cluster_key": k, "mention_count": len(v), "sample_record": v[0]["record"]}
        for k, v in clusters.items()
    ]
    return {
        "observation_mode": "directly_observed",
        "data": {"entity_filter": target_name or "all", "distinct_entities": disambiguated},
        "metrics": {"distinct_entity_count": len(disambiguated)},
    }


def handle_source_authority_evaluator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    sources = ctx["params"].get("sources", [
        {"url": "https://docs.example.org/catalog/releases", "source_type": "official_docs", "has_citations": True, "author_declared": True},
        {"url": "https://forum.example.com/thread/92", "source_type": "community_post", "has_citations": False, "author_declared": False},
    ])
    tier_weights = {
        "primary_official": 95,
        "official_docs": 92,
        "peer_reviewed": 90,
        "standards_body": 94,
        "reputable_secondary": 72,
        "community_post": 40,
        "unverified": 20,
    }
    ranked = []
    for s in sources:
        url = str(s.get("url", ""))
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        stype = str(s.get("source_type", "unverified")).lower()
        base = tier_weights.get(stype, 35)
        if host.endswith((".gov", ".edu", ".int")):
            base = max(base, 88)
        if s.get("has_citations"):
            base += 4
        if s.get("author_declared"):
            base += 3
        score = min(100, base)
        tier = (
            "primary_or_official" if score >= 88 else
            "reputable_secondary" if score >= 65 else
            "community_or_unverified"
        )
        ranked.append({"url": url, "host": host, "authority_score": score, "authority_tier": tier})
    ranked.sort(key=lambda x: x["authority_score"], reverse=True)
    return {"observation_mode": "directly_observed", "data": {"evaluated_sources": ranked}, "metrics": {"source_count": len(ranked)}}


def _canonicalize_url(url: str) -> str:
    p = urllib.parse.urlparse(url.strip())
    drop_params = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref"}
    q_pairs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k.lower() not in drop_params]
    q_pairs.sort()
    clean_path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(), clean_path, "", urllib.parse.urlencode(q_pairs), ""))


def handle_search_result_deduplicator(ctx: Dict[str, Any]) -> Dict[str, Any]:
    items = ctx["params"].get("results", [
        {"url": "https://docs.example.org/catalog/releases?utm_source=newsletter", "snippet": "Version 2.4.0 introduces bounded API pagination and improved keyboard navigation."},
        {"url": "https://docs.example.org/catalog/releases/", "snippet": "Version 2.4.0 introduces bounded API pagination and improved keyboard navigation!"},
        {"url": "https://status.example.org/health", "snippet": "All regional API gateways operating within nominal latency."},
    ])
    threshold = float(ctx["params"].get("similarity_threshold", 0.8))
    unique_clusters: List[Dict[str, Any]] = []

    for item in items:
        canon = _canonicalize_url(str(item.get("url", "")))
        words = _tokenize(str(item.get("snippet", "")))
        shingles = {" ".join(words[i:i + 3]) for i in range(max(1, len(words) - 2))} if words else set()
        matched_cluster = None
        for cl in unique_clusters:
            if cl["canonical_url"] == canon:
                matched_cluster = cl
                break
            union = cl["_shingles"] | shingles
            jacc = (len(cl["_shingles"] & shingles) / len(union)) if union else 0.0
            if jacc >= threshold:
                matched_cluster = cl
                break
        if matched_cluster is not None:
            matched_cluster["duplicates"].append(item.get("url"))
        else:
            unique_clusters.append({
                "canonical_url": canon,
                "primary_snippet": item.get("snippet", ""),
                "duplicates": [],
                "_shingles": shingles,
            })

    for cl in unique_clusters:
        cl.pop("_shingles", None)
    return {
        "observation_mode": "directly_observed",
        "data": {"deduplicated_clusters": unique_clusters},
        "metrics": {"input_count": len(items), "unique_count": len(unique_clusters), "removed_duplicates": len(items) - len(unique_clusters)},
    }


def handle_archived_web_discovery_specialist(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    target_url = str(params.get("url", "https://docs.example.org/catalog/releases"))
    prior_snapshot = str(params.get("archived_text", "Version 2.3.1 supports offset pagination."))
    current_text, _ = _read_text_or_default(ctx["input_path"], params)
    diff_lines = list(difflib.unified_diff(
        prior_snapshot.splitlines(),
        current_text.splitlines()[:30],
        fromfile="archived_snapshot",
        tofile="current_snapshot",
        lineterm="",
    ))
    encoded = urllib.parse.quote(target_url, safe="")
    return {
        "observation_mode": "directly_observed",
        "data": {
            "target_url": target_url,
            "cdx_lookup_template": f"https://web.archive.org/cdx/search/cdx?url={encoded}&output=json&limit=10",
            "snapshot_diff_preview": diff_lines[:25],
            "change_detected": len(diff_lines) > 0,
        },
        "metrics": {"diff_line_count": len(diff_lines)},
    }


def handle_research_gap_detector(ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx["params"]
    sub_questions = params.get("sub_questions", [
        "What rate limit applies to Standard tier?",
        "Is Retry-After enforced on paginated endpoints?",
        "What is the SLA uptime percentage for Enterprise Sandbox?",
    ])
    corpus = _load_search_corpus(ctx["input_path"], params)
    combined_tokens = set(_tokenize(" ".join(d.get("text", "") for d in corpus)))

    gaps = []
    covered = []
    for sq in sub_questions:
        key_terms = [t for t in _tokenize(sq) if len(t) > 3 and t not in {"what", "does", "when", "where", "which"}]
        missing = [t for t in key_terms if t not in combined_tokens]
        coverage_ratio = 1.0 - (len(missing) / max(1, len(key_terms)))
        if coverage_ratio >= 0.6:
            covered.append({"sub_question": sq, "coverage_ratio": round(coverage_ratio, 2)})
        else:
            gaps.append({
                "sub_question": sq,
                "coverage_ratio": round(coverage_ratio, 2),
                "missing_concepts": missing,
                "recommended_next_query": " ".join(key_terms),
            })
    return {
        "observation_mode": "directly_observed",
        "data": {"covered_questions": covered, "identified_gaps": gaps},
        "metrics": {"covered_count": len(covered), "gap_count": len(gaps)},
    }


SKILL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "action-authorization-gatekeeper": handle_action_authorization_gatekeeper,
    "read-only-exploration-controller": handle_read_only_exploration_controller,
    "consequential-action-confirmation-manager": handle_consequential_action_confirmation_manager,
    "credential-and-secret-handling-guardian": handle_credential_and_secret_handling_guardian,
    "target-boundary-enforcer": handle_target_boundary_enforcer,
    "terms-robots-and-access-policy-reviewer": handle_terms_robots_and_access_policy_reviewer,
    "privacy-and-personal-data-minimizer": handle_privacy_and_personal_data_minimizer,
    "safe-sandbox-execution-planner": handle_safe_sandbox_execution_planner,
    "human-handoff-and-escalation-controller": handle_human_handoff_and_escalation_controller,
    "capability-and-limitation-reporter": handle_capability_and_limitation_reporter,
    "general-web-search-specialist": handle_general_web_search_specialist,
    "deep-web-research-planner": handle_deep_web_research_planner,
    "domain-restricted-searcher": handle_domain_restricted_searcher,
    "current-information-verifier": handle_current_information_verifier,
    "multilingual-web-searcher": handle_multilingual_web_searcher,
    "entity-disambiguation-researcher": handle_entity_disambiguation_researcher,
    "source-authority-evaluator": handle_source_authority_evaluator,
    "search-result-deduplicator": handle_search_result_deduplicator,
    "archived-web-discovery-specialist": handle_archived_web_discovery_specialist,
    "research-gap-detector": handle_research_gap_detector,
}
