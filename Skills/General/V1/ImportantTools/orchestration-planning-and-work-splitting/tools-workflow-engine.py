"""Core engine for the Orchestration, Planning, and Work Splitting Skill Tool.

Standard library only. No network, no subprocess, no eval, no shell execution.
This module is imported by tools-work-splitter.py (primary CLI), tools-catalog.py,
and tools-selftest.py. It owns state, validation, scoring, planning, gates,
assembly, recovery, limits, retries, cancellation and cleanup.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"
TOOL_DIR = Path(__file__).resolve().parent
FOLDER_NAME = TOOL_DIR.name

CRITICAL_KINDS = ("verification", "integration", "gate")


class ToolError(Exception):
    """Structured, machine-readable failure."""

    def __init__(self, code, message, field=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details = details or {}

    def as_dict(self):
        d = {"code": self.code, "message": self.message}
        if self.field:
            d["field"] = self.field
        if self.details:
            d["details"] = self.details
        return d


class Cancelled(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value):
    s = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return s or "capability"


# --------------------------------------------------------------------------
# References: everything internal resolves relative to the future skill root.
# --------------------------------------------------------------------------
_UNSAFE_REF = re.compile(r"(^[A-Za-z]:)|(://)|(\.\.)|(\\)")


def resolve_reference(ref, must_exist=True):
    """Resolve an internal skill-root-relative reference such as
    'orchestration-planning-and-work-splitting/asset-skill-catalog.json'.

    Tries: skill root (parent of the tool folder), the tool folder itself,
    and the current working directory. Never follows parent traversal,
    absolute paths, drive letters, backslashes or URLs.
    """
    if not isinstance(ref, str) or not ref.strip():
        raise ToolError("ERR_REF_EMPTY", "internal reference must be a non-empty string", "reference")
    ref = ref.strip()
    if ref.startswith("/") or _UNSAFE_REF.search(ref):
        raise ToolError(
            "ERR_REF_UNSAFE",
            "internal references must be relative to the future skill root; "
            "absolute paths, drive letters, URLs, backslashes and parent traversal are rejected",
            "reference",
            {"value": ref},
        )
    parts = ref.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ToolError("ERR_REF_UNSAFE", "internal reference contains an unsafe path segment", "reference", {"value": ref})
    basename = parts[-1]
    candidates = [
        TOOL_DIR.parent / ref,   # future skill root / <folder>/asset-x
        TOOL_DIR / ref,          # when invoked from inside the skill root
        TOOL_DIR / basename,     # sibling file fallback
        Path.cwd() / ref,
        Path.cwd() / basename,
    ]
    tried = []
    for c in candidates:
        p = str(c)
        if p not in tried:
            tried.append(p)
        if c.exists():
            return c
    if must_exist:
        raise ToolError(
            "ERR_REF_MISSING",
            "referenced resource does not resolve from the future skill root",
            "reference",
            {"value": ref, "tried": tried},
        )
    return candidates[0]


# --------------------------------------------------------------------------
# Limits, deadlines, retries, cancellation, cleanup
# --------------------------------------------------------------------------
class Budget:
    """Wall-clock deadline + cooperative cancellation via an optional cancel file."""

    def __init__(self, timeout_seconds=30, cancel_file=None):
        self.started = time.monotonic()
        self.timeout = float(timeout_seconds)
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self._temp_files = []

    @property
    def elapsed(self):
        return round(time.monotonic() - self.started, 4)

    @property
    def remaining(self):
        return max(0.0, self.timeout - (time.monotonic() - self.started))

    def check(self, where="operation"):
        if self.cancel_file is not None and self.cancel_file.exists():
            raise Cancelled("cancellation requested via cancel file: %s" % self.cancel_file)
        if time.monotonic() - self.started > self.timeout:
            raise Cancelled("operation exceeded timeout of %.2fs at %s" % (self.timeout, where))

    def track_temp(self, path):
        self._temp_files.append(Path(path))

    def cleanup(self):
        removed = []
        for p in self._temp_files:
            try:
                if p.exists():
                    p.unlink()
                    removed.append(str(p))
            except OSError:
                pass
        self._temp_files = []
        return removed

    def as_dict(self):
        return {
            "timeout_seconds": self.timeout,
            "elapsed_seconds": self.elapsed,
            "remaining_seconds": round(self.remaining, 4),
            "cancel_file": str(self.cancel_file) if self.cancel_file else None,
            "cleanup_on_exit": True,
        }


def with_retries(fn, budget, retries=2, backoff=0.05):
    """Retry transient OSError/ValueError a bounded number of times."""
    last = None
    for attempt in range(int(retries) + 1):
        budget.check("retryable I/O")
        try:
            return fn()
        except (OSError, ValueError) as exc:
            last = exc
            if attempt >= int(retries):
                break
            time.sleep(min(float(backoff) * (2 ** attempt), 0.5))
    raise ToolError(
        "ERR_IO_RETRY",
        "operation failed after %d attempt(s): %s" % (int(retries) + 1, last),
        details={"attempts": int(retries) + 1, "error": str(last)},
    )


# --------------------------------------------------------------------------
# JSON schema validation (small, real subset used by asset-state-schema.json)
# --------------------------------------------------------------------------
def validate_schema(value, schema, path="$"):
    """Validate against the supported JSON Schema subset and return error strings.

    Supported keywords: type, required, properties, additionalProperties,
    items, enum, pattern, minLength, maxLength, minimum, maximum,
    minItems, maxItems.
    """
    errors = []
    if not isinstance(schema, dict):
        return errors
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else ([expected] if expected else [])
    ok = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if types and not any(ok.get(t, lambda v: True)(value) for t in types):
        return ["%s: expected type %s, got %s" % (path, "/".join(types), type(value).__name__)]
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: value not in enum %s" % (path, schema["enum"]))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: shorter than minLength %s" % (path, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append("%s: longer than maxLength %s" % (path, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: does not match pattern %s" % (path, schema["pattern"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: below minimum %s" % (path, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append("%s: above maximum %s" % (path, schema["maximum"]))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing required property '%s'" % (path, key))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errors.extend(validate_schema(value[key], sub, "%s.%s" % (path, key)))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append("%s: additional property '%s' is not allowed" % (path, key))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: fewer than minItems %s" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: more than maxItems %s" % (path, schema["maxItems"]))
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors.extend(validate_schema(item, schema["items"], "%s[%d]" % (path, i)))
    return errors


# --------------------------------------------------------------------------
# Config and file I/O
# --------------------------------------------------------------------------
DEFAULT_CONFIG_REF = "%s/tools-default-config.json" % FOLDER_NAME


def load_config(path=None, budget=None):
    ref = path or DEFAULT_CONFIG_REF
    # User-supplied absolute paths are allowed for runtime configuration;
    # all internal references must be skill-root-relative.
    if isinstance(ref, str) and Path(ref).is_absolute():
        p = Path(ref)
        if not p.is_file():
            raise ToolError("ERR_CONFIG_MISSING", "config file does not exist: %s" % p, "config")
    else:
        p = resolve_reference(ref)
    budget = budget or Budget(30)
    data = with_retries(lambda: json.loads(p.read_text(encoding="utf-8")), budget, 2, 0.05)
    if not isinstance(data, dict):
        raise ToolError("ERR_CONFIG", "config must be a JSON object", "config")
    for section in ("scoring", "stages", "limits", "approval", "continuation", "gates", "storage", "catalog", "output"):
        if section not in data:
            raise ToolError("ERR_CONFIG", "config is missing section '%s'" % section, "config")
    lim = data["limits"]
    if lim.get("timeout_seconds", 0) <= 0 or lim.get("max_stages", 0) < 1:
        raise ToolError("ERR_CONFIG", "config limits are invalid", "config")
    return data


def read_json(path, budget, max_bytes=2_000_000, label="input"):
    p = Path(path)
    if not p.exists():
        raise ToolError("ERR_INPUT_MISSING", "%s file does not exist: %s" % (label, p), "input")
    if not p.is_file():
        raise ToolError("ERR_INPUT_NOT_FILE", "%s path is not a file: %s" % (label, p), "input")
    size = p.stat().st_size
    if size > max_bytes:
        raise ToolError("ERR_INPUT_TOO_LARGE", "%s is %d bytes, limit is %d" % (label, size, max_bytes), "input", {"size": size, "limit": max_bytes})
    try:
        text = with_retries(lambda: p.read_text(encoding="utf-8"), budget, 2, 0.05)
    except ToolError:
        raise
    except UnicodeDecodeError as exc:
        raise ToolError("ERR_INPUT_ENCODING", "%s is not valid UTF-8: %s" % (label, exc), "input")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError("ERR_INPUT_JSON", "%s is not valid JSON: %s" % (label, exc), "input", {"line": exc.lineno, "column": exc.colno})


def read_text(path, budget, max_bytes=2_000_000, label="input"):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ToolError("ERR_INPUT_MISSING", "%s file does not exist: %s" % (label, p), "input")
    size = p.stat().st_size
    if size > max_bytes:
        raise ToolError("ERR_INPUT_TOO_LARGE", "%s is %d bytes, limit is %d" % (label, size, max_bytes), "input", {"size": size, "limit": max_bytes})
    text = with_retries(lambda: p.read_text(encoding="utf-8"), budget, 2, 0.05)
    return text


def write_json(path, data, budget, retries=2, backup=False, overwrite_requires_approval=False, approved=False, backoff=0.05):
    """Atomic write (temp file in the destination directory + os.replace)."""
    p = Path(path)
    if p.exists() and overwrite_requires_approval and not approved:
        raise ToolError(
            "ERR_APPROVAL_REQUIRED",
            "overwriting an existing output requires explicit approval (--approve)",
            "output",
            {"path": str(p)},
        )
    parent = p.parent if str(p.parent) else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if len(payload.encode("utf-8")) > 5_000_000:
        raise ToolError("ERR_OUTPUT_TOO_LARGE", "generated output exceeds max_output_bytes", "output")
    fd, tmp = tempfile.mkstemp(prefix=".ws-", suffix=".tmp", dir=str(parent))
    os.close(fd)
    tmppath = Path(tmp)
    budget.track_temp(tmppath)
    try:
        with_retries(lambda: tmppath.write_text(payload, encoding="utf-8"), budget, retries, backoff)
        if backup and p.exists():
            with_retries(lambda: Path(str(p) + ".bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8"), budget, retries, backoff)
        with_retries(lambda: os.replace(str(tmppath), str(p)), budget, retries, backoff)
    finally:
        if tmppath.exists():
            try:
                tmppath.unlink()
            except OSError:
                pass
    return {"path": str(p), "bytes": len(payload.encode("utf-8")), "atomic": True, "backup": bool(backup and Path(str(p) + ".bak").exists())}


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def validate_project(raw, config):
    """Validate and normalize a work request. Raises ToolError with all defects."""
    lim = config["limits"]
    errors = []
    if not isinstance(raw, dict):
        raise ToolError("ERR_PROJECT_TYPE", "project must be a JSON object", "input")
    goal = raw.get("goal") or raw.get("title") or raw.get("request")
    if not isinstance(goal, str) or len(goal.strip()) < 3:
        errors.append("goal/title/request must be a string of at least 3 characters")
    if len(goal or "") > 5000:
        errors.append("goal exceeds 5000 characters")
    reqs = raw.get("requirements", [])
    if not isinstance(reqs, list):
        errors.append("requirements must be an array")
        reqs = []
    if len(reqs) > lim["max_requirements"]:
        errors.append("requirements exceed max_requirements (%d)" % lim["max_requirements"])
    req_out = []
    for i, r in enumerate(reqs):
        if isinstance(r, str):
            item = {"id": "R%03d" % (i + 1), "text": r, "critical": False}
        elif isinstance(r, dict) and isinstance(r.get("text"), str):
            item = {"id": str(r.get("id") or ("R%03d" % (i + 1))), "text": r["text"], "critical": bool(r.get("critical", False))}
        else:
            errors.append("requirements[%d] must be a string or {id,text,critical}" % i)
            continue
        if not _SAFE_NAME.match(item["id"]):
            errors.append("requirements[%d].id contains unsafe characters" % i)
        if len(item["text"]) > 2000:
            errors.append("requirements[%d].text exceeds 2000 characters" % i)
        req_out.append(item)
    ids = [r["id"] for r in req_out]
    if len(ids) != len(set(ids)):
        errors.append("requirement ids must be unique")

    files = raw.get("files", [])
    if not isinstance(files, list):
        errors.append("files must be an array")
        files = []
    file_out = []
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not isinstance(f.get("name"), str):
            errors.append("files[%d] must be an object with a name" % i)
            continue
        name = f["name"]
        if not _SAFE_NAME.match(name) or "/" in name or "\\" in name:
            errors.append("files[%d].name is not a safe portable filename: %r" % (i, name))
        size = f.get("size_bytes", 0)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append("files[%d].size_bytes must be a non-negative integer" % i)
        file_out.append({
            "name": name,
            "size_bytes": size if isinstance(size, int) else 0,
            "present": bool(f.get("present", True)),
            "readable": bool(f.get("readable", True)),
            "corrupt": bool(f.get("corrupt", False)),
            "stale": bool(f.get("stale", False)),
            "replaced": bool(f.get("replaced", False)),
            "intended_use": str(f.get("intended_use", "unspecified")),
        })

    packages = raw.get("work_packages", [])
    if not isinstance(packages, list):
        errors.append("work_packages must be an array")
        packages = []
    if len(packages) > lim["max_packages"]:
        errors.append("work_packages exceed max_packages (%d)" % lim["max_packages"])
    pkg_out = []
    for i, p in enumerate(packages):
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip():
            errors.append("work_packages[%d] needs a non-empty name" % i)
            continue
        pid = str(p.get("id") or ("WP%03d" % (i + 1)))
        if not _SAFE_NAME.match(pid):
            errors.append("work_packages[%d].id contains unsafe characters" % i)
        deps = p.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            errors.append("work_packages[%d].depends_on must be an array of ids" % i)
            deps = []
        dur = p.get("duration", 1)
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0 or dur > 1000:
            errors.append("work_packages[%d].duration must be > 0 and <= 1000" % i)
            dur = 1
        pkg_out.append({
            "id": pid,
            "name": p["name"].strip(),
            "depends_on": list(deps),
            "duration": float(dur),
            "stream": str(p.get("stream", "general")),
            "requirement_ids": list(p.get("requirement_ids", [])) if isinstance(p.get("requirement_ids", []), list) else [],
            "inputs": list(p.get("inputs", [])) if isinstance(p.get("inputs", []), list) else [],
            "outputs": list(p.get("outputs", [])) if isinstance(p.get("outputs", []), list) else [],
            "pass_conditions": list(p.get("pass_conditions", [])) if isinstance(p.get("pass_conditions", []), list) else [],
            "critical": bool(p.get("critical", False)),
        })
    pkg_ids = {p["id"] for p in pkg_out}
    for p in pkg_out:
        for d in p["depends_on"]:
            if d not in pkg_ids:
                errors.append("work package %s depends on unknown id %s" % (p["id"], d))
        for r in p["requirement_ids"]:
            if r not in ids:
                errors.append("work package %s references unknown requirement %s" % (p["id"], r))

    numeric = {
        "tool_calls": (raw.get("tool_calls", 1), 0, 10000),
        "uncertainty": (raw.get("uncertainty", 0.2), 0.0, 1.0),
        "verification_burden": (raw.get("verification_burden", 1), 0, 100),
        "context_chars": (raw.get("context_chars", 0), 0, 100_000_000),
        "parallel_streams": (raw.get("parallel_streams", 1), 1, 100),
    }
    vals = {}
    for key, (v, lo, hi) in numeric.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
            errors.append("%s must be a number between %s and %s" % (key, lo, hi))
            vals[key] = lo
        else:
            vals[key] = float(v)
    if errors:
        raise ToolError("ERR_PROJECT_INVALID", "project input failed validation (%d defect(s))" % len(errors), "input", {"defects": errors})
    return {
        "title": str(raw.get("title") or goal)[:300],
        "goal": goal.strip(),
        "requirements": req_out,
        "files": file_out,
        "work_packages": pkg_out,
        "artifacts": [a for a in raw.get("artifacts", []) if isinstance(a, dict)] if isinstance(raw.get("artifacts", []), list) else [],
        **vals,
        "source_format": str(raw.get("source_format", "json")),
    }


def file_inventory(project):
    """File contract: identify absent/unreadable/corrupt/stale/replaced required files."""
    files = project["files"]
    issues = []
    for f in files:
        problems = []
        if not f["present"]:
            problems.append("absent")
        if not f["readable"]:
            problems.append("unreadable")
        if f["corrupt"]:
            problems.append("corrupt")
        if f["stale"]:
            problems.append("stale")
        if f["replaced"]:
            problems.append("replaced")
        if problems:
            issues.append({"name": f["name"], "problems": problems, "intended_use": f["intended_use"]})
    return {
        "count": len(files),
        "files": files,
        "issues": issues,
        "reupload_request": [{"name": i["name"], "problems": i["problems"]} for i in issues],
        "status": "blocked" if issues else "pass",
    }


# --------------------------------------------------------------------------
# Decomposition, dependency graph, critical path, parallel streams
# --------------------------------------------------------------------------
def decompose(project, config):
    units = []
    reqs = project["requirements"]
    pkgs = project["work_packages"]
    if pkgs:
        assigned = set()
        for p in pkgs:
            rid = [r for r in p["requirement_ids"] if r in {q["id"] for q in reqs}]
            assigned.update(rid)
            units.append({
                "id": p["id"], "name": p["name"], "kind": "work",
                "requirement_ids": rid, "depends_on": list(p["depends_on"]),
                "stream": p["stream"], "duration": p["duration"],
                "critical": p["critical"], "inputs": p["inputs"], "outputs": p["outputs"],
                "pass_conditions": p["pass_conditions"] or ["outputs exist and acceptance conditions pass"],
            })
        leftover = [r for r in reqs if r["id"] not in assigned]
        if leftover:
            units.append({
                "id": "WP-REQ", "name": "Requirement coverage package", "kind": "work",
                "requirement_ids": [r["id"] for r in leftover],
                "depends_on": [u["id"] for u in units], "stream": "general", "duration": 1.0,
                "critical": True, "inputs": [], "outputs": ["requirement coverage matrix"],
                "pass_conditions": ["every requirement has an owning stage and evidence"],
            })
    else:
        # Auto-decomposition: one atomic package per requirement, chained in order.
        prev = []
        for i, r in enumerate(reqs or [{"id": "R001", "text": project["goal"], "critical": True}]):
            units.append({
                "id": "WP%03d" % (i + 1), "name": r["text"][:90], "kind": "work",
                "requirement_ids": [r["id"]], "depends_on": prev,
                "stream": "general", "duration": 1.0, "critical": bool(r.get("critical", False)),
                "inputs": [], "outputs": ["%s output" % r["id"]],
                "pass_conditions": ["%s is demonstrably satisfied" % r["id"]],
            })
            prev = [units[-1]["id"]]
    last = [u["id"] for u in units[-1:]]
    units.append({
        "id": "GATE-INTEGRATION", "name": "Integration join and consistency gate", "kind": "gate",
        "requirement_ids": [], "depends_on": [u["id"] for u in units if u["kind"] == "work"],
        "stream": "integration", "duration": 1.0, "critical": True, "inputs": [],
        "outputs": ["joined stage outputs"], "pass_conditions": ["schemas, names and versions are compatible"],
    })
    units.append({
        "id": "VERIFY-FINAL", "name": "Final verification and assembly", "kind": "verification",
        "requirement_ids": [r["id"] for r in reqs], "depends_on": ["GATE-INTEGRATION"],
        "stream": "validation", "duration": 1.0, "critical": True, "inputs": [],
        "outputs": ["verified final package", "completion summary"],
        "pass_conditions": ["requirement coverage passes", "artifact checks pass", "package reopens"],
    })
    units.append({
        "id": "ASSEMBLE", "name": "Final assembly and delivery", "kind": "integration",
        "requirement_ids": [], "depends_on": ["VERIFY-FINAL"], "stream": "integration",
        "duration": 1.0, "critical": True, "inputs": ["verified final package"],
        "outputs": ["polished final package"], "pass_conditions": ["final stage includes assembly and polish"],
    })
    return units


def topological_order(units):
    by_id = {u["id"]: u for u in units}
    indeg = {u["id"]: 0 for u in units}
    adj = {u["id"]: [] for u in units}
    for u in units:
        for d in u["depends_on"]:
            if d in by_id:
                adj[d].append(u["id"])
                indeg[u["id"]] += 1
    ready = sorted([k for k, v in indeg.items() if v == 0])
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(adj[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort()
    if len(order) != len(units):
        stuck = sorted(set(indeg) - set(order))
        raise ToolError("ERR_DEPENDENCY_CYCLE", "dependency graph contains a cycle; cannot schedule", "work_packages", {"units": stuck})
    return order


def dependency_graph(units, order):
    return {
        "nodes": [{"id": u["id"], "name": u["name"], "kind": u["kind"], "critical": u["critical"]} for u in units],
        "edges": [{"from": d, "to": u["id"]} for u in units for d in u["depends_on"] if d in {x["id"] for x in units}],
        "executable_sequence": order,
        "cycle_free": True,
    }


def critical_path(units, config):
    by_id = {u["id"]: u for u in units}
    order = topological_order(units)
    earliest = {u["id"]: 0.0 for u in units}
    predecessor = {}
    for nid in order:
        u = by_id[nid]
        for d in u["depends_on"]:
            if d in earliest and earliest[d] + by_id[d]["duration"] > earliest[nid]:
                earliest[nid] = earliest[d] + by_id[d]["duration"]
                predecessor[nid] = d
    finish = {k: v + by_id[k]["duration"] for k, v in earliest.items()}
    total = max(finish.values(), default=0.0)
    end = max(finish, key=finish.get) if finish else None
    path = []
    while end:
        path.append(end)
        end = predecessor.get(end)
    path.reverse()
    latest_start = {k: total - by_id[k]["duration"] for k in finish}
    slack = {k: round(latest_start[k] - earliest[k], 3) for k in finish}
    threshold = float(config["stages"]["critical_slack_threshold"])
    return {
        "path": path,
        "total_duration": round(total, 3),
        "slack": slack,
        "buffer": round(total * float(config["stages"]["buffer_factor"]), 3),
        "escalation_triggers": [
            {"unit": k, "slack": v, "reason": "slack below configured threshold"}
            for k, v in sorted(slack.items()) if v <= threshold
        ],
    }


def parallel_streams(units, config):
    order = topological_order(units)
    by_id = {u["id"]: u for u in units}
    # A unit may join a stream only when no earlier unit in that stream is a prerequisite.
    streams = {}
    for nid in order:
        u = by_id[nid]
        name = u["stream"] or "general"
        group = streams.setdefault(name, [])
        deps = set(u["depends_on"])
        if any(d in group for d in deps):
            name = "%s-%d" % (name, len([k for k in streams if k.startswith(name + "-")]) + 2)
            group = streams.setdefault(name, [])
        group.append(nid)
    merge_contract = config["stages"].get("merge_contract", {})
    return {
        "streams": [{"name": k, "units": v, "can_run_in_parallel": True} for k, v in sorted(streams.items())],
        "merge_contract": merge_contract,
        "independent_branch_count": len(streams),
    }


# --------------------------------------------------------------------------
# Scoring, estimates, stage building and rebalancing
# --------------------------------------------------------------------------
def score_request(project, units, config):
    w = config["scoring"]["weights"]
    req_n = len(project["requirements"]) or 1
    files = project["files"]
    factors = {
        "scope": min(1.0, req_n / float(config["scoring"]["scope_divisor"])),
        "file_volume": min(1.0, (len(files) + sum(f["size_bytes"] for f in files) / 1_000_000.0) / float(config["scoring"]["file_divisor"])),
        "dependencies": min(1.0, sum(len(u["depends_on"]) for u in units) / float(config["scoring"]["dependency_divisor"])),
        "tool_calls": min(1.0, project["tool_calls"] / float(config["scoring"]["tool_call_divisor"])),
        "uncertainty": float(project["uncertainty"]),
        "verification_burden": min(1.0, project["verification_burden"] / float(config["scoring"]["verification_divisor"])),
        "context_size": min(1.0, project["context_chars"] / float(config["scoring"]["max_context_chars"])),
    }
    score = sum(factors[k] * float(w.get(k, 0.0)) for k in factors)
    score = round(min(1.0, max(0.0, score)), 4)
    threshold = float(config["scoring"]["one_turn_threshold"])
    one_turn = score <= threshold and all(
        factors[k] <= float(config["scoring"].get("hard_factor_ceiling", 0.95)) for k in factors
    )
    reasons = []
    for k, v in sorted(factors.items()):
        reasons.append("%s=%.2f (weight %.2f)" % (k, v, float(w.get(k, 0.0))))
    if one_turn:
        reasons.append("all requested outputs can be created and checked within the single-turn budget")
    else:
        reasons.append("weighted load %.4f exceeds single-turn threshold %.4f" % (score, threshold))
    return {"factors": {k: round(v, 4) for k, v in factors.items()}, "weighted_score": score,
            "threshold": threshold, "one_turn": one_turn, "reasons": reasons}


def estimate_counts(project, units, config):
    s = config["stages"]
    work_units = [u for u in units if u["kind"] == "work"]
    gates = [u for u in units if u["kind"] in ("gate", "verification")]
    groups = math.ceil(len(work_units) / max(1, int(s["units_per_stage"])))
    review_gates = math.ceil(len(gates) / max(1, int(s["gates_per_stage"])))
    file_groups = math.ceil(len(project["files"]) / max(1, int(s["files_per_group"]))) if project["files"] else 0
    integration = 1
    raw = groups + review_gates + file_groups + integration
    recommended = int(min(int(s["max"]), max(int(s["min"]), raw)))
    minimum = int(min(int(s["max"]), max(int(s["min"]), math.ceil(len(units) / max(1, int(s["max_units_per_stage"]))))))
    maximum = int(min(int(s["max"]), max(recommended, len(work_units) + 1)))
    inflated = recommended > len(units)
    too_small = recommended < minimum
    return {
        "recommended": recommended,
        "minimum": minimum,
        "maximum": maximum,
        "basis": {"work_groups": groups, "review_gates": review_gates, "file_groups": file_groups,
                  "integration_steps": integration, "raw_count": raw, "work_units": len(work_units)},
        "checks": {"not_artificially_inflated": not inflated, "safe_for_verification": not too_small},
    }


def _chunk(seq, n):
    n = max(1, int(n))
    if n == 1:
        return [list(seq)]
    size = len(seq)
    base, extra = divmod(size, n)
    out, i = [], 0
    for k in range(n):
        take = base + (1 if k < extra else 0)
        out.append(list(seq[i:i + take]))
        i += take
    return out


def build_stages(units, count, config, preserve_prefix=0, previous_stages=None):
    """Distribute units into `count` stages.

    Dependency order is preserved, final stage always holds verification +
    assembly, and no requirement is dropped. `preserve_prefix` keeps already
    completed stages byte-for-byte (used by expand/compress midstream).
    """
    cfg = config["stages"]
    count = int(count)
    if count < int(cfg["min"]) or count > int(cfg["max"]):
        raise ToolError("ERR_STAGE_COUNT", "stage count must be between %d and %d" % (cfg["min"], cfg["max"]), "stages")
    order = topological_order(units)
    by_id = {u["id"]: u for u in units}
    tail = [by_id[n] for n in order if by_id[n]["kind"] in ("verification", "integration", "gate")]
    head = [by_id[n] for n in order if by_id[n]["kind"] == "work"]
    preserved = []
    if preserve_prefix and previous_stages:
        preserved = [copy.deepcopy(s) for s in previous_stages[:preserve_prefix]]
        claimed = {u["id"] for s in preserved for u in s["units"]}
        head = [u for u in head if u["id"] not in claimed]
        tail = [u for u in tail if u["id"] not in claimed]
    remaining = max(1, count - len(preserved))
    # Last stage owns all critical gates + assembly; earlier stages share work units.
    if remaining == 1:
        chunks = [head]
    else:
        chunks = _chunk(head, remaining - 1)
        chunks.append([])
    stages = []
    for idx, s in enumerate(preserved):
        s["label"] = "Stage %d of %d" % (idx + 1, count)
        s["status"] = "completed"
        stages.append(s)
    for i, chunk in enumerate(chunks):
        num = len(stages) + 1
        units_here = [copy.deepcopy(u) for u in chunk]
        is_final = (i == len(chunks) - 1)
        if is_final:
            units_here.extend(copy.deepcopy(u) for u in tail)
        req_ids = sorted({r for u in units_here for r in u["requirement_ids"]})
        stages.append({
            "number": num,
            "label": "Stage %d of %d" % (num, count),
            "status": "completed" if num <= preserve_prefix else "pending",
            "units": units_here,
            "unit_ids": [u["id"] for u in units_here],
            "requirement_ids": req_ids,
            "entry_criteria": ["prior stage checkpoint exists"] if num > 1 else ["inputs validated"],
            "exit_criteria": ["%s verified" % u["id"] for u in units_here] or ["stage has evidence"],
            "progress_contract": {
                "begin": "Stage %d of %d" % (num, count),
                "work_package": " + ".join(u["name"] for u in units_here) or "no work units",
                "verified": [u["id"] for u in units_here],
                "continuation_state": "recorded at checkpoint",
                "pause_for": "Continue or equivalent instruction",
            },
            "is_final": bool(is_final),
            "includes_assembly_and_polish": bool(is_final and any(u["kind"] == "integration" for u in tail)),
            "is_checkpoint_boundary": True,
        })
    if len(stages) != count:
        # Only possible when preserved prefix consumed more slots than requested;
        # guard rather than silently emitting a mismatched plan.
        raise ToolError("ERR_STAGE_REBALANCE", "cannot fit %d completed stages into %d stages" % (len(preserved), count), "stages")
    owned = [r for s in stages for r in s["requirement_ids"]]
    all_req = sorted({r for u in units for r in u["requirement_ids"]})
    missing = sorted(set(all_req) - set(owned))
    if missing:
        raise ToolError("ERR_REQUIREMENT_LOST", "rebalance would drop requirements", "requirements", {"missing": missing})
    return stages


def tradeoffs(stages, recommended, config):
    n = len(stages)
    per = [len(s["units"]) for s in stages]
    critical = sum(1 for s in stages for u in s["units"] if u["critical"])
    gates = sum(1 for s in stages for u in s["units"] if u["kind"] in CRITICAL_KINDS)
    notes = []
    if n < recommended:
        notes.append("fewer checkpoints than recommended; each stage carries more work (%s units/stage avg)" % round(sum(per) / n, 2))
        notes.append("verification windows are compressed; critical gates retained: %d" % gates)
    elif n > recommended:
        notes.append("more checkpoints than recommended; more approval pauses but finer recovery granularity")
    else:
        notes.append("matches the recommended stage count; balanced verification density")
    return {
        "requested_or_selected": n,
        "recommended": recommended,
        "average_units_per_stage": round(sum(per) / n, 2),
        "units_per_stage": per,
        "critical_units_preserved": critical,
        "verification_gates_preserved": gates,
        "checkpoints": n,
        "notes": notes,
    }


def build_plan(project, stages, units, score, estimate, config):
    order = topological_order(units)
    return {
        "schema_version": SCHEMA_VERSION,
        "requested_stages": len(stages),
        "recommended_stages": estimate["recommended"],
        "minimum_stages": estimate["minimum"],
        "maximum_stages": estimate["maximum"],
        "decision": {"one_turn_feasible": score["one_turn"], "weighted_score": score["weighted_score"], "reasons": score["reasons"]},
        "stages": stages,
        "dependency_graph": dependency_graph(units, order),
        "critical_path": critical_path(units, config),
        "parallel_streams": parallel_streams(units, config),
        "requirement_ownership": {
            r["id"]: [s["number"] for s in stages if r["id"] in s["requirement_ids"]]
            for r in project["requirements"]
        },
        "milestone_gates": [
            {"stage": s["number"], "type": "exit", "criteria": s["exit_criteria"], "status": "pending"}
            for s in stages
        ],
        "tradeoffs": tradeoffs(stages, estimate["recommended"], config),
    }


# --------------------------------------------------------------------------
# Checkpoints, continuation, gates, assembly, recovery
# --------------------------------------------------------------------------
def checkpoint_receipt(state, config, stage_number):
    plan = state["plan"]
    stages = plan["stages"]
    stage = next((s for s in stages if s["number"] == stage_number), None)
    if stage is None:
        raise ToolError("ERR_STAGE_UNKNOWN", "stage %s is not in the current plan" % stage_number, "stage")
    completed = sorted(set(state["completed_stages"] + [stage_number]))
    nxt = min([s["number"] for s in stages if s["number"] not in completed], default=None)
    nxt_stage = next((s for s in stages if s["number"] == nxt), None)
    receipt = {
        "stage": stage["label"],
        "completed_work_packages": stage["unit_ids"],
        "verified": stage["exit_criteria"],
        "requirement_ids": stage["requirement_ids"],
        "continuation_state": {"path": state.get("_path", "configured state path"), "checkpoint_count": len(state["checkpoints"])},
        "next_actions": nxt_stage["unit_ids"] if nxt_stage else ["final delivery"],
        "blockers": state["blockers"],
        "pause_instruction": config["continuation"]["pause_instruction"],
        "final_stage_ready": bool(nxt is None),
    }
    return receipt, completed, nxt


def classify_reply(reply, config):
    c = config["continuation"]
    text = (reply or "").strip().lower()
    if not text:
        return {"recognized": False, "classification": "empty", "reason": "no continuation instruction supplied"}
    phrases = [p.lower() for p in c["phrases"]]
    if any(re.search(r"\b%s\b" % re.escape(p), text) for p in phrases):
        scope_words = [w.lower() for w in c.get("scope_change_words", [])]
        hits = [w for w in scope_words if re.search(r"\b%s\b" % re.escape(w), text)]
        if hits:
            return {"recognized": False, "classification": "scope_changing",
                    "reason": "reply mixes Continue intent with a scope change (%s); use negotiate/expand/compress explicitly" % ", ".join(hits)}
        return {"recognized": True, "classification": "continue", "matched": [p for p in phrases if re.search(r"\b%s\b" % re.escape(p), text)]}
    if any(re.search(r"\b%s\b" % re.escape(w), text) for w in [x.lower() for x in c.get("scope_change_words", [])]):
        return {"recognized": False, "classification": "scope_changing", "reason": "reply changes scope; use negotiate/expand/compress"}
    if "?" in text or len(text.split()) > 12:
        return {"recognized": False, "classification": "ambiguous", "reason": "reply is a question or too long to be a safe Continue signal"}
    return {"recognized": False, "classification": "ambiguous", "reason": "no recognized Continue, proceed, go on, next, or equivalent intent"}


def gate_check(stage, evidence, config, phase="build"):
    rules = config["gates"]
    phase_cfg = rules.get("phases", {}).get(phase, rules.get("phases", {}).get("build", {}))
    required = list(phase_cfg.get("required_evidence_types", ["artifact", "check"]))
    items = evidence if isinstance(evidence, list) else evidence.get("evidence", []) if isinstance(evidence, dict) else []
    if len(items) > int(config["limits"]["max_evidence_items"]):
        return {"status": "blocked", "reason": "evidence exceeds max_evidence_items (%d)" % config["limits"]["max_evidence_items"],
                "required_evidence_types": required, "next_action": "reduce or split the evidence set"}
    if not items:
        return {"status": "blocked", "reason": "no evidence supplied", "required_evidence_types": required, "next_action": "supply concrete, reproducible evidence"}
    seen_types, defects = set(), []
    for i, e in enumerate(items):
        if not isinstance(e, dict):
            defects.append("evidence[%d] must be an object" % i)
            continue
        etype = str(e.get("type", ""))
        seen_types.add(etype)
        if not str(e.get("value", "")).strip():
            defects.append("evidence[%d].value is empty; gate evidence must be concrete" % i)
        if not e.get("reproducible", False):
            defects.append("evidence[%d] is not marked reproducible" % i)
        if e.get("status") not in (None, "pass", "fail"):
            defects.append("evidence[%d].status must be pass or fail" % i)
        if e.get("status") == "fail":
            defects.append("evidence[%d] reports a failed check" % i)
    missing_types = [t for t in required if t not in seen_types]
    if missing_types:
        defects.append("missing required evidence type(s): %s" % ", ".join(missing_types))
    if defects:
        return {"status": "repair" if any("failed check" not in d for d in defects) else "blocked",
                "reason": "gate entry/exit criteria not satisfied", "defects": defects,
                "next_action": "repair the smallest responsible cause and rerun affected checks",
                "required_evidence_types": required, "seen_types": sorted(seen_types)}
    return {"status": "pass", "reason": "concrete, reproducible evidence satisfies all entry and exit criteria",
            "required_evidence_types": required, "seen_types": sorted(seen_types),
            "next_action": "advance to next milestone", "criteria": stage.get("exit_criteria", [])}


def assemble(state, stage_outputs, config):
    """Combine stage outputs, resolve inconsistencies, run coverage + artifact checks."""
    project = state["project"]
    req_ids = {r["id"] for r in project["requirements"]}
    entries = stage_outputs if isinstance(stage_outputs, list) else stage_outputs.get("stage_outputs", [])
    defects, artifacts, covered, verified = [], {}, {}, set()
    allowed = set(config["artifacts"]["allowed_extensions"])
    forbidden = [re.compile(p) for p in config["artifacts"]["forbidden_name_patterns"]]
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            defects.append("stage_outputs[%d] must be an object" % i)
            continue
        st = e.get("stage")
        arts = e.get("artifacts", [])
        if not isinstance(arts, list):
            defects.append("stage_outputs[%d].artifacts must be an array" % i)
            continue
        for a in arts:
            name = str(a.get("name", "")) if isinstance(a, dict) else str(a)
            if not name or not _SAFE_NAME.match(name):
                defects.append("invalid artifact name in stage %s: %r" % (st, name))
                continue
            if any(p.search(name) for p in forbidden):
                defects.append("temporary or forbidden artifact packaged: %s" % name)
                continue
            ext = Path(name).suffix.lower()
            if ext and allowed and ext not in allowed:
                defects.append("artifact extension not allowed: %s" % name)
                continue
            if isinstance(a, dict) and not a.get("non_empty", True):
                defects.append("artifact is empty: %s" % name)
                continue
            if name in artifacts and artifacts[name]["stage"] != st:
                defects.append("naming conflict: %s appears in stages %s and %s" % (name, artifacts[name]["stage"], st))
            artifacts[name] = {"stage": st, "bytes": a.get("bytes", 0) if isinstance(a, dict) else 0}
            for rid in (a.get("requirement_ids", []) if isinstance(a, dict) else []):
                covered.setdefault(rid, []).append(name)
                if rid not in req_ids:
                    defects.append("artifact %s references unknown requirement %s" % (name, rid))
            for v in (a.get("verified", []) if isinstance(a, dict) else []):
                verified.add(str(v))
        for rid in e.get("requirement_ids", []) or []:
            covered.setdefault(rid, []).append("stage %s" % st)
    coverage = []
    for rid in sorted(req_ids):
        ev = covered.get(rid, [])
        v = bool(ev)
        coverage.append({"requirement_id": rid, "evidence": ev, "status": "covered" if v else "missing"})
        if not v:
            defects.append("requirement %s has no artifact or stage evidence" % rid)
    # Cross-reference validation: every referenced file must exist in the inventory.
    inventory_names = {f["name"] for f in project["files"]}
    for e in entries:
        for ref in (e.get("references", []) or []):
            if ref not in inventory_names and ref not in artifacts:
                defects.append("broken reference from stage %s to %s" % (e.get("stage"), ref))
    final_stage = state["plan"]["stages"][-1]
    if not final_stage.get("includes_assembly_and_polish"):
        defects.append("final stage does not include assembly and polish")
    return {
        "status": "pass" if not defects else "repair",
        "artifact_count": len(artifacts),
        "artifacts": [{"name": k, **v} for k, v in sorted(artifacts.items())],
        "requirement_coverage": coverage,
        "requirement_coverage_rate": round(sum(1 for c in coverage if c["status"] == "covered") / max(1, len(coverage)), 4),
        "verified_checks": sorted(verified),
        "defects": defects,
        "final_stage": final_stage["label"],
        "completion_summary": "all stage outputs combined; inconsistencies resolved; coverage and artifact checks passed"
        if not defects else "assembly blocked by %d defect(s)" % len(defects),
    }


def recover_state(state, config):
    """Preserve successful work, isolate the earliest bad state, repair the
    smallest responsible cause, rerun affected checks, update remaining plan."""
    completed = sorted(set(state["completed_stages"]))
    history = state.get("history", [])
    failures = [h for h in history if h.get("status") in ("error", "blocked", "failed")]
    failed_stage = min([h.get("stage") for h in failures if isinstance(h.get("stage"), int)], default=None)
    checkpoints = state.get("checkpoints", [])
    safe = max([c.get("stage", 0) for c in checkpoints], default=0)
    total = len(state["plan"]["stages"])
    isolated = failed_stage if failed_stage is not None else min(max(completed) + 1 if completed else 1, total)
    repaired_to = min(safe, max(completed))
    repaired_to = max(0, repaired_to)
    preserved = [s for s in state["plan"]["stages"] if s["number"] <= repaired_to]
    remaining = [s for s in state["plan"]["stages"] if s["number"] > repaired_to]
    rerun = [criterion for s in remaining for criterion in s["exit_criteria"]]
    return {
        "status": "recovered",
        "earliest_bad_state": {"stage": isolated, "source": "history" if failures else "first incomplete stage"},
        "last_safe_checkpoint": safe,
        "preserved_stages": [s["label"] for s in preserved],
        "discarded_or_requeued_stages": [s["label"] for s in remaining],
        "repair": "restore last safe checkpoint, revalidate file inventory, and re-run only affected checks",
        "rerun_checks": rerun,
        "updated_next_actions": remaining[0]["unit_ids"] if remaining else ["final delivery"],
        "unchanged_failed_action_repeated": False,
    }


# --------------------------------------------------------------------------
# State store
# --------------------------------------------------------------------------
def new_state(project, plan, score, estimate, inventory, config):
    now = utcnow()
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": FOLDER_NAME,
        "created_at": now,
        "updated_at": now,
        "project": project,
        "plan": plan,
        "assessment": {"score": score, "estimate": estimate, "file_inventory": inventory},
        "completed_stages": [],
        "current_stage": 1,
        "checkpoints": [],
        "open_issues": [],
        "decisions": [],
        "blockers": [i["name"] for i in inventory["issues"]],
        "next_actions": plan["stages"][0]["unit_ids"] if plan["stages"] else [],
        "artifacts": [],
        "actions": [],
        "history": [{"at": now, "command": "init", "status": "ok", "stage": 1}],
        "recovery": {"attempts": 0, "last_safe_checkpoint": 0},
    }


class StateStore:
    def __init__(self, path, config, budget):
        self.path = Path(path)
        self.config = config
        self.budget = budget
        self.state = None

    def load(self, create_from=None):
        if not self.path.exists():
            if create_from is None:
                raise ToolError("ERR_STATE_MISSING", "state file does not exist: %s (run plan first)" % self.path, "state")
            self.state = create_from
            self.save()
            return self.state
        data = read_json(self.path, self.budget, self.config["limits"]["max_input_bytes"], "state")
        if not isinstance(data, dict) or "plan" not in data or "project" not in data:
            raise ToolError("ERR_STATE_INVALID", "state file is missing plan/project", "state")
        self.state = data
        self.state["_path"] = str(self.path)
        return self.state

    def require(self):
        if self.state is None:
            raise ToolError("ERR_STATE_MISSING", "load state before use", "state")
        return self.state

    def save(self):
        st = self.require()
        st["_path"] = str(self.path)
        st["updated_at"] = utcnow()
        info = write_json(self.path, st, self.budget,
                          retries=int(self.config["limits"]["retries"]),
                          backup=bool(self.config["storage"]["backup_on_write"]),
                          backoff=float(self.config["limits"]["retry_backoff_seconds"]))
        return info

    def record(self, command, status, stage=None, detail=None):
        st = self.require()
        entry = {"at": utcnow(), "command": command, "status": status}
        if stage is not None:
            entry["stage"] = stage
        if detail:
            entry["detail"] = detail
        st["history"].append(entry)
        if len(st["history"]) > 200:
            st["history"] = st["history"][-200:]

    def checkpoint(self, stage_number, receipt, evidence_summary):
        st = self.require()
        snapshot = copy.deepcopy({
            "plan": st["plan"], "completed_stages": st["completed_stages"],
            "artifacts": st["artifacts"], "blockers": st["blockers"],
        })
        st["checkpoints"] = [c for c in st["checkpoints"] if c["stage"] != stage_number]
        st["checkpoints"].append({
            "stage": stage_number, "at": utcnow(), "verified": receipt["verified"],
            "evidence_summary": evidence_summary, "state_snapshot": snapshot,
        })
        st["checkpoints"].sort(key=lambda c: c["stage"])
