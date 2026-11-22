#!/usr/bin/env python3
"""Voice Capability Toolkit - speech synthesis and script performance tool.

Turns scripts/briefs into performed audio with controllable pacing, emphasis,
pauses, emotion, attitude, pronunciation and rendering effects.

Commands
--------
    capabilities      Machine-readable command index
    render            Render text or a brief to audio (single file or batch)
    render-brief      Render a production brief (segments, speakers, dictionaries)
    plan-prosody      Show the prosody plan without rendering audio
    sanitize-text     Normalize numbers, dates, currency, URLs and acronyms
    apply-dictionary  Apply a pronunciation dictionary to text
    validate-dictionary  Validate a pronunciation dictionary
    build-dictionary  Build a dictionary skeleton from text (risky terms)
    providers         List rendering providers and their real capabilities

The built-in provider is a deterministic formant synthesizer: intelligible,
controllable prosody and identity, not studio neural TTS. That limitation is
declared in `providers` output and in knowledge-readme.md.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
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
PROG = "tools-audio-synthesis.py"

CAPABILITIES = [
    {"command": "capabilities", "summary": "Print this command index.", "inputs": [], "outputs": ["JSON"]},
    {"command": "render", "summary": "Render text to speech with emotion, attitude, style, pace, pauses, effects and loudness control.", "inputs": ["--text", "--input"], "outputs": ["audio", "sidecar JSON"]},
    {"command": "render-brief", "summary": "Render a production brief (segments, speakers, per-segment emotion, pronunciation preset, target duration) to a stitched track and manifest.", "inputs": ["--input brief.json"], "outputs": ["audio", "manifest", "per-segment audio"]},
    {"command": "plan-prosody", "summary": "Return the prosody plan (syllables, pitch targets, pauses, duration) without rendering.", "inputs": ["--text", "--input"], "outputs": ["prosody plan JSON"]},
    {"command": "sanitize-text", "summary": "Normalize numbers, dates, currency, measurements, acronyms, URLs and phone numbers for speaking.", "inputs": ["--text", "--input"], "outputs": ["normalized text"]},
    {"command": "apply-dictionary", "summary": "Apply a pronunciation dictionary (say/ipa/stress) to text with unused-entry reporting.", "inputs": ["--input dict.json", "--text"], "outputs": ["rewritten text", "report"]},
    {"command": "validate-dictionary", "summary": "Validate a pronunciation dictionary and cross-check terms found in a script.", "inputs": ["--input dict.json"], "outputs": ["validation report"]},
    {"command": "build-dictionary", "summary": "Detect risky terms (names, brands, acronyms, foreign words, homographs) and build a dictionary skeleton.", "inputs": ["--text", "--input"], "outputs": ["dictionary skeleton"]},
    {"command": "providers", "summary": "List rendering providers, their real capabilities and limitations.", "inputs": [], "outputs": ["JSON"]},
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _presets(assets_dir=None):
    return RT.load_asset_json("asset-voice-presets.json", assets_dir)[0]


def _schemas(assets_dir=None):
    return RT.load_asset_json("asset-schemas.json", assets_dir)[0]


def _policy(assets_dir=None):
    return RT.load_asset_json("asset-policy.json", assets_dir)[0]


def _profile_or_default(path, presets):
    if path:
        profile = RT.read_json_file(path)
        problems = RT.validate_schema(
            profile, RT.schema_for(_schemas(path and None or None), "voice_profile")) \
            if False else []
        return profile
    return {
        "profile_id": "default-voice",
        "display_name": "Default Voice",
        "origin": {"type": "synthetic-original"},
        "design": {
            "age_presentation": "adult",
            "gender_presentation": "neutral",
            "pitch": {"base_hz": 150.0, "range_st": 10.5},
            "resonance": "balanced",
            "pace_wpm": 155,
            "energy": 0.55,
            "accent": {"locale": "en-US", "strength": 0.3},
        },
        "preferred_styles": ["warm", "conversational"],
        "rights": {"origin_license": "internal-original-design",
                   "allowed_uses": ["*"], "territories": ["worldwide"],
                   "disclosure_required": True},
    }


def render_settings_from_profile(profile, presets):
    design = profile.get("design", {})
    gender = presets["gender_presentation"].get(
        design.get("gender_presentation", "neutral"), {"f0_mult": 1.0, "formant_mult": 1.0,
                                                      "breathiness_delta": 0.0})
    age = presets["age_presentation"].get(design.get("age_presentation", "adult"),
                                          {"f0_mult": 1.0, "formant_mult": 1.0,
                                           "jitter_delta": 0.0, "tremor": 0.0})
    resonance_scale = {"deep": 0.90, "chest": 0.95, "balanced": 1.0, "head": 1.06,
                       "bright": 1.10}.get(design.get("resonance", "balanced"), 1.0)
    return {
        "base_f0_hz": float(design.get("pitch", {}).get("base_hz", 150.0)),
        "formant_scale": round(gender.get("formant_mult", 1.0) * age.get("formant_mult", 1.0)
                               * resonance_scale, 4),
        "breathiness": round(RT.clamp(0.18 + gender.get("breathiness_delta", 0.0)
                                      + age.get("jitter_delta", 0.0) * 2.0, 0.05, 0.6), 3),
        "brightness": round(RT.clamp(resonance_scale - 1.0, -0.2, 0.4), 3),
        "pace_wpm": float(design.get("pace_wpm", 155.0)),
        "energy": float(design.get("energy", 0.55)),
        "styles": profile.get("preferred_styles", []),
        "tremor": age.get("tremor", 0.0),
    }


# ---------------------------------------------------------------------------
# text normalization
# ---------------------------------------------------------------------------

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

_CURRENCY = {"$": "dollars", "£": "pounds", "€": "euros", "₱": "pesos",
             "¥": "yen", "₹": "rupees"}

_UNITS = {"kg": "kilograms", "g": "grams", "km": "kilometers", "m": "meters",
          "cm": "centimeters", "mm": "millimeters", "ms": "milliseconds",
          "s": "seconds", "min": "minutes", "h": "hours", "hr": "hours",
          "°c": "degrees Celsius", "°f": "degrees Fahrenheit", "%": "percent",
          "m/s": "meters per second", "gb": "gigabytes", "mb": "megabytes",
          "kb": "kilobytes", "hz": "hertz", "khz": "kilohertz", "mhz": "megahertz",
          "l": "liters", "ml": "milliliters", "lb": "pounds", "oz": "ounces"}

_ACRONYM_OVERRIDES = {
    "NASA": "Nasa", "JPL": "J P L", "API": "A P I", "URL": "U R L",
    "US": "U S", "UK": "U K", "AI": "A I", "TTS": "T T S", "SSML": "S S M L",
    "LUFS": "L U F S", "PDF": "P D F", "CSV": "C S V", "JSON": "Jason",
    "HTML": "H T M L", "IP": "I P", "ID": "I D", "OK": "okay",
    "IVR": "I V R", "AAC": "A A C", "NPC": "N P C",
}


def spell_number(text):
    return ENGINE.spell_number(text)


def sanitize_text(text, *, locale="en-US", money=True, dates=True, units=True,
                  acronyms=True, urls=True, phones=True):
    """Normalize symbols and formats into speakable English."""
    report = {"replacements": []}

    def record(kind, before, after):
        report["replacements"].append({"kind": kind, "before": before, "after": after})

    out = text
    if urls:
        def _url(match):
            value = match.group(0)
            spoken = re.sub(r"^https?://", "", value)
            spoken = spoken.replace("www.", "")
            spoken = re.sub(r"[/?&=#._-]+", " ", spoken).strip()
            record("url", value, spoken)
            return spoken
        out = re.sub(r"https?://[^\s]+", _url, out)

    if dates:
        def _iso(match):
            year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12:
                spoken = f"{_MONTHS[month - 1]} {day}, {year}"
                record("date", match.group(0), spoken)
                return spoken
            return match.group(0)
        out = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _iso, out)

        def _slash(match):
            first, second, year = match.group(1), match.group(2), match.group(3)
            month, day = int(first), int(second)
            if 1 <= month <= 12 and 1 <= day <= 31:
                spoken = f"{_MONTHS[month - 1]} {day}, {year}"
                record("date", match.group(0), spoken)
                return spoken
            return match.group(0)
        out = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", _slash, out)

    if money:
        def _clean_amount(amount):
            value = amount.replace(",", "")
            if re.fullmatch(r"\d+\.\d*0+", value):
                value = value.rstrip("0").rstrip(".")
            elif re.fullmatch(r"\d+\.0", value):
                value = value[:-2]
            return value

        def _money(match):
            symbol, amount = match.group(1), match.group(2)
            spoken = f"{spell_number(_clean_amount(amount))} " \
                     f"{_CURRENCY.get(symbol, '')}".strip()
            record("money", match.group(0), spoken)
            return spoken
        out = re.sub(r"([$£€₱¥₹])\s?(\d[\d,]*(?:\.\d+)?)", _money, out)
        codes = {"usd": "dollars", "php": "pesos", "eur": "euros", "gbp": "pounds",
                 "jpy": "yen", "inr": "rupees"}
        def _code(match):
            code = match.group(1).lower()
            spoken = f"{spell_number(_clean_amount(match.group(2)))} {codes[code]}"
            record("money", match.group(0), spoken)
            return spoken
        out = re.sub(r"\b(USD|usd|PHP|php|EUR|eur|GBP|gbp|JPY|jpy|INR|inr)\s?"
                     r"(\d[\d,]*(?:\.\d+)?)", _code, out)
        out = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s?(USD|usd|PHP|php|EUR|eur|GBP|gbp)\b",
                     lambda m: f"{spell_number(_clean_amount(m.group(1)))} "
                               f"{codes[m.group(2).lower()]}", out)

    if units:
        def _unit(match):
            amount, unit = match.group(1), match.group(2).lower()
            spoken_unit = _UNITS.get(unit)
            if not spoken_unit:
                return match.group(0)
            spoken = f"{spell_number(amount)} {spoken_unit}"
            record("unit", match.group(0), spoken)
            return spoken
        out = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s?(kg|km|cm|mm|ms|min|hr|h|m/s|m|g|s|l|ml|lb|oz|gb|mb|kb|hz|khz|mhz|°c|°f)\b",
                     _unit, out, flags=re.I)
        out = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s?%", lambda m: f"{spell_number(m.group(1))} percent", out)

    if phones:
        def _phone(match):
            digits = re.sub(r"\D", "", match.group(0))
            if len(digits) < 7:
                return match.group(0)
            spoken = " ".join(ENGINE.spell_number(d) for d in digits)
            record("phone", match.group(0), spoken)
            return spoken
        out = re.sub(r"\+?\d[\d\s().-]{6,}\d", _phone, out)

    if acronyms:
        def _acronym(match):
            word = match.group(0)
            if word in _ACRONYM_OVERRIDES:
                spoken = _ACRONYM_OVERRIDES[word]
                record("acronym", word, spoken)
                return spoken
            if len(word) <= 5 and not re.search(r"[aeiouAEIOU]", word):
                spoken = " ".join(word.upper())
                record("acronym", word, spoken)
                return spoken
            return word
        out = re.sub(r"\b[A-Z]{2,6}\b", _acronym, out)

    # Standalone integers and decimals not already handled.
    def _number(match):
        value = match.group(0)
        if re.fullmatch(r"\d{4}", value) and 1900 <= int(value) <= 2100:
            spoken = ENGINE.spell_number(value)
        else:
            spoken = ENGINE.spell_number(value)
        record("number", value, spoken)
        return spoken
    out = re.sub(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])", _number, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    report["text"] = out.strip()
    report["changed"] = out.strip() != text.strip()
    return report


def load_dictionary(path):
    document = RT.read_json_file(path)
    problems = RT.validate_schema(
        document, RT.schema_for(_schemas(), "pronunciation_dictionary"))
    if problems:
        raise RT.ToolError(
            "Pronunciation dictionary failed validation.",
            code=RT.EXIT_INVALID_INPUT,
            details={"problems": problems[:25]},
            hint="Each entry needs 'term' and 'say'; the dictionary needs 'name' "
                 "and 'entries'.",
        )
    return document


def apply_dictionary(text, dictionary, *, locale=None):
    """Replace dictionary terms in text and report which entries were used."""
    used = set()
    out = text
    entries = sorted(dictionary.get("entries", []),
                     key=lambda e: len(e["term"]), reverse=True)
    for entry in entries:
        language = entry.get("language") or dictionary.get("language")
        if locale and language and not str(language).lower().startswith(locale.split("-")[0].lower()):
            continue
        term = entry["term"]
        flags = 0 if entry.get("case_sensitive") else re.IGNORECASE
        pattern = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", flags)
        if pattern.search(out):
            used.add(term)
            out = pattern.sub(entry["say"], out)
    unused = [e["term"] for e in dictionary.get("entries", []) if e["term"] not in used]
    return {"text": out, "used": sorted(used), "unused": unused,
            "dictionary": dictionary.get("name"),
            "entry_count": len(dictionary.get("entries", []))}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _render_one(text, *, settings, presets, args, target_duration=None):
    plan = ENGINE.plan_segment(
        text,
        pace_wpm=args.pace_wpm or settings["pace_wpm"],
        emotion=args.emotion or "neutral",
        intensity=args.intensity,
        attitude=args.attitude or "professional",
        styles=settings["styles"] if not args.no_styles else [],
        presets=presets,
        seed=args.seed,
        target_duration=target_duration,
    )
    audio = ENGINE.render_plan(
        plan, args.sample_rate,
        formant_scale=settings["formant_scale"],
        breathiness=settings["breathiness"],
        brightness=settings["brightness"],
        base_f0=settings["base_f0_hz"],
        seed=args.seed,
    )
    if args.breaths:
        audio = ENGINE.add_breaths(audio, plan, args.sample_rate)
    if args.effect and args.effect != "clean":
        effect_spec = presets.get("effects", {}).get(args.effect)
        if effect_spec is None:
            raise RT.ToolError(
                f"Unknown effect '{args.effect}'.",
                code=RT.EXIT_INVALID_INPUT,
                details={"allowed": sorted(presets.get("effects", {}))},
            )
        audio = ENGINE.apply_effect_rack(audio, args.sample_rate, effect_spec)
    if args.highpass_hz or args.lowpass_hz:
        audio = ENGINE.apply_eq(audio[:, None] if audio.ndim == 1 else audio,
                                args.sample_rate, highpass_hz=args.highpass_hz,
                                lowpass_hz=args.lowpass_hz)[:, 0]
    audio = ENGINE.trim_silence(audio, args.sample_rate, threshold_dbfs=-50.0,
                                lead=args.lead_in, tail=args.tail_time)
    if args.rate_mult and abs(args.rate_mult - 1.0) > 1e-6:
        audio = ENGINE.resample(audio, args.sample_rate,
                                int(args.sample_rate * RT.clamp(args.rate_mult, 0.4, 2.5)))
    return plan, audio


def _measure(audio, samplerate):
    return {
        "duration_seconds": round(audio.shape[0] / samplerate, 4),
        "peak_dbfs": round(ENGINE.peak_dbfs(audio), 2),
        "true_peak_dbtp": round(ENGINE.true_peak_dbtp(audio), 2),
        "rms_dbfs": round(ENGINE.rms_dbfs(audio), 2),
        "integrated_lufs": round(ENGINE.integrated_loudness(audio, samplerate), 2),
        "noise_floor_dbfs": round(ENGINE.noise_floor_dbfs(audio, samplerate), 2),
        "pitch_median_hz": ENGINE.estimate_pitch(audio, samplerate).get("median_hz"),
    }


def cmd_render(args):
    presets = _presets(args.assets_dir)
    deadline = RT.Deadline(args.timeout)
    if args.provider not in ("builtin-formant", "formant"):
        raise RT.ToolError(
            f"Provider '{args.provider}' is not available in this build.",
            code=RT.EXIT_UNAVAILABLE,
            hint="Use --provider builtin-formant. External neural engines can be "
                 "added by implementing a provider adapter and registering it in "
                 "the 'providers' command.",
            details={"available": ["builtin-formant"]},
        )
    profile = _profile_or_default(args.profile, presets)
    settings = render_settings_from_profile(profile, presets)
    text, source = RT.read_text_input(args)
    dictionary_report = None
    if args.dictionary:
        dictionary = load_dictionary(args.dictionary)
        dictionary_report = apply_dictionary(text, dictionary)
        text = dictionary_report["text"]
    if args.sanitize:
        sanitized = sanitize_text(text)
        text = sanitized["text"]
    else:
        sanitized = None
    deadline.check("render")
    plan, audio = _render_one(text, settings=settings, presets=presets, args=args)
    measurements = _measure(audio, args.sample_rate)
    if args.target_lufs is not None:
        audio, loudness_report = ENGINE.normalize_loudness(
            audio, args.sample_rate, target_lufs=args.target_lufs,
            limit_ceiling=args.true_peak_dbtp)
        measurements = _measure(audio, args.sample_rate)
        measurements["loudness_normalization"] = loudness_report

    fingerprint = RT.stable_fingerprint({
        "text": text,
        "profile": profile.get("profile_id"),
        "settings": settings,
        "emotion": plan["emotion"],
        "attitude": plan["attitude"],
        "intensity": plan["intensity"],
        "pace": plan["effective_wpm"],
        "seed": args.seed,
        "effect": args.effect,
    }, length=10)
    output_path = args.output_audio or os.path.join(
        args.out_dir or "out",
        f"{RT.slugify(profile['profile_id'])}-{fingerprint}.{args.format}")
    if not args.force and os.path.exists(output_path):
        raise RT.ToolError(
            f"Refusing to overwrite {output_path}",
            code=RT.EXIT_INVALID_INPUT, hint="Pass --force to overwrite.")
    ENGINE.write_audio(output_path, audio, args.sample_rate, fmt=args.format,
                       subtype=args.subtype, overwrite=True)
    sidecar = os.path.splitext(output_path)[0] + ".json"
    policy = _policy(args.assets_dir)
    disclosure_required = profile.get("rights", {}).get("disclosure_required", True)
    metadata = {
        "status": "ok",
        "command": "render",
        "provider": "builtin-formant",
        "source": source,
        "text": text,
        "profile_id": profile.get("profile_id"),
        "render_settings": settings,
        "direction": {"emotion": plan["emotion"], "attitude": plan["attitude"],
                      "intensity": plan["intensity"], "styles": plan["styles"],
                      "effective_wpm": plan["effective_wpm"],
                      "syllables": plan["syllables"]},
        "prosody_event_count": len(plan["events"]),
        "audio": {"path": output_path, "sample_rate": args.sample_rate,
                  "format": args.format, "metrics": measurements,
                  "sha256": RT.sha256_file(output_path)},
        "dictionary": dictionary_report,
        "sanitized": {"changed": sanitized["changed"],
                      "replacements": sanitized["replacements"][:40]} if sanitized else None,
        "disclosure": {
            "required": bool(disclosure_required),
            "text": policy["defaults"]["disclosure_text"] if disclosure_required else None,
        },
        "provenance": {
            "created_at": RT.now_iso(),
            "tool": PROG,
            "toolkit_version": RT.TOOLKIT_VERSION,
            "seed": args.seed,
        },
        "sidecar": sidecar,
    }
    if args.write_sidecar:
        RT.write_json_file(sidecar, metadata, overwrite=True)
    RT.emit(metadata, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_render_brief(args):
    presets = _presets(args.assets_dir)
    deadline = RT.Deadline(args.timeout)
    brief = RT.read_json_file(args.input)
    if isinstance(brief, dict) and "brief_example" in brief:
        brief = brief["brief_example"]
    RT.assert_valid(brief, RT.schema_for(_schemas(args.assets_dir), "production_brief"),
                    label="Production brief")
    if len(brief["segments"]) > presets["synthesis"]["max_segments_per_run"]:
        raise RT.ToolError(
            f"Brief has {len(brief['segments'])} segments, above the "
            f"{presets['synthesis']['max_segments_per_run']} per-run limit.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Split the brief or run in batches.")
    profile = _profile_or_default(args.profile, presets)
    settings = render_settings_from_profile(profile, presets)
    dictionary = None
    if args.dictionary:
        dictionary = load_dictionary(args.dictionary)
    preset_name = (brief.get("direction", {}) or {}).get("pronunciation_preset")
    if preset_name and dictionary is None and args.dictionary_library:
        candidate = os.path.join(args.dictionary_library, f"{preset_name}.json")
        if os.path.isfile(candidate):
            dictionary = load_dictionary(candidate)

    out_dir = args.out_dir or "out"
    RT.ensure_dir(out_dir)
    segments_audio = []
    segment_reports = []
    cursor = 0.0
    for index, segment in enumerate(brief["segments"], start=1):
        deadline.check(f"segment {index}")
        text = segment["text"]
        if len(text.split()) > presets["synthesis"]["max_words_per_segment"]:
            raise RT.ToolError(
                f"Segment '{segment['id']}' has {len(text.split())} words, above the "
                f"{presets['synthesis']['max_words_per_segment']} word limit.",
                code=RT.EXIT_INVALID_INPUT,
                hint="Split long segments so prosody stays predictable.")
        dictionary_report = None
        if dictionary:
            dictionary_report = apply_dictionary(text, dictionary)
            text = dictionary_report["text"]
        sanitize_report = sanitize_text(text) if args.sanitize else None
        if sanitize_report:
            text = sanitize_report["text"]
        direction = brief.get("direction", {})
        local_args = argparse.Namespace(**vars(args))
        local_args.emotion = segment.get("emotion") or direction.get("emotion") or args.emotion
        local_args.intensity = segment.get("intensity", direction.get("intensity", args.intensity))
        local_args.attitude = direction.get("attitude", args.attitude)
        local_args.pace_wpm = direction.get("pace_wpm", args.pace_wpm)
        local_args.no_styles = getattr(args, "no_styles", False)
        target = None
        if args.fit_target_duration and brief.get("target_duration_seconds"):
            target = brief["target_duration_seconds"] / len(brief["segments"])
        plan, audio = _render_one(text, settings=settings, presets=presets,
                                  args=local_args, target_duration=target)
        segments_audio.append(audio)
        metrics = _measure(audio, args.sample_rate)
        segment_reports.append({
            "id": segment["id"],
            "speaker": segment.get("speaker", "default"),
            "text": text,
            "emotion": plan["emotion"],
            "intensity": plan["intensity"],
            "syllables": plan["syllables"],
            "start": round(cursor, 3),
            "duration": metrics["duration_seconds"],
            "metrics": metrics,
            "dictionary": dictionary_report,
            "sanitized_changes": len(sanitize_report["replacements"]) if sanitize_report else 0,
        })
        cursor += metrics["duration_seconds"]
        if args.per_segment_files:
            segment_path = os.path.join(out_dir, f"{RT.slugify(brief['project'])}-"
                                                 f"{RT.slugify(segment['id'])}.{args.format}")
            ENGINE.write_audio(segment_path, audio, args.sample_rate, fmt=args.format,
                               subtype=args.subtype, overwrite=True)
            segment_reports[-1]["path"] = segment_path

    combined = ENGINE.concatenate(segments_audio, gap_seconds=args.segment_gap,
                                  samplerate=args.sample_rate)
    if args.target_lufs is not None:
        combined, loudness_report = ENGINE.normalize_loudness(
            combined, args.sample_rate, target_lufs=args.target_lufs,
            limit_ceiling=args.true_peak_dbtp)
    else:
        loudness_report = None
    master_path = os.path.join(out_dir, f"{RT.slugify(brief['project'])}-master.{args.format}")
    if os.path.exists(master_path) and not args.force:
        raise RT.ToolError(f"Refusing to overwrite {master_path}",
                           code=RT.EXIT_INVALID_INPUT, hint="Pass --force.")
    ENGINE.write_audio(master_path, combined, args.sample_rate, fmt=args.format,
                       subtype=args.subtype, overwrite=True)
    measurements = _measure(combined, args.sample_rate)
    policy = _policy(args.assets_dir)
    disclosure_required = profile.get("rights", {}).get("disclosure_required", True)
    manifest = {
        "project": brief["project"],
        "language": brief["language"],
        "generated_at": RT.now_iso(),
        "voice_profile": profile.get("profile_id"),
        "target_lufs": args.target_lufs,
        "files": [{
            "path": os.path.basename(master_path),
            "sha256": RT.sha256_file(master_path),
            "bytes": os.path.getsize(master_path),
            "sample_rate": args.sample_rate,
            "channels": 1,
            "duration_seconds": measurements["duration_seconds"],
            "format": args.format,
            "integrated_lufs": measurements["integrated_lufs"],
            "true_peak_dbtp": measurements["true_peak_dbtp"],
            "disclosure": policy["defaults"]["disclosure_text"] if disclosure_required else "not-required",
        }],
        "rights": {
            "license": profile.get("rights", {}).get("origin_license"),
            "disclosure": "required" if disclosure_required else "not-required",
            "consent_id": profile.get("rights", {}).get("consent_id"),
        },
    }
    manifest_path = os.path.join(out_dir, f"{RT.slugify(brief['project'])}-manifest.json")
    RT.write_json_file(manifest_path, manifest)
    result = {
        "status": "ok",
        "command": "render-brief",
        "project": brief["project"],
        "language": brief["language"],
        "profile_id": profile.get("profile_id"),
        "master": master_path,
        "manifest": manifest_path,
        "segments": segment_reports,
        "total_duration_seconds": measurements["duration_seconds"],
        "target_duration_seconds": brief.get("target_duration_seconds"),
        "duration_delta_seconds": round(
            measurements["duration_seconds"] - brief["target_duration_seconds"], 3)
        if brief.get("target_duration_seconds") else None,
        "metrics": measurements,
        "loudness_normalization": loudness_report,
        "provider": "builtin-formant",
    }
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_plan_prosody(args):
    presets = _presets(args.assets_dir)
    text, source = RT.read_text_input(args)
    plan = ENGINE.plan_segment(
        text, pace_wpm=args.pace_wpm, emotion=args.emotion,
        intensity=args.intensity, attitude=args.attitude,
        styles=args.styles or [], presets=presets, seed=args.seed,
        target_duration=args.target_duration)
    result = {"status": "ok", "command": "plan-prosody", "source": source, **plan,
              "syllable_rate_per_second": round(
                  plan["syllables"] / max(plan["planned_duration_seconds"], 1e-6), 2)}
    RT.emit(result, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_sanitize_text(args):
    text, source = RT.read_text_input(args)
    report = sanitize_text(text, locale=args.locale,
                           acronyms=not args.no_acronyms,
                           dates=not args.no_dates,
                           units=not args.no_units,
                           money=not args.no_money)
    report.update({"status": "ok", "command": "sanitize-text", "source": source,
                   "replacement_count": len(report["replacements"])})
    RT.emit(report, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_apply_dictionary(args):
    dictionary = load_dictionary(args.input)
    if args.text:
        text, source = args.text, "<--text>"
    elif args.input_text:
        text, source = RT.read_text_input(argparse.Namespace(
            input=args.input_text, text=None))
    elif sys.stdin is not None and not sys.stdin.isatty():
        text, source = sys.stdin.read(), "stdin"
    else:
        raise RT.ToolError(
            "No script text supplied.",
            code=RT.EXIT_INVALID_INPUT,
            hint="Pass --text '<script>' or --input-text <file>.")
    report = apply_dictionary(text, dictionary)
    report.update({"status": "ok", "command": "apply-dictionary", "source": source,
                   "idempotent_note": "Re-applying the result is safe: replaced terms no "
                                      "longer match the original entries."})
    RT.emit(report, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_validate_dictionary(args):
    dictionary = load_dictionary(args.input)
    report = {"status": "ok", "command": "validate-dictionary", "valid": True,
              "name": dictionary["name"],
              "entry_count": len(dictionary["entries"]),
              "problems": [], "warnings": []}
    seen = {}
    for entry in dictionary["entries"]:
        key = entry["term"].lower()
        if key in seen:
            report["warnings"].append(
                f"Duplicate term '{entry['term']}' (case-insensitive); the last entry wins.")
        seen[key] = entry
        if entry["say"].strip().lower() == entry["term"].strip().lower():
            report["warnings"].append(
                f"Entry '{entry['term']}' maps to itself; it has no effect.")
        if not re.search(r"[A-Za-z0-9]", entry["say"]):
            report["problems"].append(f"Entry '{entry['term']}' has an unusable 'say' value.")
    if args.text or args.input_text:
        text = args.text or RT.read_text_input(args)[0]
        applied = apply_dictionary(text, dictionary)
        report["applied"] = {"used": applied["used"], "unused": applied["unused"],
                             "text": applied["text"]}
        if applied["unused"]:
            report["warnings"].append(
                f"{len(applied['unused'])} dictionary terms do not appear in the script.")
    if report["problems"]:
        report["status"] = "invalid"
        report["valid"] = False
        RT.emit(report, output=args.output, quiet=args.quiet)
        return RT.EXIT_INVALID_INPUT
    RT.emit(report, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


_RISK_PATTERNS = [
    (r"\b[A-Z][a-z]+(?:-[A-Z0-9][a-z0-9]*)+\b", "brand", "hyphenated proper noun"),
    (r"\b[A-Z]{2,6}\b", "acronym", "uppercase acronym"),
    (r"\b[A-Z][a-z]{2,}\b", "name", "capitalized word (possible name or brand)"),
    (r"\b\w*\d\w*\b", "technical", "contains digits"),
    (r"\b(?:ph|zh|x|q|ç|ñ|ø|å|é|ü)\w*\b", "foreign", "unusual graphemes"),
    (r"\b(?:read|lead|live|wind|tear|bow|close|desert|present|record|object)\b",
     "homograph", "homograph"),
]


def cmd_build_dictionary(args):
    text, source = RT.read_text_input(args)
    dictionary = {
        "name": args.name or "draft-dictionary",
        "language": args.locale,
        "version": "0.1.0",
        "entries": [],
    }
    seen = set()
    risks = []
    for pattern, flag, label in _RISK_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE if flag == "homograph" else 0):
            term = match.group(0)
            if term.lower() in seen:
                continue
            if len(term) < 3 and flag != "acronym":
                continue
            if flag == "name" and term in _MONTHS:
                continue
            seen.add(term.lower())
            risks.append({"term": term, "flag": flag, "reason": label,
                          "context": text[max(match.start() - 30, 0):match.end() + 30].strip()})
            dictionary["entries"].append({
                "term": term,
                "say": term,
                "scope": "project",
                "source": "needs confirmation",
                "case_sensitive": flag in ("brand", "acronym", "name"),
            })
    if not dictionary["entries"]:
        dictionary["entries"].append({"term": text.split()[0] if text.split() else "term",
                                      "say": "term", "scope": "project",
                                      "source": "placeholder"})
    if args.output_dictionary:
        RT.write_json_file(args.output_dictionary, dictionary,
                           overwrite=args.force)
    RT.emit({"status": "ok", "command": "build-dictionary", "source": source,
             "risk_count": len(risks), "risks": risks[:100],
             "dictionary": dictionary, "dictionary_path": args.output_dictionary,
             "next_step": "Replace each 'say' value with an approved pronunciation "
                          "and set 'source'/'approved_by', then validate with "
                          "validate-dictionary."},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_providers(args):
    presets = _presets(args.assets_dir)
    RT.emit({
        "status": "ok",
        "command": "providers",
        "providers": [{
            "id": "builtin-formant",
            "available": True,
            "type": "deterministic formant synthesizer (NumPy)",
            "capabilities": ["prosody control", "emotion/attitude mapping",
                             "pitch/formant voice identity", "pauses and breaths",
                             "effect racks", "loudness normalisation",
                             "batch and brief rendering"],
            "limitations": presets["synthesis"]["declared_limitations"],
            "sample_rate_range": [8000, 48000],
            "needs_network": False,
        }],
        "external_adapters": {
            "note": "Add a provider by implementing render(text, settings) -> "
                    "(audio, samplerate) and registering it in cmd_render; keep "
                    "credentials in the environment, never in this folder.",
            "suggested_ids": ["external-neural-tts", "external-voice-conversion"],
        },
    }, output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_capabilities(args):
    RT.emit(RT.capabilities_report(
        PROG, CAPABILITIES,
        notes={"role": "text-to-speech, script performance, prosody, pronunciation",
               "providers": ["builtin-formant"],
               "assets": ["asset-voice-presets.json", "asset-schemas.json",
                          "asset-policy.json", "asset-fixtures.json"]}),
        output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_render_args(parser):
    RT.add_input_args(parser, text_help="Text to speak.")
    parser.add_argument("--profile", help="Voice profile JSON (defaults to neutral).")
    parser.add_argument("--dictionary", help="Pronunciation dictionary JSON.")
    parser.add_argument("--emotion", help="Emotion preset (confidence, joy, calm, ...).")
    parser.add_argument("--attitude", help="Attitude preset (professional, playful, ...).")
    parser.add_argument("--intensity", type=float, default=0.6)
    parser.add_argument("--pace-wpm", type=float, help="Override words per minute.")
    parser.add_argument("--effect", default="clean",
                        help="Effect rack name from asset-voice-presets.json (default clean).")
    parser.add_argument("--no-styles", action="store_true",
                        help="Ignore the profile's style attributes.")
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--format", default="wav", help="wav, flac, ogg, opus, mp3, aiff.")
    parser.add_argument("--subtype", help="SoundFile subtype, e.g. PCM_24, MPEG_LAYER_III.")
    parser.add_argument("--target-lufs", type=float,
                        help="Normalise to this integrated loudness after rendering.")
    parser.add_argument("--true-peak-dbtp", type=float, default=-1.0,
                        help="Limiter ceiling in dBTP (default -1.0).")
    parser.add_argument("--rate-mult", type=float, default=1.0,
                        help="Post-render speed multiplier (0.4-2.5) that also shifts pitch.")
    parser.add_argument("--highpass-hz", type=float)
    parser.add_argument("--lowpass-hz", type=float)
    parser.add_argument("--breaths", action="store_true",
                        help="Insert short breath noises before long pauses.")
    parser.add_argument("--lead-in", type=float, default=0.02)
    parser.add_argument("--tail-time", type=float, default=0.08)
    parser.add_argument("--sanitize", action="store_true",
                        help="Normalize numbers, dates, currency, units and acronyms first.")
    parser.add_argument("--provider", default="builtin-formant")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--out-dir", default="out")
    parser.add_argument("--output-audio", help="Explicit audio output path.")
    parser.add_argument("--write-sidecar", action="store_true",
                        help="Write the metadata JSON beside the audio file.")


def build_parser():
    parser = RT.build_parser(
        PROG,
        "Voice Capability Toolkit - render scripts and briefs to performed speech "
        "with prosody, emotion, pronunciation and loudness control.",
        epilog="Examples:\n"
               "  python3 tools-audio-synthesis.py render --text 'Your script here.' "
               "--out-dir out --target-lufs -16\n"
               "  python3 tools-audio-synthesis.py render-brief --input brief.json "
               "--out-dir out --per-segment-files\n",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("render", help="Render text to speech.")
    add_render_args(p)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("render-brief", help="Render a production brief.")
    p.add_argument("-i", "--input", required=True, help="Brief JSON.")
    p.add_argument("--profile", help="Voice profile JSON.")
    p.add_argument("--dictionary", help="Pronunciation dictionary JSON.")
    p.add_argument("--dictionary-library",
                   help="Directory of dictionaries for brief pronunciation presets.")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--format", default="wav")
    p.add_argument("--subtype")
    p.add_argument("--sample-rate", type=int, default=24000)
    p.add_argument("--emotion", help="Fallback emotion for segments without one.")
    p.add_argument("--attitude", help="Fallback attitude.")
    p.add_argument("--intensity", type=float, default=0.6)
    p.add_argument("--pace-wpm", type=float)
    p.add_argument("--effect", default="clean")
    p.add_argument("--no-styles", action="store_true")
    p.add_argument("--segment-gap", type=float, default=0.18,
                   help="Silence between segments in seconds (default 0.18).")
    p.add_argument("--per-segment-files", action="store_true",
                   help="Write one audio file per segment as well as the master.")
    p.add_argument("--fit-target-duration", action="store_true",
                   help="Scale each segment to its share of the brief target duration.")
    p.add_argument("--target-lufs", type=float,
                   help="Normalise the stitched master to this loudness.")
    p.add_argument("--true-peak-dbtp", type=float, default=-1.0)
    p.add_argument("--sanitize", action="store_true")
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--breaths", action="store_true", default=False)
    p.add_argument("--lead-in", type=float, default=0.02)
    p.add_argument("--tail-time", type=float, default=0.08)
    p.add_argument("--rate-mult", type=float, default=1.0)
    p.add_argument("--highpass-hz", type=float)
    p.add_argument("--lowpass-hz", type=float)
    p.add_argument("--provider", default="builtin-formant")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_render_brief)

    p = sub.add_parser("plan-prosody", help="Show the prosody plan only.")
    RT.add_input_args(p)
    p.add_argument("--emotion", default="neutral")
    p.add_argument("--attitude", default="professional")
    p.add_argument("--intensity", type=float, default=0.6)
    p.add_argument("--pace-wpm", type=float, default=155.0)
    p.add_argument("--styles", nargs="*", default=[])
    p.add_argument("--target-duration", type=float)
    p.add_argument("--seed", type=int, default=17)
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_plan_prosody)

    p = sub.add_parser("sanitize-text", help="Make numbers and symbols speakable.")
    RT.add_input_args(p)
    p.add_argument("--locale", default="en-US")
    p.add_argument("--no-acronyms", action="store_true")
    p.add_argument("--no-dates", action="store_true")
    p.add_argument("--no-units", action="store_true")
    p.add_argument("--no-money", action="store_true")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_sanitize_text)

    p = sub.add_parser("apply-dictionary", help="Apply a pronunciation dictionary.")
    p.add_argument("-i", "--input", required=True, help="Dictionary JSON.")
    p.add_argument("--text", help="Text to rewrite.")
    p.add_argument("--input-text", help="File with the text to rewrite.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_apply_dictionary)

    p = sub.add_parser("validate-dictionary", help="Validate a pronunciation dictionary.")
    p.add_argument("-i", "--input", required=True, help="Dictionary JSON.")
    p.add_argument("--text", help="Optional script to cross-check.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_validate_dictionary)

    p = sub.add_parser("build-dictionary", help="Detect risky terms and build a skeleton.")
    RT.add_input_args(p)
    p.add_argument("--name", help="Dictionary name (lowercase-hyphenated).")
    p.add_argument("--locale", default="en-US")
    p.add_argument("--output-dictionary", help="Write the dictionary JSON here.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_build_dictionary)

    p = sub.add_parser("providers", help="List providers and limitations.")
    RT.add_output_args(p)
    RT.add_common_args(p)
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("capabilities", help="Print the command index.")
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
                               ("timeout", 300.0), ("quiet", False), ("no_styles", False)):
        if not hasattr(args, attribute):
            setattr(args, attribute, default)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(RT.guard(main)())
