"""Content family: text and accessibility audits, policy and safety rule checks, documentation
generators, and genre and place planning driven by asset-place-templates.json.

Loaded as rbx_content by tools-roblox.py. These handlers produce reviewable artifacts and
findings from the supplied inputs. They do not publish, moderate, or contact any service.
"""
import math
import random
import re
import rbx_core as core
from rbx_core import chk

FAMILY = "content"
_PLACES = None


def out(result, findings, artifacts=None, warnings=None):
    return {"result": result, "findings": findings, "artifacts": artifacts or {}, "warnings": warnings or []}


def places():
    global _PLACES
    if _PLACES is None:
        _PLACES = core.res_json("asset-place-templates.json")
    return _PLACES


def steps_ok(n, ctx):
    limit = ctx.config["limits"]["simulation_steps"]
    if n > limit:
        core.fail("INPUT_LIMIT", "Computation would exceed the configured step limit.", steps=n, limit=limit)
    return n


PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|%[sd]|\bTODO\b|lorem ipsum", re.I)
ALL_CAPS_SHOUT = re.compile(r"\b[A-Z]{4,}\b")


# ================================================================ text and accessibility

def h_localization(d, cap, ctx):
    source = core.obj(d, "source", required=True)
    locales = core.array(d, "locales", required=True, kind=str, maximum=100)
    missing, extra_placeholders = {}, {}
    src_keys = set(source)
    loc_tables = d.get("tables", {})
    if not isinstance(loc_tables, dict):
        core.fail("INPUT_INVALID", "tables must map locale names to key-value objects.")
    for loc in locales:
        table = loc_tables.get(loc, {})
        if not isinstance(table, dict):
            core.fail("INPUT_INVALID", "Each locale table must be an object.", locale=loc)
        gaps = sorted(src_keys - set(table))
        if gaps:
            missing[loc] = gaps
        for k, v in table.items():
            sv = source.get(k)
            if sv is not None and sorted(PLACEHOLDER.findall(str(sv))) != sorted(PLACEHOLDER.findall(str(v))):
                extra_placeholders.setdefault(loc, []).append(k)
    findings = [chk("localization.coverage", not missing, "Every source key has a translation for every locale." if not missing else "Missing translations: " + ", ".join(f"{k} ({len(v)})" for k, v in missing.items()), "fail"),
                chk("localization.placeholders", not extra_placeholders, "Placeholders match in every translation." if not extra_placeholders else "Placeholder mismatch: " + ", ".join(f"{k}: {', '.join(v[:5])}" for k, v in extra_placeholders.items()), "fail"),
                chk("localization.hardcoded", None, "Strings inside code must go through the localization table; scan the source with the code audit.")]
    return out({"keys": len(src_keys), "locales": locales, "missing": missing, "placeholder_mismatch": extra_placeholders}, findings, {"localization-gaps.json": missing})


def h_translation(d, cap, ctx):
    src = core.text(d, "source", maximum=2000)
    tr = core.text(d, "translation", maximum=2000)
    context = core.text(d, "context", maximum=300, required=False) or ""
    max_len = core.integer(d, "max_length", minimum=1, maximum=2000)
    plural = core.text(d, "plural_rule", maximum=40, required=False) or ""
    ph_ok = sorted(PLACEHOLDER.findall(src)) == sorted(PLACEHOLDER.findall(tr))
    findings = [chk("translation.length", len(tr) <= max_len, f"{len(tr)} characters within {max_len}." if len(tr) <= max_len else f"{len(tr)} characters exceed {max_len}; the UI may clip.", "warning"),
                chk("translation.placeholders", ph_ok, "Placeholders match." if ph_ok else "Placeholders differ between source and translation.", "fail"),
                chk("translation.context", bool(context), "Context is supplied." if context else "Translators need context to choose the right sense.", "warning"),
                chk("translation.plural", bool(plural) or not re.search(r"\b(\d+)\b", src), "Plural rule present where numbers appear." if plural or not re.search(r"\b(\d+)\b", src) else "Numbers appear; define the plural rule for this locale.", "warning")]
    return out({"length": len(tr), "source_length": len(src)}, findings)


def h_captions(d, cap, ctx):
    caps = core.array(d, "captions", required=True, kind=dict, maximum=5000)
    cps_max = core.number(d, "max_characters_per_second", minimum=1, maximum=100)
    rows, fast = [], []
    for c in caps:
        t0, t1 = core.number(c, "start", minimum=0, maximum=86400), core.number(c, "end", minimum=0, maximum=86400)
        txt = core.text(c, "text", maximum=500)
        if t1 <= t0:
            core.fail("INPUT_INVALID", "caption end must be after start.", text=txt[:40])
        rate = len(txt) / (t1 - t0)
        rows.append({"start": t0, "end": t1, "cps": round(rate, 2), "chars": len(txt)})
        if rate > cps_max:
            fast.append(txt[:40])
    overlaps = sum(1 for a, b in zip(rows, rows[1:]) if b["start"] < a["end"])
    findings = [chk("captions.reading_speed", not fast, f"All captions read at or below {cps_max} characters/second." if not fast else f"{len(fast)} caption(s) exceed {cps_max} characters/second.", "warning"),
                chk("captions.timing", overlaps == 0, "Captions do not overlap." if overlaps == 0 else f"{overlaps} overlapping caption pair(s).", "warning"),
                chk("captions.speaker", None, "Identify speakers in captions for multi-voice scenes.")]
    return out({"captions": len(rows), "too_fast": fast[:20], "overlaps": overlaps, "max_cps": round(max((r["cps"] for r in rows), default=0), 2)}, findings)


def h_screen_reader(d, cap, ctx):
    controls = core.array(d, "controls", required=True, kind=dict, maximum=500)
    live = core.text(d, "live_status_label", maximum=120, required=False) or ""
    unlabeled = [c.get("id", "?") for c in controls if not str(c.get("label", "")).strip()]
    icon_only = [c.get("id", "?") for c in controls if c.get("icon_only")]
    order_ok = all(isinstance(c.get("order"), int) for c in controls)
    findings = [chk("screen_reader.labels", not unlabeled, "Every control has an accessible label." if not unlabeled else "Unlabeled controls: " + ", ".join(map(str, unlabeled[:10])), "fail"),
                chk("screen_reader.icons", not icon_only, "Icon-only controls have text alternatives." if not icon_only else "Icon-only controls need text: " + ", ".join(map(str, icon_only[:10])), "warning"),
                chk("screen_reader.order", order_ok, "Reading order is explicit." if order_ok else "Give each control an integer reading order.", "warning"),
                chk("screen_reader.live", bool(live), "Live status label is defined." if live else "Define a live status label for dynamic updates.", "info")]
    return out({"controls": len(controls), "unlabeled": unlabeled, "icon_only": icon_only}, findings)


def h_cognitive(d, cap, ctx):
    text = core.text(d, "text", maximum=5000)
    steps = core.integer(d, "steps", minimum=1, maximum=500)
    reminders = core.boolean(d, "reminders", False)
    undo = core.boolean(d, "undo", False)
    pressure = core.boolean(d, "time_pressure", False)
    words = len(text.split())
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    avg = words / sentences
    findings = [chk("cognitive.sentence_length", avg <= 20, f"Average sentence length {round(avg, 1)} words.", "warning" if avg > 20 else "info"),
                chk("cognitive.steps", steps <= 7, f"{steps} step(s) in the task." if steps <= 7 else f"{steps} steps exceed the recommended 7; split the task.", "warning"),
                chk("cognitive.reminders", reminders, "Reminders are available." if reminders else "Offer reminders for multi-step tasks.", "info"),
                chk("cognitive.undo", undo, "Actions are undoable." if undo else "Offer undo or confirmation for destructive actions.", "warning"),
                chk("cognitive.time_pressure", not pressure, "No timed pressure." if not pressure else "Timed challenges need an extend or pause option.", "warning")]
    return out({"words": words, "sentences": sentences, "average_sentence_words": round(avg, 2)}, findings)


def h_contrast(d, cap, ctx):
    pairs = core.array(d, "pairs", required=True, kind=dict, maximum=500)
    color_only = core.boolean(d, "color_only_meaning", False)
    outline = core.boolean(d, "text_outline", False)
    rows, fails = [], []
    for p in pairs:
        fg, bg = core.text(p, "foreground", maximum=10), core.text(p, "background", maximum=10)
        large = bool(p.get("large", False))
        ratio = core.contrast_ratio(fg, bg)
        need = 3.0 if large else 4.5
        rows.append({"foreground": fg, "background": bg, "ratio": round(ratio, 2), "required": need, "passes": ratio >= need})
        if ratio < need:
            fails.append(f"{fg} on {bg}")
    findings = [chk("contrast.text", not fails, "All text pairs meet WCAG contrast." if not fails else f"{len(fails)} pair(s) below contrast: " + "; ".join(fails[:5]), "fail"),
                chk("contrast.color_only", not color_only, "Meaning is not conveyed by color alone." if not color_only else "Color alone carries meaning; add text or shape.", "fail"),
                chk("contrast.outline", outline or not fails, "Outlines help legibility over busy scenes." if outline else "Consider a text outline over 3D scenes.", "info")]
    return out({"pairs": rows, "failing": fails}, findings)


def h_flashing(d, cap, ctx):
    samples = core.array(d, "samples", required=True, maximum=10000)
    hz = core.number(d, "sample_hz", minimum=1, maximum=240)
    area = core.number(d, "affected_area_fraction", minimum=0, maximum=1)
    reduced = core.boolean(d, "reduced_effects", False)
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in samples):
        core.fail("INPUT_INVALID", "samples must contain only numbers (luminance values 0-255).")
    lum = [float(x) for x in samples]
    steps_ok(len(lum), ctx)
    flashes, prev_dir = 0, 0
    for i in range(1, len(lum)):
        delta = lum[i] - lum[i - 1]
        if abs(delta) >= 0.1:
            direction = 1 if delta > 0 else -1
            if prev_dir and direction != prev_dir:
                flashes += 1
            prev_dir = direction
    seconds = len(lum) / hz
    per_second = flashes / seconds if seconds else 0
    risky = per_second > 3 and area >= 0.25
    findings = [chk("flashing.rate", per_second <= 3 or area < 0.25, f"{round(per_second, 2)} flash(es) per second over {round(area * 100)}% of the view." if not risky else f"{round(per_second, 2)} flashes per second over {round(area * 100)}% of the view exceeds the safe threshold.", "fail" if risky else "info"),
                chk("flashing.reduced_effects", reduced or not risky, "Reduced-effects mode is available." if reduced else "Provide a reduced-effects option for flashing content.", "warning"),
                chk("flashing.review", None, "Sample data is a model input; confirm with a calibrated analyzer before release.")]
    return out({"flash_transitions": flashes, "per_second": round(per_second, 3), "seconds": round(seconds, 3), "risky": risky}, findings)


