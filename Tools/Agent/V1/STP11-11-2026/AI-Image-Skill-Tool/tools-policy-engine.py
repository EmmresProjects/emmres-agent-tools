#!/usr/bin/env python3
"""Policy screens, governance records and reproducibility helpers for ai-image-toolkit.

Operations: policy.run, policy.rewrite_style, policy.manifest_check, policy.disclosure,
policy.release_gate, policy.workflow, repro.seed_record, repro.asset_name, repro.handoff.

The policy screens are keyword and pattern triage (see asset-policy-rules.json). They are not a
trained safety classifier, not legal advice, and do not replace human review. Every block or
review decision must be checked by a person before action.
"""
import datetime as _dt
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_common():
    name = "aiit_common"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _HERE / "tools-common.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


C = _load_common()
ToolError, P, op, write_json, slugify, sha256_file = C.ToolError, C.P, C.op, C.write_json, C.slugify, C.sha256_file
RULES = C.load_asset("asset-policy-rules.json")
RULESETS = sorted(RULES["rulesets"].keys())
WF = RULES["workflow"]
STATES = WF["states"]
ACTIONS = sorted(WF["transitions"].keys())
CHECKLIST = list(RULES["release_checklist"].keys())
SEVERITY_RANK = {"block": 3, "review": 2, "warn": 1}
ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2", ".xz"}


# ----------------------------------------------------------------- policy.run

def normalise(text):
    t = ZERO_WIDTH.sub("", text).lower()
    collapsed = t.translate(LEET)
    collapsed = re.sub(r"(?<=[a-z])[._*\-](?=[a-z])", "", collapsed)
    return re.sub(r"\s+", " ", collapsed)


def _snippet(text, m):
    s, e = max(0, m.start() - 20), min(len(text), m.end() + 20)
    return text[s:e]


def op_policy_run(p, ctx):
    if p.get("text"):
        raw = p["text"]
    elif p.get("text_file"):
        raw = C.read_text_file(p["text_file"], "text_file")
    else:
        raise ToolError("invalid_input", "provide 'text' or 'text_file'", field="text")
    if len(raw) > ctx.limits["max_text_chars"]:
        raise ToolError("invalid_input", "text exceeds max_text_chars", field="text")
    rs = RULES["rulesets"][p["ruleset"]]
    norm = normalise(raw)
    findings = []
    if ZERO_WIDTH.search(raw):
        findings.append({"rule_id": "obf-zero-width", "severity": "review",
                         "message": "zero-width characters present (possible hidden text)",
                         "matched": None, "alternative": "Retype the request in plain text."})
    plain = " ".join(ZERO_WIDTH.sub("", raw).lower().split())
    if rs.get("obfuscation_check") and norm != plain:
        findings.append({"rule_id": "obf-normalised-differs", "severity": "review",
                         "message": "text changes after normalising separators or leetspeak; possible obfuscation",
                         "matched": None, "alternative": "Restate the request directly."})
    for rule in rs["rules"]:
        if "any" in rule:
            hits = [m for m in (re.search(pat, norm) for pat in rule["any"]) if m]
            if hits:
                findings.append({"rule_id": rule["id"], "severity": rule["severity"], "message": rule["message"],
                                 "matched": _snippet(norm, hits[0]), "alternative": rule.get("alternative")})
        elif "all" in rule:
            hits = [re.search(pat, norm) for pat in rule["all"]]
            if all(hits):
                findings.append({"rule_id": rule["id"], "severity": rule["severity"], "message": rule["message"],
                                 "matched": _snippet(norm, hits[0]), "alternative": rule.get("alternative")})
    severities = {f["severity"] for f in findings}
    if "block" in severities:
        decision = "block"
    elif "review" in severities:
        decision = "review"
    elif "warn" in severities:
        decision = "allow_with_notes"
    else:
        decision = "allow"
    result = {"ruleset": p["ruleset"], "decision": decision, "requires_human_review": decision in ("block", "review"),
              "findings": findings, "context": p.get("context"),
              "normalised_length": len(norm),
              "note": "Keyword and pattern triage only. Decisions must be confirmed by a person; a clean result does not prove safety."}
    result["alternatives"] = [f["alternative"] for f in findings if f.get("alternative")]
    path = ctx.out_path(p.get("output"), "policy-decision.json")
    write_json(path, result)
    ctx.record(path)
    result["decision_file"] = str(path)
    return result


