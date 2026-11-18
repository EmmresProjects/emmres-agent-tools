"""Governance, research, QA/metrics, design-system and operations engines.

Categories served: 1 (Standards, Governance and Conformance), 22 (Human
Research, Usability and Participatory Testing), 23 (QA, CI/CD, Metrics and
Reporting), 24 (Design Systems, Components and Developer Enablement),
25 (Operations, Support, Procurement and Lifecycle).
"""

import datetime
import json
import os
import re
import time

S = None
T = None
H = None

OPS = {}


def init(deps):
    global S, T, H
    S = deps["support"]
    T = deps["text"]
    H = deps["html"]


def op(name, doc, params=None, accepts=("json",), engine="gov", approval=False, modes=None):
    def deco(fn):
        OPS[name] = {"name": name, "doc": doc, "params": params or {}, "accepts": list(accepts),
                     "summary": doc.split(".")[0], "fn": fn, "engine": engine, "groups": [],
                     "approval": approval, "modes": modes or []}
        return fn
    return deco


def _findings(ctx):
    data = ctx.docs[0].data
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        return data["findings"], data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"], data
    if isinstance(data, list):
        return data, {}
    raise S.InputError("Expected a findings document.",
                       hint="Pass a result document (with 'findings') or a list of finding objects.")


def _wcag_index(ctx):
    data = ctx.assets.get("wcag", {})
    idx = {}
    for sc in data.get("criteria", []):
        idx[sc["num"]] = sc
    return idx


# --------------------------------------------------------------------------- #
# Category 1: standards, conformance, registers, workflows
# --------------------------------------------------------------------------- #
@op("standards-map", "Map each finding to the applicable standards (WCAG 2.2 success criterion, EN 301 549 "
    "clause, Section 508 provision, RA 7277 reference) and produce a criterion-level coverage table.",
    params={"targets": {"type": "list", "default": None,
                        "doc": "Standards to map, e.g. wcag22,en301549,section508,ra7277."},
            "conformance_level": {"type": "enum", "values": ["a", "aa", "aaa"], "default": "aa",
                                  "doc": "Level filter for the WCAG table."}},
    accepts=("json",))
def run_standards_map(ctx):
    findings, doc = _findings(ctx)
    idx = _wcag_index(ctx)
    targets = ctx.params.get("targets") or ["wcag22", "en301549", "section508", "ra7277"]
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",")]
    level = ctx.params.get("conformance_level", "aa")
    rank = {"A": 1, "AA": 2, "AAA": 3}
    rows = []
    unmapped = 0
    for f in findings:
        criterion = str(f.get("wcag") or f.get("criterion") or "").strip()
        base = re.split(r"[,\s]+", criterion)[0] if criterion else ""
        sc = idx.get(base)
        if not sc and base:
            unmapped += 1
        entry = {
            "finding": f.get("id") or f.get("rule"),
            "rule": f.get("rule") or f.get("check"),
            "severity": f.get("severity"),
            "wcag": base or None,
            "wcag_title": sc.get("title") if sc else None,
            "wcag_level": sc.get("level") if sc else None,
            "mappings": {},
        }
        if sc:
            for target in targets:
                key = {"wcag22": "wcag22", "en301549": "en301549", "section508": "section508",
                       "ra7277": "philippines"}.get(target, target)
                entry["mappings"][target] = sc.get(key) or sc.get("en301549")
        rows.append(entry)
        if sc and rank.get(sc.get("level", "A"), 1) > rank.get(level.upper(), 2):
            ctx.note("Finding %s maps to a Level %s criterion, outside the %s target." % (
                entry["finding"], sc.get("level"), level.upper()))
    if unmapped:
        ctx.add(rule="standards-unmapped", message="%d finding(s) could not be mapped to a WCAG criterion." % unmapped,
                severity="medium", criterion="conformance",
                evidence={"examples": [r["finding"] for r in rows if not r["wcag"]][:10]},
                remediation="Tag each finding with the success criterion it violates.",
                retest="Re-run standards-map and confirm every finding maps.")
    else:
        ctx.pass_check()
    affected = sorted({r["wcag"] for r in rows if r["wcag"]})
    table = []
    for num in sorted(affected, key=lambda n: [int(p) for p in n.split(".") if p.isdigit()]):
        sc = idx.get(num, {})
        table.append({"criterion": num, "title": sc.get("title"), "level": sc.get("level"),
                      "findings": sum(1 for r in rows if r["wcag"] == num),
                      "en301549": sc.get("en301549"), "section508": sc.get("section508"),
                      "philippines": sc.get("philippines")})
    ctx.meta("findings", len(findings))
    ctx.meta("criteria_affected", table)
    ctx.meta("mappings", rows[:300])
    ctx.meta("standards_version", ctx.assets.get("wcag", {}).get("version"))


@op("conformance-report", "Generate a conformance report (VPAT 2.5 / ACR style) from findings and reviewer "
    "assertions, with per-criterion verdicts, evidence references and an explicit claim boundary.",
    params={"product": {"type": "str", "default": None, "doc": "Product or service name."},
            "version": {"type": "str", "default": None, "doc": "Product version evaluated."},
            "report_date": {"type": "str", "default": None, "doc": "Evaluation date (YYYY-MM-DD)."},
            "conformance_level": {"type": "enum", "values": ["a", "aa", "aaa"], "default": "aa",
                                  "doc": "Target level for the claim."},
            "mode": {"type": "enum", "values": ["draft", "publish"], "default": "draft",
                     "doc": "Publishing a claim requires --approve."},
            "assertions": {"type": "json", "default": None,
                           "doc": "Per-criterion manual results: [{\"num\":\"1.1.1\",\"status\":\"supports\",\"notes\":\"..\"}]"}},
    accepts=("json",), approval=True, modes=["publish"])
def run_conformance_report(ctx):
    findings, doc = _findings(ctx)
    wcag = ctx.assets.get("wcag", {})
    criteria = wcag.get("criteria", [])
    mode = ctx.params.get("mode", "draft")
    if mode == "publish" and not ctx.params.get("_approved"):
        raise S.ApprovalRequired("publish-conformance-claim",
                                 "Publishing a conformance claim is an irreversible, externally visible action.")
    level = ctx.params.get("conformance_level", "aa")
    rank = {"A": 1, "AA": 2, "AAA": 3}
    max_rank = rank[level.upper()]
    assertions = ctx.params.get("assertions") or []
    if isinstance(assertions, str):
        assertions = json.loads(assertions)
    assertion_map = {str(a.get("num")): a for a in assertions}
    by_criterion = {}
    for f in findings:
        crit = str(f.get("wcag") or f.get("criterion") or "").split(",")[0].strip()
        if crit:
            by_criterion.setdefault(crit, []).append(f)
    rows = []
    counts = {"supports": 0, "partially supports": 0, "does not support": 0, "not applicable": 0,
              "not evaluated": 0}
    for sc in criteria:
        if rank.get(sc["level"], 1) > max_rank:
            continue
        crit = sc["num"]
        hits = by_criterion.get(crit, [])
        manual = assertion_map.get(crit, {})
        if manual.get("status"):
            status = manual["status"]
        elif hits:
            blocking = [h for h in hits if (h.get("severity") or "").lower() in ("critical", "high")]
            status = "does not support" if blocking else "partially supports"
        elif sc.get("level") == "AAA" or sc.get("evaluated") is False:
            status = "not evaluated"
        else:
            status = "not evaluated"
        counts[status] = counts.get(status, 0) + 1
        rows.append({
            "criterion": crit, "title": sc.get("title"), "level": sc["level"], "status": status,
            "remarks": manual.get("notes") or (
                "%d finding(s) recorded; highest severity %s." % (
                    len(hits), max((h.get("severity") or "info" for h in hits),
                                   key=lambda s: S.SEVERITY_RANK.get(s, 0))) if hits else
                "No automated findings recorded; manual verification required."),
            "evidence": [h.get("id") or h.get("rule") for h in hits][:10],
            "en301549": sc.get("en301549"), "section508": sc.get("section508"),
        })
    report = {
        "standard": "WCAG 2.2 Level %s" % level.upper(),
        "report_type": "VPAT 2.5 accessibility conformance report (generated draft)",
        "product": ctx.params.get("product") or ctx.config.get("product", {}).get("name"),
        "product_version": ctx.params.get("version") or ctx.config.get("product", {}).get("version"),
        "report_date": ctx.params.get("report_date") or time.strftime("%Y-%m-%d"),
        "evaluation_methods": ["automated rule scanning", "manual keyboard review",
                               "assistive technology testing", "human linguistic review"],
        "claim_boundary": ("This report records the evidence available at the stated date. Automated results alone do "
                           "not establish conformance; unresolved exceptions are listed and must remain visible."),
        "mode": mode,
        "criteria": rows,
        "summary": counts,
        "unresolved_exceptions": [h for h in findings if (h.get("severity") or "") in ("critical", "high")][:50],
    }
    if counts["not evaluated"] and mode == "publish":
        raise S.ApprovalRequired(
            "publish-with-unevaluated",
            "Cannot publish: %d in-scope criteria have no recorded evaluation." % counts["not evaluated"])
    if counts["does not support"]:
        ctx.add(rule="conformance-failures", message="%d criteria are marked 'does not support'." % counts["does not support"],
                severity="high", criterion="conformance",
                evidence={"criteria": [r["criterion"] for r in rows if r["status"] == "does not support"]},
                remediation="Remediate or disclose these criteria in the report remarks before publishing.",
                retest="Re-run conformance-report and confirm the status improves.")
    else:
        ctx.pass_check()
    ctx.meta("report", report)
    ctx.meta("criteria_in_scope", len(rows))
    ctx.meta("status_counts", counts)
    ctx.note("Draft reports must be reviewed by the accessibility owner before publication.")


@op("policy-document", "Generate a governance document from validated inputs: accessibility policy, statement, "
    "definition of done, acceptance criteria or audit scope, with versioned metadata and review dates.",
    params={"kind": {"type": "enum", "values": ["policy", "statement", "definition-of-done",
                                                "acceptance-criteria", "audit-scope", "roadmap"],
                     "default": "statement", "doc": "Document type to generate."},
            "organisation": {"type": "str", "default": None, "doc": "Organisation name."},
            "contact": {"type": "str", "default": None, "doc": "Accessibility contact channel."},
            "review_months": {"type": "int", "default": 12, "doc": "Review interval in months."}},
    accepts=("json", "text"))
