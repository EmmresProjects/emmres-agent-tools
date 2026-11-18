#!/usr/bin/env python3
"""Shared core for the Offline Engineering Workbench.

Loaded by tools-workbench.py and by the supporting tools-* modules. Contains no
network access, no credential handling, and no shell execution.

Responsibilities:
  * load and validate the extracted skill catalog (asset-skill-catalog.json)
  * load and validate the check policy (asset-check-policy.json)
  * project file walking, hashing, path safety, and text detection
  * structured output, table/Markdown rendering, and secret redaction
  * report template rendering and a small JSON-Schema validator subset
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

VERSION = "1.0.0"
TOOL_NAME = "offline-engineering-workbench"

HERE = Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "asset-skill-catalog.json"
DEFAULT_POLICY = HERE / "asset-check-policy.json"
DEFAULT_TEMPLATE = HERE / "asset-report-template.md"
DEFAULT_MANIFEST_SCHEMA = HERE / "asset-manifest.schema.json"

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_GATE = 4
EXIT_RUNTIME = 5
EXIT_VERIFY = 6
EXIT_SELFTEST = 10

TEXT_EXTENSIONS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs",
    ".java", ".kt", ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".hh", ".cs", ".fs", ".vb", ".rb", ".php", ".swift", ".m", ".mm", ".f",
    ".f90", ".f95", ".for", ".jl", ".r", ".R", ".rmd", ".sql", ".sh", ".bash",
    ".zsh", ".ps1", ".psm1", ".bat", ".cmd", ".toml", ".yaml", ".yml", ".json",
    ".jsonl", ".ndjson", ".xml", ".html", ".htm", ".css", ".scss", ".sass",
    ".less", ".md", ".markdown", ".rst", ".txt", ".cfg", ".ini", ".conf",
    ".properties", ".gradle", ".sbt", ".cmake", ".mk", ".make", ".dockerfile",
    ".containerfile", ".tf", ".tfvars", ".hcl", ".nix", ".bzl", ".bazel",
    ".proto", ".graphql", ".gql", ".vue", ".svelte", ".ipynb", ".env",
    ".gitignore", ".gitattributes", ".editorconfig", ".license", ".template",
}

DATA_EXTENSIONS = {
    ".csv", ".tsv", ".parquet", ".arrow", ".feather", ".orc", ".avro",
    ".json", ".jsonl", ".ndjson", ".xlsx", ".xls", ".sqlite", ".sqlite3",
    ".db", ".npy", ".npz", ".pkl", ".pickle", ".pt", ".pth", ".onnx",
    ".safetensors", ".h5", ".hdf5", ".nc", ".zarr", ".dta", ".sav", ".rds",
    ".geojson", ".shp", ".gpkg", ".tif", ".tiff",
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".kt", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".f", ".f90", ".f95", ".jl", ".r",
}

TEST_NAME_PATTERNS = (
    "*selftest*.py", "test_*.py", "*_test.py", "test_*.js", "*.test.js", "*.test.ts",
    "*.spec.js", "*.spec.ts", "*_test.go", "*_test.rs", "test_*.c",
    "test_*.cpp", "*Test.java", "*Tests.java", "*_test.rb", "test_*.jl",
    "test_*.R", "*Test.cs", "*Tests.cs",
)
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "testing"}

LOCKFILES = {
    "python": ("poetry.lock", "uv.lock", "pylock.toml", "Pipfile.lock",
               "requirements.lock", "constraints.txt", "conda-lock.yml"),
    "node": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock",
             "npm-shrinkwrap.json"),
    "rust": ("Cargo.lock",),
    "go": ("go.sum",),
    "dotnet": ("packages.lock.json",),
}

MANIFEST_NAMES = (
    "manifest.json", "provenance.json", "checksums.sha256", "checksums.txt",
    "sha256sums.txt", "sbom.json", "sbom.spdx.json", "sbom.cdx.json",
)


class WorkbenchError(Exception):
    """Structured, user-facing failure with a stable code and exit status."""

    def __init__(self, code: str, message: str, details: Any = None,
                 exit_code: int = EXIT_RUNTIME) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.exit_code = exit_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "error",
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

_CATALOG_CACHE: Dict[str, Dict[str, Any]] = {}


def load_catalog(path: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
    """Load, validate, and cache the extracted catalog."""
    catalog_path = Path(path) if path else DEFAULT_CATALOG
    if not catalog_path.is_file():
        raise WorkbenchError(
            "catalog-missing",
            "Catalog asset not found: %s" % catalog_path,
            {"expected": str(catalog_path)}, EXIT_RUNTIME,
        )
    key = str(catalog_path.resolve())
    if key in _CATALOG_CACHE:
        return _CATALOG_CACHE[key]
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkbenchError(
            "catalog-invalid-json",
            "Catalog is not valid JSON: %s" % exc,
            {"path": str(catalog_path)}, EXIT_RUNTIME,
        ) from exc
    errors = validate_catalog(data)
    if errors:
        raise WorkbenchError(
            "catalog-invalid",
            "Catalog failed validation.",
            {"path": str(catalog_path), "errors": errors[:10]}, EXIT_RUNTIME,
        )
    _CATALOG_CACHE[key] = data
    return data


def validate_catalog(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["catalog root must be an object"]
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        errors.append("categories must be a non-empty array")
        return errors
    seen_cat: set = set()
    total = 0
    for category in categories:
        if not isinstance(category, dict):
            errors.append("category must be an object")
            continue
        number = category.get("number")
        if not isinstance(number, int):
            errors.append("category.number must be an integer")
        if number in seen_cat:
            errors.append("duplicate category number %r" % number)
        seen_cat.add(number)
        for field in ("id", "name", "slug", "scope"):
            if not category.get(field):
                errors.append("category %r missing %s" % (number, field))
        skills = category.get("skills")
        if not isinstance(skills, list) or not skills:
            errors.append("category %r has no skills" % number)
            continue
        seen_skill: set = set()
        for skill in skills:
            if not isinstance(skill, dict):
                errors.append("skill must be an object")
                continue
            if not skill.get("id"):
                errors.append("skill in category %r missing id" % number)
            if skill.get("id") in seen_skill:
                errors.append("duplicate skill id %r" % skill.get("id"))
            seen_skill.add(skill.get("id"))
            for field in ("name", "description"):
                if not skill.get(field):
                    errors.append("skill %r missing %s" % (skill.get("id"), field))
            total += 1
    declared = data.get("totals", {}).get("skills")
    if isinstance(declared, int) and declared != total:
        errors.append("totals.skills %d does not match %d entries" % (declared, total))
    return errors


def catalog_skills(catalog: Dict[str, Any]) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for category in catalog.get("categories", []):
        for skill in category.get("skills", []):
            yield category, skill


def catalog_skill_count(catalog: Dict[str, Any]) -> int:
    return sum(len(category.get("skills", [])) for category in catalog.get("categories", []))


def find_category(catalog: Dict[str, Any], query: str) -> Dict[str, Any]:
    text = str(query).strip().lower()
    for category in catalog["categories"]:
        if text in (str(category["number"]), category["id"], category["slug"], category["name"].lower()):
            return category
    matches = [c for c in catalog["categories"]
               if text in c["name"].lower() or text in c["slug"]]
    if len(matches) == 1:
        return matches[0]
    raise WorkbenchError(
        "category-not-found",
        "No unique category matches %r." % query,
        {"available": [c["id"] for c in catalog["categories"]]}, EXIT_USAGE,
    )


def find_skill(catalog: Dict[str, Any], query: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    text = str(query).strip().lower()
    for category, skill in catalog_skills(catalog):
        if text in (skill["id"].lower(), skill["slug"], skill["name"].lower()):
            return category, skill
    matches = [(c, s) for c, s in catalog_skills(catalog)
               if text in s["name"].lower() or text in s["description"].lower()]
    if len(matches) == 1:
        return matches[0]
    raise WorkbenchError(
        "skill-not-found",
        "No unique skill matches %r." % query,
        {"candidates": [s["id"] for _, s in matches[:10]]}, EXIT_USAGE,
    )


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #

BASE_POLICY: Dict[str, Any] = {
    "thresholds": {
        "max_file_bytes": 52428800,
        "max_text_bytes": 2097152,
        "max_findings_per_rule": 5,
        "max_findings_total": 200,
        "run_timeout_seconds": 300,
        "run_max_output_bytes": 200000,
        "run_retries": 0,
    },
    "quality_gate": {"fail_on": ["critical", "high"], "warn_on": ["medium", "low", "info"]},
    "exclude_dirs": [".git", "node_modules", ".venv", "__pycache__", "dist", "build"],
    "exclude_globs": ["*.lock", "*.min.js", "*.map"],
    "disabled_rules": [],
    "allow_paths": [],
    "approval_required_patterns": [],
    "environment": {"python_min": "3.9", "offline_only": True},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_policy(path: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
    """Load the policy asset and merge it over safe defaults."""
    policy_path = Path(path) if path else DEFAULT_POLICY
    if not policy_path.is_file():
        raise WorkbenchError(
            "policy-missing", "Policy file not found: %s" % policy_path,
            {"expected": str(policy_path)}, EXIT_RUNTIME,
        )
    try:
        loaded = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkbenchError(
            "policy-invalid-json", "Policy is not valid JSON: %s" % exc,
            {"path": str(policy_path)}, EXIT_USAGE,
        ) from exc
    if not isinstance(loaded, dict):
        raise WorkbenchError("policy-invalid", "Policy root must be an object.",
                             {"path": str(policy_path)}, EXIT_USAGE)
    policy = _deep_merge(BASE_POLICY, loaded)
    errors = validate_policy(policy)
    if errors:
        raise WorkbenchError("policy-invalid", "Policy failed validation.",
                             {"path": str(policy_path), "errors": errors}, EXIT_USAGE)
    policy["_path"] = str(policy_path)
    return policy


def validate_policy(policy: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    thresholds = policy.get("thresholds", {})
    for key in ("max_file_bytes", "max_text_bytes", "max_findings_per_rule",
                "max_findings_total", "run_timeout_seconds",
                "run_max_output_bytes", "run_retries"):
        value = thresholds.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append("thresholds.%s must be a non-negative integer" % key)
    if thresholds.get("max_file_bytes", 0) <= 0:
        errors.append("thresholds.max_file_bytes must be positive")
    fail_on = policy.get("quality_gate", {}).get("fail_on", [])
    if not isinstance(fail_on, list):
        errors.append("quality_gate.fail_on must be an array")
    else:
        for severity in fail_on:
            if severity not in SEVERITIES:
                errors.append("unknown severity in quality_gate.fail_on: %r" % severity)
    for key in ("exclude_dirs", "exclude_globs", "disabled_rules", "allow_paths"):
        if not isinstance(policy.get(key), list):
            errors.append("%s must be an array" % key)
    patterns = policy.get("approval_required_patterns", [])
    if not isinstance(patterns, list):
        errors.append("approval_required_patterns must be an array")
    else:
        for index, entry in enumerate(patterns):
            if not isinstance(entry, dict) or not entry.get("pattern"):
                errors.append("approval_required_patterns[%d] needs a pattern" % index)
                continue
            try:
                re.compile(entry["pattern"])
            except re.error as exc:
                errors.append("approval_required_patterns[%d] invalid regex: %s" % (index, exc))
    return errors


# --------------------------------------------------------------------------- #
# Filesystem helpers
# --------------------------------------------------------------------------- #

def require_directory(path: os.PathLike | str, label: str = "path") -> Path:
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise WorkbenchError("%s-missing" % label, "%s does not exist: %s" % (label, resolved),
                             {"path": str(resolved)}, EXIT_USAGE)
    if not resolved.is_dir():
        raise WorkbenchError("%s-not-directory" % label,
                             "%s is not a directory: %s" % (label, resolved),
                             {"path": str(resolved)}, EXIT_USAGE)
    return resolved.resolve()


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def safe_child(root: Path, relative: str) -> Path:
    """Resolve a relative path and refuse traversal outside root."""
    candidate = (root / relative).resolve()
    if not is_within(root, candidate):
        raise WorkbenchError("path-escape", "Path escapes the project root: %s" % relative,
                             {"root": str(root), "path": relative}, EXIT_USAGE)
    return candidate


def iter_project_files(root: Path, policy: Dict[str, Any],
                       apply_exclude_globs: bool = True) -> Iterator[Dict[str, Any]]:
    """Yield project files with size metadata. Symlinks are never followed."""
    exclude_dirs = set(policy.get("exclude_dirs", []))
    exclude_globs = tuple(policy.get("exclude_globs", [])) if apply_exclude_globs else ()
    max_bytes = int(policy.get("thresholds", {}).get("max_file_bytes", 52428800))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in exclude_dirs and name != ".git"
            and not os.path.islink(os.path.join(dirpath, name))
        )
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(root).as_posix()
            if exclude_globs and any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(filename, pattern)
                                     for pattern in exclude_globs):
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            yield {"path": full, "rel": rel, "size": size, "too_large": size > max_bytes}


def read_text(path: Path, max_bytes: int) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            chunk = handle.read(max_bytes + 1)
    except OSError:
        return None
    if b"\x00" in chunk:
        return None
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return chunk.decode("latin-1")
        except UnicodeDecodeError:
            return None


def is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    return path.name.lower() in {".gitignore", ".gitattributes", ".editorconfig",
                                 ".dockerignore", "dockerfile", "makefile"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1048576) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_named(root: Path, names: Sequence[str]) -> List[str]:
    wanted = {name.lower() for name in names}
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__"}]
        for filename in filenames:
            if filename.lower() in wanted:
                found.append((Path(dirpath) / filename).relative_to(root).as_posix())
    return sorted(found)


def has_test_signal(root: Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath).name.lower() in TEST_DIR_NAMES:
            return True
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__"}]
        for filename in filenames:
            if any(fnmatch.fnmatch(filename.lower(), pattern.lower()) for pattern in TEST_NAME_PATTERNS):
                return True
    return False


def has_source(root: Path) -> bool:
    for entry in iter_project_files(root, {"exclude_dirs": [".git", "node_modules", "__pycache__"],
                                           "exclude_globs": [], "thresholds": {"max_file_bytes": 1}}):
        if entry["path"].suffix.lower() in SOURCE_EXTENSIONS:
            return True
    return False


def format_bytes(size: float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%.0f %s" % (value, unit)) if unit == "B" else ("%.1f %s" % (value, unit))
        value /= 1024
    return "%.1f TB" % value


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

def _stringify(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_table(rows: Sequence[Dict[str, Any]], columns: Optional[Sequence[str]] = None,
                 max_width: int = 58) -> str:
    if not rows:
        return "(no rows)"
    if columns is None:
        columns = list(rows[0].keys())
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = min(max(widths[column], len(_stringify(row.get(column))[:max_width])), max_width)
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    divider = "  ".join("-" * widths[column] for column in columns)
    lines = [header, divider]
    for row in rows:
        lines.append("  ".join(_stringify(row.get(column))[:max_width].ljust(widths[column])
                              for column in columns))
    return "\n".join(lines)


def to_markdown(data: Any, title: str = "Report") -> str:
    lines = ["# %s" % title, ""]
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                continue
            lines.append("- **%s:** %s" % (key, _stringify(value)))
        lines.append("")
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                lines.append("## %s" % key.replace("_", " ").title())
                lines.append("")
                lines.append(render_table(value))
                lines.append("")
            elif isinstance(value, dict):
                lines.append("## %s" % key.replace("_", " ").title())
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(value, indent=2, ensure_ascii=False))
                lines.append("```")
                lines.append("")
    elif isinstance(data, list):
        lines.append(render_table(data))
    else:
        lines.append(str(data))
    return "\n".join(lines)


def write_output(text: str, output: Optional[os.PathLike | str]) -> None:
    if not output:
        sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))
        return
    destination = Path(output).expanduser()
    if destination.parent and not destination.parent.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    sys.stdout.write("wrote %s\n" % destination)


def emit(data: Any, fmt: str = "table", output: Optional[os.PathLike | str] = None,
         title: str = "Report", columns: Optional[Sequence[str]] = None) -> None:
    if fmt == "json":
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    elif fmt == "markdown":
        text = data if isinstance(data, str) else to_markdown(data, title)
    else:
        if isinstance(data, list) and data and isinstance(data[0], dict):
            text = render_table(data, columns)
        elif isinstance(data, dict) and "rows" in data and isinstance(data["rows"], list):
            text = render_table(data["rows"], columns)
        elif isinstance(data, dict):
            lines = []
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines.append("%s: %s" % (key, json.dumps(value, ensure_ascii=False, default=str)))
                else:
                    lines.append("%s: %s" % (key, _stringify(value)))
            text = "\n".join(lines)
        else:
            text = str(data)
    write_output(text, output)


def redact(text: str, limit: int = 200) -> str:
    """Mask credential-like tokens before evidence is stored or printed."""
    masked = text
    patterns = (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)\b(?:aws_access_key_id|aws_secret_access_key|api[_-]?key|secret[_-]?key|"
        r"password|passwd|token|authorization)\b\s*[:=]\s*[\"']?[^\s\"',;]{6,}",
        r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}",
        r"\b[A-Za-z0-9]{32,}\b",
    )
    for pattern in patterns:
        masked = re.sub(pattern, "<redacted>", masked)
    if len(masked) > limit:
        masked = masked[:limit] + "...<truncated>"
    return masked


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Environment and manifest schema
# --------------------------------------------------------------------------- #

OPTIONAL_TOOLS = ("git", "node", "npm", "python3", "gcc", "make", "cmake",
                  "sqlite3", "docker", "kubectl", "terraform", "pandoc", "R", "julia")


def build_environment() -> Dict[str, Any]:
    toolchain: Dict[str, Optional[str]] = {}
    for tool in OPTIONAL_TOOLS:
        path = shutil.which(tool)
        toolchain[tool] = path
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "toolchain": toolchain,
    }


def validate_manifest(data: Any, schema: Dict[str, Any]) -> List[str]:
    """Validate the subset of JSON Schema used by asset-manifest.schema.json."""
    errors: List[str] = []

    def validate_node(node: Any, subschema: Dict[str, Any], path: str) -> None:
        expected = subschema.get("type")
        if expected == "object" and not isinstance(node, dict):
            errors.append("%s must be an object" % path)
            return
        if expected == "array" and not isinstance(node, list):
            errors.append("%s must be an array" % path)
            return
        if expected == "string" and not isinstance(node, str):
            errors.append("%s must be a string" % path)
            return
        if expected == "integer" and (not isinstance(node, int) or isinstance(node, bool)):
            errors.append("%s must be an integer" % path)
            return
        if expected == "boolean" and not isinstance(node, bool):
            errors.append("%s must be a boolean" % path)
            return
        if isinstance(node, str):
            if "minLength" in subschema and len(node) < subschema["minLength"]:
                errors.append("%s is shorter than %d characters" % (path, subschema["minLength"]))
            if "maxLength" in subschema and len(node) > subschema["maxLength"]:
                errors.append("%s is longer than %d characters" % (path, subschema["maxLength"]))
            if "pattern" in subschema and not re.search(subschema["pattern"], node):
                errors.append("%s does not match %s" % (path, subschema["pattern"]))
        if isinstance(node, int) and "minimum" in subschema and node < subschema["minimum"]:
            errors.append("%s is below minimum %s" % (path, subschema["minimum"]))
        if "enum" in subschema and node not in subschema["enum"]:
            errors.append("%s must be one of %s" % (path, subschema["enum"]))
        if isinstance(node, dict):
            for required in subschema.get("required", []):
                if required not in node:
                    errors.append("%s missing required property %r" % (path, required))
            for key, value in node.items():
                if key in subschema.get("properties", {}):
                    validate_node(value, subschema["properties"][key], "%s.%s" % (path, key))
        if isinstance(node, list) and "items" in subschema:
            for index, value in enumerate(node):
                validate_node(value, subschema["items"], "%s[%d]" % (path, index))

    validate_node(data, schema, "$")
    return errors


def load_json(path: os.PathLike | str, label: str) -> Any:
    target = Path(path).expanduser()
    if not target.is_file():
        raise WorkbenchError("%s-missing" % label, "%s file not found: %s" % (label, target),
                             {"path": str(target)}, EXIT_USAGE)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkbenchError("%s-invalid-json" % label,
                             "%s is not valid JSON: %s" % (label, exc),
                             {"path": str(target)}, EXIT_USAGE) from exc


def render_template(template: str, context: Dict[str, str]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{{%s}}" % key, value)
    leftover = re.findall(r"\{\{([a-z0-9_]+)\}\}", rendered)
    if leftover:
        raise WorkbenchError("template-placeholder", "Template has unresolved placeholders.",
                             {"placeholders": sorted(set(leftover))}, EXIT_RUNTIME)
    return rendered


def severity_table(findings: Iterable[Dict[str, Any]]) -> str:
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        severity = finding.get("severity", "info")
        counts[severity] = counts.get(severity, 0) + 1
    return "\n".join("| %s | %d |" % (severity, counts[severity]) for severity in SEVERITIES)


def findings_table(findings: Sequence[Dict[str, Any]], limit: int = 50) -> str:
    if not findings:
        return "_No findings._"
    rows = [{
        "severity": f.get("severity"),
        "rule": f.get("rule_id"),
        "path": f.get("path"),
        "line": f.get("line", ""),
        "evidence": f.get("evidence", ""),
    } for f in findings[:limit]]
    table = render_table(rows, ["severity", "rule", "path", "line", "evidence"], max_width=48)
    if len(findings) > limit:
        table += "\n\n_...and %d more findings (use --format json for the full set)._" % (len(findings) - limit)
    return table


def require_python_ok(minimum: str = "3.9") -> bool:
    current = tuple(int(part) for part in platform.python_version().split(".")[:2])
    required = tuple(int(part) for part in minimum.split(".")[:2])
    return current >= required


def require_python(minimum: str = "3.9") -> None:
    current = tuple(int(part) for part in platform.python_version().split(".")[:2])
    required = tuple(int(part) for part in minimum.split(".")[:2])
    if current < required:
        raise WorkbenchError("python-too-old",
                             "Python %s or newer is required (found %s)."
                             % (minimum, platform.python_version()),
                             {"found": platform.python_version()}, EXIT_RUNTIME)
