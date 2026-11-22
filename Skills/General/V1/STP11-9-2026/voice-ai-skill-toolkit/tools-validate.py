#!/usr/bin/env python3
"""Validation and reporting for voice work packages.

Implements, with no third-party dependencies:

  * a JSON-Schema-subset validator (type/required/properties/items/enum/
    pattern/length/range/$ref to #/definitions/...) used for manifests
  * transcript-check   - SRT / VTT / JSON transcript validation
  * consent-check      - voice consent record validation
  * validate-package   - whole-package validation (manifest, artifacts,
    consent gate, approvals, embedded audio and transcripts)
  * qa-report          - Markdown human-review / handoff report

Verdicts are pass | warn | fail. A FAIL in any embedded check propagates to
the package verdict. Exit-code contract lives in tools-common.py.
"""

import importlib.util
import json
import os
import sys
import re
from datetime import date

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    # Share one module instance per name via sys.modules so that classes
    # (e.g. tools_common.ToolError) keep a single identity across siblings.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_common = _load_sibling("tools_common", "tools-common.py")
_catalog = _load_sibling("tools_catalog", "tools-catalog.py")
_audio = _load_sibling("tools_audiocheck", "tools-audiocheck.py")

ToolError = _common.ToolError
MAX_TEXT_BYTES = _common.MAX_TEXT_BYTES

# ---------------------------------------------------------------- mini jsonschema
_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def _type_ok(value, type_name):
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPE_MAP.get(type_name)
    return isinstance(value, expected) if expected else True


