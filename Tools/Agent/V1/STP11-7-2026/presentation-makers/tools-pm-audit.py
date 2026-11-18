#!/usr/bin/env python3
"""tools-pm-audit.py - accessibility, integrity, brand, privacy, and delivery audit.

Load order: after tools-pm-core.py, tools-pm-intake.py, tools-pm-plan.py and
tools-pm-render.py; before tools-pm-package.py.

Public API
----------
audit(spec, config, build_dir=None, brief=None) -> report dict
    report = {status, score, gates[], findings[], counts, evaluated[], summary}

Each finding is a structured diagnostic with a gate id, so the same report can
drive a machine decision (exit code), a Markdown report, or a benchmark score
between candidate decks.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import zipfile
from datetime import date, datetime

for _name, _file in (("pm_core", "tools-pm-core.py"), ("pm_intake", "tools-pm-intake.py"),
                     ("pm_plan", "tools-pm-plan.py"), ("pm_render", "tools-pm-render.py")):
    if _name not in sys.modules:  # pragma: no cover - import shim
        _dir = os.path.dirname(os.path.abspath(__file__))
        _spec = importlib.util.spec_from_file_location(_name, os.path.join(_dir, _file))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _mod
        _spec.loader.exec_module(_mod)
import pm_core  # noqa: E402
import pm_plan  # noqa: E402
import pm_render  # noqa: E402

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\d.\-])(?:\+\d{1,3}[\s\-]?)?(?:\(\d{2,4}\)[\s\-]?)?\d{3,4}[\s\-]\d{3,5}(?![\d.\-])")
YEAR_RANGE_RE = re.compile(r"^(?:19|20)\d{2}\s*[\-–/]\s*(?:19|20)\d{2}$")


def looks_like_phone(value):
    """Reject year ranges, decimals, and number sequences with too few digits."""
    candidate = value.strip()
    if YEAR_RANGE_RE.match(candidate):
        return False
    digits = re.sub(r"\D", "", candidate)
    if len(digits) < 7 or len(digits) > 15:
        return False
    if re.match(r"^(?:19|20)\d{2}\s*[\-–]", candidate):
        return False
    return True
EXTERNAL_REF_RE = re.compile(r"""(?:src|href)\s*=\s*["'](?P<url>[^"'#][^"']*)["']""", re.I)

ROLE_LIMITS_FALLBACK = {"max_bullets": 6, "max_words_per_bullet": 18}


class Audit:
    """Accumulates findings and the set of checks that actually ran."""

    def __init__(self, spec, config, gates_asset):
        self.spec = spec
        self.config = config
        self.findings = []
        self.evaluated = set()
        self.gate_of = {}
        for gate in gates_asset.get("gates", []):
            for code in gate.get("checks", []):
                self.gate_of.setdefault(code, gate["id"])

    def add(self, level, code, message, where=None, hint=None):
        self.evaluated.add(code)
        record = pm_core.diag(level, code, message, where=where, hint=hint)
        record["gate"] = self.gate_of.get(code, "ungated")
        self.findings.append(record)
        return record

    def ok(self, code):
        """Record that a check was evaluated and produced no finding."""
        self.evaluated.add(code)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def slide_text(slide):
    parts = [slide.get("title", ""), slide.get("subtitle", ""), slide.get("notes", "")]
    for key in ("bullets", "left", "right", "steps", "sources"):
        parts.extend(str(item) for item in slide.get(key, []) or [])
    for metric in slide.get("metrics", []) or []:
        parts.extend(str(metric.get(k, "")) for k in ("label", "value", "note", "source"))
    if slide.get("table"):
        parts.extend(str(h) for h in slide["table"].get("headers", []))
        for row in slide["table"].get("rows", []):
            parts.extend(str(c) for c in row)
    if slide.get("quote"):
        parts.extend(str(slide["quote"].get(k, "")) for k in ("text", "attribution"))
    for evidence in slide.get("evidence", []) or []:
        parts.extend(str(evidence.get(k, "")) for k in ("claim", "source", "url"))
    for link in slide.get("links", []) or []:
        parts.extend(str(link.get(k, "")) for k in ("label", "url"))
    if slide.get("image"):
        parts.extend(str(slide["image"].get(k, "")) for k in ("path", "alt_text", "rights", "credit"))
    return " \n".join(p for p in parts if p)


def _roles(config):
    data = pm_core.config_asset(config, "slide_roles")
    return {role["id"]: role for role in data["roles"]}