def h_reduced_motion(d, cap, ctx):
    settings = core.obj(d, "settings", required=False)
    reduced = core.boolean(d, "reduced", False)
    essential = set(core.array(d, "preserve_essential_motion", maximum=100, kind=str))
    effects = core.array(d, "effects", maximum=500, kind=dict)
    kept, dropped = [], []
    for e in effects:
        name = core.text(e, "name", maximum=80)
        if reduced and not e.get("essential") and name not in essential:
            dropped.append(name)
        else:
            kept.append(name)
    findings = [chk("reduced_motion.setting", True, "Reduced-motion setting is read from the player's preference." if settings is not None else "No settings object supplied; default behaviour assumed."),
                chk("reduced_motion.essential", not reduced or bool(essential) or not dropped, "Essential motion is preserved." if reduced and essential else "List the essential motion that must remain." if reduced else "Reduced motion is off."),
                chk("reduced_motion.feedback", None, "Replace removed motion with a non-motion cue (color, sound, or text).")]
    return out({"reduced": reduced, "kept": kept, "removed": dropped}, findings)


def h_accessible_controls(d, cap, ctx):
    remap = core.boolean(d, "remappable", False)
    toggle = core.boolean(d, "hold_to_toggle", False)
    sens = core.number(d, "sensitivity", minimum=0, maximum=10)
    shake = core.number(d, "camera_shake", minimum=0, maximum=1)
    one = core.boolean(d, "one_hand_mode", False)
    findings = [chk("accessible_controls.remap", remap, "Controls are remappable." if remap else "Allow remapping of every action.", "warning"),
                chk("accessible_controls.hold", toggle, "Hold actions can be toggled." if toggle else "Offer toggle for hold-to-act inputs.", "info"),
                chk("accessible_controls.sensitivity", 0.2 <= sens <= 3, f"Sensitivity {sens} is in the usable range." if 0.2 <= sens <= 3 else f"Sensitivity {sens} is outside 0.2-3.", "warning"),
                chk("accessible_controls.shake", shake <= 0.5, "Camera shake is moderate." if shake <= 0.5 else "Camera shake above 0.5 can cause discomfort; cap it.", "warning"),
                chk("accessible_controls.one_hand", one, "One-hand mode is available." if one else "Consider a one-hand layout for mobile and accessibility.", "info")]
    return out({"sensitivity": sens, "camera_shake": shake}, findings)


def h_copy(d, cap, ctx):
    title = core.text(d, "title", maximum=200)
    audience = core.text(d, "audience", maximum=200)
    action = core.text(d, "core_action", maximum=200)
    minutes = core.number(d, "session_minutes", minimum=1, maximum=600)
    update = core.text(d, "update", maximum=2000, required=False) or ""
    claims = core.array(d, "misleading_claims", maximum=100, kind=str)
    shouting = ALL_CAPS_SHOUT.findall(title + " " + update)
    findings = [chk("copy.claims", not claims, "No misleading claims are listed." if not claims else f"{len(claims)} claim(s) must be removed or proven: " + "; ".join(claims[:5]), "fail"),
                chk("copy.shouting", not shouting, "No all-caps shouting." if not shouting else "All-caps words: " + ", ".join(shouting[:5]), "info"),
                chk("copy.clarity", len(action.split()) <= 12, "Core action is stated in one short sentence." if len(action.split()) <= 12 else "Shorten the core action statement to 12 words or fewer.", "info"),
                chk("copy.session", minutes <= 120, f"Session length {minutes} minutes is stated accurately in copy." if minutes <= 120 else "Long sessions should be stated plainly.", "info")]
    return out({"title_length": len(title), "audience": audience, "session_minutes": minutes}, findings)


def h_error_messages(d, cap, ctx):
    errors = core.array(d, "errors", required=True, kind=dict, maximum=500)
    rows, bad = [], []
    for e in errors:
        code = core.text(e, "code", maximum=60)
        msg = core.text(e, "message", maximum=400)
        action = core.text(e, "action", maximum=200, required=False) or ""
        blame = bool(re.search(r"\byou (did|failed|broke|can't|cannot)\b", msg, re.I))
        has_action = bool(action) or bool(re.search(r"\b(try|check|retry|reconnect|contact)\b", msg, re.I))
        rows.append({"code": code, "has_next_step": has_action, "blames_player": blame})
        if not has_action or blame:
            bad.append(code)
    findings = [chk("error_messages.next_step", all(r["has_next_step"] for r in rows), "Every error tells the player what to do next." if all(r["has_next_step"] for r in rows) else "Errors without a next step: " + ", ".join(r["code"] for r in rows if not r["has_next_step"]), "warning"),
                chk("error_messages.tone", not any(r["blames_player"] for r in rows), "No error blames the player." if not any(r["blames_player"] for r in rows) else "Errors that blame the player: " + ", ".join(r["code"] for r in rows if r["blames_player"]), "info"),
                chk("error_messages.codes", len({r["code"] for r in rows}) == len(rows), "Error codes are unique." if len({r["code"] for r in rows}) == len(rows) else "Duplicate error codes.", "fail")]
    return out({"errors": rows, "needs_work": bad}, findings)


def h_reproduction(d, cap, ctx):
    title = core.text(d, "title", maximum=200)
    steps = core.array(d, "steps", required=True, kind=str, maximum=200)
    expected = core.text(d, "expected", maximum=500)
    actual = core.text(d, "actual", maximum=500)
    env = core.text(d, "environment", maximum=300)
    freq = core.text(d, "frequency", maximum=40)
    findings = [chk("reproduction.steps", len(steps) >= 2, f"{len(steps)} reproduction step(s)." if len(steps) >= 2 else "Provide the full sequence of steps.", "warning"),
                chk("reproduction.expected_actual", expected.lower() != actual.lower(), "Expected and actual results differ." if expected.lower() != actual.lower() else "Expected and actual are identical; the report does not describe a defect.", "warning"),
                chk("reproduction.environment", bool(env), "Environment is recorded." if env else "Record platform, device, and build.", "warning"),
                chk("reproduction.frequency", freq in ("always", "often", "sometimes", "once"), f"Frequency: {freq}." if freq in ("always", "often", "sometimes", "once") else "Frequency must be always, often, sometimes, or once.", "info")]
    md = "\n".join([f"# {title}", "", "## Steps", *[f"{i + 1}. {s}" for i, s in enumerate(steps)], "", f"**Expected:** {expected}", f"**Actual:** {actual}", f"**Environment:** {env}", f"**Frequency:** {freq}", ""])
    return out({"steps": len(steps), "frequency": freq}, findings, {"reproduction-report.md": md})


def h_safety_ux(d, cap, ctx):
    situation = core.text(d, "situation", maximum=300)
    preserved = core.array(d, "preserved", maximum=50, kind=str)
    action = core.text(d, "action", maximum=200)
    accusatory = core.boolean(d, "accusatory_language", False)
    findings = [chk("safety_ux.preserved", bool(preserved), "Relevant evidence is preserved for the report." if preserved else "Preserve the chat or action evidence before the player leaves the view.", "warning"),
                chk("safety_ux.neutral", not accusatory, "Copy is neutral and non-accusatory." if not accusatory else "Accusatory language discourages reporting; rewrite neutrally.", "warning"),
                chk("safety_ux.actions", bool(action), f"Offer the action: {action}." if action else "Offer block, mute, and report actions.", "warning")]
    return out({"situation": situation, "preserved": preserved}, findings)


def h_localization_extract(d, cap, ctx):
    source = core.text(d, "source", maximum=200000)
    locale = core.text(d, "locale", maximum=20)
    strings = re.findall(r'"([^"\\]{2,200})"', source)
    seen, uniq = set(), []
    for s in strings:
        if s not in seen and re.search(r"[A-Za-z]{2,}", s):
            seen.add(s)
            uniq.append(s)
    table = {f"key_{i + 1:04d}": s for i, s in enumerate(uniq)}
    findings = [chk("localization_extract.found", bool(uniq), f"Extracted {len(uniq)} candidate string(s)." if uniq else "No quoted UI strings found.", "info"),
                chk("localization_extract.review", None, "Review extracted strings; technical identifiers can be false positives.")]
    return out({"locale": locale, "strings": len(uniq)}, findings, {"strings-" + locale + ".json": table})


def h_dialogue_playback(d, cap, ctx):
    lines = core.array(d, "lines", required=True, kind=dict, maximum=5000)
    captions = core.boolean(d, "captions", False)
    locale = core.text(d, "locale", maximum=20)
    rows = []
    for ln in lines:
        speaker = core.text(ln, "speaker", maximum=60)
        text = core.text(ln, "text", maximum=2000)
        dur = core.number(ln, "duration", minimum=0.1, maximum=600)
        rows.append({"speaker": speaker, "chars": len(text), "seconds": dur, "cps": round(len(text) / dur, 2)})
    total = sum(r["seconds"] for r in rows)
    fast = [r for r in rows if r["cps"] > 20]
    findings = [chk("dialogue_playback.captions", captions, "Captions are enabled for dialogue." if captions else "Provide captions for all dialogue.", "fail"),
                chk("dialogue_playback.pacing", not fast, "Lines fit their durations." if not fast else f"{len(fast)} line(s) read faster than 20 characters/second.", "warning"),
                chk("dialogue_playback.locale", bool(locale), f"Locale {locale}." if locale else "Set the locale for audio selection.", "info")]
    return out({"lines": len(rows), "total_seconds": round(total, 3), "speakers": sorted({r["speaker"] for r in rows})}, findings)