def jsonschema_subset_validate(instance, schema, path="$"):
    errors = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/definitions/"):
            errors.append("%s: unsupported $ref %s" % (path, ref))
            return errors
        target = schema.get("_root", {}).get("definitions", {}).get(ref.split("/")[-1])
        if target is None:
            errors.append("%s: unresolvable $ref %s" % (path, ref))
            return errors
        merged = dict(target)
        merged["_root"] = schema.get("_root", {})
        return jsonschema_subset_validate(instance, merged, path)

    root = schema.get("_root", schema)

    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: %r not in enum %s" % (path, instance, schema["enum"]))

    type_name = schema.get("type")
    if type_name:
        types = type_name if isinstance(type_name, list) else [type_name]
        if not any(_type_ok(instance, t) for t in types):
            errors.append("%s: expected type %s, got %s" % (path, type_name, type(instance).__name__))
            return errors  # further checks meaningless

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: shorter than minLength %d" % (path, schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: longer than maxLength %d" % (path, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: does not match pattern %s" % (path, schema["pattern"]))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: below minimum %s" % (path, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: above maximum %s" % (path, schema["maximum"]))

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append("%s: missing required property '%s'" % (path, req))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in instance:
                sub = dict(sub)
                sub.setdefault("_root", root)
                errors.extend(jsonschema_subset_validate(instance[key], sub, "%s.%s" % (path, key)))

    if isinstance(instance, list) and "items" in schema:
        sub = dict(schema["items"])
        sub.setdefault("_root", root)
        for i, item in enumerate(instance):
            errors.extend(jsonschema_subset_validate(item, sub, "%s[%d]" % (path, i)))

    return errors


def load_manifest_schema():
    return _common.load_asset_json("asset-work-package-schema.json")


def validate_manifest(manifest):
    schema = load_manifest_schema()
    schema = dict(schema)
    schema.setdefault("_root", schema)
    return jsonschema_subset_validate(manifest, schema, "$.work-package")


# ---------------------------------------------------------------- transcripts
_TS_SRT = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_TS_VTT_SHORT = re.compile(r"(\d{1,2}):(\d{2})[,.](\d{1,3})")


def _parse_srt_ts(text):
    m = _TS_SRT.fullmatch(text.strip())
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_transcript(path):
    _common.check_readable_file(path, MAX_TEXT_BYTES, "Transcript")
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        raw = fh.read()

    if ext == ".json":
        data = json.loads(raw)
        cues = data.get("segments") if isinstance(data, dict) else data
        if not isinstance(cues, list):
            raise ToolError("JSON transcript must be a list of {start,end,text} or {'segments': [...]}")
        out = []
        for i, cue in enumerate(cues):
            if not isinstance(cue, dict) or "start" not in cue or "end" not in cue or "text" not in cue:
                raise ToolError("JSON cue #%d must have start, end and text" % i)
            out.append({"start": float(cue["start"]), "end": float(cue["end"]),
                        "text": str(cue["text"]).strip()})
        return "json", out

    is_vtt = ext == ".vtt" or raw.lstrip().lower().startswith("webvtt")
    cues, blocks = [], re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if is_vtt and lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
            if not lines:
                continue
        # optional numeric/identifier line before timing
        if "-->" not in lines[0] and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[1:]
        if "-->" not in lines[0]:
            continue  # NOTE/STYLE blocks etc.
        timing = lines[0]
        left, _, right = timing.partition("-->")
        right = right.strip().split()[0]
        start = _parse_srt_ts(left)
        if start is None:
            start = _parse_vtt_short(left)
        end = _parse_srt_ts(right)
        if end is None:
            end = _parse_vtt_short(right)
        if start is None or end is None:
            cues.append({"start": None, "end": None, "text": "PARSE-ERROR: " + timing})
            continue
        cues.append({"start": start, "end": end, "text": " ".join(lines[1:]).strip()})
    if not cues:
        raise ToolError("No cues found in transcript: %s" % path)
    return ("vtt" if is_vtt else "srt"), cues


def _parse_vtt_short(text):
    m = _TS_VTT_SHORT.fullmatch(text.strip())
    if not m:
        return None
    mi, s, ms = m.groups()
    return int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def check_transcript(path):
    fmt, cues = parse_transcript(path)
    issues = []
    parse_errors = sum(1 for c in cues if c["start"] is None)
    for i, c in enumerate(cues):
        if c["start"] is None:
            issues.append({"severity": "fail", "cue": i + 1, "issue": "unparseable timing line"})
            continue
        if c["start"] < 0 or c["end"] < 0:
            issues.append({"severity": "fail", "cue": i + 1, "issue": "negative timestamp"})
        if c["end"] <= c["start"]:
            issues.append({"severity": "fail", "cue": i + 1,
                           "issue": "end (%s) <= start (%s)" % (c["end"], c["start"])})
        if not c["text"]:
            issues.append({"severity": "warn", "cue": i + 1, "issue": "empty cue text"})
        dur = c["end"] - c["start"]
        if dur > 0 and c["text"]:
            cps = len(c["text"]) / dur
            if cps > 21.0:
                issues.append({"severity": "warn", "cue": i + 1,
                               "issue": "reading speed %.1f chars/sec exceeds 21 CPS" % cps})

    starts = [c["start"] for c in cues if c["start"] is not None]
    for i in range(1, len(cues)):
        prev, cur = cues[i - 1], cues[i]
        if prev["start"] is None or cur["start"] is None:
            continue
        if cur["start"] < prev["end"]:
            issues.append({"severity": "warn", "cue": i + 1,
                           "issue": "overlaps previous cue by %.3fs" % (prev["end"] - cur["start"])})
        if cur["start"] < prev["start"]:
            issues.append({"severity": "warn", "cue": i + 1,
                           "issue": "start is earlier than the previous cue (non-monotonic)"})

    words = sum(len(c["text"].split()) for c in cues)
    span = (max(starts) - min(starts)) if starts else 0.0
    wpm = (words / (span / 60.0)) if span > 0 else 0.0
    fails = sum(1 for i in issues if i["severity"] == "fail")
    warns = sum(1 for i in issues if i["severity"] == "warn")
    verdict = "fail" if fails else ("warn" if warns else "pass")
    return {
        "ok": verdict != "fail",
        "verdict": verdict,
        "file": path,
        "format": fmt,
        "cue_count": len(cues),
        "word_count": words,
        "span_seconds": round(span, 3),
        "wpm": round(wpm, 1),
        "parse_errors": parse_errors,
        "issues": issues,
    }


# ---------------------------------------------------------------- consent
_CONSENT_REQUIRED = [
    ("voice_owner_name", "string"),
    ("signed_by", "string"),
    ("signature_date", "date"),
    ("scope", "object"),
    ("disclosures", "object"),
    ("data_handling", "object"),
    ("revocation", "object"),
]


def _is_iso_date(value):
    try:
        date(int(value[:4]), int(value[5:7]), int(value[8:10]))
        return True
    except (ValueError, TypeError, IndexError):
        return False


def check_consent_record(record):
    issues = []

    def add(severity, field, message):
        issues.append({"severity": severity, "field": field, "issue": message})

    if not isinstance(record, dict):
        raise ToolError("Consent record must be a JSON object.")

    for field, kind in _CONSENT_REQUIRED:
        if field not in record or record[field] in (None, "", [], {}):
            add("fail", field, "missing or empty required field")

    if "signature_date" in record and not _is_iso_date(str(record.get("signature_date", ""))):
        add("fail", "signature_date", "not a valid ISO date (YYYY-MM-DD)")

    scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
    if scope:
        if not isinstance(scope.get("use_cases"), list) or not scope["use_cases"]:
            add("fail", "scope.use_cases", "at least one use case must be recorded")
        ts, te = scope.get("term_start"), scope.get("term_end")
        if ts and not _is_iso_date(str(ts)):
            add("fail", "scope.term_start", "not a valid ISO date")
        if te and not _is_iso_date(str(te)):
            add("fail", "scope.term_end", "not a valid ISO date")
        if ts and te and _is_iso_date(str(ts)) and _is_iso_date(str(te)) and str(te) < str(ts):
            add("fail", "scope.term_end", "term_end is before term_start")

    disclosures = record.get("disclosures") if isinstance(record.get("disclosures"), dict) else {}
    for key, value in (disclosures or {}).items():
        if value is not True:
            add("fail", "disclosures.%s" % key, "disclosure flag must be true, got %r" % value)
    for key in ("ai_generated_voice_disclosed", "synthesis_or_cloning_disclosed"):
        if disclosures and disclosures.get(key) is not True:
            add("fail", "disclosures.%s" % key, "required disclosure missing or false")

    dh = record.get("data_handling") if isinstance(record.get("data_handling"), dict) else {}
    if dh:
        retention = dh.get("retention_days")
        if not isinstance(retention, int) or isinstance(retention, bool) or retention < 1:
            add("fail", "data_handling.retention_days", "must be an integer >= 1")
        if dh.get("deletion_on_revocation") is not True:
            add("warn", "data_handling.deletion_on_revocation",
                "recommended true so revocation triggers deletion/re-derivation")

    rev = record.get("revocation") if isinstance(record.get("revocation"), dict) else {}
    if rev:
        if not str(rev.get("contact", "")).strip():
            add("fail", "revocation.contact", "a revocation contact is required")
        if not str(rev.get("process", "")).strip():
            add("fail", "revocation.process", "a revocation process description is required")
        lead = rev.get("lead_time_days")
        if not isinstance(lead, int) or isinstance(lead, bool) or lead < 0:
            add("fail", "revocation.lead_time_days", "must be an integer >= 0")

    witness = record.get("witness") if isinstance(record.get("witness"), dict) else {}
    if not witness.get("name"):
        add("warn", "witness.name", "witness name recommended for cloning/biometric use")

    fails = sum(1 for i in issues if i["severity"] == "fail")
    verdict = "fail" if fails else ("warn" if issues else "pass")
    return {
        "ok": verdict != "fail",
        "verdict": verdict,
        "record_id": record.get("record_id"),
        "issues": issues,
    }


# ---------------------------------------------------------------- packages
def _resolve_skill_ref(skill_id):
    try:
        skill, candidates = _catalog.resolve_skill(skill_id)
    except ToolError:
        return None, []
    return skill, candidates


def validate_package(package_dir, delivery_profile=None, ffmpeg=None, timeout=120):
    if not os.path.isdir(package_dir):
        raise ToolError("Package directory not found: %s" % package_dir)
    manifest_path = os.path.join(package_dir, "work-package.json")
    if not os.path.isfile(manifest_path):
        raise ToolError(
            "work-package.json not found in %s — run `init-package` to scaffold one." % package_dir
        )
    manifest = _common.read_json_file(manifest_path, "Work package manifest")

    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    # 1) schema
    schema_errors = validate_manifest(manifest)
    if schema_errors:
        for err in schema_errors[:20]:
            add("manifest-schema", "fail", err)
    else:
        add("manifest-schema", "pass", "manifest conforms to asset-work-package-schema.json")

    # 2) catalog skill resolution + tier
    skill_ref = manifest.get("skill_id") or ""
    skill, candidates = _resolve_skill_ref(skill_ref)
    tier = manifest.get("tier") or (skill.get("tier") if skill else None)
    if skill is None:
        detail = "skill_id %r not found in catalog" % skill_ref
        if candidates:
            detail += " (did you mean: %s)" % ", ".join(c["name"] for c in candidates[:5])
        add("catalog-skill", "fail", detail)
    else:
        add("catalog-skill", "pass",
            "resolved to %s (%s, tier %s)" % (skill["name"], skill["number"], tier))

    consent_required = bool(manifest.get("consent_required")) or tier == "elevated"

    # 3) approvals consistency
    status = manifest.get("status", "draft")
    approvals = manifest.get("approvals") or []
    approved = any(a.get("decision") == "approved" for a in approvals)
    if status in ("approved", "delivered", "archived"):
        if approved:
            add("approvals", "pass", "status '%s' backed by an approved approval record" % status)
        else:
            add("approvals", "fail",
                "status '%s' requires at least one approval with decision=approved" % status)
    elif approvals:
        add("approvals", "pass", "%d approval record(s) on file (status %s)" % (len(approvals), status))
    else:
        add("approvals", "warn", "no approvals recorded yet (status %s)" % status)

    # 4) consent gate
    consent_path = os.path.join(package_dir, "consent-record.json")
    consent_result = None
    if os.path.isfile(consent_path):
        consent_result = check_consent_record(_common.read_json_file(consent_path, "Consent record"))
        add("consent", consent_result["verdict"],
            "consent-record.json: %s" % ("valid" if consent_result["ok"] else "; ".join(
                i["issue"] for i in consent_result["issues"] if i["severity"] == "fail") or "warnings present"))
    elif consent_required:
        add("consent", "fail",
            "consent is required (tier=%s) but consent-record.json is missing" % tier)
    else:
        add("consent", "pass", "consent not required for this package (tier=%s)" % (tier or "unknown"))

    # 5) artifact existence + embedded checks
    outputs = manifest.get("outputs") or []
    if not outputs:
        add("outputs", "warn", "manifest declares no outputs yet")
    for artifact in outputs:
        rel = artifact.get("path")
        kind = artifact.get("kind")
        if not rel:
            add("artifact:%s" % artifact.get("name", "?"), "warn",
                "output '%s' has no path; existence not checked" % artifact.get("name"))
            continue
        full = os.path.abspath(os.path.join(package_dir, rel))
        base = os.path.abspath(package_dir)
        if full != base and not full.startswith(base + os.sep):
            add("artifact:%s" % rel, "fail", "output path escapes the package directory")
            continue
        if not os.path.isfile(full):
            add("artifact:%s" % rel, "fail", "declared output not found on disk")
            continue
        ext = os.path.splitext(full)[1].lower()
        if kind == "audio" or ext == ".wav":
            try:
                result = _audio.run_audio_check(
                    full,
                    profile_id=delivery_profile or manifest.get("delivery_profile"),
                    ffmpeg=ffmpeg,
                    timeout=timeout,
                )
                add("audio:%s" % rel, result["verdict"],
                    "verdict %s; peak %.2f dBFS, rms %.2f dBFS, %.2fs" % (
                        result["verdict"], result["measured"]["peak_dbfs"],
                        result["measured"]["rms_dbfs"], result["measured"]["duration_seconds"]))
            except ToolError as exc:
                add("audio:%s" % rel, "fail", str(exc))
        elif kind == "transcript" or ext in (".srt", ".vtt", ".json"):
            try:
                result = check_transcript(full)
                add("transcript:%s" % rel, result["verdict"],
                    "verdict %s; %d cues, %d words, wpm %.1f" % (
                        result["verdict"], result["cue_count"], result["word_count"], result["wpm"]))
            except (ToolError, ValueError) as exc:
                add("transcript:%s" % rel, "fail", str(exc))
        else:
            add("artifact:%s" % rel, "pass", "present (%d bytes)" % os.path.getsize(full))

    statuses = [c["status"] for c in checks]
    verdict = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")
    return {
        "ok": verdict != "fail",
        "verdict": verdict,
        "package_dir": package_dir,
        "package_id": manifest.get("package_id"),
        "skill": {"id": skill["id"], "number": skill["number"], "name": skill["name"]} if skill else None,
        "tier": tier,
        "consent_required": consent_required,
        "checks": checks,
    }


def render_qa_report(package_dir, delivery_profile=None, ffmpeg=None, timeout=120):
    result = validate_package(package_dir, delivery_profile=delivery_profile,
                              ffmpeg=ffmpeg, timeout=timeout)
    manifest = _common.read_json_file(os.path.join(package_dir, "work-package.json"),
                                      "Work package manifest")
    lines = []
    lines.append("# QA / Handoff Report — %s" % (result.get("package_id") or os.path.basename(package_dir)))
    lines.append("")
    lines.append("- Skill: %s" % ("%s (%s)" % (result["skill"]["name"], result["skill"]["number"]) if result["skill"] else "unresolved"))
    lines.append("- Tier: %s" % (result["tier"] or "unknown"))
    lines.append("- Status: %s" % manifest.get("status"))
    lines.append("- Language: %s" % manifest.get("language"))
    lines.append("- Verdict: **%s**" % result["verdict"].upper())
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Result | Detail |")
    lines.append("|---|---|---|")
    for c in result["checks"]:
        lines.append("| %s | %s | %s |" % (c["name"], c["status"].upper(), c["detail"].replace("|", "\\|")))
    lines.append("")
    lines.append("## Human review checklist (complete before handoff)")
    lines.append("")
    for item in [
        "Listening review performed by a qualified reviewer; notes attached",
        "Intelligibility confirmed in the target channel and environment",
        "Naturalness and pacing appropriate to the delivery profile/preset",
        "Technical measurement reviewed (see audio checks above)",
        "Consent and privacy review complete; record filed if required",
        "Accessibility addressed (transcript/captions where applicable)",
        "Delivery profile and naming conventions match the delivery spec",
        "Clean-state handoff: sources preserved, no stray files, report signed",
    ]:
        lines.append("- [ ] %s" % item)
    lines.append("")
    lines.append("## Sign-off")
    lines.append("")
    lines.append("| Role | Name | Date | Decision |")
    lines.append("|---|---|---|---|")
    lines.append("| Reviewer |  |  |  |")
    lines.append("| Approver |  |  |  |")
    lines.append("")
    lines.append("Generated by tools-voice-skill-tool.py qa-report. A PASS verdict is evidence, not approval; a human must sign.")
    return "\n".join(lines) + "\n", result
