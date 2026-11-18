"""tools-rwt-synthesis.py - literature review workflow and evidence synthesis.

Screening, PRISMA counting, evidence tables, risk-of-bias rubrics, narrative grouping and a
real fixed-effect / DerSimonian-Laird random-effects meta-analysis over user-supplied effect
data. Synthesis never invents studies: it only summarises records present in the local store
or supplied in an input file.
"""

from __future__ import annotations

import json
import math
import os
import statistics

NAME = "synthesis"
ROLE = "review planning, screening, PRISMA counts, evidence tables, risk of bias, narrative and quantitative synthesis"
GROUPS = [("synthesis", "Literature review planning, screening, PRISMA, evidence tables and synthesis")]
deps: dict = {}

ROB_DOMAINS = ["randomization", "allocation_concealment", "blinding", "completeness",
               "outcome_reporting", "selective_reporting", "other"]
JUDGEMENTS = ("low", "some-concerns", "high", "unclear")


def _store():
    return deps["store"]


def op_plan(ctx, question: str, scope: str | None = None, databases: str | None = None,
            date_from: str | None = None, date_to: str | None = None,
            languages: str | None = None, source_types: str | None = None,
            method: str = "narrative", output: str | None = None) -> dict:
    store = _store()
    if not question or len(store.tokens(question)) < 3:
        raise store.ToolError(2, "question must contain at least three words")
    decomposition = deps["retrieval"].op_decompose(ctx, question)
    plan = {
        "question": question,
        "scope": scope or "all local holdings matching the concepts below",
        "concepts": decomposition["concepts"],
        "facets": decomposition["facets"],
        "databases": [d.strip() for d in (databases or "workspace-corpus").split(",") if d.strip()],
        "date_limits": {"from": date_from, "to": date_to},
        "languages": [l.strip() for l in (languages or ctx.cfg.get("languages", {}).get("default", "en")).split(",") if l.strip()],
        "source_types": [s.strip() for s in (source_types or "journal-article,report,policy,standard").split(",") if s.strip()],
        "screening_rules": {
            "include": ["addresses the question population or jurisdiction",
                        "reports evidence about the outcome of interest",
                        "is available in the local corpus under an acceptable licence"],
            "exclude": ["duplicate or superseded version",
                        "no usable methods or data",
                        "out of scope by date, language or jurisdiction"],
        },
        "synthesis_method": method,
        "reproducibility": {"search_strategy": decomposition["suggested_boolean"],
                            "corpus_snapshot": "record with 'workflow run-manifest' after searching"},
        "review_roles": ["screener", "second screener", "domain expert", "final approver"],
    }
    payload = {"status": "ok", "plan": plan,
               "next_commands": ["synthesis screen --decision include --ref <source-id> --reviewer <name>",
                                 "synthesis prisma", "synthesis evidence-table --output evidence.md"]}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), plan)
    return payload


def op_strategy(ctx, concepts: str, databases: str | None = None, vocab: str | None = None) -> dict:
    retrieval = deps["retrieval"]
    base = retrieval.op_boolean(ctx, concepts=concepts, mode="AND", synonyms=vocab)
    variants = []
    for db in [d.strip() for d in (databases or "local-fts5").split(",") if d.strip()]:
        variants.append({"database": db,
                         "query": base["boolean"],
                         "notes": "same expression is valid for the local FTS5 index; "
                                  "external database syntaxes are recorded for reproducibility only"})
    return {"status": "ok", "strategy": variants, "base": base["boolean"],
            "translation_note": "for non-English searches add approved terms via 'search expand'"}


