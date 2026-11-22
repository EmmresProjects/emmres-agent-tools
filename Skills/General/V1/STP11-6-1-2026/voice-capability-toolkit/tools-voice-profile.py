#!/usr/bin/env python3
"""Voice Capability Toolkit - voice identity, rights and consent tool.

Creates, validates, plans, variants, checks and revokes voice profiles, and
enforces consent/licence scope before any conversion or publish step.

Commands
--------
    capabilities      Machine-readable command index
    create            Build an original voice profile from a brief
    validate          Validate a profile, consent record, brief or request
    plan              Render a design plan (identity + prosody) for a profile
    variants          Generate related variants of an approved profile
    reference-board   Build a reference board (targets, forbidden, eval lines)
    verify-consent    Validate a consent record against a requested use
    check-use         Enforce origin + consent scope for a requested use
    enroll            Register an approved consent record in a local store
    revoke            Revoke a voice profile (approval required)

All outputs are JSON on stdout (or --output). Every write is atomic and every
destructive action requires --approve.
"""

from __future__ import annotations

import argparse
import copy
import glob
import importlib.util
import os
import sys

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load(filename, modname):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load("tools-runtime-common.py", "_voice_rt")
ENGINE = _load("tools-audio-engine.py", "_voice_engine")
PROG = "tools-voice-profile.py"

