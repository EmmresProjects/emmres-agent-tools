#!/usr/bin/env python3
"""Brief and work-package scaffolding for the voice-ai-skill-toolkit.

  * build_brief(...)   - render asset-brief-template.md for any of the 500
                         catalog skills with validated, substituted values
  * cmd_init_package   - scaffold a complete work-package folder:
                         work-package.json (manifest), brief.md,
                         normalized-text.txt, transcript.srt, and
                         consent-record.json when the tier is elevated.
                         A lexicon.txt is included for pronunciation-family
                         skills. Existing files are never overwritten unless
                         --force is passed, and even then only tool-managed
                         files are replaced.
"""

import importlib.util
import json
import os
import sys
import re
from datetime import date, datetime, timezone

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

ToolError = _common.ToolError


def resolve_brief_options(catalog, skill, args):
    profiles = _common.load_asset_json("asset-delivery-profiles.json")["profiles"]
    profile = None
    if getattr(args, "delivery_profile", None):
        for p in profiles:
            if p["id"] == args.delivery_profile:
                profile = p
                break
        if profile is None:
            raise ToolError(
                "Unknown delivery profile '%s'. Available: %s"
                % (args.delivery_profile, ", ".join(p["id"] for p in profiles))
            )
    language = getattr(args, "language", None) or "en"
    locale = getattr(args, "locale", None)
    language_display = "%s (%s)" % (language, locale) if locale else language
    preset_display = "none"
    if getattr(args, "preset", None):
        presets = _common.load_asset_json("asset-direction-presets.json")["presets"]
        match = next((p for p in presets if p["id"] == args.preset), None)
        if match is None:
            raise ToolError(
                "Unknown preset '%s'. Available: %s"
                % (args.preset, ", ".join(p["id"] for p in presets))
            )
        preset_display = "%s — %s (emotion: %s, pacing ~%d wpm)" % (
            match["id"], match["name"], match["emotion"], match["pacing_wpm"])
    sample_rate = getattr(args, "sample_rate", None)
    if sample_rate is None:
        sample_rate = profile["sample_rate"] if profile else 24000
    channels = getattr(args, "channels", None)
    if channels is None:
        channels = profile["channels"] if profile else 1
    objective = getattr(args, "objective", None) or (
        "Deliver %s work for the %s category: %s" % (skill["name"], skill["category_name"], skill["focus"])
    )
    tier = getattr(args, "tier_override", None) or skill["tier"]
    if tier not in ("standard", "review", "elevated"):
        raise ToolError("tier must be standard|review|elevated, got %r" % tier)
    speaker_auth = getattr(args, "speaker_auth", None) or "not yet documented — see Section 6"
    return {
        "language_display": language_display,
        "preset_display": preset_display,
        "delivery_profile_display": profile["id"] if profile else "not specified (choose one before delivery)",
        "sample_rate": sample_rate,
        "channels": channels,
        "objective": objective,
        "tier": tier,
        "speaker_auth": speaker_auth,
    }


def consent_gate_text(tier):
    if tier == "elevated":
        return (
            "**MANDATORY — elevated tier.** This work touches real voices, personal voice "
            "data, health, legal or evidentiary material, or biometric identity. A signed "
            "consent record (consent-record.json, validated by `consent-check`) and at least "
            "one human approval with decision `approved` are required before the package may "
            "be marked approved/delivered. `validate-package` fails otherwise. See "
            "asset-safety-policy.md Section 6."
        )
    if tier == "review":
        return (
            "**Human review strongly recommended.** This work involves identity-adjacent, "
            "diarization, accessibility, safety or vendor decisions. Route the package to a "
            "qualified reviewer and record an approval before delivery."
        )
    return (
        "Standard human review applies. Record at least one reviewer sign-off in the "
        "manifest `approvals` array before marking the package delivered."
    )


def build_brief(skill, args, today=None):
    opts = resolve_brief_options(_catalog.load_catalog(), skill, args)
    template = _common.load_asset_text("asset-brief-template.md")
    today = today or date.today().isoformat()
    values = {
        "{{SKILL_NAME}}": skill["name"],
        "{{SKILL_ID}}": skill["id"],
        "{{SKILL_NUMBER}}": skill["number"],
        "{{CATEGORY_NAME}}": skill["category_name"],
        "{{CATEGORY_ID}}": str(skill["category_id"]),
        "{{TIER}}": opts["tier"],
        "{{DATE}}": today,
        "{{REQUESTER}}": getattr(args, "requester", None) or "unspecified",
        "{{OBJECTIVE}}": opts["objective"],
        "{{LANGUAGE}}": opts["language_display"],
        "{{DELIVERY_PROFILE}}": opts["delivery_profile_display"],
        "{{SAMPLE_RATE}}": str(opts["sample_rate"]),
        "{{CHANNELS}}": str(opts["channels"]),
        "{{PRESET}}": opts["preset_display"],
        "{{SPEAKER_AUTH}}": opts["speaker_auth"],
        "{{DESCRIPTION}}": skill["description"],
        "{{FOCUS}}": skill["focus"],
        "{{CONSENT_GATE}}": consent_gate_text(opts["tier"]),
    }
    brief = template
    for token, value in values.items():
        brief = brief.replace(token, str(value))
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", brief)
    if leftover:
        raise ToolError("Brief template has unresolved tokens after substitution: %s" % sorted(set(leftover)), 1)
    return brief, opts


