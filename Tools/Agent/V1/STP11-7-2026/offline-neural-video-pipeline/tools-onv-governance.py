"""Rights ledger, consent gate, provenance and delivery packaging.

The ledger is an append-only JSON document:

    {"schema_version": "1.0", "records": [ {record}, ... ]}

A record looks like::

    {"subject_id": "actor-001", "kind": "person",
     "consent": {"status": "granted", "scope": "internal-preview",
                 "granted_by": "producer", "evidence": "consent-2026-04-12.pdf",
                 "recorded_utc": "...", "expires_utc": null},
     "rights": {"license": "operator-declared", "allowed_use": "internal-preview"},
     "provenance": {"notes": "...", "sources": []},
     "approvals": [{"action": "backend_dispatch", "granted_by": "operator", "granted_utc": "..."}]}
"""

from __future__ import annotations

import datetime as dt
import shutil
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------
def empty_ledger():
    return {"schema_version": "1.0", "created_utc": None, "records": []}


def load_ledger(ctx, path):
    common = ctx.common
    doc = common.json_load(path, label="rights ledger", required=False)
    if not doc:
        ledger = empty_ledger()
        ledger["created_utc"] = common.utcnow()
        return ledger
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        common.fail("E_INPUT", "rights ledger must be an object with a 'records' list", path=str(path))
    doc.setdefault("schema_version", "1.0")
    return doc


def save_ledger(ctx, path, ledger):
    ctx.common.json_write(path, ledger)
    return path


def add_record(ctx, ledger_path, subject_id, status, scope, evidence=None, license_name=None,
               allowed_use=None, kind="person", expires_utc=None, notes=None, granted_by=None):
    common = ctx.common
    policies = (ctx.governance_policy or {}).get("scopes", {})
    allowed_scopes = policies.get("allowed_use_values") or []
    if allowed_scopes and scope not in allowed_scopes:
        common.fail(
            "E_INPUT",
            "scope '%s' is not in the governance allow-list" % scope,
            allowed=allowed_scopes,
        )
    if status not in ("granted", "denied", "withdrawn", "unknown"):
        common.fail("E_INPUT", "status must be granted, denied, withdrawn or unknown", status=status)
    if status == "granted" and not evidence:
        common.fail("E_INPUT", "a granted consent record requires --evidence (document, ticket or hash reference)")
    ledger = load_ledger(ctx, ledger_path)
    record = {
        "record_id": "rec-%04d" % (len(ledger["records"]) + 1),
        "subject_id": str(subject_id),
        "kind": kind,
        "consent": {
            "status": status,
            "scope": scope,
            "granted_by": granted_by,
            "evidence": evidence,
            "recorded_utc": common.utcnow(),
            "expires_utc": expires_utc,
        },
        "rights": {"license": license_name, "allowed_use": allowed_use or scope},
        "provenance": {"notes": notes, "sources": []},
        "approvals": [],
    }
    ledger["records"].append(record)
    save_ledger(ctx, ledger_path, ledger)
    return record


def evaluate_consent(ledger, subject_ids, allowed_use=None, now=None):
    """Return ``{"ok": bool, "reasons": [...], "subjects": {...}}``."""
    now = now or dt.datetime.now(dt.timezone.utc)
    reasons = []
    subjects = {}
    records = (ledger or {}).get("records") or []
    for subject in subject_ids:
        matching = [r for r in records if str(r.get("subject_id")) == str(subject)]
        if not matching:
            subjects[subject] = {"status": "missing", "reason": "no ledger record"}
            reasons.append("no consent record for subject '%s'" % subject)
            continue
        latest = matching[-1]
        consent = latest.get("consent") or {}
        status = consent.get("status")
        problems = []
        if status != "granted":
            problems.append("status is '%s'" % status)
        if allowed_use and consent.get("scope") and consent["scope"] != allowed_use:
            problems.append("scope '%s' does not cover '%s'" % (consent["scope"], allowed_use))
        if not consent.get("evidence"):
            problems.append("no evidence reference")
        expires = consent.get("expires_utc")
        if expires:
            try:
                expiry = dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if expiry < now:
                    problems.append("consent expired %s" % expires)
            except ValueError:
                problems.append("expires_utc is not ISO-8601")
        subjects[subject] = {
            "status": "granted" if not problems else "invalid",
            "problems": problems,
            "record_id": latest.get("record_id"),
            "evidence": consent.get("evidence"),
            "scope": consent.get("scope"),
        }
        if problems:
            reasons.append("subject '%s': %s" % (subject, "; ".join(problems)))
    return {"ok": not reasons, "reasons": reasons, "subjects": subjects, "checked_utc": now.isoformat().replace("+00:00", "Z")}


