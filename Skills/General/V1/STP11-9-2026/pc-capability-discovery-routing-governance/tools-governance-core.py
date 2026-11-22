#!/usr/bin/env python3
"""Shared governance core for the capability-discovery-routing-governance skill tool.

Provides asset loading, host-aware self-test probing, skill resolution, risk and
approval evaluation, lawful-use gate evaluation, boundary enforcement, hashing,
evidence records, reversible change plans, artifact detection, catalog search,
capability-input parsing, task decomposition, and clean-state validation.

This module performs no network access and embeds no secrets.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_APPROVAL = 3
EXIT_DENIAL = 4
EXIT_FAILURE = 5
EXIT_CANCEL = 130

MAX_ASSET_BYTES = 50 * 1024 * 1024

CATALOG_FILE = "asset-capability-catalog.json"
PROBES_FILE = "asset-selftest-probes.json"
POLICY_FILE = "asset-policy-pack.json"
SCHEMA_FILE = "asset-evidence-schema.json"

STOPWORDS = {
    "the", "a", "an", "of", "and", "to", "in", "for", "on", "with", "or", "is",
    "are", "be", "by", "at", "from", "as", "it", "that", "this", "use", "using",
    "into", "via", "per", "not", "no", "its", "their", "when", "where", "how",
}


class ToolError(Exception):
    """Structured, user-facing failure with an exit code."""

    def __init__(self, message, exit_code=EXIT_USAGE, details=None):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}


# ---------------------------------------------------------------------------
# Structured envelopes
# ---------------------------------------------------------------------------

def ok(command, result, warnings=None):
    return {
        "ok": True,
        "command": command,
        "result": result,
        "errors": [],
        "warnings": list(warnings or []),
    }


def fail(command, message, exit_code, details=None):
    return {
        "ok": False,
        "command": command,
        "result": {},
        "errors": [{"message": message, "exit_code": exit_code, "details": details or {}}],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Time and hashing
# ---------------------------------------------------------------------------

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path, max_bytes=None):
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            read += len(chunk)
            if max_bytes is not None and read > max_bytes:
                raise ToolError(
                    f"file exceeds maximum hashing size of {max_bytes} bytes: {path}",
                    EXIT_USAGE,
                )
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# Host facts
# ---------------------------------------------------------------------------

def host_info():
    system = platform.system().lower()
    if system == "darwin":
        host_type = "macos"
    elif system.startswith("win"):
        host_type = "windows"
    elif system == "linux":
        host_type = "linux"
    else:
        host_type = system or "unknown"
    machine = (platform.machine() or "").lower()
    arch_map = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64",
                "arm64": "arm64", "i386": "x86", "i686": "x86"}
    architecture = arch_map.get(machine, machine or "unknown")
    return {
        "host_type": host_type,
        "architecture": architecture,
        "system": platform.system(),
        "release": platform.release(),
        "python": platform.python_version(),
        "virtualization_available": virtualization_available(host_type),
    }


def virtualization_available(host_type):
    if host_type == "linux":
        return os.path.exists("/dev/kvm")
    if host_type == "windows":
        return True
    return False


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

def parse_version(text):
    if not text:
        return None
    m = re.search(r"\d+(?:\.\d+)*", str(text))
    if not m:
        return None
    return tuple(int(p) for p in m.group().split("."))


def _pad(v, size=4):
    return tuple(list(v) + [0] * (size - len(v)))


def version_at_least(actual, minimum):
    a = parse_version(actual)
    b = parse_version(minimum)
    if a is None or b is None:
        return None
    return _pad(a) >= _pad(b)


# ---------------------------------------------------------------------------
# Asset loading
# ---------------------------------------------------------------------------

class Assets:
    def __init__(self, base_dir=None, catalog=None, probes=None, policy=None, schema=None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.catalog_path = Path(catalog) if catalog else self.base_dir / CATALOG_FILE
        self.probes_path = Path(probes) if probes else self.base_dir / PROBES_FILE
        self.policy_path = Path(policy) if policy else self.base_dir / POLICY_FILE
        self.schema_path = Path(schema) if schema else self.base_dir / SCHEMA_FILE
        self._catalog = None
        self._probes = None
        self._policy = None
        self._schema = None
        self._skill_index = None

    @staticmethod
    def _load_json(path, label):
        path = Path(path)
        if not path.is_file():
            raise ToolError(f"{label} file not found: {path.name}", EXIT_USAGE,
                            {"path": str(path)})
        size = path.stat().st_size
        if size > MAX_ASSET_BYTES:
            raise ToolError(f"{label} file exceeds {MAX_ASSET_BYTES} bytes", EXIT_USAGE)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ToolError(f"{label} file is not valid JSON: {exc}", EXIT_FAILURE)

    @property
    def catalog(self):
        if self._catalog is None:
            data = self._load_json(self.catalog_path, "catalog")
            for key in ("category_count", "skill_count", "categories"):
                if key not in data:
                    raise ToolError("catalog is missing required key: " + key, EXIT_FAILURE)
            total = sum(len(c.get("skills", [])) for c in data["categories"])
            if total != data["skill_count"] or len(data["categories"]) != data["category_count"]:
                raise ToolError("catalog counts do not match contents", EXIT_FAILURE)
            self._catalog = data
        return self._catalog

    @property
    def probes(self):
        if self._probes is None:
            data = self._load_json(self.probes_path, "probes")
            for key in ("contract", "probes"):
                if key not in data:
                    raise ToolError("probes file is missing required key: " + key, EXIT_FAILURE)
            ids = [p.get("id") for p in data["probes"]]
            if not ids or any(not i for i in ids) or len(ids) != len(set(ids)):
                raise ToolError("probe definitions must have unique non-empty ids", EXIT_FAILURE)
            for p in data["probes"]:
                for field in ("test_command", "commands", "kind"):
                    if field not in p:
                        raise ToolError(f"probe {p.get('id')} missing {field}", EXIT_FAILURE)
            self._probes = data
        return self._probes

    @property
    def policy(self):
        if self._policy is None:
            data = self._load_json(self.policy_path, "policy")
            for key in ("approval_levels", "approval_flags", "risk_levels",
                        "risk_keywords", "prohibited", "version_pins",
                        "capability_combinations", "limits", "gate_declarations"):
                if key not in data:
                    raise ToolError("policy pack is missing required key: " + key, EXIT_FAILURE)
            self._policy = data
        return self._policy

    @property
    def schema(self):
        if self._schema is None:
            data = self._load_json(self.schema_path, "schema")
            if "definitions" not in data:
                raise ToolError("schema file is missing definitions", EXIT_FAILURE)
            self._schema = data
        return self._schema

    def skill_index(self):
        """Map skill id -> (category, skill)."""
        if self._skill_index is None:
            index = {}
            for cat in self.catalog["categories"]:
                for skill in cat["skills"]:
                    index[skill["id"]] = (cat, skill)
            self._skill_index = index
        return self._skill_index

    def find_skill(self, skill_id):
        key = str(skill_id).strip().lower()
        hit = self.skill_index().get(key)
        if not hit:
            known = sorted(self.skill_index())
            raise ToolError(f"unknown skill id: {skill_id}", EXIT_USAGE,
                            {"known_id_format": "cNNsNN", "first_ids": known[:5]})
        return hit

    def find_categories(self, selector):
        """Resolve a category selector: number, 'category N', or name substring."""
        sel = str(selector).strip()
        cats = self.catalog["categories"]
        m = re.fullmatch(r"(?:category\s*)?(\d{1,2})", sel, re.I)
        if m:
            num = int(m.group(1))
            for c in cats:
                if c["id"] == num:
                    return [c]
            raise ToolError(f"unknown category number: {num}", EXIT_USAGE)
        needle = sel.lower()
        hits = [c for c in cats if needle in c["name"].lower()]
        if not hits:
            raise ToolError(f"no category matches: {selector}", EXIT_USAGE,
                            {"example_categories": [c["name"] for c in cats[:3]]})
        return hits


# ---------------------------------------------------------------------------
# Minimal schema validation (required fields, types, enums)
# ---------------------------------------------------------------------------

def validate_record(definition, obj, label="record"):
    problems = []
    if definition.get("type") == "object" and not isinstance(obj, dict):
        return [f"{label} must be an object"]
    if not isinstance(obj, dict):
        return [f"{label} must be an object"]
    for key in definition.get("required", []):
        if key not in obj:
            problems.append(f"{label} missing required field: {key}")
    props = definition.get("properties", {})
    for key, sub in props.items():
        if key not in obj:
            continue
        value = obj[key]
        types = sub.get("type")
        if types is None:
            continue
        if isinstance(types, str):
            types = [types]
        if not any(_type_matches(value, t) for t in types):
            problems.append(f"{label}.{key} has wrong type (expected {types})")
        if "enum" in sub and value not in sub["enum"]:
            problems.append(f"{label}.{key} value not in enum: {value!r}")
    return problems


def _type_matches(value, type_name):
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    return True


# ---------------------------------------------------------------------------
# Probe runner
# ---------------------------------------------------------------------------

class ProbeRunner:
    """Runs local probe commands with timeout, retries, and version pinning."""

    def __init__(self, assets: Assets):
        self.assets = assets
        self.probes = {p["id"]: p for p in assets.probes["probes"]}
        self.pins = assets.policy.get("version_pins", {})
        self.limits = assets.policy.get("limits", {})
        self.cache = {}

    def _resolve_command(self, argv):
        return shutil.which(argv[0])

    def run(self, probe_id):
        if probe_id in self.cache:
            return dict(self.cache[probe_id])
        probe = self.probes.get(probe_id)
        if probe is None:
            row = {
                "probe_id": probe_id,
                "status": "MISSING",
                "detected_command": None,
                "detected_version": None,
                "exit_code": None,
                "test_command": None,
                "message": f"no probe definition for {probe_id}",
            }
            self.cache[probe_id] = row
            return dict(row)
        row = self._run_probe(probe)
        self.cache[probe_id] = row
        return dict(row)

    def _run_probe(self, probe):
        probe_id = probe["id"]
        test_command = probe.get("test_command")
        timeout = int(probe.get("timeout_sec") or self.limits.get("probe_timeout_sec", 10))
        retries = int(probe.get("retries", self.limits.get("probe_retries", 1)))
        patterns = probe.get("version_patterns", [])
        pin = self.pins.get(probe_id)
        base = {
            "probe_id": probe_id,
            "test_command": test_command,
            "detected_command": None,
            "detected_version": None,
            "exit_code": None,
            "status": "MISSING",
            "message": "",
        }

        # Presence: first alternative whose binary is on PATH.
        chosen = None
        resolved = None
        for argv in probe["commands"]:
            path = shutil.which(argv[0])
            if path:
                chosen = argv
                resolved = path
                break
        if chosen is None:
            base["message"] = f"command not found on PATH: {probe['commands'][0][0]}"
            return base

        base["detected_command"] = resolved
        env = dict(os.environ)
        env.setdefault("PATH", "/usr/bin:/bin")
        env["LC_ALL"] = "C"
        env["LANG"] = "C"

        if probe.get("kind") == "python-module":
            # The catalog contract probe: import check; failure means MISSING.
            output = ""
            rc = None
            try:
                proc = subprocess.run(
                    list(chosen), capture_output=True, text=True, timeout=timeout,
                    env=env, cwd=tempfile.gettempdir(), errors="replace",
                )
                rc = proc.returncode
                output = (proc.stdout or "") + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                base["status"] = "BROKEN"
                base["message"] = f"module probe timed out after {timeout}s"
                return base
            except OSError as exc:
                base["status"] = "BROKEN"
                base["message"] = f"module probe failed to start: {exc}"
                return base
            base["exit_code"] = rc
            if rc != 0:
                tail = [ln for ln in output.splitlines() if ln.strip()]
                base["status"] = "MISSING"
                base["message"] = "module import failed: " + (tail[-1] if tail else f"exit {rc}")
                return base
            base["status"] = "FOUND"
            base["message"] = "module import succeeded: " + ", ".join(probe.get("module", []))
            return base

        attempts = max(0, retries) + 1
        output = ""
        rc = None
        timed_out = False
        start = datetime.now(timezone.utc)
        for attempt in range(attempts):
            try:
                proc = subprocess.run(
                    list(chosen), capture_output=True, text=True, timeout=timeout,
                    env=env, cwd=tempfile.gettempdir(), errors="replace",
                )
                rc = proc.returncode
                output = (proc.stdout or "") + (proc.stderr or "")
                timed_out = False
                break
            except subprocess.TimeoutExpired:
                timed_out = True
                continue
            except OSError as exc:
                base["status"] = "BROKEN"
                base["message"] = f"probe failed to start: {exc}"
                return base
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        if timed_out:
            base["status"] = "BROKEN"
            base["message"] = f"probe timed out after {timeout}s ({attempts} attempt(s))"
            return base
        base["exit_code"] = rc

        version = None
        for pattern in patterns:
            m = re.search(pattern, output)
            if m:
                version = m.group(1) if m.lastindex else m.group(0)
                break
        base["detected_version"] = version

        if rc != 0 and probe.get("require_success", True):
            tail = [ln for ln in output.splitlines() if ln.strip()]
            base["status"] = "BROKEN"
            base["message"] = (f"probe exited {rc}: " +
                               (tail[-1] if tail else "no output"))[:300]
            return base

        if version and pin:
            ok_version = version_at_least(version, pin)
            if ok_version is False:
                base["status"] = "WRONG VERSION"
                base["message"] = f"detected {version} but policy pin requires >= {pin}"
                return base
            if ok_version is None:
                base["message"] = f"detected {version}; could not compare with pin {pin}"
        if version:
            base["status"] = "FOUND"
            base["message"] = f"found {version} ({duration_ms} ms)"
        else:
            base["status"] = "FOUND"
            if pin:
                base["message"] = f"present but version not parsed; pin >= {pin} unverified"
            else:
                base["message"] = f"present ({duration_ms} ms)"
        return base

    def which_any(self, names):
        for name in names:
            path = shutil.which(name)
            if path:
                return name, path
        return None, None


# ---------------------------------------------------------------------------
# Skill resolution (contract rows)
# ---------------------------------------------------------------------------

def build_requirements_list(req):
    out = []
    for h in req.get("hosts", []):
        out.append(f"host:{h}")
    for t in req.get("tools", []):
        out.append(f"tool:{t}")
    for s in req.get("software", []):
        out.append(f"software:{s}")
    if req.get("network"):
        out.append("network")
    if req.get("gpu"):
        out.append("gpu")
    if req.get("virtualization"):
        out.append("virtualization")
    if req.get("license"):
        out.append("license")
    if req.get("privileged"):
        out.append("privileged")
    return out


def _gate_status(req, host):
    """Return (status, message) when a resource/host gate blocks the capability."""
    hosts = req.get("hosts", ["linux"])
    if "macos" in hosts and host["host_type"] != "macos":
        return ("MACOS HOST REQUIRED",
                "capability delegates to a macOS worker (codesign/notarization lane)")
    if host["host_type"] not in hosts:
        if "windows" in hosts:
            return ("WINDOWS HOST REQUIRED",
                    f"requires a Windows execution host (preferred: {req.get('preferred_host', 'windows')})")
        return ("MISSING", f"requires host in {hosts}, current host is {host['host_type']}")
    if req.get("gpu"):
        return ("GPU REQUIRED",
                "requires a GPU-enabled worker; this run does not verify GPU capability")
    if req.get("virtualization") and not host.get("virtualization_available"):
        return ("VIRTUALIZATION REQUIRED",
                "requires hardware virtualization (for example /dev/kvm) or a virtualization worker")
    if req.get("network"):
        return ("NETWORK REQUIRED",
                "requires a network-enabled worker; this tool performs no network operations")
    if req.get("license"):
        return ("LICENSE REQUIRED",
                "requires a license, credential, or lawfully licensed worker before execution")
    return None, None


def resolve_skill(skill, category, runner, host, evidence_path=None):
    req = skill["requires"]
    warnings = []
    row = {
        "capability_id": skill["id"],
        "status": "FOUND",
        "detected_command": None,
        "detected_version": None,
        "host_type": host["host_type"],
        "architecture": host["architecture"],
        "requirements": build_requirements_list(req),
        "test_command": None,
        "exit_code": None,
        "evidence_path": evidence_path,
        "recommended_action": "",
        "name": skill["name"],
        "category_id": category["id"],
        "category": category["name"],
        "risk_hint": skill.get("risk_hint", "read_only"),
        "hosts": list(req.get("hosts", [])),
        "preferred_host": req.get("preferred_host", "linux"),
        "tool_results": [],
        "warnings": warnings,
    }

    gate_status, gate_message = _gate_status(req, host)
    if gate_status:
        row["status"] = gate_status
        row["recommended_action"] = _gate_action(gate_status, req)
        row["warnings"].append(gate_message)
        return row

    results = []
    for name in req.get("software", []):
        found_name, path = runner.which_any([name] + _software_alts(name))
        results.append({
            "probe_id": f"software:{name}",
            "status": "FOUND" if path else "MISSING",
            "detected_command": path,
            "detected_version": None,
            "exit_code": None,
            "test_command": None,
            "message": f"on PATH as {found_name}" if path else f"not found on PATH: {name}",
        })
    for probe_id in req.get("tools", []):
        results.append(runner.run(probe_id))
    row["tool_results"] = results

    if not results:
        row["status"] = "FOUND"
        row["recommended_action"] = _preferred_note(req, host, "no external tool dependencies")
        return row

    def rank(status):
        return {"BROKEN": 0, "MISSING": 1, "WRONG VERSION": 2}.get(status, 3)

    failing = [r for r in results if r["status"] != "FOUND"]
    if failing:
        failing.sort(key=lambda r: rank(r["status"]))
        worst = failing[0]
        row["status"] = worst["status"]
        row["detected_command"] = worst.get("detected_command")
        row["detected_version"] = worst.get("detected_version")
        row["exit_code"] = worst.get("exit_code")
        row["test_command"] = worst.get("test_command")
        failing_names = ", ".join(
            (r["probe_id"].split(":", 1)[-1]) for r in failing
        )
        if worst["status"] == "MISSING":
            row["recommended_action"] = (
                f"install or provide {failing_names} on PATH, or route to a worker "
                "that provides them"
            )
        elif worst["status"] == "WRONG VERSION":
            tool = worst["probe_id"]
            pin = runner.pins.get(tool)
            row["recommended_action"] = (
                f"upgrade {tool} to >= {pin}, or update version_pins for {tool} in "
                "asset-policy-pack.json"
            )
        else:
            row["recommended_action"] = (
                f"repair {worst['probe_id']}: probe failed ({worst.get('message', '')[:120]})"
            )
        row["warnings"].append(f"blocking dependencies: {failing_names}")
        return row

    row["status"] = "FOUND"
    first = results[0]
    row["detected_command"] = first.get("detected_command")
    row["detected_version"] = first.get("detected_version")
    row["exit_code"] = first.get("exit_code")
    row["test_command"] = first.get("test_command")
    names = ", ".join(r["probe_id"] for r in results)
    row["recommended_action"] = _preferred_note(req, host, f"dependencies ready: {names}")
    return row


def _software_alts(name):
    return {
        "powershell": ["pwsh"],
        "VBoxManage": ["vboxmanage"],
    }.get(name, [])


def _gate_action(status, req):
    preferred = req.get("preferred_host", "windows")
    return {
        "MACOS HOST REQUIRED": "route to a macOS worker (codesign/notarization lane)",
        "WINDOWS HOST REQUIRED": f"route to a Windows worker (preferred host: {preferred})",
        "GPU REQUIRED": "route to a GPU-enabled worker; GPU capability is not verified here",
        "NETWORK REQUIRED": "route to a network-enabled worker; this tool is offline by design",
        "VIRTUALIZATION REQUIRED": "enable hardware virtualization or route to a virtualization worker",
        "LICENSE REQUIRED": "confirm the required license or credential before execution",
        "MISSING": f"route to a worker in {req.get('hosts', [])}",
    }.get(status, "review requirements and route appropriately")


def _preferred_note(req, host, base):
    preferred = req.get("preferred_host", host["host_type"])
    if preferred != host["host_type"] and preferred in req.get("hosts", []):
        return f"{base}; note: preferred host is {preferred}, current host lane is acceptable"
    return base


# ---------------------------------------------------------------------------
# Risk, approval, boundary, gate
# ---------------------------------------------------------------------------

def classify_action(text, policy):
    lowered = (text or "").lower()
    matched = []
    for cls, keywords in policy.get("risk_keywords", {}).items():
        hits = [kw for kw in keywords if kw in lowered]
        if hits:
            matched.append((cls, hits))
    if not matched:
        matched = [("read_only", ["default"])]
    levels = policy["risk_levels"]

    def rank_of(cls):
        return levels.get(cls, {}).get("rank", 0)

    matched.sort(key=lambda pair: rank_of(pair[0]), reverse=True)
    top = matched[0][0]
    return {
        "classes": [
            {"class": cls, "keywords": hits, "rank": rank_of(cls)} for cls, hits in matched
        ],
        "risk_class": top,
        "rank": rank_of(top),
        "approval_required": levels.get(top, {}).get("approval", "none"),
    }


def approval_flags_present(level, approve, approver, reason):
    missing = []
    if level in ("none",):
        return True, missing
    if not approve:
        missing.append("--approve")
    if level in ("administrator", "external") and not approver:
        missing.append("--approver NAME")
    if level == "external" and not reason:
        missing.append("--reason TEXT")
    return (len(missing) == 0), missing


def boundary_check(text, policy):
    matches = []
    for item in policy.get("prohibited", []):
        try:
            if re.search(item["pattern"], text or "", re.IGNORECASE):
                matches.append({"id": item["id"], "reason": item["reason"]})
        except re.error:
            continue
    return {
        "allowed": not matches,
        "matches": matches,
        "note": policy.get("boundary_allow_note", ""),
    }


def gate_evaluate(action, declarations, policy):
    """declarations: dict with keys authorized/owns_artifact/license_ok/privacy_ok
    mapped to True/False/None (None = not provided)."""
    boundary = boundary_check(action, policy)
    reasons = []
    denied_by = []
    if not boundary["allowed"]:
        for m in boundary["matches"]:
            denied_by.append(f"{m['id']}: {m['reason']}")
    false_keys = [k for k, v in declarations.items() if v is False]
    missing_keys = [k for k, v in declarations.items() if v is None]
    labels = policy.get("gate_declaration_labels", {})

    if denied_by:
        decision = "DENY"
        reasons = denied_by
    elif false_keys:
        decision = "DENY"
        reasons = [f"declaration refused: {k} ({labels.get(k, k)})" for k in false_keys]
    elif missing_keys:
        decision = "INCOMPLETE"
        reasons = [f"missing declaration: {k} ({labels.get(k, k)})" for k in missing_keys]
    else:
        decision = "ALLOW"
        reasons = ["all declarations affirmed; no prohibited condition detected"]
    return {
        "decision": decision,
        "reasons": reasons,
        "missing_declarations": missing_keys,
        "refused_declarations": false_keys,
        "boundary": boundary,
        "action": action,
    }


# ---------------------------------------------------------------------------
# Evidence ledger
# ---------------------------------------------------------------------------

def append_evidence(path, record, schema):
    definition = schema["definitions"]["evidence_record"]
    problems = validate_record(definition, record, "evidence_record")
    if problems:
        raise ToolError("evidence record failed schema validation", EXIT_USAGE,
                        {"problems": problems})
    path = Path(path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return str(path)


def read_evidence(path, schema, last_n=None, validate=False):
    path = Path(path)
    if not path.is_file():
        raise ToolError(f"evidence file not found: {path}", EXIT_USAGE)
    limit = int(last_n) if last_n else None
    records = []
    bad_lines = []
    with open(path, "r", encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append((number, json.loads(line)))
            except json.JSONDecodeError:
                bad_lines.append(number)
    if limit is not None:
        records = records[-limit:]
    validation = []
    if validate:
        definition = schema["definitions"]["evidence_record"]
        for number, rec in records:
            problems = validate_record(definition, rec, f"line {number}")
            if problems:
                validation.append({"line": number, "problems": problems})
    return {
        "path": str(path),
        "records": [{"line": n, "record": r} for n, r in records],
        "record_count": len(records),
        "invalid_lines": bad_lines,
        "validation_problems": validation,
        "valid": (not bad_lines) and (not validation),
    }


def make_evidence_record(event, host, actor=None, skill_id=None, sources=None,
                         commands=None, tool_versions=None, approval=None,
                         result=None, note=None):
    return {
        "schema": "evidence-record/1",
        "event": event,
        "timestamp_utc": utcnow(),
        "host": host,
        "actor": actor,
        "skill_id": skill_id,
        "sources": sources or [],
        "commands": commands or [],
        "tool_versions": tool_versions or {},
        "approval": approval,
        "result": result,
        "note": note,
    }


def source_entry(path, note=None):
    path = Path(path)
    entry = {"path": str(path)}
    if path.is_file():
        entry["sha256"] = sha256_file(path)
        entry["bytes"] = path.stat().st_size
    else:
        entry["sha256"] = None
        entry["missing"] = True
    if note:
        entry["note"] = note
    return entry


# ---------------------------------------------------------------------------
# Reversible change plans
# ---------------------------------------------------------------------------

def create_change_plan(target, action, policy, schema, approve, approver, reason,
                       plan_out=None, evidence_path=None, host=None, actor=None):
    target = Path(target)
    if not target.exists():
        raise ToolError(f"target does not exist: {target}", EXIT_USAGE)
    if not target.is_file():
        raise ToolError("plan-change supports regular files only", EXIT_USAGE,
                        {"target": str(target)})
    max_bytes = policy["limits"].get("plan_max_backup_bytes", 2147483648)
    size = target.stat().st_size
    if size > max_bytes:
        raise ToolError(f"target exceeds plan_max_backup_bytes ({max_bytes})", EXIT_USAGE)

    classification = classify_action(action, policy)
    level = classification["approval_required"]
    granted, missing = approval_flags_present(level, approve, approver, reason)
    if not granted:
        raise ToolError(
            f"approval required before this change: risk class "
            f"{classification['risk_class']} needs level '{level}'",
            EXIT_APPROVAL,
            {
                "risk_class": classification["risk_class"],
                "approval_required": level,
                "missing": missing,
                "how_to_approve": policy["approval_flags"].get(level, ""),
            },
        )

    plan_id = hashlib.sha256(
        f"{target}:{time_token()}:{os.getpid()}".encode()
    ).hexdigest()[:12]
    backup_path = Path(f"{target}.pcbak-{plan_id}")
    plan_path = Path(plan_out) if plan_out else Path(f"{target}.pcplan-{plan_id}.json")

    target_sha = sha256_file(target)
    shutil.copy2(target, backup_path)
    backup_sha = sha256_file(backup_path)
    if target_sha != backup_sha:
        backup_path.unlink(missing_ok=True)
        raise ToolError("backup verification failed; backup removed", EXIT_FAILURE)

    plan = {
        "schema": "change-plan/1",
        "plan_id": plan_id,
        "created_utc": utcnow(),
        "target": str(target),
        "action": action,
        "risk_class": classification["risk_class"],
        "target_sha256": target_sha,
        "backup_path": str(backup_path),
        "backup_sha256": backup_sha,
        "backup_bytes": backup_path.stat().st_size,
        "rollback_steps": [
            f"verify sha256 of {backup_path.name} equals {backup_sha}",
            f"restore {backup_path} over {target}",
            "re-run the original verification to confirm recovery",
        ],
        "approval": {
            "level": level,
            "granted": True,
            "approver": approver,
            "reason": reason,
            "flags_used": {"approve": bool(approve), "approver": approver, "reason": reason},
        },
        "status": "open",
        "rolled_back_utc": None,
    }
    problems = validate_record(schema["definitions"]["change_plan"], plan, "change_plan")
    if problems:
        backup_path.unlink(missing_ok=True)
        raise ToolError("change plan failed schema validation", EXIT_FAILURE,
                        {"problems": problems})
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, sort_keys=True)
        fh.write("\n")

    if evidence_path:
        record = make_evidence_record(
            "plan-created", host or host_info(), actor=actor,
            sources=[source_entry(target), source_entry(backup_path)],
            commands=[f"plan-change --target {target} --action {action!r}"],
            approval=plan["approval"],
            result={"plan_id": plan_id, "plan_path": str(plan_path),
                    "risk_class": classification["risk_class"]},
            note="reversible change plan created before modification",
        )
        append_evidence(evidence_path, record, schema)

    return {"plan": plan, "plan_path": str(plan_path), "backup_path": str(backup_path)}


def rollback_change_plan(plan_path, policy, schema, approve, approver, reason,
                         evidence_path=None, host=None, actor=None):
    plan_path = Path(plan_path)
    if not plan_path.is_file():
        raise ToolError(f"plan file not found: {plan_path}", EXIT_USAGE)
    try:
        with open(plan_path, "r", encoding="utf-8") as fh:
            plan = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ToolError(f"plan file is not valid JSON: {exc}", EXIT_FAILURE)
    problems = validate_record(schema["definitions"]["change_plan"], plan, "change_plan")
    if problems:
        raise ToolError("plan file failed schema validation", EXIT_FAILURE,
                        {"problems": problems})
    if plan.get("status") == "rolled_back":
        raise ToolError("plan has already been rolled back", EXIT_USAGE,
                        {"plan_id": plan.get("plan_id"),
                         "rolled_back_utc": plan.get("rolled_back_utc")})

    level = policy["execution_gates"].get("rollback", "external")
    granted, missing = approval_flags_present(level, approve, approver, reason)
    if not granted:
        raise ToolError(
            f"rollback overwrites the current target; approval level '{level}' required",
            EXIT_APPROVAL,
            {"approval_required": level, "missing": missing,
             "how_to_approve": policy["approval_flags"].get(level, "")},
        )

    backup_path = Path(plan["backup_path"])
    if not backup_path.is_file():
        raise ToolError("backup file is missing; cannot roll back", EXIT_FAILURE,
                        {"backup_path": str(backup_path)})
    actual = sha256_file(backup_path)
    if actual != plan["backup_sha256"]:
        raise ToolError("backup integrity check failed; refusing to restore",
                        EXIT_FAILURE,
                        {"expected": plan["backup_sha256"], "actual": actual})

    target = Path(plan["target"])
    target_modified = None
    if target.is_file():
        current = sha256_file(target)
        target_modified = current != plan["target_sha256"]
    shutil.copy2(backup_path, target)
    restored_sha = sha256_file(target)
    if restored_sha != plan["backup_sha256"]:
        raise ToolError("post-restore verification failed", EXIT_FAILURE)

    plan["status"] = "rolled_back"
    plan["rolled_back_utc"] = utcnow()
    plan["approval"] = plan.get("approval") or {}
    plan["approval"]["rollback"] = {
        "level": level, "granted": True, "approver": approver, "reason": reason,
    }
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, sort_keys=True)
        fh.write("\n")

    if evidence_path:
        record = make_evidence_record(
            "plan-rolled-back", host or host_info(), actor=actor,
            sources=[source_entry(target), source_entry(backup_path)],
            commands=[f"plan-change --rollback {plan_path}"],
            approval=plan["approval"],
            result={"plan_id": plan["plan_id"], "target_modified_before_rollback": target_modified,
                    "restored_sha256": restored_sha},
            note="target restored from verified backup",
        )
        append_evidence(evidence_path, record, schema)

    return {
        "plan_id": plan["plan_id"],
        "target": str(target),
        "restored_sha256": restored_sha,
        "target_modified_before_rollback": target_modified,
        "plan_status": plan["status"],
        "plan_path": str(plan_path),
    }


def time_token():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


# ---------------------------------------------------------------------------
# Artifact detection and routing
# ---------------------------------------------------------------------------

HANDLERS = {
    "pe": ("pe-readonly-inspect", "read-only inspection (headers, imports, exports); no extraction",
           "read_only", ["c08s01", "c08s02", "c08s03", "c01s11"]),
    "mz-executable": ("pe-readonly-inspect", "legacy MZ image; inspect read-only",
                      "read_only", ["c08s01", "c01s11"]),
    "elf": ("elf-readonly-inspect", "read-only inspection with readelf/objdump",
            "read_only", ["c01s11"]),
    "zip": ("list-first-archive",
            "list entries first; reject absolute and parent-traversal paths before any extraction",
            "read_only", ["c13s01", "c13s11"]),
    "jar": ("list-first-archive",
            "list entries first; block path traversal and verify manifest read-only",
            "read_only", ["c04s03", "c13s11"]),
    "apk": ("list-first-archive", "list entries first; block path traversal before extraction",
            "read_only", ["c13s01", "c13s11"]),
    "msix": ("list-first-archive",
             "list entries first; block path traversal and inspect manifests read-only",
             "read_only", ["c12s08", "c13s11"]),
    "7z": ("list-first-archive", "list entries first; enforce expansion and traversal limits",
           "read_only", ["c13s01", "c13s11"]),
    "rar": ("list-first-archive", "list with permitted unrar tooling; report encryption and volumes",
            "read_only", ["c13s02", "c13s11"]),
    "cab": ("list-first-archive", "list first with cabextract or equivalent", "read_only",
            ["c13s03", "c13s11"]),
    "iso": ("iso-list-readonly", "list and verify read-only; mount only with explicit approval",
            "read_only", ["c13s05", "c13s14"]),
    "wim": ("wim-list-first", "list first with wimlib; apply only in an approved worker",
            "read_only", ["c13s07"]),
    "qcow2": ("disk-image-info", "inspect with qemu-img info; convert only with approval",
              "read_only", ["c13s09", "c13s13"]),
    "vhd": ("disk-image-info", "inspect with qemu-img info; convert only with approval",
            "read_only", ["c13s09", "c13s13"]),
    "vhdx": ("disk-image-info", "inspect with qemu-img info; convert only with approval",
             "read_only", ["c13s09", "c13s13"]),
    "vmdk": ("disk-image-info", "inspect with qemu-img info; convert only with approval",
             "read_only", ["c13s09", "c13s13"]),
    "tar": ("list-first-archive", "list entries first; block path traversal and link escapes",
            "read_only", ["c13s04", "c13s11"]),
    "gzip": ("list-first-archive", "list first; bound expansion ratio before any extraction",
             "read_only", ["c13s04", "c13s11"]),
    "xz": ("list-first-archive", "list first; bound expansion ratio before any extraction",
           "read_only", ["c13s04", "c13s11"]),
    "bzip2": ("list-first-archive", "list first; bound expansion ratio before any extraction",
              "read_only", ["c13s04", "c13s11"]),
    "zstd": ("list-first-archive", "list first; bound expansion ratio before any extraction",
             "read_only", ["c13s04", "c13s11"]),
    "deb": ("package-inspect-readonly", "inspect control data read-only; do not install",
            "read_only", ["c12s13"]),
    "rpm": ("package-inspect-readonly", "inspect header and scriptlets read-only; do not install",
            "read_only", ["c12s14"]),
    "ar": ("package-inspect-readonly", "static archive inspection read-only", "read_only",
           ["c01s11"]),
    "cfb": ("installer-readonly-inspect", "OLE/CFB container (MSI candidate); list streams read-only",
            "read_only", ["c09s12", "c01s11"]),
    "pdf": ("document-readonly-inspect", "read-only document inspection", "read_only",
            ["c01s11"]),
    "png": ("media-readonly-inspect", "read-only media inspection", "read_only", ["c01s11"]),
    "jpeg": ("media-readonly-inspect", "read-only media inspection", "read_only", ["c01s11"]),
    "gif": ("media-readonly-inspect", "read-only media inspection", "read_only", ["c01s11"]),
    "unknown": ("manual-triage", "identify with file(1) or strings before any handling",
                "unknown", ["c01s11"]),
}


def _read_at(fh, offset, size):
    fh.seek(offset)
    data = fh.read(size)
    return data


def detect_artifact(path):
    path = Path(path)
    if not path.exists():
        raise ToolError(f"file not found: {path}", EXIT_USAGE)
    if not path.is_file():
        raise ToolError(f"not a regular file: {path}", EXIT_USAGE)
    size = path.stat().st_size
    fmt = "unknown"
    details = {}
    notes = []
    head_limit = 8192
    with open(path, "rb") as fh:
        head = fh.read(head_limit)
        if head.startswith(b"MZ"):
            fmt = "pe"
            if len(head) >= 0x40:
                e_lfanew = int.from_bytes(head[0x3C:0x40], "little")
                if 0 < e_lfanew < size - 24:
                    fh.seek(e_lfanew)
                    sig = fh.read(4)
                    if sig == b"PE\x00\x00":
                        hdr = fh.read(24)
                        if len(hdr) >= 24:
                            machine = int.from_bytes(hdr[0:2], "little")
                            chars = int.from_bytes(hdr[22:24], "little")
                            mach_map = {0x8664: "x86_64", 0x14C: "x86",
                                        0xAA64: "arm64", 0x1C0: "arm"}
                            details = {
                                "machine": mach_map.get(machine, hex(machine)),
                                "bits": 64 if machine in (0x8664, 0xAA64) else 32,
                                "subsystem_kind": "dll" if chars & 0x2000 else "exe",
                                "pe_offset": e_lfanew,
                            }
                        else:
                            notes.append("truncated PE file header")
                    else:
                        fmt = "mz-executable"
                        notes.append("MZ image without PE signature (legacy DOS executable)")
                else:
                    fmt = "mz-executable"
                    notes.append("invalid e_lfanew; treating as legacy MZ image")
            else:
                fmt = "mz-executable"
                notes.append("truncated MZ image")
        elif head.startswith(b"\x7fELF"):
            fmt = "elf"
            ei_class = head[4] if len(head) > 4 else 0
            ei_data = head[5] if len(head) > 5 else 0
            endian = "little" if ei_data == 1 else "big"
            if len(head) >= 20:
                e_type = int.from_bytes(head[16:18], endian)
                e_machine = int.from_bytes(head[18:20], endian)
                mach_map = {0x3E: "x86_64", 0x03: "x86", 0xB7: "arm64",
                            0x28: "arm", 0xF3: "riscv"}
                type_map = {1: "relocatable", 2: "executable", 3: "shared-object",
                            4: "core"}
                details = {
                    "bits": 64 if ei_class == 2 else 32,
                    "endian": "little" if ei_data == 1 else "big",
                    "type": type_map.get(e_type, hex(e_type)),
                    "machine": mach_map.get(e_machine, hex(e_machine)),
                }
        elif head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
            suffix = path.suffix.lower()
            fmt = {".jar": "jar", ".apk": "apk",
                   ".msix": "msix", ".appx": "msix",
                   ".appxbundle": "msix"}.get(suffix, "zip")
            if fmt != "zip":
                notes.append(f"zip container with extension {suffix}")
        elif head[:6] == b"7z\xbc\xaf\x27\x1c":
            fmt = "7z"
        elif head[:6] in (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00"):
            fmt = "rar"
        elif head[:4] == b"MSCF":
            fmt = "cab"
        elif head[:6] in (b"MSWIM\x00", b"WLPWM\x00"):
            fmt = "wim"
        elif head[:4] == b"QFI\xfb":
            fmt = "qcow2"
        elif head[:4] == b"KDMV":
            fmt = "vmdk"
        elif head[:8] == b"conectix":
            fmt = "vhd"
        elif head[:4] == b"vhdx":
            fmt = "vhdx"
        elif _read_at(fh, 257, 5) == b"ustar":
            fmt = "tar"
        elif head[:2] == b"\x1f\x8b":
            fmt = "gzip"
        elif head[:6] == b"\xfd7zXZ\x00":
            fmt = "xz"
        elif head[:3] == b"BZh":
            fmt = "bzip2"
        elif head[:4] == b"\x28\xb5\x2f\xfd":
            fmt = "zstd"
        elif head[:8] == b"!<arch>\n":
            if _read_at(fh, 8, 14).startswith(b"debian-binary"):
                fmt = "deb"
            else:
                fmt = "ar"
                notes.append("plain ar archive")
        elif head[:4] == b"\xed\xab\xee\xdb":
            fmt = "rpm"
        elif _read_at(fh, 32768, 5) == b"CD001":
            fmt = "iso"
        elif head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            fmt = "cfb"
            if path.suffix.lower() == ".msi":
                notes.append("CFB container with .msi extension (MSI package candidate)")
        elif head[:5] == b"%PDF-":
            fmt = "pdf"
        elif head[:8] == b"\x89PNG\r\n\x1a\n":
            fmt = "png"
        elif head[:3] == b"\xff\xd8\xff":
            fmt = "jpeg"
        elif head[:6] in (b"GIF87a", b"GIF89a"):
            fmt = "gif"
        elif path.suffix.lower() == ".appimage":
            fmt = "elf"
            details["appimage"] = True
            notes.append("AppImage (ELF with embedded payload)")
        elif path.suffix.lower() in (".exe", ".dll") and not head.startswith(b"MZ"):
            fmt = "unknown"
            notes.append("extension suggests a PE file but content does not start with MZ")
        elif path.suffix.lower() in (".iso", ".wim", ".vhd", ".vhdx", ".qcow2", ".vmdk"):
            fmt = "unknown"
            notes.append(f"extension {path.suffix.lower()} without matching magic bytes")

    handler, extraction, risk, related = HANDLERS.get(
        fmt, HANDLERS["unknown"])
    file_sha = sha256_file(path)
    return {
        "path": str(path),
        "size_bytes": size,
        "sha256": file_sha,
        "format": fmt,
        "format_details": details,
        "handler": handler,
        "extraction_policy": extraction,
        "risk": risk,
        "related_capability_ids": related,
        "notes": notes,
        "safety": "this tool only identifies and routes; it never extracts or executes artifacts",
    }


# ---------------------------------------------------------------------------
# Catalog search and selection
# ---------------------------------------------------------------------------

def tokenize(text):
    return [t for t in re.findall(r"[a-z0-9+#.]+", (text or "").lower())
            if t not in STOPWORDS and len(t) > 1]


def search_catalog(catalog, query, limit=5):
    q_tokens = set(tokenize(query))
    if not q_tokens:
        raise ToolError("search query produced no usable terms", EXIT_USAGE)
    scored = []
    lowered_query = (query or "").strip().lower()
    for cat in catalog["categories"]:
        for skill in cat["skills"]:
            name = skill["name"].lower()
            desc = skill["description"].lower()
            name_tokens = set(tokenize(skill["name"]))
            desc_tokens = set(tokenize(skill["description"][:600]))
            score = 3 * len(q_tokens & name_tokens) + 1 * len(q_tokens & desc_tokens)
            if lowered_query and lowered_query in name:
                score += 5
            if score > 0:
                scored.append((score, skill["id"], cat, skill))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"score": score, "category": cat, "skill": skill}
        for score, _sid, cat, skill in scored[:limit]
    ]


def select_skills(catalog, category_selector=None, search=None, limit=None):
    """Resolve CLI selectors into an ordered list of (category, skill)."""
    chosen = []
    seen = set()
    if category_selector:
        for sel in category_selector:
            for cat in _resolve_categories(catalog, sel):
                for skill in cat["skills"]:
                    if skill["id"] not in seen:
                        seen.add(skill["id"])
                        chosen.append((cat, skill))
    elif search:
        for hit in search_catalog(catalog, search, limit=limit or 20):
            if hit["skill"]["id"] not in seen:
                seen.add(hit["skill"]["id"])
                chosen.append((hit["category"], hit["skill"]))
    else:
        for cat in catalog["categories"]:
            for skill in cat["skills"]:
                if skill["id"] not in seen:
                    seen.add(skill["id"])
                    chosen.append((cat, skill))
    if limit:
        chosen = chosen[: int(limit)]
    return chosen


def _resolve_categories(catalog, sel):
    m = re.fullmatch(r"(?:category\s*)?(\d{1,2})", str(sel).strip(), re.I)
    if m:
        num = int(m.group(1))
        hits = [c for c in catalog["categories"] if c["id"] == num]
        if not hits:
            raise ToolError(f"unknown category number: {num}", EXIT_USAGE)
        return hits
    needle = str(sel).strip().lower()
    hits = [c for c in catalog["categories"] if needle in c["name"].lower()]
    if not hits:
        raise ToolError(f"no category matches: {sel}", EXIT_USAGE)
    return hits


# ---------------------------------------------------------------------------
# Capability input parsing (plain text, Markdown, HTML)
# ---------------------------------------------------------------------------

def parse_capability_text(text, origin="input"):
    if text is None:
        raise ToolError("no input text provided", EXIT_USAGE)
    if len(text.encode("utf-8", "replace")) > 5 * 1024 * 1024:
        raise ToolError("input exceeds parse_input_max_bytes (5 MiB)", EXIT_USAGE)
    fmt = "plain"
    working = text
    if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", text or ""):
        fmt = "html"
        working = re.sub(r"(?is)<(style|script)\b[^>]*>.*?</\1>", " ", working)
        working = re.sub(r"(?s)<!--.*?-->", " ", working)
        # Block-level tags become line breaks; inline tags become spaces so that
        # "<b>Name</b> — description" stays on one line.
        # Headings become Markdown-style category markers before tags are stripped.
        working = re.sub(r"(?i)<h[1-6]\b[^>]*>",
                         lambda m: "\n" + "#" * int(m.group(0)[2]) + " ", working)
        working = re.sub(r"(?i)</h[1-6]>", "\n", working)
        working = re.sub(
            r"(?i)</?(?:p|div|li|tr|ul|ol|dl|dt|dd|table|section|article|"
            r"header|footer|br|hr|blockquote)\b[^>]*>",
            "\n", working)
        working = re.sub(r"<[^>]+>", " ", working)
        working = html.unescape(working)
        working = re.sub(r"[ \t]+", " ", working)
    elif re.search(r"(?m)^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)", text or "") or "#" in (text or "")[:200]:
        fmt = "markdown"

    lines = [ln.strip() for ln in working.splitlines()]
    entries = []
    categories = []
    cur_cat = None
    cur_entry = None
    skill_name_re = re.compile(r"^\d{1,3}\.\s+Skill Name:\s*(.+)$")
    desc_re = re.compile(r"^Description:\s*(.*)$")
    numbered_re = re.compile(r"^(\d{1,3})[.)]\s+(.+)$")
    name_desc_re = re.compile(
        r"^(?:[-*+]\s+|\d{1,3}[.)]\s+)?\**([A-Za-z0-9][^:*]{1,78}?)\**\s*[:：]\s+(\S.*)$"
    )
    bullet_re = re.compile(r"^[-*+]\s+(\S.*)$")
    name_dash_re = re.compile(r"^(.{2,80}?)\s+(?:—|–|--)\s+(\S.*)$")
    heading_re = re.compile(r"^#{1,6}\s+(.+)$")

    def flush():
        nonlocal cur_entry
        cur_entry = None

    nonempty = [ln for ln in lines if ln]
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line:
            continue
        m = skill_name_re.match(line)
        if m:
            flush()
            cur_entry = {
                "kind": "skill", "name": m.group(1).strip(), "description": "",
                "category": cur_cat, "source_line": i,
            }
            entries.append(cur_entry)
            continue
        m = desc_re.match(line)
        if m and cur_entry is not None:
            cur_entry["description"] = m.group(1).strip()
            while i < len(lines):
                nxt = lines[i]
                if not nxt:
                    i += 1
                    continue
                if (skill_name_re.match(nxt) or desc_re.match(nxt)
                        or numbered_re.match(nxt) or heading_re.match(nxt)):
                    break
                cur_entry["description"] += " " + nxt
                i += 1
            continue
        m = numbered_re.match(line)
        if m:
            rest = m.group(2).strip()
            nxt = next((ln for ln in lines[i:] if ln), "")
            nxt_num = numbered_re.match(nxt)
            if desc_re.match(nxt):
                flush()
                cur_entry = {"kind": "skill", "name": rest, "description": "",
                             "category": cur_cat, "source_line": i}
                entries.append(cur_entry)
                continue
            if skill_name_re.match(nxt):
                flush()
                cur_cat = rest
                categories.append(rest)
                continue
            m2 = name_desc_re.match(line)
            if m2:
                flush()
                entries.append({"kind": "skill", "name": m2.group(1).strip(),
                                "description": m2.group(2).strip(),
                                "category": cur_cat, "source_line": i})
                continue
            if nxt_num and ":" in nxt_num.group(2):
                # next numbered line is "name: description" -> this line is a header
                flush()
                cur_cat = rest
                categories.append(rest)
                continue
            flush()
            entries.append({"kind": "capability", "name": rest, "description": "",
                            "category": cur_cat, "source_line": i})
            continue
        m = heading_re.match(line)
        if m:
            title = m.group(1).strip()
            if not re.search(r"Skill Name:|Description:", title):
                flush()
                cur_cat = title
                categories.append(title)
            continue
        m = name_desc_re.match(line)
        if m:
            flush()
            entries.append({"kind": "skill", "name": m.group(1).strip(),
                            "description": m.group(2).strip(),
                            "category": cur_cat, "source_line": i})
            continue
        m3 = name_dash_re.match(line)
        if m3:
            flush()
            entries.append({"kind": "skill", "name": m3.group(1).strip(),
                            "description": m3.group(2).strip(),
                            "category": cur_cat, "source_line": i})
            continue
        m = bullet_re.match(line)
        if m:
            flush()
            entries.append({"kind": "capability", "name": m.group(1).strip(),
                            "description": "", "category": cur_cat,
                            "source_line": i})
            continue
        if not entries and not categories and len(nonempty) == 1:
            entries.append({"kind": "capability", "name": line, "description": "",
                            "category": None, "source_line": i})
        elif not entries and not categories and len(line) <= 96:
            entries.append({"kind": "capability", "name": line, "description": "",
                            "category": None, "source_line": i})

    if not entries and not categories:
        raise ToolError(
            f"no capabilities found in {origin}; expected a capability name, a "
            "'Skill Name:'/'Description:' list, or a numbered list", EXIT_USAGE)
    return {"format": fmt, "categories": categories, "entries": entries}


def read_input_file(path, max_bytes=5 * 1024 * 1024):
    path = Path(path)
    if not path.is_file():
        raise ToolError(f"input file not found: {path}", EXIT_USAGE)
    size = path.stat().st_size
    if size > max_bytes:
        raise ToolError(f"input file exceeds {max_bytes} bytes", EXIT_USAGE)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Task decomposition (PC Task Decomposer)
# ---------------------------------------------------------------------------

DECOMPOSE_STAGES = [
    ("inspect", "Collect read-only facts before touching anything.",
     ["identify artifact types, hosts, and dependencies",
      "run the self-test for related capability ids",
      "record current versions and hashes"],
     ["facts report with capability ids and probe rows"],
     "every claim has an evidence reference"),
    ("preserve", "Protect originals and record provenance.",
     ["hash every input that will change",
      "create backups or snapshots with plan-change",
      "append an evidence record for each source"],
     ["backup paths, sha256 values, evidence ledger lines"],
     "originals are restorable without network access"),
    ("reproduce", "Confirm the starting condition deterministically.",
     ["re-run the observation in a clean state",
      "capture the exact commands and outputs",
      "separate environment state from true dependencies"],
     ["reproduction transcript"],
     "the starting condition is observed twice"),
    ("repair", "Apply the smallest reversible change.",
     ["choose the lowest-risk change class",
      "obtain the required approval level",
      "apply one change and stop to measure"],
     ["change plan and applied diff"],
     "each change maps to one observed cause"),
    ("test", "Verify normal, boundary, and failure behavior.",
     ["run normal-case checks",
      "run boundary and invalid-input checks",
      "confirm no unrelated behavior changed"],
     ["test results with exit codes"],
     "all checks pass or are explicitly waived with approval"),
    ("package", "Produce the deliverable with integrity data.",
     ["assemble outputs in the requested format",
      "generate checksums for every artifact",
      "keep dependencies and versions explicit"],
     ["deliverable plus checksums"],
     "a third party can verify the package offline"),
    ("verify", "Repeat from a clean state.",
     ["rebuild or re-run in a fresh directory",
      "compare hashes and outputs to the earlier run",
      "record any divergence and its cause"],
     ["clean-state comparison report"],
     "no hidden state is required for success"),
    ("handoff", "Transfer evidence and recovery knowledge.",
     ["bundle commands, configs, hashes, and logs",
      "include rollback instructions and open issues",
      "state limits and required approvals"],
     ["handoff package"],
     "a new operator can continue without prior context"),
]


def decompose_goal(goal):
    goal = (goal or "").strip()
    if not goal:
        raise ToolError("goal text is required", EXIT_USAGE)
    if len(goal) > 2000:
        raise ToolError("goal text exceeds 2000 characters", EXIT_USAGE)
    stages = []
    for stage_id, purpose, actions, outputs, exit_criteria in DECOMPOSE_STAGES:
        stages.append({
            "stage": stage_id,
            "purpose": purpose,
            "actions": actions,
            "outputs": outputs,
            "exit_criteria": exit_criteria,
        })
    return {
        "goal": goal,
        "stage_count": len(stages),
        "stages": stages,
        "note": "stages run in order; stop at the first failed exit criterion and "
                "record evidence before continuing",
    }


# ---------------------------------------------------------------------------
# Clean-state validation (bounded command execution with approval)
# ---------------------------------------------------------------------------

_TEMP_DIRS = set()


def register_temp(path):
    _TEMP_DIRS.add(str(path))


def cleanup_temps():
    for path in list(_TEMP_DIRS):
        shutil.rmtree(path, ignore_errors=True)
        _TEMP_DIRS.discard(path)


def run_clean_state(argv, repeats, policy, timeout_sec=None):
    limits = policy["limits"]
    if not isinstance(argv, list) or not argv:
        raise ToolError("--argv must be a non-empty JSON array of strings", EXIT_USAGE)
    if len(argv) > limits.get("clean_state_max_argv", 32):
        raise ToolError(f"--argv exceeds {limits.get('clean_state_max_argv', 32)} entries",
                        EXIT_USAGE)
    for item in argv:
        if not isinstance(item, str):
            raise ToolError("--argv entries must be strings", EXIT_USAGE)
        if len(item) > limits.get("clean_state_max_arg_len", 4096):
            raise ToolError("argv entry exceeds max_arg_len", EXIT_USAGE)
    repeats = int(repeats)
    max_repeats = limits.get("clean_state_max_repeats", 3)
    if repeats < 2 or repeats > max_repeats:
        raise ToolError(f"--repeat must be between 2 and {max_repeats}", EXIT_USAGE)
    if shutil.which(argv[0]) is None:
        raise ToolError(f"command not found on PATH: {argv[0]}", EXIT_USAGE)
    timeout = int(timeout_sec or limits.get("clean_state_timeout_sec", 30))

    runs = []
    try:
        for index in range(repeats):
            workdir = tempfile.mkdtemp(prefix="pc-capability-clean-")
            register_temp(workdir)
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": workdir,
                "TMPDIR": workdir,
                "LANG": "C",
                "LC_ALL": "C",
            }
            started = datetime.now(timezone.utc)
            entry = {"run": index + 1, "workdir_created": True,
                     "exit_code": None, "stdout_sha256": None,
                     "stdout_bytes": 0, "stderr_bytes": 0, "timed_out": False,
                     "duration_ms": 0}
            try:
                proc = subprocess.run(
                    list(argv), capture_output=True, timeout=timeout,
                    cwd=workdir, env=env,
                )
                entry["exit_code"] = proc.returncode
                entry["stdout_sha256"] = hashlib.sha256(proc.stdout).hexdigest()
                entry["stdout_bytes"] = len(proc.stdout)
                entry["stderr_bytes"] = len(proc.stderr)
            except subprocess.TimeoutExpired:
                entry["timed_out"] = True
            except OSError as exc:
                entry["error"] = str(exc)
            entry["duration_ms"] = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000)
            runs.append(entry)
    finally:
        cleanup_temps()

    timed_out = any(r["timed_out"] for r in runs)
    errors = [r for r in runs if r.get("error")]
    codes = {r["exit_code"] for r in runs}
    hashes = {r["stdout_sha256"] for r in runs if r["stdout_sha256"]}
    if timed_out or errors:
        verdict = "inconclusive"
        detail = "one or more runs timed out or failed to start"
    elif codes != {0}:
        verdict = "inconclusive"
        detail = f"non-zero exit codes observed: {sorted(c for c in codes if c is not None)}"
    elif len(hashes) == 1:
        verdict = "none"
        detail = f"{repeats} clean runs produced identical stdout"
    else:
        verdict = "detected"
        detail = "stdout differs across clean runs; hidden state is likely"
    return {
        "argv": list(argv),
        "repeats": repeats,
        "timeout_sec": timeout,
        "runs": runs,
        "identical_stdout": verdict == "none",
        "hidden_state": verdict,
        "detail": detail,
        "sanitized_environment": ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"],
        "cleanup": "temporary work directories were removed",
    }


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def install_cancel_handler():
    def handler(signum, frame):
        cleanup_temps()
        sys.stderr.write("\ncancelled; temporary files cleaned up\n")
        sys.exit(EXIT_CANCEL)

    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        pass