_NORMALIZED_TEXT = """# Normalized speech text for: {skill_name}
# One utterance per line. Replace this scaffold with the final normalized
# script before synthesis. Keep this file editable and diff-friendly.
# Example line items: expand abbreviations, verbalize numbers/dates,
# resolve symbols, apply lexicon pronunciations.
"""

_TRANSCRIPT_SRT = """1
00:00:00,000 --> 00:00:02,500
Replace this with the final narration transcript.

"""

MANAGED_FILES = ["work-package.json", "brief.md", "normalized-text.txt", "transcript.srt",
                 "consent-record.json", "lexicon.txt"]


def init_package(args):
    skill, candidates = _catalog.resolve_skill(args.skill)
    if skill is None:
        hint = ""
        if candidates:
            hint = " Ambiguous — matches: " + ", ".join(
                "%s (%s)" % (c["name"], c["number"]) for c in candidates[:8])
        raise ToolError("Unknown skill '%s'.%s" % (args.skill, hint))

    target = args.dir
    if os.path.exists(target):
        if not os.path.isdir(target):
            raise ToolError("--dir exists and is not a directory: %s" % target)
        existing = os.listdir(target)
        if existing and not args.force:
            raise ToolError(
                "Directory %s is not empty. Re-run with --force to let the tool "
                "overwrite its own managed files (%s). Other files are never touched."
                % (target, ", ".join(MANAGED_FILES)))
    else:
        os.makedirs(target)

    today = date.today().isoformat()
    opts = resolve_brief_options(_catalog.load_catalog(), skill, args)
    brief_text, _ = build_brief(skill, args, today=today)

    raw_notes = (getattr(args, "objective", None) or skill["focus"]).strip()
    package_id = getattr(args, "package_id", None) or re.sub(
        r"[^a-z0-9]+", "-", ("%s-%s" % (skill["id"], today)).lower()).strip("-")
    package_id = package_id[:64].rstrip("-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", package_id):
        raise ToolError("Derived package_id '%s' is invalid; pass --package-id matching ^[a-z0-9][a-z0-9-]{2,63}$" % package_id)

    manifest = {
        "package_id": package_id,
        "skill_id": skill["id"],
        "title": (getattr(args, "title", None) or skill["name"])[:200],
        "status": "draft",
        "language": getattr(args, "language", None) or "en",
        "locale": getattr(args, "locale", None) or "",
        "created_by": getattr(args, "requester", None) or "unspecified",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consent_required": opts["tier"] == "elevated",
        "tier": opts["tier"],
        "sample_rate": int(opts["sample_rate"]),
        "channels": int(opts["channels"]),
        "delivery_profile": getattr(args, "delivery_profile", None) or "",
        "speaker_authorization": getattr(args, "speaker_auth", None) or "",
        "inputs": [],
        "outputs": [],
        "approvals": [],
        "notes": raw_notes[:4000],
    }

    tier = opts["tier"]
    files = {
        "work-package.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        "brief.md": brief_text,
        "normalized-text.txt": _NORMALIZED_TEXT.format(skill_name=skill["name"]),
        "transcript.srt": _TRANSCRIPT_SRT,
    }
    if tier == "elevated":
        files["consent-record.json"] = _common.load_asset_text("asset-consent-record-template.json")
    focus_l = (skill["focus"] + " " + skill["name"]).lower()
    if any(k in focus_l for k in ("pronunciation", "lexicon", "phoneme", "grapheme")):
        files["lexicon.txt"] = _common.load_asset_text("asset-sample-lexicon.txt")

    written, skipped = [], []
    for name, content in files.items():
        path = os.path.join(target, name)
        if os.path.exists(path):
            if args.force:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                written.append(name)
            else:
                skipped.append(name)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(name)

    return {
        "ok": True,
        "package_dir": target,
        "package_id": package_id,
        "skill": {"id": skill["id"], "number": skill["number"], "name": skill["name"]},
        "tier": tier,
        "consent_required": consent_required_flag(tier),
        "files_written": written,
        "files_skipped": skipped,
        "next_steps": [
            "Fill normalized-text.txt with the final script.",
            "Record or synthesize the audio; add outputs to work-package.json.",
            "Replace the sample cue in transcript.srt with the real transcript.",
            ("Complete consent-record.json and run `consent-check`." if tier == "elevated"
             else "Record reviewer approval in the manifest approvals array."),
            "Run `validate-package %s` then `qa-report %s`." % (target, target),
        ],
    }


def consent_required_flag(tier):
    return tier == "elevated"
