#!/usr/bin/env python3
"""Primary CLI for the unified document-productivity Skill Tool.

Run:
  python3 /document-productivity/tools-document-productivity.py help
  python3 /document-productivity/tools-document-productivity.py capabilities

The leading slash in packaged examples is skill-root relative, not a
filesystem root. No external dependency, network call, shell command, or
credential is required.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
CWD = Path.cwd().resolve()
COMMANDS = [
    "help", "capabilities", "capability-list", "list-capabilities", "explain",
    "operations", "handlers", "providers", "config", "parse", "plan", "run",
    "batch", "convert", "validate", "verify", "recover", "selftest",
]


def load_module(name: str, filename: str):
    path = BASE / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dp_safety = load_module("dp_safety", "tools-safety.py")
dp_formats = load_module("dp_formats", "tools-formats.py")
dp_catalog = load_module("dp_catalog", "tools-catalog.py")
dp_operations = load_module("dp_operations", "tools-operations.py")
dp_handlers = load_module("dp_handlers", "tools-category-handlers.py")

ToolError = dp_safety.ToolError
Config = dp_safety.Config
Policy = dp_safety.Policy
SimpleSchemaValidator = dp_safety.SimpleSchemaValidator
RunGuard = dp_safety.RunGuard
resolve_user_path = dp_safety.resolve_user_path
root_relative = dp_safety.root_relative
display_path = dp_safety.display_path
json_ready = dp_safety.json_ready
atomic_write = dp_safety.atomic_write
recover_backup = dp_safety.recover_backup
verify_package = dp_safety.verify_package
load_json = dp_safety.load_json
structured_error = dp_safety.structured_error
Catalog = dp_catalog.Catalog
OperationEngine = dp_operations.OperationEngine
normalize_format = dp_operations.normalize_format


class App:
    def __init__(self, args: argparse.Namespace, parser: argparse.ArgumentParser):
        self.args = args
        self.parser = parser
        self.exit_code = 0
        self.tool_dir = BASE
        self.cwd = CWD
        self.config_path = resolve_user_path(
            args.config or "/asset-default-config.json", self.tool_dir, self.cwd)
        self.config = Config.load(self.config_path, self.tool_dir, args.set or [])
        schemas = load_json(self.config.internal_path("schemas_path"))
        self.validator = SimpleSchemaValidator(schemas)
        self.validator.validate_named(self.config.data, "config")
        self.catalog_path = (resolve_user_path(args.catalog, self.tool_dir, self.cwd)
                             if args.catalog else self.config.internal_path("catalog_path"))
        self.policy_path = (resolve_user_path(args.policy, self.tool_dir, self.cwd)
                            if args.policy else self.config.internal_path("policy_path"))
        self.catalog = Catalog.load(self.catalog_path)
        self.catalog.validate(self.validator)
        self.policy = Policy.load(self.policy_path)
        self.engine = OperationEngine(self.catalog, self.config, self.policy,
                                      self.tool_dir, self.cwd)
        self.registration = dp_handlers.register_all(self.engine)
        self.registry = self.engine.validate_registry()
        if not self.registry["ok"]:
            raise ToolError("handlers_missing", "One or more catalog handlers are not registered.",
                            {"missing": self.registry["missing"][:20]}, exit_code=1)

    # ----------------------------- shared helpers -----------------------------
    def resolve_path(self, value: str | None) -> Path | None:
        if value is None:
            return None
        return resolve_user_path(value, self.tool_dir, self.cwd)

    def reject_inside_package(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.tool_dir.resolve())
        except ValueError:
            return
        raise ToolError("output_inside_package",
                        "Outputs must be written outside the Skill Tool folder.",
                        {"path": display_path(path)})

    def parse_options(self, args: argparse.Namespace) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if getattr(args, "options_file", None):
            path = self.resolve_path(args.options_file)
            assert path is not None
            data = load_json(path)
            if not isinstance(data, dict):
                raise ToolError("invalid_options", "Options file must contain a JSON object.",
                                {"path": display_path(path)})
            options.update(data)
        if getattr(args, "options", None):
            try:
                data = json.loads(args.options)
            except json.JSONDecodeError as exc:
                raise ToolError("invalid_options", "--options must be valid JSON.",
                                {"error": str(exc)})
            if not isinstance(data, dict):
                raise ToolError("invalid_options", "--options must contain a JSON object.")
            options.update(data)
        for key in ("to", "formula", "provider", "prompt_set", "locale", "theme",
                    "value_column", "label_column", "chart_type", "series_name",
                    "retention", "citation_style"):
            value = getattr(args, key, None)
            if value is not None:
                options[key] = value
        return options

    def source_text(self, args: argparse.Namespace) -> tuple[str, str]:
        if getattr(args, "input", None) and getattr(args, "text", None) is not None:
            raise ToolError("ambiguous_source", "Use either --input or --text, not both.")
        if getattr(args, "text", None) is not None:
            return str(args.text), "inline"
        if getattr(args, "input", None):
            path = self.resolve_path(args.input)
            assert path is not None
            if not path.is_file():
                raise ToolError("source_not_found", "Source file does not exist.",
                                {"path": display_path(path)})
            if path.is_symlink():
                raise ToolError("unsafe_symlink", "Source symlinks are not accepted.",
                                {"path": display_path(path)})
            max_chars = int(self.config.limits.get("max_text_chars", 2000000))
            text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
            return text, display_path(path)
        raise ToolError("source_required", "Provide --input or --text.")

    def load_plan(self, value: str) -> dict[str, Any]:
        path = self.resolve_path(value)
        assert path is not None
        data = load_json(path)
        self.validator.validate_named(data, "plan")
        return data

    def output_json_path(self, value: str, default_name: str = "result.json") -> Path:
        path = self.resolve_path(value)
        assert path is not None
        if path.is_dir():
            path = path / default_name
        if path.suffix.lower() != ".json":
            raise ToolError("invalid_output_extension",
                            "Structured command output must end in .json.",
                            {"path": display_path(path)})
        self.reject_inside_package(path)
        return path

    def approval_for_overwrite(self, args: argparse.Namespace, target_exists: bool) -> dict[str, Any]:
        if not target_exists or not getattr(args, "overwrite", False):
            return {"required": False, "status": "not-required"}
        return self.policy.check_approval(
            ["destructive"], getattr(args, "approval_token", None),
            getattr(args, "approval_reason", None),
            str(self.config.get("approval.token_env")), False)

    def write_structured_file(self, value: str | None, result: dict[str, Any],
                              args: argparse.Namespace, default_name: str = "result.json") -> dict[str, Any]:
        result = copy.deepcopy(result)
        if not value:
            return result
        path = self.output_json_path(value, default_name)
        existed = path.exists()
        approval = self.approval_for_overwrite(args, existed)
        content = (json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        info = atomic_write(
            path, content, overwrite=bool(getattr(args, "overwrite", False)),
            backup=bool(self.config.get("storage.backup", True)),
            backup_suffix=str(self.config.get("storage.backup_suffix", ".bak")),
            max_bytes=int(self.config.limits.get("max_output_bytes", 104857600)),
            attempts=int(self.config.limits.get("retry_count", 2)),
            delay_ms=int(self.config.limits.get("retry_delay_ms", 100)))
        inspected = dp_formats.validate_artifact(path)
        result["output_file"] = info
        result["output_file"]["inspection"] = inspected["details"]
        result["output_file"]["approval"] = approval
        return result

    def emit(self, result: dict[str, Any], command: str | None = None) -> dict[str, Any]:
        if not getattr(self.args, "quiet", False):
            if getattr(self.args, "stdout_format", "json") == "text":
                print(self.to_text(result))
            else:
                print(json.dumps(json_ready(result), indent=2, ensure_ascii=False, default=str))
        return result

    @staticmethod
    def to_text(result: dict[str, Any]) -> str:
        if "capabilities" in result and isinstance(result["capabilities"], list):
            lines = [f"Capabilities: {result.get('count', len(result['capabilities']))}"]
            lines.extend(f"{c.get('id', '')}  {c.get('name', '')}  [{c.get('category', '')}]"
                         for c in result["capabilities"] if isinstance(c, dict))
            return "\n".join(lines)
        if "error" in result:
            err = result["error"]
            return f"ERROR {err.get('code')}: {err.get('message')}\n{json.dumps(err.get('details', {}), indent=2)}"
        lines = [f"ok: {result.get('ok', True)}", f"command: {result.get('command', '')}"]
        for key in ("capability", "category", "operation", "handler", "format", "tool_name",
                    "capability_count", "file_count", "umbrella_name", "source_format"):
            if key in result:
                lines.append(f"{key}: {result[key]}")
        if result.get("artifacts"):
            lines.append("artifacts:")
            lines.extend(f"  - {a.get('path')} ({a.get('format')}, {a.get('bytes')} bytes)"
                         for a in result["artifacts"] if isinstance(a, dict))
        if result.get("checks"):
            lines.append("checks:")
            lines.extend(f"  - {c.get('status')}: {c.get('name')}"
                         for c in result["checks"] if isinstance(c, dict))
        return "\n".join(lines)

    def relative_entry(self) -> str:
        rel = os.path.relpath(self.tool_dir / "tools-document-productivity.py", self.cwd)
        return "./" + rel.replace(os.sep, "/")

    def subprocess(self, cli_args: list[str], env_extra: dict[str, str] | None = None,
                   cwd: Path | None = None, timeout: float | None = None,
                   entry: str | None = None):
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(env_extra or {})
        workdir = cwd or self.cwd
        if entry is None:
            rel = os.path.relpath(self.tool_dir / "tools-document-productivity.py", workdir)
            entry = "./" + rel.replace(os.sep, "/")
        cmd = ["python3", entry] + [str(a) for a in cli_args]
        return subprocess.run(cmd, cwd=str(workdir), env=env,
                              capture_output=True, text=True,
                              timeout=timeout or float(self.config.limits.get("timeout_seconds", 60)),
                              shell=False, check=False)

    # ------------------------------- commands ---------------------------------
    def cmd_capabilities(self, args):
        rows = self.catalog.list_capabilities(args.category, args.query, args.limit, args.offset)
        result = {"ok": True, "command": args.command, "count": len(rows),
                  "total": self.catalog.capability_count,
                  "category_count": self.catalog.category_count,
                  "category_filter": args.category, "query": args.query,
                  "offset": args.offset, "limit": args.limit,
                  "capabilities": rows}
        result = self.write_structured_file(args.output, result, args, "capabilities.json")
        return self.emit(result)

    def cmd_explain(self, args):
        plan = self.load_plan(args.plan) if args.plan else None
        capability, category = self.engine.resolve(args.capability, plan)
        public = self.catalog.public_capability(capability, category) if hasattr(self.catalog, "public_capability") else dict(capability)
        result = {"ok": True, "command": "explain", "capability": public,
                  "category": {k: category.get(k) for k in ("name", "slug", "purpose", "operation", "handler")},
                  "responsibility": capability.get("responsibility"),
                  "supported_formats": capability.get("supported_formats", []),
                  "approval_classes": capability.get("approval_classes", []),
                  "examples": [
                      f"python3 /document-productivity/tools-document-productivity.py explain --capability \"{capability.get('name')}\"",
                      f"python3 /document-productivity/tools-document-productivity.py run --capability \"{capability.get('name')}\" --input ./input.csv --output ./out",
                  ]}
        result = self.write_structured_file(args.output, result, args, "explain.json")
        return self.emit(result)

    def cmd_operations(self, args):
        rows = []
        for category in self.catalog.categories:
            if args.category and self.catalog.resolve_category(args.category) is not category:
                continue
            rows.append({"number": category.get("number"), "name": category.get("name"),
                         "slug": category.get("slug"), "purpose": category.get("purpose"),
                         "operation": category.get("operation"), "handler": category.get("handler"),
                         "default_format": category.get("default_format"),
                         "supported_formats": category.get("supported_formats"),
                         "skill_count": category.get("skill_count"),
                         "responsibility": category.get("responsibility")})
        result = {"ok": True, "command": "operations", "count": len(rows), "operations": rows}
        result = self.write_structured_file(args.output, result, args, "operations.json")
        return self.emit(result)

    def cmd_handlers(self, args):
        result = {"ok": True, "command": "handlers",
                  "registration": self.registration, "registry": self.registry,
                  "loading_order": ["tools-safety.py", "tools-formats.py", "tools-catalog.py",
                                    "tools-operations.py", "tools-category-handlers.py",
                                    "tools-document-productivity.py", "tools-self-test.py"]}
        result = self.write_structured_file(args.output, result, args, "handlers.json")
        return self.emit(result)

    def cmd_providers(self, args):
        presets = load_json(self.config.internal_path("provider_presets_path"))
        result = {"ok": True, "command": "providers",
                  "default_provider": presets.get("default_provider"),
                  "network_enabled": self.config.get("network.enabled", False),
                  "providers": presets.get("providers", {}),
                  "limitations": ["No live Microsoft 365, Google Workspace, Canva, Adobe, CRM, ERP, webhook, or database calls.",
                                  "No endpoint, credential value, paid service, or auth token is packaged."]}
        result = self.write_structured_file(args.output, result, args, "providers.json")
        return self.emit(result)

    def cmd_config(self, args):
        resolved = {}
        for key in ("catalog_path", "source_catalog_path", "policy_path", "provider_presets_path",
                    "prompt_set_path", "schemas_path", "document_template_path", "sample_data_path"):
            resolved[key] = display_path(self.config.internal_path(key), self.tool_dir)
        result = {"ok": True, "command": "config", "path": display_path(self.config.path),
                  "config": self.config.redacted(), "resolved_internal_paths": resolved,
                  "dependencies": ["Python 3.10 or newer", "standard library only"]}
        result = self.write_structured_file(args.output, result, args, "config.json")
        return self.emit(result)

    def cmd_parse(self, args):
        text, label = self.source_text(args)
        parsed = self.catalog.parse_source(text, label, self.policy, args.expand_categories)
        data = parsed.to_dict()
        self.validator.validate_named(data, "source")
        result = {"ok": True, "command": "parse", "source": data}
        if args.output:
            written = self.write_structured_file(args.output, data, args, "parse-result.json")
            result["source_output_file"] = written.get("output_file")
        return self.emit(result)

    def cmd_plan(self, args):
        text, label = self.source_text(args)
        parsed = self.catalog.parse_source(text, label, self.policy, args.expand_categories)
        plan = self.catalog.build_plan(parsed)
        data = plan.to_dict()
        self.validator.validate_named(data, "plan")
        result = {"ok": True, "command": "plan", "plan": data}
        if args.output:
            written = self.write_structured_file(args.output, data, args, "plan.json")
            result["plan_output_file"] = written.get("output_file")
        return self.emit(result)

    def cmd_run(self, args):
        plan = self.load_plan(args.plan) if args.plan else None
        capability, category = self.engine.resolve(args.capability, plan)
        if args.category and not plan:
            wanted = self.catalog.resolve_category(args.category)
            if not wanted:
                raise ToolError("unknown_category", "Category filter did not match the catalog.",
                                {"category": args.category})
            candidates = [(s, c) for s, c in self.catalog.all_capabilities()
                          if s.get("name", "").casefold() == args.capability.casefold() and c is wanted]
            if len(candidates) != 1:
                raise ToolError("ambiguous_capability",
                                "Category filter did not resolve to exactly one capability.",
                                {"capability": args.capability, "category": args.category,
                                 "matches": len(candidates)})
            capability, category = candidates[0]
        if args.action:
            capability = dict(capability)
            capability["action"] = args.action
        options = self.parse_options(args)
        input_path = self.resolve_path(args.input)
        fmt = normalize_format(args.format) if args.format else None
        if fmt is None and args.output:
            candidate = self.resolve_path(args.output)
            assert candidate is not None
            fmt = normalize_format(dp_formats.format_from_path(candidate))
        output = self.engine.output_spec(args.output, fmt or str(capability.get("default_format") or "json"),
                                         args.report, str(capability.get("global_slug") or capability.get("id")))
        guard = RunGuard(float(self.config.limits.get("timeout_seconds", 60)))
        result = self.engine.run(
            capability, category, options, input_path, output, fmt=fmt,
            dry_run=bool(args.dry_run), overwrite=bool(args.overwrite),
            approval_token=args.approval_token, approval_reason=args.approval_reason,
            guard=guard, write_result=True, command="run")
        self.validator.validate_named(result, "result")
        return self.emit(result)

    def cmd_batch(self, args):
        plan = self.load_plan(args.plan)
        capabilities = plan.get("capabilities", [])
        limit = args.limit if args.limit is not None else len(capabilities)
        max_items = int(self.config.limits.get("max_capabilities_per_batch", 1000))
        if limit > max_items:
            raise ToolError("batch_too_large", "Batch exceeds the configured capability limit.",
                            {"requested": limit, "max_items": max_items})
        capabilities = capabilities[:max(0, limit)]
        output_root_value = args.output_dir or str(Path(str(self.config.get("storage.root", "./out"))) / "batch")
        output_root = self.resolve_path(output_root_value)
        assert output_root is not None
        self.reject_inside_package(output_root)
        if output_root.is_file():
            raise ToolError("invalid_output_directory", "Batch output root is a file.",
                            {"path": display_path(output_root)})
        input_path = self.resolve_path(args.input)
        options = self.parse_options(args)
        categories = plan.get("categories", [])
        guard = RunGuard(float(self.config.limits.get("timeout_seconds", 60)))
        items: list[dict[str, Any]] = []
        for index, capability in enumerate(capabilities, 1):
            guard.check(f"batch item {index}")
            cat = next((c for c in categories
                        if re.sub(r"[^a-z0-9]+", " ", str(c.get("name", "")).lower()).strip()
                        == re.sub(r"[^a-z0-9]+", " ", str(capability.get("category_name", "")).lower()).strip()), {})
            category = {"name": capability.get("category_name") or cat.get("name", "Custom Capability"),
                        "slug": capability.get("category_slug") or cat.get("slug"),
                        "purpose": cat.get("purpose", ""),
                        "operation": capability.get("operation"),
                        "handler": capability.get("handler")}
            slug = str(capability.get("global_slug") or capability.get("id") or f"item-{index}")
            fmt = normalize_format(args.format) if args.format else str(capability.get("default_format") or "json")
            item_output = str(output_root / slug)
            spec = self.engine.output_spec(item_output, fmt, None, slug)
            try:
                item_result = self.engine.run(
                    capability, category, options, input_path, spec, fmt=fmt,
                    dry_run=bool(args.dry_run), overwrite=bool(args.overwrite),
                    approval_token=args.approval_token, approval_reason=args.approval_reason,
                    guard=guard, write_result=False, command="batch")
                items.append({"ok": True, "index": index, "id": capability.get("id"),
                              "name": capability.get("name"), "operation": capability.get("operation"),
                              "handler": capability.get("handler"), "format": fmt,
                              "output": spec.to_dict(),
                              "artifacts": item_result.get("artifacts", []),
                              "artifacts_preview": item_result.get("artifacts_preview", []),
                              "checks": item_result.get("checks", [])})
            except ToolError as exc:
                items.append({"ok": False, "index": index, "id": capability.get("id"),
                              "name": capability.get("name"),
                              "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        failures = [item for item in items if not item["ok"]]
        batch_result = {"ok": not failures, "command": "batch",
                        "plan": display_path(self.resolve_path(args.plan) or Path(args.plan)),
                        "output_root": display_path(output_root),
                        "input": display_path(input_path) if input_path else None,
                        "dry_run": bool(args.dry_run), "items": items,
                        "summary": {"requested": len(capabilities), "succeeded": len(items) - len(failures),
                                    "failed": len(failures), "operations": sorted({
                                        str(c.get("operation")) for c in capabilities}),
                                    "handlers": sorted({str(c.get("handler")) for c in capabilities})},
                        "errors": [{"code": i["error"]["code"], "message": i["error"]["message"],
                                    "details": {"index": i["index"], "name": i["name"]}}
                                   for i in failures],
                        "warnings": ["Dry runs write no artifacts."] if args.dry_run else [],
                        "checks": [{"name": "batch-limit", "status": "pass",
                                    "detail": f"{len(capabilities)} items within limit {max_items}."},
                                   {"name": "cancellation", "status": "pass",
                                    "detail": "SIGINT and timeout are enforced by one shared RunGuard."}],
                        "artifacts": [], "metrics": {"items": len(items)},
                        "limits": json_ready(self.config.limits)}
        self.validator.validate_named(batch_result, "batch")
        if not args.dry_run:
            report_value = args.report or str(output_root / "batch-report.json")
            batch_result = self.write_structured_file(report_value, batch_result, args, "batch-report.json")
        else:
            batch_result["report"] = {"written": False, "reason": "dry-run"}
        if failures:
            self.exit_code = 2
        return self.emit(batch_result)

    def cmd_convert(self, args):
        input_path = self.resolve_path(args.input)
        assert input_path is not None
        if not input_path.is_file():
            raise ToolError("input_not_found", "Conversion input does not exist.",
                            {"path": display_path(input_path)})
        target = normalize_format(args.to)
        if target not in dp_formats.SUPPORTED_FORMATS:
            raise ToolError("unsupported_format", "Conversion target is not supported.",
                            {"target": target, "supported": sorted(dp_formats.SUPPORTED_FORMATS)})
        source = input_path.suffix.lower().lstrip(".") or "unknown"
        mapping = {
            ("csv", "xlsx"): "CSV to XLSX Conversion", ("xlsx", "csv"): "XLSX to CSV Conversion",
            ("md", "docx"): "Markdown to DOCX Conversion", ("md", "pdf"): "Markdown to PDF Conversion",
            ("html", "docx"): "HTML to DOCX Conversion", ("odt", "pdf"): "ODT to PDF Conversion",
            ("ods", "xlsx"): "ODS to XLSX Conversion", ("docx", "pdf"): "DOCX to PDF Conversion",
            ("xlsx", "pdf"): "XLSX to PDF Conversion", ("pdf", "docx"): "PDF to DOCX Conversion",
        }
        capability_name = mapping.get((source, target), "Batch Conversion Pipeline")
        capability, category = self.engine.resolve(capability_name)
        options = self.parse_options(args)
        options["to"] = target
        output = self.engine.output_spec(args.output, target, args.report, slugify_name(capability_name))
        guard = RunGuard(float(self.config.limits.get("timeout_seconds", 60)))
        result = self.engine.run(
            capability, category, options, input_path, output, fmt=target,
            dry_run=bool(args.dry_run), overwrite=bool(args.overwrite),
            approval_token=args.approval_token, approval_reason=args.approval_reason,
            guard=guard, write_result=True, command="convert")
        self.validator.validate_named(result, "result")
        return self.emit(result)

    def cmd_validate(self, args):
        path = self.resolve_path(args.input)
        assert path is not None
        if not path.is_file():
            raise ToolError("input_not_found", "Validation input does not exist.",
                            {"path": display_path(path)})
        schema = args.schema
        checks: list[dict[str, Any]] = []
        details: dict[str, Any] = {"path": display_path(path)}
        if schema == "artifact" or (schema == "auto" and dp_formats.format_from_path(path) not in {None, "json"}):
            inspected = dp_formats.validate_artifact(path)
            details.update(inspected["details"])
            checks.append({"name": "artifact-structure", "status": "pass",
                           "detail": f"Parsed {inspected['details']['format']} artifact."})
        else:
            data = load_json(path)
            if schema == "auto":
                if "categories" in data and "capabilities" in data and "tool_name" in data:
                    schema = "plan"
                elif "capability_count" in data and "categories" in data:
                    schema = "catalog"
                elif "source_format" in data and "umbrella_name" in data:
                    schema = "source"
                elif "command" in data and "items" in data:
                    schema = "batch"
                elif "checks" in data and "artifacts" in data:
                    schema = "result"
                elif "catalog_path" in data and "limits" in data:
                    schema = "config"
                else:
                    schema = "json"
            if schema == "json":
                checks.append({"name": "json-parse", "status": "pass", "detail": "JSON parsed successfully."})
            else:
                self.validator.validate_named(data, schema)
                checks.append({"name": "schema-validation", "status": "pass",
                               "detail": f"Instance matched schema '{schema}'."})
            details["schema"] = schema
            details["bytes"] = len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
        result = {"ok": True, "command": "validate", "details": details, "checks": checks,
                  "errors": [], "warnings": [], "artifacts": [], "metrics": {},
                  "limits": json_ready(self.config.limits)}
        result = self.write_structured_file(args.output, result, args, "validation.json")
        return self.emit(result)

    def cmd_verify(self, args):
        default_package = "./document-productivity" if (self.cwd / "document-productivity").is_dir() else str(self.tool_dir)
        package_root = self.resolve_path(args.package_root or default_package)
        assert package_root is not None
        if not package_root.is_dir():
            raise ToolError("package_root_not_found", "Skill Tool folder does not exist.",
                            {"path": display_path(package_root)})
        result = verify_package(package_root, self.config, self.policy, COMMANDS)
        result["command"] = "verify"
        result["future_skill_root_checks"] = []
        if args.skill_root:
            skill_root = self.resolve_path(args.skill_root)
            assert skill_root is not None
            checks = []
            errors = []
            def add(name, ok, detail):
                status = "pass" if ok else "fail"
                checks.append({"name": name, "status": status, "detail": detail})
                if not ok:
                    errors.append({"code": "future_root_check_failed", "message": detail,
                                   "details": {"check": name}})
            skill_md = skill_root / "SKILL.md"
            add("one-root-skill-md", skill_md.is_file(),
                "Exactly one test SKILL.md is present at the future skill root." if skill_md.is_file()
                else "No root SKILL.md was found beside the Skill Tool folder.")
            all_skill_md = list(skill_root.rglob("SKILL.md")) if skill_root.is_dir() else []
            add("no-other-skill-md", all_skill_md == [skill_md.resolve()] if skill_md.is_file() else False,
                f"Found {len(all_skill_md)} SKILL.md file(s) in the combined test package.")
            try:
                package_root.resolve().relative_to(skill_root.resolve())
                add("tool-folder-beside-skill-md", True, "Skill Tool folder is inside the future skill root.")
            except ValueError:
                add("tool-folder-beside-skill-md", False, "Skill Tool folder is not inside the supplied skill root.")
            files = [p for p in skill_root.rglob("*") if p.is_file()]
            add("combined-file-count", len(files) <= int(self.config.limits.get("max_package_files", 500)),
                f"Combined test package has {len(files)} files.")
            depths = [len(p.relative_to(skill_root).parts) for p in files]
            add("combined-path-depth", max(depths or [0]) <= int(self.config.limits.get("max_path_depth", 3)),
                f"Maximum path depth is {max(depths or [0])}.")
            add("no-nested-archives", not any(p.suffix.lower() in {".zip", ".tar", ".gz", ".7z", ".rar"} for p in files),
                "No nested archive is present in the combined test package.")
            result["future_skill_root_checks"] = checks
            result["errors"].extend(errors)
            result["ok"] = result["ok"] and not errors
            result["future_skill_root"] = display_path(skill_root)
            result["combined_file_count"] = len(files)
        result["warnings"] = result.get("warnings", [])
        if not result["ok"]:
            self.exit_code = 2
        result = self.write_structured_file(args.output, result, args, "verify.json")
        return self.emit(result)

    def cmd_recover(self, args):
        if not args.overwrite:
            raise ToolError("overwrite_required",
                            "Recovery replaces the target output; pass --overwrite and approval.",
                            {"output": args.output})
        approval = self.policy.check_approval(
            ["destructive"], args.approval_token, args.approval_reason,
            str(self.config.get("approval.token_env")), False)
        backup = self.resolve_path(args.backup)
        output = self.resolve_path(args.output)
        assert backup is not None and output is not None
        self.reject_inside_package(output)
        guard = RunGuard(float(self.config.limits.get("timeout_seconds", 60)))
        with guard:
            info = recover_backup(backup, output, overwrite=True,
                                  backup_suffix=str(self.config.get("storage.backup_suffix", ".bak")),
                                  guard=guard)
        inspected = dp_formats.validate_artifact(output)
        result = {"ok": True, "command": "recover", "approval": approval,
                  "recovery": info, "inspection": inspected["details"],
                  "artifacts": [{"name": output.name,
                                 "format": dp_formats.format_from_path(output) or "bin",
                                 "path": info["path"], "bytes": info["bytes"],
                                 "sha256": info["sha256"], "primary": True}],
                  "checks": [{"name": "backup-restore", "status": "pass",
                              "detail": "Backup was restored atomically and parsed."}],
                  "errors": [], "warnings": [], "metrics": {}, "limits": json_ready(self.config.limits)}
        self.validator.validate_named(result, "result")
        return self.emit(result)

    def cmd_selftest(self, args):
        module = sys.modules.get("dp_selftest") or load_module("dp_selftest", "tools-self-test.py")
        result = module.run_selftest(self, args)
        if args.output:
            result = self.write_structured_file(args.output, result, args, "selftest-report.json")
        if not result.get("ok", False):
            self.exit_code = 2
        return self.emit(result)

    def dispatch(self):
        args = self.args
        command = args.command
        aliases = {"capability-list": "capabilities", "list-capabilities": "capabilities"}
        command = aliases.get(command, command)
        handlers = {
            "capabilities": self.cmd_capabilities, "explain": self.cmd_explain,
            "operations": self.cmd_operations, "handlers": self.cmd_handlers,
            "providers": self.cmd_providers, "config": self.cmd_config,
            "parse": self.cmd_parse, "plan": self.cmd_plan, "run": self.cmd_run,
            "batch": self.cmd_batch, "convert": self.cmd_convert,
            "validate": self.cmd_validate, "verify": self.cmd_verify,
            "recover": self.cmd_recover, "selftest": self.cmd_selftest,
        }
        if command not in handlers:
            raise ToolError("unknown_command", f"Unknown command '{command}'.",
                            {"available": COMMANDS})
        return handlers[command](args)


def slugify_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "result"


def add_structured_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", help="Optional structured JSON result path.")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing result file.")
    parser.add_argument("--approval-token", help="Approval token from the configured environment variable.")
    parser.add_argument("--approval-reason", help="Required human reason for an approved overwrite.")


def add_common_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", help="Input file or directory path.")
    parser.add_argument("--output", help="Primary artifact path or output directory.")
    parser.add_argument("--report", help="Structured JSON report path.")
    parser.add_argument("--options", help="Inline JSON object of operation options.")
    parser.add_argument("--options-file", help="JSON file containing operation options.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing output artifacts.")
    parser.add_argument("--dry-run", action="store_true", help="Render and validate in memory without writing.")
    parser.add_argument("--approval-token", help="Approval token from the configured environment variable.")
    parser.add_argument("--approval-reason", help="Required human reason for sensitive or destructive writes.")
    for flag in ("--to", "--formula", "--provider", "--prompt-set", "--locale", "--theme",
                 "--value-column", "--label-column", "--chart-type", "--series-name",
                 "--retention", "--citation-style"):
        parser.add_argument(flag, help=f"Shortcut that writes {flag[2:].replace('-', '_')} into --options.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools-document-productivity.py",
        description="Unified document-productivity Skill Tool: 36 categories and 784 capabilities.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Use 'help' for this text and 'capabilities' for the complete capability list.")
    parser.add_argument("--config", help="Configuration path, default /asset-default-config.json")
    parser.add_argument("--catalog", help="Override capability catalog path.")
    parser.add_argument("--policy", help="Override safety policy path.")
    parser.add_argument("--set", action="append", default=[],
                        help="Override a dotted config key, for example --set limits.timeout_seconds=120.")
    parser.add_argument("--stdout-format", choices=["json", "text"], default="json",
                        help="Structured stdout format, default json.")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout output.")
    parser.add_argument("--debug", action="store_true", help="Include a traceback for unexpected errors.")
    parser.add_argument("--version", action="version", version="document-productivity Skill Tool 1.0")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("help", help="Show built-in help.")
    p = sub.add_parser("capabilities", aliases=["capability-list", "list-capabilities"],
                       help="List all 784 capabilities or a filtered subset.")
    p.add_argument("--category", help="Filter by exact category name, slug, or number.")
    p.add_argument("--query", help="Substring filter across names and descriptions.")
    p.add_argument("--limit", type=int, help="Maximum number of rows.")
    p.add_argument("--offset", type=int, default=0, help="Row offset.")
    add_structured_output_args(p)

    p = sub.add_parser("explain", help="Explain one capability and its handler.")
    p.add_argument("--capability", required=True, help="Capability name, id, slug, or global slug.")
    p.add_argument("--plan", help="Optional unified plan containing a custom capability.")
    add_structured_output_args(p)

    p = sub.add_parser("operations", help="List all 36 category operations.")
    p.add_argument("--category", help="Filter by exact category name, slug, or number.")
    add_structured_output_args(p)

    p = sub.add_parser("handlers", help="Show registered handlers and loading order.")
    add_structured_output_args(p)

    p = sub.add_parser("providers", help="Show disabled and local provider presets.")
    add_structured_output_args(p)

    p = sub.add_parser("config", help="Show redacted configuration and resolved internal paths.")
    add_structured_output_args(p)

    for name in ("parse", "plan"):
        p = sub.add_parser(name, help=f"{name.capitalize()} a capability source into one unified result.")
        p.add_argument("--input", help="Plain text, Markdown, or copied HTML source file.")
        p.add_argument("--text", help="Inline source text instead of a file.")
        p.add_argument("--no-expand-categories", dest="expand_categories", action="store_false",
                       help="Do not expand category-only sources from the bundled catalog.")
        p.set_defaults(expand_categories=True)
        add_structured_output_args(p)

    p = sub.add_parser("run", help="Execute one capability through its registered handler.")
    p.add_argument("--capability", required=True, help="Capability name, id, slug, or global slug.")
    p.add_argument("--category", help="Optional category filter for ambiguous names.")
    p.add_argument("--plan", help="Optional unified plan containing a custom capability.")
    p.add_argument("--format", help="Primary artifact format override.")
    p.add_argument("--action", help="Override the capability action label.")
    add_common_run_args(p)

    p = sub.add_parser("batch", help="Execute every capability in a unified plan.")
    p.add_argument("--plan", required=True, help="Unified plan JSON from the plan command.")
    p.add_argument("--input", required=True, help="Shared input file or directory.")
    p.add_argument("--output-dir", help="Output root for per-capability artifacts.")
    p.add_argument("--report", help="Batch report path.")
    p.add_argument("--format", help="Primary artifact format override for all items.")
    p.add_argument("--limit", type=int, help="Run only the first N plan capabilities.")
    p.add_argument("--options", help="Inline JSON object of operation options.")
    p.add_argument("--options-file", help="JSON file containing operation options.")
    p.add_argument("--overwrite", action="store_true", help="Replace existing output artifacts.")
    p.add_argument("--dry-run", action="store_true", help="Render and validate in memory without writing.")
    p.add_argument("--approval-token", help="Approval token from the configured environment variable.")
    p.add_argument("--approval-reason", help="Required human reason for sensitive or destructive writes.")

    p = sub.add_parser("convert", help="Convert a local artifact to a supported format.")
    p.add_argument("--input", required=True, help="Source artifact path.")
    p.add_argument("--output", required=True, help="Converted artifact path.")
    p.add_argument("--to", required=True, help="Target format extension.")
    p.add_argument("--report", help="Structured JSON report path.")
    p.add_argument("--options", help="Inline JSON object of operation options.")
    p.add_argument("--options-file", help="JSON file containing operation options.")
    p.add_argument("--overwrite", action="store_true", help="Replace existing output artifacts.")
    p.add_argument("--dry-run", action="store_true", help="Render and validate in memory without writing.")
    p.add_argument("--approval-token", help="Approval token from the configured environment variable.")
    p.add_argument("--approval-reason", help="Required human reason for sensitive or destructive writes.")

    p = sub.add_parser("validate", help="Validate JSON schemas or inspect generated artifacts.")
    p.add_argument("--input", required=True, help="File to validate.")
    p.add_argument("--schema", default="auto",
                   choices=["auto", "config", "catalog", "source", "plan", "result", "batch", "json", "artifact"],
                   help="Schema name or auto detection.")
    add_structured_output_args(p)

    p = sub.add_parser("verify", help="Verify Copilot-skill package compatibility.")
    p.add_argument("--package-root", help="Skill Tool folder to verify.")
    p.add_argument("--skill-root", help="Optional clean future skill root containing one test SKILL.md.")
    add_structured_output_args(p)

    p = sub.add_parser("recover", help="Restore an output from a .bak backup.")
    p.add_argument("--backup", required=True, help="Backup path.")
    p.add_argument("--output", required=True, help="Recovery target path.")
    p.add_argument("--overwrite", action="store_true", help="Confirm replacement of the recovery target.")
    p.add_argument("--approval-token", help="Approval token from the configured environment variable.")
    p.add_argument("--approval-reason", help="Required human reason for recovery.")

    p = sub.add_parser("selftest", help="Run the built-in end-to-end validation suite.")
    p.add_argument("--output", help="Optional JSON test report path.")
    p.add_argument("--quick", action="store_true", help="Run only smoke and error-path tests.")
    p.add_argument("--all-capabilities", action="store_true",
                   help="Dry-run every bundled capability in addition to full output-format tests.")
    p.add_argument("--overwrite", action="store_true", help="Replace an existing test report.")
    p.add_argument("--approval-token", help="Optional externally supplied approval token.")
    p.add_argument("--approval-reason", help="Human reason for sensitive writes.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "help":
        parser.print_help()
        return 0
    command = args.command
    try:
        app = App(args, parser)
        result = app.dispatch()
        if getattr(result, "get", lambda *_: None)("ok") is False and command in ("validate", "batch"):
            app.exit_code = 2
        return app.exit_code
    except ToolError as exc:
        payload = structured_error(exc, command)
        if not args.quiet:
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return exc.exit_code
    except KeyboardInterrupt:
        payload = structured_error(ToolError("cancelled", "Operation was cancelled.", {}, 4), command)
        if not args.quiet:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 4
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        payload = structured_error(ToolError("unexpected_error", "Unexpected tool failure.",
                                            {"error": str(exc), "type": type(exc).__name__}, 1), command)
        if not args.quiet:
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
