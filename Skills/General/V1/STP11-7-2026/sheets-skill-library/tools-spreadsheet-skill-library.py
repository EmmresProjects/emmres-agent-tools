#!/usr/bin/env python3
"""tools-spreadsheet-skill-library.py

Primary entry point for the `sheets-skill-library` Skill Tool component.

It turns the Google Sheets Agent Skill Catalog (28 categories / 280 named
capabilities) into an executable, reviewable activation library:

  catalog      browse, filter and search the capability catalog
  extract      parse a plain-text / Markdown / HTML capability list into catalog JSON
  plan         build per-capability activation plans (surfaces, validation, limits)
  build        produce the full activation bundle (plan, workbook, manifest, audit)
  approve      record a human approval for gated capabilities
  audit        scan text or a workbook for privacy / destructive / publishing risk
  formulas     generate and validate Google Sheets formulas from built-in patterns
  validate-workbook   inspect a produced .xlsx and report its structure
  check        validate config, catalog, assets and package structure
  capabilities list the operations of this tool
  self-test    run the built-in verification suite

Runtime: Python 3.9+ standard library. `openpyxl` is optional and only needed to
write/inspect workbooks; without it the bundle degrades to CSV + Markdown.

Exit codes: 0 ok, 2 invalid input/usage, 3 approval or integration blocked,
4 internal error, 130 cancelled.
"""

from __future__ import annotations

import argparse
import csv
import html as html_module
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.0.0"
TOOL_NAME = "sheets-skill-library"
PRIMARY_FILE = "tools-spreadsheet-skill-library.py"
KNOWLEDGE_FILE = "knowledge-readme.md"
CATALOG_ASSET = "asset-skill-catalog.json"
POLICY_ASSET = "asset-approval-policy.json"
CONTRACT_ASSET = "asset-output-contract.schema.json"
PLAN_TEMPLATE_ASSET = "asset-plan-template.md"
GOVERNANCE_ASSET = "asset-governance-checklist.md"
CONFIG_FILE = "tools-config.json"
NODE_PARSER = "tools-catalog-parser.mjs"

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_BLOCKED = 3
EXIT_INTERNAL = 4
EXIT_CANCELLED = 130

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_ID_RE = re.compile(r"^\d{1,3}\.\d{1,3}$")

CAPABILITIES = [
    ("catalog", "Browse, filter and search the capability catalog (category/skill/text)."),
    ("extract", "Parse plain text, Markdown or HTML capability lists into catalog JSON."),
    ("plan", "Build activation plans: surfaces, approval gates, validation, limits, tests."),
    ("build", "Produce the activation bundle: plan JSON/MD, review workbook, manifest, audit."),
    ("approve", "Record an expiring human approval for gated capabilities (with backup)."),
    ("audit", "Scan text or workbooks for PII, secrets, destructive and publishing risk."),
    ("formulas", "Generate validated Google Sheets formulas from the built-in pattern pack."),
    ("validate-workbook", "Inspect an .xlsx and report sheets, formulas, validation, protection."),
    ("check", "Validate config, catalog, assets, references and package structure."),
    ("capabilities", "List the operations implemented by this tool."),
    ("self-test", "Run the built-in verification suite (valid, invalid, blocked, recovery)."),
]

# --------------------------------------------------------------------------- #
# Cancellation / cleanup registry
# --------------------------------------------------------------------------- #

_CANCELLED = {"flag": False}
_CLEANUP: list[str] = []


def _install_signal_handlers() -> None:
    def _handler(signum, frame):  # noqa: ARG001
        _CANCELLED["flag"] = True
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass


def register_cleanup(path: Path) -> None:
    _CLEANUP.append(str(path))


def run_cleanup() -> None:
    for item in list(_CLEANUP):
        try:
            p = Path(item)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
        except OSError:
            pass
        finally:
            _CLEANUP.remove(item)


# --------------------------------------------------------------------------- #
# Errors and structured output
# --------------------------------------------------------------------------- #


class ToolError(Exception):
    """Structured, actionable failure."""

    def __init__(self, code: str, message: str, hint: str = "", exit_code: int = EXIT_INVALID):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.exit_code = exit_code

    def as_dict(self) -> dict:
        return {
            "ok": False,
            "tool": TOOL_NAME,
            "version": VERSION,
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "exit_code": self.exit_code,
            },
        }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Emitter:
    """Handles human vs JSON output consistently."""

    def __init__(self, fmt: str = "text", quiet: bool = False):
        self.fmt = fmt
        self.quiet = quiet

    def ok(self, command: str, data: dict, text: str | None = None) -> None:
        if self.fmt == "json":
            payload = {"ok": True, "tool": TOOL_NAME, "version": VERSION, "command": command, "data": data}
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        elif not self.quiet and text is not None:
            print(text)

    def warn(self, message: str) -> None:
        if self.fmt == "json":
            print(json.dumps({"ok": True, "warning": message}, ensure_ascii=False), file=sys.stderr)
        elif not self.quiet:
            print(f"WARN  {message}", file=sys.stderr)

    def info(self, message: str) -> None:
        if self.fmt == "text" and not self.quiet:
            print(message)


# --------------------------------------------------------------------------- #
# Filesystem helpers (bounded, atomic, retrying)
# --------------------------------------------------------------------------- #


def read_text_bounded(path: Path, max_bytes: int) -> str:
    if not path.exists():
        raise ToolError("FILE_NOT_FOUND", f"Input file not found: {path}", "Pass an existing path with --input/--catalog.")
    if not path.is_file():
        raise ToolError("NOT_A_FILE", f"Expected a file but found a directory: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ToolError(
            "INPUT_TOO_LARGE",
            f"{path.name} is {size} bytes which exceeds the configured limit of {max_bytes} bytes.",
            "Raise limits.max_input_bytes in tools-config.json or split the input.",
        )
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def atomic_write_text(path: Path, text: str, retries: int = 3, backoff: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return
        except OSError as exc:  # pragma: no cover - platform dependent
            last = exc
            tmp.unlink(missing_ok=True)
            time.sleep(backoff * attempt)
    raise ToolError("WRITE_FAILED", f"Could not write {path}: {last}", "Check permissions and free space.")


def atomic_write_json(path: Path, data, retries: int = 3) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", retries=retries)


def load_json(path: Path, max_bytes: int) -> dict:
    raw = read_text_bounded(path, max_bytes)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "JSON_INVALID",
            f"{path.name} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}.",
            "Re-export the file or run the relevant --reset-* recovery flag.",
        )
    if not isinstance(data, dict):
        raise ToolError("JSON_SHAPE", f"{path.name} must contain a JSON object at the top level.")
    return data


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "version": 1,
    "paths": {
        "catalog": CATALOG_ASSET,
        "policy": POLICY_ASSET,
        "contract": CONTRACT_ASSET,
        "formula_patterns": "asset-formula-patterns.json",
        "worksheet_template": PLAN_TEMPLATE_ASSET,
        "governance_checklist": GOVERNANCE_ASSET,
        "default_output_dir": "runs",
        "approval_store": "runs/approvals.json",
    },
    "limits": {
        "max_input_bytes": 4000000,
        "max_skills_per_build": 280,
        "max_catalog_categories": 60,
        "max_skills_per_category": 40,
        "max_findings_per_audit": 200,
        "workbook_row_cap": 5000,
    },
    "timeouts": {"read_seconds": 30, "write_seconds": 30, "workbook_seconds": 120},
    "retries": {"file_write": 3, "backoff_seconds": 0.2},
    "integrations": {
        "drive": True,
        "sheets_api": True,
        "forms": False,
        "apps_script": False,
        "gmail": False,
        "calendar": False,
        "bigquery": False,
        "looker": False,
        "addons": False,
    },
    "outputs": {
        "write_workbook": True,
        "write_csv_fallback": True,
        "plan_json": "activation-plan.json",
        "plan_markdown": "activation-plan.md",
        "workbook": "activation-review.xlsx",
        "manifest": "bundle-manifest.json",
        "audit_report": "risk-audit.json",
        "csv_fallback": "activation-plan.csv",
    },
    "capabilities": {"allow_derived_semantics": False, "default_risk": "medium"},
    "approvals": {"ttl_hours": 24, "require_human_confirmation": True, "gate_scope": "*"},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(config_path: str | None, skill_root: Path, max_bytes: int = 4_000_000) -> dict:
    path = Path(config_path) if config_path else skill_root / CONFIG_FILE
    if path.exists():
        cfg = deep_merge(DEFAULT_CONFIG, load_json(path, max_bytes))
        cfg["_config_path"] = str(path)
    else:
        if config_path:
            raise ToolError("CONFIG_NOT_FOUND", f"Config file not found: {path}", f"Use the shipped {CONFIG_FILE} or omit --config.")
        cfg = dict(DEFAULT_CONFIG)
        cfg["_config_path"] = "(built-in defaults)"
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    for section in ("paths", "limits", "timeouts", "retries", "outputs", "integrations", "capabilities"):
        if not isinstance(cfg.get(section), dict):
            raise ToolError("CONFIG_INVALID", f"Config section '{section}' must be an object.")
    for key in ("max_input_bytes", "max_skills_per_build", "workbook_row_cap"):
        value = cfg["limits"].get(key)
        if not isinstance(value, int) or value <= 0:
            raise ToolError("CONFIG_INVALID", f"limits.{key} must be a positive integer.")
    for key, value in cfg["integrations"].items():
        if not isinstance(value, bool):
            raise ToolError("CONFIG_INVALID", f"integrations.{key} must be true or false.")
    for key, value in cfg["outputs"].items():
        if isinstance(value, str) and ("/" in value or "\\" in value):
            raise ToolError(
                "CONFIG_INVALID",
                f"outputs.{key} must be a bare filename, got '{value}'.",
                "Output filenames are placed inside --out; do not embed directories.",
            )
    if cfg["limits"]["max_skills_per_build"] > 1000:
        raise ToolError("CONFIG_INVALID", "limits.max_skills_per_build must be <= 1000.")


# --------------------------------------------------------------------------- #
# Catalog loading and validation
# --------------------------------------------------------------------------- #


def validate_catalog(doc: dict, limits: dict) -> list[str]:
    problems: list[str] = []
    cats = doc.get("categories")
    if not isinstance(cats, list) or not cats:
        return ["catalog.categories must be a non-empty array"]
    if len(cats) > limits["max_catalog_categories"]:
        problems.append(f"catalog has {len(cats)} categories, limit is {limits['max_catalog_categories']}")
    seen_ids: set[str] = set()
    total = 0
    for cat in cats:
        if not isinstance(cat, dict):
            problems.append("category entry is not an object")
            continue
        name = str(cat.get("name", "")).strip()
        slug = str(cat.get("slug", "")).strip()
        if not name:
            problems.append("category missing name")
        if not SLUG_RE.match(slug):
            problems.append(f"category slug not lowercase-hyphenated: {slug!r}")
        skills = cat.get("skills")
        if not isinstance(skills, list) or not skills:
            problems.append(f"category {name!r} has no skills")
            continue
        if len(skills) > limits["max_skills_per_category"]:
            problems.append(f"category {name!r} has {len(skills)} skills, limit {limits['max_skills_per_category']}")
        for sk in skills:
            total += 1
            sid = str(sk.get("id", ""))
            if not SKILL_ID_RE.match(sid):
                problems.append(f"skill id not in N.N form: {sid!r}")
            if sid in seen_ids:
                problems.append(f"duplicate skill id: {sid}")
            seen_ids.add(sid)
            if not str(sk.get("name", "")).strip():
                problems.append(f"skill {sid} missing name")
            if not SLUG_RE.match(str(sk.get("slug", "")).strip()):
                problems.append(f"skill {sid} slug not lowercase-hyphenated")
            if not str(sk.get("description", "")).strip():
                problems.append(f"skill {sid} missing description")
    if isinstance(doc.get("skill_count"), int) and doc["skill_count"] != total:
        problems.append(f"declared skill_count {doc['skill_count']} != actual {total}")
    return problems


def load_catalog(path: Path, limits: dict) -> dict:
    doc = load_json(path, limits["max_input_bytes"])
    problems = validate_catalog(doc, limits)
    if problems:
        preview = "; ".join(problems[:6])
        raise ToolError(
            "CATALOG_INVALID",
            f"Catalog failed validation ({len(problems)} problem(s)): {preview}",
            "Regenerate with `extract`, or fix the flagged entries and re-run `check`.",
        )
    return doc


def catalog_index(doc: dict) -> tuple[dict, list[dict]]:
    """Return (skills_by_id, flat_skill_list) with category context attached."""
    by_id: dict[str, dict] = {}
    flat: list[dict] = []
    for cat in doc["categories"]:
        for sk in cat["skills"]:
            record = dict(sk)
            record["category_id"] = cat.get("number")
            record["category"] = cat["name"]
            record["category_slug"] = cat["slug"]
            by_id[record["id"]] = record
            flat.append(record)
    return by_id, flat


# --------------------------------------------------------------------------- #
# Selection resolution
# --------------------------------------------------------------------------- #


def split_repeated(values) -> list[str]:
    """Expand repeatable options so 'a,b,c' behaves like three separate flags."""
    out: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def parse_number_spec(spec: str) -> list[int]:
    """Accept '1,3,7-9' style integer selections."""
    out: list[int] = []
    for chunk in re.split(r"[,\s]+", spec.strip()):
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            try:
                a, b = int(lo), int(hi)
            except ValueError:
                raise ToolError("SELECTION_INVALID", f"Bad range in selection: {chunk!r}", "Use forms like 3,7-9.")
            if a > b or b - a > 200:
                raise ToolError("SELECTION_INVALID", f"Invalid or oversized range: {chunk!r}")
            out.extend(range(a, b + 1))
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                raise ToolError("SELECTION_INVALID", f"Bad number in selection: {chunk!r}", "Use integers or ranges.")
    return out