def run_policy_document(ctx):
    kind = ctx.params.get("kind", "statement")
    org = ctx.params.get("organisation") or ctx.config.get("product", {}).get("organisation") or "the organisation"
    contact = ctx.params.get("contact") or ctx.config.get("product", {}).get("contact") or "the accessibility team"
    level = ctx.config.get("conformance_level", "aa").upper()
    focus = []
    if ctx.docs and ctx.docs[0].kind == "json":
        data = ctx.docs[0].data
        if isinstance(data, dict):
            focus = data.get("focus_areas") or data.get("requirements") or []
            org = data.get("organisation") or org
            contact = data.get("contact") or contact
    elif ctx.docs:
        focus = [l.strip() for l in ctx.docs[0].text.splitlines() if l.strip()][:20]
    templates = {
        "policy": [
            ("Purpose", "%s commits to digital products that are usable by people with disabilities and by users of every supported language." % org),
            ("Scope", "All customer-facing web, mobile, document and media deliverables, including localized variants."),
            ("Standard", "WCAG 2.2 Level %s as the conformance target, with EN 301 549 and RA 7277 obligations mapped where applicable." % level),
            ("Roles", "Product owner (accountable), engineering (implementation), design (inclusive design), accessibility lead (evidence and gate), linguists (language quality)."),
            ("Requirements", "Every release produces automated evidence, manual keyboard and screen-reader evidence, and qualified language review."),
            ("Exceptions", "Time-boxed waivers require a named owner, a remediation date within 90 days, and an accessible alternative."),
            ("Review", "Policy reviewed every %d months or on any material standards change." % int(ctx.params.get("review_months", 12))),
        ],
        "statement": [
            ("Commitment", "%s aims to meet WCAG 2.2 Level %s across its digital services." % (org, level)),
            ("Measures", "Automated rule scanning in CI, manual keyboard and screen-reader testing, and bilingual review for Filipino and English content."),
            ("Conformance status", "Partially conformant: some content may not yet meet the target; known limitations are listed in the report."),
            ("Feedback", "Report a barrier through %s. We aim to acknowledge within 5 working days." % contact),
            ("Enforcement", "Escalation path for unresolved complaints, including the relevant national accessibility authority where applicable."),
            ("Preparation", "Statement prepared using automated evidence plus human evaluation on the reported date."),
        ],
        "definition-of-done": [
            ("Automated", "Zero critical or high automated findings for in-scope templates."),
            ("Keyboard", "Every task completed with keyboard only; visible focus throughout; no traps."),
            ("Screen reader", "Task script executed with at least one desktop and one mobile screen reader."),
            ("Visual", "Text contrast, reflow at 320px, 200% zoom and text-spacing overrides pass."),
            ("Content", "Plain-language target met; captions and alt text reviewed by a human."),
            ("Localization", "No placeholder drift, no truncation, locale formats verified, terminology enforced."),
        ],
        "acceptance-criteria": [
            ("Given/When/Then", "Given a keyboard-only user, when they tab through the page, then every interactive element is reachable and clearly focused."),
            ("Forms", "Given a validation error, when it is triggered, then the error is announced and programmatically associated with the field."),
            ("Media", "Given a video, when captions are enabled, then cues are synchronized, complete and within reading-speed limits."),
            ("Localization", "Given a translated page, when the locale changes, then lang, dir, formats and terminology all follow the locale profile."),
        ],
        "audit-scope": [
            ("Templates", "Representative templates for each page archetype, including authenticated and error states."),
            ("Platforms", "Desktop web, mobile web, and the supported native shells."),
            ("Assistive technology", "NVDA with Firefox or Chrome, JAWS with Chrome, VoiceOver on macOS and iOS, TalkBack on Android."),
            ("Locales", "Default locale plus the highest-traffic locale and one RTL locale when supported."),
            ("Evidence", "Each sample records artifact, environment, AT/locale, steps, criterion, severity, owner and retest."),
        ],
        "roadmap": [
            ("Now", "Fix critical automated findings and establish the release gate."),
            ("Next", "Add screen-reader regression scripts and localization QA automation."),
            ("Later", "Participatory testing with disabled users and in-country reviewers each quarter."),
        ],
    }
    sections = templates.get(kind)
    if not sections:
        raise S.ParamError("Unknown document kind %r." % kind, hint="Choose one of: %s." % ", ".join(templates))
    body = ["# %s: %s" % (kind.replace("-", " ").title(), org), "",
            "Version 1.0 | Generated %s | Review every %d months" % (
                time.strftime("%Y-%m-%d"), int(ctx.params.get("review_months", 12))), ""]
    for title, text in sections:
        body.append("## %s" % title)
        body.append("")
        body.append(text)
        body.append("")
    if focus:
        body.append("## Focus areas")
        body.append("")
        for item in focus[:20]:
            body.append("- %s" % (item if isinstance(item, str) else json.dumps(item)))
        body.append("")
    body.append("---")
    body.append("Template output. A named owner must approve the statement before publication.")
    markdown = "\n".join(body) + "\n"
    ctx.meta("kind", kind)
    ctx.meta("document", markdown)
    ctx.meta("tokens_estimate", len(markdown.split()))
    ctx.pass_check()


@op("register", "Maintain a versioned governance register (risk, debt, ownership, waiver, exception) as an "
    "append-only JSONL ledger with hash chaining, ownership, status and aging analysis.",
    params={"register_path": {"type": "str", "default": None, "doc": "Path to the JSONL register."},
            "register_kind": {"type": "enum", "values": ["risk", "debt", "ownership", "waiver", "exception"],
                              "default": "risk", "doc": "Register type being operated on."},
            "mode": {"type": "enum", "values": ["read", "append", "update", "verify"], "default": "read",
                     "doc": "Read and verify are safe; append and update require --approve."},
            "entries": {"type": "json", "default": None,
                        "doc": "Entries to append/update: [{\"id\":..,\"title\":..,\"owner\":..}]"}},
    accepts=("json", "jsonl"), approval=True, modes=["append", "update"])