# ----------------------------------------------------------------- policy.rewrite_style

def op_rewrite_style(p, ctx):
    text = p["text"]
    styles = RULES["artist_style_map"]
    out = text
    changes, unresolved = [], []
    for name, attrs in styles.items():
        if name.startswith("_"):
            continue
        pat = re.compile(r"\b(in the style of|style of|like|by)\s+" + re.escape(name) + r"\b", re.I)
        if pat.search(out):
            out = pat.sub(lambda m: attrs, out)
            changes.append({"from": name, "to": attrs})
    for m in re.finditer(r"\b(in the style of|style of)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)", out, re.I):
        unresolved.append(m.group(2))
    if unresolved:
        ctx.warn("unresolved named style(s); describe visual attributes instead: " + ", ".join(sorted(set(unresolved))))
    return {"rewritten_text": out, "changes": changes, "unresolved_style_names": sorted(set(unresolved)),
            "note": "Only names in asset-policy-rules.json are rewritten. Maintain that list yourself, and confirm each artist's status and rights."}


# ----------------------------------------------------------------- policy.manifest_check

def op_manifest_check(p, ctx):
    data = C.read_json(p["manifest"])
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ToolError("invalid_input", "manifest must be a list or an object with an 'items' list", field="manifest")
    if len(items) > ctx.limits["max_batch_items"] * 10:
        raise ToolError("invalid_input", "manifest too large", field="manifest")
    mode = p["mode"]
    findings = []
    if mode == "rights":
        for i, it in enumerate(items, 1):
            ref = it.get("path", f"item-{i}")
            for field in ("path", "source", "license"):
                if not it.get(field):
                    findings.append({"item": ref, "severity": "block", "issue": f"missing '{field}'"})
            lic = str(it.get("license", "")).strip().lower()
            if lic in ("unknown", "unclear", "tbd"):
                findings.append({"item": ref, "severity": "block", "issue": "licence unknown"})
            consent = it.get("consent")
            if consent is False:
                findings.append({"item": ref, "severity": "block", "issue": "consent recorded as false"})
            elif consent is None and it.get("contains_people"):
                findings.append({"item": ref, "severity": "review", "issue": "people present but consent not recorded"})
            if not it.get("retention_until"):
                findings.append({"item": ref, "severity": "warn", "issue": "no retention or removal date recorded"})
        summary = {"items": len(items), "blocking": sum(1 for f in findings if f["severity"] == "block"),
                   "review": sum(1 for f in findings if f["severity"] == "review")}
    else:
        groups = {}
        empty_caps = 0
        for it in items:
            g = it.get("subject_group") or "unspecified"
            groups[g] = groups.get(g, 0) + 1
            if not str(it.get("caption", "")).strip():
                empty_caps += 1
        counts = sorted(groups.values())
        ratio = round(counts[-1] / counts[0], 2) if counts and counts[0] else None
        if "unspecified" in groups:
            findings.append({"item": None, "severity": "warn", "issue": f"{groups['unspecified']} item(s) have no subject_group"})
        if ratio and ratio > 3:
            findings.append({"item": None, "severity": "review", "issue": f"group imbalance {ratio}:1 (largest vs smallest)"})
        if empty_caps:
            findings.append({"item": None, "severity": "warn", "issue": f"{empty_caps} item(s) have empty captions"})
        summary = {"items": len(items), "group_counts": groups, "imbalance_ratio": ratio, "empty_captions": empty_caps}
    path = ctx.out_path(p.get("output"), f"manifest-{mode}-report.json")
    report = {"mode": mode, "summary": summary, "findings": findings,
              "note": "Checks the fields you recorded. It cannot verify licences or consent itself."}
    write_json(path, report)
    ctx.record(path)
    return {"mode": mode, "summary": summary, "finding_count": len(findings), "findings": findings[:50],
            "report_file": str(path)}


# ----------------------------------------------------------------- policy.disclosure