def resolve_selection(doc: dict, args) -> tuple[list[dict], dict]:
    by_id, flat = catalog_index(doc)
    selected: list[dict] = []
    notes: list[str] = []

    def add(skill: dict, why: str) -> None:
        if skill["id"] not in {s["id"] for s in selected}:
            selected.append(skill)
            notes.append(f"+ {skill['id']} {skill['name']} ({why})")

    want_all = bool(getattr(args, "all", False))
    categories = split_repeated(getattr(args, "category", None))
    skills = split_repeated(getattr(args, "skill", None))
    search = getattr(args, "search", None)
    list_file = getattr(args, "list_file", None)

    if want_all:
        for sk in flat:
            add(sk, "all")

    for spec in categories:
        ids = None
        try:
            ids = parse_number_spec(spec)
        except ToolError:
            ids = None
        matched = []
        for cat in doc["categories"]:
            hit = False
            if ids is not None:
                hit = cat.get("number") in ids
            if not hit:
                needle = spec.strip().lower()
                hit = needle in cat["name"].lower() or needle == cat.get("slug", "").lower()
            if hit:
                matched.append(cat)
        if not matched:
            raise ToolError(
                "CATEGORY_NOT_FOUND",
                f"No catalog category matched '{spec}'.",
                "Run `catalog --format text` to list valid category numbers and names.",
            )
        for cat in matched:
            for sk in cat["skills"]:
                add(dict(sk, **{"category": cat["name"], "category_id": cat.get("number"), "category_slug": cat["slug"]}), "category")

    for spec in skills:
        found = None
        key = spec.strip()
        if key in by_id:
            found = by_id[key]
        else:
            slug_like = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
            for sk in flat:
                if sk["slug"] == slug_like or key.lower() == sk["name"].lower():
                    found = sk
                    break
        if found is None:
            raise ToolError(
                "SKILL_NOT_FOUND",
                f"No catalog skill matched '{spec}'.",
                "Use `catalog --search <text>` to find the skill id (for example 13.2).",
            )
        add(found, "skill")

    if search:
        needle = search.lower()
        for sk in flat:
            haystack = f"{sk['name']} {sk['description']} {sk['category']}".lower()
            if needle in haystack:
                add(sk, "search")

    if list_file:
        lpath = Path(list_file)
        raw = read_text_bounded(lpath, 2_000_000)
        for line in raw.splitlines():
            item = line.strip(" -*\t")
            if not item:
                continue
            if item in by_id:
                add(by_id[item], "list-file")
                continue
            slug_like = re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-")
            hit = next((s for s in flat if s["slug"] == slug_like or s["name"].lower() == item.lower()), None)
            if hit:
                add(hit, "list-file")
            else:
                notes.append(f"! list-file entry not recognised: {item}")

    if not selected:
        raise ToolError(
            "SELECTION_EMPTY",
            "No capabilities were selected.",
            "Pass --all, --category, --skill, --search or --list-file. Try --category 13.",
        )

    max_skills = getattr(args, "max_skills", None)
    if max_skills is not None and max_skills > 0:
        selected = selected[:max_skills]
    if len(selected) > 280:
        raise ToolError("SELECTION_TOO_LARGE", f"Selection contains {len(selected)} capabilities (hard cap 280).")

    report = {"count": len(selected), "notes": notes[:60], "truncated_notes": max(0, len(notes) - 60)}
    return selected, report


def resolve_limits(args, cfg: dict) -> dict:
    limits = dict(cfg["limits"])
    if getattr(args, "max_skills", None):
        limits["max_skills_per_build"] = args.max_skills
    return limits


def enforce_build_limit(selected: list[dict], cfg: dict) -> None:
    cap = cfg["limits"]["max_skills_per_build"]
    if len(selected) > cap:
        raise ToolError(
            "BUILD_LIMIT_EXCEEDED",
            f"{len(selected)} capabilities selected but limits.max_skills_per_build is {cap}.",
            "Raise the limit in tools-config.json, pass --max-skills, or narrow the selection.",
        )


# --------------------------------------------------------------------------- #
# Capability engine: surfaces, risk, gates
# --------------------------------------------------------------------------- #

SURFACES = {
    "A": "copilot_answer",
    "L": "local_docs",
    "S": "sheets_cells",
    "W": "workbook_batch",
    "D": "drive_file",
    "X": "pdf_export",
    "C": "csv_export",
    "H": "chart",
    "V": "pivot",
    "F": "form",
    "G": "gmail",
    "K": "calendar",
    "B": "bigquery",
    "R": "looker",
    "P": "apps_script",
    "N": "addon",
}

SURFACE_INTEGRATION = {
    "drive_file": "drive",
    "form": "forms",
    "gmail": "gmail",
    "calendar": "calendar",
    "bigquery": "bigquery",
    "looker": "looker",
    "apps_script": "apps_script",
    "addon": "addons",
}

SURFACE_VALIDATION = {
    "sheets_cells": "Confirm the target sheet and range exist and the header row is not overwritten.",
    "workbook_batch": "Confirm workbook opens, target sheet index is stable, and a pre-change copy exists.",
    "drive_file": "Confirm Drive identity, target folder, and that no duplicate name collision is created.",
    "pdf_export": "Confirm page size, orientation, scale and print area before export.",
    "csv_export": "Confirm delimiter, encoding, quoting and header row before export.",
    "chart": "Confirm chart source range covers all rows and no axis truncation misrepresents data.",
    "pivot": "Confirm pivot source range excludes totals rows and includes new records.",
    "form": "Confirm form ownership, response destination sheet and question types are compatible.",
    "gmail": "Confirm recipient list, quota headroom and duplicate-send protection before sending.",
    "calendar": "Confirm attendee list, time zone and date range before creating events.",
    "bigquery": "Confirm project, dataset, cost guard and query dry-run before running.",
    "looker": "Confirm Looker permission scope and that returned columns match the documented schema.",
    "apps_script": "Confirm OAuth scopes are minimal, script is reviewed, and a tested deployment exists.",
    "addon": "Confirm Marketplace listing identity, requested scopes and administrator approval.",
    "copilot_answer": "Confirm the answer states assumptions and the platform feature being described.",
    "local_docs": "Confirm every referenced path resolves relative to the skill root.",
}

SURFACE_LIMITS = {
    "sheets_cells": "Direct cell writes are limited to the reviewed workbook; Google Sheets live operations require the Sheets API integration.",
    "drive_file": "Drive operations require the drive integration and an authenticated account.",
    "gmail": "Email sending is described only; the gmail integration is disabled by default and App Script quotas apply (approx. 100 recipients/day consumer).",
    "calendar": "Calendar operations require the calendar integration; no events are created by this tool.",
    "bigquery": "Queries are described, never executed: the bigquery integration is disabled by default and cost guard must be set in the destination tenant.",
    "looker": "Looker content is described only; the looker integration is disabled by default.",
    "form": "Forms are described only; the forms integration is disabled by default.",
    "apps_script": "Apps Script is described only; generated code must be pasted and reviewed in the tenant by a human.",
    "addon": "Add-ons are described only; the addons integration is disabled by default.",
    "pdf_export": "PDF rendering is not performed locally: export steps are generated for a human or the Drive export path.",
    "chart": "Charts are specified, not rendered; chart rendering happens in Google Sheets.",
    "pivot": "Pivot tables are specified, not rendered; creation happens in Google Sheets.",
    "csv_export": "Largest safe local export is governed by limits.workbook_row_cap.",
    "workbook_batch": "Local workbook operations are capped by limits.workbook_row_cap and limits.max_input_bytes.",
    "copilot_answer": "Text output only; nothing is applied to a live workbook.",
    "local_docs": "Local file output only; nothing is applied to a live workbook.",
}

RISK_BASE_GATES = {
    "low": [],
    "medium": ["review_before_publish"],
    "high": ["review_before_publish", "backup_before_change", "approval_required"],
    "critical": ["review_before_publish", "backup_before_change", "approval_required", "security_sign_off"],
}


def capability_key(skill: dict) -> str:
    """Identity of a capability is id + name + slug, so ids re-used by a
    different source list can never inherit the wrong semantics."""
    return f"{skill['id']}|{skill['name']}|{skill['slug']}"


def load_capability_docs(path: Path, limits: dict) -> dict:
    doc = load_json(path, limits["max_input_bytes"])
    caps = doc.get("capabilities")
    if not isinstance(caps, list) or not caps:
        raise ToolError("CAPABILITY_DOC_INVALID", f"{path.name} must contain a non-empty 'capabilities' array.")
    by_key: dict[str, dict] = {}
    for entry in caps:
        cid = str(entry.get("id", ""))
        if not SKILL_ID_RE.match(cid):
            raise ToolError("CAPABILITY_DOC_INVALID", f"Capability entry has bad id: {cid!r}")
        name = str(entry.get("name", "")).strip()
        slug = str(entry.get("slug", "")).strip()
        if not name or not slug:
            raise ToolError(
                "CAPABILITY_DOC_INVALID",
                f"Capability entry {cid} must declare 'name' and 'slug' (regenerate it from the catalog).",
            )
        by_key[capability_key({"id": cid, "name": name, "slug": slug})] = entry
    doc["_by_key"] = by_key
    doc["_by_id"] = {cid: entry for entry in caps for cid in (str(entry["id"]),)}
    return doc


def derive_gates(skill: dict, capdoc: dict, policy: dict, cfg: dict) -> list[dict]:
    """Gate list = risk tier baseline + keyword triggers, minus disabled integrations."""
    risk = capdoc.get("risk", "low")
    # Trigger matching uses the catalogued name/description only, so generic
    # workflow boilerplate cannot inflate the gate list.
    keywords_blob = f"{skill['name']} {skill['description']}".lower()

    gates: list[dict] = []
    seen: set[str] = set()

    def add_gate(gate_id: str, reason: str, requires_approval: bool) -> None:
        if gate_id in seen:
            return
        seen.add(gate_id)
        gates.append(
            {
                "gate": gate_id,
                "reason": reason,
                "approval_required": requires_approval,
                "ttl_hours": cfg["approvals"]["ttl_hours"],
            }
        )

    for gate_id in RISK_BASE_GATES.get(risk, []):
        spec = policy["gates"].get(gate_id, {})
        add_gate(gate_id, spec.get("reason", f"Baseline gate for {risk} risk work."), bool(spec.get("approval_required")))

    for trigger in policy["triggers"]:
        if any(word in keywords_blob for word in trigger.get("keywords", [])):
            gate_id = trigger["gate"]
            spec = policy["gates"].get(gate_id, {})
            reason = trigger.get("reason") or spec.get("reason") or f"Triggered by capability wording ({gate_id})."
            requires = bool(spec.get("approval_required", trigger.get("approval_required", True)))
            add_gate(gate_id, reason, requires)

    unavailable = [
        SURFACE_INTEGRATION[s]
        for s in capdoc.get("surfaces", [])
        if s in SURFACE_INTEGRATION and not cfg["integrations"].get(SURFACE_INTEGRATION[s], False)
    ]
    if unavailable:
        gates.append(
            {
                "gate": "external_integration_unavailable",
                "reason": "Requires integrations that are disabled in config: " + ", ".join(sorted(set(unavailable))),
                "approval_required": False,
                "ttl_hours": 0,
                "integration": sorted(set(unavailable)),
            }
        )
    return gates


def risk_for(capdoc: dict) -> str:
    return capdoc.get("risk", "low")


def build_activation_plan(skill: dict, capdoc: dict, policy: dict, cfg: dict, limits: dict) -> dict:
    surfaces = [SURFACES[c] for c in capdoc.get("surfaces", []) if c in SURFACES]
    if not surfaces:
        surfaces = ["copilot_answer"]
    gates = derive_gates(skill, capdoc, policy, cfg)
    unavailable = [
        SURFACE_INTEGRATION[s]
        for s in surfaces
        if s in SURFACE_INTEGRATION and not cfg["integrations"].get(SURFACE_INTEGRATION[s], False)
    ]
    validation = [SURFACE_VALIDATION[s] for s in surfaces if s in SURFACE_VALIDATION]
    validation.extend(capdoc.get("validation", []))
    limits_text = [SURFACE_LIMITS[s] for s in surfaces if s in SURFACE_LIMITS]
    limits_text.extend(capdoc.get("limits", []))
    limits_text.append(
        f"Per-run cap: {limits['workbook_row_cap']} rows, {limits['max_input_bytes']} input bytes, "
        f"{cfg['timeouts']['workbook_seconds']}s workbook timeout, {cfg['retries']['file_write']} write retries."
    )
    fallbacks = list(capdoc.get("fallbacks", []))
    fallbacks.append(
        "If openpyxl is unavailable, the workbook deliverable degrades to CSV + Markdown with identical content."
    )
    if unavailable:
        fallbacks.append(
            "Disabled integrations (" + ", ".join(sorted(set(unavailable))) + "): deliver reviewed manual steps instead of automation."
        )
    outputs = sorted(
        {
            "activation plan (JSON + Markdown)",
            "review rows for the activation workbook",
            *(
                ["blocked/pending approval register"]
                if any(g["approval_required"] for g in gates)
                else ["ready-to-run checklist"]
            ),
        }
    )
    tests = [
        f"Plan for {skill['id']} lists all {len(surfaces)} declared surfaces and every validation rule resolves.",
        "Gate list is reproduced exactly in the workbook Gate column.",
        "Re-running the build with the same inputs is byte-stable apart from the timestamp.",
    ]
    return {
        "id": skill["id"],
        "name": skill["name"],
        "slug": skill["slug"],
        "category": skill["category"],
        "category_id": skill["category_id"],
        "category_slug": skill["category_slug"],
        "purpose": skill["description"],
        "risk": risk_for(capdoc),
        "surfaces": surfaces,
        "surfaces_unavailable": sorted(set(unavailable)),
        "workflow": capdoc.get("workflow", []),
        "inputs": capdoc.get("inputs", []),
        "outputs": outputs,
        "validation": validation,
        "gates": gates,
        "approval_required": any(g["approval_required"] for g in gates),
        "limits": limits_text,
        "fallbacks": fallbacks,
        "tests": tests,
        "google_apis": capdoc.get("google_apis", []),
        "semantics": "derived" if capdoc.get("derived") else "catalogued",
        "execution_ready": not unavailable and not any(g["approval_required"] for g in gates),
    }