def run_register(ctx):
    path = ctx.params.get("register_path") or ctx.config.get("paths", {}).get("register")
    mode = ctx.params.get("mode", "read")
    kind = ctx.params.get("register_kind", "risk")
    if mode in ("append", "update") and not ctx.params.get("_approved"):
        raise S.ApprovalRequired("register-%s" % mode,
                                 "Writing to the %s register changes governance state." % kind)
    entries = ctx.params.get("entries")
    if isinstance(entries, str):
        entries = json.loads(entries)
    existing = []
    if path and os.path.exists(path):
        size = S.check_size(path, ctx.limits)
        for i, line in enumerate(S.read_text(path, limits=ctx.limits).splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                ctx.add(rule="register-corrupt-line", message="Register line %d is not valid JSON." % (i + 1),
                        severity="critical", criterion="register-integrity", locator="line %d" % (i + 1),
                        remediation="Restore the register from the last verified backup.",
                        retest="Re-run register --mode verify and confirm the chain validates.")
    required = ("id", "title", "owner", "status")
    chain_ok = True
    prev = None
    for e in existing:
        for field in required:
            if field not in e:
                ctx.add(rule="register-schema", message="Entry %r is missing required field %r." % (
                    e.get("id"), field), severity="high", criterion="register-integrity",
                    evidence={"entry": e},
                    remediation="Add the missing field; every entry needs id, title, owner and status.",
                    retest="Re-run and confirm the schema check passes.")
        if prev is not None and e.get("prev_sha256") and e["prev_sha256"] != prev:
            chain_ok = False
            ctx.add(rule="register-chain", message="Hash chain broken at entry %r." % e.get("id"),
                    severity="critical", criterion="register-integrity",
                    remediation="Treat the register as tampered; restore from a verified backup.",
                    retest="Re-run register --mode verify and confirm the chain validates.")
        prev = e.get("entry_sha256") or prev
    if chain_ok:
        ctx.pass_check()
    if mode in ("append", "update"):
        if not path:
            raise S.ParamError("register_path is required to write.", hint="Set --set register_path=../register.jsonl")
        by_id = {e.get("id"): e for e in existing}
        for new in entries or []:
            for field in required:
                if field not in new:
                    raise S.ParamError("New entry is missing %r." % field,
                                       hint="Entries need id, title, owner and status.")
            prev_hash = existing[-1].get("entry_sha256") if existing else None
            record = dict(new)
            record["register_kind"] = kind
            record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            record["updated_by"] = ctx.config.get("product", {}).get("operator", "unspecified-operator")
            record["prev_sha256"] = prev_hash
            record["entry_sha256"] = S.sha256_text(json.dumps(
                {k: v for k, v in record.items() if k != "entry_sha256"}, sort_keys=True))
            existing.append(record)
            by_id[record["id"]] = record
        payload = "".join(json.dumps(e, sort_keys=True) + "\n" for e in existing)
        written = S.atomic_write(path, payload)
        ctx.meta("written", written)
        ctx.meta("entries_written", len(entries or []))
    # analysis
    open_items = [e for e in existing if str(e.get("status", "")).lower() not in ("closed", "done", "verified")]
    today = time.strftime("%Y-%m-%d")
    aged = []
    for e in open_items:
        opened = str(e.get("opened") or e.get("created_at") or "")[:10]
        age = None
        if re.match(r"\d{4}-\d{2}-\d{2}", opened):
            age = (time.mktime(time.strptime(today, "%Y-%m-%d")) -
                   time.mktime(time.strptime(opened, "%Y-%m-%d"))) / 86400.0
        aged.append({"id": e.get("id"), "title": e.get("title"), "owner": e.get("owner"),
                     "severity": e.get("severity"), "age_days": int(age) if age is not None else None,
                     "waiver_expires": e.get("waiver_expires")})
        if age is not None and age > float(ctx.config.get("thresholds", {}).get("register_max_age_days", 90)):
            ctx.add(rule="register-aging", message="Entry %r has been open %d days." % (e.get("id"), int(age)),
                    severity="medium", criterion="governance", locator=str(e.get("id")),
                    evidence={"age_days": int(age), "owner": e.get("owner")},
                    remediation="Escalate to the owner or record an approved exception with an expiry date.",
                    retest="Re-run register --mode read and confirm the age is within threshold.")
        expires = str(e.get("waiver_expires") or "")[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}", expires) and expires <= today:
            ctx.add(rule="waiver-expired", message="Waiver %r expired on %s." % (e.get("id"), expires),
                    severity="critical", criterion="governance", locator=str(e.get("id")),
                    remediation="Remediate or re-approve the waiver with a new expiry date.",
                    retest="Re-run and confirm no expired waiver remains.")
    owners = {}
    for e in open_items:
        owners[e.get("owner") or "unassigned"] = owners.get(e.get("owner") or "unassigned", 0) + 1
    ctx.meta("register_path", path)
    ctx.meta("register_kind", kind)
    ctx.meta("mode", mode)
    ctx.meta("entries", len(existing))
    ctx.meta("open", len(open_items))
    ctx.meta("by_owner", owners)
    ctx.meta("items", aged[:300])
    ctx.meta("chain_verified", chain_ok)


@op("workflow", "Evaluate a governance workflow (exception, complaint, accommodation, waiver) against its states, "
    "required evidence, decision points, escalation rules and completion criteria.",
    params={"workflow": {"type": "enum", "values": ["exception", "complaint", "accommodation", "waiver"],
                         "default": "exception", "doc": "Workflow definition to apply."},
            "state": {"type": "str", "default": None, "doc": "Current state of the case."},
            "events": {"type": "json", "default": None, "doc": "Case history events."}},
    accepts=("json",))
def run_workflow(ctx):
    defs = {
        "exception": {
            "states": ["requested", "assessed", "approved", "active", "expiring", "closed", "rejected"],
            "transitions": {"requested": ["assessed", "rejected"], "assessed": ["approved", "rejected"],
                            "approved": ["active"], "active": ["expiring", "closed"],
                            "expiring": ["active", "closed"], "rejected": ["closed"], "closed": []},
            "required": {"requested": ["requester", "criterion", "justification"],
                         "assessed": ["impact", "owner", "alternative"],
                         "approved": ["approver", "expiry_date", "alternative"],
                         "active": ["review_date"],
                         "closed": ["closure_evidence"]},
            "escalate_after_days": 30,
        },
        "complaint": {
            "states": ["received", "triaged", "investigating", "resolved", "escalated", "closed"],
            "transitions": {"received": ["triaged"], "triaged": ["investigating", "escalated"],
                            "investigating": ["resolved", "escalated"], "resolved": ["closed"],
                            "escalated": ["resolved", "closed"], "closed": []},
            "required": {"received": ["channel", "received_date", "summary"],
                         "triaged": ["severity", "owner", "sla"],
                         "resolved": ["resolution", "user_confirmation"]},
            "escalate_after_days": 5,
        },
        "accommodation": {
            "states": ["requested", "reviewing", "approved", "delivered", "closed", "declined"],
            "transitions": {"requested": ["reviewing"], "reviewing": ["approved", "declined"],
                            "approved": ["delivered"], "delivered": ["closed"],
                            "declined": ["closed"], "closed": []},
            "required": {"requested": ["requester", "need", "format"],
                         "reviewing": ["assessor", "privacy_notice"],
                         "approved": ["approver", "delivery_date"],
                         "delivered": ["delivery_evidence"]},
            "escalate_after_days": 10,
        },
        "waiver": {
            "states": ["draft", "submitted", "approved", "expired", "revoked"],
            "transitions": {"draft": ["submitted"], "submitted": ["approved", "revoked"],
                            "approved": ["expired", "revoked"], "expired": ["submitted"], "revoked": []},
            "required": {"submitted": ["criterion", "risk", "mitigation"],
                         "approved": ["approver", "expiry_date"]},
            "escalate_after_days": 90,
        },
    }
    definition = defs[ctx.params.get("workflow", "exception")]
    data = ctx.docs[0].data if ctx.docs[0].kind == "json" else {}
    case = data if isinstance(data, dict) else {"events": data}
    state = ctx.params.get("state") or case.get("state") or "requested"
    events = ctx.params.get("events") or case.get("events") or []
    if isinstance(events, str):
        events = json.loads(events)
    fields = set(case.keys())
    path_states = [state]
    for ev in events:
        frm = ev.get("from") or path_states[-1]
        to = ev.get("to")
        if not to:
            continue
        if to not in definition["transitions"].get(frm, []):
            ctx.add(rule="workflow-invalid-transition", message="Illegal transition %r -> %r." % (frm, to),
                    severity="high", criterion="workflow", locator=str(ev.get("at") or "event"),
                    evidence={"allowed": definition["transitions"].get(frm, [])},
                    remediation="Apply the permitted transition or reopen the case.",
                    retest="Re-run workflow and confirm the history is valid.")
        else:
            ctx.pass_check()
        path_states.append(to)
    current = path_states[-1]
    if current not in definition["states"]:
        ctx.add(rule="workflow-unknown-state", message="Unknown workflow state %r." % current,
                severity="high", criterion="workflow",
                remediation="Use one of %s." % ", ".join(definition["states"]),
                retest="Re-run and confirm the state is valid.")
    missing = [f for f in definition["required"].get(current, []) if f not in fields and f not in
               {k for ev in events for k in ev}]
    for field in missing:
        ctx.add(rule="workflow-missing-evidence", message="State %r requires %r." % (current, field),
                severity="high", criterion="workflow", locator=field,
                remediation="Collect the required evidence before advancing the case.",
                retest="Re-run workflow and confirm no missing evidence remains.")
    if not missing:
        ctx.pass_check()
    age_days = case.get("age_days")
    if isinstance(age_days, (int, float)) and age_days > definition["escalate_after_days"]:
        ctx.add(rule="workflow-escalation", message="Case is %d days old; escalation threshold is %d." % (
            age_days, definition["escalate_after_days"]), severity="high", criterion="workflow",
            remediation="Escalate to the named owner and record the escalation.",
            retest="Re-run and confirm the case is within its SLA.")
    else:
        ctx.pass_check()
    ctx.meta("workflow", ctx.params.get("workflow", "exception"))
    ctx.meta("state_path", path_states)
    ctx.meta("allowed_next", definition["transitions"].get(current, []))
    ctx.meta("required_evidence", definition["required"].get(current, []))


@op("severity-classify", "Classify findings by impact on users, task blockage, workaround availability, frequency "
    "and audience reach, producing severity plus remediation priority.",
    params={"severity_model": {"type": "enum", "values": ["wcag-block", "impact", "translation"],
                               "default": "impact", "doc": "Classification model."}},
    accepts=("json",))
def run_severity(ctx):
    findings, doc = _findings(ctx)
    weights = ctx.assets.get("severity", {}).get("impact_weights",
                                                 {"blocked": 5, "degraded": 3, "friction": 2, "cosmetic": 1})
    results = []
    for f in findings:
        ctx.checkpoint()
        impact = (f.get("impact") or f.get("blocking") or "degraded")
        impact = impact if isinstance(impact, str) else ("blocked" if impact else "degraded")
        impact = impact.lower()
        score = float(weights.get(impact, 3))
        if f.get("no_workaround"):
            score *= 2.0
        if f.get("assistive_technology_only"):
            score *= 1.3
        reach = float(f.get("reach_pct") or 50) / 100.0
        score *= (0.6 + reach)
        severity = ("critical" if score >= 8 else "high" if score >= 5 else
                    "medium" if score >= 3 else "low")
        priority = "P1" if severity == "critical" else "P2" if severity == "high" else \
                   "P3" if severity == "medium" else "P4"
        results.append({"finding": f.get("id") or f.get("rule"), "rule": f.get("rule"),
                        "input_severity": f.get("severity"), "impact": impact,
                        "score": round(score, 2), "severity": severity, "priority": priority,
                        "owner": f.get("owner")})
        if severity in ("critical", "high"):
            ctx.add(rule="severity-%s" % severity, message="%s is %s (score %.2f): %s" % (
                f.get("id") or f.get("rule"), severity, score, T.truncate(f.get("message") or "", 110)),
                severity=severity, criterion=f.get("wcag") or f.get("criterion"),
                locator=f.get("locator"), evidence={"impact": impact, "score": round(score, 2)},
                remediation=f.get("remediation") or "Remediate before release.",
                retest=f.get("retest") or "Re-run the source check and confirm the finding is cleared.")
        else:
            ctx.pass_check()
    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    results.sort(key=lambda r: (order[r["priority"]], -r["score"]))
    for i, r in enumerate(results, 1):
        r["order"] = i
    ctx.meta("classified", results)
    ctx.meta("counts", {p: sum(1 for r in results if r["priority"] == p) for p in ("P1", "P2", "P3", "P4")})


@op("remediation-plan", "Build a sequenced remediation plan from findings using severity, effort, dependency and "
    "ownership, with waves, capacity estimate and retest order.",
    params={"capacity_per_week": {"type": "int", "default": 5, "doc": "Findings the team can close per week."},
            "effort_field": {"type": "str", "default": "effort", "doc": "Field holding the effort estimate."}},
    accepts=("json",))
def run_remediation_plan(ctx):
    findings, doc = _findings(ctx)
    capacity = max(1, int(ctx.params.get("capacity_per_week", 5)))
    effort_field = ctx.params.get("effort_field", "effort")
    scored = []
    for f in findings:
        sev = (f.get("severity") or "medium").lower()
        effort = f.get(effort_field) or f.get("effort_days") or 1
        try:
            effort = float(effort)
        except (TypeError, ValueError):
            effort = 1.0
        severity_score = S.SEVERITY_RANK.get(sev, 3)
        priority = round(severity_score / max(effort, 0.5), 2)
        scored.append({"id": f.get("id") or f.get("rule"), "rule": f.get("rule"), "severity": sev,
                       "effort_days": effort, "owner": f.get("owner") or "unassigned",
                       "criterion": f.get("wcag"), "priority_score": priority,
                       "remediation": f.get("remediation"), "retest": f.get("retest")})
    scored.sort(key=lambda r: -r["priority_score"])
    wave = 1
    count = 0
    for r in scored:
        if count >= capacity:
            wave += 1
            count = 0
        r["wave"] = wave
        r["week"] = wave
        count += 1
    ctx.meta("plan", scored)
    ctx.meta("waves", wave)
    ctx.meta("total_effort_days", round(sum(r["effort_days"] for r in scored), 1))
    ctx.meta("capacity_per_week", capacity)
    blocked = [r for r in scored if r["severity"] in ("critical", "high")]
    for r in blocked:
        ctx.add(rule="plan-blocking", message="%s (%s) must be scheduled in wave %d." % (
            r["id"], r["severity"], r["wave"]), severity="high", criterion=r.get("criterion"),
            evidence=r, remediation=r["remediation"] or "Apply the documented remediation.",
            retest=r["retest"] or "Re-run the source check.")
    if not blocked:
        ctx.pass_check()


@op("maturity-assessment", "Score accessibility and localization maturity across governance, design, engineering, "
    "testing, content and operations, with a level, gap list and next-step recommendation.",
    params={"responses": {"type": "json", "default": None,
                          "doc": "Dimension scores 0-4: {\"governance\": 2, ...}"},
            "target_level": {"type": "int", "default": 3, "doc": "Level to reach next."}},
    accepts=("json",))
def run_maturity(ctx):
    model = ctx.assets.get("maturity", {})
    dimensions = model.get("dimensions") or ["governance", "design", "engineering", "testing", "content",
                                             "operations", "localization"]
    responses = ctx.params.get("responses")
    if isinstance(responses, str):
        responses = json.loads(responses)
    if not responses:
        data = ctx.docs[0].data if ctx.docs[0].kind == "json" else {}
        responses = (data.get("responses") if isinstance(data, dict) else None) or \
                    (data if isinstance(data, dict) else {})
    if not responses:
        raise S.InputError("No maturity responses supplied.",
                           hint="Provide {governance: 2, design: 1, ...} scores from 0 to 4.")
    scores = {}
    for dim in dimensions:
        raw = responses.get(dim)
        if raw is None:
            ctx.add(rule="maturity-missing", message="No score for dimension %r." % dim, severity="medium",
                    criterion="maturity", locator=dim,
                    remediation="Score the dimension or mark it out of scope.",
                    retest="Re-run and confirm every dimension is scored.")
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise S.ParamError("Score for %r is not numeric." % dim, hint="Use 0-4 integers.")
        if not 0 <= val <= 4:
            raise S.ParamError("Score for %r must be between 0 and 4." % dim, hint="0=none, 4=optimised.")
        scores[dim] = val
        ctx.pass_check()
    average = round(sum(scores.values()) / max(len(scores), 1), 2)
    level = "initial" if average < 1 else "developing" if average < 2 else "defined" if average < 3 else \
            "managed" if average < 3.6 else "optimised"
    gaps = sorted([{"dimension": d, "score": s, "gap": round(4 - s, 2)}
                   for d, s in scores.items() if s < int(ctx.params.get("target_level", 3))],
                  key=lambda g: -g["gap"])
    for g in gaps:
        ctx.add(rule="maturity-gap", message="%s scores %.1f/4 (gap %.1f)." % (g["dimension"], g["score"], g["gap"]),
                severity="medium", criterion="maturity", locator=g["dimension"],
                remediation=(model.get("recommendations", {}) or {}).get(g["dimension"],
                "Define the practice, assign an owner and add it to the release gate."),
                retest="Re-run maturity-assessment after the next quarterly review.")
    ctx.meta("assessment", {"scores": scores, "average": average, "level": level, "gaps": gaps})


@op("claim-validate", "Validate a conformance claim document: scope, standard and level, evaluation dates, "
    "methods, unresolved exceptions and evidence links, before publication.",
    params={"require_methods": {"type": "bool", "default": True, "doc": "Require named evaluation methods."},
            "require_exceptions_section": {"type": "bool", "default": True,
                                           "doc": "Require disclosure of unresolved exceptions."}},
    accepts=("json", "text"))
def run_claim_validate(ctx):
    doc = ctx.docs[0]
    if doc.kind == "json":
        claim = doc.data
    else:
        text = doc.text
        claim = {
            "standard": (re.search(r"WCAG\s*2\.[12]\s*Level\s*(A|AA|AAA)", text, re.I) or [None, None])[1]
            if re.search(r"WCAG\s*2\.[12]\s*Level\s*(A|AA|AAA)", text, re.I) else None,
            "date": (re.search(r"\d{4}-\d{2}-\d{2}", text).group(0) if re.search(r"\d{4}-\d{2}-\d{2}", text) else None),
            "methods": re.findall(r"(?i)(automated|manual|screen reader|keyboard|usability|user testing|captions review)[^.\n]{0,60}", text),
            "exceptions": re.findall(r"(?i)(known (?:limitation|issue)[^.\n]{0,120}|partially conformant|does not conform)", text),
            "scope": re.findall(r"(?i)(scope[^.\n]{0,160})", text),
            "contact": re.findall(r"(?i)(feedback[^.\n]{0,120}|contact[^.\n]{0,120})", text),
            "text": text[:4000],
        }
    checks = [
        (bool(claim.get("standard")), "claim-standard", "Claim does not state the standard and level.", "critical"),
        (bool(claim.get("date")), "claim-date", "Claim does not state the evaluation date.", "high"),
        (bool(claim.get("scope")), "claim-scope", "Claim does not define the scope evaluated.", "high"),
        (bool(claim.get("contact")), "claim-contact", "Claim provides no feedback or contact route.", "high"),
        (bool(claim.get("methods")) or not ctx.params.get("require_methods", True), "claim-methods",
         "Claim does not list the evaluation methods used.", "medium"),
        (bool(claim.get("exceptions")) or not ctx.params.get("require_exceptions_section", True), "claim-exceptions",
         "Claim does not disclose unresolved exceptions.", "critical"),
    ]
    for ok, rule_id, message, sev in checks:
        if ok:
            ctx.pass_check()
        else:
            ctx.add(rule=rule_id, message=message, severity=sev, criterion="conformance-claim",
                    evidence={"claim_keys": sorted(claim.keys())},
                    remediation="Add the missing element; a claim without it is unverifiable.",
                    retest="Re-run claim-validate and confirm the check passes.")
    standard = claim.get("standard")
    if standard and re.search(r"(?i)fully conformant|100% accessible|fully accessible", json.dumps(claim)):
        ctx.add(rule="claim-absolute", message="Claim uses an absolute statement that cannot be supported by automated evidence.",
                severity="critical", criterion="conformance-claim",
                remediation="Replace with 'partially conformant' plus the disclosed exceptions.",
                retest="Re-run and confirm no absolute claim remains.")
    else:
        ctx.pass_check()
    ctx.meta("claim", {k: v for k, v in claim.items() if k != "text"})
    ctx.meta("verdict", "publishable" if not any(not ok for ok, *_ in checks) else "not-publishable")


@op("risk-register-seed", "Seed a risk register from findings, assigning owners, likelihood, impact and mitigation "
    "for governance review.",
    params={"default_owner": {"type": "str", "default": "accessibility-lead", "doc": "Owner for unassigned risks."}},
    accepts=("json",))
def run_risk_seed(ctx):
    findings, doc = _findings(ctx)
    owner_default = ctx.params.get("default_owner", "accessibility-lead")
    risks = []
    for f in findings:
        sev = (f.get("severity") or "medium").lower()
        likelihood = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}.get(sev, "medium")
        impact = {"critical": "severe", "high": "major", "medium": "moderate", "low": "minor"}.get(sev, "moderate")
        risks.append({
            "id": "R-%03d" % (len(risks) + 1),
            "title": T.truncate(f.get("message") or f.get("rule") or "Untitled finding", 120),
            "source_finding": f.get("id") or f.get("rule"),
            "criterion": f.get("wcag") or f.get("criterion"),
            "likelihood": likelihood, "impact": impact,
            "owner": f.get("owner") or owner_default,
            "status": "open", "opened": time.strftime("%Y-%m-%d"),
            "mitigation": f.get("remediation") or "Apply the documented remediation.",
            "retest": f.get("retest") or "Re-run the source check.",
        })
    ctx.meta("risks", risks)
    ctx.meta("seed_count", len(risks))
    ctx.note("Seed output is not persisted; use register --mode append with --approve to store it.")