# --------------------------------------------------------------------------
# Check groups
# --------------------------------------------------------------------------
def check_narrative(audit):
    spec = audit.spec
    slides = spec.get("slides", [])
    sections = spec.get("sections", [])
    roles = [s.get("role") for s in slides]

    decision = (spec.get("call_to_action") or {}).get("decision", "").strip()
    if decision or "decision" in roles:
        audit.ok("narrative.cta-missing")
    else:
        audit.add("error", "narrative.cta-missing",
                  "No call to action: add spec.call_to_action.decision or a decision-role slide",
                  hint="A deck without an explicit ask cannot be acted on.")

    if len(sections) >= 2:
        audit.ok("narrative.sections-missing")
    else:
        audit.add("warning", "narrative.sections-missing",
                  "The deck has %d section(s); a structured deck usually needs at least two" % len(sections))

    visible = [s for s in slides if not s.get("hidden")]
    if len(visible) >= 6 and "agenda" not in roles:
        audit.add("warning", "narrative.agenda-missing",
                  "No agenda slide in a %d-slide deck" % len(visible),
                  hint="Add an agenda slide, or accept that the route is only in the speaker notes.")
    else:
        audit.ok("narrative.agenda-missing")

    if "summary" in roles or "decision" in roles or len(visible) < 5:
        audit.ok("narrative.summary-missing")
    else:
        audit.add("warning", "narrative.summary-missing",
                  "No summary or decision slide closes the argument",
                  hint="End on the action, not on a content slide.")

    unsourced = 0
    for slide in slides:
        if slide.get("role") in ("evidence", "metrics"):
            has_source = bool(slide.get("sources")) or bool(slide.get("evidence")) or any(
                m.get("source") for m in slide.get("metrics", []) or [])
            if not has_source:
                unsourced += 1
                audit.add("error", "narrative.evidence-unsourced",
                          "Evidence slide '%s' carries no source" % slide.get("title", slide.get("id")),
                          where=slide.get("id"),
                          hint="Add slide.sources, slide.evidence[].source, or metrics[].source.")
    if not unsourced:
        audit.ok("narrative.evidence-unsourced")


def check_data(audit, today=None):
    today = today or date.today()
    slides = audit.spec.get("slides", [])
    any_data = False
    for slide in slides:
        data = slide.get("data") or {}
        metrics = slide.get("metrics") or []
        if data or metrics:
            any_data = True
        if data:
            source = data.get("source") or (slide.get("sources") or [""])[0]
            if source:
                audit.ok("data.missing-source")
            else:
                audit.add("error", "data.missing-source",
                          "Data slide '%s' has no source" % slide.get("title", slide.get("id")),
                          where=slide.get("id"))
            if not slide.get("sources") and any(m.get("source") for m in metrics):
                audit.ok("data.missing-source")
            if data.get("percent_parts"):
                total = sum(float(v) for v in data["percent_parts"])
                if abs(total - 100.0) > 0.5:
                    audit.add("warning", "data.percent-sum",
                              "Percent parts on '%s' sum to %.1f, not 100" % (slide.get("title"), total),
                              where=slide.get("id"))
                else:
                    audit.ok("data.percent-sum")
            chart = (data.get("chart_type") or "none").lower()
            if chart in ("bar", "line", "area"):
                if data.get("y_axis_starts_at_zero") is False:
                    audit.add("warning", "data.truncated-axis",
                              "Chart on '%s' does not start at zero; label the axis cut explicitly"
                              % slide.get("title"), where=slide.get("id"))
                elif data.get("y_axis_starts_at_zero") is None:
                    audit.add("warning", "data.missing-baseline",
                              "Chart on '%s' does not declare its baseline" % slide.get("title"),
                              where=slide.get("id"),
                              hint="Set data.y_axis_starts_at_zero true or false deliberately.")
                else:
                    audit.ok("data.missing-baseline")
                    audit.ok("data.truncated-axis")
            if data.get("as_of"):
                try:
                    as_of = datetime.strptime(str(data["as_of"])[:10], "%Y-%m-%d").date()
                    age = (today - as_of).days
                    if age > 365:
                        audit.add("warning", "data.stale",
                                  "Data on '%s' is %d days old (as of %s)"
                                  % (slide.get("title"), age, data["as_of"]), where=slide.get("id"))
                    else:
                        audit.ok("data.stale")
                except ValueError:
                    audit.add("warning", "data.stale",
                              "Data date '%s' on '%s' is not ISO formatted" % (data["as_of"], slide.get("title")),
                              where=slide.get("id"))
            else:
                audit.ok("data.stale")
        table = slide.get("table") or {}
        if table:
            rows = table.get("rows", [])
            headers = table.get("headers", [])
            cols = max([len(r) for r in rows] + [len(headers)])
            if len(rows) > 6 or cols > 4:
                audit.add("warning", "data.table-too-dense",
                          "Table on '%s' is %dx%d; six rows by four columns reads reliably"
                          % (slide.get("title"), len(rows), cols), where=slide.get("id"))
            else:
                audit.ok("data.table-too-dense")
            if not headers:
                audit.add("error", "a11y.table-header-missing",
                          "Table on '%s' has no header row" % slide.get("title"), where=slide.get("id"))
            else:
                audit.ok("a11y.table-header-missing")
    for code in ("data.missing-source", "data.percent-sum", "data.stale", "data.table-too-dense",
                 "data.truncated-axis", "data.missing-baseline", "a11y.table-header-missing"):
        if code not in audit.evaluated:
            audit.ok(code)
    if not any_data:
        audit.ok("data.missing-source")


