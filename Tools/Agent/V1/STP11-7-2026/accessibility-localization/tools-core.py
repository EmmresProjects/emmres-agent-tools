"""Shared core for the accessibility-localization Skill Tool.

Pure standard library. No network access, no third-party packages.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

TOOL_NAME = "accessibility-localization"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
DEFAULT_CONFIG_NAME = "asset-default-config.json"

SEVERITIES = ("critical", "serious", "moderate", "minor", "info")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
STATUSES = ("passed", "attention", "failed", "error", "skipped")


class ToolError(Exception):
    """Structured, user-facing error."""

    def __init__(self, code: str, message: str, recovery: str = "", field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery
        self.field = field

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recovery": self.recovery,
            "field": self.field,
        }


# --------------------------------------------------------------------------- #
# Time / identifiers
# --------------------------------------------------------------------------- #
def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short_hash(text: str, length: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:length]


def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


# --------------------------------------------------------------------------- #
# Filesystem helpers
# --------------------------------------------------------------------------- #
def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    if not p.is_dir():
        raise ToolError("output-not-directory", f"Output path is not a directory: {p}",
                        "Choose a different --output path.")
    return p


def read_text(path: str | Path, max_bytes: int = 8 * 1024 * 1024) -> str:
    p = Path(path)
    if not p.exists():
        raise ToolError("input-missing", f"Input file does not exist: {p}",
                        "Check the path and try again.", str(p))
    if p.is_dir():
        raise ToolError("input-is-directory", f"Expected a file but got a directory: {p}",
                        "Pass a file, or use a directory scanner capability.", str(p))
    size = p.stat().st_size
    if size > max_bytes:
        raise ToolError("input-too-large", f"Input is {size} bytes; limit is {max_bytes} bytes.",
                        "Split the input or raise --max-bytes.", str(p))
    data = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ToolError("input-encoding", f"Input is not valid UTF-8: {p}",
                    "Convert the file to UTF-8 and retry.", str(p))


def read_json(path: str | Path, max_bytes: int = 8 * 1024 * 1024) -> Any:
    text = read_text(path, max_bytes)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError("input-invalid-json", f"Invalid JSON in {path}: {exc}",
                        "Fix the JSON syntax (line/column in the message) and retry.", str(path))


def read_csv(path: str | Path, max_bytes: int = 8 * 1024 * 1024) -> tuple[list[dict[str, str]], list[str]]:
    text = read_text(path, max_bytes)
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader, start=2):
        clean = {}
        for k, v in row.items():
            if k is None:
                continue
            clean[k.strip()] = (v or "").strip()
        rows.append(clean)
    return rows, list(reader.fieldnames or [])


def write_text(path: str | Path, text: str) -> Path:
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def write_json(path: str | Path, obj: Any) -> Path:
    return write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def iter_files(root: str | Path, suffixes: Iterable[str], max_files: int = 200,
               max_bytes: int = 8 * 1024 * 1024) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root] if root.suffix.lower() in set(suffixes) else []
    allowed = {s.lower() for s in suffixes}
    found: list[Path] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__"))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() not in allowed:
                continue
            try:
                if p.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            found.append(p)
            total += 1
            if total >= max_files:
                return found
    return found


def file_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    mapping = {
        ".html": "html", ".htm": "html", ".xhtml": "html",
        ".css": "css", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".vue": "html",
        ".svelte": "html", ".json": "json", ".csv": "csv", ".md": "markdown",
        ".txt": "text", ".vtt": "captions", ".srt": "captions",
        ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".ogg": "audio",
        ".docx": "ooxml", ".xlsx": "ooxml", ".pptx": "ooxml",
        ".pdf": "pdf", ".epub": "epub", ".yml": "yaml", ".yaml": "yaml",
    }
    return mapping.get(suffix, "unknown")


def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# --------------------------------------------------------------------------- #
# Minimal JSON Schema validator (subset used by this tool)
# --------------------------------------------------------------------------- #
class SchemaValidator:
    """Validate a small, explicit JSON Schema subset.

    Supported keywords: type, required, properties, additionalProperties,
    enum, pattern, minLength, maxLength, minimum, maximum, items, oneOf,
    anyOf, description, default. Unknown keywords are ignored.
    """

    def __init__(self, schema: dict[str, Any]):
        self.schema = schema or {}

    def validate(self, instance: Any, path: str = "$") -> list[str]:
        errors: list[str] = []
        self._validate(instance, self.schema, path, errors)
        return errors

    def _validate(self, value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
        if "oneOf" in schema:
            options = schema["oneOf"]
            for option in options:
                if not self._validate_quiet(value, option):
                    continue
                return
            errors.append(f"{path}: value does not match any allowed schema variant")
            return
        if "anyOf" in schema:
            if not any(self._validate_quiet(value, option) for option in schema["anyOf"]):
                errors.append(f"{path}: value does not match any allowed type")
            return
        expected = schema.get("type")
        if expected is not None:
            types = expected if isinstance(expected, list) else [expected]
            ok = False
            for t in types:
                if t == "object" and isinstance(value, dict):
                    ok = True
                elif t == "array" and isinstance(value, list):
                    ok = True
                elif t == "string" and isinstance(value, str):
                    ok = True
                elif t == "integer" and isinstance(value, int) and not isinstance(value, bool):
                    ok = True
                elif t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
                    ok = True
                elif t == "boolean" and isinstance(value, bool):
                    ok = True
                elif t == "null" and value is None:
                    ok = True
            if not ok:
                errors.append(f"{path}: expected {types}, got {type(value).__name__}")
                return
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: must be one of {schema['enum']}")
        if isinstance(value, str):
            if "pattern" in schema and not re.search(schema["pattern"], value):
                errors.append(f"{path}: does not match pattern {schema['pattern']}")
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"{path}: shorter than {schema['minLength']} characters")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: below minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: above maximum {schema['maximum']}")
        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"{path}.{key}: required property is missing")
            props = schema.get("properties", {})
            for key, sub in props.items():
                if key in value:
                    self._validate(value[key], sub, f"{path}.{key}", errors)
            if schema.get("additionalProperties") is False:
                extra = set(value) - set(props)
                if extra:
                    errors.append(f"{path}: unexpected properties {sorted(extra)}")
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                self._validate(item, schema["items"], f"{path}[{i}]", errors)

    def _validate_quiet(self, value: Any, schema: dict[str, Any]) -> bool:
        return not self._validate(value, schema, "$", [])


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "tool": TOOL_NAME,
    "locale": {
        "default": "en-PH",
        "source": "en",
        "targets": ["fil-PH", "en-PH"],
        "fallback_chain": ["fil-PH", "en-PH", "en"],
        "time_zone": "Asia/Manila",
        "currency": "PHP",
        "date_format": "YYYY-MM-DD",
        "number_format": "en-PH",
    },
    "standards": {
        "wcag": "2.2",
        "levels": ["A", "AA"],
        "documents": ["PDF/UA-1", "WCAG 2.2", "EPUB 3"],
        "localization": ["ISO 17100", "ASTM F2575", "WCAG 2.2 3.1.x"],
    },
    "limits": {
        "max_findings": 500,
        "max_files": 200,
        "max_bytes": 8388608,
        "timeout_seconds": 60,
        "max_text_chars": 200000,
        "max_rows": 5000,
    },
    "paths": {
        "output_dir": "output",
        "artifacts_dir": "artifacts",
        "cache_dir": ".accessibility-localization-cache",
    },
    "privacy": {
        "redact_pii": True,
        "retain_recordings": False,
        "allow_external_services": False,
    },
    "approval": {
        "require_for": ["publish", "delete", "credential", "financial", "security"],
        "default": "deny",
    },
    "organization": {
        "name": "The organization",
        "scope": "digital products and content",
        "owner": "Accessibility lead",
        "feedback_channel": "accessibility@example.org",
        "response_days": 5,
        "escalation_contact": "Accessibility lead",
    },
    "release_gate": {
        "block_on": ["critical", "serious"],
        "allow_with_exception": True,
        "max_exception_days": 90,
        "require_owner": True,
    },
    "report": {
        "include_evidence": True,
        "include_remediation": True,
        "include_protocol": True,
        "markdown": True,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = deep_merge(DEFAULT_CONFIG, data or {})

    @classmethod
    def load(cls, path: str | Path | None, schema: dict[str, Any] | None = None) -> "Config":
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            raise ToolError("config-missing", f"Config file not found: {p}",
                            "Create one with `config --init` or omit --config.", str(p))
        data = read_json(p)
        if not isinstance(data, dict):
            raise ToolError("config-invalid", "Config root must be a JSON object.",
                            "Fix the config file and retry.", str(p))
        if schema is not None:
            errors = SchemaValidator(schema).validate(data)
            if errors:
                raise ToolError("config-invalid", "Config validation failed: " + "; ".join(errors[:6]),
                                "Fix the listed config fields and retry.", str(p))
        return cls(data)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def limit(self, name: str) -> int:
        value = int(self.get(f"limits.{name}", DEFAULT_CONFIG["limits"][name]))
        return max(1, value)

    def timeout(self) -> int:
        return max(1, int(self.get("limits.timeout_seconds", 60)))

    def redact(self, text: str) -> str:
        if not self.get("privacy.redact_pii", True):
            return text
        text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[email]", text)
        text = re.sub(r"\b(?:\+63|0)\d{9,12}\b", "[phone]", text)
        text = re.sub(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", "[card]", text)
        return text


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    check: str
    severity: str
    title: str
    description: str = ""
    location: str = ""
    evidence: str = ""
    wcag: str = ""
    remediation: str = ""
    capability: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "evidence": self.evidence,
            "wcag": self.wcag,
            "remediation": self.remediation,
        }


def finding(check: str, severity: str, title: str, **kwargs: Any) -> Finding:
    return Finding(check=check, severity=severity, title=title, **kwargs)


def normalize_findings(items: Iterable[Finding | dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Finding):
            data = item.as_dict()
        elif isinstance(item, dict):
            data = {
                "check": str(item.get("check", "general")),
                "severity": str(item.get("severity", "info")).lower(),
                "title": str(item.get("title", "Finding")),
                "description": str(item.get("description", "")),
                "location": str(item.get("location", "")),
                "evidence": str(item.get("evidence", "")),
                "wcag": str(item.get("wcag", "")),
                "remediation": str(item.get("remediation", "")),
            }
        else:
            continue
        if data["severity"] not in SEVERITY_RANK:
            data["severity"] = "info"
        out.append(data)
    out.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9), f["check"], f["location"]))
    return out


def status_from_findings(findings: list[dict[str, Any]]) -> str:
    severities = {f["severity"] for f in findings}
    if severities & {"critical", "serious"}:
        return "failed"
    if severities & {"moderate", "minor"}:
        return "attention"
    return "passed"


def count_by_severity(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Timeout / cancellation
# --------------------------------------------------------------------------- #
class TimeoutGuard:
    """Bound a synchronous operation. POSIX signal based; no-op elsewhere."""

    def __init__(self, seconds: int):
        self.seconds = max(1, int(seconds))
        self._armed = False

    def __enter__(self) -> "TimeoutGuard":
        if self.seconds and hasattr(signal, "SIGALRM"):
            try:
                signal.signal(signal.SIGALRM, self._handler)
                signal.alarm(self.seconds)
                self._armed = True
            except (ValueError, AttributeError):
                self._armed = False
        return self

    def _handler(self, signum, frame):  # noqa: ANN001
        raise ToolError("timeout", f"Operation exceeded {self.seconds}s.",
                        "Increase --timeout or reduce the input size.")

    def __exit__(self, *exc) -> None:
        if self._armed and hasattr(signal, "SIGALRM"):
            signal.alarm(0)


def run_bounded(func, timeout: int, *args, **kwargs):
    with TimeoutGuard(timeout):
        return func(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #
class Markdown:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def h(self, level: int, text: str) -> None:
        self.lines.append(f"{'#' * level} {text}")
        self.lines.append("")

    def p(self, text: str = "") -> None:
        self.lines.append(text)
        self.lines.append("")

    def bullets(self, items: Iterable[str]) -> None:
        for item in items:
            self.lines.append(f"- {item}")
        self.lines.append("")

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if not rows:
            self.lines.append("_No rows._")
            self.lines.append("")
            return
        self.lines.append("| " + " | ".join(headers) + " |")
        self.lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
            self.lines.append("| " + " | ".join(cells) + " |")
        self.lines.append("")

    def code(self, text: str, lang: str = "") -> None:
        self.lines.append(f"```{lang}".rstrip())
        self.lines.extend(text.splitlines())
        self.lines.append("```")
        self.lines.append("")

    def render(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"


def render_report(envelope: dict[str, Any]) -> str:
    md = Markdown()
    cap = envelope.get("capability", {})
    md.h(1, f"{cap.get('name', 'Accessibility and localization')}")
    md.p(f"**Skill Tool:** `{TOOL_NAME}` · **Capability ID:** `{cap.get('id','')}` · "
         f"**Status:** `{envelope.get('status','')}` · **Generated:** {envelope.get('generated_at','')}")
    md.p(envelope.get("summary", ""))
    md.h(2, "Result")
    counts = envelope.get("metrics", {}).get("severity_counts", {})
    md.table(["Severity", "Count"], [[s, counts.get(s, 0)] for s in SEVERITIES])
    md.h(2, "Findings")
    findings = envelope.get("findings", [])
    if findings:
        md.table(["Severity", "Check", "Finding", "Location", "WCAG"],
                 [[f["severity"], f["check"], f["title"], f.get("location", ""), f.get("wcag", "")]
                  for f in findings])
        for f in findings:
            if f.get("description") or f.get("remediation"):
                md.h(3, f"{f['title']} ({f['severity']})")
                if f.get("description"):
                    md.p(f["description"])
                if f.get("evidence"):
                    md.p(f"**Evidence:** `{f['evidence']}`")
                if f.get("remediation"):
                    md.p(f"**Remediation:** {f['remediation']}")
    else:
        md.p("_No findings were produced by this run._")
    protocol = envelope.get("protocol")
    if protocol:
        md.h(2, "Execution protocol")
        if protocol.get("objective"):
            md.p(f"**Objective:** {protocol['objective']}")
        if protocol.get("roles"):
            md.p("**Roles:** " + ", ".join(protocol["roles"]))
        if protocol.get("steps"):
            md.bullets([f"{i}. {s}" for i, s in enumerate(protocol["steps"], 1)])
        if protocol.get("pass_criteria"):
            md.p("**Pass criteria:** " + "; ".join(protocol["pass_criteria"]))
        if protocol.get("fail_criteria"):
            md.p("**Fail criteria:** " + "; ".join(protocol["fail_criteria"]))
        if protocol.get("evidence"):
            md.p("**Evidence to retain:** " + "; ".join(protocol["evidence"]))
        if protocol.get("human_review"):
            md.p(f"**Human review:** {protocol['human_review']}")
    if envelope.get("artifacts"):
        md.h(2, "Artifacts")
        md.table(["Path", "Kind", "Purpose"],
                 [[a.get("path", ""), a.get("kind", ""), a.get("description", "")] for a in envelope["artifacts"]])
    if envelope.get("limitations"):
        md.h(2, "Limitations")
        md.bullets(envelope["limitations"])
    if envelope.get("next_steps"):
        md.h(2, "Next steps")
        md.bullets(envelope["next_steps"])
    if envelope.get("config"):
        md.h(2, "Configuration used")
        md.code(json.dumps(envelope["config"], indent=2, ensure_ascii=False), "json")
    return md.render()


# --------------------------------------------------------------------------- #
# Output envelope
# --------------------------------------------------------------------------- #
def make_envelope(capability: dict[str, Any], status: str, summary: str,
                  findings: list[dict[str, Any]] | None = None,
                  config: dict[str, Any] | None = None,
                  input_info: dict[str, Any] | None = None,
                  protocol: dict[str, Any] | None = None,
                  artifacts: list[dict[str, Any]] | None = None,
                  limitations: list[str] | None = None,
                  next_steps: list[str] | None = None,
                  metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    findings = normalize_findings(findings or [])
    metrics = dict(metrics or {})
    metrics.setdefault("severity_counts", count_by_severity(findings))
    metrics.setdefault("finding_count", len(findings))
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": utcnow(),
        "capability": {
            "id": capability.get("id", ""),
            "name": capability.get("name", ""),
            "category": capability.get("category", ""),
            "category_title": capability.get("category_title", ""),
            "operation": capability.get("operation", ""),
            "status": capability.get("status", ""),
        },
        "status": status,
        "summary": summary,
        "input": input_info or {},
        "findings": findings,
        "metrics": metrics,
        "protocol": protocol,
        "artifacts": artifacts or [],
        "limitations": limitations or [],
        "next_steps": next_steps or [],
        "config": config or {},
    }


def error_envelope(capability: dict[str, Any], error: ToolError, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": utcnow(),
        "capability": {
            "id": capability.get("id", ""),
            "name": capability.get("name", ""),
            "category": capability.get("category", ""),
            "category_title": capability.get("category_title", ""),
            "operation": capability.get("operation", ""),
            "status": capability.get("status", ""),
        },
        "status": "error",
        "summary": error.message,
        "error": error.as_dict(),
        "input": {},
        "findings": [],
        "metrics": {"severity_counts": count_by_severity([]), "finding_count": 0},
        "protocol": None,
        "artifacts": [],
        "limitations": [],
        "next_steps": [error.recovery] if error.recovery else [],
        "config": config or {},
    }


def output_paths(output: str | Path, capability_id: str, config: Config) -> tuple[Path, Path, Path]:
    base = Path(output)
    if base.suffix.lower() == ".json":
        directory = base.parent if str(base.parent) else Path(".")
        stem = base.stem
    else:
        directory = base
        stem = capability_id
    ensure_dir(directory)
    artifacts_dir = directory / config.get("paths.artifacts_dir", "artifacts")
    return directory / f"{stem}.json", directory / f"{stem}.md", artifacts_dir


def write_outputs(envelope: dict[str, Any], output: str | Path, config: Config,
                  markdown: bool = True) -> tuple[Path, Path | None]:
    json_path, md_path, _ = output_paths(output, envelope["capability"]["id"], config)
    write_json(json_path, envelope)
    written_md: Path | None = None
    if markdown:
        written_md = write_text(md_path, render_report(envelope))
    return json_path, written_md


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #
def parse_key_value(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def temp_dir(prefix: str = "accessibility-localization-") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def cleanup(path: str | Path) -> None:
    p = Path(path)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def now_perf() -> float:
    return time.perf_counter()