def op_disclosure(p, ctx):
    tpl = RULES["disclosure_templates"]
    ctxname = p["context"]
    if ctxname not in tpl["contexts"]:
        raise ToolError("invalid_input", f"unknown context {ctxname}", field="context")
    phrase = tpl["edit_phrases"][p["edit"]]
    tool = p.get("tool_name")
    tool_clause = f"Tool: {tool}." if tool else ""
    short = tpl["contexts"][ctxname]["short"].format(edit_phrase=phrase, tool_clause=tool_clause).strip()
    long = tpl["contexts"][ctxname]["long"].format(edit_phrase=phrase, tool_clause=tool_clause).strip()
    return {"context": ctxname, "edit": p["edit"], "short_label": short, "long_label": long,
            "note": "Use the label that fits the placement. Keep it visible and do not remove it when the image is reused."}


# ----------------------------------------------------------------- policy.release_gate

def op_release_gate(p, ctx):
    checks = p["checks"]
    if not isinstance(checks, dict):
        raise ToolError("invalid_input", "checks must be a JSON object of item -> true/false", field="checks")
    unknown = [k for k in checks if k not in CHECKLIST]
    if unknown:
        raise ToolError("invalid_input", f"unknown checklist item(s): {unknown}", field="checks",
                        hint=f"Use: {', '.join(CHECKLIST)}")
    missing = [k for k in CHECKLIST if checks.get(k) is not True]
    action = p["action"]
    if missing:
        status = "blocked"
    elif action == "publish" and not ctx.approve:
        status = "approval_required"
    else:
        status = "ready"
    if action == "publish" and status == "approval_required":
        raise ToolError("approval_required", "publishing needs explicit human approval",
                        hint="Re-run with --approve after a reviewer confirms every checklist item.")
    result = {"action": action, "status": status, "missing_items": missing,
              "checklist": {k: RULES["release_checklist"][k] for k in CHECKLIST},
              "note": "This toolkit does not publish or upload anything. The gate result is advisory input to your release process."}
    if missing:
        result["next_step"] = "resolve each missing item and re-run the gate"
    path = ctx.out_path(p.get("output"), "release-gate.json")
    write_json(path, result)
    ctx.record(path)
    result["gate_file"] = str(path)
    if action == "publish" and status == "blocked":
        # Non-zero exit so scripts and CI cannot treat a blocked publish as success.
        raise ToolError("invalid_input", f"publish blocked; unchecked items: {', '.join(missing)}",
                        field="checks", hint=f"Gate record written to {path}")
    return result


# ----------------------------------------------------------------- policy.workflow

def op_workflow(p, ctx):
    state, action, actor = p["state"], p["action"], p["actor"].strip()
    if not actor:
        raise ToolError("invalid_input", "actor must not be empty", field="actor")
    tr = WF["transitions"][action]
    if state not in tr["from"]:
        raise ToolError("invalid_input", f"action '{action}' is not allowed from state '{state}' "
                                         f"(allowed from: {', '.join(tr['from'])})", field="action")
    if tr.get("requires_different_actor"):
        author = (p.get("author") or "").strip()
        if not author:
            raise ToolError("invalid_input", "approve needs 'author' so the approver can be checked", field="author")
        if author == actor:
            raise ToolError("invalid_input", "the author cannot approve their own item", field="actor")
    if tr.get("needs_approval") and not ctx.approve:
        raise ToolError("approval_required", f"action '{action}' needs explicit approval",
                        hint="Re-run with --approve once a human has confirmed the release.")
    lineage_path = Path(p["lineage"]) if p.get("lineage") else ctx.cap_dir / "lineage.json"
    log = []
    if lineage_path.exists():
        try:
            log = C.read_json(lineage_path).get("events", [])
        except json.JSONDecodeError:
            raise ToolError("invalid_input", "lineage file is corrupt; it was not modified", field="lineage")
        if log and log[-1].get("to") != state:
            ctx.warn(f"supplied state '{state}' differs from last recorded state '{log[-1].get('to')}'")
    event = {"time": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "action": action,
             "actor": actor, "from": state, "to": tr["to"], "note": p.get("note") or ""}
    log.append(event)
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(lineage_path, {"events": log})
    ctx.record(lineage_path)
    return {"new_state": tr["to"], "event": event, "lineage_file": str(lineage_path), "events_recorded": len(log),
            "note": "Lineage is appended, never rewritten. Keep this file with the exported assets."}


