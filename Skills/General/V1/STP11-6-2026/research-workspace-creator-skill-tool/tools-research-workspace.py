#!/usr/bin/env python3
"""
Primary runnable CLI entry point for the research-workspace-creator Skill Tool.

Unifies all 41 categories and 15 specialist roles (615 capabilities) from the
Comprehensive Research & Workspace Creator Skill Catalog into a deterministic,
permission-aware, provenance-backed production CLI.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOL_DIR = Path(__file__).resolve().parent
SKILL_TOOL_NAME = "research-workspace-creator"


def _bootstrap_modules() -> Dict[str, Any]:
    """Load sibling tools-* modules in deterministic dependency order without __pycache__."""
    order = [
        ("core_runtime", "tools-core-runtime.py"),
        ("spec_parser", "tools-spec-parser.py"),
        ("ingestion_extractors", "tools-ingestion-extractors.py"),
        ("knowledge_index", "tools-knowledge-index.py"),
        ("workspace_ops", "tools-workspace-ops.py"),
    ]
    loaded: Dict[str, Any] = {}
    for key, fname in order:
        mod_id = f"_rwc_{key}"
        if mod_id in sys.modules:
            loaded[key] = sys.modules[mod_id]
            continue
        fpath = TOOL_DIR / fname
        if not fpath.is_file():
            raise FileNotFoundError(f"Missing required module: {SKILL_TOOL_NAME}/{fname}")
        spec = importlib.util.spec_from_file_location(mod_id, str(fpath))
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to load spec for {SKILL_TOOL_NAME}/{fname}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_id] = mod
        spec.loader.exec_module(mod)
        loaded[key] = mod
    return loaded


MODULES = _bootstrap_modules()
core = MODULES["core_runtime"]
spec_mod = MODULES["spec_parser"]
ops_mod = MODULES["workspace_ops"]


def _parse_json_params(raw_params: Optional[str], allow_external: bool = False) -> Dict[str, Any]:
    if not raw_params:
        return {}
    s = raw_params.strip()
    if s.startswith("@"):
        p = core.validate_io_path(s[1:], allow_external=allow_external, must_exist=True)
        s = p.read_text(encoding="utf-8")
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as exc:
        raise core.ValidationError(f"Invalid JSON passed to --params: {exc}") from exc
    if not isinstance(parsed, dict):
        raise core.ValidationError("--params must decode to a JSON object.")
    return parsed


def _safe_stdout_write(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass


def _emit_output(data: Any, fmt: str = "json") -> None:
    if fmt == "json":
        _safe_stdout_write(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
        return
    if fmt == "markdown":
        if isinstance(data, dict) and "capability" in data:
            cap = data["capability"]
            qe = data.get("quality_evaluation", {})
            _safe_stdout_write(
                f"# {cap['name']}\n\n"
                f"- **Status:** `{data['status']}`\n"
                f"- **Run ID:** `{data['run_id']}`\n"
                f"- **Category:** `{cap['category_id']}` - {cap['category_name']}\n"
                f"- **Role:** `{cap['role_name']}`\n"
                f"- **Quality Score:** `{qe.get('overall_score')}/100 ({qe.get('grade')})`\n"
                f"- **Provenance SHA-256:** `{data.get('provenance', {}).get('sha256')}`\n\n"
                f"## Domain Result\n\n```json\n{json.dumps(data.get('domain_result'), indent=2, ensure_ascii=True)}\n```\n"
            )
            return
        _safe_stdout_write(f"```json\n{json.dumps(data, indent=2, ensure_ascii=True)}\n```\n")
        return

    # Plain text concise rendering
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=True)}")
            else:
                lines.append(f"{k}: {v}")
        _safe_stdout_write("\n".join(lines) + "\n")
    else:
        _safe_stdout_write(str(data) + "\n")


def _verify_target(target_path: Path, config: Any) -> Dict[str, Any]:
    validator = core.SchemaValidator(config.schemas)
    governor = core.SafetyGovernor(config=config)

    if target_path.is_file():
        raw = target_path.read_text(encoding="utf-8")
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise core.ValidationError(f"Target file is not valid JSON: {exc}") from exc

        schema_errors: List[str] = []
        if "capability" in doc and "domain_result" in doc:
            schema_errors = validator.validate("capability_result_envelope", doc)
        sec_scan = governor.scan_text(raw)
        expected_digest = doc.get("provenance", {}).get("payload_digest")
        actual_digest = (
            core.sha256_text(json.dumps(doc.get("domain_result"), sort_keys=True, default=str))
            if "domain_result" in doc
            else None
        )
        digest_match = (expected_digest == actual_digest) if expected_digest else True
        passed = (len(schema_errors) == 0) and (not sec_scan["has_secrets"]) and digest_match
        return {
            "status": "ok" if passed else "verification_failed",
            "verified_target": core.to_portable_path(target_path),
            "passed": passed,
            "schema_errors": schema_errors,
            "payload_digest_verified": digest_match,
            "security_scan": sec_scan,
        }

    if target_path.is_dir():
        ledger_file = target_path / "provenance-ledger.jsonl"
        entries: List[Dict[str, Any]] = []
        chain_valid = True
        prev_expected = "0" * 64
        if ledger_file.is_file():
            for ln in ledger_file.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                item = json.loads(ln)
                if item.get("prev_sha256") != prev_expected:
                    chain_valid = False
                prev_expected = item.get("sha256", "")
                entries.append(item)
        return {
            "status": "ok" if chain_valid else "verification_failed",
            "verified_workspace": core.to_portable_path(target_path),
            "passed": chain_valid,
            "provenance_entries_checked": len(entries),
            "hash_chain_intact": chain_valid,
        }

    raise core.ValidationError("Target does not exist for verification.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"python3 {SKILL_TOOL_NAME}/tools-research-workspace.py",
        description=(
            "Unified Research & Workspace Creator Skill Tool (41 categories, 15 specialist roles, 615 capabilities)."
        ),
    )
    parser.add_argument(
        "--config",
        default=f"{SKILL_TOOL_NAME}/asset-default-config.json",
        help=f"Relative path to JSON configuration (default: {SKILL_TOOL_NAME}/asset-default-config.json).",
    )
    parser.add_argument(
        "--allow-external-paths",
        action="store_true",
        help="Allow absolute or parent-traversal paths (requires --approve credential_or_policy_override).",
    )

    sub = parser.add_subparsers(dest="command")

    # help
    sub.add_parser("help", help="Show CLI overview and command reference.")

    # list-capabilities
    p_list = sub.add_parser(
        "list-capabilities",
        help="List unified categories, specialist roles, and all 615 capabilities.",
    )
    p_list.add_argument("--summary", action="store_true", help="Return category and role summary instead of full 615 items.")
    p_list.add_argument("--category", help="Filter by category ID (1..41), slug, or name.")
    p_list.add_argument("--role", help="Filter by specialist role slug or name.")
    p_list.add_argument("--domain", help="Filter by domain group slug.")
    p_list.add_argument("--search", help="Substring filter across names and descriptions.")
    p_list.add_argument("--limit", type=int, default=0, help="Maximum capabilities to display (0 = all matching).")
    p_list.add_argument("--format", choices=["json", "text", "markdown"], default="json")
    p_list.add_argument("--output", help="Optional relative file path to write JSON listing.")

    # inspect-capability
    p_insp = sub.add_parser(
        "inspect-capability",
        help="Inspect full triggers, inputs, outputs, permissions, quality checks, and recovery contract for a capability.",
    )
    p_insp.add_argument("capability", nargs="?", help="Capability ID (1..615), slug, or exact Skill Name.")
    p_insp.add_argument("--category", help="Category ID, slug, or name (used with --role).")
    p_insp.add_argument("--role", help="Role slug or name (defaults to operator).")
    p_insp.add_argument("--format", choices=["json", "text", "markdown"], default="json")
    p_insp.add_argument("--output", help="Optional relative output file path.")

    # parse-spec
    p_parse = sub.add_parser(
        "parse-spec",
        help="Extract normalized categories, skill names, descriptions, and umbrella name from text, Markdown, HTML, JSON, or PDF.",
    )
    p_parse.add_argument("--input", help="Relative path to plain text, Markdown, HTML, JSON, or PDF spec.")
    p_parse.add_argument("--input-text", help="Inline specification string (plain text, Markdown, or copied HTML).")
    p_parse.add_argument("--output", help="Optional relative output JSON path.")
    p_parse.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    # init-workspace
    p_init = sub.add_parser(
        "init-workspace",
        help="Initialize a clean, isolated research workspace with standard zones and provenance ledger.",
    )
    p_init.add_argument("--workspace-dir", default="workspace-runs/default", help="Relative workspace target folder.")
    p_init.add_argument("--output", help="Optional relative output JSON path.")
    p_init.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    # run-capability
    p_run = sub.add_parser(
        "run-capability",
        help="Run any of the 615 capabilities on real inputs with provenance, validation, and role specialization.",
    )
    p_run.add_argument("--capability", help="Capability ID (1..615), slug, or exact Skill Name.")
    p_run.add_argument("--category", help="Category ID (1..41), slug, or name.")
    p_run.add_argument("--role", help="Role slug or name (default: operator when --category is supplied).")
    p_run.add_argument("--input", help="Relative input file or folder path.")
    p_run.add_argument("--input-text", help="Inline text/HTML/SQL/code input.")
    p_run.add_argument("--query", help="Research question, search query, or SQL SELECT statement.")
    p_run.add_argument("--params", help="JSON object string or @relative-json-file with handler parameters.")
    p_run.add_argument("--workspace-dir", default="workspace-runs/default", help="Relative workspace folder.")
    p_run.add_argument("--output", help="Optional relative path to save structured JSON result envelope.")
    p_run.add_argument(
        "--approve",
        action="append",
        default=[],
        help="Explicitly approve a gated action (repeatable, e.g. --approve code_or_subprocess_execution).",
    )
    p_run.add_argument("--enable-network", action="store_true", help="Allow bounded outbound HTTP/HTTPS requests.")
    p_run.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    # run-workflow
    p_wf = sub.add_parser(
        "run-workflow",
        help="Execute a multi-stage, multi-agent DAG workflow with checkpointing and resume support.",
    )
    p_wf.add_argument("--workflow", help="Relative path to workflow JSON file (omit to run built-in sample DAG).")
    p_wf.add_argument("--workspace-dir", default="workspace-runs/workflow", help="Relative workspace folder.")
    p_wf.add_argument("--checkpoint-id", help="Custom checkpoint identifier.")
    p_wf.add_argument("--resume", action="store_true", help="Resume from existing checkpoint.")
    p_wf.add_argument("--output", help="Optional relative path to save workflow execution summary JSON.")
    p_wf.add_argument("--approve", action="append", default=[], help="Explicit approval token(s).")
    p_wf.add_argument("--enable-network", action="store_true", help="Allow bounded outbound HTTP/HTTPS requests.")
    p_wf.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    # recover
    p_rec = sub.add_parser(
        "recover",
        help="Inspect, repair, or restore a workflow/capability checkpoint state.",
    )
    p_rec.add_argument("--workspace-dir", default="workspace-runs/workflow", help="Relative workspace folder.")
    p_rec.add_argument("--checkpoint-id", required=True, help="Checkpoint identifier to inspect/recover.")
    p_rec.add_argument("--output", help="Optional relative output JSON path.")
    p_rec.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    # verify
    p_ver = sub.add_parser(
        "verify",
        help="Validate a result envelope JSON file or workspace provenance ledger.",
    )
    p_ver.add_argument("--input", required=True, help="Relative path to result JSON file or workspace directory.")
    p_ver.add_argument("--output", help="Optional relative output JSON path.")
    p_ver.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    # self-test
    p_test = sub.add_parser(
        "self-test",
        help="Run the built-in automated validation suite in an isolated temporary directory.",
    )
    p_test.add_argument("--format", choices=["json", "text", "markdown"], default="json")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.command or args.command == "help":
        parser.print_help()
        return 0

    try:
        approvals: List[str] = getattr(args, "approve", []) or []
        allow_ext = bool(args.allow_external_paths)
        default_cfg_str = f"{SKILL_TOOL_NAME}/asset-default-config.json"
        custom_cfg = args.config if args.config != default_cfg_str else None
        config = core.RuntimeConfig(config_rel_path=custom_cfg, allow_external_paths=allow_ext)

        if allow_ext:
            gov_check = core.SafetyGovernor(config=config, approved_actions=approvals)
            gov_check.require_approval(
                "credential_or_policy_override",
                "Enabling --allow-external-paths bypasses default relative path jail",
            )

        registry = spec_mod.CatalogRegistry(config.catalog_path)

        if args.command == "list-capabilities":
            if args.summary:
                payload: Dict[str, Any] = {
                    "skill_tool": SKILL_TOOL_NAME,
                    "total_categories": len(registry.categories),
                    "total_roles": len(registry.roles),
                    "total_capabilities": len(registry.capabilities),
                    "roles": [
                        {"slug": k, "name": v["role_name"], "purpose": v["purpose"]}
                        for k, v in registry.roles.items()
                    ],
                    "categories": [
                        {
                            "category_id": c["category_id"],
                            "slug": c["slug"],
                            "name": c["name"],
                            "domain_slug": c["domain_slug"],
                            "skill_count": c["skill_count"],
                        }
                        for c in registry.categories
                    ],
                }
            else:
                matched = registry.filter_capabilities(
                    category=args.category,
                    role=args.role,
                    domain=args.domain,
                    search=args.search,
                )
                total_matched = len(matched)
                if args.limit and args.limit > 0:
                    matched = matched[: args.limit]
                payload = {
                    "skill_tool": SKILL_TOOL_NAME,
                    "matched_count": total_matched,
                    "returned_count": len(matched),
                    "capabilities": matched,
                }
            if args.output:
                out_p = core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                core.atomic_write_json(out_p, payload)
            _emit_output(payload, fmt=args.format)
            return 0

        if args.command == "inspect-capability":
            cap = registry.resolve_capability(
                capability_ref=args.capability,
                category_ref=args.category,
                role_ref=args.role,
            )
            payload = registry.inspect_capability(cap)
            if args.output:
                out_p = core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                core.atomic_write_json(out_p, payload)
            _emit_output(payload, fmt=args.format)
            return 0

        if args.command == "parse-spec":
            if not args.input and not args.input_text:
                bundle = json.loads(config.sample_bundle_path.read_text(encoding="utf-8"))
                raw_spec = bundle["copied_html_skill_list"]
                hint = f"{SKILL_TOOL_NAME}/asset-sample-research-bundle.json#copied_html_skill_list"
            elif args.input:
                in_p = core.validate_io_path(args.input, allow_external=allow_ext, must_exist=True)
                raw_spec = spec_mod.extract_text_from_spec_file(in_p)
                hint = core.to_portable_path(in_p)
            else:
                raw_spec = args.input_text
                hint = "inline-text"
            parsed = spec_mod.parse_capability_spec(raw_spec, source_hint=hint)
            if args.output:
                out_p = core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                core.atomic_write_json(out_p, parsed)
            _emit_output(parsed, fmt=args.format)
            return 0

        if args.command == "init-workspace":
            ws_dir = core.validate_io_path(args.workspace_dir, allow_external=allow_ext, must_exist=False)
            cap = registry.resolve_capability(category_ref="workspace-and-sandbox-architecture", role_ref="architect")
            out_p = (
                core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                if args.output
                else None
            )
            env = ops_mod.run_single_capability(
                cap=cap,
                registry=registry,
                config=config,
                workspace_dir=ws_dir,
                output_path=out_p,
            )
            _emit_output(env, fmt=args.format)
            return 0

        if args.command == "run-capability":
            if not args.capability and not args.category:
                raise core.ValidationError(
                    "Provide --capability <id|slug|name> or --category <id|slug|name> [--role <role>]."
                )
            cap = registry.resolve_capability(
                capability_ref=args.capability,
                category_ref=args.category,
                role_ref=args.role,
            )
            ws_dir = core.validate_io_path(args.workspace_dir, allow_external=allow_ext, must_exist=False)
            in_p = (
                core.validate_io_path(args.input, allow_external=allow_ext, must_exist=True)
                if args.input
                else None
            )
            out_p = (
                core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                if args.output
                else None
            )
            params = _parse_json_params(args.params, allow_external=allow_ext)
            env = ops_mod.run_single_capability(
                cap=cap,
                registry=registry,
                config=config,
                workspace_dir=ws_dir,
                input_path=in_p,
                input_text=args.input_text,
                query=args.query,
                params=params,
                output_path=out_p,
                approved_actions=approvals,
                enable_network=bool(args.enable_network),
            )
            _emit_output(env, fmt=args.format)
            return 0

        if args.command == "run-workflow":
            ws_dir = core.validate_io_path(args.workspace_dir, allow_external=allow_ext, must_exist=False)
            if args.workflow:
                wf_path = core.validate_io_path(args.workflow, allow_external=allow_ext, must_exist=True)
                wf_spec = json.loads(wf_path.read_text(encoding="utf-8"))
            else:
                bundle = json.loads(config.sample_bundle_path.read_text(encoding="utf-8"))
                wf_spec = bundle["workflow_dag"]
            out_p = (
                core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                if args.output
                else None
            )
            res = ops_mod.run_dag_workflow(
                workflow_spec=wf_spec,
                registry=registry,
                config=config,
                workspace_dir=ws_dir,
                checkpoint_id=args.checkpoint_id,
                resume=bool(args.resume),
                approved_actions=approvals,
                enable_network=bool(args.enable_network),
                output_path=out_p,
            )
            _emit_output(res, fmt=args.format)
            return 0

        if args.command == "recover":
            ws_dir = core.validate_io_path(args.workspace_dir, allow_external=allow_ext, must_exist=True)
            ckpt_mgr = core.CheckpointManager(ws_dir / "checkpoints")
            state, was_recovered, note = ckpt_mgr.load_or_recover(args.checkpoint_id)
            payload = {
                "status": "recovered" if was_recovered else "ok",
                "checkpoint_id": args.checkpoint_id,
                "was_recovered": was_recovered,
                "recovery_note": note,
                "state": state,
            }
            if args.output:
                out_p = core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                core.atomic_write_json(out_p, payload)
            _emit_output(payload, fmt=args.format)
            return 0

        if args.command == "verify":
            in_p = core.validate_io_path(args.input, allow_external=allow_ext, must_exist=True)
            verdict = _verify_target(in_p, config=config)
            if args.output:
                out_p = core.validate_io_path(args.output, allow_external=allow_ext, must_exist=False)
                core.atomic_write_json(out_p, verdict)
            _emit_output(verdict, fmt=args.format)
            return 0 if verdict["passed"] else 2

        if args.command == "self-test":
            test_mod = core.load_sibling_tool("test_suite", "tools-test-suite.py")
            report = test_mod.run_all_tests()
            _emit_output(report, fmt=args.format)
            return 0 if report["passed"] else 1

        parser.print_help()
        return 2

    except KeyError as exc:
        err_doc = {"status": "validation_error", "code": "not_found", "message": str(exc).strip("'\"")}
        sys.stdout.write(json.dumps(err_doc, indent=2, ensure_ascii=True) + "\n")
        return 2
    except core.ApprovalRequiredError as exc:
        err_doc = {
            "status": "approval_required",
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
        sys.stdout.write(json.dumps(err_doc, indent=2, ensure_ascii=True) + "\n")
        return 3
    except core.ValidationError as exc:
        err_doc = {
            "status": "validation_error",
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
        sys.stdout.write(json.dumps(err_doc, indent=2, ensure_ascii=True) + "\n")
        return 2
    except core.SkillToolError as exc:
        err_doc = {
            "status": "error",
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
        sys.stdout.write(json.dumps(err_doc, indent=2, ensure_ascii=True) + "\n")
        return 4


if __name__ == "__main__":
    sys.exit(main())
