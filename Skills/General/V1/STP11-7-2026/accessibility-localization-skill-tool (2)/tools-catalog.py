"""Catalog index and skill routing for the accessibility-and-localization Skill Tool.

Loads asset-catalog.json (25 categories, 970 skill concepts extracted from the
source catalog), resolves a skill by id, slug, name or list number, and routes
that skill to the concrete operation that implements it. Routing is keyword
based and deterministic: the first matching pattern in the category wins, and
each category has an explicit default operation.
"""

import json
import os
import re

S = None


def init(deps):
    global S
    S = deps["support"]


CATALOG_FILE = "asset-catalog.json"

# Ordered (pattern, operation) rules per category number.
ROUTES = {
    1: [(r"claim|conformance claim", "claim-validate"),
        (r"release gate|definition-of-done|definition of done", "metrics-report"),
        (r"register|debt|ownership matrix|risk", "register"),
        (r"waiver", "register"),
        (r"evidence archive", "register"),
        (r"workflow|complaint|accommodation|review board|legal review handoff|feedback channel", "workflow"),
        (r"severit|prioriti", "severity-classify"),
        (r"roadmap", "remediation-plan"),
        (r"maturity", "maturity-assessment"),
        (r"conformance report|vpat|acr", "conformance-report"),
        (r"policy|statement|requirements builder|acceptance criteria|procurement checklist|audit scope",
         "policy-document"),
        (r".*", "standards-map")],
    2: [(r"runner|harness|insights|playwright|cypress|selenium|storybook|lighthouse|pa11y|axe-core|html_codesniffer",
         "harness-config"),
        (r"deduplicat|false positive", "dedupe"),
        (r"baseline", "baseline"),
        (r"ci quality gate", "ci-gate"),
        (r".*", "web-scan")],
    3: [(r"test environment|matrix|compatibility", "at-matrix"),
        (r"test script|protocol|human screen reader", "at-script"),
        (r"regression suite|defect reproducer", "at-findings"),
        (r".*", "at-probe")],
    4: [(r"matrix", "test-matrix"),
        (r"human|protocol|review", "at-script"),
        (r".*", "web-scan")],
    5: [(r"design token|custom property", "token-audit"),
        (r"contrast|colour|color", "contrast-check"),
        (r".*", "web-scan")],
    6: [(r"timeout|reduced-motion|reduced motion|pause|stop control|session", "web-scan"),
        (r".*", "motion-audit")],
    7: [(r"consistent|timeout|form|error|navigation|predictable|task|memory|attention|focus", "cognitive-support-audit"),
        (r".*", "plain-language-audit")],
    8: [(r"chart|graph|data vis|diagram|infographic|map|complex image|canvas", "chart-accessibility"),
        (r".*", "alt-text-audit")],
    9: [(r"quality gate", "caption-quality-gate"),
        (r"audio description|described|ad script|description track", "audio-description-script"),
        (r"player|media element|transcript|autoplay|keyboard|control", "media-player-audit"),
        (r".*", "captions-audit")],
    10: [(r"remediation|fix plan|checklist|workflow|conversion|template", "document-remediation-plan"),
         (r".*", "document-audit")],
    11: [(r".*", "web-scan")],
    12: [(r"matrix|device lab|emulator|compat", "test-matrix"),
         (r".*", "element-tree-audit")],
    13: [(r"icu|messageformat|plural|gender|variant|escaping", "icu-validate"),
         (r"hard-coded|hardcoded|extract|concatenat|placeholder|interpolat|untranslated string|resource bundle|message catalog",
          "hardcoded-scan"),
         (r"date|time|number|currency|percentage|measurement|address|telephone|name formatting|list formatting|collation|sort|search normal|case mapping|word boundary|line break|hyphenat",
          "locale-format-audit"),
         (r"expansion|contraction|truncat|resilien", "expansion-budget"),
         (r".*", "i18n-audit")],
    14: [(r"leverage|align|match|dedup|quality scorer", "tm-match"),
         (r"translation memory|tmx|segment", "tm-audit"),
         (r".*", "term-audit")],
    15: [(r"mqm|scorecard|lisa|quality assurance score|severity classifier", "mqm-score"),
         (r"brief|handoff package|handoff", "translation-brief"),
         (r"review workflow|reviewer|second linguist|in-country|community|sign-off|query|back translation|workflow|subtitle review|caption review|alt text|error message|notification|voice script|survey|debriefing|pre-editing|simplification",
          "review-plan"),
         (r".*", "translation-qa")],
    16: [(r"readiness|cebuano|ilocano|hiligaynon|waray|kapampangan|pangasinan|bikol|regional|roster", "language-readiness"),
         (r"font coverage", "font-coverage"),
         (r"screen reader pronunciation", "bidi-audit"),
         (r"locale profile|date|time|currency|address|telephone|name format|government term|caption style|subtitle style|orthograph|pronunciation",
          "philippine-locale-check"),
         (r".*", "filipino-review")],
    17: [(r"layout|mirror|icon|animation|scroll|form layout|table layout|navigation|pdf export|office|vertical|visual regression",
          "rtl-layout-audit"),
         (r"arabic|hebrew|persian|urdu|indic|southeast asian|combining|cjk|shaping|tex", "script-rendering"),
         (r".*", "bidi-audit")],
    18: [(r"license|embedding permission|embedding", "font-license"),
         (r"readab|typograph|weight|italic|all-caps|small-caps|pairing|line height|letter spacing|density|size",
          "typography-audit"),
         (r".*", "font-coverage")],
    19: [(r"generator|pseudo|double-width|stress test|expansion|contraction", "pseudolocalize"),
         (r".*", "pseudo-audit")],
    20: [(r"plain language|reading age", "plain-language-audit"),
         (r"inclusive|gender|disability|age|bias|stereotype|persona|tone|voice|transcreation", "inclusive-language-audit"),
         (r".*", "cultural-adaptation-audit")],
    21: [(r"alt text|caption quality|reading level|output|benchmark", "ai-output-audit"),
         (r"translation|terminolog|locale detection|language switching|rtl response|retrieval|consistency|prompt localization|hallucination",
          "ai-translation-audit"),
         (r".*", "conversational-interface-audit")],
    22: [(r"usability test", "sus-score"),
         (r"theme|synthesis|prioriti|severity rating|journey|comprehension|preference|cultural relevance",
          "findings-synthesis"),
         (r".*", "research-plan")],
    23: [(r"test plan|matrix|scheduler|baseline|suite|environment|retention", "test-plan"),
         (r"packager|evidence pack", "evidence-package"),
         (r"coverage|traceability|dashboard|metric|trend|count|rate|escape", "coverage-matrix"),
         (r".*", "metrics-report")],
    24: [(r"token", "token-audit"),
         (r"linter|lint", "lint-config"),
         (r"checklist|recipe|documentation|scaffold|adapter", "code-review-checklist"),
         (r".*", "component-contract-audit")],
    25: [(r"procurement|vendor|supplier|scorecard|contract|third-party|saas|evidence review", "procurement-score"),
         (r"maintenance|monitor|backup|recovery|end-of-life|update|lifecycle", "lifecycle-schedule"),
         (r".*", "operational-runbook")],
}