# --------------------------------------------------------------------------- #
# Category 22: human research
# --------------------------------------------------------------------------- #
@op("research-plan", "Generate a participatory accessibility research plan: sample size, participant profiles, "
    "sessions, accommodations, consent, task scripts and evidence handling.",
    params={"users": {"type": "int", "default": 5, "doc": "Planned participants per profile."},
            "profiles": {"type": "list", "default": None,
                         "doc": "Participant profiles, e.g. screen-reader,keyboard,low-vision,cognitive."},
            "sessions_per_user": {"type": "int", "default": 1, "doc": "Sessions per participant."},
            "remote": {"type": "bool", "default": True, "doc": "Whether sessions are remote."},
            "locale": {"type": "str", "default": None, "doc": "Session language/locale."}},
    accepts=("json", "text", "html"))
def run_research_plan(ctx):
    profiles = ctx.params.get("profiles") or ["screen-reader", "keyboard-only", "low-vision", "cognitive", "mobile"]
    if isinstance(profiles, str):
        profiles = [p.strip() for p in profiles.split(",")]
    users = int(ctx.params.get("users", 5))
    sessions = int(ctx.params.get("sessions_per_user", 1))
    locale = ctx.params.get("locale") or ctx.config.get("locale", {}).get("default_locale", "fil-PH")
    instruments = ctx.assets.get("at_matrix", {}).get("research_instruments", {})
    accommodation_map = ctx.assets.get("at_matrix", {}).get("accommodations", {})
    plan = {
        "locale": locale,
        "remote": bool(ctx.params.get("remote", True)),
        "participants": [{"profile": p, "count": users,
                          "accommodations": accommodation_map.get(p, ["confirm needs in screening"]),
                          "recruitment": instruments.get("recruitment", "Partner with a disability organisation; "
                                                                         "never recruit from the delivery team.")}
                         for p in profiles],
        "sessions_total": users * sessions * len(profiles),
        "session_length_minutes": instruments.get("session_minutes", 60),
        "consent": {
            "accessible_formats": ["screen-reader friendly HTML", "large print", "plain-language summary"],
            "elements": ["purpose", "what is recorded", "right to stop", "compensation", "data retention",
                          "privacy contact"],
            "compensation": instruments.get("compensation", "Compensate at or above local market rate; "
                                                             "pay even if the session ends early."),
        },
        "tasks": [
            {"task": "Find the information about X", "success": "reaches X without assistance"},
            {"task": "Complete the form", "success": "submits successfully with correct error recovery"},
            {"task": "Use the media player", "success": "plays media with captions/description enabled"},
            {"task": "Recover from an error", "success": "understands the error and completes the step"},
        ],
        "metrics": ["task success", "time on task", "assistance required", "error count",
                    "satisfaction (1-7)", "single ease question"],
        "evidence": ["transcript", "session note per observation", "severity rating",
                     "environment (AT, browser, OS, locale)", "clip or screenshot with consent"],
        "safety": {
            "privacy": "Collect only what the research question needs; store recordings in access-controlled storage.",
            "accessibility": "All materials and scheduling flows must themselves pass the accessibility gate.",
            "stop_criteria": "Stop the session if the participant is distressed or if a defect puts their data at risk.",
        },
    }
    ctx.meta("plan", plan)
    ctx.meta("sessions_total", plan["sessions_total"])
    ctx.note("Five participants per profile is a heuristic for qualitative saturation, not a statistical sample.")


