#!/usr/bin/env python3
"""tools-rwt-workflow.py - Review queues, approval gates, decisions, PRISMA, workflow runner.

Capabilities: review/claim/screening queues with decisions, approval gates
(high-impact + publication readiness), hash-chained decision log, change
control, parallel reviewer reconciliation (agreement + disagreement list),
PRISMA flow data builder (+SVG), literature review planner (skeleton), risk-of
bias structured judgments, narrative synthesis grouping with vote-count
warning, evidence table builder, living review diff, robustness checklist,
durable run manifests/checkpoints, deterministic pipeline runner with
checkpoints/resume, cron-style "due" computation (no daemon), backup/restore
verification, and a regression release gate comparing benchmark runs.
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_w", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_w"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _ws(args):
    ws = C.Workspace(args.workspace)
    ws.load()
    return ws

def _queue_kind(kind):
    allowed = {"source", "claim", "screening", "entity", "escalation", "freshness-review",
               "transcription", "translation", "draft", "corrections"}
    return kind if kind in allowed else "source"

def cmd_queue_add(args):
    ws = _ws(args)
    detail = {}
    if args.context_file and os.path.isfile(args.context_file):
        with open(args.context_file, "r", encoding="utf-8") as fh:
            detail = json.load(fh)
    rec = ws.add_record("queue", {
        "kind": _queue_kind(args.kind), "status": "open",
        "title": args.title, "priority": args.priority,
        "assignee": args.assignee, "due": args.due,
        "detail": detail or {"note": args.note},
        "opened": C.now_iso(),
    }, actor=args.actor)
    return {"queue_id": rec["id"], "kind": rec["kind"]}

def cmd_queue_list(args):
    ws = _ws(args)
    items = ws.list_records("queue")
    if args.kind:
        items = [q for q in items if q.get("kind") == _queue_kind(args.kind)]
    if args.status:
        items = [q for q in items if q.get("status") == args.status]
    if args.assignee:
        items = [q for q in items if q.get("assignee") == args.assignee]
    prio = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    items.sort(key=lambda q: prio.get(q.get("priority", "normal"), 2))
    return {"items": items, "count": len(items)}

def cmd_queue_decide(args):
    ws = _ws(args)
    items = ws.list_records("queue")
    target = next((q for q in items if q.get("id") == args.id), None)
    if not target:
        raise C.ValidationError(f"Queue item {args.id} not found")
    if args.decision not in ("approve", "reject", "needs-info", "escalate", "defer"):
        raise C.ValidationError("decision must be approve|reject|needs-info|escalate|defer")
    updated = ws.update_record("queue", args.id, {
        "status": {"approve": "approved", "reject": "rejected", "needs-info": "open",
                   "escalate": "escalated", "defer": "deferred"}[args.decision],
        "decision": args.decision, "decision_note": args.note,
        "decided_by": args.actor, "decided": C.now_iso(),
    }, actor=args.actor)
    ws.add_record("decisions", {"subject": f"queue:{args.id}", "decision": args.decision,
                                "note": args.note, "actor": args.actor}, actor=args.actor)
    return {"id": args.id, "decision": args.decision, "status": updated.get("status")}

def cmd_gate(args):
    """High-impact approval gate: blocked until required role approvals exist."""
    ws = _ws(args)
    cfg = ws.load()
    decisions = ws.list_records("decisions")
    subject = args.subject
    needed_roles = [r.strip() for r in args.required_roles.split(",") if r.strip()]
    approvals = [d for d in decisions
                 if d.get("subject") == subject and d.get("decision") == "approve"
                 and d.get("role") in needed_roles]
    approved_roles = {d.get("role") for d in approvals}
    missing = [r for r in needed_roles if r not in approved_roles]
    embargo_ok = True
    embargo_until = (cfg.get("export_gate") or {}).get("embargo_until")
    if embargo_until and C.now_iso() < str(embargo_until):
        embargo_ok = False
    ok = not missing and embargo_ok
    return {"subject": subject, "required_roles": needed_roles,
            "approved_roles": sorted(approved_roles), "missing_roles": missing,
            "embargo_blocked": not embargo_ok, "embargo_until": embargo_until,
            "gate": "open" if ok else "blocked",
            "note": "Approvals must be recorded as decisions (workflow decisions-add --subject SUBJECT --role ROLE --decision approve)."}

def cmd_decisions_add(args):
    ws = _ws(args)
    rec = ws.add_record("decisions", {"subject": args.subject, "decision": args.decision,
                                      "role": args.role, "note": args.note,
                                      "actor": args.actor}, actor=args.actor)
    return {"decision_id": rec["id"]}

def cmd_decisions_list(args):
    ws = _ws(args)
    recs = ws.list_records("decisions")
    if args.subject:
        recs = [d for d in recs if d.get("subject") == args.subject]
    return {"decisions": recs, "count": len(recs)}

def cmd_change_control(args):
    ws = _ws(args)
    rec = ws.add_record("changes", {
        "baseline": args.baseline, "change": args.change,
        "rationale": args.rationale, "impact": args.impact,
        "status": "proposed", "requires_review": True,
        "opened": C.now_iso(),
    }, actor=args.actor)
    return {"change_id": rec["id"], "status": "proposed",
            "note": "Corpus/model/method/conclusion changes after an approved baseline need review; record the disposition as a decision."}

def cmd_change_decide(args):
    ws = _ws(args)
    rec = ws.update_record("changes", args.id,
                           {"status": args.disposition, "disposition_note": args.note,
                            "decided_by": args.actor, "decided": C.now_iso()},
                           actor=args.actor)
    ws.add_record("decisions", {"subject": f"change:{args.id}", "decision": args.disposition,
                                "role": args.role, "note": args.note, "actor": args.actor}, actor=args.actor)
    return {"change_id": args.id, "status": rec.get("status")}

def cmd_adjudicate(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    reviews = data.get("reviews", [])
    if len(reviews) < 2:
        raise C.ValidationError("Provide at least two independent reviews in {'reviews':[{reviewer, verdicts:{item: verdict}}]}")
    votes = {}
    for r in reviews:
        for item, verdict in (r.get("verdicts") or {}).items():
            votes.setdefault(item, []).append((r.get("reviewer"), verdict))
    agreement, disagreements = [], []
    for item, entries in votes.items():
        verdicts = [v for _r, v in entries]
        if len(set(verdicts)) == 1:
            agreement.append({"item": item, "verdict": verdicts[0]})
        else:
            disagreements.append({"item": item,
                                  "votes": [{"reviewer": r, "verdict": v} for r, v in entries]})
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_numbers_a",
                                                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools-rwt-numbers.py"))
        numbers = importlib.util.module_from_spec(spec)
        sys.modules["rwt_numbers_a"] = numbers
        spec.loader.exec_module(numbers)
        # two-rater kappa for items where all reviewers voted
        if len(reviews) == 2 and votes:
            items = sorted(votes)
            r1 = [dict(reviews[0]["verdicts"]).get(i) for i in items]
            r2 = [dict(reviews[1]["verdicts"]).get(i) for i in items]
            kappa_tmp = numbers.cmd_kappa(argparse.Namespace(input=None)) if False else None
            # inline kappa for the two raters
            labels = sorted({v for v in r1 + r2})
            idx = {l: i for i, l in enumerate(labels)}
            n = len(items)
            p_o = sum(1 for a, b in zip(r1, r2) if a == b) / n
            from collections import Counter as _Counter
            c1, c2 = _Counter(r1), _Counter(r2)
            p_e = sum((c1[l] / n) * (c2[l] / n) for l in labels)
            kappa = (p_o - p_e) / (1 - p_e) if p_e != 1 else 1.0
        else:
            kappa = None
    except Exception:
        kappa = None
    return {"items": len(votes), "agreement": agreement, "disagreements": disagreements,
            "cohen_kappa_two_reviewers": round(kappa, 4) if kappa is not None else None,
            "adjudication_required": [d["item"] for d in disagreements],
            "note": "Adjudicate disagreements against source evidence, terminology, style guide and user impact; record the outcome (workflow decisions-add)."}

def cmd_prisma(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    idf = int(data.get("identified", 0))
    dup = int(data.get("duplicates_removed", 0))
    screened = int(data.get("screened", idf - dup))
    excluded = int(data.get("excluded_at_screening", 0))
    fulltext = int(data.get("full_text_assessed", screened - excluded))
    excl_reasons = data.get("exclusion_reasons", {})
    included = int(data.get("included", fulltext - sum(int(v) for v in excl_reasons.values())))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 760" font-family="sans-serif" font-size="13">
<rect width="640" height="760" fill="#ffffff"/>
<text x="320" y="28" text-anchor="middle" font-size="17" font-weight="bold">PRISMA 2020 flow — {data.get('title', 'systematic review')}</text>
<g fill="#eef2f7" stroke="#33475b" stroke-width="1.5">
<rect x="60" y="50" width="230" height="70" rx="6"/>
<rect x="350" y="50" width="230" height="70" rx="6"/>
<rect x="60" y="170" width="230" height="70" rx="6"/>
<rect x="350" y="170" width="230" height="70" rx="6"/>
<rect x="60" y="290" width="230" height="70" rx="6"/>
<rect x="350" y="290" width="230" height="110" rx="6"/>
<rect x="60" y="450" width="230" height="80" rx="6"/>
<rect x="60" y="580" width="520" height="90" rx="6"/>
</g>
<g fill="#1a2b3c">
<text x="175" y="75" text-anchor="middle" font-weight="bold">Identification</text>
<text x="175" y="98" text-anchor="middle">Records identified</text><text x="175" y="113" text-anchor="middle">from databases (n = {idf})</text>
<text x="465" y="75" text-anchor="middle" font-weight="bold">Duplicates</text>
<text x="465" y="98" text-anchor="middle">Records removed</text><text x="465" y="113" text-anchor="middle">as duplicates (n = {dup})</text>
<text x="175" y="195" text-anchor="middle" font-weight="bold">Screening</text>
<text x="175" y="218" text-anchor="middle">Records screened</text><text x="175" y="233" text-anchor="middle">(n = {screened})</text>
<text x="465" y="195" text-anchor="middle" font-weight="bold">Excluded</text>
<text x="465" y="218" text-anchor="middle">Records excluded</text><text x="465" y="233" text-anchor="middle">(n = {excluded})</text>
<text x="175" y="315" text-anchor="middle" font-weight="bold">Eligibility</text>
<text x="175" y="338" text-anchor="middle">Full-text articles</text><text x="175" y="353" text-anchor="middle">assessed (n = {fulltext})</text>
<text x="465" y="315" text-anchor="middle" font-weight="bold">Excluded with reasons</text>
{''.join(f'<text x="360" y="{338 + i * 16}" font-size="11">{k}: n = {v}</text>' for i, (k, v) in enumerate(list(excl_reasons.items())[:6]))}
<text x="175" y="475" text-anchor="middle" font-weight="bold">Included</text>
<text x="175" y="498" text-anchor="middle">Studies included</text><text x="175" y="513" text-anchor="middle">in synthesis (n = {included})</text>
<text x="320" y="610" text-anchor="middle" font-weight="bold">Notes</text>
<text x="320" y="633" text-anchor="middle" font-size="11">{data.get('notes', 'Counts are as recorded in the screening log; discrepancies route to adjudication.')}</text>
<text x="320" y="652" text-anchor="middle" font-size="11">Generated by rwt workflow prisma; verify counts against the log before publication.</text>
</g></svg>"""
    if args.svg_output:
        with open(args.svg_output, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return {"identified": idf, "duplicates_removed": dup, "screened": screened,
            "excluded_at_screening": excluded, "full_text_assessed": fulltext,
            "exclusion_reasons": excl_reasons, "included": included,
            "svg_written": args.svg_output or None,
            "note": "Flow diagram reflects the numbers you supply; it does not validate them."}

def cmd_review_plan(args):
    return {"question": args.question,
            "scope": {"population": args.population, "concepts": args.concepts,
                      "context": args.context},
            "databases": [d.strip() for d in args.databases.split(",") if d.strip()],
            "date_range": args.date_range, "languages": args.languages.split(","),
            "source_types": [s.strip() for s in args.source_types.split(",") if s.strip()],
            "screening": {"title_abstract": "two reviewers, kappa reported",
                          "full_text": "dual with adjudication",
                          "exclusion_reasons": "recorded per PRISMA"},
            "synthesis": args.synthesis,
            "registration": "register the protocol before screening when applicable (e.g., PROSPERO/OSF); record the id in the decision log",
            "deliverables": ["PRISMA flow (workflow prisma)", "evidence table (review evidence-table)",
                             "risk-of-bias judgments (review riskofbias)", "synthesis + limitations"]}

def cmd_riskofbias(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    studies = data.get("studies", [])
    out = []
    for s in studies:
        domains = s.get("domains", {})
        judgments = {k: v for k, v in domains.items()}
        highs = sum(1 for v in judgments.values() if str(v).lower().startswith("high"))
        unclear = sum(1 for v in judgments.values() if str(v).lower().startswith("unclear") or str(v).lower().startswith("some"))
        overall = "high" if highs >= 1 else "some-concerns" if unclear >= 1 else "low"
        out.append({"study": s.get("id") or s.get("study"), "judgments": judgments,
                    "overall": overall,
                    "rationale": s.get("rationale")})
    return {"studies": out, "reviewers": data.get("reviewers"),
            "note": "Domain criteria follow the named tool (RoB2/ROBINS-I/QUADAS-2/Newcastle-Ottawa) supplied in input domains; judgments and rationale are recorded verbatim."}

def cmd_synthesis(args):
    with open(args.input, "r", encoding="utf-8") as fh:
        studies = json.load(fh)
    if isinstance(studies, dict):
        studies = studies.get("studies", [])
    groups = {}
    for s in studies:
        key = s.get("design") or s.get("domain") or "ungrouped"
        groups.setdefault(key, []).append(s)
    summary = []
    for key, members in sorted(groups.items()):
        votes_pos = sum(1 for m in members if str(m.get("direction", "")).lower() in ("positive", "supports", "benefit"))
        votes_neg = sum(1 for m in members if str(m.get("direction", "")).lower() in ("negative", "harm", "against"))
        summary.append({"group": key, "n_studies": len(members),
                        "supports": votes_pos, "against": votes_neg,
                        "vote_count_warning": votes_pos + votes_neg != len(members) or votes_pos == votes_neg,
                        "studies": [m.get("id") for m in members]})
    return {"groups": summary,
            "note": "Vote counting is shown only as a descriptive signal; synthesis must weight quality, design and directness of evidence, not tally."}

def cmd_evidence_table(args):
    ws = _ws(args)
    recs = ws.list_records("evidence")
    columns = ["claim_id", "document_id", "relation", "locator", "review_status"]
    md = "| " + " | ".join(columns) + " |\n" + "| " + " | ".join(["---"] * len(columns)) + " |\n"
    for r in recs:
        md += "| " + " | ".join(str(r.get(c, "")).replace("|", "/")[:80] for c in columns) + " |\n"
    html_rows = "".join("<tr>" + "".join(f"<td>{str(r.get(c, ''))}</td>" for c in columns) + "</tr>" for r in recs)
    return {"rows": len(recs), "markdown": md,
            "html": f"<table><thead><tr>{''.join(f'<th>{c}</th>' for c in columns)}</tr></thead><tbody>{html_rows}</tbody></table>",
            "note": "Evidence links from the workspace store; relations are as recorded by reviewers."}

def cmd_living_update(args):
    with open(args.baseline, "r", encoding="utf-8") as fh:
        baseline = json.load(fh)
    with open(args.new_studies, "r", encoding="utf-8") as fh:
        new = json.load(fh)
    if isinstance(baseline, dict):
        baseline = baseline.get("studies", [])
    if isinstance(new, dict):
        new = new.get("studies", [])
    base_keys = {C.normalize_ws(str(s.get("id") or s.get("title", "")).lower()) for s in baseline}
    fresh = [s for s in new if C.normalize_ws(str(s.get("id") or s.get("title", "")).lower()) not in base_keys]
    flag = [s for s in fresh if str(s.get("direction", "")).lower() in ("contradicts", "harm", "negative")]
    return {"new_studies": len(fresh), "contradicting_signals": flag,
            "action": "Re-screen and re-run the synthesis/robustness review if contradicting signals appear; record disposition."}

def cmd_robustness(args):
    checklist = [
        "Re-run synthesis excluding studies at high risk of bias",
        "Re-run synthesis excluding duplicate/maximal-overlap datasets",
        "Re-run with alternative effect measure or outcome definition",
        "Re-run with fixed vs random effects (if meta-analytic)",
        "Re-run excluding industry-funded studies as sensitivity",
        "Check conclusions against smallest plausible effect",
        "Check whether excluding the most-cited study changes the direction",
        "Review conflicts-of-interest declarations of included studies",
        "Confirm PRISMA flow arithmetic closes",
        "Confirm no outcome switching vs registered protocol",
    ]
    with open(args.answers, "r", encoding="utf-8") as fh:
        answers = json.load(fh)
    results = []
    for item in checklist:
        key = re.sub(r"\W+", "_", item.lower())[:60]
        val = answers.get(key) or answers.get(str(checklist.index(item) + 1))
        results.append({"check": item, "answer": val, "status": "pass" if val is True else "fail" if val is False else "unrecorded"})
    unrecorded = [r for r in results if r["status"] == "unrecorded"]
    fails = [r for r in results if r["status"] == "fail"]
    return {"checks": results, "unrecorded": len(unrecorded), "fails": len(fails),
            "verdict": "fragile" if fails else "robust-within-recorded-checks" if not unrecorded else "incomplete",
            "note": "Robustness is bounded by what was actually re-analyzed; document exclusions and assumptions."}

def cmd_runner(args):
    """Deterministic pipeline runner: execute named rwt steps from a JSON plan, checkpointing state."""
    ws = _ws(args)
    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    steps = plan.get("steps", [])
    state_path = args.state_file or os.path.join(ws.root, C.WORKSPACE_DIR, "workflow-state.json")
    state = {}
    if os.path.isfile(state_path):
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    elif args.resume:
        raise C.ValidationError(f"--resume requested but no state at {state_path}")
    results = []
    for i, step in enumerate(steps):
        sid = step.get("id") or f"step-{i+1}"
        if args.resume and state.get(sid, {}).get("status") == "done":
            results.append({"id": sid, "status": "skipped-resuming"})
            continue
        engine, action = (step.get("command") or "").split(" ", 1) if step.get("command") else (None, None)
        if not engine or not action:
            results.append({"id": sid, "status": "failed", "error": "step requires 'command' like 'search query --query x'"})
            state[sid] = {"status": "failed", "error": "bad step"}
            break
        r = _invoke_sibling(engine, [a for a in (action.split() + (step.get("args") or []))], args.workspace)
        state[sid] = {"status": "done" if r["ok"] else "failed", "ok": r["ok"]}
        results.append({"id": sid, "status": state[sid]["status"],
                        "error": None if r["ok"] else r.get("error", {}).get("message")})
        if not r["ok"]:
            break  # stop-on-failure; resume later with --resume
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    done = sum(1 for r in results if r["status"] == "done")
    return {"steps": results, "completed": done,
            "state_file": state_path,
            "note": "Resume with the same --plan --resume; failed steps halt the pipeline."}

def _invoke_sibling(engine, argv, workspace):
    import importlib.util
    this = os.path.dirname(os.path.abspath(__file__))
    sibling = {"corpus": "tools-rwt-corpus.py", "search": "tools-rwt-search.py",
               "citation": "tools-rwt-citation.py", "text": "tools-rwt-text.py",
               "numbers": "tools-rwt-numbers.py", "claims": "tools-rwt-claims.py",
               "collections": "tools-rwt-collections.py", "translate": "tools-rwt-translation.py",
               "locale": "tools-rwt-locale.py", "extract": "tools-rwt-extract.py",
               "media": "tools-rwt-media.py", "deliver": "tools-rwt-deliver.py",
               "security": "tools-rwt-security.py", "benchmark": "tools-rwt-benchmark.py",
               "models": "tools-rwt-models.py", "workflow": "tools-rwt-workflow.py"}.get(engine)
    if not sibling:
        return {"ok": False, "error": {"message": f"unknown engine '{engine}'"}}
    spec = importlib.util.spec_from_file_location("rwt_invoke_" + engine.replace("-", "_"), os.path.join(this, sibling))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        argv = [a.replace("{workspace}", workspace) for a in argv]
        return _run_module(module, argv, workspace)
    except SystemExit as exc:
        return {"ok": False, "error": {"message": f"step argument error: {exc}"}}
    except Exception as exc:
        return {"ok": False, "error": {"message": f"step failed: {exc}"}}

def _run_module(module, argv, workspace="."):
    parser = module.build_parser(prog="rwt-invoke")
    try:
        args = parser.parse_args(argv + (["--workspace", workspace] if "--workspace" not in argv else []))
    except SystemExit:
        args = parser.parse_args(argv)  # subcommand may not accept --workspace
    if not getattr(args, "func", None):
        return {"ok": False, "error": {"message": "no action given"}}
    try:
        payload = args.func(args)
    except SystemExit as exc:
        return {"ok": False, "error": {"message": f"step argument error: {exc}"}}
    except Exception as exc:
        return {"ok": False, "error": {"message": f"step failed: {exc}"}}
    return payload if isinstance(payload, dict) and "ok" in payload else {"ok": True, "result": payload}

def cmd_schedule_due(args):
    with open(args.schedule, "r", encoding="utf-8") as fh:
        entries = json.load(fh)
    if isinstance(entries, dict):
        entries = entries.get("jobs", [])
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    due, future = [], []
    for job in entries:
        nxt = job.get("next_due")
        try:
            dt = datetime.strptime(str(nxt), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            due.append({**job, "issue": "unparseable next_due"})
            continue
        (due if dt <= now else future).append(job)
    return {"now": now.strftime("%Y-%m-%d"), "due_jobs": due, "future_jobs": len(future),
            "note": "This computes what is due; an external scheduler (cron/systemd timer) must trigger rwt itself. No daemon is included."}

def cmd_state(args):
    ws = _ws(args)
    if args.action == "checkpoint":
        rec = ws.add_record("snapshots", {"kind": "checkpoint", "label": args.label,
                                          "corpus_hash": C.sha256_bytes(
                                              "".join(l for l in open(os.path.join(ws.root, C.AUDIT_PATH), encoding="utf-8").read().splitlines(True)).encode()),
                                          "created": C.now_iso()}, actor=args.actor)
        return {"checkpoint_id": rec["id"]}
    if args.action == "list":
        return {"checkpoints": ws.list_records("snapshots")}
    raise C.ValidationError("action must be checkpoint|list")

def cmd_backup_verify(args):
    ws = _ws(args)
    import tempfile, shutil
    backup_dir = args.backup_dir or os.path.join(ws.root, C.EXPORTS_DIR, "backup-verify-tmp")
    os.makedirs(backup_dir, exist_ok=True)
    problems = []
    verified = 0
    src_root = os.path.join(ws.root, C.STORES_DIR)
    for name in sorted(os.listdir(src_root)) if os.path.isdir(src_root) else []:
        if not name.endswith(".jsonl"):
            continue
        src = os.path.join(src_root, name)
        dst = os.path.join(backup_dir, name)
        shutil.copy2(src, dst)
        if C.sha256_file(src) != C.sha256_file(dst):
            problems.append({"file": name, "issue": "copy checksum mismatch"})
        else:
            verified += 1
    if not args.keep:
        shutil.rmtree(backup_dir, ignore_errors=True)
    return {"stores_verified": verified, "problems": problems,
            "backup_dir": backup_dir if args.keep else None,
            "ok": not problems,
            "note": "Verification restores stores to a temp dir and re-hashes; it does not validate corpus semantics."}

def cmd_regression_gate(args):
    with open(args.baseline, "r", encoding="utf-8") as fh:
        baseline = json.load(fh)
    with open(args.candidate, "r", encoding="utf-8") as fh:
        candidate = json.load(fh)
    b, c = baseline.get("scores", {}), candidate.get("scores", {})
    deltas = {k: round(c.get(k, 0) - v, 4) for k, v in b.items()}
    regressions = {k: d for k, d in deltas.items() if d < -args.tolerance}
    gate = "block" if regressions else "pass"
    return {"deltas": deltas, "regressions": regressions, "tolerance": args.tolerance,
            "gate": gate,
            "note": "Block = do not release. Investigate the benchmark per-case breakdown before overriding."}

def cmd_audit_verify(args):
    ws = _ws(args)
    return ws.verify_audit()

COMMANDS = {
    "queue-add": cmd_queue_add, "queue-list": cmd_queue_list, "queue-decide": cmd_queue_decide,
    "gate": cmd_gate, "decisions-add": cmd_decisions_add, "decisions-list": cmd_decisions_list,
    "change-control": cmd_change_control, "change-decide": cmd_change_decide,
    "adjudicate": cmd_adjudicate, "prisma": cmd_prisma, "review-plan": cmd_review_plan,
    "riskofbias": cmd_riskofbias, "synthesis": cmd_synthesis, "evidence-table": cmd_evidence_table,
    "living-update": cmd_living_update, "robustness": cmd_robustness,
    "runner": cmd_runner, "schedule-due": cmd_schedule_due, "state": cmd_state,
    "backup-verify": cmd_backup_verify, "regression-gate": cmd_regression_gate,
    "audit-verify": cmd_audit_verify,
}

def build_parser(prog="rwt workflow"):
    p = argparse.ArgumentParser(prog=prog, description="Workflow engines: queues, gates, PRISMA, runner, regression gate.")
    sub = p.add_subparsers(dest="action", metavar="action")

    sp = sub.add_parser("queue-add", help="Add an item to a review queue")
    sp.add_argument("--workspace", default="."); sp.add_argument("--kind", default="source",
                    choices=["source", "claim", "screening", "entity", "escalation", "freshness-review", "transcription", "translation", "draft", "corrections"])
    sp.add_argument("--title", required=True); sp.add_argument("--priority", default="normal",
                    choices=["critical", "high", "normal", "low"])
    sp.add_argument("--assignee"); sp.add_argument("--due"); sp.add_argument("--note")
    sp.add_argument("--context-file"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_queue_add)

    sp = sub.add_parser("queue-list", help="List queue items")
    sp.add_argument("--workspace", default="."); sp.add_argument("--kind")
    sp.add_argument("--status"); sp.add_argument("--assignee"); sp.set_defaults(func=cmd_queue_list)

    sp = sub.add_parser("queue-decide", help="Record a queue decision (also appends to decision log)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--id", required=True)
    sp.add_argument("--decision", required=True); sp.add_argument("--note"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_queue_decide)

    sp = sub.add_parser("gate", help="High-impact approval gate (blocked until required-role approvals recorded)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--subject", required=True)
    sp.add_argument("--required-roles", default="approver"); sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("decisions-add", help="Append a decision to the log")
    sp.add_argument("--workspace", default="."); sp.add_argument("--subject", required=True)
    sp.add_argument("--decision", required=True); sp.add_argument("--role", default="approver")
    sp.add_argument("--note"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_decisions_add)

    sp = sub.add_parser("decisions-list", help="List decisions (optionally by subject)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--subject")
    sp.set_defaults(func=cmd_decisions_list)

    sp = sub.add_parser("change-control", help="Propose a post-baseline change (corpus/model/method/conclusion)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--baseline", required=True)
    sp.add_argument("--change", required=True); sp.add_argument("--rationale", required=True)
    sp.add_argument("--impact", default="unspecified"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_change_control)

    sp = sub.add_parser("change-decide", help="Disposition a proposed change (approved/rejected/deferred)")
    sp.add_argument("--workspace", default="."); sp.add_argument("--id", required=True)
    sp.add_argument("--disposition", required=True, choices=["approved", "rejected", "deferred", "needs-info"])
    sp.add_argument("--role", default="approver"); sp.add_argument("--note"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_change_decide)

    sp = sub.add_parser("adjudicate", help="Reconcile two+ independent reviews; list disagreements")
    sp.add_argument("--input", required=True, help="JSON {reviews:[{reviewer, verdicts:{item: verdict}}]}")
    sp.set_defaults(func=cmd_adjudicate)

    sp = sub.add_parser("prisma", help="PRISMA 2020 flow data builder (+optional SVG)")
    sp.add_argument("--input", required=True, help="JSON {identified, duplicates_removed, screened, excluded_at_screening, exclusion_reasons, included, title?}")
    sp.add_argument("--svg-output"); sp.set_defaults(func=cmd_prisma)

    sp = sub.add_parser("review-plan", help="Skeleton a literature review protocol")
    sp.add_argument("--question", required=True); sp.add_argument("--population", default="")
    sp.add_argument("--concepts", default=""); sp.add_argument("--context", default="")
    sp.add_argument("--databases", default="local-scholarly,local-preprints")
    sp.add_argument("--date-range", default="2010-present"); sp.add_argument("--languages", default="en")
    sp.add_argument("--source-types", default="article,preprint,thesis,report")
    sp.add_argument("--synthesis", default="narrative"); sp.set_defaults(func=cmd_review_plan)

    sp = sub.add_parser("riskofbias", help="Record RoB domain judgments with rationale")
    sp.add_argument("--input", required=True, help="JSON {studies:[{id, domains:{name: low|some-concerns|high}, rationale}]}")
    sp.set_defaults(func=cmd_riskofbias)

    sp = sub.add_parser("synthesis", help="Group studies and tabulate direction (descriptive; not a substitute for weighting)")
    sp.add_argument("--input", required=True); sp.set_defaults(func=cmd_synthesis)

    sp = sub.add_parser("evidence-table", help="Render the evidence link table (Markdown + HTML)")
    sp.add_argument("--workspace", default="."); sp.set_defaults(func=cmd_evidence_table)

    sp = sub.add_parser("living-update", help="Diff new corpus findings against a baseline review")
    sp.add_argument("--baseline", required=True); sp.add_argument("--new-studies", required=True)
    sp.set_defaults(func=cmd_living_update)

    sp = sub.add_parser("robustness", help="Robustness checklist with recorded answers")
    sp.add_argument("--answers", required=True, help="JSON {check_key: true|false} or positional keys '1'..'10'")
    sp.set_defaults(func=cmd_robustness)

    sp = sub.add_parser("runner", help="Run a JSON pipeline of rwt commands with checkpoints and resume")
    sp.add_argument("--workspace", default="."); sp.add_argument("--plan", required=True)
    sp.add_argument("--state-file"); sp.add_argument("--resume", action="store_true")
    sp.set_defaults(func=cmd_runner)

    sp = sub.add_parser("schedule-due", help="Compute due jobs from a schedule JSON (external cron triggers runs)")
    sp.add_argument("--schedule", required=True, help="JSON list of {name, next_due: YYYY-MM-DD, command?}")
    sp.set_defaults(func=cmd_schedule_due)

    sp = sub.add_parser("state", help="Durable checkpoints: checkpoint|list")
    sp.add_argument("--workspace", default="."); sp.add_argument("--action", required=True, choices=["checkpoint", "list"])
    sp.add_argument("--label", default="checkpoint"); sp.add_argument("--actor", default="local")
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("backup-verify", help="Test-restore workspace stores and verify checksums")
    sp.add_argument("--workspace", default="."); sp.add_argument("--backup-dir")
    sp.add_argument("--keep", action="store_true"); sp.set_defaults(func=cmd_backup_verify)

    sp = sub.add_parser("regression-gate", help="Block release if benchmark scores drop beyond tolerance")
    sp.add_argument("--baseline", required=True, help="JSON {scores:{metric: value}}")
    sp.add_argument("--candidate", required=True); sp.add_argument("--tolerance", type=float, default=0.02)
    sp.set_defaults(func=cmd_regression_gate)

    sp = sub.add_parser("audit-verify", help="Verify the tamper-evident audit hash chain")
    sp.add_argument("--workspace", default="."); sp.set_defaults(func=cmd_audit_verify)
    return p

def run(argv):
    parser = build_parser(prog="rwt workflow")
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