def op_screen(ctx, decision: str, ref: str, reason: str | None = None, stage: str = "title-abstract",
              reviewer: str | None = None, blinded: bool = True) -> dict:
    store = _store()
    if decision not in ("include", "exclude", "maybe"):
        raise store.ToolError(2, "decision must be include|exclude|maybe")
    conn = ctx.conn()
    known = conn.execute("SELECT id FROM sources WHERE id=?", (ref,)).fetchone()
    if not known:
        raise store.ToolError(2, f"unknown source id: {ref}")
    if decision == "exclude" and not reason:
        raise store.ToolError(2, "exclusion requires a --reason for PRISMA reporting")
    review_id = f"scr-{store.slug(ref, 30)}-{store.slug(stage, 12)}-{store.slug(reviewer or ctx.actor, 12)}"
    conn.execute("""INSERT OR REPLACE INTO reviews(id,kind,ref,assignee,status,priority,decision,note,
        created,decided) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                 (review_id, f"screening:{stage}", ref, reviewer or ctx.actor,
                  "closed" if decision in ("include", "exclude") else "open",
                  "normal", decision, reason or "", store.now_iso(),
                  store.now_iso() if decision != "maybe" else None))
    conflicts = [dict(r) for r in conn.execute(
        "SELECT id,assignee,decision FROM reviews WHERE kind=? AND ref=? AND decision!=?",
        (f"screening:{stage}", ref, decision))]
    ctx.commit()
    return {"status": "ok", "review_id": review_id, "source_id": ref, "stage": stage,
            "decision": decision, "reason": reason, "reviewer": reviewer or ctx.actor,
            "conflicting_decisions": conflicts,
            "needs_adjudication": len(conflicts) > 0,
            "blinded": blinded}


def op_prisma(ctx) -> dict:
    conn = ctx.conn()
    sources = conn.execute("SELECT count(*) c FROM sources WHERE status!='deleted'").fetchone()["c"]
    dupes = conn.execute("SELECT count(*) c FROM (SELECT checksum FROM sources GROUP BY checksum "
                         "HAVING count(*)>1)").fetchone()["c"]
    screening = [dict(r) for r in conn.execute(
        "SELECT decision, stage, count(*) n FROM (SELECT decision, "
        "replace(kind,'screening:','') stage FROM reviews WHERE kind LIKE 'screening:%') "
        "GROUP BY decision, stage")]
    included = conn.execute("SELECT count(DISTINCT ref) c FROM reviews WHERE kind LIKE 'screening:%' "
                            "AND decision='include'").fetchone()["c"]
    excluded = conn.execute("SELECT count(*) c FROM reviews WHERE kind LIKE 'screening:%' "
                            "AND decision='exclude'").fetchone()["c"]
    reasons = [dict(r) for r in conn.execute(
        "SELECT note, count(*) n FROM reviews WHERE kind LIKE 'screening:%' AND decision='exclude' "
        "GROUP BY note ORDER BY n DESC")]
    assessed = included
    flow = {
        "identified": sources,
        "duplicates_removed": dupes,
        "screened": sources - dupes if sources >= dupes else sources,
        "excluded_at_screening": excluded,
        "full_text_assessed": max(included, 0),
        "included_in_synthesis": included,
        "included_without_screening_record": max(sources - included, 0),
    }
    return {"status": "ok", "flow": flow, "by_stage": screening, "exclusion_reasons": reasons,
            "complete": flow["included_in_synthesis"] > 0,
            "note": "counts come from the local store and screening records only; "
                    "record every inclusion and exclusion for a reproducible PRISMA diagram"}


def op_evidence_table(ctx, output: str | None = None, fmt: str = "md",
                      source_ids: str | None = None) -> dict:
    store = _store()
    conn = ctx.conn()
    ids = [s.strip() for s in source_ids.split(",")] if source_ids else None
    rows = []
    for row in conn.execute("SELECT * FROM sources WHERE status!='deleted' ORDER BY issued"):
        if ids and row["id"] not in ids:
            continue
        meta = json.loads(row["meta"] or "{}")
        text = "\n".join(r["text"] for r in conn.execute(
            "SELECT text FROM segments WHERE source_id=? ORDER BY ordinal", (row["id"],)))
        method = deps["extract"].op_methodology(ctx, text=text)
        rows.append({"source_id": row["id"], "title": row["title"], "doctype": row["doctype"],
                     "year": (row["issued"] or "")[:4], "language": row["language"],
                     "peer_status": row["peer_status"],
                     "design": ", ".join(method["fields"].get("design", [])) or "not stated",
                     "population": ", ".join(method["fields"].get("participants", [])) or "not stated",
                     "analysis_present": "analysis" in method["fields"],
                     "limitations_present": "limitations" in method["fields"],
                     "license": row["license"], "jurisdiction": row["jurisdiction"],
                     "locator": row["path"]})
    headers = ["source_id", "title", "doctype", "year", "language", "peer_status", "design",
               "population", "analysis_present", "limitations_present", "license", "jurisdiction", "locator"]
    if fmt == "md":
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
        for row in rows:
            lines.append("| " + " | ".join(str(row[h]).replace("|", "/") for h in headers) + " |")
        body = "\n".join(lines) + "\n"
    elif fmt == "csv":
        import csv as _csv
        import io as _io
        buf = _io.StringIO()
        writer = _csv.DictWriter(buf, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        body = buf.getvalue()
    else:
        raise store.ToolError(2, "fmt must be md or csv")
    payload = {"status": "ok", "rows": len(rows), "format": fmt, "table": rows[:100],
               "note": "every row links to a local source id; design/population cues are extracted, not verified"}
    out = ctx.out_path(output or f"evidence-table.{'md' if fmt == 'md' else 'csv'}")
    store.write_text(out, body)
    payload["output"] = out
    return payload


def op_risk_of_bias(ctx, ref: str, judgements: str, rationale: str = "", reviewer: str | None = None) -> dict:
    """Record a domain-level risk-of-bias judgement (e.g. RoB 2 / ROBINS-I style)."""
    store = _store()
    conn = ctx.conn()
    if not conn.execute("SELECT 1 FROM sources WHERE id=?", (ref,)).fetchone():
        raise store.ToolError(2, f"unknown source id: {ref}")
    try:
        supplied = json.loads(judgements)
    except json.JSONDecodeError:
        supplied = {k.strip(): v.strip() for k, v in
                    (pair.split("=") for pair in judgements.split(",") if "=" in pair)}
    if not isinstance(supplied, dict) or not supplied:
        raise store.ToolError(2, "judgements must be JSON or 'domain=judgement' pairs")
    bad = {k: v for k, v in supplied.items() if v not in JUDGEMENTS}
    if bad:
        raise store.ToolError(2, f"invalid judgements {bad}", {"allowed": list(JUDGEMENTS)})
    unknown = [k for k in supplied if k not in ROB_DOMAINS]
    overall = ("high" if "high" in supplied.values() else
               "some-concerns" if "some-concerns" in supplied.values() else
               "unclear" if "unclear" in supplied.values() else "low")
    review_id = f"rob-{store.slug(ref, 30)}-{store.slug(reviewer or ctx.actor, 12)}"
    conn.execute("""INSERT OR REPLACE INTO reviews(id,kind,ref,assignee,status,priority,decision,note,
        created,decided) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                 (review_id, "risk-of-bias", ref, reviewer or ctx.actor, "closed", "normal",
                  overall, json.dumps({"judgements": supplied, "rationale": rationale},
                                      ensure_ascii=False), store.now_iso(), store.now_iso()))
    others = [json.loads(r["note"] or "{}").get("judgements", {}) for r in conn.execute(
        "SELECT note FROM reviews WHERE kind='risk-of-bias' AND ref=? AND id!=?", (ref, review_id))]
    disagreements = []
    for other in others:
        for domain, value in other.items():
            if domain in supplied and supplied[domain] != value:
                disagreements.append({"domain": domain, "this_review": supplied[domain],
                                      "other_review": value})
    ctx.commit()
    return {"status": "ok", "review_id": review_id, "source_id": ref, "judgements": supplied,
            "overall": overall, "unknown_domains": unknown,
            "disagreements": disagreements, "needs_adjudication": bool(disagreements)}