def h_storytelling(d, cap, ctx):
    beats = core.array(d, "beats", required=True, kind=dict, maximum=500)
    names = [core.text(b, "name", maximum=80) for b in beats]
    if any(isinstance(b.get("tension", 0), bool) or not isinstance(b.get("tension", 0), (int, float)) for b in beats):
        core.fail("INPUT_INVALID", "Each beat tension must be a number.")
    tension = [float(b.get("tension", 0)) for b in beats]
    dupes = sorted({n for n in names if names.count(n) > 1})
    peak = max(range(len(tension)), key=lambda i: tension[i]) if tension else None
    rising = all(tension[i] <= tension[i + 1] for i in range(peak)) if peak is not None else False
    findings = [chk("storytelling.beats", not dupes, "Beat names are unique." if not dupes else "Duplicate beats: " + ", ".join(dupes), "fail"),
                chk("storytelling.arc", peak is None or peak > 0, f"Peak tension at beat {names[peak]}." if peak is not None else "No tension values supplied.", "info"),
                chk("storytelling.climb", rising or peak in (None, 0), "Tension rises into the peak." if rising else "Tension dips before the peak; check pacing.", "info")]
    return out({"beats": len(beats), "peak": names[peak] if peak is not None else None}, findings)


def h_ugc_text(d, cap, ctx):
    text = core.text(d, "text", maximum=4000)
    uid = core.integer(d, "from_user_id", minimum=1, maximum=10 ** 15)
    context = core.text(d, "context", maximum=60)
    resp = core.obj(d, "filter_response")
    flagged = bool(resp.get("flagged", False))
    filtered = resp.get("filtered_text")
    display = filtered if isinstance(filtered, str) and flagged else text
    findings = [chk("ugc_text.filter", not flagged, "Platform text filter passed." if not flagged else "Text was flagged; show only the filtered version.", "fail" if flagged else "info"),
                chk("ugc_text.display", display == text or flagged, "Displayed text is the source text." if display == text else "Displayed text is the filtered version.", "info"),
                chk("ugc_text.context", bool(context), f"Context {context} recorded." if context else "Record where the text appears.", "info")]
    return out({"display": display, "flagged": flagged, "sender": uid}, findings)


def h_inclusion(d, cap, ctx):
    choices = core.array(d, "avatar_choices", maximum=500, kind=dict)
    locked = core.array(d, "gender_locked_items", maximum=500, kind=str)
    pron = core.boolean(d, "pronoun_optional", False)
    block = core.boolean(d, "block", False)
    mute = core.boolean(d, "mute", False)
    report = core.boolean(d, "report", False)
    alt = core.boolean(d, "nonvoice_alternative", False)
    findings = [chk("inclusion.avatar", not locked, "No gender-locked avatar items." if not locked else f"{len(locked)} item(s) are gender-locked; offer neutral options.", "warning"),
                chk("inclusion.pronouns", pron, "Pronouns are optional and can be blank." if pron else "Make pronouns optional, not required.", "warning"),
                chk("inclusion.safety_tools", block and mute and report, "Block, mute and report are available." if block and mute and report else "Block, mute and report must all be available.", "fail"),
                chk("inclusion.voice", alt, "A non-voice alternative exists." if alt else "Provide a non-voice alternative for voice features.", "warning")]
    return out({"avatar_choices": len(choices), "gender_locked": locked}, findings)


# ================================================================ policy and safety

def h_data_retention(d, cap, ctx):
    fields = core.array(d, "fields", required=True, kind=dict, maximum=500)
    export = core.boolean(d, "export_user", False)
    rows = []
    for f in fields:
        name = core.text(f, "name", maximum=80)
        days = core.integer(f, "retention_days", minimum=0, maximum=36500)
        purpose = core.text(f, "purpose", maximum=200, required=False) or ""
        personal = bool(f.get("personal", False))
        rows.append({"name": name, "days": days, "purpose": purpose, "personal": personal})
    no_purpose = [r["name"] for r in rows if not r["purpose"]]
    long_personal = [r["name"] for r in rows if r["personal"] and r["days"] > 365]
    findings = [chk("data_retention.purpose", not no_purpose, "Every field states a purpose." if not no_purpose else "Fields without a purpose: " + ", ".join(no_purpose), "fail"),
                chk("data_retention.limits", not long_personal, "Personal fields are kept at most a year." if not long_personal else "Personal fields kept over a year: " + ", ".join(long_personal), "warning"),
                chk("data_retention.export", export, "User export is supported." if export else "Support a user data export request.", "warning"),
                chk("data_retention.deletion", None, "Deletion must propagate to backups and analytics; confirm with the operator's policy.")]
    return out({"fields": rows}, findings, {"retention-schedule.json": rows})


def h_ugc_flow(d, cap, ctx):
    text_in = core.boolean(d, "user_text", False)
    images_in = core.boolean(d, "user_images", False)
    mod = core.boolean(d, "moderation_gate", False)
    elig = core.boolean(d, "eligibility_gate", False)
    consent = core.boolean(d, "explicit_publish_consent", False)
    findings = [chk("ugc_flow.moderation", mod or not (text_in or images_in), "User content passes a moderation gate before display." if mod or not (text_in or images_in) else "User content needs a moderation gate before display.", "fail"),
                chk("ugc_flow.eligibility", elig, "Eligibility is checked before publishing." if elig else "Check eligibility before publishing.", "fail"),
                chk("ugc_flow.consent", consent, "Explicit publish consent is recorded." if consent else "Require explicit consent before publishing user content.", "fail")]
    return out({"publishable": all(f["status"] == "pass" for f in findings)}, findings)


def h_threat_model(d, cap, ctx):
    threats = core.array(d, "threats", required=True, kind=dict, maximum=500)
    rows, unmitigated = [], []
    for t in threats:
        name = core.text(t, "name", maximum=120)
        likelihood = core.integer(t, "likelihood", minimum=1, maximum=5)
        impact = core.integer(t, "impact", minimum=1, maximum=5)
        mitigations = core.array(t, "mitigations", maximum=20, kind=str)
        score = likelihood * impact
        rows.append({"threat": name, "score": score, "mitigations": len(mitigations)})
        if score >= 12 and not mitigations:
            unmitigated.append(name)
    rows.sort(key=lambda r: -r["score"])
    findings = [chk("threat_model.mitigations", not unmitigated, "Every high-risk threat has a mitigation." if not unmitigated else "High-risk threats without mitigations: " + ", ".join(unmitigated), "fail"),
                chk("threat_model.ranking", True, f"Highest score: {rows[0]['threat']} ({rows[0]['score']})." if rows else "No threats listed."),
                chk("threat_model.review", None, "A security review must approve the model; this ranking is an aid, not an approval.")]
    return out({"ranked": rows}, findings, {"threat-ranking.json": rows})


def h_admin(d, cap, ctx):
    uid = core.integer(d, "user_id", minimum=1, maximum=10 ** 15)
    roles = core.array(d, "roles", maximum=20, kind=str)
    cmd = core.text(d, "command", maximum=60)
    arg = core.text(d, "argument", maximum=500, required=False) or ""
    allow = set(core.array(d, "allowlist", maximum=200, kind=str))
    revoked = set(core.array(d, "revoked", maximum=200, kind=str))
    arg_max = core.integer(d, "argument_max_length", minimum=1, maximum=5000)
    sensitive = cmd in ("ban", "grant_currency", "delete_data", "set_role", "shutdown")
    allowed = cmd in allow and "admin" in roles and str(uid) not in revoked
    findings = [chk("admin.allowlist", cmd in allow, "Command is on the allowlist." if cmd in allow else f"{cmd} is not on the allowlist.", "fail"),
                chk("admin.role", "admin" in roles, "Caller holds the admin role." if "admin" in roles else "Caller lacks the admin role.", "fail"),
                chk("admin.revocation", str(uid) not in revoked, "Caller is not revoked." if str(uid) not in revoked else "Caller's access was revoked.", "fail"),
                chk("admin.argument", len(arg) <= arg_max, "Argument length is within the limit." if len(arg) <= arg_max else "Argument exceeds the length limit.", "fail"),
                chk("admin.approval", None if sensitive else True, "Sensitive command: require a second approver and an audit record." if sensitive else "Non-sensitive command.")]
    return out({"allowed": allowed and len(arg) <= arg_max, "sensitive": sensitive}, findings)


def h_evidence(d, cap, ctx):
    events = core.array(d, "events", required=True, kind=dict, maximum=5000)
    keep = set(core.array(d, "keep", maximum=100, kind=str))
    days = core.integer(d, "retention_days", minimum=1, maximum=3650)
    kept = [e for e in events if e.get("type") in keep]
    dropped = len(events) - len(kept)
    findings = [chk("evidence.scope", bool(keep), f"{len(kept)} event(s) kept by type." if keep else "No event types are listed to keep.", "warning"),
                chk("evidence.retention", days <= 365, f"Evidence kept {days} days." if days <= 365 else f"Evidence kept {days} days is long; justify it.", "warning"),
                chk("evidence.minimization", True, f"{dropped} event(s) excluded from the evidence set.")]
    return out({"kept": len(kept), "excluded": dropped, "retention_days": days}, findings)


def h_safety_review(d, cap, ctx):
    features = core.array(d, "features", required=True, kind=dict, maximum=200)
    human = core.boolean(d, "human_review", False)
    risky = [f.get("name") for f in features if f.get("minors_exposure") or f.get("open_chat") or f.get("user_images")]
    findings = [chk("safety_review.human", human or not risky, "A human reviewed the risky features." if human or not risky else "Features with minor or open-chat exposure need human review.", "fail"),
                chk("safety_review.features", not risky, "No high-exposure feature detected." if not risky else "High-exposure features: " + ", ".join(map(str, risky)), "warning"),
                chk("safety_review.policy", None, "Map each feature to the current platform safety policy; this tool does not certify compliance.")]
    return out({"features": len(features), "high_exposure": risky}, findings)


