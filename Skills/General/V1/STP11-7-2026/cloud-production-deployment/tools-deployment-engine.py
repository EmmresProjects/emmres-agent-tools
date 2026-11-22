#!/usr/bin/env python3
"""
tools-deployment-engine.py — Core engine for the Cloud Production Deployment Skill Tool.

Role in the Skill Tool
----------------------
Shared library consumed by tools-production-deployment.py (the primary CLI entry point).
Holds everything that must behave identically no matter which command drives it:

  * asset loading with size caps and structured errors (Catalog, rubric, criteria, template)
  * catalog query primitives (list/search/show) over asset-catalog.json
  * the assessment engine (check evaluation, weighted scoring, verdict/risk mapping)
    that powers `assess` and `validate` and the acceptance criteria inside `plan`
  * the weighted decision-matrix engine used by `decision-matrix`
  * the deployment-plan builder used by `plan`

This module has no CLI of its own and performs no network or cloud calls. All
file access is read-only except for the atomic writer used by the CLI for --output.
Interrupts (Ctrl+C) propagate to the caller as KeyboardInterrupt.
"""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

ENGINE_VERSION = "1.0.0"
TOOL_DIR = Path(__file__).resolve().parent

CATALOG_ASSET = TOOL_DIR / "asset-catalog.json"
RUBRIC_ASSET = TOOL_DIR / "asset-assessment-rubric.json"
CRITERIA_ASSET = TOOL_DIR / "asset-decision-criteria.json"
PLAN_TEMPLATE_ASSET = TOOL_DIR / "asset-plan-template.md"

MAX_ASSET_BYTES = 8 * 1024 * 1024        # catalog is ~550 KB; 8 MB cap is generous
MAX_CONFIG_BYTES = 1024 * 1024           # deployment configs should never approach 1 MB
MAX_OPTIONS_BYTES = 256 * 1024           # decision-matrix options files
MAX_INPUT_FILE_BYTES = 4 * 1024 * 1024   # generic guard for --file reads

PROVIDERS = ["aws", "azure", "gcp", "oracle", "cloudflare", "digitalocean", "self-hosted", "multi-cloud"]
ENVIRONMENTS = ["development", "test", "staging", "production", "shared-services", "security", "logging", "network", "sandbox"]
ROLLBACK_STRATEGIES = ["blue-green", "canary", "rolling", "recreate"]