# Skills whose name implies a different (more specific) operation than the category default.
OVERRIDES = {
    "AI Caption Quality Gate": "caption-quality-gate",
    "Automated Test Baseline Manager": "baseline",
    "Golden Page Accessibility Baseline": "baseline",
    "Translation Memory Leverage Analyzer": "tm-match",
    "Translation Memory Deduplicator": "tm-audit",
    "MQM Quality Evaluation": "mqm-score",
    "Linguistic Quality Assurance Scorecard": "mqm-score",
    "Filipino Terminology Database": "term-audit",
    "Bilingual Filipino-English Glossary": "term-audit",
    "Philippine Language Font Coverage Test": "font-coverage",
    "Filipino Screen Reader Pronunciation Test": "bidi-audit",
    "Accessibility Severity Scoring": "severity-classify",
    "Localization Severity Scoring": "severity-classify",
    "Accessibility Bug Severity Classifier": "severity-classify",
    "Accessibility Remediation Prioritizer": "remediation-plan",
    "Research Findings Prioritizer": "findings-synthesis",
    "SUS Questionnaire Analysis": "sus-score",
    "Accessibility Maturity Assessment": "maturity-assessment",
    "Conformance Claim Validator": "claim-validate",
    "Accessibility Release Gate": "metrics-report",
    "CI Accessibility Gate": "ci-gate",
    "CI Localization Gate": "ci-gate",
    "Accessibility Definition-of-Done Gate": "metrics-report",
    "Design System Accessibility Gate": "metrics-report",
    "Quality Gate Override Audit": "metrics-report",
    "Accessibility Evidence Archive": "register",
    "Accessibility Test Plan Generator": "test-plan",
    "Combined Accessibility and Localization Test Plan": "test-plan",
    "Localization Test Plan Generator": "test-plan",
    "Locale Coverage Matrix": "coverage-matrix",
    "Requirement Coverage Matrix": "coverage-matrix",
    "WCAG Criterion Traceability": "coverage-matrix",
    "Conformance Evidence Packager": "evidence-package",
    "Test Artifact Retention Manager": "retention-schedule",
    "Color Token Contrast Validator": "token-audit",
    "Accessible Design Token Manager": "token-audit",
    "Focus Token Validator": "token-audit",
    "Motion Token Accessibility Validator": "token-audit",
    "Typography Token Accessibility Validator": "token-audit",
    "Developer Accessibility Linter": "lint-config",
    "Developer Localization Linter": "lint-config",
    "Accessibility Code Review Checklist": "code-review-checklist",
    "Localization Code Review Checklist": "code-review-checklist",
    "Locale Resolver": "locale-format-audit",
    "Accessibility Statement Generator": "policy-document",
    "Accessibility Policy Builder": "policy-document",
    "Accessibility Acceptance Criteria Generator": "policy-document",
    "Accessibility Audit Scope Builder": "policy-document",
    "Accessibility Conformance Requirements Builder": "policy-document",
    "Accessibility Roadmap Planner": "remediation-plan",
    "Accessibility Risk Register": "risk-register-seed",
    "Accessibility Debt Register": "register",
    "Accessibility Ownership Matrix": "register",
    "Accessibility Waiver Expiration Tracker": "register",
    "Support Escalation Matrix": "operational-runbook",
    "Operational Runbook": "operational-runbook",
    "Accessibility Maintenance Plan": "lifecycle-schedule",
    "Localization Maintenance Plan": "lifecycle-schedule",
    "Standard Update Monitor": "lifecycle-schedule",
    "Locale Data Update Monitor": "lifecycle-schedule",
    "Translation Memory Backup": "lifecycle-schedule",
    "Termbase Backup": "lifecycle-schedule",
    "Accessibility Test Environment Backup": "lifecycle-schedule",
    "Localization Asset Recovery": "lifecycle-schedule",
    "Accessibility Bug Intake": "intake-triage",
    "Localization Bug Intake": "intake-triage",
    "User Feedback Triage": "intake-triage",
    "Language Support Routing": "intake-triage",
    "Accessibility Conformance Report Generator": "conformance-report",
    "VPAT 2.5 Authoring Assistant": "conformance-report",
    "Screen Reader Test Script Generator": "at-script",
    "Human Screen Reader Test Protocol": "at-script",
    "Screen Reader Compatibility Matrix": "at-matrix",
    "Assistive Technology Compatibility Matrix": "at-matrix",
    "Cross-Browser Accessibility Matrix": "test-matrix",
    "Cross-Device Accessibility Matrix": "test-matrix",
    "Cross-Locale Test Matrix": "test-matrix",
    "Input Modality Compatibility Matrix": "test-matrix",
    "Assistive Technology Defect Reproducer": "at-findings",
    "Translation Brief Generator": "translation-brief",
    "Translator Handoff Package": "translation-brief",
    "Reviewer Handoff Package": "translation-brief",
    "Human Translation Review": "review-plan",
    "Second Linguist Review": "review-plan",
    "In-Country Review": "review-plan",
    "Usability Task Script Generator": "research-plan",
    "Observation Note Template": "research-plan",
    "Qualitative Theme Analysis": "findings-synthesis",
    "Accessible Research Consent Workflow": "research-plan",
    "Disabled Participant Recruitment Workflow": "research-plan",
    "Assistive Technology Participant Matching": "research-plan",
    "Participant Privacy Safeguard": "research-plan",
    "Research Evidence Packager": "evidence-package",
    "Document Localization Remediation Plan": "document-remediation-plan",
    "ICU MessageFormat Validator": "icu-validate",
    "Plural Rules Validator": "icu-validate",
    "Hard-Coded String Detector": "hardcoded-scan",
    "Translatable String Extractor": "hardcoded-scan",
    "String Concatenation Detector": "hardcoded-scan",
    "Placeholder Integrity Checker": "translation-qa",
    "Text Expansion Resilience Test": "expansion-budget",
    "Text Contraction Resilience Test": "expansion-budget",
    "Accented Pseudolocale Generator": "pseudolocalize",
    "Expanded Pseudolocale Generator": "pseudolocalize",
    "Bidirectional Pseudolocale Generator": "pseudolocalize",
    "Mirrored Pseudolocale Generator": "pseudolocalize",
    "Pseudolocalization Tests": "pseudolocalize",
    "Font Coverage for Target Languages": "font-coverage",
    "Unicode Glyph Coverage Scanner": "font-coverage",
    "Missing Glyph Detector": "font-coverage",
    "Tofu Character Detector": "font-coverage",
    "Filipino Language Review": "filipino-review",
    "Filipino Plain Language Review": "filipino-review",
    "Taglish Content Review": "filipino-review",
    "Filipino Inclusive Language Review": "filipino-review",
    "Filipino Accessibility Terminology Review": "filipino-review",
    "Philippine Government Terminology Review": "philippine-locale-check",
    "Philippine Currency Formatting": "philippine-locale-check",
    "Philippine Date Format Review": "philippine-locale-check",
    "Locale-Specific Date and Number Formatting": "locale-format-audit",
    "Cebuano Localization Readiness": "language-readiness",
    "Ilocano Localization Readiness": "language-readiness",
    "Hiligaynon Localization Readiness": "language-readiness",
    "Waray Localization Readiness": "language-readiness",
    "Kapampangan Localization Readiness": "language-readiness",
    "Pangasinan Localization Readiness": "language-readiness",
    "Bikol Localization Readiness": "language-readiness",
    "Regional Language Readiness Review": "language-readiness",
    "Contract Accessibility Clause Builder": "policy-document",
    "Contract Localization Clause Builder": "policy-document",
    "Accessible Customer Support Workflow": "operational-runbook",
    "Multilingual Customer Support Workflow": "operational-runbook",
    "Accessibility Procurement Checklist": "procurement-score",
    "Accessibility Procurement Gate": "procurement-score",
    "Localization Procurement Gate": "procurement-score",
    "Translation Vendor Evaluation": "procurement-score",
    "Accessibility Vendor Evaluation": "procurement-score",
    "Language Service Provider Scorecard": "procurement-score",
    "SaaS Accessibility Evidence Review": "procurement-score",
    "Third-Party Widget Accessibility Review": "procurement-score",
    "Third-Party Content Localization Review": "procurement-score",
    "Supplier Accessibility Evidence Reviewer": "procurement-score",
}


