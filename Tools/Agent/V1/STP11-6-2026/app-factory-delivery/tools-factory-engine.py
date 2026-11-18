#!/usr/bin/env python3
"""Domain engine for the app-factory-delivery Skill Tool.

Pure, importable functions used by ``tools-app-factory.py`` (and by
``tools-selftest.py``) for:

* catalog lookup, term-weighted routing and preset recommendation
* requirements -> backlog generation with acceptance criteria and NFR coverage
* weighted stack selection plus architecture and decision records
* risk-based test strategy generation
* security/privacy control evaluation against supplied evidence
* project inventory, SHA-256 manifest, semver/changelog and release notes
* stage gate, readiness scoring and capability-boundary checks

The module performs no network access, no shell execution and writes no files;
callers own I/O. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+([0-9A-Za-z.\-]+))?$")

STOPWORDS = {
    "a", "an", "and", "app", "application", "applications", "are", "as", "at", "be", "build", "builder",
    "by", "capability", "capabilities", "create", "creates", "creating", "design", "do", "for", "from",
    "general", "generate", "has", "have", "how", "in", "into", "is", "it", "its", "make", "makes", "of",
    "on", "or", "our", "per", "produce", "produces", "read", "repairs", "skill", "skills", "that", "the",
    "their", "them", "then", "there", "these", "this", "to", "tool", "tools", "using", "via", "was", "well",
    "what", "when", "where", "which", "while", "who", "will", "with", "works", "your",
    "add", "adds", "allow", "allows", "also", "any", "all", "each", "let", "lets", "need", "needs",
    "show", "shows", "print", "prints", "see", "so", "such", "use", "used", "uses", "want", "wants",
    "may", "can", "could", "should", "must", "just", "only", "like", "give", "gives", "set", "sets",
}

SEVERITY_ORDER = {"blocker": 0, "critical": 0, "high": 1, "major": 1, "medium": 2, "minor": 2, "low": 3}

ARCHITECTURE_PROFILES = {
    "web-static": {
        "components": [
            {"name": "Page shell", "responsibility": "Renders semantic markup and the shared layout for every page.", "interface": "HTML documents served over HTTPS"},
            {"name": "Styles and assets", "responsibility": "Applies responsive, accessible styling and delivers images and fonts.", "interface": "CSS and static asset URLs with cache headers"},
            {"name": "Client behaviour", "responsibility": "Adds progressive enhancement and client-side validation only where it helps.", "interface": "Script module entry point"},
            {"name": "Content source", "responsibility": "Supplies text, data files or API responses the pages render.", "interface": "Static JSON or a read-only API"},
            {"name": "Delivery", "responsibility": "Serves the built tree from a static host or CDN with compression and cache policy.", "interface": "Host or edge configuration"},
        ],
        "data_model": "Content lives in static files or a read-only API; any client state is derived at render time and never treated as authoritative.",
        "deployment": "Static assets are published to a static host or CDN over HTTPS with per-class cache headers and an invalidation step for deploys.",
    },
    "web-spa": {
        "components": [
            {"name": "Application shell", "responsibility": "Boots the single-page app and hosts routing and layout.", "interface": "Index document and bundled entry module"},
            {"name": "Route views", "responsibility": "Renders each route with loading, empty and error states.", "interface": "Route table with lazy-loaded views"},
            {"name": "State store", "responsibility": "Holds shared client state and cache with explicit ownership rules.", "interface": "Store slices and selectors"},
            {"name": "API client", "responsibility": "Calls the backend with typed contracts, timeouts and error mapping.", "interface": "Typed HTTP client per service"},
            {"name": "Observability", "responsibility": "Captures client errors and key journey metrics without personal data.", "interface": "Error and event sink"},
        ],
        "data_model": "Server data is authoritative and cached in the client store with an expiry policy; UI state is derived and disposable.",
        "deployment": "Built bundles are published to a static host or CDN, with the API served from its own origin and CORS, CSP and cache headers configured explicitly.",
    },
    "web-ssr": {
        "components": [
            {"name": "Route tree", "responsibility": "Maps URLs to server and client components with layout composition.", "interface": "File-based or declared route tree"},
            {"name": "Server data layer", "responsibility": "Fetches and caches data on the server with revalidation policy.", "interface": "Server data functions with cache keys"},
            {"name": "Mutation endpoints", "responsibility": "Validates input, enforces authorization and revalidates affected views.", "interface": "Server actions or route handlers"},
            {"name": "Metadata and assets", "responsibility": "Provides titles, social metadata, icons and optimized assets.", "interface": "Metadata exports and asset pipeline"},
            {"name": "Observability", "responsibility": "Logs server errors and reports client-side exceptions.", "interface": "Log sink and error reporter"},
        ],
        "data_model": "Server-side data access owns persistence and caching; the client receives serializable props only and never holds credentials for the data store.",
        "deployment": "The app runs on a Node runtime behind a proxy or a managed platform, with build output cached and environment configuration injected per stage.",
    },
    "backend": {
        "components": [
            {"name": "HTTP API", "responsibility": "Accepts requests, validates payloads and returns documented responses.", "interface": "Versioned HTTP routes with schema validation"},
            {"name": "Domain services", "responsibility": "Owns business rules and transaction boundaries.", "interface": "In-process service calls"},
            {"name": "Data access", "responsibility": "Maps domain objects to storage with parameterized queries.", "interface": "Repository interface per aggregate"},
            {"name": "Background worker", "responsibility": "Runs scheduled and queued work idempotently with retries.", "interface": "Queue or scheduler contract"},
            {"name": "Observability", "responsibility": "Emits traces, metrics and structured audit logs.", "interface": "OTLP or log and metric sink"},
        ],
        "data_model": "Primary records live in the chosen database with explicit constraints, indexes and transaction boundaries; derived caches are disposable.",
        "deployment": "The service runs behind a reverse proxy or managed runtime with health and readiness endpoints, bounded timeouts and structured logs.",
    },
    "cli": {
        "components": [
            {"name": "Command parser", "responsibility": "Defines commands, options, defaults and help text; rejects unknown input.", "interface": "Declarative argument schema with exit codes"},
            {"name": "Command handlers", "responsibility": "Implements one behaviour per subcommand with explicit input validation.", "interface": "Handler per command with a typed options object"},
            {"name": "Application core", "responsibility": "Holds the reusable logic that is unit tested without the command layer.", "interface": "Pure functions or service classes"},
            {"name": "Configuration and IO", "responsibility": "Resolves config sources, file paths and streams without hidden globals.", "interface": "Config loader and explicit path handling"},
            {"name": "Diagnostics", "responsibility": "Writes structured diagnostics to stderr and machine output to stdout.", "interface": "Exit codes, stderr messages and optional JSON output"},
        ],
        "data_model": "Input arrives from arguments, environment or a config file; durable state is written only to documented paths with atomic replace semantics.",
        "deployment": "Distributed as a source archive or an installed console entry point, with a documented interpreter version and an explicit run command.",
    },
    "mobile": {
        "components": [
            {"name": "App shell", "responsibility": "Boots the app, holds navigation and lifecycle handling.", "interface": "Platform entry point"},
            {"name": "Screen and navigation layer", "responsibility": "Presents screens and routes between them with state restoration.", "interface": "Route table and view models"},
            {"name": "Local persistence", "responsibility": "Stores data offline with migrations and corruption recovery.", "interface": "Repository over the embedded database or files"},
            {"name": "Network client", "responsibility": "Calls remote APIs with retries, backoff and cache policy.", "interface": "Typed client per remote service"},
            {"name": "Platform services", "responsibility": "Wraps permissions, notifications, background tasks and secure storage.", "interface": "Platform adapter interfaces"},
        ],
        "data_model": "Records are stored on device with migrations and conflict handling; remote sync is idempotent and resumable after interruption.",
        "deployment": "A signed application package is distributed through a store track or internal channel with staged rollout and a documented rollback path.",
    },
    "desktop": {
        "components": [
            {"name": "Main process", "responsibility": "Owns window lifecycle, privileged operations and update checks.", "interface": "Process entry point"},
            {"name": "UI renderer", "responsibility": "Renders the interface with no direct privileged access.", "interface": "Renderer entry point"},
            {"name": "Privileged bridge", "responsibility": "Exposes an explicit, validated command surface to the UI.", "interface": "Named IPC channels or commands"},
            {"name": "Local storage", "responsibility": "Persists settings and user data with schema migrations.", "interface": "Settings and data files in the user profile"},
            {"name": "Updater", "responsibility": "Checks signed update feeds and applies updates safely.", "interface": "Signed update manifest"},
        ],
        "data_model": "Settings and user data are stored in the user profile with schema migrations and atomic writes; no credentials are kept in plain text.",
        "deployment": "A signed installer or bundle is distributed per platform with an update feed and rollback to the previous version.",
    },
    "container": {
        "components": [
            {"name": "Image build", "responsibility": "Builds a reproducible, minimal image with pinned base and no build tools in the final layer.", "interface": "Multi-stage Dockerfile with digest-pinned base"},
            {"name": "Runtime process", "responsibility": "Runs as a non-root user with a single entry process and signal handling.", "interface": "Container entry point"},
            {"name": "Configuration and secrets", "responsibility": "Reads configuration from the environment or secret mounts at start-up.", "interface": "Environment variables and mounted files"},
            {"name": "Health and readiness", "responsibility": "Exposes health and readiness signals used by the orchestrator.", "interface": "HTTP or exec probes"},
            {"name": "Observability", "responsibility": "Emits logs and metrics to stdout and the platform collector.", "interface": "stdout log stream and metric endpoint"},
        ],
        "data_model": "The container is stateless; durable data lives in an external volume or managed service reachable with bounded timeouts.",
        "deployment": "The image is published to a registry by digest and run by an orchestrator or platform with resource limits and health checks.",
    },
}

COMPONENT_SET_BY_PLATFORM = {"web": "web-spa", "backend": "backend", "mobile": "mobile", "desktop": "desktop"}

BASE_RISKS = [
    {"risk": "Target version and toolchain are unresolved, so syntax or API guidance could mix incompatible versions.", "mitigation": "Record the exact language, framework, SDK and builder versions before generating or editing code.", "severity": "high"},
    {"risk": "Verification may be claimed without being run, especially where the toolchain is absent from the environment.", "mitigation": "Require a recorded verification report; report missing executables as blocked instead of passing.", "severity": "high"},
    {"risk": "Dependency drift or an unpatched library introduces a known vulnerability at release time.", "mitigation": "Pin dependencies in a lockfile, run an advisory scan and record the audit result in the security stage.", "severity": "medium"},
    {"risk": "Scope grows into unrequested platforms or features and the delivery slips.", "mitigation": "Keep the backlog limited to the recorded stories and route out-of-scope requests to explicit new stories.", "severity": "medium"},
    {"risk": "Credentials or customer data leak into the delivery set.", "mitigation": "Run the sensitive-file check in packaging and keep secrets out of the project tree entirely.", "severity": "high"},
    {"risk": "Store or platform policy rejection late in the cycle.", "mitigation": "Review permissions, privacy declarations and metadata during architecture, not at submission time.", "severity": "medium"},
]

CONVENTIONAL_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$")


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #
def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, max_bytes: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 256)
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise ValueError(f"file exceeds max_file_bytes_hashed ({max_bytes}): {path}")
            digest.update(chunk)
    return digest.hexdigest(), size


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        if len(raw) > 3 and raw.endswith("ies"):
            raw = raw[:-3] + "y"
        elif len(raw) > 3 and raw.endswith("s") and not raw.endswith("ss"):
            raw = raw[:-1]
        tokens.append(raw)
    return tokens


def slugify(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-")


def catalog_index(catalog: dict) -> dict:
    index = {}
    for category in catalog.get("categories", []):
        for skill in category.get("skills", []):
            index[skill["slug"]] = {
                "name": skill["name"],
                "slug": skill["slug"],
                "description": skill.get("description", ""),
                "category": category["name"],
                "category_slug": category["slug"],
            }
    return index


def build_idf(catalog: dict) -> dict:
    """Inverse document frequency over skill name + description + category name."""
    documents = []
    for category in catalog.get("categories", []):
        for skill in category.get("skills", []):
            text = f"{skill['name']} {skill.get('description', '')} {category['name']}"
            documents.append(set(tokenize(text)))
    total = len(documents) or 1
    counts: dict[str, int] = {}
    for tokens in documents:
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
    return {token: 1.0 + math.log((total + 1) / (count + 1)) for token, count in counts.items()}


def find_category(catalog: dict, selector: str) -> dict | None:
    selector = (selector or "").strip().lower()
    for category in catalog.get("categories", []):
        if selector in {str(category.get("index")), str(category.get("number")), category["slug"], category["name"].lower()}:
            return category
    return None


def search_catalog(catalog: dict, query: str, category_selector: str | None = None, limit: int = 20) -> dict:
    """Term-weighted search across the catalog; returns ranked skills."""
    idf = build_idf(catalog)
    tokens = tokenize(query or "")
    categories = catalog.get("categories", [])
    if category_selector:
        category = find_category(catalog, category_selector)
        if category is None:
            raise ValueError(f"unknown category selector: {category_selector}")
        categories = [category]

    results = []
    for category in categories:
        category_tokens = set(tokenize(category["name"]))
        for skill in category.get("skills", []):
            name_tokens = set(tokenize(skill["name"]))
            desc_tokens = set(tokenize(skill.get("description", "")))
            matched = []
            score = 0.0
            for token in tokens:
                weight = idf.get(token, 1.0)
                hit = 0.0
                if token in name_tokens:
                    hit += 3.0
                if token in category_tokens:
                    hit += 1.6
                if token in desc_tokens:
                    hit += 1.0
                if hit:
                    matched.append(token)
                    score += hit * weight
            if not tokens:
                score = 1.0
            if score > 0:
                results.append(
                    {
                        "name": skill["name"],
                        "slug": skill["slug"],
                        "category": category["name"],
                        "category_slug": category["slug"],
                        "description": skill.get("description", ""),
                        "score": round(score, 4),
                        "matched_terms": sorted(set(matched)),
                    }
                )
    results.sort(key=lambda item: (-item["score"], item["name"]))
    ranked = results[: max(1, limit)]
    top = ranked[0]["score"] if ranked else 0.0
    for item in ranked:
        item["relevance"] = round(item["score"] / top, 3) if top else 0.0
    return {"query": query, "category": category_selector, "tokens": sorted(set(tokens)), "count": len(results), "results": ranked}


# --------------------------------------------------------------------------- #
# routing and presets
# --------------------------------------------------------------------------- #
def recommend_presets(presets: dict, request: str, platform: str | None, language: str | None, limit: int = 3) -> list[dict]:
    tokens = set(tokenize(request or ""))
    recomputed = []
    for key, preset in presets.get("presets", {}).items():
        score = 0.0
        reasons = []
        if platform and platform.lower() == str(preset.get("platform", "")).lower():
            score += 3.0
            reasons.append(f"platform match: {platform.lower()}")
        preset_languages = {str(item).lower() for item in preset.get("languages", [])}
        if language:
            if language.lower() in preset_languages:
                score += 3.0
                reasons.append(f"language match: {language.lower()}")
        overlap = tokens & (preset_languages | set(tokenize(preset.get("label", ""))) | set(tokenize(key.replace("-", " "))))
        if overlap:
            score += 1.2 * len(overlap)
            reasons.append("request terms: " + ", ".join(sorted(overlap)[:4]))
        for category_slug in preset.get("catalog_categories", []):
            category_tokens = set(tokenize(category_slug.replace("-", " ")))
            shared = tokens & category_tokens
            if shared:
                score += 0.4 * len(shared)
        if score > 0:
            recomputed.append(
                {
                    "key": key,
                    "label": preset.get("label", key),
                    "platform": preset.get("platform", ""),
                    "languages": preset.get("languages", []),
                    "score": round(score, 3),
                    "reasons": reasons or ["weak lexical match"],
                }
            )
    recomputed.sort(key=lambda item: (-item["score"], item["key"]))
    return recomputed[: max(1, limit)]


def route(catalog: dict, presets: dict, workflow: dict, request: str, platform=None, language=None, category=None, limit: int = 12) -> dict:
    """Route a free-text request to catalog skills, presets and factory stages."""
    request = (request or "").strip()
    if not request:
        raise ValueError("route requires a non-empty --request")
    max_chars = int(workflow.get("limits", {}).get("max_request_chars", 8000))
    if len(request) > max_chars:
        raise ValueError(f"request exceeds max_request_chars ({max_chars})")

    search = search_catalog(catalog, request, category_selector=category, limit=max(limit * 3, 30))
    preset_ranking = recommend_presets(presets, request, platform, language)
    boosted_categories = set()
    for item in preset_ranking[:2]:
        boosted_categories.update(presets["presets"][item["key"]].get("catalog_categories", []))

    boosted = []
    for item in search["results"]:
        bonus = 1.5 if item["category_slug"] in boosted_categories else 0.0
        boosted.append({**item, "score": round(item["score"] + bonus, 4), "preset_boost": bool(bonus)})
    boosted.sort(key=lambda item: (-item["score"], item["name"]))
    top = boosted[: max(1, limit)]
    max_score = top[0]["score"] if top else 0.0
    for item in top:
        item["relevance"] = round(item["score"] / max_score, 3) if max_score else 0.0

    composition = []
    claimed = {item["slug"] for item in top}
    index = catalog_index(catalog)
    for stage in workflow.get("stages", []):
        skills = []
        for slug in stage.get("catalog_skills", []):
            entry = index.get(slug)
            skills.append(
                {
                    "name": entry["name"] if entry else slug,
                    "slug": slug,
                    "in_catalog": bool(entry),
                }
            )
        specialists = [item for item in top if item["slug"] not in stage.get("catalog_skills", [])][:4]
        composition.append(
            {
                "stage": stage["id"],
                "title": stage["title"],
                "required_skills": skills,
                "specialists": [{"name": item["name"], "slug": item["slug"], "relevance": item["relevance"]} for item in specialists],
            }
        )

    notes = []
    if platform and platform.lower() not in {str(p.get("platform", "")).lower() for p in presets.get("presets", {}).values()}:
        notes.append(f"platform '{platform}' has no dedicated preset; nearest presets are listed instead")
    if not preset_ranking:
        notes.append("no preset matched the request terms; choose one explicitly with 'init --preset'")
    return {
        "request": request,
        "constraints": {"platform": platform, "language": language, "category": category},
        "catalog_counts": catalog.get("counts", {}),
        "skills": top,
        "presets": preset_ranking,
        "stage_composition": composition,
        "notes": notes,
    }


def select_stack(presets: dict, preset_key: str, weights: dict | None = None, candidate: str | None = None) -> dict:
    """Weighted stack ranking for one preset (Technology Stack Selector behaviour)."""
    preset = presets.get("presets", {}).get(preset_key)
    if preset is None:
        raise ValueError(f"unknown preset '{preset_key}'")
    criteria = presets.get("criteria", [])
    if not criteria:
        raise ValueError("preset file defines no scoring criteria")
    effective = {}
    for criterion in criteria:
        default = float(criterion.get("default_weight", 0))
        effective[criterion["id"]] = float((weights or {}).get(criterion["id"], default))
    unknown = set((weights or {})) - set(effective)
    if unknown:
        raise ValueError("unknown weight criteria: " + ", ".join(sorted(unknown)))
    total_weight = sum(effective.values())
    if total_weight <= 0:
        raise ValueError("weights must sum to a positive value")

    candidates = []
    for item in preset.get("candidates", []):
        scores = {key: float(value) for key, value in item.get("scores", {}).items()}
        missing = set(effective) - set(scores)
        if missing:
            raise ValueError(f"candidate '{item['key']}' is missing scores for: {', '.join(sorted(missing))}")
        weighted = sum(scores[key] * effective[key] for key in effective) / total_weight
        candidates.append(
            {
                "key": item["key"],
                "label": item.get("label", item["key"]),
                "notes": item.get("notes", ""),
                "scores": scores,
                "weighted_score": round(weighted, 3),
                "out_of": 5,
            }
        )
    candidates.sort(key=lambda item: (-item["weighted_score"], item["key"]))
    for position, item in enumerate(candidates, start=1):
        item["rank"] = position

    chosen = candidates[0]["key"]
    if candidate:
        match = next((item for item in candidates if item["key"] == candidate), None)
        if match is None:
            raise ValueError(
                f"candidate '{candidate}' is not defined for preset '{preset_key}'; "
                f"available: {', '.join(item['key'] for item in candidates)}"
            )
        chosen = match["key"]

    return {
        "preset": preset_key,
        "preset_label": preset.get("label", preset_key),
        "platform": preset.get("platform", ""),
        "criteria": [
            {"id": c["id"], "label": c["label"], "weight": round(effective[c["id"]], 4), "default_weight": c.get("default_weight", 0)}
            for c in criteria
        ],
        "weight_total": round(total_weight, 4),
        "candidates": candidates,
        "chosen": chosen,
        "method": "weighted sum of 1-5 criterion scores divided by the total criterion weight; ties broken by candidate key",
    }


# --------------------------------------------------------------------------- #
# requirements -> backlog
# --------------------------------------------------------------------------- #
STORY_PREFIX_RE = re.compile(r"^\s*(?:[-*\u2022\u25aa]|\d{1,3}[.)])\s+")
AC_PREFIX_RE = re.compile(r"^\s*(?:[-*\u2022\u25aa]|ac\d*[:.)]|acceptance(?:\s+criteria)?[:.)])\s*(.+)$", re.I)
QUESTION_RE = re.compile(r"^\s*(?:[-*\u2022]\s*)?(?:open\s+question|question|q)\s*[:.)-]\s*(.+)$", re.I)
ASSUMPTION_RE = re.compile(r"^\s*(?:[-*\u2022]\s*)?(?:assumption|assume)\s*[:.)-]\s*(.+)$", re.I)
AS_A_RE = re.compile(r"^as\s+an?\s+(?P<persona>[^,]+),?\s+i\s+want\s+(?P<want>.+?)(?:\s+so\s+that\s+(?P<benefit>.+))?$", re.I)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

DERIVED_QUESTIONS = [
    "Which exact language, framework, SDK and builder versions must be targeted for this delivery?",
    "Which platforms and environments must the first release actually run on, including minimum OS or browser versions?",
    "What data classification applies to the records the application stores, and who approves retention and deletion?",
]


def _priority_for(text: str, rules: list[dict]) -> str:
    lowered = (text or "").lower()
    for rule in rules:
        if any(keyword in lowered for keyword in rule.get("keywords", [])):
            return rule["priority"]
    return "P2"


def build_requirements(
    sources: list[str],
    workflow: dict,
    project_name: str,
    preset_key: str | None = None,
    source_path: str = "",
    default_persona: str = "end user",
    max_stories: int | None = None,
) -> dict:
    """Turn free text requirement notes into a structured backlog."""
    templates = workflow.get("templates", {})
    nfr_keywords = templates.get("nfr_keywords", {})
    priority_rules = templates.get("priority_rules", [])
    ac_templates = templates.get("acceptance_criteria", [])
    limits = workflow.get("limits", {})
    max_stories = int(max_stories or limits.get("max_stories", 400))

    lines: list[str] = []
    for source in sources:
        for raw in (source or "").split("\n"):
            line = raw.replace("\u00a0", " ").rstrip()
            if line.strip():
                lines.append(line)
    if not lines:
        raise ValueError("no requirement text supplied; pass --input <file> or one or more --requirement values")

    epics: list[dict] = []
    stories: list[dict] = []
    assumptions: list[dict] = []
    questions: list[str] = []
    notes: list[str] = []
    current_epic = None
    current_story = None

    def start_epic(title: str) -> dict:
        epic = {"id": f"EP-{len(epics) + 1}", "title": title.strip(), "stories": []}
        epics.append(epic)
        return epic

    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            title = heading.group(2).strip()
            lowered = title.lower()
            if lowered.startswith(("epic", "capability", "module")):
                title = re.sub(r"^(epic|capability|module)\s*[:.-]\s*", "", title, flags=re.I)
            current_epic = start_epic(title)
            current_story = None
            continue

        question = QUESTION_RE.match(line)
        if question:
            questions.append(question.group(1).strip())
            continue

        assumption = ASSUMPTION_RE.match(line)
        if assumption:
            assumptions.append({"text": assumption.group(1).strip(), "origin": "supplied"})
            continue

        bullet = STORY_PREFIX_RE.match(line)
        text = line[len(bullet.group(0)):].strip() if bullet else line.strip()

        explicit_ac = re.match(r"^\s*(?:ac\d*[:.)]|acceptance(?:\s+criteria)?[:.)])\s*(.+)$", line, re.I)
        if explicit_ac and current_story is not None:
            criterion = explicit_ac.group(1).strip()
            if criterion:
                current_story["_explicit_ac"].append(criterion)
            continue

        if bullet or (current_story is None and len(text) <= 400):
            as_a = AS_A_RE.match(text)
            persona = default_persona
            want = text
            benefit = ""
            if as_a:
                persona = as_a.group("persona").strip()
                want = as_a.group("want").strip()
                benefit = (as_a.group("benefit") or "").strip()
            else:
                persona_match = re.search(r"\b(?:user|admin|administrator|operator|customer|developer|reviewer|analyst|teacher|student|patient|clerk)s?\b", text, re.I)
                if persona_match:
                    persona = persona_match.group(0).lower()
            if current_epic is None:
                current_epic = start_epic("Core capability")
            story = {
                "id": f"US-{len(stories) + 1:03d}",
                "epic": current_epic["title"],
                "epic_id": current_epic["id"],
                "title": _normalise_title(want),
                "persona": persona,
                "benefit": benefit,
                "priority": _priority_for(text, priority_rules),
                "source_text": text,
                "_explicit_ac": [],
            }
            stories.append(story)
            current_epic["stories"].append(story["id"])
            current_story = story
            if len(stories) > max_stories:
                raise ValueError(f"story count exceeds max_stories ({max_stories}); split the requirement set")
        else:
            notes.append(text)

    if not stories:
        raise ValueError("no user stories could be derived from the supplied text; use bullets, numbered lines or 'As a ... I want ...' statements")

    persona_counts: dict[str, int] = {}
    for story in stories:
        persona_counts[story["persona"]] = persona_counts.get(story["persona"], 0) + 1

    for story in stories:
        criteria = []
        explicit = story.pop("_explicit_ac")
        for index, criterion in enumerate(explicit, start=1):
            criteria.append({"id": f"AC-{index}", "text": criterion, "generated": False})
        target = max(3, len(explicit))
        for template in ac_templates:
            if len(criteria) >= target:
                break
            text = template.format(persona=story["persona"], feature=story["title"], title=story["title"])
            criteria.append({"id": f"AC-{len(criteria) + 1}", "text": text, "generated": True})
        story["acceptance_criteria"] = criteria
        story["nfr_tags"] = _nfr_tags(f"{story['source_text']} {story['benefit']}", nfr_keywords)
        story.pop("source_text", None)

    corpus = " ".join(lines).lower()
    coverage = {}
    for name, keywords in nfr_keywords.items():
        hits = sorted({keyword for keyword in keywords if keyword in corpus})
        coverage[name] = {"addressed": bool(hits), "evidence": hits[:5]}

    if not assumptions:
        assumptions = [
            {"text": f"Delivery targets the '{preset_key}' preset; toolchain versions are still to be confirmed.", "origin": "derived"}
            if preset_key
            else {"text": "A preset must be selected before code generation begins.", "origin": "derived"},
            {"text": "The first release is limited to the stories listed here; new requests become new stories.", "origin": "derived"},
        ]
    if not questions:
        questions = list(DERIVED_QUESTIONS)

    payload = {
        "schema_version": "1.0",
        "artifact": "requirements-backlog",
        "project": project_name,
        "preset": preset_key,
        "generated_utc": now_utc(),
        "source": {
            "path": source_path,
            "sha256": sha256_bytes("\n".join(sources).encode("utf-8")),
            "chars": len("\n".join(sources)),
        },
        "epics": epics,
        "stories": stories,
        "personas": [{"persona": name, "stories": count} for name, count in sorted(persona_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "nfr_coverage": coverage,
        "assumptions": assumptions,
        "open_questions": questions,
        "unparsed_notes": notes[:50],
    }
    return payload


def _normalise_title(text: str) -> str:
    """Clean a story title: drop leading infinitive, trailing punctuation, cap length."""
    title = re.sub(r"\s+", " ", (text or "")).strip()
    title = re.sub(r"^(?:i\s+(?:want|need|would\s+like)\s+)", "", title, flags=re.I)
    title = re.sub(r"^to\s+", "", title, flags=re.I)
    title = title.rstrip(" .;:")
    if len(title) > 160:
        title = title[:157].rstrip() + "..."
    return title[:1].upper() + title[1:] if title else title


def _nfr_tags(text: str, nfr_keywords: dict) -> list[str]:
    lowered = (text or "").lower()
    return sorted(name for name, keywords in nfr_keywords.items() if any(keyword in lowered for keyword in keywords))


def render_requirements_md(payload: dict) -> str:
    lines = [
        f"# Requirements and backlog: {payload['project']}",
        "",
        f"- Generated: {payload['generated_utc']}",
        f"- Preset: `{payload.get('preset') or 'not selected'}`",
        f"- Stories: {len(payload['stories'])}",
        f"- Source: {payload['source'].get('path') or 'inline text'} (sha256 `{payload['source']['sha256'][:16]}...`)",
        "",
        "## Personas",
        "",
        "| Persona | Stories |",
        "| --- | --- |",
    ]
    for persona in payload["personas"]:
        lines.append(f"| {persona['persona']} | {persona['stories']} |")

    lines += ["", "## Epics", ""]
    for epic in payload["epics"]:
        lines.append(f"- **{epic['id']}** {epic['title']} ({len(epic['stories'])} stories)")

    lines += ["", "## Stories", ""]
    for story in payload["stories"]:
        lines += [
            f"### {story['id']} {story['title']}",
            "",
            f"- Epic: {story['epic_id']} {story['epic']}",
            f"- Persona: {story['persona']}",
            f"- Priority: {story['priority']}",
            f"- NFR tags: {', '.join(story['nfr_tags']) or 'none'}",
        ]
        if story.get("benefit"):
            lines.append(f"- Benefit: {story['benefit']}")
        lines.append("")
        lines.append("Acceptance criteria:")
        for criterion in story["acceptance_criteria"]:
            suffix = " _(generated template, review before use)_" if criterion["generated"] else ""
            lines.append(f"- `{criterion['id']}` {criterion['text']}{suffix}")
        lines.append("")

    lines += ["## Non-functional coverage", "", "| Category | Addressed | Evidence |", "| --- | --- | --- |"]
    for name, entry in payload["nfr_coverage"].items():
        lines.append(f"| {name} | {'yes' if entry['addressed'] else 'no'} | {', '.join(entry['evidence']) or '-'} |")

    lines += ["", "## Assumptions", ""]
    for item in payload["assumptions"]:
        lines.append(f"- ({item['origin']}) {item['text']}")
    lines += ["", "## Open questions", ""]
    for item in payload["open_questions"]:
        lines.append(f"- {item}")
    if payload.get("unparsed_notes"):
        lines += ["", "## Notes captured outside stories", ""]
        for item in payload["unparsed_notes"]:
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# architecture
# --------------------------------------------------------------------------- #
def build_architecture(
    presets: dict,
    preset_key: str,
    project_name: str,
    requirements: dict | None,
    weights: dict | None = None,
    candidate: str | None = None,
    target_version: str | None = None,
    notes: str = "",
) -> dict:
    preset = presets["presets"][preset_key]
    decision = select_stack(presets, preset_key, weights=weights, candidate=candidate)
    chosen = next(item for item in decision["candidates"] if item["key"] == decision["chosen"])
    platform = preset.get("platform", "web")
    profile_key = preset.get("component_set") or COMPONENT_SET_BY_PLATFORM.get(platform, "web-spa")
    profile = ARCHITECTURE_PROFILES.get(profile_key)
    if profile is None:
        raise ValueError(
            f"preset '{preset_key}' declares component_set '{profile_key}', which is not defined; "
            f"known sets: {', '.join(sorted(ARCHITECTURE_PROFILES))}"
        )
    components = profile["components"]

    risks = list(BASE_RISKS)
    missing = [name for name in preset.get("required_executables", []) if not _which(name)]
    for name in missing:
        risks.insert(
            0,
            {
                "risk": f"Required toolchain executable '{name}' is not available in this environment, so build and test claims cannot be verified here.",
                "mitigation": f"Install '{name}' or run verification on a machine that has it; keep the verification stage reported as blocked until then.",
                "severity": "high",
            },
        )

    risky_candidates = [item for item in decision["candidates"] if item["weighted_score"] < 3.6]
    for item in risky_candidates[:2]:
        risks.append(
            {
                "risk": f"Candidate stack '{item['label']}' scored {item['weighted_score']}/5 under the supplied weights.",
                "mitigation": f"Confirm the trade-off explicitly or select '{decision['chosen']}' instead.",
                "severity": "low",
            }
        )

    stories = (requirements or {}).get("stories", [])
    milestones = [
        {"name": "M1 Walking skeleton", "scope": "One happy-path journey end to end, with build and run commands recorded.", "checks": ["Build command succeeds", "Journey observable in the delivered artifact"]},
        {"name": "M2 Correctness", "scope": "All P0 stories implemented with acceptance criteria covered by tests.", "checks": ["Test strategy map has no unmapped criterion", "Unit and integration suites pass"]},
        {"name": "M3 Hardening", "scope": "Security, accessibility and performance controls evidenced.", "checks": ["Security stage has no open critical/high finding", "Accessibility checks recorded"]},
        {"name": "M4 Release", "scope": "Packaging, provenance and rollback recorded; readiness gate passed.", "checks": ["Manifest and checksums match the tree", "Readiness gate >= pass threshold"]},
    ]

    deployment = profile["deployment"]
    data_model = profile["data_model"]

    payload = {
        "schema_version": "1.0",
        "artifact": "architecture-decision",
        "project": project_name,
        "generated_utc": now_utc(),
        "preset": preset_key,
        "preset_label": preset.get("label", preset_key),
        "platform": platform,
        "component_set": profile_key,
        "target_version": target_version or "",
        "version_policy": {
            "languages": preset.get("languages", []),
            "target_version": target_version or "UNRESOLVED",
            "rule": "Resolve the exact language/framework/SDK/builder versions before generating code; never mix guidance from incompatible versions.",
            "required_executables": preset.get("required_executables", []),
            "optional_executables": preset.get("optional_executables", []),
        },
        "stack_decision": decision,
        "chosen": chosen,
        "components": components,
        "interfaces": [
            {"name": "Request/response contract", "description": "Every external call has a schema, a documented error shape and a timeout."},
            {"name": "Persistence contract", "description": "Reads and writes go through a repository boundary so storage can be replaced or mocked."},
        ],
        "data_model": data_model,
        "deployment": deployment,
        "risks": risks,
        "milestones": milestones,
        "acceptance_checks": [
            "Documented build command completes and produces the expected artifact.",
            "Documented run command starts the application and a smoke check passes.",
            "Every P0 acceptance criterion maps to at least one automated or scripted check.",
            "Release manifest checksums match the delivered tree.",
        ],
        "story_count": len(stories),
        "adr": {
            "id": "ADR-0001",
            "title": f"Adopt {chosen['label']} for the {preset.get('label', preset_key)} delivery",
            "status": "accepted" if candidate or chosen["rank"] == 1 else "proposed",
            "context": notes or "Stack chosen from the requested constraints and the preset candidate set.",
            "decision": f"Use {chosen['label']} ({chosen['weighted_score']}/5 weighted score) as the implementation stack.",
            "consequences": chosen.get("notes", ""),
        },
        "notes": notes,
    }
    return payload


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def render_architecture_md(payload: dict, workflow: dict) -> str:
    decision = payload["stack_decision"]
    lines = [
        f"# Architecture decision: {payload['project']}",
        "",
        f"- Generated: {payload['generated_utc']}",
        f"- Preset: `{payload['preset']}` ({payload['preset_label']})",
        f"- Platform: {payload['platform']}",
        f"- Target version: {payload['target_version'] or '**unresolved — resolve before generating code**'}",
        f"- Requirements stories considered: {payload['story_count']}",
        "",
        "## Chosen stack and rejected alternatives",
        "",
        f"**{payload['chosen']['label']}** — weighted score {payload['chosen']['weighted_score']}/5. {payload['chosen'].get('notes', '')}",
        "",
        "| Rank | Candidate | Weighted score | " + " | ".join(c["label"] for c in decision["criteria"]) + " |",
        "| --- | --- | --- | " + " | ".join("---" for _ in decision["criteria"]) + " |",
    ]
    for item in decision["candidates"]:
        cells = " | ".join(str(item["scores"][c["id"]]) for c in decision["criteria"])
        lines.append(f"| {item['rank']} | {item['label']} | {item['weighted_score']} | {cells} |")

    lines += ["", "Criterion weights: " + ", ".join(f"{c['id']}={c['weight']}" for c in decision["criteria"]), ""]

    lines += ["## Components and responsibilities", "", "| Component | Responsibility | Interface |", "| --- | --- | --- |"]
    for component in payload["components"]:
        lines.append(f"| {component['name']} | {component['responsibility']} | {component['interface']} |")

    lines += ["", "## Interfaces and contracts", ""]
    for item in payload["interfaces"]:
        lines.append(f"- **{item['name']}**: {item['description']}")

    lines += [
        "",
        "## Data model and storage",
        "",
        payload["data_model"],
        "",
        "## Deployment and runtime",
        "",
        payload["deployment"],
        "",
        "## Version and toolchain policy",
        "",
        f"- Languages: {', '.join(payload['version_policy']['languages']) or 'not specified'}",
        f"- Target version: {payload['version_policy']['target_version']}",
        f"- Required executables: {', '.join(payload['version_policy']['required_executables']) or 'none'}",
        f"- Optional executables: {', '.join(payload['version_policy']['optional_executables']) or 'none'}",
        f"- {payload['version_policy']['rule']}",
        "",
        "## Risks and mitigations",
        "",
        "| Severity | Risk | Mitigation |",
        "| --- | --- | --- |",
    ]
    for risk in payload["risks"]:
        lines.append(f"| {risk['severity']} | {risk['risk']} | {risk['mitigation']} |")

    lines += ["", "## Milestones and acceptance checks", "", "| Milestone | Scope | Checks |", "| --- | --- | --- |"]
    for milestone in payload["milestones"]:
        lines.append(f"| {milestone['name']} | {milestone['scope']} | {'; '.join(milestone['checks'])} |")

    lines += ["", "## Delivery acceptance checks", ""]
    for check in payload["acceptance_checks"]:
        lines.append(f"- [ ] {check}")

    adr = payload["adr"]
    lines += [
        "",
        f"## {adr['id']}: {adr['title']}",
        "",
        f"- Status: {adr['status']}",
        f"- Context: {adr['context']}",
        f"- Decision: {adr['decision']}",
        f"- Consequences: {adr['consequences']}",
        "",
        "## Factory rules applied",
        "",
    ]
    for rule in workflow.get("templates", {}).get("rules", []):
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# test strategy
# --------------------------------------------------------------------------- #
LEVEL_HINTS = [
    ("e2e", ["journey", "checkout", "submit", "onboard", "login", "sign in", "workflow", "end to end", "browse", "schedule"]),
    ("integration", ["persist", "stored", "database", "reopen", "re-read", "sync", "queue", "api", "export", "import"]),
    ("unit", ["reject", "validate", "invalid", "error", "limit", "format", "calculate", "rule", "empty", "oversized"]),
]


def build_test_plan(requirements: dict, workflow: dict) -> dict:
    """Risk-based test plan mapped onto the acceptance criteria of the backlog."""
    templates = workflow.get("templates", {})
    levels = templates.get("test_levels", [])
    level_ids = [level["id"] for level in levels]

    cases = []
    unmapped = []
    for story in requirements.get("stories", []):
        for criterion in story.get("acceptance_criteria", []):
            text = criterion["text"].lower()
            chosen_levels = [level for level, hints in LEVEL_HINTS if any(hint in text for hint in hints)]
            if not chosen_levels:
                chosen_levels = ["unit", "integration"]
            for level in dict.fromkeys(chosen_levels):
                cases.append(
                    {
                        "id": f"TC-{len(cases) + 1:03d}",
                        "story": story["id"],
                        "criterion": criterion["id"],
                        "level": level,
                        "priority": story["priority"],
                        "title": f"[{level}] {story['title']} — {criterion['text'][:110]}",
                        "expected": criterion["text"],
                        "automated": level in {"unit", "component", "integration", "contract"},
                    }
                )
        if not story.get("acceptance_criteria"):
            unmapped.append({"story": story["id"], "criterion": "", "reason": "story has no acceptance criteria"})

    covered = {(case["story"], case["criterion"]) for case in cases}
    for story in requirements.get("stories", []):
        for criterion in story.get("acceptance_criteria", []):
            if (story["id"], criterion["id"]) not in covered:
                unmapped.append({"story": story["id"], "criterion": criterion["id"], "reason": "no test case generated"})

    cross_cutting = [
        {"level": "security", "title": "Authorization denial matrix for every route or action", "evidence": "Table of principal x action with an expected denial per row."},
        {"level": "security", "title": "Injection and malformed-input probes on each trust boundary", "evidence": "Recorded request/response pairs showing rejection without stack traces."},
        {"level": "accessibility", "title": "Keyboard-only journey and screen-reader announcement of state changes", "evidence": "Manual script plus automated rule results (contrast, labels, focus order)."},
        {"level": "performance", "title": "Latency and resource ceiling for the primary journey", "evidence": "Measured p95 against the stated target on a defined dataset size."},
        {"level": "recovery", "title": "Interrupted operation, retry and rollback behaviour", "evidence": "Replay of the failure path showing no duplicate or lost writes."},
        {"level": "contract", "title": "Consumer/provider schema compatibility for external interfaces", "evidence": "Schema diff or contract test run on the pinned versions."},
    ]

    levels_used = sorted({case["level"] for case in cases})
    payload = {
        "schema_version": "1.0",
        "artifact": "test-strategy",
        "project": requirements.get("project", ""),
        "preset": requirements.get("preset"),
        "generated_utc": now_utc(),
        "levels": [level for level in levels if level["id"] in levels_used or level["id"] in {"unit", "integration", "e2e"}],
        "levels_used": levels_used,
        "levels_available": level_ids,
        "cases": cases,
        "cross_cutting": cross_cutting,
        "coverage": {
            "criteria_total": sum(len(story.get("acceptance_criteria", [])) for story in requirements.get("stories", [])),
            "criteria_mapped": len({(case["story"], case["criterion"]) for case in cases}),
            "unmapped": unmapped,
        },
        "data_and_isolation": {
            "data": "Use synthetic fixtures keyed per test; never copy production records into tests or the release set.",
            "isolation": "Each test run gets a fresh database or schema, ephemeral storage and a unique namespace; clean up in teardown.",
            "determinism": "Fix clocks and random seeds, pin locales and time zones, and forbid live network calls in unit tests.",
        },
        "regression_policy": "Every reproduced defect first becomes a failing test at the lowest level that reproduces it; the fix lands only with that test.",
        "exit_criteria": [
            "All blocker-priority cases pass in the recorded verification run.",
            "No acceptance criterion is unmapped.",
            "Cross-cutting security, accessibility, performance and recovery checks have recorded evidence.",
        ],
    }
    return payload


def render_test_plan_md(payload: dict) -> str:
    lines = [
        f"# Test strategy: {payload['project']}",
        "",
        f"- Generated: {payload['generated_utc']}",
        f"- Criteria mapped: {payload['coverage']['criteria_mapped']}/{payload['coverage']['criteria_total']}",
        f"- Levels planned: {', '.join(payload['levels_used']) or 'none'}",
        f"- Test cases: {len(payload['cases'])}",
        "",
        "## Level plan",
        "",
        "| Level | Focus |",
        "| --- | --- |",
    ]
    for level in payload["levels"]:
        lines.append(f"| {level['label']} | {level['focus']} |")

    lines += ["", "## Cases by story", ""]
    current_story = None
    for case in payload["cases"]:
        if case["story"] != current_story:
            current_story = case["story"]
            lines += ["", f"### {case['story']}", ""]
        lines.append(f"- `{case['id']}` ({case['level']}, {case['priority']}) {case['title']}")
    if not payload["cases"]:
        lines.append("_No cases generated._")

    lines += ["", "## Cross-cutting suites", "", "| Level | Test | Evidence required |", "| --- | --- | --- |"]
    for item in payload["cross_cutting"]:
        lines.append(f"| {item['level']} | {item['title']} | {item['evidence']} |")

    lines += [
        "",
        "## Test data and isolation",
        "",
        f"- Data: {payload['data_and_isolation']['data']}",
        f"- Isolation: {payload['data_and_isolation']['isolation']}",
        f"- Determinism: {payload['data_and_isolation']['determinism']}",
        "",
        "## Regression policy",
        "",
        payload["regression_policy"],
        "",
        "## Exit criteria",
        "",
    ]
    for item in payload["exit_criteria"]:
        lines.append(f"- {item}")
    if payload["coverage"]["unmapped"]:
        lines += ["", "## Unmapped criteria", ""]
        for item in payload["coverage"]["unmapped"]:
            lines.append(f"- {item['story']} {item['criterion']}: {item['reason']}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# security review
# --------------------------------------------------------------------------- #
PASSING = {"pass", "not_applicable"}


def evaluate_security(controls_doc: dict, preset_key: str | None, evidence: dict, project_name: str = "") -> dict:
    statuses = set(controls_doc.get("status_values", ["pass", "fail", "partial", "not_applicable", "unknown"]))
    supplied = (evidence or {}).get("controls", {}) or {}
    unknown_keys = sorted(set(supplied) - {control["id"] for control in controls_doc.get("controls", [])})
    if unknown_keys:
        raise ValueError("evidence references unknown control ids: " + ", ".join(unknown_keys))

    findings = []
    for control in controls_doc.get("controls", []):
        applies = control.get("applies_to", ["*"])
        applicable = "*" in applies or (preset_key is not None and preset_key in applies)
        if not applicable:
            continue
        entry = supplied.get(control["id"], {})
        status = str(entry.get("status", "unknown")).lower()
        if status not in statuses:
            raise ValueError(f"control {control['id']} has invalid status '{status}'; allowed: {', '.join(sorted(statuses))}")
        note = str(entry.get("note", "")).strip()
        owner = str(entry.get("owner", "")).strip()
        findings.append(
            {
                "id": control["id"],
                "category": control["category"],
                "control": control["control"],
                "severity": control["severity"],
                "status": status,
                "note": note,
                "owner": owner,
                "evidence_hint": control.get("evidence_hint", ""),
            }
        )

    rollup: dict[str, dict[str, int]] = {}
    for finding in findings:
        by_status = rollup.setdefault(finding["severity"], {})
        by_status[finding["status"]] = by_status.get(finding["status"], 0) + 1

    open_high = [f for f in findings if f["severity"] in {"critical", "high"} and f["status"] not in PASSING]
    unevidenced = [f for f in findings if f["status"] == "unknown"]
    medium_untracked = [f for f in findings if f["severity"] == "medium" and f["status"] in {"fail", "partial"} and not (f["note"] or f["owner"])]
    privacy = [f for f in findings if f["category"] == "privacy"]
    secrets = [f for f in findings if f["category"] == "secrets"]

    payload = {
        "schema_version": "1.0",
        "artifact": "security-review",
        "project": project_name,
        "preset": preset_key,
        "generated_utc": now_utc(),
        "reviewer": (evidence or {}).get("reviewer", ""),
        "controls_total": len(findings),
        "rollup": rollup,
        "findings": findings,
        "open_critical_high": [f["id"] for f in open_high],
        "unevidenced": [f["id"] for f in unevidenced],
        "medium_untracked": [f["id"] for f in medium_untracked],
        "privacy_gaps": [f["id"] for f in privacy if f["status"] not in PASSING],
        "secrets_gaps": [f["id"] for f in secrets if f["status"] not in PASSING],
        "notes": (evidence or {}).get("notes", ""),
    }
    return payload


def render_security_md(payload: dict) -> str:
    lines = [
        f"# Security and privacy review: {payload['project'] or 'project'}",
        "",
        f"- Generated: {payload['generated_utc']}",
        f"- Preset: `{payload.get('preset') or 'none'}`",
        f"- Reviewer: {payload.get('reviewer') or 'not stated'}",
        f"- Controls in scope: {payload['controls_total']}",
        "",
        "## Rollup by severity",
        "",
        "| Severity | " + " | ".join(["pass", "partial", "fail", "not_applicable", "unknown"]) + " |",
        "| --- | " + " | ".join("---" for _ in range(5)) + " |",
    ]
    for severity in ["critical", "high", "medium", "low"]:
        counts = payload["rollup"].get(severity, {})
        cells = " | ".join(str(counts.get(status, 0)) for status in ["pass", "partial", "fail", "not_applicable", "unknown"])
        lines.append(f"| {severity} | {cells} |")

    lines += ["", "## Findings", "", "| Control | Severity | Status | Control statement | Note |", "| --- | --- | --- | --- | --- |"]
    for finding in payload["findings"]:
        note = finding["note"] or ("_" + finding["evidence_hint"] + "_" if finding["evidence_hint"] else "")
        lines.append(f"| {finding['id']} | {finding['severity']} | {finding['status']} | {finding['control']} | {note} |")

    lines += ["", "## Open items", ""]
    lines.append(f"- Critical/high not passing or unevidenced: {', '.join(payload['open_critical_high']) or 'none'}")
    lines.append(f"- Controls without evidence: {', '.join(payload['unevidenced']) or 'none'}")
    lines.append(f"- Medium findings without a note or owner: {', '.join(payload['medium_untracked']) or 'none'}")
    lines.append(f"- Privacy gaps: {', '.join(payload['privacy_gaps']) or 'none'}")
    lines.append(f"- Secret-handling gaps: {', '.join(payload['secrets_gaps']) or 'none'}")
    lines.append("")
    lines.append("Statuses come only from the supplied evidence file. A control with no evidence is reported as `unknown` and never counted as passing.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# project inventory and packaging
# --------------------------------------------------------------------------- #
def iter_project_files(project_dir: str, limits: dict, exclude_paths: list[str] | None = None) -> dict:
    excluded_names = set(limits.get("excluded_dir_names", []))
    max_files = int(limits.get("max_files_scanned", 5000))
    exclude = {os.path.normpath(path) for path in (exclude_paths or [])}
    files, skipped, dirs_seen = [], [], 0
    for root, dir_names, file_names in os.walk(project_dir):
        dir_names[:] = sorted(
            name
            for name in dir_names
            if name not in excluded_names and os.path.normpath(os.path.join(root, name)) not in exclude
        )
        dirs_seen += 1
        if dirs_seen > max_files:
            skipped.append({"path": root, "reason": "max_files_scanned reached"})
            break
        for name in sorted(file_names):
            full = os.path.normpath(os.path.join(root, name))
            relative = os.path.relpath(full, project_dir).replace(os.sep, "/")
            if full in exclude:
                skipped.append({"path": relative, "reason": "excluded by request"})
                continue
            files.append({"path": full, "relative": relative})
            if len(files) > max_files:
                raise ValueError(f"project tree exceeds max_files_scanned ({max_files}); narrow the project directory or raise the limit")
    return {"files": files, "skipped": skipped}


def compute_manifest(project_dir: str, limits: dict, version: str, exclude_paths: list[str] | None = None, exclusions: list[str] | None = None) -> dict:
    inventory = iter_project_files(project_dir, limits, exclude_paths=exclude_paths)
    max_file_bytes = int(limits.get("max_file_bytes_hashed", 67108864))
    max_total_bytes = int(limits.get("max_total_bytes_hashed", 536870912))
    patterns = [re.compile(pattern) for pattern in limits.get("sensitive_file_patterns", [])]

    entries, skipped, sensitive = [], list(inventory["skipped"]), []
    total_bytes = 0
    for item in inventory["files"]:
        relative = item["relative"]
        for pattern in patterns:
            if pattern.search(relative):
                sensitive.append({"path": relative, "pattern": pattern.pattern})
                break
        try:
            digest, size = sha256_file(item["path"], max_bytes=max_file_bytes)
        except ValueError:
            skipped.append({"path": relative, "reason": f"larger than max_file_bytes_hashed ({max_file_bytes})"})
            continue
        except OSError as error:
            skipped.append({"path": relative, "reason": f"unreadable: {error.strerror or error}"})
            continue
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise ValueError(f"project bytes exceed max_total_bytes_hashed ({max_total_bytes})")
        entries.append({"path": relative, "bytes": size, "sha256": digest})

    return {
        "schema_version": "1.0",
        "artifact": "release-manifest",
        "version": version,
        "generated_utc": now_utc(),
        "root": ".",
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
        "skipped": skipped,
        "sensitive_files": sensitive,
        "excluded_dirs": limits.get("excluded_dir_names", []),
    }


def tree_digest(project_dir: str, limits: dict, exclude_paths: list[str] | None = None) -> dict:
    """Digest of the current project tree, used to prove a verification run is current."""
    inventory = iter_project_files(project_dir, limits, exclude_paths=exclude_paths)
    max_file_bytes = int(limits.get("max_file_bytes_hashed", 67108864))
    lines, unhashed = [], []
    for item in inventory["files"]:
        try:
            digest, size = sha256_file(item["path"], max_bytes=max_file_bytes)
        except (ValueError, OSError):
            unhashed.append(item["relative"])
            lines.append(f"{item['relative']}:unhashed")
            continue
        lines.append(f"{item['relative']}:{digest}:{size}")
    lines.sort()
    return {
        "digest": sha256_bytes("\n".join(lines).encode("utf-8")),
        "file_count": len(inventory["files"]),
        "unhashed": unhashed,
    }


def parse_semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match((value or "").strip())
    if not match:
        raise ValueError(f"'{value}' is not a valid semantic version (expected MAJOR.MINOR.PATCH)")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def parse_commits(text: str) -> list[dict]:
    commits = []
    for raw in (text or "").split("\n"):
        line = raw.strip().lstrip("-*").strip()
        if not line:
            continue
        match = CONVENTIONAL_RE.match(line)
        if match:
            commits.append(
                {
                    "raw": line,
                    "type": match.group("type").lower(),
                    "scope": match.group("scope") or "",
                    "breaking": bool(match.group("breaking")) or "BREAKING CHANGE" in line,
                    "subject": match.group("subject").strip(),
                }
            )
        else:
            commits.append(
                {
                    "raw": line,
                    "type": "other",
                    "scope": "",
                    "breaking": line.upper().startswith("BREAKING"),
                    "subject": line,
                }
            )
    return commits


def next_version(current: str, commits: list[dict], bump: str = "auto") -> dict:
    major, minor, patch = parse_semver(current)
    if bump not in {"auto", "major", "minor", "patch", "none"}:
        raise ValueError("bump must be one of: auto, major, minor, patch, none")
    breaking = any(commit["breaking"] for commit in commits)
    features = any(commit["type"] in {"feat", "feature"} for commit in commits)
    fixes = any(commit["type"] in {"fix", "perf", "refactor", "security"} for commit in commits)
    if bump == "auto":
        if breaking:
            selected = "major"
        elif features:
            selected = "minor"
        elif fixes or commits:
            selected = "patch"
        else:
            selected = "none"
    else:
        selected = bump
    if selected == "major":
        major, minor, patch = major + 1, 0, 0
    elif selected == "minor":
        minor, patch = minor + 1, 0
    elif selected == "patch":
        patch += 1
    return {
        "previous": current,
        "version": f"{major}.{minor}.{patch}",
        "bump": selected,
        "signals": {"breaking": breaking, "features": features, "fixes": fixes, "commits": len(commits)},
    }


def build_release(manifest: dict, version_info: dict, commits: list[dict], project_name: str, preset_key: str | None, notes: str = "") -> dict:
    groups: dict[str, list[str]] = {}
    for commit in commits:
        groups.setdefault(commit["type"], []).append((f"{commit['scope']}: " if commit["scope"] else "") + commit["subject"])

    manifest_names = {
        "package.json", "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "pubspec.yaml", "pom.xml",
        "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "cargo.toml", "go.mod",
        "composer.json", "gemfile", "packages.config", "directory.packages.props", "packages.lock.json",
    }
    dependency_manifests = [
        entry["path"]
        for entry in manifest["files"]
        if os.path.basename(entry["path"]).lower() in manifest_names
        or re.match(r"^requirements[^/]*\.txt$", os.path.basename(entry["path"]).lower())
        or os.path.basename(entry["path"]).lower().endswith((".csproj", ".fsproj", ".sln"))
    ]
    payload = {
        "schema_version": "1.0",
        "artifact": "release",
        "project": project_name,
        "preset": preset_key,
        "generated_utc": now_utc(),
        "version": version_info,
        "changelog": groups,
        "sbom": {
            "format": "inventory-lite",
            "note": "File inventory with SHA-256 for the delivered tree plus detected dependency manifests. Not a full transitive SBOM; use the ecosystem tool for that.",
            "components": [{"name": entry["path"], "version": version_info["version"], "hashes": [{"alg": "SHA-256", "content": entry["sha256"]}], "bytes": entry["bytes"]} for entry in manifest["files"]],
            "dependency_manifests": dependency_manifests,
        },
        "checksums": [{"sha256": entry["sha256"], "path": entry["path"]} for entry in manifest["files"]],
        "limits_hit": manifest["skipped"],
        "notes": notes,
    }
    return payload


def render_release_md(release: dict, version_info: dict, manifest: dict) -> str:
    lines = [
        f"# Release notes: {release['project'] or 'project'} {version_info['version']}",
        "",
        f"- Generated: {release['generated_utc']}",
        f"- Previous version: {version_info['previous']}",
        f"- Bump: {version_info['bump']} (breaking={version_info['signals']['breaking']}, features={version_info['signals']['features']}, fixes={version_info['signals']['fixes']}, commits={version_info['signals']['commits']})",
        f"- Files: {manifest['file_count']} ({manifest['total_bytes']} bytes)",
        "",
        "## Changes",
        "",
    ]
    if release["changelog"]:
        for kind, items in sorted(release["changelog"].items()):
            lines.append(f"### {kind}")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
    else:
        lines += ["_No change entries were supplied; record the change set before publishing._", ""]

    lines += ["## Component inventory (inventory-lite SBOM)", "", f"- Dependency manifests detected: {', '.join(release['sbom']['dependency_manifests']) or 'none'}", ""]
    lines += ["| File | Bytes | SHA-256 |", "| --- | --- | --- |"]
    for entry in manifest["files"][:400]:
        lines.append(f"| `{entry['path']}` | {entry['bytes']} | `{entry['sha256'][:16]}...` |")
    if len(manifest["files"]) > 400:
        lines.append(f"| _({len(manifest['files']) - 400} more entries in release-manifest.json)_ | | |")

    lines += ["", "## Rollback and recovery", "",
              "- Keep the previous release artifact and its checksums; redeploy it if the new version fails smoke checks.",
              "- Reverse any migration with its documented down-migration before restoring the previous artifact.",
              "- Record who approved the release, the verification run id and the rollback decision point.",
              ""]
    if release["limits_hit"]:
        lines += ["## Files skipped during hashing", ""]
        for item in release["limits_hit"]:
            lines.append(f"- `{item['path']}`: {item['reason']}")
        lines += [""]
    if release.get("notes"):
        lines += ["## Notes", "", release["notes"], ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# gates and readiness
# --------------------------------------------------------------------------- #
def evaluate_stage_gate(stage_id: str, summary: dict, workflow: dict) -> dict:
    """Evaluate the gate criteria of one stage from that stage's summary facts."""
    stage = next((item for item in workflow.get("stages", []) if item["id"] == stage_id), None)
    if stage is None:
        raise ValueError(f"unknown stage '{stage_id}'")
    facts = summary.get("facts", {}) if summary else {}
    criteria = []
    for criterion in stage.get("gate", {}).get("criteria", []):
        key = criterion["id"]
        value = facts.get(key)
        passed = bool(value) if isinstance(value, bool) else bool(value)
        detail = (summary.get("details", {}) if summary else {}).get(key, "")
        criteria.append(
            {
                "id": key,
                "label": criterion["label"],
                "severity": criterion["severity"],
                "pass": passed,
                "detail": detail,
            }
        )
    return {"stage": stage_id, "criteria": criteria, **_score_criteria(criteria, workflow)}