def preflight_gate(ctx, spec, plan):
    """Gate run by the executor before any media work."""
    common = ctx.common
    rights = spec.get("rights") or {}
    governance_policy = ctx.governance_policy or {}
    op = plan.get("operation")
    consent_required_ops = (governance_policy.get("consent") or {}).get("required_when") or []
    must_consent = bool(rights.get("require_consent")) or (op in consent_required_ops and rights.get("subject_ids"))

    facts = {"operation": op, "consent_required": must_consent, "subjects": rights.get("subject_ids") or []}

    if op in ((governance_policy.get("disclosure") or {}).get("required_for_ops") or []) and not rights.get("disclosure_required", True):
        return {
            "stage": "governance",
            "status": "failed",
            "message": "operation '%s' requires disclosure, but spec.rights.disclosure_required is false" % op,
            "outputs": [],
            "facts": facts,
        }

    allowed_use = rights.get("allowed_use") or (governance_policy.get("scopes") or {}).get("default_allowed_use")
    if not must_consent:
        return {"stage": "governance", "status": "skipped", "message": "no consent gate required for this job", "outputs": [], "facts": facts}

    if not rights.get("subject_ids"):
        return {
            "stage": "governance",
            "status": "failed",
            "message": "consent is required but spec.rights.subject_ids is empty",
            "outputs": [],
            "facts": facts,
        }
    ledger_path = rights.get("ledger")
    if not ledger_path:
        return {
            "stage": "governance",
            "status": "failed",
            "message": "consent is required but spec.rights.ledger is not set",
            "outputs": [],
            "facts": facts,
        }
    ledger = load_ledger(ctx, ledger_path)
    gate = evaluate_consent(ledger, rights["subject_ids"], allowed_use)
    facts["gate"] = gate
    if not gate["ok"]:
        return {
            "stage": "governance",
            "status": "failed",
            "message": "consent gate blocked the job: %s" % "; ".join(gate["reasons"]),
            "outputs": [],
            "facts": facts,
        }
    return {
        "stage": "governance",
        "status": "succeeded",
        "message": "consent gate satisfied for %d subject(s)" % len(rights["subject_ids"]),
        "outputs": [],
        "facts": facts,
    }