# ----------------------------------------------------------------- repro.seed_record

_RECORD_FIELDS = ["seed", "model", "sampler", "steps", "cfg", "width", "height", "prompt", "negative_prompt"]


def op_seed_record(p, ctx):
    if p["mode"] == "compare":
        if not (p.get("record_a") and p.get("record_b")):
            raise ToolError("invalid_input", "compare needs record_a and record_b", field="record_a")
        a = C.read_json(p["record_a"])
        b = C.read_json(p["record_b"])
        diffs = {k: {"a": a.get("settings", {}).get(k), "b": b.get("settings", {}).get(k)}
                 for k in _RECORD_FIELDS if a.get("settings", {}).get(k) != b.get("settings", {}).get(k)}
        return {"identical_settings": not diffs, "differences": diffs,
                "note": "Same settings and seed should reproduce on the same software and hardware. Kernel or driver changes can still alter output."}
    missing = [k for k in ("seed", "model") if p.get(k) in (None, "")]
    if missing:
        raise ToolError("invalid_input", f"record mode needs {missing}", field=missing[0])
    settings = {k: p.get(k) for k in _RECORD_FIELDS}
    settings["extra"] = p.get("extra") or {}
    canon = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    record = {"settings": settings, "settings_sha256": digest, "toolkit_version": C.TOOLKIT_VERSION,
              "reproduce": "Load the same model, sampler, steps, cfg, size, prompts and seed in the same software version."}
    path = ctx.out_path(p.get("output"), f"seed-record-{digest[:12]}.json")
    write_json(path, record)
    ctx.record(path)
    return {"record_file": str(path), "settings_sha256": digest, "settings": settings}


# ----------------------------------------------------------------- repro.asset_name

_ASSET_EXT = {"png", "jpg", "jpeg", "webp", "avif", "tif", "tiff", "psd", "svg", "json", "txt", "zip"}


def op_asset_name(p, ctx):
    tmpl = p["template"]
    items = p["items"]
    if not isinstance(items, list) or not items:
        raise ToolError("invalid_input", "items must be a non-empty list of objects", field="items")
    if len(items) > ctx.limits["max_batch_items"]:
        raise ToolError("invalid_input", "too many items", field="items")
    names, seen, errors = [], set(), []
    for i, it in enumerate(items, 1):
        fields = {}
        for k, v in it.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                fields[k] = v
            else:
                fields[k] = slugify(v, 40)
        fields.setdefault("seq", i)
        fields.setdefault("ext", "png")
        if str(fields["ext"]).lower() not in _ASSET_EXT:
            errors.append(f"item {i}: extension '{fields['ext']}' not allowed")
            continue
        try:
            name = tmpl.format(**fields)
        except KeyError as e:
            raise ToolError("invalid_input", f"template uses field {e} missing from item {i}", field="template")
        except (ValueError, TypeError) as e:
            raise ToolError("invalid_input", f"template cannot be formatted: {e}", field="template")
        name = re.sub(r"[^a-z0-9._-]", "-", name.lower()).strip("-")
        if name in seen:
            errors.append(f"duplicate name '{name}' from item {i}")
        seen.add(name)
        names.append({"item": i, "name": name})
    if errors:
        raise ToolError("invalid_input", "; ".join(errors), field="items")
    return {"names": names, "count": len(names),
            "note": "Names are lower-case ASCII with hyphens. Keep the same template for a campaign so names stay sortable."}


# ----------------------------------------------------------------- repro.handoff