class EngineError(Exception):
    """Structured, user-facing error. `kind` is stable and machine-readable."""

    def __init__(self, kind, message, details=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {"kind": self.kind, "message": self.message, "details": self.details}


# ---------------------------------------------------------------------------
# Asset loading
# ---------------------------------------------------------------------------

def read_text_capped(path, max_bytes, what):
    p = Path(path)
    if not p.is_file():
        raise EngineError("not-found", f"{what} not found: {p}")
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise EngineError("unreadable", f"Cannot stat {what}: {p} ({exc})")
    if size > max_bytes:
        raise EngineError("too-large", f"{what} exceeds {max_bytes} bytes (got {size}): {p}")
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EngineError("unreadable", f"Cannot read {what}: {p} ({exc})")


def read_json_capped(path, max_bytes, what):
    text = read_text_capped(path, max_bytes, what)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EngineError("invalid-json", f"{what} is not valid JSON: {exc.msg}",
                          {"path": str(path), "line": exc.lineno, "column": exc.colno})
    if not isinstance(data, dict):
        raise EngineError("invalid-structure", f"{what} must be a JSON object at the top level")
    return data


def atomic_write_text(path, text):
    """Write atomically: temp file in the same directory, then rename. No partial files."""
    p = Path(path)
    if p.exists():
        raise EngineError("output-exists", f"Refusing to overwrite existing file (use --force to allow): {p}")
    _atomic_write(p, text)


def atomic_write_text_force(path, text):
    _atomic_write(Path(path), text)


def _atomic_write(p, text):
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class Catalog:
    def __init__(self, data):
        self.data = data
        self.categories = data["categories"]
        self._skills = []
        self._by_id = {}
        for cat in self.categories:
            for skill in cat["skills"]:
                record = dict(skill)
                record["category"] = cat["name"]
                record["categoryId"] = cat["id"]
                record["categoryNumber"] = cat["number"]
                record["categorySlug"] = cat["slug"]
                self._skills.append(record)
                self._by_id[record["id"]] = record

    @classmethod
    def load(cls, path=CATALOG_ASSET):
        data = read_json_capped(path, MAX_ASSET_BYTES, "catalog asset (asset-catalog.json)")
        for key in ("catalog", "edition", "categoryCount", "skillCount", "categories"):
            if key not in data:
                raise EngineError("invalid-structure", f"catalog asset is missing required key: {key}")
        cats = data["categories"]
        if not isinstance(cats, list) or not cats:
            raise EngineError("invalid-structure", "catalog asset 'categories' must be a non-empty list")
        total = 0
        for cat in cats:
            for key in ("number", "id", "slug", "name", "skills"):
                if key not in cat:
                    raise EngineError("invalid-structure", f"catalog category is missing key: {key}")
            total += len(cat["skills"])
        if total != data.get("skillCount"):
            raise EngineError("invalid-structure",
                              f"catalog asset inconsistent: skillCount={data.get('skillCount')} but {total} skill records found")
        return cls(data)

    def stats(self):
        pattern_counts = {}
        for s in self._skills:
            pattern_counts[s["pattern"]] = pattern_counts.get(s["pattern"], 0) + 1
        return {
            "catalog": self.data.get("catalog"),
            "edition": self.data.get("edition"),
            "published": self.data.get("published"),
            "categoryCount": len(self.categories),
            "skillCount": len(self._skills),
            "patternCounts": pattern_counts,
        }

    def list_categories(self):
        return [{"number": c["number"], "id": c["id"], "slug": c["slug"],
                 "name": c["name"], "skillCount": len(c["skills"])} for c in self.categories]

    def find_category(self, query):
        """Locate a category by number (1-30), id, slug, or case-insensitive name substring."""
        q = str(query).strip().lower()
        if not q:
            raise EngineError("invalid-input", "Empty category query")
        for c in self.categories:
            if q in (str(c["number"]), c["id"].lower(), c["slug"].lower()):
                return c
        matches = [c for c in self.categories if q in c["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise EngineError("ambiguous", f"Category query '{query}' matches {len(matches)} categories",
                              {"candidates": [c["name"] for c in matches]})
        raise EngineError("not-found", f"No category matches '{query}'. Use 'list' to see categories.")

    def list_skills(self, category_query, pattern=None):
        cat = self.find_category(category_query)
        skills = cat["skills"]
        if pattern:
            skills = [s for s in skills if s["pattern"] == pattern]
        return cat, skills

    def find_skill(self, query):
        """Locate exactly one skill by id, exact (case-insensitive) name, or unique substring."""
        q = str(query).strip()
        if not q:
            raise EngineError("invalid-input", "Empty skill query")
        if q in self._by_id:
            return self._by_id[q]
        ql = q.lower()
        exact = [s for s in self._skills if s["name"].lower() == ql]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise EngineError("ambiguous", f"Skill name '{q}' exists in several categories",
                              {"candidates": [{"id": s["id"], "name": s["name"], "category": s["category"]} for s in exact]})
        partial = [s for s in self._skills if ql in s["name"].lower() or ql in s["id"].lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise EngineError("ambiguous", f"Skill query '{q}' matches {len(partial)} skills",
                              {"candidates": [{"id": s["id"], "name": s["name"]} for s in partial[:15]],
                               "truncated": len(partial) > 15})
        raise EngineError("not-found", f"No skill matches '{q}'. Use 'search' to find skills.")

    def search(self, query, category_query=None, pattern=None, limit=25, use_regex=False):
        if not str(query).strip():
            raise EngineError("invalid-input", "Empty search query")
        limit = max(1, min(int(limit), 200))
        if use_regex:
            try:
                rx = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                raise EngineError("invalid-regex", f"Invalid regular expression: {exc}")
            pred = lambda text: bool(rx.search(text))
        else:
            needle = query.lower()
            pred = lambda text: needle in text.lower()
        pool = self._skills
        if category_query:
            cat = self.find_category(category_query)
            cat_num = cat["number"]
            pool = [s for s in pool if s["categoryNumber"] == cat_num]
        if pattern:
            pool = [s for s in pool if s["pattern"] == pattern]
        hits = [s for s in pool if pred(s["name"]) or pred(s["description"])]
        return {"query": query, "regex": bool(use_regex), "total": len(hits),
                "returned": min(len(hits), limit), "results": hits[:limit]}


# ---------------------------------------------------------------------------
# Assessment engine
# ---------------------------------------------------------------------------

def get_path(obj, dotted):
    """Resolve 'a.b.c' against nested dicts. Raises EngineError('path') when missing."""
    cur = obj
    walked = []
    for part in str(dotted).split("."):
        walked.append(part)
        if not isinstance(cur, dict) or part not in cur:
            raise EngineError("path", f"Path '{dotted}' not found (missing at '{'.'.join(walked)}')",
                              {"path": dotted, "missingAt": ".".join(walked)})
        cur = cur[part]
    return cur


def _op_eq(a, b): return a == b
def _op_neq(a, b): return a != b
def _op_gt(a, b): return _num(a) > _num(b)
def _op_gte(a, b): return _num(a) >= _num(b)
def _op_lt(a, b): return _num(a) < _num(b)
def _op_lte(a, b): return _num(a) <= _num(b)
def _op_in(a, b): return a in b
def _op_not_in(a, b): return a not in b
def _op_present(a, b): return a is not None and a != "" and a != [] and a != {}
def _op_truthy(a, b): return bool(a)
def _op_falsy(a, b): return not bool(a)
def _op_contains(a, b): return b in a
def _op_matches(a, b): return re.search(str(b), str(a)) is not None

def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            return float(v)
        except (TypeError, ValueError):
            raise EngineError("type", f"Expected a number, got {type(v).__name__}: {v!r}")
    return float(v)

def _op_required_keys(a, b):
    if not isinstance(a, dict):
        return False
    return all(k in a and a[k] not in (None, "", [], {}) for k in b)

OPS = {
    "eq": _op_eq, "neq": _op_neq, "gt": _op_gt, "gte": _op_gte, "lt": _op_lt, "lte": _op_lte,
    "in": _op_in, "not_in": _op_not_in, "present": _op_present, "truthy": _op_truthy,
    "falsy": _op_falsy, "contains": _op_contains, "matches": _op_matches,
    "required_keys": _op_required_keys,
}

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def load_rubric(path=RUBRIC_ASSET):
    data = read_json_capped(path, MAX_ASSET_BYTES, "assessment rubric asset")
    for key in ("rubricId", "rubricVersion", "checks", "riskBands", "verdictThresholds"):
        if key not in data:
            raise EngineError("invalid-structure", f"rubric asset is missing required key: {key}")
    if not isinstance(data["checks"], list) or not data["checks"]:
        raise EngineError("invalid-structure", "rubric 'checks' must be a non-empty list")
    seen = set()
    for chk in data["checks"]:
        for key in ("id", "title", "domain", "severity", "weight", "path", "op"):
            if key not in chk:
                raise EngineError("invalid-structure", f"rubric check missing key '{key}': {chk.get('id', '<no id>')}")
        if chk["id"] in seen:
            raise EngineError("invalid-structure", f"duplicate rubric check id: {chk['id']}")
        seen.add(chk["id"])
        if chk["op"] not in OPS:
            raise EngineError("invalid-structure", f"rubric check {chk['id']} uses unknown op: {chk['op']}")
        if chk["severity"] not in SEVERITY_ORDER:
            raise EngineError("invalid-structure", f"rubric check {chk['id']} has unknown severity: {chk['severity']}")
        if not isinstance(chk["weight"], (int, float)) or chk["weight"] <= 0:
            raise EngineError("invalid-structure", f"rubric check {chk['id']} weight must be a positive number")
        applies = chk.get("appliesWhen")
        if applies is not None:
            if not isinstance(applies, dict) or "path" not in applies or "equals" not in applies:
                raise EngineError("invalid-structure", f"rubric check {chk['id']} appliesWhen needs 'path' and 'equals'")
    return data


def load_config(path, rubric=None):
    cap = MAX_CONFIG_BYTES
    if isinstance(rubric, dict):
        cap = int(rubric.get("maxConfigBytes", MAX_CONFIG_BYTES))
    cfg = read_json_capped(path, cap, "deployment config")
    return cfg


def evaluate_check(cfg, check):
    applies = check.get("appliesWhen")
    if applies:
        try:
            actual = get_path(cfg, applies["path"])
        except EngineError:
            return {"result": "skip", "reason": f"appliesWhen path '{applies['path']}' not present in config",
                    "actual": None, "expected": check.get("expect")}
        if actual != applies["equals"]:
            return {"result": "skip", "reason": f"appliesWhen '{applies['path']}' is {actual!r}, not {applies['equals']!r}",
                    "actual": actual, "expected": check.get("expect")}
    try:
        actual = get_path(cfg, check["path"])
    except EngineError as exc:
        return {"result": "fail", "reason": exc.message, "actual": None,
                "expected": check.get("expect"), "missing": True}
    op = OPS[check["op"]]
    try:
        ok = bool(op(actual, check.get("expect")))
    except EngineError as exc:
        return {"result": "fail", "reason": exc.message, "actual": actual, "expected": check.get("expect")}
    except (TypeError, ValueError, re.error) as exc:
        return {"result": "fail", "reason": f"evaluation error: {exc}", "actual": actual,
                "expected": check.get("expect")}
    if ok:
        return {"result": "pass", "reason": "ok", "actual": actual, "expected": check.get("expect")}
    on_fail = check.get("onFail", "fail")
    return {"result": "warn" if on_fail == "warn" else "fail",
            "reason": f"expected {check['path']} {check['op']} {check.get('expect')!r}, got {actual!r}",
            "actual": actual, "expected": check.get("expect")}


def assess_config(cfg, rubric, strict=False):
    results = []
    total_weight = 0.0
    earned = 0.0
    scale = rubric.get("scoreScale", {"pass": 1.0, "warn": 0.5, "fail": 0.0})
    domain_totals = {}
    for check in rubric["checks"]:
        outcome = evaluate_check(cfg, check)
        w = float(check["weight"])
        res = outcome["result"]
        if res != "skip":
            total_weight += w
            earned += w * float(scale.get(res, 0.0))
        dom = domain_totals.setdefault(check["domain"], {"weight": 0.0, "earned": 0.0, "fails": 0, "warns": 0})
        if res != "skip":
            dom["weight"] += w
            dom["earned"] += w * float(scale.get(res, 0.0))
        if res == "fail":
            dom["fails"] += 1
        elif res == "warn":
            dom["warns"] += 1
        results.append({
            "id": check["id"], "title": check["title"], "domain": check["domain"],
            "severity": check["severity"], "weight": check["weight"],
            "result": res, "reason": outcome["reason"],
            "remediation": check.get("remediation", ""),
            "evidence": check.get("evidence", ""),
        })
    score = round(100.0 * earned / total_weight, 1) if total_weight else 0.0
    thresholds = rubric["verdictThresholds"]
    fails = [r for r in results if r["result"] == "fail"]
    warns = [r for r in results if r["result"] == "warn"]
    critical_fail = any(r["severity"] == "critical" for r in fails)
    high_fail = any(r["severity"] == "high" for r in fails)
    if critical_fail or high_fail or score < thresholds.get("failBelow", 50):
        verdict = "fail"
    elif fails or warns or score < thresholds.get("passBelow", 85):
        verdict = "warn"
    else:
        verdict = "pass"
    if strict and verdict == "warn":
        verdict = "fail"
    risk = "critical"
    for band in sorted(rubric["riskBands"], key=lambda b: b["minScore"], reverse=True):
        if score >= band["minScore"]:
            risk = band["risk"]
            break
    if critical_fail:
        risk = "critical"
    elif high_fail and SEVERITY_ORDER.get(risk, 0) < SEVERITY_ORDER["high"]:
        risk = "high"
    domain_summary = []
    for dom, agg in sorted(domain_totals.items()):
        dom_score = round(100.0 * agg["earned"] / agg["weight"], 1) if agg["weight"] else None
        domain_summary.append({"domain": dom, "score": dom_score, "fails": agg["fails"], "warns": agg["warns"]})
    identifiers = {k: cfg.get(k) for k in ("service", "environment", "provider", "region", "exposure") if k in cfg}
    return {
        "service": identifiers,
        "rubricId": rubric.get("rubricId"),
        "rubricVersion": rubric.get("rubricVersion"),
        "score": score,
        "verdict": verdict,
        "risk": risk,
        "checkedChecks": sum(1 for r in results if r["result"] != "skip"),
        "skippedChecks": sum(1 for r in results if r["result"] == "skip"),
        "failures": len(fails),
        "warnings": len(warns),
        "checks": results,
        "domains": domain_summary,
        "retestProcedure": rubric.get("retestProcedure", []),
        "strict": bool(strict),
    }


# ---------------------------------------------------------------------------
# Decision matrix engine
# ---------------------------------------------------------------------------

def load_criteria(path=CRITERIA_ASSET):
    data = read_json_capped(path, MAX_ASSET_BYTES, "decision criteria asset")
    for key in ("criteriaId", "criteriaVersion", "criteria"):
        if key not in data:
            raise EngineError("invalid-structure", f"criteria asset is missing required key: {key}")
    criteria = data["criteria"]
    if not isinstance(criteria, list) or len(criteria) < 2:
        raise EngineError("invalid-structure", "criteria asset 'criteria' must be a list with at least 2 entries")
    seen = set()
    weight_sum = 0.0
    for c in criteria:
        for key in ("id", "name", "weight", "direction", "description"):
            if key not in c:
                raise EngineError("invalid-structure", f"criterion missing key '{key}': {c.get('id', '<no id>')}")
        if c["id"] in seen:
            raise EngineError("invalid-structure", f"duplicate criterion id: {c['id']}")
        seen.add(c["id"])
        if c["direction"] not in ("higher-better", "lower-better"):
            raise EngineError("invalid-structure", f"criterion {c['id']} direction must be higher-better|lower-better")
        if not isinstance(c["weight"], (int, float)) or c["weight"] <= 0:
            raise EngineError("invalid-structure", f"criterion {c['id']} weight must be a positive number")
        weight_sum += float(c["weight"])
    if abs(weight_sum - 100.0) > 0.5:
        raise EngineError("invalid-structure", f"criterion weights must sum to 100 (got {weight_sum})")
    data["weightSum"] = weight_sum
    return data


def load_options(path):
    data = read_json_capped(path, MAX_OPTIONS_BYTES, "decision options file")
    options = data.get("options")
    if not isinstance(options, list):
        raise EngineError("invalid-structure", "options file must contain an 'options' list")
    if not (2 <= len(options) <= 12):
        raise EngineError("invalid-input", f"provide between 2 and 12 options (got {len(options)})")
    names = []
    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            raise EngineError("invalid-structure", f"option #{i + 1} must be a JSON object")
        if not str(opt.get("name", "")).strip():
            raise EngineError("invalid-structure", f"option #{i + 1} is missing a non-empty 'name'")
        if not isinstance(opt.get("scores"), dict):
            raise EngineError("invalid-structure", f"option '{opt.get('name')}' is missing a 'scores' object")
        names.append(opt["name"].strip().lower())
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise EngineError("invalid-input", f"option names must be unique (duplicates: {', '.join(dupes)})")
    return data


def _normalize(score, direction):
    # Raw scores are always expressed as 1 (worst fit) to 5 (best fit) for the
    # decision at hand — including for lower-better real-world metrics, where a
    # 5 means the LOWEST cost/burden/lock-in. Normalize uniformly; `direction`
    # is reported as context, it does not invert the scale.
    s = float(score)
    return (s - 1.0) / 4.0


def evaluate_decision(options_data, criteria_data, memo=""):
    criteria = criteria_data["criteria"]
    options = options_data["options"]
    crit_ids = [c["id"] for c in criteria]
    rows = []
    problems = []
    for opt in options:
        scores = opt["scores"]
        missing = [cid for cid in crit_ids if cid not in scores]
        invalid = {cid: scores[cid] for cid in crit_ids
                   if cid in scores and (isinstance(scores[cid], bool) or not isinstance(scores[cid], (int, float))
                                         or not (1.0 <= float(scores[cid]) <= 5.0))}
        if missing or invalid:
            problems.append({"option": opt["name"], "missing": missing,
                             "invalid": {k: v for k, v in invalid.items()}})
            continue
        breakdown = []
        total = 0.0
        for c in criteria:
            raw = float(scores[c["id"]])
            norm = _normalize(raw, c["direction"])
            contribution = c["weight"] * norm
            total += contribution
            breakdown.append({"criterion": c["id"], "name": c["name"], "weight": c["weight"],
                              "direction": c["direction"], "rawScore": raw,
                              "normalized": round(norm, 4), "weighted": round(contribution, 3)})
        rows.append({
            "name": opt["name"].strip(),
            "totalScore": round(total, 2),
            "breakdown": breakdown,
            "notes": str(opt.get("notes", "")).strip(),
        })
    if problems:
        raise EngineError("invalid-input",
                          "Some options have missing or out-of-range scores (scores must be numbers 1-5 for every criterion).",
                          {"problems": problems})
    rows.sort(key=lambda r: r["totalScore"], reverse=True)
    winner = rows[0]
    rejected = []
    for row in rows[1:]:
        shortfalls = []
        for b in row["breakdown"]:
            shortfalls.append({
                "criterion": b["criterion"], "name": b["name"],
                "rawScore": b["rawScore"], "weight": b["weight"], "direction": b["direction"],
                "pointsLost": round(b["weight"] * (1.0 - b["normalized"]), 3),
            })
        shortfalls.sort(key=lambda s: s["pointsLost"], reverse=True)
        rejected.append({
            "name": row["name"],
            "totalScore": row["totalScore"],
            "gapToWinner": round(winner["totalScore"] - row["totalScore"], 2),
            "weakestCriteria": shortfalls[:3],
            "notes": row["notes"],
        })
    return {
        "criteriaId": criteria_data.get("criteriaId"),
        "criteriaVersion": criteria_data.get("criteriaVersion"),
        "context": str(options_data.get("context", "")).strip(),
        "memo": str(memo or options_data.get("memo", "")).strip(),
        "ranking": rows,
        "winner": {"name": winner["name"], "totalScore": winner["totalScore"]},
        "rejected": rejected,
        "weightSum": criteria_data.get("weightSum", 100.0),
    }


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

PATTERN_PHASES = {
    "provision-from-code": [
        ("Phase 0 — Review and approval gate", [
            "Land the change as reviewed infrastructure code (PR) with policy checks green; no console-first changes.",
            "Peer review covers: account/subscription boundaries, network CIDRs, IAM model, tagging, budget, rollback and teardown path.",
            "Approve the change ticket and record the approver, date, and rollback sign-off.",
        ], "Approval recorded; artifact hashes pinned in the pipeline; plan diff reviewed."),
        ("Phase 1 — Identity and accounts", [
            "Create or select the target account/subscription/project via the landing-zone vending path.",
            "Bootstrap least-privilege roles; deploy credentials isolated from user credentials; enable audit logging.",
        ], "Account vending completes from code; break-glass access tested and logged."),
        ("Phase 2 — Network, DNS and TLS", [
            "Apply VPC/VNet/subnet layout, routing, and egress controls; register DNS records through managed DNS.",
            "Issue and validate TLS certificates (automated issuance + renewal); confirm managed TLS on every public endpoint.",
        ], "dig/curl from an external vantage point returns the expected records and a valid certificate chain."),
        ("Phase 3 — Provision via reviewed IaC", [
            "Apply the reviewed plan with remote, encrypted, locked state; record the artifact digests applied.",
            "Apply cost guardrails (budgets, quotas, tag policies) before or with the workload resources.",
        ], "State is remote and locked; drift detection scheduled; budget alert armed."),
        ("Phase 4 — Verify live endpoint and dependencies", [
            "Run connectivity, authentication, encryption, backup, restore, failover, and rollback tests against the live service.",
            "Validate golden-signal dashboards (latency, traffic, errors, saturation) and alert delivery to on-call.",
        ], "Synthetic check green; restore test evidence attached; alert fires in the on-call rota."),
        ("Phase 5 — Evidence and handover", [
            "Attach evidence bundle (plan output, applies, test results, dashboards, restore log, drill notes) to the change record.",
            "Update the ownership registry; hand over runbooks; schedule the first post-deploy review.",
        ], "Change record complete; ownership registry updated; rollback tested before closure."),
    ],
    "runbook-definition": [
        ("Phase 0 — Scope and accountable roles", [
            "Name the accountable owner, deputy, and escalation chain; agree the verified completion condition.",
            "List prerequisites: access, tooling, credentials (short-lived), communication channels, maintenance window.",
        ], "Roles and completion condition agreed in writing; prerequisites satisfied or scheduled."),
        ("Phase 1 — Ordered actions with approval points", [
            "Draft the ordered action sequence; mark every irreversible step as an explicit approval point.",
            "Define the rollback or abort action attached to each approval point.",
        ], "Runbook reviewed by a second operator; each approval point has a paired rollback step."),
        ("Phase 2 — Recovery objectives and escalation", [
            "Set RPO/RTO targets; define escalation thresholds (time, impact, error budget) and who is paged at each level.",
            "Document communication templates for stakeholders, status page, and post-incident review.",
        ], "Escalation matrix tested with a page drill; RPO/RTO acknowledged by the service owner."),
        ("Phase 3 — Rehearsal (game day)", [
            "Execute the runbook in staging or during a scheduled game day; measure timings against RTO.",
            "Capture gaps, fix the runbook, and re-rehearse until the completion condition is met unaided.",
        ], "Two consecutive clean rehearsals; runbook versioned in the operations repository."),
        ("Phase 4 — Activation and audit", [
            "Activate the runbook in production under change governance; log every step and decision.",
            "Attach required evidence (timestamps, outputs, approvals); close with a verified completion condition.",
        ], "Evidence complete; completion condition verified; review scheduled within 10 business days."),
    ],
    "decision-matrix": [
        ("Phase 0 — Frame the decision", [
            "Write the problem statement, constraints (residency, budget, deadlines), and the options under consideration (2-12).",
            "Confirm the scoring guide 1-5 per criterion and who scores (at least two reviewers).",
        ], "Decision brief reviewed; options fixed before scoring to avoid anchoring."),
        ("Phase 1 — Score with the weighted matrix", [
            "Run `decision-matrix` with asset-decision-criteria.json (availability, latency, residency, security, operational burden, recovery targets, scalability, lock-in, total cost).",
            "Record raw scores with evidence references; note assumptions per option.",
        ], "Matrix output stored with the decision record; reviewers sign off on scores."),
        ("Phase 2 — Record rejected options", [
            "Document why each rejected option lost, the weighted gap, and its weakest criteria.",
            "Capture conditions under which a rejected option would be revisited (e.g. residency law change, price shift).",
        ], "Rejected options recorded with reasons and revisit triggers."),
        ("Phase 3 — Approve and communicate", [
            "Route the recommendation through the approval path; record approver and date.",
            "Communicate the decision, caveats, and review date to stakeholders and the ownership registry.",
        ], "Decision record complete; follow-up review scheduled."),
    ],
    "evaluation-gate": [
        ("Phase 0 — Define thresholds", [
            "Fix explicit pass / warn / fail thresholds for every measured signal; name the risk rating scale.",
            "Define the evidence to retain: environment, region, resource identifiers, timestamps, observed values, owner.",
        ], "Thresholds and evidence list approved by the service owner."),
        ("Phase 1 — Measure in the target environment", [
            "Run the evaluation against the real target environment (not a marketing page); capture raw evidence.",
            "Record observed values verbatim; never edit evidence after the fact.",
        ], "Evidence bundle collected and checksummed where applicable."),
        ("Phase 2 — Rate, remediate, retest", [
            "Apply thresholds: pass / warn / fail per check; roll up to a risk rating.",
            "File remediation with owner and date for every warn/fail; schedule the repeatable retest procedure.",
        ], "Rating assigned; remediations tracked; retest date booked."),
        ("Phase 3 — Gate and record", [
            "Feed the result into the release gate (block on fail; warn requires explicit sign-off).",
            "Archive the evaluation record with the change or audit trail.",
        ], "Gate decision recorded; audit trail updated."),
    ],
    "operate-service": [
        ("Phase 0 — Versioned configuration baseline", [
            "Move the service definition into versioned configuration (IaC / manifests); enable remote encrypted state and locking.",
            "Declare quotas, health checks, and observability hooks in the same configuration.",
        ], "Configuration in git; state remote and locked; drift detection enabled."),
        ("Phase 1 — Least privilege and encryption", [
            "Scope identities to least privilege; enable short-lived credentials; isolate deploy credentials.",
            "Enforce encryption at rest and in transit with managed keys; rotate secrets on schedule.",
        ], "Access review passes; key rotation tested; no embedded secrets detected."),
        ("Phase 2 — Health, observability, automated recovery", [
            "Wire health checks with defined failure behavior (restart, failover, shed load); alert only on actionable conditions.",
            "Verify automated recovery by killing a replica / failing over a dependency in a controlled test.",
        ], "Failure behavior demonstrated; alerts route to on-call with tested delivery."),
        ("Phase 3 — Quota, drift and failure testing", [
            "Set and monitor quotas; alert at 70/85/95 percent consumption.",
            "Run scheduled failure-behavior tests; review results; fix and re-test.",
        ], "Quota alerts armed; latest failure test green; evidence retained."),
        ("Phase 4 — Review and iterate", [
            "Quarterly: configuration drift, credential age, certificate expiry, quota headroom, alert quality (noisy alerts retired).",
            "Feed findings back into the baseline configuration; bump versions; record in the ownership registry.",
        ], "Quarterly review logged; configuration version bumped; owners current."),
    ],
    "focused-capability": [
        ("Phase 0 — Declare inputs and outputs", [
            "Write the input contract (identity, region, network, data classification, budget) and the output contract (endpoints, artifacts, evidence).",
            "Fix production acceptance criteria up front, including rollback and teardown.",
        ], "Input/output contract reviewed; acceptance criteria agreed."),
        ("Phase 1 — Reference implementation", [
            "Implement via reviewed infrastructure code with pinned artifacts; least privilege; encryption everywhere.",
            "Monitor the rollout with golden-signal dashboards; alert on actionable conditions only.",
        ], "Implementation applied from code; dashboards and alerts live."),
        ("Phase 2 — Verify against acceptance criteria", [
            "Execute the verification suite: connectivity, authn/authz, encryption, backup+restore, failover, rollback.",
            "Run `assess` with a deployment config to score the service against the production rubric; close all fail findings.",
        ], "Assessment verdict pass (or warn with signed-off exceptions); evidence attached."),
        ("Phase 3 — Rollback and disaster readiness", [
            "Document and test rollback (automated where possible) and regional failover drill; measure against RPO/RTO.",
            "Register runbooks, owners, and alert routes; schedule the recurring restore test.",
        ], "Rollback and failover evidence attached; registry updated."),
    ],
}

EVIDENCE_CHECKLIST = [
    "Reviewed IaC plan output and applied artifact digests (pinned versions, signed where available).",
    "Remote encrypted state with locking enabled; state backup confirmed; drift detection scheduled.",
    "Change ticket with approvals, maintenance window, and rollback sign-off.",
    "Live endpoint verification: DNS resolution, valid TLS chain, HTTP security headers, no unintended public services.",
    "Connectivity and authentication tests (service-to-service, database, queue/cache, object storage).",
    "Backup success log AND a dated restore test with validated data and timings.",
    "Regional failover or AZ-loss drill notes with measured RTO against target; RPO evidence.",
    "Rollback rehearsal record (automated rollback if configured) with time-to-recover measurement.",
    "Golden-signal dashboards (latency, traffic, errors, saturation) plus business-health SLIs.",
    "Alert delivery test evidence (page received by on-call) and alert routing configuration.",
    "Certificate expiry monitoring (>= 30 days notice) and secret rotation records.",
    "Ownership registry entry, runbook links, escalation path, and cost/budget guardrail confirmation.",
]

ROLLBACK_STEPS = [
    "Trigger the rollback mechanism approved for this change (automated rollback, blue/green cutover back, or canary abort) — never repair production by hand-editing under pressure.",
    "Verify rollback health: endpoint responds, error rate back to baseline, saturation normal, synthetic checks green.",
    "If data was written, execute the documented data rollback or forward-fix plan; confirm integrity with application-level checks.",
    "Disable or quarantine the failed artifact; preserve logs, metrics, and state for the post-incident review.",
    "Communicate status (stakeholders/status page); update the change record with timeline and decision points.",
    "Open a blameless post-incident review within 5 business days; file follow-up actions with owners and dates.",
    "Before re-attempting: fix forward in code, re-run the full verification suite including rollback rehearsal.",
]

ESCALATION_PATH = [
    "Level 1 — On-call engineer acknowledges within 15 minutes; follows the service runbook; mitigates or escalates.",
    "Level 2 — Service owner / secondary on-call within 30 minutes; decides rollback vs. fix-forward; owns stakeholder comms.",
    "Level 3 — Incident commander + management chain for customer-impacting or security events; invoke the incident management process and comms templates.",
    "Level 4 — Vendor/provider support under the support contract for platform faults; security incidents engage the security incident process immediately.",
]

APPROVAL_GATES = [
    "Gate A — Design/plan approval: peer review of the IaC diff, risk notes, and rollback plan (before apply).",
    "Gate B — Maintenance window approval: service owner confirms window, comms, and on-call coverage.",
    "Gate C — Go/no-go before cutover: checklist complete, monitoring green, smoke tests pass or abort.",
    "Gate D — Closure approval: evidence bundle attached, restore/rollback evidence present, registry updated.",
]


def build_plan(catalog, *, skill_query=None, category_query=None, provider="aws", environment="production",
               rpo_minutes=15, rto_minutes=60, now=None):
    if provider not in PROVIDERS:
        raise EngineError("invalid-input", f"provider must be one of: {', '.join(PROVIDERS)}")
    if environment not in ENVIRONMENTS:
        raise EngineError("invalid-input", f"environment must be one of: {', '.join(ENVIRONMENTS)}")
    try:
        rpo_minutes = int(rpo_minutes)
        rto_minutes = int(rto_minutes)
    except (TypeError, ValueError):
        raise EngineError("invalid-input", "rpo-minutes and rto-minutes must be integers")
    if rpo_minutes < 0 or rto_minutes < 1:
        raise EngineError("invalid-input", "rpo-minutes must be >= 0 and rto-minutes must be >= 1")
    if not skill_query and not category_query:
        raise EngineError("invalid-input", "Provide --skill <id-or-name> or --category <name-id-or-number>")

    generated = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if skill_query:
        skill = catalog.find_skill(skill_query)
        return {
            "planType": "skill",
            "title": f"Production Deployment Plan — {skill['name']}",
            "generatedAt": generated,
            "provider": provider,
            "environment": environment,
            "rpoMinutes": rpo_minutes,
            "rtoMinutes": rto_minutes,
            "skill": {k: skill[k] for k in ("id", "number", "name", "description", "pattern", "category", "categoryId")},
            "patternSummary": catalog.data.get("patternLegend", {}).get(skill["pattern"], ""),
            "phases": [{"name": n, "steps": s, "exitCriteria": e} for n, s, e in PATTERN_PHASES[skill["pattern"]]],
            "approvalGates": APPROVAL_GATES,
            "evidenceChecklist": EVIDENCE_CHECKLIST,
            "rollback": ROLLBACK_STEPS,
            "escalation": ESCALATION_PATH,
            "coreProductionAcceptance": catalog.data.get("coreProductionAcceptance", []),
        }

    cat = catalog.find_category(category_query)
    sections = []
    for s in cat["skills"]:
        sections.append({
            "number": s["number"], "name": s["name"], "id": s["id"], "pattern": s["pattern"],
            "summary": catalog.data.get("patternLegend", {}).get(s["pattern"], ""),
            "acceptanceHint": _acceptance_hint(s["pattern"]),
        })
    return {
        "planType": "category",
        "title": f"Category Adoption Plan — {cat['name']}",
        "generatedAt": generated,
        "provider": provider,
        "environment": environment,
        "rpoMinutes": rpo_minutes,
        "rtoMinutes": rto_minutes,
        "skill": None,
        "category": {"number": cat["number"], "id": cat["id"], "slug": cat["slug"], "name": cat["name"],
                     "skillCount": len(cat["skills"])},
        "skills": sections,
        "approvalGates": APPROVAL_GATES,
        "evidenceChecklist": EVIDENCE_CHECKLIST,
        "rollback": ROLLBACK_STEPS,
        "escalation": ESCALATION_PATH,
        "coreProductionAcceptance": catalog.data.get("coreProductionAcceptance", []),
    }


def _acceptance_hint(pattern):
    return {
        "provision-from-code": "Applies cleanly from reviewed code; endpoint verified; restore and rollback evidence attached.",
        "runbook-definition": "Runbook rehearsed twice to a verified completion condition; timings within RTO.",
        "decision-matrix": "Weighted scores recorded; rejected options documented with revisit triggers.",
        "evaluation-gate": "Pass/warn/fail thresholds applied; remediation and retest date recorded for every fail.",
        "operate-service": "Automated recovery demonstrated; drift detection and quota alerts armed; review logged.",
        "focused-capability": "Acceptance criteria met; `assess` verdict pass (or warn with sign-off); evidence attached.",
    }.get(pattern, "Acceptance criteria met and evidence attached.")


def render_plan_markdown(plan, template):
    """Fill asset-plan-template.md placeholders. Simple, dependency-free substitution."""
    def phases_md():
        out = []
        if plan["planType"] == "skill":
            for ph in plan["phases"]:
                out.append(f"### {ph['name']}\n")
                for step in ph["steps"]:
                    out.append(f"- [ ] {step}")
                out.append(f"\n**Exit criteria:** {ph['exitCriteria']}\n")
        else:
            for s in plan["skills"]:
                out.append(f"### {s['number']:02d}. {s['name']}  \n`{s['id']}` · pattern: `{s['pattern']}`\n")
                out.append(f"- {s['summary']}")
                out.append(f"- **Done when:** {s['acceptanceHint']}\n")
        return "\n".join(out)

    def bullets(items, check=False):
        return "\n".join((f"- [ ] {i}" if check else f"- {i}") for i in items)

    if plan["planType"] == "skill":
        scope = (f"**Skill:** {plan['skill']['name']} (`{plan['skill']['id']}`)\n"
                 f"**Category {plan['skill']['categoryId'][4:]}:** {plan['skill']['category']}\n"
                 f"**Catalog pattern:** `{plan['skill']['pattern']}` — {plan['patternSummary']}\n\n"
                 f"**Intent (catalog description):** {plan['skill']['description']}")
    else:
        scope = (f"**Category {plan['category']['number']}:** {plan['category']['name']} "
                 f"({plan['category']['skillCount']} skills)\n"
                 f"**Category id:** `{plan['category']['id']}` · adoption scope: all skills in this category")

    replacements = {
        "{{PLAN_TITLE}}": plan["title"],
        "{{GENERATED_AT}}": plan["generatedAt"],
        "{{GENERATED_BY}}": f"tools-production-deployment.py (engine {ENGINE_VERSION})",
        "{{PROVIDER}}": plan["provider"],
        "{{ENVIRONMENT}}": plan["environment"],
        "{{RPO_MINUTES}}": str(plan["rpoMinutes"]),
        "{{RTO_MINUTES}}": str(plan["rtoMinutes"]),
        "{{SCOPE}}": scope,
        "{{PHASES}}": phases_md(),
        "{{APPROVAL_GATES}}": bullets(plan["approvalGates"]),
        "{{EVIDENCE_CHECKLIST}}": bullets(plan["evidenceChecklist"], check=True),
        "{{ROLLBACK}}": bullets(plan["rollback"], check=True),
        "{{ESCALATION}}": bullets(plan["escalation"]),
        "{{CORE_ACCEPTANCE}}": bullets(plan["coreProductionAcceptance"]),
    }
    out = template
    for key, value in replacements.items():
        out = out.replace(key, value)
    missing = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", out)))
    if missing:
        raise EngineError("template", f"plan template has unfilled placeholders: {', '.join(missing)}")
    return out
