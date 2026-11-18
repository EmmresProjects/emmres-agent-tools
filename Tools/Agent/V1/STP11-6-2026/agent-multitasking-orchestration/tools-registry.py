#!/usr/bin/env python3
"""Catalog and capability-profile registry for the unified Skill Tool.

The registry is the single source of truth for the 25 categories and 256
capabilities extracted from the supplied catalog. Each capability has a stable
handler id, artifact contract, risk classification, and approval scope.
"""

from __future__ import annotations

import re
import sys as _sys
from collections import Counter
from importlib import import_module as _import_module
from typing import Any

_sys.modules.setdefault("tools_common", _import_module("tools-common"))
from tools_common import (
    ToolError,
    config_path,
    load_json,
    load_schema,
    now_iso,
    require_schema,
    unique,
)

FOCUS_STOPWORDS = {
    "a", "about", "above", "across", "after", "against", "all", "also", "an",
    "and", "another", "any", "are", "as", "at", "based", "be", "before", "being",
    "between", "both", "but", "by", "can", "each", "for", "from", "has", "have",
    "into", "its", "more", "not", "of", "on", "one", "or", "other", "over",
    "rather", "so", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "to", "under", "until", "using", "when",
    "where", "while", "with", "without", "rather", "defined", "same", "clear",
    "new", "using", "only", "then", "well", "after", "across", "before",
}


def classify_risk(capability: dict[str, Any], policy: dict[str, Any]) -> str:
    """Classify a capability using explicit overrides, keywords, and floors."""
    order = policy.get("risk_order", ["low", "medium", "high", "critical"])
    rank = {level: index for index, level in enumerate(order)}
    levels = ["low"]
    explicit = policy.get("explicit_capability_risks", {}).get(capability["slug"])
    if explicit:
        levels.append(explicit)
    floor = policy.get("category_risk_floor", {}).get(capability["category_slug"])
    if floor:
        levels.append(floor)
    text = f"{capability['name']} {capability['description']}".lower()
    for level, keywords in policy.get("keyword_risks", {}).items():
        for keyword in keywords:
            pattern = r"\b" + re.escape(str(keyword).lower()) + r"\b"
            if re.search(pattern, text):
                levels.append(level)
                break
    return max(levels, key=lambda level: rank.get(level, 0))


def _focus_terms(capability: dict[str, Any]) -> list[str]:
    text = f"{capability['name']} {capability['description']}".lower()
    words = re.findall(r"[a-z][a-z-]{2,}", text)
    return unique([word for word in words if word not in FOCUS_STOPWORDS and len(word) > 2])[:8]


def _operation(description: str) -> str:
    first = re.split(r"[\s,;:]", description.strip(), maxsplit=1)[0].strip(".,").lower()
    return first or "orchestrate"