@op("sus-score", "Score System Usability Scale responses (10 items, 1-5) into SUS scores with mean, distribution "
    "and a plain-language adjective band.",
    params={"scale_max": {"type": "int", "default": 5, "doc": "Maximum value of the response scale."},
            "benchmark": {"type": "float", "default": 68.0, "doc": "Industry average SUS score."}},
    accepts=("json",))
def run_sus(ctx):
    data = ctx.docs[0].data
    responses = data.get("responses") if isinstance(data, dict) else data
    if not isinstance(responses, list) or not responses:
        raise S.InputError("No SUS responses supplied.",
                           hint="Provide {\"responses\":[[5,1,4,...10 items]]} or a list of 10-item lists.")
    scale_max = int(ctx.params.get("scale_max", 5))
    scores = []
    for i, resp in enumerate(responses):
        if not isinstance(resp, list) or len(resp) != 10:
            raise S.InputError("Response set %d does not have exactly 10 items." % (i + 1),
                              hint="SUS requires all ten items; do not submit partial questionnaires.")
        for v in resp:
            if not isinstance(v, (int, float)) or not 1 <= v <= scale_max:
                raise S.ParamError("Response set %d has a value outside 1..%d." % (i + 1, scale_max))
        total = 0
        for idx, v in enumerate(resp, start=1):
            total += (v - 1) if idx % 2 == 1 else (scale_max - v)
        scores.append(round(total * (100.0 / (scale_max * 5)), 2))
        ctx.pass_check()
    mean = round(sum(scores) / len(scores), 2)
    band = ("excellent" if mean >= 85 else "good" if mean >= 72 else "acceptable" if mean >= 68 else
            "poor" if mean >= 51 else "awful")
    benchmark = float(ctx.params.get("benchmark", 68.0))
    if mean < benchmark:
        ctx.add(rule="sus-below-benchmark", message="Mean SUS %.2f is below the %.2f benchmark." % (mean, benchmark),
                severity="medium", criterion="usability", evidence={"scores": scores},
                remediation="Investigate the lowest-scoring items with participants before redesign.",
                retest="Re-run sus-score on the next round and compare the mean.")
    else:
        ctx.pass_check()
    ctx.meta("scores", scores)
    ctx.meta("mean", mean)
    ctx.meta("min", min(scores))
    ctx.meta("max", max(scores))
    ctx.meta("band", band)
    ctx.meta("participants", len(scores))


@op("findings-synthesis", "Synthesise observation notes into themes with frequency, severity, affected participants "
    "and prioritised recommendations.",
    params={"min_participants": {"type": "int", "default": 2, "doc": "Occurrences before a theme is promoted."}},
    accepts=("json",))
def run_synthesis(ctx):
    data = ctx.docs[0].data
    observations = data.get("observations") if isinstance(data, dict) else data
    if not isinstance(observations, list) or not observations:
        raise S.InputError("No observations supplied.",
                           hint="Provide [{\"participant\":\"P1\",\"theme\":\"focus lost\",\"severity\":\"high\",\"note\":\"..\"}].")
    min_p = int(ctx.params.get("min_participants", 2))
    themes = {}
    for ob in observations:
        ctx.checkpoint()
        theme = T.norm_ws(ob.get("theme") or ob.get("category") or "unclassified")
        entry = themes.setdefault(theme.lower(), {"theme": theme, "participants": set(), "notes": [],
                                                  "severities": [], "criteria": set()})
        entry["participants"].add(str(ob.get("participant") or ob.get("session") or "unknown"))
        entry["notes"].append(T.truncate(ob.get("note") or ob.get("observation") or "", 200))
        entry["severities"].append((ob.get("severity") or "medium").lower())
        if ob.get("wcag") or ob.get("criterion"):
            entry["criteria"].add(str(ob.get("wcag") or ob.get("criterion")))
    ranked = []
    for key, e in themes.items():
        sev_rank = max((S.SEVERITY_RANK.get(s, 3) for s in e["severities"]), default=3)
        score = len(e["participants"]) * 2 + sev_rank
        ranked.append({"theme": e["theme"], "participants": len(e["participants"]),
                       "occurrences": len(e["notes"]), "severity": max(e["severities"], key=lambda s: S.SEVERITY_RANK.get(s, 0)),
                       "criteria": sorted(e["criteria"]), "priority_score": score,
                       "sample_notes": e["notes"][:3]})
    ranked.sort(key=lambda r: -r["priority_score"])
    for r in ranked:
        if r["participants"] >= min_p or r["severity"] in ("critical", "high"):
            ctx.add(rule="research-theme", message="Theme %r reported by %d participant(s) (%s)." % (
                r["theme"], r["participants"], r["severity"]), severity=r["severity"] if r["severity"] in S.SEVERITIES else "medium",
                criterion=(r["criteria"][0] if r["criteria"] else None),
                evidence=r, remediation="Address the theme in the design or implementation backlog.",
                retest="Re-test the same task with the same participant profile after the fix.")
        else:
            ctx.pass_check()
    ctx.meta("themes", ranked)
    ctx.meta("observations", len(observations))
    ctx.meta("participants", len({str(o.get("participant") or o.get("session")) for o in observations}))


# --------------------------------------------------------------------------- #
# Category 23: QA, CI/CD, metrics, reporting
# --------------------------------------------------------------------------- #
@op("test-plan", "Generate a combined accessibility and localization test plan for a release: scope, environments, "
    "criteria, tasks, evidence and exit conditions.",
    params={"release": {"type": "str", "default": None, "doc": "Release identifier."},
            "locales": {"type": "list", "default": None, "doc": "Locales in scope."},
            "include_localization": {"type": "bool", "default": True, "doc": "Include localization coverage."}},
    accepts=("json", "text", "html"))
def run_test_plan(ctx):
    release = ctx.params.get("release") or ctx.config.get("product", {}).get("version") or "unreleased"
    locales = ctx.params.get("locales") or ctx.assets.get("locale_profiles", {}).get("default_locales") or ["en-PH", "fil-PH"]
    if isinstance(locales, str):
        locales = [l.strip() for l in locales.split(",")]
    wcag = ctx.assets.get("wcag", {})
    aa = [c for c in wcag.get("criteria", []) if c.get("level") in ("A", "AA")]
    matrix = ctx.assets.get("at_matrix", {})
    plan = {
        "release": release,
        "generated": time.strftime("%Y-%m-%d"),
        "standard": "WCAG 2.2 Level AA plus locale profile checks",
        "scope": {
            "web": ["representative template per archetype", "authenticated flows", "error and empty states"],
            "documents": ["one artifact per generated format"],
            "media": ["captioned video sample", "audio with transcript"],
        },
        "environments": matrix.get("combinations", [])[:8],
        "criteria_in_scope": {"count": len(aa), "sample": [c["num"] for c in aa[:12]]},
        "locales": locales,
        "test_types": [
            {"type": "automated", "owner": "engineering", "evidence": "rule-set report with hashes"},
            {"type": "manual keyboard", "owner": "QA", "evidence": "task-by-task notes with focus screenshots"},
            {"type": "screen reader", "owner": "accessibility specialist", "evidence": "announcement transcripts"},
            {"type": "visual", "owner": "design", "evidence": "contrast table, zoom and reflow captures"},
            {"type": "linguistic", "owner": "bilingual reviewer", "evidence": "MQM error list"},
            {"type": "participatory", "owner": "research", "evidence": "session notes and themes"},
        ],
        "localization_checks": [
            "no hard-coded strings", "no placeholder drift", "no truncation within budget",
            "locale formats correct", "terminology enforced", "captions and alt text localised",
            "font coverage confirmed", "bidi layout verified where applicable",
        ],
        "exit_criteria": [
            "zero critical findings in automated and manual evidence",
            "zero high findings without an approved, time-boxed waiver",
            "every locale in scope has completed linguistic review",
            "participatory testing complete for high-risk journeys",
        ],
        "evidence_package": ["result JSON per skill run", "markdown reports", "session notes",
                             "reviewer sign-offs", "waiver records"],
    }
    for item in plan["exit_criteria"]:
        ctx.pass_check()
    ctx.meta("plan", plan)
    ctx.meta("locales", locales)
    ctx.meta("criteria_count", len(aa))


@op("coverage-matrix", "Build a requirement traceability matrix: which criteria and requirements are covered by "
    "which evidence, and which are untested.",
    params={"requirements": {"type": "json", "default": None,
                             "doc": "Requirement ids in scope; defaults to all in-scope criteria."},
            "conformance_level": {"type": "enum", "values": ["a", "aa", "aaa"], "default": "aa",
                                  "doc": "Level applied when requirements are not supplied."}},
    accepts=("json",))