class Catalog(object):
    def __init__(self, root, data):
        self.root = root
        self.data = data
        self.categories = data.get("categories", [])
        self.by_number = {c["number"]: c for c in self.categories}
        self.skills = []
        self.by_id = {}
        self.by_slug = {}
        self.by_name = {}
        for cat in self.categories:
            for sk in cat.get("skills", []):
                record = dict(sk)
                record["category"] = {"number": cat["number"], "name": cat["name"], "slug": cat["slug"]}
                self.skills.append(record)
                self.by_id[record["id"].lower()] = record
                self.by_slug[record["slug"]] = record
                self.by_name[record["name"].lower()] = record

    # -- resolution -------------------------------------------------------- #
    def resolve(self, query):
        if query is None:
            raise S.ResolutionError("A skill reference is required.",
                                    hint="Use --skill with an id (C02-S25), list number (2.25), slug or name.")
        q = str(query).strip()
        low = q.lower()
        if low in self.by_id:
            return self.by_id[low]
        if low in self.by_slug:
            return self.by_slug[low]
        if low in self.by_name:
            return self.by_name[low]
        m = re.match(r"^(\d{1,2})[.\-/](\d{1,2})$", q)
        if m:
            cat = self.by_number.get(int(m.group(1)))
            if cat:
                for sk in cat["skills"]:
                    if sk["index"] == int(m.group(2)):
                        return self.by_id[sk["id"].lower()]
        if re.match(r"^\d{1,3}$", q):
            num = int(q)
            if 1 <= num <= len(self.skills):
                return self.skills[num - 1]
        matches = [s for s in self.skills if low in s["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            exact = [s for s in matches if s["name"].lower() == low]
            if len(exact) == 1:
                return exact[0]
            raise S.ResolutionError(
                "Skill reference %r is ambiguous." % q,
                hint="Candidates: %s" % "; ".join("%s (%s)" % (m["id"], m["name"]) for m in matches[:8]))
        raise S.ResolutionError(
            "No skill matches %r." % q,
            hint="Run 'capabilities' to list categories, or 'search <text>' to find a skill.")

    def search(self, text, limit=40):
        words = [w for w in re.split(r"\s+", (text or "").lower()) if w]
        scored = []
        for s in self.skills:
            hay = (s["name"] + " " + s.get("description", "") + " " + s["category"]["name"]).lower()
            score = sum(hay.count(w) for w in words)
            if all(w in hay for w in words):
                score += 3 * len(words)
            if score:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1]["category"]["number"], x[1]["index"]))
        return [s for _, s in scored[:limit]]

    # -- routing ----------------------------------------------------------- #
    def op_for(self, skill):
        override = OVERRIDES.get(skill["name"])
        if override:
            return override, "override"
        rules = ROUTES.get(skill["category"]["number"], [(r".*", "web-scan")])
        name = skill["name"].lower()
        for pattern, op_name in rules:
            if re.search(pattern, name):
                return op_name, "rule"
        return "web-scan", "fallback"

    def categories_summary(self):
        out = []
        for cat in self.categories:
            ops = {}
            for sk in cat["skills"]:
                op_name, _ = self.op_for(self.by_id[sk["id"].lower()])
                ops[op_name] = ops.get(op_name, 0) + 1
            out.append({"number": cat["number"], "name": cat["name"], "slug": cat["slug"],
                        "skills": len(cat["skills"]), "operations": ops})
        return out