def h_block_mute(d, cap, ctx):
    blocked = set(core.array(d, "blocked", maximum=5000, kind=str))
    muted = set(core.array(d, "muted", maximum=5000, kind=str))
    senders = core.array(d, "senders", maximum=5000, kind=str)
    invitations = core.array(d, "invitations", maximum=5000, kind=str)
    persistent = core.boolean(d, "persistent", False)
    delivered = [s for s in senders if s not in blocked and s not in muted]
    blocked_invites = [i for i in invitations if i in blocked]
    findings = [chk("block_mute.delivery", True, f"{len(senders) - len(delivered)} message(s) filtered by block or mute."),
                chk("block_mute.invitations", not blocked_invites, "Blocked users cannot send invitations." if not blocked_invites else f"{len(blocked_invites)} invitation(s) from blocked users were not filtered.", "fail"),
                chk("block_mute.persistence", persistent, "Blocks persist across sessions." if persistent else "Blocks should persist across sessions.", "warning")]
    return out({"delivered": len(delivered), "filtered": len(senders) - len(delivered)}, findings)


def h_age_features(d, cap, ctx):
    features = core.array(d, "features", required=True, kind=dict, maximum=200)
    birth = core.boolean(d, "collect_birthdate", False)
    gated = [f.get("name") for f in features if f.get("requires_age") and not f.get("age_gated")]
    findings = [chk("age_features.gating", not gated, "Age-restricted features are gated." if not gated else "Ungated age-restricted features: " + ", ".join(map(str, gated)), "fail"),
                chk("age_features.collection", not birth, "Birthdates are not collected." if not birth else "Collecting birthdates needs a documented legal basis; review.", "warning"),
                chk("age_features.legal", None, "Age requirements vary by jurisdiction; confirm with counsel.")]
    return out({"gated_features": len([f for f in features if f.get("age_gated")]), "ungated": gated}, findings)


def h_private_safety(d, cap, ctx):
    private = core.boolean(d, "private", False)
    report = core.boolean(d, "report", False)
    block = core.boolean(d, "block", False)
    mod = core.boolean(d, "moderation", False)
    silence = core.boolean(d, "owner_can_silence_reports", False)
    invites = core.text(d, "invite_permissions", maximum=40)
    findings = [chk("private_safety.report", report, "Reporting is available in private servers." if report else "Private servers need reporting.", "fail"),
                chk("private_safety.block", block, "Blocking works in private servers." if block else "Blocking must work in private servers.", "fail"),
                chk("private_safety.moderation", mod or not private, "Moderation covers private servers." if mod or not private else "Private servers need moderation coverage.", "fail"),
                chk("private_safety.owner_silence", not silence, "Owners cannot silence reports against themselves." if not silence else "Owners can silence reports; reports must still reach moderators.", "fail"),
                chk("private_safety.invites", invites in ("friends", "owner"), f"Invites limited to {invites}." if invites in ("friends", "owner") else "Limit invites to friends or the owner.", "warning")]
    return out({"private": private}, findings)


def h_rights(d, cap, ctx):
    assets = core.array(d, "assets", required=True, kind=dict, maximum=5000)
    rows, missing = [], []
    for a in assets:
        name = core.text(a, "name", maximum=120)
        lic = core.text(a, "license", maximum=60, required=False) or ""
        owner = core.text(a, "owner", maximum=120, required=False) or ""
        rows.append({"name": name, "license": lic, "owner": owner})
        if not lic or not owner:
            missing.append(name)
    findings = [chk("rights.records", not missing, "Every asset has a license and owner." if not missing else f"{len(missing)} asset(s) lack a license or owner.", "fail"),
                chk("rights.review", None, "Licenses must be verified against the source; this checks the record only.")]
    return out({"assets": len(rows), "missing": missing}, findings, {"rights-register.json": rows})


def h_music_rights(d, cap, ctx):
    tracks = core.array(d, "tracks", required=True, kind=dict, maximum=5000)
    rows = []
    for t in tracks:
        name = core.text(t, "name", maximum=120)
        lic = core.text(t, "license", maximum=60, required=False) or ""
        term_end = core.text(t, "license_expires", maximum=20, required=False) or ""
        rows.append({"name": name, "license": lic, "expires": term_end, "has_license": bool(lic)})
    unlicensed = [r["name"] for r in rows if not r["has_license"]]
    findings = [chk("music_rights.license", not unlicensed, "Every track has a license record." if not unlicensed else "Unlicensed tracks: " + ", ".join(unlicensed[:20]), "fail"),
                chk("music_rights.term", True, "License terms recorded where provided."),
                chk("music_rights.review", None, "License terms and territories must be checked by the rights holder or counsel.")]
    return out({"tracks": len(rows), "unlicensed": unlicensed}, findings, {"music-register.json": rows})


def h_voice(d, cap, ctx):
    elig = core.boolean(d, "eligibility", False)
    spatial = core.boolean(d, "spatial", False)
    mute = core.boolean(d, "mute", False)
    report = core.boolean(d, "report", False)
    alt = core.boolean(d, "text_alternative", False)
    ptt = core.boolean(d, "push-to-talk", False) if "push-to-talk" in d else core.boolean(d, "push_to_talk", False)
    findings = [chk("voice.eligibility", elig, "Voice chat is limited to eligible users." if elig else "Gate voice chat behind eligibility.", "fail"),
                chk("voice.mute_report", mute and report, "Players can mute and report voice." if mute and report else "Mute and report must be available for voice.", "fail"),
                chk("voice.text_alternative", alt, "A text alternative exists." if alt else "Offer a text alternative to voice.", "info"),
                chk("voice.spatial", True, "Spatial voice setting recorded." if spatial else "Spatial voice is off; positional voice is not configured."),
                chk("voice.push_to_talk", ptt, "Push-to-talk is available." if ptt else "Offer push-to-talk.", "info")]
    return out({"eligible": elig, "push_to_talk": ptt}, findings)


def h_chat(d, cap, ctx):
    channels = core.array(d, "channels", required=True, kind=dict, maximum=200)
    custom = core.boolean(d, "custom_filter_replaces_platform", False)
    authz = core.boolean(d, "private_message_authorization", False)
    unsafe = [c.get("name") for c in channels if c.get("cross_server") and not c.get("filtered")]
    findings = [chk("chat.platform_filter", not custom, "The platform text filter is used." if not custom else "A custom filter replaces the platform filter; keep the platform filter in place.", "fail"),
                chk("chat.private_authorization", authz, "Private messages check authorization on the server." if authz else "Check private-message authorization on the server.", "fail"),
                chk("chat.cross_server", not unsafe, "Cross-server channels are filtered." if not unsafe else "Unfiltered cross-server channels: " + ", ".join(map(str, unsafe)), "fail")]
    return out({"channels": len(channels)}, findings)


def h_guild(d, cap, ctx):
    members = core.array(d, "members", required=True, kind=dict, maximum=5000)
    actor = core.text(d, "actor", maximum=80)
    action = core.text(d, "action", maximum=40)
    perms = core.obj(d, "permissions", required=True)
    roles = {m.get("id"): m.get("role") for m in members}
    need = perms.get(action)
    allowed = need is not None and roles.get(actor) in need
    findings = [chk("guild.permission", allowed, f"{actor} may {action}." if allowed else f"{actor} may not {action}.", "fail" if not allowed else "info"),
                chk("guild.last_leader", action != "leave" or sum(1 for m in members if m.get("role") == "leader") > 1 or roles.get(actor) != "leader", "The last leader cannot leave without a successor." if action == "leave" else "Not a leave action."),
                chk("guild.server_check", None, "Guild permission checks must run on the server.")]
    return out({"allowed": allowed, "role": roles.get(actor)}, findings)


def h_referrals(d, cap, ctx):
    inviter = core.text(d, "inviter", maximum=80)
    recipient = core.text(d, "recipient", maximum=80)
    new = core.boolean(d, "recipient_new", False)
    consent = core.boolean(d, "consent", False)
    played = core.number(d, "qualified_play_seconds", minimum=0, maximum=10 ** 7)
    required = core.number(d, "required_seconds", minimum=0, maximum=10 ** 7)
    rewarded = core.boolean(d, "already_rewarded", False)
    self_ref = inviter == recipient
    grant = new and consent and played >= required and not rewarded and not self_ref
    findings = [chk("referrals.self", not self_ref, "Self-referrals are rejected." if not self_ref else "Self-referral detected; reject.", "fail"),
                chk("referrals.new_user", new, "Recipient is a new user." if new else "Recipient is not new; no reward.", "fail"),
                chk("referrals.qualified_play", played >= required, "Recipient reached the qualifying play time." if played >= required else "Recipient has not played long enough.", "warning"),
                chk("referrals.idempotent", not rewarded, "Reward is granted once." if not rewarded else "Reward already granted; do not grant again.", "fail"),
                chk("referrals.consent", consent, "Recipient consented to the referral." if consent else "Recipient consent is required.", "fail")]
    return out({"grant": grant}, findings)


def h_playtest(d, cap, ctx):
    groups = core.array(d, "groups", required=True, kind=dict, maximum=200)
    minutes = core.number(d, "session_minutes", minimum=1, maximum=600)
    incentive = core.text(d, "incentive", maximum=200, required=False) or ""
    guardian = core.boolean(d, "guardian_consent_when_required", False)
    tasks = core.array(d, "tasks", maximum=200, kind=str)
    minors = [g.get("name") for g in groups if g.get("minors")]
    findings = [chk("playtest.consent", guardian or not minors, "Guardian consent is recorded for minors." if guardian or not minors else "Minors need guardian consent before playtesting.", "fail"),
                chk("playtest.incentive", not incentive or len(incentive) < 200, "Incentive is stated plainly." if incentive else "State any incentive clearly and without pressure.", "info"),
                chk("playtest.tasks", bool(tasks), f"{len(tasks)} task(s) defined." if tasks else "Define playtest tasks.", "warning"),
                chk("playtest.length", minutes <= 60, "Session length is reasonable." if minutes <= 60 else "Sessions over 60 minutes need explicit consent language.", "info")]
    return out({"groups": len(groups), "minors_groups": minors}, findings)