def op_narrative(ctx, topic: str, limit: int = 12) -> dict:
    """Group compatible evidence by direction and size; explicitly avoid vote counting."""
    store = _store()
    retrieval = deps["retrieval"]
    verify = deps["verify"]
    hits = retrieval.op_hybrid(ctx, topic, limit=limit, diversify=True)["results"]
    groupings = {}
    for hit in hits:
        result = verify.op_claim(ctx, hit.get("snippet") or topic, limit=1)
        direction = {"supported": "supports", "contradicted": "contradicts",
                     "qualified": "qualified", "insufficient": "unclear"}[result["verdict"]]
        groupings.setdefault(direction, []).append(
            {"source_id": hit["source_id"], "title": hit.get("title"), "issued": hit.get("issued"),
             "snippet": (hit.get("snippet") or "")[:200]})
    unweighted = sum(len(v) for v in groupings.values())
    paragraphs = []
    for direction, items in groupings.items():
        titles = "; ".join(f"{i['title']} ({i['issued'] or 'undated'})" for i in items[:5])
        paragraphs.append(f"{direction.capitalize()}: {len(items)} local source(s) — {titles}.")
    return {"status": "ok", "topic": topic, "groupings": groupings,
            "paragraph_draft": " ".join(paragraphs),
            "evidence_count": unweighted,
            "vote_counting_warning": ("groups are shown with their scope and quality; do not convert "
                                      "counts into a claim that the majority is correct"),
            "quality_note": "weigh each source with 'verify authority' before drawing a conclusion"}