def load(root):
    path = os.path.join(root, CATALOG_FILE)
    if not os.path.exists(path):
        raise S.InputError("Catalog asset not found: %s" % CATALOG_FILE,
                           hint="Keep asset-catalog.json beside tools-a11yl10n.py.")
    data = S.load_json(path)
    for key in ("categories", "skill_count", "category_count"):
        if key not in data:
            raise S.InputError("Catalog asset is missing the %r section." % key,
                               hint="Regenerate asset-catalog.json from the source catalog.")
    if data.get("category_count") != len(data.get("categories", [])):
        raise S.InputError("Catalog asset category_count does not match its category list.")
    total = sum(len(c.get("skills", [])) for c in data["categories"])
    if total != data.get("skill_count"):
        raise S.InputError("Catalog asset skill_count (%s) does not match its skills (%s)." % (
            data.get("skill_count"), total))
    return Catalog(root, data)


def op_routing_report(catalog, ops_registry):
    """Return every skill with its routed operation and whether the op exists."""
    rows = []
    for sk in catalog.skills:
        op_name, how = catalog.op_for(sk)
        rows.append({"id": sk["id"], "name": sk["name"], "category": sk["category"]["name"],
                     "op": op_name, "via": how, "implemented": op_name in ops_registry})
    return rows
