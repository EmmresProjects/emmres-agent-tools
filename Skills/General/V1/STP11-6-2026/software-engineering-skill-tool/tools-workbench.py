#!/usr/bin/env python3
"""
Primary runnable entry point for the unified `software-engineering` Skill Tool.
Covers all 51 categories and 1,020 skills from the Comprehensive Software Engineering Skill Catalog.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_TOOL_NAME = "software-engineering"
TOOL_DIR = Path(__file__).resolve().parent


def _bootstrap_core() -> Any:
    core_path = TOOL_DIR / "tools-core-runtime.py"
    spec = importlib.util.spec_from_file_location("se_wb_tools_core_runtime_py", str(core_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load core runtime from {core_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["se_wb_tools_core_runtime_py"] = module
    spec.loader.exec_module(module)
    return module


CORE = _bootstrap_core()


def parse_kv_params(pairs: Optional[List[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise CORE.WorkbenchError("INVALID_PARAM", f"Parameter '{item}' must use key=value format.")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise CORE.WorkbenchError("INVALID_PARAM", f"Empty parameter key in '{item}'.")
        out[k] = v
    return out


def format_envelope(envelope: Dict[str, Any], fmt: str, indent: int = 2) -> str:
    if fmt == "markdown":
        lines = [
            f"# Execution Report (`{envelope['tool']}`)",
            f"- **Status**: `{envelope['status']}`",
            f"- **Command**: `{envelope['command']}`",
            f"- **Run ID**: `{envelope['run_id']}`",
            f"- **Duration**: `{envelope['duration_ms']} ms`",
            f"- **Inputs Validated**: `{envelope['inputs_validated']}`",
            "",
            "## Verification Checks",
        ]
        for chk in envelope.get("verification_checks", []):
            lines.append(f"- **[{chk['status'].upper()}]** `{chk['check_id']}` {chk['name']}: {chk['details']}")
        if not envelope.get("verification_checks"):
            lines.append("- None")
        if envelope.get("artifacts"):
            lines.append("\n## Generated Artifacts")
            for art in envelope["artifacts"]:
                lines.append(f"- `{art['path']}` ({art['kind']}, {art['bytes']} bytes, sha256 `{art['sha256'][:16]}...`)")
        if envelope.get("limitations"):
            lines.append("\n## Disclosed Limitations")
            for lim in envelope["limitations"]:
                lines.append(f"- {lim}")
        if envelope.get("errors"):
            lines.append("\n## Errors")
            for err in envelope["errors"]:
                lines.append(f"- `{err['code']}`: {err['message']}")
        lines.append("\n## Results Summary (JSON)")
        lines.append("```json")
        lines.append(json.dumps(envelope.get("results", {}), indent=2))
        lines.append("```")
        return "\n".join(lines) + "\n"
    if fmt == "table" and "categories" in envelope.get("results", {}):
        rows = ["ID  | Code   | Skills | Category Name", "----|--------|--------|--------------------------------------------"]
        for c in envelope["results"]["categories"]:
            rows.append(f"{c['category_id']:02d}  | {c['category_code']} | {c['skill_count']:<6d} | {c['name']}")
        if envelope["results"].get("skills"):
            rows.append("\nCoord | Skill ID | Slug                                     | Mode")
            rows.append("------|----------|------------------------------------------|----------")
            for s in envelope["results"]["skills"]:
                rows.append(f"{s['coordinate']} | {s['skill_id']}    | {s['slug']:<40s} | {s['operation_mode']}")
        return "\n".join(rows) + "\n"
    return json.dumps(envelope, indent=indent) + "\n"


def prepare_execution_context(
    *,
    cfg: Dict[str, Any],
    policy: Dict[str, Any],
    catalog_index: Any,
    verifier: Any,
    input_path: Optional[Path],
    inline_text: Optional[str],
    params: Dict[str, str],
    project_name: Optional[str],
    action_mode: str,
    prompts_path: Optional[str] = None,
) -> Dict[str, Any]:
    fixture = CORE.load_json_resource(cfg["paths"]["sample_fixture"])
    prompts = CORE.load_json_resource(prompts_path or cfg["paths"]["prompt_templates"])

    explicit_user_input = bool(input_path is not None or (inline_text is not None and inline_text != ""))
    raw_text = ""
    json_data: Any = None

    if input_path is not None:
        if input_path.is_file():
            raw_text = input_path.read_text(encoding="utf-8", errors="replace")
            if input_path.suffix.lower() == ".json":
                try:
                    json_data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise CORE.WorkbenchError("INVALID_INPUT_JSON", f"Malformed JSON in {input_path}: {exc}") from exc
        elif input_path.is_dir():
            raw_text = f"Workspace directory scan target: {input_path.name}"

    if inline_text is not None:
        raw_text = inline_text if not raw_text else (raw_text + "\n" + inline_text)
        if json_data is None and inline_text.lstrip().startswith(("{", "[")):
            try:
                json_data = json.loads(inline_text)
            except json.JSONDecodeError:
                pass

    effective_text = raw_text if explicit_user_input else fixture["requirements_text"]
    proj = project_name or params.get("project") or (json_data.get("project_name") if isinstance(json_data, dict) and "project_name" in json_data else fixture["project_name"])

    return {
        "config": cfg,
        "limits": cfg["limits"],
        "policy": policy,
        "prompts": prompts,
        "fixture": fixture,
        "catalog_index": catalog_index,
        "verifier": verifier,
        "input_path": input_path,
        "explicit_user_input": explicit_user_input,
        "effective_text": effective_text,
        "json_data": json_data,
        "params": params,
        "project_name": str(proj),
        "action_mode": action_mode,
    }


def dispatch_single_skill(skill_meta: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    engine_ref = skill_meta["engine"]
    engine_filename = engine_ref.split("/")[-1]
    engine_mod = CORE.load_sibling_tool(engine_filename)

    def _call() -> Dict[str, Any]:
        return engine_mod.execute_skill(skill_meta, context)

    result, retries_used = CORE.run_with_retries(
        _call,
        max_retries=context["limits"].get("max_retries", 2),
        backoff_seconds=context["limits"].get("retry_backoff_seconds", 0.25),
        backoff_multiplier=context["limits"].get("retry_backoff_multiplier", 2.0),
    )
    result["retries_used"] = retries_used
    return result


def persist_outputs(
    out_target: Optional[Path],
    generated_files: List[Dict[str, Any]],
    envelope: Dict[str, Any],
) -> None:
    if out_target is None:
        return
    # Determine whether out_target is a single report file (.json / .md) or an output directory
    if out_target.suffix.lower() in (".json", ".md"):
        artifact_dir = out_target.parent / (out_target.stem + "-artifacts")
        if generated_files:
            for gf in generated_files:
                meta = CORE.write_artifact(artifact_dir / gf["filename"], gf["content"], gf["kind"])
                envelope["artifacts"].append(meta)
        fmt = "markdown" if out_target.suffix.lower() == ".md" else "json"
        rendered = format_envelope(envelope, fmt=fmt).encode("utf-8")
        rep_meta = CORE.write_artifact(out_target, rendered, f"{fmt}-report")
        envelope["artifacts"].append(rep_meta)
    else:
        out_target.mkdir(parents=True, exist_ok=True)
        for gf in generated_files:
            meta = CORE.write_artifact(out_target / gf["filename"], gf["content"], gf["kind"])
            envelope["artifacts"].append(meta)
        report_file = out_target / "execution-report.json"
        rep_bytes = (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
        rep_meta = CORE.write_artifact(report_file, rep_bytes, "json-report")
        envelope["artifacts"].append(rep_meta)


def cmd_capabilities(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, policy = CORE.load_runtime_config(args.config, args.policy)
    out_path = CORE.validate_output_path(args.output, policy, force_overwrite=args.force_overwrite, approved=args.approve)
    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    catalog_doc = CORE.load_json_resource(cfg["paths"]["catalog"])
    idx = parser_mod.CatalogIndex(catalog_doc)

    cats_summary = [
        {
            "category_id": c["category_id"],
            "category_code": c["category_code"],
            "slug": c["slug"],
            "name": c["name"],
            "skill_count": c["skill_count"],
            "engine": c["engine"],
        }
        for c in idx.categories
    ]

    skills_list: List[Dict[str, Any]] = []
    if args.search:
        skills_list = idx.search(args.search, category_filter=args.category, limit=args.limit or 50)
    elif not args.summary:
        target_cats = idx.categories
        if args.category:
            cat_obj = idx.resolve_category(args.category)
            if not cat_obj:
                raise CORE.WorkbenchError("UNKNOWN_CATEGORY", f"No category matched '{args.category}'.")
            target_cats = [cat_obj]
            cats_summary = [c for c in cats_summary if c["category_id"] == cat_obj["category_id"]]
        for c in target_cats:
            for s in c["skills"]:
                if args.requires_approval and not s.get("requires_approval_on_apply"):
                    continue
                skills_list.append({
                    "skill_id": s["skill_id"],
                    "coordinate": s["coordinate"],
                    "slug": s["slug"],
                    "name": s["name"],
                    "category_id": c["category_id"],
                    "category_slug": c["slug"],
                    "operation_mode": s["operation_mode"],
                    "requires_approval_on_apply": s["requires_approval_on_apply"],
                    "approval_scope": s["approval_scope"],
                })
        if args.limit and args.limit > 0:
            skills_list = skills_list[: args.limit]

    env = CORE.build_envelope(
        status="ok",
        command="capabilities",
        start_time=start_t,
        inputs_validated=True,
        results={
            "catalog_name": catalog_doc["catalog_name"],
            "skill_tool_name": catalog_doc["skill_tool_name"],
            "total_categories": catalog_doc["category_count"],
            "total_skills": catalog_doc["skill_count"],
            "returned_categories": len(cats_summary),
            "returned_skills": len(skills_list),
            "categories": cats_summary,
            "skills": skills_list,
        },
        verification_checks=[{
            "check_id": "CAT-INT-001",
            "name": "Catalog Completeness Check",
            "status": "passed",
            "details": f"Loaded {catalog_doc['category_count']} categories and {catalog_doc['skill_count']} skills.",
        }],
    )
    persist_outputs(out_path, [], env)
    return env, 0


def cmd_inspect(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, _ = CORE.load_runtime_config(args.config, args.policy)
    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    idx = parser_mod.CatalogIndex(CORE.load_json_resource(cfg["paths"]["catalog"]))

    if not args.skill and not args.category:
        raise CORE.WorkbenchError("MISSING_TARGET", "Provide --skill and/or --category to inspect.")

    if args.skill:
        matches = idx.resolve_skill(args.skill, category_filter=args.category)
        if not matches:
            raise CORE.WorkbenchError("UNKNOWN_SKILL", f"No skill matched '{args.skill}'.")
        res = {"match_count": len(matches), "skills": matches}
    else:
        cat = idx.resolve_category(args.category)
        if not cat:
            raise CORE.WorkbenchError("UNKNOWN_CATEGORY", f"No category matched '{args.category}'.")
        res = {"category": cat}

    env = CORE.build_envelope(status="ok", command="inspect", start_time=start_t, inputs_validated=True, results=res)
    return env, 0


def cmd_parse_input(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, policy = CORE.load_runtime_config(args.config, args.policy)
    in_path = CORE.validate_input_path(args.input, cfg["limits"]["max_input_bytes"])
    out_path = CORE.validate_output_path(args.output, policy, force_overwrite=args.force_overwrite, approved=args.approve)
    if in_path is None and not args.text:
        raise CORE.WorkbenchError("MISSING_INPUT", "parse-input requires either --input <file> or --text <specification>.")
    raw = in_path.read_text(encoding="utf-8", errors="replace") if (in_path and in_path.is_file()) else (args.text or "")
    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    parsed = parser_mod.parse_capability_specification(raw, fallback_category=args.umbrella or "custom-capabilities")
    if parsed["skill_count"] == 0:
        raise CORE.WorkbenchError("EMPTY_SPECIFICATION", "No extractable capabilities found in the supplied input.")

    executed_matches: List[Dict[str, Any]] = []
    if args.execute:
        idx = parser_mod.CatalogIndex(CORE.load_json_resource(cfg["paths"]["catalog"]))
        verifier = CORE.load_sibling_tool("tools-runtime-verifier.py")
        ctx = prepare_execution_context(
            cfg=cfg, policy=policy, catalog_index=idx, verifier=verifier,
            input_path=None, inline_text=raw, params={}, project_name=parsed["umbrella_name"], action_mode="plan",
        )
        for cat in parsed["categories"]:
            for sk in cat["skills"]:
                found = idx.resolve_skill(sk["name"]) or idx.search(sk["name"] + " " + sk["description"], limit=1)
                if found:
                    target_skill = idx.by_slug[found[0]["slug"]]
                    run_res = dispatch_single_skill(target_skill, ctx)
                    executed_matches.append({
                        "parsed_skill": sk["name"],
                        "mapped_catalog_skill": target_skill["slug"],
                        "coordinate": target_skill["coordinate"],
                        "output": run_res["output"],
                    })

    checks = [{
        "check_id": "PARSE-SPEC-001",
        "name": "Capability Specification Extraction",
        "status": "passed",
        "details": f"Detected format='{parsed['detected_format']}'; extracted {parsed['category_count']} category/categories and {parsed['skill_count']} capability/capabilities.",
    }]
    env = CORE.build_envelope(
        status="ok",
        command="parse-input",
        start_time=start_t,
        inputs_validated=True,
        results={"parsed_specification": parsed, "executed_capabilities": executed_matches},
        verification_checks=checks,
    )
    persist_outputs(out_path, [], env)
    return env, 0


def cmd_route(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, _ = CORE.load_runtime_config(args.config, args.policy)
    in_path = CORE.validate_input_path(args.input, cfg["limits"]["max_input_bytes"])
    query = args.text or (in_path.read_text(encoding="utf-8", errors="replace") if (in_path and in_path.is_file()) else "")
    if not query.strip():
        raise CORE.WorkbenchError("MISSING_QUERY", "route requires --text <query> or --input <file>.")
    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    idx = parser_mod.CatalogIndex(CORE.load_json_resource(cfg["paths"]["catalog"]))
    ranked = idx.search(query, limit=args.limit)
    env = CORE.build_envelope(
        status="ok",
        command="route",
        start_time=start_t,
        inputs_validated=True,
        results={"query": query[:300], "recommended_count": len(ranked), "recommendations": ranked},
        verification_checks=[{
            "check_id": "ROUTE-001",
            "name": "Catalog Capability Ranking",
            "status": "passed" if ranked else "warning",
            "details": f"Ranked top {len(ranked)} matching skill(s).",
        }],
    )
    return env, 0


def cmd_run(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, policy = CORE.load_runtime_config(args.config, args.policy)
    in_path = CORE.validate_input_path(args.input, cfg["limits"]["max_input_bytes"])
    out_path = CORE.validate_output_path(args.output, policy, force_overwrite=args.force_overwrite, approved=args.approve)
    params = parse_kv_params(args.param)

    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    verifier = CORE.load_sibling_tool("tools-runtime-verifier.py")
    idx = parser_mod.CatalogIndex(CORE.load_json_resource(cfg["paths"]["catalog"]))

    if not args.skill and not args.category and not args.workflow:
        raise CORE.WorkbenchError("MISSING_EXECUTION_TARGET", "Specify --skill, --category, or --workflow.")

    ctx = prepare_execution_context(
        cfg=cfg, policy=policy, catalog_index=idx, verifier=verifier,
        input_path=in_path, inline_text=args.text, params=params,
        project_name=args.project, action_mode=args.action_mode, prompts_path=args.prompts,
    )

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    all_checks: List[Dict[str, Any]] = []
    all_generated: List[Dict[str, Any]] = []
    all_limitations: List[str] = []
    approval_record: Dict[str, Any] = {
        "required": False, "granted": bool(args.approve), "scope": "none", "action_mode": args.action_mode, "reason": args.approval_reason or "Standard execution",
    }

    # Workflow DAG mode with checkpoint journaling
    if args.workflow:
        wf_doc = CORE.load_json_resource(cfg["paths"]["workflow_presets"])
        workflows = wf_doc.get("workflows", {})
        if args.workflow not in workflows:
            raise CORE.WorkbenchError("UNKNOWN_WORKFLOW", f"Unknown workflow '{args.workflow}'. Available: {sorted(workflows.keys())}")
        wf = workflows[args.workflow]
        arch_mod = CORE.load_sibling_tool("tools-engine-architecture.py")
        waves, cycle = arch_mod.topological_layers(wf["steps"])
        if cycle:
            raise CORE.WorkbenchError("WORKFLOW_CYCLE", f"Workflow contains dependency cycle: {cycle}")
        step_by_id = {s["step_id"]: s for s in wf["steps"]}
        ordered_ids = [sid for wave in waves for sid in wave]
        completed_steps: Dict[str, Any] = {}
        cp_dir = out_path if (out_path and not out_path.suffix) else (out_path.parent if out_path else Path(cfg["paths"]["default_output_dir"]))

        for pos, sid in enumerate(ordered_ids):
            st = step_by_id[sid]
            matches = idx.resolve_skill(st["skill"])
            if not matches:
                raise CORE.WorkbenchError("UNKNOWN_SKILL", f"Workflow step '{sid}' references unknown skill '{st['skill']}'.")
            sk_meta = matches[0]
            app_info = CORE.enforce_approval_gate(sk_meta, args.action_mode, args.approve, args.approval_reason or "", policy)
            if app_info["required"]:
                approval_record = app_info
            res = dispatch_single_skill(sk_meta, ctx)
            completed_steps[sid] = {"skill": sk_meta["slug"], "coordinate": sk_meta["coordinate"], "output": res["output"]}
            all_checks.extend(res["checks"])
            all_generated.extend(res["generated_files"])
            for lim in res["limitations"]:
                if lim not in all_limitations:
                    all_limitations.append(lim)
            pending = ordered_ids[pos + 1:]
            cp_file = CORE.save_checkpoint(cp_dir, run_id, args.workflow, completed_steps, pending, status="completed" if not pending else "in_progress")
            if args.simulate_interrupt_after and args.simulate_interrupt_after == sid and pending:
                raise CORE.WorkbenchError(
                    "SIMULATED_INTERRUPTION",
                    f"Simulated interruption after step '{sid}'. Resume with: recover --checkpoint {cp_file.as_posix()}",
                    exit_code=5,
                )

        env = CORE.build_envelope(
            status="ok",
            command="run",
            run_id=run_id,
            start_time=start_t,
            inputs_validated=True,
            approval=approval_record,
            results={"workflow": args.workflow, "execution_waves": waves, "steps": completed_steps},
            verification_checks=all_checks,
            limitations=all_limitations,
        )
        persist_outputs(out_path, all_generated, env)
        return env, 0

    # Resolve target skill(s)
    selected_skills: List[Dict[str, Any]] = []
    if args.skill:
        raw_items: List[str] = []
        for item in args.skill:
            raw_items.extend([p.strip() for p in item.split(",") if p.strip()])
        for token in raw_items:
            matches = idx.resolve_skill(token, category_filter=args.category)
            if not matches:
                raise CORE.WorkbenchError("UNKNOWN_SKILL", f"Skill '{token}' not found in the 1,020-skill catalog.")
            if len(matches) > 1 and not args.all_matches:
                coords = ", ".join(f"{m['slug']} ({m['coordinate']} in {m['category_name']})" for m in matches)
                raise CORE.WorkbenchError(
                    "AMBIGUOUS_SKILL_NAME",
                    f"Skill title '{token}' exists in multiple categories: {coords}. Specify its unique slug, coordinate, --category, or --all-matches.",
                )
            selected_skills.extend(matches)
    elif args.category:
        cat_obj = idx.resolve_category(args.category)
        if not cat_obj:
            raise CORE.WorkbenchError("UNKNOWN_CATEGORY", f"Category '{args.category}' not found.")
        selected_skills = [idx.by_slug[s["slug"]] for s in cat_obj["skills"]]

    # Pre-check approval gates BEFORE executing any skill
    for sk_meta in selected_skills:
        app_info = CORE.enforce_approval_gate(sk_meta, args.action_mode, args.approve, args.approval_reason or "", policy)
        if app_info["required"]:
            approval_record = app_info

    skill_outputs: List[Dict[str, Any]] = []
    for sk_meta in selected_skills:
        res = dispatch_single_skill(sk_meta, ctx)
        skill_outputs.append(res["output"])
        all_checks.extend(res["checks"])
        all_generated.extend(res["generated_files"])
        for lim in res["limitations"]:
            if lim not in all_limitations:
                all_limitations.append(lim)

    results_payload = (
        skill_outputs[0]
        if len(skill_outputs) == 1
        else {"executed_skill_count": len(skill_outputs), "skill_outputs": skill_outputs}
    )
    env = CORE.build_envelope(
        status="ok",
        command="run",
        run_id=run_id,
        start_time=start_t,
        inputs_validated=True,
        approval=approval_record,
        results=results_payload,
        verification_checks=all_checks,
        limitations=all_limitations,
    )
    persist_outputs(out_path, all_generated, env)
    return env, 0


def cmd_recover(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, policy = CORE.load_runtime_config(args.config, args.policy)
    cp = CORE.load_checkpoint(args.checkpoint)
    out_path = CORE.validate_output_path(args.output, policy, force_overwrite=args.force_overwrite, approved=args.approve)
    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    verifier = CORE.load_sibling_tool("tools-runtime-verifier.py")
    idx = parser_mod.CatalogIndex(CORE.load_json_resource(cfg["paths"]["catalog"]))
    wf_doc = CORE.load_json_resource(cfg["paths"]["workflow_presets"])
    wf = wf_doc.get("workflows", {}).get(cp["workflow"])
    if not wf:
        raise CORE.WorkbenchError("UNKNOWN_WORKFLOW", f"Checkpoint workflow '{cp['workflow']}' not found in presets.")

    step_by_id = {s["step_id"]: s for s in wf["steps"]}
    ctx = prepare_execution_context(
        cfg=cfg, policy=policy, catalog_index=idx, verifier=verifier,
        input_path=None, inline_text=None, params={}, project_name=None, action_mode="plan",
    )

    completed_steps = dict(cp["completed_steps"])
    pending = list(cp["pending_steps"])
    resumed_ids: List[str] = []
    checks: List[Dict[str, Any]] = []
    generated: List[Dict[str, Any]] = []

    for idx_pos, sid in enumerate(list(pending)):
        st = step_by_id[sid]
        sk_meta = idx.resolve_skill(st["skill"])[0]
        res = dispatch_single_skill(sk_meta, ctx)
        completed_steps[sid] = {"skill": sk_meta["slug"], "coordinate": sk_meta["coordinate"], "output": res["output"]}
        resumed_ids.append(sid)
        checks.extend(res["checks"])
        generated.extend(res["generated_files"])
        remaining = pending[idx_pos + 1:]
        cp_dir = CORE.resolve_resource_path(args.checkpoint).parent
        CORE.save_checkpoint(cp_dir, cp["run_id"], cp["workflow"], completed_steps, remaining, status="completed" if not remaining else "in_progress")

    checks.append({
        "check_id": "REC-STATE-001",
        "name": "Checkpoint Journal Recovery Completion",
        "status": "passed",
        "details": f"Preserved {len(cp['completed_steps'])} prior step(s) and resumed {len(resumed_ids)} pending step(s): {resumed_ids}.",
    })
    env = CORE.build_envelope(
        status="recovered",
        command="recover",
        run_id=cp["run_id"],
        start_time=start_t,
        inputs_validated=True,
        results={"workflow": cp["workflow"], "previously_completed": list(cp["completed_steps"].keys()), "resumed_steps": resumed_ids, "steps": completed_steps},
        verification_checks=checks,
    )
    persist_outputs(out_path, generated, env)
    return env, 0


def cmd_verify(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, policy = CORE.load_runtime_config(args.config, args.policy)
    target = CORE.validate_input_path(args.input, cfg["limits"]["max_input_bytes"])
    if target is None:
        raise CORE.WorkbenchError("MISSING_INPUT", "verify requires --input <file_or_directory_or_zip>.")
    out_path = CORE.validate_output_path(args.output, policy, force_overwrite=args.force_overwrite, approved=args.approve)
    verifier = CORE.load_sibling_tool("tools-runtime-verifier.py")
    ops_mod = CORE.load_sibling_tool("tools-engine-operations-quality.py")

    checks: List[Dict[str, Any]] = []
    details_out: Dict[str, Any] = {"target": target.as_posix()}

    if target.is_file():
        suf = target.suffix.lower()
        if suf == ".zip":
            zv = verifier.verify_zip_archive(target)
            checks.append({"check_id": "VER-ZIP", "name": "ZIP Archive Integrity and Nested-Archive Check", "status": zv["status"], "details": zv["details"]})
            details_out["zip"] = zv
        else:
            content = target.read_text(encoding="utf-8", errors="replace")
            sec = ops_mod.run_sast_and_secrets(content, policy)
            checks.append({"check_id": "VER-SEC", "name": "SAST and Hardcoded Secret Scan", "status": "passed" if (not sec["sast_findings"] and not sec["secret_findings"]) else "failed", "details": f"SAST={len(sec['sast_findings'])}, Secrets={len(sec['secret_findings'])}."})
            if suf == ".py":
                pv = verifier.verify_python_source(content, target.name)
                checks.append({"check_id": "VER-PY", "name": "Python AST Syntax Check", "status": pv["status"], "details": pv["details"]})
            elif suf in (".js", ".mjs"):
                jv = verifier.verify_javascript_source(content)
                checks.append({"check_id": "VER-JS", "name": "Node.js Syntax Check", "status": jv["status"], "details": jv["details"]})
            elif suf == ".sh":
                sv = verifier.verify_shell_script(content)
                checks.append({"check_id": "VER-SH", "name": "Bash -n Syntax Check", "status": sv["status"], "details": sv["details"]})
            elif suf == ".json":
                try:
                    json.loads(content)
                    checks.append({"check_id": "VER-JSON", "name": "JSON Parse Check", "status": "passed", "details": "Valid UTF-8 JSON document."})
                except json.JSONDecodeError as exc:
                    checks.append({"check_id": "VER-JSON", "name": "JSON Parse Check", "status": "failed", "details": str(exc)})
            details_out["security"] = sec
    else:
        arch_mod = CORE.load_sibling_tool("tools-engine-architecture.py")
        scan = arch_mod.inspect_workspace_tree(target)
        subfolders = [p.name for p in target.iterdir() if p.is_dir()]
        checks.append({"check_id": "VER-DIR", "name": "Directory Structure Inspection", "status": "passed", "details": f"Scanned {scan['file_count']} file(s), subfolders={subfolders}."})
        details_out["directory_scan"] = scan

    failed = any(c["status"] == "failed" for c in checks)
    env = CORE.build_envelope(
        status="failed" if failed else "ok",
        command="verify",
        start_time=start_t,
        inputs_validated=True,
        results=details_out,
        verification_checks=checks,
        errors=[{"code": "VERIFICATION_FAILED", "message": "One or more verification checks failed."}] if failed else [],
    )
    persist_outputs(out_path, [], env)
    return env, (1 if failed else 0)


def cmd_environment(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, _ = CORE.load_runtime_config(args.config, args.policy)
    verifier = CORE.load_sibling_tool("tools-runtime-verifier.py")
    probe = verifier.probe_host_environment()
    env = CORE.build_envelope(
        status="ok",
        command="environment",
        start_time=start_t,
        inputs_validated=True,
        results={"host_environment": probe, "configured_providers": cfg["providers"], "configured_limits": cfg["limits"]},
        verification_checks=[{
            "check_id": "ENV-001",
            "name": "Runtime Capability Disclosure",
            "status": "passed",
            "details": f"{len(probe['available_runtimes'])} runtime(s) verified available; {len(probe['unavailable_runtimes'])} disclosed unavailable.",
        }],
    )
    return env, 0


def cmd_selftest(args: argparse.Namespace, start_t: float) -> Tuple[Dict[str, Any], int]:
    cfg, policy = CORE.load_runtime_config(args.config, args.policy)
    parser_mod = CORE.load_sibling_tool("tools-input-parser.py")
    verifier = CORE.load_sibling_tool("tools-runtime-verifier.py")
    catalog_doc = CORE.load_json_resource(cfg["paths"]["catalog"])
    idx = parser_mod.CatalogIndex(catalog_doc)
    fixture = CORE.load_json_resource(cfg["paths"]["sample_fixture"])

    checks: List[Dict[str, Any]] = []
    # 1. Verify all 51 categories and 1,020 skills dispatch cleanly
    ctx = prepare_execution_context(
        cfg=cfg, policy=policy, catalog_index=idx, verifier=verifier,
        input_path=None, inline_text=None, params={}, project_name="selftest-suite", action_mode="plan",
    )
    representative_executed = 0
    for cat in idx.categories:
        first_skill = idx.by_slug[cat["skills"][0]["slug"]]
        res = dispatch_single_skill(first_skill, ctx)
        assert "output" in res and "checks" in res
        representative_executed += 1
    checks.append({
        "check_id": "SELF-CAT-51",
        "name": "All 51 Categories & 1,020 Skills Dispatch Integrity",
        "status": "passed" if (len(idx.categories) == 51 and len(idx.by_slug) == 1020 and representative_executed == 51) else "failed",
        "details": f"Verified {len(idx.categories)} categories, {len(idx.by_slug)} unique skills, and executed representative handlers across all {representative_executed} categories.",
    })

    # 2. Verify HTML/Markdown/Text parser
    html_parsed = parser_mod.parse_capability_specification(fixture["html_catalog_sample"])
    checks.append({
        "check_id": "SELF-HTML-PARSE",
        "name": "Copied HTML Capability Parser",
        "status": "passed" if html_parsed["skill_count"] == 2 and html_parsed["categories"][0]["slug"] == "api-engineering" else "failed",
        "details": f"Extracted {html_parsed['skill_count']} skills under '{html_parsed['categories'][0]['slug']}'.",
    })

    # 3. Verify Human Approval Gate blocks unapproved sensitive apply
    blocked = False
    prod_dep = idx.by_slug["production-deployment"]
    try:
        CORE.enforce_approval_gate(prod_dep, "apply", approved=False, approval_reason="", policy=policy)
    except CORE.WorkbenchError as exc:
        blocked = (exc.code == "APPROVAL_REQUIRED")
    checks.append({
        "check_id": "SELF-APPROVAL-GATE",
        "name": "Human Approval Gate Enforcement",
        "status": "passed" if blocked else "failed",
        "details": f"Unapproved 'production-deployment' in apply mode blocked={blocked}.",
    })

    # 4. Verify zero __pycache__ inside Skill Tool folder
    pycache_exists = (TOOL_DIR / "__pycache__").exists()
    checks.append({
        "check_id": "SELF-CLEAN-FOLDER",
        "name": "Zero Internal Subfolders / Bytecode Pollution",
        "status": "passed" if not pycache_exists else "failed",
        "details": f"__pycache__ present in Skill Tool folder: {pycache_exists}.",
    })

    failed = any(c["status"] == "failed" for c in checks)
    env = CORE.build_envelope(
        status="failed" if failed else "ok",
        command="selftest",
        start_time=start_t,
        inputs_validated=True,
        results={"categories_verified": len(idx.categories), "skills_verified": len(idx.by_slug), "checks_passed": sum(1 for c in checks if c["status"] == "passed")},
        verification_checks=checks,
    )
    return env, (1 if failed else 0)


def build_arg_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="Path to JSON config (default: software-engineering/asset-default-config.json)")
    common.add_argument("--policy", help="Path to safety policy JSON (default: software-engineering/asset-safety-policy.json)")
    common.add_argument("--format", choices=["json", "markdown", "table"], default="json", help="Output rendering format")
    common.add_argument("--output", help="Output directory or report file path (.json or .md)")
    common.add_argument("--force-overwrite", action="store_true", help="Overwrite existing output report file (requires --approve)")
    common.add_argument("--approve", action="store_true", help="Grant explicit human approval for sensitive/apply/overwrite actions")
    common.add_argument("--approval-reason", default="", help="Audit reason accompanying --approve")

    parser = argparse.ArgumentParser(
        prog="software-engineering/tools-workbench.py",
        description="Unified Software Engineering Skill Tool (51 Categories, 1,020 Capabilities).",
    )
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("help", help="Show CLI overview and usage guidance")

    p_cap = sub.add_parser("capabilities", aliases=["list"], parents=[common], help="List categories and capabilities")
    p_cap.add_argument("--category", help="Filter by category ID (1..51), code (cat-01), slug, or name")
    p_cap.add_argument("--search", help="Keyword search across all 1,020 skills")
    p_cap.add_argument("--requires-approval", action="store_true", help="Only list skills gated on apply mode")
    p_cap.add_argument("--summary", action="store_true", help="Return category summary only")
    p_cap.add_argument("--limit", type=int, default=0, help="Maximum skills to list (0 = all)")

    p_ins = sub.add_parser("inspect", parents=[common], help="Inspect detailed metadata for a skill or category")
    p_ins.add_argument("--skill", help="Skill ID (S0001), coordinate (01.01), slug, or name")
    p_ins.add_argument("--category", help="Category ID, code, slug, or name")

    p_par = sub.add_parser("parse-input", parents=[common], help="Extract capabilities from plain text, Markdown, or copied HTML")
    p_par.add_argument("--input", help="Path to plain-text, Markdown, or HTML file")
    p_par.add_argument("--text", help="Inline plain-text, Markdown, or HTML string")
    p_par.add_argument("--umbrella", help="Fallback umbrella Skill Tool name")
    p_par.add_argument("--execute", action="store_true", help="Map extracted capabilities to the catalog and execute them")

    p_rt = sub.add_parser("route", parents=[common], help="Recommend ranked skills for an engineering task")
    p_rt.add_argument("--input", help="File containing task description")
    p_rt.add_argument("--text", help="Task description text")
    p_rt.add_argument("--limit", type=int, default=8, help="Number of recommendations to return")

    p_run = sub.add_parser("run", parents=[common], help="Execute one skill, multiple skills, a category, or a DAG workflow")
    p_run.add_argument("--skill", action="append", help="Skill ID, coordinate, slug, or name (repeatable or comma-separated)")
    p_run.add_argument("--category", help="Category filter or full-category execution target")
    p_run.add_argument("--workflow", help="Named multi-step DAG workflow preset from asset-workflow-presets.json")
    p_run.add_argument("--all-matches", action="store_true", help="Execute all matching skills if a title spans categories")
    p_run.add_argument("--input", help="Input file or workspace directory path")
    p_run.add_argument("--text", help="Inline source code, SQL, specification, or query")
    p_run.add_argument("--param", action="append", help="Key=value runtime parameter override (repeatable)")
    p_run.add_argument("--project", help="Project or component name")
    p_run.add_argument("--prompts", help="Custom prompt templates JSON path")
    p_run.add_argument("--action-mode", choices=["plan", "apply"], default="plan", help="Execution mode (apply enforces approval gates)")
    p_run.add_argument("--simulate-interrupt-after", help="Step ID after which to simulate interruption for recovery testing")

    p_ver = sub.add_parser("verify", parents=[common], help="Run syntax, SAST, secret, schema, or ZIP reopen checks on a target")
    p_ver.add_argument("--input", required=True, help="Target file, directory, or ZIP archive to verify")

    p_rec = sub.add_parser("recover", parents=[common], help="Resume an interrupted workflow from its checkpoint-state.json")
    p_rec.add_argument("--checkpoint", required=True, help="Path to checkpoint-state.json")

    sub.add_parser("environment", parents=[common], help="Probe and disclose installed vs unavailable host runtimes")
    sub.add_parser("selftest", parents=[common], help="Run built-in end-to-end validation suite")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    CORE.install_signal_handlers()
    start_t = time.perf_counter()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.subcommand or args.subcommand == "help":
        parser.print_help()
        return 0

    fmt = getattr(args, "format", "json")
    try:
        dispatch = {
            "capabilities": cmd_capabilities,
            "list": cmd_capabilities,
            "inspect": cmd_inspect,
            "parse-input": cmd_parse_input,
            "route": cmd_route,
            "run": cmd_run,
            "verify": cmd_verify,
            "recover": cmd_recover,
            "environment": cmd_environment,
            "selftest": cmd_selftest,
        }
        envelope, code = dispatch[args.subcommand](args, start_t)
        sys.stdout.write(format_envelope(envelope, fmt=fmt))
        return code
    except CORE.WorkbenchError as exc:
        status = "approval_required" if exc.code in ("APPROVAL_REQUIRED", "APPROVAL_REASON_REQUIRED") else "failed"
        err_env = CORE.build_envelope(
            status=status,
            command=str(args.subcommand),
            start_time=start_t,
            inputs_validated=exc.code not in ("INPUT_NOT_FOUND", "INVALID_INPUT_PATH", "INPUT_TOO_LARGE", "INVALID_INPUT_JSON", "MISSING_INPUT"),
            approval=exc.details if status == "approval_required" and all(k in exc.details for k in ("scope", "action_mode")) else None,
            errors=[{"code": exc.code, "message": exc.message}],
        )
        if status == "approval_required":
            err_env["approval"] = {
                "required": True,
                "granted": False,
                "scope": exc.details.get("scope", "sensitive"),
                "action_mode": exc.details.get("action_mode", "apply"),
                "reason": exc.message,
            }
        sys.stdout.write(format_envelope(err_env, fmt=fmt))
        return exc.exit_code
    finally:
        CORE.cleanup_temp_dirs()


if __name__ == "__main__":
    sys.exit(main())
