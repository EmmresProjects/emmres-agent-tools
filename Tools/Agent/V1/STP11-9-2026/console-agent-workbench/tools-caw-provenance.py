#!/usr/bin/env python3
"""caw_provenance - provenance records, hash-chained ledger, plans, retention.

Implements the catalog's foundation skills:

* Preservation Provenance Recorder (record + validate + tamper-evident ledger)
* Reversible Experiment Planner and Console Task Decomposer
* Evidence Retention Controller (evaluate, quarantine, hard delete, restore)

The schema validator is a deliberately small subset of JSON Schema
(types, required, properties, additionalProperties, enum, pattern,
format: date-time, items, minLength, minimum, maximum). The limitation is
documented in knowledge-readme.md.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil

import caw_runtime as rt

SCHEMA_FILENAME = "asset-provenance-schema.json"
PLAYBOOK_FILENAME = "asset-plan-playbook.json"

DECOMPOSITION_STAGES = (
    "acquire", "preserve", "identify", "inspect", "reproduce",
    "test", "compare", "package", "handoff",
)


# ---------------------------------------------------------------------------
# Minimal JSON Schema subset validator
# ---------------------------------------------------------------------------
_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, expected):
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _TYPE_MAP.get(expected)
    if py is None:
        return True
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, py) and not (expected != "boolean" and isinstance(value, bool))


def validate(instance, schema, path="#"):
    """Validate *instance* against the supported schema subset.

    Returns a list of ``{path, message}`` problems (empty list means valid).
    """
    import re

    problems = []

    def add(p, message):
        problems.append({"path": p, "message": message})

    def walk(value, node, cur):
        if not isinstance(node, dict):
            return
        expected = node.get("type")
        if expected is not None:
            options = expected if isinstance(expected, list) else [expected]
            if not any(_type_ok(value, o) for o in options):
                add(cur, f"expected type {'/'.join(options)}, got {type(value).__name__}")
                return
        if "enum" in node and value not in node["enum"]:
            add(cur, f"value {value!r} is not one of {node['enum']}")
        if isinstance(value, str):
            if "minLength" in node and len(value) < node["minLength"]:
                add(cur, f"shorter than minLength {node['minLength']}")
            if "maxLength" in node and len(value) > node["maxLength"]:
                add(cur, f"longer than maxLength {node['maxLength']}")
            if "pattern" in node and not re.search(node["pattern"], value):
                add(cur, f"does not match pattern {node['pattern']}")
            if node.get("format") == "date-time":
                try:
                    rt.parse_iso(value)
                except rt.ValidationError:
                    add(cur, "not an ISO-8601 date-time (expected e.g. 2026-01-31T09:00:00Z)")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in node and value < node["minimum"]:
                add(cur, f"below minimum {node['minimum']}")
            if "maximum" in node and value > node["maximum"]:
                add(cur, f"above maximum {node['maximum']}")
        if isinstance(value, dict):
            for key in node.get("required", []):
                if key not in value:
                    add(cur, f"missing required property {key!r}")
            props = node.get("properties", {})
            for key, sub in props.items():
                if key in value:
                    walk(value[key], sub, f"{cur}/{key}")
            extra = node.get("additionalProperties", True)
            if extra is False:
                for key in value:
                    if key not in props:
                        add(cur, f"unexpected property {key!r}")
            elif isinstance(extra, dict):
                for key, val in value.items():
                    if key not in props:
                        walk(val, extra, f"{cur}/{key}")
        if isinstance(value, list):
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                for i, item in enumerate(value):
                    walk(item, item_schema, f"{cur}/{i}")
            if "maxItems" in node and len(value) > node["maxItems"]:
                add(cur, f"more than maxItems {node['maxItems']}")
            if "minItems" in node and len(value) < node["minItems"]:
                add(cur, f"fewer than minItems {node['minItems']}")

    walk(instance, schema, path)
    return problems


def load_schema(path=None):
    return rt.load_json(path or rt.resource_default(SCHEMA_FILENAME), "provenance schema")


# ---------------------------------------------------------------------------
# Provenance records
# ---------------------------------------------------------------------------
def build_record(operator, material_path, media_type="unknown", region="unknown",
                 revision="unknown", ownership="owned", method="", reader="", hardware="",
                 firmware="", software_versions=None, reads=1, source="", notes="",
                 redistribution="none", attribution="", dat_match=None, hash_cap=None,
                 material_inspect=None):
    p = rt.require_existing(material_path, "material file", kind="file")
    size = os.path.getsize(p)
    info = rt.hash_file(p, ("crc32", "md5", "sha1", "sha256"))
    record = {
        "schema_version": "1.0",
        "record_type": "provenance",
        "record_id": rt.new_id("prov"),
        "operator": operator,
        "created_at": rt.iso(),
        "material": {
            "reference": os.path.basename(p),
            "media_type": media_type,
            "region": region,
            "revision": revision,
            "ownership": ownership,
        },
        "dump": {
            "method": method or "unspecified",
            "reader": reader or "unspecified",
            "hardware": hardware or "unspecified",
            "firmware": firmware or "unspecified",
            "software_versions": software_versions or {},
            "reads": max(1, int(reads)),
        },
        "integrity": {
            "size": size,
            "crc32": info["hashes"]["crc32"],
            "md5": info["hashes"]["md5"],
            "sha1": info["hashes"]["sha1"],
            "sha256": info["hashes"]["sha256"],
        },
        "provenance": {
            "source": source or "operator-supplied",
            "acquisition_date": rt.iso().split("T")[0],
            "notes": notes,
        },
        "rights": {
            "redistribution": redistribution,
            "attribution": attribution,
        },
    }
    if dat_match:
        record["integrity"]["dat_match"] = dat_match
    if material_inspect:
        record["material"]["detected_format"] = material_inspect.get("detected_format")
        record["material"]["platform"] = material_inspect.get("platform")
    return record


# ---------------------------------------------------------------------------
# Hash-chained ledger (append-only evidence trail)
# ---------------------------------------------------------------------------
GENESIS_PREV = "0" * 64


def ledger_read(path):
    p = rt.require_existing(path, "ledger", kind="file")
    entries = []
    with open(p, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                import json
                entries.append(json.loads(line))
            except ValueError:
                raise rt.ValidationError(f"ledger line {lineno} is not valid JSON")
    return entries


def ledger_append(path, record, operator):
    p = rt.resolve_input(path)
    existing = []
    if os.path.exists(p):
        existing = ledger_read(p)
    prev = existing[-1]["entry_sha256"] if existing else GENESIS_PREV
    entry = {
        "seq": len(existing) + 1,
        "appended_at": rt.iso(),
        "appended_by": operator,
        "prev_sha256": prev,
        "record": record,
    }
    entry["entry_sha256"] = rt.sha256_obj(entry)
    rt.ensure_dir(os.path.dirname(p))
    with open(p, "a", encoding="utf-8") as fh:
        import json
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return {"ledger": p, "seq": entry["seq"], "entry_sha256": entry["entry_sha256"],
            "prev_sha256": prev, "entries": entry["seq"]}


def ledger_verify(path):
    entries = ledger_read(path)
    problems = []
    prev = GENESIS_PREV
    for i, entry in enumerate(entries, start=1):
        stored = entry.get("entry_sha256")
        body = {k: v for k, v in entry.items() if k != "entry_sha256"}
        if entry.get("seq") != i:
            problems.append({"seq": i, "problem": f"expected seq {i}, found {entry.get('seq')}"})
        if entry.get("prev_sha256") != prev:
            problems.append({"seq": i, "problem": "prev_sha256 does not chain to the previous entry"})
        if rt.sha256_obj(body) != stored:
            problems.append({"seq": i, "problem": "entry_sha256 mismatch: entry content changed"})
        prev = stored
    return {
        "ledger": rt.resolve_input(path),
        "entries": len(entries),
        "valid": not problems,
        "problems": problems,
        "records": [{"seq": e.get("seq"), "appended_at": e.get("appended_at"),
                     "record_type": (e.get("record") or {}).get("record_type"),
                     "record_id": (e.get("record") or {}).get("record_id"),
                     "sha256": ((e.get("record") or {}).get("integrity") or {}).get("sha256")}
                    for e in entries],
    }


# ---------------------------------------------------------------------------
# Reversible experiment plan + task decomposition
# ---------------------------------------------------------------------------
def load_playbook(path=None):
    return rt.load_json(path or rt.resource_default(PLAYBOOK_FILENAME), "plan playbook")


def build_plan(goal, playbook, platform=None, operator=None, risk="medium", stages=None):
    if not goal or not goal.strip():
        raise rt.ValidationError("--goal is required")
    if risk not in ("low", "medium", "high"):
        raise rt.ValidationError("risk must be low, medium, or high")
    platform_key = (platform or "").strip().lower()
    addendum = (playbook.get("platform_addenda") or {}).get(platform_key, {})
    wanted = stages or [s["id"] for s in playbook["stages"]]
    unknown = [s for s in wanted if s not in [x["id"] for x in playbook["stages"]]]
    if unknown:
        raise rt.ValidationError("unknown plan stage(s): " + ", ".join(unknown),
                                 ["valid stages: " + ", ".join(s["id"] for s in playbook["stages"])])
    steps = []
    for stage in playbook["stages"]:
        if stage["id"] not in wanted:
            continue
        checklist = list(stage["checklist"])
        checklist.extend(addendum.get("extra_" + stage["id"], []))
        steps.append({
            "stage": stage["id"],
            "objective": stage["objective"],
            "checklist": checklist,
            "tool_hint": addendum.get("tool_hint") or stage.get("tool_hint", ""),
            "approval_required": bool(stage.get("approval_required")) or
            stage["id"] in (addendum.get("approval_stages") or []),
            "exit_criteria": stage.get("exit_criteria", ""),
        })
    return {
        "plan_id": rt.new_id("plan"),
        "goal": goal.strip(),
        "platform": platform_key or None,
        "operator": operator or "unspecified",
        "risk": risk,
        "created_at": rt.iso(),
        "stages": steps,
        "baseline": playbook["baseline"],
        "stop_conditions": playbook["stop_conditions"],
        "rollback": playbook["rollback"],
        "approvals": playbook["approvals"],
        "platform_notes": addendum.get("notes", ""),
        "decomposition": DECOMPOSITION_STAGES,
        "policy_reference": "knowledge-readme.md#safety-and-approval-rules",
    }


# ---------------------------------------------------------------------------
# Evidence retention
# ---------------------------------------------------------------------------
def _classify(name, retention):
    ext = os.path.splitext(name.lower())[1]
    for cls in retention.get("classes", []):
        exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in cls.get("extensions", [])}
        if ext in exts:
            return cls
    return {"id": retention.get("default_class", "default"),
            "days": retention.get("default_days", 365)}


def retention_evaluate(root, policy, now=None, max_files=20000):
    retention = policy["retention"]
    now = now or rt.utc_now()
    base = rt.require_existing(root, "evidence root", kind="dir")
    files = []
    for abs_path, rel, _depth in rt.walk_bounded(base, max_files=max_files):
        try:
            st = os.stat(abs_path)
        except OSError:
            continue
        if not os.path.isfile(abs_path):
            continue
        rel_norm = rel.replace(os.sep, "/")
        if rel_norm.startswith(retention.get("quarantine_dirname", "caw-quarantine")):
            continue
        cls = _classify(rel_norm, retention)
        modified = _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc)
        age = (now - modified).days
        files.append({
            "path": rel_norm,
            "size": st.st_size,
            "class": cls["id"],
            "retention_days": int(cls["days"]),
            "age_days": age,
            "modified_at": rt.iso(modified),
            "status": "due" if age >= int(cls["days"]) else "keep",
        })
    due = [f for f in files if f["status"] == "due"]
    return {
        "root": base,
        "evaluated_at": rt.iso(now),
        "file_count": len(files),
        "due_count": len(due),
        "due_bytes": sum(f["size"] for f in due),
        "classes": sorted({f["class"] for f in files}),
        "files": sorted(files, key=lambda f: f["path"]),
    }


def _scope_entries(entries):
    return [{"path": e["path"], "size": e["size"]} for e in entries]


def retention_apply(root, evaluation, policy, quarantine_subdir=None, hard_delete=False):
    """Move (or delete) retention-due evidence.

    Moving to quarantine is the default so the action stays reversible.
    """
    base = rt.require_existing(root, "evidence root", kind="dir")
    quarantine_name = quarantine_subdir or policy["retention"].get("quarantine_dirname",
                                                                  "caw-quarantine")
    quarantine = os.path.join(base, quarantine_name)
    due = [f for f in evaluation["files"] if f["status"] == "due"]
    moved = []
    manifest_path = os.path.join(quarantine, ".caw-quarantine-manifest.json")
    manifest = {"created_at": rt.iso(), "root": base, "mode": "hard-delete" if hard_delete else "quarantine",
                "entries": []}
    if os.path.exists(manifest_path):
        try:
            prev_manifest = rt.load_json(manifest_path, "quarantine manifest")
            manifest["entries"] = prev_manifest.get("entries", [])
        except rt.ValidationError:
            pass
    for entry in due:
        src = os.path.join(base, entry["path"].replace("/", os.sep))
        if not os.path.isfile(src):
            continue
        if hard_delete:
            os.remove(src)
            moved.append({"path": entry["path"], "action": "deleted", "size": entry["size"]})
            continue
        dst = os.path.join(quarantine, entry["path"].replace("/", os.sep))
        rt.ensure_dir(os.path.dirname(dst))
        shutil.move(src, dst)
        manifest["entries"].append({"path": entry["path"], "size": entry["size"],
                                    "quarantined_at": rt.iso(),
                                    "sha256": rt.hash_file(dst, ("sha256",))["hashes"]["sha256"]})
        moved.append({"path": entry["path"], "action": "quarantined", "size": entry["size"]})
    if not hard_delete:
        rt.write_json(manifest_path, manifest)
    return {
        "root": base,
        "quarantine": None if hard_delete else quarantine,
        "mode": "hard-delete" if hard_delete else "quarantine",
        "moved_count": len(moved),
        "moved_bytes": sum(m["size"] for m in moved),
        "moved": moved,
    }


def retention_restore(quarantine_dir):
    q = rt.require_existing(quarantine_dir, "quarantine directory", kind="dir")
    manifest_path = os.path.join(q, ".caw-quarantine-manifest.json")
    manifest = rt.load_json(manifest_path, "quarantine manifest")
    root = manifest["root"]
    restored = []
    problems = []
    for entry in manifest.get("entries", []):
        src = os.path.join(q, entry["path"].replace("/", os.sep))
        dst = os.path.join(root, entry["path"].replace("/", os.sep))
        if not os.path.isfile(src):
            problems.append({"path": entry["path"], "problem": "not present in quarantine"})
            continue
        if os.path.exists(dst):
            problems.append({"path": entry["path"], "problem": "destination already exists"})
            continue
        rt.ensure_dir(os.path.dirname(dst))
        shutil.move(src, dst)
        digest = rt.hash_file(dst, ("sha256",))["hashes"]["sha256"]
        if entry.get("sha256") and digest != entry["sha256"]:
            problems.append({"path": entry["path"], "problem": "restored content hash differs"})
        restored.append({"path": entry["path"], "sha256": digest})
    remaining = manifest.get("entries", [])
    manifest["entries"] = [e for e in remaining
                           if not any(r["path"] == e["path"] for r in restored)]
    rt.write_json(manifest_path, manifest)
    return {"quarantine": q, "root": root, "restored_count": len(restored),
            "restored": restored, "problems": problems}
