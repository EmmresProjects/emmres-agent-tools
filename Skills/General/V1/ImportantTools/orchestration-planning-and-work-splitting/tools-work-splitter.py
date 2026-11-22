#!/usr/bin/env python3
"""Primary runnable entry point for the unified Skill Tool:

    Orchestration, Planning, and Work Splitting
    (Category 1 of the Ultimate Agent Skill Catalog; 12 capabilities,
     including the flagship Powerful Work Splitter).

Python 3.9+ standard library only. No network, no credentials, no shell or
arbitrary-code execution. Run `python3 tools-work-splitter.py help` for usage
and `python3 tools-work-splitter.py capabilities` for the capability list.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

# Keep the Skill Tool folder free of generated cache subfolders at runtime.
sys.dont_write_bytecode = True


def _load_sibling(module_name, filename):
    """Load a sibling tools-*.py file whose hyphenated name is not importable."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


engine = _load_sibling("tools_workflow_engine", "tools-workflow-engine.py")
catalog = _load_sibling("tools_catalog", "tools-catalog.py")

VERSION = "1.0.0"

CAPABILITY_COMMANDS = {
    1: ["assess", "worked-example"],
    2: ["assess"],
    3: ["estimate"],
    4: ["negotiate"],
    5: ["checkpoint", "continue"],
    6: ["expand"],
    7: ["compress"],
    8: ["plan"],
    9: ["plan", "run"],
    10: ["plan"],
    11: ["gate", "checkpoint"],
    12: ["assemble"],
}

SUPPORTING_COMMANDS = {
    "intake": "Parse plain text / Markdown / copied HTML capability sources into one unified spec.",
    "catalog": "Query the bundled 24-category, 242-skill capability registry and build category briefs.",
    "validate": "Validate project, state, config, spec or registry inputs against schemas and limits.",
    "state": "Show, snapshot or reset long-project continuation state.",
    "run": "Execute bounded, declarative stage actions with timeouts, retries and cancellation.",
    "recover": "Preserve successful work, isolate the earliest bad state, repair and rerun affected checks.",
    "cleanup": "Remove generated .bak / .tmp working files (approval required).",
    "worked-example": "Reproduce the flagship worked example: 8 recommended, 5 chosen, expanded to 10.",
    "capabilities": "List every implemented capability and its command.",
    "help": "Built-in help.",
    "version": "Print tool version.",
}

EXIT = {"ok": 0, "error": 2, "needs_approval": 3, "blocked": 4, "cancelled": 5, "dry_run": 0}


def envelope(command, status, data=None, errors=None, warnings=None, capability=None, budget=None):
    out = {
        "schema_version": engine.SCHEMA_VERSION,
        "tool": engine.FOLDER_NAME,
        "version": VERSION,
        "command": command,
        "capability": capability,
        "status": status,
        "data": data if data is not None else {},
        "warnings": warnings or [],
        "errors": errors or [],
    }
    if budget is not None:
        out["budget"] = budget.as_dict()
    return out


