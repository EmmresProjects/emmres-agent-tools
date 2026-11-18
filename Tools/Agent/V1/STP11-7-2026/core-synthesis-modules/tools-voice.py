#!/usr/bin/env python3
"""tools-voice.py — primary entry point for the core-synthesis-modules Skill Tool.

Unified Skill Tool for catalog category "Core Synthesis Scripts and Modules"
(Offline Neural Voice System — Required and Missing Items Skill Catalog,
November 2026). This CLI implements every capability of the category:

  synthesize       synthesize.py builder            — end-to-end text -> WAV
  model-loader     model_loader.py builder         — model resolution + integrity
  tokenize         tokenizer.py builder            — vocabulary encode/decode
  phonemize        phonemizer.py builder           — G2P + dictionary rules
  presets          voice_presets.py builder        — voice preset management
  chunk            sentence_chunker.py builder     — long-text chunking
  pronunciation    pronunciation.py builder        — overrides/acronyms
  providers        provider_router.py builder      — routing, health, fallback
  audio            Audio Pipeline Module           — WAV I/O + mastering
  config           Configuration Loader            — config load/validate/merge
  errors           Structured Error Module         — error registry
  contract-test    Core Module Contract Tester     — module contract tests
  acceptance       Core Synthesis Acceptance Gate  — full offline acceptance run

Run `python3 tools-voice.py capabilities` for the capability list and
`python3 tools-voice.py help` for command help.

Exit codes: 0 success, 1 internal, 2 usage, 3 input/config validation,
4 provider failure, 5 audio validation failure, 6 acceptance failure,
7 unsupported/approval required.
"""

import argparse
import importlib.util as _ilu
import json
import os
import sys as _sys


def _load_sibling(name):
    """Load a sibling tools-*.py module by file (filenames contain hyphens,
    so plain `import` syntax is not possible). Results are cached in
    sys.modules under a private key."""
    key = "voice-skill-" + name
    mod = _sys.modules.get(key)
    if mod is None:
        here = os.path.dirname(os.path.abspath(__file__))
        spec = _ilu.spec_from_file_location(key, os.path.join(here, name + ".py"))
        mod = _ilu.module_from_spec(spec)
        _sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


_audio = _load_sibling("tools-audio")
_core = _load_sibling("tools-core")
_providers = _load_sibling("tools-providers")

integrated_loudness = _audio.integrated_loudness
master = _audio.master
peak_dbfs = _audio.peak_dbfs
read_wav = _audio.read_wav
validate_audio = _audio.validate_audio
wav_header_info = _audio.wav_header_info
write_wav = _audio.write_wav
ERROR_REGISTRY = _core.ERROR_REGISTRY
validate_config = _core.validate_config
Phonemizer = _core.Phonemizer
ModelLoader = _core.ModelLoader
EXIT_CODES = _core.EXIT_CODES
SkillToolError = _core.SkillToolError
Tokenizer = _core.Tokenizer
VoicePresets = _core.VoicePresets
chunk_text = _core.chunk_text
load_config = _core.load_config
plan_utterance = _core.plan_utterance
sha256_file = _core.sha256_file
sha256_text = _core.sha256_text
skill_root = _core.skill_root
ProviderRouter = _providers.ProviderRouter

try:
    _contract = _load_sibling("tools-contract")
    acceptance_gate = _contract.acceptance_gate
    run_all = _contract.run_all
except Exception:  # pragma: no cover — contract module always present in delivery
    acceptance_gate = None
    run_all = None