def op_handoff(p, ctx):
    files = [Path(f) for f in p["files"]]
    for f in files:
        if f.suffix.lower() in ARCHIVE_EXT:
            raise ToolError("invalid_input", f"nested archives are not allowed: {f.name}", field="files",
                            hint="Unpack the archive and include its files directly.")
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        raise ToolError("invalid_input", "file base names must be unique inside the handoff", field="files")
    total = sum(f.stat().st_size for f in files)
    if total > ctx.limits["max_file_bytes"] * 2:
        raise ToolError("invalid_input", "handoff total size is above the configured limit", field="files")
    out = ctx.out_path(p.get("output"), "handoff-package.zip", inputs=[str(f) for f in files],
                       allowed_ext={".zip"})
    lines = ["HANDOFF PACKAGE", "", f"Name: {p['name']}", f"Created (UTC): {_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')}",
             f"Toolkit version: {C.TOOLKIT_VERSION}", "", "Files (sha256 and size):"]
    for f in files:
        lines.append(f"- {f.name}  {sha256_file(f)}  {f.stat().st_size} bytes")
    if p.get("readme"):
        lines += ["", "Notes:", p["readme"]]
    readme = "\n".join(lines) + "\n"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", readme)
        for f in files:
            zf.write(f, arcname=f.name)
    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
        members = zf.namelist()
    if bad:
        raise ToolError("runtime_error", f"archive failed integrity check at {bad}")
    ctx.record(out)
    return {"package": str(out), "members": members, "file_count": len(files),
            "integrity": "passed zip test", "note": "Archive contains files flattened at the root plus README.txt. Nested archives are refused."}


# ----------------------------------------------------------------- registry

OPS = {
    "policy.run": op(op_policy_run, [
        P("ruleset", "choice", True, choices=RULESETS),
        P("text", "str", help="Text to screen (request, caption or alt text)"),
        P("text_file", "file", help="Text file to screen instead of --text"),
        P("context", "str", help="Optional audit note stored with the decision"),
        P("output", "outfile"),
    ], "Screen text against a policy ruleset and return allow, review or block findings"),
    "policy.rewrite_style": op(op_rewrite_style, [
        P("text", "str", True), P("output", "outfile"),
    ], "Replace listed artist names with visual attributes"),
    "policy.manifest_check": op(op_manifest_check, [
        P("manifest", "file", True, help="JSON list (or {items:[...]}) of dataset records"),
        P("mode", "choice", default="rights", choices=["rights", "bias"]),
        P("output", "outfile"),
    ], "Check dataset rights fields or subject balance from a manifest"),
    "policy.disclosure": op(op_disclosure, [
        P("context", "choice", default="social", choices=sorted(RULES["disclosure_templates"]["contexts"].keys())),
        P("edit", "choice", default="generated", choices=sorted(RULES["disclosure_templates"]["edit_phrases"].keys())),
        P("tool_name", "str", help="Optional tool name shown in the long label"),
    ], "Write short and long AI generation or edit disclosure labels"),
    "policy.release_gate": op(op_release_gate, [
        P("checks", "json", True, help="JSON object: checklist item -> true when verified"),
        P("action", "choice", default="share_internal", choices=["share_internal", "publish"]),
        P("output", "outfile"),
    ], "Pre-release checklist gate; publishing requires --approve"),
    "policy.workflow": op(op_workflow, [
        P("state", "choice", True, choices=STATES),
        P("action", "choice", True, choices=ACTIONS),
        P("actor", "str", True), P("author", "str", help="Original author (required for approve)"),
        P("note", "str"), P("lineage", "outfile", help="Lineage JSON (default: capability folder)"),
    ], "Move an asset through review states with an append-only lineage log"),
    "repro.seed_record": op(op_seed_record, [
        P("mode", "choice", default="record", choices=["record", "compare"]),
        P("seed", "int", min=0, max=2**32 - 1), P("model", "str"), P("sampler", "str"),
        P("steps", "int", min=1, max=500), P("cfg", "float", min=0, max=30),
        P("width", "int", min=64, max=8192), P("height", "int", min=64, max=8192),
        P("prompt", "str"), P("negative_prompt", "str"), P("extra", "json"),
        P("record_a", "file"), P("record_b", "file"), P("output", "outfile"),
    ], "Record generation settings with a hash, or compare two records"),
    "repro.asset_name": op(op_asset_name, [
        P("template", "str", default="{campaign}_{subject}_{variant}_{seq:03d}.{ext}"),
        P("items", "json", True, help="List of objects with fields used by the template"),
    ], "Generate stable, sanitised asset file names from a template"),
    "repro.handoff": op(op_handoff, [
        P("files", "files", True, help="Comma list or JSON list of existing files"),
        P("name", "str", default="handoff"),
        P("readme", "str", help="Notes to include in README.txt"),
        P("output", "outfile"),
    ], "Create a ZIP handoff with README, checksums and integrity test"),
}