def h_partnership(d, cap, ctx):
    brand = core.text(d, "brand", maximum=120)
    objective = core.text(d, "objective", maximum=300)
    disclosure = core.boolean(d, "disclosure", False)
    approvals = core.boolean(d, "asset_approvals", False)
    value = core.text(d, "player_value", maximum=300)
    measure = core.text(d, "measurement", maximum=200)
    findings = [chk("partnership.disclosure", disclosure, "Sponsorship is disclosed to players." if disclosure else "Disclose the partnership to players.", "fail"),
                chk("partnership.approvals", approvals, "Brand assets are approved before use." if approvals else "Approve brand assets before use.", "fail"),
                chk("partnership.player_value", len(value) >= 10, "Players receive a stated benefit." if len(value) >= 10 else "State the value to players.", "warning"),
                chk("partnership.measurement", bool(measure), "Success is measured." if measure else "Define how success is measured.", "info")]
    return out({"brand": brand, "objective": objective}, findings)


def h_external_boundary(d, cap, ctx):
    service = core.text(d, "service", maximum=120)
    fields = core.array(d, "data_fields", maximum=200, kind=str)
    creds = core.text(d, "credential_storage", maximum=60)
    fallback = core.text(d, "fallback", maximum=200, required=False) or ""
    terms = core.boolean(d, "terms_reviewed", False)
    slo = core.number(d, "availability_slo", minimum=0, maximum=100)
    cost = core.number(d, "cost_monthly", minimum=0, maximum=10 ** 9)
    secret_like = [f for f in fields if re.search(r"password|token|secret|key", f, re.I)]
    findings = [chk("external_boundary.data", not secret_like, "No credential-like fields are sent." if not secret_like else "Credential-like fields would leave the game: " + ", ".join(secret_like), "fail"),
                chk("external_boundary.credentials", creds in ("environment", "secret_store"), f"Credentials are stored in {creds}." if creds in ("environment", "secret_store") else "Store credentials in an environment variable or secret store only.", "fail"),
                chk("external_boundary.fallback", bool(fallback), "A fallback exists when the service is down." if fallback else "Define a fallback for service outages.", "warning"),
                chk("external_boundary.terms", terms, "Terms of service were reviewed." if terms else "Review the service terms before use.", "fail"),
                chk("external_boundary.slo", slo >= 99.0 or slo == 0, f"Availability target {slo}%." if slo >= 99.0 or slo == 0 else f"Availability target {slo}% is low; plan for outages.", "warning"),
                chk("external_boundary.cost", None, f"Monthly cost {cost} must be approved by the budget owner.")]
    return out({"service": service, "fields_sent": len(fields), "credential_storage": creds}, findings)


def h_plugin_security(d, cap, ctx):
    source = core.text(d, "source", maximum=300)
    trusted = core.boolean(d, "trusted_source", False)
    reviewed = core.boolean(d, "updates_reviewed", False)
    findings = [chk("plugin_security.source", trusted, "Plugin comes from a trusted source." if trusted else "Only install plugins from a trusted source.", "fail"),
                chk("plugin_security.updates", reviewed, "Updates are reviewed before install." if reviewed else "Review each update before install.", "warning"),
                chk("plugin_security.permissions", None, "Check the plugin's requested permissions and network access manually.")]
    return out({"source": source}, findings)


def h_licenses(d, cap, ctx):
    deps = core.array(d, "dependencies", required=True, kind=dict, maximum=5000)
    allowed = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0", "Unlicense"}
    rows = []
    for dep in deps:
        name = core.text(dep, "name", maximum=120)
        lic = core.text(dep, "license", maximum=60, required=False) or "UNKNOWN"
        rows.append({"name": name, "license": lic, "allowed": lic in allowed})
    bad = [r["name"] for r in rows if not r["allowed"]]
    findings = [chk("licenses.allowed", not bad, "All dependencies use an allowed license." if not bad else f"{len(bad)} dependency license(s) need review.", "fail" if bad else "info"),
                chk("licenses.unknown", not any(r["license"] == "UNKNOWN" for r in rows), "Every dependency declares a license." if not any(r["license"] == "UNKNOWN" for r in rows) else "Unknown licenses must be resolved before release.", "warning")]
    return out({"dependencies": len(rows), "review": bad}, findings, {"license-report.json": rows})


def h_acceptance(d, cap, ctx):
    evidence = core.array(d, "evidence", required=True, kind=dict, maximum=500)
    version = core.text(d, "candidate_version", maximum=40)
    passed = [e for e in evidence if e.get("passed") is True]
    failed = [e.get("name") for e in evidence if e.get("passed") is False]
    unverified = [e.get("name") for e in evidence if e.get("passed") is None]
    findings = [chk("acceptance.evidence", not failed, "No failed acceptance evidence." if not failed else "Failed: " + ", ".join(map(str, failed)), "fail"),
                chk("acceptance.unverified", not unverified, "All acceptance items are verified." if not unverified else f"{len(unverified)} item(s) unverified.", "warning"),
                chk("acceptance.version", bool(version), f"Candidate {version}." if version else "Name the candidate version.", "info")]
    return out({"passed": len(passed), "failed": failed, "unverified": unverified, "candidate": version}, findings)


def h_reporting(d, cap, ctx):
    cats = core.array(d, "categories", required=True, kind=str, maximum=50)
    fields = core.array(d, "evidence_fields", maximum=50, kind=str)
    confirm = core.boolean(d, "confirmation", False)
    target = core.text(d, "notify_target", maximum=60, required=False) or ""
    findings = [chk("reporting.categories", len(set(cats)) == len(cats), "Report categories are unique." if len(set(cats)) == len(cats) else "Duplicate report categories.", "fail"),
                chk("reporting.evidence", bool(fields), "Evidence fields are collected." if fields else "Collect evidence fields with each report.", "warning"),
                chk("reporting.confirmation", confirm, "Reporter confirms before submitting." if confirm else "Ask the reporter to confirm before submitting.", "info"),
                chk("reporting.notify", target != "reporter_public", "Reporter identity is not exposed publicly." if target != "reporter_public" else "Do not expose reporter identity publicly.", "fail")]
    return out({"categories": len(cats), "evidence_fields": fields}, findings)


# ================================================================ documentation generators

def _doc(title, sections):
    lines = [f"# {title}", ""]
    for head, body in sections:
        lines += [f"## {head}", ""]
        if isinstance(body, list):
            lines += [f"- {x}" for x in body] if body else ["_None listed._"]
        else:
            lines += [str(body)]
        lines.append("")
    return "\n".join(lines)


def h_technical_design(d, cap, ctx):
    system = core.text(d, "system", maximum=120)
    authority = core.text(d, "authority", maximum=200)
    inputs = core.array(d, "inputs", required=True, kind=str, maximum=100)
    outputs = core.array(d, "outputs", required=True, kind=str, maximum=100)
    states = core.array(d, "states", maximum=100, kind=str)
    store = core.text(d, "data_store", maximum=120, required=False) or "none"
    acceptance = core.array(d, "acceptance", maximum=100, kind=str)
    findings = [chk("technical_design.authority", "server" in authority.lower() or "client" not in authority.lower(), "Authority is stated as server-side where it matters." if "server" in authority.lower() else "State where authority lives; server is expected for game state.", "warning"),
                chk("technical_design.acceptance", bool(acceptance), "Acceptance criteria are listed." if acceptance else "List acceptance criteria.", "warning"),
                chk("technical_design.states", bool(states), "States are enumerated." if states else "Enumerate the states of this system.", "info")]
    md = _doc(f"Technical design: {system}", [("Authority", authority), ("Inputs", inputs), ("Outputs", outputs), ("States", states), ("Data store", store), ("Acceptance criteria", acceptance)])
    return out({"system": system, "sections": 6}, findings, {"technical-design.md": md})


def h_api_docs(d, cap, ctx):
    module = core.text(d, "module", maximum=120)
    funcs = core.array(d, "functions", required=True, kind=dict, maximum=500)
    rows, undocumented = [], []
    for f in funcs:
        name = core.text(f, "name", maximum=120)
        params = core.array(f, "params", maximum=50, kind=str)
        desc = core.text(f, "description", maximum=500, required=False) or ""
        rows.append((f"{name}({', '.join(params)})", desc or "_No description._"))
        if not desc:
            undocumented.append(name)
    md = _doc(f"API: {module}", [(sig, desc) for sig, desc in rows])
    findings = [chk("api_docs.coverage", not undocumented, "Every function has a description." if not undocumented else f"{len(undocumented)} undocumented function(s): " + ", ".join(undocumented[:10]), "warning")]
    return out({"functions": len(funcs), "undocumented": undocumented}, findings, {"api-reference.md": md})


def h_tutorial(d, cap, ctx):
    topic = core.text(d, "topic", maximum=120)
    audience = core.text(d, "audience", maximum=120)
    prereq = core.array(d, "prerequisites", maximum=50, kind=str)
    steps = core.array(d, "steps", required=True, kind=str, maximum=200)
    verify = core.array(d, "verification", maximum=50, kind=str)
    findings = [chk("tutorial.steps", 2 <= len(steps) <= 15, f"{len(steps)} steps." if 2 <= len(steps) <= 15 else f"{len(steps)} steps; keep tutorials between 2 and 15.", "warning"),
                chk("tutorial.verification", bool(verify), "Each tutorial lists how to check the result." if verify else "Add verification so readers know it worked.", "warning"),
                chk("tutorial.prerequisites", bool(prereq) or len(steps) <= 3, "Prerequisites are listed." if prereq else "List prerequisites for this tutorial.", "info")]
    md = _doc(topic, [("Audience", audience), ("Prerequisites", prereq), ("Steps", [f"{i + 1}. {s}" for i, s in enumerate(steps)]), ("Verification", verify)])
    return out({"steps": len(steps)}, findings, {"tutorial.md": md})


def h_luau_guide(d, cap, ctx):
    topic = core.text(d, "topic", maximum=120)
    exp = core.text(d, "lua_experience", maximum=40)
    concept = core.text(d, "concept", maximum=400)
    findings = [chk("luau_guide.level", exp in ("none", "beginner", "intermediate", "advanced"), f"Written for {exp} readers." if exp in ("none", "beginner", "intermediate", "advanced") else "Experience must be none, beginner, intermediate or advanced.", "fail"),
                chk("luau_guide.concept", len(concept) >= 20, "Concept is explained in full sentences." if len(concept) >= 20 else "Explain the concept in at least a full sentence.", "warning")]
    md = _doc(f"Luau guide: {topic}", [("Concept", concept), ("Audience level", exp)])
    return out({"topic": topic}, findings, {"luau-guide.md": md})