def check_accessibility(audit):
    spec = audit.spec
    config = audit.config
    theme = spec.get("theme") or {}
    audit_cfg = config.get("audit", {})
    sizes = theme.get("typography_pt") or {}

    for slide in spec.get("slides", []):
        title = (slide.get("title") or "").strip()
        if len(title) >= 3:
            audit.ok("a11y.missing-title")
        else:
            audit.add("error", "a11y.missing-title",
                      "Slide %s has no usable title" % slide.get("id"),
                      where=slide.get("id"),
                      hint="Every slide needs a title: screen readers announce it as the slide name.")

        image = slide.get("image")
        if image:
            if image.get("decorative") and image.get("alt_text"):
                audit.add("warning", "a11y.decorative-conflict",
                          "Slide '%s' marks the image decorative but also gives alt text" % title,
                          where=slide.get("id"))
            elif image.get("decorative") or (image.get("alt_text") or "").strip():
                audit.ok("a11y.missing-alt-text")
            else:
                audit.add("error", "a11y.missing-alt-text",
                          "Image on slide '%s' has no alt text and is not marked decorative" % title,
                          where=slide.get("id"),
                          hint="Write what the image conveys, or set image.decorative = true.")
        else:
            audit.ok("a11y.missing-alt-text")

        order = slide.get("reading_order")
        if order:
            if order[0].lower() not in ("title", "heading", title.lower()):
                audit.add("warning", "a11y.reading-order",
                          "Reading order on '%s' does not start with the title" % title, where=slide.get("id"))
            elif len(order) != len(set(order)):
                audit.add("warning", "a11y.reading-order",
                          "Reading order on '%s' repeats an entry" % title, where=slide.get("id"))
            else:
                audit.ok("a11y.reading-order")
        else:
            audit.ok("a11y.reading-order")

        for media in slide.get("media", []) or []:
            if media.get("kind") in ("audio", "video", "screen-recording") and not media.get("captions"):
                audit.add("error", "a11y.media-caption-missing",
                          "Media on '%s' has no captions file" % title, where=slide.get("id"),
                          hint="Supply media.captions (VTT or SRT path) or remove the media.")
            else:
                audit.ok("a11y.media-caption-missing")
            if media.get("autoplay"):
                audit.add("warning", "a11y.autoplay-media",
                          "Media on '%s' autoplays; it may start before the room is ready" % title,
                          where=slide.get("id"))
            else:
                audit.ok("a11y.autoplay-media")
    for code in ("a11y.media-caption-missing", "a11y.autoplay-media", "a11y.decorative-conflict",
                 "a11y.table-header-missing"):
        if code not in audit.evaluated:
            audit.ok(code)

    # contrast math on the theme actually used
    pairs = [
        ("text on background", theme.get("text_primary"), theme.get("background"), False, "error"),
        ("secondary text on background", theme.get("text_secondary"), theme.get("background"), False, "error"),
        ("text on accent", theme.get("text_on_accent"), theme.get("accent"), False, "error"),
        ("accent graphic on background", theme.get("accent"), theme.get("background"), True, "warning"),
        ("alt accent graphic on background", theme.get("accent_alt"), theme.get("background"), True, "warning"),
    ]
    for label, fg, bg, large, level in pairs:
        try:
            result = pm_core.wcag_result(pm_core.contrast_ratio(fg, bg), large, config)
        except pm_core.PMError as exc:
            audit.add("error", "a11y.contrast-text", "Theme colour invalid for %s: %s" % (label, exc.message))
            continue
        code = "a11y.contrast-graphic" if large else "a11y.contrast-text"
        if result["passes"]:
            audit.ok(code)
        else:
            audit.add(level, code, "%s is %.2f:1, below the %.1f:1 requirement"
                      % (label, result["ratio"], result["required"]),
                      hint="Darken or lighten the pair in the theme, then re-run audit.")

    minimum = float(audit_cfg.get("min_body_font_pt", 14))
    body = float(sizes.get("body", 18))
    caption = float(sizes.get("caption", 12))
    if body >= minimum:
        audit.ok("a11y.font-size-small")
    else:
        audit.add("error", "a11y.font-size-small",
                  "Body type is %.0fpt, below the %.0fpt minimum" % (body, minimum),
                  hint="Raise theme.typography_pt.body.")
    if caption < 10:
        audit.add("warning", "a11y.font-size-small",
                  "Caption type is %.0fpt; 10pt is the practical floor on a projector" % caption)
    flashes = 0
    for slide in spec.get("slides", []):
        for anim in slide.get("animation", []) or []:
            if (anim.get("flashes_per_second") or 0) > audit_cfg.get("max_flashes_per_second", 3):
                flashes += 1
                audit.add("error", "a11y.motion-flash-risk",
                          "Animation on '%s' flashes %.1f times per second"
                          % (slide.get("title"), anim["flashes_per_second"]), where=slide.get("id"))
    if not flashes:
        audit.ok("a11y.motion-flash-risk")


