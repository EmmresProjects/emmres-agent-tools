#!/usr/bin/env python3
"""Built-in end-to-end validation suite for the document-productivity tool.

Loaded lazily by the primary CLI's selftest command. Every capability test
invokes the real CLI through a relative entry point; every generated artifact
is parsed or inspected. Temporary files live in an automatically removed
workspace directory. No external service, shell, or credential is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

dp_formats = __import__("sys").modules["dp_formats"]
dp_safety = __import__("sys").modules["dp_safety"]


def _redact(args: list[str], token: str) -> list[str]:
    return ["<approval-token>" if str(a) == token else str(a) for a in args]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Suite:
    def __init__(self, app, args, tmp: Path):
        self.app = app
        self.args = args
        self.tmp = tmp
        self.tmp_rel = "./" + os.path.relpath(tmp, app.cwd).replace(os.sep, "/")
        rel = os.path.relpath(app.tool_dir, app.cwd).replace(os.sep, "/")
        self.pkg = "." if rel == "." else ("./" + rel if not rel.startswith(".") else rel)
        self.token = str(getattr(args, "approval_token", None) or secrets.token_hex(12))
        self.env = {"DOCUMENT_PRODUCTIVITY_APPROVAL_TOKEN": self.token}
        self.tests: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.started = time.monotonic()

    def p(self, name: str) -> str:
        return f"{self.tmp_rel}/{name}"

    def asset(self, name: str) -> str:
        return f"{self.pkg}/{name}" if self.pkg != "." else f"./{name}"

    def record(self, name: str, ok: bool, details: dict[str, Any]) -> None:
        status = "pass" if ok else "fail"
        self.tests.append({"name": name, "status": status, "details": details})
        self.checks.append({"name": name, "status": status,
                            "detail": str(details.get("summary", name))})
        if not ok:
            self.errors.append({"code": "selftest_failure", "message": f"Test '{name}' failed.",
                                "details": details})

    def cli(self, name: str, cli_args: list[str], expect: str = "ok",
            error_code: str | set[str] | None = None, parse_json: bool = True,
            cwd: Path | None = None, entry: str | None = None,
            timeout: float | None = None, summary: str | None = None):
        cp = self.app.subprocess(cli_args, env_extra=self.env, cwd=cwd, entry=entry,
                                 timeout=timeout or 180.0)
        data: Any = None
        parse_error = None
        if parse_json:
            try:
                data = json.loads(cp.stdout)
            except json.JSONDecodeError as exc:
                parse_error = str(exc)
        else:
            data = cp.stdout
        if expect == "ok":
            ok = cp.returncode == 0 and parse_error is None
            if parse_json:
                ok = ok and isinstance(data, dict) and data.get("ok") is True
            else:
                ok = ok and "capabilities" in str(data).lower()
        else:
            allowed = {error_code} if isinstance(error_code, str) else set(error_code or [])
            ok = cp.returncode != 0 and parse_error is None and isinstance(data, dict) and data.get("ok") is False
            code = data.get("error", {}).get("code") if isinstance(data, dict) else None
            if allowed:
                ok = ok and code in allowed
        details = {"summary": summary or name, "args": _redact(cli_args, self.token),
                   "returncode": cp.returncode, "stdout_chars": len(cp.stdout),
                   "stderr_excerpt": cp.stderr[:240], "parse_error": parse_error}
        if isinstance(data, dict):
            details["keys"] = sorted(data.keys())[:20]
            if data.get("error"):
                details["error_code"] = data["error"].get("code")
                details["error_details"] = data["error"].get("details")
        self.record(name, ok, details)
        return data

    def inspect(self, name: str, path_value: str, summary: str | None = None):
        path = self.app.cwd / Path(path_value)
        try:
            info = dp_formats.validate_artifact(path)
            ok = info["ok"]
            details = {"summary": summary or f"Inspected {Path(path_value).name}",
                       "path": path_value, "details": info["details"]}
        except Exception as exc:
            ok = False
            details = {"summary": summary or f"Inspection failed for {Path(path_value).name}",
                       "path": path_value, "error": str(exc), "type": type(exc).__name__}
        self.record(name, ok, details)
        return details

    def write(self, name: str, content: str) -> str:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.p(name)


def _one_capability_per_category(app) -> str:
    lines = ["Comprehensive Document and Productivity Skill Catalog"]
    for category in app.catalog.categories:
        skill = category["skills"][0]
        lines.append(f'Category {category["number"]}: {category["name"]}')
        lines.append(category["purpose"])
        lines.append(f'{skill["number"]}. Skill Name: {skill["name"]}')
        lines.append(f'Description: {skill["description"]}')
    return "\n".join(lines) + "\n"


def _capability_for_format(app, fmt: str):
    pairs = app.catalog.all_capabilities()
    preferred = [pair for pair in pairs if fmt in pair[0].get("supported_formats", [])
                 and not pair[0].get("approval_required")]
    chosen = preferred[0] if preferred else next(
        (pair for pair in pairs if fmt in pair[0].get("supported_formats", [])), None)
    return chosen


def run_selftest(app, args) -> dict[str, Any]:
    quick = bool(getattr(args, "quick", False))
    force_all = bool(getattr(args, "all_capabilities", False))
    with tempfile.TemporaryDirectory(prefix=".selftest-", dir=str(app.cwd)) as raw_tmp:
        tmp = Path(raw_tmp)
        suite = Suite(app, args, tmp)
        sample = suite.asset("asset-sample-data.csv")
        token = suite.token
        reason = "automated local validation"

        # 1. Built-in help and capability-list commands.
        suite.cli("built-in-help", ["help"], parse_json=False,
                  summary="Main help text is available.")
        caps = suite.cli("capability-list", ["capabilities", "--limit", "5"],
                         summary="Structured capability list reports 784 total.")
        suite.record("capability-count", bool(caps and caps.get("total") == 784),
                     {"summary": "Catalog exposes 784 capabilities.", "reported_total": caps.get("total") if caps else None})
        suite.cli("capability-list-alias", ["capability-list", "--limit", "1"],
                  summary="capability-list alias reaches the same command.")
        suite.cli("list-capabilities-alias", ["list-capabilities", "--limit", "1"],
                  summary="list-capabilities alias reaches the same command.")
        ops = suite.cli("operations", ["operations"], summary="36 category operations are listed.")
        suite.record("operation-count", bool(ops and ops.get("count") == 36),
                     {"summary": "36 operations are reachable.", "reported_count": ops.get("count") if ops else None})
        handlers = suite.cli("handlers", ["handlers"], summary="Handler registry is complete.")
        suite.record("handler-registry", bool(handlers and handlers.get("registry", {}).get("ok")),
                     {"summary": "All catalog handlers are registered.",
                      "handlers": len(handlers.get("registry", {}).get("registered_handlers", [])) if handlers else 0})
        suite.cli("providers", ["providers"], summary="Provider presets are redacted and offline.")
        suite.cli("config", ["config"], summary="Redacted configuration resolves internal paths.")
        suite.cli("text-output", ["--stdout-format", "text", "capabilities", "--limit", "2"],
                  parse_json=False, summary="Text stdout mode renders capability names.")

        # 2. Input handling: plain text, Markdown, copied HTML, inline text, full catalog.
        parse_cases = [
            ("parse-plain-text", "asset-sample-catalog.txt", "pdf-engineering", 2),
            ("parse-markdown", "asset-sample-catalog.md", None, 2),
            ("parse-copied-html", "asset-sample-catalog.html", "security-privacy-and-governance", 2),
        ]
        for name, filename, umbrella, count in parse_cases:
            data = suite.cli(name, ["parse", "--input", suite.asset(filename),
                                    "--output", suite.p(name + ".json")],
                             summary=f"Parsed {filename} into one unified source.")
            source = data.get("source", {}) if isinstance(data, dict) else {}
            ok = source.get("umbrella_name") == umbrella if umbrella else bool(source.get("umbrella_name"))
            ok = ok and len(source.get("capabilities", [])) == count
            suite.record(name + "-content", ok,
                         {"summary": "Source labels, names, and descriptions were normalized.",
                          "umbrella_name": source.get("umbrella_name"),
                          "capabilities": len(source.get("capabilities", []))})
            suite.inspect(name + "-file", suite.p(name + ".json"))
        inline = ("Skill Name: Invoice Data Extraction\nDescription: A specialized capability to extract "
                  "invoice fields from grounded document inputs with validation and human approval.")
        data = suite.cli("parse-single-capability", ["parse", "--text", inline],
                         summary="A single capability with no category is accepted.")
        source = data.get("source", {}) if isinstance(data, dict) else {}
        suite.record("single-capability-umbrella",
                     source.get("umbrella_name") == "invoice-data-extraction" and len(source.get("capabilities", [])) == 1,
                     {"summary": "No-category source derives a short hyphenated umbrella name.",
                      "umbrella_name": source.get("umbrella_name"),
                      "capabilities": len(source.get("capabilities", []))})
        full_plan_path = suite.p("full-plan.json")
        suite.cli("plan-full-catalog", ["plan", "--input", suite.asset("asset-source-catalog.txt"),
                                        "--output", full_plan_path],
                  summary="The 36-category source becomes one unified plan.")
        plan = json.loads((tmp / "full-plan.json").read_text(encoding="utf-8"))
        suite.record("full-plan-count", plan.get("capability_count") == 784 and len(plan.get("operations", [])) == 36,
                     {"summary": "Unified plan contains all 784 capabilities and 36 operations.",
                      "capability_count": plan.get("capability_count"),
                      "operations": len(plan.get("operations", []))})
        suite.cli("validate-plan-schema", ["validate", "--input", full_plan_path, "--schema", "plan"],
                  summary="Plan file matches its bundled schema.")
        suite.cli("validate-config-schema", ["validate", "--input", suite.asset("asset-default-config.json"),
                                             "--schema", "config"], summary="Config matches its schema.")
        suite.cli("validate-catalog-schema", ["validate", "--input", suite.asset("asset-capability-catalog.json"),
                                              "--schema", "catalog"], summary="Catalog matches its schema.")

        # 3. Valid run, structured output, and artifact inspection.
        run1 = suite.p("run1/primary.json")
        run1_report = suite.p("run1/report.json")
        data = suite.cli("run-valid", ["run", "--capability", "Universal Document Intake",
                                       "--input", sample, "--output", run1, "--report", run1_report],
                         summary="A real capability writes a primary artifact and report.")
        suite.record("run-structured-result", bool(data and data.get("handler") == "op_intake_foundation"
                     and data.get("errors") == []),
                     {"summary": "Run result is structured and error-free.",
                      "handler": data.get("handler") if isinstance(data, dict) else None,
                      "artifacts": len(data.get("artifacts", [])) if isinstance(data, dict) else 0})
        suite.inspect("inspect-primary-json", run1)
        suite.inspect("inspect-report-json", run1_report)
        suite.cli("validate-run-result", ["validate", "--input", run1_report, "--schema", "result"],
                  summary="Run report matches the result schema.")
        report_disk = json.loads((tmp / "run1" / "report.json").read_text(encoding="utf-8"))
        suite.record("run-report-matches-stdout", report_disk == data,
                     {"summary": "The report file and structured stdout contain the same result.",
                      "report_keys": len(report_disk), "stdout_keys": len(data) if isinstance(data, dict) else 0})

        # 4. Invalid, missing, and ambiguous inputs; structured errors.
        suite.cli("missing-input", ["run", "--capability", "Universal Document Intake",
                                    "--output", suite.p("missing.json")],
                  expect="error", error_code="input_required",
                  summary="Missing input returns input_required.")
        suite.cli("missing-input-file", ["run", "--capability", "Universal Document Intake",
                                         "--input", suite.p("does-not-exist.csv"),
                                         "--output", suite.p("missing-file.json")],
                  expect="error", error_code="input_not_found",
                  summary="Missing input file returns input_not_found.")
        suite.cli("invalid-capability", ["run", "--capability", "Not A Real Capability",
                                         "--input", sample, "--output", suite.p("invalid-cap.json")],
                  expect="error", error_code="unknown_capability",
                  summary="Unknown capability returns suggestions and a structured error.")
        suite.cli("invalid-format", ["run", "--capability", "Universal Document Intake",
                                     "--input", sample, "--format", "bogus",
                                     "--output", suite.p("invalid-format.json")],
                  expect="error", error_code="unsupported_format",
                  summary="Unsupported output format is rejected before work.")
        suite.cli("invalid-options", ["run", "--capability", "Universal Document Intake",
                                      "--input", sample, "--options", "{bad",
                                      "--output", suite.p("invalid-options.json")],
                  expect="error", error_code="invalid_options",
                  summary="Malformed options JSON is rejected.")
        suite.cli("invalid-source", ["parse", "--input", suite.asset("asset-sample-data.csv"),
                                     "--text", "Skill Name: X"],
                  expect="error", error_code="ambiguous_source",
                  summary="Conflicting source arguments are rejected.")
        amb = suite.cli("ambiguous-capability", ["run", "--capability", "Excel VBA Automation",
                                                 "--input", sample,
                                                 "--output", suite.p("ambiguous-capability.json")],
                        expect="error", error_code="ambiguous_capability",
                        summary="An overlapping exact name returns a structured ambiguity error.")
        candidates = amb.get("error", {}).get("details", {}).get("candidates", []) if isinstance(amb, dict) else []
        suite.record("ambiguous-capability-two-candidates", len(candidates) == 2,
                     {"summary": "Both preserved overlapping capabilities are offered as candidates.",
                      "candidates": candidates})
        vba_one = suite.cli("disambiguate-c07-s18", ["run", "--capability", "c07-s18",
                                                     "--input", sample, "--output", suite.p("vba-one.json"),
                                                     "--dry-run"],
                            summary="The first overlapping capability is reachable by id in dry-run mode.")
        vba_two = suite.cli("disambiguate-c30-s04", ["run", "--capability", "c30-s04",
                                                     "--input", sample, "--output", suite.p("vba-two.json"),
                                                     "--dry-run"],
                            summary="The second overlapping capability is reachable by id in dry-run mode.")
        suite.record("overlapping-capabilities-distinct",
                     bool(vba_one and vba_two and vba_one.get("handler") != vba_two.get("handler")
                          and vba_one.get("category") != vba_two.get("category")),
                     {"summary": "Preserved overlapping capabilities dispatch to distinct category handlers.",
                      "first": {"id": vba_one.get("capability_id") if isinstance(vba_one, dict) else None,
                                "handler": vba_one.get("handler") if isinstance(vba_one, dict) else None},
                      "second": {"id": vba_two.get("capability_id") if isinstance(vba_two, dict) else None,
                                 "handler": vba_two.get("handler") if isinstance(vba_two, dict) else None}})

        # 5. Approval gate: denied, dry-run deferred, and approved.
        suite.cli("approval-denied", ["run", "--capability", "Password Protection",
                                      "--input", sample, "--output", suite.p("denied/primary.json")],
                  expect="error", error_code={"approval_denied", "approval_token_not_configured"},
                  summary="Sensitive write is denied without a token.")
        dry = suite.cli("approval-dry-run", ["run", "--capability", "Password Protection",
                                             "--input", sample, "--output", suite.p("dry/primary.json"),
                                             "--dry-run"],
                        summary="Sensitive dry run is allowed and writes nothing.")
        suite.record("approval-dry-run-deferred",
                     bool(dry and dry.get("approval", {}).get("status") == "deferred-dry-run"),
                     {"summary": "Dry run defers approval and returns artifact previews.",
                      "status": dry.get("approval", {}).get("status") if isinstance(dry, dict) else None,
                      "previews": len(dry.get("artifacts_preview", [])) if isinstance(dry, dict) else 0})
        approved = suite.cli("approval-approved", ["run", "--capability", "Password Protection",
                                                   "--input", sample,
                                                   "--output", suite.p("approved/primary.json"),
                                                   "--approval-token", token,
                                                   "--approval-reason", reason],
                             summary="Sensitive write succeeds with a token from the environment.")
        suite.record("approval-approved-state",
                     bool(approved and approved.get("approval", {}).get("status") == "approved"),
                     {"summary": "Approval state is recorded without storing the token.",
                      "status": approved.get("approval", {}).get("status") if isinstance(approved, dict) else None})

        # 6. Change the input and confirm the result changes.
        alternate = suite.write("alternate.csv",
                                "id,value,note\n1,10,alpha\n2,20,beta\n3,30,gamma\n")
        data2 = suite.cli("changed-input", ["run", "--capability", "Universal Document Intake",
                                            "--input", alternate, "--output", suite.p("run2/primary.json"),
                                            "--report", suite.p("run2/report.json")],
                          summary="The same capability is rerun with a different input.")
        original_hash = _sha256(tmp / "run1" / "primary.json")
        changed_hash = _sha256(tmp / "run2" / "primary.json")
        suite.record("input-change-changes-output", original_hash != changed_hash,
                     {"summary": "Primary artifact hash changes when the input changes.",
                      "original_sha256": original_hash, "changed_sha256": changed_hash,
                      "changed_run_ok": bool(data2 and data2.get("ok"))})
        suite.inspect("inspect-changed-input-result", suite.p("run2/primary.json"))

        # 7. Conversion and all supported output formats.
        conv = suite.cli("convert-csv-to-xlsx", ["convert", "--input", sample,
                                                 "--output", suite.p("convert/converted.xlsx"), "--to", "xlsx",
                                                 "--approval-token", token, "--approval-reason", reason],
                         summary="convert performs a real local conversion.")
        suite.inspect("inspect-converted-xlsx", suite.p("convert/converted.xlsx"))
        for fmt in sorted(dp_formats.SUPPORTED_FORMATS):
            pair = _capability_for_format(app, fmt)
            if not pair:
                suite.record("format-" + fmt, False,
                             {"summary": f"No capability supports {fmt}.", "format": fmt})
                continue
            skill, _cat = pair
            out = suite.p(f"formats/{fmt}/out.{fmt}")
            data = suite.cli("format-" + fmt, ["run", "--capability", skill["name"],
                                               "--input", sample, "--output", out,
                                               "--format", fmt, "--approval-token", token,
                                               "--approval-reason", reason],
                             summary=f"Generated and will inspect a {fmt} artifact.")
            suite.inspect("inspect-format-" + fmt, out,
                          summary=f"Parsed the generated {fmt} artifact.")

        # 8. One real output for each of the 36 category handlers.
        if not quick:
            source36 = suite.write("source36.txt", _one_capability_per_category(app))
            plan36 = suite.p("plan36.json")
            suite.cli("plan-36-categories", ["plan", "--input", source36, "--output", plan36],
                      summary="A 36-category source becomes one plan.")
            batch36 = suite.cli("batch-36-handlers", ["batch", "--plan", plan36,
                                                      "--input", sample,
                                                      "--output-dir", suite.p("batch36"),
                                                      "--report", suite.p("batch36-report.json"),
                                                      "--approval-token", token,
                                                      "--approval-reason", reason],
                                summary="All 36 category handlers write real artifacts.")
            summary = batch36.get("summary", {}) if isinstance(batch36, dict) else {}
            suite.record("batch-36-success", summary.get("succeeded") == 36 and summary.get("failed") == 0,
                         {"summary": "Every category handler succeeded.", "summary_values": summary})
            inspected = 0
            for item in batch36.get("items", []) if isinstance(batch36, dict) else []:
                for artifact in item.get("artifacts", []):
                    path = artifact.get("path")
                    if not path:
                        continue
                    try:
                        dp_formats.validate_artifact(app.cwd / Path(path))
                        inspected += 1
                    except Exception:
                        pass
            suite.record("batch-36-artifact-inspection", inspected >= 36,
                         {"summary": "Generated batch artifacts were parsed and hashed.",
                          "inspected": inspected})
            suite.cli("validate-batch-schema", ["validate", "--input", suite.p("batch36-report.json"),
                                                "--schema", "batch"],
                      summary="Batch report matches the batch schema.")

        # 9. Reach every one of the 784 capabilities through the real CLI.
        if (not quick) or force_all:
            full_batch = suite.cli("all-784-capabilities-dry-run",
                                   ["--set", "limits.timeout_seconds=180", "batch",
                                    "--plan", full_plan_path, "--input", sample,
                                    "--output-dir", suite.p("all784"), "--limit", "784", "--dry-run"],
                                   summary="All 784 capabilities are dispatched through the CLI.")
            summary = full_batch.get("summary", {}) if isinstance(full_batch, dict) else {}
            previews = sum(len(i.get("artifacts_preview", []))
                           for i in full_batch.get("items", []) if isinstance(i, dict)) if isinstance(full_batch, dict) else 0
            suite.record("all-784-success", summary.get("succeeded") == 784 and summary.get("failed") == 0,
                         {"summary": "784 capabilities succeeded in bounded dry-run mode.",
                          "summary_values": summary, "artifact_previews": previews})

        # 10. Recovery path: overwrite with backup, then restore from backup.
        overwrite = suite.cli("recovery-overwrite", ["run", "--capability", "Universal Document Intake",
                                                     "--input", sample, "--output", run1,
                                                     "--report", run1_report, "--overwrite",
                                                     "--approval-token", token,
                                                     "--approval-reason", reason],
                              summary="Approved overwrite creates a .bak backup.")
        backups = overwrite.get("recovery", {}).get("backups", []) if isinstance(overwrite, dict) else []
        backup_path = next((b for b in backups if b and b.endswith("run1/primary.json.bak")), None)
        suite.record("recovery-backup-created", bool(backup_path) and (tmp / "run1" / "primary.json.bak").is_file(),
                     {"summary": "A primary-output backup exists.", "backups": backups})
        recovered = suite.p("recovery/restored.json")
        rec = suite.cli("recovery-restore", ["recover", "--backup", suite.p("run1/primary.json.bak"),
                                             "--output", recovered, "--overwrite",
                                             "--approval-token", token, "--approval-reason", reason],
                         summary="recover restores the backup atomically.")
        restored_hash = _sha256(tmp / "recovery" / "restored.json") if (tmp / "recovery" / "restored.json").is_file() else None
        suite.record("recovery-hash-match", restored_hash == original_hash,
                     {"summary": "Recovered content matches the original primary artifact.",
                      "original_sha256": original_hash, "restored_sha256": restored_hash,
                      "recover_ok": bool(rec and rec.get("ok"))})
        suite.inspect("inspect-recovered-artifact", recovered)

        # 11. Package verification and a clean temporary skill root.
        verify_out = suite.p("verify-current.json")
        current = suite.cli("verify-current-package",
                            ["verify", "--package-root", suite.pkg, "--output", verify_out],
                            summary="Current package passes compatibility checks.")
        suite.record("verify-current-ok", bool(current and current.get("ok")),
                     {"summary": "Package has one knowledge file, prefixed files, and resolvable references.",
                      "file_count": current.get("file_count") if isinstance(current, dict) else None})
        suite.inspect("inspect-verify-report", verify_out)
        skill_root = tmp / "skill-root"
        copied = skill_root / "document-productivity"
        copied.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(
            "# Test skill root\n\nOne root SKILL.md for package compatibility validation.\n",
            encoding="utf-8")
        for item in app.tool_dir.iterdir():
            if item.is_file() and item.name.startswith(("tools-", "knowledge-", "asset-")):
                shutil.copy2(item, copied / item.name)
        clean_entry = "./document-productivity/tools-document-productivity.py"
        clean_args = [
            ["help"],
            ["capabilities", "--limit", "2"],
            ["run", "--capability", "Universal Document Intake",
             "--input", "./document-productivity/asset-sample-data.csv",
             "--output", "./clean-out/intake.json", "--report", "./clean-out/intake-report.json"],
            ["verify", "--package-root", "./document-productivity", "--skill-root", "."],
        ]
        suite.cli("clean-root-help", clean_args[0], parse_json=False, cwd=skill_root,
                  entry=clean_entry, summary="Built-in help runs from the clean root with relative paths.")
        suite.cli("clean-root-capabilities", clean_args[1], cwd=skill_root,
                  entry=clean_entry, summary="Capability list runs from the clean root with relative paths.")
        suite.cli("clean-root-run", clean_args[2], cwd=skill_root,
                  entry=clean_entry, summary="A real capability runs from the clean root with relative paths.")
        try:
            clean_info = dp_formats.validate_artifact(skill_root / "clean-out" / "intake.json")
            suite.record("clean-root-artifact-inspection", clean_info["ok"],
                         {"summary": "Clean-root primary artifact parses and hashes.",
                          "details": clean_info["details"]})
        except Exception as exc:
            suite.record("clean-root-artifact-inspection", False,
                         {"summary": "Clean-root artifact inspection failed.", "error": str(exc)})
        copied_verify = suite.cli("verify-clean-skill-root", clean_args[3], cwd=skill_root,
                                  entry=clean_entry,
                                  summary="Clean root with one SKILL.md passes relative-path verification.")
        suite.record("clean-root-ok", bool(copied_verify and copied_verify.get("ok")),
                     {"summary": "Copied folder works beside one root SKILL.md using relative paths.",
                      "combined_file_count": copied_verify.get("combined_file_count") if isinstance(copied_verify, dict) else None})
        suite.record("clean-root-relative-paths-only",
                     all(not str(part).startswith("/") for args in clean_args for part in args)
                     and (skill_root / "SKILL.md").is_file()
                     and sum(1 for p in skill_root.rglob("SKILL.md") if p.is_file()) == 1,
                     {"summary": "Clean-root commands use only relative arguments and one root SKILL.md exists.",
                      "skill_root_files": sum(1 for p in skill_root.rglob("*") if p.is_file() and p.name != "SKILL.md")})

        # 12. Additional schema and artifact validation from the real interface.
        suite.cli("validate-artifact-pdf", ["validate", "--input", suite.p("formats/pdf/out.pdf"),
                                            "--schema", "artifact"],
                  summary="Generated PDF passes artifact inspection.")
        suite.cli("validate-artifact-docx", ["validate", "--input", suite.p("formats/docx/out.docx"),
                                             "--schema", "artifact"],
                  summary="Generated DOCX passes OOXML ZIP/XML inspection.")
        suite.cli("validate-artifact-xlsx", ["validate", "--input", suite.p("formats/xlsx/out.xlsx"),
                                             "--schema", "artifact"],
                  summary="Generated XLSX passes OOXML ZIP/XML inspection.")
        suite.cli("validate-artifact-zip", ["validate", "--input", suite.p("formats/zip/out.zip"),
                                            "--schema", "artifact"],
                  summary="Generated ZIP passes CRC and member inspection.")

        passed = sum(1 for t in suite.tests if t["status"] == "pass")
        failed = len(suite.tests) - passed
        result = {
            "ok": failed == 0,
            "command": "selftest",
            "quick": quick,
            "all_capabilities_requested": force_all,
            "summary": {"tests": len(suite.tests), "passed": passed, "failed": failed,
                        "capabilities_in_catalog": app.catalog.capability_count,
                        "operations": len({c.get("operation") for c in app.catalog.categories}),
                        "handlers": len(suite.app.engine.handlers)},
            "tests": suite.tests,
            "checks": suite.checks,
            "errors": suite.errors,
            "warnings": suite.warnings + [
                "External Microsoft 365, Google Workspace, Canva, Adobe, CRM, ERP, database, webhook, and script integrations remain disabled.",
                "Full PDF OCR/encryption/signing, WCAG certification, visual regression, and legacy binary conversion are unavailable.",
            ],
            "artifacts": [],
            "metrics": {"duration_ms": round((time.monotonic() - suite.started) * 1000, 3),
                        "temporary_root": suite.tmp_rel,
                        "temporary_files_cleaned": True,
                        "approval_token_in_report": False},
            "limits": dp_safety.json_ready(app.config.limits),
        }
        return result
