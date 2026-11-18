#!/usr/bin/env python3
"""tools-gates.py -- layer 4 of the offline-game-development Skill Tool.

Role in the Skill Tool
    The checks that decide whether work may be called done, and the reports around them:
    a JSON-Schema subset validator (only the keywords listed in the schema asset's
    validator_support are honoured, and 'doctor' fails if that list and the schema drift),
    the rights gate R1-R10, the performance budget gate, the evidence gate, the offline
    audit of this tool's own folder, coverage statistics of the catalog, and 'doctor'.

    Every check returns data. It raises GateFailure / ApprovalRequired only where a caller asked
    for enforcement, so a report can always be produced and inspected.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ALIASES = {"tools-runtime-kit.py": "ogd_tools-runtime-kit", "tools-catalog-engine.py": "ogd_tools-catalog-engine",
            "tools-artifact-generators.py": "ogd_tools-artifact-generators"}


def _load(filename: str):
    key = _ALIASES[filename]
    if key in sys.modules:
        return sys.modules[key]
    import importlib.util

    spec = importlib.util.spec_from_file_location(key, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


kit = _load("tools-runtime-kit.py")
engine = _load("tools-catalog-engine.py")
generators = _load("tools-artifact-generators.py")

TRUTHY = {"true", "yes", "y", "1", "allowed", "permitted", "commercial", "redistributable"}
FALSY = {"false", "no", "n", "0", "not-allowed", "prohibited", "none"}
FILL_TOKENS = {"", "fill", "tbd", "n/a", "none", "unknown", "-", "?"}
# The policy tells a team to write 'n/a' rather than leave a field blank, so R2 does not count
# 'n/a' as empty; R6 still does, because a generated asset needs a real generator name.
PROVENANCE_BLANK = FILL_TOKENS - {"n/a"}


# ---------------------------------------------------------------------------
# schema subset validator
# ---------------------------------------------------------------------------
def _resolve_ref(node: dict, root: dict):
    ref = node.get("$ref") or ""
    if not ref.startswith("#/"):
        return None, f"unsupported $ref {ref!r} (only local '#/...' pointers are honoured)"
    current = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None, f"$ref {ref!r} does not resolve"
        current = current[part]
    return current, ""


def validate_against_schema(payload, schema: dict, root: dict | None = None, path: str = "$") -> list:
    """Validate a record against the supported JSON-Schema subset. Returns issues.

    Each issue is {at, keyword, expected, found, message}. Unsupported keywords are ignored on
    purpose; 'schema-consistency' in doctor reports any keyword the schema uses that is not in
    the schema asset's own validator_support list, so the two files cannot drift silently.
    """
    root = root if root is not None else schema
    issues: list = []
    if not isinstance(schema, dict):
        return issues
    if "$ref" in schema:
        target, problem = _resolve_ref(schema, root)
        if problem:
            return [{"at": path, "keyword": "$ref", "expected": schema["$ref"], "found": "",
                     "message": problem}]
        issues += validate_against_schema(payload, target, root, path)
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}

    def issue(keyword, expected, found, message):
        issues.append({"at": path, "keyword": keyword, "expected": expected,
                       "found": found if isinstance(found, (str, int, float, bool, type(None))) else
                       type(found).__name__, "message": message})

    if "const" in schema and payload != schema["const"]:
        issue("const", schema["const"], payload, f"must be exactly {schema['const']!r}")
    if "enum" in schema and payload not in schema["enum"]:
        issue("enum", schema["enum"], payload, "value is not one of the allowed values")
    types = schema.get("type")
    if types:
        names = types if isinstance(types, list) else [types]
        if not any(_type_ok(payload, name) for name in names):
            issue("type", "|".join(names), payload, f"expected {'|'.join(names)}, found "
                  f"{type(payload).__name__}")
            return issues
    if isinstance(payload, str):
        if "minLength" in schema and len(payload) < schema["minLength"]:
            issue("minLength", schema["minLength"], len(payload), "string is too short")
        if "maxLength" in schema and len(payload) > schema["maxLength"]:
            issue("maxLength", schema["maxLength"], len(payload), "string is too long")
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, payload):
            issue("pattern", pattern, payload[:80], "string does not match the required pattern")
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        if "minimum" in schema and payload < schema["minimum"]:
            issue("minimum", schema["minimum"], payload, "number is below the minimum")
        if "maximum" in schema and payload > schema["maximum"]:
            issue("maximum", schema["maximum"], payload, "number is above the maximum")
    if isinstance(payload, list):
        if "minItems" in schema and len(payload) < schema["minItems"]:
            issue("minItems", schema["minItems"], len(payload), "array is too short")
        if "maxItems" in schema and len(payload) > schema["maxItems"]:
            issue("maxItems", schema["maxItems"], len(payload), "array is too long")
        items = schema.get("items")
        if isinstance(items, dict):
            for position, value in enumerate(payload):
                issues += validate_against_schema(value, items, root, f"{path}[{position}]")
    if isinstance(payload, dict):
        for required in schema.get("required") or []:
            if required not in payload or payload[required] in (None, ""):
                issue("required", required, "missing" if required not in payload else "empty",
                      f"required property '{required}' is not filled in")
        properties = schema.get("properties") or {}
        for key, value in payload.items():
            if key in properties:
                issues += validate_against_schema(value, properties[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                issue("additionalProperties", sorted(properties), key, f"'{key}' is not an allowed property")
        if "minProperties" in schema and len(payload) < schema["minProperties"]:
            issue("minProperties", schema["minProperties"], len(payload), "object has too few properties")
    if "anyOf" in schema:
        branches = [validate_against_schema(payload, branch, root, path) for branch in schema["anyOf"]]
        if all(branches):
            issue("anyOf", len(schema["anyOf"]), payload, "no branch of anyOf matched")
    if "oneOf" in schema:
        matched = [index for index, branch in enumerate(schema["oneOf"])
                   if not validate_against_schema(payload, branch, root, path)]
        if len(matched) != 1:
            issue("oneOf", "exactly one branch", f"{len(matched)} matched", "oneOf did not match exactly once")
    return issues


def _type_ok(value, name: str) -> bool:
    return {"object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "null": lambda v: v is None}.get(name, lambda v: True)(value)


SCHEMA_KEYWORDS = {"$ref", "$defs", "$id", "$schema", "type", "enum", "const", "required", "properties",
                   "items", "additionalProperties", "pattern", "patternProperties", "propertyNames",
                   "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems",
                   "uniqueItems", "minLength", "maxLength", "minProperties", "maxProperties", "anyOf",
                   "allOf", "oneOf", "not", "if", "then", "else", "contains", "format", "default",
                   "examples", "title", "description", "nullable", "definitions"}


def schema_keywords(schema, found=None) -> set:
    """Every keyword the schema actually uses, recursively."""
    found = found if found is not None else set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in SCHEMA_KEYWORDS:
                found.add(key)
            schema_keywords(value, found)
    elif isinstance(schema, list):
        for item in schema:
            schema_keywords(item, found)
    return found


def check_schema_support(schema: dict) -> list:
    """Findings where the schema uses a keyword the validator does not honour."""
    supported = set(schema.get("validator_support") or [])
    used = schema_keywords(schema) - {"$schema", "$id", "title", "description", "$defs",
                                      "validator_support", "default", "examples"}
    missing = sorted(used - supported)
    return [] if not missing else [{"severity": "error", "code": "E-SCHEMA-KEYWORD",
                                    "detail": f"schema uses {missing}, which validator_support does not "
                                              f"list; the validator would silently ignore them",
                                    "location": "validator_support"}]


def load_schema(path: str, max_mb: float = 8, deadline=None) -> dict:
    schema = kit.load_json(path, max_mb, deadline)
    if not isinstance(schema, dict):
        raise kit.InputError("the output schema must be a JSON object", where=path)
    problems = check_schema_support(schema)
    if problems:
        raise kit.InputError(problems[0]["detail"],
                            hint="either implement the keyword in tools-gates.py or remove it from "
                                 "asset-output-schema.json; run 'doctor' to re-check", where=path)
    return schema


def record_schema(schema: dict, name: str) -> dict:
    defs = schema.get("$defs") or {}
    if name not in defs:
        available = ", ".join(sorted(defs))
        raise kit.NotFoundError(f"the output schema has no record definition named {name!r}",
                                hint=f"available: {available}", where="$defs." + name)
    return defs[name]


# ---------------------------------------------------------------------------
# rights
# ---------------------------------------------------------------------------
def load_manifest(path: str, max_mb: float = 16, deadline=None) -> tuple:
    """Read a rights manifest: JSON array, JSON object with assets[], or delimited CSV/TSV."""
    findings: list = []
    text = kit.read_text(path, max_mb, deadline)
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise kit.InputError(f"invalid JSON in the rights manifest: {exc}",
                                 hint="the manifest is an array of records or an object with an 'assets' "
                                      "array", where=path) from exc
        if isinstance(raw, dict):
            findings += [{"severity": "warning", "code": "W-MANIFEST-KEYS",
                          "detail": f"the object carries extra top-level keys "
                                    f"{sorted(set(raw) - {'assets', 'notes'})}", "location": path}]
            records = raw.get("assets") or raw.get("records") or raw.get("entries") or []
        else:
            records = raw
        if not isinstance(records, list):
            raise kit.InputError("the rights manifest must hold an array of records", where=path)
        return [dict(record) for record in records if isinstance(record, dict)], findings
    import csv as _csv

    first = (text or "").split("\n", 1)[0]
    delimiter = "\t" if "\t" in first else ","
    rows = list(_csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) < 2:
        raise kit.InputError(f"the rights manifest at {os.path.basename(path)} has no data rows",
                             hint="row 1 is the header; then one row per asset, with the required columns "
                                  "named in the policy's manifest_input_contract", where=path)
    header = [kit.tidy(cell).lower().replace(" ", "_").replace("-", "_") for cell in rows[0]]
    records = []
    for row in rows[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        record = {name: (row[pos].strip() if pos < len(row) else "") for pos, name in enumerate(header)}
        record["_source"] = os.path.basename(path)
        records.append(record)
    if not records:
        raise kit.InputError(f"no manifest records were read from {os.path.basename(path)}",
                             hint="the CSV needs an 'asset' column and a 'license' column, plus the "
                                  "required provenance fields", where=path)
    return records, findings


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    return None


def rights_check(records: list, policy: dict, ctx, commercial: bool = True, bundle: bool | None = None,
                 root: str = ".", enforce_record_files: bool = False, today: str = "") -> dict:
    """Apply R1-R10 to a rights manifest. Returns findings, per-asset verdicts, and a summary.

    Waiving is never automatic: 'rights_check' only reports. A waiver is the CLI's job and needs
    the approval token the policy names, because the decision belongs to a human owner and to the
    asset's own record file.
    """
    gates = {gate["rule"]: gate for gate in policy.get("gates", [])}
    required = list(policy.get("provenance_required_fields") or [])
    by_text = ""
    licenses = {str(item.get("id", "")).lower(): item for item in policy.get("licenses", [])}
    aliases = {"cc0": "cc0-1.0", "cc-by": "cc-by-4.0", "mit-expat": "mit", "standard-unity":
               "unity-license", "ue-standard": "epic-games"}
    kinds = set(policy.get("asset_kinds") or [])
    window = int(policy.get("review_window_days") or 365)
    verdicts: list = []
    findings: list = []
    bundle_default = policy.get("bundle_default", False) if bundle is None else bundle

    def gate_finding(rule, record, message="", **details):
        spec = gates.get(rule, {})
        finding = {"gate_id": spec.get("id", rule.upper()), "rule": rule,
                   "severity": spec.get("severity", "warn"), "result": "fail",
                   "message": message or spec.get("message", rule),
                   "remediation": spec.get("remediation", ""),
                   "applies_to": str(record.get("asset", "?"))}
        if details:
            finding["details"] = details
        findings.append(finding)
        return finding

    for position, record in enumerate(records, start=1):
        asset = str(record.get("asset") or record.get("name") or f"record #{position}")
        license_id = str(record.get("license") or "").strip()
        lowered = license_id.lower()
        lowered = aliases.get(lowered, lowered)
        known = licenses.get(lowered)
        asset_findings: list = []
        kind = str(record.get("kind") or "").strip().lower()

        # R1 license-known
        if not license_id:
            asset_findings.append(gate_finding("license-known", record, "license is empty",
                                               asset=asset))
        elif known is None:
            asset_findings.append(gate_finding("license-known", record,
                                               f"license {license_id!r} is not in the bundled policy",
                                               asset=asset, suggestion=_closest(sorted(licenses), lowered)))
        # R2 required fields
        empty = [field for field in required if str(record.get(field, "")).strip().lower() in PROVENANCE_BLANK
                 and record.get(field) is not None]
        if [field for field in required if field not in record]:
            empty = sorted(set(empty) | (set(required) - set(record)))
        if empty:
            asset_findings.append(gate_finding("required-fields", record,
                                               f"provenance fields not filled: {', '.join(sorted(empty))}",
                                               fields=sorted(empty)))
        if kind and kinds and kind not in kinds:
            findings.append({"gate_id": "K1", "rule": "asset-kind", "severity": "warn", "result": "warn",
                             "message": f"asset kind {kind!r} is not in the policy's kind list",
                             "applies_to": asset,
                             "remediation": "add the kind to asset-license-policy.json or correct the record"})
        # R3 commercial use
        wants_bundle = record.get("bundled")
        wants_bundle = bundle_default if wants_bundle is None else bool(_flag(wants_bundle))
        if commercial and known is not None and known.get("commercial_use") is False:
            asset_findings.append(gate_finding("commercial-use", record,
                                               f"{license_id} forbids commercial use; this project is "
                                               f"declared commercial", license=license_id))
        # R4 redistribution
        if wants_bundle and known is not None and known.get("redistribution") is False:
            asset_findings.append(gate_finding("redistribution", record,
                                               f"{license_id} forbids redistribution and this asset is "
                                               f"bundled", license=license_id))
        # R5 attribution
        attribution = record.get("attribution") or record.get("attribution_text") or ""
        if known is not None and known.get("attribution_required") and str(attribution).strip() in FILL_TOKENS:
            asset_findings.append(gate_finding("attribution", record,
                                               f"{license_id} requires a credit line, none is recorded"))
        # R6 generated assets
        generated = kind in ("generated-asset", "model-weights", "dataset") or "generated" in lowered
        by = str(record.get("generated_by") or "").strip()
        if generated and by.lower() in FILL_TOKENS:
            asset_findings.append(gate_finding("generated-assets", record,
                                               f"{asset} is generated content with no generator or review "
                                               f"recorded"))
        # R7 engines and SDKs
        engine_like = kind in ("engine", "sdk", "engine-module", "middleware") or any(
            needle in lowered for needle in ("console", "confidential", "ndk", "sdk-"))
        if engine_like and wants_bundle:
            asset_findings.append(gate_finding("engine-and-sdks", record,
                                               f"{asset} may not be bundled into a shared package",
                                               license=license_id or None))
        # R8 record file
        record_path = str(record.get("record_path") or "").strip()
        if record_path and record_path.lower() not in FILL_TOKENS:
            resolved = record_path if os.path.isabs(record_path) else os.path.join(root, record_path)
            if not os.path.isfile(resolved):
                severity_block = enforce_record_files
                asset_findings.append(gate_finding("record-file", record,
                                                   f"record_path {record_path} does not exist",
                                                   expected_path=record_path))
                if severity_block:
                    asset_findings[-1]["severity"] = "block"
        # R9 stale record
        reviewed = str(record.get("reviewed_on") or record.get("acquired_on") or "").strip()
        age = _age_days(reviewed, today)
        if age is not None and age > window:
            asset_findings.append(gate_finding("stale-record", record,
                                               f"the record is {age} days old, past the {window}-day review "
                                               f"window", age_days=age))
        # R10 approval required
        if _flag(record.get("requires_approval")) or any(
                needle in asset.lower() for needle in ("payout", "tax", "store-listing")):
            asset_findings.append(gate_finding("approval-required", record,
                                               f"{asset} needs a named human approver, not an automated pass"))
        blockers = [f for f in asset_findings if f["severity"] == "block"]
        verdicts.append({"asset": asset, "kind": kind or "unspecified", "license": license_id or "unset",
                         "license_known": known is not None, "commercial_use": _flag(record.get("commercial_use")),
                         "redistribution": _flag(record.get("redistribution")), "bundled": wants_bundle,
                         "findings": [f["gate_id"] for f in asset_findings], "blockers": len(blockers),
                         "warnings": len([f for f in asset_findings if f["severity"] != "block"]),
                         "verdict": "blocked" if blockers else
                         ("review" if asset_findings else "cleared")})
        for finding in asset_findings:
            finding["location"] = f"{os.path.basename(str(record.get('_source', 'manifest')))}#{asset}"

    counts = {"records": len(records), "cleared": sum(1 for v in verdicts if v["verdict"] == "cleared"),
              "review": sum(1 for v in verdicts if v["verdict"] == "review"),
              "blocked": sum(1 for v in verdicts if v["verdict"] == "blocked"),
              "by_rule": {}}
    for finding in findings:
        counts["by_rule"][finding["gate_id"]] = counts["by_rule"].get(finding["gate_id"], 0) + 1
    return {"kind": "rights-report", "commercial_use": commercial, "bundle": bundle_default,
            "review_window_days": window, "policy_id": policy.get("policy_id"),
            "verdict": "blocked" if counts["blocked"] else ("review" if counts["review"] else "cleared"),
            "verdicts": verdicts, "findings": findings, "summary": counts,
            "waiver_allowed": bool((policy.get("waiver") or {}).get("allowed"))}


def _closest(candidates, needle: str, limit: int = 3) -> list:
    needle = str(needle).lower()
    scored = []
    for candidate in candidates:
        if needle and (needle in candidate or candidate in needle):
            scored.append((2, candidate))
        elif needle and len(set(needle) & set(candidate)) / max(1, len(set(needle) | set(candidate))) > 0.6:
            scored.append((1, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in scored[:limit]]


def _age_days(date_text: str, today: str = "") -> int | None:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(date_text or ""))
    if not match:
        return None
    import datetime

    try:
        parsed = datetime.date(*map(int, match.groups()))
    except ValueError:
        return None
    now = datetime.date.today()
    if today:
        ref = re.match(r"^(\d{4})-(\d{2})-(\d{2})", today)
        if ref:
            now = datetime.date(*map(int, ref.groups()))
    return (now - parsed).days


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------
def load_measurements(path: str, max_mb: float = 16, deadline=None) -> list:
    raw = kit.load_json(path, max_mb, deadline)
    records = raw.get("measurements") if isinstance(raw, dict) else raw
    if not isinstance(records, list) or not records:
        raise kit.InputError(f"no measurement records found in {os.path.basename(path)}",
                             hint="pass an array of {capability_id, profile, values} objects, as "
                                  "offline-game-development/asset-output-schema.json defines "
                                  "measurements_record", where=path)
    return [dict(record) for record in records if isinstance(record, dict)]


def budget_check(measurements: list, budgets: dict, ctx, profile_override: str = "",
                 schema: dict | None = None) -> dict:
    """Compare measured values with the device profile ceilings.

    headroom is ceiling minus value for a 'max' metric and value minus ceiling for a 'min' one, so a
    positive number always means inside the budget and a negative one means over it.
    """
    profiles = budgets.get("profiles") or {}
    metrics = budgets.get("metrics") or {}
    findings: list = []
    rows: list = []
    blocked = 0
    warned = 0
    for position, record in enumerate(measurements, start=1):
        profile_name = str(record.get("profile") or profile_override or ctx.device_profile)
        profile = profiles.get(profile_name)
        if profile is None:
            findings.append({"gate_id": "BUDGET-PROFILE-UNKNOWN", "rule": "profile-known", "severity": "block",
                             "result": "fail",
                             "message": f"profile {profile_name!r} is not in the preset set",
                             "remediation": f"pick one of: {', '.join(sorted(profiles))}",
                             "applies_to": str(record.get("capability_id") or f"record #{position}"),
                             "location": f"measurements[{position - 1}].profile"})
            continue
        if schema:
            for issue in validate_against_schema(record, record_schema(schema, "measurements_record"), schema):
                findings.append({"gate_id": "BUDGET-RECORD-SCHEMA", "rule": "measurements_record",
                                 "severity": "block", "result": "fail",
                                 "message": f"{issue['at']}: {issue['message']}",
                                 "remediation": issue.get("expected") and f"expected {issue['expected']}" or "",
                                 "applies_to": str(record.get("capability_id") or profile_name),
                                 "location": issue["at"]})
                blocked += 1
        ceilings = profile.get("metrics") or {}
        values = record.get("values") or {}
        if not isinstance(values, dict) or not values:
            findings.append({"gate_id": "BUDGET-NO-VALUES", "rule": "values-present", "severity": "block",
                             "result": "fail", "message": "the record has no measured values",
                             "remediation": "measure on the release build on the real device",
                             "applies_to": str(record.get("capability_id") or profile_name)})
            blocked += 1
            continue
        for metric, value in sorted(values.items()):
            spec = metrics.get(metric)
            ceiling = ceilings.get(metric) or {}
            number = _number(value)
            if spec is None or not ceiling:
                findings.append({"gate_id": "BUDGET-METRIC-UNDEFINED", "rule": "metric-in-profile",
                                 "severity": "warn", "result": "warn",
                                 "message": f"'{metric}' is not defined for profile {profile_name}; an "
                                            f"undefined metric is a warning to review, never a pass",
                                 "remediation": "measure it against the profile that defines it, or add it "
                                                "to the preset set",
                                 "applies_to": str(record.get("capability_id") or metric)})
                warned += 1
                rows.append({"capability_id": record.get("capability_id"), "profile": profile_name,
                             "metric": metric, "measured": value, "ceiling": None, "direction": None,
                             "headroom": None, "verdict": "review", "severity": "warn",
                             "unit": (spec or {}).get("unit", "")})
                continue
            direction = (spec or {}).get("direction", "max")
            limit = ceiling.get(direction, ceiling.get("max"))
            severity = ceiling.get("severity", (spec or {}).get("severity", "warn"))
            if number is None:
                findings.append({"gate_id": "BUDGET-NOT-NUMERIC", "rule": "numeric-measurement",
                                 "severity": "block", "result": "fail",
                                 "message": f"'{metric}' measured {value!r}; the check needs a number",
                                 "remediation": "record the numeric value, not a note",
                                 "applies_to": str(record.get("capability_id") or metric)})
                blocked += 1
                continue
            over = number > limit if direction == "max" else number < limit
            headroom = round((limit - number) if direction == "max" else (number - limit), 4)
            verdict = "over" if over else "inside"
            rows.append({"capability_id": record.get("capability_id"), "profile": profile_name,
                         "metric": metric, "measured": number, "ceiling": limit, "direction": direction,
                         "headroom": headroom, "verdict": verdict, "severity": severity,
                         "unit": (spec or {}).get("unit", "")})
            if over:
                finding = {"gate_id": "BUDGET-" + re.sub(r"[^A-Z0-9]", "-", metric.upper()),
                           "rule": f"{metric} within ceiling", "severity": severity, "result": "fail",
                           "message": f"{metric} measured {number}{(spec or {}).get('unit', '')} against a "
                                      f"{direction} ceiling of {limit} on {profile_name} "
                                      f"({'over by ' + format(abs(headroom), '.4g')})",
                           "remediation": (budgets.get("fallback_guidance") or {}).get(
                               metric, "reduce the load on this metric and re-measure on the same device"),
                           "applies_to": str(record.get("capability_id") or metric),
                           "details": {"headroom": headroom, "direction": direction}}
                findings.append(finding)
                if severity == "block":
                    blocked += 1
                else:
                    warned += 1
    blocked = len([item for item in findings if item["severity"] == "block"])
    warned = len([item for item in findings if item["severity"] != "block"])
    counts = {"records": len(measurements), "metrics_compared": len(rows),
              "inside": sum(1 for row in rows if row["verdict"] == "inside"),
              "over": sum(1 for row in rows if row["verdict"] == "over"),
              "review": sum(1 for row in rows if row["verdict"] == "review"),
              "blockers": blocked, "warnings": warned}
    verdict = "blocked" if blocked else ("review" if (warned or counts["review"]) else "within budget")
    return {"kind": "budget-report", "verdict": verdict, "profiles_available": sorted(profiles),
            "measurement_rules": budgets.get("measurement_rules", []), "rows": rows, "findings": findings,
            "summary": counts, "default_profile": budgets.get("default_profile"),
            "selection_hint": budgets.get("selection_hint", "")}


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------
def load_evidence(path: str, max_mb: float = 16, deadline=None) -> list:
    raw = kit.load_json(path, max_mb, deadline)
    if isinstance(raw, dict) and isinstance(raw.get("records"), list):
        return [dict(record) for record in raw["records"] if isinstance(record, dict)]
    if isinstance(raw, dict):
        return [dict(raw)]
    if isinstance(raw, list):
        return [dict(record) for record in raw if isinstance(record, dict)]
    raise kit.InputError("the evidence file must hold one record, an array of records, or an object "
                         "with a records array", where=path)


def evidence_check(records: list, catalog, schema: dict | None, ctx, archetypes: dict | None = None,
                   require_gates: bool = False, stamp_evidence: str = "") -> dict:
    """Validate evidence records and compare them with what the capability's gates demand."""
    findings: list = []
    verdicts: list = []
    definition = record_schema(schema, "evidence_record") if schema else None
    for position, record in enumerate(records, start=1):
        capability_id = str(record.get("capability_id") or "")
        issues: list = []
        if definition:
            issues = validate_against_schema(record, definition, schema)
        for issue in issues:
            findings.append({"gate_id": "EVIDENCE-SCHEMA", "rule": "evidence_record", "severity": "block",
                             "result": "fail", "message": f"{issue['at']}: {issue['message']}",
                             "remediation": f"fix {issue['at'].split('.')[-1]} in the record",
                             "applies_to": capability_id or f"record #{position}", "location": issue["at"]})
        capability = catalog.index.get(capability_id)
        if capability is None and capability_id:
            findings.append({"gate_id": "EVIDENCE-CAPABILITY", "rule": "capability-known", "severity": "block",
                             "result": "fail", "message": f"evidence cites {capability_id!r}, which the "
                                                           f"catalog does not hold",
                             "remediation": "use an id from 'list', or update the dataset",
                             "applies_to": capability_id})
        elif capability is not None:
            name = str(record.get("capability_name") or "")
            if name and engine.kit.tidy(name) != capability["name"]:
                findings.append({"gate_id": "EVIDENCE-NAME", "rule": "capability-name-matches",
                                 "severity": "warn", "result": "warn",
                                 "message": f"the record calls {capability_id} '{name}', the catalog calls "
                                            f"it '{capability['name']}'",
                                 "remediation": "copy the name from 'show' so the two agree",
                                 "applies_to": capability_id})
        checks = [check for check in record.get("checks") or [] if isinstance(check, dict)]
        verdict = str(record.get("verdict") or "").lower()
        results = [str(check.get("result") or "").lower() for check in checks]
        for check in checks:
            measured, threshold = check.get("measured"), check.get("pass_threshold")
            declared = str(check.get("result") or "").lower()
            if isinstance(threshold, bool) or isinstance(measured, bool):
                agreed = bool(measured) == bool(threshold)
                if declared == "pass" and not agreed:
                    findings.append({"gate_id": "EVIDENCE-CONTRADICTION", "rule": "check-agrees-with-result",
                                     "severity": "block", "result": "fail",
                                     "message": f"{check.get('id')} says pass, but measured {measured} does "
                                                f"not equal the threshold {threshold}",
                                     "remediation": "record the real result, or fix the threshold",
                                     "applies_to": check.get("id", capability_id)})
            elif _number(measured) is not None and _number(threshold) is not None and threshold not in (None, ""):
                value, ceiling = _number(measured), _number(threshold)
                within = value <= ceiling          # a numeric threshold is a ceiling
                if declared == "pass" and not within:
                    findings.append({"gate_id": "EVIDENCE-CONTRADICTION", "rule": "check-agrees-with-result",
                                     "severity": "block", "result": "fail",
                                     "message": f"{check.get('id')} says pass, but measured {value} is above "
                                                f"the threshold {ceiling}",
                                     "remediation": "record the real result or the real threshold",
                                     "applies_to": check.get("id", capability_id),
                                     "details": {"measured": value, "pass_threshold": ceiling}})
        if verdict == "pass" and "fail" in results:
            findings.append({"gate_id": "EVIDENCE-VERDICT", "rule": "verdict-matches-checks",
                             "severity": "block", "result": "fail",
                             "message": "verdict is 'pass' while a check failed",
                             "remediation": "set verdict to fail, or re-run the failing check on a new build",
                             "applies_to": capability_id})
        if verdict == "pass" and any(item in ("blocked", "not-run") for item in results):
            findings.append({"gate_id": "EVIDENCE-VERDICT", "rule": "verdict-matches-checks",
                             "severity": "block", "result": "fail",
                             "message": "verdict is 'pass' while a check is blocked or not run",
                             "remediation": "a pass needs every listed check to be recorded as pass",
                             "applies_to": capability_id})
        if verdict in ("fail", "blocked"):
            for field in ("failure_evidence", "remediation", "regression_retest"):
                value = record.get(field)
                if value in (None, "") or (isinstance(value, str) and value.strip().lower() in
                                           {"fill", "tbd", "n/a", "none", "unknown"}):
                    findings.append({"gate_id": "EVIDENCE-FAILURE-PACKET", "rule": f"{field}-required",
                                     "severity": "block", "result": "fail",
                                     "message": f"a {verdict} verdict needs {field}; the catalog's evaluate "
                                                f"archetype lists failure evidence, remediation, and "
                                                f"regression retest as one packet",
                                     "remediation": f"fill '{field}' in the record",
                                     "applies_to": capability_id})
        elif verdict == "not-run":
            findings.append({"gate_id": "EVIDENCE-NOT-RUN", "rule": "verdict-recorded", "severity": "warn",
                             "result": "warn",
                             "message": f"{capability_id or position} is recorded as not-run; nothing is "
                                        f"blocked, nothing is proven",
                             "remediation": "run the checks or mark the capability blocked with a reason",
                             "applies_to": capability_id})
        gates = []
        if archetypes and capability is not None:
            spec = (archetypes.get("archetypes") or {}).get(capability["archetype"], {})
            declared = {gate["id"]: gate for gate in spec.get("gates", [])}
            recorded = {}
            for check in checks:
                needle = str(check.get("gate") or check.get("id") or "")
                if needle in declared:
                    recorded[needle] = check
                else:
                    for gate_id in declared:
                        if gate_id in str(check.get("description", "")):
                            recorded[gate_id] = check
            for gate_id, gate in declared.items():
                result = recorded.get(gate_id, {}).get("result")
                if result is None:
                    severity = "block" if require_gates else "warn"
                    findings.append({"gate_id": "EVIDENCE-GATE-" + gate_id.upper().replace("-", "_"),
                                     "rule": "gate-recorded", "severity": severity,
                                     "result": "fail" if require_gates else "warn",
                                     "message": f"gate '{gate_id}' of {capability_id} has no recorded result",
                                     "remediation": f"add a check whose gate field is '{gate_id}' reporting "
                                                    f"'{gate['rule']}'",
                                     "applies_to": capability_id})
                elif verdict == "pass" and str(result).lower() != "pass":
                    findings.append({"gate_id": "EVIDENCE-GATE-" + gate_id.upper().replace("-", "_"),
                                     "rule": "gate-passed", "severity": "block", "result": "fail",
                                     "message": f"gate '{gate_id}' is {result}, so {capability_id} cannot be "
                                                f"marked passed",
                                     "remediation": "close the gate properly, then re-record the evidence",
                                     "applies_to": capability_id})
                gates.append({"gate_id": gate_id.upper().replace("-", "_"), "rule": gate["rule"],
                              "severity": gate.get("severity", "block"),
                              "result": str(result or "not-run"),
                              "message": "recorded result matches the gate" if result else
                              "no result recorded for this gate",
                              "applies_to": capability_id})
        verdicts.append({"capability_id": capability_id, "verdict": verdict or "unset",
                         "checks": len(checks), "findings": [f["gate_id"] for f in findings
                                                              if f.get("applies_to") == capability_id],
                         "gates": gates, "operator": record.get("operator"),
                         "run_started": record.get("run_started"),
                         "environment": record.get("environment") or {}})
    blockers = [f for f in findings if f["severity"] == "block"]
    return {"kind": "evidence-report", "verdict": "blocked" if blockers else "accepted",
            "records": len(records), "findings": findings, "verdicts": verdicts,
            "summary": {"records": len(records), "blockers": len(blockers),
                        "warnings": len([f for f in findings if f["severity"] != "block"]),
                        "clean": sum(1 for item in verdicts if not item["findings"])},
            "require_gates": require_gates}


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------
def coverage_report(catalog, archetypes: dict | None = None, expected: dict | None = None) -> dict:
    """What the dataset actually holds: per category, archetype, layer, owner, difficulty, engine."""
    findings: list = []
    categories = []
    for number in sorted(catalog.categories):
        category = catalog.categories[number]
        records = category["entries"]
        by_archetype: dict = {}
        for record in records:
            by_archetype[record["archetype"]] = by_archetype.get(record["archetype"], 0) + 1
        template_ok = sum(1 for record in records if record.get("template_ok"))
        categories.append({"id": f"c{number:02d}", "number": number, "title": category["title"],
                           "declared_count": category["declared_count"], "count": len(records),
                           "layer": category["layer"], "archetypes": by_archetype,
                           "template_ok": template_ok, "slug": category["slug"],
                           "matching_descriptions": template_ok == len(records)})
        if category["declared_count"] and category["declared_count"] != len(records):
            findings.append({"severity": "error", "code": "E-CAT-COUNT",
                             "detail": f"'{category['title']}' declares {category['declared_count']}, "
                                       f"holds {len(records)}", "location": f"c{number:02d}"})
    totals = {"categories": len(categories), "entries": len(catalog),
              "archetypes": {name: 0 for name in engine.ARCHETYPE_NAMES},
              "layers": {name: 0 for name, _low, _high in engine.LAYERS},
              "template_ok": sum(item["template_ok"] for item in categories),
              "roles": {}, "difficulties": {}, "engines": {}, "risk": {}}
    for record in catalog.records:
        totals["archetypes"][record["archetype"]] = totals["archetypes"].get(record["archetype"], 0) + 1
        totals["layers"][record["layer"]] = totals["layers"].get(record["layer"], 0) + 1
        for key, bucket in (("role", totals["roles"]), ("difficulty", totals["difficulties"]),
                            ("risk_class", totals["risk"])):
            bucket[record[key]] = bucket.get(record[key], 0) + 1
        for engine_id in record["engines"]:
            totals["engines"][engine_id] = totals["engines"].get(engine_id, 0) + 1
    if expected:
        if expected.get("entries") and expected["entries"] != totals["entries"]:
            findings.append({"severity": "error", "code": "E-COUNT-MISMATCH",
                             "detail": f"expected {expected['entries']} capabilities, found "
                                       f"{totals['entries']}", "location": "entry_count"})
        if expected.get("fingerprint") and expected["fingerprint"] != catalog.meta.get("fingerprint"):
            findings.append({"severity": "error", "code": "E-FINGERPRINT",
                             "detail": f"fingerprint {catalog.meta.get('fingerprint')} does not match the "
                                       f"expected {expected['fingerprint']}", "location": "fingerprint"})
    if archetypes and totals["template_ok"] != totals["entries"]:
        findings.append({"severity": "warning", "code": "W-DESC-TEMPLATE",
                         "detail": f"{totals['entries'] - totals['template_ok']} description(s) do not "
                                   f"reproduce from their archetype template",
                         "location": "asset-archetypes.json"})
    return {"kind": "coverage", "dataset_id": catalog.meta.get("dataset_id"),
            "fingerprint": catalog.meta.get("fingerprint"), "source_document": catalog.meta.get("source_document"),
            "edition": catalog.meta.get("edition"), "categories": categories, "totals": totals,
            "findings": findings, "complete": not any(f["severity"] == "error" for f in findings)}