def run_coverage(ctx):
    level = ctx.params.get("conformance_level", "aa")
    rank = {"A": 1, "AA": 2, "AAA": 3}
    requirements = ctx.params.get("requirements")
    if isinstance(requirements, str):
        requirements = json.loads(requirements)
    if not requirements:
        requirements = [c["num"] for c in ctx.assets.get("wcag", {}).get("criteria", [])
                        if rank.get(c["level"], 1) <= rank[level.upper()]]
    evidence = {}
    data = ctx.docs[0].data
    docs = data if isinstance(data, list) else [data]
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for crit in ((doc.get("summary") or {}).get("criteria_affected") or {}):
            evidence.setdefault(crit, []).append(doc.get("skill", {}).get("name"))
        for f in doc.get("findings") or []:
            crit = str(f.get("wcag") or "").split(",")[0]
            if crit:
                evidence.setdefault(crit, []).append(f.get("id"))
    covered = [r for r in requirements if r in evidence]
    uncovered = [r for r in requirements if r not in evidence]
    for crit in uncovered:
        ctx.add(rule="coverage-gap", message="Requirement %s has no evidence." % crit, severity="medium",
                criterion=crit, locator=crit,
                remediation="Add a manual or automated test that produces evidence for this criterion.",
                retest="Re-run coverage-matrix and confirm the requirement is covered.")
    if not uncovered:
        ctx.pass_check()
    ctx.meta("requirements", len(requirements))
    ctx.meta("covered", len(covered))
    ctx.meta("uncovered", uncovered)
    ctx.meta("coverage_pct", round(100.0 * len(covered) / max(len(requirements), 1), 2))
    ctx.meta("matrix", [{"requirement": r, "evidence": evidence.get(r, [])[:6],
                         "covered": r in evidence} for r in requirements][:400])


@op("metrics-report", "Aggregate one or more result documents into release metrics: findings by severity and "
    "criterion, escape rate, coverage, gate status and trends.",
    params={"previous": {"type": "str", "default": None, "doc": "Previous aggregate JSON for trend comparison."},
            "fail_on": {"type": "enum", "values": ["critical", "high", "medium", "low"], "default": "high",
                        "doc": "Severity that fails the release gate."}},
    accepts=("json",))
def run_metrics(ctx):
    data = ctx.docs[0].data
    docs = data if isinstance(data, list) else [data]
    totals = {s: 0 for s in S.SEVERITIES}
    criteria = {}
    skills = []
    checks = passed = 0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        summary = doc.get("summary") or {}
        skills.append((doc.get("skill") or {}).get("name"))
        for sev, count in (summary.get("by_severity") or {}).items():
            totals[sev] = totals.get(sev, 0) + count
        for crit, count in (summary.get("criteria_affected") or {}).items():
            criteria[crit] = criteria.get(crit, 0) + count
        checks += int(summary.get("checks_run") or 0)
        passed += int(summary.get("checks_passed") or 0)
    fail_on = ctx.params.get("fail_on", "high")
    rank = S.SEVERITY_RANK
    blocking = sum(v for k, v in totals.items() if rank.get(k, 0) >= rank.get(fail_on, 4))
    decision = "pass" if blocking == 0 else "fail"
    previous = ctx.params.get("previous")
    trend = None
    if previous:
        prev = S.load_json(previous)
        prev_findings = sum((prev.get("summary") or {}).get("by_severity", {}).values()) or prev.get("findings_total") or 0
        current = sum(totals.values())
        trend = {"previous_findings": prev_findings, "current_findings": current,
                 "delta": current - prev_findings}
    coverage = round(100.0 * passed / max(checks, 1), 2)
    ctx.meta("documents", len(docs))
    ctx.meta("skills", skills)
    ctx.meta("by_severity", totals)
    ctx.meta("criteria", criteria)
    ctx.meta("checks_run", checks)
    ctx.meta("checks_passed", passed)
    ctx.meta("pass_rate_pct", coverage)
    ctx.meta("blocking", blocking)
    ctx.meta("decision", decision)
    ctx.meta("trend", trend)
    if decision == "fail":
        ctx.add(rule="release-gate", message="%d finding(s) at or above %r block the release." % (blocking, fail_on),
                severity=fail_on if fail_on in S.SEVERITIES else "high", criterion="quality-gate",
                evidence={"by_severity": totals},
                remediation="Close the blocking findings or obtain an approved exception.",
                retest="Re-run metrics-report on the new evidence set and confirm decision=pass.")
    else:
        ctx.pass_check()


@op("evidence-package", "Assemble an evidence package manifest: which artifacts support which claims, their hashes, "
    "timestamps and retention class, with gaps called out.",
    params={"claims": {"type": "json", "default": None,
                       "doc": "[{\"claim\":\"..\",\"evidence\":[\"path\",..]}]"},
            "retention_days": {"type": "int", "default": 730, "doc": "Retention period for the package."}},
    accepts=("json",))
def run_evidence_package(ctx):
    data = ctx.docs[0].data
    claims = (data.get("claims") if isinstance(data, dict) else data) or []
    if not isinstance(claims, list) or not claims:
        raise S.InputError("No claims supplied.",
                           hint="Provide [{\"claim\":\"WCAG 2.2 AA on checkout\",\"evidence\":[\"out/run.json\"]}].")
    retention = int(ctx.params.get("retention_days", 730))
    manifest = []
    for claim in claims:
        ctx.checkpoint()
        paths = claim.get("evidence") or []
        items = []
        for p in paths:
            if os.path.exists(p):
                digest = S.sha256_bytes(S.read_bytes(p, limits=ctx.limits))
                items.append({"path": p, "sha256": digest, "bytes": os.path.getsize(p)})
            else:
                ctx.add(rule="evidence-missing", message="Evidence path %r does not exist." % p, severity="critical",
                        criterion="evidence-integrity", locator=p,
                        remediation="Regenerate the evidence or correct the path.",
                        retest="Re-run evidence-package and confirm every path resolves.")
        if not items:
            ctx.add(rule="evidence-claim-unsupported", message="Claim %r has no usable evidence." % claim.get("claim"),
                    severity="high", criterion="evidence-integrity",
                    remediation="Attach at least one reproducible artifact to the claim.",
                    retest="Re-run and confirm the claim has evidence.")
        else:
            ctx.pass_check()
        manifest.append({"claim": claim.get("claim"), "evidence": items,
                         "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    ctx.meta("manifest", manifest)
    ctx.meta("claims", len(manifest))
    ctx.meta("retention_days", retention)
    ctx.meta("retention_until", time.strftime("%Y-%m-%d", time.gmtime(time.time() + retention * 86400)))


@op("retention-schedule", "Plan test artifact retention: which artifacts to keep, for how long, in what form, and "
    "what to purge, with approval required for deletion.",
    params={"retention_days": {"type": "int", "default": 730, "doc": "Default retention in days."},
            "purge": {"type": "bool", "default": False, "doc": "Deleting artifacts requires --approve."},
            "paths": {"type": "list", "default": None, "doc": "Artifact paths or directories in scope."}},
    accepts=("json",))
def run_retention(ctx):
    paths = ctx.params.get("paths") or []
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",")]
    if ctx.params.get("purge") and not ctx.params.get("_approved"):
        raise S.ApprovalRequired("purge-artifacts",
                                 "Deleting test evidence is irreversible and destroys audit trail.")
    now = time.time()
    retention = int(ctx.params.get("retention_days", 730))
    rows = []
    for p in paths:
        if not os.path.exists(p):
            ctx.add(rule="retention-missing", message="Artifact %r not found." % p, severity="low",
                    criterion="evidence-retention", locator=p,
                    remediation="Confirm the path or drop it from the schedule.",
                    retest="Re-run retention-schedule and confirm the path resolves.")
            continue
        age_days = (now - os.path.getmtime(p)) / 86400.0
        action = "purge" if age_days > retention else "retain"
        rows.append({"path": p, "age_days": int(age_days), "action": action,
                     "retention_days": retention})
        if action == "retain":
            ctx.pass_check()
        else:
            ctx.note("Artifact %r exceeds retention (%d days) and is a purge candidate." % (p, int(age_days)))
    ctx.meta("schedule", rows)
    ctx.meta("purge_candidates", [r["path"] for r in rows if r["action"] == "purge"])
    ctx.meta("purged", False)


# --------------------------------------------------------------------------- #
# Category 24: design systems and developer enablement
# --------------------------------------------------------------------------- #
COMPONENT_CONTRACTS = {
    "button": {"role": "button", "name_required": True, "keyboard": ["Enter", "Space"],
               "states": ["disabled", "pressed?"]},
    "link": {"role": "link", "name_required": True, "keyboard": ["Enter"], "states": []},
    "input": {"role": "textbox", "name_required": True, "keyboard": [], "states": ["required", "invalid", "disabled"],
              "associations": ["label", "description", "error"]},
    "select": {"role": "combobox", "name_required": True, "keyboard": ["ArrowDown", "Enter", "Escape"],
               "states": ["expanded", "disabled"]},
    "combobox": {"role": "combobox", "name_required": True,
                 "keyboard": ["ArrowDown", "ArrowUp", "Enter", "Escape", "Home", "End"],
                 "states": ["expanded", "selected"], "associations": ["listbox"]},
    "dialog": {"role": "dialog", "name_required": True, "keyboard": ["Escape", "Tab", "Shift+Tab"],
               "states": ["modal"], "requirements": ["focus trap", "focus restore", "labelledby"]},
    "tabs": {"role": "tablist", "name_required": True, "keyboard": ["ArrowLeft", "ArrowRight", "Home", "End"],
             "states": ["selected"]},
    "menu": {"role": "menu", "name_required": True,
             "keyboard": ["ArrowUp", "ArrowDown", "Escape", "Home", "End"], "states": ["expanded"]},
    "table": {"role": "table", "name_required": True, "keyboard": ["ArrowUp", "ArrowDown", "Home", "End"],
              "states": ["sort"]},
    "toast": {"role": "status", "name_required": True, "keyboard": [], "states": ["polite"]},
    "tooltip": {"role": "tooltip", "name_required": True, "keyboard": ["Escape"], "states": []},
    "switch": {"role": "switch", "name_required": True, "keyboard": ["Space"], "states": ["checked", "disabled"]},
}


@op("component-contract-audit", "Validate one or more component specifications against role, name, state, keyboard, "
    "focus and localization contracts, per component type.",
    params={"component_type": {"type": "str", "default": None, "doc": "Component type to validate."},
            "require_localization": {"type": "bool", "default": True,
                                     "doc": "Require documented localization behaviour."}},
    accepts=("json",))