def check_layout(audit):
    config = audit.config
    spec = audit.spec
    roles = _roles(config)
    limits = config.get("limits", {})
    audit_cfg = config.get("audit", {})
    layouts = pm_render.layout_deck(spec)

    for slide in spec.get("slides", []):
        role_id = slide.get("role", "concept")
        role = roles.get(role_id, {})
        bullets = slide.get("bullets") or []
        max_bullets = min(role.get("max_bullets", limits.get("max_bullets_per_slide", 7)),
                          limits.get("max_bullets_per_slide", 7))
        if len(bullets) > max_bullets:
            audit.add("warning", "density.bullets-exceeded",
                      "Slide '%s' has %d bullets, above the %d allowed for role '%s'"
                      % (slide.get("title"), len(bullets), max_bullets, role_id),
                      where=slide.get("id"),
                      hint="Split the slide or move detail into the notes.")
        else:
            audit.ok("density.bullets-exceeded")

        max_chars = limits.get("max_chars_per_bullet", 180)
        long_bullets = [b for b in bullets if len(b) > max_chars]
        if long_bullets:
            audit.add("warning", "density.bullet-length",
                      "Slide '%s' has a bullet of %d characters (limit %d)"
                      % (slide.get("title"), len(long_bullets[0]), max_chars),
                      where=slide.get("id"))
        else:
            audit.ok("density.bullet-length")

        words = pm_core.word_count(" ".join(bullets))
        pacing = int(slide.get("pacing_seconds", 0) or 0)
        if pacing > 0 and words:
            wpm = words / (pacing / 60.0)
            if wpm > limits.get("max_words_per_minute", 180):
                audit.add("warning", "density.words-per-minute",
                          "Slide '%s' speaks at %d words per minute against a %d limit"
                          % (slide.get("title"), int(wpm), limits["max_words_per_minute"]),
                          where=slide.get("id"))
            else:
                audit.ok("density.words-per-minute")
        else:
            audit.ok("density.words-per-minute")

        # geometric overflow estimation on the real layout model
        overflow = False
        for frame in layouts.get(slide.get("id"), []):
            if frame["kind"] != "text" or not frame["paragraphs"]:
                continue
            needed = 0.0
            for para in frame["paragraphs"]:
                prefix = 0.06 if para.get("bullet") else 0.0
                needed += (pm_core.text_height_in(para["text"], frame["w"] - 0.12 - prefix, para["size"])
                           + para.get("space_after", 4) / 72.0)
            if needed > frame["h"] * 1.02:
                overflow = True
                audit.add("error", "density.overflow-risk",
                          "Text is likely to overflow on '%s' (needs %.2fin, box is %.2fin)"
                          % (slide.get("title"), needed, frame["h"]),
                          where=slide.get("id"),
                          hint="Shorten the text; the renderer shrinks type only to the %.0fpt floor."
                               % pm_render.MIN_BODY_PT)
                break
        if not overflow:
            audit.ok("density.overflow-risk")

        off_slide = [f for f in layouts.get(slide.get("id"), [])
                     if f["x"] < -0.01 or f["y"] < -0.01
                     or f["x"] + f["w"] > pm_render.SLIDE_W_IN + 0.02
                     or f["y"] + f["h"] > pm_render.SLIDE_H_IN + 0.02]
        if off_slide:
            audit.add("error", "geometry.off-slide",
                      "%d frame(s) fall outside the slide on '%s'" % (len(off_slide), slide.get("title")),
                      where=slide.get("id"))
        else:
            audit.ok("geometry.off-slide")

        if len(layouts.get(slide.get("id"), [])) > 40:
            audit.add("warning", "geometry.too-many-objects",
                      "Slide '%s' uses %d frames; simplify the composition"
                      % (slide.get("title"), len(layouts.get(slide.get("id"), []))),
                      where=slide.get("id"))
        else:
            audit.ok("geometry.too-many-objects")

        count = len(slide.get("animation") or [])
        if count > audit_cfg.get("max_animation_objects_per_slide", 8):
            audit.add("warning", "motion.excessive-objects",
                      "Slide '%s' animates %d objects" % (slide.get("title"), count), where=slide.get("id"))
        else:
            audit.ok("motion.excessive-objects")