CAPABILITIES = [
    ("synthesize", "synthesize.py Builder", "End-to-end synthesis: text -> prosody plan -> provider -> mastered WAV",
     "python3 tools-voice.py synthesize --text 'Greetings, friends! Welcome to my video.'"),
    ("model-loader", "model_loader.py Builder", "Resolve model assets, verify checksum integrity, report capabilities",
     "python3 tools-voice.py model-loader"),
    ("tokenize", "tokenizer.py Builder", "Encode/decode text with the local vocabulary",
     "python3 tools-voice.py tokenize --text 'Hello world'"),
    ("phonemize", "phonemizer.py Builder", "Convert text to phonemes; apply pronunciation dictionaries",
     "python3 tools-voice.py phonemize --text 'Hello offline neural voice'"),
    ("presets", "voice_presets.py Builder", "List, show, and validate voice presets",
     "python3 tools-voice.py presets --list"),
    ("chunk", "sentence_chunker.py Builder", "Chunk long text at sentence/word boundaries",
     "python3 tools-voice.py chunk --text 'A long paragraph...' --max-chars 200"),
    ("pronunciation", "pronunciation.py Builder", "Apply pronunciation overrides and acronym expansion",
     "python3 tools-voice.py pronunciation --text 'NASA and TTS work offline'"),
    ("providers", "provider_router.py Builder", "List providers, health checks, routing and fallback",
     "python3 tools-voice.py providers --check"),
    ("audio", "Audio Pipeline Module Builder", "WAV I/O, loudness/peak mastering, audio validation",
     "python3 tools-voice.py audio --in greeting.wav --out out.wav --master"),
    ("config", "Configuration Loader Builder", "Load, merge, and validate configuration",
     "python3 tools-voice.py config --check"),
    ("errors", "Structured Error Module Builder", "Structured error registry and exit-code standard",
     "python3 tools-voice.py errors --list"),
    ("contract-test", "Core Module Contract Tester", "Run the per-module contract tests",
     "python3 tools-voice.py contract-test"),
    ("acceptance", "Core Synthesis Acceptance Gate", "Full offline acceptance run for the required greeting sample",
     "python3 tools-voice.py acceptance"),
]