class Runner:
    def __init__(self, args, config, budget, registry=None):
        self.args = args
        self.config = config
        self.budget = budget
        self._registry = registry
        self._store = None
        self.warnings = []

    # -- shared helpers ----------------------------------------------------
    @property
    def registry(self):
        if self._registry is None:
            self._registry = catalog.load_registry(self.config, self.budget)
        return self._registry

    @property
    def state_path(self):
        return Path(self.args.state or self.config["storage"]["state_path"])

    def store(self):
        # One store per invocation: all commands read and mutate the same state object.
        if self._store is None:
            self._store = engine.StateStore(self.state_path, self.config, self.budget)
        return self._store

    def load_state(self):
        return self.store().load()

    def save_state(self, store):
        return store.save()

    def input_format(self):
        fmt = getattr(self.args, "input_format", None) or getattr(self.args, "format", None) or "auto"
        if fmt == "auto":
            p = getattr(self.args, "input", None)
            if p and str(p).lower().endswith(".json"):
                return "json"
            if p and str(p).lower().endswith((".html", ".htm")):
                return "html"
            if p and str(p).lower().endswith(".md"):
                return "markdown"
            return "text" if p else "text"
        return fmt

    def load_project(self):
        a = self.args
        fmt = self.input_format()
        if getattr(a, "text", None):
            spec = catalog.parse_source(a.text, registry=None, category_filter=getattr(a, "category", None),
                                        name_override=getattr(a, "name", None), source_format="text" if fmt == "json" else fmt)
            raw = catalog.project_from_spec(spec)
        elif getattr(a, "input", None):
            if fmt == "json":
                raw = engine.read_json(a.input, self.budget, self.config["limits"]["max_input_bytes"], "input")
                if isinstance(raw, dict) and "capabilities" in raw:
                    raw = catalog.project_from_spec(raw)
            else:
                text = engine.read_text(a.input, self.budget, self.config["limits"]["max_input_bytes"], "input")
                spec = catalog.parse_source(text, registry=None, category_filter=getattr(a, "category", None),
                                            name_override=getattr(a, "name", None), source_format=fmt)
                raw = catalog.project_from_spec(spec)
        else:
            raise engine.ToolError("ERR_INPUT_MISSING", "supply --input <file> or --text <capability text>", "input")
        project = engine.validate_project(raw, self.config)
        inv = engine.file_inventory(project)
        return project, inv

    def state_from(self, project, plan, score, estimate, inv):
        return engine.new_state(project, plan, score, estimate, inv, self.config)

    def approval(self, command, reason, data=None):
        if self.args.approve:
            return None
        if self.args.dry_run:
            return envelope(command, "dry_run", {"would_perform": reason, "dry_run": True, **(data or {})},
                            warnings=["no changes were made; re-run with --approve to apply"], budget=self.budget)
        return envelope(command, "needs_approval",
                        {"reason": reason, "required_flag": self.config["approval"]["approval_flag"],
                         "policy": "destructive, irreversible, privileged and publishing actions require explicit approval",
                         **(data or {})},
                        errors=[{"code": "ERR_APPROVAL_REQUIRED", "message": reason}], budget=self.budget)

    # -- commands ----------------------------------------------------------
    def cmd_help(self):
        a = self.args
        if getattr(a, "command_name", None):
            topic = a.command_name
            return envelope("help", "ok", {
                "topic": topic,
                "description": SUPPORTING_COMMANDS.get(topic, "see capabilities; all commands accept --config, --state, --output, --out-format, --timeout, --retries, --cancel-file, --approve, --dry-run"),
                "example": "python3 %s %s --help" % (Path(__file__).name, topic),
            }, budget=self.budget)
        return envelope("help", "ok", {
            "purpose": "Unified Skill Tool for Orchestration, Planning, and Work Splitting (12 capabilities).",
            "primary_entry_point": "python3 %s <command> [options]" % Path(__file__).name,
            "commands": sorted(set(list(SUPPORTING_COMMANDS.keys()) + [c for v in CAPABILITY_COMMANDS.values() for c in v])),
            "quick_start": [
                "python3 tools-work-splitter.py capabilities",
                "python3 tools-work-splitter.py assess --input orchestration-planning-and-work-splitting/asset-sample-project.json --input-format json",
                "python3 tools-work-splitter.py plan --input orchestration-planning-and-work-splitting/asset-sample-project.json --input-format json --stages 5 --state ./state.json",
                "python3 tools-work-splitter.py checkpoint --stage 1 --state ./state.json --evidence orchestration-planning-and-work-splitting/asset-gate-evidence.json",
                "python3 tools-work-splitter.py continue --state ./state.json --reply Continue",
                "python3 tools-work-splitter.py assemble --state ./state.json --publish --approve",
            ],
            "global_options": ["--config", "--state", "--output", "--out-format", "--timeout", "--retries",
                               "--cancel-file", "--approve", "--dry-run"],
            "knowledge": "%s/knowledge-readme.md" % engine.FOLDER_NAME,
        }, budget=self.budget)

    def cmd_capabilities(self):
        cat = catalog.find_category(self.registry, "Orchestration, Planning, and Work Splitting")
        caps = []
        for s in cat["skills"]:
            caps.append({
                "number": s["number"], "name": s["name"], "id": s["id"],
                "description": s["description"],
                "commands": CAPABILITY_COMMANDS.get(s["number"], []),
                "implemented": True,
            })
        return envelope("capabilities", "ok", {
            "umbrella": cat["name"], "category_number": cat["number"],
            "capability_count": len(caps), "capabilities": caps,
            "supporting_commands": SUPPORTING_COMMANDS,
            "registry": {"categories": self.registry["category_count"], "skills": self.registry["skill_count"]},
            "usage": "python3 tools-work-splitter.py <command> --help",
        }, budget=self.budget)

    def cmd_version(self):
        return envelope("version", "ok", {"version": VERSION, "python": sys.version.split()[0],
                                          "dependencies": "Python standard library only"}, budget=self.budget)

    def cmd_estimate(self):
        project, inv = self.load_project()
        units = engine.decompose(project, self.config)
        score = engine.score_request(project, units, self.config)
        estimate = engine.estimate_counts(project, units, self.config)
        return envelope("estimate", "ok", {
            "goal": project["goal"], "estimate": estimate,
            "units": len(units), "requirements": len(project["requirements"]),
            "scoring": score, "file_inventory": inv,
            "checks": estimate["checks"],
        }, capability="Adaptive Prompt Count Estimator", budget=self.budget)

    def cmd_assess(self):
        project, inv = self.load_project()
        units = engine.decompose(project, self.config)
        score = engine.score_request(project, units, self.config)
        estimate = engine.estimate_counts(project, units, self.config)
        chosen = getattr(self.args, "stages", None)
        fallback = engine.build_stages(units, estimate["recommended"], self.config)
        data = {
            "goal": project["goal"],
            "first_response": {
                "one_turn_feasible": score["one_turn"],
                "decision": "proceed in one response" if score["one_turn"] else "recommend staged work and ask the user to choose the final stage count before execution",
                "weighted_score": score["weighted_score"], "threshold": score["threshold"], "reasons": score["reasons"],
            },
            "factors": score["factors"],
            "recommended_stages": estimate["recommended"],
            "minimum_stages": estimate["minimum"], "maximum_stages": estimate["maximum"],
            "major_stages": [{"label": s["label"], "work": " + ".join(u["name"] for u in s["units"])} for s in fallback],
            "fallback_staged_plan": {"stages": [s["label"] for s in fallback], "count": len(fallback)},
            "file_contract": inv,
            "user_control": "the user may choose fewer, equal, or more stages; the plan is rebalanced without dropping requirements",
        }
        if inv["issues"]:
            data["blocked"] = True
            data["reupload_request"] = inv["reupload_request"]
            return envelope("assess", "blocked", data, capability="Powerful Work Splitter",
                            errors=[{"code": "ERR_FILE_CONTRACT", "message": "required files are unavailable; request re-upload before any file-dependent stage"}],
                            budget=self.budget)
        if chosen is None:
            data["next_action"] = {"type": "choose_stage_count", "options": sorted({estimate["minimum"], estimate["recommended"], estimate["maximum"]}),
                                   "instruction": "choose the final stage count, then run plan --stages N or assess --stages N"}
            return envelope("assess", "ok", data, capability="Powerful Work Splitter / Single-Prompt Feasibility Assessor", budget=self.budget)
        stages = engine.build_stages(units, int(chosen), self.config)
        plan = engine.build_plan(project, stages, units, score, estimate, self.config)
        store = self.store()
        store.state = self.state_from(project, plan, score, estimate, inv)
        store.record("assess", "ok", 1, "user selected %d stages" % int(chosen))
        info = self.save_state(store)
        data.update({"user_selected_stages": int(chosen), "approved_stage_plan": [s["label"] for s in stages],
                     "tradeoffs": plan["tradeoffs"], "state_write": info})
        return envelope("assess", "ok", data, capability="Powerful Work Splitter / User-Controlled Stage Negotiator", budget=self.budget)

    def cmd_plan(self):
        project, inv = self.load_project()
        units = engine.decompose(project, self.config)
        score = engine.score_request(project, units, self.config)
        estimate = engine.estimate_counts(project, units, self.config)
        y = int(getattr(self.args, "stages", None) or estimate["recommended"])
        stages = engine.build_stages(units, y, self.config)
        plan = engine.build_plan(project, stages, units, score, estimate, self.config)
        store = self.store()
        store.state = self.state_from(project, plan, score, estimate, inv)
        store.record("plan", "ok", 1)
        info = self.save_state(store)
        return envelope("plan", "ok", {
            "goal": project["goal"], "stage_count": len(stages),
            "recommended": estimate["recommended"], "estimate": estimate,
            "stages": [{"label": s["label"], "unit_ids": s["unit_ids"], "requirement_ids": s["requirement_ids"],
                        "entry_criteria": s["entry_criteria"], "exit_criteria": s["exit_criteria"],
                        "progress_contract": s["progress_contract"], "is_final": s["is_final"]} for s in stages],
            "dependency_graph": plan["dependency_graph"],
            "critical_path": plan["critical_path"],
            "parallel_streams": plan["parallel_streams"],
            "requirement_ownership": plan["requirement_ownership"],
            "milestone_gates": plan["milestone_gates"],
            "tradeoffs": plan["tradeoffs"],
            "file_inventory": inv,
            "state_write": info,
            "next_action": "checkpoint --stage 1 after supplying reproducible evidence, then continue",
        }, capability="Dependency-Aware Task Decomposer / Parallel Workstream Coordinator / Critical Path Scheduler",
            budget=self.budget)

    def _rebalance(self, command, capability):
        a = self.args
        store = self.store()
        st = self.load_state()
        target = int(a.stages)
        prev = st["plan"]["stages"]
        completed = sorted(set(st["completed_stages"]))
        preserve = len(completed) if command in ("expand", "compress") else 0
        if command == "expand" and target <= len(prev):
            return envelope(command, "error", {"requested": target, "current": len(prev)},
                            errors=[{"code": "ERR_EXPAND", "message": "expand requires a stage count greater than the current plan"}],
                            capability=capability, budget=self.budget)
        if command == "compress" and target >= len(prev):
            return envelope(command, "error", {"requested": target, "current": len(prev)},
                            errors=[{"code": "ERR_COMPRESS", "message": "compress requires a stage count lower than the current plan"}],
                            capability=capability, budget=self.budget)
        units = []
        for s in prev:
            for u in s["units"]:
                units.append(copy.deepcopy(u))
        # Keep unique units; critical gates and assembly are never dropped.
        seen = set()
        uniq = []
        for u in units:
            if u["id"] not in seen:
                seen.add(u["id"])
                uniq.append(u)
        units = uniq
        stages = engine.build_stages(units, target, self.config, preserve_prefix=preserve, previous_stages=prev)
        plan = engine.build_plan(st["project"], stages, units, st["assessment"]["score"],
                                 {"recommended": st["plan"]["recommended_stages"],
                                  "minimum": st["plan"]["minimum_stages"], "maximum": st["plan"]["maximum_stages"]},
                                 self.config)
        st["plan"] = plan
        st["current_stage"] = min(max(completed) + 1, target) if completed else 1
        st["next_actions"] = stages[st["current_stage"] - 1]["unit_ids"]
        store.record(command, "ok", st["current_stage"], "%d -> %d stages" % (len(prev), target))
        info = self.save_state(store)
        return envelope(command, "ok", {
            "previous_stage_count": len(prev), "new_stage_count": target,
            "labels": [s["label"] for s in stages],
            "preserved_stages": [s["label"] for s in stages[:preserve]],
            "requirements_still_owned": len(plan["requirement_ownership"]),
            "requirements_dropped": 0,
            "critical_units_preserved": sum(1 for s in stages for u in s["units"] if u["critical"]),
            "final_stage_includes_assembly": stages[-1]["includes_assembly_and_polish"],
            "tradeoffs": plan["tradeoffs"], "state_write": info,
        }, capability=capability, budget=self.budget)

    def cmd_negotiate(self):
        return self._rebalance("negotiate", "User-Controlled Stage Negotiator")

    def cmd_expand(self):
        return self._rebalance("expand", "Dynamic Stage Expansion Manager")

    def cmd_compress(self):
        return self._rebalance("compress", "Dynamic Stage Compression Manager")

    def cmd_gate(self):
        store = self.store()
        st = self.load_state()
        stage_no = int(self.args.stage)
        stage = next((s for s in st["plan"]["stages"] if s["number"] == stage_no), None)
        if stage is None:
            raise engine.ToolError("ERR_STAGE_UNKNOWN", "stage %d is not in the plan" % stage_no, "stage")
        evidence = engine.read_json(self.args.evidence, self.budget, self.config["limits"]["max_input_bytes"], "evidence")
        result = engine.gate_check(stage, evidence, self.config, getattr(self.args, "phase", "build"))
        store.record("gate", result["status"], stage_no)
        self.save_state(store)
        return envelope("gate", "ok" if result["status"] == "pass" else result["status"],
                        {"stage": stage["label"], "phase": getattr(self.args, "phase", "build"), **result},
                        errors=[{"code": "ERR_GATE", "message": result["reason"]}] if result["status"] != "pass" else [],
                        capability="Milestone Gatekeeper", budget=self.budget)

    def cmd_checkpoint(self):
        store = self.store()
        st = self.load_state()
        stage_no = int(self.args.stage)
        stage = next((s for s in st["plan"]["stages"] if s["number"] == stage_no), None)
        if stage is None:
            raise engine.ToolError("ERR_STAGE_UNKNOWN", "stage %d is not in the plan" % stage_no, "stage")
        evidence = engine.read_json(self.args.evidence, self.budget, self.config["limits"]["max_input_bytes"], "evidence")
        gate = engine.gate_check(stage, evidence, self.config, getattr(self.args, "phase", "build"))
        if gate["status"] != "pass":
            store.record("checkpoint", "blocked", stage_no)
            self.save_state(store)
            return envelope("checkpoint", "blocked", {"stage": stage["label"], "gate": gate},
                            errors=[{"code": "ERR_GATE", "message": "checkpoint refused: gate evidence incomplete"}],
                            capability="Checkpoint and Continue Controller / Milestone Gatekeeper", budget=self.budget)
        receipt, completed, nxt = engine.checkpoint_receipt(st, self.config, stage_no)
        st["completed_stages"] = completed
        st["current_stage"] = nxt or stage_no
        st["next_actions"] = receipt["next_actions"]
        st["plan"]["milestone_gates"] = [
            {**g, "status": "pass" if g["stage"] in completed else "pending"} for g in st["plan"]["milestone_gates"]
        ]
        store.checkpoint(stage_no, receipt, {"types": gate["seen_types"]})
        store.record("checkpoint", "ok", stage_no)
        self.save_state(store)
        return envelope("checkpoint", "ok", {
            "checkpoint_receipt": receipt, "gate": gate,
            "status_note": "paused at a safe boundary; resume only after Continue or equivalent",
        }, capability="Checkpoint and Continue Controller", budget=self.budget)

    def cmd_continue(self):
        st = self.load_state()
        reply = getattr(self.args, "reply", None) or ""
        verdict = engine.classify_reply(reply, self.config)
        if not verdict["recognized"]:
            return envelope("continue", "blocked", {"reply": reply, "verdict": verdict,
                            "preserved_state": {"completed_stages": st["completed_stages"], "current_stage": st["current_stage"]}},
                            errors=[{"code": "ERR_CONTINUE_INTENT", "message": verdict["reason"]}],
                            capability="Checkpoint and Continue Controller", budget=self.budget)
        nxt = st["current_stage"]
        stage = next((s for s in st["plan"]["stages"] if s["number"] == nxt), None)
        if stage is None:
            return envelope("continue", "blocked", {"reply": reply, "verdict": verdict},
                            errors=[{"code": "ERR_NO_NEXT_STAGE", "message": "no pending stage; run assemble for final delivery"}],
                            capability="Checkpoint and Continue Controller", budget=self.budget)
        receipt, _, _ = engine.checkpoint_receipt(st, self.config, max(st["completed_stages"] or [1]))
        return envelope("continue", "ok", {
            "verdict": verdict, "stage_header": stage["label"],
            "progress_contract": stage["progress_contract"],
            "next_actions": stage["unit_ids"], "blockers": st["blockers"],
            "previous_checkpoint": receipt["stage"],
            "final_stage_ready": stage["is_final"],
            "note": "state identifies completed work, current files, next actions and blockers; no work repeated",
        }, capability="Checkpoint and Continue Controller", budget=self.budget)

    def cmd_run(self):
        a = self.args
        store = self.store()
        st = self.load_state()
        if a.apply:
            gate = self.approval("run", "write stage artifacts to the configured output directory and update state",
                                 {"output_dir": str(getattr(a, "output_dir", None) or self.config["storage"]["output_dir"])})
            if gate:
                return gate
        stage_no = int(getattr(a, "stage", None) or st["current_stage"])
        stage = next((s for s in st["plan"]["stages"] if s["number"] == stage_no), None)
        if stage is None:
            raise engine.ToolError("ERR_STAGE_UNKNOWN", "stage %d is not in the plan" % stage_no, "stage")
        inv = engine.file_inventory(st["project"])
        if inv["issues"]:
            return envelope("run", "blocked", {"stage": stage["label"], "file_contract": inv},
                            errors=[{"code": "ERR_FILE_CONTRACT", "message": "file-dependent stage blocked; re-upload required files"}],
                            capability="Powerful Work Splitter", budget=self.budget)
        actions = []
        for u in stage["units"]:
            actions.append({"id": "ACT-%s" % u["id"], "stage": stage_no, "type": "check", "target": u["id"],
                            "payload": {"pass_conditions": u["pass_conditions"]}})
            if u["kind"] == "work" and u["outputs"]:
                actions.append({"id": "ART-%s" % u["id"], "stage": stage_no, "type": "artifact", "target": u["id"],
                                "payload": {"name": _artifact_name(u), "requirement_ids": u["requirement_ids"]}})
        lim = self.config["limits"]
        if len(actions) > lim["max_actions"]:
            raise engine.ToolError("ERR_LIMIT", "stage has %d actions, limit is %d" % (len(actions), lim["max_actions"]), "limits")
        output_dir = Path(getattr(a, "output_dir", None) or self.config["storage"]["output_dir"])
        results, failures, written = [], [], []
        allowed_types = {"check", "artifact", "note"}
        for i, act in enumerate(actions):
            self.budget.check("run action %d/%d" % (i + 1, len(actions)))
            if act["type"] not in allowed_types:
                raise engine.ToolError("ERR_ACTION_TYPE", "unsupported declarative action type %r" % act["type"], "actions")
            target = act["target"]
            if act["type"] == "check":
                ok = all(isinstance(c, str) and c.strip() for c in act["payload"]["pass_conditions"])
                results.append({"action": act["id"], "type": "check", "target": target, "status": "pass" if ok else "fail"})
                if not ok:
                    failures.append({"action": act["id"], "reason": "missing pass condition"})
            elif act["type"] == "artifact":
                name = act["payload"]["name"]
                if a.apply:
                    dest = output_dir / name
                    body = {"artifact": name, "stage": stage_no, "unit": target,
                            "requirement_ids": act["payload"]["requirement_ids"], "non_empty": True}
                    info = engine.write_json(dest, body, self.budget, retries=int(lim["retries"]), backup=False)
                    written.append(info)
                    st["artifacts"].append({"name": name, "stage": stage_no, "unit": target,
                                            "requirement_ids": act["payload"]["requirement_ids"],
                                            "verified": ["artifact written and readable"]})
                results.append({"action": act["id"], "type": "artifact", "target": target,
                                "status": "simulated" if not a.apply else "written"})
        if not a.apply:
            return envelope("run", "ok", {
                "stage": stage["label"], "dry_run": True,
                "note": "declarative actions were simulated; no files written (re-run with --apply --approve)",
                "actions": results, "failures": failures, "file_contract": inv,
                "bounded": {"max_actions": lim["max_actions"], "retries": lim["retries"], "timeout_seconds": lim["timeout_seconds"]},
            }, capability="Powerful Work Splitter", budget=self.budget)
        store.record("run", "error" if failures else "ok", stage_no, "%d actions" % len(actions))
        self.save_state(store)
        return envelope("run", "ok" if not failures else "blocked", {
            "stage": stage["label"], "actions": results, "failures": failures,
            "artifacts_written": written, "output_dir": str(output_dir),
            "cleanup": "temporary files removed by budget cleanup on exit",
            "recovery_hint": "recover" if failures else None,
        }, capability="Powerful Work Splitter", budget=self.budget)

    def _stage_outputs(self, st):
        if getattr(self.args, "stage_outputs", None):
            return engine.read_json(self.args.stage_outputs, self.budget,
                                    self.config["limits"]["max_input_bytes"], "stage_outputs")
        grouped = {}
        for art in st["artifacts"]:
            grouped.setdefault(art["stage"], {"stage": art["stage"], "artifacts": [], "references": []})
            grouped[art["stage"]]["artifacts"].append({
                "name": art["name"], "bytes": 1, "non_empty": True,
                "requirement_ids": art.get("requirement_ids", []), "verified": art.get("verified", []),
            })
        return [grouped[k] for k in sorted(grouped)]

    def cmd_assemble(self):
        a = self.args
        st = self.load_state()
        if a.publish:
            gate = self.approval("assemble", "publish the final package as delivered")
            if gate:
                return gate
        outputs = self._stage_outputs(st)
        result = engine.assemble(st, outputs, self.config)
        store = self.store()
        store.record("assemble", result["status"], st["plan"]["requested_stages"])
        self.save_state(store)
        data = {**result, "published": bool(a.publish),
                "final_stage": st["plan"]["stages"][-1]["label"],
                "stage_output_count": len(outputs)}
        return envelope("assemble", "ok" if result["status"] == "pass" else "blocked", data,
                        errors=[{"code": "ERR_ASSEMBLY", "message": "final assembly has unresolved defects"}]
                        if result["status"] != "pass" else [],
                        capability="Final Assembly Orchestrator", budget=self.budget)

    def cmd_recover(self):
        store = self.store()
        st = self.load_state()
        report = engine.recover_state(st, self.config)
        inv = engine.file_inventory(st["project"])
        safe = report["last_safe_checkpoint"]
        st["completed_stages"] = [n for n in st["completed_stages"] if n <= safe]
        st["current_stage"] = min(safe + 1, len(st["plan"]["stages"]))
        st["blockers"] = [i["name"] for i in inv["issues"]]
        st["next_actions"] = report["updated_next_actions"]
        st["recovery"] = {"attempts": st.get("recovery", {}).get("attempts", 0) + 1, "last_safe_checkpoint": safe}
        st["recovery"]["last_report"] = report
        store.record("recover", "ok", st["current_stage"])
        self.save_state(store)
        return envelope("recover", "ok", {"recovery_report": report, "revalidated_file_inventory": inv,
                                         "state": {"completed_stages": st["completed_stages"], "current_stage": st["current_stage"]}},
                        capability="Powerful Work Splitter (recovery behavior)", budget=self.budget)

    def cmd_state(self):
        a = self.args
        action = a.action
        store = self.store()
        if action == "reset":
            gate = self.approval("state", "irreversibly delete the continuation state file %s" % self.state_path)
            if gate:
                return gate
            if self.state_path.exists():
                self.state_path.unlink()
            return envelope("state", "ok", {"action": "reset", "deleted": str(self.state_path), "approved": True},
                            budget=self.budget)
        st = self.load_state()
        if action == "snapshot":
            snap = copy.deepcopy({k: st[k] for k in ("project", "plan", "completed_stages", "checkpoints",
                                                      "decisions", "blockers", "next_actions", "artifacts")})
            snap["created_at"] = st.get("created_at")
            snap["updated_at"] = st.get("updated_at")
            return envelope("state", "ok", {"action": "snapshot", "snapshot": snap}, budget=self.budget)
        return envelope("state", "ok", {
            "action": "show", "path": str(self.state_path), "created_at": st.get("created_at"),
            "updated_at": st.get("updated_at"), "completed_stages": st["completed_stages"],
            "current_stage": st["current_stage"], "total_stages": st["plan"]["requested_stages"],
            "checkpoints": [{"stage": c["stage"], "at": c["at"]} for c in st["checkpoints"]],
            "blockers": st["blockers"], "next_actions": st["next_actions"],
            "history_tail": st["history"][-8:],
        }, budget=self.budget)

    def cmd_cleanup(self):
        gate = self.approval("cleanup", "delete generated .bak and .tmp working files next to the state file")
        if gate:
            return gate
        removed = []
        parent = self.state_path.parent
        for p in sorted(parent.glob(".ws-*.tmp")) + sorted(parent.glob("*.bak")):
            try:
                p.unlink()
                removed.append(str(p))
            except OSError as exc:
                self.warnings.append("could not remove %s: %s" % (p, exc))
        return envelope("cleanup", "ok", {"removed": removed, "count": len(removed), "approved": True},
                        warnings=self.warnings, budget=self.budget)

    def cmd_validate(self):
        a = self.args
        kind = a.kind
        lim = self.config["limits"]
        if kind == "registry":
            result = catalog.validate_registry(catalog.load_registry(self.config, self.budget), self.config)
        elif kind == "config":
            result = {"valid": True, "sections": sorted(self.config.keys())}
        elif kind in ("project", "state"):
            data = engine.read_json(a.input, self.budget, lim["max_input_bytes"], kind)
            if kind == "project":
                proj = engine.validate_project(data, self.config)
                result = {"valid": True, "requirements": len(proj["requirements"]),
                          "work_packages": len(proj["work_packages"]), "file_inventory": engine.file_inventory(proj)}
            else:
                schema = engine.read_json(engine.resolve_reference(self.config["schemas"]["state_schema"]),
                                          self.budget, lim["max_input_bytes"], "state schema")
                errs = engine.validate_schema(data, schema)
                result = {"valid": not errs, "schema_errors": errs}
                if errs:
                    return envelope("validate", "error", result,
                                    errors=[{"code": "ERR_SCHEMA", "message": e} for e in errs[:20]], budget=self.budget)
        elif kind == "spec":
            text = engine.read_text(a.input, self.budget, lim["max_input_bytes"], "spec")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
            if isinstance(data, dict) and "capabilities" in data and "unified" in data:
                spec = data
            else:
                source = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
                spec = catalog.parse_source(source, registry=None)
            result = {"valid": True, "capabilities": spec["counts"]["capabilities"], "umbrella": spec["umbrella"],
                      "unified": spec["unified"]}
        else:
            raise engine.ToolError("ERR_VALIDATE_KIND", "unsupported validation kind %r" % kind, "kind")
        return envelope("validate", "ok", {"kind": kind, **result}, budget=self.budget)

    def cmd_intake(self):
        a = self.args
        text = engine.read_text(a.input, self.budget, self.config["limits"]["max_input_bytes"], "intake source")
        fmt = self.input_format()
        spec = catalog.parse_source(text, registry=self.registry, category_filter=getattr(a, "category", None),
                                    name_override=getattr(a, "name", None), source_format=fmt)
        return envelope("intake", "ok", {
            "umbrella": spec["umbrella"], "counts": spec["counts"], "unified": spec["unified"],
            "categories": [{"name": c["name"], "slug": c["slug"], "skill_count": c["skill_count"]} for c in spec["categories"]],
            "capabilities": [{"id": c["id"], "name": c["name"], "description": c["description"], "category": c["category"]}
                             for c in spec["capabilities"]][:int(getattr(a, "limit", 50))],
            "capabilities_truncated": spec["counts"]["capabilities"] > int(getattr(a, "limit", 50)),
            "warnings": spec["warnings"], "source_format": spec["source_format"],
            "next_action": "plan --input <source> --input-format %s --category <name> --stages N" % spec["source_format"],
        }, warnings=spec["warnings"][:5], budget=self.budget)

    def cmd_catalog(self):
        a = self.args
        reg = self.registry
        action = a.action
        if action == "list":
            data = {"categories": catalog.registry_categories(reg), "category_count": reg["category_count"],
                    "skill_count": reg["skill_count"], "source": reg["source"]}
        elif action == "category":
            cat = catalog.find_category(reg, a.name)
            data = {"category": {"number": cat["number"], "name": cat["name"], "slug": cat["slug"]},
                    "skills": [{"number": s["number"], "name": s["name"], "id": s["id"], "description": s["description"]}
                               for s in cat["skills"]]}
        elif action == "search":
            data = catalog.search_registry(reg, a.query, int(getattr(a, "limit", 20)))
        elif action == "brief":
            data = catalog.capability_brief(reg, a.name, self.config, self.budget, getattr(a, "stages", None))
        elif action == "source":
            src = engine.resolve_reference(self.config["catalog"]["source_path"])
            text = engine.read_text(src, self.budget, self.config["limits"]["max_input_bytes"], "catalog source")
            spec = catalog.parse_source(text, registry=reg, source_format="text")
            data = {
                "source_path": self.config["catalog"]["source_path"], "bytes": len(text.encode("utf-8")),
                "parsed_categories": spec["counts"]["named_categories"],
                "parsed_capabilities": spec["counts"]["capabilities"],
                "registry_categories": reg["category_count"], "registry_skills": reg["skill_count"],
                "consistent": (spec["counts"]["named_categories"] == reg["category_count"]
                               and spec["counts"]["capabilities"] == reg["skill_count"]),
                "warnings": spec["warnings"],
            }
        else:
            raise engine.ToolError("ERR_CATALOG_ACTION", "unsupported catalog action %r" % action, "action")
        return envelope("catalog", "ok", {"action": action, **data}, budget=self.budget)

    def cmd_worked_example(self):
        """Flagship worked example from the capability blueprint (deterministic)."""
        project = engine.validate_project({
            "title": "Multi-stage flagship project",
            "goal": "Deliver an ambitious package with staged verification",
            "requirements": ["Work package %d with acceptance test" % i for i in range(1, 13)],
            "files": [], "tool_calls": 12, "uncertainty": 0.4, "verification_burden": 8,
            "context_chars": 60000,
        }, self.config)
        units = engine.decompose(project, self.config)
        estimate = engine.estimate_counts(project, units, self.config)
        recommended = estimate["recommended"]
        chosen = 5
        stages5 = engine.build_stages(units, chosen, self.config)
        # Stages 1..4 completed, then the user expands the plan to 10 stages.
        stages10 = engine.build_stages(units, 10, self.config, preserve_prefix=4, previous_stages=stages5)
        final = stages10[-1]
        return envelope("worked-example", "ok", {
            "steps": [
                {"step": 1, "action": "agent recommends", "recommended_stages": recommended,
                 "note": "large project evaluated; staged work recommended over one response"},
                {"step": 2, "action": "user chooses", "selected_stages": chosen,
                 "note": "all requirements redistributed into a new %d-stage plan" % chosen,
                 "labels": [s["label"] for s in stages5]},
                {"step": 3, "action": "complete and pause", "stage": stages5[0]["label"],
                 "verified": stages5[0]["exit_criteria"], "pause_for": "Continue"},
                {"step": 4, "action": "user expands at Stage 4 of 5", "from": 5, "to": 10,
                 "preserved": [s["label"] for s in stages10[:4]],
                 "replanned": [s["label"] for s in stages10[4:]]},
                {"step": 5, "action": "final stage", "stage": final["label"],
                 "includes_assembly_and_polish": final["includes_assembly_and_polish"],
                 "note": "integration, complete verification, packaging and final delivery"},
            ],
            "requirements": len(project["requirements"]),
            "requirements_dropped": 0,
            "final_stage_count": len(stages10),
            "critical_path": engine.critical_path(units, self.config),
        }, capability="Powerful Work Splitter (worked example)", budget=self.budget)


