#!/usr/bin/env python3
"""CLI engine and deterministic planning workflows for Ultimate Web Maker."""
from __future__ import annotations

import argparse
import copy
import html
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
CATALOG_FILE = "asset-capabilities.json"
DEFAULT_CONFIG_FILE = "asset-default-config.json"
BRIEF_TEMPLATE_FILE = "asset-project-brief-template.md"
SITE_TEMPLATES = {
    "index.html": "asset-index-template.html",
    "styles.css": "asset-styles-template.css",
    "app.js": "asset-app-template.js",
    "README.md": "asset-site-readme-template.md",
}
SEVERITIES = ("critical", "high", "medium", "low", "info")


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str = "", exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.exit_code = exit_code


class ToolArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ToolError("invalid_arguments", message, "Run the command with --help to see accepted options.")


def _load_sibling_tool(filename: str, module_name: str):
    path = ROOT / filename
    if not path.is_file():
        raise ToolError("missing_tool_file", f"Required runtime file {filename!r} is missing.", "Restore all tools-* files from the package archive.")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ToolError("tool_load_error", f"Could not load {filename!r}.", "Restore the package files without renaming them.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ToolError("tool_load_error", f"Could not initialize {filename!r} ({type(exc).__name__}).", "Check that the Python runtime supports this package and that files were not edited incompletely.") from None
    return module


STATIC_AUDIT = _load_sibling_tool("tools-static-audit.py", "ultimate_web_maker_static_audit")


def _load_json_bytes(raw: bytes, label: str) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise ToolError("invalid_encoding", f"{label} must be UTF-8 text.", "Save it as UTF-8 and try again.") from None
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_json", f"{label} is not valid JSON (line {exc.lineno}, column {exc.colno}).", "Correct the JSON syntax and retry.") from None
    if not isinstance(obj, dict):
        raise ToolError("invalid_json_shape", f"{label} must contain a JSON object at its top level.", "Use an object with named fields.")
    return obj


def _read_bounded_file(path_value: str | Path, label: str, max_bytes: int) -> bytes:
    path = Path(path_value).expanduser()
    try:
        if not path.is_file():
            raise ToolError("missing_input_file", f"{label} is not an existing regular file.", "Check the path and pass a readable file.")
        size = path.stat().st_size
        if size > max_bytes:
            raise ToolError("input_too_large", f"{label} exceeds the configured {max_bytes}-byte limit.", "Use a smaller input or increase the relevant limit within the package's hard maximum.")
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except ToolError:
        raise
    except OSError as exc:
        raise ToolError("input_read_error", f"Could not read {label} ({exc.strerror or type(exc).__name__}).", "Check permissions and try a local readable path.") from None
    if len(raw) > max_bytes:
        raise ToolError("input_too_large", f"{label} grew beyond the configured {max_bytes}-byte limit while being read.", "Use a stable, smaller input file.")
    return raw


def _load_asset_text(filename: str, max_bytes: int = 2_000_000) -> str:
    if not filename.startswith("asset-") or "/" in filename or "\\" in filename:
        raise ToolError("unsafe_asset_reference", "An internal asset reference is not a package-root asset filename.", "Restore the exact asset-* filenames from the package.")
    path = ROOT / filename
    try:
        raw = path.read_bytes()
    except OSError:
        raise ToolError("missing_asset", f"Required asset {filename!r} is missing or unreadable.", "Restore the asset files directly inside the ultimate-web-maker/ folder.") from None
    if len(raw) > max_bytes:
        raise ToolError("asset_too_large", f"Asset {filename!r} exceeds its safe read limit.", "Check the asset contents and restore the unmodified package version.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ToolError("invalid_asset_encoding", f"Asset {filename!r} is not UTF-8 text.", "Restore the UTF-8 asset from the package.") from None


def _load_catalog() -> dict:
    obj = _load_json_bytes(_load_asset_text(CATALOG_FILE).encode("utf-8"), CATALOG_FILE)
    categories = obj.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ToolError("invalid_catalog", "The capability catalog has no category list.", "Restore asset-capabilities.json.")
    seen_category_ids: set[int] = set()
    seen_names: set[str] = set()
    count = 0
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("id"), int) or not isinstance(category.get("name"), str):
            raise ToolError("invalid_catalog", "The capability catalog contains a malformed category.", "Restore asset-capabilities.json or correct its JSON structure.")
        if category["id"] in seen_category_ids or not category["name"].strip():
            raise ToolError("invalid_catalog", "Category identifiers and names must be unique and non-empty.", "Correct asset-capabilities.json.")
        seen_category_ids.add(category["id"])
        skills = category.get("capabilities")
        if not isinstance(skills, list) or not skills:
            raise ToolError("invalid_catalog", f"Category {category['id']} has no capabilities.", "Correct asset-capabilities.json.")
        for skill in skills:
            if not isinstance(skill, dict) or not isinstance(skill.get("name"), str) or not isinstance(skill.get("description"), str):
                raise ToolError("invalid_catalog", f"Category {category['id']} contains a malformed capability.", "Correct asset-capabilities.json.")
            key = _normalize_text(skill["name"])
            if not key or key in seen_names or not skill["description"].strip():
                raise ToolError("invalid_catalog", "Capability names must be unique and descriptions must be non-empty.", "Correct asset-capabilities.json.")
            seen_names.add(key)
            count += 1
    if obj.get("category_count") != len(categories) or obj.get("capability_count") != count:
        raise ToolError("invalid_catalog", "Catalog count metadata does not match its entries.", "Update category_count/capability_count or restore the package catalog.")
    return obj


def _read_custom_config(path_value: str, max_bytes: int) -> dict:
    raw = _read_bounded_file(path_value, "Configuration file", max_bytes)
    return _load_json_bytes(raw, "Configuration file")


def _merge_config(base: dict, override: dict, path: str = "") -> dict:
    if not isinstance(override, dict):
        raise ToolError("invalid_config", f"Configuration section {path or '<root>'} must be an object.", "Use a JSON object and only documented setting names.")
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key == "schema_version" and not path:
            if value != 1:
                raise ToolError("unsupported_config_version", "Only config schema_version 1 is supported.", "Use schema_version: 1.")
            result[key] = value
            continue
        full = f"{path}.{key}" if path else key
        if path == "profiles" and key not in result:
            if not isinstance(value, dict):
                raise ToolError("invalid_config", f"Custom profile {key!r} must be an object.", "Provide all required profile color and font fields.")
            result[key] = copy.deepcopy(value)
            continue
        if key not in result:
            raise ToolError("unknown_config_key", f"Unknown configuration setting {full!r}.", "Use the names in asset-default-config.json and knowledge-readme.md.")
        if isinstance(result[key], dict):
            result[key] = _merge_config(result[key], value, full)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_config(config: dict, catalog: dict) -> dict:
    if config.get("schema_version") != 1:
        raise ToolError("unsupported_config_version", "Only config schema_version 1 is supported.", "Use schema_version: 1.")
    numeric_bounds = {
        "max_brief_bytes": (1, 5_242_880),
        "max_selection_bytes": (1, 1_048_576),
        "max_config_bytes": (1, 2_097_152),
        "max_project_files": (1, 10_000),
        "max_project_entries": (1, 50_000),
        "max_file_bytes": (1, 5_242_880),
        "max_total_bytes": (1, 100_000_000),
        "max_depth": (1, 24),
        "max_findings": (1, 2_000),
        "operation_timeout_seconds": (1, 600),
    }
    limits = config.get("limits")
    if not isinstance(limits, dict):
        raise ToolError("invalid_config", "limits must be a JSON object.", "Restore or correct asset-default-config.json.")
    for key, (low, high) in numeric_bounds.items():
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ToolError("invalid_config_limit", f"limits.{key} must be an integer from {low} through {high}.", "Reduce the value to the documented hard maximum.")
    if limits["max_file_bytes"] > limits["max_total_bytes"]:
        raise ToolError("invalid_config_limit", "max_file_bytes cannot exceed max_total_bytes.", "Adjust the two byte limits consistently.")
    if limits["max_project_files"] > limits["max_project_entries"]:
        raise ToolError("invalid_config_limit", "max_project_files cannot exceed max_project_entries.", "Increase the filesystem-entry budget or lower the file budget.")
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        raise ToolError("invalid_config", "scanner must be a JSON object.", "Restore or correct asset-default-config.json.")
    skip_dirs = scanner.get("skip_directories")
    if not isinstance(skip_dirs, list) or any(not isinstance(x, str) or not x or x in {".", ".."} or "/" in x or "\\" in x for x in skip_dirs):
        raise ToolError("invalid_config", "scanner.skip_directories must be directory names without path separators.", "Use basenames only.")
    extensions = scanner.get("text_extensions")
    if not isinstance(extensions, list) or any(not isinstance(x, str) or not re.fullmatch(r"\.[a-z0-9][a-z0-9._-]{0,20}", x.casefold()) for x in extensions):
        raise ToolError("invalid_config", "scanner.text_extensions must be a list of dotted lowercase extensions.", "Use entries such as .html or .js.")
    if scanner.get("fail_on") not in {"none", "critical", "high", "medium", "low", "info"}:
        raise ToolError("invalid_config", "scanner.fail_on must be none, critical, high, medium, low, or info.", "Correct the audit gate setting.")
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ToolError("invalid_config", "profiles must contain at least one design profile.", "Restore or correct asset-default-config.json.")
    color_keys = {"background", "surface", "ink", "muted", "accent", "accent_ink"}
    for name, profile in profiles.items():
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not isinstance(profile, dict):
            raise ToolError("invalid_profile", f"Design profile name {name!r} is invalid.", "Use lowercase hyphenated profile names with a profile object.")
        if set(profile) != color_keys | {"font_stack"}:
            raise ToolError("invalid_profile", f"Profile {name!r} must define exactly the six color tokens and font_stack.", "Use the documented profile shape in asset-default-config.json.")
        for key in color_keys:
            if not isinstance(profile[key], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", profile[key]):
                raise ToolError("invalid_profile", f"Profile {name!r} token {key!r} must be a six-digit hex color.", "Use a value such as #123ABC.")
        font_stack = profile["font_stack"]
        if not isinstance(font_stack, str) or not 1 <= len(font_stack) <= 160 or not re.fullmatch(r"[-A-Za-z0-9 ,\"']+", font_stack):
            raise ToolError("invalid_profile", f"Profile {name!r} font_stack contains unsupported characters.", "Use a plain CSS font-family list without declarations or markup.")
    workflows = config.get("workflows")
    if not isinstance(workflows, dict):
        raise ToolError("invalid_config", "workflows must be a JSON object keyed by category id.", "Restore or correct asset-default-config.json.")
    expected_ids = {str(cat["id"]) for cat in catalog["categories"]}
    if set(workflows) != expected_ids:
        raise ToolError("invalid_config", "There must be one workflow entry for every catalog category.", "Add or restore the matching workflow entries.")
    allowed_kinds = {"planning", "research", "design", "audit", "orchestration"}
    for category_id, workflow in workflows.items():
        if not isinstance(workflow, dict) or workflow.get("kind") not in allowed_kinds:
            raise ToolError("invalid_config", f"Workflow {category_id} has an unsupported kind.", "Use planning, research, design, audit, or orchestration.")
        for key in ("deliverables", "checks", "audit_tags"):
            value = workflow.get(key)
            if not isinstance(value, list) or (key != "audit_tags" and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ToolError("invalid_config", f"Workflow {category_id}.{key} must be a list of non-empty strings.", "Correct the workflow entry.")
    policy = config.get("approval_policy")
    if (not isinstance(policy, dict)
            or not isinstance(policy.get("explicit_approval_required_for"), list)
            or not policy["explicit_approval_required_for"]
            or any(not isinstance(item, str) or not item.strip() for item in policy["explicit_approval_required_for"])):
        raise ToolError("invalid_config", "approval_policy must define explicit_approval_required_for as non-empty strings.", "Restore or correct the approval policy.")
    if policy.get("scaffold_overwrite") != "never" or policy.get("report_overwrite_flag") != "--force":
        raise ToolError("invalid_config", "The configured overwrite policy is not supported.", "Keep scaffold_overwrite=never and report_overwrite_flag=--force.")
    return config


def _load_config(custom_path: str | None, catalog: dict) -> dict:
    base = _load_json_bytes(_load_asset_text(DEFAULT_CONFIG_FILE).encode("utf-8"), DEFAULT_CONFIG_FILE)
    if custom_path:
        override = _read_custom_config(custom_path, base["limits"]["max_config_bytes"])
        base = _merge_config(base, override)
    return _validate_config(base, catalog)


def _normalize_text(text: str) -> str:
    text = html.unescape(text).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _strip_html(text: str) -> str:
    parser = STATIC_AUDIT.VisibleHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # Malformed pasted markup is treated as text after removing tag-like spans.
        return re.sub(r"<[^>]*>", " ", text)
    return "".join(parser.parts)


def _markdown_to_text(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*(?!\s)|(?<!\w)_(?!\s)|(?<!\s)\*(?!\w)|(?<!\s)_(?!\w)", "", text)
    return text


FIELD_ALIASES = {
    "title": "title", "project": "title", "project name": "title", "name": "title", "website": "title",
    "goal": "goal", "website goal": "goal", "purpose": "goal", "objective": "goal", "description": "goal",
    "audience": "audience", "users": "audience", "user group": "audience", "target audience": "audience",
    "features": "features", "feature scope": "features", "functionality": "features", "scope": "features",
    "content": "content", "pages": "content", "content needs": "content",
    "stack": "stack", "technology": "stack", "tech stack": "stack", "framework": "stack", "runtime": "stack",
    "integrations": "integrations", "services": "integrations", "apis": "integrations", "api": "integrations",
    "hosting": "hosting", "deployment": "hosting", "host": "hosting",
    "constraints": "constraints", "limits": "constraints", "non goals": "constraints", "non goals and constraints": "constraints",
    "success criteria": "success_criteria", "acceptance criteria": "success_criteria", "acceptance checks": "success_criteria", "success": "success_criteria",
    "language": "language", "locale": "language", "primary language": "language",
}
LIST_FIELDS = {"features", "content", "integrations", "constraints", "success_criteria"}


def _value_to_text(value: Any, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_value_to_text(item, label) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            rendered = _value_to_text(item, label)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "\n".join(parts)
    return ""


def _clean_value(value: str, max_chars: int = 4000) -> str:
    value = html.unescape(value)
    value = " ".join(value.replace("\x00", " ").split())
    return value[:max_chars].strip()


def _parse_brief(raw: str, source_name: str = "text") -> dict:
    if not raw.strip():
        raise ToolError("empty_brief", "The project brief is empty.", "Provide plain text, Markdown, copied HTML, or a JSON object.")
    raw = raw.lstrip("\ufeff")
    if "\x00" in raw:
        raise ToolError("binary_input", "The brief contains a NUL byte and is not accepted as text.", "Provide a UTF-8 text, Markdown, HTML, or JSON file.")
    first = raw.lstrip()[:1]
    is_json_input = source_name.casefold().endswith(".json") or first in {"{", "["}
    structured: dict[str, Any] = {}
    if is_json_input:
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolError("invalid_brief_json", f"The JSON brief is invalid (line {exc.lineno}, column {exc.colno}).", "Correct the JSON syntax or save the content as plain Markdown.") from None
        if not isinstance(candidate, dict):
            raise ToolError("invalid_brief_json", "A JSON brief must be an object with named fields.", "Use keys such as title, goal, audience, features, stack, and constraints.")
        for key, value in candidate.items():
            normalized_key = " ".join(re.findall(r"[a-z0-9]+", str(key).casefold()))
            target = FIELD_ALIASES.get(normalized_key)
            if target:
                rendered = _value_to_text(value, target)
                if rendered:
                    structured[target] = rendered
        visible = _strip_html(raw)
        text = _markdown_to_text(visible)
        # A JSON object should not become the fallback public-facing summary.
        general_lines: list[str] = []
    else:
        visible = _strip_html(raw)
        text = _markdown_to_text(visible)
        general_lines = []
        collected: dict[str, list[str]] = {}
        active_field: str | None = None
        label_pattern = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s*)?(?:\*\*)?([A-Za-z][A-Za-z _-]{0,45})(?:\*\*)?\s*:\s*(.*)$")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                active_field = None
                general_lines.append("")
                continue
            match = label_pattern.match(line)
            if match:
                label = " ".join(match.group(1).replace("_", " ").replace("-", " ").casefold().split())
                target = FIELD_ALIASES.get(label)
                if target:
                    active_field = target
                    collected.setdefault(target, [])
                    if match.group(2).strip():
                        collected[target].append(match.group(2).strip())
                    continue
                active_field = None
            if active_field:
                item = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
                if item:
                    collected.setdefault(active_field, []).append(item)
            else:
                general_lines.append(line)
        for key, lines in collected.items():
            if key in LIST_FIELDS:
                structured[key] = "\n".join(lines)
            else:
                structured[key] = " ".join(lines)
    fields: dict[str, Any] = {}
    for key in ("title", "goal", "audience", "stack", "hosting", "language"):
        fields[key] = _clean_value(str(structured.get(key, "")), 1600)
    for key in LIST_FIELDS:
        value = structured.get(key, "")
        if isinstance(value, list):
            candidates = value
        else:
            candidates = str(value).splitlines()
        cleaned = []
        seen = set()
        for item in candidates:
            item = _clean_value(str(item), 800)
            if item and _normalize_text(item) not in seen:
                seen.add(_normalize_text(item))
                cleaned.append(item)
        fields[key] = cleaned[:50]
    if not fields["title"]:
        source_lines = [line.strip() for line in (general_lines if not is_json_input else []) if line.strip()]
        if source_lines:
            fields["title"] = _clean_value(source_lines[0], 90)
        elif fields["goal"]:
            fields["title"] = _clean_value(fields["goal"].split(".")[0], 90)
        elif fields["features"]:
            fields["title"] = _clean_value(fields["features"][0], 90)
        else:
            fields["title"] = "Untitled web experience"
    if not fields["goal"]:
        source_lines = [line for line in (general_lines if not is_json_input else []) if line.strip()]
        if source_lines:
            if len(source_lines) > 1 and _normalize_text(source_lines[0]) == _normalize_text(fields["title"]):
                source_lines = source_lines[1:]
            if source_lines:
                fields["goal"] = _clean_value(" ".join(source_lines), 1200)
    fields["title"] = _clean_value(fields["title"], 90) or "Untitled web experience"
    fields["summary"] = _clean_value(fields["goal"] or (" ".join(fields["content"]) if fields["content"] else fields["title"]), 480)
    fields["source_kind"] = "json" if is_json_input else ("html-or-markdown" if re.search(r"<\s*[A-Za-z][^>]*>", raw) or re.search(r"(?m)^\s{0,3}#{1,6}\s", raw) else "plain-text")
    fields["input_characters"] = len(raw)
    return fields


def _read_input_source(args: argparse.Namespace, label: str, config: dict) -> tuple[str, str]:
    path_value = getattr(args, "brief", None)
    text_value = getattr(args, "text", None)
    if bool(path_value is not None) == bool(text_value is not None):
        raise ToolError("missing_or_ambiguous_input", f"Provide exactly one {label} using --brief PATH or --text TEXT.", "Use --brief - to read bounded UTF-8 content from standard input.")
    limit = config["limits"]["max_brief_bytes"]
    if path_value is not None:
        if path_value == "-":
            try:
                raw = sys.stdin.buffer.read(limit + 1)
            except OSError as exc:
                raise ToolError("stdin_read_error", f"Could not read standard input ({type(exc).__name__}).") from None
            source_name = "stdin"
        else:
            raw = _read_bounded_file(path_value, "Brief file", limit)
            source_name = str(path_value)
    else:
        raw = str(text_value).encode("utf-8")
        source_name = "text"
    if len(raw) > limit:
        raise ToolError("input_too_large", f"The {label} exceeds the configured {limit}-byte limit.", "Use a smaller brief or adjust max_brief_bytes within its hard maximum.")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ToolError("invalid_encoding", f"The {label} must be UTF-8 text.", "Save it as UTF-8 and retry.") from None
    return decoded, source_name


def _read_selection(args: argparse.Namespace, config: dict) -> str:
    selection = getattr(args, "selection", None)
    selection_file = getattr(args, "selection_file", None)
    if bool(selection is not None) == bool(selection_file is not None):
        raise ToolError("missing_or_ambiguous_selection", "Provide exactly one selection using --selection TEXT or --selection-file PATH.", "You may pass a single capability, numbered Markdown list, one category, several categories, or copied HTML.")
    max_bytes = config["limits"]["max_selection_bytes"]
    if selection_file is not None:
        raw = _read_bounded_file(selection_file, "Selection file", max_bytes)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ToolError("invalid_encoding", "The selection file must be UTF-8 text.", "Save it as UTF-8 and retry.") from None
    else:
        text = str(selection)
        if len(text.encode("utf-8")) > max_bytes:
            raise ToolError("selection_too_large", f"The selection exceeds the configured {max_bytes}-byte limit.", "Use a shorter selection or adjust max_selection_bytes within its hard maximum.")
    if not text.strip():
        raise ToolError("empty_selection", "The selection is empty.", "Name at least one catalog capability or category.")
    return text


def _category_by_id(catalog: dict, category_id: int) -> dict | None:
    return next((item for item in catalog["categories"] if item["id"] == category_id), None)


def _resolve_category(catalog: dict, selector: str) -> dict | None:
    normalized = _normalize_text(selector)
    match = re.fullmatch(r"category\s+(\d+)", normalized)
    if match:
        return _category_by_id(catalog, int(match.group(1)))
    exact = [cat for cat in catalog["categories"] if _normalize_text(cat["name"]) == normalized]
    if exact:
        return exact[0]
    partial = [cat for cat in catalog["categories"] if normalized and normalized in _normalize_text(cat["name"])]
    if len(partial) == 1:
        return partial[0]
    return None


def _resolve_capability(catalog: dict, selector: str) -> tuple[dict, dict] | None:
    normalized = _normalize_text(selector)
    for category in catalog["categories"]:
        for capability in category["capabilities"]:
            if _normalize_text(capability["name"]) == normalized:
                return category, capability
    partial = [(cat, skill) for cat in catalog["categories"] for skill in cat["capabilities"] if normalized and normalized in _normalize_text(skill["name"])]
    if len(partial) == 1:
        return partial[0]
    return None


def _resolve_selection(catalog: dict, source: str) -> list[dict]:
    clean = _strip_html(source)
    clean = _markdown_to_text(clean)
    normalized_text = _normalize_text(clean)
    padded = f" {normalized_text} "
    selected: dict[str, dict] = {}
    category_ids: set[int] = set()
    for match in re.finditer(r"\bcategory\s*(?:number\s*)?#?\s*0*(\d{1,3})\b", clean, re.I):
        category = _category_by_id(catalog, int(match.group(1)))
        if category:
            category_ids.add(category["id"])
    for category in catalog["categories"]:
        needle = f" {_normalize_text(category['name'])} "
        if needle.strip() and needle in padded:
            category_ids.add(category["id"])
    for category in catalog["categories"]:
        for skill in category["capabilities"]:
            needle = f" {_normalize_text(skill['name'])} "
            if needle.strip() and needle in padded:
                selected[skill["name"]] = {"category": category, "capability": skill}
    for category_id in category_ids:
        category = _category_by_id(catalog, category_id)
        if category:
            for skill in category["capabilities"]:
                selected[skill["name"]] = {"category": category, "capability": skill}
    if not selected:
        direct_category = _resolve_category(catalog, clean.strip())
        direct_skill = _resolve_capability(catalog, clean.strip())
        if direct_category:
            for skill in direct_category["capabilities"]:
                selected[skill["name"]] = {"category": direct_category, "capability": skill}
        elif direct_skill:
            category, skill = direct_skill
            selected[skill["name"]] = {"category": category, "capability": skill}
    if not selected:
        excerpt = " ".join(clean.split())[:100]
        raise ToolError("unmatched_selection", f"No catalog capability or category matched the selection {excerpt!r}.", "Use `capabilities search` to find exact names, or include the full category title/number.")
    return [selected[name] for name in sorted(selected, key=lambda item: (_normalize_text(item), item))]


def _capabilities_report(catalog: dict) -> dict:
    return {
        "schema_version": 1,
        "tool": "ultimate-web-maker",
        "operation": "capabilities-list",
        "category_count": len(catalog["categories"]),
        "capability_count": catalog["capability_count"],
        "categories": catalog["categories"],
    }


def _search_capabilities(catalog: dict, query: str) -> dict:
    query_terms = _normalize_text(query).split()
    if not query_terms:
        raise ToolError("empty_query", "Search query is empty.", "Provide one or more words, such as `accessibility` or `API version`.")
    results = []
    for category in catalog["categories"]:
        for capability in category["capabilities"]:
            corpus = _normalize_text(f"{category['name']} {capability['name']} {capability['description']}")
            if all(term in corpus for term in query_terms):
                results.append({"category_id": category["id"], "category": category["name"], **capability})
    return {"schema_version": 1, "tool": "ultimate-web-maker", "operation": "capabilities-search", "query": query, "result_count": len(results), "results": results}


def _show_capability(catalog: dict, selector: str) -> dict:
    category = _resolve_category(catalog, selector)
    if category:
        return {"schema_version": 1, "tool": "ultimate-web-maker", "operation": "capability-show", "category": category}
    capability = _resolve_capability(catalog, selector)
    if capability:
        cat, skill = capability
        return {"schema_version": 1, "tool": "ultimate-web-maker", "operation": "capability-show", "category_id": cat["id"], "category": cat["name"], "capability": skill}
    raise ToolError("unknown_capability", f"No unique category or capability matches {selector!r}.", "Use `capabilities search` and copy an exact category or capability name.")


def _render_report(report: dict, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    return _render_markdown(report)


def _render_markdown(report: dict) -> str:
    op = report.get("operation", "report")
    lines: list[str] = []
    if op == "plan":
        lines.extend([f"# Web project plan: {report.get('project', {}).get('title', 'Untitled')}", "", f"**Status:** {report.get('status', 'draft')}", "", "## Brief", "", (report.get("project", {}).get("goal") or "No specific goal was supplied."), ""])
        project = report.get("project", {})
        if project.get("audience"):
            lines.append(f"**Audience:** {project['audience']}")
        recommendation = report.get("recommendation", {})
        lines.extend(["", "## Delivery recommendation", "", f"- Mode: {recommendation.get('delivery_mode', 'undetermined')}", f"- Stack: {recommendation.get('stack', 'undetermined')}", f"- Local scaffold supported: {recommendation.get('static_scaffold_supported', False)}", "", "## Phases", ""])
        for phase in report.get("phases", []):
            lines.append(f"### {phase.get('name', 'Phase')}")
            for item in phase.get("tasks", []):
                lines.append(f"- {item}")
            if phase.get("exit_check"):
                lines.append(f"- **Exit check:** {phase['exit_check']}")
            lines.append("")
        lines.extend(["## Open questions", ""])
        for item in report.get("open_questions", []):
            lines.append(f"- {item}")
        if not report.get("open_questions"):
            lines.append("- None inferred from the supplied brief.")
        lines.extend(["", "## Risks and approval gates", ""])
        for item in report.get("risks", []):
            lines.append(f"- **{item.get('level', 'note')}:** {item.get('risk', '')} — {item.get('mitigation', '')}")
        for item in report.get("approval_gates", []):
            lines.append(f"- **Approval required:** {item}")
        lines.extend(["", "## Suggested capability categories", ""])
        for item in report.get("recommended_categories", []):
            lines.append(f"- {item['id']}. {item['name']}")
        lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report.get("limitations", [])], ""])
        return "\n".join(lines)
    if op == "audit":
        summary = report.get("summary", {})
        lines.extend(["# Static project audit", "", f"**Status:** {summary.get('status', 'unknown')} · **Findings:** {summary.get('finding_count', 0)}"])
        if report.get("gate"):
            gate = report["gate"]
            lines.append(f"**Quality gate:** {'passed' if gate.get('passed') else 'failed'} (fail-on: {gate.get('fail_on')})")
        lines.extend(["", "## Finding counts", ""])
        for severity in SEVERITIES:
            lines.append(f"- {severity.title()}: {summary.get('by_severity', {}).get(severity, 0)}")
        lines.extend(["", "## Findings", ""])
        if not report.get("findings"):
            lines.append("No findings were emitted by the configured static checks.")
        for item in report.get("findings", []):
            location = item.get("path", ".")
            if item.get("line"):
                location += f":{item['line']}"
            lines.extend([f"### [{item.get('severity', 'info').upper()}] {item.get('id', '')} — {location}", "", item.get("message", ""), "", f"**Recommended next step:** {item.get('recommendation', '')}", ""])
        if report.get("scan", {}).get("warnings"):
            lines.extend(["## Scan warnings", ""])
            for warning in report["scan"]["warnings"]:
                lines.append(f"- {warning.get('code')}: {warning.get('path', '.')}")
        lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report.get("limitations", [])], ""])
        return "\n".join(lines)
    if op == "run":
        lines.extend(["# Capability work report", "", f"**Selected capabilities:** {report.get('selected_capability_count', 0)}", ""])
        for work in report.get("work_products", []):
            category = work.get("category", {})
            lines.extend([f"## {category.get('id', '')}. {category.get('name', 'Category')}", "", f"**Workflow:** {work.get('kind', 'planning')} · **Status:** {work.get('status', 'draft')}", "", category.get("summary", ""), ""])
            if work.get("selected_capabilities"):
                lines.extend(["### Selected capabilities", "", *[f"- {name}" for name in work["selected_capabilities"]], ""])
            if work.get("capability_descriptions"):
                lines.extend(["### Capability descriptions", ""])
                for capability in work["capability_descriptions"]:
                    lines.append(f"- **{capability['name']}** — {capability['description']}")
                lines.append("")
            if work.get("deliverables"): 
                lines.extend(["### Work products", "", *[f"- {item}" for item in work["deliverables"]], ""])
            if work.get("checks"):
                lines.extend(["### Review checklist", "", *[f"- {item}" for item in work["checks"]], ""])
            if work.get("design_tokens"):
                lines.extend(["### Design tokens", "", f"Profile: {work.get('design_profile')}", "", "```json", json.dumps(work["design_tokens"], ensure_ascii=False, indent=2), "```", ""])
            if work.get("audit"):
                audit = work["audit"]
                lines.extend(["### Static audit subset", "", f"Findings: {audit.get('summary', {}).get('finding_count', 0)}", ""])
                for finding in audit.get("findings", []):
                    lines.append(f"- **{finding['severity']} {finding['id']}** `{finding['path']}`: {finding['message']} — {finding['recommendation']}")
                lines.append("")
            if work.get("verification_note"):
                lines.extend(["### Verification boundary", "", work["verification_note"], ""])
            if work.get("plan"):
                plan = work["plan"]
                lines.extend(["### Plan snapshot", "", f"Goal: {plan.get('project', {}).get('goal', '')}", f"Delivery mode: {plan.get('recommendation', {}).get('delivery_mode', '')}", ""])
        lines.extend(["## Safety", "", *[f"- {item}" for item in report.get("safety_notes", [])], ""])
        return "\n".join(lines)
    if op == "capabilities-list":
        lines.extend(["# Ultimate Web Maker capabilities", "", f"{report.get('category_count', 0)} categories · {report.get('capability_count', 0)} capabilities", ""])
        for category in report.get("categories", []):
            lines.extend([f"## {category['id']}. {category['name']}", "", category.get("summary", ""), ""])
            for skill in category.get("capabilities", []):
                lines.append(f"- **{skill['name']}** — {skill['description']}")
            lines.append("")
        return "\n".join(lines)
    if op == "capabilities-search":
        lines.extend([f"# Capability search: {report.get('query', '')}", "", f"{report.get('result_count', 0)} result(s)", ""])
        for skill in report.get("results", []):
            lines.append(f"- **{skill['name']}** — {skill['category']}: {skill['description']}")
        lines.append("")
        return "\n".join(lines)
    if op == "capability-show":
        lines.append("# Capability details")
        category = report.get("category")
        if isinstance(category, dict):
            lines.extend(["", f"## {category['id']}. {category['name']}", "", category.get("summary", ""), ""])
            for skill in category.get("capabilities", []):
                lines.append(f"- **{skill['name']}** — {skill['description']}")
        else:
            skill = report.get("capability", {})
            lines.extend(["", f"## {skill.get('name', '')}", "", f"Category: {report.get('category')}", "", skill.get("description", ""), ""])
        return "\n".join(lines)
    return "# Tool report\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n"