def emit(data, human):
    if human:
        print(data)
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def fail(err, human=True):
    payload = err.to_dict() if isinstance(err, SkillToolError) else {
        "error": {"code": "INTERNAL", "message": str(err),
                  "details": {}, "exit_code": 1}}
    if human:
        print("ERROR %s: %s" % (payload["error"]["code"], payload["error"]["message"]))
        if payload["error"]["details"]:
            print("details: %s" % json.dumps(payload["error"]["details"], ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload["error"]["exit_code"]


def validate_text(text, cfg):
    if text is None or not str(text).strip():
        raise SkillToolError("INPUT_VALIDATION", "text must not be empty")
    text = str(text)
    if len(text) > cfg["limits"]["max_text_chars"]:
        raise SkillToolError("INPUT_VALIDATION",
                             "text exceeds max_text_chars (%d)" % cfg["limits"]["max_text_chars"],
                             {"length": len(text)})
    if any(ord(c) < 9 or (13 < ord(c) < 32) for c in text):
        raise SkillToolError("INPUT_VALIDATION", "text contains control characters")
    return text


def resolve_out_path(args, cfg):
    if args.out:
        return args.out
    digest = sha256_text(args.text if getattr(args, "text", None) else "synthesis")[:8]
    out_dir = cfg.get("paths", {}).get("output_dir", "output")
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(skill_root(), out_dir)
    return os.path.join(out_dir, "synth-%s.wav" % digest)


def check_overwrite(path, args, cfg):
    """Approval gate: never overwrite an existing file without --overwrite."""
    if os.path.exists(path) and cfg["audio"].get("overwrite_requires_approval", True):
        if not getattr(args, "overwrite", False):
            raise SkillToolError("APPROVAL_REQUIRED",
                                 "output file exists; pass --overwrite to replace it: %s" % path)


def cmd_capabilities(args, cfg):
    data = {
        "skill_tool": "core-synthesis-modules",
        "umbrella_category": "Core Synthesis Scripts and Modules",
        "capabilities": [
            {"command": c, "catalog_skill": t, "description": d, "example": e}
            for c, t, d, e in CAPABILITIES
        ],
    }
    emit(data, not args.json)
    return 0


def cmd_config(args, cfg):
    if args.check:
        errors = validate_config(cfg)
        data = {"check": "config", "ok": not errors, "errors": errors,
                "config_path": args.config or "asset-default-config.json"}
        emit(data, not args.json)
        return 0 if not errors else 3
    if args.show:
        emit(cfg, not args.json)
        return 0
    emit({"check": "config", "ok": True,
          "message": "Configuration is valid. Use --show to print it or --check to validate a file.",
          "config_path": args.config or "asset-default-config.json"}, not args.json)
    return 0


def cmd_synthesize(args, cfg):
    text = validate_text(args.text, cfg)
    if args.provider and args.provider not in ("auto", "onnx", "piper", "stub"):
        raise SkillToolError("INPUT_VALIDATION", "unknown provider: %s" % args.provider)

    presets = VoicePresets()
    preset = presets.get(args.preset or cfg["preset_default"])
    plan = plan_utterance(text, preset)

    out_path = resolve_out_path(args, cfg)
    check_overwrite(out_path, args, cfg)

    router = ProviderRouter(cfg)
    deadline = ProviderRouter.make_deadline(cfg["limits"]["synthesis_timeout_seconds"])
    preference = None if args.provider == "auto" else args.provider
    samples, meta, used, warnings = router.synthesize(
        text, plan, preset, cfg["audio"], preference=preference, deadline=deadline)

    # Mastering pipeline (DC removal -> loudness -> peak limit).
    if args.no_master:
        mastered, mrep = samples, None
    else:
        mastered, mrep = master(
            samples, cfg["audio"]["sample_rate"],
            target_lufs=cfg["audio"]["target_lufs"],
            peak_dbfs_target=cfg["audio"]["target_peak_dbfs"],
            normalize=cfg["audio"].get("loudness_normalization", True))

    report = validate_audio(mastered, cfg["audio"]["sample_rate"])
    if not report["ok"]:
        raise SkillToolError("AUDIO_VALIDATION_FAILED", "synthesized audio failed validation",
                             {"failed_checks": [c["name"] for c in report["checks"] if not c["ok"]]})

    write_wav(out_path, mastered, cfg["audio"]["sample_rate"],
              sample_width=cfg["audio"].get("sample_width_bytes", 2))
    meta_path = os.path.splitext(out_path)[0] + ".meta.json"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump({"text": text, "plan": plan, "provider": meta,
                   "mastering": mrep, "audio_validation": report,
                   "sha256": sha256_file(out_path)}, fh, indent=2)

    data = {
        "result": "synthesized",
        "provider_used": used,
        "quality_warnings": warnings,
        "output": out_path,
        "metadata": meta_path,
        "duration_ms": report["duration_ms"],
        "peak_dbfs": report["peak_dbfs"],
        "loudness_lufs": integrated_loudness(mastered, cfg["audio"]["sample_rate"]),
        "sha256": sha256_file(out_path),
        "mastering": mrep,
        "planned_total_ms": plan["total_ms"],
        "word_regions": meta.get("word_regions", []),
    }
    emit(data, not args.json)
    return 0


def cmd_tokenize(args, cfg):
    tok = Tokenizer()
    ids = tok.encode(args.text)
    data = {
        "result": "tokenized",
        "text": args.text,
        "ids": ids,
        "decoded": tok.decode(ids),
        "vocab_size": tok.vocab_size,
    }
    if args.spans:
        ids2, spans = tok.encode_with_spans(args.text)
        data["spans"] = [{"word_index": s[0], "char_position": s[1]} for s in spans]
    emit(data, not args.json)
    return 0


def cmd_phonemize(args, cfg):
    ph = Phonemizer()
    expanded, applied = ph.apply_dictionary(args.text)
    seq = ph.phonemize(args.text)
    data = {
        "result": "phonemized",
        "text": args.text,
        "expanded_text": expanded,
        "applied_rules": applied,
        "phonemes": seq,
    }
    emit(data, not args.json)
    return 0


def cmd_presets(args, cfg):
    presets = VoicePresets()
    if args.list:
        names = presets.list()
        emit({"result": "presets", "presets": [
            {"name": n, "display_name": presets.get(n)["display_name"]} for n in names]},
            not args.json)
        return 0
    if args.name:
        preset = presets.get(args.name)
        errors = presets.validate(preset)
        emit({"result": "preset", "preset": preset, "validation_errors": errors,
              "valid": not errors}, not args.json)
        return 0 if not errors else 3
    emit({"result": "presets", "presets": presets.list(),
          "default": cfg["preset_default"],
          "usage": "pass --list to list, or --name NAME to show/validate a preset"},
        not args.json)
    return 0


def cmd_chunk(args, cfg):
    text = validate_text(args.text, cfg)
    chunks = chunk_text(text, max_chars=args.max_chars, max_tokens=args.max_tokens,
                        overlap_chars=args.overlap)
    data = {"result": "chunked", "chunk_count": len(chunks), "chunks": chunks,
            "max_chars": args.max_chars, "total_chars": len(text)}
    emit(data, not args.json)
    return 0


def cmd_pronunciation(args, cfg):
    ph = Phonemizer()
    expanded, applied = ph.apply_dictionary(args.text)
    emit({"result": "pronunciation", "text": args.text, "expanded_text": expanded,
          "applied_rules": applied}, not args.json)
    return 0


def cmd_model_loader(args, cfg):
    loader = ModelLoader(cfg)
    if args.checksum and args.provider:
        info = loader.resolve(args.provider)
        emit({"result": "model-integrity", **loader.verify(info)}, not args.json)
        return 0 if loader.verify(info)["ok"] else 4
    caps = loader.capabilities()
    emit({"result": "model-loader", "capabilities": caps}, not args.json)
    return 0


def cmd_providers(args, cfg):
    router = ProviderRouter(cfg)
    health = router.health()
    if args.check:
        data = {"result": "provider-health", "health": health,
                "routing_order": router.order}
        emit(data, not args.json)
        return 0 if any(h.get("available") for h in health.values()) else 4
    emit({"result": "providers", "providers": health,
          "routing_order": router.order,
          "note": "Providers are local-only; the router falls back in order."},
        not args.json)
    return 0


def cmd_audio(args, cfg):
    if not os.path.exists(args.in_path):
        raise SkillToolError("AUDIO_INVALID", "input file not found: %s" % args.in_path)
    header = wav_header_info(args.in_path)
    if not header["valid"]:
        raise SkillToolError("AUDIO_INVALID", "invalid WAV header",
                             {"error": header.get("error")})
    data = read_wav(args.in_path)
    samples = data["samples"]
    fs = data["sample_rate"]

    if args.check:
        report = validate_audio(samples, fs)
        emit({"result": "audio-check", "header": header, **report}, not args.json)
        return 0 if report["ok"] else 5

    if args.master:
        mastered, mrep = master(
            samples, fs,
            target_lufs=args.target_lufs if args.target_lufs is not None else cfg["audio"]["target_lufs"],
            peak_dbfs_target=args.peak_dbfs if args.peak_dbfs is not None else cfg["audio"]["target_peak_dbfs"],
            normalize=cfg["audio"].get("loudness_normalization", True))
        out_path = args.out
        check_overwrite(out_path, args, cfg)
        write_wav(out_path, mastered, fs,
                  sample_width=cfg["audio"].get("sample_width_bytes", 2))
        report = validate_audio(mastered, fs)
        emit({"result": "audio-mastered", "output": out_path, "mastering": mrep,
              "audio_validation": report}, not args.json)
        return 0

    emit({"result": "audio-info", "header": header,
          "duration_ms": data["duration_ms"],
          "peak_dbfs": round(peak_dbfs(samples), 2),
          "loudness_lufs": round(integrated_loudness(samples, fs), 2),
          "usage": "pass --check to validate, or --master to master and write a new WAV"},
        not args.json)
    return 0


def cmd_errors(args, cfg):
    if args.code:
        info = ERROR_REGISTRY.get(args.code)
        if not info:
            raise SkillToolError("INPUT_VALIDATION", "unknown error code: %s" % args.code,
                                 {"known_codes": sorted(ERROR_REGISTRY.keys())})
        emit({"result": "error-info", "code": args.code, "exit_code": EXIT_CODES.get(args.code),
              **info}, not args.json)
        return 0
    emit({"result": "error-registry", "exit_code_standard": EXIT_CODES,
          "codes": ERROR_REGISTRY}, not args.json)
    return 0


def cmd_contract_test(args, cfg):
    if run_all is None:
        raise SkillToolError("INTERNAL", "contract module unavailable")
    summary = run_all(verbose=args.verbose)
    emit(summary, not args.json)
    return 0 if summary["ok"] else 1


def cmd_acceptance(args, cfg):
    if acceptance_gate is None:
        raise SkillToolError("INTERNAL", "contract module unavailable")
    report = acceptance_gate(cfg, out_dir=args.out_dir, preference=args.provider)
    emit(report, not args.json)
    return 0 if report["passed"] else 6


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable JSON output")

    parser = argparse.ArgumentParser(
        prog="tools-voice.py",
        description="core-synthesis-modules Skill Tool — offline neural voice core synthesis. "
                    "Run 'capabilities' for the full capability list.")
    parser.add_argument("--config", help="path to a user config JSON (merged over defaults)")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("capabilities", parents=[common], help="list all capabilities of this Skill Tool")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("config", parents=[common], help="load/validate configuration")
    p.add_argument("--check", action="store_true", help="validate config and exit")
    p.add_argument("--show", action="store_true", help="print the merged config")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("synthesize", parents=[common], help="synthesize text to WAV")
    p.add_argument("--text", required=True, help="text to speak")
    p.add_argument("--provider", choices=["auto", "onnx", "piper", "stub"], default="auto",
                   help="provider preference (default auto = router order)")
    p.add_argument("--preset", help="voice preset name (default from config)")
    p.add_argument("--out", help="output WAV path (default output/synth-<hash>.wav)")
    p.add_argument("--overwrite", action="store_true",
                   help="approval to replace an existing output file")
    p.add_argument("--no-master", action="store_true", help="skip loudness/peak mastering")
    p.set_defaults(func=cmd_synthesize)

    p = sub.add_parser("tokenize", parents=[common], help="encode text with the local vocabulary")
    p.add_argument("--text", required=True)
    p.add_argument("--spans", action="store_true", help="include word/char spans")
    p.set_defaults(func=cmd_tokenize)

    p = sub.add_parser("phonemize", parents=[common], help="convert text to phonemes")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_phonemize)

    p = sub.add_parser("presets", parents=[common], help="manage voice presets")
    p.add_argument("--list", action="store_true", help="list available presets")
    p.add_argument("--name", help="show and validate one preset")
    p.set_defaults(func=cmd_presets)

    p = sub.add_parser("chunk", parents=[common], help="chunk long text")
    p.add_argument("--text", required=True)
    p.add_argument("--max-chars", type=int, default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--overlap", type=int, default=0)
    p.set_defaults(func=cmd_chunk)

    p = sub.add_parser("pronunciation", parents=[common], help="apply pronunciation overrides")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_pronunciation)

    p = sub.add_parser("model-loader", parents=[common], help="inspect model assets and integrity")
    p.add_argument("--provider", choices=["onnx", "piper", "stub"])
    p.add_argument("--checksum", action="store_true", help="verify checksum for one provider")
    p.set_defaults(func=cmd_model_loader)

    p = sub.add_parser("providers", parents=[common], help="list providers and health")
    p.add_argument("--check", action="store_true", help="health check (exit 4 if none available)")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("audio", parents=[common], help="audio pipeline: check or master a WAV")
    p.add_argument("--in", dest="in_path", required=True, help="input WAV")
    p.add_argument("--out", help="output WAV (with --master)")
    p.add_argument("--check", action="store_true", help="validate audio only")
    p.add_argument("--master", action="store_true", help="master loudness/peak and write WAV")
    p.add_argument("--target-lufs", type=float, default=None)
    p.add_argument("--peak-dbfs", type=float, default=None)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_audio)

    p = sub.add_parser("errors", parents=[common], help="structured error registry")
    p.add_argument("--list", action="store_true")
    p.add_argument("--code", help="show details for one error code")
    p.set_defaults(func=cmd_errors)

    p = sub.add_parser("contract-test", parents=[common], help="run module contract tests")
    p.add_argument("--verbose", action="store_true", help="per-test output")
    p.set_defaults(func=cmd_contract_test)

    p = sub.add_parser("acceptance", parents=[common], help="run the core synthesis acceptance gate")
    p.add_argument("--provider", choices=["auto", "onnx", "piper", "stub"])
    p.add_argument("--out-dir", default=None, help="acceptance output directory")
    p.set_defaults(func=cmd_acceptance)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        cfg = load_config(config_path=args.config)
        return args.func(args, cfg)
    except SkillToolError as exc:
        return fail(exc, human=not getattr(args, "json", False))
    except KeyboardInterrupt:
        print("\ncancelled by user", file=_sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover — defensive
        import traceback
        traceback.print_exc()
        return fail(exc, human=not getattr(args, "json", False))


if __name__ == "__main__":
    _sys.exit(main())
