#!/usr/bin/env python3
"""tools-pm-plan.py - requirements, narrative, platform, and drafting engine.

Load order: after tools-pm-core.py and tools-pm-intake.py; before
tools-pm-render.py, tools-pm-audit.py, tools-pm-package.py.

Responsibilities
----------------
* load_brief()        validate and normalise a presentation brief
* choose_framework()  pick the narrative spine from asset-narrative-frameworks.json
* score_platforms()   rank the 12 platforms in asset-platform-profiles.json
* risk_register()     font/media/network/device/permission/conversion risks
* production_plan()   thin end-to-end sequence with the hardest dependency first
* parse_content()     read supplied Markdown/JSON source material
* build_deck_spec()   plan + content -> a schema-valid deck specification
* speaker_notes()     deterministic note drafting for every slide

Everything is offline and deterministic: the same inputs produce the same spec.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

for _name, _file in (("pm_core", "tools-pm-core.py"), ("pm_intake", "tools-pm-intake.py")):
    if _name not in sys.modules:  # pragma: no cover - import shim
        _dir = os.path.dirname(os.path.abspath(__file__))
        _spec = importlib.util.spec_from_file_location(_name, os.path.join(_dir, _file))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _mod
        _spec.loader.exec_module(_mod)
import pm_core  # noqa: E402
import pm_intake  # noqa: E402

AUDIENCE_EXPERTISE_WORDS_PER_MINUTE = {
    "novice": 110, "mixed": 130, "practitioner": 140, "expert": 150, "executive": 145,
}

PURPOSE_TO_FRAMEWORK_HINT = {
    "decide": "executive-scia",
    "align": "executive-scia",
    "inform": "classic-narrative",
    "persuade": "classic-narrative",
    "recap": "project-status",
    "status": "project-status",
    "sell": "sales-story",
    "fundraise": "investor-pitch",
    "teach": "educational-sequence",
    "train": "training-onboarding",
    "research": "research-findings",
    "launch": "campaign-plan",
}

REVIEW_CHECKPOINTS = [
    {"stage": "claims-and-sources", "approver": "content owner", "required": True, "status": "pending"},
    {"stage": "sensitive-content", "approver": "data owner", "required": True, "status": "pending"},
    {"stage": "brand-and-legal", "approver": "brand owner", "required": True, "status": "pending"},
    {"stage": "accessibility", "approver": "presentation owner", "required": True, "status": "pending"},
    {"stage": "external-publishing", "approver": "communications owner", "required": False, "status": "pending"},
]


# --------------------------------------------------------------------------
# Brief
# --------------------------------------------------------------------------
def _require(condition, message, code="brief-invalid", hint=None):
    if not condition:
        raise pm_core.PMError(message, code=code, hint=hint)


def load_brief(path, config):
    """Validate a brief file and fill documented defaults."""
    raw = pm_core.read_json(path)
    errors = pm_core.validate_against_asset(raw, config, "brief_schema")
    hard = [e for e in errors if e["level"] == "error"]
    if hard:
        detail = "; ".join("%s %s" % (e.get("where", "$"), e["message"]) for e in hard[:6])
        raise pm_core.PMError(
            "Brief does not match asset-brief-schema.json: %s" % detail,
            code="brief-schema",
            hint="Run 'validate --kind brief --input <file>' for the full list of problems.",
        )
    brief = _normalise_brief(raw)
    return brief, errors


def _normalise_brief(raw):
    brief = dict(raw)
    brief.setdefault("brief_version", "1.0")
    audience = dict(brief.get("audience") or {})
    audience.setdefault("expertise", "mixed")
    audience.setdefault("language", "en")
    audience.setdefault("size", "not specified")
    audience.setdefault("objections", [])
    brief["audience"] = audience

    delivery = dict(brief.get("delivery") or {})
    delivery.setdefault("mode", "live-stage")
    delivery.setdefault("duration_minutes", 20)
    delivery.setdefault("recorded", False)
    delivery.setdefault("asynchronous", False)
    delivery.setdefault("network", "reliable")
    delivery.setdefault("devices", [])
    brief["delivery"] = delivery

    constraints = dict(brief.get("constraints") or {})
    constraints.setdefault("slides_target", max(6, min(30, int(delivery["duration_minutes"] / 1.7))))
    constraints.setdefault("must_include", [])
    constraints.setdefault("must_exclude", [])
    constraints.setdefault("editability", "editable-source-required")
    constraints.setdefault("logo_required", True)
    brief["constraints"] = constraints

    brief.setdefault("priorities", _infer_priorities(brief))
    brief.setdefault("platform", {})
    brief.setdefault("content", {})
    brief.setdefault("narrative", {})
    brief.setdefault("accessibility", {})
    brief["accessibility"].setdefault("standard", "wcag-aa")
    brief["accessibility"].setdefault("captions_required", delivery["recorded"])
    brief["accessibility"].setdefault("reduced_motion", delivery["mode"] in ("live-stage", "classroom"))
    brief.setdefault("security", {})
    brief["security"].setdefault("confidential", False)
    brief["security"].setdefault("external_audience", False)
    brief["security"].setdefault("pii_expected", False)
    brief["security"].setdefault("public_publishing", delivery["mode"] in ("embedded-web", "self-running-kiosk"))
    brief.setdefault("call_to_action", {})
    brief.setdefault("success_criteria", [])
    brief.setdefault("review_checkpoints", REVIEW_CHECKPOINTS)
    return brief


def _infer_priorities(brief):
    delivery = brief.get("delivery", {})
    priorities = []
    if delivery.get("mode") in ("live-stage", "classroom"):
        priorities += ["precision", "animation", "offline"]
    if delivery.get("mode") in ("remote-meeting", "sales-call"):
        priorities += ["collaboration", "compatibility"]
    if delivery.get("mode") in ("self-running-kiosk", "embedded-web"):
        priorities += ["web_publishing", "affordability"]
    if delivery.get("mode") == "recorded-narration":
        priorities += ["animation", "print"]
    if brief.get("purpose") in ("decide", "status", "fundraise"):
        priorities += ["data_visuals", "compatibility"]
    if brief.get("purpose") in ("teach", "train", "research"):
        priorities += ["accessibility", "print"]
    if delivery.get("network") == "offline":
        priorities += ["offline", "affordability"]
    seen, unique = set(), []
    for item in priorities:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique[:6] or ["precision", "compatibility", "accessibility"]


def brief_assumptions(brief):
    """Explicit list of defaults the tool applied, so nothing is silent."""
    out = []
    out.append("Delivery mode assumed '%s' with %d minutes on stage."
               % (brief["delivery"]["mode"], brief["delivery"]["duration_minutes"]))
    out.append("Slide target set to %d from the time budget; override with constraints.slides_target."
               % brief["constraints"]["slides_target"])
    out.append("Audience expertise assumed '%s' (%d words per minute pacing)."
               % (brief["audience"]["expertise"],
                  AUDIENCE_EXPERTISE_WORDS_PER_MINUTE.get(brief["audience"]["expertise"], 130)))
    out.append("Accessibility target assumed '%s'; contrast is recalculated by audit."
               % brief["accessibility"]["standard"])
    out.append("Tone assumed '%s' unless narrative.tone is set."
               % brief.get("narrative", {}).get("tone", "plain"))
    return out


# --------------------------------------------------------------------------
# Narrative framework selection
# --------------------------------------------------------------------------
def choose_framework(brief, config):
    data = pm_core.config_asset(config, "narrative_frameworks")
    frameworks = data.get("frameworks", [])
    requested = (brief.get("narrative") or {}).get("framework")
    if requested:
        for framework in frameworks:
            if framework["id"] == requested:
                return framework, [pm_core.diag("info", "framework-explicit",
                                                "Framework '%s' requested by the brief" % requested)]
    hints = [brief.get("purpose"), (brief.get("purpose_detail") or "")]
    audience = (brief.get("audience") or {}).get("primary", "")
    mode = brief["delivery"]["mode"]
    corpus = " ".join([brief.get("title", ""), brief.get("purpose_detail") or "", audience]).lower()
    scored = []
    for framework in frameworks:
        score = framework.get("priority", 10)
        match = framework.get("match", {})
        if brief.get("purpose") in match.get("purposes", []):
            score += 40
        if any(k.lower() in audience.lower() for k in match.get("audiences", [])):
            score += 25
        if mode in match.get("delivery_modes", []):
            score += 20
        for token in match.get("purposes", []):
            if token.replace("-", " ") in corpus:
                score += 8
        if PURPOSE_TO_FRAMEWORK_HINT.get(brief.get("purpose")) == framework["id"]:
            score += 12
        scored.append((score, framework))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    best_score, best = scored[0]
    diags = [pm_core.diag("info", "framework-selected",
                          "Selected '%s' (%s) scoring %d" % (best["id"], best["name"], best_score))]
    if len(scored) > 1:
        runner_score, runner = scored[1]
        diags.append(pm_core.diag("info", "framework-runner-up",
                                  "Runner-up '%s' scored %d" % (runner["id"], runner_score)))
    return best, diags


# --------------------------------------------------------------------------
# Platform selection
# --------------------------------------------------------------------------
def score_platforms(brief, config):
    data = pm_core.config_asset(config, "platform_profiles")
    platforms = data.get("platforms", [])
    available = set(data.get("attributes", {}).keys())
    priorities = [p for p in brief.get("priorities", []) if p in available]
    preferred = (brief.get("platform", {}) or {}).get("preferred", "")
    forbidden = {f.lower() for f in (brief.get("platform", {}) or {}).get("forbidden", [])}
    must = [m.lower() for m in (brief.get("platform", {}) or {}).get("must_support", [])]

    ranked = []
    for platform in platforms:
        if platform["id"] in forbidden or platform["name"].lower() in forbidden:
            continue
        scores = platform["scores"]
        if priorities:
            raw = sum(scores.get(key, 0) for key in priorities) / (5.0 * len(priorities))
        else:
            raw = sum(scores.values()) / (5.0 * len(scores))
        value = round(raw * 100)
        reasons, cautions = [], []
        for key in priorities:
            value_of = scores.get(key, 0)
            if value_of >= 4:
                reasons.append("strong %s (%d/5)" % (key, value_of))
            elif value_of <= 2:
                cautions.append("weak %s (%d/5)" % (key, value_of))
        supports = {fmt.lower() for fmt in platform.get("export_formats", [])}
        missing = [m for m in must if m not in supports]
        if missing:
            cautions.append("cannot export %s directly" % ", ".join(missing))
            value = max(0, value - 25)
        if preferred and (platform["id"] == preferred or platform["name"].lower() == preferred.lower()):
            value += 15
            reasons.append("preferred by the brief")
        ranked.append({
            "platform": platform["id"],
            "name": platform["name"],
            "score": min(100, value),
            "reasons": reasons[:4],
            "cautions": cautions[:4],
            "requires": platform.get("requires", ""),
            "export_formats": platform.get("export_formats", []),
        })
    ranked.sort(key=lambda item: (-item["score"], item["platform"]))
    if not ranked:
        raise pm_core.PMError("Every platform was excluded by platform.forbidden",
                             code="platform-none", hint="Remove a platform from platform.forbidden.")
    return ranked


# --------------------------------------------------------------------------
# Risk and production planning
# --------------------------------------------------------------------------
def risk_register(brief, platform_row):
    mode = brief["delivery"]["mode"]
    constraints = brief["constraints"]
    risks = []

    def add(risk, level, trigger, mitigation, tested):
        risks.append({"risk": risk, "level": level, "trigger": trigger,
                      "mitigation": mitigation, "test": tested})

    if mode in ("live-stage", "classroom", "remote-meeting"):
        add("Presenter machine differs from the build machine", "high",
            "Fonts, codecs, or PowerPoint version differ",
            "Carry a PDF fallback plus the editable source on the same drive",
            "Open both files on the venue machine during setup")
    if mode in ("self-running-kiosk", "embedded-web"):
        add("Network or embed blocked at the venue", "high",
            "Captive portal, admin policy, or expiring public link",
            "Prefetch a local HTML build and a PDF; keep links behind a stable host",
            "Open the kiosk build with networking disabled")
    if brief["delivery"].get("network") == "offline":
        add("External asset unavailable offline", "high",
            "Linked video, font CDN, or remote image",
            "Inline or bundle every media file; no remote font references",
            "Audit reports delivery.html-external-resource")
    if constraints.get("editability") == "editable-source-required":
        add("Handoff recipient needs editable source", "medium",
            "Native format gaps in the chosen platform",
            "Ship PPTX plus the deck spec JSON as the editable twin",
            "Open the PPTX and edit one slide in a clean account")
    if brief["accessibility"].get("captions_required"):
        add("Recorded media missing captions", "high",
            "Narration recorded without a transcript",
            "Draft the transcript before recording and burn in or side-load captions",
            "Audit reports a11y.media-caption-missing")
    if brief["security"].get("external_audience") or brief["security"].get("public_publishing"):
        add("Sensitive content reaches an external audience", "high",
            "Confidential marker, personal data, or hidden slide left in",
            "Run the privacy gate and remove or approve every finding",
            "audit --gate privacy-governance must pass")
    if platform_row and platform_row.get("cautions"):
        add("Chosen platform has known weaknesses for this brief", "medium",
            "; ".join(platform_row["cautions"]),
            "Use the documented workaround for %s or accept the trade-off" % platform_row["name"],
            "Render one representative slide and compare against the spec")
    if brief["constraints"].get("deadline", "").strip():
        add("Compression near the deadline", "medium",
            "Late content changes after polish",
            "Freeze content at the thin-deck checkpoint; polish only after approval",
            "Run pipeline twice and diff the outputs")
    if not risks:
        add("No elevated risk detected", "low",
            "Standard offline build with no external media",
            "Keep the standard preflight in the package", "package --verify")
    return risks


def production_plan(brief, platform_row):
    return [
        {"step": 1, "name": "Thin end-to-end deck",
         "detail": "Build the riskiest dependency first: one title slide, one content slide, and the hardest export (usually PDF or PPTX) rendered from the real pipeline.",
         "exit": "A 2-slide deck opens cleanly on the target device."},
        {"step": 2, "name": "Narrative lock",
         "detail": "Freeze sections, slide roles, evidence list, and call to action against the brief.",
         "exit": "Every section maps to a brief requirement and every claim has a source slot."},
        {"step": 3, "name": "Content drafting",
         "detail": "Fill bullets, metrics, and speaker notes; move overflow into notes or the appendix.",
         "exit": "audit passes content-integrity and layout-safety."},
        {"step": 4, "name": "Visual and brand pass",
         "detail": "Apply the theme preset, one idea per slide, direct labels on data, logo and footer in place.",
         "exit": "audit passes brand-consistency."},
        {"step": 5, "name": "Accessibility pass",
         "detail": "Titles, alt text, reading order, contrast, captions, and motion limits.",
         "exit": "audit passes accessible-by-default."},
        {"step": 6, "name": "Export and verify",
         "detail": "Render every promised format for platform %s, then re-open each file."
         % (platform_row["name"] if platform_row else "the target platform"),
         "exit": "audit passes delivery-readiness and package --verify succeeds."},
        {"step": 7, "name": "Clean-state rehearsal and delivery",
         "detail": "Open the package on a fresh account or second machine, rehearse the timing, and confirm approvals.",
         "exit": "Release checklist signed; package zip opens offline."},
    ]


# --------------------------------------------------------------------------
# Content intake
# --------------------------------------------------------------------------
METRIC_RE = re.compile(
    r"^(?:metric|kpi|stat|figure)\s*[:\-]\s*(?P<label>[^:=]{2,60}?)\s*[:=]\s*"
    r"(?P<value>[A-Za-z0-9$€£%.,+\-\u00a0 ]{1,24}?)\s*"
    r"(?:(?:\(|\u2014|\u2013|\s-\s|:)\s*(?P<note>.+?))?\)?$",
    re.I)
SOURCE_RE = re.compile(r"^(?:source|sources|citation|ref)\s*[:\-]\s*(?P<value>.+)$", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-*+\u2022]|\d{1,2}[.)])\s+(?P<text>.+?)\s*$")
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")
NOTE_RE = re.compile(r"^(?:note|notes|say|narrate)\s*[:\-]\s*(?P<text>.+)$", re.I)
BULLET_PREFIX_RE = re.compile(r"^(?:[-*+\u2022]|\d{1,2}[.)])\s+")


def parse_content(text, source_name="content"):
    """Parse Markdown-ish or JSON source material into section records."""
    if not text or not text.strip():
        raise pm_core.PMError("Content source is empty: %s" % source_name, code="content-empty")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            import json
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("sections"):
            return _content_from_json(data, source_name)
    return _content_from_markdown(text, source_name)


def _content_from_json(data, source_name):
    doc = {"title": data.get("title") or source_name,
           "sections": [], "sources": list(data.get("sources") or [])}
    for index, section in enumerate(data.get("sections", []), start=1):
        doc["sections"].append({
            "id": "c%d" % index,
            "title": (section.get("title") or "Section %d" % index).strip(),
            "points": [str(p).strip() for p in section.get("points", section.get("bullets", [])) if str(p).strip()],
            "metrics": [{"label": m.get("label", ""), "value": str(m.get("value", "")),
                         "note": m.get("note", ""), "source": m.get("source", "")}
                        for m in section.get("metrics", []) if isinstance(m, dict)],
            "sources": [str(s) for s in section.get("sources", [])],
            "notes": section.get("notes", ""),
        })
        doc["sources"].extend(doc["sections"][-1]["sources"])
    return doc


def _content_from_markdown(text, source_name):
    doc = {"title": None, "sections": [], "sources": [], "preamble": ""}
    current = None
    preamble = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group("hashes"))
            label = heading.group("text").strip()
            if level == 1 and doc["title"] is None:
                doc["title"] = label
                continue
            current = {"id": "c%d" % (len(doc["sections"]) + 1), "title": label,
                       "points": [], "metrics": [], "sources": [], "notes": ""}
            doc["sections"].append(current)
            continue
        stripped = BULLET_PREFIX_RE.sub("", line.strip())
        if current is None:
            # text before the first heading is document preamble, never a section
            preamble.append(stripped)
            continue
        metric = METRIC_RE.match(stripped)
        if metric:
            current["metrics"].append({
                "label": metric.group("label").strip(),
                "value": metric.group("value").strip(),
                "note": (metric.group("note") or "").strip().rstrip(")."),
                "source": "",
            })
            continue
        source = SOURCE_RE.match(stripped)
        if source:
            current["sources"].append(source.group("value").strip())
            doc["sources"].append(source.group("value").strip())
            continue
        note = NOTE_RE.match(stripped)
        if note:
            current["notes"] = (current["notes"] + " " + note.group("text").strip()).strip()
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            current["points"].append(bullet.group("text").strip())
            continue
        # plain paragraph: split into sentences so each becomes a candidate point
        for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
            if len(sentence) >= 12:
                current["points"].append(sentence.strip())
    doc["title"] = doc["title"] or source_name
    doc["preamble"] = " ".join(preamble)[:600]
    doc["sources"] = list(dict.fromkeys(doc["sources"]))
    if not doc["sections"]:
        raise pm_core.PMError(
            "No sections found in %s" % source_name,
            code="content-unparsed",
            hint="Use Markdown headings ('## Section') with '- ' bullets, 'Metric: label = 42%' lines, "
                 "and 'Source: ...' lines, or supply a JSON file shaped {\"sections\": [...]}.",
        )
    return doc


# --------------------------------------------------------------------------
# Deck specification
# --------------------------------------------------------------------------
def _roles(config):
    data = pm_core.config_asset(config, "slide_roles")
    return {role["id"]: role for role in data["roles"]}, data["layouts"]


def _theme(config, brief):
    data = pm_core.config_asset(config, "theme_presets")
    wanted = (brief.get("theme") or (brief.get("narrative") or {}).get("theme")
              or config.get("render", {}).get("default_theme") or data.get("default_theme"))
    for theme in data["themes"]:
        if theme["id"] == wanted:
            return theme
    raise pm_core.PMError(
        "Unknown theme '%s'" % wanted, code="theme-unknown",
        hint="Available themes: %s" % ", ".join(t["id"] for t in data["themes"]),
    )


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


def _similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def _match_sections(framework, content):
    """Greedy best-match between framework sections and supplied content."""
    pairs = []
    used = set()
    for fw_index, fw_section in enumerate(framework["sections"]):
        best, best_score = None, 0.0
        for content_section in content.get("sections", []):
            if content_section["id"] in used:
                continue
            score = (_similarity(fw_section["title"], content_section["title"]) * 2.0
                     + _similarity(fw_section["purpose"], content_section["title"])
                     + _similarity(fw_section["title"], " ".join(content_section["points"][:4])) * 0.6)
            if score > best_score:
                best, best_score = content_section, score
        if best is not None and best_score >= 0.12:
            used.add(best["id"])
            pairs.append((fw_index, fw_section, best, round(best_score, 3)))
        else:
            pairs.append((fw_index, fw_section, None, 0.0))
    leftover = [s for s in content.get("sections", []) if s["id"] not in used]
    return pairs, leftover


COLUMN_TITLE_RULES = (
    (("risk", "issue", "mitigation", "concern"), ("Risk", "Response")),
    (("trade", "option", "alternativ", "comparison", "versus", "vs"), ("Option A", "Option B")),
    (("before", "after", "current", "proposed"), ("Before", "After")),
    (("objection",), ("Objection", "Response")),
    (("pro", "con"), ("Strengths", "Weaknesses")),
)


def _column_titles(title, purpose):
    corpus = ("%s %s" % (title or "", purpose or "")).lower()
    for keywords, titles in COLUMN_TITLE_RULES:
        if any(keyword in corpus for keyword in keywords):
            return titles
    return ("Current state", "Proposed state")


def _split_points(points, per_slide):
    chunks = [points[i:i + per_slide] for i in range(0, len(points), per_slide)]
    return chunks or [[]]


def _claim_title(section_title, role_id):
    title = pm_core.sentence_case(section_title)
    suffix = {
        "evidence": " - what the data shows",
        "metrics": " - the numbers",
        "decision": " - the decision we need",
        "process": " - how it works",
        "timeline": " - sequence and dates",
        "comparison": " - options compared",
        "concept": "",
        "summary": " - what happens next",
    }.get(role_id, "")
    return pm_core.truncate(title + suffix, 140)


def build_deck_spec(brief, config, content=None, content_sources=None, framework=None):
    """Create a schema-valid deck specification from a brief and optional content."""
    roles, layouts = _roles(config)
    theme = _theme(config, brief)
    framework = framework or choose_framework(brief, config)[0]
    limits = config["limits"]
    opened = []
    if framework:
        opened.append(pm_core.diag("info", "spec-framework", "Narrative framework: %s" % framework["id"]))
    cta = brief.get("call_to_action") or {}
    wpm = AUDIENCE_EXPERTISE_WORDS_PER_MINUTE.get(brief["audience"]["expertise"], 130)
    target_slides = brief["constraints"]["slides_target"]

    slides = []
    sections = []
    counter = [0]

    def new_slide(role_id, title, **fields):
        counter[0] += 1
        role = roles.get(role_id)
        slide = {
            "id": "%s-%02d" % (role_id, counter[0]),
            "role": role_id,
            "title": pm_core.truncate(title, 200),
            "pacing_seconds": (role or {}).get("default_pacing_seconds", 60),
            "notes": "",
        }
        slide.update({k: v for k, v in fields.items() if v not in (None, [], "", {})})
        slides.append(slide)
        return slide

    organization = brief.get("organization") or brief.get("security", {}).get("organization", "")
    subtitle_bits = [brief["audience"]["primary"]]
    if brief.get("narrative", {}).get("event_date"):
        subtitle_bits.append(brief["narrative"]["event_date"])
    elif brief.get("delivery", {}).get("date"):
        subtitle_bits.append(brief["delivery"]["date"])
    if brief["delivery"].get("duration_minutes"):
        subtitle_bits.append("%d minutes" % brief["delivery"]["duration_minutes"])

    title_slide = new_slide("title", brief["title"], subtitle=" | ".join(b for b in subtitle_bits if b),
                            organization=organization)
    title_slide["notes"] = (
        "Open with the promise: %s. State the audience (%s), the time available (%d minutes), and the "
        "decision or action you want by the end." % (
            brief["title"], brief["audience"]["primary"], brief["delivery"]["duration_minutes"]))

    if framework is None:
        framework = {"sections": [], "default_pacing_seconds": 75, "id": "custom", "call_to_action": ""}

    pairs, leftover = ([], [])
    if content:
        pairs, leftover = _match_sections(framework, content)

    section_records = []
    fw_count = len(framework["sections"])
    # Section dividers only when the time and slide budget can afford them.
    use_dividers = fw_count >= 5 and target_slides >= fw_count + 4
    for index, fw_section in enumerate(framework["sections"], start=1):
        match = None
        match_score = 0.0
        if pairs:
            for fw_index, fw, content_section, score in pairs:
                if fw_index == index - 1:
                    match, match_score = content_section, score
                    break
        section_id = "s%02d" % index
        record = {
            "id": section_id,
            "title": (match["title"] if match else fw_section["title"]),
            "purpose": fw_section["purpose"],
            "slide_ids": [],
        }
        section_records.append(record)

        if use_dividers and index > 1:
            divider = new_slide("section", "%d - %s" % (index, record["title"]),
                                subtitle=fw_section["purpose"], section_id=section_id)
            record["slide_ids"].append(divider["id"])

        role_id = fw_section["role"]
        role = roles.get(role_id, roles["concept"])
        points = list(match["points"]) if match else []
        metrics = list(match["metrics"]) if match else []
        sources = list(match["sources"]) if match else []
        extra = {}
        if role_id == "metrics" and metrics:
            extra["metrics"] = metrics[:4]
            if len(points) > 2:
                extra["notes"] = ("Say: " + " ".join(pm_core.sentence_case(p) for p in points[2:5])
                                  + " Time budget: about 70 seconds.")
        elif role_id == "quote":
            extra["quote"] = {"text": (points[0] if points else record["title"]),
                              "attribution": (match.get("notes") or "Source to confirm") if match else ""}
        elif role_id == "timeline":
            phases = []
            for point in points[:6]:
                date_match = re.match(r"^(Q[1-4]\s*\d{0,4}|\d{4}-\d{2}(?:-\d{2})?|[A-Z][a-z]{2,9}\s+\d{1,2}|\d{1,2}\s+[A-Z][a-z]{2,9})\s*[:\-]\s*(?P<rest>.+)$", point)
                if date_match:
                    phases.append({"date": date_match.group(1), "label": pm_core.truncate(date_match.group("rest"), 60)})
                else:
                    phases.append({"label": pm_core.truncate(point, 60), "date": "TBC"})
            extra["phases"] = phases[:6]
        elif role_id == "comparison":
            half = max(1, (len(points) + 1) // 2)
            left_title, right_title = _column_titles(record["title"], fw_section["purpose"])
            extra["left_title"] = left_title
            extra["right_title"] = right_title
            extra["left"] = [pm_core.truncate(p, 160) for p in points[:half]][:4]
            extra["right"] = [pm_core.truncate(p, 160) for p in points[half:]][:4]
            if points:
                opened.append(pm_core.diag(
                    "info", "plan-comparison-split",
                    "Comparison slide '%s' splits the supplied points into '%s' and '%s'; "
                    "rewrite the two columns so each side is one coherent argument."
                    % (record["title"], left_title, right_title), where=section_id))
        elif role_id == "table":
            headers = ["Item", "Detail", "Owner", "Date"]
            rows = []
            for point in points[:6]:
                parts = [p.strip() for p in re.split(r"\s*[|;]\s*", point)][:4]
                while len(parts) < 4:
                    parts.append("")
                rows.append([pm_core.truncate(p, 70) for p in parts])
            extra["table"] = {"headers": headers, "rows": rows}
        elif role_id in ("process", "agenda"):
            steps = [pm_core.truncate(p, 120) for p in points[:6]] or [s["title"] for s in [fw_section]]
            extra["steps"] = steps
        else:
            capacity = min(role.get("max_bullets", 5), limits["max_bullets_per_slide"])
            chunks = _split_points(points, capacity) if points else [[]]
            extra["bullets"] = [pm_core.truncate(p, limits["max_chars_per_bullet"]) for p in chunks[0]]
            extra["_overflow"] = chunks[1:]

        if sources:
            extra["sources"] = sources[:8]
        if match and match.get("notes"):
            extra["notes"] = match["notes"]

        slide = new_slide(role_id, _claim_title(record["title"], role_id), section_id=section_id, **extra)
        record["slide_ids"].append(slide["id"])
        slide.pop("_overflow", None)

        # spill overflow points into additional slides until the target is reached
        overflow = extra.get("_overflow") or []
        for chunk in overflow:
            if len(slides) >= target_slides and len(slides) >= 6:
                break
            more = new_slide(role_id, "%s (continued)" % _claim_title(record["title"], role_id),
                             section_id=section_id,
                             bullets=[pm_core.truncate(p, limits["max_chars_per_bullet"]) for p in chunk])
            record["slide_ids"].append(more["id"])

        if role_id in ("evidence", "metrics") and not sources and not comments_disabled(brief):
            opened.append(pm_core.diag(
                "warning", "plan-evidence-unsourced",
                "Section '%s' is an evidence slide with no source; audit will block it" % record["title"],
                where=slide["id"],
                hint="Add a 'Source: ...' line to the matching content section.",
            ))

    opening_slide_ids = [title_slide["id"]]

    # Agenda slide, inserted after the title
    if len(section_records) >= 3:
        agenda = {
            "id": "agenda-01", "role": "agenda", "title": "Agenda",
            "steps": [record["title"] for record in section_records][:6],
            "pacing_seconds": 40,
            "notes": "Walk the route once, name the decision point, and give the time budget.",
        }
        slides.insert(1, agenda)
        opening_slide_ids.append(agenda["id"])

    # Call to action closing slide
    decision = cta.get("decision") or framework.get("call_to_action") or "Decide the next step"
    decision = decision.replace("{recommendation}", brief["title"]).replace("{owner}",
                                                                          cta.get("owner") or "owner")
    decision = decision.replace("{deadline}", cta.get("deadline") or "an agreed date")
    decision = decision.replace("{action}", brief["title"]).replace("{date}", cta.get("deadline") or "the agreed date")
    decision = decision.replace("{start_date}", cta.get("deadline") or "the agreed date")
    closing = new_slide("decision", "The decision we need today",
                        bullets=[pm_core.truncate(decision, 160),
                                 "Owner: %s" % (cta.get("owner") or "to confirm"),
                                 "By: %s" % (cta.get("deadline") or "to confirm")],
                        pacing_seconds=90)
    closing["notes"] = ("Restate the ask in one sentence, name the owner, and confirm the date out loud. "
                        "Then stop talking and take the decision.")

    # Appendix
    all_sources = list(content.get("sources", [])) if content else []
    for slide in slides:
        all_sources.extend(slide.get("sources", []))
    all_sources = list(dict.fromkeys([s for s in all_sources if s]))
    if all_sources:
        appendix = new_slide("appendix", "Sources and references",
                            bullets=[pm_core.truncate(s, 220) for s in all_sources[:7]],
                            hidden=True, pacing_seconds=0)
        appendix["notes"] = "Hidden backup slide. Show it only when a source is challenged."
        sections.append({"id": "appendix", "title": "Appendix",
                         "purpose": "Backup detail and sources", "slide_ids": [appendix["id"]]})

    sections.insert(0, {"id": "opening", "title": "Opening",
                        "purpose": "Title and route through the deck",
                        "slide_ids": opening_slide_ids})
    for record in section_records:
        if record["slide_ids"]:
            sections.append(record)

    # pacing budget
    budget = brief["delivery"]["duration_minutes"] * 60
    total = sum(s.get("pacing_seconds", 0) for s in slides)
    if total > budget and total > 0:
        scale = budget / float(total)
        for slide in slides:
            slide["pacing_seconds"] = int(round(slide.get("pacing_seconds", 0) * scale))
        opened.append(pm_core.diag(
            "warning", "plan-pacing-trimmed",
            "Planned pacing exceeded the %d-minute budget and was scaled by %.2f"
            % (brief["delivery"]["duration_minutes"], scale),
            hint="Cut slides or increase delivery.duration_minutes.",
        ))

    spec = {
        "spec_version": pm_core.SPEC_VERSION,
        "deck": {
            "title": brief["title"],
            "subtitle": brief.get("purpose_detail") or "",
            "organization": organization or "",
            "author": brief.get("author") or "",
            "audience": brief["audience"]["primary"],
            "purpose": brief.get("purpose", ""),
            "delivery_mode": brief["delivery"]["mode"],
            "platform": (brief.get("platform") or {}).get("preferred") or "powerpoint",
            "language": brief["audience"]["language"],
            "duration_minutes": brief["delivery"]["duration_minutes"],
            "framework": framework.get("id", "custom"),
            "theme_id": theme["id"],
            "classification": "confidential" if brief["security"].get("confidential") else
                              ("public" if brief["security"].get("public_publishing") else "internal"),
            "reduced_motion": bool(brief["accessibility"].get("reduced_motion")),
        },
        "theme": theme,
        "call_to_action": {"decision": decision, "owner": cta.get("owner", ""), "deadline": cta.get("deadline", "")},
        "sections": sections,
        "slides": slides,
        "sources": all_sources,
        "assumptions": brief_assumptions(brief) + [
            "Narrative framework '%s' was selected by score, not by human review." % framework.get("id"),
            "Speaker notes are drafts generated from the supplied bullets; they need a human read before recording.",
        ],
        "review_checkpoints": brief.get("review_checkpoints", REVIEW_CHECKPOINTS),
        "provenance": {
            "generated_by": "%s %s" % (pm_core.TOOL_NAME, pm_core.TOOL_VERSION),
            "generated_at": pm_core.now_iso(),
            "brief_title": brief["title"],
            "content_files": content_sources or [],
        },
    }
    if content:
        if leftover:
            opened.append(pm_core.diag(
                "info", "plan-content-unused",
                "%d supplied content section(s) were not mapped to the narrative: %s"
                % (len(leftover), ", ".join(s["title"] for s in leftover[:6])),
                hint="Add them to the appendix content or adjust the framework.",
            ))
        if sum(1 for s in slides if s.get("bullets") or s.get("metrics") or s.get("steps")) < 3:
            opened.append(pm_core.diag("warning", "plan-thin-content",
                                       "Fewer than three content-bearing slides were produced"))
    else:
        opened.append(pm_core.diag("info", "plan-no-content",
                                   "No content file supplied: slides carry the narrative skeleton only"))

    if len(slides) > target_slides * 1.25:
        opened.append(pm_core.diag(
            "warning", "plan-over-target",
            "Produced %d slides against a target of %d" % (len(slides), target_slides),
            hint="Reduce sections, raise constraints.slides_target, or set delivery.duration_minutes "
                 "to the real time available."))

    draft_notes(spec)
    spec["stats"] = spec_stats(spec)
    return spec, opened


def comments_disabled(brief):  # pragma: no cover - compatibility hook
    return bool((brief.get("narrative") or {}).get("suppress_warnings"))


def draft_notes(spec):
    """Fill speaker notes deterministically where they are still empty."""
    for slide in spec.get("slides", []):
        if slide.get("notes"):
            continue
        bits = []
        body = slide.get("bullets") or slide.get("steps") or []
        if slide.get("metrics"):
            body = ["%s: %s %s" % (m.get("label", ""), m.get("value", ""), m.get("note", "")) for m in slide["metrics"]]
        if slide.get("quote"):
            body = ["\"%s\" - %s" % (slide["quote"].get("text", ""), slide["quote"].get("attribution", ""))]
        if slide.get("table"):
            body = [" ".join(row) for row in slide["table"].get("rows", [])]
        if body:
            bits.append("Say: " + " ".join(pm_core.sentence_case(str(b)) for b in body[:3]))
        if slide.get("sources"):
            bits.append("Evidence: " + "; ".join(slide["sources"][:2]))
        bits.append("Time budget: about %d seconds." % slide.get("pacing_seconds", 60))
        if slide.get("role") in ("evidence", "metrics", "decision"):
            bits.append("Pause for questions before moving on.")
        slide["notes"] = " ".join(bits)
    return spec


def spec_stats(spec):
    slides = spec.get("slides", [])
    words = 0
    bullets = 0
    for slide in slides:
        for key in ("bullets", "left", "right", "steps"):
            for item in slide.get(key, []) or []:
                words += pm_core.word_count(item)
                bullets += 1
        for metric in slide.get("metrics", []) or []:
            words += pm_core.word_count(metric.get("label", "")) + 1
        words += pm_core.word_count(slide.get("notes", "") or "") // 4
    pacing = sum(s.get("pacing_seconds", 0) for s in slides)
    return {
        "slide_count": len(slides),
        "section_count": len(spec.get("sections", [])),
        "hidden_slide_count": sum(1 for s in slides if s.get("hidden")),
        "bullet_count": bullets,
        "on_slide_word_count": words,
        "planned_seconds": pacing,
        "planned_minutes": round(pacing / 60.0, 1),
        "source_count": len(spec.get("sources", [])),
    }


def pacing_plan(spec):
    """Per-slide and per-section time budget for rehearsal."""
    rows = []
    cumulative = 0
    for index, slide in enumerate(spec.get("slides", []), start=1):
        seconds = int(slide.get("pacing_seconds", 0) or 0)
        cumulative += seconds
        rows.append({
            "order": index,
            "slide_id": slide.get("id"),
            "role": slide.get("role"),
            "title": slide.get("title"),
            "seconds": seconds,
            "cumulative_seconds": cumulative,
            "speakers_notes_words": pm_core.word_count(slide.get("notes", "") or ""),
        })
    return {"slides": rows, "total_seconds": cumulative, "total_minutes": round(cumulative / 60.0, 1)}


if __name__ == "__main__":  # pragma: no cover - module smoke test
    cfg = pm_core.load_config()
    brief, _ = load_brief("asset-example-brief.json", cfg)
    spec, _diags = build_deck_spec(brief, cfg)
    print("slides", len(spec["slides"]), "sections", len(spec["sections"]))