def _ensure_outside_package(path: Path, label: str) -> None:
    try:
        resolved = path.expanduser().resolve(strict=False)
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return
    except OSError:
        return
    raise ToolError("package_path_refused", f"{label} cannot be created inside the Skill Tool folder.", "Choose a path outside this component folder so its packaged files remain unchanged.")


def _atomic_write(path_value: str, text: str, force: bool, protected_paths: list[str] | None = None) -> None:
    target = Path(path_value).expanduser()
    _ensure_outside_package(target, "Report output")
    if target.exists() and target.is_dir():
        raise ToolError("output_is_directory", "The output path is a directory.", "Choose a file path.")
    if target.is_symlink():
        raise ToolError("output_symlink_refused", "The output path is a symbolic link and will not be replaced.", "Choose a normal file path.")
    for protected in protected_paths or []:
        try:
            if target.resolve() == Path(protected).expanduser().resolve():
                raise ToolError("input_output_collision", "The output path is also an input file; refusing to replace it.", "Choose a different output path.")
        except OSError:
            pass
    if target.exists() and not force:
        raise ToolError("output_exists", "The output file already exists; it was not changed.", "Choose another path or repeat with --force to explicitly approve replacement.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".webmaker-", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except ToolError:
        raise
    except OSError as exc:
        raise ToolError("output_write_error", f"Could not write output ({exc.strerror or type(exc).__name__}).", "Check parent-directory permissions and available storage.") from None


def _emit(report: dict, output_format: str, output_path: str | None, force: bool, protected_paths: list[str] | None = None) -> None:
    text = _render_report(report, output_format)
    if output_path:
        _atomic_write(output_path, text, force, protected_paths)
        print(json.dumps({"ok": True, "written": output_path, "operation": report.get("operation")}, ensure_ascii=False))
    else:
        sys.stdout.write(text)


class RuntimeBudget:
    def __init__(self, seconds: int) -> None:
        self.deadline = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise ToolError("timeout", "The operation exceeded its configured time limit.", "Reduce input scope or raise operation_timeout_seconds within the supported maximum.", 1)


def _brief_keywords(brief: dict) -> str:
    parts = [brief.get("title", ""), brief.get("goal", ""), brief.get("audience", ""), brief.get("stack", ""), brief.get("hosting", ""), brief.get("language", "")]
    for key in LIST_FIELDS:
        parts.extend(brief.get(key, []))
    return " ".join(str(item) for item in parts if item)


def _recommend_categories(brief: dict, catalog: dict) -> list[dict]:
    ids = {1, 2, 3, 4, 5, 9, 10, 23, 24, 25, 26, 33, 36}
    text = _normalize_text(_brief_keywords(brief))
    rules = [
        (11, r"\b(?:cms|wordpress|editorial|publishing|blog|content management)\b"),
        (12, r"\b(?:ecommerce|commerce|shop|store|cart|checkout|payment|payments|orders|tax|shipping)\b"),
        (13, r"\b(?:login|sign in|authentication|auth|account|accounts|passkey|identity|session)\b"),
        (14, r"\b(?:support|crm|ticket|helpdesk|customer service|chat)\b"),
        (15, r"\b(?:backend|server|api|endpoint|webhook)\b"),
        (16, r"\b(?:go runtime|php runtime|python backend|ruby|java service|\.net|rust service)\b"),
        (17, r"\b(?:integration|integrations|third party|provider|automation)\b"),
        (18, r"\b(?:database|data model|persistence|sql|postgres|mysql|sqlite)\b"),
        (19, r"\b(?:realtime|real time|queue|messaging|event stream|websocket)\b"),
        (20, r"\b(?:cloud|aws|azure|gcp|compute region)\b"),
        (21, r"\b(?:domain|dns|cdn|hosting|certificate|edge delivery)\b"),
        (22, r"\b(?:docker|container|kubernetes|infrastructure as code|terraform)\b"),
        (27, r"\b(?:pwa|progressive web app|offline|installable|service worker)\b"),
        (28, r"\b(?:electron|cordova|capacitor|hybrid app|mobile app|desktop app)\b"),
        (29, r"\b(?:video|audio|stream|webrtc|camera|microphone|media)\b"),
        (30, r"\b(?:saas|multi tenant|subscription billing|business dashboard)\b"),
        (31, r"\b(?:portfolio|marketplace|booking|reservation|healthcare|education|government|ticketing)\b"),
        (32, r"\b(?:localization|localisation|translation|multilingual|right to left|rtl|locale)\b"),
        (34, r"\b(?:production|operations|observability|sre|backup|disaster recovery|slo|incident)\b"),
        (35, r"\b(?:legacy|migration|modernize|modernise|upgrade existing|preservation)\b"),
    ]
    for category_id, pattern in rules:
        if re.search(pattern, text):
            ids.add(category_id)
    return [cat for cat in catalog["categories"] if cat["id"] in ids]


def _build_plan(brief: dict, catalog: dict, config: dict, budget: RuntimeBudget | None = None) -> dict:
    if budget:
        budget.check()
    text = _normalize_text(_brief_keywords(brief))
    transactional = bool(re.search(r"\b(?:payment|payments|checkout|cart|orders|booking|reservation|subscription|billing|marketplace|ecommerce|commerce|online store|shop)\b", text))
    identity = bool(re.search(r"\b(?:login|sign in|authentication|auth|account|accounts|passkey|identity|session)\b", text))
    personal_data = bool(re.search(r"\b(?:personal data|customer data|health|patient|address|email address|user data|profile)\b", text))
    external = bool(brief.get("integrations") or re.search(r"\b(?:api|webhook|provider|third party|integration)\b", text))
    requested_stack = brief.get("stack", "").strip()
    static_terms = r"\b(?:static|html|css|vanilla javascript|no framework|plain javascript)\b"
    if requested_stack:
        delivery_mode = "user-specified-stack"
        stack = f"Requested: {requested_stack}. Version/install/build behavior is not verified by this offline tool."
        static_supported = bool(re.search(static_terms, requested_stack, re.I))
    elif transactional or identity or personal_data or external:
        delivery_mode = "application-architecture-required"
        stack = "Choose a frontend, server/API, identity/data, and hosting stack after the trust boundaries and integrations are specified."
        static_supported = True
    else:
        delivery_mode = "static-first-candidate"
        stack = "Semantic HTML, modern CSS, and vanilla JavaScript are a suitable low-dependency starting point for a content-focused site."
        static_supported = True
    risks = []
    if transactional:
        risks.append({"level": "high", "risk": "The brief appears to include transactions, bookings, or billing.", "mitigation": "Define provider-owned payment flows, idempotency, refunds, reconciliation, taxes, and sandbox testing; obtain owner approval before live activity."})
    if identity:
        risks.append({"level": "high", "risk": "Accounts or authentication appear in scope.", "mitigation": "Use a reviewed identity design/provider, least privilege, safe recovery, abuse controls, and secret storage; never put credentials in source."})
    if personal_data:
        risks.append({"level": "high", "risk": "Personal or sensitive data may be collected or displayed.", "mitigation": "Document purpose, consent, minimization, access, retention, deletion, protection, and incident handling with the responsible owner."})
    if external:
        risks.append({"level": "medium", "risk": "One or more external integrations may affect reliability or privacy.", "mitigation": "Record contracts, owners, versions, data flows, bounded timeouts/retries, outage behavior, and credential rotation."})
    if requested_stack:
        risks.append({"level": "medium", "risk": "The requested stack is not runtime-verified here.", "mitigation": "Check current official documentation and run install, build, type, and test commands in the actual target environment."})
    if brief.get("hosting") or re.search(r"\b(?:publish|deploy|production|dns|domain|cloud)\b", text):
        risks.append({"level": "high", "risk": "Publishing or production infrastructure may be involved.", "mitigation": "Require a named owner, preview approval, cost review, health criteria, backup/rollback, and explicit approval before any live change."})
    if brief.get("language") and not brief["language"].casefold().startswith("en"):
        risks.append({"level": "medium", "risk": f"The requested language/locale is {brief['language']}; the included scaffold copy is English-only.", "mitigation": "Plan translation, localized routes and formatting, right-to-left behavior where needed, and a human-reviewed language QA pass."})
    if not risks:
        risks.append({"level": "note", "risk": "No transactional or external-service requirement was explicit in the supplied brief.", "mitigation": "Confirm this assumption before treating the static-first recommendation as final."})
    phases = [
        {"name": "1. Discovery and acceptance", "tasks": ["Confirm goal, primary audience, required pages, content owner, constraints, and non-goals.", "Turn each priority feature into an observable acceptance check."], "exit_check": "A named owner accepts the brief, scope, and success measures."},
        {"name": "2. Experience and architecture", "tasks": ["Map the main user journey, information architecture, responsive layout, and interaction states.", "Choose static or application architecture from the data, identity, integration, and operations needs.", "Record exact supported runtime/framework versions and verify version-sensitive decisions officially."], "exit_check": "The core flow, content model, and technical boundary are reviewed before implementation."},
        {"name": "3. Vertical slice and build", "tasks": ["Build one complete representative path before expanding the rest of the site.", "Use the local static scaffold only when its HTML/CSS/JavaScript boundary fits the brief.", "Keep secrets and real personal/payment data out of source, prompts, fixtures, and reports."], "exit_check": "A clean local start demonstrates the primary path and its failure/empty states."},
        {"name": "4. Integrations and data", "tasks": ["Define API/data contracts, access boundaries, retention, retries, and outage behavior where required.", "Use sandbox/test environments for identity, payment, messaging, and provider callbacks.", "Review schema migrations, backups, and rollback before any production data change."], "exit_check": "Integration contracts and failure paths pass in a non-production environment."},
        {"name": "5. Quality, trust, and performance", "tasks": ["Run the target stack's lint, type, unit, integration, and browser checks from a clean state.", "Test keyboard use, visible focus, labels, reduced motion, contrast, responsive behavior, and content extremes.", "Review threat boundaries, privacy, dependency risk, metadata, and measured performance."], "exit_check": "Release gates pass and any remaining risk has an accountable owner."},
        {"name": "6. Release and operations", "tasks": ["Prepare staging, owner approval, health checks, monitoring, incident contacts, and rollback.", "Confirm domain/DNS/certificate, cost, backup/restore, and data-location needs with service owners.", "Publish only through an approved provider workflow after explicit human approval."], "exit_check": "A named release owner approves the verified preview and recovery plan."},
    ]
    open_questions = []
    for key, prompt in (("goal", "What is the site's primary outcome, and how will it be observed?"),
                        ("audience", "Who is the primary audience and what access needs must be supported?"),
                        ("features", "Which features are launch-critical, and how will each be accepted?"),
                        ("constraints", "What budget, deadline, browser/device, privacy, or legal constraints apply?"),
                        ("success_criteria", "What measurable signal will show the site is working?")):
        if not brief.get(key):
            open_questions.append(prompt)
    if not brief.get("integrations") and external:
        open_questions.append("Which external providers or APIs are required, who owns them, and what data is exchanged?")
    approval_gates = [f"Explicit owner approval is required before {action.replace('_', ' ')}." for action in config["approval_policy"]["explicit_approval_required_for"]]
    recommended = _recommend_categories(brief, catalog)
    return {
        "schema_version": 1,
        "tool": "ultimate-web-maker",
        "tool_version": VERSION,
        "operation": "plan",
        "status": "draft-plan-not-built",
        "project": {
            "title": brief["title"],
            "goal": brief["goal"],
            "audience": brief["audience"],
            "features": brief["features"],
            "content": brief["content"],
            "requested_stack": requested_stack or None,
            "integrations": brief["integrations"],
            "hosting": brief["hosting"],
            "constraints": brief["constraints"],
            "success_criteria": brief["success_criteria"],
            "language": brief["language"] or "unspecified",
            "brief_source_kind": brief["source_kind"],
        },
        "recommendation": {
            "delivery_mode": delivery_mode,
            "stack": stack,
            "static_scaffold_supported": static_supported,
            "version_status": "not-verified",
        },
        "phases": phases,
        "open_questions": open_questions,
        "risks": risks,
        "approval_gates": approval_gates,
        "recommended_categories": [{"id": cat["id"], "name": cat["name"]} for cat in recommended],
        "limitations": [
            "Planning is deterministic and based only on the supplied brief; no model/provider, current documentation, repository, or external service was queried.",
            "The plan does not generate a framework application, backend, database, commerce/auth integration, cloud infrastructure, or deployment.",
            "A static scaffold is available separately for local HTML/CSS/JavaScript prototypes; review it before publishing.",
        ],
    }


def _validate_profile_name(profile: str, config: dict) -> dict:
    if profile not in config["profiles"]:
        choices = ", ".join(sorted(config["profiles"]))
        raise ToolError("unknown_profile", f"Design profile {profile!r} is not configured.", f"Choose one of: {choices}.")
    return config["profiles"][profile]


def _substitute_template(template: str, replacements: dict[str, str], label: str) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in replacements:
            raise ToolError("template_error", f"Template {label!r} contains an unknown token {key!r}.", "Restore the matching asset template or update the renderer.")
        return replacements[key]
    return re.sub(r"\{\{([A-Z_]+)\}\}", replace, template)


def _write_new_file(path: Path, content: str) -> None:
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise ToolError("scaffold_collision", f"A file appeared at {path.name!r} during scaffold creation.", "Choose a new output directory and retry.") from None
    except OSError as exc:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise ToolError("scaffold_write_error", f"Could not write generated file {path.name!r} ({exc.strerror or type(exc).__name__}).", "Check target-directory permissions and available storage.") from None


def _remove_our_scaffold_files(target: Path, created_names: list[str]) -> None:
    for name in reversed(created_names):
        try:
            candidate = target / name
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()
        except OSError:
            pass
    try:
        target.rmdir()
    except OSError:
        pass


def _scaffold(brief: dict, args: argparse.Namespace, config: dict) -> dict:
    profile_name = args.profile or "editorial"
    profile = _validate_profile_name(profile_name, config)
    requested_stack = brief.get("stack", "").strip()
    if requested_stack and not re.search(r"\b(?:static|html|css|vanilla javascript|plain javascript|no framework)\b", requested_stack, re.I) and not args.allow_static_prototype:
        raise ToolError("stack_mismatch", f"The brief requests stack {requested_stack!r}, while scaffold supports static HTML/CSS/JavaScript only.", "Use `plan` for the requested stack; add --allow-static-prototype only when you intentionally want a separate static mockup.")
    language = brief.get("language", "").strip()
    english_language = not language or language.casefold() in {"en", "en-us", "en-gb", "en-au", "en-ca", "en-nz"}
    if not english_language and not args.allow_english_copy:
        raise ToolError("language_mismatch", f"The brief requests language {language!r}, but the built-in scaffold copy is English-only.", "Use plan for localization requirements, or add --allow-english-copy for an English draft that must be translated and relabeled before use.")
    title = _clean_value(args.title or brief["title"], 90)
    if not title:
        raise ToolError("invalid_title", "The scaffold title must contain readable text.", "Pass a short --title or provide a title in the brief.")
    summary = _clean_value(brief.get("summary") or brief.get("goal") or title, 480)
    audience = _clean_value(brief.get("audience", ""), 120).strip()
    audience_short = audience.rstrip(" .!?…")
    eyebrow = f"A focused experience for {audience_short}" if audience_short else "A thoughtful digital experience"
    audience_note = f"Designed for {audience_short}." if audience_short else "Responsive, accessible, and ready to shape."
    description = summary or f"A clear, considered web experience for {title}."
    replacements_html = {
        "TITLE": html.escape(title, quote=True),
        "DESCRIPTION": html.escape(description, quote=True),
        "SUMMARY": html.escape(summary, quote=True),
        "EYEBROW": html.escape(eyebrow, quote=True),
        "AUDIENCE": html.escape(audience_note, quote=True),
        "LANG": "en",
    }
    replacements_css = {
        "COLOR_BG": profile["background"],
        "COLOR_SURFACE": profile["surface"],
        "COLOR_INK": profile["ink"],
        "COLOR_MUTED": profile["muted"],
        "COLOR_ACCENT": profile["accent"],
        "COLOR_ACCENT_INK": profile["accent_ink"],
        "FONT_STACK": profile["font_stack"],
    }
    files: dict[str, str] = {}
    for output_name, asset_name in SITE_TEMPLATES.items():
        template = _load_asset_text(asset_name)
        if output_name == "index.html":
            files[output_name] = _substitute_template(template, replacements_html, asset_name)
        elif output_name == "styles.css":
            files[output_name] = _substitute_template(template, replacements_css, asset_name)
        elif output_name == "README.md":
            files[output_name] = _substitute_template(template, {
                "TITLE": title,
                "DESCRIPTION": description,
            }, asset_name)
        else:
            if re.search(r"\{\{[A-Z_]+\}\}", template):
                raise ToolError("template_error", f"The code template {asset_name!r} contains an unresolved token.", "Restore the template asset.")
            files[output_name] = template
    target = Path(args.output_dir).expanduser()
    _ensure_outside_package(target, "Scaffold directory")
    if target.exists() or target.is_symlink():
        raise ToolError("scaffold_target_exists", "The scaffold target already exists; existing directories are never overwritten.", "Choose a new output directory. Move or archive existing content yourself before retrying.")
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError("scaffold_parent_error", f"Could not prepare the scaffold parent directory ({exc.strerror or type(exc).__name__}).", "Check the chosen parent path and permissions.") from None
    stage: Path | None = None
    target_created = False
    created_names: list[str] = []
    try:
        stage = Path(tempfile.mkdtemp(prefix=".webmaker-stage-", dir=str(parent)))
        for name, content in files.items():
            _write_new_file(stage / name, content)
        # Verify generated outputs through the same safe analyzer before publishing the folder.
        audit_config = copy.deepcopy(config)
        audit_report = STATIC_AUDIT.audit_project(stage, audit_config)
        blocking = [item for item in audit_report["findings"] if item["severity"] in {"critical", "high"}]
        if blocking:
            raise ToolError("generated_validation_failed", "The generated static site did not pass its blocking static checks.", "Inspect the package templates and rerun after repairing the check.", 1)
        # mkdir is an exclusive reservation; each new file is created with mode 'x', so existing paths are never replaced.
        try:
            target.mkdir()
        except FileExistsError:
            raise ToolError("scaffold_target_exists", "The scaffold target appeared during creation; no existing content was replaced.", "Choose a new output directory and retry.") from None
        except OSError as exc:
            raise ToolError("scaffold_publish_error", f"Could not reserve the scaffold directory ({exc.strerror or type(exc).__name__}).", "Check permissions and choose a writable parent directory.") from None
        target_created = True
        for name, content in files.items():
            _write_new_file(target / name, content)
            created_names.append(name)
        warnings = []
        if requested_stack and not re.search(r"\b(?:static|html|css|vanilla javascript|plain javascript|no framework)\b", requested_stack, re.I):
            warnings.append("A separate static prototype was generated despite the requested application stack; it does not implement that stack.")
        if not english_language:
            warnings.append(f"English interface copy was generated for a brief labeled {language!r}; translate and set the document language before use.")
        if brief.get("source_kind") == "html-or-markdown":
            warnings.append("Brief text was treated as content only; pasted HTML tags and attributes were not executed.")
        return {
            "schema_version": 1,
            "tool": "ultimate-web-maker",
            "tool_version": VERSION,
            "operation": "scaffold",
            "completed": True,
            "output_dir": str(target),
            "profile": profile_name,
            "document_language": "en",
            "files": list(files.keys()),
            "generated_audit": {
                "status": audit_report["summary"]["status"],
                "finding_count": audit_report["summary"]["finding_count"],
                "high_or_critical_findings": len(blocking),
            },
            "warnings": warnings,
            "limitations": [
                "This command creates a static English HTML/CSS/JavaScript starter only; it does not build framework, backend, account, payment, CMS, database, cloud, or deployment integrations.",
                "Review all generated content, language metadata, accessibility, responsive behavior, security, and real-browser behavior before publishing.",
                "No source code from the brief is executed and no network service is contacted.",
            ],
        }
    except Exception:
        if target_created:
            _remove_our_scaffold_files(target, created_names)
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def _audit_root(path_value: str) -> Path:
    root = Path(path_value).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ToolError("invalid_project", "The project path must be an existing directory and must not be a symbolic link.", "Pass a real directory using --project.")
    return root


def _gate_passed(report: dict, fail_on: str) -> bool:
    if fail_on == "none":
        return True
    rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    threshold = rank[fail_on]
    return not any(rank.get(item.get("severity", "info"), 99) <= threshold for item in report.get("findings", []))


def _run_capabilities(selections: list[dict], brief: dict | None, project: Path | None,
                      catalog: dict, config: dict, profile_name: str) -> dict:
    groups: dict[int, dict] = {}
    for selection in selections:
        category = selection["category"]
        groups.setdefault(category["id"], {"category": category, "capabilities": []})["capabilities"].append(selection["capability"])
    budget = RuntimeBudget(config["limits"]["operation_timeout_seconds"])
    plan = _build_plan(brief, catalog, config, budget) if brief else None
    full_audit = None
    if project and any(config["workflows"][str(category_id)]["kind"] in {"audit", "orchestration"} for category_id in groups):
        try:
            full_audit = STATIC_AUDIT.audit_project(project, config)
        except STATIC_AUDIT.AuditFailure as exc:
            raise ToolError(exc.code, exc.message, exc.hint, 1) from None
    profile = _validate_profile_name(profile_name, config)
    work_products = []
    for category_id in sorted(groups):
        budget.check()
        group = groups[category_id]
        category = group["category"]
        workflow = config["workflows"][str(category_id)]
        kind = workflow["kind"]
        status = "checklist-generated"
        item: dict[str, Any] = {
            "category": {"id": category["id"], "name": category["name"], "summary": category.get("summary", "")},
            "kind": kind,
            "status": status,
            "selected_capabilities": [skill["name"] for skill in group["capabilities"]],
            "capability_descriptions": [{"name": skill["name"], "description": skill["description"]} for skill in group["capabilities"]],
            "deliverables": workflow["deliverables"],
            "checks": workflow["checks"],
        }
        if kind == "research":
            item["status"] = "verification-required"
            item["verification_note"] = "No official documentation or release endpoint was queried. Confirm exact versions from official sources and run the proposed behavior in the actual environment before relying on compatibility claims."
        if kind == "design":
            item["design_profile"] = profile_name
            item["design_tokens"] = profile
            item["status"] = "design-starting-point"
        if category_id in {1, 36} and plan:
            item["plan"] = plan
            item["status"] = "draft-plan-created"
        if kind == "audit":
            if full_audit:
                tags = set(workflow["audit_tags"])
                related = [finding for finding in full_audit["findings"] if finding["category"] in tags]
                counts = {severity: sum(1 for finding in related if finding["severity"] == severity) for severity in SEVERITIES}
                item["audit"] = {
                    "status": full_audit["summary"]["status"],
                    "scope_tags": sorted(tags),
                    "summary": {"finding_count": len(related), "by_severity": counts},
                    "findings": related,
                    "warnings": full_audit["scan"]["warnings"],
                    "limitations": full_audit["limitations"],
                }
                item["status"] = "static-review-completed"
            else:
                item["status"] = "project-source-required-for-static-findings"
                item["verification_note"] = "No project directory was supplied. The checklist is ready, but no source-file findings were produced."
        if kind == "orchestration" and full_audit:
            item["project_audit_summary"] = full_audit["summary"]
        work_products.append(item)
    return {
        "schema_version": 1,
        "tool": "ultimate-web-maker",
        "tool_version": VERSION,
        "operation": "run",
        "completed": True,
        "selected_capability_count": len(selections),
        "selected_capabilities": [
            {"category_id": entry["category"]["id"], "category": entry["category"]["name"], "name": entry["capability"]["name"]}
            for entry in selections
        ],
        "brief": ({"title": brief["title"], "goal": brief["goal"], "source_kind": brief["source_kind"]} if brief else None),
        "project": ({"name": project.name or ".", "root": "."} if project else None),
        "work_products": work_products,
        "safety_notes": [
            "The run operation uses deterministic local checklists and static heuristics; it does not call a model, provider, browser, shell, API, or network service.",
            "Publishing, deployment, DNS, live payments, credential use, production data changes, destructive overwrite, and privileged security changes require separate human approval.",
            "An audit subset is only as complete as the supplied files and configured limits; it is not a compliance or security certification.",
        ],
    }


def _render_capability_text(args: argparse.Namespace, catalog: dict) -> dict:
    if args.command == "list-capabilities":
        return _capabilities_report(catalog)
    if args.command != "capabilities":
        raise ToolError("internal_error", "Unknown capability operation.")
    if args.capability_action == "list":
        return _capabilities_report(catalog)
    if args.capability_action == "search":
        return _search_capabilities(catalog, args.query)
    if args.capability_action == "show":
        return _show_capability(catalog, args.selector)
    raise ToolError("invalid_arguments", "Choose capabilities list, search, or show.", "Run `capabilities --help` for command help.")


def _add_output_args(parser: argparse.ArgumentParser, default_format: str = "json") -> None:
    parser.add_argument("--format", choices=("json", "md"), default=default_format, help="Output format (default: %(default)s).")
    parser.add_argument("--output", help="Optional report file path; existing files require --force.")
    parser.add_argument("--force", action="store_true", help="Explicitly approve replacing an existing report file (never replaces an input file).")


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Optional JSON config file; relative paths resolve from the current shell directory.")


def _add_brief_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brief", help="Brief file path, or - to read bounded UTF-8 input from stdin.")
    parser.add_argument("--text", help="Inline plain-text, Markdown, HTML, or JSON brief.")


def build_parser() -> ToolArgumentParser:
    parser = ToolArgumentParser(prog="tools-webmaker.py", description="Offline-capable web planning, static scaffolding, capability routing, and bounded project audit.")
    parser.add_argument("--version", action="version", version=f"Ultimate Web Maker {VERSION}")
    sub = parser.add_subparsers(dest="command", parser_class=ToolArgumentParser)

    caps = sub.add_parser("capabilities", help="List, search, or show the bundled 36-category capability catalog.")
    cap_sub = caps.add_subparsers(dest="capability_action", parser_class=ToolArgumentParser)
    list_parser = cap_sub.add_parser("list", help="List all categories and capabilities.")
    list_parser.add_argument("--format", choices=("json", "md"), default="md")
    list_parser.add_argument("--output")
    list_parser.add_argument("--force", action="store_true")
    search_parser = cap_sub.add_parser("search", help="Search names, category titles, and descriptions.")
    search_parser.add_argument("query")
    search_parser.add_argument("--format", choices=("json", "md"), default="md")
    search_parser.add_argument("--output")
    search_parser.add_argument("--force", action="store_true")
    show_parser = cap_sub.add_parser("show", help="Show one exact/unique category or capability.")
    show_parser.add_argument("selector")
    show_parser.add_argument("--format", choices=("json", "md"), default="md")
    show_parser.add_argument("--output")
    show_parser.add_argument("--force", action="store_true")
    cap_sub.add_parser("help", help="Show capability command help.")

    all_caps = sub.add_parser("list-capabilities", help="Alias for `capabilities list`.")
    all_caps.add_argument("--format", choices=("json", "md"), default="md")
    all_caps.add_argument("--output")
    all_caps.add_argument("--force", action="store_true")

    plan_parser = sub.add_parser("plan", help="Turn a supplied brief into a structured, deterministic project plan.")
    _add_brief_args(plan_parser)
    _add_output_args(plan_parser)
    _add_config_arg(plan_parser)

    run_parser = sub.add_parser("run", help="Resolve capabilities/categories and create category-specific local work products.")
    run_parser.add_argument("--selection", "--capability", dest="selection", help="Capability/category text (supports a numbered list, Markdown, or copied HTML).")
    run_parser.add_argument("--selection-file", help="Text, Markdown, or HTML file containing capability/category names.")
    _add_brief_args(run_parser)
    run_parser.add_argument("--project", help="Optional existing project directory for applicable static audit workflows.")
    run_parser.add_argument("--profile", help="Design profile name for visual-design workflows.")
    _add_output_args(run_parser)
    _add_config_arg(run_parser)

    scaffold_parser = sub.add_parser("scaffold", help="Create a new static HTML/CSS/JavaScript starter directory; never overwrites.")
    _add_brief_args(scaffold_parser)
    scaffold_parser.add_argument("--output-dir", required=False, help="New directory to create; it must not already exist.")
    scaffold_parser.add_argument("--title", help="Optional page title (text is safely HTML-escaped).")
    scaffold_parser.add_argument("--profile", help="Design profile (default: editorial).")
    scaffold_parser.add_argument("--allow-static-prototype", action="store_true", help="Explicitly allow a separate static mockup when the brief names another stack.")
    scaffold_parser.add_argument("--allow-english-copy", action="store_true", help="Explicitly allow the English starter copy for a non-English brief; translation and correct lang metadata remain your responsibility.")
    scaffold_parser.add_argument("--output", help="Optional JSON creation report path; existing files require --force.")
    scaffold_parser.add_argument("--force", action="store_true", help="Explicitly approve replacing an existing report file, not the scaffold directory.")
    _add_config_arg(scaffold_parser)

    audit_parser = sub.add_parser("audit", help="Inspect local source without executing code or accessing a network.")
    audit_parser.add_argument("--project", help="Existing project directory to inspect.")
    audit_parser.add_argument("--fail-on", choices=("none", "critical", "high", "medium", "low", "info"), help="Exit nonzero if a finding reaches this severity (default from config: high).")
    _add_output_args(audit_parser)
    _add_config_arg(audit_parser)

    template_parser = sub.add_parser("template", help="Print or write the reusable project brief template.")
    template_parser.add_argument("--output", help="Optional file path; existing files require --force.")
    template_parser.add_argument("--force", action="store_true", help="Explicitly approve replacing the output file.")

    doctor_parser = sub.add_parser("doctor", help="Check package assets, capability counts, workflow mappings, and templates locally.")
    doctor_parser.add_argument("--format", choices=("json", "md"), default="json")
    doctor_parser.add_argument("--output")
    doctor_parser.add_argument("--force", action="store_true")
    _add_config_arg(doctor_parser)
    return parser


def _dispatch(args: argparse.Namespace, catalog: dict, config: dict) -> int:
    if args.command is None:
        return 0
    if args.command in {"capabilities", "list-capabilities"}:
        if args.command == "capabilities" and args.capability_action in {None, "help"}:
            args._root_parser = None
            print("Usage: tools-webmaker.py capabilities {list,search,show} ...\nUse `tools-webmaker.py capabilities --help` for detailed options.")
            return 0
        report = _render_capability_text(args, catalog)
        output = getattr(args, "output", None)
        _emit(report, getattr(args, "format", "md"), output, bool(getattr(args, "force", False)))
        return 0
    if args.command == "template":
        template = _load_asset_text(BRIEF_TEMPLATE_FILE)
        if args.output:
            _atomic_write(args.output, template, args.force)
            print(json.dumps({"ok": True, "written": args.output, "operation": "template"}, ensure_ascii=False))
        else:
            sys.stdout.write(template)
        return 0
    if args.command == "doctor":
        template_files = [CATALOG_FILE, DEFAULT_CONFIG_FILE, BRIEF_TEMPLATE_FILE, *SITE_TEMPLATES.values()]
        missing = [name for name in template_files if not (ROOT / name).is_file()]
        if missing:
            raise ToolError("doctor_failed", "One or more package assets are missing.", "Restore: " + ", ".join(missing))
        unresolved = []
        for output_name, asset_name in SITE_TEMPLATES.items():
            source = _load_asset_text(asset_name)
            allowed = {"TITLE", "DESCRIPTION", "SUMMARY", "EYEBROW", "AUDIENCE", "LANG"} if output_name == "index.html" else ({"COLOR_BG", "COLOR_SURFACE", "COLOR_INK", "COLOR_MUTED", "COLOR_ACCENT", "COLOR_ACCENT_INK", "FONT_STACK"} if output_name == "styles.css" else ({"TITLE", "DESCRIPTION"} if output_name == "README.md" else set()))
            tokens = set(re.findall(r"\{\{([A-Z_]+)\}\}", source))
            if tokens - allowed:
                unresolved.append({"asset": asset_name, "tokens": sorted(tokens - allowed)})
        if unresolved:
            raise ToolError("doctor_failed", "A template has unrecognized substitution tokens.", json.dumps(unresolved, ensure_ascii=False))
        report = {"schema_version": 1, "tool": "ultimate-web-maker", "tool_version": VERSION, "operation": "doctor", "ok": True,
                  "python_version": sys.version.split()[0], "category_count": len(catalog["categories"]), "capability_count": catalog["capability_count"],
                  "workflow_count": len(config["workflows"]), "assets_checked": template_files, "network_accessed": False,
                  "runtime_dependencies": "Python standard library only"}
        _emit(report, args.format, args.output, args.force)
        return 0
    if args.command == "plan":
        raw, source_name = _read_input_source(args, "brief", config)
        brief = _parse_brief(raw, source_name)
        report = _build_plan(brief, catalog, config, RuntimeBudget(config["limits"]["operation_timeout_seconds"]))
        _emit(report, args.format, args.output, args.force, [args.brief] if args.brief and args.brief != "-" else None)
        return 0
    if args.command == "scaffold":
        if not args.output_dir:
            raise ToolError("missing_output_path", "scaffold requires --output-dir.", "Choose a new directory path that does not already exist.")
        raw, source_name = _read_input_source(args, "brief", config)
        brief = _parse_brief(raw, source_name)
        report = _scaffold(brief, args, config)
        _emit(report, "json", args.output, args.force, [args.brief] if args.brief and args.brief != "-" else None)
        return 0
    if args.command == "audit":
        if not args.project:
            raise ToolError("missing_project", "audit requires --project.", "Pass an existing local project directory.")
        root = _audit_root(args.project)
        try:
            report = STATIC_AUDIT.audit_project(root, config)
        except STATIC_AUDIT.AuditFailure as exc:
            raise ToolError(exc.code, exc.message, exc.hint, 1) from None
        fail_on = args.fail_on or config["scanner"]["fail_on"]
        report["gate"] = {"fail_on": fail_on, "passed": _gate_passed(report, fail_on)}
        _emit(report, args.format, args.output, args.force)
        return 0 if report["gate"]["passed"] else 1
    if args.command == "run":
        selection_text = _read_selection(args, config)
        selections = _resolve_selection(catalog, selection_text)
        raw_brief = None
        source_name = "text"
        has_brief = args.brief is not None or args.text is not None
        if has_brief:
            raw_brief, source_name = _read_input_source(args, "brief", config)
            brief = _parse_brief(raw_brief, source_name)
        else:
            brief = None
        project = _audit_root(args.project) if args.project else None
        if not brief and not project:
            raise ToolError("missing_work_input", "run requires a project brief or an existing --project directory.", "Provide --brief/--text for planning, or --project for applicable static audits.")
        if not brief and project:
            brief = _parse_brief(f"Project: {project.name or 'Existing web project'}\nGoal: Review the supplied project and define a safe, testable web-production workflow.", "text")
        report = _run_capabilities(selections, brief, project, catalog, config, args.profile or "editorial")
        protected = []
        if args.brief and args.brief != "-":
            protected.append(args.brief)
        if args.selection_file:
            protected.append(args.selection_file)
        _emit(report, args.format, args.output, args.force, protected)
        return 0
    raise ToolError("invalid_command", f"Unknown command {args.command!r}.", "Run --help to see supported commands.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return 0
        catalog = _load_catalog()
        config_path = getattr(args, "config", None)
        config = _load_config(config_path, catalog) if args.command not in {"template", "capabilities", "list-capabilities"} else _load_config(None, catalog)
        return _dispatch(args, catalog, config)
    except SystemExit as exc:
        return int(exc.code or 0)
    except ToolError as exc:
        error = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        if exc.hint:
            error["error"]["hint"] = exc.hint
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "cancelled", "message": "Operation cancelled by the user; temporary scaffold files are cleaned up when possible."}}, ensure_ascii=False), file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": "internal_error", "message": "An unexpected internal error occurred.", "hint": f"Exception type: {type(exc).__name__}. Check the target directory before retrying a failed scaffold."}}, ensure_ascii=False), file=sys.stderr)
        return 1