def op_meta(ctx, effects_path: str, model: str = "random", output: str | None = None) -> dict:
    """Fixed-effect (inverse variance) or DerSimonian-Laird random-effects pooling."""
    store = _store()
    if not os.path.exists(effects_path):
        raise store.ToolError(2, f"effects file not found: {effects_path}")
    try:
        data = json.loads(store.read_text(effects_path))
    except json.JSONDecodeError as exc:
        raise store.ToolError(2, f"effects file must be JSON: {exc}")
    if isinstance(data, dict):
        data = data.get("studies", [])
    if not isinstance(data, list) or len(data) < 2:
        raise store.ToolError(2, "provide at least two studies with effect and standard error")
    studies = []
    for item in data:
        try:
            effect = float(item["effect"])
            se = float(item["se"])
        except (KeyError, TypeError, ValueError):
            raise store.ToolError(2, "each study needs numeric 'effect' and 'se' fields",
                                  {"item": item})
        if se <= 0:
            raise store.ToolError(2, f"standard error must be positive for study {item.get('study')}")
        studies.append({"study": item.get("study") or f"study-{len(studies) + 1}",
                        "effect": effect, "se": se, "weight_fe": 1 / se ** 2})
    sum_w = sum(s["weight_fe"] for s in studies)
    pooled_fe = sum(s["effect"] * s["weight_fe"] for s in studies) / sum_w
    se_fe = math.sqrt(1 / sum_w)
    q = sum(s["weight_fe"] * (s["effect"] - pooled_fe) ** 2 for s in studies)
    df = len(studies) - 1
    i2 = max(0.0, (q - df) / q) * 100 if q > 0 else 0.0
    tau2 = max(0.0, (q - df) / (sum_w - sum(s["weight_fe"] ** 2 for s in studies) / sum_w)) if df > 0 else 0.0
    if model == "fixed":
        pooled, se_pooled, used = pooled_fe, se_fe, "fixed-effect"
    elif model == "random":
        weights = [1 / (s["se"] ** 2 + tau2) for s in studies]
        total = sum(weights)
        pooled = sum(s["effect"] * w for s, w in zip(studies, weights)) / total
        se_pooled = math.sqrt(1 / total)
        used = "random-effects (DerSimonian-Laird)"
    else:
        raise store.ToolError(2, "model must be fixed or random")
    for s, w in zip(studies, [s["weight_fe"] for s in studies]):
        s["weight_pct"] = round(100 * w / sum_w, 2)
    payload = {"status": "ok", "model": used, "studies": len(studies),
               "pooled_effect": round(pooled, 6),
               "ci95": [round(pooled - 1.959964 * se_pooled, 6),
                        round(pooled + 1.959964 * se_pooled, 6)],
               "se": round(se_pooled, 6), "heterogeneity": {"Q": round(q, 4), "df": df,
                                                           "I2_percent": round(i2, 2),
                                                           "tau2": round(tau2, 6)},
               "study_weights": studies,
               "interpretation": ("substantial heterogeneity; report a prediction interval or subgroup "
                                  "analysis rather than a single pooled number" if i2 > 50 else
                                  "heterogeneity is low to moderate; report the pooled estimate with its CI"),
               "requires_human_review": True}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def op_gaps(ctx) -> dict:
    conn = ctx.conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,doctype,language,jurisdiction,issued,peer_status FROM sources WHERE status!='deleted'")]
    if not rows:
        raise store.ToolError(2, "corpus is empty; ingest sources before mapping gaps")
    def counter(key):
        out = {}
        for r in rows:
            k = (r[key] or "unspecified")
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
    years = sorted({(r["issued"] or "")[:4] for r in rows if (r["issued"] or "")[:4].isdigit()})
    gaps = []
    if len(counter("language")) == 1:
        gaps.append({"dimension": "language", "issue": "single-language corpus",
                     "detail": "no evidence beyond one language"})
    if len(years) <= 1:
        gaps.append({"dimension": "period", "issue": "single-period corpus",
                     "detail": f"years present: {years or 'none'}"})
    doctypes = counter("doctype")
    for required in ("journal-article", "policy", "statute", "dataset"):
        if required not in doctypes:
            gaps.append({"dimension": "source_type", "issue": f"no {required} sources",
                         "detail": "claims needing this source type cannot be supported"})
    if len(counter("jurisdiction")) == 1:
        gaps.append({"dimension": "jurisdiction", "issue": "single-jurisdiction corpus",
                     "detail": "legal and policy claims cannot transfer across jurisdictions"})
    return {"status": "ok", "sources": len(rows), "by_doctype": counter("doctype"),
            "by_language": counter("language"), "by_jurisdiction": counter("jurisdiction"),
            "by_year": {y: sum(1 for r in rows if (r["issued"] or "").startswith(y)) for y in years},
            "gaps": gaps, "gap_count": len(gaps)}