def h_runbook(d, cap, ctx):
    system = core.text(d, "system", maximum=120)
    owner = core.text(d, "owner", maximum=120)
    signals = core.array(d, "signals", required=True, kind=str, maximum=100)
    thresholds = core.array(d, "thresholds", maximum=100, kind=str)
    recovery = core.array(d, "recovery", required=True, kind=str, maximum=100)
    escalation = core.text(d, "escalation", maximum=200)
    findings = [chk("runbook.owner", bool(owner), f"Owner: {owner}." if owner else "Name an owner.", "fail"),
                chk("runbook.thresholds", len(thresholds) >= len(signals), "Each signal has a threshold." if len(thresholds) >= len(signals) else "Some signals lack a threshold.", "warning"),
                chk("runbook.recovery", len(recovery) >= 1, f"{len(recovery)} recovery step(s).", "info"),
                chk("runbook.escalation", bool(escalation), "Escalation path is defined." if escalation else "Define the escalation path.", "fail")]
    md = _doc(f"Runbook: {system}", [("Owner", owner), ("Signals", signals), ("Thresholds", thresholds), ("Recovery", recovery), ("Escalation", escalation)])
    return out({"system": system, "signals": len(signals)}, findings, {"runbook.md": md})


def h_help_center(d, cap, ctx):
    topic = core.text(d, "topic", maximum=160)
    audience = core.text(d, "audience", maximum=120)
    facts = core.array(d, "facts", required=True, kind=str, maximum=100)
    actions = core.array(d, "actions", maximum=50, kind=str)
    support = core.text(d, "support_route", maximum=200)
    findings = [chk("help_center.facts", len(facts) >= 1, f"{len(facts)} fact(s) listed; each must be verified." , "info"),
                chk("help_center.actions", bool(actions), "Each article ends with an action." if actions else "End the article with what the player should do.", "warning"),
                chk("help_center.support", bool(support), "Support route is stated." if support else "State the support route.", "info")]
    md = _doc(topic, [("Who this is for", audience), ("What to know", facts), ("What to do", actions), ("Get support", support)])
    return out({"facts": len(facts)}, findings, {"help-article.md": md})


def h_release_notes(d, cap, ctx):
    version = core.text(d, "version", maximum=40)
    changes = core.array(d, "changes", required=True, kind=str, maximum=500)
    known = core.array(d, "known_issues", maximum=200, kind=str)
    migration = core.text(d, "migration", maximum=1000, required=False) or ""
    findings = [chk("release_notes.version", bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), f"Version {version} is semantic." if re.fullmatch(r"\d+\.\d+\.\d+", version) else "Use semantic versioning (MAJOR.MINOR.PATCH).", "warning"),
                chk("release_notes.known_issues", True, f"{len(known)} known issue(s) disclosed."),
                chk("release_notes.migration", bool(migration) or not any("data" in c.lower() or "schema" in c.lower() for c in changes), "Migration notes cover data or schema changes." if migration or not any("data" in c.lower() or "schema" in c.lower() for c in changes) else "Data or schema changes need migration notes.", "fail")]
    md = _doc(f"Release {version}", [("Changes", changes), ("Known issues", known), ("Migration", migration or "_None._")])
    return out({"version": version, "changes": len(changes)}, findings, {"RELEASE-NOTES.md": md})


def h_incident(d, cap, ctx):
    inc = core.text(d, "incident", maximum=160)
    sev = core.text(d, "severity", maximum=10)
    detected = core.text(d, "detected_at", maximum=40)
    flag = core.text(d, "feature_flag", maximum=80, required=False) or ""
    known_good = core.text(d, "known_good_version", maximum=40, required=False) or ""
    owner = core.text(d, "owner", maximum=120)
    if sev not in ("SEV1", "SEV2", "SEV3", "SEV4"):
        core.fail("INPUT_INVALID", "severity must be SEV1, SEV2, SEV3 or SEV4.", severity=sev)
    findings = [chk("incident.owner", bool(owner), f"Owner: {owner}." if owner else "Assign an incident owner.", "fail"),
                chk("incident.mitigation", bool(flag) or bool(known_good), "A mitigation exists (feature flag or known-good version)." if flag or known_good else "Identify a mitigation: a feature flag or a known-good version.", "fail" if sev in ("SEV1", "SEV2") else "warning"),
                chk("incident.timestamp", bool(re.match(r"\d{4}-\d{2}-\d{2}T", detected)), "Detection time is recorded." if re.match(r"\d{4}-\d{2}-\d{2}T", detected) else "Record detection time as ISO 8601.", "warning")]
    md = _doc(f"Incident: {inc} ({sev})", [("Owner", owner), ("Detected", detected), ("Mitigation", [x for x in (f"Feature flag: {flag}" if flag else "", f"Known-good version: {known_good}" if known_good else "") if x])])
    return out({"severity": sev, "mitigation_available": bool(flag or known_good)}, findings, {"incident-report.md": md})


def h_dashboard(d, cap, ctx):
    title = core.text(d, "title", maximum=160)
    metrics = core.array(d, "metrics", required=True, kind=str, maximum=200)
    updated = core.text(d, "updated", maximum=40)
    status = core.text(d, "status", maximum=20)
    findings = [chk("dashboard.status", status in ("ok", "warning", "critical", "stale"), f"Status {status}." if status in ("ok", "warning", "critical", "stale") else "Status must be ok, warning, critical or stale.", "fail"),
                chk("dashboard.freshness", bool(re.match(r"\d{4}-\d{2}-\d{2}", updated)), "Last-updated date is recorded." if re.match(r"\d{4}-\d{2}-\d{2}", updated) else "Record when the data was last updated.", "warning"),
                chk("dashboard.metrics", len(metrics) >= 1, f"{len(metrics)} metric(s) on the board.")]
    md = _doc(title, [("Status", status), ("Last updated", updated), ("Metrics", metrics)])
    return out({"metrics": len(metrics), "status": status}, findings, {"dashboard.md": md})


def h_positioning(d, cap, ctx):
    title = core.text(d, "title", maximum=160)
    audience = core.text(d, "audience", maximum=160)
    fantasy = core.text(d, "fantasy", maximum=300)
    diffs = core.array(d, "differentiators", required=True, kind=str, maximum=20)
    minutes = core.number(d, "session_minutes", minimum=1, maximum=600)
    findings = [chk("positioning.differentiators", len(diffs) >= 2, f"{len(diffs)} differentiator(s)." if len(diffs) >= 2 else "Give at least two differentiators.", "warning"),
                chk("positioning.fantasy", len(fantasy.split()) >= 4, "The core fantasy is stated." if len(fantasy.split()) >= 4 else "State the fantasy in at least four words.", "info"),
                chk("positioning.claims", None, "Every differentiator must be true for the shipped experience; verify before publishing.")]
    md = _doc(f"Positioning: {title}", [("Audience", audience), ("Fantasy", fantasy), ("Differentiators", diffs), ("Session length", f"{minutes} minutes")])
    return out({"differentiators": len(diffs)}, findings, {"positioning.md": md})


