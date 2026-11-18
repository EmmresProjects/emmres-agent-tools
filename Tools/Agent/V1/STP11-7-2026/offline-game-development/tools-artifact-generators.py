#!/usr/bin/env python3
"""tools-artifact-generators.py -- layer 3 of the offline-game-development Skill Tool.

Role in the Skill Tool
    Turns capability records into the artifacts a team files: the spec page (rendered from
    offline-game-development/asset-spec-template.md plus the archetype's section contract),
    the ordered sprint plan, the checklist that closes the gates, the offline prompt pack for
    a local model, and the record sets that 'export' writes. Every renderer is deterministic:
    the same selection, target, and config produce the same bytes.

    It reads data and returns data plus text. It never writes a file and never runs a command.
"""

from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ALIASES = {"tools-runtime-kit.py": "ogd_tools-runtime-kit", "tools-catalog-engine.py": "ogd_tools-catalog-engine"}


def _load(filename: str):
    key = _ALIASES[filename]
    import sys

    if key in sys.modules:
        return sys.modules[key]
    import importlib.util

    spec = importlib.util.spec_from_file_location(key, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


kit = _load("tools-runtime-kit.py")
engine = _load("tools-catalog-engine.py")

RUN_PREFIX = "python3 offline-game-development/tools-offline-game-development.py"

# Rows the tool can legitimately pre-fill, because they come from the catalog, the target, or
# the shipped policies. Anything a specific team decides stays 'fill'.
SEEDS: dict = {
    "inputs": [{"path_or_source": f"source content and configuration for {{subject}}", "format": "fill",
                "pinned_version": "fill", "license_ref": "rights/{{id}}-inputs.json",
                "size_limit": "fill", "owner": "{{owner}}"},
               {"path_or_source": "offline-bundle/packages/", "format": "archive",
                "pinned_version": "recorded in the bundle manifest",
                "license_ref": "offline-bundle/LICENSES/", "size_limit": "bundle budget",
                "owner": "build-engineer"}],
    "outputs": [{"artifact": "the built {{archetype_label_lower}} output for {{name}}", "format": "fill",
                 "destination": "builds/{{platform}}/{{id}}/",
                 "consumer": "the {{platform}} target build", "retention": "keep the last three builds"}],
    "offline_dependencies": [{"dependency": "engine editor and export templates",
                              "version": "pinned to one exact release",
                              "cached_path": "offline-bundle/engine/", "integrity": "checksum recorded",
                              "network_free": "yes"},
                             {"dependency": "local package cache and model weights",
                              "version": "pinned", "cached_path": "offline-bundle/models/",
                              "integrity": "checksum plus license file beside the model",
                              "network_free": "yes"}],
    "rights_metadata": [{"asset": "each consumed and produced asset", "license": "fill",
                         "provenance_source": "recorded in the manifest",
                         "commercial_use": "fill", "redistribution": "fill", "attribution": "fill",
                         "record_path": "rights/{{id}}-assets.json"}],
    "performance_budgets": [],          # seeded from the device profile, see _budget_rows()
    "quality_budgets": [],
    "health_budgets": [],
    "diagnostics": [{"signal": "start, finish, and failure of each unit of work",
                     "channel": "structured log line", "level": "info / error",
                     "fields": "capability, target, duration_ms, failure_reason",
                     "retention": "until the next clean build"}],
    "tests": [{"test": "the reference case for {{name}} passes", "kind": "automated",
               "command_hint": "project harness: run the frozen reference case in a headless editor",
               "pass_condition": "no failing assertions and no error-level log lines",
               "cadence": "every commit touching this capability"},
              {"test": "the capability degrades correctly with the network blocked", "kind": "automated",
               "command_hint": "run the same case inside a network namespace with no route",
               "pass_condition": "identical result to the connected run, or the documented offline path",
               "cadence": "weekly"}],
    "target_acceptance": [{"criterion": "{{name}} works on a real build of the declared target",
                           "target": "{{device_profile}}", "method": "manual playthrough plus log capture",
                           "threshold": "the acceptance clause in the capability description",
                           "evidence": "test-report on the milestone record"}],
    "prerequisites": [{"item": "the decision this capability depends on is recorded",
                      "why_needed": "an unrecorded decision re-opens the scope later",
                      "owner": "{{owner}}", "verified_by": "reviewed in the sprint planning session"}],
    "owners": [{"role": "{{owner}}", "responsibility": "keeps this capability's spec and evidence current",
                "decision_right": "accepts or rejects the gate result",
                "changed_by": "technical-director"}],
    "constraints": [{"constraint": "must be doable with the offline bundle alone", "kind": "offline",
                     "source": "catalog offline statement", "impact": "cache every dependency in advance"},
                    {"constraint": "{{engine_label}} conventions are hints, not guarantees",
                     "kind": "engine", "source": "{{engine_label}} documentation held in the bundle",
                     "impact": "verify on a target build before closing the gate"}],
    "ordered_steps": [{"step": 1, "action": "confirm the prerequisites and pin the inputs",
                       "output": "a filled inputs section", "owner": "{{owner}}"},
                      {"step": 2, "action": "run the work in the project, offline",
                       "output": "the artifact or decision this capability produces",
                       "owner": "{{owner}}"},
                      {"step": 3, "action": "record the evidence and close the gates",
                       "output": "an evidence file that passes 'evidence check'", "owner": "{{owner}}"}],
    "license_gates": [{"asset": "every input, model, and produced asset", "license": "fill",
                       "allowed_use": "the project's declared commercial or noncommercial use",
                       "blocker": "unknown, non-commercial, or non-redistributable license"}],
    "fallback_paths": [{"trigger": "a dependency is missing from the offline bundle",
                        "fallback": "switch to the cached alternative or mark the capability blocked",
                        "cost": "one planning cycle", "decided_by": "producer"}],
    "evidence_required": [{"evidence": "the recorded gate results named by this spec",
                           "format": "one JSON file per capability, schema-checked by this tool",
                           "filed_by": "{{owner}}", "retention": "for the life of the release"}],
    "completion": [{"condition": "every blocking section is filled and every gate has a recorded pass",
                    "verified_by": "{{owner}}", "date": "fill"}],
    "check_design": [{"check": "the check measures exactly one acceptance clause", "kind": "design",
                      "tool": "project harness", "frequency": "once per capability, reviewed on change"}],
    "automated_checks": [{"check": "the reference case for {{name}}",
                          "command_hint": "project harness: run the frozen reference case headless",
                          "pass_threshold": "0 failing assertions",
                          "fixture": "builds/{{platform}}/reference-slice"}],
    "manual_checks": [{"check": "the player-visible behaviour matches the acceptance clause",
                       "protocol": "scripted playthrough on the min-spec device",
                       "observer": "qa-lead", "target": "{{device_profile}}"}],
    "measurements": [],
    "failure_evidence": [{"artifact": "the failing log, capture, or repro save",
                          "path": "evidence/failures/{{id}}/", "format": "text or zip, redacted",
                          "redaction": "secrets removed by this tool before the file is committed"}],
    "remediation": [{"finding": "fill from the failure evidence", "fix": "fill", "owner": "{{owner}}",
                     "severity": "block"}],
    "regression_retest": [{"retest": "the same case that failed, on the same build id plus the fix",
                           "condition": "passes twice, once on a clean checkout",
                           "recorded_by": "qa-lead", "date": "fill"}],
    "runbook": [{"step": 1, "command_hint": "start the local service from the offline bundle",
                 "expected": "the health signal reports ready within the budget", "operator": "{{owner}}"}],
    "monitoring": [{"signal": "availability, latency, and error rate of the local run",
                    "threshold": "fill from the profile budgets", "alert_route": "team channel plus log",
                    "retention": "30 days on the local collector"}],
    "incident_response": [{"scenario": "the capability fails on a target but not in the editor",
                           "first_action": "capture the log and the build id, then reproduce offline",
                           "escalate_to": "{{owner}}", "evidence": "incident note filed with the build"}],
    "maintenance": [{"task": "re-pin versions after any engine or package change", "cadence": "monthly",
                     "owner": "{{owner}}", "rollback": "restore the previous pinned set and rebuild"}],
    "pinned_inputs": [{"input": "source content and configuration for {{subject}}", "version": "fill",
                       "cached_path": "offline-bundle/inputs/",
                       "integrity": "checksum recorded with the input list"},
                      {"input": "generated-asset model weights and settings", "version": "fill",
                       "cached_path": "offline-bundle/models/",
                       "integrity": "checksum plus license file beside the model"}],
    "license_integrity": [{"check": "rights gate R1-R4 on every input", "applies_to": "{{subject}} inputs",
                           "rule": "block if any license is unknown, non-commercial, or non-redistributable",
                           "on_fail": "stop before the build starts and list the offending assets"},
                          {"check": "integrity gate on cached inputs", "applies_to": "offline bundle",
                           "rule": "checksum must match the bundle manifest",
                           "on_fail": "refuse the build; re-cache the input"}],
    "deterministic_settings": [{"setting": "seed, compression, and quantization settings", "value": "fill",
                                "source": "versioned config file",
                                "variation_risk": "thread count and locale can change output"},
                               {"setting": "engine and tool versions", "value": "fill",
                                "source": "bundle manifest",
                                "variation_risk": "patch-level engine changes alter cooked data"}],
    "platform_metadata": [{"field": "version string and build id", "value": "fill",
                           "required_by": "{{platform}} store metadata"},
                          {"field": "rights and attribution list",
                           "value": "generated from rights/{{id}}-assets.json",
                           "required_by": "credits screen and store listing"}],
    "rollback_cleanup": [{"trigger": "build produced a broken artifact",
                          "action": "restore the previous artifact set and mark this build failed",
                          "residue_check": "no temp files left in the output directory"},
                         {"trigger": "an interrupted write",
                          "action": "this Skill Tool writes with a temp file then a rename; interrupted "
                                    "runs leave only .tmp-* files to prune",
                          "residue_check": f"{RUN_PREFIX} cleanup --directory ."}],
    "post_build_validation": [{"validation": "import the artifact into a clean project",
                               "method": "{{engine_check_command}}",
                               "pass_condition": "opens with no import errors", "target": "{{platform}}"},
                              {"validation": "launch and play the core loop",
                               "method": "run the built artifact on the min-spec device",
                               "pass_condition": "first run completes the loop and saves",
                               "target": "{{device_profile}}"}],
    "contract": [{"item": "the public surface of {{name}}", "version": "fill",
                  "guaranteed": "stable within a major version", "consumers": "fill"},
                 {"item": "the mock used when the platform service is absent", "version": "fill",
                  "guaranteed": "same error paths as the real service", "consumers": "local tests"}],
    "version_policy": [{"artifact": "{{name}} package or module", "scheme": "semver",
                        "support_window": "the two most recent major versions",
                        "break_policy": "a deprecation note plus one release of overlap"}],
    "packaging": [{"contents": "code, schema, mock, and docs for {{name}}",
                   "destination": "offline-bundle/packages/{{id}}/",
                   "checksum": "recorded in the bundle manifest", "size_budget": "fill"}],
    "adoption": [{"target": "the first project that consumes it", "integration_step": "fill",
                  "verification": "the consumer's reference case still passes", "owner": "{{owner}}"}],
    "support": [{"question_type": "usage and integration", "first_responder": "{{owner}}",
                 "docs_path": f"{engine.kit.SKILL_TOOL_NAME}/knowledge-readme.md",
                 "escalation": "weekly triage"}],
}

EVIDENCE_BY_SECTION = {
    "inputs": "build-artifact", "outputs": "build-artifact", "offline_dependencies": "checksum",
    "rights_metadata": "rights-record", "performance_budgets": "measurement", "diagnostics": "run-log",
    "tests": "test-report", "target_acceptance": "test-report", "prerequisites": "decision-record",
    "owners": "decision-record", "constraints": "review-signoff", "ordered_steps": "milestone-note",
    "license_gates": "rights-record", "quality_budgets": "measurement", "fallback_paths": "decision-record",
    "evidence_required": "review-signoff", "completion": "review-signoff", "check_design": "test-report",
    "automated_checks": "test-report", "manual_checks": "test-report", "measurements": "measurement",
    "failure_evidence": "failure-evidence", "remediation": "regression-retest",
    "regression_retest": "regression-retest", "runbook": "run-log", "monitoring": "monitoring-snapshot",
    "incident_response": "incident-note", "maintenance": "drill-report", "health_budgets": "measurement",
    "pinned_inputs": "checksum", "license_integrity": "rights-record",
    "deterministic_settings": "build-log", "platform_metadata": "post-build-report",
    "rollback_cleanup": "build-log", "post_build_validation": "post-build-report",
    "contract": "version-notes", "version_policy": "version-notes", "packaging": "package-manifest",
    "adoption": "adoption-record", "support": "support-handoff",
}

_FILL_TOKEN = "fill"
_APPROVAL_TOKEN_BY_ACTION = {"publish": "PUBLISH", "signing": "SIGN", "monetization-change": "MONETIZE",
                             "credential-access": "CREDENTIALS", "device-flash": "FLASH",
                             "rights-waiver": "WAIVE-RIGHTS"}


class RenderContext:
    """What the generators are allowed to know: config, target, engine, and the loaded assets."""

    def __init__(self, catalog, archetypes, policy, budgets, cfg, engine_id="godot",
                 template_text="", spec_path=f"{engine.kit.SKILL_TOOL_NAME}/asset-spec-template.md"):
        self.catalog = catalog
        self.archetypes = archetypes or {}
        self.archetypes_by_name = self.archetypes.get("archetypes", self.archetypes) or {}
        self.policy = policy or {}
        self.budgets = budgets or {}
        self.cfg = cfg or {}
        self.engine = engine_id
        self.template_text = template_text
        self.spec_ref_path = spec_path
        target = (cfg or {}).get("target", {})
        self.platform = target.get("platform", "desktop")
        self.device_profile = target.get("device_profile") or self.budgets.get("default_profile", "desktop-mid")
        self.region = target.get("region", "worldwide")

    # -- convenience ------------------------------------------------------
    def archetype(self, name: str) -> dict:
        return self.archetypes_by_name.get(name) or {}

    def engine_info(self) -> dict:
        return engine.ENGINES.get(self.engine, engine.ENGINES["engine-neutral"])

    def substitutions(self, record) -> dict:
        arch = self.archetype(record["archetype"])
        return {"{{subject}}": record["subject"], "{{name}}": record["name"], "{{id}}": record["id"],
                "{{owner}}": record["role"], "{{platform}}": self.platform,
                "{{device_profile}}": self.device_profile, "{{region}}": self.region,
                "{{engine_label}}": self.engine_info()["label"],
                "{{engine_check_command}}": self.engine_info()["check"],
                "{{archetype_label_lower}}": (arch.get("label") or record["archetype"]).lower()}

    def fill(self, row: dict, record) -> dict:
        subs = self.substitutions(record)
        out = {}
        for key, value in row.items():
            text = str(value)
            for needle, replacement in subs.items():
                text = text.replace(needle, str(replacement))
            out[key] = text
        return out


def _budget_rows(ctx: RenderContext, fields: list, limit: int = 4) -> list:
    """Seed the budget section from the shipped device profile, so the ceilings are real numbers."""
    profile = (ctx.budgets.get("profiles") or {}).get(ctx.device_profile) or {}
    ceilings = profile.get("metrics") or {}
    metrics = ctx.budgets.get("metrics") or {}
    rows = []
    for metric, spec in metrics.items():
        if len(rows) >= limit:
            break
        ceiling = ceilings.get(metric) or {}
        direction = (spec or {}).get("direction", "max")
        value = ceiling.get(direction) if isinstance(ceiling, dict) else ceiling
        if value is None:
            continue
        unit = (spec or {}).get("unit", "")
        budget = f"{'<=' if direction == 'max' else '>='} {value}{(' ' + unit) if unit else ''}"
        row = {"metric": metric, "budget": budget, "measured": _FILL_TOKEN,
               "target_profile": ctx.device_profile, "how_measured": "profiler capture on the target",
               "severity": ceiling.get("severity", "warn") if isinstance(ceiling, dict) else "warn",
               "unit": unit, "profile": ctx.device_profile, "delta_vs_previous": _FILL_TOKEN}
        rows.append({key: row[key] for key in fields if key in row})
    return rows


def build_spec(record, ctx: RenderContext) -> dict:
    """A spec record: the archetype's section contract filled as far as the catalog allows."""
    arch = ctx.archetype(record["archetype"])
    sections = []
    for section in arch.get("sections", []):
        fields = list(section.get("fields") or [])
        seeds = SEEDS.get(section["id"])
        if section["id"] in ("performance_budgets", "quality_budgets", "health_budgets", "measurements"):
            rows = _budget_rows(ctx, fields) or [{"metric": _FILL_TOKEN, "budget": _FILL_TOKEN,
                                                 "measured": _FILL_TOKEN, "target_profile": ctx.device_profile,
                                                 "how_measured": _FILL_TOKEN, "severity": "block",
                                                 "unit": _FILL_TOKEN, "profile": ctx.device_profile,
                                                 "delta_vs_previous": _FILL_TOKEN}]
            rows = [{key: row.get(key, _FILL_TOKEN) for key in fields} for row in rows]
        elif seeds:
            rows = [ctx.fill({key: seed.get(key, _FILL_TOKEN) for key in fields}, record) for seed in seeds]
        else:
            rows = [{key: _FILL_TOKEN for key in fields}]
        gaps = sorted({key for row in rows for key, value in row.items() if _is_fill(value)})
        sections.append({"id": section["id"], "title": section["title"], "purpose": section.get("purpose", ""),
                         "blocking": bool(section.get("blocking")), "fields": fields, "rows": rows,
                         "gaps": gaps, "evidence_kind": EVIDENCE_BY_SECTION.get(section["id"], "note")})
    gates = [{"gate": gate["id"], "rule": gate["rule"], "severity": gate.get("severity", "block"),
              "requires": "needs a recorded result, not an opinion"} for gate in arch.get("gates", [])]
    approvals = _approvals_for(record, ctx)
    conventions = ctx.engine_info()
    assumptions = [
        f"Engine conventions are hints for {conventions['label']}: {conventions['conventions']}",
        conventions["settings"],
        f"Platform target {ctx.platform} (declared platform target); device profile {ctx.device_profile}",
        "No engine, compiler, or device command is executed by this tool; generated commands are for a "
        "human or CI runner",
        "Rows still marked 'fill' are decisions this team owns; the spec stays 'draft' until they are gone",
        f"Local check while offline: `{conventions['check']}`",
    ]
    if ctx.cfg.get("offline", {}).get("forbid_remote_urls_in_specs") and ctx.cfg.get("provider", {}) \
            .get("endpoint"):
        assumptions.append("A provider endpoint is configured; specs must not embed it - prompts reference "
                           "the local model id only")
    capability_slug = record["slug"]
    return {
        "kind": "spec", "capability_id": record["id"], "capability_name": record["name"],
        "category": record["category"], "category_title": record["category_title"],
        "layer": record["layer"], "archetype": record["archetype"],
        "archetype_label": arch.get("label", record["archetype"].capitalize()),
        "intent": arch.get("intent", ""), "subject": record["subject"],
        "description": record["description"], "owner": record["role"],
        "generated_for": {"engine": ctx.engine, "engine_label": conventions["label"],
                          "platform": ctx.platform, "device_profile": ctx.device_profile,
                          "region": ctx.region,
                          "skill_root": engine.kit.SKILL_TOOL_NAME,
                          "dataset_fingerprint": ctx.catalog.meta.get("fingerprint")},
        "sections": sections, "gates": gates,
        "evidence_to_file": list(arch.get("evidence_kinds") or []) or sorted(
            {EVIDENCE_BY_SECTION.get(s["id"], "note") for s in sections}),
        "assumptions": assumptions, "approvals": approvals, "risk_class": record["risk_class"],
        "risk_reasons": record["risk_reasons"], "tags": record["tags"],
        "engines_in_scope": record["engines"],
        "source_ref": f"{engine.kit.SKILL_TOOL_NAME}/asset-catalog.json#{record['id']}",
        "slug": capability_slug,
        "status": "draft" if any(s["gaps"] for s in sections) else "ready",
        "usage": [
            "Fill every row marked **fill** before the capability enters the sprint.",
            f"Run `{RUN_PREFIX} checklist {record['id']} --format md` to re-derive this page after "
            "editing the selection or the target.",
            f"Close the gates in order; a blocked gate is escalated to `{record['role']}`, not skipped.",
            "Attach the listed evidence to the milestone record, then mark the capability complete.",
        ],
    }


def _is_fill(value) -> bool:
    return value in (_FILL_TOKEN, "", None) or str(value).strip() == _FILL_TOKEN


def _approvals_for(record, ctx: RenderContext) -> list:
    """Which configured approval actions this capability touches, with the exact token needed."""
    require = set(ctx.cfg.get("approvals", {}).get("require_for") or [])
    hay = " ".join([record["name"], record["description"]] + list(record["risk_reasons"])).lower()
    out = []
    for action, token in _APPROVAL_TOKEN_BY_ACTION.items():
        if action not in require:
            continue
        if action in ("publish", "signing") and not any(w in hay for w in (action, "store", "certificate")):
            continue
        if action == "monetization-change" and "monetiz" not in hay:
            continue
        if action == "credential-access" and not any(w in hay for w in ("credential", "account", "auth")):
            continue
        if action == "device-flash" and not any(w in hay for w in ("flash", "devkit", "console")):
            continue
        if action == "rights-waiver" and "rights" not in hay:
            continue
        out.append({"action": action, "approve_token": token,
                     "why": "the capability description touches this action"})
    return out


def render_spec(spec: dict, ctx: RenderContext, template: str = "") -> str:
    """The spec page, from the shipped template when present, else from this code path."""
    body = template or ctx.template_text or ""
    record = ctx.catalog.get(spec["capability_id"])
    sections_out = []
    for section in spec["sections"]:
        lines = [f"### {section['title']}" + (" (blocking)" if section["blocking"] else ""),
                 f"_{section['purpose']}_" if section["purpose"] else "", ""]
        fields = section["fields"]
        lines.append("| " + " | ".join(fields) + " |")
        lines.append("|" + "|".join(["---"] * len(fields)) + "|")
        for row in section["rows"]:
            lines.append("| " + " | ".join(kit.tidy(str(row.get(field, _FILL_TOKEN))) for field in fields) + " |")
        if section["gaps"]:
            lines += ["", "To fill: " + ", ".join(f"`{gap}`" for gap in section["gaps"])]
        sections_out.append("\n".join(line for line in lines if line is not None).rstrip())
    severity_label = {"block": "blocking", "warn": "warning", "info": "info"}
    gates = "\n".join(f"- [ ] {gate['gate']} ({severity_label.get(gate['severity'], gate['severity'])}; "
                      f"{gate['requires']})" for gate in spec["gates"]) or "- none recorded"
    evidence = "\n".join(f"- {kind}" for kind in spec["evidence_to_file"])
    assumptions = "\n".join(f"- {line}" for line in spec["assumptions"])
    approvals = "; ".join(f"{item['action']} (needs `--approve {item['approve_token']}`)"
                          for item in spec["approvals"]) or "none for this capability"
    values = {
        "CAPABILITY_NAME": spec["capability_name"], "CAPABILITY_ID": spec["capability_id"],
        "CATEGORY_NUMBER": str(spec["category"]), "CATEGORY_TITLE": spec["category_title"],
        "ARCHETYPE_LABEL": spec["archetype_label"], "CATALOG_DESCRIPTION": spec["description"],
        "SKILL_TOOL_NAME": engine.kit.SKILL_TOOL_NAME, "ENGINE": spec["generated_for"]["engine_label"],
        "PLATFORM": spec["generated_for"]["platform"],
        "DEVICE_PROFILE": spec["generated_for"]["device_profile"],
        "INTENT": spec["intent"], "SECTIONS": "\n\n".join(sections_out), "GATES": gates,
        "EVIDENCE": evidence, "ASSUMPTIONS": assumptions, "OWNER": spec["owner"],
        "APPROVALS": approvals, "RUN_VALIDATOR_HINT": f"{RUN_PREFIX} checklist {spec['capability_id']} --format md",
    }
    if body:
        out = body
        for token, value in values.items():
            out = out.replace("{{" + token + "}}", value)
        out = re.sub(r"\{\{[A-Z_]+\}\}", "", out)
    else:  # pragma: no cover - the template asset ships with the package
        out = _spec_fallback(spec, values)
    out = kit.normalize_text(out) + "\n"
    if record is not None and spec.get("risk_reasons"):
        pass
    return out


def _spec_fallback(spec: dict, values: dict) -> str:  # pragma: no cover
    return (f"# {values['CAPABILITY_NAME']}\n\n`{values['CAPABILITY_ID']}` · "
            f"Category {values['CATEGORY_NUMBER']} — {values['CATEGORY_TITLE']}\n\n"
            f"> {values['CATALOG_DESCRIPTION']}\n\n{values['SECTIONS']}\n")


def spec_text(spec: dict, ctx: RenderContext) -> str:
    """A plain-text digest of the spec, for terminals and issue trackers."""
    lines = [f"{spec['capability_name']}  ({spec['capability_id']})",
             f"category {spec['category']} {spec['category_title']}",
             f"archetype {spec['archetype']} · owner {spec['owner']} · status {spec['status']}",
             f"target {spec['generated_for']['engine']} / {spec['generated_for']['platform']} / "
             f"{spec['generated_for']['device_profile']}", ""]
    for section in spec["sections"]:
        marks = "blocking" if section["blocking"] else "optional"
        lines.append(f"- {section['title']} [{marks}] gaps: {', '.join(section['gaps']) or 'none'}")
    lines.append("")
    lines.append("gates:")
    for gate in spec["gates"]:
        lines.append(f"  {gate['gate']}: {gate['rule']}")
    lines.append("")
    lines.append("evidence to file: " + ", ".join(spec["evidence_to_file"]))
    if spec["approvals"]:
        lines.append("approvals: " + ", ".join(item["action"] for item in spec["approvals"]))
    return "\n".join(lines) + "\n"


def build_plan(records, ctx: RenderContext, sprint: str = "", owner_override: str = "") -> dict:
    """An ordered plan: decisions first, then production, build, operation, support, verification."""
    order = {"decide": 0, "produce": 1, "build": 2, "operate": 3, "provide": 4, "verify": 5}
    items = []
    for record in records:
        arch = ctx.archetype(record["archetype"])
        phase = arch.get("plan_phase", "decide")
        effort = "S" if record["est_days"] <= 2 else "M" if record["est_days"] <= 5 else \
            "L" if record["est_days"] <= 10 else "XL"
        items.append({"seq": 0, "phase": phase, "capability_id": record["id"],
                      "capability_name": record["name"], "owner": owner_override or record["role"],
                      "gates": [gate["id"] for gate in arch.get("gates", [])], "effort": effort,
                      "est_days": record["est_days"], "depends_on": [],
                      "risk_class": record["risk_class"], "risk_reasons": record["risk_reasons"],
                      "category_title": record["category_title"],
                      "note": f"{len([s for s in arch.get('sections', []) if s.get('blocking')])} blocking "
                              f"sections to fill before this can start",
                      "evidence_kinds": arch.get("evidence_kinds", [])})
    items.sort(key=lambda item: (order.get(item["phase"], 9), -item["est_days"], item["capability_id"]))
    for position, item in enumerate(items, start=1):
        item["seq"] = position
        if position > 1 and item["phase"] == "verify":
            item["depends_on"] = [prev["capability_id"] for prev in items[:position - 1]
                                  if prev["phase"] != "verify"][:3]
        elif position > 1:
            previous = items[position - 2]
            if previous["phase"] == "decide" and item["phase"] != "decide":
                item["depends_on"] = [previous["capability_id"]]
    points = {"S": 1, "M": 2, "L": 5, "XL": 8}
    totals = {"capabilities": len(items), "effort_points": sum(points[i["effort"]] for i in items),
              "est_days": sum(i["est_days"] for i in items),
              "phases": {phase: sum(1 for i in items if i["phase"] == phase) for phase in
                        sorted({i["phase"] for i in items}, key=lambda p: order.get(p, 9))},
              "blocking_gates": sum(len(i["gates"]) for i in items)}
    return {"kind": "plan", "sprint": sprint or "unassigned",
            "umbrella": ctx.catalog.umbrella_name(records),
            "generated_for": {"engine": ctx.engine, "platform": ctx.platform,
                              "device_profile": ctx.device_profile,
                              "dataset_fingerprint": ctx.catalog.meta.get("fingerprint")},
            "items": items, "totals": totals,
            "approval_required": [i["capability_id"] for i in items if i["risk_class"] == "approval-required"]}


def render_plan_md(plan: dict) -> str:
    lines = [f"# Sprint plan - {plan['umbrella']}", "",
             f"{plan['totals']['capabilities']} capabilities · {plan['totals']['effort_points']} story "
             f"points · about {plan['totals']['est_days']} person-days · sprint `{plan['sprint']}`", "",
             "Target: " + ", ".join(f"{key} `{value}`" for key, value in plan["generated_for"].items()), ""]
    current = None
    for item in plan["items"]:
        if item["phase"] != current:
            current = item["phase"]
            lines += [f"## {kit.title_case(current)} phase", ""]
        risk = "" if item["risk_class"] == "routine" else f" · risk {item['risk_class']}"
        lines.append(f"{item['seq']}. **{item['capability_name']}** (`{item['capability_id']}`) - "
                     f"{item['owner']}, {item['effort']}/{item['est_days']}d{risk}")
        lines.append(f"   - gates: {', '.join(item['gates'])}")
        if item["depends_on"]:
            lines.append(f"   - after: {', '.join(item['depends_on'])}")
        lines.append(f"   - {item['note']}")
    lines += ["", "## Totals", ""]
    for key, value in plan["totals"].items():
        if key == "phases":
            continue
        lines.append(f"- {key}: {value}")
    lines.append("- phases: " + ", ".join(f"{phase}={count}" for phase, count in
                                         plan["totals"]["phases"].items()))
    if plan["approval_required"]:
        lines += ["", "Needs an approval token before it can be scheduled: "
                  + ", ".join(plan["approval_required"]), ""]
    return "\n".join(lines) + "\n"


def build_checklist(records, ctx: RenderContext, done_ids=(), done_capabilities=()) -> dict:
    """One checkbox per blocking section, plus one per gate and one per evidence kind.

    'done_ids' are individual check ids from an evidence file; 'done_capabilities' are capability ids
    whose evidence verdict is pass, which satisfies every item of that capability.
    """
    done = {str(item) for item in done_ids or ()}
    capabilities = {str(item) for item in done_capabilities or ()}
    items = []
    for record in records:
        spec = build_spec(record, ctx)
        position = 0
        for section in spec["sections"]:
            if not section["blocking"]:
                continue
            position += 1
            items.append({"id": f"CHK-{record['id']}-{position:02d}", "capability_id": record["id"],
                          "capability_name": record["name"], "section": section["id"],
                          "section_title": section["title"],
                          "text": f"{section['title']}: fill every row and resolve "
                                  f"{len(section['gaps'])} gap(s) - {section['purpose']}".strip(),
                          "blocking": True, "evidence_kind": section["evidence_kind"],
                          "gaps": section["gaps"],
                          "done": f"CHK-{record['id']}-{position:02d}" in done
                          or record["id"] in capabilities})
        for gate in spec["gates"]:
            position += 1
            items.append({"id": f"CHK-{record['id']}-{position:02d}", "capability_id": record["id"],
                          "capability_name": record["name"], "section": "gates",
                          "section_title": "Gates", "text": f"gate `{gate['gate']}`: {gate['rule']}",
                          "blocking": gate["severity"] == "block", "gate": gate["gate"],
                          "evidence_kind": "gate-verdict",
                          "done": f"CHK-{record['id']}-{position:02d}" in done
                          or record["id"] in capabilities})
        for kind in spec["evidence_to_file"]:
            position += 1
            items.append({"id": f"CHK-{record['id']}-{position:02d}", "capability_id": record["id"],
                          "capability_name": record["name"], "section": "evidence",
                          "section_title": "Evidence", "text": f"file a `{kind}` record for {record['id']}",
                          "blocking": True, "evidence_kind": kind,
                          "done": f"CHK-{record['id']}-{position:02d}" in done
                          or record["id"] in capabilities})
    totals = {"items": len(items), "done": sum(1 for i in items if i["done"]),
              "blocking": sum(1 for i in items if i["blocking"]),
              "capabilities": len(records)}
    return {"kind": "checklist", "umbrella": ctx.catalog.umbrella_name(records),
            "generated_for": {"engine": ctx.engine, "platform": ctx.platform,
                              "device_profile": ctx.device_profile},
            "items": items, "totals": totals}


def render_checklist_md(checklist: dict) -> str:
    lines = [f"# Checklist - {checklist['umbrella']}", "",
             f"{checklist['totals']['done']} of {checklist['totals']['items']} items recorded. "
             "A box is ticked only by a matching record in the evidence file.", ""]
    capability = None
    for item in checklist["items"]:
        if item["capability_id"] != capability:
            capability = item["capability_id"]
            lines += [f"## {item['capability_name']} (`{capability}`)", ""]
        lines.append(f"- [{'x' if item['done'] else ' '}] {item['text']} "
                     f"_(evidence: `{item['evidence_kind']}`)_")
    lines += ["", f"Remaining: {checklist['totals']['items'] - checklist['totals']['done']}", ""]
    return "\n".join(lines) + "\n"


CHECKLIST_COLUMNS = ["id", "capability_id", "capability_name", "section", "text", "blocking",
                     "evidence_kind", "done"]


def render_checklist_csv(checklist: dict) -> str:
    return kit.csv_render(CHECKLIST_COLUMNS, [{key: item.get(key) for key in CHECKLIST_COLUMNS}
                                              for item in checklist["items"]])


def build_prompt_pack(records, ctx: RenderContext, style: str = "engineer") -> dict:
    """Prompts for a LOCAL model only: the configured provider must be offline-capable."""
    provider = ctx.cfg.get("provider", {}) or {}
    kind = str(provider.get("kind") or "local").lower()
    if kind not in ("local", "none"):
        raise kit.InputError(f"provider.kind is {kind!r}; this Skill Tool only drives local models",
                             hint="set provider.kind to 'local' (or 'none' to skip prompts), or edit the "
                                  "--config file you passed", where="provider.kind")
    if provider.get("allow_network") or ctx.cfg.get("offline", {}).get("allow_network"):
        raise kit.InputError("a prompt pack refuses to run while network access is enabled",
                             hint="set offline.allow_network and provider.allow_network to false, or use "
                                  "a connected tool for that work", where="offline.allow_network")
    endpoint = str(provider.get("endpoint") or "")
    if endpoint and not re.match(r"^(https?://)?(localhost|127\.0\.0\.1|::1|\[::1\])([:/]|$)", endpoint):
        raise kit.InputError(f"provider.endpoint {endpoint!r} is not a loopback address",
                             hint="point it at a local server (for example http://127.0.0.1:11434) or "
                                  "leave it empty", where="provider.endpoint")
    packs = []
    for record in records:
        spec = build_spec(record, ctx)
        open_gaps = sorted({gap for section in spec["sections"] for gap in section["gaps"]})
        task = {
            "engineer": ("Fill the rows marked 'fill' for one capability of an offline game project, "
                         "using only the supplied context. Ask for missing values instead of inventing "
                         "licenses, versions, or measurements."),
            "producer": ("Turn this capability spec into a two-week work order: sequencing, owners, and "
                         "the gates that block it. Do not add scope."),
            "qa": ("Write the exact check for each gate of this capability, with the measured value, the "
                   "threshold, and the evidence file name."),
            "concise": ("Answer with the filled rows only, one per line, in the same column order as "
                        "the section table."),
        }.get(style, "Fill the rows marked 'fill' from the supplied context only.")
        packs.append({
            "capability_id": record["id"], "capability_name": record["name"],
            "style": style,
            "model": provider.get("model_id") or "unconfigured",
            "system": "You are an offline assistant for a game production team. Never claim a test was "
                      "run. Never propose a download, a remote asset, or a credential. Every license, "
                      "version, and budget value must come from the provided context or be marked unknown.",
            "prompt": "\n".join([
                task, "", f"Capability: {record['name']} ({record['id']})",
                f"Archetype: {record['archetype']} - {spec['intent']}",
                f"Catalog description: {record['description']}",
                f"Target: {spec['generated_for']['engine_label']}, platform {ctx.platform}, device profile "
                f"{ctx.device_profile}",
                f"Sections still open: {', '.join(open_gaps) or 'none'}",
                f"Gates: " + "; ".join(f"{gate['gate']} ({gate['rule']})" for gate in spec["gates"]),
                "Reply in the same table shape, and mark any value you cannot fill as 'fill'."]),
            "context_refs": [spec["source_ref"], f"{engine.kit.SKILL_TOOL_NAME}/asset-license-policy.json",
                             f"{engine.kit.SKILL_TOOL_NAME}/asset-budget-presets.json"],
            "prohibitions": ["no network calls", "no engine or device command executed here",
                            "no license claim without the rights manifest row",
                            "no measured value without an evidence record"],
        })
    return {"kind": "prompt-pack", "provider": {"kind": kind, "model_id": provider.get("model_id") or "",
                                                "endpoint": endpoint or "(unset)", "network": False},
            "style": style, "generated_for": {"engine": ctx.engine, "platform": ctx.platform,
                                              "device_profile": ctx.device_profile},
            "packs": packs, "count": len(packs)}


def render_prompt_pack_md(pack: dict) -> str:
    lines = [f"# Offline prompt pack ({pack['count']} capabilities)", "",
             f"Provider kind `{pack['provider']['kind']}`, model `{pack['provider']['model_id'] or 'unconfigured'}`, "
             f"endpoint `{pack['provider']['endpoint']}`. No network access is permitted by this pack.", ""]
    if not pack["packs"]:
        lines.append("(no capabilities selected)")
    for item in pack["packs"]:
        lines += [f"## {item['capability_name']} (`{item['capability_id']}`)", "",
                  f"Model: {item['model']} · style: {item['style']}", "", "```text", item["prompt"], "```",
                  "", "Context to attach: " + ", ".join(f"`{ref}`" for ref in item["context_refs"]), "",
                  "Hard rules: " + "; ".join(item["prohibitions"]), ""]
    return "\n".join(lines) + "\n"


EXPORT_COLUMNS = list(engine.ENTRY_KEYS) + ["template_ok"]


def render_records_csv(records, columns=None) -> str:
    """The 'export' shape: one row per capability, columns in the documented order."""
    columns = list(columns or EXPORT_COLUMNS)
    rows = [{key: record.get(key) for key in columns} for record in records]
    return kit.csv_render(columns, rows)

# ---------------------------------------------------------------------------
# text rendering of records and lists (used by the CLI, kept here so both stay in step)
# ---------------------------------------------------------------------------
def render_categories_md(catalog) -> str:
    lines = ["# Categories", "",
             f"{len(catalog.categories)} categories, {len(catalog)} capabilities, dataset fingerprint "
             f"`{catalog.meta.get('fingerprint')}`", ""]
    for number in sorted(catalog.categories):
        category = catalog.categories[number]
        lines.append(f"- **c{number:02d}** {category['title']} - {category['count']} capabilities · "
                     f"layer `{category['layer']}`")
    return "\n".join(lines) + "\n"


def render_list_md(rows, header: str, ctx: RenderContext) -> str:
    lines = [f"# {header}", ""]
    for record in rows:
        lines.append(f"- `{record['id']}` ({record['ref']}) **{record['name']}** - {record['archetype']}, "
                     f"{record['difficulty']}, {record['est_days']}d, {record['role']}")
    return "\n".join(lines) + "\n"


def render_show_md(records, ctx: RenderContext) -> str:
    lines = []
    for record in records:
        category = ctx.catalog.category(record)
        lines += [f"## {record['name']}", "",
                  f"`{record['id']}` · Category {category['number']} — {category['title']} · "
                  f"archetype **{record['archetype'].capitalize()}** · owner `{record['role']}` · "
                  f"{record['difficulty']}, {record['est_days']} days",
                  "", f"> {record['description']}", "",
                  f"- tags: {', '.join(record['tags'])}",
                  f"- engines in scope: {', '.join(record['engines'])}",
                  f"- layer: `{record['layer']}` · risk class: `{record['risk_class']}`"
                  + (f" ({', '.join(record['risk_reasons'])})" if record["risk_reasons"] else ""),
                  f"- description matches the archetype template: "
                  f"{'yes' if record.get('template_ok', True) else 'no'}",
                  f"- source: `{engine.kit.SKILL_TOOL_NAME}/asset-catalog.json#{record['id']}`", ""]
    return "\n".join(lines) + "\n"


def render_search_md(result: dict, ctx: RenderContext) -> str:
    lines = [f"# Search: {result['query']}", "",
             f"{result['total']} match(es)" + (f", showing {len(result['hits'])}" if result.get("truncated")
                                              else ""), ""]
    for hit in result["hits"]:
        record = hit["record"]
        lines.append(f"- `{record['id']}` **{record['name']}** (score {hit['score']}) - "
                     f"category {record['category']} {record['category_title']}")
        lines.append(f"  {kit.wrap(record['description'], 100, '  ').splitlines()[0]}")
    return "\n".join(lines) + "\n"


def record_lines(records, ctx: RenderContext, show_desc: bool = True) -> str:
    out = []
    for record in records:
        out.append(f"{record['id']}  {record['name']}")
        if show_desc:
            out.append("        " + kit.wrap(record["description"], 88, "        ").lstrip())
    return "\n".join(out) + ("\n" if out else "")


def build_evidence_template(records, ctx: RenderContext) -> list:
    """Skeleton evidence records: one per capability, with every check the checklist demands."""
    out = []
    for record in records:
        spec = build_spec(record, ctx)
        checks = []
        number = 0
        for section in spec["sections"]:
            if not section["blocking"]:
                continue
            number += 1
            checks.append({"id": f"CHK-{record['id']}-{number:02d}", "kind": "manual",
                           "description": f"{section['title']}: {section['purpose']}".strip(),
                           "command_hint": f"{RUN_PREFIX} checklist {record['id']} --format md",
                           "measured": _FILL_TOKEN, "pass_threshold": _FILL_TOKEN, "result": "not-run"})
        for gate in spec["gates"]:
            number += 1
            checks.append({"id": f"CHK-{record['id']}-{number:02d}", "kind": "gate",
                           "description": f"gate {gate['gate']}: {gate['rule']}",
                           "pass_threshold": "a recorded pass with evidence", "result": "not-run"})
        out.append({"kind": "evidence", "capability_id": record["id"],
                    "capability_name": record["name"], "verdict": "not-run",
                    "operator": record["role"], "run_started": _FILL_TOKEN,
                    "environment": {"engine": ctx.engine, "engine_version": _FILL_TOKEN,
                                    "platform": ctx.platform, "hardware": ctx.device_profile,
                                    "scene_or_build": _FILL_TOKEN, "network_state": "blocked"},
                    "checks": checks,
                    "measured_result": _FILL_TOKEN, "pass_threshold": _FILL_TOKEN,
                    "failure_evidence": "", "remediation": "", "regression_retest": "",
                    "reproducibility": {"offline_only": True, "inputs_pinned": False, "bundle_id": ""},
                    "notes": "skeleton written by the Skill Tool; replace 'fill' before submitting"})
    return out


def build_rights_manifest_template(count: int = 3) -> list:
    """A manifest skeleton with the policy's required fields, so 'rights' has something to check."""
    required = ["asset", "kind", "license", "source", "acquired_from", "acquired_on", "generated_by",
                "commercial_use", "redistribution", "record_path"]
    return [{key: _FILL_TOKEN for key in required} for _ in range(max(1, count))]


__all__ = [name for name in dir() if not name.startswith("_")]