CAPABILITIES = [
    {"command": "capabilities", "summary": "Print this command index.", "inputs": [], "outputs": ["JSON"]},
    {"command": "create", "summary": "Create a validated original voice profile from a design brief (age, gender presentation, pitch, resonance, pace, energy, accent, styles, personality).", "inputs": ["--brief", "--text", "--name"], "outputs": ["profile JSON", "design plan"]},
    {"command": "validate", "summary": "Validate a profile, consent record, brief or usage request against asset-schemas.json.", "inputs": ["--input"], "outputs": ["validation report"]},
    {"command": "plan", "summary": "Combine a profile with a script to produce a full performance plan (prosody events per segment, style/emotion mapping, render settings).", "inputs": ["--profile", "--text"], "outputs": ["performance plan JSON"]},
    {"command": "variants", "summary": "Generate deterministic related variants of one approved profile for moods, ages, languages or contexts.", "inputs": ["--input", "--count", "--axis"], "outputs": ["variant profiles"]},
    {"command": "reference-board", "summary": "Turn descriptive references into a structured voice board: target traits, forbidden traits, sample lines and evaluation criteria.", "inputs": ["--input", "--text"], "outputs": ["reference board JSON + Markdown"]},
    {"command": "verify-consent", "summary": "Check a consent record's structure, signature field, expiry and scope for a requested use.", "inputs": ["--input", "--request"], "outputs": ["verification report"]},
    {"command": "check-use", "summary": "Enforce origin policy, consent scope, territory, medium and commercial rights for a use of a profile.", "inputs": ["--profile", "--request", "--consent"], "outputs": ["allow/block decision"]},
    {"command": "enroll", "summary": "Store an approved consent record (with a hash chain) in a local consent store directory.", "inputs": ["--input", "--store"], "outputs": ["enrollment record"]},
    {"command": "revoke", "summary": "Revoke a profile and list every dependent asset found under --assets-root. Requires --approve.", "inputs": ["--input", "--assets-root"], "outputs": ["revocation record"]},
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _schemas(assets_dir=None):
    return RT.load_asset_json("asset-schemas.json", assets_dir)[0]


def _presets(assets_dir=None):
    return RT.load_asset_json("asset-voice-presets.json", assets_dir)[0]


def _policy(assets_dir=None):
    return RT.load_asset_json("asset-policy.json", assets_dir)[0]


def _validate(document, schema_name, *, assets_dir=None, label=None):
    schema_doc = _schemas(assets_dir)
    problems = RT.validate_schema(document, RT.schema_for(schema_doc, schema_name))
    if problems:
        raise RT.ToolError(
            f"{label or schema_name} failed validation ({len(problems)} problem(s)).",
            code=RT.EXIT_INVALID_INPUT,
            hint="Fix the reported fields and re-run; use the 'validate' command "
                 "for a report against any document.",
            details={"problems": problems[:25]},
        )
    return True


def load_brief(args):
    """Read a brief JSON, inline text, or fall back to documented defaults."""
    if args.brief:
        data = RT.read_json_file(args.brief)
        if "profile_id" in data and "design" in data:
            return data, "profile"
        if "categories" in data:
            raise RT.ToolError(
                f"'{args.brief}' is a catalog, not a voice brief.",
                code=RT.EXIT_INVALID_INPUT,
                hint="Pass a brief with profile_id/design fields, or use "
                     "tools-catalog-builder.py to work with catalogs.",
            )
        return data, "brief"
    if args.text:
        return parse_brief_text(args.text), "text"
    if args.name:
        return {"profile_id": RT.slugify(args.name), "display_name": args.name.title(),
                "origin": {"type": "synthetic-original"}}, "name"
    raise RT.ToolError(
        "No brief supplied.",
        code=RT.EXIT_INVALID_INPUT,
        hint="Pass --brief <file.json>, --text '<description>', --name '<name>', "
             "or use the defaults with --sample.",
    )


def parse_brief_text(text):
    """Extract design controls from a free-text voice brief."""
    lowered = text.lower()
    presets = _presets()
    design = {}
    pitch_match = re_search(r"\b(\d{2,3})\s*hz\b", lowered)
    if pitch_match:
        design["pitch"] = {"base_hz": float(pitch_match), "range_st": 10.0}
    wpm_match = re_search(r"\b(\d{2,3})\s*(?:wpm|words per minute)\b", lowered)
    pace = float(wpm_match) if wpm_match else 155.0
    design["pace_wpm"] = pace
    for gender in presets["gender_presentation"]:
        if gender in lowered:
            design["gender_presentation"] = gender
            break
    for age in presets["age_presentation"]:
        if age.replace("-", " ") in lowered.replace("-", " ") or age in lowered:
            design["age_presentation"] = age
            break
    for resonance in ("deep", "chest", "balanced", "head", "bright"):
        if resonance in lowered:
            design["resonance"] = resonance
            break
    styles = [name for name in presets["style_attributes"] if name in lowered]
    design["personality"] = styles[:4]
    design["energy"] = RT.clamp(0.4 + 0.05 * len(styles), 0.1, 0.95)
    design["texture"] = [word for word in ("clean", "warm", "airy", "raspy", "smooth")
                         if word in lowered][:3]
    accent_locale = re_search(r"\b([a-z]{2}-[A-Z]{2})\b", text)
    if accent_locale:
        design["accent"] = {"locale": accent_locale, "strength": 0.4}
    name = re_search(r"^(?:voice|profile|name|character)\s*[:\-]\s*(.+)$", text, flags=0x8)
    profile_id = RT.slugify(name or "voice")
    return {
        "profile_id": profile_id,
        "display_name": (name or "Voice").strip().title()[:120],
        "origin": {"type": "synthetic-original"},
        "design": design,
        "preferred_styles": styles,
        "source_text": text[:400],
    }


def re_search(pattern, text, flags=0):
    import re
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def build_profile(document, presets, *, umbrella=None, name=None):
    """Normalize a brief or partial profile into a complete, valid profile."""
    source = copy.deepcopy(document)
    if "design" in source and "rights" in source and "origin" in source:
        profile = source
        profile.setdefault("profile_id", RT.slugify(profile.get("display_name", "voice")))
        profile.setdefault("display_name", profile["profile_id"].replace("-", " ").title())
        return profile

    design = source.get("design", {})
    presets_design = presets["gender_presentation"]
    gender = design.get("gender_presentation", "neutral")
    if gender not in presets_design:
        raise RT.ToolError(
            f"Unknown gender_presentation '{gender}'.",
            code=RT.EXIT_INVALID_INPUT,
            details={"allowed": sorted(presets_design)},
        )
    age = design.get("age_presentation", "adult")
    if age not in presets["age_presentation"]:
        raise RT.ToolError(
            f"Unknown age_presentation '{age}'.",
            code=RT.EXIT_INVALID_INPUT,
            details={"allowed": sorted(presets["age_presentation"])},
        )
    base_pitch = float(design.get("pitch", {}).get("base_hz", 150.0))
    base_pitch *= presets_design[gender]["f0_mult"] * presets["age_presentation"][age]["f0_mult"]
    base_pitch = RT.clamp(round(base_pitch, 2), 55.0, 480.0)
    styles = [s for s in (source.get("preferred_styles") or design.get("personality") or [])
              if s in presets["style_attributes"]]
    if not styles:
        styles = ["warm", "conversational"]
    style_pace = sum(presets["style_attributes"][s].get("pace_wpm_delta", 0) for s in styles) / len(styles)
    style_energy = sum(presets["style_attributes"][s].get("energy_delta", 0) for s in styles) / len(styles)
    pace = RT.clamp(float(design.get("pace_wpm", 155.0)) + style_pace, 70.0, 240.0)
    energy = RT.clamp(float(design.get("energy", 0.55)) + style_energy, 0.05, 1.0)
    display = name or source.get("display_name") or source.get("profile_id") or "voice"
    profile_id = RT.slugify(source.get("profile_id") or display)
    origin = source.get("origin") or {"type": "synthetic-original"}
    rights = source.get("rights") or {
        "origin_license": "internal-original-design",
        "allowed_uses": ["internal-preview"],
        "territories": ["worldwide"],
        "disclosure_required": True,
        "revocable": True,
    }
    rights.setdefault("origin_license", "internal-original-design")
    rights.setdefault("allowed_uses", ["internal-preview"])
    if origin.get("type") == "authorized-real-person" and origin.get("consent_id"):
        rights["consent_id"] = origin["consent_id"]
    evaluation = source.get("evaluation") or {
        "sample_lines": [
            "This is a consistency check for the designed voice.",
            "Numbers must be clear: 2026, 45, and 3.5.",
        ],
        "criteria": ["identity stable across lines", "no metallic artefacts",
                     "words intelligible at normal playback"],
    }
    profile = {
        "profile_id": profile_id,
        "display_name": display.title()[:120],
        "umbrella": umbrella or source.get("umbrella") or "voice-creation-and-identity-design",
        "origin": {
            "type": origin.get("type", "synthetic-original"),
            "notes": origin.get("notes", "Generated by tools-voice-profile.py create."),
            **({"consent_id": origin["consent_id"]} if origin.get("consent_id") else {}),
        },
        "design": {
            "age_presentation": age,
            "gender_presentation": gender,
            "pitch": {"base_hz": base_pitch,
                      "range_st": RT.clamp(float(design.get("pitch", {}).get("range_st", 10.5)), 2.0, 24.0)},
            "resonance": design.get("resonance", "balanced"),
            "pace_wpm": round(pace, 1),
            "energy": round(energy, 3),
            "texture": design.get("texture", ["clean"]),
            "accent": design.get("accent", {"locale": "en-US", "strength": 0.35}),
            "personality": design.get("personality") or styles,
            "signature_habits": design.get("signature_habits") or [
                "slight downward cadence at the end of a statement",
                "short breath before emphasis",
            ],
        },
        "preferred_styles": styles,
        "forbidden_traits": source.get("forbidden_traits") or ["shouty", "raspy"],
        "evaluation": evaluation,
        "rights": rights,
        "provenance": {
            "created_at": RT.now_iso(),
            "created_by": "voice-capability-toolkit/tools-voice-profile.py",
            "model_version": f"formant-synthesizer-{RT.TOOLKIT_VERSION}",
            "asset_hash": RT.stable_fingerprint(
                {k: v for k, v in source.items() if k != "provenance"}, length=32),
        },
    }
    return profile


def render_settings(profile, presets):
    """Map a profile onto concrete synthesizer settings."""
    design = profile["design"]
    gender = presets["gender_presentation"][design.get("gender_presentation", "neutral")]
    age = presets["age_presentation"].get(design.get("age_presentation", "adult"),
                                          presets["age_presentation"]["adult"])
    resonance_scale = {"deep": 0.90, "chest": 0.95, "balanced": 1.0, "head": 1.06,
                       "bright": 1.10}
    return {
        "base_f0_hz": design["pitch"]["base_hz"],
        "formant_scale": round(gender["formant_mult"] * age["formant_mult"]
                               * resonance_scale.get(design.get("resonance", "balanced"), 1.0), 4),
        "breathiness": round(RT.clamp(gender["breathiness_delta"] + 0.18
                                      + age["jitter_delta"] * 2.0, 0.05, 0.6), 3),
        "brightness": round(RT.clamp(resonance_scale.get(design.get("resonance", "balanced"), 1.0)
                                     - 1.0, -0.2, 0.4), 3),
        "pace_wpm": design["pace_wpm"],
        "energy": design["energy"],
        "styles": profile.get("preferred_styles", []),
        "tremor": age.get("tremor", 0.0),
    }


def style_summary(styles, presets):
    rows = []
    for style in styles:
        spec = presets["style_attributes"].get(style)
        if not spec:
            rows.append({"style": style, "known": False})
            continue
        rows.append({"style": style, "known": True,
                     "pace_wpm_delta": spec.get("pace_wpm_delta", 0),
                     "pitch_st_delta": spec.get("pitch_st_delta", 0),
                     "energy_delta": spec.get("energy_delta", 0),
                     "tags": spec.get("tags", [])})
    return rows


# ---------------------------------------------------------------------------
# rights / consent
# ---------------------------------------------------------------------------

def _consent_problems(consent, request=None, *, policy=None, now=None):
    """Return a list of blocking problems for a consent record."""
    policy = policy or {}
    problems = []
    warnings = []
    _validate(consent, "consent_record", label="Consent record")
    signature = consent.get("signature", {})
    if not signature.get("value", "").strip():
        problems.append("Consent signature value is empty.")
    if signature.get("method") == "sha256-body":
        body = {k: v for k, v in consent.items() if k != "signature"}
        expected = RT.sha256_text(RT.canonical_json(body))
        actual = signature.get("value", "").strip().lower()
        if actual.startswith("sha256:") and actual[7:] != expected:
            problems.append("Consent signature hash does not match the record body.")
        elif not actual.startswith("sha256:") and actual not in {
                "example-signature-placeholder-not-a-secret"} and len(actual) != 64:
            warnings.append("Signature value is neither a sha256: hash nor a 64-char "
                            "digest; treated as an external reference.")
    expires = consent.get("expires_at")
    if expires:
        today = now or RT.now_iso()[:10]
        if str(expires) < str(today):
            problems.append(f"Consent expired on {expires} (today is {today}).")
    if consent.get("subject", {}).get("is_real_person") is not True:
        warnings.append("Subject is not marked as a real person; a consent record is "
                        "usually only required for recognizable real people.")
    if request:
        scope = consent.get("scope", {})
        for field, request_field in (("uses", "use"), ("projects", "project"),
                                     ("territories", "territory"), ("media", "medium")):
            allowed = [str(v).lower() for v in scope.get(field, [])]
            wanted = str(request.get(request_field, "")).lower()
            if not wanted:
                continue
            if wanted not in allowed and "worldwide" not in allowed and "*" not in allowed:
                problems.append(
                    f"Requested {request_field} '{wanted}' is outside the consent "
                    f"scope {field}: {scope.get(field, [])}.")
        if request.get("commercial") and not scope.get("commercial", False):
            problems.append("Requested commercial use is not granted by the consent record.")
        duration = request.get("duration")
        if duration and scope.get("duration") and duration != scope.get("duration"):
            warnings.append(f"Requested duration '{duration}' differs from the consent "
                            f"duration '{scope.get('duration')}'.")
    if consent.get("revocation", {}).get("revocable") is False:
        warnings.append("Consent is marked non-revocable; confirm this is lawful in the "
                        "relevant jurisdiction.")
    return problems, warnings


def _origin_policy(profile, policy):
    origin = profile.get("origin", {}).get("type", "synthetic-original")
    spec = policy.get("origin_policies", {}).get(origin)
    if spec is None:
        raise RT.ToolError(
            f"Origin type '{origin}' is not defined in asset-policy.json.",
            code=RT.EXIT_INVALID_INPUT,
            details={"allowed": sorted(policy.get("origin_policies", {}))},
        )
    return origin, spec


def evaluate_use(profile, request, *, consent=None, policy=None, assets_dir=None):
    """Return an allow/block decision for one requested use of a profile."""
    policy = policy or _policy(assets_dir)
    _validate(request, "usage_request", assets_dir=assets_dir, label="Usage request")
    origin, origin_spec = _origin_policy(profile, policy)
    problems, warnings = [], []
    domain = request.get("sensitive_domain", "none")
    domain_spec = policy.get("sensitive_domains", {}).get(domain, {"action": "review"})
    if domain_spec.get("action") == "block":
        problems.append(f"Blocked sensitive domain '{domain}': {domain_spec.get('reason')}")
    elif domain_spec.get("action") == "review":
        warnings.append(f"Domain '{domain}' requires review: {domain_spec.get('reason')}")

    if request.get("imitate_real_person"):
        target = request.get("target_person") or "an unnamed person"
        problems.append(
            f"Request imitates a real person ({target}). Only original or fully "
            "authorized voices are permitted; provide a consent record and set "
            "origin.type to authorized-real-person if you hold those rights.")
    if origin == "authorized-real-person" or origin_spec.get("requires_consent_record"):
        if consent is None:
            problems.append(
                "A consent record is required for this origin but none was supplied.")
        else:
            consent_problems, consent_warnings = _consent_problems(consent, request, policy=policy)
            problems.extend(consent_problems)
            warnings.extend(consent_warnings)
    if origin_spec.get("requires_license_id") and not (
            profile.get("rights", {}).get("origin_license") or "").strip():
        problems.append("A licence id is required for licensed-model voices.")

    rights = profile.get("rights", {})
    allowed_uses = [str(u).lower() for u in rights.get("allowed_uses", [])]
    if allowed_uses and "*" not in allowed_uses and request["use"].lower() not in allowed_uses:
        problems.append(
            f"Requested use '{request['use']}' is not in the profile's allowed uses "
            f"{rights.get('allowed_uses', [])}.")
    territories = [str(t).lower() for t in rights.get("territories", [])]
    worldwide = {"*", "worldwide", "global"} & set(territories)
    if territories and not worldwide and request["territory"].lower() not in territories:
        problems.append(
            f"Requested territory '{request['territory']}' is outside the profile "
            f"territories {rights.get('territories', [])}.")
    if request.get("commercial") and rights.get("allowed_uses") and \
            "commercial" not in allowed_uses and "*" not in allowed_uses:
        warnings.append("Commercial use requested; confirm the licence covers it.")

    disclosure = rights.get("disclosure_required",
                            policy.get("defaults", {}).get("disclosure_required", True))
    return {
        "decision": "block" if problems else ("review" if warnings and
                                              domain_spec.get("action") == "review" else "allow"),
        "origin": origin,
        "problems": problems,
        "warnings": warnings,
        "disclosure": {
            "required": bool(disclosure),
            "text": policy.get("defaults", {}).get("disclosure_text")
                    if disclosure else None,
        },
        "request": request,
        "profile_id": profile.get("profile_id"),
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_create(args):
    presets = _presets(args.assets_dir)
    deadline = RT.Deadline(args.timeout)
    document, kind = load_brief(args)
    if args.name:
        document = dict(document)
        document["display_name"] = args.name
        document["profile_id"] = RT.slugify(args.name)
    deadline.check("brief loading")
    profile = build_profile(document, presets, umbrella=args.umbrella, name=args.name)
    _validate(profile, "voice_profile", assets_dir=args.assets_dir, label="Voice profile")
    settings = render_settings(profile, presets)
    board = build_reference_board(profile, presets)
    result = {
        "status": "ok",
        "command": "create",
        "input_kind": kind,
        "profile": profile,
        "render_settings": settings,
        "styles": style_summary(profile.get("preferred_styles", []), presets),
        "reference_board": board,
        "next_commands": [
            f"python3 {PROG} plan --profile <profile.json> --text '<script>'",
            "python3 tools-audio-synthesis.py render-brief --input <brief.json> "
            "--profile <profile.json> --out-dir out",
        ],
    }
    if args.out_dir:
        RT.ensure_dir(args.out_dir)
        path = os.path.join(args.out_dir, f"asset-{profile['profile_id']}-profile.json")
        RT.write_json_file(path, profile, overwrite=args.force)
        result["profile_path"] = path
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def build_reference_board(profile, presets):
    design = profile.get("design", {})
    styles = profile.get("preferred_styles", [])
    targets = []
    for style in styles:
        spec = presets["style_attributes"].get(style, {})
        targets.extend(spec.get("tags", []))
    lines = profile.get("evaluation", {}).get("sample_lines", [])
    return {
        "profile_id": profile.get("profile_id"),
        "target_traits": sorted(set(targets)) or ["warm", "clear"],
        "forbidden_traits": profile.get("forbidden_traits", []),
        "measurable_targets": {
            "base_f0_hz": design.get("pitch", {}).get("base_hz"),
            "range_semitones": design.get("pitch", {}).get("range_st"),
            "pace_wpm": design.get("pace_wpm"),
            "energy": design.get("energy"),
        },
        "sample_lines": lines,
        "evaluation_criteria": profile.get("evaluation", {}).get("criteria", []),
        "markdown": reference_board_markdown(profile, targets, lines),
    }


def reference_board_markdown(profile, targets, lines):
    design = profile.get("design", {})
    rows = [
        f"# Voice reference board - {profile.get('display_name', profile.get('profile_id'))}",
        "",
        f"- **Profile id:** `{profile.get('profile_id')}`",
        f"- **Origin:** {profile.get('origin', {}).get('type')}",
        f"- **Pitch:** {design.get('pitch', {}).get('base_hz')} Hz "
        f"(+/- {design.get('pitch', {}).get('range_st')} semitones)",
        f"- **Pace:** {design.get('pace_wpm')} wpm",
        f"- **Resonance:** {design.get('resonance')}",
        f"- **Energy:** {design.get('energy')}",
        f"- **Styles:** {', '.join(profile.get('preferred_styles', []))}",
        "",
        "## Target traits",
        "",
        ", ".join(sorted(set(targets))) or "(none declared)",
        "",
        "## Forbidden traits",
        "",
        ", ".join(profile.get("forbidden_traits", [])) or "(none declared)",
        "",
        "## Sample lines",
        "",
    ]
    for index, line in enumerate(lines, start=1):
        rows.append(f"{index}. Eval line {index}: {line}")
    rows.append("")
    rows.append("## Evaluation criteria")
    rows.append("")
    for criterion in profile.get("evaluation", {}).get("criteria", []):
        rows.append(f"- [ ] {criterion}")
    rows.append("")
    return "\n".join(rows)


def cmd_validate(args):
    schemas = _schemas(args.assets_dir)
    document = RT.read_json_file(args.input)
    name = args.schema
    if not name:
        if "profile_id" in document and "design" in document:
            name = "voice_profile"
        elif "consent_id" in document:
            name = "consent_record"
        elif "segments" in document:
            name = "production_brief"
        elif "territory" in document:
            name = "usage_request"
        elif "files" in document:
            name = "delivery_manifest"
        elif "entries" in document:
            name = "pronunciation_dictionary"
        else:
            raise RT.ToolError(
                "Could not infer the schema for this document.",
                code=RT.EXIT_INVALID_INPUT,
                hint="Pass --schema with one of: " +
                     ", ".join(sorted(schemas.get("$defs", {}))),
            )
    problems = RT.validate_schema(document, RT.schema_for(schemas, name))
    extra = {}
    if name == "voice_profile" and not problems:
        extra["origin_policy"] = _origin_policy(document, _policy(args.assets_dir))[1]
    result = {"status": "ok" if not problems else "invalid", "command": "validate",
              "input": args.input, "schema": name, "valid": not problems,
              "problems": problems[:50], "problem_count": len(problems), **extra}
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK if not problems else RT.EXIT_INVALID_INPUT


def cmd_plan(args):
    presets = _presets(args.assets_dir)
    deadline = RT.Deadline(args.timeout)
    profile = RT.read_json_file(args.profile) if args.profile else build_profile(
        {"profile_id": "default-voice", "display_name": "Default Voice"}, presets)
    _validate(profile, "voice_profile", assets_dir=args.assets_dir, label="Voice profile")
    text, source = RT.read_text_input(args)
    segments = split_segments(text, max_words=args.max_words)
    settings = render_settings(profile, presets)
    plans = []
    cursor = 0.0
    for index, segment in enumerate(segments, start=1):
        deadline.check("prosody planning")
        plan = ENGINE.plan_segment(
            segment,
            pace_wpm=args.pace_wpm or settings["pace_wpm"],
            emotion=args.emotion or "neutral",
            intensity=args.intensity,
            attitude=args.attitude or "professional",
            styles=profile.get("preferred_styles", []),
            presets=presets,
            seed=args.seed + index,
        )
        plans.append({
            "segment_id": f"s{index:03d}",
            "text": segment,
            "start": round(cursor, 3),
            "duration": plan["planned_duration_seconds"],
            "words": plan["words"],
            "syllables": plan["syllables"],
            "effective_wpm": plan["effective_wpm"],
            "emotion": plan["emotion"],
            "attitude": plan["attitude"],
            "events": plan["events"],
        })
        cursor += plan["planned_duration_seconds"]
    result = {
        "status": "ok",
        "command": "plan",
        "profile_id": profile["profile_id"],
        "source": source,
        "render_settings": settings,
        "emotion": args.emotion,
        "attitude": args.attitude,
        "total_duration_seconds": round(cursor, 3),
        "segment_count": len(plans),
        "segments": plans,
        "disclosure": {
            "required": profile.get("rights", {}).get("disclosure_required", True),
            "text": _policy(args.assets_dir)["defaults"]["disclosure_text"],
        },
    }
    if args.output:
        RT.write_json_file(args.output, result, overwrite=args.force)
    RT.emit(result, output=None, quiet=args.quiet)
    return RT.EXIT_OK


def split_segments(text, *, max_words=60):
    """Split text into speakable segments on sentence boundaries."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise RT.ToolError("No script text supplied.", code=RT.EXIT_INVALID_INPUT)
    sentences = [s.strip() for s in re_split_sentences(cleaned) if s.strip()]
    segments = []
    for sentence in sentences:
        words = sentence.split()
        while len(words) > max_words:
            segments.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if words:
            segments.append(" ".join(words))
    return segments


def re_split_sentences(text):
    import re
    return re.split(r"(?<=[.!?])\s+|\n+", text)


def cmd_variants(args):
    presets = _presets(args.assets_dir)
    profile = RT.read_json_file(args.input)
    _validate(profile, "voice_profile", assets_dir=args.assets_dir, label="Voice profile")
    axis = args.axis
    if axis not in ("mood", "age", "language", "context", "energy"):
        raise RT.ToolError(
            f"Unknown --axis '{axis}'.",
            code=RT.EXIT_INVALID_INPUT,
            details={"allowed": ["mood", "age", "language", "context", "energy"]},
        )
    variants = []
    for index in range(args.count):
        variant = copy.deepcopy(profile)
        suffix = match_suffix(axis, index)
        variant["profile_id"] = f"{profile['profile_id']}-{suffix}"
        variant["display_name"] = f"{profile['display_name']} ({suffix.replace('-', ' ')})"
        variant["origin"] = dict(profile.get("origin", {}))
        variant["origin"]["notes"] = (
            f"Variant of {profile['profile_id']} on the {axis} axis.")
        design = variant["design"]
        if axis == "energy":
            design["energy"] = round(RT.clamp(design["energy"] + (index + 1) * 0.1, 0.05, 1.0), 3)
        elif axis == "mood":
            styles = list(presets["style_attributes"])
            design["personality"] = [styles[index % len(styles)]]
            variant["preferred_styles"] = design["personality"]
        elif axis == "age":
            ages = list(presets["age_presentation"])
            target = ages[min(index, len(ages) - 1)]
            design["age_presentation"] = target
            design["pitch"]["base_hz"] = round(RT.clamp(
                design["pitch"]["base_hz"] * presets["age_presentation"][target]["f0_mult"],
                55.0, 480.0), 2)
        elif axis == "language":
            locales = ["en-US", "en-GB", "es-ES", "fr-FR", "de-DE", "ja-JP", "fil-PH"]
            design.setdefault("accent", {})["locale"] = locales[index % len(locales)]
        elif axis == "context":
            contexts = ["narration", "conversational", "announcement", "tutorial", "promo"]
            design["signature_habits"] = [f"tuned for {contexts[index % len(contexts)]}"]
        variant["provenance"] = dict(profile.get("provenance", {}))
        variant["provenance"]["previous_version"] = profile["profile_id"]
        variant["provenance"]["created_at"] = RT.now_iso()
        variant["provenance"]["asset_hash"] = RT.stable_fingerprint(variant, length=32)
        _validate(variant, "voice_profile", assets_dir=args.assets_dir, label="Variant profile")
        variants.append({"profile": variant,
                         "render_settings": render_settings(variant, presets)})
    result = {"status": "ok", "command": "variants", "axis": axis,
              "base_profile": profile["profile_id"], "count": len(variants),
              "variants": variants}
    if args.out_dir:
        RT.ensure_dir(args.out_dir)
        written = []
        for item in variants:
            path = os.path.join(args.out_dir, f"asset-{item['profile']['profile_id']}-profile.json")
            RT.write_json_file(path, item["profile"], overwrite=args.force)
            written.append(path)
        result["files"] = written
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def match_suffix(axis, index):
    names = {
        "mood": ["warm", "bright", "calm", "dramatic", "playful"],
        "age": ["younger", "same", "older", "senior"],
        "language": ["en-us", "en-gb", "es-es", "fr-fr", "de-de", "ja-jp", "fil-ph"],
        "context": ["narration", "conversation", "announcement", "tutorial", "promo"],
        "energy": ["low", "medium", "high", "peak"],
    }[axis]
    return names[min(index, len(names) - 1)] + (f"-{index + 1}" if index >= len(names) else "")


def cmd_reference_board(args):
    presets = _presets(args.assets_dir)
    if args.input:
        profile = RT.read_json_file(args.input)
        _validate(profile, "voice_profile", assets_dir=args.assets_dir, label="Voice profile")
    else:
        text, _ = RT.read_text_input(args)
        profile = build_profile(parse_brief_text(text), presets,
                               name=args.name)
    board = build_reference_board(profile, presets)
    if args.output_md:
        RT.atomic_write_text(args.output_md, board["markdown"] + "\n")
    RT.emit({"status": "ok", "command": "reference-board", "board": board,
             "markdown_path": args.output_md},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_verify_consent(args):
    consent = RT.read_json_file(args.input)
    request = RT.read_json_file(args.request) if args.request else None
    policy = _policy(args.assets_dir)
    problems, warnings = _consent_problems(consent, request, policy=policy)
    result = {
        "status": "ok" if not problems else "blocked",
        "command": "verify-consent",
        "consent_id": consent.get("consent_id"),
        "verified": not problems,
        "problems": problems,
        "warnings": warnings,
        "scope_checked_against": request,
        "revocable": consent.get("revocation", {}).get("revocable"),
        "expires_at": consent.get("expires_at"),
        "required_disclosure": consent.get("disclosure", {}).get("form", "metadata"),
    }
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK if not problems else RT.EXIT_BLOCKED


def cmd_check_use(args):
    profile = RT.read_json_file(args.profile)
    _validate(profile, "voice_profile", assets_dir=args.assets_dir, label="Voice profile")
    request = RT.read_json_file(args.request)
    consent = RT.read_json_file(args.consent) if args.consent else None
    decision = evaluate_use(profile, request, consent=consent,
                            policy=_policy(args.assets_dir), assets_dir=args.assets_dir)
    status = {"allow": "ok", "review": "review", "block": "blocked"}[decision["decision"]]
    code = RT.EXIT_OK if decision["decision"] == "allow" else (
        RT.EXIT_INVALID_INPUT if decision["decision"] == "review" else RT.EXIT_BLOCKED)
    RT.emit({"status": status, "command": "check-use", **decision},
            output=args.output, quiet=args.quiet)
    return code


def cmd_enroll(args):
    consent = RT.read_json_file(args.input)
    problems, warnings = _consent_problems(consent, None, policy=_policy(args.assets_dir))
    if problems:
        raise RT.ToolError(
            "Consent record cannot be enrolled until these problems are fixed.",
            code=RT.EXIT_BLOCKED, details={"problems": problems})
    store = args.store or os.path.join(os.getcwd(), "consent-store")
    RT.ensure_dir(store)
    body = {k: v for k, v in consent.items() if k != "signature"}
    signature_hash = RT.sha256_text(RT.canonical_json(body))
    existing = sorted(glob.glob(os.path.join(store, "consent-*.json")))
    previous_hash = "genesis"
    if existing:
        try:
            previous = RT.read_json_file(existing[-1])
            previous_hash = previous.get("chain", {}).get("head_hash", "genesis")
        except RT.ToolError:
            previous_hash = "unreadable"
    head_hash = RT.sha256_text(previous_hash + signature_hash)
    record = {
        "consent_id": consent["consent_id"],
        "enrolled_at": RT.now_iso(),
        "subject": consent["subject"],
        "scope": consent["scope"],
        "expires_at": consent.get("expires_at"),
        "revocable": consent.get("revocation", {}).get("revocable"),
        "disclosure": consent.get("disclosure"),
        "body_hash": signature_hash,
        "chain": {"previous_hash": previous_hash, "head_hash": head_hash},
        "source": args.input,
        "warnings": warnings,
    }
    path = os.path.join(store, f"{consent['consent_id']}.json")
    if os.path.exists(path) and not args.force:
        raise RT.ToolError(
            f"Consent record '{consent['consent_id']}' is already enrolled.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Pass --force to append a new version with the same id.",
        )
    RT.write_json_file(path, record, overwrite=args.force)
    RT.emit({"status": "ok", "command": "enroll", "store": store, "path": path,
             "record": record}, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_revoke(args):
    profile = RT.read_json_file(args.input)
    _validate(profile, "voice_profile", assets_dir=args.assets_dir, label="Voice profile")
    dependent = []
    if args.assets_root:
        if not os.path.isdir(args.assets_root):
            raise RT.ToolError(f"--assets-root is not a directory: {args.assets_root}",
                               code=RT.EXIT_NOT_FOUND)
        profile_id = profile["profile_id"]
        for path in sorted(glob.glob(os.path.join(args.assets_root, "**", "*"),
                                     recursive=True)):
            if not os.path.isfile(path) or os.path.getsize(path) > 8 * 1024 * 1024:
                continue
            if os.path.splitext(path)[1].lower() not in (
                    ".json", ".md", ".txt", ".wav", ".mp3", ".flac", ".ogg"):
                continue
            try:
                with open(path, "rb") as handle:
                    blob = handle.read()
            except OSError:
                continue
            if profile_id.encode() in blob:
                dependent.append({"path": os.path.relpath(path, args.assets_root),
                                  "bytes": len(blob),
                                  "sha256": RT.sha256_bytes(blob)})
    RT.require_approval(args, "revoke",
                        detail=f"{len(dependent)} dependent asset(s) reference "
                               f"'{profile['profile_id']}'")
    record = {
        "profile_id": profile["profile_id"],
        "revoked_at": RT.now_iso(),
        "origin": profile.get("origin", {}),
        "dependents": dependent,
        "replacement": {
            "action": "re-render with an original or licensed profile",
            "command": "python3 tools-audio-synthesis.py render-brief "
                       "--input <brief.json> --profile <replacement-profile.json> "
                       "--out-dir out",
        },
    }
    if args.output:
        RT.write_json_file(args.output, record, overwrite=True)
    RT.emit({"status": "ok", "command": "revoke", "record": record,
             "output": args.output}, output=None, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_capabilities(args):
    RT.emit(RT.capabilities_report(
        PROG, CAPABILITIES,
        notes={"role": "voice identity, profile design, rights and consent enforcement",
               "assets": ["asset-voice-presets.json", "asset-policy.json",
                          "asset-schemas.json", "asset-fixtures.json"]}),
        output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = RT.build_parser(
        PROG,
        "Voice Capability Toolkit - create and govern voice profiles: design "
        "identity, plan performance, generate variants, enforce consent scope.",
        epilog="Examples:\n"
               "  python3 tools-voice-profile.py create --text 'warm 165hz en-US "
               "youthful narrator' --out-dir out\n"
               "  python3 tools-voice-profile.py check-use --profile out/asset-x-profile.json "
               "--request request.json --consent consent.json\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("create", help="Create a validated voice profile from a brief.")
    RT.add_input_args(p, text_help="Free-text voice brief.")
    p.add_argument("--brief", help="Brief JSON file (design/origin/rights fields).")
    p.add_argument("--name", help="Display name when no other input is given.")
    p.add_argument("--umbrella", help="Umbrella Skill Tool name recorded in the profile.")
    p.add_argument("--sample", action="store_true",
                   help="Create the documented default profile (no other input needed).")
    p.add_argument("--out-dir", help="Write asset-<id>-profile.json here.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("validate", help="Validate a profile, consent record, brief or request.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--schema", help="voice_profile, consent_record, production_brief, "
                                    "usage_request, delivery_manifest, pronunciation_dictionary")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("plan", help="Build a full performance plan for a script.")
    p.add_argument("--profile", help="Voice profile JSON (defaults to a neutral profile).")
    RT.add_input_args(p, text_help="Script text.")
    p.add_argument("--emotion", help="Emotion preset name (see asset-voice-presets.json).")
    p.add_argument("--attitude", help="Attitude preset name.")
    p.add_argument("--intensity", type=float, default=0.6)
    p.add_argument("--pace-wpm", type=float, help="Override the profile pace.")
    p.add_argument("--max-words", type=int, default=60,
                   help="Split segments longer than this (default 60).")
    p.add_argument("--seed", type=int, default=17)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("variants", help="Generate related variants of a profile.")
    p.add_argument("-i", "--input", required=True, help="Base profile JSON.")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--axis", default="mood",
                   help="mood, age, language, context or energy (default mood).")
    p.add_argument("--out-dir", help="Write variant profiles here.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_variants)

    p = sub.add_parser("reference-board", help="Build a structured voice reference board.")
    p.add_argument("-i", "--input", help="Profile JSON (or use --text).")
    p.add_argument("--text", help="Free-text voice description.")
    p.add_argument("--name", help="Display name for --text input.")
    p.add_argument("--output-md", help="Write the Markdown board here.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_reference_board)

    p = sub.add_parser("verify-consent", help="Verify a consent record for a requested use.")
    p.add_argument("-i", "--input", required=True, help="Consent record JSON.")
    p.add_argument("--request", help="Usage request JSON to check the scope against.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_verify_consent)

    p = sub.add_parser("check-use", help="Enforce origin, consent and scope for a use.")
    p.add_argument("--profile", required=True, help="Voice profile JSON.")
    p.add_argument("--request", required=True, help="Usage request JSON.")
    p.add_argument("--consent", help="Consent record JSON (required for real-person origin).")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_check_use)

    p = sub.add_parser("enroll", help="Enroll a consent record in a local store.")
    p.add_argument("-i", "--input", required=True, help="Consent record JSON.")
    p.add_argument("--store", help="Consent store directory (default ./consent-store).")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("revoke", help="Revoke a profile and list dependent assets.")
    p.add_argument("-i", "--input", required=True, help="Profile JSON to revoke.")
    p.add_argument("--assets-root", help="Directory to scan for dependent assets.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("capabilities", help="Print the machine-readable command index.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_capabilities)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    for attribute, default in (("assets_dir", None), ("force", False), ("approve", False),
                               ("timeout", 180.0), ("quiet", False)):
        if not hasattr(args, attribute):
            setattr(args, attribute, default)
    if getattr(args, "command", "") == "create" and getattr(args, "sample", False) \
            and not (getattr(args, "brief", None) or getattr(args, "text", None)
                     or getattr(args, "name", None)):
        args.text = "warm conversational narrator 165hz en-US adult"
        args.name = "Sample Voice"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(RT.guard(main)())