# ---------------------------------------------------------------------------
# provenance + disclosure
# ---------------------------------------------------------------------------
def build_provenance(ctx, spec, plan, state, media_path):
    common = ctx.common
    media_path = Path(media_path)
    validation_report = None
    if state.data.get("validation_report"):
        try:
            validation_report = common.json_load(state.data["validation_report"], label="validation report", required=False)
        except common.OnvError:
            validation_report = None
    inputs = []
    for entry in spec.get("inputs") or []:
        path = Path(entry["path"])
        inputs.append(
            {
                "role": entry.get("role"),
                "path": entry["path"],
                "exists": path.exists(),
                "bytes": common.file_size(path),
                "sha256": common.sha256_file(path) if path.exists() else None,
            }
        )
    routing = plan.get("routing") or {}
    model = routing.get("model") or {}
    record = {
        "provenance_version": "1.0",
        "job_id": spec["job_id"],
        "title": spec.get("title"),
        "capability": plan.get("capability", {}).get("name"),
        "capability_slug": plan.get("capability", {}).get("slug"),
        "operation": plan.get("operation"),
        "mode": spec.get("mode"),
        "preset": spec.get("preset"),
        "tool": {"name": common.TOOL_NAME, "version": common.TOOL_VERSION},
        "catalog_version": (ctx.catalog or {}).get("catalog_version") if isinstance(ctx.catalog, dict) else None,
        "registry_version": (ctx.registry or {}).get("registry_version") if isinstance(ctx.registry, dict) else None,
        "models": [
            {
                "id": routing.get("model_id"),
                "label": model.get("label"),
                "license": (routing.get("license") or {}).get("name"),
                "license_redistribution": (routing.get("license") or {}).get("redistribution"),
                "weights": model.get("weights"),
            }
        ] if routing.get("model_id") else [],
        "spec_sha256": spec.get("spec_sha256"),
        "prompt_sha256": common.sha256_text(spec.get("prompt") or ""),
        "inputs": inputs,
        "output": {
            "path": str(media_path),
            "bytes": common.file_size(media_path),
            "sha256": common.sha256_file(media_path) if media_path.exists() else None,
        },
        "validation": {
            "passed": (validation_report or {}).get("passed"),
            "summary": (validation_report or {}).get("summary"),
            "reason": (validation_report or {}).get("reason"),
            "report_path": state.data.get("validation_report"),
        },
        "proxy_path_used": bool(state.data.get("proxy_stages")),
        "proxy_stages": state.data.get("proxy_stages") or [],
        "operator_approvals": {
            "approved": bool(ctx.approve),
            "approved_at": common.utcnow() if ctx.approve else None,
        },
        "environment": {
            "platform": ctx.environment.get("platform"),
            "python": ctx.environment.get("python"),
            "ffmpeg": ctx.environment.get("ffmpeg"),
        },
        "created_utc": common.utcnow(),
    }
    return record


def render_template(ctx, name, fields):
    templates = ((ctx.disclosures or {}).get("templates") or {})
    if name not in templates:
        ctx.common.fail("E_INPUT", "disclosure template '%s' not found" % name, available=sorted(templates))
    body = templates[name]["body"]
    out = body
    for key, value in fields.items():
        if value is None:
            value = "n/a"
        out = out.replace("{%s}" % key, str(value))
    return out


def write_disclosure(ctx, spec, plan, provenance, target_dir):
    common = ctx.common
    governance = ctx.governance_policy or {}
    disclosure_cfg = governance.get("disclosure") or {}
    fields = {
        "job_id": spec["job_id"],
        "capability": plan.get("capability", {}).get("name"),
        "operation": plan.get("operation"),
        "model_ids": ", ".join(m["id"] for m in provenance.get("models") or []) or "none (local operation)",
        "preset": spec.get("preset"),
        "mode": spec.get("mode"),
        "created_date": provenance.get("created_utc"),
        "allowed_use": (spec.get("rights") or {}).get("allowed_use") or disclosure_cfg.get("default_allowed_use", "internal-preview"),
        "ledger_reference": (spec.get("rights") or {}).get("ledger") or "not recorded",
        "tool_version": common.TOOL_VERSION,
    }
    template_name = disclosure_cfg.get("default_template") or "synthetic-media-full"
    path = Path(target_dir) / "disclosure.md"
    body = render_template(ctx, template_name, fields)
    if provenance.get("proxy_path_used"):
        body += "\n" + render_template(ctx, "proxy-work-warning", fields) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def _file_table(directory, names):
    rows = ["| File | Bytes |", "| --- | --- |"]
    for name in names:
        path = Path(directory) / name
        rows.append("| `%s` | %s |" % (name, path.stat().st_size if path.exists() else "missing"))
    return "\n".join(rows)