def h_creative_brief(d, cap, ctx):
    title = core.text(d, "title", maximum=160)
    subject = core.text(d, "subject", maximum=300)
    mood = core.text(d, "mood", maximum=200)
    palette = core.array(d, "palette", maximum=12, kind=str)
    actual = core.boolean(d, "actual_gameplay", False)
    text = core.text(d, "text", maximum=300, required=False) or ""
    bad_colors = [c for c in palette if not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", c)]
    findings = [chk("creative_brief.gameplay", actual, "Imagery shows actual gameplay." if actual else "Imagery must show actual gameplay, not concept art.", "fail"),
                chk("creative_brief.palette", not bad_colors, "Palette entries are hex colors." if not bad_colors else "Invalid palette entries: " + ", ".join(bad_colors), "warning"),
                chk("creative_brief.text", len(text) <= 60, "Overlay text is short." if len(text) <= 60 else "Overlay text over 60 characters is hard to read at thumbnail size.", "info"),
                chk("creative_brief.accuracy", None, "Thumbnails must not show features the game lacks; confirm with the team.")]
    md = _doc(f"Creative brief: {title}", [("Subject", subject), ("Mood", mood), ("Palette", palette), ("Overlay text", text or "_None._")])
    return out({"palette": len(palette), "actual_gameplay": actual}, findings, {"creative-brief.md": md})


def h_community(d, cap, ctx):
    channels = core.array(d, "channels", required=True, kind=str, maximum=50)
    mods = core.integer(d, "moderators", minimum=0, maximum=10000)
    playtests = core.integer(d, "weekly_playtests", minimum=0, maximum=100)
    hours = core.number(d, "response_hours", minimum=0, maximum=24 * 14)
    states = core.array(d, "issue_states", maximum=20, kind=str)
    findings = [chk("community.coverage", mods >= 1, f"{mods} moderator(s) cover {len(channels)} channel(s)." if mods >= 1 else "No moderators assigned.", "fail" if mods == 0 else "info"),
                chk("community.response", hours <= 48, f"Target response within {hours} hours." if hours <= 48 else f"Response target {hours} hours is slow.", "warning"),
                chk("community.issues", {"open", "closed"} <= set(states) or not states, "Issue states include open and closed." if {"open", "closed"} <= set(states) or not states else "Define open and closed issue states.", "info"),
                chk("community.playtests", playtests >= 1, f"{playtests} playtest(s) per week." if playtests >= 1 else "Schedule at least one playtest per week.", "info")]
    md = _doc("Community operations plan", [("Channels", channels), ("Moderators", str(mods)), ("Weekly playtests", str(playtests)), ("Response target (hours)", str(hours)), ("Issue states", states)])
    return out({"channels": len(channels), "moderators": mods}, findings, {"community-plan.md": md})


def h_portfolio(d, cap, ctx):
    project = core.text(d, "project", maximum=160)
    role = core.text(d, "role", maximum=120)
    goals = core.array(d, "goals", required=True, kind=str, maximum=20)
    choices = core.array(d, "choices", maximum=20, kind=str)
    outcomes = core.array(d, "outcomes", maximum=20, kind=str)
    lessons = core.array(d, "lessons", maximum=20, kind=str)
    evidence = core.boolean(d, "evidence_available", False)
    findings = [chk("portfolio.evidence", evidence, "Outcomes are backed by evidence." if evidence else "Every outcome needs evidence (metrics, playtests, or releases).", "warning"),
                chk("portfolio.outcomes", bool(outcomes), "Outcomes are listed." if outcomes else "List measurable outcomes.", "warning"),
                chk("portfolio.lessons", bool(lessons), "Lessons are recorded." if lessons else "Record what you would change.", "info")]
    md = _doc(f"Portfolio: {project}", [("Role", role), ("Goals", goals), ("Key choices", choices), ("Outcomes", outcomes), ("Lessons", lessons)])
    return out({"goals": len(goals), "outcomes": len(outcomes)}, findings, {"portfolio.md": md})


# ================================================================ genre and place planning

def _genre(name):
    g = places()["genres"].get(name)
    if g is None:
        core.fail("RESOURCE_INVALID", "Unknown genre preset.", genre=name)
    return g


def _genre_result(name, d, findings, metrics):
    g = _genre(name)
    systems, remotes, keys = g.get("systems", []), g.get("remotes", []), g.get("data_keys", [])
    plan = {"genre": name, "systems": systems, "remotes": remotes, "data_keys": keys, "defaults": g.get("defaults", {})}
    findings = findings + [chk("genre.remote_authority", True, "Remotes are validated on the server; the client never grants rewards or ownership.")]
    return out(dict(metrics, genre=name), findings, {f"genre-{name}-plan.json": plan})


def h_place_template(d, cap, ctx):
    genre = core.text(d, "genre", maximum=40)
    seed = core.integer(d, "seed", minimum=0, maximum=2 ** 31)
    rooms = core.integer(d, "rooms", minimum=1, maximum=50)
    if genre not in places()["genres"]:
        core.fail("INPUT_INVALID", "Unknown genre.", genre=genre, allowed=sorted(places()["genres"]))
    gen = random.Random(seed)
    names = ["spawn", "hub", "challenge", "reward", "social", "secret", "exit"]
    rows = []
    for i in range(rooms):
        rows.append({"room": f"room_{i + 1:02d}", "zone": names[i % len(names)], "size": [gen.choice([48, 64, 96]), gen.choice([24, 32]), gen.choice([48, 64, 96])]})
    bounds = [sum(r["size"][0] for r in rows), max(r["size"][1] for r in rows), max(r["size"][2] for r in rows)]
    findings = [chk("place_template.bounds", bounds[0] <= 4096 and bounds[2] <= 4096, f"Combined footprint {bounds[0]}x{bounds[2]} studs.", "warning" if bounds[0] > 4096 else "info"),
                chk("place_template.seed", True, f"Layout is deterministic for seed {seed}.")]
    layout = {"genre": genre, "seed": seed, "rooms": rows, "bounds": bounds}
    return out({"genre": genre, "rooms": len(rows), "bounds": bounds}, findings, {"place-layout.json": layout})


def h_genre_obby(d, cap, ctx):
    stages = core.integer(d, "stages", minimum=1, maximum=500)
    jd = core.number(d, "jump_distance", minimum=0.1, maximum=200)
    walk = core.number(d, "walk_speed", minimum=0, maximum=200)
    jp = core.number(d, "jump_power", minimum=0.1, maximum=500)
    g = core.number(d, "gravity", minimum=0.1, maximum=1000)
    every = core.integer(d, "checkpoint_every", minimum=1, maximum=500)
    mobile = core.boolean(d, "mobile", False)
    air = 2 * jp / g
    reach = walk * air
    apex = jp * jp / (2 * g)
    cps = math.ceil(stages / every)
    findings = [chk("obby.reachable_gaps", jd <= reach, f"Gap {jd} studs is reachable (air reach {round(reach, 2)})." if jd <= reach else f"Gap {jd} exceeds reach {round(reach, 2)}; the stage is impossible.", "fail"),
                chk("obby.mobile_gaps", (not mobile) or jd <= reach * 0.85, "Gaps leave a mobile margin." if (not mobile) or jd <= reach * 0.85 else "Mobile players need a 15% margin on gaps.", "warning"),
                chk("obby.checkpoints", cps >= 2 or stages == 1, f"{cps} checkpoint(s) across {stages} stage(s).")]
    return _genre_result("obby", d, findings, {"air_seconds": round(air, 4), "reach_studs": round(reach, 3), "apex_studs": round(apex, 3), "checkpoints": cps})


def h_genre_tycoon(d, cap, ctx):
    drops = core.integer(d, "droppers", minimum=0, maximum=500)
    value = core.number(d, "drop_value", minimum=0, maximum=1e9)
    interval = core.number(d, "drop_interval", minimum=0.1, maximum=3600)
    seconds = core.integer(d, "seconds", minimum=1, maximum=86400)
    unlock = core.number(d, "unlock_cost", minimum=0, maximum=1e12)
    mult = core.number(d, "rebirth_multiplier", minimum=1, maximum=100)
    owner = core.text(d, "plot_owner", maximum=80)
    req = core.text(d, "requester", maximum=80)
    steps_ok(min(seconds, 2000), ctx)
    rate = drops * value / interval
    total = rate * seconds
    t_unlock = unlock / rate if rate > 0 else None
    findings = [chk("tycoon.authority", owner == req, "Only the plot owner can purchase here." if owner == req else "Requester is not the plot owner; reject the purchase.", "fail"),
                chk("tycoon.income", rate > 0, f"Income {round(rate, 3)} per second.", "warning" if rate == 0 else "info"),
                chk("tycoon.first_unlock", t_unlock is None or t_unlock <= seconds, f"First unlock in {round(t_unlock, 1)} s." if t_unlock is not None else "No income, so nothing unlocks.", "warning"),
                chk("tycoon.rebirth", mult <= 5, f"Rebirth multiplier {mult}." if mult <= 5 else "Rebirth multipliers above 5 break progression balance.", "warning")]
    return _genre_result("tycoon", d, findings, {"income_per_second": round(rate, 4), "income_total": round(total, 2), "seconds_to_first_unlock": round(t_unlock, 2) if t_unlock is not None else None})


def h_genre_simulator(d, cap, ctx):
    cap_n = core.integer(d, "capacity", minimum=1, maximum=100000)
    act = core.number(d, "action_seconds", minimum=0.1, maximum=3600)
    price = core.number(d, "sell_price", minimum=0, maximum=1e9)
    upgrade = core.number(d, "upgrade_cost", minimum=0, maximum=1e12)
    bal = core.number(d, "balance", minimum=0, maximum=1e12)
    actions = core.integer(d, "actions", minimum=1, maximum=100000)
    rebirth = core.number(d, "rebirth_cost", minimum=0, maximum=1e15)
    steps_ok(min(actions, 10000), ctx)
    earned_per_action = price
    fill_cycles = math.ceil(actions / cap_n)
    total = bal + actions * earned_per_action
    actions_to_upgrade = max(0, math.ceil((upgrade - bal) / earned_per_action)) if earned_per_action > 0 and upgrade > bal else 0
    findings = [chk("simulator.capacity", True, f"Capacity {cap_n} causes {fill_cycles} sell cycle(s) for {actions} action(s)."),
                chk("simulator.upgrade", upgrade <= bal or earned_per_action > 0, f"About {actions_to_upgrade} more action(s) to afford the upgrade." if upgrade > bal else "Upgrade is already affordable.", "warning" if upgrade > bal and earned_per_action == 0 else "info"),
                chk("simulator.rebirth", rebirth == 0 or rebirth > upgrade, "Rebirth costs more than the upgrade." if rebirth == 0 or rebirth > upgrade else "Rebirth should cost more than a single upgrade.", "warning"),
                chk("simulator.time", act * actions / 60 <= 600, f"Estimated play time {round(act * actions / 60, 1)} minutes.", "info")]
    return _genre_result("simulator", d, findings, {"earned_per_action": earned_per_action, "projected_balance": round(total, 2), "actions_to_upgrade": actions_to_upgrade, "sell_cycles": fill_cycles})


def h_genre_roleplay(d, cap, ctx):
    plots = core.array(d, "plots", required=True, kind=dict, maximum=5000)
    actor = core.text(d, "actor", maximum=80)
    action = core.text(d, "action", maximum=40)
    perm = core.text(d, "permission", maximum=40)
    jobs = core.array(d, "jobs", maximum=100, kind=str)
    report = core.boolean(d, "report", False)
    block = core.boolean(d, "block", False)
    owned = {p.get("id") for p in plots if p.get("owner") == actor}
    target = core.text(d, "plot", maximum=80, required=False) or ""
    allowed = action != "build" or target in owned or perm == "builder"
    findings = [chk("roleplay.plot_permission", allowed, "The action is allowed for this plot." if allowed else f"{actor} may not {action} on {target or 'this plot'}.", "fail" if not allowed else "info"),
                chk("roleplay.jobs", bool(jobs), f"{len(jobs)} job(s) defined." if jobs else "Define the jobs players can take.", "info"),
                chk("roleplay.safety", report and block, "Report and block are available." if report and block else "Report and block are required in roleplay.", "fail")]
    return _genre_result("roleplay", d, findings, {"allowed": allowed, "owned_plots": len(owned)})


def h_genre_tower(d, cap, ctx):
    waves = core.integer(d, "waves", minimum=1, maximum=200)
    base = core.integer(d, "base_enemies", minimum=1, maximum=10000)
    growth = core.number(d, "growth", minimum=1.0, maximum=3.0)
    ehp = core.number(d, "enemy_health", minimum=1, maximum=1e9)
    towers = core.integer(d, "towers", minimum=0, maximum=500)
    tdmg = core.number(d, "tower_damage", minimum=0, maximum=1e9)
    tint = core.number(d, "tower_interval", minimum=0.05, maximum=600)
    wsec = core.number(d, "wave_seconds", minimum=1, maximum=3600)
    plen = core.number(d, "path_length", minimum=1, maximum=100000)
    espeed = core.number(d, "enemy_speed", minimum=0.1, maximum=1000)
    steps_ok(waves, ctx)
    dps = towers * tdmg / tint
    arrive = plen / espeed
    rows, failing = [], []
    for w in range(1, waves + 1):
        n = math.ceil(base * growth ** (w - 1))
        clear = n * ehp / dps if dps > 0 else math.inf
        rows.append({"wave": w, "enemies": n, "clear_seconds": round(clear, 2) if clear != math.inf else None})
        if clear > wsec:
            failing.append(w)
    findings = [chk("tower.dps", dps > 0, f"Tower DPS {round(dps, 2)}." if dps > 0 else "No damage output; no wave can be cleared.", "fail" if dps == 0 else "info"),
                chk("tower.waves", not failing, "Every wave clears within its time." if not failing else f"Waves {failing[:10]} cannot clear in {wsec}s at this damage.", "warning"),
                chk("tower.path", arrive <= wsec, f"Enemies walk the path in {round(arrive, 2)} s." if arrive <= wsec else "Enemies take longer to arrive than a wave lasts; lengthen the wave or shorten the path.", "warning")]
    return _genre_result("tower", d, findings, {"dps": round(dps, 3), "waves": rows[:50], "failing_waves": failing[:50], "path_seconds": round(arrive, 3)})


def h_genre_survival(d, cap, ctx):
    maps = core.array(d, "maps", required=True, kind=str, maximum=50)
    rsec = core.number(d, "round_seconds", minimum=30, maximum=86400)
    players = core.integer(d, "players", minimum=1, maximum=500)
    hazard = core.number(d, "hazard_start", minimum=0, maximum=86400)
    late = core.text(d, "late_join", maximum=40)
    reward = core.text(d, "reward", maximum=200)
    findings = [chk("survival.hazard_timing", hazard < rsec, f"Hazard starts at {hazard}s of {rsec}s." if hazard < rsec else "Hazard starts after the round ends.", "fail"),
                chk("survival.late_join", late in ("spectate", "next_round", "wait"), f"Late joiners {late}." if late in ("spectate", "next_round", "wait") else "Define late join as spectate, next_round or wait.", "warning"),
                chk("survival.players", players <= 50, "Player count is within a typical round size." if players <= 50 else "More than 50 players per round needs load testing.", "warning"),
                chk("survival.reward", bool(reward), "Reward is defined." if reward else "Define the round reward; grant it on the server once.", "warning")]
    return _genre_result("survival", d, findings, {"maps": len(maps), "hazard_fraction": round(hazard / rsec, 4) if rsec else None, "hazard_after_seconds": round(max(0.0, rsec - hazard), 2)})


def h_genre_rpg(d, cap, ctx):
    level = core.integer(d, "level", minimum=1, maximum=500)
    bh = core.number(d, "base_health", minimum=1, maximum=1e9)
    hpl = core.number(d, "health_per_level", minimum=0, maximum=1e9)
    bd = core.number(d, "base_damage", minimum=0, maximum=1e9)
    dpl = core.number(d, "damage_per_level", minimum=0, maximum=1e9)
    party = core.integer(d, "party_size", minimum=1, maximum=20)
    quests = core.integer(d, "quest_count", minimum=0, maximum=10000)
    save = core.integer(d, "save_version", minimum=1, maximum=100000)
    steps_ok(level, ctx)
    table = [{"level": l, "health": bh + hpl * (l - 1), "damage": bd + dpl * (l - 1)} for l in range(1, level + 1)]
    findings = [chk("rpg.curve", dpl <= hpl * 2 or hpl == 0, "Damage growth stays within twice health growth." if dpl <= hpl * 2 or hpl == 0 else "Damage outpaces health by more than 2x; fights become one-shots.", "warning"),
                chk("rpg.party", party <= 4, "Party size is within a common range." if party <= 4 else "Parties above 4 need encounter scaling.", "info"),
                chk("rpg.quests", quests >= 0, f"{quests} quest(s) tracked."),
                chk("rpg.save", save >= 1, f"Save version {save}; migrate older saves with the migration capability.", "info")]
    return _genre_result("rpg", d, findings, {"level": level, "final_health": table[-1]["health"], "final_damage": table[-1]["damage"], "party": party})


def h_genre_horror(d, cap, ctx):
    duration = core.number(d, "duration", minimum=10, maximum=86400)
    safe = core.number(d, "safe_seconds", minimum=0, maximum=86400)
    chase = core.number(d, "chase_seconds", minimum=0, maximum=86400)
    puzzle = core.number(d, "puzzle_seconds", minimum=0, maximum=86400)
    cps = core.integer(d, "checkpoint_count", minimum=0, maximum=500)
    reduced = core.boolean(d, "reduced_intensity", False)
    flashing = core.boolean(d, "flashing", False)
    total = safe + chase + puzzle
    findings = [chk("horror.timing", abs(total - duration) < 1e-6, "Phases add up to the duration." if abs(total - duration) < 1e-6 else f"Phases total {total}s but duration is {duration}s.", "fail"),
                chk("horror.safe_space", safe >= 0.15 * duration, "At least 15% of the run is a safe space." if safe >= 0.15 * duration else "Add safe space; under 15% leaves no recovery.", "warning"),
                chk("horror.checkpoints", cps >= 1 or duration < 300, f"{cps} checkpoint(s)." if cps >= 1 or duration < 300 else "Add a checkpoint for runs over five minutes.", "warning"),
                chk("horror.reduced", reduced, "Reduced-intensity mode is available." if reduced else "Offer a reduced-intensity mode.", "warning"),
                chk("horror.flashing", not flashing or reduced, "Flashing is paired with a reduced mode." if not flashing or reduced else "Flashing content needs a reduced-intensity option.", "fail")]
    return _genre_result("horror", d, findings, {"chase_fraction": round(chase / duration, 4), "phases_total": total})


def h_genre_hangout(d, cap, ctx):
    zones = core.array(d, "zones", required=True, kind=str, maximum=50)
    prop = core.integer(d, "prop_limit_per_player", minimum=0, maximum=1000)
    block = core.boolean(d, "block", False)
    report = core.boolean(d, "report", False)
    join = core.number(d, "join_seconds", minimum=0, maximum=3600)
    findings = [chk("hangout.safety", block and report, "Block and report are available." if block and report else "Block and report are required.", "fail"),
                chk("hangout.props", prop <= 100, f"Up to {prop} props per player." if prop <= 100 else "Prop limits above 100 per player cost memory and moderation time.", "warning"),
                chk("hangout.join", join <= 10, "Join time is short." if join <= 10 else "Long join times lose visitors.", "info"),
                chk("hangout.zones", len(zones) >= 1 and len(set(zones)) == len(zones), "Zones are unique." if len(set(zones)) == len(zones) else "Duplicate zones.", "fail")]
    return _genre_result("hangout", d, findings, {"zones": len(zones), "prop_limit": prop})


def h_genre_sandbox(d, cap, ctx):
    owner = core.text(d, "owner", maximum=80)
    actor = core.text(d, "actor", maximum=80)
    placed = core.integer(d, "placed", minimum=0, maximum=100000)
    limit = core.integer(d, "limit", minimum=1, maximum=100000)
    ops = core.array(d, "operations", required=False, maximum=5000, kind=dict)
    moderated = core.boolean(d, "moderated", False)
    save = core.integer(d, "save_version", minimum=1, maximum=100000)
    denied = [o for o in ops if o.get("actor") != owner and not o.get("granted")]
    findings = [chk("sandbox.permissions", not denied, "Every operation is by the owner or a granted builder." if not denied else f"{len(denied)} operation(s) by ungranted users are rejected.", "fail" if denied else "info"),
                chk("sandbox.limit", placed <= limit, f"{placed} of {limit} placed objects." if placed <= limit else f"{placed} objects exceed the limit {limit}.", "fail" if placed > limit else "info"),
                chk("sandbox.moderation", moderated, "User-placed content is moderated." if moderated else "Moderate user-placed content before it is shown to others.", "fail"),
                chk("sandbox.save", save >= 1, f"Save version {save}.", "info")]
    return _genre_result("sandbox", d, findings, {"owner": owner, "actor": actor, "placed": placed, "denied_operations": len(denied)})


HANDLERS = {
    "localization": h_localization, "translation": h_translation, "captions": h_captions, "screen_reader": h_screen_reader,
    "cognitive": h_cognitive, "contrast": h_contrast, "flashing": h_flashing, "reduced_motion": h_reduced_motion,
    "accessible_controls": h_accessible_controls, "copy": h_copy, "error_messages": h_error_messages,
    "reproduction": h_reproduction, "safety_ux": h_safety_ux, "localization_extract": h_localization_extract,
    "dialogue_playback": h_dialogue_playback, "storytelling": h_storytelling, "ugc_text": h_ugc_text, "inclusion": h_inclusion,
    "data_retention": h_data_retention, "ugc_flow": h_ugc_flow, "threat_model": h_threat_model, "admin": h_admin,
    "evidence": h_evidence, "safety_review": h_safety_review, "block_mute": h_block_mute, "age_features": h_age_features,
    "private_safety": h_private_safety, "rights": h_rights, "music_rights": h_music_rights, "voice": h_voice, "chat": h_chat,
    "guild": h_guild, "referrals": h_referrals, "playtest": h_playtest, "partnership": h_partnership,
    "external_boundary": h_external_boundary, "plugin_security": h_plugin_security, "licenses": h_licenses,
    "acceptance": h_acceptance, "reporting": h_reporting,
    "technical_design": h_technical_design, "api_docs": h_api_docs, "tutorial": h_tutorial, "luau_guide": h_luau_guide,
    "runbook": h_runbook, "help_center": h_help_center, "release_notes": h_release_notes, "incident": h_incident,
    "dashboard": h_dashboard, "positioning": h_positioning, "creative_brief": h_creative_brief, "community": h_community,
    "portfolio": h_portfolio,
    "place_template": h_place_template, "genre_obby": h_genre_obby, "genre_tycoon": h_genre_tycoon,
    "genre_simulator": h_genre_simulator, "genre_roleplay": h_genre_roleplay, "genre_tower": h_genre_tower,
    "genre_survival": h_genre_survival, "genre_rpg": h_genre_rpg, "genre_horror": h_genre_horror,
    "genre_hangout": h_genre_hangout, "genre_sandbox": h_genre_sandbox,
}