def check_brand(audit, brief=None):
    spec = audit.spec
    theme = spec.get("theme") or {}
    constraints = (brief or {}).get("constraints") or {}
    allowed_fonts = constraints.get("approved_fonts") or spec.get("deck", {}).get("approved_fonts")
    fonts = theme.get("fonts") or {}
    if allowed_fonts:
        used = [fonts.get("heading", ""), fonts.get("body", "")]
        offenders = [f for f in used if f and not any(allowed.lower() in f.lower() for allowed in allowed_fonts)]
        if offenders:
            audit.add("warning", "brand.font-not-allowed",
                      "Font stack '%s' is outside the approved list (%s)"
                      % (offenders[0].split(",")[0], ", ".join(allowed_fonts)),
                      hint="Swap the theme fonts for an approved family or record an exception.")
        else:
            audit.ok("brand.font-not-allowed")
    else:
        audit.ok("brand.font-not-allowed")

    allowed_colors = constraints.get("approved_colors")
    if allowed_colors:
        palette = [theme.get(k) for k in ("background", "text_primary", "accent", "accent_alt")]
        approved = {pm_core.to_hex(pm_core.parse_hex(c)).upper() for c in allowed_colors}
        offenders = [c for c in palette if c and pm_core.to_hex(pm_core.parse_hex(c)).upper() not in approved]
        if offenders:
            audit.add("warning", "brand.color-not-in-theme",
                      "%d theme colour(s) are outside the approved palette: %s"
                      % (len(offenders), ", ".join(offenders)))
        else:
            audit.ok("brand.color-not-in-theme")
    else:
        audit.ok("brand.color-not-in-theme")

    title_slide = next((s for s in spec.get("slides", []) if s.get("role") == "title"), None)
    if constraints.get("logo_required"):
        if title_slide and (title_slide.get("image") or title_slide.get("logo")):
            audit.ok("brand.logo-missing")
        else:
            audit.add("warning", "brand.logo-missing",
                      "The brief requires a logo but the title slide carries no logo or image",
                      hint="Add title-slide image (or logo) with alt text before publishing.")
    else:
        audit.ok("brand.logo-missing")

    if spec.get("deck", {}).get("organization"):
        audit.ok("brand.footer-missing")
    else:
        audit.add("info", "brand.footer-missing",
                  "No organisation set, so rendered footers are empty",
                  hint="Set deck.organization in the brief or spec.")


