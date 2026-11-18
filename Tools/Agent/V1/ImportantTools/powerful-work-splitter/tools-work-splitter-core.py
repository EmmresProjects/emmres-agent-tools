#!/usr/bin/env python3
"""powerful-work-splitter :: shared domain and planning module.

This module holds every reusable rule of the Skill Tool: input normalization,
requirement and file extraction, feasibility scoring, stage-count estimation,
stage construction, rebalancing, dependency decomposition, critical-path
scheduling, gate definitions, plan validation, ledger export and text
rendering. It has no CLI of its own; tools-work-splitter.py is the entry point
and tools-skill-validator.py reuses this module for its verification work.

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import math
import re
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_NAME = "powerful-work-splitter"
MODULE_VERSION = "1.0.0"
PLAN_SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3
EXIT_GATE = 4
EXIT_APPROVAL = 5
EXIT_LIMIT = 6
EXIT_PARTIAL = 7
EXIT_INTERRUPTED = 130

SKILL_ROOT = Path(__file__).resolve().parent

CAPABILITIES: List[Dict[str, str]] = [
    {
        "id": "powerful-work-splitter",
        "name": "Powerful Work Splitter",
        "summary": "Decides single-response versus staged work, recommends and rebalances a stage count, and returns an approved stage plan plus continuation state.",
        "commands": "plan, assess, estimate, negotiate",
    },
    {
        "id": "single-prompt-feasibility-assessor",
        "name": "Single-Prompt Feasibility Assessor",
        "summary": "Compares scope, files, tool limits and verification burden against a one-response budget and returns a decision with a fallback staged plan.",
        "commands": "assess",
    },
    {
        "id": "adaptive-prompt-count-estimator",
        "name": "Adaptive Prompt Count Estimator",
        "summary": "Converts work packages, review gates and file groups into recommended, minimum and maximum stage counts and recalculates on scope change.",
        "commands": "estimate",
    },
    {
        "id": "user-controlled-stage-negotiator",
        "name": "User-Controlled Stage Negotiator",
        "summary": "Rebalances the plan to any user-chosen stage count without dropping requirements and discloses the trade-offs.",
        "commands": "negotiate, checkpoint set-count",
    },
    {
        "id": "checkpoint-and-continue-controller",
        "name": "Checkpoint and Continue Controller",
        "summary": "Pauses at safe boundaries, classifies Continue or equivalent intent, rejects ambiguous replies and resumes with the next real work package.",
        "commands": "checkpoint start, checkpoint status, checkpoint continue, checkpoint reopen, checkpoint block",
    },
    {
        "id": "dynamic-stage-expansion-manager",
        "name": "Dynamic Stage Expansion Manager",
        "summary": "Extends an active plan, preserves completed stages, renumbers the remainder and adds validation gates.",
        "commands": "negotiate --direction expand, checkpoint set-count --direction expand",
    },
    {
        "id": "dynamic-stage-compression-manager",
        "name": "Dynamic Stage Compression Manager",
        "summary": "Merges compatible work packages while protecting safety, accuracy and final-assembly checks and disclosing quality trade-offs.",
        "commands": "negotiate --direction compress, checkpoint set-count --direction compress",
    },
    {
        "id": "dependency-aware-task-decomposer",
        "name": "Dependency-Aware Task Decomposer",
        "summary": "Breaks the goal into atomic ordered work packages with prerequisites, waves and merge contracts.",
        "commands": "decompose",
    },
    {
        "id": "parallel-workstream-coordinator",
        "name": "Parallel Workstream Coordinator",
        "summary": "Finds independent branches, defines merge contracts and verifies parallel outputs share names, schemas and versions.",
        "commands": "decompose --parallel, negotiate",
    },
    {
        "id": "critical-path-scheduler",
        "name": "Critical Path Scheduler",
        "summary": "Ranks work that controls total completion time and returns the critical path, buffers and escalation triggers.",
        "commands": "scheduler",
    },
    {
        "id": "milestone-gatekeeper",
        "name": "Milestone Gatekeeper",
        "summary": "Applies entry and exit criteria before a stage may start or close and returns pass, repair or blocked.",
        "commands": "gate",
    },
    {
        "id": "final-assembly-orchestrator",
        "name": "Final Assembly Orchestrator",
        "summary": "Checks assembly, naming, references and reopenability of the combined output and returns a polished delivery decision.",
        "commands": "verify, ledger, checkpoint continue (final stage)",
    },
]

CAPABILITY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "powerful-work-splitter": ("stage", "staged", "split", "plan", "prompt count", "how many"),
    "single-prompt-feasibility-assessor": ("one response", "single response", "one prompt", "feasible", "fit"),
    "adaptive-prompt-count-estimator": ("estimate", "how many stages", "recommend", "count"),
    "user-controlled-stage-negotiator": ("fewer stages", "more stages", "choose", "override", "rebalance"),
    "checkpoint-and-continue-controller": ("continue", "checkpoint", "pause", "resume", "proceed"),
    "dynamic-stage-expansion-manager": ("expand", "add a stage", "more stages"),
    "dynamic-stage-compression-manager": ("compress", "merge stages", "fewer stages"),
    "dependency-aware-task-decomposer": ("depend", "order", "sequence", "prerequisite", "break down"),
    "parallel-workstream-coordinator": ("parallel", "in parallel", "workstream", "independent"),
    "critical-path-scheduler": ("critical path", "deadline", "schedule", "priority", "blocker"),
    "milestone-gatekeeper": ("gate", "milestone", "sign-off", "entry criteria", "exit criteria"),
    "final-assembly-orchestrator": ("assemble", "final", "package", "deliver", "polish", "integrate"),
}

KIND_WEIGHTS: Dict[str, float] = {"deliverable": 1.0, "quality": 0.9, "process": 0.6}

DISCOVERY_CUES = (
    "restate", "goal", "scope", "requirement", "inventory", "confirm", "clarify",
    "plan", "assumption", "constraint", "audience", "brief",
)
DELIVERY_CUES = (
    "deliver", "package", "final", "reopen", "assemble", "remove temporary",
    "clean", "handoff", "delivery", "zip", "export",
)
QUALITY_CUES = (
    "verify", "check", "validate", "reconcile", "test", "audit", "ensure",
    "review", "accessib", "consistency", "no more than", "must not", "do not",
)
PROCESS_CUES = (
    "inventory", "log", "record", "confirm", "track", "document", "register",
    "restate", "clarify",
)

TEMP_PATTERNS = (
    "*.tmp", "*.temp", "*.log", "*.bak", "*.orig", "*.rej", "*.swp", "*~",
    ".DS_Store", "Thumbs.db", "*.pyc",
)
TEMP_DIR_NAMES = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ipynb_checkpoints")

FILE_EXTENSIONS = (
    "pdf", "docx", "doc", "xlsx", "xls", "csv", "tsv", "pptx", "ppt", "txt", "md",
    "json", "jsonl", "yaml", "yml", "xml", "html", "htm", "css", "scss", "js", "mjs",
    "ts", "tsx", "jsx", "py", "rb", "go", "rs", "java", "kt", "cs", "php", "c", "h",
    "cpp", "hpp", "sh", "bash", "zsh", "ps1", "bat", "zip", "tar", "gz", "bz2", "7z",
    "rar", "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "tif", "tiff", "ico",
    "mp4", "mov", "avi", "mkv", "webm", "mp3", "wav", "flac", "aac", "m4a", "ogg",
    "opus", "fig", "sketch", "xd", "psd", "ai", "indd", "ipynb", "sql", "db",
    "sqlite", "parquet", "rtf", "odt", "ods", "odp", "epub", "ttf", "otf", "woff",
    "woff2", "srt", "vtt", "sav", "dta",
)

FILE_RE = re.compile(
    r"(?<![\w/.-])([A-Za-z0-9][A-Za-z0-9._%+\-]*\.(?:" + "|".join(FILE_EXTENSIONS) + r"))\b",
    re.IGNORECASE,
)

LIST_MARK_RE = re.compile(r"^(?:\(?\d{1,3}[.)]|[-*+\u2022])\s*")
HEADING_MARK_RE = re.compile(r"^#{1,6}\s*")
NAME_ITEM_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9'&/().\u2011\-]*(?:\s+[A-Za-z0-9'&/().\u2011\-]+){1,8})"
    r"\s*(?::|\u2014|\u2013|\s-\s)\s*(?P<desc>\S.*)$"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c])")
SLUG_RE = re.compile(r"[^a-z0-9]+")

SECTION_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "files": ("file", "files", "asset", "assets", "attachment", "attachments", "upload", "uploads", "source"),
    "deliverables": ("deliverable", "deliverables", "output", "outputs", "produce", "result", "results"),
    "constraints": ("constraint", "constraints", "requirement", "requirements", "must", "rules", "limits"),
    "goal": ("goal", "objective", "request", "task", "brief", "mission"),
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Structured, user-facing failure with a stable code and exit status."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        exit_code: int = EXIT_USAGE,
        hint: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.hint = hint
        self.details = details or {}

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "exit_code": self.exit_code,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str, *, fallback: str = "untitled", max_length: int = 60) -> str:
    value = SLUG_RE.sub("-", text.lower()).strip("-")
    value = re.sub(r"-{2,}", "-", value)[:max_length].strip("-")
    return value or fallback


def read_text(path: Path, *, limit: int = 400000) -> str:
    if not path.exists():
        raise ToolError(
            f"Input file not found: {path}",
            code="input-missing",
            exit_code=EXIT_MISSING,
            hint="Pass an existing path with --request, or use --request-text.",
        )
    if path.is_dir():
        raise ToolError(
            f"Expected a file but got a directory: {path}",
            code="input-not-file",
            exit_code=EXIT_USAGE,
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ToolError(
            f"Input file is not readable: {path}",
            code="input-unreadable",
            exit_code=EXIT_MISSING,
            details={"error": str(exc)},
        ) from exc
    if len(raw) > limit:
        raise ToolError(
            f"Input text exceeds the configured limit of {limit} bytes",
            code="input-too-large",
            exit_code=EXIT_LIMIT,
            details={"bytes": len(raw), "limit": limit},
        )
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ToolError(
        f"Could not decode input as text: {path}",
        code="input-encoding",
        exit_code=EXIT_USAGE,
        hint="Re-save the file as UTF-8.",
    )


def atomic_write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def atomic_write_json(path: Path, payload: Any) -> Path:
    return atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def load_json_file(path: Path, *, required: bool = True, label: str = "JSON file") -> Dict[str, Any]:
    if not path.exists():
        if required:
            raise ToolError(
                f"{label} not found: {path}",
                code="resource-missing",
                exit_code=EXIT_MISSING,
            )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(
            f"{label} is not readable: {path}",
            code="resource-unreadable",
            exit_code=EXIT_MISSING,
            details={"error": str(exc)},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"{label} is not valid JSON: {path}",
            code="resource-invalid-json",
            exit_code=EXIT_USAGE,
            details={"line": exc.lineno, "column": exc.colno, "error": exc.msg},
        ) from exc
    if not isinstance(data, dict):
        raise ToolError(
            f"{label} must contain a JSON object: {path}",
            code="resource-invalid-shape",
            exit_code=EXIT_USAGE,
        )
    return data


def resource_path(name: str, *, must_exist: bool = True) -> Path:
    """Resolve a packaged resource using a case-sensitive skill-relative name."""
    if not name or not isinstance(name, str):
        raise ToolError("Resource name is required", code="resource-name-missing")
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ToolError(
            f"Packaged resources must use a relative name without '..': {name}",
            code="resource-path-invalid",
            hint="Pass a name such as asset-default-policy.json or an explicit --policy path.",
        )
    resolved = SKILL_ROOT / candidate
    if must_exist and not resolved.is_file():
        raise ToolError(
            f"Packaged resource not found: {name}",
            code="resource-missing",
            exit_code=EXIT_MISSING,
            hint=f"Expected the file beside the tool at {SKILL_ROOT.name}/{name}.",
        )
    return resolved


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    base = load_json_file(resource_path("asset-default-policy.json"), label="Default policy")
    if not path:
        return base
    override_path = Path(path)
    override = load_json_file(override_path, label="Policy override")
    merged = deep_merge(base, override)
    _validate_policy(merged, source=str(override_path))
    return merged


def _validate_policy(policy: Dict[str, Any], *, source: str) -> None:
    bounds = policy.get("stage_bounds") or {}
    low, high = bounds.get("min"), bounds.get("max")
    if not isinstance(low, int) or not isinstance(high, int) or low < 1 or high < low:
        raise ToolError(
            "Policy stage_bounds must satisfy 1 <= min <= max",
            code="policy-invalid",
            exit_code=EXIT_USAGE,
            details={"source": source, "stage_bounds": bounds},
        )
    if high > 14:
        raise ToolError(
            "Policy stage_bounds.max must not exceed 14",
            code="policy-invalid",
            exit_code=EXIT_USAGE,
            details={"source": source},
        )
    capacity = (policy.get("budget") or {}).get("stage_capacity_units")
    overhead = (policy.get("unit_defaults") or {}).get("stage_overhead_units")
    if not isinstance(capacity, (int, float)) or not isinstance(overhead, (int, float)) or capacity <= overhead:
        raise ToolError(
            "Policy budget.stage_capacity_units must be greater than unit_defaults.stage_overhead_units",
            code="policy-invalid",
            exit_code=EXIT_USAGE,
            details={"source": source},
        )


def load_intents(path: Optional[str] = None) -> Dict[str, Any]:
    config = load_json_file(resource_path("asset-continue-intents.json"), label="Continue lexicon")
    if path:
        config = deep_merge(config, load_json_file(Path(path), label="Continue lexicon override"))
    for key in ("continue_patterns", "hard_stop_patterns", "scope_change_patterns", "ambiguous_patterns"):
        patterns = config.get(key)
        if not isinstance(patterns, list) or not patterns:
            raise ToolError(
                f"Continue lexicon key '{key}' must be a non-empty list",
                code="config-invalid",
                exit_code=EXIT_USAGE,
            )
    return config


def load_gates(path: Optional[str] = None) -> Dict[str, Any]:
    config = load_json_file(resource_path("asset-gate-criteria.json"), label="Gate criteria")
    if path:
        config = deep_merge(config, load_json_file(Path(path), label="Gate criteria override"))
    gates = config.get("gates")
    if not isinstance(gates, dict) or not gates:
        raise ToolError("Gate criteria must define at least one gate", code="config-invalid")
    return config


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[?/!]?[A-Za-z][^<>]*>")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|`{1,3}|~~)")


def normalize_input(text: str) -> str:
    """Strip HTML, CSS-in-attribute noise, comments, markdown marks and extra spacing.

    Returns one logical item per line with inline whitespace collapsed. List
    marks and heading marks are preserved because later stages use them to
    distinguish catalog items from free-form requirements.
    """
    if text is None:
        return ""
    value = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = _COMMENT_RE.sub("\n", value)
    value = _SCRIPT_STYLE_RE.sub("\n", value)
    value = re.sub(r"<(br|/p|/div|/li|/tr|/h[1-6])\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _MD_LINK_RE.sub(lambda m: m.group(1) or m.group(2), value)
    value = _MD_EMPHASIS_RE.sub("", value)
    lines: List[str] = []
    for raw in value.split("\n"):
        line = HEADING_MARK_RE.sub("", raw)
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r" {2,}", " ", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def looks_like_title(line: str) -> bool:
    """True for catalog headings and title-case labels rather than real goals."""
    words = [w for w in re.split(r"\s+", line.strip()) if w]
    if len(words) < 3:
        return True
    body = [w for w in words if any(ch.isalpha() for ch in w)]
    if not body:
        return True
    capitalized = sum(1 for w in body if w[0].isupper())
    return capitalized >= max(3, int(len(body) * 0.7))


def _section_of(line: str) -> Optional[str]:
    probe = LIST_MARK_RE.sub("", line).strip().rstrip(":").lower()
    if len(probe) > 60:
        return None
    for section, keywords in SECTION_KEYWORDS.items():
        for keyword in keywords:
            if probe == keyword or probe.startswith(keyword + " ") or probe == keyword + "s":
                return section
    return None


def split_sections(normalized: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Split normalized text into section-labelled lines plus unlabelled lines."""
    sections: Dict[str, List[str]] = {key: [] for key in SECTION_KEYWORDS}
    unlabelled: List[str] = []
    current: Optional[str] = None
    for line in normalized.split("\n"):
        section = _section_of(line)
        if section and (line.rstrip().endswith(":") or len(LIST_MARK_RE.sub("", line)) <= 40):
            current = section
            continue
        if current:
            sections[current].append(line)
        else:
            unlabelled.append(line)
    return sections, unlabelled


