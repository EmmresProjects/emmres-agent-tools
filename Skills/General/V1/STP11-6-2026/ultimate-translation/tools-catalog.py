#!/usr/bin/env python3
"""Operational catalog registry and preflight for all 144 supplied capabilities."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from tools_core import ToolError


def load_registry(sibling_dir: Path) -> dict[str, Any]:
    path = sibling_dir / "asset-capability-registry.json"
    if not path.is_file():
        raise ToolError("missing_asset", "Operational capability registry is missing: asset-capability-registry.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError("invalid_asset", "Capability registry is not valid UTF-8 JSON.", details=str(exc)) from exc
    capabilities = value.get("capabilities", [])
    if value.get("version") != 1 or value.get("categories") != 16 or len(capabilities) != 144:
        raise ToolError("invalid_asset", "Capability registry must describe version 1, 16 categories, and 144 capabilities.")
    ids = [row.get("id") for row in capabilities]
    names = [row.get("name") for row in capabilities]
    if len(set(ids)) != 144 or len(set(names)) != 144:
        raise ToolError("invalid_asset", "Capability registry IDs and names must be unique.")
    for row in capabilities:
        if not row.get("command") or row.get("mode") not in {"offline", "hybrid", "provider"} or not row.get("description"):
            raise ToolError("invalid_asset", "Every capability needs a command, mode, and description.", details=row.get("id"))
    return value


def capability_list(registry: dict[str, Any], *, category: str | None = None, query: str | None = None, commands: set[str] | None = None) -> dict[str, Any]:
    rows = registry["capabilities"]
    if category:
        needle = category.casefold()
        rows = [row for row in rows if needle in str(row["category"]).casefold() or needle == str(row["category_number"])]
        if not rows:
            raise ToolError("unknown_category", "No category matches the supplied filter.", details=category)
    if query:
        needle = query.casefold()
        rows = [row for row in rows if needle in row["name"].casefold() or needle in row["description"].casefold() or needle in row["id"]]
        if not rows:
            raise ToolError("unknown_capability", "No capability matches the supplied query.", details=query)
    output = []
    for row in rows:
        item = dict(row)
        if commands is not None:
            primary = row["command"].split()[0]
            item["reachable"] = primary in commands
        output.append(item)
    result = {"version": 1, "total_catalog_capabilities": 144, "returned": len(output), "categories": len({row["category"] for row in output}), "capabilities": output}
    if commands is not None:
        result["preflight"] = {"passed": all(row["reachable"] for row in output), "unreachable": [row["name"] for row in output if not row["reachable"]]}
    return result


def find_capability(registry: dict[str, Any], reference: str) -> dict[str, Any]:
    needle = reference.casefold().strip()
    exact = [row for row in registry["capabilities"] if needle in {row["id"].casefold(), row["name"].casefold()}]
    if len(exact) == 1:
        return exact[0]
    matches = [row for row in registry["capabilities"] if needle in row["name"].casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ToolError("unknown_capability", "Capability was not found.", details=reference)
    raise ToolError("ambiguous_capability", "Capability reference is ambiguous; use its catalog ID.", details=[{"id": row["id"], "name": row["name"]} for row in matches[:20]])