def check_privacy(audit, brief=None):
    spec = audit.spec
    hits_email, hits_phone, external_links, hidden = [], [], [], []
    for slide in spec.get("slides", []):
        text = slide_text(slide)
        for match in EMAIL_RE.findall(text):
            hits_email.append((slide.get("id"), match))
        for match in PHONE_RE.findall(text):
            if looks_like_phone(match):
                hits_phone.append((slide.get("id"), match))
        for link in slide.get("links", []) or []:
            if link.get("kind") == "external" or str(link.get("url", "")).startswith("http"):
                external_links.append((slide.get("id"), link.get("url", "")))
        if slide.get("hidden"):
            hidden.append(slide.get("id"))

    if hits_email:
        audit.add("error", "privacy.pii-email",
                  "%d email address(es) appear in the deck: %s"
                  % (len(hits_email), ", ".join(sorted({h[1] for h in hits_email})[:3])),
                  where=hits_email[0][0],
                  hint="Remove or replace them, or record an explicit approval.")
    else:
        audit.ok("privacy.pii-email")
    if hits_phone:
        audit.add("warning", "privacy.pii-phone",
                  "%d phone-number-like string(s) appear in the deck" % len(hits_phone),
                  where=hits_phone[0][0])
    else:
        audit.ok("privacy.pii-phone")

    classification = spec.get("deck", {}).get("classification", "internal")
    if classification in ("confidential", "restricted"):
        audit.add("info", "privacy.confidential-marker",
                  "Deck is classified '%s': confirm the confidentiality marker is visible before sharing"
                  % classification)
    else:
        audit.ok("privacy.confidential-marker")

    if external_links:
        audit.add("warning", "privacy.external-link-review",
                  "%d external link(s) need a live check and an owner: %s"
                  % (len(external_links), ", ".join(url for _, url in external_links[:3])),
                  hint="Confirm each link resolves without a login wall before delivery.")
    else:
        audit.ok("privacy.external-link-review")

    reviewable = []
    for slide in spec.get("slides", []):
        if not slide.get("hidden"):
            continue
        body = slide.get("bullets") or []
        only_sources = slide.get("role") == "appendix" and all(
            str(item) in (spec.get("sources") or []) or len(str(item)) > 0 for item in body) and \
            not slide.get("metrics") and not slide.get("table") and not slide.get("image")
        if only_sources:
            audit.add("info", "privacy.hidden-slide-review",
                      "Hidden backup slide '%s' ships in the file; keep it free of restricted material"
                      % slide.get("title"), where=slide.get("id"))
        else:
            reviewable.append(slide.get("id"))
    if reviewable:
        audit.add("warning", "privacy.hidden-slide-review",
                  "%d hidden slide(s) ship inside the file and can be revealed" % len(reviewable),
                  where=", ".join(reviewable[:4]),
                  hint="Remove anything you would not show, rather than hiding it.")

    if (brief or {}).get("security", {}).get("retention_days"):
        audit.add("info", "privacy.metadata",
                  "Retention set to %d days; schedule deletion and link expiry accordingly"
                  % brief["security"]["retention_days"])
    else:
        audit.ok("privacy.metadata")


