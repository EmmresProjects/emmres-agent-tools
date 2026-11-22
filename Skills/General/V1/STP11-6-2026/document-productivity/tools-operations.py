#!/usr/bin/env python3
"""Operation engine for the unified document-productivity Skill Tool.

The engine owns capability dispatch, validation, safe formulas, approval gates,
bounded execution, atomic output, recovery metadata, and structured results.
Category-specific handlers are registered from tools-category-handlers.py by
the primary CLI.
"""
from __future__ import annotations

import ast
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

dp_safety = __import__("sys").modules["dp_safety"]
dp_formats = __import__("sys").modules["dp_formats"]
dp_catalog = __import__("sys").modules["dp_catalog"]
ToolError = dp_safety.ToolError
RunGuard = dp_safety.RunGuard
resolve_user_path = dp_safety.resolve_user_path
root_relative = dp_safety.root_relative
display_path = dp_safety.display_path
slugify = dp_safety.slugify
json_ready = dp_safety.json_ready
sha256_bytes = dp_safety.sha256_bytes
atomic_write = dp_safety.atomic_write
retry_call = dp_safety.retry_call
load_json = dp_safety.load_json
Payload = dp_formats.Payload
Artifact = dp_formats.Artifact
InputData = dp_formats.InputData

FORMAT_ALIASES = {"markdown": "md", "text": "txt", "htm": "html"}


def normalize_format(value: str | None) -> str | None:
    if not value:
        return None
    fmt = str(value).lower().lstrip(".")
    return FORMAT_ALIASES.get(fmt, fmt)


@dataclass
class OutputSpec:
    requested: str | None
    directory: Path
    primary_path: Path
    report_path: Path
    primary_format: str
    directory_style: bool

    def to_dict(self) -> dict[str, Any]:
        return {"requested": self.requested, "directory": display_path(self.directory),
                "primary_path": display_path(self.primary_path),
                "report_path": display_path(self.report_path),
                "primary_format": self.primary_format,
                "directory_style": self.directory_style}

    @classmethod
    def build(cls, output: str | None, fmt: str, report: str | None,
              config: Any, tool_dir: Path, cwd: Path | None = None,
              capability_slug: str = "result") -> "OutputSpec":
        cwd = (cwd or Path.cwd()).resolve()
        fmt = normalize_format(fmt) or "json"
        if fmt not in dp_formats.SUPPORTED_FORMATS:
            raise ToolError("unsupported_format", f"Output format '{fmt}' is not supported.",
                            {"format": fmt, "supported": sorted(dp_formats.SUPPORTED_FORMATS)})
        storage_root = config.get("storage.root", "./out")
        base = resolve_user_path(storage_root, tool_dir, cwd)
        if output:
            target = resolve_user_path(output, tool_dir, cwd)
            ext = normalize_format(dp_formats.format_from_path(target))
            directory_style = target.is_dir() or (ext is None and not target.suffix)
            if directory_style:
                directory = target
                primary = directory / f"primary.{fmt}"
            else:
                if ext not in dp_formats.SUPPORTED_FORMATS:
                    raise ToolError("unsupported_output_extension",
                                    "Output file extension is not a supported artifact format.",
                                    {"path": display_path(target), "extension": ext})
                directory = target.parent
                primary = target.with_suffix("." + fmt)
            report_path = (resolve_user_path(report, tool_dir, cwd) if report
                           else (directory / "result.json" if directory_style
                                 else directory / f"{target.stem}.report.json"))
        else:
            directory = base / slugify(capability_slug, "result")
            primary = directory / f"primary.{fmt}"
            report_path = (resolve_user_path(report, tool_dir, cwd) if report
                           else directory / "result.json")
        for candidate in (primary, report_path):
            try:
                candidate.resolve().relative_to(tool_dir.resolve())
            except ValueError:
                continue
            raise ToolError("output_inside_package",
                            "Outputs must be written outside the Skill Tool folder.",
                            {"path": display_path(candidate)})
        return cls(output, directory, primary, report_path, fmt, directory_style)


