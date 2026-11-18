#!/usr/bin/env python3
"""tools-nv-voice.py - primary entry point for the Offline Neural Voice Skill Tool.

A local, CPU-only, air-gapped capable neural text-to-speech control plane with
rights gating, expressive presets, mastering, measurable QA, packaging,
fallbacks, diagnostics and lifecycle governance.

Usage
-----
  python tools-nv-voice.py <command> [options]

Discovery
  list-tools                     every command with capability and category mapping
  list-categories                the 18 catalog categories and implemented capabilities
  list-voices                    registry voices, installed state and licence status
  list-presets                   expressive delivery presets
  help <command>                 detailed help for one command

Model and environment
  fetch-model --voice <id> [--plan|--approve --approval-token T]
                                 download a pinned voice pair (the only network step)
  inspect-model --voice <id>     ONNX graph, opset, interface and footprint report
  compatibility                  model/voice/tokenizer/opset/runtime compatibility matrix
  doctor                         dependency, CPU, memory, offline and asset health
  offline-check                  prove the air gap and the offline guard
  dependencies                   third-party package availability

Synthesis
  prepare-text                   normalisation and pronunciation plan (no audio)
  synthesize                     text -> mastered WAV (+MP3), evidence and manifest
  preset-build                   render one sentence under several presets for review
  batch                          synthesize a manifest of clips from a JSON plan

Quality and evidence
  qa                             measurable acceptance checks on produced audio
  compare                        compare two audio files (before/after, A/B)
  evidence                       collect a combined evidence bundle for one run
  acceptance                     run the video-greeting acceptance scenario

Operations
  license-audit                  machine readable rights decision per voice
  manifest                       versioned manifest of artifacts and checksums
  package                        bundle a deliverable folder (files, no archives)
  journal                        inspect the audit journal and recover stale intents
  benchmark                      measure load and inference timing on this CPU
  explain                        explain a failure code or a command

Global options
  --config FILE      configuration JSON (default asset-default-config.json when present)
  --format json|text stdout format, default json; text is screen-reader friendly
  --verbose          include full detail
  --quiet            suppress stdout on success

Exit codes: 0 ok, 1 fail, 2 usage, 3 dependency, 4 invalid input, 5 approval
required, 130 cancelled. The full catalogue is available with `explain exit-codes`.

Safety: paths are sealed to the skill root, network sockets are blocked during
synthesis, checksums are verified before inference, rights gates can block use,
and destructive or publishing actions require --approve plus a plan token.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

if "nvcore" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("nvcore", HERE / "tools-nvcore.py")
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["nvcore"] = _module
    _spec.loader.exec_module(_module)

import nvcore

# Sibling modules are hyphenated on disk; load_sibling registers them by module name.
engine = nvcore.load_sibling("tools-nv-engine.py", "nvengine")
textprep = nvcore.load_sibling("tools-nv-text.py", "nvtext")
mastering = nvcore.load_sibling("tools-nv-master.py", "nvmaster")

TOOL = "offline-neural-voice"
VERSION = nvcore.__version__

#: The catalogue categories served by this Skill Tool, with the commands that
#: implement them. Used by list-tools / list-categories and by the docs.
CATEGORY_MAP = [
    ("Neural Models, Weights, and Voice Assets",
     ["list-voices", "inspect-model", "compatibility", "fetch-model"],
     "Registry, checksum verification, ONNX inspection, speaker data and compatibility."),
    ("Offline Dependencies and Installation",
     ["dependencies", "doctor"],
     "Local-only dependency inventory, CPU/OS/ABI facts, install guidance."),
    ("Voice Engine and Neural Inference Tools",
     ["synthesize", "benchmark", "preset-build"],
     "CPU ONNX inference, timing, deterministic seeds, engine comparison."),
    ("Voice Presets and Performance Direction",
     ["list-presets", "preset-build", "synthesize --preset"],
     "Versioned delivery presets that map direction to engine parameters."),
    ("Text Preparation, Normalization, and Pronunciation",
     ["prepare-text", "synthesize"],
     "Number/date/money expansion, markup stripping, lexicon and IPA overrides."),
    ("Audio Rendering, Mastering, and Export",
     ["synthesize", "compare"],
     "Trim, high-pass, de-ess, EQ, resample, loudness normalise, limit, WAV/MP3."),
    ("Quality Assurance and Acceptance Testing",
     ["qa", "acceptance", "evidence"],
     "Duration, loudness, true peak, clipping, silence, transcript, decoding."),
    ("Skill Components, Scripts, and References",
     ["list-tools", "explain"],
     "Tool inventory, shared runtime components and reference assets."),
    ("Offline Package and Distribution",
     ["package", "manifest"],
     "Deliverable folders with evidence, checksums and captions (no archives)."),
    ("Sandbox, Runtime, and Resource Management",
     ["doctor", "benchmark", "offline-check"],
     "CPU threads, memory envelope, timeouts, cancellation and sealed paths."),
    ("Safety, Rights, Consent, and Provenance",
     ["license-audit", "synthesize", "journal"],
     "Rights decisions, consent records, provenance in every result."),
    ("Fallbacks, Routing, and Resilience",
     ["synthesize", "explain"],
     "Engine routing, disclosed degraded fallback, retries and recovery."),
    ("Final Acceptance Scenario: Video Greeting",
     ["acceptance"],
     "The canonical greeting fixture with measurable pass/fail evidence."),
    ("Performance, Benchmarking, and Optimization",
     ["benchmark", "doctor"],
     "Warm-up, load timing, real-time factor and memory footprint."),
    ("Integration, Interfaces, and Automation",
     ["batch", "manifest"],
     "JSON-first CLI, batch manifests and machine readable results."),
    ("Diagnostics, Observability, and Support",
     ["doctor", "journal", "explain"],
     "Structured errors, exit codes, audit journal and recovery guidance."),
    ("Accessibility, Localization, and Inclusive Output",
     ["list-categories", "synthesize --text-format text"],
     "Screen-reader friendly output, plain language, locale profiles, captions."),
    ("Governance, Release, and Lifecycle Management",
     ["manifest", "package", "acceptance"],
     "Version pinning, change records, release gates and retention."),
]


# --------------------------------------------------------------------------- #
# Shared CLI infrastructure
# --------------------------------------------------------------------------- #

def add_global(parser):
    parser.add_argument("--config", metavar="FILE",
                        help="configuration JSON relative to the skill root")
    parser.add_argument("--format", choices=["json", "text"], default="json",
                        help="stdout format (default json; text is screen-reader friendly)")
    parser.add_argument("--verbose", action="store_true", help="include full detail")
    parser.add_argument("--quiet", action="store_true", help="suppress stdout on success")
    parser.add_argument("--no-journal", action="store_true", help="do not append to the audit journal")
    parser.add_argument("--actor", default="cli", help="journal actor label")
    return parser


def add_approval(parser, what):
    parser.add_argument("--approve", action="store_true",
                        help="confirm you reviewed the plan for %s" % what)
    parser.add_argument("--approval-token", default=None,
                        help="token printed by the --plan run, binds the approval to that plan")
    parser.add_argument("--approved-by", default=None, help="name of the human approving")
    return parser


def load_config_for(args, *, root=None):
    return nvcore.load_config(getattr(args, "config", None), root=root or HERE)


def envelope(tool_command, data=None, *, ok=True, warnings=None, artifacts=None,
             started=None, approval_required=False):
    """Build a result envelope; `started` is a monotonic clock reading."""
    return nvcore.result_envelope(
        ok=ok, tool=TOOL, command=tool_command, data=data,
        warnings=warnings or [], artifacts=artifacts or [],
        started_at=(nvcore.utc_now() if started is None else
                    _iso_from_monotonic(started)),
        duration_s=(time.monotonic() - started) if started else None,
        approval_required=approval_required)


def _iso_from_monotonic(started):
    wall = time.time() - (time.monotonic() - started)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wall))


def emit(payload, args):
    if getattr(args, "quiet", False) and payload.get("ok"):
        return nvcore.EXIT_OK
    if getattr(args, "format", "json") == "text":
        sys.stdout.write(nvcore.human_result(payload, verbose=getattr(args, "verbose", False)) + "\n")
    else:
        nvcore.print_json(payload)
    return nvcore.EXIT_OK if payload.get("ok") else nvcore.EXIT_FAIL


def artifact(path, kind, **extra):
    record = {"kind": kind, "path": str(path).replace(os.sep, "/")}
    full = (HERE / record["path"]) if not Path(str(path)).is_absolute() else Path(str(path))
    if full.is_file():
        record["bytes"] = full.stat().st_size
        record["sha256"] = nvcore.sha256_file(full)
    record.update(extra)
    return record


def resolve_in(cfg, value, *, must_exist=False, kind=None):
    return cfg.resolve(value, must_exist=must_exist, kind=kind)


def default_preset(cfg):
    presets_data, presets, _ = nvcore.load_presets(cfg)
    return presets, presets_data


def default_voice_id(cfg, registry, presets=None, preset=None):
    """Pick the voice to use when none was passed.

    Order: preset.preferred_voices -> registry default -> the only registry voice.
    Never guesses when several voices are installed and none is preferred.
    """
    if preset and preset.get("preferred_voices"):
        installed = cfg.ensure_dir("paths.models_dir")
        for candidate in preset["preferred_voices"]:
            entry = next((v for v in registry.get("voices", []) if v.get("id") == candidate), None)
            if entry and (installed / (entry.get("files") or {}).get("model", "")).is_file():
                return candidate
    if registry.get("default_voice"):
        return registry["default_voice"]
    voices = registry.get("voices", [])
    if len(voices) == 1:
        return voices[0].get("id")
    return None


def get_preset(cfg, preset_id):
    presets, presets_data = default_preset(cfg)
    if isinstance(preset_id, (list, tuple)):
        if not preset_id:
            preset_id = None
        elif len(preset_id) == 1:
            preset_id = preset_id[0]
        else:
            raise nvcore.InputError(
                "this command accepts one preset, got %d" % len(preset_id),
                hint="use `preset-build --preset <id> --preset <id>` to compare presets")
    if preset_id is None:
        preset_id = presets_data.get("default_preset")
    preset = presets.get(preset_id)
    if preset is None:
        raise nvcore.InputError("unknown preset %r" % preset_id,
                                hint="available presets: %s" % ", ".join(sorted(presets)))
    return preset


def preset_to_scales(preset, *, base=None):
    """Map a delivery preset onto engine scales with documented, bounded math."""
    base = base or {}
    length = float(preset.get("length_scale", base.get("length_scale", 1.0)))
    noise = float(preset.get("noise_scale", base.get("noise_scale", 0.667)))
    noise_w = float(preset.get("noise_w", base.get("noise_w", 0.8)))
    return {
        "length_scale": max(0.45, min(2.2, length)),
        "noise_scale": max(0.2, min(1.2, noise)),
        "noise_w": max(0.2, min(1.2, noise_w)),
    }


def read_text_input(args, cfg, text_arg):
    if text_arg:
        return text_arg
    if getattr(args, "text_file", None):
        path = cfg.resolve(args.text_file, must_exist=True, kind="file")
        return path.read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    raise nvcore.InputError("no text supplied",
                            hint="pass --text \"...\", --text-file <relative path>, "
                                 "or pipe text on stdin")


# --------------------------------------------------------------------------- #
# Discovery commands
# --------------------------------------------------------------------------- #

def cmd_list_tools(args):
    started = time.monotonic()
    rows = []
    for category, commands, purpose in CATEGORY_MAP:
        rows.append({"category": category, "commands": commands, "purpose": purpose})
    commands = {}
    for name, fn, summary in COMMAND_TABLE:
        commands[name] = {"summary": summary,
                          "capabilities": [c for c, cmds, _ in CATEGORY_MAP if name in cmds]}
    data = {
        "summary": "%d commands across %d capability categories" % (len(commands), len(CATEGORY_MAP)),
        "commands": commands,
        "categories": rows,
        "tools": [
            {"file": "tools-nvcore.py", "role": "shared runtime: config, paths, offline guard, "
                                                   "journal, WAV, loudness, schema, cancellation",
             "order": "loaded first by every tool"},
            {"file": "tools-nv-engine.py", "role": "phonemizer, phoneme ids, ONNX VITS engine, "
                                                   "piper adapter, fallback engine, voice resolution",
             "order": "loaded second"},
            {"file": "tools-nv-text.py", "role": "text normalisation, lexicon, directions, chunking",
             "order": "loaded third"},
            {"file": "tools-nv-master.py", "role": "mastering chain, loudness, limiter, export",
             "order": "loaded fourth"},
            {"file": "tools-nv-qa.py", "role": "measurable QA, ASR transcript check, acceptance fixture",
             "order": "loaded fifth"},
            {"file": "tools-nv-ops.py", "role": "fetch-model, packaging, manifest, journal, benchmark",
             "order": "loaded sixth"},
            {"file": "tools-nv-voice.py", "role": "primary entry point and command router",
             "order": "entry"},
        ],
        "assets": [
            {"file": "asset-default-config.json", "role": "complete default configuration"},
            {"file": "asset-model-registry.json", "role": "pinned models, checksums, licenses"},
            {"file": "asset-voice-presets.json", "role": "versioned delivery presets"},
            {"file": "asset-rights-policy.json", "role": "licence and consent gates"},
            {"file": "asset-pronunciation-lexicon.json", "role": "pronunciation overrides"},
            {"file": "asset-acceptance-fixture.json", "role": "video greeting acceptance case"},
            {"file": "asset-result-schema.json", "role": "JSON Schema for every CLI result"},
            {"file": "asset-config-schema.json", "role": "JSON Schema for the configuration"},
            {"file": "asset-batch-manifest.json", "role": "batch synthesis plan template"},
            {"file": "asset-caption-policy.json", "role": "SRT, VTT and accessibility "
                                                         "caption rules"},
        ],
    }
    return emit(envelope("list-tools", data, started=started), args)


def cmd_list_categories(args):
    started = time.monotonic()
    data = {
        "summary": "%d categories unified into one Skill Tool" % len(CATEGORY_MAP),
        "categories": [{"category": c, "commands": cmds, "purpose": p}
                       for c, cmds, p in CATEGORY_MAP],
    }
    return emit(envelope("list-categories", data, started=started), args)


def cmd_list_voices(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    policy, policy_rel = nvcore.load_policy(cfg)
    models_dir = cfg.ensure_dir("paths.models_dir")
    rows = []
    for entry in registry["voices"]:
        files = entry.get("files") or {}
        model_rel = files.get("model")
        config_rel = files.get("config") or (model_rel + ".json" if model_rel else None)
        installed_model = (models_dir / model_rel) if model_rel else None
        installed_config = (models_dir / config_rel) if config_rel else None
        installed = bool(installed_model and installed_model.is_file() and
                         installed_config and installed_config.is_file())
        rights = nvcore.evaluate_rights(entry, policy, use=args.use,
                                        redistributing=args.redistribute_use)
        row = {
            "id": entry.get("id"),
            "aliases": entry.get("aliases", []),
            "language": entry.get("language"),
            "gender": entry.get("gender"),
            "age_style": entry.get("age_style"),
            "quality": entry.get("quality"),
            "sample_rate": entry.get("sample_rate"),
            "num_speakers": entry.get("num_speakers"),
            "default": bool(entry.get("default")),
            "installed": installed,
            "model": model_rel,
            "selected": entry.get("id") == args.voice,
            "license_id": entry.get("license_id"),
            "rights": {"use": args.use, "decision": rights["decision"],
                       "obligations": rights["obligations"]},
            "notes": entry.get("notes"),
        }
        if installed and args.details:
            try:
                row["config"] = nvcore.inspect_voice_config(installed_config)
                row["config"].pop("path", None)
            except Exception as exc:  # inspection is best effort
                row["config_error"] = str(exc)
        rows.append(row)
    installed_count = sum(1 for r in rows if r["installed"])
    data = {
        "summary": "%d registry voices, %d installed (%s)" % (
            len(rows), installed_count, cfg.rel(models_dir)),
        "registry": registry_rel,
        "policy": policy_rel,
        "models_dir": cfg.rel(models_dir),
        "use": args.use,
        "voices": rows,
        "enabled_engines": registry.get("engines", {}),
    }
    return emit(envelope("list-voices", data, started=started,
                         warnings=[] if installed_count else
                         ["no voice is installed yet: run `fetch-model --voice <id> --plan`"]), args)


def cmd_list_presets(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    presets_data, presets, presets_rel = nvcore.load_presets(cfg)
    rows = []
    for preset in presets_data["presets"]:
        rows.append({
            "id": preset.get("id"),
            "version": preset.get("version"),
            "description": preset.get("description"),
            "emotion": preset.get("emotion"),
            "style": preset.get("style"),
            "scales": preset_to_scales(preset),
            "mastering": preset.get("mastering", {}),
            "default": preset.get("id") == presets_data.get("default_preset"),
        })
    data = {
        "summary": "%d presets, default %s" % (len(rows), presets_data.get("default_preset")),
        "presets_file": presets_rel,
        "presets": rows,
    }
    return emit(envelope("list-presets", data, started=started), args)


def cmd_help(args):
    started = time.monotonic()
    command = args.command or ""
    match = [row for row in COMMAND_TABLE if row[0] == command]
    if not match:
        raise nvcore.InputError("unknown command %r" % command,
                                hint="run `list-tools` to see every command")
    name, fn, summary = match[0]
    parser = build_parser()
    sub = [action for action in parser._actions
           if isinstance(action, argparse._SubParsersAction)][0]
    text = sub.choices[name].format_help()
    data = {"summary": summary, "usage": text,
            "capabilities": [c for c, cmds, _ in CATEGORY_MAP if name in cmds]}
    payload = envelope("help", data, started=started)
    if args.format == "text":
        sys.stdout.write("%s\n\n%s\n" % (summary, text))
        return nvcore.EXIT_OK
    return emit(payload, args)


# --------------------------------------------------------------------------- #
# Model inspection and compatibility
# --------------------------------------------------------------------------- #

def cmd_inspect_model(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    if args.model:
        model_path = cfg.resolve(args.model, must_exist=True, kind="file")
        config_path = cfg.resolve(args.voice_config, must_exist=True, kind="file") \
            if args.voice_config else None
        voice_id = None
    else:
        registry, _, _ = nvcore.load_registry(cfg)
        asset = engine.resolve_voice(cfg, args.voice, speaker=args.speaker, registry=registry)
        model_path, config_path, voice_id = asset.model_path, asset.config_path, asset.voice_id
    report = nvcore.inspect_onnx_model(model_path, config_path=config_path)
    warnings = list(report.get("warnings", []))
    if report.get("voice_config"):
        config = engine.VoiceConfig(nvcore.read_json(config_path))
        compat = config.compatibility(model_report=report, model_path=model_path)
        report["compatibility"] = compat
        warnings.extend(compat["warnings"])
    try:
        report["runtime_probe"] = nvcore.probe_onnxruntime(
            model_path, threads=int(cfg.get("inference.intra_op_threads", 1)),
            providers=tuple(cfg.get("inference.providers") or ("CPUExecutionProvider",)))
    except nvcore.NVError as exc:
        report["runtime_probe"] = {"error": exc.to_dict()}
        warnings.append("runtime probe failed: %s" % exc.message)
    report["voice_id"] = voice_id
    report["summary"] = "%s: %s MiB, opset %s, %s inputs" % (
        model_path.name, report["footprint"]["on_disk_mib"], report["opset"],
        len(report["inputs"]))
    return emit(envelope("inspect-model", report, started=started, warnings=warnings), args)


def cmd_compatibility(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    registry, _, registry_rel = nvcore.load_registry(cfg)
    espeak_report = engine.Phonemizer().probe()
    ort_version = None
    ort_providers = []
    try:
        ort = nvcore.import_optional("onnxruntime", feature="compat",
                                     install_hint="pip install onnxruntime")
        ort_version = ort.__version__
        ort_providers = ort.get_available_providers()
    except nvcore.NVError:
        pass
    rows = []
    for entry in registry["voices"]:
        model_rel = (entry.get("files") or {}).get("model")
        path = cfg.ensure_dir("paths.models_dir") / model_rel if model_rel else None
        row = {"id": entry.get("id"), "installed": bool(path and path.is_file()),
               "sample_rate": entry.get("sample_rate"),
               "language": entry.get("language"),
               "checks": {}}
        if row["installed"]:
            try:
                report = nvcore.inspect_onnx_model(path)
                config = engine.VoiceConfig(
                    nvcore.read_json(path.parent / (path.name + ".json")))
                compat = config.compatibility(model_report=report)
                row["checks"] = {
                    "opset": report.get("opset"),
                    "opset_ok": (report.get("opset") or 0) <= 21,
                    "sample_rate_ok": config.sample_rate in registry.get("supported_sample_rates",
                                                                        [16000, 22050, 24000]),
                    "phoneme_map_ok": config.num_symbols_consistent,
                    "provider_ok": "CPUExecutionProvider" in ort_providers,
                    "warnings": compat["warnings"],
                }
                row["ok"] = all([row["checks"]["opset_ok"], row["checks"]["sample_rate_ok"],
                                 row["checks"]["provider_ok"]])
            except Exception as exc:
                row["checks"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
                row["ok"] = False
        else:
            row["ok"] = None
        rows.append(row)
    engine_availability = None
    try:
        engine_module = nvcore.load_sibling("tools-nv-engine.py", "nvengine")
        engine_availability = engine_module.engine_availability(cfg)
    except nvcore.NVError:
        engine_availability = None
    data = {
        "summary": "%d of %d registry voices installed and checked" % (
            sum(1 for r in rows if r["installed"]), len(rows)),
        "registry": registry_rel,
        "engines": engine_availability,
        "runtime": {"onnxruntime": ort_version, "providers": ort_providers,
                    "tested_opset_max": 21, "python": sys.version.split()[0]},
        "phonemizer": espeak_report,
        "voices": rows,
        "matrix": {
            "model_family": "Piper VITS (single and multi speaker)",
            "tokenizer": "espeak-ng IPA phoneme_id_map embedded in the .onnx.json",
            "hop_length": 256,
            "sample_rates_tested": [16000, 22050],
            "unsupported": ["Tacotron/GlowTTS ONNX without a Piper .onnx.json",
                            "phoneme_type pinyin/hebrew/japanese/thai (needs its own phonemizer)"],
        },
    }
    warnings = []
    if not any(r["installed"] for r in rows):
        warnings.append("no voice installed: compatibility was checked against registry "
                        "metadata only; install one with "
                        "`fetch-model --voice <id> --plan`")
    return emit(envelope("compatibility", data, started=started, warnings=warnings), args)


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #

def _gather_synthesis_inputs(args, cfg, *, with_preset=True):
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    policy, policy_rel = nvcore.load_policy(cfg)
    preset = get_preset(cfg, getattr(args, "preset", None)) if with_preset else None
    text = read_text_input(args, cfg, getattr(args, "text", None))
    return registry, policy, preset, text


def _rights_gate(cfg, asset, policy, *, use, redistributing, consent_record, journal):
    decision = nvcore.evaluate_rights(asset.registry_entry, policy, use=use,
                                      redistributing=redistributing,
                                      voice_consent=consent_record)
    journal.record("rights-decision", voice=asset.voice_id, use=use,
                   decision=decision["decision"], license=decision["license_id"],
                   blocked=decision["blocked"])
    if decision["blocked"] and cfg.get("execution.fail_on_blocked_rights", True):
        raise nvcore.PolicyError(
            "rights gate blocked this request",
            hint="use a voice whose licence permits %r, or record an approval" % use,
            details=decision)
    return decision


def _consent_record(args):
    if not getattr(args, "consent_record", None):
        return None
    return {"affirmed": True, "record_id": args.consent_record,
            "affirmed_by": getattr(args, "consent_by", None) or "unnamed",
            "affirmed_at": nvcore.utc_now()}


def _phonemize(text, cfg, asset, *, directions=None, cancel=None):
    phonemizer = engine.Phonemizer(espeak_voice=asset.config.espeak_voice,
                                   allow_binary=True)
    phonemizer.probe()
    sentences, warnings = phonemizer.phonemize(text, cancel=cancel)
    return phonemizer, sentences, warnings


def _synthesize_audio(cfg, asset, phonemizer, sentences, *, preset, args, cancel, journal):
    scales = preset_to_scales(preset, base=cfg.get("synthesis", {}) or {})
    seed = getattr(args, "seed", None)
    if seed is None:
        seed = int(cfg.get("synthesis.seed", 1337))
    engine_name = getattr(args, "engine", None) or "auto"
    chosen = engine_name
    fallback_reason = None
    if chosen == "auto":
        availability = engine.engine_availability(cfg)
        if availability[engine.ENGINE_ONNX]["available"]:
            chosen = engine.ENGINE_ONNX
        elif availability.get("piper", {}).get("available"):
            chosen = engine.ENGINE_PIPER
        elif cfg.get("execution.allow_fallback_engine", True):
            chosen = engine.ENGINE_FALLBACK
            fallback_reason = "no neural runtime available"
        else:
            raise nvcore.DependencyError(
                "no engine is available and fallback is disabled",
                hint="pip install numpy onnxruntime, or set execution.allow_fallback_engine=true")
    if chosen == engine.ENGINE_FALLBACK and not cfg.get("execution.allow_fallback_engine", True):
        raise nvcore.DependencyError("fallback engine disabled by configuration",
                                     hint="set execution.allow_fallback_engine=true to allow "
                                          "clearly labelled degraded output")
    # Seed the numpy generator used by the ONNX model for sampler noise when present.
    try:
        import numpy as _np
        _np.random.seed(int(seed) % (2 ** 32))
    except Exception:
        pass
    started = time.monotonic()
    results = []
    total_samples = array("h")
    pause_ms = int(cfg.get("accessibility.pause_after_sentence_ms", 0) or 0)
    synth_engine = engine.build_engine(chosen, asset, cfg=cfg, cancel=cancel)
    with nvcore.offline_guard(cfg.offline):
        for index, phonemes in enumerate(sentences):
            cancel.check()
            request_scales = dict(scales)
            request_scales["seed"] = seed
            if chosen == engine.ENGINE_PIPER:
                request_scales["_text"] = getattr(args, "text_for_piper", None) or ""
            request = engine.SynthesisRequest(
                phonemes, scales=request_scales, speaker_index=asset.speaker_index,
                seed=seed, timeout_s=float(cfg.get("execution.timeout_seconds", 120.0)))
            try:
                result = synth_engine.synthesize(request)
            except nvcore.NVError as exc:
                if chosen != engine.ENGINE_FALLBACK and cfg.get("execution.allow_fallback_engine", True):
                    journal.record("engine-fallback", from_engine=chosen,
                                   reason=exc.message)
                    fallback_reason = "%s failed: %s" % (chosen, exc.message)
                    chosen = engine.ENGINE_FALLBACK
                    synth_engine = engine.build_engine(chosen, asset, cfg=cfg, cancel=cancel)
                    result = synth_engine.synthesize(request)
                else:
                    raise
            results.append(result)
            total_samples.extend(result.samples)
            if pause_ms and index < len(sentences) - 1:
                total_samples.extend(array("h", bytes(2 * int(result.sample_rate * pause_ms / 1000.0))))
    elapsed = time.monotonic() - started
    degraded = any(r.degraded for r in results)
    disclosure = next((r.disclosure for r in results if r.disclosure), None)
    return {
        "samples": total_samples,
        "sample_rate": results[0].sample_rate if results else asset.config.sample_rate,
        "engine": chosen,
        "engine_requested": engine_name,
        "fallback_reason": fallback_reason,
        "degraded": degraded,
        "disclosure": disclosure,
        "scales": scales,
        "seed": seed,
        "inference_ms": round(elapsed * 1000, 2),
        "sentences": [r.describe() for r in results],
        "warnings": asset.warnings + (["degraded fallback engine used"] if degraded else []),
    }


def _strip_directions_for_piper(text, directions):
    return text


def cmd_synthesize(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    journal = nvcore.build_journal(cfg, actor=args.actor)
    recovery = journal.recover()
    registry, policy, preset, raw_text = _gather_synthesis_inputs(args, cfg)
    asset = engine.resolve_voice(cfg, args.voice, speaker=args.speaker, registry=registry)
    rights = _rights_gate(cfg, asset, policy, use=args.use, redistributing=args.redistributing,
                          consent_record=_consent_record(args), journal=journal)
    lexicon, _, lexicon_rel = nvcore.load_lexicon(cfg)
    locale = args.locale or asset.config.language or "en-US"
    prepared = textprep.prepare_text(
        raw_text, cfg=cfg, lexicon=lexicon, locale=locale,
        max_chars=int(cfg.get("execution.max_chars", 4000)),
        script_guard=str(cfg.get("execution.script_guard", "block")))
    if not prepared["chunks"]:
        raise nvcore.InputError("text preparation produced no speakable content")
    # captions must show the words a human would read, so they are derived from the
    # same preparation WITHOUT the lexicon IPA overrides.
    display_prepared = textprep.prepare_text(
        raw_text, cfg=cfg, lexicon=None, locale=locale,
        max_chars=int(cfg.get("execution.max_chars", 4000)),
        script_guard=str(cfg.get("execution.script_guard", "block")))
    out_dir = cfg.ensure_dir("paths.output_dir")
    name = nvcore.slugify(args.name or _default_name(raw_text), max_len=40)
    wav_rel = "%s/%s.wav" % (cfg.get("paths.output_dir"), name)
    wav_path = cfg.resolve(wav_rel)
    mp3_path = wav_path.with_suffix(".mp3")
    if wav_path.exists() and not (args.overwrite or cfg.get("execution.overwrite", False)):
        raise nvcore.InputError(
            "output already exists: %s" % cfg.rel(wav_path),
            hint="pass --overwrite to replace it, or --name to write a new artifact")

    cancel = nvcore.CancellationToken(
        cfg.resolve(args.cancel_file) if args.cancel_file else None,
        poll_seconds=float(cfg.get("execution.cancel_poll_seconds", 0.2)))
    cancel.set_deadline(float(cfg.get("execution.run_timeout_seconds", 900.0)))

    plan = engine.plan_synthesis(cfg, asset, text=prepared["text"], preset=preset,
                                 engine_name=args.engine or "auto")
    plan["outputs_planned"] = [wav_rel] + (["%s/%s.mp3" % (cfg.get("paths.output_dir"), name)]
                                           if args.mp3 else [])
    plan["writes"] = plan["outputs_planned"]
    plan["preset"] = {"id": preset.get("id"), "version": preset.get("version")}
    plan["rights_decision"] = rights["decision"]
    plan["locale"] = locale
    plan["degraded_fallback_allowed"] = bool(cfg.get("execution.allow_fallback_engine", True))
    token = nvcore.plan_token(plan)

    if args.plan:
        data = {"summary": "dry run: no audio written", "plan": plan,
                "plan_token": token, "approval_needed": bool(args.mp3 or args.use != "personal_use")}
        return emit(envelope("synthesize", data, started=started), args)

    if rights["decision"] != "allow" and cfg.get("safety.require_approval_for_release", True):
        nvcore.require_approval(cfg, action="synthesize-under-rights-review",
                                approval_flag=args.approve, approval_token=args.approval_token,
                                approved_by=args.approved_by, journal=journal)

    journal.begin_intent("synthesize", voice=asset.voice_id, text_chars=len(prepared["text"]),
                         plan_token=token)
    try:
        phonemizer, sentences, ph_warnings = _phonemize(prepared["text"], cfg, asset,
                                                        directions=prepared["directions"],
                                                        cancel=cancel)
        synth = _synthesize_audio(cfg, asset, phonemizer, sentences, preset=preset,
                                  args=_with_text(args, prepared["text"]), cancel=cancel,
                                  journal=journal)
        samples = synth["samples"]
        if not samples:
            raise nvcore.NVError("synthesis produced no samples")
        master_cfg = dict(cfg.get("mastering", {}))
        master_cfg.update(preset.get("mastering", {}) or {})
        mastered, rate, master_report = mastering.master_pipeline(
            samples, synth["sample_rate"], cfg=cfg,
            target_lufs=args.target_lufs if args.target_lufs is not None else master_cfg.get("target_lufs"),
            ceiling_dbfs=master_cfg.get("max_true_peak_dbfs"),
            eq_curve=preset.get("eq_curve", "neutral"),
            cancel=cancel)
        export_wav = cfg.resolve(wav_rel)
        mastering.export_wav(export_wav, mastered, rate, overwrite=True)
        artifacts = [artifact(wav_rel, "mastered-wav", sample_rate=rate,
                              seconds=round(len(mastered) / float(rate), 4))]
        mp3_report = None
        if args.mp3:
            try:
                mastered_module = mastering
                mastered_module.export_mp3(mp3_path, mastered, rate,
                                           bitrate_kbps=int(cfg.get("packaging.mp3_bitrate_kbps", 192)),
                                           overwrite=True)
                mp3_report = mastering.verify_mp3(mp3_path)
                artifacts.append(artifact("%s/%s.mp3" % (cfg.get("paths.output_dir"), name),
                                          "mp3", decoded=mp3_report))
            except nvcore.DependencyError as exc:
                ph_warnings.append("MP3 export skipped: %s" % exc.message)
        evidence_dir = cfg.ensure_dir("paths.evidence_dir")
        transcript = {
            "requested_text": raw_text,
            "prepared_text": prepared["text"],
            "chunks": prepared["chunks"],
            "rewrites": prepared["rewrites"],
            "phonemes": ["".join(s) for s in sentences],
            "phonemizer_backend": phonemizer.backend,
        }
        evidence = {
            "generated_at": nvcore.utc_now(),
            "voice": asset.describe(),
            "engine": {k: synth[k] for k in ("engine", "engine_requested", "fallback_reason",
                                             "degraded", "disclosure", "scales", "seed",
                                             "inference_ms")},
            "preset": {"id": preset.get("id"), "version": preset.get("version"),
                       "emotion": preset.get("emotion"), "style": preset.get("style")},
            "rights": rights,
            "text": transcript,
            "mastering": master_report,
            "mp3": mp3_report,
            "plan_token": token,
            "recovery": recovery,
        }
        evidence_path = evidence_dir / ("%s.evidence.json" % name)
        nvcore.atomic_write_json(evidence_path, evidence, overwrite=True)
        artifacts.append(artifact("%s/%s.evidence.json" % (cfg.get("paths.evidence_dir"), name),
                                  "evidence"))
        captions = None
        if args.captions:
            captions = _write_captions(cfg, name, display_prepared, mastered, rate,
                                       degraded=synth["degraded"])
            artifacts.extend(captions["artifacts"])
        warnings = synth["warnings"] + ph_warnings
        if prepared["warnings"]:
            warnings.extend(prepared["warnings"])
        if synth["degraded"]:
            warnings.append(synth["disclosure"])
        journal.record("synthesize", voice=asset.voice_id, preset=preset.get("id"),
                       engine=synth["engine"], wav=wav_rel, plan_token=token,
                       degraded=synth["degraded"], lufs=master_report["final"]["loudness"].get("integrated_lufs"))
        journal.clear_intent()
        data = {
            "summary": "%s -> %s (%.2f s, %s LUFS, %s dBTP, engine %s)" % (
                cfg.rel(asset.model_path), wav_rel,
                len(mastered) / float(rate),
                master_report["final"]["loudness"].get("integrated_lufs"),
                master_report["final"]["true_peak"]["true_peak_dbfs"], synth["engine"]),
            "output": {"wav": wav_rel, "mp3": mp3_report and "%s/%s.mp3" % (cfg.get("paths.output_dir"), name),
                       "evidence": "%s/%s.evidence.json" % (cfg.get("paths.evidence_dir"), name),
                       "captions": captions and captions["paths"]},
            "metrics": {"duration_s": round(len(mastered) / float(rate), 4),
                        "loudness_lufs": master_report["final"]["loudness"].get("integrated_lufs"),
                        "true_peak_dbfs": master_report["final"]["true_peak"]["true_peak_dbfs"],
                        "sample_rate": rate,
                        "inference_ms": synth["inference_ms"],
                        "real_time_factor": round((synth["inference_ms"] / 1000.0) /
                                                  max(0.001, len(mastered) / float(rate)), 3)},
            "engine": {k: synth[k] for k in ("engine", "engine_requested", "fallback_reason",
                                             "degraded", "disclosure")},
            "rights": {"decision": rights["decision"], "license_id": rights["license_id"],
                       "obligations": rights["obligations"]},
            "preset": {"id": preset.get("id"), "version": preset.get("version")},
            "text": {"prepared": prepared["text"], "rewrites": len(prepared["rewrites"]),
                     "phonemizer": phonemizer.backend},
            "mastering_compliant": master_report["compliant"],
            "plan_token": token,
        }
        return emit(envelope("synthesize", data, started=started, warnings=warnings,
                             artifacts=artifacts), args)
    except BaseException:
        journal.clear_intent()
        raise


def _default_name(text):
    """Short, readable, deterministic artifact basename from the spoken text."""
    words = " ".join(str(text).split()[:4])
    return words or "clip"


def _with_text(args, text):
    clone = argparse.Namespace(**vars(args))
    clone.text_for_piper = text
    return clone


def _write_captions(cfg, name, prepared, samples, rate, *, degraded=False):
    """Write SRT, VTT and plain transcript captions derived from the final timing."""
    out_dir = cfg.ensure_dir("paths.output_dir")
    duration = len(samples) / float(rate)
    caption_text = re.sub(r"\[\[(.*?)\]\]", "", prepared["text"]).replace("  ", " ").strip()
    sentences = nvcore.split_sentences(caption_text) or [caption_text]
    label = None
    if degraded:
        try:
            policy, _ = nvcore.load_policy(cfg)
        except nvcore.NVError:
            policy = {}
        label = ((policy.get("fallback_disclosure") or {}).get("degraded_label")
                 or "DEGRADED ENGINE OUTPUT")
    weights = [max(1, nvcore.count_words(s)) for s in sentences]
    total = float(sum(weights))
    srt_lines = []
    vtt_lines = ["WEBVTT", ""]
    cursor = 0.0
    paths = []
    artifacts_out = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights), start=1):
        span = duration * (weight / total)
        start, end = cursor, min(duration, cursor + span)
        cursor = end
        srt_lines.append("%d" % index)
        srt_lines.append("%s --> %s" % (_stamp(start, ","), _stamp(end, ",")))
        srt_lines.append(sentence.strip())
        srt_lines.append("")
        vtt_lines.append("%s --> %s" % (_stamp(start, "."), _stamp(end, ".")))
        vtt_lines.append(sentence.strip())
        vtt_lines.append("")
    srt_text = "\n".join(srt_lines)
    vtt_text = "\n".join(vtt_lines)
    plain_text = "\n".join(([label] if label else []) + sentences)
    for suffix, payload in ((".srt", srt_text), (".vtt", vtt_text), (".txt", plain_text)):
        rel = "%s/%s%s" % (cfg.get("paths.output_dir"), name, suffix)
        nvcore.atomic_write_text(cfg.resolve(rel), payload, overwrite=True)
        paths.append(rel)
        artifacts_out.append(artifact(rel, "caption"))
    return {"paths": paths, "artifacts": artifacts_out, "cue_count": len(sentences)}


def _stamp(seconds, sep=","):
    ms = int(round(seconds * 1000))
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return "%02d:%02d:%02d%s%03d" % (hours, minutes, secs, sep, millis)


def cmd_preset_build(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    journal = nvcore.build_journal(cfg, actor=args.actor)
    registry, policy, _, raw_text = _gather_synthesis_inputs(args, cfg, with_preset=False)
    presets_data, presets, _ = nvcore.load_presets(cfg)
    if not args.voice:
        first_preset = presets.get(args.preset[0]) if args.preset else None
        args.voice = default_voice_id(cfg, registry, preset=first_preset)
    asset = engine.resolve_voice(cfg, args.voice, speaker=args.speaker, registry=registry)
    rights = _rights_gate(cfg, asset, policy, use=args.use, redistributing=False,
                          consent_record=None, journal=journal)
    lexicon, _, _ = nvcore.load_lexicon(cfg)
    prepared = textprep.prepare_text(
        raw_text, cfg=cfg, lexicon=lexicon,
        locale=args.locale or asset.config.language or "en-US",
        script_guard=str(cfg.get("execution.script_guard", "block")))
    wanted = args.preset or [p["id"] for p in presets_data["presets"]]
    if isinstance(wanted, str):
        wanted = [wanted]
    out_dir = cfg.ensure_dir("paths.output_dir")
    rows = []
    artifacts_out = []
    cancel = nvcore.CancellationToken(None)
    for preset_id in wanted:
        preset = presets.get(preset_id)
        if preset is None:
            raise nvcore.InputError("unknown preset %r" % preset_id)
        synth = _synthesize_audio(cfg, asset, engine.Phonemizer(espeak_voice=asset.config.espeak_voice),
                                  _phonemize(prepared["text"], cfg, asset, cancel=cancel)[1],
                                  preset=preset, args=_with_text(args, prepared["text"]),
                                  cancel=cancel, journal=journal)
        mastered, rate, report = mastering.master_pipeline(
            synth["samples"], synth["sample_rate"], cfg=cfg,
            target_lufs=preset.get("mastering", {}).get("target_lufs"),
            ceiling_dbfs=preset.get("mastering", {}).get("max_true_peak_dbfs"),
            eq_curve=preset.get("eq_curve", "neutral"))
        rel = "%s/%s-%s.wav" % (cfg.get("paths.output_dir"),
                                nvcore.slugify(args.name or _default_name(raw_text),
                                               max_len=32), preset_id)
        mastering.export_wav(cfg.resolve(rel), mastered, rate, overwrite=True)
        artifacts_out.append(artifact(rel, "preset-sample", preset=preset_id))
        rows.append({
            "preset": preset_id, "version": preset.get("version"),
            "emotion": preset.get("emotion"), "style": preset.get("style"),
            "scales": synth["scales"], "output": rel,
            "duration_s": round(len(mastered) / float(rate), 3),
            "loudness_lufs": report["final"]["loudness"].get("integrated_lufs"),
            "true_peak_dbfs": report["final"]["true_peak"]["true_peak_dbfs"],
            "engine": synth["engine"],
        })
    journal.record("preset-build", voice=asset.voice_id, presets=list(wanted))
    data = {"summary": "rendered %d presets for review" % len(rows), "samples": rows,
            "rights": {"decision": rights["decision"]}}
    return emit(envelope("preset-build", data, started=started, artifacts=artifacts_out), args)


def cmd_batch(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    journal = nvcore.build_journal(cfg, actor=args.actor)
    plan_path = cfg.resolve(args.plan_file, must_exist=True, kind="file")
    plan = nvcore.read_json(plan_path, what="batch plan")
    if "clips" not in plan or not isinstance(plan["clips"], list):
        raise nvcore.InputError("batch plan must contain a `clips` list")
    if len(plan["clips"]) > int(plan.get("limits", {}).get("max_clips", 50)):
        raise nvcore.InputError("batch plan exceeds the clip limit",
                                hint="raise limits.max_clips in the plan or split the batch")
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    policy, policy_rel = nvcore.load_policy(cfg)
    results = []
    failures = 0
    output_dir = cfg.ensure_dir("paths.output_dir")
    base_name = args.name or "batch"
    plan_defaults = plan.get("defaults") or {}
    if not args.voice:
        args.voice = plan_defaults.get("voice") or default_voice_id(cfg, registry)
    if not args.preset:
        args.preset = plan_defaults.get("preset")
    for index, clip in enumerate(plan["clips"], start=1):
        text = clip.get("text")
        if not text:
            raise nvcore.InputError("clip %d has no text" % index)
        ns = argparse.Namespace(**vars(args))
        ns.text = text
        # batch clips reuse the parent namespace; make every attribute explicit so a
        # missing default never surfaces as an AttributeError mid-run
        ns.speaker = clip.get("speaker", getattr(args, "speaker", None))
        ns.locale = clip.get("locale", getattr(args, "locale", None))
        ns.seed = clip.get("seed", getattr(args, "seed", None))
        ns.target_lufs = getattr(args, "target_lufs", None)
        ns.captions = bool(clip.get("captions", False))
        ns.cancel_file = getattr(args, "cancel_file", None)
        ns.consent_record = getattr(args, "consent_record", None)
        ns.consent_by = getattr(args, "consent_by", None)
        ns.redistributing = getattr(args, "redistributing", False)
        ns.engine = clip.get("engine", getattr(args, "engine", "auto"))
        ns.use = clip.get("use", getattr(args, "use", "personal_use"))
        ns.name = "%s-%02d-%s" % (base_name, index, nvcore.slugify(clip.get("id") or text[:20]))
        ns.preset = clip.get("preset") or args.preset
        ns.voice = clip.get("voice") or args.voice
        ns.mp3 = bool(clip.get("mp3", args.mp3))
        ns.overwrite = True
        ns.plan = False
        ns.approve = args.approve
        ns.approval_token = args.approval_token
        ns.approved_by = args.approved_by
        ns.format = "json"
        ns.quiet = True
        try:
            code = cmd_synthesize(ns)
            results.append({"clip": clip.get("id", index), "ok": code == 0,
                            "name": ns.name})
        except nvcore.NVError as exc:
            failures += 1
            results.append({"clip": clip.get("id", index), "ok": False,
                            "error": exc.to_dict()})
    total = len(plan["clips"])
    summary = {"total": total, "ok": total - failures, "failed": failures}
    data = {"summary": "%(ok)d/%(total)d clips synthesized" % summary,
            "batch": summary, "results": results,
            "output_dir": cfg.rel(output_dir)}
    warnings = ["%d of %d clips failed" % (failures, total)] if failures else []
    payload = envelope("batch", data, ok=(failures == 0), started=started,
                       warnings=warnings)
    if failures >= total:
        payload["errors"] = [{"code": "batch-failed",
                              "message": "every clip in the batch failed",
                              "hint": "see data.results for each clip error"}]
    code = emit(payload, args)
    return code if failures == 0 else nvcore.EXIT_FAIL


# --------------------------------------------------------------------------- #
# QA, evidence and acceptance
# --------------------------------------------------------------------------- #

def cmd_qa(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    qa = nvcore.load_sibling("tools-nv-qa.py", "nvqa")
    report = qa.assess_audio_file(
        cfg, args.audio, text=args.expect_text,
        locale=args.locale or "en-US",
        duration_range=(args.min_seconds, args.max_seconds),
        transcript=args.transcript,
        asr_model=args.asr_model)
    payload = envelope("qa", report, started=started,
                       warnings=report.get("warnings", []),
                       artifacts=[{"kind": "input-audio", "path": args.audio}])
    code = emit(payload, args)
    return code if report["verdict"] != "fail" else nvcore.EXIT_FAIL


def cmd_compare(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    a = cfg.resolve(args.file_a, must_exist=True, kind="file")
    b = cfg.resolve(args.file_b, must_exist=True, kind="file")
    ra = nvcore.audio_file_report(a)
    rb = nvcore.audio_file_report(b)
    def key(report):
        loud = report.get("loudness", {}) or {}
        peak = report.get("true_peak", {}) or {}
        return {"duration_s": report.get("duration_s") or report.get("decoded", {}).get("duration_s"),
                "lufs": loud.get("integrated_lufs"),
                "true_peak_dbfs": peak.get("true_peak_dbfs"),
                "peak_dbfs": (report.get("stats") or {}).get("peak_dbfs")}
    ka, kb = key(ra), key(rb)
    delta = {k: (None if ka[k] is None or kb[k] is None else round(kb[k] - ka[k], 3))
             for k in ka}
    data = {
        "summary": "deltas (B - A): %s" % ", ".join(
            "%s %+0.3f" % (k, v) for k, v in delta.items() if isinstance(v, (int, float))),
        "a": {"path": cfg.rel(a), "metrics": ka},
        "b": {"path": cfg.rel(b), "metrics": kb},
        "delta_b_minus_a": delta,
    }
    return emit(envelope("compare", data, started=started,
                         artifacts=[{"kind": "audio-a", "path": cfg.rel(a)},
                                    {"kind": "audio-b", "path": cfg.rel(b)}]), args)


def cmd_evidence(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    qa_tool = nvcore.load_sibling("tools-nv-qa.py", "nvqa")
    audio = cfg.resolve(args.audio, must_exist=True, kind="file")
    report = qa_tool.assess_audio_file(cfg, args.audio, text=args.expect_text,
                                       duration_range=(args.min_seconds, args.max_seconds),
                                       transcript=args.transcript)
    bundle = {
        "generated_at": nvcore.utc_now(),
        "audio": nvcore.audio_file_report(audio),
        "qa": report,
        "environment": nvcore.environment_report(),
        "offline": nvcore.offline_probe(cfg, timeout=1.0),
        "dependencies": nvcore.dependency_report(),
        "runtime_files": [{"file": name, "sha256": nvcore.sha256_file(HERE / name)}
                          for name in sorted(p.name for p in HERE.glob("tools-*.py"))],
    }
    rel = args.out or "%s/evidence-%s.json" % (cfg.get("paths.evidence_dir"),
                                                nvcore.slugify(audio.stem))
    nvcore.atomic_write_json(cfg.resolve(rel), bundle, overwrite=True)
    bundle["evidence_file"] = rel
    bundle["summary"] = "evidence bundle for %s (%s)" % (cfg.rel(audio), report["verdict"])
    return emit(envelope("evidence", bundle, started=started,
                         artifacts=[artifact(rel, "evidence-bundle")]), args)


def cmd_acceptance(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    qa = nvcore.load_sibling("tools-nv-qa.py", "nvqa")
    journal = nvcore.build_journal(cfg, actor=args.actor)
    acceptance, acceptance_rel = qa.load_acceptance_fixture(cfg)
    result = qa.run_acceptance(cfg, acceptance, journal=journal, overwrite=args.overwrite,
                               name=args.name, approve=args.approve,
                               approval_token=args.approval_token,
                               approved_by=args.approved_by, actor=args.actor)
    payload = envelope("acceptance", result, started=started,
                       warnings=result.get("warnings", []),
                       artifacts=result.get("artifacts", []))
    code = emit(payload, args)
    return nvcore.EXIT_OK if result.get("passed") else nvcore.EXIT_FAIL


# --------------------------------------------------------------------------- #
# Operations: doctor, offline, dependencies, journal, benchmark, license, manifest
# --------------------------------------------------------------------------- #

def cmd_doctor(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    ops = nvcore.load_sibling("tools-nv-ops.py", "nvops")
    report = ops.diagnose(cfg)
    payload = envelope("doctor", report, started=started,
                       warnings=report.get("warnings", []))
    code = emit(payload, args)
    return code if report.get("healthy", True) else nvcore.EXIT_FAIL


def cmd_offline_check(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    probe = nvcore.offline_probe(cfg, timeout=args.timeout)
    guarded = False
    guard_error = None
    with nvcore.offline_guard(True):
        try:
            import socket
            socket.create_connection(("127.0.0.1", 9), timeout=0.2)
            guarded = False
        except nvcore.NetworkBlockedError:
            guarded = True
        except OSError as exc:
            guard_error = str(exc)
    data = {
        "summary": "offline guard %s; policy offline=%s; environment network reachable=%s" % (
            "active" if guarded else "FAILED", cfg.offline, probe["network_reachable"]),
        "policy_offline": cfg.offline,
        "guard_blocks_sockets": guarded,
        "guard_error": guard_error,
        "probe": probe,
        "guarded_operations": ["synthesize", "preset-build", "batch", "acceptance"],
        "note": "fetch-model is the only command allowed to use the network and it "
                "refuses to run unless you approve the plan",
    }
    return emit(envelope("offline-check", data, started=started,
                         warnings=[] if guarded else ["offline guard did not block a socket"]), args)


def cmd_dependencies(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    rows = nvcore.dependency_report()
    required = {"numpy", "onnxruntime"}
    missing_required = [r["module"] for r in rows if r["module"] in required and not r["available"]]
    data = {
        "summary": "%d of %d optional packages available" % (
            sum(1 for r in rows if r["available"]), len(rows)),
        "python": sys.version.split()[0],
        "packages": rows,
        "required_for_synthesis": sorted(required),
        "missing_required": missing_required,
        "install_command": "pip install numpy onnxruntime lameenc soundfile",
        "offline_install": "pip install --no-index --find-links <wheelhouse> numpy onnxruntime",
    }
    warnings = []
    if missing_required:
        warnings.append("missing required packages: %s" % ", ".join(missing_required))
    return emit(envelope("dependencies", data, started=started, warnings=warnings), args)


def cmd_journal(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    journal = nvcore.build_journal(cfg, actor=args.actor)
    recovered = journal.recover() if args.recover else {"interrupted": False}
    rows = journal.tail(args.tail, kinds=args.kind)
    data = {
        "summary": "%d journal entries shown (recovered: %s)" % (
            len(rows), recovered.get("interrupted")),
        "journal_file": cfg.rel(journal.path),
        "summary_counts": journal.summary(),
        "recovery": recovered,
        "entries": rows,
    }
    return emit(envelope("journal", data, started=started), args)


def cmd_benchmark(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    if not args.voice:
        registry, _, _ = nvcore.load_registry(cfg)
        args.voice = default_voice_id(cfg, registry)
    ops = nvcore.load_sibling("tools-nv-ops.py", "nvops")
    report = ops.benchmark(cfg, voice_id=args.voice, runs=args.runs, text=args.text)
    return emit(envelope("benchmark", report, started=started,
                         warnings=report.get("warnings", [])), args)


def cmd_license_audit(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    policy, policy_rel = nvcore.load_policy(cfg)
    targets = [args.voice] if args.voice else sorted(by_id)
    rows = []
    for voice_id in targets:
        entry = by_id.get(voice_id)
        if entry is None:
            raise nvcore.InputError("unknown voice %r" % voice_id,
                                    hint="run `list-voices` for registry ids")
        decisions = {}
        for use in ("personal_use", "commercial_release", "public_redistribution",
                    "voice_cloning"):
            decisions[use] = nvcore.evaluate_rights(
                entry, policy, use=use,
                redistributing=use in ("public_redistribution", "model_redistribution"))
        rows.append({"voice": voice_id, "license_id": entry.get("license_id"),
                     "license_url": (policy.get("licenses", {}).get(entry.get("license_id"), {})
                                     or {}).get("url"),
                     "source": entry.get("source"), "decisions": decisions,
                     "obligations": sorted({o for d in decisions.values()
                                            for o in d["obligations"]})})
    out_rel = args.out or "%s/license-audit.json" % cfg.get("paths.evidence_dir")
    cfg.ensure_dir("paths.evidence_dir")
    nvcore.atomic_write_json(cfg.resolve(out_rel), {"generated_at": nvcore.utc_now(),
                                                    "policy": policy_rel,
                                                    "registry": registry_rel,
                                                    "audit": rows}, overwrite=True)
    data = {"summary": "%d voices audited against %s" % (len(rows), policy_rel),
            "registry": registry_rel, "policy": policy_rel, "audit": rows,
            "output": out_rel}
    return emit(envelope("license-audit", data, started=started,
                         artifacts=[artifact(out_rel, "license-audit")]), args)


def cmd_manifest(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    ops = nvcore.load_sibling("tools-nv-ops.py", "nvops")
    report = ops.build_manifest(cfg, directory=args.directory, name=args.name)
    return emit(envelope("manifest", report, started=started,
                         artifacts=report.get("artifacts", [])), args)


def cmd_package(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    journal = nvcore.build_journal(cfg, actor=args.actor)
    ops = nvcore.load_sibling("tools-nv-ops.py", "nvops")
    plan = ops.package_plan(cfg, source=args.source, name=args.name, include_captions=args.captions)
    token = nvcore.plan_token(plan)
    if args.plan:
        data = {"summary": "dry run: package plan only", "plan": plan, "plan_token": token}
        return emit(envelope("package", data, started=started, approval_required=True), args)
    nvcore.require_approval(cfg, action="package-release",
                            approval_flag=args.approve, approval_token=args.approval_token,
                            approved_by=args.approved_by,
                            policy_key="safety.require_approval_for_release", journal=journal)
    if args.approval_token and args.approval_token != token:
        raise nvcore.ApprovalError("approval token does not match the current package plan",
                                   hint="re-run --plan and approve that exact token")
    result = ops.package_build(cfg, plan=plan, journal=journal)
    return emit(envelope("package", result, started=started,
                         artifacts=result.get("artifacts", [])), args)


def cmd_fetch_model(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    journal = nvcore.build_journal(cfg, actor=args.actor)
    ops = nvcore.load_sibling("tools-nv-ops.py", "nvops")
    registry, by_id, registry_rel = nvcore.load_registry(cfg)
    entry = by_id.get(args.voice)
    if entry is None:
        raise nvcore.InputError("unknown voice %r" % args.voice,
                                hint="run `list-voices` for registry ids")
    if getattr(args, "source_dir", None):
        # local intake: no network, no approval gate, still digest verified
        result = ops.local_intake(cfg, entry, source_dir=args.source_dir, journal=journal)
        return emit(envelope("fetch-model", result, started=started,
                             artifacts=result.get("artifacts", [])), args)
    plan = ops.fetch_plan(cfg, entry)
    token = nvcore.plan_token(plan)
    if args.plan or not args.approve:
        data = {"summary": "network step pending approval: %s" % entry.get("id"),
                "plan": plan, "plan_token": token,
                "approval_required": True,
                "note": "fetch-model is the only command that touches the network; it "
                        "downloads the exact pinned files and verifies digests"}
        payload = envelope("fetch-model", data, started=started, approval_required=True)
        return emit(payload, args)
    nvcore.require_approval(cfg, action="download-model",
                            approval_flag=args.approve, approval_token=args.approval_token,
                            approved_by=args.approved_by, journal=journal)
    if args.approval_token != token:
        raise nvcore.ApprovalError("approval token does not match the current fetch plan",
                                   hint="re-run `fetch-model --voice %s --plan`" % args.voice)
    result = ops.fetch_model(cfg, entry, plan=plan, journal=journal)
    return emit(envelope("fetch-model", result, started=started,
                         artifacts=result.get("artifacts", [])), args)


def cmd_explain(args):
    started = time.monotonic()
    topic = (args.topic or "").strip()
    catalogue = {
        "exit-codes": {str(k): v for k, v in nvcore.EXIT_NAMES.items()},
        "failures": {
            "invalid-input": "A path, value or text failed validation before work began. "
                             "Read `errors[0].hint`; paths must stay inside the skill root.",
            "missing-dependency": "An optional package is absent. Run `dependencies` and "
                                  "install exactly the listed package.",
            "approval-required": "The action is destructive or publishing. Run with --plan, "
                                 "review it, then re-run with --approve --approval-token.",
            "policy-blocked": "The rights gate blocked the requested use for this voice "
                              "licence. Run `license-audit` to see permitted uses.",
            "cancelled": "The run was cancelled by a cancel file, deadline, or Ctrl-C. "
                         "No partial artifact replaces a previous good one.",
            "checksum": "The model file does not match the registry digest. Reinstall it "
                        "from the pinned source; do not synthesize from it.",
            "unknown-phoneme": "The voice phoneme map does not cover a phoneme in the text. "
                               "Use a lexicon entry, simplify the word, or change voice.",
        },
        "commands": {name: summary for name, _, summary in COMMAND_TABLE},
        "engines": {
            engine.ENGINE_ONNX: "CPU ONNX Runtime VITS inference (preferred)",
            engine.ENGINE_PIPER: "piper-tts reference adapter (cross-check)",
            engine.ENGINE_FALLBACK: "clearly labelled degraded tone sketch for pipeline tests",
        },
    }
    if topic not in catalogue:
        raise nvcore.InputError("unknown topic %r" % topic,
                                hint="topics: %s" % ", ".join(sorted(catalogue)))
    data = {"summary": "explain %s" % topic, "topic": topic, "explanation": catalogue[topic]}
    return emit(envelope("explain", data, started=started), args)


# --------------------------------------------------------------------------- #
# Command table and parser
# --------------------------------------------------------------------------- #

def cmd_prepare_text(args):
    started = time.monotonic()
    cfg = load_config_for(args)
    registry, _, _ = nvcore.load_registry(cfg)
    asset = None
    voice_note = None
    try:
        asset = engine.resolve_voice(cfg, args.voice, speaker=args.speaker, registry=registry)
    except nvcore.NVError as exc:
        voice_note = "voice not resolved (%s); using the default espeak voice" % exc.message
    lexicon, _, lexicon_rel = nvcore.load_lexicon(cfg)
    text = read_text_input(args, cfg, args.text)
    locale = args.locale or (asset.config.language if asset else "en-US") or "en-US"
    prepared = textprep.prepare_text(
        text, cfg=cfg, lexicon=lexicon, locale=locale, chunk_chars=args.chunk_chars,
        script_guard=str(cfg.get("execution.script_guard", "block")))
    phoneme_report = None
    if asset is not None:
        phonemizer = engine.Phonemizer(espeak_voice=asset.config.espeak_voice)
        probes = phonemizer.probe()
        warnings = []
        try:
            sentences, ph_warnings = phonemizer.phonemize(prepared["text"])
            warnings.extend(ph_warnings)
            coverage = asset.config.mapper.check([p for s in sentences for p in s])
            phoneme_report = {"backend": phonemizer.backend, "probes": probes,
                              "sentences": ["".join(s) for s in sentences],
                              "coverage": coverage}
        except nvcore.NVError as exc:
            phoneme_report = {"error": exc.to_dict(), "probes": probes}
    else:
        warnings = []
    data = {
        "summary": "%d chars, %d words, %d chunk(s), %d rewrite(s)" % (
            prepared["analysis"]["chars"], prepared["analysis"]["words"],
            len(prepared["chunks"]), len(prepared["rewrites"])),
        "text": prepared["text"],
        "chunks": prepared["chunks"],
        "directions": prepared["directions"],
        "rewrites": prepared["rewrites"],
        "analysis": prepared["analysis"],
        "phonemes": phoneme_report,
        "lexicon": lexicon_rel,
        "locale": locale,
        "voice": asset.describe() if asset else None,
        "voice_note": voice_note,
    }
    return emit(envelope("prepare-text", data, started=started,
                         warnings=warnings + (prepared["warnings"] or [])), args)


COMMAND_TABLE = [
    ("list-tools", cmd_list_tools, "list every command, category mapping, tools and assets"),
    ("list-categories", cmd_list_categories, "list the 18 unified capability categories"),
    ("list-voices", cmd_list_voices, "registry voices with install state and rights status"),
    ("list-presets", cmd_list_presets, "expressive delivery presets and their scales"),
    ("help", cmd_help, "detailed help for one command"),
    ("fetch-model", cmd_fetch_model, "download a pinned voice pair after plan approval"),
    ("inspect-model", cmd_inspect_model, "ONNX graph, opset, interface and footprint"),
    ("compatibility", cmd_compatibility, "model/voice/tokenizer/runtime compatibility matrix"),
    ("prepare-text", cmd_prepare_text, "normalise text and show the pronunciation plan"),
    ("synthesize", cmd_synthesize, "text to mastered WAV/MP3 with evidence"),
    ("preset-build", cmd_preset_build, "render one sentence under several presets"),
    ("batch", cmd_batch, "synthesize a JSON manifest of clips"),
    ("qa", cmd_qa, "measurable acceptance checks on produced audio"),
    ("compare", cmd_compare, "compare two audio files"),
    ("evidence", cmd_evidence, "combined evidence bundle for an audio file"),
    ("acceptance", cmd_acceptance, "run the video greeting acceptance scenario"),
    ("doctor", cmd_doctor, "dependency, CPU, memory, asset and offline health"),
    ("offline-check", cmd_offline_check, "prove the offline guard blocks sockets"),
    ("dependencies", cmd_dependencies, "third-party package availability"),
    ("journal", cmd_journal, "audit journal tail and stale intent recovery"),
    ("benchmark", cmd_benchmark, "load and inference timing on this CPU"),
    ("license-audit", cmd_license_audit, "rights decision per voice and use"),
    ("manifest", cmd_manifest, "versioned artifact manifest with checksums"),
    ("package", cmd_package, "approved deliverable folder with evidence and captions"),
    ("explain", cmd_explain, "explain an exit code, failure or command"),
]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tools-nv-voice.py",
        description="Offline Neural Voice Skill Tool: CPU-only, air-gapped, rights-aware "
                    "neural text-to-speech with measurable QA.",
        epilog="Run `list-tools` for the command inventory, `list-categories` for the "
               "18 unified capability categories, or `help <command>` for details.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version="offline-neural-voice %s" % VERSION)
    add_global(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    def sub(name, help_text, func):
        child = subparsers.add_parser(name, help=help_text, description=help_text)
        child = add_global(child)
        child.set_defaults(func=func, command=name)
        return child

    sub("list-tools", "list every command and capability mapping", cmd_list_tools)
    sub("list-categories", "list the unified capability categories", cmd_list_categories)
    p = sub("list-voices", "registry voices, install state and rights status", cmd_list_voices)
    p.add_argument("--voice", help="mark one voice id as selected")
    p.add_argument("--use", default="personal_use",
                   choices=["personal_use", "commercial_release", "public_redistribution",
                            "voice_cloning"],
                   help="rights context used for the listed decision")
    p.add_argument("--redistribute-use", action="store_true",
                   help="also require redistribution permission")
    p.add_argument("--details", action="store_true", help="include installed voice config facts")
    sub("list-presets", "expressive delivery presets", cmd_list_presets)
    p = sub("help", "detailed help for one command", cmd_help)
    p.add_argument("command", help="command name, for example synthesize")

    p = sub("fetch-model", "download a pinned voice pair (network, approval required)",
            cmd_fetch_model)
    p.add_argument("--voice", required=True, help="registry voice id")
    p.add_argument("--plan", action="store_true", help="print the download plan and stop")
    p.add_argument("--source-dir", help="import the pinned files from a local folder inside "
                                        "the skill root instead of downloading (air-gapped)")
    add_approval(p, "the download")

    p = sub("inspect-model", "inspect an ONNX model and its voice configuration",
            cmd_inspect_model)
    p.add_argument("--voice", help="registry voice id to inspect")
    p.add_argument("--model", help="explicit .onnx path relative to the skill root")
    p.add_argument("--voice-config", help="explicit .onnx.json path")
    p.add_argument("--speaker", help="speaker name or index for multi-speaker voices")

    sub("compatibility", "compatibility matrix across registry voices", cmd_compatibility)

    p = sub("prepare-text", "normalise text and show the pronunciation plan",
            cmd_prepare_text)
    p.add_argument("--text", help="text to prepare")
    p.add_argument("--text-file", help="file with text (relative to the skill root)")
    p.add_argument("--voice", help="voice id used to validate phoneme coverage")
    p.add_argument("--speaker", help="speaker name or index")
    p.add_argument("--locale", help="locale tag such as en-US or en-GB")
    p.add_argument("--chunk-chars", type=int, default=240, help="chunk size for synthesis")

    p = sub("synthesize", "synthesize text to a mastered WAV (and optional MP3)",
            cmd_synthesize)
    p.add_argument("--text", help="text to synthesize")
    p.add_argument("--text-file", help="file with text (relative to the skill root)")
    p.add_argument("--voice", help="registry voice id or installed model id")
    p.add_argument("--speaker", help="speaker name or index")
    p.add_argument("--preset", help="delivery preset id (default from presets asset)")
    p.add_argument("--name", help="output basename (default derived from the text)")
    p.add_argument("--engine", choices=["auto", engine.ENGINE_ONNX, engine.ENGINE_PIPER,
                                        engine.ENGINE_FALLBACK], default="auto",
                   help="engine to use (default auto)")
    p.add_argument("--seed", type=int, help="sampler seed for reproducible takes")
    p.add_argument("--locale", help="locale tag used for text normalisation")
    p.add_argument("--use", default="personal_use",
                   choices=["personal_use", "commercial_release", "public_redistribution",
                            "voice_cloning"], help="rights context")
    p.add_argument("--redistributing", action="store_true",
                   help="the audio will be redistributed with a model or dataset")
    p.add_argument("--consent-record", help="consent record id for cloned voices")
    p.add_argument("--consent-by", help="who affirmed the consent record")
    p.add_argument("--mp3", action="store_true", help="also export a verified MP3")
    p.add_argument("--captions", action="store_true", help="write SRT/VTT/TXT captions")
    p.add_argument("--target-lufs", type=float, help="override the loudness target")
    p.add_argument("--cancel-file", help="cancel when this relative path appears")
    p.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    p.add_argument("--plan", action="store_true", help="print the plan and stop")
    add_approval(p, "synthesis under rights review")

    p = sub("preset-build", "render one sentence under several presets", cmd_preset_build)
    p.add_argument("--text", default="Greetings, friends! Welcome to my video.")
    p.add_argument("--text-file", help="file with text")
    p.add_argument("--voice", help="registry voice id")
    p.add_argument("--speaker", help="speaker name or index")
    p.add_argument("--preset", action="append", help="preset id (repeatable)")
    p.add_argument("--name", help="output basename")
    p.add_argument("--locale", help="locale tag")
    p.add_argument("--use", default="personal_use",
                   choices=["personal_use", "commercial_release", "public_redistribution",
                            "voice_cloning"])
    p.add_argument("--engine", default="auto",
                   choices=["auto", engine.ENGINE_ONNX, engine.ENGINE_PIPER,
                            engine.ENGINE_FALLBACK])

    p = sub("batch", "synthesize a JSON batch plan", cmd_batch)
    p.add_argument("--plan-file", required=True, help="batch plan JSON (relative path)")
    p.add_argument("--voice", help="default voice id")
    p.add_argument("--preset", help="default preset id")
    p.add_argument("--name", help="output basename prefix")
    p.add_argument("--mp3", action="store_true", help="also export MP3 for each clip")
    p.add_argument("--overwrite", action="store_true")
    add_approval(p, "batch synthesis")

    p = sub("qa", "measurable quality checks on an audio file", cmd_qa)
    p.add_argument("--audio", required=True, help="WAV or MP3 path (relative)")
    p.add_argument("--expect-text", help="expected transcript for comparison")
    p.add_argument("--transcript", action="store_true",
                   help="run local ASR (faster-whisper) transcript comparison")
    p.add_argument("--asr-model", default=None, help="ASR model name, default from config")
    p.add_argument("--min-seconds", type=float, default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--locale", help="locale for transcript normalisation")

    p = sub("compare", "compare two audio files", cmd_compare)
    p.add_argument("--file-a", required=True, help="baseline audio (relative)")
    p.add_argument("--file-b", required=True, help="candidate audio (relative)")

    p = sub("evidence", "combined evidence bundle for one audio file", cmd_evidence)
    p.add_argument("--audio", required=True)
    p.add_argument("--expect-text", help="expected transcript")
    p.add_argument("--transcript", action="store_true", help="include ASR comparison")
    p.add_argument("--min-seconds", type=float, default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--out", help="evidence JSON output path")

    p = sub("acceptance", "run the video greeting acceptance scenario", cmd_acceptance)
    p.add_argument("--name", help="output basename")
    p.add_argument("--overwrite", action="store_true")
    add_approval(p, "the acceptance run")

    sub("doctor", "dependency, CPU, memory, asset and offline health", cmd_doctor)
    p = sub("offline-check", "prove the offline guard blocks sockets", cmd_offline_check)
    p.add_argument("--timeout", type=float, default=2.0, help="probe timeout seconds")
    sub("dependencies", "third-party package availability", cmd_dependencies)

    p = sub("journal", "audit journal tail and recovery", cmd_journal)
    p.add_argument("--tail", type=int, default=20, help="number of entries")
    p.add_argument("--kind", action="append", help="filter by entry kind (repeatable)")
    p.add_argument("--recover", action="store_true", help="clear a stale intent and report")

    p = sub("benchmark", "measure load and inference timing", cmd_benchmark)
    p.add_argument("--voice", help="registry voice id")
    p.add_argument("--runs", type=int, default=3, help="timed runs per stage")
    p.add_argument("--text", default="Greetings, friends! Welcome to my video.")

    p = sub("license-audit", "rights decisions per voice and use", cmd_license_audit)
    p.add_argument("--voice", help="single voice id (default: all)")
    p.add_argument("--out", help="audit output path")

    p = sub("manifest", "versioned artifact manifest with checksums", cmd_manifest)
    p.add_argument("--directory", default="output", help="directory to manifest (relative)")
    p.add_argument("--name", help="manifest basename")

    p = sub("package", "approved deliverable folder", cmd_package)
    p.add_argument("--source", default="output", help="directory to package (relative)")
    p.add_argument("--name", help="package folder name")
    p.add_argument("--captions", action="store_true", help="include captions if present")
    p.add_argument("--plan", action="store_true", help="print the package plan and stop")
    add_approval(p, "the release package")

    p = sub("explain", "explain an exit code, failure, engine or command", cmd_explain)
    p.add_argument("topic", nargs="?", default="commands", help="topic to explain")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return nvcore.EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return nvcore.EXIT_USAGE
    return nvcore.run_cli(lambda _argv: func(args), tool_name=TOOL, argv=argv,
                          command=getattr(args, "command", None))


if __name__ == "__main__":
    sys.exit(main())
