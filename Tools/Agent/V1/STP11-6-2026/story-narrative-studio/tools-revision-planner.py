#!/usr/bin/env python3
"""tools-revision-planner.py - turns audit findings into an ordered revision plan.

Loaded by ``tools-story-narrative.py`` (the ``revise`` operation). It reuses
``tools-manuscript-audit.py`` so a manuscript is analysed exactly once with the
same metrics, then converts findings and section-level evidence into a
sequenced plan: tasks carry a location, a causal problem, the smallest
sufficient fix, a risk, and the revision pass that should perform it.

Nothing is rewritten here. The plan is executable by a writer or an agent and
never modifies the manuscript on disk.
"""

from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import importlib.util as _ilu


def _load(filename, alias):
    if alias in sys.modules:  # share one instance per module across all tools- files
        return sys.modules[alias]
    spec = _ilu.spec_from_file_location(alias, os.path.join(_HERE, filename))
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[alias] = module
    return module


core = _load("tools-narrative-core.py", "sns_core")
audit = _load("tools-manuscript-audit.py", "sns_audit")

FIX_FOR = {
    "prose_clarity": "Rewrite the offending sentences with concrete verbs and a varied rhythm; keep the meaning and cut the scaffolding.",
    "dialogue_economy": "Replace tag-and-explain dialogue with tactics and action beats; let one character misunderstand on purpose.",
    "character_agency": "Give the POV character a decision in the scene's first third and make them pay for it before the end.",
    "pacing": "Compress the low-pressure stretch to one scene, and move the removed material into the decision scene as resistance.",
    "causality": "Insert the causal hinge: name who chose the action that makes the next event inevitable.",
    "originality": "Replace the stock phrase or beat with the premise-specific detail that only this story could use.",
    "audience_fit": "Adjust sentence length, vocabulary, and content reference to the declared audience envelope, or restate the envelope in the brief.",
    "format_readiness": "Re-cut to the production unit (pages, panels, scenes, nodes) and re-check the budget numbers.",
    "representation_ethics": "Add a source, a specificity, or a change of framing; remove method detail, never describe procedure.",
    "structure": "Re-place the turn: the beat must close a door, not just add information.",
    "theme_integration": "Convert the thematic statement into a test that the character can fail in action.",
    "coherence": "Decide which source of truth wins (bible or manuscript), then update the other in one pass.",
}