class SafeFormulaEvaluator:
    """Evaluate a tiny arithmetic expression without executing Python code."""

    BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
              ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
              ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
    UNARYOPS = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}
    FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sum": sum,
             "floor": math.floor, "ceil": math.ceil, "sqrt": math.sqrt}

    def __init__(self, expression: str, limits: dict[str, Any]):
        self.expression = expression.strip()
        self.max_chars = int(limits.get("max_formula_chars", 500))
        self.max_nodes = int(limits.get("max_ast_nodes", 120))
        if not self.expression:
            raise ToolError("invalid_formula", "Formula is empty.")
        if len(self.expression) > self.max_chars:
            raise ToolError("invalid_formula", "Formula exceeds the configured character limit.",
                            {"length": len(self.expression), "max_chars": self.max_chars})
        if any(token in self.expression for token in ("__", "import", "lambda", ";", "\n")):
            raise ToolError("unsafe_formula", "Formula contains disallowed syntax.")
        try:
            self.tree = ast.parse(self.expression, mode="eval")
        except SyntaxError as exc:
            raise ToolError("invalid_formula", "Formula could not be parsed.",
                            {"error": str(exc)}) from exc
        nodes = list(ast.walk(self.tree))
        if len(nodes) > self.max_nodes:
            raise ToolError("unsafe_formula", "Formula exceeds the configured AST node limit.",
                            {"nodes": len(nodes), "max_nodes": self.max_nodes})
        self._validate(self.tree)

    def _validate(self, node: ast.AST) -> None:
        allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp,
                   ast.UnaryOp, ast.Call, ast.List, ast.Tuple, ast.keyword)
        if not isinstance(node, allowed):
            raise ToolError("unsafe_formula", f"Formula node {type(node).__name__} is not allowed.")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in self.FUNCS:
                raise ToolError("unsafe_formula", "Only whitelisted math functions are allowed.")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ToolError("unsafe_formula", "Private names are not allowed.")
        for child in ast.iter_child_nodes(node):
            self._validate(child)

    @staticmethod
    def _finite(value: Any) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ToolError("formula_result_not_finite", "Formula produced a non-finite result.")
        return result

    def evaluate(self, row: dict[str, Any], constants: dict[str, Any] | None = None) -> float:
        env: dict[str, Any] = {}
        for key, value in row.items():
            safe = re.sub(r"[^A-Za-z0-9_]", "_", str(key))
            env[safe] = dp_formats._number(value)
        for key, value in (constants or {}).items():
            safe = re.sub(r"[^A-Za-z0-9_]", "_", str(key))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolError("invalid_formula_constant", "Formula constants must be numeric.",
                                {"constant": key})
            env[safe] = float(value)

        def visit(node: ast.AST) -> Any:
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    raise ToolError("invalid_formula", "Only numeric literals are allowed.")
                return float(node.value)
            if isinstance(node, ast.Name):
                if node.id in self.FUNCS:
                    raise ToolError("unsafe_formula", "A function name cannot be used as a value.")
                if node.id not in env:
                    raise ToolError("unknown_formula_name",
                                    f"Formula name '{node.id}' is not an input column or constant.",
                                    {"available": sorted(env)})
                return env[node.id]
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                op = type(node.op)
                if op not in self.BINOPS:
                    raise ToolError("unsafe_formula", f"Operator {op.__name__} is not allowed.")
                if op is ast.Div and right == 0:
                    raise ToolError("formula_division_by_zero", "Formula attempted division by zero.")
                if op is ast.Pow and (abs(right) > 8 or abs(left) > 1e9):
                    raise ToolError("unsafe_formula", "Exponent is outside safe bounds.")
                return self._finite(self.BINOPS[op](left, right))
            if isinstance(node, ast.UnaryOp):
                op = type(node.op)
                if op not in self.UNARYOPS:
                    raise ToolError("unsafe_formula", f"Unary operator {op.__name__} is not allowed.")
                return self._finite(self.UNARYOPS[op](visit(node.operand)))
            if isinstance(node, (ast.List, ast.Tuple)):
                return [self._finite(visit(x)) for x in node.elts]
            if isinstance(node, ast.Call):
                func = self.FUNCS[node.func.id]
                args = [visit(arg) for arg in node.args]
                if node.keywords:
                    raise ToolError("unsafe_formula", "Keyword arguments are not allowed in formulas.")
                if node.func.id == "sqrt" and args and args[0] < 0:
                    raise ToolError("invalid_formula", "Square root of a negative number is not supported.")
                return self._finite(func(*args))
            raise ToolError("unsafe_formula", f"Formula node {type(node).__name__} is not allowed.")

        return self._finite(visit(self.tree))