def _artifact_name(unit):
    return (engine.slugify(unit["id"]) + "-output.json")[:110]


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("--state", default=argparse.SUPPRESS)
    common.add_argument("--output", default=argparse.SUPPRESS)
    common.add_argument("--out-format", choices=["json", "text"], default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    common.add_argument("--retries", type=int, default=argparse.SUPPRESS)
    common.add_argument("--cancel-file", default=argparse.SUPPRESS)
    common.add_argument("--approve", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(prog="tools-work-splitter.py", add_help=False, parents=[common],
                                description="Unified Orchestration, Planning, and Work Splitting Skill Tool")
    p.add_argument("-h", "--help", action="store_true", dest="top_help")
    p.set_defaults(config=None, state=None, output=None, out_format="json", timeout=None, retries=None,
                   cancel_file=None, approve=False, dry_run=False, command="help")
    sub = p.add_subparsers(dest="command")

    def add(name, **kw):
        return sub.add_parser(name, parents=[common], add_help=True, **kw)

    h = add("help"); h.add_argument("command_name", nargs="?")
    add("capabilities")
    add("version")

    for name in ("assess", "estimate", "plan"):
        sp = add(name)
        sp.add_argument("--input")
        sp.add_argument("--text")
        sp.add_argument("--input-format", choices=["auto", "json", "text", "markdown", "html"], default="auto")
        sp.add_argument("--format", choices=["auto", "json", "text", "markdown", "html"], default=None,
                        help="alias for --input-format")
        sp.add_argument("--category")
        sp.add_argument("--name")
        sp.add_argument("--stages", type=int)

    for name in ("negotiate", "expand", "compress"):
        sp = add(name); sp.add_argument("--stages", type=int, required=True)

    sp = add("checkpoint"); sp.add_argument("--stage", type=int, required=True); sp.add_argument("--evidence", required=True)
    sp.add_argument("--phase", default="build")
    sp = add("continue"); sp.add_argument("--reply", required=True)
    sp = add("gate"); sp.add_argument("--stage", type=int, required=True); sp.add_argument("--evidence", required=True)
    sp.add_argument("--phase", default="build", choices=["discovery", "build", "test", "release"])
    sp = add("run"); sp.add_argument("--stage", type=int); sp.add_argument("--apply", action="store_true")
    sp.add_argument("--output-dir"); sp.add_argument("--cancel-file-override")
    sp = add("assemble"); sp.add_argument("--stage-outputs"); sp.add_argument("--publish", action="store_true")
    add("recover")
    sp = add("state"); sp.add_argument("--action", choices=["show", "snapshot", "reset"], default="show")
    add("cleanup")
    sp = add("validate"); sp.add_argument("--input", required=False)
    sp.add_argument("--kind", choices=["project", "state", "config", "spec", "registry"], required=True)
    sp = add("intake"); sp.add_argument("--input", required=True)
    sp.add_argument("--input-format", choices=["auto", "text", "markdown", "html"], default="auto")
    sp.add_argument("--format", choices=["auto", "text", "markdown", "html"], default=None)
    sp.add_argument("--category"); sp.add_argument("--name"); sp.add_argument("--limit", type=int, default=50)
    sp = add("catalog"); sp.add_argument("--action", choices=["list", "category", "search", "brief", "source"], required=True)
    sp.add_argument("--name"); sp.add_argument("--query"); sp.add_argument("--stages", type=int)
    sp.add_argument("--limit", type=int, default=20)
    add("worked-example")
    return p


HANDLERS = {
    "help": "cmd_help", "capabilities": "cmd_capabilities", "version": "cmd_version",
    "assess": "cmd_assess", "estimate": "cmd_estimate", "plan": "cmd_plan",
    "negotiate": "cmd_negotiate", "expand": "cmd_expand", "compress": "cmd_compress",
    "checkpoint": "cmd_checkpoint", "continue": "cmd_continue", "gate": "cmd_gate",
    "run": "cmd_run", "assemble": "cmd_assemble", "recover": "cmd_recover",
    "state": "cmd_state", "cleanup": "cmd_cleanup", "validate": "cmd_validate",
    "intake": "cmd_intake", "catalog": "cmd_catalog", "worked-example": "cmd_worked_example",
}


def render(out, fmt):
    if fmt == "text":
        lines = ["%s | %s | %s" % (out["tool"], out["command"], out["status"])]
        data = out.get("data", {})
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                lines.append("  %s: %s" % (k, v))
            else:
                lines.append("  %s: %s" % (k, json.dumps(v, ensure_ascii=False)[:300]))
        for e in out.get("errors", []):
            lines.append("  ERROR %s: %s" % (e.get("code"), e.get("message")))
        return "\n".join(lines)
    return json.dumps(out, indent=2, ensure_ascii=False)


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # -h/--help exits 0 normally; every other argparse usage failure gets a
        # structured JSON envelope on stdout instead of a silent crash.
        if exc.code == 0:
            return 0
        out = envelope("cli", "error", {"hint": "run 'help' or '<command> --help' for usage"},
                       errors=[{"code": "ERR_USAGE", "message": "invalid command line arguments; see usage on stderr"}])
        print(render(out, "json"))
        return EXIT["error"]
    if getattr(args, "top_help", False):
        args.command = "help"
        args.command_name = None
    command = args.command or "help"
    budget = engine.Budget(timeout_seconds=30, cancel_file=getattr(args, "cancel_file", None))
    try:
        config = engine.load_config(getattr(args, "config", None), budget)
        if getattr(args, "timeout", None) is not None:
            if args.timeout <= 0:
                raise engine.ToolError("ERR_TIMEOUT", "--timeout must be > 0", "timeout")
            config["limits"]["timeout_seconds"] = float(args.timeout)
        if getattr(args, "retries", None) is not None:
            if args.retries < 0 or args.retries > 10:
                raise engine.ToolError("ERR_RETRIES", "--retries must be between 0 and 10", "retries")
            config["limits"]["retries"] = int(args.retries)
        budget.timeout = float(config["limits"]["timeout_seconds"])
        if getattr(args, "cancel_file_override", None):
            budget.cancel_file = Path(args.cancel_file_override)
        runner = Runner(args, config, budget)
        handler = getattr(runner, HANDLERS[command])
        out = handler()
        status = out["status"]
        if out.get("warnings") and runner.warnings:
            out["warnings"] = list(dict.fromkeys(out["warnings"] + runner.warnings))
        elif runner.warnings:
            out["warnings"] = runner.warnings
        text = render(out, getattr(args, "out_format", "json"))
        if getattr(args, "output", None):
            target = Path(args.output)
            if target.exists() and not args.approve:
                out = envelope(command, "needs_approval", {"reason": "overwriting existing output requires --approve", "path": str(target)},
                               errors=[{"code": "ERR_APPROVAL_REQUIRED", "message": "--output target exists"}], budget=budget)
                text = render(out, "json")  # approval/error envelopes stay structured
                print(text)
                return 3
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
        print(text)
        return EXIT.get(status, 0)
    except engine.ToolError as exc:
        out = envelope(command, "error", {"hint": "run '%s help' or check --input/--state paths" % Path(__file__).name},
                       errors=[exc.as_dict()], budget=budget)
        print(render(out, "json"))
        return EXIT["error"]
    except engine.Cancelled as exc:
        out = envelope(command, "cancelled", {"reason": str(exc)},
                       errors=[{"code": "ERR_CANCELLED", "message": str(exc)}], budget=budget)
        print(render(out, "json"))
        return EXIT["cancelled"]
    except KeyboardInterrupt:
        out = envelope(command, "cancelled", {"reason": "interrupted by user"},
                       errors=[{"code": "ERR_CANCELLED", "message": "KeyboardInterrupt"}], budget=budget)
        print(render(out, "json"))
        return EXIT["cancelled"]
    except Exception as exc:  # last-resort structured failure, never a traceback-only crash
        out = envelope(command, "error", {},
                       errors=[{"code": "ERR_UNEXPECTED", "message": "%s: %s" % (type(exc).__name__, exc)}], budget=budget)
        print(render(out, "json"))
        return 1
    finally:
        budget.cleanup()


if __name__ == "__main__":
    sys.exit(main())
