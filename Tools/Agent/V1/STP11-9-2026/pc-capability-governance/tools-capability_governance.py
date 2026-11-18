#!/usr/bin/env python3
"""Primary entry point for the ``pc-capability-governance`` Skill Tool.

Unified, lawful governance and routing layer for PC work (catalog category
"Capability Discovery, Routing, and Governance"). Every command is read-only
unless explicitly documented; nothing executes arbitrary shell input.

Commands
--------
  capabilities     list every implemented capability (capability-list command)
  help             general or per-command help
  inventory        detect tools, versions, architectures and missing dependencies
  self-test        deterministic probes + Startup Self-Test Contract report
  route            choose the least-privilege worker for required capabilities
  approval-check   classify actions and require the correct approval level
  reversible-plan  hash/backup plan with explicit rollback commands
  boundary-check   lawful-use and operational-boundary screen for a request
  provenance       record inputs, hashes, versions, commands and transforms
  parse            extract categories and Skill Tool names from pasted sources
  validate-output  validate an emitted JSON document against its schema
  policy           show the effective configuration and approval policy

Run ``python3 tools-capability_governance.py help`` for options.
Standard library only (Python 3.9+).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import sys
import tempfile
import time

SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_ROOT)


def _load(module_name, filename):
    """Load a hyphenated Skill Tool file as an importable module."""
    import importlib.util

    if module_name in sys.modules:
        return sys.modules[module_name]
    path = os.path.join(SKILL_ROOT, filename)
    if not os.path.isfile(path):
        raise SystemExit(f"missing required Skill Tool file: {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


lib = _load("tools_probe_lib", "tools-probe-lib.py")
providers_mod = _load("tools_provider_commands", "tools-provider-commands.py")

TOOL_VERSION = "1.0.0"
ASSET_DEFAULT_CONFIG = "asset-default-config.json"
ASSET_APPROVAL_POLICY = "asset-approval-policy.json"
ASSET_BOUNDARY_TERMS = "asset-boundary-terms.json"
ASSET_PROVIDER_CATALOG = "asset-provider-catalog.json"
ASSET_SCHEMA_SELFTEST = "asset-schema-selftest-report.json"
ASSET_SCHEMA_PROVENANCE = "asset-schema-provenance.json"
ASSET_SCHEMA_PARSE = "asset-schema-category-parse.json"
ASSET_READINESS_PROFILES = "asset-readiness-profiles.json"
ASSET_WORKER_PROFILES = "asset-worker-profiles.json"

CAPABILITIES = [
    {
        "id": "capability-inventory",
        "command": "inventory",
        "name": "PC Capability Inventory",
        "description": "Detects installed tools, versions, architectures, licenses, host requirements and missing dependencies on Linux and optional Windows/GPU/virtualized workers.",
        "approval": "read-only",
        "outputs": ["inventory.json"],
    },
    {
        "id": "startup-self-test",
        "command": "self-test",
        "name": "Startup Self-Test Orchestrator",
        "description": "Runs deterministic probes and reports FOUND, MISSING, WRONG VERSION, BROKEN, LICENSE REQUIRED, WINDOWS HOST REQUIRED, GPU REQUIRED, VIRTUALIZATION REQUIRED or NETWORK REQUIRED with every contract field.",
        "approval": "read-only",
        "outputs": ["self-test-report.json", "evidence/**"],
    },
    {
        "id": "host-aware-routing",
        "command": "route",
        "name": "Host-Aware Task Router",
        "description": "Routes a job to Linux, Windows, ARM64, GPU, virtualized or network-enabled workers according to hard requirements and least privilege.",
        "approval": "read-only",
        "outputs": ["routing-plan.json"],
    },
    {
        "id": "lawful-use-gate",
        "command": "boundary-check",
        "name": "Lawful Use Gate",
        "description": "Checks authorization, ownership, licensing, privacy scope and prohibited bypass conditions before inspection, repair, extraction or modification.",
        "approval": "read-only",
        "outputs": ["boundary-check.json"],
    },
    {
        "id": "reversible-change-planning",
        "command": "reversible-plan",
        "name": "Reversible Change Planner",
        "description": "Creates hashes, backup copies and explicit rollback steps before any system or artifact modification.",
        "approval": "read-only (copy with --apply)",
        "outputs": ["reversible-plan.json"],
    },
    {
        "id": "evidence-provenance",
        "command": "provenance",
        "name": "Evidence and Provenance Recorder",
        "description": "Records source paths, hashes, tool versions, commands, timestamps and transformation history for repeatable work.",
        "approval": "read-only",
        "outputs": ["provenance.json"],
    },
    {
        "id": "risk-based-approval",
        "command": "approval-check",
        "name": "Risk-Based Execution Approver",
        "description": "Classifies read-only, reversible, privileged, networked, destructive and signing actions and requires the matching approval level.",
        "approval": "read-only",
        "outputs": ["approval-decision.json"],
    },
    {
        "id": "output-validation",
        "command": "validate-output",
        "name": "Structured Output Validator",
        "description": "Validates emitted JSON documents (self-test, routing, provenance, parse) against the packaged schemas before handoff.",
        "approval": "read-only",
        "outputs": ["validation.json"],
    },
    {
        "id": "source-extraction",
        "command": "parse",
        "name": "Category and Skill Tool Extractor",
        "description": "Extracts categories, Skill Tool names and descriptions from plain text, Markdown or copied HTML while ignoring tags, CSS classes, data attributes, numbering marks and spacing.",
        "approval": "read-only",
        "outputs": ["category-parse.json"],
    },
    {
        "id": "determinism-check",
        "command": "self-test --check-runs N",
        "name": "Clean-State Validation Manager",
        "description": "Repeats the probe set from a clean working state and compares status maps so hidden state is separated from true dependencies.",
        "approval": "read-only",
        "outputs": ["determinism.json"],
    },
    {
        "id": "toolchain-policy",
        "command": "policy",
        "name": "Toolchain Version Policy Manager",
        "description": "Pins supported minimum/maximum versions and flags unsupported or missing toolchain combinations before execution.",
        "approval": "read-only",
        "outputs": ["effective-policy.json"],
    },
    {
        "id": "task-decomposition",
        "command": "route --decompose",
        "name": "PC Task Decomposer",
        "description": "Breaks a broad desktop goal into inspect, preserve, reproduce, repair, test, package, verify and handoff stages, each with its approval level.",
        "approval": "read-only",
        "outputs": ["task-plan.json"],
    },
]


# --------------------------------------------------------------- utilities --
def asset_path(rel):
    """Resolve a packaged asset relative to this Skill Tool folder."""
    return os.path.join(SKILL_ROOT, rel)


def load_config(path=None):
    config_path = path or asset_path(ASSET_DEFAULT_CONFIG)
    config = lib.read_json(config_path, "configuration file")
    defaults = {
        "timeout_seconds": 10,
        "retries": 1,
        "offline": False,
        "evidence_dir": "evidence",
        "provider_file": None,
        "approval_policy": ASSET_APPROVAL_POLICY,
        "boundary_terms": ASSET_BOUNDARY_TERMS,
        "route_mode": "least-privilege",
        "max_text_bytes": 2000000,
        "network_probe": {"host": "1.1.1.1", "port": 443, "timeout_seconds": 0.6},
        "keep_probe_payloads": False,
        "evidence_root": ".",
    }
    defaults.update(config or {})
    if not isinstance(defaults.get("network_probe"), dict):
        raise lib.SkillError("INVALID_INPUT", "config.network_probe must be an object")
    return defaults


def resolve_policy_file(config, override=None):
    if override:
        return override
    value = config.get("approval_policy") or ASSET_APPROVAL_POLICY
    return value if os.path.isabs(value) else asset_path(value)


def resolve_terms_file(config, override=None):
    if override:
        return override
    value = config.get("boundary_terms") or ASSET_BOUNDARY_TERMS
    return value if os.path.isabs(value) else asset_path(value)


def resolve_schema(kind, override=None):
    if override:
        return override
    mapping = {
        "self-test": ASSET_SCHEMA_SELFTEST,
        "provenance": ASSET_SCHEMA_PROVENANCE,
        "parse": ASSET_SCHEMA_PARSE,
    }
    if kind not in mapping:
        raise lib.SkillError("INVALID_INPUT", f"unknown schema kind '{kind}'; use one of {sorted(mapping)}")
    return asset_path(mapping[kind])


def write_or_emit(payload, output):
    if output:
        lib.write_json(output, payload)
        return output
    lib.emit(payload)
    return None


def redact(text):
    """Remove obvious credentials from recorded commands."""
    if not text:
        return text
    patterns = [
        # Most specific first: scheme tokens, then key=value secrets, then emails.
        (r"(?i)(?<![a-z])(bearer|basic)\s+([A-Za-z0-9._~+/=\-]{6,})", r"\1 <redacted>"),
        (r"(?i)(\s-u\s+)([^\s:]+):([^\s]+)", r"\1<redacted>:<redacted>"),
        (r"(?i)(--(?:password|passwd|token|secret|key|api-key)[ =])(\S+)", r"\1<redacted>"),
        (r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization)(\s*[=:]\s*)(\S+)", r"\1\2<redacted>"),
        (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "<redacted-email>"),
    ]
    out = text
    for pattern, replacement in patterns:
        out = re.sub(pattern, replacement, out)
    return out


def host_of(args, config):
    probe = config.get("network_probe") or {}
    return lib.detect_host(
        offline=bool(args.offline or config.get("offline")),
        network_probe={"host": probe.get("host", "1.1.1.1"), "port": int(probe.get("port", 443))},
        network_timeout=float(probe.get("timeout_seconds", 0.6)),
    )


# ------------------------------------------------------------ detection -----
def probe_provider(entry, host, timeout, retries=1):
    """Probe one provider and return a contract-shaped record."""
    attempts = max(1, int(retries) + 1)
    record = {
        "capability_id": entry["id"],
        "label": entry["label"],
        "category": entry["category"],
        "status": lib.STATUS_MISSING,
        "detected_command": "",
        "detected_version": None,
        "host_type": host["host_type"],
        "architecture": host["architecture"],
        "requirements": entry["requirements"],
        "test_command": entry["version_cmd"],
        "exit_code": None,
        "evidence_path": "",
        "recommended_action": entry["install_hint"],
        "license_required": bool(entry["license_required"]),
        "version_window": {"min": entry["version_min"], "max": entry["version_max"]},
        "detail": "",
        "duration_ms": 0,
    }

    if entry.get("requires_module"):
        module = entry["requires_module"]
        probe = lib.run_capture(
            [sys.executable, "-c", f"import {module}, sys; print(getattr({module}, '__version__', 'unknown'))"],
            timeout=timeout,
        )
        record["detected_command"] = f"python3 -c 'import {module}'"
        record["exit_code"] = probe["exit_code"]
        record["duration_ms"] = probe["duration_ms"]
        if probe["exit_code"] != 0:
            record["status"] = entry["missing_status"]
            record["detail"] = f"Python module '{module}' is not importable"
            record["recommended_action"] = entry["install_hint"]
            return record
        record["detected_version"] = lib.parse_version(probe["stdout"], entry["version_regex"])
    elif not entry["tools"] and not entry["version_cmd"]:
        met, status, reason = lib.host_matches(entry["requirements"], host)
        record["status"] = lib.STATUS_FOUND if met else status
        record["detected_command"] = "(host state probe)"
        record["detail"] = reason
        record["detected_version"] = "present" if met else None
        record["test_command"] = record["detected_command"]
        record["exit_code"] = 0 if met else 1
        if not met:
            record["recommended_action"] = entry["install_hint"]
        return record
    else:
        executable = None
        for candidate in entry["tools"]:
            found = shutil.which(candidate)
            if found:
                executable = found
                break
        if not executable:
            record["status"] = entry["missing_status"]
            record["detected_command"] = entry["tools"][0] if entry["tools"] else ""
            record["detail"] = f"none of {entry['tools']} found on PATH"
            record["exit_code"] = 127
            return record
        record["detected_command"] = executable
        argv = [executable] + entry["_argv"][1:] if entry["_argv"] else [executable, "--version"]
        probe = None
        for attempt in range(attempts):
            probe = lib.run_capture(argv, timeout=timeout)
            if probe["exit_code"] == 0 and not probe["timed_out"]:
                break
            if attempt + 1 < attempts:
                time.sleep(0.15)
        record["exit_code"] = probe["exit_code"]
        record["duration_ms"] = probe["duration_ms"]
        combined = (probe["stdout"] + "\n" + probe["stderr"]).strip()
        record["detected_version"] = lib.parse_version(combined, entry["version_regex"])
        if probe["timed_out"]:
            record["status"] = lib.STATUS_BROKEN
            record["detail"] = f"probe timed out after {timeout}s"
            record["recommended_action"] = "Raise --timeout or repair the tool installation."
            return record
        if probe["exit_code"] != 0:
            record["status"] = lib.STATUS_BROKEN
            record["detail"] = f"probe exited {probe['exit_code']}: {combined[:300]}"
            record["recommended_action"] = "Tool is present but its version probe fails; repair or reinstall it."
            return record
        if not combined:
            record["status"] = lib.STATUS_BROKEN
            record["detail"] = "probe produced no output"
            return record

    if record["detected_version"] and (entry["version_min"] or entry["version_max"]):
        ok, reason = lib.version_in_range(record["detected_version"], entry["version_min"], entry["version_max"])
        if not ok:
            record["status"] = lib.STATUS_WRONG_VERSION
            window = f">= {entry['version_min']}" if entry["version_min"] else ""
            if entry["version_max"]:
                window += f" and <= {entry['version_max']}"
            record["detail"] = f"{reason} ({window.strip()})"
            record["recommended_action"] = f"Install a supported version ({window.strip()})."
            return record

    met, status, reason = lib.host_matches(entry["requirements"], host)
    if not met:
        record["status"] = status
        record["detail"] = reason
        record["recommended_action"] = entry["install_hint"] or f"Use a worker that satisfies {status}."
        return record

    if entry["license_required"]:
        record["status"] = lib.STATUS_LICENSE_REQUIRED
        record["detail"] = "tool detected but lawful use requires a licence you supply"
        record["recommended_action"] = "Obtain and document the vendor licence before use; do not bypass it."
        return record

    record["status"] = lib.STATUS_FOUND
    record["detail"] = entry.get("notes") or "probe succeeded"
    record["recommended_action"] = ""
    return record


def condense(records):
    """Acceptance summary used by reports and the determinism check."""
    return {
        "total": len(records),
        "by_status": _tally(record["status"] for record in records),
        "usable": sum(1 for r in records if r["status"] == lib.STATUS_FOUND),
        "blocked": sum(1 for r in records if r["status"] != lib.STATUS_FOUND),
    }


def _tally(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


# ------------------------------------------------------------- commands -----
def cmd_capabilities(args):
    payload = {
        "tool": lib.TOOL_ID,
        "version": TOOL_VERSION,
        "schema_version": lib.SCHEMA_VERSION,
        "python": platform.python_version(),
        "category": "Capability Discovery, Routing, and Governance",
        "capability_count": len(CAPABILITIES),
        "contract_statuses": list(lib.CONTRACT_STATUSES),
        "contract_fields": list(lib.CONTRACT_FIELDS),
        "capabilities": CAPABILITIES,
        "entry_point": "tools-capability_governance.py",
        "support_tools": [
            {"file": "tools-probe-lib.py", "role": "shared validation, hashing, host detection, parsing and evidence helpers"},
            {"file": "tools-provider-commands.py", "role": "data-driven provider probe table and host requirement rules"},
        ],
    }
    if args.verbose:
        payload["effective_policy"] = load_config(args.config)
    write_or_emit(payload, args.output)
    return lib.EXIT_OK


def cmd_inventory(args):
    config = load_config(args.config)
    host = host_of(args, config)
    timeout = float(args.timeout or config["timeout_seconds"])
    retries = int(config.get("retries", 1))
    provider_file = args.providers or config.get("provider_file")
    table = providers_mod.providers(provider_file)
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        unknown = wanted - {entry["id"] for entry in table}
        if unknown:
            raise lib.SkillError("INVALID_INPUT", f"unknown capability ids: {sorted(unknown)}")
        table = [entry for entry in table if entry["id"] in wanted]

    run_id = args.run_id or lib.new_run_id()
    writer = None
    if args.evidence:
        writer = lib.EvidenceWriter(args.evidence, run_id)

    records = []
    for entry in table:
        record = probe_provider(entry, host, timeout, retries)
        if writer:
            saved = writer.write(f"probe-{entry['id']}", record)
            record["evidence_path"] = saved["path"]
        records.append(record)

    payload = {
        "tool": lib.TOOL_ID,
        "tool_version": TOOL_VERSION,
        "schema_version": lib.SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": lib.utc_now(),
        "operation": "inventory",
        "host": host,
        "summary": condense(records),
        "capabilities": records,
    }
    if writer:
        payload["evidence"] = writer.manifest()
    write_or_emit(payload, args.output)
    return lib.EXIT_OK if payload["summary"]["blocked"] == 0 or not args.strict else lib.EXIT_FINDINGS


def cmd_self_test(args):
    config = load_config(args.config)
    host = host_of(args, config)
    timeout = float(args.timeout or config["timeout_seconds"])
    retries = int(config.get("retries", 1))
    provider_file = args.providers or config.get("provider_file")
    table = providers_mod.providers(provider_file)

    runs = max(1, min(int(args.check_runs or 1), 10))
    evidence_dir = args.evidence or config.get("evidence_dir") or "evidence"
    run_id = args.run_id or lib.new_run_id()
    writer = lib.EvidenceWriter(evidence_dir, run_id)

    started = time.time()
    iterations = []
    for index in range(runs):
        iteration_started = time.time()
        records = []
        for entry in table:
            record = probe_provider(entry, host, timeout, retries)
            saved = writer.write(f"probe-{entry['id']}" + (f"-run{index + 1}" if runs > 1 else ""), record)
            record["evidence_path"] = saved["path"]
            records.append(record)
        iterations.append(
            {
                "iteration": index + 1,
                "started_at": lib.utc_now(),
                "duration_ms": int((time.time() - iteration_started) * 1000),
                "records": records,
                "status_map": {r["capability_id"]: r["status"] for r in records},
                "version_map": {r["capability_id"]: r["detected_version"] for r in records},
            }
        )

    records = iterations[-1]["records"]
    determinism = None
    if len(iterations) > 1:
        baseline = iterations[0]["status_map"]
        drift = []
        for iteration in iterations[1:]:
            for capability_id, status in iteration["status_map"].items():
                if baseline.get(capability_id) != status:
                    drift.append(
                        {
                            "capability_id": capability_id,
                            "first": baseline.get(capability_id),
                            "later": status,
                            "iteration": iteration["iteration"],
                        }
                    )
        determinism = {"runs": len(iterations), "status_drift": drift, "deterministic": not drift}

    contract_issues = []
    for record in records:
        missing = [field for field in lib.CONTRACT_FIELDS if field not in record]
        if missing:
            contract_issues.append({"capability_id": record["capability_id"], "missing_fields": missing})
        if record["status"] not in lib.CONTRACT_STATUSES:
            contract_issues.append(
                {"capability_id": record["capability_id"], "invalid_status": record["status"]}
            )
    missing_tools = [r for r in records if r["status"] in (lib.STATUS_MISSING,)]
    silent_deps = [r["capability_id"] for r in records if r["status"] != lib.STATUS_FOUND and not r["recommended_action"] and r["status"] != lib.STATUS_LICENSE_REQUIRED]

    profile_result = None
    if args.profile:
        profile_result = evaluate_profile(args.profile, records, args.profiles)
    report = {
        "tool": lib.TOOL_ID,
        "tool_version": TOOL_VERSION,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "self-test",
        "run_id": run_id,
        "generated_at": lib.utc_now(),
        "host": host,
        "summary": condense(records),
        "deterministic": determinism,
        "contract": {
            "statuses": list(lib.CONTRACT_STATUSES),
            "fields": list(lib.CONTRACT_FIELDS),
            "issues": contract_issues,
            "valid": not contract_issues,
        },
        "capabilities": records,
        "capability_count": len(records),
        "missing_tool_count": len(missing_tools),
        "unactions": silent_deps,
        "evidence": writer.manifest(),
        "duration_ms": int((time.time() - started) * 1000),
        "preflight": _preflight_checks(config, table, host),
        "profile": profile_result,
        "ok": (not contract_issues and len(records) > 0) and (profile_result is None or profile_result["ready"]),
    }
    saved = writer.write("self-test-report", report)
    report["report_path"] = saved["path"]
    if args.output:
        lib.write_json(args.output, report)
    else:
        lib.emit(report)
    return lib.EXIT_OK if report["ok"] else lib.EXIT_FINDINGS


def evaluate_profile(name, records, path=None):
    """Evaluate a readiness profile (asset-readiness-profiles.json) against records."""
    source = path or asset_path(ASSET_READINESS_PROFILES)
    data = lib.read_json(source, "readiness profile file")
    profiles = data.get("profiles", {})
    key = name
    if key not in profiles:
        raise lib.SkillError("INVALID_INPUT", f"unknown readiness profile '{name}'", {"known": sorted(profiles)})
    profile = profiles[key]
    status_of = {record["capability_id"]: record["status"] for record in records}
    actions = {record["capability_id"]: record["recommended_action"] for record in records}
    fail_statuses = profile.get("fail_statuses", [lib.STATUS_MISSING])
    missing_required = []
    unmet = []
    for capability_id in profile.get("required", []):
        status = status_of.get(capability_id)
        if status is None:
            missing_required.append(capability_id)
            continue
        if status in fail_statuses or status != lib.STATUS_FOUND:
            unmet.append(
                {
                    "capability_id": capability_id,
                    "status": status,
                    "recommended_action": actions.get(capability_id, ""),
                }
            )
    optional_gaps = [
        {"capability_id": capability_id, "status": status_of.get(capability_id, "NOT IN PROFILE")}
        for capability_id in profile.get("optional", [])
        if status_of.get(capability_id) != lib.STATUS_FOUND
    ]
    ready = not missing_required and not unmet
    return {
        "profile": key,
        "description": profile.get("description", ""),
        "source": os.path.basename(source),
        "required": profile.get("required", []),
        "optional": profile.get("optional", []),
        "unknown_required_ids": missing_required,
        "unmet_required": unmet,
        "optional_gaps": optional_gaps,
        "ready": ready,
        "verdict": "READY" if ready else "NOT READY",
    }


def _preflight_checks(config, table, host):
    """Cheap sanity checks that the toolchain policy itself is coherent."""
    checks = []
    ids = [entry["id"] for entry in table]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    checks.append({"check": "provider_ids_unique", "ok": not duplicates, "detail": duplicates})
    bad_windows = [e["id"] for e in table if e["requirements"].get("os") == ["windows"] and host["host_type"] != "windows"]
    checks.append(
        {
            "check": "windows_only_providers_reported",
            "ok": True,
            "detail": f"{len(bad_windows)} provider(s) require a Windows host and are reported as WINDOWS HOST REQUIRED",
        }
    )
    windows = [
        entry
        for entry in table
        if entry["version_min"] and entry["version_max"]
        and lib.compare_versions(entry["version_min"], entry["version_max"]) > 0
    ]
    checks.append({"check": "version_windows_sane", "ok": not windows, "detail": [w["id"] for w in windows]})
    checks.append(
        {
            "check": "offline_mode_respected",
            "ok": True,
            "detail": f"network probes {'disabled' if host['offline_mode'] else 'enabled'}",
        }
    )
    return checks


def plan_stages(goal):
    """Decompose a desktop goal into the eight governed stages."""
    stages = [
        ("inspect", "read-only", "Collect evidence with read-only probes and record hashes."),
        ("preserve", "read-only", "Create hashes, backups or snapshots of every artifact before changes."),
        ("reproduce", "reversible", "Reproduce the state in a clean VM/worker without touching production."),
        ("repair", "privileged", "Apply the minimal change; requires an approval record."),
        ("test", "reversible", "Run acceptance and boundary-case tests in the sandbox."),
        ("package", "reversible", "Package artifacts with documented provenance."),
        ("verify", "read-only", "Confirm original failure, normal use, rollback and absence of regressions."),
        ("handoff", "read-only", "Deliver report, evidence paths and rollback commands."),
    ]
    return {
        "goal": goal,
        "stages": [
            {"stage": name, "approval": approval, "description": text, "required": True}
            for name, approval, text in stages
        ],
    }


def cmd_route(args):
    config = load_config(args.config)
    host = host_of(args, config)
    timeout = float(args.timeout or config["timeout_seconds"])
    table = providers_mod.providers(args.providers or config.get("provider_file"))
    index = {entry["id"]: entry for entry in table}

    if args.capability:
        wanted = [item.strip() for item in args.capability.split(",") if item.strip()]
    else:
        wanted = []
    if not wanted and not args.decompose:
        raise lib.SkillError("INVALID_INPUT", "route requires --capability <ids> (comma separated) or --decompose")
    unknown = [item for item in wanted if item not in index]
    if unknown:
        raise lib.SkillError(
            "INVALID_INPUT",
            f"unknown capability ids: {unknown}",
            {"known": sorted(index)[:100]},
        )

    records = [probe_provider(index[item], host, timeout) for item in wanted]
    needs = {"windows_host": False, "gpu": False, "virtualization": False, "network": False, "arm64": False}
    for entry in (index[item] for item in wanted):
        req = entry["requirements"]
        if "windows" in [o.lower() for o in req.get("os", [])]:
            needs["windows_host"] = True
        needs["gpu"] = needs["gpu"] or bool(req.get("gpu"))
        needs["virtualization"] = needs["virtualization"] or bool(req.get("virtualization"))
        needs["network"] = needs["network"] or bool(req.get("network"))
        if "arm64" in [a.lower() for a in req.get("arch", [])]:
            needs["arm64"] = True

    if needs["windows_host"]:
        worker = "windows-host"
    elif needs["gpu"]:
        worker = "gpu-worker"
    elif needs["arm64"]:
        worker = "arm64-worker"
    elif needs["virtualization"]:
        worker = "virtualized-worker"
    elif needs["network"]:
        worker = "network-enabled-worker"
    else:
        worker = "linux-local"

    ready = [r["capability_id"] for r in records if r["status"] == lib.STATUS_FOUND]
    not_ready = [
        {"capability_id": r["capability_id"], "status": r["status"], "recommended_action": r["recommended_action"]}
        for r in records
        if r["status"] != lib.STATUS_FOUND
    ]
    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "route",
        "generated_at": lib.utc_now(),
        "mode": args.route_mode or config.get("route_mode", "least-privilege"),
        "host": host,
        "requested_capabilities": wanted,
        "target_worker": worker,
        "hard_requirements": needs,
        "assignments": [
            {
                "capability_id": r["capability_id"],
                "status": r["status"],
                "detected_version": r["detected_version"],
                "worker": r["host_type"] if r["status"] == lib.STATUS_FOUND else worker,
            }
            for r in records
        ],
        "ready": ready,
        "not_ready": not_ready,
        "routable": len(ready) == len(wanted) and bool(wanted),
        "least_privilege_note": (
            "Local Linux worker satisfies every hard requirement; no privileged worker needed."
            if worker == "linux-local"
            else f"Routing requires a {worker}; record the justification before use."
        ),
        "recommended_action": (
            f"Proceed on the {worker}: every requested capability is usable there."
            if len(ready) == len(wanted) and bool(wanted)
            else (
                not_ready[0]["recommended_action"]
                or f"Provision or approve a {worker} that satisfies the hard requirements."
            )
        ),
    }
    if args.decompose:
        payload["task_plan"] = plan_stages(args.goal or "unspecified desktop goal")
    write_or_emit(payload, args.output)
    return lib.EXIT_OK if payload["routable"] or not wanted else lib.EXIT_FINDINGS


def cmd_approval_check(args):
    config = load_config(args.config)
    policy = lib.read_json(resolve_policy_file(config, args.policy), "approval policy")
    actions = []
    if args.actions_file:
        data = lib.read_json(args.actions_file, "actions file")
        raw_actions = data.get("actions") if isinstance(data, dict) else data
        if not isinstance(raw_actions, list) or not raw_actions:
            raise lib.SkillError("INVALID_INPUT", "actions file must contain a non-empty list")
        actions = raw_actions
    elif args.action:
        actions = [{"class": args.action_class, "name": args.action, "target": args.target or ""}]
    else:
        raise lib.SkillError("INVALID_INPUT", "approval-check requires --action and --action-class, or --actions-file")

    approvals = set()
    for item in args.approval or []:
        approvals.update(part.strip() for part in item.split(",") if part.strip())

    results = []
    worst = 0
    for raw in actions:
        if isinstance(raw, str):
            raw = {"class": raw}
        if not isinstance(raw, dict):
            raise lib.SkillError("INVALID_INPUT", "each action must be an object or a class string")
        action_class = (raw.get("class") or "read-only").strip().lower()
        blocked = policy.get("blocked_classes", {})
        if action_class in blocked:
            results.append(
                {
                    "action": raw.get("name") or action_class,
                    "target": raw.get("target", ""),
                    "class": action_class,
                    "risk_rank": 6,
                    "decision": "BLOCKED",
                    "approval_required": False,
                    "roles_required": [],
                    "roles_present": sorted(set(raw.get("approvals", []) or []) | approvals),
                    "missing_roles": [],
                    "reversible": False,
                    "reason": blocked[action_class],
                    "controls": ["refuse the bypass portion", "offer a lawful alternative"],
                }
            )
            worst = max(worst, lib.EXIT_BLOCKED)
            continue
        spec = policy["levels"].get(action_class)
        if not spec:
            raise lib.SkillError(
                "INVALID_INPUT",
                f"unknown action class '{action_class}'",
                {"known": sorted(policy["levels"]) + sorted(blocked)},
            )
        declared = set(raw.get("approvals", []) or [])
        present = sorted(declared | approvals)
        missing_roles = [role for role in spec["roles"] if role not in present]
        required = bool(spec["roles"]) and not raw.get("already_approved", False)
        allowed = not required or not missing_roles
        if spec.get("denied") or policy.get("blocked_classes", {}).get(action_class):
            decision = "BLOCKED"
            worst = max(worst, lib.EXIT_BLOCKED)
            reason = policy.get("blocked_classes", {}).get(action_class, "action class is prohibited")
        elif allowed:
            decision = "ALLOWED"
            reason = spec["description"]
        else:
            decision = "APPROVAL REQUIRED"
            worst = max(worst, lib.EXIT_APPROVAL_REQUIRED)
            reason = f"needs role(s): {', '.join(missing_roles)}"
        results.append(
            {
                "action": raw.get("name") or f"{action_class} action",
                "target": raw.get("target", ""),
                "class": action_class,
                "risk_rank": spec["rank"],
                "decision": decision,
                "approval_required": required,
                "roles_required": spec["roles"],
                "roles_present": present,
                "missing_roles": missing_roles,
                "reversible": spec.get("reversible", False),
                "reason": reason,
                "controls": spec.get("controls", []),
            }
        )

    overall = "ALLOWED" if worst == 0 else ("APPROVAL REQUIRED" if worst == lib.EXIT_APPROVAL_REQUIRED else "BLOCKED")
    approved_classes = [d["class"] for d in results if d["decision"] == "ALLOWED" and d["class"] != "read-only"]
    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "approval-check",
        "generated_at": lib.utc_now(),
        "policy_version": policy.get("policy_version"),
        "policy_source": os.path.basename(resolve_policy_file(config, args.policy)),
        "decisions": results,
        "overall": overall,
        "next_step": (
            "Approval requirements satisfied for: " + ", ".join(approved_classes) + ". Attach the approval record to the run evidence."
            if overall == "ALLOWED" and approved_classes
            else (
                "No approval needed: read-only or reversible action."
                if overall == "ALLOWED"
                else (
                    "Stop and collect the listed approval role(s) before running this action."
                    if overall == "APPROVAL REQUIRED"
                    else "Refuse: the requested class is a protected boundary; propose a lawful alternative."
                )
            )
        ),
    }
    write_or_emit(payload, args.output)
    return worst


def cmd_reversible_plan(args):
    target = os.path.abspath(args.target)
    if not os.path.exists(target):
        raise lib.SkillError("INVALID_INPUT", f"target does not exist: {args.target}")
    run_id = args.run_id or lib.new_run_id()
    backup_root = os.path.abspath(args.backup_dir or os.path.join("backups", run_id))
    if args.dest:
        dest = os.path.abspath(args.dest)
    else:
        dest = os.path.join(backup_root, os.path.basename(target.rstrip(os.sep)) or "root")

    target_prefix = target.rstrip(os.sep) + os.sep
    if backup_root == target or backup_root.startswith(target_prefix):
        raise lib.SkillError("INVALID_INPUT", "backup destination must not live inside the target")

    is_dir = os.path.isdir(target)
    if is_dir:
        digest, entry_count = lib.sha256_tree(target)
        size = None
        kind = "directory"
    else:
        digest = lib.sha256_file(target)
        size = os.path.getsize(target)
        entry_count = 1
        kind = "file"

    applied = False
    if args.apply:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        if os.path.exists(dest):
            raise lib.SkillError(
                "INVALID_INPUT",
                f"backup destination already exists, refusing to overwrite: {dest}",
                {"hint": "Choose a new --dest or remove the stale backup deliberately."},
            )
        if is_dir:
            shutil.copytree(target, dest, symlinks=True)
        else:
            shutil.copy2(target, dest)
        applied = True
        verify = lib.sha256_tree(dest)[0] if is_dir else lib.sha256_file(dest)
        if verify != digest:
            raise lib.SkillError("BROKEN", f"backup verification failed for {dest}")
    else:
        verify = None

    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "reversible-plan",
        "run_id": run_id,
        "generated_at": lib.utc_now(),
        "target": args.target,
        "target_kind": kind,
        "target_entries": entry_count,
        "target_bytes": size,
        "pre_change_sha256": digest,
        "backup": {
            "dest": dest.replace(os.sep, "/"),
            "applied": applied,
            "verified_sha256": verify,
            "matches_original": (verify == digest) if verify else None,
        },
        "rollback_command": (
            f"rm -rf {shlex.quote(target)} && cp -a {shlex.quote(dest)} {shlex.quote(target)}"
            if is_dir
            else f"cp -a {shlex.quote(dest)} {shlex.quote(target)}"
        ),
        "rollback_steps": (
            [
                f"rm -rf {shlex.quote(target)}   # destructive: confirm the backup hash first",
                f"cp -a {shlex.quote(dest)} {shlex.quote(target)}",
                f"python3 tools-capability_governance.py provenance --input {shlex.quote(target)}  # must equal {digest}",
            ]
            if is_dir
            else [
                f"cp -a {shlex.quote(dest)} {shlex.quote(target)}",
                f"sha256sum {shlex.quote(target)}  # must equal {digest}",
            ]
        ),
        "approval_required_for_plan": False,
        "warning": (
            "Restore deliberately: overwriting the current target destroys work done after this snapshot."
        ),
        "next_step": (
            "Snapshot created and verified; modifications may proceed with an approval record."
            if applied
            else "Review the plan, then re-run with --apply to create the backup, or copy the rollback steps into the run book."
        ),
    }
    write_or_emit(payload, args.output)
    return lib.EXIT_OK


def cmd_boundary_check(args):
    config = load_config(args.config)
    terms = lib.read_json(resolve_terms_file(config, args.terms), "boundary terms")
    if args.text_file:
        size = os.path.getsize(args.text_file)
        if size > int(config.get("max_text_bytes", 2000000)):
            raise lib.SkillError("INVALID_INPUT", f"text file exceeds max_text_bytes ({size} bytes)")
        with open(args.text_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif args.text:
        text = args.text
    else:
        raise lib.SkillError("INVALID_INPUT", "boundary-check requires --text or --text-file")
    if len(text.encode("utf-8")) > int(config.get("max_text_bytes", 2000000)):
        raise lib.SkillError("INVALID_INPUT", "input text exceeds max_text_bytes")

    normalized = lib.normalize_term_text(text)
    tokens = lib.term_tokens(normalized)
    matches = []
    seen_terms = set()
    for category in terms["categories"]:
        for term in category["terms"]:
            key = (category["id"], lib.normalize_term_text(term))
            if key in seen_terms:
                continue  # spelling variants (licence/license) collapse to one finding
            how = lib.match_term(normalized, tokens, term)
            if how:
                seen_terms.add(key)
                matches.append(
                    {
                        "category": category["id"],
                        "term": term,
                        "matched_by": how,
                        "severity": category["severity"],
                        "decision": category["decision"],
                        "rationale": category["rationale"],
                        "required_context": category.get("required_context", []),
                    }
                )
    negations = [marker for marker in terms.get("negation_markers", []) if lib.match_term(normalized, tokens, marker)]
    exemptions = []
    clues = []
    for item in terms.get("exemptions", []):
        hit = next((p for p in item["phrases"] if lib.match_term(normalized, tokens, p)), None)
        if not hit:
            continue
        strong = len(lib.term_tokens(lib.normalize_term_text(hit))) >= 2
        record = {
            "id": item["id"],
            "matched_phrase": hit,
            "strength": "strong" if strong else "weak",
            "why": item["why"],
            "covers": item.get("covers", []),
            "still_requires": item.get("still_requires", []),
        }
        if strong:
            exemptions.append(record)
        else:
            clues.append(record)

    blocking = [m for m in matches if m["decision"] == "BLOCK"]
    review = [m for m in matches if m["decision"] == "REVIEW"]
    covered = set() if negations else {cid for ex in exemptions for cid in ex["covers"]}
    exemptions_never_cover = set(terms.get("exemptions_never_cover", []))
    resolved_by_exemption = [m for m in review if m["category"] in covered and m["category"] not in exemptions_never_cover]
    review = [m for m in review if m not in resolved_by_exemption]
    authorization_note = None
    if args.authorization:
        authorization_note = f"User-declared authorization: {args.authorization}"

    if blocking:
        decision = "BLOCKED"
        exit_code = lib.EXIT_BLOCKED
        summary = "Refuse the bypass portion; keep any lawful compatibility or preservation work."
    elif review:
        decision = "REVIEW"
        exit_code = lib.EXIT_FINDINGS
        summary = "Collect the listed authorization, licence or privacy scope before proceeding."
    else:
        decision = "ALLOW"
        exit_code = lib.EXIT_OK
        summary = "No prohibited bypass condition detected."

    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "boundary-check",
        "generated_at": lib.utc_now(),
        "decision": decision,
        "matches": matches,
        "blocking": blocking,
        "review_required": review,
        "exemptions": exemptions,
        "context_clues": clues,
        "negation_markers_found": negations,
        "exemptions_applied": not negations,
        "resolved_by_exemption": resolved_by_exemption,
        "unmet_context": sorted({ctx for m in review for ctx in m["required_context"]}),
        "authorization": authorization_note,
        "boundary_rules": terms.get("global_rules", []),
        "summary": summary,
        "exit_code": exit_code,
    }
    write_or_emit(payload, args.output)
    return exit_code


def cmd_provenance(args):
    config = load_config(args.config)
    if not args.input:
        raise lib.SkillError("INVALID_INPUT", "provenance requires at least one --input")
    run_id = args.run_id or lib.new_run_id()
    inputs = []
    for raw in args.input:
        path = os.path.abspath(raw)
        if not os.path.exists(path):
            raise lib.SkillError("INVALID_INPUT", f"input does not exist: {raw}")
        if os.path.isdir(path):
            digest, count = lib.sha256_tree(path)
            inputs.append({"path": raw, "kind": "directory", "sha256": digest, "entries": count, "bytes": None})
        else:
            inputs.append(
                {
                    "path": raw,
                    "kind": "file",
                    "sha256": lib.sha256_file(path),
                    "entries": 1,
                    "bytes": os.path.getsize(path),
                }
            )
    table = providers_mod.providers(args.providers or config.get("provider_file"))
    host = host_of(args, config)
    tools = []
    for entry in table:
        if entry["id"] in ("python3", "git", "gcc", "clang", "cmake", "ninja", "7zip", "java", "node", "npm", "openssl", "sqlite3", "ffmpeg"):
            record = probe_provider(entry, host, float(config.get("timeout_seconds", 10)))
            tools.append(
                {
                    "capability_id": entry["id"],
                    "status": record["status"],
                    "detected_command": record["detected_command"],
                    "detected_version": record["detected_version"],
                }
            )
    payload = {
        "tool": lib.TOOL_ID,
        "tool_version": TOOL_VERSION,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "provenance",
        "run_id": run_id,
        "generated_at": lib.utc_now(),
        "host": host,
        "goal": args.note or "",
        "inputs": inputs,
        "tool_versions": tools,
        "commands": [{"argv": redact(item), "executed": False} for item in (args.command_argv or [])],
        "transforms": [],
        "chain_of_custody": [
            {"step": "record", "at": lib.utc_now(), "detail": "inputs hashed before any modification"}
        ],
        "integrity": {
            "algorithm": "sha256",
            "input_count": len(inputs),
            "recompute": "python3 tools-capability_governance.py provenance --input <path> --output <file>",
        },
        "notes": [
            "Commands are recorded, never executed by this tool.",
            "Credentials are redacted before being written.",
        ],
    }
    write_or_emit(payload, args.output)
    return lib.EXIT_OK


def cmd_parse(args):
    config = load_config(args.config)
    if args.source:
        size = os.path.getsize(args.source)
        if size > int(config.get("max_text_bytes", 2000000)):
            raise lib.SkillError("INVALID_INPUT", f"source exceeds max_text_bytes ({size} bytes)")
        with open(args.source, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise lib.SkillError("INVALID_INPUT", "parse requires --source <path>, --text <string> or piped stdin")
    detected_format = args.format
    if detected_format in (None, "auto"):
        lowered_head = text.lstrip()[:400].lower()
        if "<" in lowered_head and re.search(r"</?[a-z][\w:-]*[\s>/]", text[:4000], re.I):
            detected_format = "html"
        elif re.search(r"^\s{0,3}#{1,6}\s", text, re.M):
            detected_format = "markdown"
        else:
            detected_format = "text"

    parsed = lib.parse_catalog(text)
    for category in parsed["categories"]:
        declared = category["declared_count"]
        category["actual_count"] = len(category["skills"])
        category["count_matches"] = None if declared is None else declared == category["actual_count"]
        for internal in ("from_index", "declared_number", "index_only_headings_dropped"):
            category.pop(internal, None)
    counts = parsed["counts"]
    counts["format"] = detected_format
    counts["declared_total"] = sum(c["declared_count"] or 0 for c in parsed["categories"])
    counts["count_mismatches"] = [c["name"] for c in parsed["categories"] if c["count_matches"] is False]
    counts["index_entries"] = len(parsed.get("index_entries", []))
    counts["index_declared_total"] = sum(e["declared_count"] or 0 for e in parsed.get("index_entries", []))
    unnamed = [s["slug"] for c in parsed["categories"] for s in c["skills"] if not s["name"]]
    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "parse",
        "generated_at": lib.utc_now(),
        "source": args.source or "(inline text)",
        "format": detected_format,
        "counts": counts,
        "categories": parsed["categories"],
        "index_entries": parsed.get("index_entries", []),
        "skills": parsed["skills"],
        "empty_names": unnamed,
        "umbrella_suggestion": _umbrella(parsed),
    }
    write_or_emit(payload, args.output)
    return lib.EXIT_OK if parsed["skills"] else lib.EXIT_FINDINGS


def _umbrella(parsed):
    categories = [c for c in parsed["categories"] if c["skills"]]
    if len(categories) == 1:
        return {"category": categories[0]["name"], "slug": categories[0]["slug"], "skill_count": len(categories[0]["skills"])}
    if not categories:
        return None
    return {
        "category": "multiple",
        "categories": [c["name"] for c in categories],
        "skill_count": parsed["counts"]["skills"],
        "note": "Several categories detected; build one unified Skill Tool per category or ask for the target category.",
    }


def cmd_validate_output(args):
    if not args.input:
        raise lib.SkillError("INVALID_INPUT", "validate-output requires --input <document.json>")
    if len(args.input) > 1:
        raise lib.SkillError("INVALID_INPUT", "validate-output accepts exactly one --input document")
    document_path = args.input[0]
    schema_path = resolve_schema(args.kind, args.schema)
    schema = lib.read_json(schema_path, "schema")
    document = lib.read_json(document_path, "document")
    errors = validate_json(schema, document, path="$")
    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "validate-output",
        "input": document_path,
        "schema": os.path.basename(schema_path),
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors[:50],
    }
    write_or_emit(payload, args.output)
    return lib.EXIT_OK if not errors else lib.EXIT_FINDINGS


def validate_json(schema, value, path="$"):
    """Validate with the JSON Schema subset used by the packaged schemas."""
    errors = []
    expected = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if expected:
        wanted = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(value, type_map[t]) and not (t in ("number", "integer") and isinstance(value, bool)) for t in wanted if t in type_map):
            errors.append({"path": path, "error": f"expected type {wanted}, got {type(value).__name__}"})
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path, "error": f"value {value!r} not in enum {schema['enum']}"})
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errors.append({"path": path, "error": f"string {value!r} does not match pattern {schema['pattern']}"})
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        errors.append({"path": path, "error": f"shorter than minLength {schema['minLength']}"})
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        errors.append({"path": path, "error": f"below minimum {schema['minimum']}"})
    if "minItems" in schema and isinstance(value, list) and len(value) < schema["minItems"]:
        errors.append({"path": path, "error": f"fewer than minItems {schema['minItems']}"})
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"path": f"{path}.{key}", "error": "required property missing"})
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors.extend(validate_json(properties[key], item, f"{path}.{key}"))
            elif isinstance(additional, dict):
                errors.extend(validate_json(additional, item, f"{path}.{key}"))
            elif additional is False:
                errors.append({"path": f"{path}.{key}", "error": "additional property not allowed"})
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value[:1000]):
            errors.extend(validate_json(schema["items"], item, f"{path}[{index}]"))
    for combinator in ("allOf", "anyOf"):
        if combinator in schema:
            branch_results = [validate_json(sub, value, path) for sub in schema[combinator]]
            if combinator == "allOf":
                for result in branch_results:
                    errors.extend(result)
            elif all(branch_results):
                errors.append({"path": path, "error": "value matches no anyOf branch"})
    if "if" in schema:
        if not validate_json(schema["if"], value, path) and "then" in schema:
            errors.extend(validate_json(schema["then"], value, path))
    return errors


def cmd_policy(args):
    config = load_config(args.config)
    policy = lib.read_json(resolve_policy_file(config, args.policy), "approval policy")
    terms = lib.read_json(resolve_terms_file(config, args.terms), "boundary terms")
    table = providers_mod.providers(args.providers or config.get("provider_file"))
    windows = [e["id"] for e in table if "windows" in [o.lower() for o in e["requirements"].get("os", [])]]
    virtualized = [e["id"] for e in table if e["requirements"].get("virtualization")]
    licensed = [e["id"] for e in table if e["license_required"]]
    payload = {
        "tool": lib.TOOL_ID,
        "schema_version": lib.SCHEMA_VERSION,
        "operation": "policy",
        "generated_at": lib.utc_now(),
        "config": config,
        "config_source": os.path.basename(args.config or ASSET_DEFAULT_CONFIG),
        "approval_policy": {
            "version": policy.get("policy_version"),
            "levels": policy["levels"],
            "blocked": policy.get("blocked_classes", {}),
            "rule": policy.get("escalation_rule"),
        },
        "boundary_categories": [
            {"id": c["id"], "severity": c["severity"], "decision": c["decision"], "terms": len(c["terms"])}
            for c in terms["categories"]
        ],
        "providers": {
            "total": len(table),
            "windows_required": windows,
            "virtualization_required": virtualized,
            "licence_required": licensed,
        },
        "limits": {
            "probe_timeout_seconds": config["timeout_seconds"],
            "retries": config["retries"],
            "max_text_bytes": config["max_text_bytes"],
            "max_determinism_runs": 10,
        },
    }
    write_or_emit(payload, args.output)
    return lib.EXIT_OK


# ------------------------------------------------------------------ help ----
HELP_TEXT = """pc-capability-governance {version} - lawful PC capability discovery, routing and governance