@dataclass
class HandlerResult:
    payload: Payload
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    extra_artifacts: list[Artifact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    recovery: dict[str, Any] = field(default_factory=dict)


class OperationContext:
    def __init__(self, engine: "OperationEngine", capability: dict[str, Any],
                 category: dict[str, Any], options: dict[str, Any], input_path: Path | None,
                 output: OutputSpec, guard: RunGuard, dry_run: bool, approval: dict[str, Any]):
        self.engine = engine
        self.capability = capability
        self.category = category
        self.options = options
        self.input_path = input_path
        self.output = output
        self.guard = guard
        self.dry_run = dry_run
        self.approval = approval
        self._input: InputData | None = None
        self.started = time.monotonic()

    @property
    def config(self):
        return self.engine.config

    @property
    def policy(self):
        return self.engine.policy

    @property
    def catalog(self):
        return self.engine.catalog

    @property
    def tool_dir(self):
        return self.engine.tool_dir

    def check_guard(self, point: str) -> None:
        self.guard.check(point)

    def input(self) -> InputData:
        if self._input is None:
            if self.input_path is None:
                raise ToolError("input_required", "This capability requires --input.",
                                {"capability": self.capability.get("name")})
            if not self.input_path.exists():
                raise ToolError("input_not_found", "Input path does not exist.",
                                {"path": display_path(self.input_path)})
            if self.input_path.is_symlink():
                raise ToolError("unsafe_symlink", "Input symlinks are not accepted.",
                                {"path": display_path(self.input_path)})
            self._input = dp_formats.read_input(self.input_path, self.options,
                                                self.config.limits, self.guard)
        return self._input

    def has_input(self) -> bool:
        return self.input_path is not None and self.input_path.exists()

    def require_records(self, min_rows: int = 1) -> tuple[list[str], list[list[Any]]]:
        data = self.input()
        if len(data.records) < min_rows:
            raise ToolError("insufficient_records",
                            "This capability requires tabular input records.",
                            {"records": len(data.records), "min_rows": min_rows,
                             "path": display_path(data.path)})
        headers = data.headers or list(data.records[0].keys())
        rows = [[record.get(h, "") for h in headers] for record in data.records]
        return dp_formats.coerce_table(headers, rows)

    def text_lines(self, limit: int = 40) -> list[str]:
        if self.has_input():
            data = self.input()
            lines = [line.strip() for line in data.text.splitlines() if line.strip()]
            return lines[:limit]
        return [f"Capability: {self.capability.get('name')}"]

    def base_metadata(self) -> dict[str, Any]:
        provider = self.provider_info()
        prompts = self.prompt_info()
        return {
            "tool": self.catalog.data.get("tool_name", "document-productivity"),
            "capability": self.capability.get("name"),
            "capability_id": self.capability.get("id"),
            "category": self.category.get("name"),
            "operation": self.capability.get("operation"),
            "handler": self.capability.get("handler"),
            "action": self.capability.get("action", "operate"),
            "responsibility": self.capability.get("responsibility"),
            "provider": provider.get("name"),
            "provider_mode": provider.get("adapter_mode"),
            "prompt_set": prompts.get("set_id"),
            "policy": display_path(self.policy.path, self.tool_dir),
            "input": display_path(self.input_path) if self.input_path else None,
            "output_primary": display_path(self.output.primary_path),
            "dry_run": self.dry_run,
            "approval": self.approval.get("status"),
        }

    def provider_info(self) -> dict[str, Any]:
        presets = self.engine.asset_json("provider_presets_path")
        name = str(self.options.get("provider") or self.capability.get("provider_hint") or
                   self.config.get("providers.default", "local"))
        provider = dict(presets.get("providers", {}).get(name, {}))
        provider.setdefault("name", name)
        provider.setdefault("enabled", False)
        if name != "local" or not provider.get("enabled", False):
            provider["adapter_mode"] = "plan-only"
            provider["network_called"] = False
        else:
            provider["adapter_mode"] = "stdlib-local"
            provider["network_called"] = False
        if self.config.get("providers.allow_remote_calls", False) or self.config.get("network.enabled", False):
            self.policy.check_blocked("network_request")
        return provider

    def prompt_info(self) -> dict[str, Any]:
        prompts = self.engine.asset_json("prompt_set_path")
        set_id = str(self.options.get("prompt_set") or self.config.get("prompts.default_set") or
                     prompts.get("default_set"))
        chosen = prompts.get("sets", {}).get(set_id, {})
        template_id = str(self.options.get("prompt_template") or
                          {"prompt": "summarize", "analyze": "qa", "validate": "qa",
                           "author": "draft", "transform": "rewrite", "govern": "classify",
                           "integrate": "formula"}.get(self.capability.get("action"), "summarize"))
        templates = chosen.get("templates", {})
        if template_id not in templates:
            template_id = sorted(templates)[0] if templates else "summarize"
        return {"set_id": set_id, "template_id": template_id,
                "system": chosen.get("system", ""),
                "template": templates.get(template_id, ""),
                "human_approval_required": bool(chosen.get("approval_gate")),
                "provenance_fields": chosen.get("provenance", [])}

    def design_tokens(self) -> dict[str, Any]:
        defaults = {
            "color-text": "#172033", "color-accent": "#2457a6", "color-panel": "#f5f8fc",
            "color-line": "#d9e0ea", "font-body": "Arial, Helvetica, sans-serif",
            "font-heading": "Georgia, serif", "space-1": "0.5rem", "space-2": "1rem",
            "radius-1": "4px", "border-1": "1px solid var(--color-line)",
            "max-width": "960px", "line-height": "1.55",
        }
        custom = self.options.get("tokens") or self.options.get("theme") or {}
        if isinstance(custom, dict):
            defaults.update({str(k): str(v) for k, v in custom.items()})
        return defaults

    def find_numeric_column(self, headers: list[str], rows: list[list[Any]],
                            preferred: str | None = None) -> int:
        if preferred and preferred in headers:
            return headers.index(preferred)
        for index, header in enumerate(headers):
            values = [dp_formats._number(row[index]) for row in rows if index < len(row)]
            if values and any(abs(v) > 0 for v in values):
                return index
        return 0

    def numeric_series(self, headers: list[str], rows: list[list[Any]],
                       column: str | None = None, label_column: str | None = None) -> tuple[list[str], list[float]]:
        label_index = headers.index(label_column) if label_column in headers else 0
        value_index = self.find_numeric_column(headers, rows, column)
        labels = [str(row[label_index]) if label_index < len(row) else f"row_{i+1}"
                  for i, row in enumerate(rows)]
        values = [dp_formats._number(row[value_index]) if value_index < len(row) else 0.0
                  for row in rows]
        return labels, values

    def formula(self, expression: str | None = None) -> SafeFormulaEvaluator:
        expr = expression or self.options.get("formula") or "units * unit_price - cost"
        return SafeFormulaEvaluator(str(expr), self.config.limits)

    def check(self, name: str, status: str, detail: str) -> dict[str, Any]:
        if status not in {"pass", "fail", "warn", "skipped"}:
            status = "warn"
        return {"name": name, "status": status, "detail": detail}

    def finding(self, fid: str, status: str, severity: str, title: str,
                detail: str, action: str) -> dict[str, Any]:
        return {"id": fid, "status": status, "severity": severity, "title": title,
                "detail": detail, "action": action}

    def document(self, title: str | None = None, subtitle: str | None = None,
                 sections: list[dict[str, Any]] | None = None,
                 metadata: dict[str, Any] | None = None) -> Payload:
        meta = self.base_metadata()
        meta.update(metadata or {})
        name = str(self.capability.get("name"))
        base_sections = [
            {"heading": "Capability", "paragraphs": [
                f"Capability: {name}",
                f"Category: {self.category.get('name')}",
                f"Responsibility: {self.capability.get('responsibility')}",
                f"Description: {self.capability.get('description')}",
            ], "bullets": [], "table": None},
            {"heading": "Execution", "paragraphs": [
                f"Operation: {self.capability.get('operation')}",
                f"Handler: {self.capability.get('handler')}",
                f"Action: {self.capability.get('action', 'operate')}",
                f"Approval: {self.approval.get('status')}",
            ], "bullets": [], "table": None},
        ]
        return dp_formats.document_payload(title or name,
                                           subtitle or str(self.category.get("purpose", "")),
                                           base_sections + (sections or []), meta)

    def report(self, title: str | None = None, summary: str | None = None,
               findings: list[dict[str, Any]] | None = None,
               checks: list[dict[str, Any]] | None = None,
               metrics: dict[str, Any] | None = None,
               metadata: dict[str, Any] | None = None) -> Payload:
        meta = self.base_metadata()
        meta.update(metadata or {})
        return dp_formats.report_payload(
            title or f"{self.capability.get('name')} report",
            summary or f"Executed {self.capability.get('name')} through the unified operation engine.",
            findings or [], checks or [], metrics or {}, meta)

    def table(self, title: str | None = None, headers: list[str] | None = None,
              rows: list[list[Any]] | None = None,
              metadata: dict[str, Any] | None = None) -> Payload:
        meta = self.base_metadata()
        meta.update(metadata or {})
        return dp_formats.table_payload(title or f"{self.capability.get('name')} table",
                                        headers or [], rows or [], meta)

    def chart(self, title: str | None = None, labels: list[str] | None = None,
              values: list[float] | None = None, series_name: str = "value",
              chart_type: str | None = None,
              metadata: dict[str, Any] | None = None) -> Payload:
        meta = self.base_metadata()
        meta.update(metadata or {})
        return dp_formats.chart_payload(title or f"{self.capability.get('name')} chart",
                                        labels or [], [{"name": series_name, "values": values or []}],
                                        chart_type or str(self.options.get("chart_type", "bar")), meta)

    def text(self, title: str | None = None, text: str | None = None,
             media_type: str = "text/plain",
             metadata: dict[str, Any] | None = None) -> Payload:
        meta = self.base_metadata()
        meta.update(metadata or {})
        return dp_formats.text_payload(title or f"{self.capability.get('name')} asset",
                                       text or "", media_type, meta)

    def bundle(self, title: str | None = None, files: list[dict[str, Any]] | None = None,
               metadata: dict[str, Any] | None = None) -> Payload:
        meta = self.base_metadata()
        meta.update(metadata or {})
        return dp_formats.bundle_payload(title or f"{self.capability.get('name')} bundle",
                                         files or [], meta)

    def art(self, name: str, fmt: str, content: bytes | str,
            description: str = "", primary: bool = False) -> Artifact:
        pure = Path(name)
        if not name or pure.is_absolute() or ".." in pure.parts:
            raise ToolError("invalid_artifact_name", "Extra artifact names must be relative and safe.",
                            {"name": name})
        if fmt.lower().lstrip(".") not in dp_formats.SUPPORTED_FORMATS:
            raise ToolError("unsupported_format", "Extra artifact format is not supported.",
                            {"name": name, "format": fmt})
        if isinstance(content, str):
            content = content.encode("utf-8")
        return Artifact(name, fmt.lower().lstrip("."), content, primary, description)


class OperationEngine:
    def __init__(self, catalog: Any, config: Any, policy: Any, tool_dir: Path,
                 cwd: Path | None = None):
        self.catalog = catalog
        self.config = config
        self.policy = policy
        self.tool_dir = tool_dir
        self.cwd = (cwd or Path.cwd()).resolve()
        self.handlers: dict[str, Callable[[OperationContext, dict[str, Any]], HandlerResult]] = {}
        self.assets: dict[str, Any] = {}
        self.register("op_generic_capability", self.generic_handler)

    def register(self, name: str, func: Callable[[OperationContext, dict[str, Any]], HandlerResult]) -> None:
        self.handlers[name] = func

    def register_module(self, module: Any) -> None:
        for name in dir(module):
            if name.startswith("op_") and callable(getattr(module, name)):
                self.register(name, getattr(module, name))

    def asset_json(self, key: str) -> dict[str, Any]:
        if key not in self.assets:
            path = self.config.internal_path(key)
            self.assets[key] = load_json(path)
        return self.assets[key]

    def template(self) -> str:
        path = self.config.internal_path("document_template_path")
        return path.read_text(encoding="utf-8")

    def validate_registry(self) -> dict[str, Any]:
        missing = []
        for skill, _ in self.catalog.all_capabilities():
            if skill.get("handler") not in self.handlers:
                missing.append({"id": skill.get("id"), "handler": skill.get("handler")})
        return {"ok": not missing, "registered_handlers": sorted(self.handlers),
                "missing": missing}

    def resolve(self, identifier: str, plan: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if plan:
            for cap in plan.get("capabilities", []):
                if identifier in {cap.get("id"), cap.get("name"), cap.get("slug"), cap.get("global_slug")}:
                    category = next((c for c in plan.get("categories", [])
                                     if norm_name(c.get("name")) == norm_name(cap.get("category_name"))), {})
                    category = {"name": cap.get("category_name", category.get("name", "Custom Capability")),
                                "slug": cap.get("category_slug", category.get("slug")),
                                "purpose": category.get("purpose", ""),
                                "operation": cap.get("operation"),
                                "handler": cap.get("handler")}
                    return cap, category
        return self.catalog.resolve_capability(identifier)

    def generic_handler(self, ctx: OperationContext, capability: dict[str, Any]) -> HandlerResult:
        lines = ctx.text_lines(20) if ctx.has_input() else []
        sections = [
            {"heading": "Source grounding", "paragraphs": lines or [
                "No input file was supplied; this generic capability produced a bounded execution plan."],
             "bullets": [], "table": None},
            {"heading": "Unified responsibility", "paragraphs": [
                str(capability.get("responsibility") or capability.get("description"))],
             "bullets": [], "table": None},
            {"heading": "Operator options", "paragraphs": [],
             "bullets": [f"{k}: {v}" for k, v in sorted(ctx.options.items())] or ["No custom options supplied."],
             "table": None},
        ]
        payload = ctx.document(sections=sections)
        checks = [ctx.check("generic-handler", "pass", "Custom capability was normalized into the unified plan."),
                  ctx.check("no-arbitrary-code", "pass", "No code, shell, or network action was executed.")]
        return HandlerResult(payload, checks=checks,
                             metrics={"options": len(ctx.options), "source_lines": len(lines)},
                             notes=["Custom capabilities use the bounded generic handler."])

    def output_spec(self, output: str | None, fmt: str, report: str | None,
                    capability_slug: str) -> OutputSpec:
        return OutputSpec.build(output, fmt, report, self.config, self.tool_dir,
                                self.cwd, capability_slug)

    def run(self, capability: dict[str, Any], category: dict[str, Any], options: dict[str, Any],
            input_path: Path | None, output: OutputSpec, fmt: str | None = None,
            dry_run: bool = False, overwrite: bool = False, approval_token: str | None = None,
            approval_reason: str | None = None, guard: RunGuard | None = None,
            write_result: bool = True, command: str = "run") -> dict[str, Any]:
        guard = guard or RunGuard(float(self.config.limits.get("timeout_seconds", 60)))
        fmt = normalize_format(fmt or capability.get("default_format") or "json")
        supported = [normalize_format(x) for x in capability.get("supported_formats", ["json"])]
        if fmt not in supported:
            raise ToolError("format_not_supported_for_capability",
                            "The requested format is not supported for this capability.",
                            {"capability": capability.get("name"), "format": fmt,
                             "supported": supported})
        handler_name = str(capability.get("handler") or self.GENERIC_HANDLER())
        if handler_name not in self.handlers:
            raise ToolError("handler_not_registered", "Capability handler is not registered.",
                            {"capability": capability.get("name"), "handler": handler_name})
        classes = self.policy.required_classes(capability, category, overwrite,
                                               str(capability.get("action", "operate")))
        approval = self.policy.check_approval(classes, approval_token, approval_reason,
                                              str(self.config.get("approval.token_env")), dry_run)
        # Validate paths before any handler work begins.
        if input_path is not None:
            if not input_path.exists():
                raise ToolError("input_not_found", "Input path does not exist.",
                                {"path": display_path(input_path)})
            if input_path.is_symlink():
                raise ToolError("unsafe_symlink", "Input symlinks are not accepted.",
                                {"path": display_path(input_path)})
        targets = [output.primary_path, output.report_path]
        existing = [display_path(p) for p in targets if p.exists()]
        if existing and not overwrite and not dry_run:
            raise ToolError("output_exists", "Output or report path exists. Use --overwrite with approval.",
                            {"paths": existing})
        started = time.monotonic()
        with guard:
            guard.check("preflight")
            ctx = OperationContext(self, capability, category, options, input_path,
                                   output, guard, dry_run, approval)
            result_handler = self.handlers[handler_name](ctx, capability)
            guard.check("handler")
            if not isinstance(result_handler, HandlerResult):
                raise ToolError("invalid_handler_result", "Handler returned an invalid result.",
                                {"handler": handler_name})
            primary_content = dp_formats.render(
                result_handler.payload, fmt, options, self.template(),
                extra_files=[{"name": a.name, "content": a.content, "format": a.format,
                              "description": a.description} for a in result_handler.extra_artifacts])
            guard.check("render")
            report_payload = self._build_result(
                command, capability, category, ctx, result_handler, fmt, output,
                primary_content, [], dry_run, approval, started)
            if dry_run:
                report_payload["checks"].append(ctx.check("dry-run", "pass",
                    "All artifacts were rendered in memory; no files were written."))
                report_payload["metrics"]["dry_run"] = True
                report_payload["artifacts_preview"] = [
                    {"name": a["name"], "format": a["format"], "path": a["path"],
                     "bytes": a["bytes"], "sha256": a["sha256"], "primary": a["primary"]}
                    for a in report_payload["artifacts"]]
                report_payload["artifacts"] = []
                return report_payload
            written = self._write_outputs(output, primary_content, result_handler.extra_artifacts,
                                          overwrite, guard)
            report_payload["artifacts"] = written["artifacts"]
            report_payload["checks"].extend(written["checks"])
            report_payload["recovery"] = written["recovery"]
            if write_result:
                report_payload["checks"].append(ctx.check("structured-report", "pass",
                    "Final JSON report is written with complete artifact metadata."))
                report_payload["report"] = {
                    "written": True,
                    "path": display_path(output.report_path),
                    "format": "json",
                    "atomic": bool(self.config.get("storage.atomic", True)),
                    "backup": bool(self.config.get("storage.backup", True)),
                    "backup_suffix": str(self.config.get("storage.backup_suffix", ".bak")),
                }
                body_bytes = (json.dumps(report_payload, indent=2, ensure_ascii=False,
                                         default=str) + "\n").encode("utf-8")
                json.loads(body_bytes.decode("utf-8"))
                report_payload["metrics"]["report_payload_inspection"] = {
                    "format": "json",
                    "bytes": len(body_bytes),
                    "sha256": sha256_bytes(body_bytes),
                    "parsed": "json",
                    "note": "Inspection of the report body before this inspection record was appended.",
                }
                final_bytes = (json.dumps(report_payload, indent=2, ensure_ascii=False,
                                          default=str) + "\n").encode("utf-8")
                atomic_write(
                    output.report_path, final_bytes, overwrite=overwrite,
                    backup=bool(self.config.get("storage.backup", True)),
                    backup_suffix=str(self.config.get("storage.backup_suffix", ".bak")),
                    max_bytes=int(self.config.limits.get("max_output_bytes", 104857600)),
                    guard=guard, attempts=int(self.config.limits.get("retry_count", 2)),
                    delay_ms=int(self.config.limits.get("retry_delay_ms", 100)))
            else:
                report_payload["checks"].append(ctx.check("structured-report", "skipped",
                    "Per-item report writing was disabled by the batch caller."))
                report_payload["report"] = {"written": False,
                                            "planned_path": display_path(output.report_path)}
            return report_payload

    @staticmethod
    def GENERIC_HANDLER() -> str:
        return "op_generic_capability"

    def _build_result(self, command: str, capability: dict[str, Any], category: dict[str, Any],
                      ctx: OperationContext, handler: HandlerResult, fmt: str,
                      output: OutputSpec, primary_content: bytes,
                      written_artifacts: list[dict[str, Any]], dry_run: bool,
                      approval: dict[str, Any], started: float) -> dict[str, Any]:
        primary_name = output.primary_path.name
        artifact_entries = [{
            "name": primary_name, "format": fmt, "path": display_path(output.primary_path),
            "bytes": len(primary_content), "sha256": sha256_bytes(primary_content),
            "primary": True, "description": "Primary capability artifact",
        }]
        for artifact in handler.extra_artifacts:
            target = output.directory / artifact.name
            artifact_entries.append({
                "name": artifact.name, "format": artifact.format,
                "path": display_path(target), "bytes": len(artifact.content),
                "sha256": sha256_bytes(artifact.content), "primary": False,
                "description": artifact.description,
            })
        checks = [
            ctx.check("capability-registry", "pass",
                      f"Resolved {capability.get('name')} to {capability.get('operation')}."),
            ctx.check("handler-dispatch", "pass",
                      f"Executed {capability.get('handler')}."),
            ctx.check("format-validation", "pass",
                      f"Requested format {fmt} is supported and was rendered."),
            ctx.check("approval-gate", "pass" if approval.get("status") != "denied" else "fail",
                      f"Approval status: {approval.get('status')}."),
            ctx.check("bounded-execution", "pass",
                      "Timeout, cancellation, retries, and cleanup are enforced by RunGuard."),
        ] + list(handler.checks)
        warnings = list(handler.warnings)
        if capability.get("external_integration"):
            warnings.append("External provider is disabled; only a local integration plan was produced.")
        if approval.get("status") == "deferred-dry-run":
            warnings.append("Sensitive capability approval is deferred because this dry run writes nothing.")
        if ctx.has_input():
            try:
                data = ctx.input()
                warnings.extend(data.metadata.get("reader_limitations", []))
            except ToolError:
                pass
        metrics = {
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "primary_bytes": len(primary_content),
            "extra_artifacts": len(handler.extra_artifacts),
            "input_present": ctx.has_input(),
            "options": len(ctx.options),
            "handler_notes": handler.notes,
            "provider": ctx.provider_info(),
            "prompt": ctx.prompt_info(),
        }
        metrics.update(handler.metrics)
        return {
            "ok": True, "command": command, "capability": capability.get("name"),
            "capability_id": capability.get("id"), "category": category.get("name"),
            "category_slug": capability.get("category_slug") or category.get("slug"),
            "operation": capability.get("operation"), "handler": capability.get("handler"),
            "action": capability.get("action", "operate"),
            "input": display_path(ctx.input_path) if ctx.input_path else None,
            "output": output.to_dict(), "format": fmt, "dry_run": dry_run,
            "approval": approval, "policy": {
                "path": display_path(self.policy.path, self.tool_dir),
                "classes": self.policy.classify(capability.get("name", ""),
                                                capability.get("description", ""),
                                                category.get("name", "")),
                "blocked_actions": sorted(self.policy.blocked),
            },
            "providers": {"network_enabled": False,
                          "selected": metrics["provider"].get("name"),
                          "adapter_mode": metrics["provider"].get("adapter_mode")},
            "prompts": {"set_id": metrics["prompt"].get("set_id"),
                        "template_id": metrics["prompt"].get("template_id")},
            "artifacts": written_artifacts or artifact_entries,
            "checks": checks, "errors": [], "warnings": warnings,
            "metrics": json_ready(metrics),
            "limits": json_ready(self.config.limits),
            "recovery": {"atomic_writes": bool(self.config.get("storage.atomic", True)),
                         "backup": bool(self.config.get("storage.backup", True)),
                         "backup_suffix": self.config.get("storage.backup_suffix", ".bak"),
                         "cleanup": "partial and temporary files are removed after each write",
                         "restore": "use the recover command with --backup and --output"},
            "payload": {"kind": handler.payload.kind, "title": handler.payload.title,
                        "subtitle": handler.payload.subtitle},
        }

    def _write_outputs(self, output: OutputSpec, primary_content: bytes,
                       extras: list[Artifact], overwrite: bool,
                       guard: RunGuard) -> dict[str, Any]:
        max_bytes = int(self.config.limits.get("max_output_bytes", 104857600))
        attempts = int(self.config.limits.get("retry_count", 2))
        delay = int(self.config.limits.get("retry_delay_ms", 100))
        backup = bool(self.config.get("storage.backup", True))
        suffix = str(self.config.get("storage.backup_suffix", ".bak"))
        artifacts: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        primary_info = atomic_write(output.primary_path, primary_content, overwrite=overwrite,
                                    backup=backup, backup_suffix=suffix, max_bytes=max_bytes,
                                    guard=guard, attempts=attempts, delay_ms=delay)
        artifacts.append({"name": output.primary_path.name, "format": output.primary_format,
                          "path": primary_info["path"], "bytes": primary_info["bytes"],
                          "sha256": primary_info["sha256"], "primary": True,
                          "description": "Primary capability artifact"})
        inspected = dp_formats.validate_artifact(output.primary_path, guard)
        checks.append({"name": "primary-artifact-inspection", "status": "pass",
                       "detail": f"{output.primary_format} parsed with {inspected['details']}"})
        for artifact in extras:
            target = output.directory / artifact.name
            info = atomic_write(target, artifact.content, overwrite=overwrite, backup=backup,
                                backup_suffix=suffix, max_bytes=max_bytes, guard=guard,
                                attempts=attempts, delay_ms=delay)
            artifacts.append({"name": artifact.name, "format": artifact.format,
                              "path": info["path"], "bytes": info["bytes"],
                              "sha256": info["sha256"], "primary": False,
                              "description": artifact.description})
            inspected = dp_formats.validate_artifact(target, guard)
            checks.append({"name": f"artifact-{artifact.name}", "status": "pass",
                           "detail": f"{artifact.format} parsed and hashed."})
        backups = [info["backup"] for info in [primary_info] if info.get("backup")]
        recovery = {"backup_suffix": suffix, "atomic_writes": True,
                    "backups": backups,
                    "cleanup": "partial and temporary files removed",
                    "restore": "python3 /document-productivity/tools-document-productivity.py recover --backup BACKUP --output OUTPUT --overwrite --approval-token TOKEN --approval-reason REASON"}
        return {"artifacts": artifacts, "checks": checks, "recovery": recovery}


def norm_name(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()