def is_name_line(line: str) -> bool:
    """True when a line looks like '<Skill Name>: <description>' or '<Skill Name> - <desc>'."""
    clean = LIST_MARK_RE.sub("", line).strip()
    match = NAME_ITEM_RE.match(clean)
    if not match:
        return False
    name = match.group("name").strip()
    words = name.split()
    if len(words) < 2 or len(words) > 9 or len(name) > 80:
        return False
    if all(word.islower() for word in words[1:]):
        return False
    if clean.rstrip().endswith(":"):
        return False
    return True


def extract_catalog_items(normalized: str) -> List[Dict[str, str]]:
    """Extract Skill Tool Name / Description pairs from pasted Markdown or HTML text."""
    items: List[Dict[str, str]] = []
    seen = set()
    for line in normalized.split("\n"):
        if not is_name_line(line):
            continue
        clean = LIST_MARK_RE.sub("", line).strip()
        match = NAME_ITEM_RE.match(clean)
        if not match:
            continue
        name = re.sub(r"\s{2,}", " ", match.group("name")).strip(" .:-\u2013\u2014")
        description = match.group("desc").strip()
        key = name.lower()
        if key in seen or len(description) < 8:
            continue
        seen.add(key)
        items.append({"name": name, "description": description})
    return items


def extract_files(normalized: str, *, max_files: int = 500) -> List[str]:
    seen: List[str] = []
    lowered = set()
    for match in FILE_RE.finditer(normalized):
        name = match.group(1).strip(".-")
        key = name.lower()
        if key in lowered:
            continue
        lowered.add(key)
        seen.append(name)
        if len(seen) >= max_files:
            break
    return seen