def op_robustness(ctx, effects_path: str, quality_path: str | None = None) -> dict:
    """Sensitivity analysis: drop low-quality/duplicate studies and re-pool."""
    store = _store()
    data = json.loads(store.read_text(effects_path))
    if isinstance(data, dict):
        data = data.get("studies", [])
    quality = {}
    if quality_path:
        if not os.path.exists(quality_path):
            raise store.ToolError(2, f"quality file not found: {quality_path}")
        quality = json.loads(store.read_text(quality_path))
    scenarios = {"all": data}
    if quality:
        scenarios["excluding_low_quality"] = [s for s in data
                                              if quality.get(s.get("study"), {}).get("quality") != "low"]
        scenarios["excluding_high_rob"] = [s for s in data
                                           if quality.get(s.get("study"), {}).get("rob") != "high"]
        scenarios["excluding_duplicates"] = [s for s in data
                                             if not quality.get(s.get("study"), {}).get("duplicate")]
    results = {}
    tmp = ctx.out_path("sensitivity-effects.json")
    for name, subset in scenarios.items():
        if len(subset) < 2:
            results[name] = {"skipped": "fewer than two studies remain"}
            continue
        store.write_json(tmp, subset)
        pooled = op_meta(ctx, tmp)
        results[name] = {"pooled_effect": pooled["pooled_effect"], "ci95": pooled["ci95"],
                         "I2": pooled["heterogeneity"]["I2_percent"], "studies": pooled["studies"]}
    try:
        os.remove(tmp)
    except OSError:
        pass
    values = [r["pooled_effect"] for r in results.values() if "pooled_effect" in r]
    spread = round(max(values) - min(values), 6) if values else None
    stable = bool(values) and spread is not None and abs(spread) < 0.1
    return {"status": "ok", "scenarios": results, "spread": spread,
            "conclusions_stable": stable,
            "guidance": ("conclusion is sensitive to exclusions; report the range, not one number"
                         if not stable else "pooled estimate is stable across the tested exclusions")}