RISK_FOR = {
    "prose_clarity": "Line-level edits can flatten voice; keep one deliberate stylistic eccentricity per page.",
    "dialogue_economy": "Cutting exposition can orphan information the reader still needs; relocate it into action.",
    "character_agency": "Adding decisions can over-explain motive; let the decision stand without commentary.",
    "pacing": "Compression can break continuity of place; check the timeline after cutting.",
    "causality": "A new hinge may contradict an earlier setup; check the bible.",
    "originality": "Novel detail can read as decorative; tie it to the conflict.",
    "audience_fit": "Simplifying prose can patronise; keep the conceptual complexity.",
    "format_readiness": "Re-cutting may be structural, not cosmetic; plan for a second outline pass.",
    "representation_ethics": "Any substantive change to a depicted group should be reviewed by someone from that group.",
    "structure": "Moving a turn can collapse a subplot; re-check the B-story's payoff.",
    "theme_integration": "Explicit thematic tests can turn into message fiction; keep the opposition strong.",
    "coherence": "Choosing the manuscript over the bible may invalidate earlier scenes; audit the whole work afterwards.",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def section_evidence(text):
    """Per-section production evidence used to place revision tasks."""
    sections = audit.split_sections(text)
    rows = []
    for section in sections:
        body = section["text"]
        dialogue = audit.dialogue_stats(body)
        stats = audit.sentence_stats(audit.sentences(body))
        decisions = len(re.findall(
            r"\b(decided|chose|refused|agreed|promised|lied|admitted|signed|left|paid)\b", body, flags=re.I))
        passive, _ = audit.passive_ratio(body)
        pressure = round(audit.core.clamp(
            dialogue["dialogue_ratio"] * 1.3 + (1 - min(stats["avg"], 30) / 30) * 0.7, 0, 1), 3)
        first = audit.sentences(body)
        rows.append({
            "section": section["index"],
            "words": section["words"],
            "dialogue_ratio": dialogue["dialogue_ratio"],
            "avg_sentence": stats["avg"],
            "passive": passive,
            "decisions": decisions,
            "pressure": pressure,
            "opening_line": (first[0][:120] if first else ""),
            "flags": [
                flag for flag, condition in (
                    ("no_decision", decisions == 0),
                    ("low_pressure", pressure < 0.25),
                    ("passive_dominant", passive > 0.4),
                    ("dialogue_starved", dialogue["dialogue_ratio"] < 0.05),
                    ("overlong", section["words"] > 6000),
                ) if condition
            ],
        })
    return rows


def build_plan(text, spec, passes=None, max_tasks=40, timeout=None):
    deadline = core.Deadline(timeout or 45)
    report, _ = audit.audit_manuscript(text, spec, timeout=timeout)
    deadline.check("audit")
    sections = section_evidence(text)
    deadline.check("sections")

    prompt_lib = core.load_asset("prompts")
    available_passes = {p["id"]: p for p in prompt_lib["revision_passes"]}
    requested = passes or list(available_passes.keys())
    unknown = [p for p in requested if p not in available_passes]
    if unknown:
        raise core.StudioError(
            "Unknown revision pass(es): %s. Available: %s" % (", ".join(unknown), ", ".join(available_passes)),
            field="pass",
        )

    tasks = []
    task_id = 1
    for issue in report["issues"]:
        dim = issue["dimension"]
        tasks.append({
            "id": "t-%03d" % task_id,
            "severity": issue["severity"],
            "dimension": dim,
            "location": "manuscript-wide",
            "problem": issue["message"],
            "evidence": issue["evidence"],
            "fix": FIX_FOR.get(dim, "Investigate at the scene level and rewrite the smallest sufficient unit."),
            "risk": RISK_FOR.get(dim, "Structural edits can break continuity; re-audit after the pass."),
            "pass": _pass_for(dim),
            "requires_approval": issue["severity"] in ("critical", "high"),
        })
        task_id += 1

    for dim, data in report["dimensions"].items():
        score = data.get("score")
        if score is None or score >= 3.0:
            continue
        tasks.append({
            "id": "t-%03d" % task_id,
            "severity": "high" if score < 2.0 else "medium",
            "dimension": dim,
            "location": "manuscript-wide",
            "problem": "Dimension %s scored %s / 5." % (dim, score),
            "evidence": "; ".join(data.get("evidence", [])[:2]),
            "fix": FIX_FOR.get(dim, "Diagnose with the relevant pass, then rewrite the smallest sufficient unit."),
            "risk": RISK_FOR.get(dim, "Re-audit after the pass to confirm the score moved for the right reason."),
            "pass": _pass_for(dim),
            "requires_approval": score < 2.0,
        })
        task_id += 1

    for row in sections:
        for flag in row["flags"]:
            severity = {"no_decision": "high", "overlong": "medium", "passive_dominant": "medium"}.get(flag, "low")
            tasks.append({
                "id": "t-%03d" % task_id,
                "severity": severity,
                "dimension": {"no_decision": "character_agency", "overlong": "pacing",
                              "passive_dominant": "prose_clarity", "low_pressure": "pacing",
                              "dialogue_starved": "dialogue_economy"}.get(flag, "pacing"),
                "location": "section %s" % row["section"],
                "problem": "Section %s flagged %s." % (row["section"], flag.replace("_", " ")),
                "evidence": "opening: %s | words %s | pressure %s | decisions %s" % (
                    row["opening_line"], row["words"], row["pressure"], row["decisions"]),
                "fix": {
                    "no_decision": "Give this section one decision with a cost, made on the page.",
                    "overlong": "Split or compress; a section above 6,000 words needs its own turn.",
                    "passive_dominant": "Reassign agency: name who acts in each passive clause.",
                    "low_pressure": "Raise pressure or merge with the neighbouring section.",
                    "dialogue_starved": "Let the characters negotiate instead of narrating the negotiation.",
                }.get(flag, "Review with the relevant pass."),
                "risk": "Local edits may shift tension balance; re-check the tension curve after the pass.",
                "pass": {"no_decision": "character", "overlong": "structural", "passive_dominant": "prose",
                         "low_pressure": "pacing", "dialogue_starved": "dialogue"}.get(flag, "scene"),
                "requires_approval": severity == "high",
            })
            task_id += 1

    tasks = [t for t in tasks if t["pass"] in requested]
    tasks.sort(key=lambda t: (SEVERITY_ORDER.get(t["severity"], 4), t["pass"], t["location"]))
    tasks = tasks[:max_tasks]
    for index, task in enumerate(tasks, 1):
        task["order"] = index

    sequence = []
    for pass_id in requested:
        chunk = [t for t in tasks if t["pass"] == pass_id]
        if not chunk:
            continue
        meta = available_passes[pass_id]
        sequence.append({
            "pass": pass_id,
            "label": meta["label"],
            "level": meta["level"],
            "instruction": meta["instruction"],
            "task_ids": [t["id"] for t in chunk],
            "estimated_effort": "structural" if meta["level"] in ("act and sequence", "whole work") else "focused",
        })

    plan = {
        "title": (spec or {}).get("title") or "Untitled manuscript",
        "source_words": report["metrics"]["words"],
        "source_verdict": report["verdict"]["verdict"],
        "tasks": tasks,
        "task_counts": {sev: sum(1 for t in tasks if t["severity"] == sev)
                        for sev in ("critical", "high", "medium", "low")},
        "sequence": sequence,
        "sections": sections,
        "approval_required": [t["id"] for t in tasks if t["requires_approval"]],
        "generated_at": core.now_iso(),
    }
    return plan, audit_render(plan, spec, report), report


def _pass_for(dimension):
    return {
        "prose_clarity": "prose",
        "dialogue_economy": "dialogue",
        "character_agency": "character",
        "character_depth": "character",
        "pacing": "pacing",
        "causality": "structural",
        "structure": "structural",
        "originality": "theme",
        "theme_integration": "theme",
        "audience_fit": "readiness",
        "format_readiness": "readiness",
        "representation_ethics": "ethics",
        "coherence": "continuity",
        "continuity": "continuity",
        "emotional_impact": "scene",
    }.get(dimension, "scene")


def audit_render(plan, spec, report):
    out = ["# Revision Plan - %s" % plan["title"], ""]
    out.append("*Source: %s words | audit verdict: **%s** | generated %s*" % (
        format(plan["source_words"], ","), plan["source_verdict"], plan["generated_at"]))
    out.append("")
    out.append("| Severity | Tasks |")
    out.append("| --- | --- |")
    for sev in ("critical", "high", "medium", "low"):
        out.append("| %s | %s |" % (sev, plan["task_counts"][sev]))
    out.append("")
    if plan["approval_required"]:
        out.append("**Approval gate:** %s task(s) change structure, ethics framing, or format. "
                   "Confirm each before an agent edits the manuscript: %s." % (
                       len(plan["approval_required"]), ", ".join(plan["approval_required"])))
        out.append("")
    out.append("## Pass sequence")
    out.append("")
    for step in plan["sequence"]:
        out.append("### %s (%s level)" % (step["label"], step["level"]))
        out.append("")
        out.append("%s" % step["instruction"])
        out.append("")
        out.append("Tasks: %s | effort: %s" % (", ".join(step["task_ids"]), step["estimated_effort"]))
        out.append("")
    out.append("## Tasks")
    out.append("")
    for task in plan["tasks"]:
        out.append("<details open><summary><strong>%s - [%s] %s</strong> (%s, %s pass)</summary>" % (
            task["id"], task["severity"], task["problem"], task["location"], task["pass"]))
        out.append("")
        out.append("- **Evidence:** %s" % task["evidence"])
        out.append("- **Fix:** %s" % task["fix"])
        out.append("- **Risk:** %s" % task["risk"])
        out.append("- **Approval required:** %s" % ("yes" if task["requires_approval"] else "no"))
        out.append("")
        out.append("</details>")
        out.append("")
    out.append("## Section evidence")
    out.append("")
    out.append("| Section | Words | Dialogue | Pressure | Decisions | Passive | Flags |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in plan["sections"]:
        out.append("| %s | %s | %.0f%% | %s | %s | %.0f%% | %s |" % (
            row["section"], row["words"], row["dialogue_ratio"] * 100, row["pressure"],
            row["decisions"], row["passive"] * 100, ", ".join(row["flags"]) or "-"))
    out.append("")
    out.append("## Order of work")
    out.append("")
    out.append("1. Resolve critical tasks and ethics flags; do not draft new material until they are closed.")
    out.append("2. Run the structural pass, then the character pass; structural moves invalidate line edits.")
    out.append("3. Run scene and dialogue passes, then prose, then pacing.")
    out.append("4. Run the continuity pass against the bible and update it in the same sitting.")
    out.append("5. Re-run `audit` and `continuity`; compare verdicts, not vibes.")
    out.append("")
    out.append("## Limits")
    out.append("")
    out.append("Task placement uses section-level proxies (dialogue share, sentence length, decision verbs, passive rate). "
               "A human or model read is still required to confirm that a flagged section is actually weak.")
    out.append("")
    return "\n".join(out)
