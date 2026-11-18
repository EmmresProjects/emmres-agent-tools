"""Runtime support for the accessibility-and-localization Skill Tool.

Responsibilities: structured errors, config loading/validation, bounded IO with
retries and evidence hashing, limits/deadline/cancellation, the finding model,
report rendering (markdown/JSON/SARIF/JUnit) and atomic output writing.

Standard library only. Loaded by ``tools-a11yl10n.py`` through the prefixed
file loader; see tools-a11yl10n.py for the loading order.
"""

import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

TOOL_VERSION = "1.0.0"
RESULT_SCHEMA = "a11yl10n.result/1"
SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}

DEFAULT_LIMITS = {
    "max_file_bytes": 8 * 1024 * 1024,
    "max_files": 200,
    "max_records": 50000,
    "timeout_seconds": 120,
    "read_retries": 2,
    "max_findings": 5000,
    "max_text_chars": 4000000,
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class SkillToolError(Exception):
    """Base class for errors that must surface as structured results."""

    code = "error"
    exit_code = 1
    http_like_status = 400

    def __init__(self, message, hint=None, details=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def as_dict(self):
        out = {"error": {"code": self.code, "message": self.message}}
        if self.hint:
            out["error"]["hint"] = self.hint
        if self.details:
            out["error"]["details"] = self.details
        return out


class InputError(SkillToolError):
    code = "input_error"
    exit_code = 2


class ParamError(SkillToolError):
    code = "parameter_error"
    exit_code = 2


class ResolutionError(SkillToolError):
    code = "skill_not_found"
    exit_code = 4


class ApprovalRequired(SkillToolError):
    code = "approval_required"
    exit_code = 3

    def __init__(self, action, message=None):
        super().__init__(
            message or "This operation changes persisted state and requires explicit approval.",
            hint="Re-run the identical command with --approve after human review.",
            details={"action": action},
        )


class LimitExceeded(SkillToolError):
    code = "limit_exceeded"
    exit_code = 5


class DeadlineExceeded(SkillToolError):
    code = "timeout"
    exit_code = 6


# --------------------------------------------------------------------------- #
# Config / assets
# --------------------------------------------------------------------------- #
def deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_json(path, retries=2):
    raw = read_bytes(path, retries=retries)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise InputError("File is not valid UTF-8: %s" % path, details={"reason": str(exc)})
    except json.JSONDecodeError as exc:
        raise InputError(
            "Invalid JSON in %s at line %s column %s" % (path, exc.lineno, exc.colno),
            hint="Fix the JSON syntax or point --input at a different file.",
            details={"reason": exc.msg},
        )


def validate_config(cfg):
    """Validate the runtime config shape. Returns list of human-readable problems."""
    problems = []
    if not isinstance(cfg, dict):
        return ["config root must be an object"]
    limits = cfg.get("limits", {})
    for key, default in DEFAULT_LIMITS.items():
        val = limits.get(key, default)
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            problems.append("limits.%s must be a positive integer" % key)
    thresholds = cfg.get("thresholds", {})
    if not isinstance(thresholds, dict):
        problems.append("thresholds must be an object")
    for key in ("contrast_text_aa", "contrast_text_aaa", "contrast_nontext_aa",
                "contrast_large_aa", "min_target_size_css_px", "max_caption_cps",
                "expansion_budget_pct", "max_reading_grade"):
        val = thresholds.get(key)
        if val is not None and not isinstance(val, (int, float)):
            problems.append("thresholds.%s must be a number" % key)
    outcfg = cfg.get("output", {})
    if outcfg and not isinstance(outcfg, dict):
        problems.append("output must be an object")
    return problems


def resolve_asset_path(root, relpath):
    """Resolve an asset reference relative to the skill root; reject traversal."""
    if os.path.isabs(relpath) or re.match(r"^[A-Za-z]:", relpath or ""):
        raise InputError("Asset references must be relative: %s" % relpath)
    norm = os.path.normpath(relpath).replace(os.sep, "/")
    if norm.startswith("..") or "/../" in norm:
        raise InputError("Asset reference may not traverse upward: %s" % relpath)
    path = os.path.join(root, *norm.split("/"))
    if not os.path.exists(path):
        raise InputError(
            "Referenced resource is missing: %s" % norm,
            hint="Verify the prefixed filename and its case; all references are relative to the skill root.",
        )
    return path


# --------------------------------------------------------------------------- #
# Bounded IO
# --------------------------------------------------------------------------- #
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8", "replace"))


def check_size(path, limits):
    limit = int(limits.get("max_file_bytes", DEFAULT_LIMITS["max_file_bytes"]))
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise InputError("Cannot stat %s: %s" % (path, exc))
    if size > limit:
        raise LimitExceeded(
            "File exceeds the configured size limit: %s (%d bytes > %d)" % (path, size, limit),
            hint="Raise limits.max_file_bytes in the config or split the artifact.",
            details={"path": path, "bytes": size, "limit": limit},
        )
    return size


def read_bytes(path, retries=2, limits=None):
    limits = limits or DEFAULT_LIMITS
    if not os.path.exists(path):
        raise InputError(
            "Input file not found: %s" % path,
            hint="Check the path, or use --input with an existing file or directory.",
        )
    if os.path.isdir(path):
        raise InputError("Expected a file, found a directory: %s" % path)
    check_size(path, limits)
    last = None
    for attempt in range(retries + 1):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError as exc:  # transient locks, network shares
            last = exc
            time.sleep(0.15 * (attempt + 1))
    raise InputError(
        "Unable to read %s after %d attempts: %s" % (path, retries + 1, last),
        hint="Close the file if another process holds a lock, then retry.",
    )


def read_text(path, limits=None, encoding="utf-8"):
    raw = read_bytes(path, retries=int((limits or DEFAULT_LIMITS).get("read_retries", 2)), limits=limits)
    max_chars = int((limits or DEFAULT_LIMITS).get("max_text_chars", DEFAULT_LIMITS["max_text_chars"]))
    for enc in (encoding, "utf-8-sig", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        raise InputError("Unable to decode %s" % path)
    if len(text) > max_chars:
        raise LimitExceeded(
            "Decoded text exceeds limits.max_text_chars (%d > %d) for %s" % (len(text), max_chars, path),
            hint="Raise limits.max_text_chars or analyse a smaller slice.",
        )
    return text


def atomic_write(path, data, encoding="utf-8"):
    """Write bytes/str atomically so interrupted runs never leave partial files."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    if isinstance(data, str):
        data = data.encode(encoding)
    fd, tmp = tempfile.mkstemp(prefix=".a11yl10n-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def cleanup(paths):
    for p in paths:
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass


def slugify(text, maxlen=72):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:maxlen].strip("-") or "item"


def truncate(text, n=140):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "\u2026"


# --------------------------------------------------------------------------- #
# Run context
# --------------------------------------------------------------------------- #
class InputDoc(object):
    """A loaded input artifact plus provenance for the evidence record."""

    def __init__(self, path, kind, size, digest, text=None, data=None, meta=None):
        self.path = path
        self.kind = kind
        self.size = size
        self.digest = digest
        self.text = text
        self.data = data
        self.meta = meta or {}

    def record(self):
        return {
            "path": self.path,
            "kind": self.kind,
            "bytes": self.size,
            "sha256": self.digest,
            "meta": self.meta,
        }


class Context(object):
    def __init__(self, skill, op, params, limits, config, assets, ruleset=None):
        self.skill = skill
        self.op = op
        self.params = params
        self.limits = limits
        self.config = config
        self.assets = assets
        self.ruleset = ruleset or {}
        self.findings = []
        self.extras = {}
        self.docs = []
        self.notes = []
        self.checks_run = 0
        self.checks_passed = 0
        self.started = time.time()
        self._deadline = self.started + float(limits.get("timeout_seconds", 120))
        self._cancelled = False

    # -- time / control ---------------------------------------------------- #
    def checkpoint(self):
        if self._cancelled:
            raise DeadlineExceeded("Run cancelled by operator.")
        if time.time() > self._deadline:
            raise DeadlineExceeded(
                "Run exceeded limits.timeout_seconds (%ss)." % self.limits.get("timeout_seconds"),
                hint="Raise limits.timeout_seconds, or narrow the input with --input.",
            )

    def check_checks(self):
        self.checks_run += 1

    def pass_check(self):
        self.checks_run += 1
        self.checks_passed += 1

    def note(self, message, level="info"):
        self.notes.append({"level": level, "message": message})

    def meta(self, key, value):
        self.extras[key] = value

    # -- findings ---------------------------------------------------------- #
    def add(self, rule, message, severity="medium", criterion=None, level=None,
            evidence=None, remediation=None, retest=None, refs=None, locator=None,
            rule_id=None, category=None, tags=None):
        if len(self.findings) >= int(self.limits.get("max_findings", 5000)):
            raise LimitExceeded(
                "Findings exceeded limits.max_findings (%s)." % self.limits.get("max_findings"),
                hint="Fix the highest-severity findings first, then re-run.",
            )
        self.checks_run += 1
        fid = len(self.findings) + 1
        self.findings.append({
            "id": "F%04d" % fid,
            "rule": rule_id or rule,
            "check": rule,
            "category": category or self.skill["category"]["name"],
            "severity": severity if severity in SEVERITIES else "medium",
            "wcag": criterion if criterion else None,
            "level": level,
            "locator": locator,
            "message": message,
            "evidence": evidence,
            "remediation": remediation or "Apply the correction defined for this check and re-run the same command.",
            "retest": retest or "Re-run the same command on the corrected artifact and confirm the finding is absent.",
            "refs": refs or [],
            "tags": tags or [],
        })
        return self.findings[-1]

    def pass_if(self, condition, *args, **kwargs):
        if condition:
            self.checks_run += 1
            self.checks_passed += 1
            return True
        self.add(*args, **kwargs)
        return False

    # -- result ------------------------------------------------------------ #
    def summary(self):
        by_sev = {s: 0 for s in SEVERITIES}
        by_wcag = {}
        for f in self.findings:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            if f.get("wcag"):
                by_wcag[f["wcag"]] = by_wcag.get(f["wcag"], 0) + 1
        blocking = sum(1 for f in self.findings if f["severity"] in ("critical", "high"))
        total = len(self.findings)
        checks = max(self.checks_run, 1)
        score = round(max(0.0, (self.checks_run - total) / float(checks)), 4)
        human = sorted({f["wcag"] for f in self.findings
                        if f["severity"] in ("critical", "high") and f.get("wcag")})
        return {
            "findings": total,
            "by_severity": by_sev,
            "blocking": blocking,
            "criteria_affected": by_wcag,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "evidence_score": score,
            "pass": blocking == 0,
            "automated_claim_limit": (
                "Automated checks do not establish conformance. Disabled-user testing, "
                "assistive-technology testing and qualified linguistic review remain required."
            ),
            "human_review_queue": human,
        }


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #
SEV_ORDER = {s: i for i, s in enumerate(reversed(SEVERITIES))}


def _md_escape(text):
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(result):
    run = result["run"]
    sk = result["skill"]
    s = result["summary"]
    out = io.StringIO()
    w = out.write
    w("# %s\n\n" % sk["name"])
    w("**Category:** %s  \n" % sk["category"]["name"])
    w("**Operation:** `%s`  \n" % result["op"])
    w("**Skill id:** `%s`  \n" % sk["id"])
    w("**Status:** %s  \n" % run["status"])
    w("**Generated:** %s  \n" % run["finished_at"])
    w("**Tool:** accessibility-and-localization %s (result schema %s)\n\n" % (run["tool_version"], run["schema"]))
    w("## Verdict\n\n")
    w("- Findings: **%d** (critical %d / high %d / medium %d / low %d / info %d)\n" % (
        s["findings"], s["by_severity"]["critical"], s["by_severity"]["high"], s["by_severity"]["medium"],
        s["by_severity"]["low"], s["by_severity"]["info"]))
    w("- Checks run: %d, passed: %d, evidence score: %.2f\n" % (s["checks_run"], s["checks_passed"], s["evidence_score"]))
    w("- Gate result for this operation: **%s**\n" % ("PASS (no blocking findings)" if s["pass"] else "FAIL (blocking findings present)"))
    for n in result.get("notes", []):
        w("- Note (%s): %s\n" % (n["level"], n["message"]))
    w("\n> %s\n\n" % s["automated_claim_limit"])

    if result.get("parameters"):
        w("## Parameters\n\n")
        for k, v in sorted(result["parameters"].items()):
            w("- `%s` = `%s`\n" % (k, v))
        w("\n")

    if result.get("inputs"):
        w("## Inputs and evidence hashes\n\n")
        w("| Path | Kind | Bytes | SHA-256 |\n|---|---|---|---|\n")
        for d in result["inputs"]:
            w("| `%s` | %s | %d | `%s` |\n" % (d["path"], d["kind"], d["bytes"], d["sha256"][:32] + "\u2026"))
        w("\n")

    if result["findings"]:
        w("## Findings\n\n")
        w("| # | Severity | Check | WCAG | Location | Finding | Remediation | Retest |\n")
        w("|---|---|---|---|---|---|---|---|\n")
        for f in sorted(result["findings"], key=lambda x: (-SEVERITY_RANK[x["severity"]], x["id"])):
            w("| %s | %s | %s | %s | %s | %s | %s | %s |\n" % (
                f["id"], f["severity"].upper(), _md_escape(f["check"]),
                f.get("wcag") or "-", _md_escape(f.get("locator") or "-"),
                _md_escape(f["message"]), _md_escape(f["remediation"]), _md_escape(f["retest"])))
        w("\n")
        w("### Evidence detail\n\n")
        for f in result["findings"]:
            w("<details><summary>%s \u2014 %s (%s)</summary>\n\n" % (f["id"], _md_escape(f["check"]), f["severity"]))
            w("- Rule: `%s`\n" % f["rule"])
            if f.get("wcag"):
                w("- Success criterion / rule: %s%s\n" % (f["wcag"], " (Level %s)" % f["level"] if f.get("level") else ""))
            if f.get("evidence") is not None:
                w("- Evidence: `%s`\n" % json.dumps(f["evidence"], ensure_ascii=False)[:1200])
            for r in f.get("refs") or []:
                w("- Reference: %s\n" % r)
            w("\n</details>\n\n")
    else:
        w("## Findings\n\nNo findings for the checks selected by this skill.\n\n")

    extras = result.get("extras") or {}
    if extras:
        w("## Operation output\n\n")
        for key, val in extras.items():
            if isinstance(val, (dict, list)):
                w("### %s\n\n```json\n%s\n```\n\n" % (key, json.dumps(val, indent=2, ensure_ascii=False)[:6000]))
            else:
                w("- **%s:** %s\n" % (key, val))
        w("\n")

    if result.get("approval"):
        w("## Approval record\n\n")
        for k, v in result["approval"].items():
            w("- %s: %s\n" % (k, v))
        w("\n")
    w("---\n\nGenerated by the accessibility-and-localization Skill Tool. "
      "Automated output is evidence, not a conformance claim.\n")
    return out.getvalue()


def render_sarif(result):
    sk = result["skill"]
    rules = {}
    results = []
    for f in result["findings"]:
        rid = f["rule"]
        if rid not in rules:
            rules[rid] = {
                "id": rid,
                "name": f["check"],
                "shortDescription": {"text": f["check"]},
                "help": {"text": f["remediation"]},
                "properties": {"wcag": f.get("wcag"), "tags": f.get("tags") or []},
            }
        results.append({
            "ruleId": rid,
            "level": {"critical": "error", "high": "error", "medium": "warning",
                      "low": "note", "info": "none"}.get(f["severity"], "warning"),
            "message": {"text": f["message"]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": (f.get("evidence") or {}).get("artifact")
                        or (result["inputs"][0]["path"] if result.get("inputs") else "unknown")
                    }
                }
            }],
            "properties": {"severity": f["severity"], "wcag": f.get("wcag"),
                           "remediation": f["remediation"], "retest": f["retest"]},
        })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "accessibility-and-localization",
                "version": result["run"]["tool_version"],
                "informationUri": "https://www.w3.org/WAI/WCAG22/",
                "rules": list(rules.values()),
            }},
            "results": results,
            "invocations": [{"executionSuccessful": result["run"]["status"] == "ok"}],
            "properties": {"skill": sk["name"], "skillId": sk["id"], "category": sk["category"]["name"]},
        }],
    }


def _xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def render_junit(result):
    s = result["summary"]
    sk = result["skill"]
    from xml.etree import ElementTree as ET

    suite = ET.Element("testsuite", {
        "name": "%s [%s]" % (sk["name"], sk["id"]),
        "tests": str(max(s["checks_run"], 1)),
        "failures": str(len(result["findings"])),
        "errors": "1" if result["run"]["status"] == "error" else "0",
        "skipped": "0",
        "time": "%.3f" % (result["run"]["duration_ms"] / 1000.0),
    })
    for f in result["findings"]:
        tc = ET.SubElement(suite, "testcase", {
            "classname": sk["category"]["name"], "name": f["check"]})
        fail = ET.SubElement(tc, "failure", {
            "type": f["severity"], "message": truncate(f["message"], 200)})
        fail.text = "%s\nLocator: %s\nWCAG: %s\nRemediation: %s\nRetest: %s" % (
            f["message"], f.get("locator"), f.get("wcag"), f["remediation"], f["retest"])
    buf = io.BytesIO()
    ET.ElementTree(suite).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_result(ctx, status="ok", error=None):
    duration = time.time() - ctx.started
    result = {
        "schema": RESULT_SCHEMA,
        "skill": {
            "id": ctx.skill["id"],
            "name": ctx.skill["name"],
            "slug": ctx.skill["slug"],
            "category": {"number": ctx.skill["category"]["number"], "name": ctx.skill["category"]["name"]},
            "description": ctx.skill.get("description", ""),
        },
        "op": ctx.op,
        "run": {
            "schema": RESULT_SCHEMA,
            "tool_version": TOOL_VERSION,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ctx.started)),
            "finished_at": now_iso(),
            "duration_ms": int(duration * 1000),
            "status": status,
            "dry_run": bool(ctx.params.get("_dry_run")),
            "limits": dict(ctx.limits),
            "ruleset": ctx.ruleset,
        },
        "parameters": {k: v for k, v in ctx.params.items() if not k.startswith("_")},
        "inputs": [d.record() for d in ctx.docs],
        "summary": ctx.summary(),
        "findings": ctx.findings,
        "extras": ctx.extras,
        "notes": ctx.notes,
    }
    if error is not None:
        result["error"] = error
    return result


def render_result(result, fmt):
    if fmt == "md":
        return render_markdown(result)
    if fmt == "sarif":
        return json.dumps(render_sarif(result), indent=2, ensure_ascii=False) + "\n"
    if fmt == "junit":
        return render_junit(result)
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


def tail_trace(limit=6):
    return "".join(traceback.format_exc().splitlines(True)[-limit:])
