"""tools-rwt-eval.py - benchmarks, clean-state validation, leakage and regression release gates.

Benchmarks run the real operations of this workbench against a JSON suite supplied by the user
(see asset-benchmark-suite.json) and record every item in the local database. Nothing is
pre-scored: a run either reproduces the expected outcome or it does not.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

NAME = "eval"
ROLE = "environment health, capability benchmarks, leakage tests, clean-state validation, regression gates"
GROUPS = [("eval", "Health checks, benchmarks, leakage tests, clean-state validation and release gates")]
deps: dict = {}

CHECKS = ("factcheck", "retrieval", "citation", "translation", "ocr", "readability", "numbers", "style")


def _store():
    return deps["store"]


def op_health(ctx) -> dict:
    store = _store()
    conn = ctx.conn()
    checks = []
    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    db_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    add("database", db_integrity == "ok", db_integrity)
    add("fts5", store.meta(conn, "fts5", "0") == "1", "full-text index available")
    for sub in ("corpus", "outputs", "snapshots", "exports", "state"):
        path = ctx.path(sub)
        try:
            probe = os.path.join(path, ".write-probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            add(f"writable:{sub}", True, path)
        except OSError as exc:
            add(f"writable:{sub}", False, str(exc))
    try:
        free_mb = shutil.disk_usage(ctx.root).free // (1024 * 1024)
    except OSError:
        free_mb = 0
    add("disk_space", free_mb > 50, f"{free_mb} MB free")
    counts = {t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
              for t in ("sources", "segments", "claims", "tm_units", "terms")}
    add("corpus_present", True, dict(counts, note="informational: an empty corpus is a valid start"))
    adapters = deps["adapters"].adapter_status(ctx)
    add("adapters_optional", True,
        {k: ("available" if v["available"] else "not configured") for k, v in adapters.items()})
    audit_state = store.audit_verify(conn)
    add("audit_chain", audit_state["status"] == "ok", audit_state)
    ok = all(c["ok"] for c in checks)
    return {"status": "ok", "healthy": ok, "checks": checks, "counts": counts,
            "unavailable_features": [k for k, v in adapters.items() if not v["available"]],
            "note": "adapter-dependent capabilities (MT, OCR, ASR, embeddings, reranking) are optional; "
                    "their absence is not a health failure"}


def _run_check(ctx, check: str, item: dict) -> dict:
    store = _store()
    expected = item.get("expected")
    if check == "factcheck":
        claim = item.get("claim")
        if not claim:
            raise store.ToolError(2, "factcheck item needs 'claim'")
        result = deps["verify"].op_claim(ctx, claim)
        return {"actual": result["verdict"], "passed": result["verdict"] == expected,
                "detail": {"confidence": result["confidence"], "support": result["support_count"],
                           "oppose": result["oppose_count"]}}
    if check == "retrieval":
        query = item.get("query")
        relevant = set(item.get("relevant") or [])
        if not query or not relevant:
            raise store.ToolError(2, "retrieval item needs 'query' and 'relevant'")
        hits = deps["retrieval"].op_query(ctx, query, limit=int(item.get("k", 10)))["results"]
        found = {h["segment_id"] for h in hits} | {h["source_id"] for h in hits}
        passed = bool(relevant & found)
        return {"actual": sorted(relevant & found), "passed": passed,
                "detail": {"missing": sorted(relevant - found)}}
    if check == "citation":
        ref = item.get("ref") or {}
        style = item.get("style", "apa")
        actual = deps["citation"].format_reference(ref, style)
        norm = lambda s: " ".join((s or "").split()).lower()
        return {"actual": actual, "passed": norm(actual) == norm(expected), "detail": {"style": style}}
    if check == "translation":
        pair = item.get("segment") or {}
        if "source" not in pair or "target" not in pair:
            raise store.ToolError(2, "translation item needs segment.source and segment.target")
        findings = deps["translation"].qa_segment(ctx, pair)
        by_sev = {sev: sum(1 for f in findings if f.get("severity") == sev)
                  for sev in ("critical", "major", "minor")}
        max_critical = int(item.get("max_critical", 0))
        max_major = int(item.get("max_major", 0))
        passed = by_sev["critical"] <= max_critical and by_sev["major"] <= max_major
        return {"actual": by_sev, "passed": passed, "detail": {"findings": findings[:5]}}
    if check == "ocr":
        reference = item.get("reference_path")
        hypothesis = item.get("hypothesis_path")
        if not reference or not hypothesis:
            raise store.ToolError(2, "ocr item needs reference_path and hypothesis_path")
        result = deps["extract"].op_ocr_eval(ctx, reference, hypothesis)
        max_wer = float(item.get("max_wer", 0.1))
        return {"actual": result["wer"], "passed": result["wer"] <= max_wer,
                "detail": {"cer": result["cer"], "max_wer": max_wer}}
    if check == "readability":
        metrics = deps["writing"].readability_metrics(item.get("text") or "")
        max_grade = float(item.get("max_grade", 16))
        return {"actual": metrics["flesch_kincaid_grade"],
                "passed": metrics["flesch_kincaid_grade"] <= max_grade,
                "detail": metrics}
    if check == "numbers":
        result = deps["verify"].op_numbers(ctx, text=item.get("text") or "")
        return {"actual": result["mismatches"], "passed": result["mismatches"] == int(item.get("expected_mismatches", 0)),
                "detail": result["findings"][:5]}
    if check == "style":
        result = deps["writing"].op_style(ctx, text=item.get("text") or "", profile=item.get("profile", "academic"))
        limit = int(item.get("max_findings", 999))
        return {"actual": result["finding_count"], "passed": result["finding_count"] <= limit,
                "detail": result["by_rule"]}
    raise store.ToolError(2, f"unknown check '{check}'", {"supported": list(CHECKS)})


def op_benchmark(ctx, suite_path: str, name: str | None = None, threshold: float | None = None) -> dict:
    store = _store()
    if not os.path.exists(suite_path):
        raise store.ToolError(2, f"suite file not found: {suite_path}")
    try:
        suite = json.loads(store.read_text(suite_path))
    except json.JSONDecodeError as exc:
        raise store.ToolError(2, f"suite file must be JSON: {exc}")
    items = suite.get("items") if isinstance(suite, dict) else suite
    if not isinstance(items, list) or not items:
        raise store.ToolError(2, "suite must contain a non-empty 'items' array")
    max_items = int(ctx.cfg.get("evaluation", {}).get("max_benchmark_items", 500))
    if len(items) > max_items:
        raise store.ToolError(2, f"suite has {len(items)} items; limit is {max_items}")
    run_id = f"bench-{store.sha256_text(json.dumps(items, sort_keys=True) + store.now_iso())[:16]}"
    conn = ctx.conn()
    results, failures = [], 0
    for index, item in enumerate(items, 1):
        check = item.get("check")
        if check not in CHECKS:
            raise store.ToolError(2, f"item {index} has unsupported check '{check}'",
                                  {"supported": list(CHECKS)})
        try:
            outcome = _run_check(ctx, check, item)
        except store.ToolError as exc:
            outcome = {"actual": f"error: {exc.message}", "passed": False, "detail": {}}
        if not outcome["passed"]:
            failures += 1
        conn.execute("""INSERT INTO benchmarks(run_id,suite,item,metric,expected,actual,passed,detail)
            VALUES(?,?,?,?,?,?,?,?)""",
                     (run_id, name or os.path.basename(suite_path), item.get("id") or str(index),
                      check, json.dumps(item.get("expected")), json.dumps(outcome["actual"], default=str),
                      1 if outcome["passed"] else 0, json.dumps(outcome.get("detail", {}), default=str)))
        results.append({"item": item.get("id") or str(index), "check": check,
                        "expected": item.get("expected"), "actual": outcome["actual"],
                        "passed": outcome["passed"]})
    conn.commit()
    pass_rate = round((len(items) - failures) / len(items), 4)
    bar = float(threshold if threshold is not None else
                ctx.cfg.get("evaluation", {}).get("thresholds", {}).get("factcheck_accuracy", 0.85))
    return {"status": "ok", "run_id": run_id, "suite": name or os.path.basename(suite_path),
            "items": len(items), "passed": len(items) - failures, "failed": failures,
            "pass_rate": pass_rate, "threshold": bar, "gate_passed": pass_rate >= bar,
            "results": results[:200],
            "note": "a benchmark only measures the items it contains; extend the suite for real coverage"}


def op_regression(ctx, baseline_run: str, current_run: str, tolerance: float = 0.05) -> dict:
    store = _store()
    conn = ctx.conn()
    def metrics(run_id):
        rows = conn.execute("SELECT item,passed FROM benchmarks WHERE run_id=?", (run_id,)).fetchall()
        if not rows:
            raise store.ToolError(2, f"no benchmark results for run: {run_id}")
        return {r["item"]: bool(r["passed"]) for r in rows}
    base, current = metrics(baseline_run), metrics(current_run)
    common = set(base) & set(current)
    if not common:
        raise store.ToolError(2, "the two runs share no benchmark items")
    regressions = [item for item in sorted(common) if base[item] and not current[item]]
    improvements = [item for item in sorted(common) if not base[item] and current[item]]
    base_rate = sum(1 for i in common if base[i]) / len(common)
    curr_rate = sum(1 for i in common if current[i]) / len(common)
    drop = round(base_rate - curr_rate, 4)
    blocked = drop > tolerance or bool(regressions)
    return {"status": "ok", "baseline": baseline_run, "current": current_run,
            "common_items": len(common), "baseline_pass_rate": round(base_rate, 4),
            "current_pass_rate": round(curr_rate, 4), "drop": drop, "tolerance": tolerance,
            "regressions": regressions, "improvements": improvements,
            "release_gate": "block" if blocked else "allow",
            "reason": ("pass rate dropped beyond tolerance or items regressed" if blocked
                       else "no material regression detected")}


def op_cleanstate(ctx, required_paths: str | None = None, output: str | None = None) -> dict:
    """Validate that a clean skill root can run the workbench from relative paths only."""
    store = _store()
    base = store.base_dir()
    required = [p.strip() for p in (required_paths or "").split(",") if p.strip()] or [
        "tools-offline-research.py", "tools-rwt-store.py", "tools-rwt-retrieval.py",
        "knowledge-readme.md", "asset-default-config.json", "asset-capability-registry.json",
    ]
    missing, present = [], []
    for rel in required:
        full = os.path.join(base, rel)
        (present if os.path.exists(full) else missing).append(rel)
    with tempfile.TemporaryDirectory(prefix="rwt-clean-") as tmp:
        workdir = os.path.join(tmp, "clean-workspace")
        probe_ctx = store.Ctx(workspace=workdir, config=None, actor="clean-state-probe")
        try:
            health = op_health(probe_ctx)
            created = os.path.isdir(os.path.join(workdir, "db"))
        finally:
            probe_ctx.close()
    payload = {"status": "ok", "skill_root": os.path.basename(base), "present": present,
               "missing": missing, "fresh_workspace_bootstraps": created,
               "fresh_workspace_healthy": health["healthy"],
               "clean_state_passed": not missing and created and health["healthy"],
               "checks": health["checks"],
               "note": "the probe creates and discards a temporary workspace; nothing is written to the "
                       "declared workspace"}
    if output:
        payload["output"] = store.write_json(ctx.out_path(output), payload)
    return payload


def op_leakage(ctx, outputs_dir: str | None = None, restricted_labels: str = "confidential,restricted,legal-privileged") -> dict:
    """Detect restricted-corpus text or restricted labels appearing in exported outputs."""
    store = _store()
    conn = ctx.conn()
    target = outputs_dir or ctx.path("exports")
    labels = {l.strip() for l in restricted_labels.split(",") if l.strip()}
    restricted = []
    for row in conn.execute("SELECT id,title,meta FROM sources"):
        meta = json.loads(row["meta"] or "{}")
        if meta.get("sensitivity") in labels:
            text = "\n".join(r["text"] for r in conn.execute(
                "SELECT text FROM segments WHERE source_id=?", (row["id"],)))
            restricted.append({"source_id": row["id"], "label": meta.get("sensitivity"),
                               "shingles": store.shingles(text, 6)})
    leaks, scanned = [], 0
    if os.path.isdir(target):
        for dirpath, dirnames, filenames in os.walk(target):
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                if os.path.splitext(fn)[1].lower() not in store.TEXT_SUFFIXES:
                    continue
                body = store.read_text(path)
                scanned += 1
                body_shingles = store.shingles(body, 6)
                for entry in restricted:
                    overlap = len(body_shingles & entry["shingles"])
                    if overlap >= 3:
                        leaks.append({"output": os.path.relpath(path, ctx.root),
                                      "source_id": entry["source_id"], "label": entry["label"],
                                      "overlapping_ngrams": overlap})
    verdict = "fail" if leaks else "pass"
    action = ctx.cfg.get("evaluation", {}).get("leakage_terms_action", "fail")
    return {"status": "ok", "restricted_sources": len(restricted), "outputs_scanned": scanned,
            "leaks": leaks, "verdict": verdict, "policy_action": action,
            "blocked": verdict == "fail" and action == "fail",
            "note": "run this against the exports directory immediately before release"}


def op_acceptance(ctx, reviewer: str, task: str, score: int, notes: str = "", role: str = "researcher") -> dict:
    store = _store()
    if not (0 <= int(score) <= 5):
        raise store.ToolError(2, "score must be 0-5")
    conn = ctx.conn()
    rid = f"acc-{store.slug(reviewer, 20)}-{store.slug(task, 30)}"
    conn.execute("""INSERT OR REPLACE INTO reviews(id,kind,ref,assignee,status,priority,decision,note,
        created,decided) VALUES(?,?,?,?,'closed','normal',?,?,?,?)""",
                 (rid, "acceptance", task, reviewer, str(score),
                  f"role={role}; {notes}", store.now_iso(), store.now_iso()))
    conn.commit()
    rows = [dict(r) for r in conn.execute("SELECT assignee,decision FROM reviews WHERE kind='acceptance'")]
    scores = [int(r["decision"]) for r in rows if r["decision"] and r["decision"].isdigit()]
    return {"status": "ok", "recorded": rid, "score": score, "reviewer": reviewer,
            "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
            "responses": len(scores),
            "interpretation": "human acceptance measures repair effort real users needed, not model metrics"}


def register(groups, common):
    g = groups["eval"]

    def sub(cmd, help_text, func, /, **opts):
        p = g.add_parser(cmd, parents=[common], help=help_text, description=help_text)
        for key, spec in opts.items():
            kw = dict(spec[1])
            kw["dest"] = key
            p.add_argument(*spec[0], **kw)
        p.set_defaults(handler=func)

    sub("health", "Run the environment health check", op_health)
    sub("benchmark", "Run a capability benchmark suite", op_benchmark,
        suite_path=(("--suite",), dict(required=True)), name=(("--name",), dict(default=None)),
        threshold=(("--threshold",), dict(type=float, default=None)))
    sub("regression", "Compare two benchmark runs and gate the release", op_regression,
        baseline_run=(("--baseline",), dict(required=True)),
        current_run=(("--current",), dict(required=True)),
        tolerance=(("--tolerance",), dict(type=float, default=0.05)))
    sub("cleanstate", "Validate a clean skill root and fresh workspace", op_cleanstate,
        required_paths=(("--required",), dict(default=None)),
        output=(("--output", "-o"), dict(default=None)))
    sub("leakage", "Test outputs for restricted corpus leakage", op_leakage,
        outputs_dir=(("--outputs-dir",), dict(default=None)),
        restricted_labels=(("--labels",), dict(default="confidential,restricted,legal-privileged")))
    sub("acceptance", "Record a human acceptance rating", op_acceptance,
        reviewer=(("--reviewer",), dict(required=True)), task=(("--task",), dict(required=True)),
        score=(("--score",), dict(type=int, required=True)), notes=(("--notes",), dict(default="")),
        role=(("--role",), dict(default="researcher")))