def package_delivery(ctx, spec, plan, state, media_path):
    """Copy the delivery media, provenance, disclosure and report into a ZIP."""
    common = ctx.common
    media_path = Path(media_path)
    if not media_path.exists():
        common.fail("E_INPUT", "delivery media missing: %s" % media_path)
    package_dir = ctx.paths["packages"] / spec["job_id"]
    if package_dir.exists():
        shutil.rmtree(str(package_dir), ignore_errors=True)
    package_dir.mkdir(parents=True, exist_ok=True)

    provenance = build_provenance(ctx, spec, plan, state, media_path)
    media_copy = package_dir / ("%s%s" % (spec["job_id"], media_path.suffix))
    shutil.copyfile(str(media_path), str(media_copy))

    provenance_path = common.json_write(package_dir / "provenance.json", provenance)
    disclosure_path = write_disclosure(ctx, spec, plan, provenance, package_dir)

    validation_path = None
    if state.data.get("validation_report"):
        source = Path(state.data["validation_report"])
        if source.exists():
            validation_path = package_dir / "validation-report.json"
            shutil.copyfile(str(source), str(validation_path))

    names = sorted(p.name for p in package_dir.iterdir() if p.is_file())
    summary = provenance.get("validation") or {}
    readme_body = render_template(
        ctx,
        "delivery-readme",
        {
            "job_id": spec["job_id"],
            "capability": plan.get("capability", {}).get("name"),
            "operation": plan.get("operation"),
            "mode": spec.get("mode"),
            "preset": spec.get("preset"),
            "model_ids": ", ".join(m["id"] for m in provenance.get("models") or []) or "none (local operation)",
            "created_date": provenance.get("created_utc"),
            "tool_version": common.TOOL_VERSION,
            "allowed_use": (spec.get("rights") or {}).get("allowed_use") or "internal-preview",
            "file_table": _file_table(package_dir, names),
            "validation_summary": "passed=%s, %s" % (summary.get("passed"), summary.get("reason")),
        },
    )
    readme_path = package_dir / "README.md"
    readme_path.write_text(readme_body, encoding="utf-8")

    archive = ctx.paths["packages"] / ("%s-delivery.zip" % spec["job_id"])
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(str(archive), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(package_dir.iterdir()):
            if path.is_file():
                bundle.write(str(path), arcname=path.name)

    check = verify_archive(archive, expected_names=sorted(p.name for p in package_dir.iterdir() if p.is_file()))
    if not check["ok"]:
        common.fail("E_RUNTIME", "delivery archive failed verification: %s" % "; ".join(check["problems"]), archive=str(archive))
    return {
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "package_dir": str(package_dir),
        "provenance": str(provenance_path),
        "disclosure": str(disclosure_path),
        "validation_report": str(validation_path) if validation_path else None,
        "files": check["files"],
        "verified": check["ok"],
    }


def verify_archive(archive, expected_names=None):
    """Open an archive and confirm it has no nested archives and lists cleanly."""
    problems = []
    files = []
    with zipfile.ZipFile(str(archive), "r") as bundle:
        bad = bundle.testzip()
        if bad is not None:
            problems.append("corrupt member: %s" % bad)
        for info in bundle.infolist():
            name = info.filename
            files.append({"name": name, "bytes": info.file_size})
            if name.lower().endswith((".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar")):
                problems.append("nested archive member: %s" % name)
            if name.startswith("/") or ".." in Path(name).parts:
                problems.append("unsafe member path: %s" % name)
    if expected_names:
        missing = sorted(set(expected_names) - {f["name"] for f in files})
        if missing:
            problems.append("missing members: %s" % ", ".join(missing))
    return {"ok": not problems, "problems": problems, "files": files, "member_count": len(files)}


def ledger_lines(ledger, path):
    records = (ledger or {}).get("records") or []
    lines = ["Rights ledger: %s (%d record(s))" % (path, len(records)), "-" * 78]
    for record in records:
        consent = record.get("consent") or {}
        lines.append(
            "  %-10s %-22s %-9s scope=%-20s evidence=%s"
            % (
                record.get("record_id"),
                record.get("subject_id"),
                consent.get("status"),
                consent.get("scope"),
                consent.get("evidence"),
            )
        )
    if not records:
        lines.append("  (empty ledger)")
    return lines