def run_component_contract(ctx):
    data = ctx.docs[0].data
    components = data.get("components") if isinstance(data, dict) else data
    if not isinstance(components, list) or not components:
        raise S.InputError("No component specifications found.",
                           hint="Provide {\"components\":[{\"name\":..,\"type\":\"button\",...}]}.")
    require_l10n = bool(ctx.params.get("require_localization", True))
    for i, comp in enumerate(components):
        ctx.checkpoint()
        ctype = (comp.get("type") or ctx.params.get("component_type") or "").lower()
        name = comp.get("name") or "component %d" % (i + 1)
        contract = COMPONENT_CONTRACTS.get(ctype)
        if not contract:
            ctx.add(rule="component-unknown-type", message="%s has unknown type %r." % (name, ctype),
                    severity="medium", criterion="component-contract", locator=name,
                    evidence={"supported": sorted(COMPONENT_CONTRACTS)},
                    remediation="Declare a supported component type.",
                    retest="Re-run and confirm the type is recognised.")
            continue
        declared_role = (comp.get("role") or "").lower()
        if declared_role != contract["role"]:
            ctx.add(rule="component-role", message="%s declares role %r; expected %r." % (
                name, declared_role or "(none)", contract["role"]), severity="high",
                criterion="4.1.2", locator=name,
                remediation="Use the correct role for the component pattern.",
                retest="Re-run and confirm the role matches the contract.")
        else:
            ctx.pass_check()
        if contract.get("name_required"):
            if comp.get("accessible_name") in (False, None, "") and not comp.get("name_source"):
                ctx.add(rule="component-name", message="%s has no documented accessible name source." % name,
                        severity="critical", criterion="4.1.2", locator=name,
                        remediation="Document the name API (label prop, slot, or labelledby).",
                        retest="Re-run and confirm a name source is documented.")
            else:
                ctx.pass_check()
        keys = {(k or "").replace(" ", "").lower() for k in (comp.get("keyboard") or [])}
        missing_keys = [k for k in contract.get("keyboard", []) if k.replace(" ", "").lower() not in keys]
        if missing_keys:
            ctx.add(rule="component-keyboard", message="%s does not document keyboard support for %s." % (
                name, ", ".join(missing_keys)), severity="high", criterion="2.1.1", locator=name,
                evidence={"declared": sorted(keys), "expected": contract.get("keyboard")},
                remediation="Implement and document the required keyboard interactions.",
                retest="Re-run and confirm every required key is declared.")
        else:
            ctx.pass_check()
        states = {s.lower() for s in (comp.get("states") or [])}
        missing_states = [s for s in contract.get("states", []) if s.replace("?", "").lower() not in states
                          and not s.endswith("?")]
        if missing_states:
            ctx.add(rule="component-state", message="%s does not expose state(s) %s." % (name, ", ".join(missing_states)),
                    severity="medium", criterion="4.1.2", locator=name,
                    remediation="Expose the state through native semantics or ARIA state attributes.",
                    retest="Re-run and confirm all required states are declared.")
        else:
            ctx.pass_check()
        for req in contract.get("requirements", []):
            if req not in (comp.get("behaviours") or comp.get("requirements") or []):
                ctx.add(rule="component-behaviour", message="%s does not document %r." % (name, req),
                        severity="high", criterion="2.4.3", locator=name,
                        remediation="Implement and test the required behaviour.",
                        retest="Re-run and confirm the behaviour is documented.")
            else:
                ctx.pass_check()
        if require_l10n:
            if not (comp.get("localization") or comp.get("text_api")):
                ctx.add(rule="component-localization", message="%s has no documented localization contract "
                                                              "(text API, expansion behaviour, direction)." % name,
                        severity="medium", criterion="locale-architecture", locator=name,
                        remediation="Document translatable strings, expansion handling and RTL behaviour.",
                        retest="Re-run and confirm the localization contract exists.")
            else:
                ctx.pass_check()
    ctx.meta("components", len(components))
    ctx.meta("contracts_available", sorted(COMPONENT_CONTRACTS))


@op("lint-config", "Generate developer lint configuration for accessibility and localization from the selected rule "
    "set (eslint-plugin-jsx-a11y, stylelint, custom AST rules).",
    params={"stack": {"type": "enum", "values": ["react", "vue", "svelte", "angular", "html"],
                      "default": "react", "doc": "Framework the rules target."},
            "severity": {"type": "enum", "values": ["error", "warn"], "default": "error",
                         "doc": "Default severity for generated rules."}},
    accepts=("json", "html"))
def run_lint_config(ctx):
    stack = ctx.params.get("stack", "react")
    sev = ctx.params.get("severity", "error")
    rules = {
        "jsx-a11y/alt-text": sev,
        "jsx-a11y/anchor-has-content": sev,
        "jsx-a11y/anchor-is-valid": sev,
        "jsx-a11y/aria-activedescendant-has-tabindex": sev,
        "jsx-a11y/aria-props": sev,
        "jsx-a11y/aria-proptypes": sev,
        "jsx-a11y/aria-role": sev,
        "jsx-a11y/aria-unsupported-elements": sev,
        "jsx-a11y/click-events-have-key-events": sev,
        "jsx-a11y/heading-has-content": sev,
        "jsx-a11y/html-has-lang": sev,
        "jsx-a11y/iframe-has-title": sev,
        "jsx-a11y/img-redundant-alt": sev,
        "jsx-a11y/interactive-supports-focus": sev,
        "jsx-a11y/label-has-associated-control": sev,
        "jsx-a11y/no-access-key": "warn",
        "jsx-a11y/no-autofocus": "warn",
        "jsx-a11y/no-distracting-elements": sev,
        "jsx-a11y/no-noninteractive-tabindex": sev,
        "jsx-a11y/no-redundant-roles": "warn",
        "jsx-a11y/role-has-required-aria-props": sev,
        "jsx-a11y/role-supports-aria-props": sev,
        "jsx-a11y/scope": sev,
        "jsx-a11y/tabindex-no-positive": sev,
        "react/jsx-no-target-blank": "off",
        "i18n/no-literal-string": "warn",
        "i18n/no-unknown-placeholders": sev,
    }
    if stack in ("vue", "svelte", "angular", "html"):
        rules = {k.replace("jsx-a11y/", "%s-a11y/" % stack): v for k, v in rules.items()
                 if k.startswith("jsx-a11y/")}
        rules.update({"i18n/no-literal-string": "warn"})
    config = {
        "parserOptions": {"ecmaVersion": 2022, "sourceType": "module"},
        "plugins": ["jsx-a11y", "i18n"] if stack == "react" else ["%s-a11y" % stack, "i18n"],
        "rules": rules,
        "settings": {"polymorphicPropName": "as", "components": {"Button": "button", "Link": "a"}},
        "overrides": [{"files": ["**/*.test.*", "**/*.stories.*"],
                       "rules": {"i18n/no-literal-string": "off"}}],
    }
    stylelint = {
        "rules": {
            "a11y/no-outline-none": True,
            "a11y/selector-pseudo-class-focus": True,
            "a11y/media-prefers-reduced-motion": True,
            "declaration-property-value-disallowed-list": {
                "outline": ["none", "/^0/"],
                "font-size": ["/^\\d+px$/"],
            },
        }
    }
    ctx.meta("stack", stack)
    ctx.meta("eslint_config", config)
    ctx.meta("stylelint_config", stylelint)
    ctx.meta("rules", len(rules))
    ctx.note("Generated configuration targets eslint-plugin-jsx-a11y / eslint-plugin-i18next; install those plugins "
             "in the consuming project. The tool does not install or run them.")


@op("code-review-checklist", "Generate an accessibility and localization code review checklist tailored to the "
    "selected rule groups and framework.",
    params={"focus": {"type": "list", "default": None,
                      "doc": "Focus areas: markup, keyboard, aria, forms, media, i18n, css."},
            "framework": {"type": "str", "default": "react", "doc": "Framework in use."}},
    accepts=("json", "html", "text"))
def run_checklist(ctx):
    focus = ctx.params.get("focus") or ["markup", "keyboard", "aria", "forms", "media", "i18n", "css"]
    if isinstance(focus, str):
        focus = [f.strip() for f in focus.split(",")]
    framework = ctx.params.get("framework", "react")
    items = {
        "markup": ["Semantic elements before ARIA roles", "One h1, sequential heading levels",
                   "Every image has meaningful alt or explicit empty alt", "Page lang and dir set from locale",
                   "Lists and tables use real list/table markup"],
        "keyboard": ["Everything operable by keyboard", "No positive tabindex", "Visible focus indicator",
                     "No keyboard trap; Escape closes overlays", "Focus moves to overlay and returns on close",
                     "Focus not obscured by sticky headers"],
        "aria": ["Roles valid and supported", "aria-* attributes valid for the role",
                 "Reference ids resolve", "No focusable content inside aria-hidden",
                 "Live regions scoped, not on the whole document"],
        "forms": ["Every control has a persistent label", "Errors described in text and associated",
                  "Required fields marked programmatically and visually", "autocomplete tokens on identity fields",
                  "Authentication allows paste and password managers"],
        "media": ["Video has captions and description tracks", "Players expose controls",
                  "No autoplay with sound", "Transcript available"],
        "i18n": ["No hard-coded user-facing strings", "Placeholders survive translation",
                 "Pluralisation uses ICU messages", "Dates/numbers formatted via locale API",
                 "No string concatenation for sentences", "Logical CSS properties for RTL"],
        "css": ["No outline:none without replacement", "Contrast tokens meet 4.5:1",
                "Motion respects prefers-reduced-motion", "Reflow at 320px without horizontal scroll",
                "Text spacing overrides do not clip"],
    }
    selected = {k: items[k] for k in items if k in focus}
    lines = ["# Code review checklist (%s)" % framework, ""]
    for area, entries in selected.items():
        lines.append("## %s" % area.capitalize())
        lines.append("")
        for e in entries:
            lines.append("- [ ] %s" % e)
        lines.append("")
    lines.append("Reviewer must record the files reviewed, the framework version and any waivers with expiry dates.")
    ctx.meta("checklist", "\n".join(lines))
    ctx.meta("areas", list(selected))
    ctx.meta("item_count", sum(len(v) for v in selected.values()))


# --------------------------------------------------------------------------- #
# Category 25: operations, support, procurement, lifecycle
# --------------------------------------------------------------------------- #
@op("procurement-score", "Score supplier accessibility and localization evidence (VPAT/ACR, test reports) against "
    "weighted criteria and return a recommendation with the questions to ask.",
    params={"weights": {"type": "json", "default": None,
                        "doc": "Override criterion weights, e.g. {\"conformance\":0.4}"},
            "threshold": {"type": "float", "default": 70.0, "doc": "Minimum acceptable weighted score."}},
    accepts=("json",))