def group_by_category(plans: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for plan in plans:
        key = plan["category_slug"]
        entry = grouped.setdefault(
            key,
            {"category": plan["category"], "category_id": plan["category_id"], "skills": []},
        )
        entry["skills"].append(plan)
    return [grouped[k] for k in sorted(grouped, key=lambda s: grouped[s]["category_id"])]


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #


def approval_store_path(cfg: dict, out_dir: Path | None, skill_root: Path) -> Path:
    raw = Path(cfg["paths"]["approval_store"])
    if out_dir is not None:
        return out_dir / "approvals.json"
    if raw.is_absolute():
        return raw
    return skill_root / raw


def load_approvals(path: Path, limits: dict, reset: bool = False) -> dict:
    if reset or not path.exists():
        return {"version": 1, "approvals": {}}
    doc = load_json(path, limits["max_input_bytes"])
    doc.setdefault("approvals", {})
    return doc


def approval_status(store: dict, cap_id: str, gate: str, now: datetime) -> tuple[bool, str]:
    entry = store["approvals"].get(f"{cap_id}:{gate}") or store["approvals"].get(cap_id)
    if not entry:
        return False, "no approval recorded"
    try:
        expires = datetime.fromisoformat(str(entry["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False, "approval record has no valid expires_at"
    if expires <= now:
        return False, f"approval expired at {iso(expires)}"
    return True, f"approved by {entry.get('approver', 'unknown')} until {iso(expires)}"


def gate_report(plans: list[dict], store: dict, now: datetime) -> list[dict]:
    rows = []
    for plan in plans:
        for gate in plan["gates"]:
            if not gate["approval_required"]:
                rows.append({**gate, "capability_id": plan["id"], "capability": plan["name"], "state": "advisory", "detail": "no approval needed"})
                continue
            ok, detail = approval_status(store, plan["id"], gate["gate"], now)
            rows.append(
                {
                    **gate,
                    "capability_id": plan["id"],
                    "capability": plan["name"],
                    "state": "approved" if ok else "pending",
                    "detail": detail,
                }
            )
    return rows


# --------------------------------------------------------------------------- #
# List extraction (plain text / Markdown / copied HTML)
# --------------------------------------------------------------------------- #

INLINE_TAGS = (
    "span", "strong", "em", "b", "i", "u", "a", "code", "mark", "small", "sup", "sub",
    "abbr", "time", "font", "label", "kbd", "s", "del", "ins",
)
BLOCK_TAGS = ("p", "li", "ul", "ol", "div", "section", "article", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6")


def strip_html(raw: str) -> str:
    """Convert pasted HTML into plain text without requiring a parser library."""
    text = re.sub(r"(?is)<script.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    for tag in BLOCK_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    for tag in INLINE_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "", text)
    text = re.sub(r"<[^>]+>", "", text)  # any leftover tags
    text = html_module.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2013", "-").replace("\u2014", "-")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines)


NUM_PREFIX = re.compile(r"^\s*(?:[#>*-]+\s*)*(?:\(?\d{1,3}[.)]|\d{1,3}\s*\|)\s*")
CATEGORY_RE = re.compile(
    r"^\s*(?:[#>*\-]+\s*)*(?:(?P<num>\d{1,3})[.)]\s*)?(?:\|\s*)?(?P<name>[A-Z][A-Za-z0-9 ,/&'()\-]{3,90})\s*$"
)
SKILL_LINE_RE = re.compile(
    r"^\s*(?:[#>*\-]+\s*)*(?P<num>\d{1,3})[.)]\s*(?:Skill\s+Name|Skill|Capability|Tool|Name)\s*[:\-]?\s*(?P<name>.+?)\s*$",
    re.IGNORECASE,
)
LABELLED_SKILL_RE = re.compile(
    r"^\s*(?:[#>*\-]+\s*)*(?:Skill\s+Name|Skill|Capability)\s*[:\-]\s*(?P<name>.+?)\s*$",
    re.IGNORECASE,
)
DESC_RE = re.compile(r"^\s*(?:[#>*\-]+\s*)*(?:Description|Desc|Details)\s*[:\-]?\s*(?P<desc>.*)$", re.IGNORECASE)
NAME_DESC_RE = re.compile(r"^(?P<name>[^:]{3,90}?)\s*[:\-]\s*(?P<desc>[A-Z].{15,})$")
CATEGORY_HINT_RE = re.compile(r"\b(skill|capabilit|tool|feature|operations?|analysis|design|automation)\b", re.IGNORECASE)


def normalise_lines(raw: str) -> list[str]:
    if re.search(r"(?i)<(p|div|li|span|td|br|h[1-6])\b", raw):
        raw = strip_html(raw)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in raw.split("\n"):
        line = line.replace("\t", " ").strip()
        line = re.sub(r"^[-*+]\s+(?=\d{1,3}[.)])", "", line)  # markdown bullet before a number
        line = re.sub(r"\s{2,}", " ", line)
        if line:
            out.append(line)
    return out


def extract_catalog(raw: str, limits: dict) -> dict:
    if len(raw.encode("utf-8")) > limits["max_input_bytes"]:
        raise ToolError("INPUT_TOO_LARGE", "Input exceeds limits.max_input_bytes.", "Split the source or raise the limit.")
    lines = normalise_lines(raw)
    if not lines:
        raise ToolError("INPUT_EMPTY", "No usable text found in the input.", "Paste plain text, Markdown or HTML containing the capability list.")

    categories: list[dict] = []
    current: dict | None = None
    pending_desc_target: dict | None = None
    warnings: list[str] = []

    def new_category(name: str, num: int | None) -> dict:
        cat = {
            "number": num if num is not None else len(categories) + 1,
            "name": name.strip(" .:-"),
            "description": "",
            "skills": [],
            "_desc_lines": [],
        }
        categories.append(cat)
        return cat

    for line in lines:
        skill_match = SKILL_LINE_RE.match(line)
        labelled_match = None if skill_match else LABELLED_SKILL_RE.match(line)
        if skill_match or labelled_match:
            if current is None:
                current = new_category("extracted-capabilities", None)
            source_num = int(skill_match.group("num")) if skill_match else None
            raw_name = (skill_match or labelled_match).group("name")
            name = re.sub(r"[.。]\s*$", "", raw_name).strip()
            skill = {
                "number": source_num if source_num is not None else len(current["skills"]) + 1,
                "source_number": source_num,
                "name": name,
                "description": "",
                "_desc_lines": [],
            }
            current["skills"].append(skill)
            pending_desc_target = skill
            continue

        desc_match = DESC_RE.match(line)
        if desc_match and pending_desc_target is not None:
            pending_desc_target["_desc_lines"].append(desc_match.group("desc").strip())
            continue

        if pending_desc_target is not None and not SKILL_LINE_RE.match(line):
            # continuation of the previous description unless it looks like a heading
            if CATEGORY_RE.match(line) and _looks_like_category(line) and not line.endswith("."):
                pending_desc_target = None
            else:
                if line and not _looks_like_category(line):
                    pending_desc_target["_desc_lines"].append(line)
                    continue

        cat_match = CATEGORY_RE.match(line)
        if cat_match and _looks_like_category(line):
            current = new_category(cat_match.group("name"), int(cat_match.group("num")) if cat_match.group("num") else None)
            pending_desc_target = current
            continue

        pair = NAME_DESC_RE.match(line)
        if pair and current is not None:
            current["skills"].append(
                {
                    "number": len(current["skills"]) + 1,
                    "name": pair.group("name").strip(" .:-"),
                    "description": pair.group("desc").strip(),
                    "_desc_lines": [],
                }
            )
            pending_desc_target = current["skills"][-1]
            continue

        if pending_desc_target is not None:
            pending_desc_target["_desc_lines"].append(line)

    # finalise descriptions and assign stable ids
    seen_numbers: set[int] = set()
    for cat in categories:
        if cat["number"] in seen_numbers:
            cat["number"] = max(seen_numbers) + 1 if seen_numbers else 1
        seen_numbers.add(cat["number"])
    for cat in categories:
        desc = " ".join(cat.pop("_desc_lines", [])).strip()
        cat["description"] = re.sub(r"\s+", " ", desc)
        kept = []
        for skill in cat["skills"]:
            sdesc = " ".join(skill.pop("_desc_lines", [])).strip()
            skill["description"] = re.sub(r"\s+", " ", sdesc)
            if not skill["description"]:
                warnings.append(f"skill '{skill['name']}' had no description; recorded as action-only capability")
            skill["slug"] = slugify(skill["name"])
            skill["keywords"] = sorted(set(re.findall(r"[a-z][a-z0-9+/#]{2,}", (skill["name"] + " " + skill["description"]).lower())) - STOPWORDS)
            kept.append(skill)
        for position, skill in enumerate(kept, start=1):
            skill["number"] = position
            skill["id"] = f"{cat['number']}.{position}"
        cat["skills"] = kept
        cat["slug"] = slugify(cat["name"])
    categories = [c for c in categories if c["skills"]]

    if not categories:
        raise ToolError(
            "EXTRACT_EMPTY",
            "Could not find any capability entries in the input.",
            "Use lines like '1. Skill Name: Name' + 'Description: ...' or 'Name: description'.",
        )
    if len(categories) > limits["max_catalog_categories"]:
        raise ToolError("EXTRACT_TOO_LARGE", f"{len(categories)} categories exceeds limits.max_catalog_categories ({limits['max_catalog_categories']}).")
    for cat in categories:
        if len(cat["skills"]) > limits["max_skills_per_category"]:
            raise ToolError(
                "EXTRACT_TOO_LARGE",
                f"category '{cat['name']}' has {len(cat['skills'])} skills, limit {limits['max_skills_per_category']}.",
            )

    total = sum(len(c["skills"]) for c in categories)
    doc = {
        "catalog": "extracted-capability-catalog",
        "source": "user-supplied capability list",
        "category_count": len(categories),
        "skill_count": total,
        "extraction_warnings": warnings[:40],
        "categories": categories,
    }
    return doc


def _looks_like_category(line: str) -> bool:
    text = NUM_PREFIX.sub("", line).strip(" .:-")
    if not text or len(text) < 4 or len(text) > 90:
        return False
    if line.rstrip().endswith((".", ";", ":")) and not text.endswith(":"):
        if not CATEGORY_HINT_RE.search(text):
            return False
    words = text.split()
    if len(words) < 2 or len(words) > 12:
        return False
    if not text[0].isupper():
        return False
    if CATEGORY_HINT_RE.search(text):
        return True
    return all(w[:1].isupper() or w.lower() in STOPWORDS for w in words if w[:1].isalpha())


STOPWORDS = {
    "and", "or", "of", "the", "for", "a", "an", "with", "to", "in", "on", "by", "into", "from", "as", "at",
}


def slugify(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "item"


# --------------------------------------------------------------------------- #
# Rendering: Markdown plan, CSV fallback, manifest
# --------------------------------------------------------------------------- #

RULES_SECTION = [
    "1. Any capability with `approval_required: true` stays blocked until a named approver records a scoped approval.",
    "2. Destructive steps (delete, replace, bulk rewrite, permission change) always run against a copied workbook.",
    "3. Publishing steps (share links, email, PDF/CSV export to others) require `review_before_publish`.",
    "4. Disabled integrations are never simulated: the plan lists manual steps instead.",
    "5. Approvals expire; expired approvals revert the capability to pending.",
]


def load_plan_scaffold(root: Path, cfg: dict) -> tuple[str, str]:
    """Return (template, checklist) text from the configured assets with safe fallbacks."""
    template = ""
    checklist = ""
    template_path = root / cfg["paths"]["worksheet_template"]
    checklist_path = root / cfg["paths"]["governance_checklist"]
    try:
        if template_path.exists():
            template = template_path.read_text(encoding="utf-8")
    except OSError:
        template = ""
    try:
        if checklist_path.exists():
            checklist = checklist_path.read_text(encoding="utf-8").strip()
    except OSError:
        checklist = ""
    if "{{body}}" not in template:
        template = (
            "# Activation Plan - {{subject}}\n\nGenerated: {{generated}}  \nTool: `{{tool}}` v{{version}}  \n"
            "{{summary_line}}\n\n## Run summary\n\n{{risk_table}}\n\n{{gate_section}}\n\n## Capabilities\n\n"
            "{{body}}\n\n## Pre-handoff checklist\n\n{{checklist}}\n\n## Approval and safety rules\n\n{{rules}}\n"
        )
    if not checklist:
        checklist = "- [ ] Confirm a workbook copy exists before any destructive step."
    return template, checklist


def render_plan_markdown(plans: list[dict], meta: dict, store: dict, now: datetime, root: Path | None = None, cfg: dict | None = None) -> str:
    lines: list[str] = []
    gates = gate_report(plans, store, now)
    pending = [g for g in gates if g["state"] == "pending"]
    risk_counts: dict[str, int] = {}
    for plan in plans:
        risk_counts[plan["risk"]] = risk_counts.get(plan["risk"], 0) + 1
    risk_lines = ["| Risk tier | Capabilities |", "| --- | --- |"]
    for tier in ("critical", "high", "medium", "low"):
        if risk_counts.get(tier):
            risk_lines.append(f"| {tier} | {risk_counts[tier]} |")

    gate_lines: list[str] = []
    if pending:
        gate_lines.append("## Pending approvals (blocked until approved)")
        gate_lines.append("")
        gate_lines.append("| Capability | Gate | Detail |")
        gate_lines.append("| --- | --- | --- |")
        for gate in pending:
            gate_lines.append(f"| {gate['capability_id']} {gate['capability']} | {gate['gate']} | {gate['detail']} |")
        gate_lines.append("")
        gate_lines.append(
            "Record approvals with "
            f"`python {PRIMARY_FILE} approve --out <dir> --capability <id> --gate <gate> --approver <name> "
            "--confirm-approval`."
        )
        gate_lines.append("")

    for category in group_by_category(plans):
        lines.append(f"## {category['category_id']}. {category['category']}")
        lines.append("")
        for plan in category["skills"]:
            gate_labels = ", ".join(f"`{g['gate']}`" for g in plan["gates"]) or "none"
            lines.append(f"### {plan['id']} {plan['name']}")
            lines.append("")
            lines.append(f"- **Purpose:** {plan['purpose']}")
            lines.append(
                f"- **Risk:** {plan['risk']}  |  **Surfaces:** {', '.join(plan['surfaces'])}  |  **Gates:** {gate_labels}"
            )
            if plan.get("semantics") == "derived":
                lines.append(
                    "- **Semantics:** derived baseline (no id+name+slug match in asset-capability-map.json; review before execution)"
                )
            if plan["surfaces_unavailable"]:
                lines.append(f"- **Unavailable integrations:** {', '.join(plan['surfaces_unavailable'])} (deliver manual steps)")
            lines.append(f"- **Workflow:** {' -> '.join(plan['workflow']) if plan['workflow'] else 'n/a'}")
            if plan["inputs"]:
                lines.append(f"- **Inputs:** {'; '.join(plan['inputs'])}")
            lines.append(f"- **Outputs:** {'; '.join(plan['outputs'])}")
            for rule in plan["validation"]:
                lines.append(f"- **Validate:** {rule}")
            for limit in plan["limits"]:
                lines.append(f"- **Limit:** {limit}")
            for fallback in plan["fallbacks"]:
                lines.append(f"- **Fallback:** {fallback}")
            lines.append("")
    body = "\n".join(lines)
    if root is not None and cfg is not None:
        template, checklist = load_plan_scaffold(root, cfg)
    else:
        template, checklist = (
            "# Activation Plan - {{subject}}\n\n{{summary_line}}\n\n{{risk_table}}\n\n{{gate_section}}\n\n"
            "{{body}}\n\n{{checklist}}\n\n{{rules}}\n",
            "- [ ] Confirm a workbook copy exists before any destructive step.",
        )
    replacements = {
        "{{subject}}": meta["subject"],
        "{{generated}}": iso(now),
        "{{tool}}": TOOL_NAME,
        "{{version}}": VERSION,
        "{{summary_line}}": (
            f"Capabilities: {len(plans)} across {len({p['category'] for p in plans})} categories  \n"
            f"Approval gates pending: {len(pending)}  "
        ),
        "{{risk_table}}": "\n".join(risk_lines),
        "{{gate_section}}": "\n".join(gate_lines).strip(),
        "{{body}}": body,
        "{{checklist}}": checklist,
        "{{rules}}": "\n".join(RULES_SECTION),
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered if rendered.endswith("\n") else rendered + "\n"


def render_manifest(plans: list[dict], meta: dict, artifacts: dict, cfg: dict, now: datetime, store: dict) -> dict:
    gates = gate_report(plans, store, now)
    return {
        "tool": TOOL_NAME,
        "tool_version": VERSION,
        "generated_at": iso(now),
        "subject": meta["subject"],
        "selection": meta["selection"],
        "capability_count": len(plans),
        "category_count": len({p["category"] for p in plans}),
        "origins": meta.get("origins", {}),
        "config_path": cfg.get("_config_path", "(built-in defaults)"),
        "integrations_enabled": sorted(k for k, v in cfg["integrations"].items() if v),
        "integrations_disabled": sorted(k for k, v in cfg["integrations"].items() if not v),
        "approval_summary": {
            "required": sum(1 for g in gates if g["approval_required"]),
            "pending": sum(1 for g in gates if g["state"] == "pending"),
            "approved": sum(1 for g in gates if g["state"] == "approved"),
            "advisory": sum(1 for g in gates if g["state"] == "advisory"),
        },
        "artifacts": artifacts,
        "limits": cfg["limits"],
    }


def render_csv(plans: list[dict]) -> str:
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["capability_id", "capability", "category", "risk", "surfaces", "gates", "approval_required",
         "validation_rules", "limits_rules", "fallbacks", "execution_ready"]
    )
    for plan in plans:
        writer.writerow(
            [
                plan["id"],
                plan["name"],
                plan["category"],
                plan["risk"],
                "; ".join(plan["surfaces"]),
                "; ".join(g["gate"] for g in plan["gates"]),
                "yes" if plan["approval_required"] else "no",
                len(plan["validation"]),
                len(plan["limits"]),
                len(plan["fallbacks"]),
                "yes" if plan["execution_ready"] else "no",
            ]
        )
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Workbook writer (openpyxl optional)
# --------------------------------------------------------------------------- #

HEADERS = [
    "Capability ID", "Capability", "Category", "Risk", "Surfaces", "Gates", "Approval Required",
    "Validation Rules", "Limits", "Fallbacks", "Execution Ready", "Status",
]
COLUMN_WIDTHS = [13, 34, 30, 10, 26, 30, 12, 44, 44, 44, 12, 14]
STATUS_FILLS = {"pending": "FFF4CC", "approved": "DFF5E1", "blocked": "F8D7DA"}


def write_workbook(path: Path, plans: list[dict], store: dict, now: datetime, limits: dict, retries: int = 3) -> dict:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        return {"written": False, "reason": "openpyxl not installed", "format": "xlsx"}

    if len(plans) > limits["workbook_row_cap"]:
        raise ToolError(
            "WORKBOOK_ROW_CAP",
            f"{len(plans)} rows exceed limits.workbook_row_cap ({limits['workbook_row_cap']}).",
            "Raise the cap in tools-config.json or narrow the selection.",
        )

    gates = {f"{g['capability_id']}:{g['gate']}": g for g in gate_report(plans, store, now)}
    wb = Workbook()

    # --- Activation Plan sheet ------------------------------------------------
    ws = wb.active
    ws.title = "Activation Plan"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F3864")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append(HEADERS)
    for idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
        cell = ws.cell(row=1, column=idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = border
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 28

    for plan in plans:
        gate_names = [g["gate"] for g in plan["gates"]]
        pending = any(
            g["approval_required"] and gates.get(f"{plan['id']}:{g['gate']}", {}).get("state") == "pending"
            for g in plan["gates"]
        )
        approved_all = all(
            gates.get(f"{plan['id']}:{g['gate']}", {}).get("state") in ("approved", "advisory")
            for g in plan["gates"]
        )
        status = "blocked" if pending else ("ready" if approved_all else "review")
        row = [
            plan["id"],
            plan["name"],
            plan["category"],
            plan["risk"],
            ", ".join(plan["surfaces"]),
            ", ".join(gate_names) or "none",
            "yes" if plan["approval_required"] else "no",
            " | ".join(plan["validation"]),
            " | ".join(plan["limits"]),
            " | ".join(plan["fallbacks"]),
            "yes" if plan["execution_ready"] else "no",
            status,
        ]
        ws.append(row)
        r = ws.max_row
        for c in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=r, column=c)
            cell.alignment = Alignment(vertical="top", wrap_text=c in (6, 8, 9, 10))
            cell.border = border
        ws.cell(row=r, column=12).fill = PatternFill("solid", fgColor=STATUS_FILLS.get(status, "FFFFFF"))

    if ws.max_row > 1:
        dv = DataValidation(type="list", formula1='"ready,review,blocked"', allow_blank=True)
        dv.error = "Use ready, review or blocked."
        dv.errorTitle = "Invalid status"
        ws.add_data_validation(dv)
        dv.add(f"L2:L{ws.max_row}")

    # --- Capability Index sheet ---------------------------------------------
    idx = wb.create_sheet("Capability Index")
    idx.append(["Category ID", "Category", "Capabilities", "High/Critical", "Approval Needed"])
    for c in range(1, 6):
        cell = idx.cell(row=1, column=c)
        cell.font = header_font
        cell.fill = header_fill
    idx.freeze_panes = "A2"
    for band in group_by_category(plans):
        idx.append(
            [
                band["category_id"],
                band["category"],
                len(band["skills"]),
                sum(1 for s in band["skills"] if s["risk"] in ("high", "critical")),
                sum(1 for s in band["skills"] if s["approval_required"]),
            ]
        )
    for c, width in enumerate([12, 46, 14, 16, 16], start=1):
        idx.column_dimensions[get_column_letter(c)].width = width

    # --- Gate Register sheet ------------------------------------------------
    gr = wb.create_sheet("Gate Register")
    gr.append(["Capability ID", "Capability", "Gate", "Approval Required", "State", "Detail", "TTL Hours"])
    for c in range(1, 8):
        cell = gr.cell(row=1, column=c)
        cell.font = header_font
        cell.fill = header_fill
    gr.freeze_panes = "A2"
    for gate in gates.values():
        gr.append(
            [
                gate["capability_id"],
                gate["capability"],
                gate["gate"],
                "yes" if gate["approval_required"] else "no",
                gate["state"],
                gate["detail"],
                gate.get("ttl_hours", 0),
            ]
        )
    for c, width in enumerate([13, 34, 28, 16, 12, 44, 10], start=1):
        gr.column_dimensions[get_column_letter(c)].width = width

    # --- Run Log sheet ------------------------------------------------------
    log = wb.create_sheet("Run Log")
    log.append(["Field", "Value"])
    log.append(["Tool", f"{TOOL_NAME} v{VERSION}"])
    log.append(["Generated at", iso(now)])
    log.append(["Capabilities", len(plans)])
    log.append(["Categories", len({p["category"] for p in plans})])
    log.append(["Row cap", limits["workbook_row_cap"]])
    log.append(["Note", "Generated workbook. No Google Sheets API call was made by this tool."])
    log.column_dimensions["A"].width = 22
    log.column_dimensions["B"].width = 70

    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp = path.with_name(f".{path.stem}.tmp-{os.getpid()}{path.suffix}")
        try:
            wb.save(tmp)
            os.replace(tmp, path)
            return {"written": True, "format": "xlsx", "sheets": wb.sheetnames, "rows": ws.max_row - 1}
        except OSError as exc:  # pragma: no cover
            last = exc
            tmp.unlink(missing_ok=True)
            time.sleep(0.2 * attempt)
    raise ToolError("WORKBOOK_WRITE_FAILED", f"Could not write workbook {path}: {last}", "Close the file in Excel and retry.")


# --------------------------------------------------------------------------- #
# Risk / privacy audit
# --------------------------------------------------------------------------- #

AUDIT_RULES = [
    # (id, severity, category, regex, message, recommendation)
    ("PII_EMAIL", "medium", "pii", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "Email address present in output text.", "Replace with a role address or pseudonymise the local part."),
    ("PII_PHONE", "medium", "pii", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]\d{4}(?!\d)"), "Phone-like identifier present.", "Mask to last 4 digits or move to a key file."),
    ("PII_ID_PH", "high", "pii", re.compile(r"\b\d{12}\b"), "12-digit government-ID-like string.", "Remove or pseudonymise before sharing the workbook."),
    ("SECRET_TOKEN", "critical", "secret", re.compile(r"\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,})\b"), "Credential-like token detected.", "Revoke the secret, purge it from the workbook and history, then rotate."),
    ("SECRET_ASSIGN", "high", "secret", re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|token)\s*[:=]\s*\S+"), "Inline credential assignment.", "Use account-level OAuth instead of literals; see integration governance."),
    ("DESTRUCTIVE", "high", "destructive", re.compile(r"(?i)\b(delete|drop|truncate|permanently remove|overwrite all|clear all|wipe)\b"), "Destructive verb in the plan text.", "Run against a copy and require backup_before_change approval."),
    ("PUBLISH", "medium", "publishing", re.compile(r"(?i)\b(publish|share publicly|public link|anyone with the link|send email|email to|distribute|send pdf)\b"), "Publishing or distribution step.", "Require review_before_publish before executing."),
    ("PERMISSION", "high", "permission", re.compile(r"(?i)\b(change owner|transfer ownership|grant edit|grant editor|make public|editor access)\b"), "Permission-changing step.", "Require approval and record the previous access state."),
    ("FINANCIAL", "medium", "financial", re.compile(r"(?i)\b(transfer funds|payment|invoice total|payroll|salary|bank account)\b"), "Financial data or action.", "Require approval and restrict to named reviewers."),
    ("EXTERNAL_FETCH", "medium", "external", re.compile(r"(?i)\b(importrange|importhtml|importxml|importdata|urlfetch|webhook)\b"), "External data dependency.", "Record owner, refresh expectation and fallback snapshot."),
]

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Action-verb rules frequently match policy prose ("require approval before delete").
# When the same line carries an advisory marker the finding is recorded as context
# so the audit does not block on its own guidance text.
ADVISORY_RE = re.compile(
    r"(?i)\b(require[sd]?|must|before|approval|approve[sd]?|confirm|review|recommend\w*|avoid|never|"
    r"do not|don't|gate|policy|checklist|caveat|warning|run against|copied|copies|backup|snapshot|instead)\b"
)
CONTEXT_RULES = {"destructive", "publishing", "permission", "financial", "external"}


def audit_text(text: str, limits: dict, source: str = "<text>") -> dict:
    findings: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule_id, severity, category, pattern, message, recommendation in AUDIT_RULES:
            for match in pattern.finditer(line):
                snippet = match.group(0)
                if len(snippet) > 40:
                    snippet = snippet[:37] + "..."
                context = category in CONTEXT_RULES and bool(ADVISORY_RE.search(line))
                findings.append(
                    {
                        "rule": rule_id,
                        "severity": "low" if context else severity,
                        "declared_severity": severity,
                        "category": category,
                        "line": line_no,
                        "snippet": snippet,
                        "message": message + (" (advisory context detected)" if context else ""),
                        "recommendation": recommendation,
                        "context_only": context,
                        "source": source,
                    }
                )
                if len(findings) >= limits["max_findings_per_audit"]:
                    break
            if len(findings) >= limits["max_findings_per_audit"]:
                break
        if len(findings) >= limits["max_findings_per_audit"]:
            break
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    blocking_findings = [f for f in findings if not f["context_only"]]
    worst = max((SEVERITY_ORDER[f["severity"]] for f in blocking_findings), default=0)
    level = next((k for k, v in SEVERITY_ORDER.items() if v == worst), "none")
    return {
        "source": source,
        "finding_count": len(findings),
        "severity_counts": counts,
        "overall": level,
        "blocking": worst >= SEVERITY_ORDER["high"],
        "blocking_finding_count": sum(1 for f in blocking_findings if SEVERITY_ORDER[f["severity"]] >= SEVERITY_ORDER["high"]),
        "context_only_count": sum(1 for f in findings if f["context_only"]),
        "truncated": len(findings) >= limits["max_findings_per_audit"],
        "findings": findings,
    }


def audit_workbook(path: Path, limits: dict) -> dict:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ToolError(
            "OPENPYXL_MISSING",
            "Workbook auditing needs openpyxl.",
            "pip install openpyxl==3.1.5, or audit the Markdown/JSON artifacts instead.",
        )
    if not path.exists():
        raise ToolError("FILE_NOT_FOUND", f"Workbook not found: {path}")
    wb = load_workbook(path, data_only=False, read_only=False)
    findings: list[dict] = []
    texts: list[str] = []
    for ws in wb.worksheets:
        texts.append(f"# sheet {ws.title}")
        for row in ws.iter_rows(values_only=True):
            texts.append(" ".join(str(c) for c in row if c is not None))
    audit = audit_text("\n".join(texts), limits, source=str(path))
    def _protected(sheet) -> bool:
        protection = getattr(sheet, "protection", None)
        return bool(getattr(protection, "sheet", False))

    audit["structure"] = {
        "sheets": wb.sheetnames,
        "rows_scanned": len(texts),
        "external_links": len(getattr(wb, "_external_links", []) or []),
        "defined_names": len(list(wb.defined_names)),
        "protected_sheets": [ws.title for ws in wb.worksheets if _protected(ws)],
        "hidden_sheets": [ws.title for ws in wb.worksheets if getattr(ws, "sheet_state", "visible") != "visible"],
    }
    findings = audit["findings"]
    return audit


# --------------------------------------------------------------------------- #
# Formula pack
# --------------------------------------------------------------------------- #

FORMULA_SPECS = {
    "lookup": {
        "description": "Exact-match lookup with a labelled fallback.",
        "template": '=IFNA(XLOOKUP({key},{key_range},{value_range},"NOT_FOUND"))',
        "args": {"key": "D2", "key_range": "Lookup!A2:A500", "value_range": "Lookup!C2:C500"},
        "rules": ["key", "key_range", "value_range"],
        "notes": "XLOOKUP requires a Workspace edition that supports it; VLOOKUP is the documented fallback.",
    },
    "conditional_sum": {
        "description": "Criteria-driven aggregation across two ranges.",
        "template": '=SUMIFS({sum_range},{criteria_range1},{criteria1},{criteria_range2},{criteria2})',
        "args": {"sum_range": "Data!H2:H5000", "criteria_range1": "Data!B2:B5000", "criteria1": '"East"', "criteria_range2": "Data!A2:A5000", "criteria2": '">="&DATE(2026,1,1)'},
        "rules": ["sum_range", "criteria_range1", "criteria1", "criteria_range2", "criteria2"],
        "notes": "Keep sum_range and criteria ranges the same height; mismatched sizes return #VALUE!.",
    },
    "dynamic_filter": {
        "description": "Self-expanding filtered output that degrades to a message.",
        "template": '=IFERROR(FILTER({array},{condition}),"No matching records")',
        "args": {"array": "Data!A2:H5000", "condition": "Data!C2:C5000>0"},
        "rules": ["array"],
        "notes": "The output block must be empty below the formula or the spill returns #REF!.",
    },
    "query_summary": {
        "description": "SQL-style grouped summary over a range.",
        "template": '=QUERY({range},"select {select_cols}, sum({sum_col}) where {where} group by {select_cols} order by {select_cols} label sum({sum_col}) \'{label}\'",1)',
        "args": {"range": "Data!A1:H5000", "select_cols": "C", "sum_col": "H", "where": "A is not null", "label": "Total"},
        "rules": ["range", "select_cols", "sum_col"],
        "notes": "QUERY columns are letters of the source range; label text uses single quotes.",
    },
    "array_row": {
        "description": "ARRAYFORMULA helper that survives blank rows.",
        "template": '=ARRAYFORMULA(IF({key_range}="","",{expression}))',
        "args": {"key_range": "Data!A2:A5000", "expression": "Data!B2:B5000*Data!C2:C5000"},
        "rules": ["key_range", "expression"],
        "notes": "Never place a second ARRAYFORMULA in the same spill column.",
    },
    "error_guard": {
        "description": "Error handling that keeps #N/A distinguishable from real blanks.",
        "template": '=IFERROR({expression},{fallback})',
        "args": {"expression": "VLOOKUP(A2,Lookup!A:C,3,FALSE)", "fallback": '"MISSING_KEY"'},
        "rules": ["expression", "fallback"],
        "notes": "Prefer IFNA when only #N/A should be swallowed.",
    },
    "date_math": {
        "description": "Business-day deadline arithmetic with a holiday list.",
        "template": '=WORKDAY({start},{days},{holidays})',
        "args": {"start": "TODAY()", "days": "10", "holidays": "Config!A2:A20"},
        "rules": ["start", "days"],
        "notes": "Holiday ranges must contain true dates, not text.",
    },
    "text_clean": {
        "description": "Whitespace and case normalisation in one step.",
        "template": '=PROPER(TRIM(SUBSTITUTE({cell},CHAR(160)," ")))',
        "args": {"cell": "A2"},
        "rules": ["cell"],
        "notes": "CHAR(160) handles non-breaking spaces that TRIM alone ignores.",
    },
    "percent_of_total": {
        "description": "Share-of-total with a safe divide.",
        "template": '=IFERROR({cell}/SUM({total_range}),0)',
        "args": {"cell": "H2", "total_range": "H2:H5000"},
        "rules": ["cell", "total_range"],
        "notes": "Format the result as a percentage, not as text.",
    },
    "regex_extract": {
        "description": "Pattern extraction for codes and identifiers.",
        "template": '=IFERROR(REGEXEXTRACT({cell},{pattern}),"")',
        "args": {"cell": "A2", "pattern": '"[A-Z]{3}-\\d{4}"'},
        "rules": ["cell", "pattern"],
        "notes": "Google Sheets RE2 has no backreferences or lookbehind.",
    },
}


PATTERN_CACHE: dict[str, dict] = {}


def load_formula_patterns(root: Path, cfg: dict, limits: dict) -> dict:
    """Merge built-in patterns with asset-formula-patterns.json (asset wins)."""
    key = str(root / cfg["paths"]["formula_patterns"])
    if key in PATTERN_CACHE:
        return PATTERN_CACHE[key]
    merged: dict[str, dict] = {}
    for name, spec in FORMULA_SPECS.items():
        merged[name] = {
            "description": spec["description"],
            "template": spec["template"],
            "args": dict(spec["args"]),
            "rules": list(spec["rules"]),
            "required": list(spec["rules"]),
            "notes": spec["notes"],
        }
    asset_path = root / cfg["paths"]["formula_patterns"]
    if asset_path.exists():
        doc = load_json(asset_path, limits["max_input_bytes"])
        for name, spec in (doc.get("patterns") or {}).items():
            if not isinstance(spec, dict) or "template" not in spec or "args" not in spec:
                raise ToolError(
                    "FORMULA_PATTERN_INVALID",
                    f"Pattern '{name}' in {asset_path.name} needs 'template' and 'args'.",
                    "See asset-formula-patterns.json for the expected shape.",
                )
            merged[name] = {
                "description": spec.get("description", ""),
                "template": spec["template"],
                "args": dict(spec["args"]),
                "rules": list(spec.get("required") or spec["args"].keys()),
                "required": list(spec.get("required") or spec["args"].keys()),
                "notes": spec.get("notes", ""),
            }
    PATTERN_CACHE[key] = merged
    return merged


def generate_formula(name: str, overrides: dict, patterns: dict | None = None) -> str:
    pattern_specs = patterns if patterns is not None else FORMULA_SPECS
    spec = pattern_specs.get(name)
    if spec is None:
        raise ToolError(
            "FORMULA_PATTERN_UNKNOWN",
            f"Unknown formula pattern '{name}'.",
            "Available: " + ", ".join(sorted(pattern_specs)),
        )
    args = dict(spec["args"])
    unknown = [k for k in overrides if k not in args]
    if unknown:
        raise ToolError(
            "FORMULA_ARG_UNKNOWN",
            f"Unknown argument(s) for '{name}': {', '.join(unknown)}",
            "Known arguments: " + ", ".join(sorted(args)),
        )
    for key, value in overrides.items():
        if not str(value).strip():
            raise ToolError("FORMULA_ARG_EMPTY", f"Argument '{key}' cannot be empty.")
        args[key] = value
    required = spec.get("required") or spec.get("rules") or list(spec["args"].keys())
    missing = [k for k in required if not str(args.get(k, "")).strip()]
    if missing:
        raise ToolError("FORMULA_ARG_MISSING", f"Required argument(s) missing: {', '.join(missing)}")
    try:
        formula = spec["template"].format(**args)
    except KeyError as exc:
        raise ToolError("FORMULA_TEMPLATE_BROKEN", f"Pattern '{name}' references undefined argument {exc}.")
    return formula


def validate_formula(formula: str) -> dict:
    issues: list[str] = []
    if not formula.startswith("="):
        issues.append("formula must start with '='")
    depth = 0
    in_quote = False
    for ch in formula:
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch == "(":
            depth += 1
        elif not in_quote and ch == ")":
            depth -= 1
            if depth < 0:
                issues.append("closing parenthesis without an opening one")
                break
    if depth != 0:
        issues.append(f"unbalanced parentheses (depth {depth})")
    if in_quote:
        issues.append("unbalanced double quote")
    if re.search(r",\s*,", formula):
        issues.append("empty argument between commas")
    if ";" in formula and "," in formula:
        issues.append("mixed ',' and ';' argument separators (locale mismatch risk)")
    if re.search(r"(?i)\bVLOOKUP\(", formula) and "FALSE" not in formula.upper() and "0)" not in formula:
        issues.append("VLOOKUP without an explicit exact-match flag (choose FALSE)")
    if re.search(r"(?i)\b(?:SUMIF|SUMIFS|COUNTIF|COUNTIFS|AVERAGEIF|AVERAGEIFS)\(", formula) and not re.search(r"(?i)(SUMIFS|COUNTIFS|AVERAGEIFS)\(", formula):
        issues.append("single-criteria function used where multi-criteria function may be safer")
    return {"formula": formula, "valid": not issues, "issues": issues, "length": len(formula)}


# --------------------------------------------------------------------------- #
# Command implementations
# --------------------------------------------------------------------------- #


def prepare_output_dir(raw: str | None, cfg: dict, root: Path) -> Path:
    target = Path(raw) if raw else root / cfg["paths"]["default_output_dir"]
    if target.exists() and not target.is_dir():
        raise ToolError("OUTPUT_NOT_DIR", f"--out must be a directory, found a file: {target}")
    target.mkdir(parents=True, exist_ok=True)
    probe = target / f".write-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ToolError("OUTPUT_NOT_WRITABLE", f"Cannot write to {target}: {exc}", "Choose another --out directory.")
    return target


def write_outputs(out_dir: Path, artifacts: dict[str, str | Path], cfg: dict) -> dict:
    """Write artifacts atomically and return a manifest-ready artifact map."""
    produced: dict[str, dict] = {}
    for key, value in artifacts.items():
        path = Path(value)
        if not path.exists():
            continue
        produced[key] = {
            "file": path.name,
            "bytes": path.stat().st_size,
            "relative": path.name,
        }
    return produced


def catalog_overview(doc: dict) -> str:
    lines = [f"{doc['category_count']} categories / {doc['skill_count']} capabilities", ""]
    for cat in doc["categories"]:
        lines.append(f"{cat['number']:>2}. {cat['name']}  ({len(cat['skills'])} capabilities)")
        for sk in cat["skills"]:
            lines.append(f"     {sk['id']:<6} {sk['name']}")
    return "\n".join(lines)


def dispatch_catalog(args, cfg, root, out: Emitter) -> int:
    limits = cfg["limits"]
    catalog_path = Path(args.catalog) if args.catalog else root / cfg["paths"]["catalog"]
    doc = load_catalog(catalog_path, limits)

    cats = doc["categories"]
    if args.category:
        wanted = parse_number_spec(",".join(args.category))
        cats = [c for c in cats if c.get("number") in wanted]
    if args.search:
        needle = args.search.lower()
        filtered = []
        for cat in cats:
            hits = [s for s in cat["skills"] if needle in f"{s['name']} {s['description']}".lower()]
            if hits:
                filtered.append({**cat, "skills": hits})
        cats = filtered
    if not cats:
        raise ToolError("CATALOG_EMPTY", "No catalog entries matched the given filters.", "Try `catalog --format text` without filters.")

    view = {**doc, "categories": cats, "category_count": len(cats), "skill_count": sum(len(c["skills"]) for c in cats)}
    if args.format == "json":
        out.ok("catalog", view)
    else:
        out.ok("catalog", {"category_count": view["category_count"], "skill_count": view["skill_count"]}, catalog_overview(view))
    return EXIT_OK


def dispatch_extract(args, cfg, root, out: Emitter) -> int:
    limits = cfg["limits"]
    if not args.input:
        raise ToolError("MISSING_INPUT", "extract requires --input.", "Pass a .txt, .md or .html capability list.")
    raw = read_text_bounded(Path(args.input), limits["max_input_bytes"])
    doc = extract_catalog(raw, limits)
    problems = validate_catalog(doc, limits)
    if problems:
        raise ToolError("EXTRACT_INVALID", "Extracted catalog failed validation: " + "; ".join(problems[:5]))
    target = Path(args.out)
    if target.suffix.lower() != ".json":
        target = target / "extracted-catalog.json"
    atomic_write_json(target, doc)
    summary = {
        "output": str(target),
        "category_count": doc["category_count"],
        "skill_count": doc["skill_count"],
        "warnings": doc["extraction_warnings"],
        "categories": [{"number": c["number"], "name": c["name"], "skills": len(c["skills"])} for c in doc["categories"]],
    }
    out.ok("extract", summary, f"Extracted {doc['skill_count']} capabilities in {doc['category_count']} categories -> {target}")
    return EXIT_OK


def _selection_from_args(args, doc):
    selected, report = resolve_selection(doc, args)
    return selected, report


DERIVE_SURFACE_HINTS = [
    (("permission", "sharing", "access", "owner", "guest", "exposure"), ["D", "S"], "high"),
    (("privacy", "de-identif", "sensitive", "retention", "disposal", "confidential"), ["D", "S"], "high"),
    (("script", "macro", "trigger", "automation", "add-on", "addon", "webhook"), ["P"], "high"),
    (("form ", "response", "survey", "registration"), ["F", "S"], "medium"),
    (("email", "notify", "notification", "reminder"), ["G"], "high"),
    (("calendar", "roster", "schedule", "shift"), ["S", "K"], "medium"),
    (("bigquery", "looker", "importrange", "import", "web", "external"), ["B", "S"], "medium"),
    (("pivot", "summary", "summaries"), ["S", "V"], "low"),
    (("chart", "dashboard", "visual", "kpi"), ["S", "H"], "low"),
    (("pdf", "print", "export", "publish", "html"), ["X", "C"], "medium"),
    (("validate", "validation", "dropdown", "input", "checkbox"), ["S"], "low"),
    (("format", "number", "currency", "date", "style", "typograph", "colour", "color"), ["S"], "low"),
    (("formula", "lookup", "array", "query", "function", "reference"), ["S"], "low"),
    (("clean", "parse", "duplicate", "standard", "quality"), ["S", "C"], "medium"),
    (("test", "audit", "troubleshoot", "performance", "reconcile", "health"), ["S", "L"], "medium"),
    (("template", "tracker", "gradebook", "budget", "inventory", "crm"), ["W", "S"], "medium"),
    (("dashboard", "report", "decision", "scenario"), ["S", "H", "C"], "medium"),
    (("intent", "architecture", "routing", "orchestrat", "quality gate", "assumption"), ["A", "L"], "medium"),
]


def derive_semantics(skill: dict, cfg: dict, why: str = "no catalogued semantics") -> dict:
    """Baseline execution semantics for a capability that is not in the shipped map."""
    blob = f"{skill['name']} {skill['description']}".lower()
    surfaces: list[str] = []
    risk = cfg.get("capabilities", {}).get("default_risk", "medium")
    for keywords, hint_surfaces, hint_risk in DERIVE_SURFACE_HINTS:
        if any(word in blob for word in keywords):
            surfaces.extend(s for s in hint_surfaces if s not in surfaces)
            order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
            risk = max(risk, hint_risk, key=lambda r: order.get(r, 0))
    if not surfaces:
        surfaces = ["A", "L"]
    return {
        "id": skill["id"],
        "risk": risk,
        "surfaces": surfaces[:4],
        "workflow": [
            f"Confirm the request matches {skill['name']} and not a broader destructive operation.",
            f"Inspect the current state of the target ({', '.join(SURFACES[x] for x in surfaces[:2])}) and record a baseline.",
            "Produce or apply the change, then re-read the affected area to confirm the result.",
            "Report what changed, what was validated, and what still needs a human decision.",
        ],
        "inputs": ["workbook id or local file path", "goal statement and acceptance criteria"],
        "validation": [
            "Confirm the target exists and the request is in scope for this capability.",
            "Re-read the affected range and compare against the acceptance criteria.",
        ],
        "limits": [
            "No live Google Sheets API call is made by this tool; live operations need the sheets_api/drive integrations.",
            f"Single run is capped by limits.workbook_row_cap rows.",
        ],
        "fallbacks": ["Deliver documented manual steps when the integration needed is disabled."],
        "google_apis": [],
        "derived": True,
        "derived_reason": why,
    }


def _assemble_plans(selected, capdoc, policy, cfg, limits, allow_derived: bool = False):
    by_cap = capdoc["_by_key"]
    plans = []
    for skill in selected:
        capdoc_entry = by_cap.get(capability_key(skill))
        if capdoc_entry is None:
            if not allow_derived:
                raise ToolError(
                    "CAPABILITY_DOC_MISSING_ENTRY",
                    f"No capability semantics found for {skill['id']} ({skill['name']}).",
                    "Add the id to asset-capability-map.json, or pass --allow-derived to use baseline semantics.",
                )
            capdoc_entry = derive_semantics(skill, cfg)
        plans.append(build_activation_plan(skill, capdoc_entry, policy, cfg, limits))
    return plans


def dispatch_plan(args, cfg, root, out: Emitter) -> int:
    limits = resolve_limits(args, cfg)
    catalog_path = Path(args.catalog) if args.catalog else root / cfg["paths"]["catalog"]
    doc = load_catalog(catalog_path, limits)
    selected, selection_report = _selection_from_args(args, doc)
    enforce_build_limit(selected, {"limits": limits})
    capdoc = load_capability_docs(root / "asset-capability-map.json", limits)
    policy = load_json(root / cfg["paths"]["policy"], limits["max_input_bytes"])
    plans = _assemble_plans(selected, capdoc, policy, cfg, limits, allow_derived=getattr(args, "allow_derived", False) or bool(cfg.get("capabilities", {}).get("allow_derived_semantics")))
    out_dir = prepare_output_dir(args.out, cfg, root)
    store = load_approvals(approval_store_path(cfg, out_dir, root), limits, reset=args.reset_approvals)
    now = now_utc()
    gates = gate_report(plans, store, now)
    block = args.fail_on_blocked and any(g["state"] == "pending" for g in gates)

    meta = {
        "subject": args.subject or (doc.get("catalog") or "capability-library"),
        "selection": selection_report,
        "origins": {"catalog": str(catalog_path.name)},
    }
    plan_json = out_dir / cfg["outputs"]["plan_json"]
    plan_md = out_dir / cfg["outputs"]["plan_markdown"]
    payload = {
        "tool": TOOL_NAME,
        "version": VERSION,
        "generated_at": iso(now),
        "subject": meta["subject"],
        "selection": selection_report,
        "gate_summary": {
            "required": sum(1 for g in gates if g["approval_required"]),
            "pending": sum(1 for g in gates if g["state"] == "pending"),
            "approved": sum(1 for g in gates if g["state"] == "approved"),
        },
        "gates": gates,
        "plans": plans,
    }
    atomic_write_json(plan_json, payload)
    atomic_write_text(plan_md, render_plan_markdown(plans, meta, store, now, root, cfg))
    artifacts = {"plan_json": str(plan_json), "plan_markdown": str(plan_md)}
    if block:
        raise ToolError(
            "APPROVAL_PENDING",
            f"{payload['gate_summary']['pending']} approval gate(s) are pending for {len(plans)} capability(ies).",
            f"Review {plan_md.name}, then approve with `approve --out {out_dir}`.",
            exit_code=EXIT_BLOCKED,
        )
    result = {
        "output_dir": str(out_dir),
        "capability_count": len(plans),
        "gate_summary": payload["gate_summary"],
        "artifacts": {k: Path(v).name for k, v in artifacts.items()},
        "selection_notes": selection_report["notes"][:10],
    }
    out.ok("plan", result, f"Plan written for {len(plans)} capabilities -> {plan_json.name}, {plan_md.name}")
    return EXIT_OK


def dispatch_build(args, cfg, root, out: Emitter) -> int:
    limits = resolve_limits(args, cfg)
    catalog_path = Path(args.catalog) if args.catalog else root / cfg["paths"]["catalog"]
    doc = load_catalog(catalog_path, limits)
    selected, selection_report = _selection_from_args(args, doc)
    if len(selected) > limits["max_skills_per_build"]:
        raise ToolError(
            "BUILD_LIMIT_EXCEEDED",
            f"{len(selected)} capabilities selected but the limit is {limits['max_skills_per_build']}.",
            "Pass --max-skills, edit limits.max_skills_per_build, or narrow the selection.",
        )
    capdoc = load_capability_docs(root / "asset-capability-map.json", limits)
    policy = load_json(root / cfg["paths"]["policy"], limits["max_input_bytes"])
    plans = _assemble_plans(selected, capdoc, policy, cfg, limits, allow_derived=getattr(args, "allow_derived", False) or bool(cfg.get("capabilities", {}).get("allow_derived_semantics")))

    out_dir = prepare_output_dir(args.out, cfg, root)
    run_label = re.sub(r"[^a-z0-9]+", "-", (args.subject or "run").lower()).strip("-") or "run"
    if args.no_workbook_overwrite and (out_dir / cfg["outputs"]["workbook"]).exists():
        raise ToolError(
            "OUTPUT_EXISTS",
            f"{cfg['outputs']['workbook']} already exists in {out_dir}.",
            "Remove the file, pass a fresh --out, or allow overwrite (default).",
        )
    store = load_approvals(approval_store_path(cfg, out_dir, root), limits, reset=args.reset_approvals)
    now = now_utc()
    gates = gate_report(plans, store, now)

    meta = {
        "subject": args.subject or doc.get("catalog") or "capability-library",
        "selection": selection_report,
        "origins": {"catalog": catalog_path.name},
    }

    plan_json = out_dir / cfg["outputs"]["plan_json"]
    plan_md = out_dir / cfg["outputs"]["plan_markdown"]
    atomic_write_json(plan_json, {"tool": TOOL_NAME, "version": VERSION, "generated_at": iso(now), "plans": plans, "gates": gates})
    atomic_write_text(plan_md, render_plan_markdown(plans, meta, store, now, root, cfg))

    artifacts: dict[str, str] = {"plan_json": str(plan_json), "plan_markdown": str(plan_md)}
    workbook_info: dict = {"written": False, "reason": "disabled in config"}
    if cfg["outputs"]["write_workbook"] and not args.no_workbook:
        wb_path = out_dir / cfg["outputs"]["workbook"]
        workbook_info = write_workbook(wb_path, plans, store, now, limits, cfg["retries"]["file_write"])
        if workbook_info.get("written"):
            artifacts["workbook"] = str(wb_path)
            audit = audit_workbook(wb_path, limits)
            audit_path = out_dir / cfg["outputs"]["audit_report"]
            atomic_write_json(audit_path, audit)
            artifacts["risk_audit"] = str(audit_path)
            workbook_info["risk_audit"] = audit_path.name
    if cfg["outputs"]["write_csv_fallback"]:
        csv_path = out_dir / cfg["outputs"]["csv_fallback"]
        atomic_write_text(csv_path, render_csv(plans))
        artifacts["csv_fallback"] = str(csv_path)

    produced = write_outputs(out_dir, artifacts, cfg)
    manifest = render_manifest(plans, meta, produced, cfg, now, store)
    manifest_path = out_dir / cfg["outputs"]["manifest"]
    atomic_write_json(manifest_path, manifest)
    produced = write_outputs(out_dir, artifacts, cfg)
    manifest["artifacts"] = produced
    atomic_write_json(manifest_path, manifest)

    pending = manifest["approval_summary"]["pending"]
    result = {
        "output_dir": str(out_dir),
        "run_label": run_label,
        "capability_count": len(plans),
        "category_count": manifest["category_count"],
        "approval_summary": manifest["approval_summary"],
        "workbook": workbook_info,
        "artifacts": sorted(p["file"] for p in produced.values()),
        "blocked_by_approval": pending > 0,
    }
    text = "\n".join(
        [
            f"Built bundle for {len(plans)} capabilities -> {out_dir}",
            f"  workbook : {workbook_info.get('format', 'n/a')} ({'written' if workbook_info.get('written') else workbook_info.get('reason')})",
            f"  plan     : {plan_json.name}, {plan_md.name}",
            f"  manifest : {manifest_path.name}",
            f"  approvals: {manifest['approval_summary']['approved']} approved / {pending} pending",
        ]
    )
    if args.fail_on_blocked and pending:
        raise ToolError(
            "APPROVAL_PENDING",
            f"{pending} approval gate(s) pending; bundle artifacts were still written for review.",
            f"Approve with `python {PRIMARY_FILE} approve --out {out_dir} --capability <id> --gate <gate> --approver <name> --confirm-approval`.",
            exit_code=EXIT_BLOCKED,
        )
    out.ok("build", result, text)
    return EXIT_OK


def dispatch_approve(args, cfg, root, out: Emitter) -> int:
    limits = cfg["limits"]
    catalog_path = Path(args.catalog) if args.catalog else root / cfg["paths"]["catalog"]
    doc = load_catalog(catalog_path, limits)
    out_dir = prepare_output_dir(args.out, cfg, root)
    store_path = approval_store_path(cfg, out_dir, root)

    if args.reset_approvals and not args.confirm_approval:
        raise ToolError(
            "APPROVAL_CONFIRMATION_REQUIRED",
            "Resetting the approval store discards recorded approvals.",
            "Re-run with --confirm-approval after confirming with the workbook owner.",
            exit_code=EXIT_BLOCKED,
        )
    store = load_approvals(store_path, limits, reset=args.reset_approvals)
    if args.confirm_approval:
        backup = store_path.with_name(f"{store_path.stem}.backup.json")
        if store_path.exists():
            shutil.copy2(store_path, backup)
        plan_path = out_dir / cfg["outputs"]["plan_json"]
        if not plan_path.exists():
            raise ToolError(
                "PLAN_MISSING",
                f"No activation plan found at {plan_path}.",
                f"Run `plan` or `build` with --out {out_dir} first.",
            )
        plan_doc = load_json(plan_path, limits["max_input_bytes"])
        plans = plan_doc["plans"]
        wanted_ids = set(args.capability or [])
        known_ids = {p["id"] for p in plans}
        if wanted_ids:
            unknown = wanted_ids - known_ids
            if unknown:
                raise ToolError(
                    "CAPABILITY_NOT_IN_PLAN",
                    f"Capabilities not in this plan: {', '.join(sorted(unknown))}",
                    "Use ids from " + ", ".join(sorted(known_ids)[:10]),
                )
            targets = [p for p in plans if p["id"] in wanted_ids]
        else:
            targets = plans

        now = now_utc()
        expires = now + timedelta(hours=args.hours or cfg["approvals"]["ttl_hours"])
        recorded = []
        skip = []
        for plan in targets:
            for gate in plan["gates"]:
                if not gate["approval_required"]:
                    continue
                if args.gate and gate["gate"] not in split_repeated(args.gate):
                    continue
                store["approvals"][f"{plan['id']}:{gate['gate']}"] = {
                    "approver": args.approver,
                    "approved_at": iso(now),
                    "expires_at": iso(expires),
                    "scope": gate["gate"],
                    "note": args.note or "",
                    "capability": plan["name"],
                }
                recorded.append(f"{plan['id']}:{gate['gate']}")
        for plan in targets:
            if not any(g["approval_required"] for g in plan["gates"]):
                skip.append(plan["id"])
        store["updated_at"] = iso(now)
        atomic_write_json(store_path, store)
        result = {
            "approval_store": str(store_path),
            "approver": args.approver,
            "expires_at": iso(expires),
            "recorded": recorded,
            "skipped_no_gate": skip,
            "backup": str(backup.name) if backup.exists() else None,
        }
        if not recorded:
            out.ok("approve", result, "No gated capabilities matched; nothing was recorded.")
            return EXIT_OK
        out.ok("approve", result, f"Recorded {len(recorded)} approval(s) valid until {iso(expires)} -> {store_path}")
        return EXIT_OK

    # read-only status view
    plan_path = out_dir / cfg["outputs"]["plan_json"]
    if not plan_path.exists():
        raise ToolError("PLAN_MISSING", f"No activation plan found at {plan_path}.", "Run `plan` or `build` first.")
    plans = load_json(plan_path, limits["max_input_bytes"])["plans"]
    gates = gate_report(plans, store, now_utc())
    rows = [f"{g['state']:<9} {g['capability_id']:<6} {g['gate']:<22} {g['capability']}" for g in gates if g["approval_required"]]
    out.ok("approve", {"approvals": gates, "store": str(store_path)}, "\n".join(rows) or "No approval gates in this plan.")
    return EXIT_OK


def dispatch_audit(args, cfg, root, out: Emitter) -> int:
    limits = cfg["limits"]
    if args.workbook:
        report = audit_workbook(Path(args.workbook), limits)
    elif args.input:
        raw = read_text_bounded(Path(args.input), limits["max_input_bytes"])
        report = audit_text(raw, limits, source=str(args.input))
    else:
        raise ToolError("MISSING_INPUT", "audit needs --input <file> or --workbook <file.xlsx>.", "Example: audit --input runs/activation-plan.md")
    if args.out:
        target = Path(args.out)
        if target.suffix.lower() != ".json":
            target = prepare_output_dir(args.out, cfg, root) / "risk-audit.json"
        atomic_write_json(target, report)
        report["written_to"] = str(target)
    text = (
        f"Audit: {report['source']}\n"
        f"  findings : {report['finding_count']} (overall {report['overall']})\n"
        f"  blocking : {'yes' if report['blocking'] else 'no'}"
    )
    for finding in report["findings"][:15]:
        text += f"\n  - [{finding['severity']}] {finding['rule']} line {finding['line']}: {finding['recommendation']}"
    out.ok("audit", report, text)
    if report["blocking"] and args.fail_on_high:
        raise ToolError(
            "AUDIT_BLOCKING",
            f"Audit found {report.get('blocking_finding_count', report['finding_count'])} issue(s) with severity high or above.",
            "Resolve the flagged items or re-run without --fail-on-high after documenting the exception.",
            exit_code=EXIT_BLOCKED,
        )
    return EXIT_OK


def dispatch_formulas(args, cfg, root, out: Emitter) -> int:
    limits = cfg["limits"]
    if getattr(args, "reset_patterns", False):
        PATTERN_CACHE.clear()
    patterns = load_formula_patterns(root, cfg, limits)

    if args.list or (not args.pattern and not args.validate_formula):
        table = "\n".join(f"{name:<18} {spec['description']}" for name, spec in sorted(patterns.items()))
        out.ok(
            "formulas",
            {
                "patterns": [
                    {
                        "name": name,
                        "description": spec["description"],
                        "arguments": sorted(spec["args"]),
                        "required": spec.get("required") or spec.get("rules") or sorted(spec["args"]),
                        "notes": spec["notes"],
                    }
                    for name, spec in sorted(patterns.items())
                ],
                "count": len(patterns),
            },
            f"{len(patterns)} formula patterns:\n" + table,
        )
        return EXIT_OK

    if args.validate_formula:
        result = validate_formula(args.validate_formula)
        out.ok(
            "formulas",
            result,
            f"{'VALID  ' if result['valid'] else 'INVALID'} {result['formula']}"
            + "".join(f"\n  - {i}" for i in result["issues"]),
        )
        return EXIT_OK

    overrides = {}
    for pair in args.arg or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ToolError("FORMULA_ARG_INVALID", f"--arg must be key=value, got {pair!r}")
        overrides[key.strip()] = value
    formula = generate_formula(args.pattern, overrides, patterns)
    result = validate_formula(formula)
    result["pattern"] = args.pattern
    result["notes"] = patterns[args.pattern]["notes"]

    if args.out:
        target = prepare_output_dir(args.out, cfg, root) / "formula.json"
        atomic_write_json(target, result)
        result["written_to"] = str(target)
    out.ok(
        "formulas",
        result,
        f"{formula}\n  valid: {result['valid']}\n  notes: {result['notes']}"
        + "".join(f"\n  issue: {i}" for i in result["issues"]),
    )
    return EXIT_OK


def dispatch_validate_workbook(args, cfg, root, out: Emitter) -> int:
    path = Path(args.workbook)
    if not path.exists():
        raise ToolError("FILE_NOT_FOUND", f"Workbook not found: {path}", "Pass the .xlsx produced by `build`.")
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ToolError("NOT_A_WORKBOOK", f"{path.name} is not an .xlsx/.xlsm file.", "Older .xls files cannot be inspected; open and re-save as .xlsx.")
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ToolError("OPENPYXL_MISSING", "openpyxl is required to inspect workbooks.", "pip install openpyxl==3.1.5")
    wb = load_workbook(path, data_only=False)
    sheets = []
    for ws in wb.worksheets:
        formula_cells = 0
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_cells += 1
        sheets.append(
            {
                "title": ws.title,
                "dimensions": ws.dimensions,
                "max_row": ws.max_row,
                "max_column": ws.max_column,
                "formula_cells": formula_cells,
                "freeze_panes": ws.freeze_panes,
                "data_validations": len(ws.data_validations.dataValidation),
                "protected": bool(getattr(getattr(ws, "protection", None), "sheet", False)),
                "state": ws.sheet_state,
            }
        )
    report = {
        "workbook": str(path),
        "bytes": path.stat().st_size,
        "sheet_count": len(sheets),
        "sheets": sheets,
        "defined_names": list(wb.defined_names.keys()) if hasattr(wb.defined_names, "keys") else [],
        "external_links": len(getattr(wb, "_external_links", []) or []),
        "has_skill_md": False,
        "validation": "ok" if sheets and all(s["max_row"] >= 1 for s in sheets) else "empty-workbook",
    }
    text = "\n".join(
        [f"{path.name}: {report['sheet_count']} sheet(s), {report['bytes']} bytes"]
        + [f"  {s['title']:<20} rows={s['max_row']:<6} cols={s['max_column']:<4} formulas={s['formula_cells']:<4} freeze={s['freeze_panes']}" for s in sheets]
    )
    out.ok("validate-workbook", report, text)
    return EXIT_OK


def dispatch_check(args, cfg, root, out: Emitter) -> int:
    limits = cfg["limits"]
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})
        if status == "fail":
            errors.append(f"{name}: {detail}")
        elif status == "warn":
            warnings.append(f"{name}: {detail}")

    # structure
    entries = sorted(p.name for p in root.iterdir()) if root.exists() else []
    if any(e == "SKILL.md" for e in entries):
        add("no-skill-md", "fail", "Skill Tool folder must not contain SKILL.md")
    else:
        add("no-skill-md", "pass", "no SKILL.md in the component folder")
    subdirs = [e for e in entries if (root / e).is_dir()]
    if subdirs:
        add("no-subfolders", "fail", f"unexpected subfolders: {', '.join(subdirs)}")
    else:
        add("no-subfolders", "pass", "all files sit directly in the component folder")
    bad_prefix = [e for e in entries if not e.startswith(("tools-", "knowledge-", "asset-"))]
    if bad_prefix:
        add("filename-prefixes", "fail", f"unprefixed files: {', '.join(bad_prefix)}")
    else:
        add("filename-prefixes", "pass", "every file uses tools-/knowledge-/asset-")
    knowledge_files = [e for e in entries if e.startswith("knowledge-")]
    add(
        "single-knowledge-file",
        "pass" if knowledge_files == [KNOWLEDGE_FILE] else "fail",
        f"knowledge files: {', '.join(knowledge_files) or 'none'}",
    )
    tool_files = [e for e in entries if e.startswith("tools-")]
    add("primary-entry-point", "pass" if PRIMARY_FILE in tool_files else "fail", f"tools files: {', '.join(tool_files) or 'none'}")
    nested_archives = [e for e in entries if e.lower().endswith((".zip", ".tar", ".gz", ".7z", ".rar"))]
    add("no-nested-archives", "fail" if nested_archives else "pass", f"archives: {', '.join(nested_archives) or 'none'}")
    depth = max((len(p.parts) - len(root.parts) for p in root.rglob("*")), default=0) if root.exists() else 0
    add("path-depth", "pass" if depth <= 1 else "fail", f"max depth below component folder: {depth}")

    # config
    add("config", "pass", f"config source: {cfg.get('_config_path')}")

    # catalog
    try:
        doc = load_catalog(root / cfg["paths"]["catalog"], limits)
        add("catalog", "pass", f"{doc['category_count']} categories / {doc['skill_count']} capabilities validated")
    except ToolError as exc:
        doc = None
        add("catalog", "fail", exc.message)

    # capability map coverage
    try:
        capdoc = load_capability_docs(root / "asset-capability-map.json", limits)
        if doc:
            _, flat = catalog_index(doc)
            missing = [s["id"] for s in flat if capability_key(s) not in capdoc["_by_key"]]
            add(
                "capability-map-coverage",
                "pass" if not missing else "fail",
                f"{len(capdoc['_by_key'])} entries; missing ids: {', '.join(missing[:8]) or 'none'}",
            )
        else:
            add("capability-map-coverage", "warn", "catalog unavailable; coverage not checked")
    except ToolError as exc:
        add("capability-map-coverage", "fail", exc.message)

    # policy + contract + assets
    for key, label in (("policy", "asset-approval-policy.json"), ("contract", "asset-output-contract.schema.json")):
        try:
            load_json(root / cfg["paths"][key], limits["max_input_bytes"])
            add(label, "pass", "parsed as JSON object")
        except ToolError as exc:
            add(label, "fail", exc.message)
    # output contract self-consistency: every ToolError code must be declared
    try:
        contract = load_json(root / cfg["paths"]["contract"], limits["max_input_bytes"])
        declared = set(
            contract.get("$defs", {}).get("errorEnvelope", {}).get("properties", {}).get("error", {})
            .get("properties", {}).get("code", {}).get("enum", [])
        )
        used = set(re.findall(r'ToolError\(\s*"([A-Z_]+)"', Path(__file__).read_text(encoding="utf-8")))
        undeclared = sorted(used - declared)
        unused = sorted(declared - used)
        add(
            "output-contract-codes",
            "pass" if not undeclared else "fail",
            f"{len(used)} codes raised, {len(declared)} declared; undeclared: {', '.join(undeclared) or 'none'}"
            + (f"; declared-but-unused: {', '.join(unused)}" if unused else ""),
        )
    except ToolError as exc:
        add("output-contract-codes", "fail", exc.message)

    try:
        patterns = load_json(root / cfg["paths"]["formula_patterns"], limits["max_input_bytes"])
        add("asset-formula-patterns.json", "pass", f"{len(patterns.get('patterns', {}))} patterns")
    except ToolError as exc:
        add("asset-formula-patterns.json", "warn", exc.message)
    for asset in (PLAN_TEMPLATE_ASSET, GOVERNANCE_ASSET, NODE_PARSER):
        add(asset, "pass" if (root / asset).exists() else "fail", "present" if (root / asset).exists() else "missing")

    # knowledge references
    if (root / KNOWLEDGE_FILE).exists():
        text = read_text_bounded(root / KNOWLEDGE_FILE, limits["max_input_bytes"])
        # Only component files (tools-/asset-/knowledge- prefixes) must exist here.
        # Bare artefact names such as activation-plan.json live in the --out directory.
        referenced = {
            r
            for r in re.findall(r"`(?:\./)?([A-Za-z0-9._-]+\.(?:py|mjs|json|md))`", text)
            if r.startswith(("tools-", "asset-", "knowledge-"))
        }
        missing_refs = sorted(r for r in referenced if not (root / r).exists())
        add("knowledge-references", "pass" if not missing_refs else "fail", f"{len(referenced)} references checked; missing: {', '.join(missing_refs) or 'none'}")
        absolute = re.findall(r"(?<![\w.])/(?:home|Users|mnt|var|opt)/[\w./-]+", text)
        add("no-absolute-paths", "pass" if not absolute else "fail", f"absolute paths found: {', '.join(absolute[:3]) or 'none'}")
    else:
        add("knowledge-reference-check", "fail", f"{KNOWLEDGE_FILE} not found")

    # solution folder name
    add("folder-name", "pass" if SLUG_RE.match(root.name) else "warn", f"folder '{root.name}'")

    result = {
        "root": str(root),
        "file_count": len([p for p in root.rglob("*") if p.is_file()]) if root.exists() else 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }
    text = "\n".join(f"[{c['status'].upper():<4}] {c['check']}: {c['detail']}" for c in checks)
    text += f"\n\n{len(errors)} error(s), {len(warnings)} warning(s) in {result['file_count']} file(s)."
    out.ok("check", result, text)
    return EXIT_OK if not errors else EXIT_INVALID


def dispatch_capabilities(args, cfg, root, out: Emitter) -> int:
    rows = [{"operation": name, "description": desc} for name, desc in CAPABILITIES]
    try:
        doc = load_json(root / cfg["paths"]["catalog"], cfg["limits"]["max_input_bytes"])
        skill_total, category_total = doc.get("skill_count"), doc.get("category_count")
    except ToolError:
        skill_total, category_total = None, None
    text = "Operations:\n" + "\n".join(f"  {r['operation']:<18} {r['description']}" for r in rows)
    text += f"\n\nRunnable entry point: {PRIMARY_FILE}"
    if skill_total:
        text += f"\nCapability catalog: {skill_total} capabilities across {category_total} categories."
    out.ok(
        "capabilities",
        {
            "operations": rows,
            "entry_point": PRIMARY_FILE,
            "catalog_skills": skill_total,
            "catalog_categories": category_total,
        },
        text,
    )
    return EXIT_OK


def dispatch_self_test(args, cfg, root, out: Emitter) -> int:
    """End-to-end verification through the real command layer."""
    results: list[dict] = []
    limits = cfg["limits"]
    workdir = Path(tempfile.mkdtemp(prefix="ssl-selftest-"))
    register_cleanup(workdir)

    def record(name: str, ok: bool, detail: str) -> None:
        results.append({"test": name, "passed": bool(ok), "detail": detail})

    quiet = Emitter("json", quiet=True)

    # 1. catalog loads and validates
    try:
        doc = load_catalog(root / cfg["paths"]["catalog"], limits)
        record("catalog-validates", doc["skill_count"] == 280 and doc["category_count"] == 28, f"{doc['category_count']} categories / {doc['skill_count']} capabilities")
    except ToolError as exc:
        doc = None
        record("catalog-validates", False, exc.message)

    # 2. capability map covers the catalog
    try:
        capdoc = load_capability_docs(root / "asset-capability-map.json", limits)
        _, flat = catalog_index(doc)
        missing = [s["id"] for s in flat if capability_key(s) not in capdoc["_by_key"]]
        record("capability-map-coverage", not missing, f"missing {len(missing)} id(s) of {len(capdoc['_by_key'])}")
    except (ToolError, TypeError) as exc:
        record("capability-map-coverage", False, str(exc))

    # 3. HTML + Markdown extraction
    sample_html = """
    <div class="cat"><h2>12. Cleaning and Parsing</h2>
      <ol><li><span class="n">1.</span> <strong>Skill Name:</strong> Whitespace Cleaner
      <p class="desc">Description: Trims stray spaces and non-breaking characters safely.</p></li>
      <li><span>2.</span> <strong>Skill Name:</strong> Split Engine
      <p class="desc">Description: Splits delimited text into typed columns.</p></li></ol></div>
    """
    try:
        extracted = extract_catalog(sample_html, limits)
        ok = extracted["category_count"] == 1 and extracted["skill_count"] == 2
        ok = ok and extracted["categories"][0]["skills"][0]["slug"] == "whitespace-cleaner"
        ok = ok and "non-breaking" in extracted["categories"][0]["skills"][0]["description"]
        record("extract-html", ok, f"{extracted['category_count']} category / {extracted['skill_count']} skills")
    except ToolError as exc:
        record("extract-html", False, exc.message)

    sample_md = """
## 3. Reporting Basics
1. Skill Name: KPI Card Designer
   Description: Builds compact KPI cards with a target and a trend arrow.
2. Skill Name: Trend Chart Builder
   Description: Builds a time-series chart with a documented date axis.
"""
    try:
        extracted_md = extract_catalog(sample_md, limits)
        ok = extracted_md["skill_count"] == 2 and extracted_md["categories"][0]["name"].lower().startswith("reporting")
        record("extract-markdown", ok, f"category={extracted_md['categories'][0]['name']!r} skills={extracted_md['skill_count']}")
    except ToolError as exc:
        record("extract-markdown", False, exc.message)

    # 4. invalid input handling
    try:
        extract_catalog("   ", limits)
        record("extract-invalid-input", False, "empty input was accepted")
    except ToolError as exc:
        record("extract-invalid-input", exc.code == "INPUT_EMPTY", f"raised {exc.code}")

    # 5. selection resolves categories, ranges, skills and search
    try:
        class Sel:
            all = False
            category = ["13"]
            skill = ["27.4"]
            search = "dashboard"
            list_file = None
            max_skills = None

        selected, report = resolve_selection(doc, Sel())
        ids = {s["id"] for s in selected}
        ok = len([i for i in ids if i.startswith("13.")]) == 10 and "27.4" in ids and len(selected) > 10
        record("selection-resolution", ok, f"{len(selected)} capabilities, report notes {report['count']}")
    except ToolError as exc:
        record("selection-resolution", False, exc.message)

    try:
        class BadSel(Sel):
            category = ["999"]
            skill = []
            search = None

        resolve_selection(doc, BadSel())
        record("selection-invalid-category", False, "unknown category accepted")
    except ToolError as exc:
        record("selection-invalid-category", exc.code == "CATEGORY_NOT_FOUND", f"raised {exc.code}")

    # 6. plans, gates and policy
    try:
        policy = load_json(root / cfg["paths"]["policy"], limits["max_input_bytes"])
        by_cap = capdoc["_by_key"]
        _, flat = catalog_index(doc)
        by_skill_id = {s["id"]: s for s in flat}
        wanted_sample = ("25.4", "23.6", "1.1")
        sample = [by_skill_id[i] for i in wanted_sample if i in by_skill_id] or selected[:3]
        plans = [
            build_activation_plan(s, by_cap[capability_key(s)], policy, cfg, limits)
            for s in sample
            if capability_key(s) in by_cap
        ]
        gated = [p for p in plans if p["approval_required"]]
        record("plan-generation", len(plans) == len(sample) and bool(gated), f"{len(plans)} plans, {len(gated)} gated")
    except (ToolError, KeyError) as exc:
        plans = []
        record("plan-generation", False, str(exc))

    # 7. approval lifecycle: pending -> approved -> expired
    try:
        store = {"version": 1, "approvals": {}}
        plan = next(p for p in plans if p["approval_required"])
        gate_name = next(g["gate"] for g in plan["gates"] if g["approval_required"])
        now = now_utc()
        before, _ = approval_status(store, plan["id"], gate_name, now)
        store["approvals"][f"{plan['id']}:{gate_name}"] = {
            "approver": "selftest",
            "approved_at": iso(now),
            "expires_at": iso(now + timedelta(hours=1)),
        }
        during, detail = approval_status(store, plan["id"], gate_name, now)
        after, detail2 = approval_status(store, plan["id"], gate_name, now + timedelta(hours=2))
        record("approval-lifecycle", (not before) and during and (not after), f"pending -> {detail} -> {detail2}")
    except (StopIteration, KeyError) as exc:
        record("approval-lifecycle", False, str(exc))

    # 7b. a different source list re-using the same ids never inherits shipped semantics
    try:
        foreign = extract_catalog(
            "1. Payroll Operations\n1. Skill Name: Payroll Cycle Planner\nDescription: Plans pay periods and cut-off dates.\n",
            limits,
        )
        foreign_plans = _assemble_plans(catalog_index(foreign)[1][:1], capdoc, policy, cfg, limits, allow_derived=True)
        plan_entry = foreign_plans[0]
        record(
            "no-id-collision",
            plan_entry["semantics"] == "derived" and "Payroll Cycle Planner" == plan_entry["name"],
            f"{plan_entry['id']} {plan_entry['name']} -> {plan_entry['semantics']} semantics",
        )
    except ToolError as exc:
        record("no-id-collision", False, exc.message)

    # 7c. plan document is rendered from the template and checklist assets
    try:
        template, checklist = load_plan_scaffold(root, cfg)
        rendered = render_plan_markdown(plans, {"subject": "selftest", "selection": {}}, {"version": 1, "approvals": {}},
                                        now_utc(), root, cfg)
        first_checklist_line = next((l.strip() for l in checklist.splitlines() if l.strip()), "")
        ok = (
            "{{" not in rendered
            and rendered.startswith("# Activation Plan - selftest")
            and first_checklist_line in rendered
            and "## Pre-handoff checklist" in rendered
            and "## Approval and safety rules" in rendered
        )
        record("plan-template-render", ok, f"template {len(template)} chars, checklist item present: {bool(first_checklist_line)}")
    except (ToolError, OSError) as exc:
        record("plan-template-render", False, str(exc))

    # 8. workbook write + inspect + audit
    try:
        wb_path = workdir / "selftest-workbook.xlsx"
        info = write_workbook(wb_path, plans, {"version": 1, "approvals": {}}, now_utc(), limits)
        if info.get("written"):
            audit = audit_workbook(wb_path, limits)
            ok = wb_path.exists() and audit["structure"]["sheets"][0] == "Activation Plan" and audit["finding_count"] == 0
            record("workbook-write-and-audit", ok, f"sheets={info['sheets']} findings={audit['finding_count']}")
        else:
            record("workbook-write-and-audit", True, f"skipped: {info.get('reason')} (CSV fallback still produced)")
    except ToolError as exc:
        record("workbook-write-and-audit", False, exc.message)

    # 9. audit catches risk
    try:
        risky = 'Send email to juan.delacruz@example.com with password=Hunter2 and delete all rows.'
        report = audit_text(risky, limits)
        rules = {f["rule"] for f in report["findings"]}
        ok = {"PII_EMAIL", "SECRET_ASSIGN", "DESTRUCTIVE"} <= rules and report["blocking"]
        record("audit-detects-risk", ok, f"rules={sorted(rules)} overall={report['overall']}")
    except ToolError as exc:
        record("audit-detects-risk", False, exc.message)

    # 10. formula generation and validation
    try:
        formula = generate_formula("lookup", {"key": "B2", "key_range": "Ref!A:A", "value_range": "Ref!C:C"})
        good = validate_formula(formula)["valid"]
        bad = validate_formula("=SUM(A1:A2")
        record("formula-engine", good and not bad["valid"] and len(bad["issues"]) >= 1, f"{formula} | detected: {bad['issues']}")
    except ToolError as exc:
        record("formula-engine", False, exc.message)

    try:
        patterns = load_formula_patterns(root, cfg, limits)
        asset_only = "grade_band" in patterns
        band = generate_formula("grade_band", {"value": "D2"}, patterns)
        record(
            "formula-asset-patterns",
            asset_only and "MATCH(D2" in band and len(patterns) >= len(FORMULA_SPECS),
            f"{len(patterns)} patterns, asset-only grade_band present: {asset_only}",
        )
    except ToolError as exc:
        record("formula-asset-patterns", False, exc.message)

    try:
        generate_formula("lookup", {"nope": "1"})
        record("formula-invalid-arg", False, "unknown argument accepted")
    except ToolError as exc:
        record("formula-invalid-arg", exc.code == "FORMULA_ARG_UNKNOWN", f"raised {exc.code}")

    # 11. changing the input changes the output
    try:
        a = generate_formula("conditional_sum", {"criteria1": '"East"'})
        b = generate_formula("conditional_sum", {"criteria1": '"West"'})
        record("input-change-reflected", a != b and '"West"' in b, "criteria override changed the formula")
    except ToolError as exc:
        record("input-change-reflected", False, exc.message)

    # 12. config validation rejects a bad override
    try:
        bad_cfg = deep_merge(DEFAULT_CONFIG, {"limits": {"max_input_bytes": "big"}})
        validate_config(bad_cfg)
        record("config-validation", False, "invalid config accepted")
    except ToolError as exc:
        record("config-validation", exc.code == "CONFIG_INVALID", f"raised {exc.code}")

    # 13. structured error envelope
    try:
        raise ToolError("SELECTION_EMPTY", "example envelope", "hint")
    except ToolError as exc:
        payload = exc.as_dict()
        record(
            "structured-errors",
            payload["ok"] is False and payload["error"]["code"] in {
                c for c in json.loads((root / cfg["paths"]["contract"]).read_text(encoding="utf-8"))
                ["$defs"]["errorEnvelope"]["properties"]["error"]["properties"]["code"]["enum"]
            },
            "error envelope shaped correctly and its code is declared in the contract",
        )

    # 14. recovery path: corrupt plan JSON is reported, not crashed
    try:
        corrupt = workdir / "corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")
        load_json(corrupt, limits["max_input_bytes"])
        record("recovery-corrupt-json", False, "corrupt JSON accepted")
    except ToolError as exc:
        record("recovery-corrupt-json", exc.code == "JSON_INVALID", f"raised {exc.code} with line info")

    # 15. cancellation cleanup helper
    try:
        tmpdir = workdir / "cleanup-target"
        tmpdir.mkdir()
        (tmpdir / "x.txt").write_text("x", encoding="utf-8")
        register_cleanup(tmpdir)
        run_cleanup()
        record("cleanup-registry", not tmpdir.exists(), "registered temp path removed")
    except OSError as exc:
        record("cleanup-registry", False, str(exc))

    # 16. reference resolution from the component folder
    try:
        refs = []
        for key in ("catalog", "policy", "contract", "formula_patterns"):
            refs.append((root / cfg["paths"][key]).exists())
        for asset in ("asset-capability-map.json", PLAN_TEMPLATE_ASSET, GOVERNANCE_ASSET, NODE_PARSER):
            refs.append((root / asset).exists())
        record("asset-references", all(refs), f"{sum(refs)}/{len(refs)} referenced assets present")
    except KeyError as exc:
        record("asset-references", False, str(exc))

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    payload = {"passed": passed, "total": total, "failed": [r for r in results if not r["passed"]], "results": results}
    text = "\n".join(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['test']}: {r['detail']}" for r in results)
    text += f"\n\n{passed}/{total} checks passed."
    out.ok("self-test", payload, text)
    return EXIT_OK if passed == total else EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def add_selection_args(parser: argparse.ArgumentParser, require: bool = False) -> None:
    group = parser.add_argument_group("capability selection")
    group.add_argument("--all", action="store_true", help="select every capability in the catalog")
    group.add_argument("--category", action="append", metavar="SPEC", help="category number, range (3,7-9) or name fragment; repeatable")
    group.add_argument("--skill", action="append", metavar="ID", help="capability id (13.2), slug or exact name; repeatable")
    group.add_argument("--search", metavar="TEXT", help="free-text match over name, description and category")
    group.add_argument("--list-file", metavar="PATH", help="newline-separated capability ids, slugs or names")
    group.add_argument("--max-skills", type=int, metavar="N", help="hard cap on the number of selected capabilities")
    group.add_argument("--catalog", metavar="PATH", help=f"catalog JSON to read (default: {CATALOG_ASSET})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PRIMARY_FILE,
        description=(
            "sheets-skill-library: turn the Google Sheets Agent Skill Catalog into reviewed, "
            "gated activation plans and workbooks."
        ),
        epilog=(
            f"Run `python {PRIMARY_FILE} capabilities` for the operation list and "
            f"`python {PRIMARY_FILE} self-test` to verify the installation."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    parser.add_argument("--config", metavar="PATH", help=f"config file (default: {CONFIG_FILE} beside this tool)")
    parser.add_argument("--skill-root", metavar="PATH", help="component folder containing tools-/asset- files (default: script folder)")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")
    parser.add_argument("--quiet", action="store_true", help="suppress human-readable text")
    parser.add_argument("--log-level", choices=("error", "warn", "info", "debug"), default="info")
    parser.add_argument("--debug", action="store_true", help="print a traceback on unexpected failures")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_cat = sub.add_parser("catalog", help="browse, filter and search the capability catalog")
    p_cat.add_argument("--category", action="append", metavar="SPEC", help="category number, range or name fragment")
    p_cat.add_argument("--search", metavar="TEXT", help="free-text filter")
    p_cat.add_argument("--catalog", metavar="PATH", help="catalog JSON path")
    p_cat.set_defaults(func=dispatch_catalog)

    p_ext = sub.add_parser("extract", help="parse text/Markdown/HTML capability lists into catalog JSON")
    p_ext.add_argument("--input", metavar="PATH", help="source .txt/.md/.html file")
    p_ext.add_argument("--out", metavar="PATH", default="extracted-catalog.json", help="output .json file or directory")
    p_ext.set_defaults(func=dispatch_extract)

    p_plan = sub.add_parser("plan", help="build activation plans without producing a workbook")
    add_selection_args(p_plan)
    p_plan.add_argument("--out", metavar="DIR", help="output directory (default: runs/)")
    p_plan.add_argument("--subject", metavar="NAME", help="label for the plan header")
    p_plan.add_argument("--reset-approvals", action="store_true", help="ignore and recreate the approval store in --out")
    p_plan.add_argument("--fail-on-blocked", action="store_true", help="exit 3 when any approval gate is pending")
    p_plan.add_argument("--allow-derived", action="store_true", help="use baseline semantics for capabilities missing from asset-capability-map.json")
    p_plan.set_defaults(func=dispatch_plan)

    p_build = sub.add_parser("build", help="produce the full activation bundle")
    add_selection_args(p_build)
    p_build.add_argument("--out", metavar="DIR", help="output directory (default: runs/)")
    p_build.add_argument("--subject", metavar="NAME", help="label for the plan header")
    p_build.add_argument("--no-workbook", action="store_true", help="skip the .xlsx deliverable")
    p_build.add_argument("--no-workbook-overwrite", action="store_true", help="refuse to replace an existing workbook")
    p_build.add_argument("--reset-approvals", action="store_true", help="discard recorded approvals in --out first")
    p_build.add_argument("--fail-on-blocked", action="store_true", help="exit 3 after writing artifacts when gates are pending")
    p_build.add_argument("--allow-derived", action="store_true", help="use baseline semantics for capabilities missing from asset-capability-map.json")
    p_build.set_defaults(func=dispatch_build)

    p_app = sub.add_parser("approve", help="record or inspect human approvals")
    p_app.add_argument("--out", metavar="DIR", help="run directory holding activation-plan.json")
    p_app.add_argument("--capability", action="append", metavar="ID", help="capability id to approve; repeatable (default: all)")
    p_app.add_argument("--gate", action="append", metavar="NAME", help="limit to a specific gate; repeatable")
    p_app.add_argument("--approver", metavar="NAME", default="", help="named human approver (required with --confirm-approval)")
    p_app.add_argument("--hours", type=float, metavar="H", help="approval lifetime in hours (default: approvals.ttl_hours)")
    p_app.add_argument("--note", metavar="TEXT", help="justification recorded with the approval")
    p_app.add_argument("--confirm-approval", action="store_true", help="apply the approval; without it this is a read-only status view")
    p_app.add_argument("--reset-approvals", action="store_true", help="clear the store (requires --confirm-approval)")
    p_app.add_argument("--catalog", metavar="PATH", help="catalog JSON path")
    p_app.set_defaults(func=dispatch_approve)

    p_aud = sub.add_parser("audit", help="scan text or a workbook for privacy and execution risk")
    p_aud.add_argument("--input", metavar="PATH", help="text/JSON/Markdown file to scan")
    p_aud.add_argument("--workbook", metavar="PATH", help=".xlsx file to scan")
    p_aud.add_argument("--out", metavar="PATH", help="write JSON report to this file or directory")
    p_aud.add_argument("--fail-on-high", action="store_true", help="exit 3 when severity is high or above")
    p_aud.set_defaults(func=dispatch_audit)

    p_for = sub.add_parser("formulas", help="generate and validate Google Sheets formulas")
    p_for.add_argument("--pattern", metavar="NAME", help="formula pattern name")
    p_for.add_argument("--arg", action="append", metavar="KEY=VALUE", help="override a pattern argument; repeatable")
    p_for.add_argument("--validate-formula", metavar="FORMULA", help="validate a literal formula and exit")
    p_for.add_argument("--list", action="store_true", help="list available patterns")
    p_for.add_argument("--reset-patterns", action="store_true", help="ignore the in-process pattern cache (use after editing asset-formula-patterns.json)")
    p_for.add_argument("--out", metavar="DIR", help="write the result to DIR/formula.json")
    p_for.set_defaults(func=dispatch_formulas)

    p_val = sub.add_parser("validate-workbook", help="inspect a produced .xlsx")
    p_val.add_argument("--workbook", metavar="PATH", required=True, help=".xlsx/.xlsm file to inspect")
    p_val.set_defaults(func=dispatch_validate_workbook)

    p_chk = sub.add_parser("check", help="validate config, catalog, assets and package structure")
    p_chk.set_defaults(func=dispatch_check)

    p_cap = sub.add_parser("capabilities", help="list implemented operations")
    p_cap.set_defaults(func=dispatch_capabilities)

    p_st = sub.add_parser("self-test", help="run the built-in verification suite")
    p_st.set_defaults(func=dispatch_self_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    parser = build_parser()
    args = parser.parse_args(argv)
    out = Emitter(getattr(args, "format", "text"), getattr(args, "quiet", False))

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_INVALID

    root = Path(args.skill_root).resolve() if args.skill_root else Path(__file__).resolve().parent
    try:
        cfg = load_config(args.config, root)
        return int(args.func(args, cfg, root, out))
    except ToolError as exc:
        if out.fmt == "json":
            print(json.dumps(exc.as_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"ERROR [{exc.code}] {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"HINT  {exc.hint}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        if out.fmt == "json":
            print(json.dumps({"ok": False, "error": {"code": "CANCELLED", "message": "cancelled by user"}}))
        else:
            print("\nCancelled.", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        if getattr(args, "debug", False):
            traceback.print_exc()
        if out.fmt == "json":
            print(json.dumps({"ok": False, "error": {"code": "INTERNAL", "message": str(exc)}}))
        else:
            print(f"INTERNAL ERROR: {exc}", file=sys.stderr)
            print("Re-run with --debug for a traceback.", file=sys.stderr)
        return EXIT_INTERNAL
    finally:
        run_cleanup()


if __name__ == "__main__":
    sys.exit(main())
