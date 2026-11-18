#!/usr/bin/env python3
"""Deterministic task planner for the Offline Engineering Workbench.

Maps a free-text task to the 480 registered catalog skills and emits a phased,
offline execution plan with gates, inputs, outputs, limits, approvals, and
rollback. The planner is deterministic: the same task and parameters always
produce the same plan id and selection.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "onto", "when",
    "then", "than", "must", "should", "would", "could", "can", "may", "are", "was",
    "were", "has", "have", "had", "not", "but", "you", "your", "our", "their",
    "its", "it", "is", "be", "been", "being", "will", "shall", "does", "did",
    "doing", "done", "each", "every", "all", "any", "some", "more", "most",
    "other", "such", "only", "also", "just", "very", "well", "make", "made",
    "use", "used", "using", "work", "works", "working", "need", "needs", "needed",
    "want", "wants", "like", "able", "via", "per", "off", "out", "over", "under",
    "about", "after", "before", "between", "during", "through", "against",
    "within", "without", "across", "around", "because", "while", "where", "what",
    "which", "who", "whom", "how", "why", "there", "here", "both", "either",
    "neither", "one", "two", "three", "first", "second", "new", "old", "good",
    "best", "better", "ensure", "make", "allow", "allows", "including", "include",
    "offline", "local", "air", "gapped", "manager", "builder", "operator",
    "specialist", "architect", "tool", "tools", "skill", "skills", "catalog",
}


def _load_core():
    existing = sys.modules.get("wb_core")
    if existing is not None and getattr(existing, "VERSION", None):
        return existing
    path = Path(__file__).resolve().parent / "tools-workbench-core.py"
    spec = importlib.util.spec_from_file_location("wb_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load tools-workbench-core.py at %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wb_core"] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def tokenize(text: str) -> List[str]:
    raw = re.findall(r"[a-z0-9]+", str(text).lower())
    tokens: List[str] = []
    for token in raw:
        if len(token) < 3 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _unique(tokens: Sequence[str]) -> List[str]:
    seen: set = set()
    result: List[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def build_token_frequencies(catalog: Dict[str, Any]) -> Dict[str, int]:
    """Document frequency of name tokens, used to down-weight generic words."""
    frequencies: Dict[str, int] = {}
    for category in catalog["categories"]:
        for skill in category["skills"]:
            for token in set(tokenize(skill.get("name", ""))):
                frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies


def _token_weight(token: str, frequencies: Dict[str, int], total_skills: int) -> float:
    frequency = frequencies.get(token, 0)
    if total_skills and frequency > total_skills * 0.15:
        return 0.15
    if total_skills and frequency > total_skills * 0.05:
        return 0.4
    return 1.0


def score_skill(skill: Dict[str, Any], category: Dict[str, Any],
                task_tokens: Sequence[str], task_text: str,
                frequencies: Dict[str, int], total_skills: int) -> Tuple[float, List[str]]:
    name_tokens = set(tokenize(skill.get("name", "")))
    scope_tokens = set(tokenize(skill.get("scope", "")))
    category_tokens = set(tokenize(category.get("name", "")))
    description_tokens = set(tokenize(skill.get("description", "")))
    matched: List[str] = []
    score = 0.0
    for token in task_tokens:
        weight = _token_weight(token, frequencies, total_skills)
        if token in name_tokens:
            score += 4.0 * weight
            matched.append(token)
        elif token in scope_tokens:
            score += 2.0 * weight
            matched.append(token)
        elif token in category_tokens:
            score += 1.5 * weight
            matched.append(token)
        elif token in description_tokens:
            score += 0.5 * weight
    phrase = task_text.strip().lower()
    if len(phrase) > 8 and phrase in skill.get("name", "").lower():
        score += 8.0
    if matched:
        score += min(len(set(matched)), 6) * 0.25
    return score, sorted(set(matched))


def _phase_skills(catalog: Dict[str, Any], category_numbers: Sequence[int],
                  ranked: List[Tuple[float, Dict[str, Any], Dict[str, Any], List[str]]],
                  limit: int, used: set, fallback_ids: Sequence[str] = ()) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for score, category, skill, matched in ranked:
        if len(selected) >= limit:
            break
        if score <= 0 or category["number"] not in category_numbers or skill["id"] in used:
            continue
        used.add(skill["id"])
        selected.append({
            "id": skill["id"],
            "name": skill["name"],
            "category": category["name"],
            "category_number": category["number"],
            "score": round(score, 2),
            "matched_terms": matched,
            "why": "Matched task terms: %s." % ", ".join(matched),
        })
    if not selected and fallback_ids:
        by_id = {skill["id"]: (category, skill)
                 for category in catalog["categories"] for skill in category["skills"]}
        for fallback_id in fallback_ids:
            if fallback_id not in by_id or fallback_id in used or len(selected) >= limit:
                continue
            category, skill = by_id[fallback_id]
            used.add(fallback_id)
            selected.append({
                "id": skill["id"], "name": skill["name"], "category": category["name"],
                "category_number": category["number"], "score": 0.0, "matched_terms": [],
                "why": "Baseline lifecycle capability for this phase.",
            })
    return selected


def build_plan(task: str, catalog: Dict[str, Any], policy: Dict[str, Any],
               max_skills: int = 12, max_per_category: int = 2,
               root: str = ".") -> Dict[str, Any]:
    if not task or not task.strip():
        raise core.WorkbenchError("task-empty", "A task description is required.",
                                  {}, core.EXIT_USAGE)
    max_skills = max(1, min(int(max_skills), 40))
    max_per_category = max(1, min(int(max_per_category), 5))
    task = task.strip()
    task_tokens = _unique(tokenize(task))
    if not task_tokens:
        raise core.WorkbenchError("task-not-usable",
                                  "The task has no usable terms after removing stopwords.",
                                  {"task": task[:200]}, core.EXIT_USAGE)

    frequencies = build_token_frequencies(catalog)
    total_skills = core.catalog_skill_count(catalog)
    ranked: List[Tuple[float, Dict[str, Any], Dict[str, Any], List[str]]] = []
    for category in catalog["categories"]:
        for skill in category["skills"]:
            score, matched = score_skill(skill, category, task_tokens, task,
                                         frequencies, total_skills)
            ranked.append((score, category, skill, matched))
    ranked.sort(key=lambda item: (-item[0], item[2]["id"]))

    used: set = set()
    selected: List[Dict[str, Any]] = []
    per_category: Dict[int, int] = {}
    for score, category, skill, matched in ranked:
        if len(selected) >= max_skills or score <= 0:
            break
        if per_category.get(category["number"], 0) >= max_per_category:
            continue
        per_category[category["number"]] = per_category.get(category["number"], 0) + 1
        used.add(skill["id"])
        selected.append({
            "id": skill["id"],
            "name": skill["name"],
            "category": category["name"],
            "category_number": category["number"],
            "score": round(score, 2),
            "matched_terms": matched,
            "why": ("Matched task terms: %s." % ", ".join(matched)) if matched
                   else "Related lifecycle capability.",
        })

    phases = catalog.get("lifecycle_phases", {})
    plan_phases: List[Dict[str, Any]] = []

    prepare = _phase_skills(catalog, phases.get("prepare", {}).get("category_numbers", [1, 10]),
                            ranked, 2, used,
                            fallback_ids=("cat-01-skill-03", "cat-10-skill-01"))
    plan_phases.append({
        "id": "prepare",
        "name": phases.get("prepare", {}).get("name", "Prepare offline inputs"),
        "objective": "Resolve every runtime, package, and artifact from a local mirror with "
                     "verified signatures and a committed lockfile.",
        "gate": phases.get("prepare", {}).get("gate", "Clean-state install succeeds with networking disabled."),
        "skills": prepare,
        "commands": [
            "python3 offline-engineering-workbench/tools-workbench.py doctor --format json",
            "python3 offline-engineering-workbench/tools-workbench.py check --root . --only deps --format json",
        ],
    })

    plan_phases.append({
        "id": "implement",
        "name": phases.get("implement", {}).get("name", "Implement and build"),
        "objective": "Execute the selected implementation capabilities against pinned inputs and "
                     "record provenance for every artifact.",
        "gate": phases.get("implement", {}).get("gate",
                                                "Build completes deterministically with pinned inputs."),
        "skills": selected,
        "commands": [
            "python3 offline-engineering-workbench/tools-workbench.py run --dry-run -- <build-command>",
            "python3 offline-engineering-workbench/tools-workbench.py run --timeout 900 -- <build-command>",
        ],
    })

    verify = _phase_skills(catalog, phases.get("verify", {}).get("category_numbers", [25, 26, 29, 30]),
                           ranked, 3, used,
                           fallback_ids=("cat-25-skill-01", "cat-29-skill-01", "cat-30-skill-15"))
    plan_phases.append({
        "id": "verify",
        "name": phases.get("verify", {}).get("name", "Verify and validate"),
        "objective": "Run tests, static analysis, security scans, and quality gates; record every "
                     "result and exception.",
        "gate": phases.get("verify", {}).get("gate",
                                             "All gates pass or carry an approved, documented exception."),
        "skills": verify,
        "commands": [
            "python3 offline-engineering-workbench/tools-workbench.py check --root . --format json --output reports/check.json",
            "python3 offline-engineering-workbench/tools-workbench.py run --timeout 900 -- <test-command>",
        ],
    })

    deliver = _phase_skills(catalog, phases.get("deliver", {}).get("category_numbers", [31, 32]),
                            ranked, 2, used,
                            fallback_ids=("cat-32-skill-01", "cat-32-skill-03"))
    plan_phases.append({
        "id": "deliver",
        "name": phases.get("deliver", {}).get("name", "Package, document, and recover"),
        "objective": "Produce the reproducible package, checksums, handoff guide, and a tested "
                     "rollback path.",
        "gate": phases.get("deliver", {}).get("gate",
                                              "Manifest verifies and the clean-room run reproduces the result."),
        "skills": deliver,
        "commands": [
            "python3 offline-engineering-workbench/tools-workbench.py manifest --root . --output reports/manifest.json",
            "python3 offline-engineering-workbench/tools-workbench.py verify --manifest reports/manifest.json",
            "python3 offline-engineering-workbench/tools-workbench.py report --result reports/check.json --output reports/report.md",
        ],
    })

    # Fallback: if nothing scored, use a minimal lifecycle baseline so the plan is usable.
    if not selected:
        baseline_ids = ("cat-01-skill-01", "cat-10-skill-01", "cat-25-skill-01",
                        "cat-29-skill-01", "cat-32-skill-01")
        by_id = {skill["id"]: (category, skill)
                 for category in catalog["categories"] for skill in category["skills"]}
        for skill_id in baseline_ids:
            if skill_id not in by_id or skill_id in used:
                continue
            category, skill = by_id[skill_id]
            used.add(skill_id)
            selected.append({
                "id": skill_id, "name": skill["name"], "category": category["name"],
                "category_number": category["number"], "score": 0.0,
                "matched_terms": [],
                "why": "Baseline lifecycle capability applied because no direct term matched.",
            })
        plan_phases[1]["skills"] = selected

    all_selected = [item for phase in plan_phases for item in phase["skills"]]
    unique_selected = []
    seen: set = set()
    for item in all_selected:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique_selected.append(item)

    top_score = selected[0]["score"] if selected else 0.0
    confidence = "high" if top_score >= 8 else "medium" if top_score >= 3 else "low"
    plan_id = hashlib.sha256(
        ("\n".join([task, str(max_skills), str(max_per_category), root])).encode("utf-8")
    ).hexdigest()[:16]
    approval_ids = sorted({entry.get("id", "unspecified")
                           for entry in policy.get("approval_required_patterns", [])})
    categories_covered = sorted({item["category_number"] for item in unique_selected})

    return {
        "plan_id": plan_id,
        "tool": core.TOOL_NAME,
        "tool_version": core.VERSION,
        "task": task,
        "root": root,
        "generated_at": core.now_iso(),
        "totals": {
            "registered_skills": core.catalog_skill_count(catalog),
            "registered_categories": len(catalog["categories"]),
            "selected_skills": len(unique_selected),
            "categories_covered": len(categories_covered),
            "max_skills": max_skills,
            "max_per_category": max_per_category,
        },
        "confidence": confidence,
        "matched_terms": _unique([term for _, _, _, matched in ranked for term in matched])[:25],
        "phases": plan_phases,
        "selected_skills": unique_selected,
        "inputs": [
            "Task description (plain text or Markdown; HTML tags are ignored by the caller).",
            "Project root containing source, data, build files, and tests.",
            "Policy file offline-engineering-workbench/asset-check-policy.json.",
            "Catalog asset offline-engineering-workbench/asset-skill-catalog.json.",
        ],
        "outputs": [
            "reports/check.json - structured check results and gate status.",
            "reports/manifest.json - SHA-256 provenance manifest.",
            "reports/report.md - rendered Markdown handoff report.",
            "reports/run-*.jsonl - bounded command audit log (when run is used).",
        ],
        "limits": {
            "network": "disabled; all commands must resolve from local mirrors",
            "run_timeout_seconds": int(policy.get("thresholds", {}).get("run_timeout_seconds", 300)),
            "run_max_output_bytes": int(policy.get("thresholds", {}).get("run_max_output_bytes", 200000)),
            "run_retries": int(policy.get("thresholds", {}).get("run_retries", 0)),
            "max_findings_per_rule": int(policy.get("thresholds", {}).get("max_findings_per_rule", 5)),
            "max_findings_total": int(policy.get("thresholds", {}).get("max_findings_total", 200)),
        },
        "approval_required": approval_ids,
        "approval_note": "Any command matching an approval pattern is refused unless --approve is "
                         "passed; approval is recorded in the audit log.",
        "rollback": [
            "Restore the previous artifact set from the local artifact repository using the prior manifest.",
            "Re-run the cleanroom command against the previous manifest to confirm the restored state.",
            "Keep the failed run log and check report for triage; do not delete them until the incident closes.",
        ],
    }