# ---------------------------------------------------------------------------
# offline audit of the tool's own package
# ---------------------------------------------------------------------------
NETWORK_LIBS = ("requests", "urllib.request", "urllib2", "httpx", "aiohttp", "ftplib", "urlopen", "smtplib", "socket.create_connection")  # audit: allow
FETCH_COMMANDS = ("wget", "curl", "pip install", "npm install", "yarn add", "git clone", "apt-get install", "Invoke-WebRequest")  # audit: allow
CODE_TOKENS = re.compile(r"(?i)\b(" + "|".join(re.escape(item) for item in NETWORK_LIBS) + r")\b")
FETCH_TOKENS = re.compile(r"(?i)\b(" + "|".join(re.escape(item) for item in FETCH_COMMANDS) + r")\s")
URL_TOKENS = re.compile(r"https?://[^\s<>]+")
EXEC_TOKENS = re.compile(r"(?i)\b(subprocess|os\.system|popen|eval\(|exec\(|__import__|pty\.|shutil\.rmtree)")
FILE_PATTERN = re.compile(r"^(?:tools|knowledge|asset)-[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def offline_audit(ctx, scan_paths=(), extra_text: str = "", allow_network: bool = False) -> dict:
    """Self-check the package: no network calls, no code execution, no secrets, correct layout.

    Layout rules come from the skill package contract: flat folder, tools-/knowledge-/asset- prefixes,
    lower-case-hyphen folder name, exactly one knowledge-readme.md, no SKILL.md inside the folder.
    """
    findings: list = []
    here = ctx.package_dir
    files = sorted(os.listdir(here))
    for name in files:
        path = os.path.join(here, name)
        if os.path.isdir(path):
            if name == "__pycache__":
                continue
            findings.append({"gate_id": "AUDIT-SUBFOLDER", "rule": "flat-layout", "severity": "block",
                             "result": "fail", "message": f"{name}/ is a subfolder inside the Skill Tool",
                             "remediation": "a Skill Tool folder holds only files", "applies_to": name})
            continue
        if not FILE_PATTERN.match(name):
            findings.append({"gate_id": "AUDIT-FILENAME", "rule": "filename-discipline", "severity": "block",
                             "result": "fail",
                             "message": f"{name} is not named tools-*, knowledge-*, or asset-* in "
                                        f"lower-case-hyphen form",
                             "remediation": "rename it, or move it out of the Skill Tool folder",
                             "applies_to": name})
        if name == "SKILL.md" or name.endswith(".zip") or name.startswith(".tmp-"):
            findings.append({"gate_id": "AUDIT-STRAY-FILE", "rule": "no-stray-files", "severity": "block",
                             "result": "fail", "message": f"{name} must not sit inside the Skill Tool folder",
                             "remediation": "SKILL.md belongs beside the folder; archives and temp files "
                                            "belong elsewhere", "applies_to": name})
    knowledge = [name for name in files if name.startswith("knowledge-") and name.endswith(".md")]
    if knowledge != ["knowledge-readme.md"]:
        findings.append({"gate_id": "AUDIT-README", "rule": "exactly-one-knowledge-file",
                         "severity": "block", "result": "fail",
                         "message": f"found {knowledge or 'no knowledge-* file'}; the contract is exactly "
                                    f"knowledge-readme.md",
                         "remediation": "keep one knowledge-readme.md and document the rest in it",
                         "applies_to": ", ".join(knowledge) or "none"})
    scanned = [os.path.join(here, name) for name in files
               if name.endswith((".py", ".md", ".json")) and os.path.isfile(os.path.join(here, name))]
    scanned += [path for path in scan_paths if os.path.isfile(path)]
    secrets = 0
    for path in scanned:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            findings.append({"gate_id": "AUDIT-READ", "rule": "readable", "severity": "block", "result": "fail",
                             "message": f"cannot read {os.path.basename(path)}: {exc.strerror or exc}",
                             "remediation": "fix the file permissions", "applies_to": path})
            continue
        rel = os.path.relpath(path, os.path.dirname(here))
        suffix = os.path.splitext(path)[1]
        for hit in kit.scan_secrets(text):
            secrets += 1
            findings.append({"gate_id": "AUDIT-SECRET", "rule": "no-embedded-secrets", "severity": "block",
                             "result": "fail",
                             "message": f"{rel} line {hit['line']} looks like a {hit['pattern']}",
                             "remediation": "read credentials from the environment at run time; never store "
                                            "them in the package", "applies_to": rel,
                             "details": {"pattern": hit["pattern"], "column": hit["column"],
                                         "length": hit["length"]}})
        for lineno, line in enumerate(text.split("\n"), start=1):
            guard = ("offline_audit" in line or "CODE_TOKENS" in line or "FETCH_TOKENS" in line
                     or "URL_TOKENS" in line or "re.compile" in line or "audit: allow" in line)
            token = None if guard else (CODE_TOKENS.search(line) or FETCH_TOKENS.search(line))
            if token is None and suffix in (".py", ".md") and not guard:
                url = URL_TOKENS.search(line)
                if url and "json-schema.org" not in url.group(0) \
                        and not re.search(r"://(localhost|127\.0\.0\.1|\[?::1\]?|0\.0\.0\.0)([:/]|$)",
                                          url.group(0)):
                    token = url
            if token is not None:
                severity = "warn" if allow_network else "block"
                findings.append({"gate_id": "AUDIT-NETWORK", "rule": "no-network-access", "severity": severity,
                                 "result": "fail",
                                 "message": f"{rel}:{lineno} references {token.group(0).strip()!r}",
                                 "remediation": "this Skill Tool must run fully offline; read a local file "
                                                "instead" + (" (allowed because offline.allow_network is on)"
                                                             if allow_network else ""),
                                 "applies_to": rel})
                continue
            if os.path.basename(path).startswith("tools-") and suffix == ".py":
                exec_token = EXEC_TOKENS.search(line)
                if exec_token and "EXEC_TOKENS" not in line and "audit: allow" not in line:
                    findings.append({"gate_id": "AUDIT-EXEC", "rule": "no-code-execution", "severity": "block",
                                     "result": "fail",
                                     "message": f"{rel}:{lineno} uses {exec_token.group(0).strip()!r}, which "
                                                f"could run an arbitrary command",
                                     "remediation": "delete the call; this tool only writes files and prints "
                                                    "reports", "applies_to": rel})
                    break
    for path in scan_paths:
        if not os.path.exists(path):
            findings.append({"gate_id": "AUDIT-MISSING", "rule": "extra-paths-exist", "severity": "warn",
                             "result": "warn", "message": f"{path} was listed but does not exist",
                             "remediation": "check the --scan path", "applies_to": path})
    blocks = [f for f in findings if f["severity"] == "block"]
    return {"kind": "offline-audit", "package": os.path.basename(here), "files": len(files),
            "verdict": "blocked" if blocks else "clean", "findings": findings,
            "secret_hits": secrets, "scanned": [os.path.relpath(p, os.path.dirname(here)) for p in scanned],
            "allow_network": allow_network, "note": "checks layout, references, and text; runs nothing"}


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def doctor_checks(ctx) -> list:
    """Twelve checks, each with the reason it passed or failed. Read-only."""
    checks: list = []
    here = ctx.package_dir
    entries = sorted(os.listdir(here)) if os.path.isdir(here) else []
    files = [name for name in entries if os.path.isfile(os.path.join(here, name))]

    def check(check_id: str, ok: bool, detail: str, remediation: str = "") -> None:
        checks.append({"id": check_id, "status": "pass" if ok else "fail", "detail": detail,
                       "remediation": remediation})

    version = sys.version_info
    check("python-version", version >= kit.MIN_PYTHON,
          f"python {version.major}.{version.minor}.{version.micro} "
          f"(needs {'.'.join(map(str, kit.MIN_PYTHON))}+)",
          "run the tool with a newer python3")
    tools = [name for name in files if name.startswith("tools-") and name.endswith(".py")]
    required = ["tools-offline-game-development.py", "tools-runtime-kit.py", "tools-catalog-engine.py",
                "tools-artifact-generators.py", "tools-gates.py", "tools-selftest.py"]
    absent = sorted(set(required) - set(files))
    loaded = sorted(name for name, module in ctx.loaded.items() if module is not None)
    absent_text = "all six present" if not absent else "missing " + ", ".join(absent)
    check("sibling-modules", not absent,
          f"{len(tools)} tool modules in the folder, {len(loaded)} loaded in this run; {absent_text}",
          f"re-extract the package so every tools-*.py sits in {kit.SKILL_TOOL_NAME}/")
    subfolders = [name for name in entries
                  if os.path.isdir(os.path.join(here, name)) and name != "__pycache__"]
    bad_names = [name for name in files if not FILE_PATTERN.match(name)] + subfolders
    layout = f"{len(files)} files with the correct prefixes and {len(subfolders)} subfolder(s)"
    if bad_names:
        layout += "; offenders: " + ", ".join(bad_names)
    check("file-discipline", not bad_names, layout,
          "rename or move out: " + ", ".join(bad_names) if bad_names else "")
    asset_files = [name for name in files if name.startswith("asset-")]
    expected_assets = [ctx.cfg.get(key) for key in ("dataset", "archetypes", "license_policy", "budget_presets",
                                                    "output_schema", "spec_template", "evidence_fixture")
                       if ctx.cfg.get(key)]
    missing = [name for name in expected_assets if name not in files]
    check("assets-present", not missing and "knowledge-readme.md" in files,
          f"{len(asset_files)} asset files; {len(expected_assets)} required by config; readme "
          f"{'found' if 'knowledge-readme.md' in files else 'MISSING'}"
          + (f"; missing {missing}" if missing else ""),
          f"restore the missing files into {kit.SKILL_TOOL_NAME}/")
    try:
        arch = ctx.archetypes_raw() or {}
        dataset, findings = engine.load_catalog(ctx.paths["dataset"], arch, ctx.max_input_mb, ctx.deadline)
        catalog = engine.Catalog(dataset, arch)
        parsed_ok = bool(dataset["records"])
        detail = (f"{dataset['entry_count']} capabilities in {dataset['category_count']} categories, "
                  f"fingerprint {dataset.get('fingerprint')}, {len(findings)} finding(s)")
    except (kit.ToolError, OSError) as exc:
        parsed_ok, detail, catalog, dataset = False, f"{type(exc).__name__}: {exc}", None, None
    check("dataset-parses", parsed_ok, detail, "run 'validate' for the finding list")
    if catalog is not None:
        mismatches = 0
        for record in catalog.records:
            if engine.describe(record["archetype"], record["subject"]) != record["description"]:
                mismatches += 1
        check("catalog-round-trip", mismatches == 0,
              f"{len(catalog.records)} descriptions rebuilt from the archetype templates, "
              f"{mismatches} mismatch(es)",
              "the templates and the dataset disagree; re-import the source or fix the asset")
    else:  # pragma: no cover - only when the dataset is broken
        check("catalog-round-trip", False, "skipped: the dataset did not load")
    try:
        schema = load_schema(ctx.paths["output_schema"], 8, ctx.deadline)
        drift = check_schema_support(schema)
        check("schema-consistency", not drift,
              f"{len(schema.get('$defs', {}))} record definitions, {len(schema.get('validator_support', []))} "
              f"supported keywords, no drift" if not drift else drift[0]["detail"],
              "align validator_support with the keywords used, or implement them in tools-gates.py")
    except (kit.ToolError, OSError) as exc:
        check("schema-consistency", False, f"{type(exc).__name__}: {exc}", "restore asset-output-schema.json")
    layers = ", ".join(ctx.config_layers) if isinstance(ctx.config_layers, list) and ctx.config_layers \
        else "built-in defaults only"
    check("config-layers", bool(ctx.cfg), "layers merged: " + layers,
          "the config file named by --config could not be read" if not ctx.cfg else "")
    required_actions = ctx.cfg.get("approvals", {}).get("require_for") or []
    enforced = {"overwrite-file", "replace-dataset", "rights-waiver", "delete-file"}
    check("approval-policy", bool(required_actions),
          f"{len(required_actions)} actions need approval ({', '.join(sorted(enforced))} enforced by the "
          f"writers); a run without a terminal never guesses a token",
          "set approvals.require_for to the actions your team must approve")
    writable = True
    probe = os.path.join(ctx.out_dir, kit.TEMP_PREFIX + "doctor-probe")
    try:
        if ctx.out_dir and not os.path.isdir(ctx.out_dir):
            os.makedirs(ctx.out_dir, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
    except OSError as exc:
        writable = False
        detail = f"cannot write in {ctx.out_dir}: {exc.strerror or exc}"
    else:
        detail = f"{ctx.out_dir} accepts writes through a temp file plus rename"
    finally:
        try:
            if os.path.exists(probe):
                os.unlink(probe)
        except OSError:  # pragma: no cover
            pass
    check("write-target", writable, detail, "choose --out under a writable directory")
    network_ok = not ctx.cfg.get("offline", {}).get("allow_network", False)
    provider = ctx.cfg.get("provider", {}) or {}
    provider_kind = str(provider.get("kind") or "local").lower()
    provider_ok = provider_kind in ("local", "none") and not provider.get("allow_network", False)
    redaction = str(bool(ctx.cfg.get("output", {}).get("redact_secrets"))).lower()
    posture = (f"offline.allow_network="
               f"{str(bool((ctx.cfg.get('offline', {}) or {}).get('allow_network', False))).lower()}, "
               f"provider.kind={provider_kind!r}, "
               f"redact_secrets={redaction}; no engine, device, or store command is executed by this tool")
    check("offline-posture", network_ok and provider_ok, posture,
          "set offline.allow_network and provider.allow_network to false and provider.kind to local")
    readme_path = os.path.join(here, "knowledge-readme.md")
    try:
        readme = open(readme_path, encoding="utf-8").read() if os.path.isfile(readme_path) else ""
    except OSError:
        readme = ""
    commands = ctx.commands or []
    undocumented = [name for name in commands if name not in readme]
    pattern = r"`(?:python3 )?(?:\./)?" + re.escape(kit.SKILL_TOOL_NAME) + \
              r"/tools-offline-game-development\.py ([a-z-]+)`"
    stale = sorted({name for name in re.findall(pattern, readme) if commands and name not in commands})
    detail = (f"{len(readme.splitlines())} lines; {len(commands) - len(undocumented)}/{len(commands)} "
              f"commands documented")
    if undocumented:
        detail += "; undocumented: " + ", ".join(undocumented)
    if stale:
        detail += "; unknown commands referenced: " + ", ".join(stale)
    check("readme-coverage", bool(readme) and not undocumented and not stale, detail,
          "document every command in knowledge-readme.md")
    return checks


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------
def _findings_lines(findings: list, limit: int = 50) -> list:
    lines = []
    for finding in findings[:limit]:
        marker = "x" if finding.get("severity") == "block" else "!"
        lines.append(f"- [{marker}] {finding.get('gate_id', finding.get('code', ''))} "
                     f"({finding.get('applies_to') or finding.get('location') or '-'}): "
                     f"{finding.get('message') or finding.get('detail')}")
        if finding.get("remediation"):
            lines.append(f"    -> {finding['remediation']}")
    if len(findings) > limit:
        lines.append(f"- ... {len(findings) - limit} more finding(s) in the JSON output")
    return lines


def render_rights_md(report: dict) -> str:
    lines = [f"# Rights gate - {report['verdict']}", "",
             f"{report['summary']['records']} records · cleared {report['summary']['cleared']} · "
             f"review {report['summary']['review']} · blocked {report['summary']['blocked']} · "
             f"policy `{report['policy_id']}` · review window {report['review_window_days']} days", "",
             "Commercial use declared: " + ("yes" if report["commercial_use"] else "no") +
             f"; bundling assumed: {'yes' if report['bundle'] else 'no'}", ""]
    lines += ["| asset | license | kind | verdict | blockers |", "|---|---|---|---|---|"]
    for verdict in report["verdicts"]:
        lines.append(f"| {verdict['asset']} | {verdict['license']} | {verdict['kind']} | "
                     f"{verdict['verdict']} | {verdict['blockers']} |")
    if report["findings"]:
        lines += ["", "## Findings", ""]
        lines += _findings_lines(report["findings"])
    if not report["waiver_allowed"]:
        lines += ["", "Waivers are not automatic: the policy says only a named owner may decide a finding "
                  "does not apply, and that decision belongs in the asset's own record file "
                  "(`--waive --approve WAIVE-RIGHTS` records that the human decision was made)."]
    return "\n".join(lines) + "\n"


def render_budget_md(report: dict) -> str:
    lines = [f"# Budget gate - {report['verdict']}", "",
             f"{report['summary']['metrics_compared']} metrics compared across "
             f"{report['summary']['records']} record(s): {report['summary']['inside']} inside, "
             f"{report['summary']['over']} over, {report['summary']['review']} to review", "",
             "Headroom is ceiling minus measurement, so positive means inside the budget.", ""]
    lines += ["| capability | profile | metric | measured | ceiling | headroom | verdict |",
              "|---|---|---|---|---|---|---|"]
    for row in report["rows"]:
        lines.append(f"| {row['capability_id']} | {row['profile']} | {row['metric']} | "
                     f"{row['measured']}{row['unit']} | "
                     f"{row['ceiling'] if row['ceiling'] is not None else 'not in profile'}{row['unit'] if row['ceiling'] is not None else ''} | "
                     f"{row['headroom'] if row['headroom'] is not None else '-'} | {row['verdict']} |")
    if report["findings"]:
        lines += ["", "## Findings", ""] + _findings_lines(report["findings"])
    lines += ["", "## Measurement rules", ""] + [f"- {rule}" for rule in report["measurement_rules"]]
    lines += ["", f"Profile hint: {report['selection_hint']}", ""]
    return "\n".join(lines) + "\n"


def render_evidence_md(report: dict) -> str:
    lines = [f"# Evidence gate - {report['verdict']}", "",
             f"{report['summary']['records']} record(s) · {report['summary']['clean']} with no findings · "
             f"{report['summary']['blockers']} blocker(s) · {report['summary']['warnings']} warning(s) · "
             f"gate coverage enforced: {'yes' if report['require_gates'] else 'no'}", ""]
    for verdict in report["verdicts"]:
        lines.append(f"## {verdict['capability_id'] or 'record'} - {verdict['verdict']}")
        lines.append(f"operator `{verdict['operator'] or 'unset'}`, run_started "
                     f"`{verdict['run_started'] or 'unset'}`, {verdict['checks']} check(s)")
        environment = verdict["environment"] or {}
        if environment:
            lines.append("environment: " + ", ".join(f"{key} `{value}`" for key, value in environment.items()))
        if verdict["gates"]:
            lines += ["", "gates:"]
            for gate in verdict["gates"]:
                lines.append(f"- [{ 'x' if gate['result'] == 'fail' else ' ' }] {gate['gate_id'].lower()}: "
                             f"{gate['result']} - {gate['message']}")
        if verdict["findings"]:
            lines.append("findings: " + ", ".join(verdict["findings"]))
        lines.append("")
    if report["findings"]:
        lines += ["## Findings", ""] + _findings_lines(report["findings"])
    return "\n".join(lines).rstrip() + "\n"


def render_coverage_md(report: dict) -> str:
    totals = report["totals"]
    lines = [f"# Catalog coverage - {report['dataset_id']}", "",
             f"{totals['entries']} capabilities in {totals['categories']} categories · fingerprint "
             f"`{report['fingerprint']}` · edition {report.get('edition') or '-'}",
             f"descriptions that rebuild from their archetype template: {totals['template_ok']}/{totals['entries']}",
             ""]
    lines += ["| cat | title | declared | held | archetypes | layer |", "|---|---|---|---|---|---|"]
    for category in report["categories"]:
        archetypes = " ".join(f"{key}={value}" for key, value in sorted(category["archetypes"].items()))
        lines.append(f"| {category['number']} | {category['title']} | {category['declared_count']} | "
                     f"{category['count']} | {archetypes} | {category['layer']} |")
    lines += ["", "## Totals", "",
              "- archetypes: " + ", ".join(f"{key}={value}" for key, value in sorted(totals["archetypes"].items())),
              "- layers: " + ", ".join(f"{key}={value}" for key, value in sorted(totals["layers"].items())),
              "- difficulty: " + ", ".join(f"{key}={value}" for key, value in sorted(totals["difficulties"].items())),
              "- engine scope: " + ", ".join(f"{key}={value}" for key, value in sorted(totals["engines"].items())),
              "- risk: " + ", ".join(f"{key}={value}" for key, value in sorted(totals["risk"].items())), ""]
    if report["findings"]:
        lines += ["## Findings", ""] + _findings_lines(report["findings"])
    return "\n".join(lines) + "\n"


def render_audit_md(report: dict) -> str:
    lines = [f"# Offline audit - {report['verdict']}", "",
             f"package `{report['package']}` · {report['files']} files · {len(report['scanned'])} files "
             f"scanned · {report['secret_hits']} secret-shaped string(s) · network permitted: "
             f"{'yes' if report['allow_network'] else 'no'}", "", report["note"], ""]
    if report["findings"]:
        lines += ["## Findings", ""] + _findings_lines(report["findings"])
    else:
        lines += ["No findings: flat layout, prefixes correct, no network or exec tokens, no "
                  "credential-shaped strings.", ""]
    return "\n".join(lines) + "\n"


def render_doctor_md(checks: list) -> str:
    passed = sum(1 for check in checks if check["status"] == "pass")
    lines = [f"# Doctor - {passed}/{len(checks)} checks pass", "",
             f"skill tool folder: `{kit.SKILL_TOOL_NAME}/`", ""]
    for check in checks:
        mark = "PASS" if check["status"] == "pass" else "FAIL"
        lines.append(f"- [{mark}] {check['id']}: {check['detail']}")
        if check["status"] == "fail" and check.get("remediation"):
            lines.append(f"    -> {check['remediation']}")
    return "\n".join(lines) + "\n"


BUDGET_COLUMNS = ["capability_id", "profile", "metric", "measured", "ceiling", "direction", "headroom",
                  "verdict", "severity", "unit"]
RIGHTS_COLUMNS = ["asset", "kind", "license", "license_known", "commercial_use", "redistribution",
                  "bundled", "verdict", "blockers", "warnings"]


def render_rights_csv(report: dict) -> str:
    return kit.csv_render(RIGHTS_COLUMNS, [{key: verdict.get(key) for key in RIGHTS_COLUMNS}
                                           for verdict in report["verdicts"]])


def render_budget_csv(report: dict) -> str:
    return kit.csv_render(BUDGET_COLUMNS, [{key: row.get(key) for key in BUDGET_COLUMNS}
                                           for row in report["rows"]])


__all__ = [name for name in dir() if not name.startswith("_")]