def build_profiles(catalog: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for capability in catalog["capabilities"]:
        slug = capability["slug"]
        risk = classify_risk(capability, policy)
        profiles.append(
            {
                "id": capability["id"],
                "slug": slug,
                "name": capability["name"],
                "category": capability["category"],
                "category_slug": capability["category_slug"],
                "description": capability["description"],
                "handler": f"handle-{slug}",
                "artifact_type": f"{slug}-report",
                "operation": _operation(capability["description"]),
                "focus_terms": _focus_terms(capability),
                "risk": risk,
                "approval_scope": f"capability.execute:{slug}",
                "output_contract": {
                    "artifact_type": f"{slug}-report",
                    "required_fields": [
                        "type",
                        "operation",
                        "responsibility",
                        "input_summary",
                        "focus",
                        "metrics",
                        "findings",
                        "checks",
                        "recommendation",
                    ],
                    "structured_conclusion_only": True,
                },
                "timeout_seconds": 2.0,
                "retries": 1,
            }
        )
    risk_counts = dict(Counter(item["risk"] for item in profiles))
    return {
        "schema_version": "1.0",
        "umbrella": catalog["umbrella"],
        "counts": {
            "categories": catalog["counts"]["categories"],
            "capabilities": len(profiles),
            "risk": risk_counts,
        },
        "profiles": profiles,
    }


class CapabilityRegistry:
    def __init__(self, catalog: dict[str, Any], profiles: dict[str, Any], policy: dict[str, Any]) -> None:
        self.catalog = catalog
        self.profiles = profiles
        self.policy = policy
        self.by_slug: dict[str, dict[str, Any]] = {}
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_name: dict[str, dict[str, Any]] = {}
        for profile in profiles["profiles"]:
            slug = profile["slug"]
            if slug in self.by_slug:
                raise ToolError(
                    "DUPLICATE_CAPABILITY_SLUG",
                    f"Duplicate capability slug: {slug}",
                    details={"slug": slug},
                    exit_code=4,
                )
            self.by_slug[slug] = profile
            self.by_id[profile["id"]] = profile
            self.by_name[profile["name"].lower()] = profile
        self.category_counts = {
            category["slug"]: len(category["capabilities"])
            for category in catalog["categories"]
        }

    def resolve(self, value: str) -> dict[str, Any]:
        if not isinstance(value, str) or not value.strip():
            raise ToolError(
                "CAPABILITY_REQUIRED",
                "Provide --capability with a slug, id, or exact Skill Tool Name.",
                recovery="Run capabilities to list accepted identifiers.",
            )
        key = value.strip()
        lowered = key.lower()
        if key in self.by_slug:
            return self.by_slug[key]
        if key in self.by_id:
            return self.by_id[key]
        if lowered in self.by_slug:
            return self.by_slug[lowered]
        if lowered in self.by_name:
            return self.by_name[lowered]
        slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        if slug in self.by_slug:
            return self.by_slug[slug]
        suggestions = [item for item in self.by_slug if slug[:4] and slug[:4] in item][:8]
        raise ToolError(
            "CAPABILITY_NOT_FOUND",
            f"Unknown capability: {value}",
            details={"suggestions": suggestions},
            recovery="Run capabilities --category <category-slug> or search with --query.",
        )

    def list_capabilities(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        risk: str | None = None,
    ) -> list[dict[str, Any]]:
        result = list(self.profiles["profiles"])
        if category:
            category_slug = re.sub(r"[^a-z0-9]+", "-", category.lower()).strip("-")
            result = [item for item in result if item["category_slug"] == category_slug]
            if not result:
                raise ToolError(
                    "CATEGORY_NOT_FOUND",
                    f"Unknown category: {category}",
                    details={"available_categories": [item["slug"] for item in self.catalog["categories"]]},
                    recovery="Use catalog categories to list exact category slugs.",
                )
        if risk:
            result = [item for item in result if item["risk"] == risk]
        if query:
            needle = query.lower()
            result = [
                item
                for item in result
                if needle in f"{item['name']} {item['description']} {item['slug']}".lower()
            ]
        return result

    def compact(self, items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        items = items or list(self.profiles["profiles"])
        return [
            {
                "id": item["id"],
                "slug": item["slug"],
                "name": item["name"],
                "category": item["category"],
                "category_slug": item["category_slug"],
                "risk": item["risk"],
                "handler": item["handler"],
                "description": item["description"],
            }
            for item in items
        ]

    def coverage(self) -> dict[str, Any]:
        categories = [
            {
                "index": category["index"],
                "name": category["name"],
                "slug": category["slug"],
                "catalog_capabilities": len(category["capabilities"]),
                "registered_handlers": sum(
                    1 for profile in self.profiles["profiles"] if profile["category_slug"] == category["slug"]
                ),
            }
            for category in self.catalog["categories"]
        ]
        return {
            "umbrella": self.catalog["umbrella"],
            "catalog_counts": self.catalog["counts"],
            "profile_counts": self.profiles["counts"],
            "categories": categories,
            "all_capabilities_have_handlers": all(
                item["registered_handlers"] == item["catalog_capabilities"] for item in categories
            ),
            "generated_at": now_iso(),
        }


def load_policy(config: dict[str, Any], *, paths: Any = None) -> dict[str, Any]:
    policy = load_json(config_path(config, "policy", paths=paths), label="Safety policy")
    required = {"schema_version", "approval", "risk_order", "keyword_risks", "explicit_capability_risks", "execution"}
    missing = sorted(required - set(policy))
    if missing:
        raise ToolError(
            "INVALID_POLICY",
            "The safety policy is missing required sections.",
            details={"missing": missing},
            exit_code=4,
        )
    return policy


def load_prompts(config: dict[str, Any], *, paths: Any = None) -> dict[str, Any]:
    prompts = load_json(config_path(config, "prompts", paths=paths), label="Reasoning lenses")
    if "lenses" not in prompts or not isinstance(prompts["lenses"], dict):
        raise ToolError("INVALID_PROMPT_SET", "Prompt set must contain a lenses object.", exit_code=4)
    return prompts


def load_role_blueprints(config: dict[str, Any], *, paths: Any = None) -> dict[str, Any]:
    blueprints = load_json(config_path(config, "role_blueprints", paths=paths), label="Role blueprints")
    if "roles" not in blueprints or not isinstance(blueprints["roles"], dict):
        raise ToolError("INVALID_ROLE_BLUEPRINTS", "Role blueprints must contain a roles object.", exit_code=4)
    return blueprints


def load_registry(config: dict[str, Any], *, paths: Any = None) -> CapabilityRegistry:
    catalog = load_json(config_path(config, "catalog", paths=paths), label="Catalog")
    profiles = load_json(config_path(config, "profiles", paths=paths), label="Capability profiles")
    policy = load_policy(config, paths=paths)
    require_schema(catalog, load_schema("catalog", paths=paths), label="Catalog")
    require_schema(profiles, load_schema("capability_profiles", paths=paths), label="Capability profiles")
    if catalog["counts"]["capabilities"] != len(catalog["capabilities"]):
        raise ToolError("CATALOG_COUNT_MISMATCH", "Catalog count does not match capability array.", exit_code=4)
    if profiles["counts"]["capabilities"] != len(profiles["profiles"]):
        raise ToolError("PROFILE_COUNT_MISMATCH", "Profile count does not match profile array.", exit_code=4)
    if len(profiles["profiles"]) != len(catalog["capabilities"]):
        raise ToolError(
            "CATALOG_PROFILE_MISMATCH",
            "Every catalog capability must have exactly one runtime profile.",
            details={
                "catalog": len(catalog["capabilities"]),
                "profiles": len(profiles["profiles"]),
            },
            exit_code=4,
        )
    return CapabilityRegistry(catalog, profiles, policy)