def _score_criteria(criteria: list[dict], workflow: dict) -> dict:
    weights = workflow.get("readiness", {}).get("severity_weight", {"blocker": 5, "major": 3, "minor": 1})
    total = sum(weights.get(item["severity"], 1) for item in criteria) or 1
    earned = sum(weights.get(item["severity"], 1) for item in criteria if item["pass"])
    blockers = [item["id"] for item in criteria if not item["pass"] and item["severity"] == "blocker"]
    return {
        "score": round(100.0 * earned / total, 1),
        "pass": not blockers,
        "blockers": blockers,
        "failed": [item["id"] for item in criteria if not item["pass"]],
    }


def evaluate_readiness(
    state: dict,
    workflow: dict,
    project_dir: str,
    artifact_root: str,
    default_limits: dict,
    require_verification: bool | None = None,
    exclude_paths: list[str] | None = None,
) -> dict:
    """Aggregate stage gates, artifact stability and verification into a readiness report."""
    readiness_cfg = workflow.get("readiness", {})
    pass_threshold = float(readiness_cfg.get("pass_threshold", 80))
    strict_threshold = float(readiness_cfg.get("strict_threshold", 95))
    verification_share = float(readiness_cfg.get("verification_share", 0.15))
    if require_verification is None:
        require_verification = bool(readiness_cfg.get("require_verification", True))

    stage_ids = [stage["id"] for stage in workflow.get("stages", []) if stage["id"] != "readiness"]
    stage_weights = {stage["id"]: float(stage.get("gate", {}).get("weight", 10)) for stage in workflow.get("stages", [])}

    stages_report = []
    blockers = []
    advisories = []
    weighted_total = 0.0
    weight_sum = 0.0
    for stage_id in stage_ids:
        record = state.get("stages", {}).get(stage_id)
        weight = stage_weights.get(stage_id, 10)
        weight_sum += weight
        if not record:
            stages_report.append({"stage": stage_id, "status": "missing", "score": 0.0, "pass": False, "blockers": ["stage_not_completed"]})
            blockers.append({"source": stage_id, "id": "stage_not_completed", "detail": f"stage '{stage_id}' has not been run"})
            continue
        gate = record.get("gate", {})
        stages_report.append(
            {
                "stage": stage_id,
                "status": record.get("status", "unknown"),
                "score": float(gate.get("score", 0.0)),
                "pass": bool(gate.get("pass", False)),
                "blockers": gate.get("blockers", []),
                "failed": gate.get("failed", []),
                "completed_utc": record.get("completed_utc", ""),
            }
        )
        weighted_total += weight * float(gate.get("score", 0.0))
        for item in gate.get("blockers", []):
            blockers.append({"source": stage_id, "id": item, "detail": "blocker criterion failed"})
        for item in gate.get("failed", []):
            if item not in gate.get("blockers", []):
                advisories.append({"source": stage_id, "id": item, "detail": "non-blocking criterion failed"})

    stage_average = weighted_total / weight_sum if weight_sum else 0.0

    # artifact stability: recorded checksums must still match what is on disk
    drift = []
    missing_artifacts = []
    for stage_id, record in state.get("stages", {}).items():
        for artifact in record.get("artifacts", []):
            path = os.path.join(state.get("project_dir_abs") or project_dir, artifact["path"])
            if not os.path.isfile(path):
                missing_artifacts.append({"stage": stage_id, "path": artifact["path"]})
                continue
            digest, _ = sha256_file(path)
            if digest != artifact.get("sha256"):
                drift.append({"stage": stage_id, "path": artifact["path"]})
    if drift or missing_artifacts:
        blockers.append({"source": "artifacts", "id": "artifacts_stable", "detail": f"{len(drift)} modified, {len(missing_artifacts)} missing"})

    verification = state.get("verification", {})
    verification_score = 0.0
    current_digest = tree_digest(project_dir, default_limits, exclude_paths)["digest"]
    recorded_digest = verification.get("tree_digest", "")
    verification_stale = bool(verification) and recorded_digest != current_digest
    if verification and not verification_stale:
        verification_score = float(verification.get("score", 0.0))
        if not verification.get("pass", False):
            blockers.append({"source": "verification", "id": "verification_current", "detail": verification.get("detail", "verification run did not pass")})
    elif verification_stale:
        blockers.append(
            {
                "source": "verification",
                "id": "verification_stale",
                "detail": f"project tree changed after the verification run (recorded {recorded_digest[:12]}..., now {current_digest[:12]}...)",
            }
        )
    elif require_verification:
        blockers.append({"source": "verification", "id": "verification_current", "detail": "no verification run recorded"})

    if verification:
        overall = (1 - verification_share) * stage_average + verification_share * verification_score
    else:
        overall = (1 - verification_share) * stage_average
    overall = round(overall, 1)

    payload = {
        "schema_version": "1.0",
        "artifact": "readiness-report",
        "project": state.get("name", ""),
        "preset": state.get("preset"),
        "generated_utc": now_utc(),
        "version": state.get("target_version", ""),
        "stages": stages_report,
        "stage_average": round(stage_average, 1),
        "verification_score": round(verification_score, 1),
        "verification_present": bool(verification),
        "verification_stale": verification_stale,
        "verification_tree_digest": current_digest,
        "require_verification": bool(require_verification),
        "drift": drift,
        "missing_artifacts": missing_artifacts,
        "score": overall,
        "pass_threshold": pass_threshold,
        "strict_threshold": strict_threshold,
        "release_ready": overall >= pass_threshold and not blockers,
        "strictly_ready": overall >= strict_threshold and not blockers,
        "blockers": blockers,
        "advisories": advisories,
        "recommendations": _recommendations(stages_report, blockers, drift, missing_artifacts, verification, verification_stale),
    }
    return payload