def check_delivery(audit, build_dir, config):
    if not build_dir:
        for code in ("delivery.build-missing", "delivery.pptx-invalid", "delivery.pdf-unreadable",
                     "delivery.html-external-resource", "delivery.file-too-large"):
            audit.ok(code)
        slides_without_notes = [s.get("id") for s in audit.spec.get("slides", [])
                                if not (s.get("notes") or "").strip() and not s.get("hidden")]
        if slides_without_notes:
            audit.add("info", "delivery.notes-missing",
                      "%d slide(s) have no speaker notes" % len(slides_without_notes),
                      where=", ".join(slides_without_notes[:5]))
        else:
            audit.ok("delivery.notes-missing")
        return

    build_dir = pm_core.resolve_path(build_dir)
    expected = {"pptx": "deck.pptx", "pdf": "handout.pdf", "html": "deck.html"}
    declared = set((audit.spec.get("provenance") or {}).get("rendered_formats") or expected.keys())
    missing = []
    for fmt, filename in expected.items():
        path = os.path.join(build_dir, filename)
        if fmt in declared and not os.path.isfile(path):
            missing.append(filename)
    if missing:
        audit.add("error", "delivery.build-missing",
                  "Declared outputs are missing: %s" % ", ".join(missing),
                  hint="Re-run build; check the output directory is the one you are auditing.")
    else:
        audit.ok("delivery.build-missing")

    pptx_path = os.path.join(build_dir, "deck.pptx")
    if os.path.isfile(pptx_path):
        problems = []
        try:
            with zipfile.ZipFile(pptx_path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names:
                    problems.append("[Content_Types].xml missing")
                slide_parts = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
                if len(slide_parts) != len(audit.spec.get("slides", [])):
                    problems.append("%d slide parts for %d spec slides"
                                    % (len(slide_parts), len(audit.spec.get("slides", []))))
                for name in sorted(slide_parts)[:20]:
                    raw = archive.read(name).decode("utf-8", "replace")
                    if "<p:sld" not in raw or "<p:spTree>" not in raw:
                        problems.append("%s is not a slide part" % name)
        except zipfile.BadZipFile:
            problems.append("not a zip archive")
        if problems:
            audit.add("error", "delivery.pptx-invalid", "PPTX check failed: " + "; ".join(problems),
                      hint="Rebuild the deck; the file must open in PowerPoint, Keynote, and Impress.")
        else:
            audit.ok("delivery.pptx-invalid")

        size = os.path.getsize(pptx_path)
        if size > config["limits"].get("max_file_bytes", 26214400):
            audit.add("warning", "delivery.file-too-large",
                      "deck.pptx is %.1f MB" % (size / 1048576.0),
                      hint="Compress media or split the deck.")
        else:
            audit.ok("delivery.file-too-large")

    pdf_path = os.path.join(build_dir, "handout.pdf")
    if os.path.isfile(pdf_path):
        with open(pdf_path, "rb") as fh:
            head = fh.read(8)
            fh.seek(-64, os.SEEK_END)
            tail = fh.read()
        if head.startswith(b"%PDF-") and b"%%EOF" in tail and b"/Type /Page" in tail + head:
            audit.ok("delivery.pdf-unreadable")
        elif head.startswith(b"%PDF-") and b"%%EOF" in tail:
            audit.ok("delivery.pdf-unreadable")
        else:
            audit.add("error", "delivery.pdf-unreadable",
                      "handout.pdf does not look like a complete PDF",
                      hint="Re-run build --format pdf.")
    else:
        audit.ok("delivery.pdf-unreadable")

    html_path = os.path.join(build_dir, "deck.html")
    if os.path.isfile(html_path):
        with open(html_path, "r", encoding="utf-8", errors="replace") as fh:
            html_text = fh.read()
        external = [m.group("url") for m in EXTERNAL_REF_RE.finditer(html_text)
                    if not m.group("url").startswith("data:") and not m.group("url").startswith("#")]
        if external:
            audit.add("warning", "delivery.html-external-resource",
                      "deck.html references %d external resource(s): %s"
                      % (len(external), ", ".join(external[:3])),
                      hint="Inline the asset so the deck opens with no network.")
        else:
            audit.ok("delivery.html-external-resource")
    else:
        audit.ok("delivery.html-external-resource")

    notes_missing = [s.get("id") for s in audit.spec.get("slides", [])
                     if not (s.get("notes") or "").strip() and not s.get("hidden")]
    if notes_missing:
        audit.add("info", "delivery.notes-missing",
                  "%d slide(s) have no speaker notes" % len(notes_missing),
                  where=", ".join(notes_missing[:5]))
    else:
        audit.ok("delivery.notes-missing")


# --------------------------------------------------------------------------
# Scoring and gates
# --------------------------------------------------------------------------
def score_gates(audit, gates_asset):
    gates = []
    findings_by_code = {}
    for finding in audit.findings:
        findings_by_code.setdefault(finding["code"], []).append(finding)

    for gate in gates_asset.get("gates", []):
        checks = gate.get("checks", [])
        applicable = [c for c in checks if c in audit.evaluated]
        errors, warnings = [], []
        for code in applicable:
            for finding in findings_by_code.get(code, []):
                if finding["level"] == "error":
                    errors.append(finding)
                elif finding["level"] == "warning":
                    warnings.append(finding)
        if not applicable:
            score = 100.0
            status = "not-applicable"
        else:
            penalty = len(errors) + 0.5 * len(warnings)
            score = max(0.0, round(100.0 * (1.0 - penalty / len(applicable)), 1))
            status = "fail" if errors else ("warn" if warnings else "pass")
        gates.append({
            "id": gate["id"],
            "name": gate["name"],
            "weight": gate.get("weight", 1),
            "blocking": bool(gate.get("blocking")),
            "checks_evaluated": len(applicable),
            "checks_total": len(checks),
            "errors": len(errors),
            "warnings": len(warnings),
            "score": score,
            "status": status,
            "remediation": gate.get("remediation", ""),
        })

    total_weight = sum(g["weight"] for g in gates if g["status"] != "not-applicable") or 1
    overall = round(sum(g["score"] * g["weight"] for g in gates if g["status"] != "not-applicable") / total_weight, 1)
    return gates, overall


def audit(spec, config, build_dir=None, brief=None, today=None):
    gates_asset = pm_core.config_asset(config, "quality_gates")
    collector = Audit(spec, config, gates_asset)
    check_narrative(collector)
    check_data(collector, today=today)
    check_accessibility(collector)
    check_layout(collector)
    check_brand(collector, brief=brief)
    check_privacy(collector, brief=brief)
    check_delivery(collector, build_dir, config)
    gates, score = score_gates(collector, gates_asset)

    counts = pm_core.count_diagnostics(collector.findings)
    blocking_failures = [g for g in gates if g["blocking"] and g["status"] == "fail"]
    if counts["error"]:
        status = "blocked"
    elif counts["warning"]:
        status = "review"
    else:
        status = "pass"

    return {
        "tool": pm_core.TOOL_NAME,
        "command": "audit",
        "status": status,
        "score": score,
        "findings": collector.findings,
        "counts": counts,
        "gates": gates,
        "blocking_gate_failures": [g["id"] for g in blocking_failures],
        "evaluated_checks": sorted(collector.evaluated),
        "deck": {
            "title": spec.get("deck", {}).get("title", ""),
            "slides": len(spec.get("slides", [])),
            "sections": len(spec.get("sections", [])),
            "planned_minutes": spec.get("stats", {}).get("planned_minutes"),
        },
        "generated_at": pm_core.now_iso(),
    }


def render_report(report):
    """Markdown rendering of an audit report."""
    lines = [
        "# Audit report - %s" % report["deck"]["title"],
        "",
        "- Status: **%s**" % report["status"],
        "- Score: **%s / 100**" % report["score"],
        "- Errors: %d, warnings: %d, notes: %d" % (report["counts"]["error"], report["counts"]["warning"],
                                                   report["counts"]["info"]),
        "- Slides: %d, sections: %d, planned minutes: %s"
        % (report["deck"]["slides"], report["deck"]["sections"], report["deck"]["planned_minutes"]),
        "",
        "## Gates",
        "",
        "| Gate | Status | Score | Errors | Warnings | Blocking |",
        "|---|---|---|---|---|---|",
    ]
    for gate in report["gates"]:
        lines.append("| %s | %s | %s | %d | %d | %s |" % (
            gate["name"], gate["status"], gate["score"], gate["errors"], gate["warnings"],
            "yes" if gate["blocking"] else "no"))
    lines += ["", "## Findings", ""]
    if not report["findings"]:
        lines.append("No findings: every evaluated check passed.")
    else:
        for level in ("error", "warning", "info"):
            subset = [f for f in report["findings"] if f["level"] == level]
            if not subset:
                continue
            lines.append("### %s (%d)" % (level.capitalize(), len(subset)))
            lines.append("")
            for finding in subset:
                where = " _(%s)_" % finding["where"] if finding.get("where") else ""
                lines.append("- `%s` %s%s" % (finding["code"], finding["message"], where))
                if finding.get("hint"):
                    lines.append("  - Fix: %s" % finding["hint"])
            lines.append("")
    if report["blocking_gate_failures"]:
        lines += ["## Blocking gates", "",
                  "Delivery is blocked by: %s." % ", ".join(report["blocking_gate_failures"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def compare_specs(spec_a, spec_b):
    """Structural diff between two deck specs (regression review)."""
    def index(spec):
        return {s.get("id"): s for s in spec.get("slides", [])}

    a, b = index(spec_a), index(spec_b)
    added = [sid for sid in b if sid not in a]
    removed = [sid for sid in a if sid not in b]
    changed = []
    for sid in a:
        if sid not in b:
            continue
        left, right = a[sid], b[sid]
        fields = []
        for key in ("title", "role", "bullets", "steps", "left", "right", "metrics", "notes",
                    "sources", "table", "quote", "pacing_seconds", "hidden"):
            if left.get(key) != right.get(key):
                fields.append(key)
        if fields:
            changed.append({"slide_id": sid, "fields": fields,
                            "title": right.get("title") or left.get("title")})
    order_a = [s.get("id") for s in spec_a.get("slides", [])]
    order_b = [s.get("id") for s in spec_b.get("slides", [])]
    return {
        "added": [{"slide_id": sid, "title": b[sid].get("title")} for sid in added],
        "removed": [{"slide_id": sid, "title": a[sid].get("title")} for sid in removed],
        "changed": changed,
        "reordered": order_a != order_b and not added and not removed,
        "identical": not added and not removed and not changed and order_a == order_b,
    }


if __name__ == "__main__":  # pragma: no cover - module smoke test
    cfg = pm_core.load_config()
    brief, _ = pm_plan.load_brief("asset-example-brief.json", cfg)
    content = pm_plan.parse_content(open("asset-example-content.md").read())
    spec, _d = pm_plan.build_deck_spec(brief, cfg, content=content)
    report = audit(spec, cfg, brief=brief)
    print(report["status"], report["score"], report["counts"])