USAGE
  python3 tools-capability_governance.py <command> [options]

COMMANDS
  capabilities     List implemented capabilities, contract statuses and fields.
  inventory        Detect tools, versions, host requirements and missing dependencies.
  self-test        Deterministic probes plus full Startup Self-Test Contract report.
  route            Route required capabilities to the least-privilege worker.
  approval-check   Classify actions and decide allow / approval required / blocked.
  reversible-plan  Hash a target and plan (or apply) a verified backup with rollback.
  boundary-check   Screen a request for prohibited bypass conditions.
  provenance       Record input hashes, tool versions, commands and custody chain.
  parse            Extract categories and Skill Tool names from text/Markdown/HTML.
  validate-output  Validate a JSON document against a packaged schema.
  policy           Show effective configuration, approval and boundary policy.
  help             Show this text, or help for one command.

GLOBAL OPTIONS
  --config PATH         Configuration file (default: asset-default-config.json).
  --providers PATH      Extra provider definitions merged over the built-in table.
  --offline             Disable the network reachability probe.
  --timeout SECONDS     Per-probe timeout (default from config: {timeout}).
  --run-id ID           Reuse a run identifier; otherwise generated.
  --evidence DIR        Write per-probe evidence records under DIR/<run-id>/.
  --output PATH         Write the JSON document to PATH instead of stdout.
  --verbose             Include the effective policy in `capabilities`.