def _recommendations(stages_report, blockers, drift, missing_artifacts, verification, verification_stale: bool = False) -> list[str]:
    tips = []
    for stage in stages_report:
        if stage["status"] == "missing":
            tips.append(f"Run `stage {stage['stage']}` — it has not been executed for this project.")
        elif not stage["pass"]:
            tips.append(f"Clear blocker criteria in stage `{stage['stage']}`: {', '.join(stage['blockers']) or 'see stage report'}.")
    if drift:
        tips.append("Re-run the affected stages: recorded artifact checksums no longer match the files on disk.")
    if missing_artifacts:
        tips.append("Restore or regenerate missing artifacts, then re-run the stages that produced them.")
    if verification_stale:
        tips.append("Re-run `verify` — the project tree changed after the recorded verification run, so those results no longer describe the current tree.")
    if not verification:
        tips.append("Run `verify` with an approval token so readiness reflects an executed check rather than a plan.")
    elif not verification.get("pass", False):
        tips.append("Fix the failing verification commands or mark them not-required in the preset, then re-run `verify`.")
    if not tips:
        tips.append("All gates pass; archive the readiness report with the release artifacts.")
    return tips


def render_readiness_md(payload: dict) -> str:
    lines = [
        f"# Release readiness: {payload['project'] or 'project'}",
        "",
        f"- Generated: {payload['generated_utc']}",
        f"- Preset: `{payload.get('preset') or 'none'}`",
        f"- Score: **{payload['score']}/100** (pass >= {payload['pass_threshold']}, strict >= {payload['strict_threshold']})",
        f"- Release ready: {'YES' if payload['release_ready'] else 'NO'}",
        f"- Strictly ready: {'YES' if payload['strictly_ready'] else 'NO'}",
        f"- Stage average: {payload['stage_average']}  |  Verification: {payload['verification_score']} (present={payload['verification_present']})",
        "",
        "## Stage gates",
        "",
        "| Stage | Status | Score | Pass | Open blockers |",
        "| --- | --- | --- | --- | --- |",
    ]
    for stage in payload["stages"]:
        lines.append(f"| {stage['stage']} | {stage['status']} | {stage['score']} | {'yes' if stage['pass'] else 'no'} | {', '.join(stage['blockers']) or '-'} |")

    lines += ["", "## Blockers", ""]
    if payload["blockers"]:
        for blocker in payload["blockers"]:
            lines.append(f"- **{blocker['source']} / {blocker['id']}**: {blocker['detail']}")
    else:
        lines.append("- None.")

    if payload["advisories"]:
        lines += ["", "## Advisories", ""]
        for advisory in payload["advisories"]:
            lines.append(f"- {advisory['source']} / {advisory['id']}: {advisory['detail']}")

    lines += ["", "## Recommendations", ""]
    for item in payload["recommendations"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("A readiness score is a statement about the recorded checks, not a guarantee that the application works. Only executed verification counts.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# capability boundary
# --------------------------------------------------------------------------- #
def boundary_check(presets: dict, preset_key: str | None, workflow: dict, project_dir: str | None = None) -> dict:
    import shutil

    presets_data = presets.get("presets", {})
    if preset_key and preset_key not in presets_data:
        raise ValueError(f"unknown preset '{preset_key}'; available: {', '.join(sorted(presets_data))}")
    selected = {preset_key: presets_data[preset_key]} if preset_key else presets_data

    toolchain = []
    for key, preset in selected.items():
        for name in preset.get("required_executables", []):
            resolved = shutil.which(name)
            toolchain.append({"preset": key, "executable": name, "required": True, "available": bool(resolved), "resolved": resolved or ""})
        for name in preset.get("optional_executables", []):
            resolved = shutil.which(name)
            toolchain.append({"preset": key, "executable": name, "required": False, "available": bool(resolved), "resolved": resolved or ""})

    blocked = [item for item in toolchain if item["required"] and not item["available"]]
    missing_artifact_tools = []
    if preset_key and project_dir and os.path.isdir(project_dir):
        preset = presets_data[preset_key]
        for check in preset.get("artifact_checks", []):
            import glob

            matches = glob.glob(os.path.join(project_dir, check["glob"]), recursive=True)
            if not matches:
                missing_artifact_tools.append({"id": check["id"], "label": check["label"], "glob": check["glob"], "severity": check["severity"]})

    within_limits = True
    note = ""
    if project_dir and os.path.isdir(project_dir):
        limits = workflow.get("limits", {})
        try:
            inventory = iter_project_files(project_dir, limits, exclude_paths=[os.path.join(project_dir, "factory")])
            if len(inventory["files"]) > int(limits.get("max_files_scanned", 5000)):
                within_limits = False
                note = "project tree exceeds max_files_scanned"
        except ValueError as error:
            within_limits = False
            note = str(error)

    escalations = [
        f"Provide '{item['executable']}' in PATH (or run verification in an environment that has it) before claiming build or test results for preset '{item['preset']}'."
        for item in blocked
    ]
    if blocked:
        escalations.append("Until then, keep the verification gate reported as blocked; do not mark the release ready.")

    return {
        "schema_version": "1.0",
        "artifact": "boundary-report",
        "generated_utc": now_utc(),
        "preset": preset_key,
        "toolchain": toolchain,
        "blocked_required": blocked,
        "missing_artifact_checks": missing_artifact_tools,
        "within_limits": within_limits,
        "limit_note": note,
        "escalations": escalations,
        "can_verify_here": not blocked,
    }


def render_boundary_md(payload: dict) -> str:
    lines = [
        "# Capability boundary report",
        "",
        f"- Generated: {payload['generated_utc']}",
        f"- Preset: `{payload.get('preset') or 'all presets'}`",
        f"- Required toolchain complete here: {'YES' if payload['can_verify_here'] else 'NO'}",
        "",
        "## Toolchain",
        "",
        "| Preset | Executable | Required | Available | Resolved path |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["toolchain"]:
        lines.append(f"| {item['preset']} | {item['executable']} | {'yes' if item['required'] else 'no'} | {'yes' if item['available'] else 'no'} | {item['resolved'] or '-'} |")
    if not payload["toolchain"]:
        lines.append("| _none declared_ | | | | |")

    lines += ["", "## Missing project artifacts", ""]
    if payload["missing_artifact_checks"]:
        for item in payload["missing_artifact_checks"]:
            lines.append(f"- ({item['severity']}) {item['label']} — expected `{item['glob']}`")
    else:
        lines.append("- None detected.")

    lines += ["", "## Escalations", ""]
    for item in payload["escalations"] or ["No escalation needed for the declared toolchain."]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
