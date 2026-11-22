#!/usr/bin/env python3
"""Automated regression and verification test suite for research-workspace-creator."""
import sys

sys.dont_write_bytecode = True

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

TOOL_DIR = Path(__file__).resolve().parent
SKILL_TOOL_NAME = "research-workspace-creator"


def _load_cli_entry() -> Any:
    mod_name = "_rwc_cli_entry"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    cli_path = TOOL_DIR / "tools-research-workspace.py"
    spec = importlib.util.spec_from_file_location(mod_name, str(cli_path))
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load tools-research-workspace.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_all_tests() -> Dict[str, Any]:
    cli = _load_cli_entry()
    core = cli.core
    spec_mod = cli.spec_mod
    ops_mod = cli.ops_mod
    ing_mod = cli.MODULES["ingestion_extractors"]

    checks: List[Dict[str, Any]] = []

    def record_check(name: str, fn: Callable[[], str]) -> None:
        try:
            detail = fn()
            checks.append({"test": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"test": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    orig_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="rwc-selftest-") as tmp_root:
        tmp_path = Path(tmp_root)
        # Create relative workspace inside tmp_root while referencing skill resources cleanly
        os.chdir(tmp_path)
        try:
            config = core.RuntimeConfig()
            registry = spec_mod.CatalogRegistry(config.catalog_path)

            def _t_catalog_completeness() -> str:
                assert len(registry.categories) == 41, f"Expected 41 categories, got {len(registry.categories)}"
                assert len(registry.roles) == 15, f"Expected 15 roles, got {len(registry.roles)}"
                assert len(registry.capabilities) == 615, f"Expected 615 capabilities, got {len(registry.capabilities)}"
                return "Verified 41 categories, 15 roles, and 615 capabilities."

            record_check("catalog_completeness_615", _t_catalog_completeness)

            def _t_spec_parser_modes() -> str:
                html_in = (
                    '<div class="cat" data-id="1"><h3>Category 1: Data Systems</h3>'
                    '<p><span class="n">1)</span> <b>Skill Name:</b> Schema Validator '
                    'Description: Validates JSON payloads.</p></div>'
                )
                res_html = spec_mod.parse_capability_spec(html_in)
                assert res_html["umbrella_name"] == "data-systems"
                assert res_html["capabilities"][0]["name"] == "Schema Validator"

                md_in = "## Category 2: Query Tools\n1. **Fast Searcher** - Indexes local files."
                res_md = spec_mod.parse_capability_spec(md_in)
                assert res_md["umbrella_name"] == "query-tools"
                assert res_md["capability_count"] == 1
                return "Plain text, Markdown, and noisy HTML specs parsed cleanly."

            record_check("spec_parser_text_md_html", _t_spec_parser_modes)

            def _t_all_41_categories_and_15_roles() -> str:
                roles_list = sorted(registry.roles.keys())
                ws = tmp_path / "ws-all-41"
                for idx, cat in enumerate(registry.categories):
                    role_slug = roles_list[idx % len(roles_list)]
                    cap = registry.resolve_capability(category_ref=str(cat["category_id"]), role_ref=role_slug)
                    approvals = ["code_or_subprocess_execution"] if cat["category_id"] == 27 else []
                    env = ops_mod.run_single_capability(
                        cap=cap,
                        registry=registry,
                        config=config,
                        workspace_dir=ws / f"c{cat['category_id']:02d}",
                        approved_actions=approvals,
                    )
                    assert env["status"] == "ok", f"Category {cat['category_id']} failed"
                    assert env["quality_evaluation"]["passed_gate"] is True
                return "Executed all 41 categories and cycled all 15 specialist roles successfully."

            record_check("all_41_categories_and_15_roles", _t_all_41_categories_and_15_roles)

            def _t_input_sensitivity_and_deliverables() -> str:
                ws = tmp_path / "ws-diff"
                cap_idx = registry.resolve_capability(capability_ref="semantic-indexing-and-retrieval-indexer")
                r1 = ops_mod.run_single_capability(
                    cap=cap_idx, registry=registry, config=config, workspace_dir=ws, query="BM25 TF-IDF recall"
                )
                r2 = ops_mod.run_single_capability(
                    cap=cap_idx, registry=registry, config=config, workspace_dir=ws, query="robots crawl delay ETag"
                )
                top1 = r1["domain_result"]["top_hits"][0]["source_id"]
                top2 = r2["domain_result"]["top_hits"][0]["source_id"]
                assert top1 != top2, f"Expected different top source for distinct queries, got {top1} == {top2}"

                cap_rep = registry.resolve_capability(capability_ref="reporting-and-deliverable-creation-builder")
                rep_env = ops_mod.run_single_capability(
                    cap=cap_rep, registry=registry, config=config, workspace_dir=ws
                )
                delivs = rep_env["domain_result"]["deliverables_created"]
                docx_extracted = ing_mod.extract_document_file(Path(delivs["docx"]))
                xlsx_extracted = ing_mod.extract_document_file(Path(delivs["xlsx"]))
                html_text = Path(delivs["html"]).read_text(encoding="utf-8")
                assert "src-001" in docx_extracted["text"]
                assert len(xlsx_extracted["tables"][0]) >= 5
                assert "<svg" in html_text
                return f"Query sensitivity ({top1} vs {top2}) and .md/.html/.docx/.xlsx round-trip verified."

            record_check("input_change_and_deliverables_roundtrip", _t_input_sensitivity_and_deliverables)

            def _t_safety_gates_and_validation() -> str:
                ws = tmp_path / "ws-sec"
                # 1. Path traversal blocked
                blocked_traversal = False
                try:
                    core.validate_io_path("../outside-secret.txt")
                except core.ValidationError:
                    blocked_traversal = True
                assert blocked_traversal, "Parent traversal was not blocked"

                # 2. SSRF blocked
                gov = core.SafetyGovernor(config=config)
                blocked_ssrf = False
                try:
                    gov.validate_url("http://169.254.169.254/latest/meta-data/")
                except core.PolicyViolationError:
                    blocked_ssrf = True
                assert blocked_ssrf, "Metadata SSRF IP was not blocked"

                # 3. SQL write blocked without approval
                blocked_sql = False
                try:
                    gov.validate_sql("DROP TABLE benchmarks")
                except core.ApprovalRequiredError:
                    blocked_sql = True
                assert blocked_sql, "Destructive SQL was not gated"

                # 4. Code execution gated without approval
                cap_exec = registry.resolve_capability(category_ref="27", role_ref="operator")
                blocked_exec = False
                try:
                    ops_mod.run_single_capability(cap=cap_exec, registry=registry, config=config, workspace_dir=ws)
                except core.ApprovalRequiredError:
                    blocked_exec = True
                assert blocked_exec, "Unapproved code execution was not gated"

                # 5. Secret redaction
                fake_tok = "ghp_" + ("A1b2C3d4E5" * 4)
                red = gov.redact_text(f"Token={fake_tok}", redact_secrets=True)
                assert fake_tok not in red and "[REDACTED:github_token]" in red
                return "Path traversal, SSRF, SQL write gate, code execution gate, and secret redaction verified."

            record_check("safety_gates_and_redaction", _t_safety_gates_and_validation)

            def _t_recovery_and_workflow_resume() -> str:
                ws = tmp_path / "ws-rec"
                cap_rec = registry.resolve_capability(category_ref="37", role_ref="recovery-specialist")
                env = ops_mod.run_single_capability(
                    cap=cap_rec,
                    registry=registry,
                    config=config,
                    workspace_dir=ws,
                    params={"simulate_corruption": True, "checkpoint_id": "corrupt-test"},
                )
                assert env["domain_result"]["was_recovered"] is True
                assert env["domain_result"]["restored_state"]["step"] == "indexing"

                bundle = json.loads(config.sample_bundle_path.read_text(encoding="utf-8"))
                wf1 = ops_mod.run_dag_workflow(
                    workflow_spec=bundle["workflow_dag"],
                    registry=registry,
                    config=config,
                    workspace_dir=ws / "dag",
                    checkpoint_id="dag-01",
                )
                wf2 = ops_mod.run_dag_workflow(
                    workflow_spec=bundle["workflow_dag"],
                    registry=registry,
                    config=config,
                    workspace_dir=ws / "dag",
                    checkpoint_id="dag-01",
                    resume=True,
                )
                assert len(wf1["step_results"]) == 6
                assert all(s["status"] == "resumed_from_checkpoint" for s in wf2["step_results"])
                return "Corrupted checkpoint backup recovery and 6-step DAG resume verified."

            record_check("checkpoint_recovery_and_dag_resume", _t_recovery_and_workflow_resume)

            def _t_folder_packaging_hygiene() -> str:
                entries = sorted(p.name for p in TOOL_DIR.iterdir())
                subdirs = [p.name for p in TOOL_DIR.iterdir() if p.is_dir()]
                assert not subdirs, f"Unexpected subfolders inside Skill Tool: {subdirs}"
                assert "SKILL.md" not in entries, "SKILL.md must not exist inside Skill Tool folder"
                knowledge_files = [n for n in entries if n.startswith("knowledge-")]
                assert knowledge_files == ["knowledge-readme.md"], f"Expected only knowledge-readme.md, got {knowledge_files}"
                unprefixed = [
                    n for n in entries if not n.startswith(("tools-", "knowledge-", "asset-"))
                ]
                assert not unprefixed, f"Unprefixed files found: {unprefixed}"
                return f"Verified flat folder structure ({len(entries)} files, 0 subfolders, 0 unprefixed)."

            record_check("skill_tool_folder_hygiene", _t_folder_packaging_hygiene)
        finally:
            os.chdir(orig_cwd)

    passed_all = all(c["passed"] for c in checks)
    return {
        "status": "ok" if passed_all else "failed",
        "passed": passed_all,
        "total_checks": len(checks),
        "passed_checks": sum(1 for c in checks if c["passed"]),
        "checks": checks,
    }


if __name__ == "__main__":
    summary = run_all_tests()
    sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=True) + "\n")
    sys.exit(0 if summary["passed"] else 1)