EXAMPLES
  python3 tools-capability_governance.py capabilities
  python3 tools-capability_governance.py inventory --only gcc,cmake,dotnet-sdk --output inventory.json
  python3 tools-capability_governance.py self-test --evidence evidence --check-runs 2\n  python3 tools-capability_governance.py self-test --profile governance-baseline\n  python3 tools-capability_governance.py self-test --list-profiles
  python3 tools-capability_governance.py route --capability gcc,cmake --decompose --goal "rebuild tool"
  python3 tools-capability_governance.py approval-check --action "resize partition" --action-class destructive
  python3 tools-capability_governance.py reversible-plan --target ./app.exe --apply
  python3 tools-capability_governance.py boundary-check --text "bypass the license check"
  python3 tools-capability_governance.py parse --source catalog.md --output parse.json
  python3 tools-capability_governance.py validate-output --kind self-test --input report.json

EXIT CODES
  0 success   1 findings/not-routable   2 usage or validation error
  3 approval required   4 blocked by boundary policy   5 internal error

Run `python3 tools-capability_governance.py help <command>` for command options.
"""

COMMAND_HELP = {
    "capabilities": "capabilities [--verbose] [--output PATH]\n\nLists the 12 implemented capabilities with ids, commands, approval class and outputs.",
    "inventory": "inventory [--only IDS] [--providers PATH] [--evidence DIR] [--offline] [--strict] [--output PATH]\n\n--only: comma-separated provider ids. --strict: exit 1 when any capability is not FOUND.",
    "self-test": "self-test [--evidence DIR] [--check-runs N] [--offline] [--providers PATH] [--output PATH]\n\nRuns every provider probe, validates the contract fields, and (with --check-runs > 1) reports status drift.",
    "route": "route --capability IDS [--decompose] [--goal TEXT] [--route-mode least-privilege] [--output PATH]\n\nChooses linux-local, windows-host, gpu-worker, arm64-worker, virtualized-worker or network-enabled-worker.",
    "approval-check": "approval-check (--action TEXT --action-class CLASS | --actions-file PATH) [--approval ROLE,...] [--policy PATH]\n\nCLASS: read-only, reversible, privileged, networked, destructive, signing, financial, credential, publishing.",
    "reversible-plan": "reversible-plan --target PATH [--dest PATH] [--backup-dir DIR] [--apply] [--output PATH]\n\nWithout --apply nothing is copied: only hashes and rollback steps are produced.",
    "boundary-check": "boundary-check (--text TEXT | --text-file PATH) [--authorization TEXT] [--terms PATH] [--output PATH]",
    "provenance": "provenance --input PATH [--input PATH ...] [--command 'CMD'] [--note TEXT] [--output PATH]",
    "parse": "parse (--source PATH | --text TEXT | stdin) [--format auto|text|markdown|html] [--output PATH]",
    "validate-output": "validate-output --input PATH [--kind self-test|provenance|parse] [--schema PATH] [--output PATH]",
    "policy": "policy [--policy PATH] [--terms PATH] [--output PATH]",
    "help": "help [command]",
}


def build_parser():
    config = {}
    try:
        config = load_config(os.environ.get("PCGOV_CONFIG"))
    except lib.SkillError:
        config = {}
    parser = argparse.ArgumentParser(
        prog="tools-capability_governance.py",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Lawful PC capability discovery, routing and governance (one unified Skill Tool).",
    )
    parser.add_argument("command", nargs="?", default="help")
    parser.add_argument("topic", nargs="?", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--providers", default=None)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--only", default=None)
    parser.add_argument("--capability", default=None)
    parser.add_argument("--decompose", action="store_true")
    parser.add_argument("--goal", default=None)
    parser.add_argument("--route-mode", default=None)
    parser.add_argument("--check-runs", type=int, default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--profiles", default=None)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--action", default=None)
    parser.add_argument("--action-class", default="read-only")
    parser.add_argument("--actions-file", default=None)
    parser.add_argument("--approval", action="append", default=None)
    parser.add_argument("--target", default=None)
    parser.add_argument("--dest", default=None)
    parser.add_argument("--backup-dir", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--text", default=None)
    parser.add_argument("--text-file", default=None)
    parser.add_argument("--authorization", default=None)
    parser.add_argument("--terms", default=None)
    parser.add_argument("--input", action="append", default=None)
    parser.add_argument("--command", "--command-argv", dest="command_argv", action="append", default=None)
    parser.add_argument("--note", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--format", default=None)
    parser.add_argument("--kind", default="self-test")
    parser.add_argument("--schema", default=None)
    parser.add_argument("--policy", default=None)
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.command = args.command or "help"

    if args.help or args.command in ("help", "-h", "--help"):
        if args.topic and args.topic in COMMAND_HELP:
            print(COMMAND_HELP[args.topic])
        elif args.command in COMMAND_HELP and args.command != "help":
            print(COMMAND_HELP[args.command])
        else:
            config_timeout = 10
            try:
                config_timeout = load_config(args.config).get("timeout_seconds", 10)
            except Exception:
                pass
            print(HELP_TEXT.format(version=TOOL_VERSION, timeout=config_timeout))
        return lib.EXIT_OK

    if args.list_profiles:
        data = lib.read_json(args.profiles or asset_path(ASSET_READINESS_PROFILES), "readiness profile file")
        lib.emit(
            {
                "tool": lib.TOOL_ID,
                "default_profile": data.get("default_profile"),
                "profiles": [
                    {"name": key, "description": value.get("description", ""), "required": value.get("required", [])}
                    for key, value in data.get("profiles", {}).items()
                ],
            }
        )
        return lib.EXIT_OK

    handlers = {
        "capabilities": cmd_capabilities,
        "inventory": cmd_inventory,
        "self-test": cmd_self_test,
        "route": cmd_route,
        "approval-check": cmd_approval_check,
        "reversible-plan": cmd_reversible_plan,
        "boundary-check": cmd_boundary_check,
        "provenance": cmd_provenance,
        "parse": cmd_parse,
        "validate-output": cmd_validate_output,
        "policy": cmd_policy,
    }
    handler = handlers.get(args.command)
    if handler is None:
        lib.emit(
            lib.SkillError(
                "UNKNOWN_COMMAND",
                f"unknown command: {args.command}",
                {"commands": sorted(handlers)},
                lib.EXIT_USAGE,
            ).to_dict()
        )
        return lib.EXIT_USAGE

    try:
        return handler(args)
    except lib.SkillError as exc:
        lib.emit(exc.to_dict())
        return exc.exit_code
    except KeyboardInterrupt:
        lib.emit({"error": True, "status": "CANCELLED", "message": "interrupted by user", "exit_code": 130})
        return 130
    except Exception as exc:  # pragma: no cover - defensive
        lib.emit(
            {
                "error": True,
                "status": "INTERNAL ERROR",
                "message": f"{type(exc).__name__}: {exc}",
                "exit_code": lib.EXIT_INTERNAL,
            }
        )
        return lib.EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