def op_update_check(ctx, previous_claims_path: str, current_claims_path: str) -> dict:
    """Compare two claim snapshots and flag changes that could alter published conclusions."""
    store = _store()
    for path in (previous_claims_path, current_claims_path):
        if not os.path.exists(path):
            raise store.ToolError(2, f"claims file not found: {path}")
    previous = json.loads(store.read_text(previous_claims_path))
    current = json.loads(store.read_text(current_claims_path))
    def norm(items):
        return {" ".join(sorted(set(store.tokens(i.get("text", "")))))[:200]: i for i in items}
    prev_map, curr_map = norm(previous), norm(current)
    added = [curr_map[k] for k in curr_map.keys() - prev_map.keys()]
    removed = [prev_map[k] for k in prev_map.keys() - curr_map.keys()]
    changed = []
    for key in prev_map.keys() & curr_map.keys():
        before, after = prev_map[key], curr_map[key]
        if before.get("ctype") != after.get("ctype") or before.get("source_id") != after.get("source_id"):
            changed.append({"before": before, "after": after})
    return {"status": "ok", "added": len(added), "removed": len(removed), "changed": len(changed),
            "impact": ("conclusions may change; re-screen before republishing"
                       if (added or removed) else "no claim-level change detected"),
            "added_claims": added[:20], "removed_claims": removed[:20], "changed_claims": changed[:20]}


def register(groups, common):
    g = groups["synthesis"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("plan", "Create a reproducible review plan", op_plan,
        question=(("--question", "-q"), dict(required=True)), scope=(("--scope",), dict(default=None)),
        databases=(("--databases",), dict(default=None)), date_from=(("--date-from",), dict(default=None)),
        date_to=(("--date-to",), dict(default=None)), languages=(("--languages",), dict(default=None)),
        source_types=(("--source-types",), dict(default=None)),
        method=(("--method",), dict(default="narrative")), output=(("--output", "-o"), dict(default=None)))
    sub("strategy", "Build a systematic search strategy per database", op_strategy,
        concepts=(("--concepts",), dict(required=True)), databases=(("--databases",), dict(default=None)),
        vocab=(("--vocab",), dict(default=None)))
    sub("screen", "Record an inclusion/exclusion decision", op_screen,
        decision=(("--decision",), dict(required=True, choices=["include", "exclude", "maybe"])),
        ref=(("--ref",), dict(required=True)), reason=(("--reason",), dict(default=None)),
        stage=(("--stage",), dict(default="title-abstract")),
        reviewer=(("--reviewer",), dict(default=None)),
        blinded=(("--no-blind",), dict(action="store_false", default=True)))
    sub("prisma", "Compute PRISMA flow counts from local records", op_prisma)
    sub("evidence-table", "Build a traceable evidence table", op_evidence_table,
        output=(("--output", "-o"), dict(default=None)),
        fmt=(("--format",), dict(default="md", choices=["md", "csv"])),
        source_ids=(("--source-ids",), dict(default=None)))
    sub("risk-of-bias", "Record a domain-level risk-of-bias judgement", op_risk_of_bias,
        ref=(("--ref",), dict(required=True)), judgements=(("--judgements",), dict(required=True)),
        rationale=(("--rationale",), dict(default="")), reviewer=(("--reviewer",), dict(default=None)))
    sub("narrative", "Group evidence by direction without vote counting", op_narrative,
        topic=(("--topic",), dict(required=True)), limit=(("--limit",), dict(type=int, default=12)))
    sub("meta", "Pool effect sizes (fixed or random effects)", op_meta,
        effects_path=(("--effects",), dict(required=True)),
        model=(("--model",), dict(default="random", choices=["fixed", "random"])),
        output=(("--output", "-o"), dict(default=None)))
    sub("gaps", "Map coverage gaps across dimensions", op_gaps)
    sub("robustness", "Run sensitivity analysis on pooled estimates", op_robustness,
        effects_path=(("--effects",), dict(required=True)),
        quality_path=(("--quality",), dict(default=None)))
    sub("update-check", "Compare claim snapshots for living-review updates", op_update_check,
        previous_claims_path=(("--previous",), dict(required=True)),
        current_claims_path=(("--current",), dict(required=True)))