def run_procurement(ctx):
    data = ctx.docs[0].data
    suppliers = data.get("suppliers") if isinstance(data, dict) else data
    if not isinstance(suppliers, list) or not suppliers:
        raise S.InputError("No supplier records supplied.",
                           hint="Provide {\"suppliers\":[{\"name\":..,\"scores\":{..},\"evidence\":[..]}]}.")
    weights = ctx.params.get("weights")
    if isinstance(weights, str):
        weights = json.loads(weights)
    weights = weights or {"conformance": 0.35, "evidence_quality": 0.2, "locale_coverage": 0.2,
                          "remediation": 0.15, "contract_terms": 0.10}
    threshold = float(ctx.params.get("threshold", 70.0))
    results = []
    for sup in suppliers:
        ctx.checkpoint()
        scores = sup.get("scores") or {}
        missing = [k for k in weights if k not in scores]
        total = 0.0
        for key, weight in weights.items():
            try:
                val = float(scores.get(key, 0))
            except (TypeError, ValueError):
                val = 0.0
            total += min(max(val, 0.0), 100.0) * float(weight)
        recommendation = "approve" if total >= threshold else "conditional" if total >= threshold - 15 else "reject"
        results.append({"supplier": sup.get("name") or "unnamed", "weighted_score": round(total, 2),
                        "recommendation": recommendation, "missing_criteria": missing,
                        "evidence": sup.get("evidence") or []})
        if missing:
            ctx.add(rule="procurement-missing-criteria", message="%s has no score for %s." % (
                sup.get("name"), ", ".join(missing)), severity="medium", criterion="procurement",
                locator=sup.get("name"), evidence={"missing": missing},
                remediation="Request the missing evidence before awarding.",
                retest="Re-run procurement-score and confirm no criteria are missing.")
        else:
            ctx.pass_check()
        if recommendation != "approve":
            ctx.add(rule="procurement-%s" % recommendation, message="%s scores %.1f (%s)." % (
                sup.get("name"), total, recommendation), severity="high", criterion="procurement",
                locator=sup.get("name"), evidence={"score": round(total, 2), "weights": weights},
                remediation="Require a remediation plan with dates, or reject the supplier.",
                retest="Re-run procurement-score after the supplier responds.")
        questions = ["Which WCAG 2.2 level does the current version conform to, and where is the dated ACR?",
                     "Which assistive technologies and locales were included in testing?",
                     "How are accessibility defects reported, prioritised and fixed under contract?",
                     "What is the roadmap for the outstanding criteria?",
                     "Which locales are supported, and who performs linguistic review?"]
        results[-1]["questions"] = questions
    ctx.meta("results", results)
    ctx.meta("weights", weights)
    ctx.meta("threshold", threshold)


@op("operational-runbook", "Generate an operations runbook: accessibility and localization incident intake, "
    "triage, escalation matrix, service levels and recovery steps.",
    params={"support_channel": {"type": "str", "default": None, "doc": "Primary support channel."},
            "service_hours": {"type": "str", "default": "09:00-18:00 Asia/Manila", "doc": "Support hours."}},
    accepts=("json", "text"))
def run_runbook(ctx):
    channel = ctx.params.get("support_channel") or ctx.config.get("product", {}).get("contact") or "support desk"
    hours = ctx.params.get("service_hours", "09:00-18:00 Asia/Manila")
    runbook = {
        "intake": {
            "channels": [channel, "accessible web form", "phone with relay support", "email"],
            "required_fields": ["what the user was trying to do", "what happened", "assistive technology and version",
                                "browser/OS or device", "locale", "contact preference", "urgency"],
            "first_response": {"critical": "2 hours", "high": "1 working day", "medium": "3 working days",
                               "low": "10 working days"},
        },
        "triage": [
            {"step": "Reproduce with the reported AT/locale", "owner": "tier 1"},
            {"step": "Classify severity using severity-classify", "owner": "tier 1"},
            {"step": "Identify affected templates and locales", "owner": "tier 2"},
            {"step": "Route to the owning team with the evidence bundle", "owner": "tier 2"},
            {"step": "Confirm fix with the reporting user where possible", "owner": "tier 1"},
        ],
        "escalation": [
            {"trigger": "critical accessibility barrier blocking a task", "escalate_to": "accessibility lead",
             "notify": "product owner", "within": "1 hour"},
            {"trigger": "language error published to users", "escalate_to": "localization lead",
             "notify": "content owner", "within": "4 hours"},
            {"trigger": "complaint or legal notice", "escalate_to": "legal", "notify": "executive sponsor",
             "within": "immediately"},
            {"trigger": "third-party component failure", "escalate_to": "vendor manager",
             "notify": "accessibility lead", "within": "1 working day"},
        ],
        "recovery": {
            "rollback": "Revert the release that introduced the barrier; keep the evidence bundle attached to the incident.",
            "workaround": "Publish an accessible alternative (alternate format, phone/assisted channel) within the SLA.",
            "post_incident": ["root-cause note", "regression test added", "gate updated", "user follow-up"],
        },
        "service_levels": {"acknowledge": "1 working day", "workaround": "3 working days",
                           "permanent fix": "next release or 30 days, whichever is sooner"},
        "hours": hours,
    }
    for stage in runbook["triage"]:
        ctx.pass_check()
    ctx.meta("runbook", runbook)
    ctx.meta("stages", len(runbook["triage"]))


@op("lifecycle-schedule", "Compute maintenance schedule: standard reviews, locale data updates, TM and termbase "
    "backups, test-environment refreshes and end-of-life reviews.",
    params={"start_date": {"type": "str", "default": None, "doc": "Anchor date (YYYY-MM-DD)."},
            "horizon_months": {"type": "int", "default": 12, "doc": "Planning horizon."},
            "cadence": {"type": "json", "default": None,
                        "doc": "Override cadences in months, e.g. {\"tm_backup\":1}"}},
    accepts=("json",))
def run_lifecycle(ctx):
    anchor = ctx.params.get("start_date") or time.strftime("%Y-%m-%d")
    try:
        start = datetime.date.fromisoformat(anchor)
    except ValueError:
        raise S.ParamError("start_date must be ISO YYYY-MM-DD.", hint="Example: --set start_date=2026-11-07")
    defaults = {"standards_review": 6, "locale_data_update": 3, "tm_backup": 1, "termbase_backup": 1,
                "test_environment_refresh": 3, "evidence_archive_review": 6, "end_of_life_review": 12,
                "policy_review": 12, "participatory_testing": 3}
    cadence = dict(defaults)
    override = ctx.params.get("cadence")
    if isinstance(override, str):
        override = json.loads(override)
    cadence.update(override or {})
    horizon = int(ctx.params.get("horizon_months", 12))
    schedule = []
    for task, months in cadence.items():
        months = max(1, int(months))
        date = start
        for _ in range(max(1, horizon // months)):
            schedule.append({"task": task, "due": date.isoformat(), "cadence_months": months})
            year = date.year + (date.month - 1 + months) // 12
            month = (date.month - 1 + months) % 12 + 1
            day = min(date.day, 28)
            date = datetime.date(year, month, day)
        ctx.pass_check()
    schedule.sort(key=lambda r: r["due"])
    for i, row in enumerate(schedule, 1):
        row["order"] = i
    ctx.meta("schedule", schedule)
    ctx.meta("tasks", len(cadence))
    ctx.meta("occurrences", len(schedule))
    ctx.meta("horizon_months", horizon)
    ctx.note("Dates use a 28-day-safe month increment; confirm against your own calendar policy.")


@op("intake-triage", "Triage incoming accessibility or localization defect reports: classify, assign owners, set "
    "service levels and produce a prioritised queue.",
    params={"sla_hours": {"type": "json", "default": None,
                          "doc": "Response targets per severity, e.g. {\"critical\":2}"}},
    accepts=("json",))
def run_intake(ctx):
    data = ctx.docs[0].data
    reports = data.get("reports") if isinstance(data, dict) else data
    if not isinstance(reports, list) or not reports:
        raise S.InputError("No reports supplied.",
                           hint="Provide {\"reports\":[{\"title\":..,\"severity\":..,\"locale\":..}]}.")
    sla = {"critical": 2, "high": 8, "medium": 24, "low": 80}
    override = ctx.params.get("sla_hours")
    if isinstance(override, str):
        override = json.loads(override)
    sla.update(override or {})
    queue = []
    for i, r in enumerate(reports):
        ctx.checkpoint()
        sev = (r.get("severity") or "").lower()
        if sev not in S.SEVERITIES:
            text = " ".join(str(r.get(k) or "") for k in ("title", "description", "body")).lower()
            if re.search(r"(cannot|can't|blocked|unable|no way)", text):
                sev = "critical"
            elif re.search(r"(wrong|incorrect|misleading|error)", text):
                sev = "high"
            elif re.search(r"(confusing|hard|difficult)", text):
                sev = "medium"
            else:
                sev = "low"
        owner = "localization" if r.get("locale_issue") or (r.get("locale") and r.get("locale") !=
                                                           ctx.config.get("locale", {}).get("default_locale")) else "accessibility"
        queue.append({"id": r.get("id") or "R-%03d" % (i + 1), "title": T.truncate(r.get("title") or "", 120),
                      "severity": sev, "owner": owner, "locale": r.get("locale"),
                      "response_due_hours": sla.get(sev, 24), "at": r.get("at"), "channel": r.get("channel")})
        if sev in ("critical", "high"):
            ctx.add(rule="intake-%s" % sev, message="%s triaged as %s (respond within %sh)." % (
                queue[-1]["id"], sev, sla.get(sev)), severity=sev, criterion="support-sla",
                locator=queue[-1]["id"], evidence=queue[-1],
                remediation="Assign an owner, reproduce with the reported AT or locale, and publish a workaround.",
                retest="Re-run intake-triage and confirm the case is tracked to closure.")
        else:
            ctx.pass_check()
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    queue.sort(key=lambda r: (order[r["severity"]], r["response_due_hours"]))
    for i, row in enumerate(queue, 1):
        row["position"] = i
    ctx.meta("queue", queue)
    ctx.meta("reports", len(queue))
    ctx.meta("by_severity", {s: sum(1 for r in queue if r["severity"] == s) for s in S.SEVERITIES})