def _classify_kind(text: str) -> str:
    low = text.lower()
    if any(cue in low for cue in QUALITY_CUES):
        return "quality"
    if any(cue in low for cue in PROCESS_CUES) and not any(cue in low for cue in ("produce", "create", "deliver", "build", "write", "draft", "design", "generate", "export")):
        return "process"
    return "deliverable"


def _acceptance_for(kind: str) -> str:
    return {
        "deliverable": "An artifact exists at a declared relative path and opens or parses cleanly",
        "quality": "A recorded check result shows the criterion passing",
        "process": "A ledger, log or inventory entry evidences the step",
    }.get(kind, "The requirement is evidenced by an inspectable record")


def extract_requirements(
    normalized: str,
    *,
    max_requirements: int = 200,
    explicit: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Build the requirement ledger from normalized text.

    Priority: explicit --requirement values, then catalog descriptions, then
    section-labelled list items, then cue-bearing sentences from free text.
    """
    candidates: List[Tuple[str, str]] = []  # (text, source)
    for value in explicit or []:
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        if cleaned:
            candidates.append((cleaned, "explicit"))

    sections, unlabelled = split_sections(normalized)

    for section in ("deliverables", "constraints", "goal"):
        for line in sections.get(section, []):
            text = LIST_MARK_RE.sub("", line).strip()
            if len(text) >= 8 and not is_name_line(line):
                candidates.append((text, section))

    if not candidates:
        catalog = extract_catalog_items(normalized)
        if catalog:
            for item in catalog:
                candidates.append((f"{item['name']}: {item['description']}", "catalog"))

    if not candidates:
        if not candidates:
            blocks = list(unlabelled)
            for section in ("files", "goal"):
                blocks.extend(sections.get(section, []))
            for line in blocks:
                if is_name_line(line):
                    continue
                text = LIST_MARK_RE.sub("", line).strip()
                if len(text) < 12:
                    continue
                for piece in SENTENCE_SPLIT_RE.split(text):
                    piece = piece.strip()
                    if len(piece) < 12 or piece.endswith("?"):
                        continue
                    candidates.append((piece, "text"))

    requirements: List[Dict[str, Any]] = []
    seen = set()
    for text, source in candidates:
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        kind = _classify_kind(text)
        requirements.append(
            {
                "id": f"R{len(requirements) + 1}",
                "text": text,
                "kind": kind,
                "owner_stage": None,
                "acceptance": _acceptance_for(kind),
                "status": "pending",
                "evidence": "",
                "source": source,
            }
        )
        if len(requirements) >= max_requirements:
            break
    return requirements


def classify_capabilities(text: str) -> List[str]:
    low = (text or "").lower()
    hits = [cap for cap, cues in CAPABILITY_KEYWORDS.items() if any(cue in low for cue in cues)]
    return hits


# ---------------------------------------------------------------------------
# Feasibility scoring and stage-count estimation
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_metrics(
    requirements: Sequence[Dict[str, Any]],
    files: Sequence[Dict[str, Any]],
    *,
    policy: Dict[str, Any],
    tool_calls: int = 0,
    dependency_depth: Optional[int] = None,
    uncertainty: Optional[float] = None,
    verification: Optional[float] = None,
) -> Dict[str, Any]:
    caps = policy["metric_caps"]
    weights = policy["metric_weights"]
    defaults = policy["unit_defaults"]
    if tool_calls < 0:
        raise ToolError("--tool-calls must be zero or greater", code="input-invalid")
    if uncertainty is None:
        uncertainty = float(defaults["uncertainty_default"])
    if verification is None:
        verification = float(defaults["verification_default"])
    if not 0.0 <= float(uncertainty) <= 1.0:
        raise ToolError("--uncertainty must be between 0.0 and 1.0", code="input-invalid")
    if not 0.0 <= float(verification) <= 1.0:
        raise ToolError("--verification must be between 0.0 and 1.0", code="input-invalid")

    scope_units = 0.0
    for requirement in requirements:
        scope_units += defaults["units_per_requirement"] * KIND_WEIGHTS.get(requirement.get("kind", "deliverable"), 1.0)
    scope_units = min(scope_units, float(caps["scope_items"]))
    file_units = min(len(files) * float(defaults["units_per_file"]), float(caps["files"]) * 0.5)
    depth = dependency_depth if dependency_depth is not None else (2 if files else 1)
    depth = int(_clamp(depth, 0, caps["dependency_depth"]))
    calls = min(int(tool_calls), caps["tool_calls"])

    units = (
        weights["scope"] * scope_units
        + weights["files"] * file_units * 2.0
        + weights["dependency_depth"] * depth
        + weights["tool_calls"] * calls
        + weights["uncertainty"] * float(uncertainty)
        + weights["verification"] * float(verification)
    )
    deliverable_count = sum(1 for r in requirements if r.get("kind") == "deliverable")
    budget = float(policy["budget"]["single_response_budget_units"])
    feasible = units <= budget and deliverable_count <= 4 and float(uncertainty) <= 0.30
    missing_files = sum(1 for f in files if not f.get("present", True))
    risk = "low" if units <= budget * 0.6 and not missing_files else ("high" if units > budget * 1.6 or missing_files else "medium")
    return {
        "units": round(units, 2),
        "single_response_budget_units": budget,
        "single_response_feasible": bool(feasible),
        "risk": risk,
        "inputs": {
            "requirements": len(requirements),
            "requirements_per_stage": int(policy["budget"].get("requirements_per_stage", 4)),
            "deliverables": deliverable_count,
            "files": len(files),
            "files_present": len(files) - missing_files,
            "files_missing": missing_files,
            "dependency_depth": depth,
            "tool_calls": calls,
            "uncertainty": round(float(uncertainty), 2),
            "verification": round(float(verification), 2),
            "scope_units": round(scope_units, 2),
        },
    }


def estimate_counts(units: float, metrics: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    bounds = policy["stage_bounds"]
    budget = policy["budget"]
    capacity = float(budget["stage_capacity_units"])
    overhead = float(policy["unit_defaults"]["stage_overhead_units"])
    usable = max(capacity - overhead, 0.5)
    unit_based = 1 if metrics["single_response_feasible"] else int(
        math.ceil(max(units - float(budget["single_response_budget_units"]), 0.0) / usable)
    ) + 1
    per_stage = max(int(budget.get("requirements_per_stage", 4)), 1)
    requirement_based = int(math.ceil(metrics["inputs"]["requirements"] / per_stage)) + 1
    if metrics["single_response_feasible"]:
        # A feasible request is done in one response; the catalog's first rule is
        # to proceed in one response rather than manufacture stages.
        recommended = int(_clamp(bounds["min"], bounds["min"], bounds["max"]))
        minimum = recommended
    else:
        needed = max(1, unit_based, requirement_based)
        recommended = int(_clamp(needed, bounds["min"], bounds["max"]))
        minimum = int(_clamp(max(1, math.ceil(units / capacity)), bounds["min"], recommended))
    maximum = int(_clamp(recommended + int(budget["recommended_headroom_stages"]), recommended, bounds["max"]))
    confidence = "high" if units > float(budget["single_response_budget_units"]) * 2 else ("medium" if units > 6 else "low")
    return {
        "units": round(units, 2),
        "recommended": recommended,
        "minimum": minimum,
        "maximum": maximum,
        "confidence": confidence,
        "reasons": _estimate_reasons(metrics, units, recommended),
    }


def policy_budget_per_stage(metrics: Dict[str, Any]) -> float:
    return float(metrics.get("inputs", {}).get("requirements_per_stage", 4))


def _estimate_reasons(metrics: Dict[str, Any], units: float, recommended: int) -> List[str]:
    reasons: List[str] = []
    inputs = metrics["inputs"]
    if metrics["single_response_feasible"]:
        reasons.append("Scope fits the one-response budget; a single stage is defensible")
    else:
        reasons.append(f"Work measures {units} units against a {metrics['single_response_budget_units']} unit one-response budget")
    if inputs["deliverables"] > 4:
        reasons.append(f"{inputs['deliverables']} deliverables need separate build and verification effort")
    if inputs["files_missing"]:
        reasons.append(f"{inputs['files_missing']} required file(s) are not present and must be recovered before building")
    if inputs["files"]:
        reasons.append(f"{inputs['files']} file(s) must be inventoried and validated")
    if inputs["dependency_depth"] >= 3:
        reasons.append("Deep dependency chains require ordered gates rather than parallel guessing")
    if inputs["tool_calls"] >= 10:
        reasons.append(f"{inputs['tool_calls']} planned tool calls consume response budget")
    if inputs["uncertainty"] >= 0.5:
        reasons.append("High uncertainty warrants a discovery checkpoint before building")
    if metrics["risk"] == "high":
        reasons.append("Risk is high, so verification cannot be collapsed into the last sentence of one response")
    per_stage = max(int(policy_budget_per_stage(metrics)), 1)
    requirement_based = int(math.ceil(inputs["requirements"] / per_stage)) + 1
    if not metrics["single_response_feasible"] and requirement_based >= recommended:
        reasons.append(
            f"{inputs['requirements']} requirement(s) at {per_stage} per stage need {requirement_based} stage(s) including assembly"
        )
    reasons.append(f"{recommended} stage(s) keep each checkpoint verifiable without inflating the count")
    return reasons


# ---------------------------------------------------------------------------
# Stage construction
# ---------------------------------------------------------------------------


def _build_stage_titles(policy: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
    blueprints = list(policy.get("stage_blueprints") or [])
    assembly = dict(policy.get("assembly_stage") or {})
    if not blueprints or not assembly:
        raise ToolError("Policy must define stage_blueprints and assembly_stage", code="policy-invalid")
    discovery = [b for b in blueprints if b.get("role") == "discovery"]
    build = [b for b in blueprints if b.get("role") == "build"]
    if not discovery or not build:
        raise ToolError("Policy stage_blueprints must include discovery and build blueprints", code="policy-invalid")

    if count == 1:
        single = policy.get("single_stage") or assembly
        return [{"title": single["title"], "objective": single["objective"], "role": "assembly"}]

    discovery_stage = {
        "title": discovery[0]["title"],
        "objective": discovery[0]["objective"],
        "role": "discovery",
    }
    if count == 2:
        return [discovery_stage, {"title": assembly["title"], "objective": assembly["objective"], "role": "assembly"}]

    middle_count = count - 2
    middle: List[Dict[str, Any]] = []
    for index in range(middle_count):
        blueprint = build[index % len(build)]
        title = blueprint["title"]
        if index >= len(build):
            title = f"{title} (continued {index // len(build) + 1})"
        middle.append({"title": title, "objective": blueprint["objective"], "role": "build"})
    return [discovery_stage] + middle + [{"title": assembly["title"], "objective": assembly["objective"], "role": "assembly"}]


def _is_discovery_requirement(text: str) -> bool:
    return any(cue in text.lower() for cue in DISCOVERY_CUES)


def _is_delivery_requirement(text: str) -> bool:
    return any(cue in text.lower() for cue in DELIVERY_CUES)


def allocate_requirements(
    requirements: Sequence[Dict[str, Any]],
    stages: Sequence[Dict[str, Any]],
    *,
    policy: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Assign every requirement to exactly one stage, preserving request order."""
    warnings: List[str] = []
    reqs = [dict(r) for r in requirements]
    if not reqs:
        return reqs, warnings
    if len(stages) == 1:
        for req in reqs:
            req["owner_stage"] = stages[0]["id"]
        return reqs, warnings

    first, last = stages[0], stages[-1]
    middle = list(stages[1:-1])

    discovery_idx: List[int] = []
    delivery_idx: Optional[int] = None
    remaining_idx: List[int] = []

    for index, req in enumerate(reqs):
        text = req["text"]
        if _is_discovery_requirement(text) and req.get("kind") != "deliverable" and not _is_delivery_requirement(text):
            discovery_idx.append(index)
        elif _is_delivery_requirement(text) and index >= len(reqs) - max(2, len(reqs) // 3):
            delivery_idx = index
        else:
            remaining_idx.append(index)

    if delivery_idx is not None:
        reqs[delivery_idx]["owner_stage"] = last["id"]
    if not middle:
        for index in remaining_idx:
            reqs[index]["owner_stage"] = first["id"]
        for index in discovery_idx:
            reqs[index]["owner_stage"] = first["id"]
        return reqs, warnings

    if len(remaining_idx) < len(middle):
        warnings.append(
            f"Only {len(remaining_idx)} build requirement(s) for {len(middle)} build stage(s); later build stages carry evidence and gate work"
        )

    weights = [KIND_WEIGHTS.get(reqs[i].get("kind", "deliverable"), 1.0) for i in remaining_idx]
    total = sum(weights) or 1.0
    target = total / len(middle)
    buckets: List[List[int]] = [[] for _ in middle]
    bucket_weights = [0.0] * len(middle)
    cursor = 0
    for position, index in enumerate(remaining_idx):
        while cursor < len(middle) - 1 and bucket_weights[cursor] >= target and buckets[cursor]:
            cursor += 1
        buckets[cursor].append(index)
        bucket_weights[cursor] += weights[position]

    # Guarantee monotonic stage order and no empty leading bucket when work exists.
    for stage_index, bucket in enumerate(buckets):
        for index in bucket:
            reqs[index]["owner_stage"] = middle[stage_index]["id"]

    if discovery_idx:
        for index in discovery_idx:
            reqs[index]["owner_stage"] = first["id"]
    else:
        # First middle stage requirement becomes the discovery entry requirement.
        candidates = remaining_idx
        if candidates:
            reqs[candidates[0]]["owner_stage"] = first["id"]
            buckets[0] = [i for i in buckets[0] if i != candidates[0]]

    for index, req in enumerate(reqs):
        if req.get("owner_stage") is None:
            req["owner_stage"] = first["id"]
    return reqs, warnings


def build_plan(
    *,
    goal: str,
    requirements: Sequence[Dict[str, Any]],
    files: Sequence[Dict[str, Any]],
    metrics: Dict[str, Any],
    estimate: Dict[str, Any],
    policy: Dict[str, Any],
    stage_count: Optional[int] = None,
    stage_count_origin: str = "recommended",
    capabilities: Optional[Sequence[str]] = None,
    assumptions: Optional[Sequence[Dict[str, Any]]] = None,
    scope: Optional[Dict[str, List[str]]] = None,
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    bounds = policy["stage_bounds"]
    count = int(stage_count if stage_count is not None else estimate["recommended"])
    if count < bounds["min"] or count > bounds["max"]:
        raise ToolError(
            f"Stage count {count} is outside the allowed range {bounds['min']}-{bounds['max']}",
            code="stage-count-invalid",
            exit_code=EXIT_USAGE,
            hint=f"Choose a count between {bounds['min']} and {bounds['max']}.",
        )
    plan_warnings = list(warnings or [])
    titles = _build_stage_titles(policy, count)

    stages: List[Dict[str, Any]] = []
    for index, blueprint in enumerate(titles, start=1):
        stages.append(
            {
                "id": f"S{index}",
                "title": blueprint["title"],
                "role": blueprint["role"],
                "objective": blueprint["objective"],
                "requirements": [],
                "outputs": [],
                "entry_gate": None,
                "exit_gate": None,
                "status": "pending",
                "depends_on": [f"S{index - 1}"] if index > 1 else [],
            }
        )

    assigned, alloc_warnings = allocate_requirements(requirements, stages, policy=policy)
    plan_warnings.extend(alloc_warnings)

    for req in assigned:
        for stage in stages:
            if stage["id"] == req["owner_stage"]:
                stage["requirements"].append(req["id"])
                break

    for index, stage in enumerate(stages):
        if stage["role"] == "discovery":
            stage["entry_gate"] = "discovery"
            stage["exit_gate"] = "file-inventory"
        elif stage["role"] == "build":
            stage["entry_gate"] = "build"
            stage["exit_gate"] = "verification" if stage["id"] == f"S{len(stages) - 1}" else "build"
        else:
            stage["entry_gate"] = "final-assembly"
            stage["exit_gate"] = "final-assembly"

    for stage in stages:
        if stage["role"] == "discovery" and any(
            r["kind"] == "deliverable" for r in assigned if r["owner_stage"] == stage["id"]
        ):
            stage["title"] = "Scope lock and first work package"
            stage["objective"] = (
                "Lock the requirement ledger and file contract, then produce the first work package because "
                "the request carried no separate discovery requirement"
            )

    empty_stages = [s["id"] for s in stages if not s["requirements"]]
    if empty_stages:
        plan_warnings.append(
            "Stage(s) " + ", ".join(empty_stages) + " carry gate and integration work only because requirements were fewer than stages"
        )

    total_units = sum(KIND_WEIGHTS.get(r.get("kind", "deliverable"), 1.0) for r in assigned)
    for stage in stages:
        stage_units = sum(KIND_WEIGHTS.get(r["kind"], 1.0) for r in assigned if r["owner_stage"] == stage["id"])
        share = stage_units / total_units if total_units else 0.0
        stage["effort_share"] = round(share, 3)
        stage["estimated_units"] = round(stage_units + float(policy["unit_defaults"]["stage_overhead_units"]), 2)

    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "goal": goal.strip(),
        "single_response_feasible": metrics["single_response_feasible"],
        "metrics": metrics,
        "recommendation": {
            "recommended": estimate["recommended"],
            "minimum": estimate["minimum"],
            "maximum": estimate["maximum"],
            "confidence": estimate["confidence"],
            "reasons": estimate["reasons"],
        },
        "stage_count": count,
        "stage_count_origin": stage_count_origin,
        "user_selected_stage_count": count if stage_count_origin == "user-selected" else None,
        "stages": stages,
        "requirements": assigned,
        "files": list(files),
        "assumptions": list(assumptions or []),
        "scope": scope or {"in": [], "out": [], "deferred": []},
        "risks": _derive_risks(metrics, files),
        "capabilities": list(capabilities or []),
        "warnings": plan_warnings,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    errors = validate_plan(plan, policy=policy)
    if errors:
        raise ToolError(
            "Generated plan failed internal validation",
            code="plan-invalid",
            exit_code=EXIT_USAGE,
            details={"errors": errors},
        )
    return plan


def _derive_risks(metrics: Dict[str, Any], files: Sequence[Dict[str, Any]]) -> List[str]:
    risks: List[str] = []
    if metrics["inputs"]["files_missing"]:
        risks.append("Required input files are missing; building on absent files would fabricate content")
    if metrics["inputs"]["uncertainty"] >= 0.5:
        risks.append("High uncertainty; discovery must resolve open questions before build work")
    if metrics["risk"] == "high":
        risks.append("Work exceeds the one-response budget; verification must be scheduled, not assumed")
    if not risks:
        risks.append("No dominant risk detected; standard verification gates still apply")
    return risks


# ---------------------------------------------------------------------------
# Plan validation and rebalancing
# ---------------------------------------------------------------------------


def _merge_title(left: str, right: str) -> str:
    base_left = re.sub(r" \((?:second pass|combined)\)$", "", left)
    base_right = re.sub(r" \((?:second pass|combined)\)$", "", right)
    if base_left == base_right:
        return f"{base_left} (combined)"
    joined = f"{base_left} + {base_right}"
    if len(joined) <= 64:
        return joined
    return "Combined build and verification block"


def validate_plan(plan: Dict[str, Any], *, policy: Optional[Dict[str, Any]] = None) -> List[str]:
    errors: List[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    goal = plan.get("goal")
    if not isinstance(goal, str) or len(goal.strip()) < 3:
        errors.append("goal must be a non-empty string of at least 3 characters")
    stages = plan.get("stages")
    requirements = plan.get("requirements")
    if not isinstance(stages, list) or not stages:
        errors.append("stages must be a non-empty list")
        stages = []
    if not isinstance(requirements, list):
        errors.append("requirements must be a list")
        requirements = []
    ids = [s.get("id") for s in stages]
    if len(set(ids)) != len(ids):
        errors.append("stage ids must be unique")
    for stage in stages:
        if stage.get("role") not in ("discovery", "build", "assembly"):
            errors.append(f"stage {stage.get('id')} has invalid role {stage.get('role')!r}")
        if not stage.get("title"):
            errors.append(f"stage {stage.get('id')} is missing a title")
        for req_id in stage.get("requirements", []):
            if req_id not in {r.get("id") for r in requirements}:
                errors.append(f"stage {stage.get('id')} references unknown requirement {req_id}")
    owner_map: Dict[str, List[str]] = {}
    for req in requirements:
        owner = req.get("owner_stage")
        if owner is None:
            errors.append(f"requirement {req.get('id')} has no owner stage")
            continue
        if owner not in ids:
            errors.append(f"requirement {req.get('id')} is owned by unknown stage {owner}")
        owner_map.setdefault(owner, []).append(req.get("id"))
        for key in ("text", "acceptance", "kind"):
            if not req.get(key):
                errors.append(f"requirement {req.get('id')} is missing {key}")
    for stage in stages:
        listed = set(stage.get("requirements", []))
        owned = set(owner_map.get(stage.get("id"), []))
        if listed != owned:
            errors.append(f"stage {stage.get('id')} requirement list does not match ledger ownership")
    if requirements and stages:
        if stages[0].get("role") != "discovery" and len(stages) > 1:
            errors.append("first stage must have the discovery role")
        if stages[-1].get("role") != "assembly":
            errors.append("last stage must have the assembly role")
    count = plan.get("stage_count")
    if isinstance(count, int) and count != len(stages):
        errors.append("stage_count must equal the number of stages")
    if policy:
        bounds = policy["stage_bounds"]
        if isinstance(count, int) and not bounds["min"] <= count <= bounds["max"]:
            errors.append(f"stage_count {count} is outside {bounds['min']}-{bounds['max']}")
        # Protected gates scale with the plan shape: a one-stage plan needs only
        # final assembly, a two-stage plan adds the file contract, and three or
        # more stages must carry an explicit verification gate.
        required_gates = {"final-assembly"}
        if len(stages) > 1:
            required_gates.add("file-inventory")
        if len(stages) >= 3:
            required_gates.add("verification")
        required_gates &= set(policy.get("protected_checks", [])) or required_gates
        gates = {s.get("exit_gate") for s in stages}
        missing = required_gates - gates
        if missing:
            errors.append("protected gate(s) missing from the plan: " + ", ".join(sorted(missing)))
    return errors


def rebalance_plan(
    plan: Dict[str, Any],
    new_count: int,
    *,
    policy: Dict[str, Any],
    direction: str = "auto",
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Rebalance an existing plan to a new stage count without dropping requirements."""
    bounds = policy["stage_bounds"]
    if not bounds["min"] <= int(new_count) <= bounds["max"]:
        raise ToolError(
            f"Stage count {new_count} is outside the allowed range {bounds['min']}-{bounds['max']}",
            code="stage-count-invalid",
            hint=f"Choose a count between {bounds['min']} and {bounds['max']}.",
        )
    current = len(plan["stages"])
    if direction == "auto":
        direction = "expand" if int(new_count) > current else ("compress" if int(new_count) < current else "hold")
    if direction not in ("expand", "compress", "hold"):
        raise ToolError("--direction must be auto, expand, compress or hold", code="input-invalid")

    warnings: List[str] = []
    tradeoffs: List[str] = []
    working = copy.deepcopy(plan)
    if direction == "hold":
        warnings.append("Stage count unchanged; the plan was re-validated only")
        tradeoffs.append("No reallocation was needed")
        return working, warnings, tradeoffs

    if direction == "compress":
        if int(new_count) >= current:
            raise ToolError(
                f"Cannot compress from {current} to {new_count} stages",
                code="direction-mismatch",
                hint="Use --direction expand to increase the stage count.",
            )
        protected = set(policy.get("protected_checks", []))
        while len(working["stages"]) > int(new_count):
            stages = working["stages"]
            merged = False
            for index in range(len(stages) - 2, 0, -1):
                left, right = stages[index], stages[index + 1]
                if left["role"] != "build" or right["role"] != "build":
                    continue
                # A protection gate must survive the merge: either the left stage
                # keeps it, or the right stage carries it forward.
                if left.get("exit_gate") in protected and right.get("exit_gate") not in protected:
                    continue
                left["title"] = _merge_title(left["title"], right["title"])
                left["objective"] = left["objective"].rstrip(".") + "; " + right["objective"].lower()
                left["requirements"] = left["requirements"] + right["requirements"]
                for req in working["requirements"]:
                    if req.get("owner_stage") == right["id"]:
                        req["owner_stage"] = left["id"]
                left["outputs"] = sorted(set(left.get("outputs", [])) | set(right.get("outputs", [])))
                left["exit_gate"] = right.get("exit_gate") or left.get("exit_gate")
                left["estimated_units"] = round(float(left.get("estimated_units", 0)) + float(right.get("estimated_units", 0)), 2)
                left["effort_share"] = round(float(left.get("effort_share", 0)) + float(right.get("effort_share", 0)), 3)
                tradeoffs.append(f"Merged {right['id']} into {left['id']}; both work packages now close at the same checkpoint")
                del stages[index + 1]
                merged = True
                break
            if not merged:
                raise ToolError(
                    f"Cannot compress to {new_count} stages: the remaining work cannot be merged into the "
                    "discovery stage or the final assembly stage without breaking a gate boundary",
                    code="compression-blocked",
                    exit_code=EXIT_GATE,
                    hint=(
                        f"Choose at least {len(stages)} stages, or start from a fresh plan with "
                        f"'plan --count {new_count}' if that structure is genuinely wanted."
                    ),
                )
        tradeoffs.append("Fewer checkpoints: combined stages are verified together rather than incrementally")
    else:  # expand
        if int(new_count) <= current:
            raise ToolError(
                f"Cannot expand from {current} to {new_count} stages",
                code="direction-mismatch",
                hint="Use --direction compress to reduce the stage count.",
            )
        while len(working["stages"]) < int(new_count):
            stages = working["stages"]
            splittable = [
                s for s in stages
                if s["role"] == "build" and len(s.get("requirements", [])) >= 2
            ]
            if splittable:
                target = max(splittable, key=lambda s: (len(s["requirements"]), -int(s["id"][1:])))
                ids = target["requirements"]
                half = max(1, len(ids) // 2)
                new_stage = {
                    "id": "",
                    "title": target["title"] + " (second pass)",
                    "role": "build",
                    "objective": target["objective"],
                    "requirements": ids[half:],
                    "outputs": [],
                    "entry_gate": target.get("entry_gate"),
                    "exit_gate": target.get("exit_gate"),
                    "status": "pending",
                    "depends_on": [],
                    "effort_share": 0.0,
                    "estimated_units": 0.0,
                }
                target["requirements"] = ids[:half]
                position = stages.index(target) + 1
                stages.insert(position, new_stage)
                tradeoffs.append(f"Split {target['id']} so {target['title']} is verified in two checkpoints")
                for req in working["requirements"]:
                    if req["id"] in new_stage["requirements"]:
                        req["owner_stage"] = "__pending__"
            else:
                base_index = max(1, len(stages) - 1)
                build_blueprints = [b for b in policy.get("stage_blueprints") or [] if b.get("role") == "build"] or [
                    {"title": "Review gate", "objective": "Verify the preceding work package"}
                ]
                used_titles = {stage["title"] for stage in stages}
                blueprint = next(
                    (b for b in build_blueprints if b["title"] not in used_titles),
                    build_blueprints[len(stages) % len(build_blueprints)],
                )
                stages.insert(
                    base_index,
                    {
                        "id": "",
                        "title": blueprint.get("title", "Review gate"),
                        "role": "build",
                        "objective": blueprint.get("objective", "Verify the preceding work package"),
                        "requirements": [],
                        "outputs": [],
                        "entry_gate": "build",
                        "exit_gate": "build",
                        "status": "pending",
                        "depends_on": [],
                        "effort_share": 0.0,
                        "estimated_units": float(policy["unit_defaults"]["stage_overhead_units"]),
                    },
                )
                tradeoffs.append("Added a gate-only stage; requirements remain in their original stages")
        tradeoffs.append("More checkpoints: each stage holds a smaller work package and is verified sooner")

    # Renumber, rebuild chains, and relink requirement ownership.
    remap: Dict[str, str] = {}
    for index, stage in enumerate(working["stages"], start=1):
        old_id = stage["id"]
        stage["id"] = f"S{index}"
        remap[old_id] = stage["id"]
    for index, stage in enumerate(working["stages"]):
        stage["depends_on"] = [f"S{index}"] if index > 0 else []
        if stage["role"] == "discovery":
            stage["entry_gate"], stage["exit_gate"] = "discovery", "file-inventory"
        elif stage["role"] == "assembly":
            stage["entry_gate"], stage["exit_gate"] = "final-assembly", "final-assembly"
        else:
            # Build stages normalise their gates: only the last build stage
            # carries the verification gate, so the protected check survives
            # exactly one checkpoint.
            stage["entry_gate"] = "build"
            stage["exit_gate"] = "verification" if index == len(working["stages"]) - 2 else "build"
    for req in working["requirements"]:
        owner = req.get("owner_stage")
        if owner == "__pending__":
            for stage in working["stages"]:
                if req["id"] in stage["requirements"]:
                    req["owner_stage"] = stage["id"]
                    break
        elif owner in remap:
            req["owner_stage"] = remap[owner]
    for stage in working["stages"]:
        stage["requirements"] = [rid for rid in stage["requirements"] if any(r["id"] == rid for r in working["requirements"])]
    for stage in working["stages"]:
        owned = sorted(
            (r["id"] for r in working["requirements"] if r["owner_stage"] == stage["id"]),
            key=lambda rid: int(rid[1:]),
        )
        stage["requirements"] = owned

    working["stage_count"] = len(working["stages"])
    working["stage_count_origin"] = "user-selected"
    working["user_selected_stage_count"] = len(working["stages"])
    working["updated_at"] = now_iso()
    working.setdefault("warnings", []).extend(warnings)
    tradeoffs.append(
        f"Stage count changed from {current} to {len(working['stages'])}; every requirement still has exactly one owner and the final stage still performs assembly"
    )
    errors = validate_plan(working, policy=policy)
    if errors:
        raise ToolError(
            "Rebalanced plan failed validation",
            code="plan-invalid",
            exit_code=EXIT_USAGE,
            details={"errors": errors},
        )
    return working, warnings, tradeoffs


# ---------------------------------------------------------------------------
# Decomposition, scheduling, gates and ledgers
# ---------------------------------------------------------------------------


def decompose(plan: Dict[str, Any], *, parallel: bool = False) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    order = {req["id"]: index for index, req in enumerate(plan["requirements"])}

    for req in plan["requirements"]:
        nodes.append(
            {
                "id": req["id"],
                "text": req["text"],
                "kind": req["kind"],
                "owner_stage": req["owner_stage"],
                "units": KIND_WEIGHTS.get(req["kind"], 1.0),
                "pass_condition": req["acceptance"],
            }
        )
    dependency_cues = ("after ", "then ", "based on", "depends on", "using the", "once ", "following the")
    previous_by_stage: Dict[str, str] = {}
    first_of_stage: Dict[str, str] = {}
    last_of_stage: Dict[str, str] = {}
    for req in plan["requirements"]:
        stage_id = req["owner_stage"]
        text = req["text"].lower()
        if stage_id in previous_by_stage and any(cue in text for cue in dependency_cues):
            edges.append({"from": previous_by_stage[stage_id], "to": req["id"], "kind": "sequence"})
        previous_by_stage[stage_id] = req["id"]
        first_of_stage.setdefault(stage_id, req["id"])
        last_of_stage[stage_id] = req["id"]

    # Every stage boundary is a handoff: the previous stage must close its gate
    # before any root work package of the next stage starts. Requirements inside
    # a stage stay parallel unless the text declares a dependency.
    roots_of_stage: Dict[str, List[str]] = {}
    for req in plan["requirements"]:
        if req["id"] not in {edge["to"] for edge in edges}:
            roots_of_stage.setdefault(req["owner_stage"], []).append(req["id"])
    upstream: Optional[str] = None
    for stage in plan["stages"]:
        stage_id = stage["id"]
        if upstream:
            for root in roots_of_stage.get(stage_id, []):
                edges.append({"from": upstream, "to": root, "kind": "handoff"})
        if last_of_stage.get(stage_id):
            upstream = last_of_stage[stage_id]

    waves: List[List[str]] = []
    indegree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        indegree[edge["to"]] += 1
    available = sorted([nid for nid, degree in indegree.items() if degree == 0], key=lambda n: order[n])
    while available:
        waves.append(available)
        following: List[str] = []
        for nid in available:
            for edge in edges:
                if edge["from"] == nid:
                    indegree[edge["to"]] -= 1
                    if indegree[edge["to"]] == 0:
                        following.append(edge["to"])
        available = sorted(set(following), key=lambda n: order[n])

    contracts: List[Dict[str, Any]] = []
    if parallel:
        for stage in plan["stages"]:
            owned = [r for r in plan["requirements"] if r["owner_stage"] == stage["id"]]
            contracts.append(
                {
                    "stage": stage["id"],
                    "outputs": stage.get("outputs", []),
                    "requires_fields": ["id", "path", "status", "evidence"],
                    "merge_key": f"{slugify(stage['title'], max_length=32)}-{stage['id'].lower()}",
                    "version": MODULE_VERSION,
                    "requirements": [r["id"] for r in owned],
                }
            )

    return {
        "goal": plan["goal"],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "waves": waves,
        "wave_count": len(waves),
        "nodes": nodes,
        "edges": edges,
        "merge_contracts": contracts,
        "parallel_eligible": bool(parallel),
        "stage_chain": [
            {"id": stage["id"], "depends_on": stage["depends_on"], "outputs": stage.get("outputs", [])}
            for stage in plan["stages"]
        ],
    }


def critical_path(plan: Dict[str, Any], *, policy: Dict[str, Any], parallel: bool = False) -> Dict[str, Any]:
    stage_map = {stage["id"]: stage for stage in plan["stages"]}
    durations: Dict[str, float] = {}
    for stage in plan["stages"]:
        units = sum(KIND_WEIGHTS.get(r["kind"], 1.0) for r in plan["requirements"] if r["owner_stage"] == stage["id"])
        durations[stage["id"]] = round(units + float(policy["unit_defaults"]["stage_overhead_units"]), 2)

    graph = decompose(plan, parallel=parallel)
    longest: Dict[str, float] = {}
    predecessor: Dict[str, Optional[str]] = {}
    node_stage = {node["id"]: node["owner_stage"] for node in graph["nodes"]}
    incoming = {node_id: [] for node_id in node_stage}
    for edge in graph["edges"]:
        incoming[edge["to"]].append(edge["from"])
    node_duration = {node["id"]: node["units"] for node in graph["nodes"]}

    for node_id in [nid for wave in graph["waves"] for nid in wave]:
        if not incoming[node_id]:
            longest[node_id] = node_duration[node_id]
            predecessor[node_id] = None
        else:
            best_from = max(incoming[node_id], key=lambda nid: longest.get(nid, 0.0))
            longest[node_id] = round(longest.get(best_from, 0.0) + node_duration[node_id], 2)
            predecessor[node_id] = best_from

    if not node_duration:
        return {
            "critical_path": [],
            "total_estimate_units": 0.0,
            "buffer_units": 0.0,
            "stage_durations": durations,
            "escalation_triggers": ["No requirements to schedule"],
        }

    tail = max(node_duration, key=lambda nid: longest[nid])
    path_nodes: List[str] = []
    cursor: Optional[str] = tail
    while cursor is not None:
        path_nodes.append(cursor)
        cursor = predecessor.get(cursor)
    path_nodes.reverse()
    path_stages: List[str] = []
    for node_id in path_nodes:
        stage_id = node_stage[node_id]
        if not path_stages or path_stages[-1] != stage_id:
            path_stages.append(stage_id)

    # Gate-only stages still consume their overhead, so they extend the path.
    last_index = max((index for index, stage in enumerate(plan["stages"]) if stage["id"] in path_stages), default=-1)
    for index, stage in enumerate(plan["stages"]):
        if index > last_index and stage["id"] not in path_stages:
            path_stages.append(stage["id"])

    uncertainty = float(plan.get("metrics", {}).get("inputs", {}).get("uncertainty", 0.15))
    total = round(sum(durations.values()), 2)
    buffer_units = round(total * uncertainty, 2)
    triggers = [
        f"Escalate if stage {stage_id} exceeds {round(durations[stage_id] * 1.5, 2)} units (50 percent over estimate)"
        for stage_id in path_stages
    ]
    triggers.append("Escalate immediately if a required input file is reported missing")
    triggers.append("Escalate if a blocked gate has no evidence after two repair attempts")
    return {
        "critical_path": [
            {"stage": stage_id, "title": stage_map[stage_id]["title"], "duration_units": durations[stage_id]}
            for stage_id in path_stages
        ],
        "critical_nodes": path_nodes,
        "total_estimate_units": total,
        "buffer_units": buffer_units,
        "committed_total_units": round(total + buffer_units, 2),
        "stage_durations": durations,
        "parallel_option": {
            "used": bool(parallel),
            "note": "Stages remain a dependency chain; parallel branches only apply to gate-only splits",
        },
        "escalation_triggers": triggers,
    }


def check_gate(
    gate_name: str,
    *,
    gates_config: Dict[str, Any],
    evidence: Dict[str, Any],
    phase: str = "exit",
) -> Dict[str, Any]:
    gates = gates_config.get("gates", {})
    if gate_name not in gates:
        raise ToolError(
            f"Unknown gate: {gate_name}",
            code="gate-unknown",
            exit_code=EXIT_USAGE,
            hint="Run 'gate --list' to see the available gates.",
            details={"available": sorted(gates)},
        )
    gate = gates[gate_name]
    criteria = list(gate.get(phase) or [])
    if not criteria:
        raise ToolError(
            f"Gate {gate_name} has no {phase} criteria",
            code="gate-empty",
            exit_code=EXIT_USAGE,
        )
    results: List[Dict[str, Any]] = []
    missing_required: List[str] = []
    failed_required: List[str] = []
    for criterion in criteria:
        cid = criterion["id"]
        entry = evidence.get(cid) or {}
        passed = bool(entry.get("passed"))
        supplied = "evidence" in entry and str(entry.get("evidence", "")).strip() != ""
        results.append(
            {
                "id": cid,
                "criterion": criterion["criterion"],
                "required": bool(criterion.get("required")),
                "passed": passed,
                "has_evidence": supplied,
                "evidence": entry.get("evidence", ""),
            }
        )
        if criterion.get("required"):
            if not passed:
                failed_required.append(cid)
            if not supplied:
                missing_required.append(cid)
    blocking = bool(gate.get("blocking", True))
    if failed_required or missing_required:
        status = "blocked" if blocking else "repair"
    else:
        status = "pass"
    unknown = sorted(set(evidence) - {c["id"] for c in criteria})
    return {
        "gate": gate_name,
        "phase": phase,
        "status": status,
        "blocking": blocking,
        "results": results,
        "failed_required": failed_required,
        "missing_evidence": missing_required,
        "unknown_evidence_keys": unknown,
        "next_action": {
            "pass": f"Proceed past gate {gate_name}",
            "repair": f"Repair the failed criteria of gate {gate_name} before proceeding",
            "blocked": f"Do not start dependent work; satisfy gate {gate_name} first",
        }[status],
    }


def ledger_rows(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    rows = []
    for req in plan["requirements"]:
        rows.append(
            {
                "id": req["id"],
                "text": req["text"],
                "kind": req["kind"],
                "owner_stage": req.get("owner_stage") or "",
                "acceptance": req["acceptance"],
                "status": req.get("status", "pending"),
                "evidence": req.get("evidence", ""),
            }
        )
    return rows


def ledger_csv(plan: Dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["id", "text", "kind", "owner_stage", "acceptance", "status", "evidence"],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in ledger_rows(plan):
        writer.writerow(row)
    return buffer.getvalue()


def ledger_markdown(plan: Dict[str, Any]) -> str:
    lines = [
        f"# Requirement ledger: {plan['goal']}",
        "",
        f"Stages: {plan['stage_count']} | Requirements: {len(plan['requirements'])} | Single-response feasible: {'yes' if plan.get('single_response_feasible') else 'no'}",
        "",
        "| ID | Requirement | Kind | Owner | Acceptance | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for req in plan["requirements"]:
        text = req["text"].replace("|", "\\|")
        acceptance = req["acceptance"].replace("|", "\\|")
        lines.append(
            f"| {req['id']} | {text} | {req['kind']} | {req.get('owner_stage') or '-'} | {acceptance} | {req.get('status', 'pending')} |"
        )
    return "\n".join(lines) + "\n"


def render_plan_text(plan: Dict[str, Any]) -> str:
    metrics = plan.get("metrics", {})
    inputs = metrics.get("inputs", {})
    recommendation = plan.get("recommendation", {})
    lines = [
        f"Goal: {plan['goal']}",
        f"Single-response feasible: {'yes' if plan.get('single_response_feasible') else 'no'}"
        f"  |  Work units: {metrics.get('units')} of {metrics.get('single_response_budget_units')} budget  |  Risk: {metrics.get('risk')}",
        f"Recommended stages: {recommendation.get('recommended')}"
        f" (min {recommendation.get('minimum')}, max {recommendation.get('maximum')}, confidence {recommendation.get('confidence')})",
        f"Stage count in plan: {plan['stage_count']} ({plan.get('stage_count_origin', 'recommended')})",
        f"Requirements: {len(plan['requirements'])}  |  Files: {inputs.get('files', 0)}"
        f" ({inputs.get('files_present', 0)} present, {inputs.get('files_missing', 0)} missing)"
        f"  |  Dependencies depth: {inputs.get('dependency_depth')}  |  Tool calls: {inputs.get('tool_calls')}",
        "",
        "Stages",
    ]
    header = f"{'ID':<4}{'Stage':<44}{'Entry gate':<18}{'Exit gate':<18}{'Reqs':<8}{'Status'}"
    lines.append(header)
    lines.append("-" * len(header))
    for stage in plan["stages"]:
        reqs = ",".join(stage["requirements"]) or "-"
        lines.append(
            f"{stage['id']:<4}{stage['title'][:43]:<44}{(stage.get('entry_gate') or '-'):<18}"
            f"{(stage.get('exit_gate') or '-'):<18}{reqs[:7]:<8}{stage.get('status', 'pending')}"
        )
    lines.append("")
    lines.append("Why this count:")
    for reason in recommendation.get("reasons", []):
        lines.append(f"  - {reason}")
    if plan.get("capabilities"):
        lines.append("")
        lines.append("Capabilities engaged: " + ", ".join(plan["capabilities"]))
    if plan.get("files"):
        lines.append("")
        lines.append("File contract:")
        for item in plan["files"]:
            state = "present" if item.get("present") else ("missing" if item.get("required", True) else "optional-missing")
            lines.append(f"  - {item['path']} [{item.get('role', 'input')}] -> {state}")
    if plan.get("assumptions"):
        lines.append("")
        lines.append("Assumptions:")
        for item in plan["assumptions"]:
            lines.append(f"  - {item.get('text')} (impact {item.get('impact', 'medium')}; recheck when {item.get('recheck_trigger', 'new information appears')})")
    if plan.get("risks"):
        lines.append("")
        lines.append("Risks:")
        for risk in plan["risks"]:
            lines.append(f"  - {risk}")
    if plan.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        for warning in plan["warnings"]:
            lines.append(f"  - {warning}")
    lines.append("")
    lines.append(
        "Next action: confirm or override the count, then run "
        "'checkpoint start' to open Stage 1 of "
        f"{plan['stage_count']}."
    )
    return "\n".join(lines)


def render_checkpoint_text(state: Dict[str, Any]) -> str:
    plan = state["plan"]
    total = state["stage_count"]
    index = state["current_stage_index"]
    stage = state["stages"][min(index, total - 1)]
    lines = [
        f"Stage {min(index + 1, total)} of {total}: {stage['title']}",
        f"Goal: {plan['goal']}",
        f"Objective: {stage['objective']}",
        f"Stage status: {stage['status']}",
    ]
    if stage.get("requirements"):
        lines.append("Requirements in this stage:")
        for req_id in stage["requirements"]:
            req = next((r for r in plan["requirements"] if r["id"] == req_id), None)
            if req:
                lines.append(f"  - {req_id} [{req['kind']}] {req['text']}")
    if stage.get("outputs"):
        lines.append("Declared outputs: " + ", ".join(stage["outputs"]))
    else:
        lines.append("Declared outputs: none declared yet (record them when completing the stage)")
    lines.append(f"Entry gate: {stage.get('entry_gate') or '-'} | Exit gate: {stage.get('exit_gate') or '-'}")
    if stage.get("blockers"):
        lines.append("Blockers:")
        for blocker in stage["blockers"]:
            lines.append(f"  - {blocker}")
    lines.append("")
    lines.append(f"Completed stages: {state['completed_count']} of {total}")
    remaining = total - state["completed_count"]
    lines.append(f"Remaining stages: {remaining}")
    lines.append(f"Next action: {state['next_action']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Continue-intent classification
# ---------------------------------------------------------------------------


def normalize_reply(reply: str) -> str:
    text = normalize_input(reply or "").lower()
    text = re.sub(r"[\u2018\u2019]", "'", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" \t\r\n.!;:")
    return text


def classify_reply(reply: str, intents: Dict[str, Any]) -> Dict[str, Any]:
    text = normalize_reply(reply)
    if not text:
        return {
            "intent": "needs_clarification",
            "reply": reply,
            "normalized": text,
            "exit_code": EXIT_USAGE,
            "message": "No reply was supplied; the plan stays paused until the user signals continuation",
        }

    stage_match = re.match(r"^stage (\d+)$", text)
    phases = (
        ("stop", "hard_stop_patterns"),
        ("scope_change", "scope_change_patterns"),
        ("continue", "continue_patterns"),
        ("needs_clarification", "ambiguous_patterns"),
    )
    for intent, key in phases:
        for pattern in intents.get(key, []):
            if re.search(pattern, text, flags=re.IGNORECASE):
                if intent == "stop":
                    return {
                        "intent": "stop",
                        "reply": reply,
                        "normalized": text,
                        "exit_code": EXIT_PARTIAL,
                        "message": "Work paused by explicit stop request; the checkpoint state is preserved",
                    }
                if intent == "scope_change":
                    return {
                        "intent": "scope_change",
                        "reply": reply,
                        "normalized": text,
                        "exit_code": EXIT_USAGE,
                        "message": "Reply changes scope or stage count; rebalance the plan before continuing",
                        "recommended_command": "checkpoint set-count --count N",
                    }
                if intent == "continue":
                    return {
                        "intent": "continue",
                        "reply": reply,
                        "normalized": text,
                        "exit_code": EXIT_OK,
                        "message": "Continuation recognized; advance to the next work package",
                        "target_stage": int(stage_match.group(1)) if stage_match else None,
                    }
                return {
                    "intent": "needs_clarification",
                    "reply": reply,
                    "normalized": text,
                    "exit_code": EXIT_USAGE,
                    "message": "Reply is ambiguous; ask for Continue, a stage count change, or a stop",
                }
    return {
        "intent": "needs_clarification",
        "reply": reply,
        "normalized": text,
        "exit_code": EXIT_USAGE,
        "message": "Reply does not match a known continuation, scope change, or stop intent; ask the user to confirm",
    }


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def state_from_plan(plan: Dict[str, Any], *, policy: Dict[str, Any], files_state: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    stamp = now_iso()
    stages = []
    for stage in plan["stages"]:
        stages.append(
            {
                "id": stage["id"],
                "title": stage["title"],
                "role": stage["role"],
                "objective": stage["objective"],
                "requirements": list(stage["requirements"]),
                "outputs": list(stage.get("outputs", [])),
                "entry_gate": stage.get("entry_gate"),
                "exit_gate": stage.get("exit_gate"),
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "verified": [],
                "blockers": [],
                "attempts": 0,
                "notes": "",
            }
        )
    state = {
        "kind": TOOL_NAME,
        "schema_version": policy["checkpoint"]["schema_version"],
        "created_at": stamp,
        "updated_at": stamp,
        "stage_count": plan["stage_count"],
        "current_stage_index": 0,
        "completed_count": 0,
        "paused": True,
        "next_action": f"Begin Stage 1 of {plan['stage_count']}",
        "plan": plan,
        "stages": stages,
        "files": list(files_state if files_state is not None else plan.get("files", [])),
        "history": [{"at": stamp, "event": "checkpoint-start", "detail": f"Plan opened with {plan['stage_count']} stage(s)"}],
    }
    return state


def load_state(path: Path, *, policy: Dict[str, Any]) -> Dict[str, Any]:
    state = load_json_file(path, label="Checkpoint state")
    if state.get("kind") != TOOL_NAME:
        raise ToolError(
            f"Checkpoint state has an unexpected kind: {state.get('kind')!r}",
            code="state-invalid",
            exit_code=EXIT_USAGE,
            hint="Create a state file with 'checkpoint start --plan <file>'.",
        )
    expected = policy["checkpoint"]["schema_version"]
    if state.get("schema_version") != expected:
        raise ToolError(
            f"Checkpoint schema version {state.get('schema_version')} does not match the policy version {expected}",
            code="state-schema-mismatch",
            exit_code=EXIT_USAGE,
            hint="Regenerate the checkpoint with the current policy, or override checkbox.schema_version deliberately.",
            details={"state": state.get("schema_version"), "policy": expected},
        )
    for key in ("stages", "plan", "stage_count", "current_stage_index"):
        if key not in state:
            raise ToolError(
                f"Checkpoint state is missing required key: {key}",
                code="state-invalid",
                exit_code=EXIT_USAGE,
            )
    return state


class StoreLock:
    """Small cross-platform lock used to keep checkpoint writes atomic."""

    def __init__(self, path: Path, *, timeout: float, stale: float, retry: float) -> None:
        self.path = path.with_name(path.name + ".lock")
        self.timeout = timeout
        self.stale = stale
        self.retry = retry
        self.acquired = False

    def __enter__(self) -> "StoreLock":
        deadline = datetime.now(timezone.utc).timestamp() + self.timeout
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(self.path, "x", encoding="utf-8")
                handle.write(now_iso())
                handle.close()
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > self.stale:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if datetime.now(timezone.utc).timestamp() > deadline:
                    raise ToolError(
                        f"Could not acquire the checkpoint lock within {self.timeout}s: {self.path.name}",
                        code="lock-timeout",
                        exit_code=EXIT_LIMIT,
                        hint="Retry after the other writer finishes, or delete a stale .lock file.",
                    )
                import time as _time

                _time.sleep(self.retry)

    def __exit__(self, *_exc: Any) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass


def save_state(path: Path, state: Dict[str, Any], *, policy: Dict[str, Any]) -> Path:
    state["updated_at"] = now_iso()
    lock_cfg = policy["checkpoint"]
    with StoreLock(
        path,
        timeout=float(lock_cfg["lock_timeout_seconds"]),
        stale=float(lock_cfg["lock_stale_seconds"]),
        retry=float(lock_cfg["lock_retry_seconds"]),
    ):
        return atomic_write_json(path, state)


# ---------------------------------------------------------------------------
# File contract
# ---------------------------------------------------------------------------


def inventory_files(
    root: Path,
    *,
    policy: Dict[str, Any],
    declared: Sequence[str] = (),
    required: Sequence[str] = (),
) -> Dict[str, Any]:
    """Inventory declared files plus everything present under root (bounded)."""
    limits = policy["files"]
    if not root.exists():
        raise ToolError(
            f"Workspace root not found: {root}",
            code="root-missing",
            exit_code=EXIT_MISSING,
            hint="Create the folder first or pass an existing --root.",
        )
    if not root.is_dir():
        raise ToolError(f"--root must be a directory: {root}", code="root-invalid")
    ignore = set(limits.get("ignore_globs", []))
    max_files = int(limits["max_files"])
    max_depth = int(limits["max_depth"])

    entries: List[Dict[str, Any]] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        if len(relative.parts) > max_depth:
            continue
        if any(part in ignore for part in relative.parts) or any(part in TEMP_DIR_NAMES for part in relative.parts):
            continue
        scanned += 1
        if scanned > max_files:
            raise ToolError(
                f"Workspace exceeds the configured file limit of {max_files}",
                code="file-limit",
                exit_code=EXIT_LIMIT,
                hint="Raise files.max_files in a policy override or narrow the root.",
            )
        try:
            size = path.stat().st_size
            readable = path.stat().st_size >= 0
        except OSError:
            size, readable = -1, False
        entries.append(
            {
                "path": relative.as_posix(),
                "size": size,
                "readable": readable,
                "declared": False,
                "required": False,
            }
        )

    by_name: Dict[str, List[str]] = {}
    for entry in entries:
        by_name.setdefault(Path(entry["path"]).name.lower(), []).append(entry["path"])

    for index, entry in enumerate(entries):
        twins = by_name.get(Path(entry["path"]).name.lower(), [])
        entry["duplicate_of"] = [p for p in twins if p != entry["path"]] if len(twins) > 1 else []
        entry["status"] = "ok" if entry["readable"] else "unreadable"
        if entry["duplicate_of"]:
            entry["status"] = "duplicate"

    present_names = {Path(e["path"]).name.lower() for e in entries}
    required_set = {Path(name).name.lower() for name in required}
    for name in declared:
        base = Path(name).name.lower()
        if base in present_names:
            for entry in entries:
                if Path(entry["path"]).name.lower() == base:
                    entry["declared"] = True
                    entry["required"] = base in required_set
            continue
        entries.append(
            {
                "path": name,
                "size": 0,
                "readable": False,
                "declared": True,
                "required": base in required_set or not required,
                "duplicate_of": [],
                "status": "missing",
            }
        )

    missing = [e["path"] for e in entries if e["status"] == "missing"]
    missing_required = [e["path"] for e in entries if e["status"] == "missing" and e["required"]]
    duplicates = [e["path"] for e in entries if e["status"] == "duplicate"]
    plan_actions = []
    for name in missing_required:
        plan_actions.append(f"Request re-upload of {name} before any work that depends on it")
    for name in duplicates:
        plan_actions.append(f"Confirm the canonical version of {name} and label superseded copies")
    if not plan_actions:
        plan_actions.append("All declared files are present; continue with content validation")
    return {
        "root": root.as_posix(),
        "count": len(entries),
        "present": len([e for e in entries if e["status"] != "missing"]),
        "missing": missing,
        "missing_required": missing_required,
        "duplicates": duplicates,
        "files": entries,
        "next_actions": plan_actions,
    }
