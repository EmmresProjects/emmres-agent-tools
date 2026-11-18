#!/usr/bin/env python3
"""Offline Neural Voice — unified Skill Tool (primary entry point).

CPU-first, air-gapped neural text-to-speech with an en-US friendly male 30s
voice preset, SSML-lite direction, mastering, validation, batch synthesis,
consent gates and full provenance. No network access, no cloud endpoints,
no silent formant fallback.

Run:  python3 tools-offline-voice.py --help
      python3 tools-offline-voice.py capabilities
"""
from __future__ import annotations

import argparse
import sys
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # never litter the skill folder with __pycache__

SKILL_ROOT = Path(__file__).resolve().parent


def _load_sibling(filename: str, module_name: str):
    path = SKILL_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _load_sibling("tools-ov-core.py", "ov_core")
TEXT = _load_sibling("tools-ov-text.py", "ov_text")
SSML = _load_sibling("tools-ov-ssml.py", "ov_ssml")
AUDIO = _load_sibling("tools-ov-audio.py", "ov_audio")
PROVIDERS = _load_sibling("tools-ov-providers.py", "ov_providers")
SAFETY = _load_sibling("tools-ov-safety.py", "ov_safety")

OvError = CORE.OvError
EXIT_OK, EXIT_PARTIAL = CORE.EXIT_OK, CORE.EXIT_PARTIAL
EXIT_USAGE, EXIT_ENGINE = CORE.EXIT_USAGE, CORE.EXIT_ENGINE_UNAVAILABLE
EXIT_SAFETY, EXIT_IO = CORE.EXIT_SAFETY_BLOCKED, CORE.EXIT_IO
EXIT_BATCH, EXIT_DEPENDENCY = CORE.EXIT_BATCH_FAILURES, CORE.EXIT_DEPENDENCY

MAX_BATCH_ITEMS = 200


# ---------- shared helpers ----------

def _print(payload, human: bool):
    if human:
        print(CORE.humanize(payload))
    else:
        print(CORE.json_dump(payload))


def _read_text_arg(text: str | None, text_file: str | None, label: str = "text") -> str:
    if text and text_file:
        raise OvError(f"pass either --{label} or --{label}-file, not both", EXIT_USAGE)
    if text_file:
        p = Path(text_file).expanduser()
        if not p.is_file():
            raise OvError(f"file not found: {p}", EXIT_IO)
        return p.read_text(encoding="utf-8")
    if text is not None:
        return text
    raise OvError(f"missing input: provide --{label} or --{label}-file", EXIT_USAGE)


def _preset_or_default(cfg: dict, preset_name: str | None) -> dict:
    presets = CORE.load_json_asset(CORE.ASSET_PRESETS)["presets"]
    name = preset_name or cfg.get("preset") or "friendly-male-30s"
    if name not in presets:
        raise OvError(f"unknown preset {name!r}; available: {', '.join(sorted(presets))}", EXIT_USAGE)
    preset = dict(presets[name])
    preset["name"] = name
    return preset


def _engine_params(cfg: dict, preset: dict, args) -> dict:
    params = dict(preset.get("engine_params", {}))
    if args.length_scale is not None:
        params["length_scale"] = args.length_scale
    if args.noise_scale is not None:
        params["noise_scale"] = args.noise_scale
    if args.noise_w is not None:
        params["noise_w"] = args.noise_w
    if args.rate_percent is not None:
        rate = 1.0 + args.rate_percent / 100.0
        if rate <= 0:
            raise OvError("--rate-percent produced a non-positive speaking rate", EXIT_USAGE)
        params["length_scale"] = round(1.0 / rate, 4)
    params.setdefault("length_scale", 1.0)
    params.setdefault("noise_scale", 0.667)
    params.setdefault("noise_w", 0.8)
    params.setdefault("sentence_silence", cfg.get("sentence_silence", 0.3))
    return params


def _build_segments(args, cfg: dict, preset: dict, dictionary: dict):
    """Resolve the input into (segments, meta)."""
    meta = {"mode": None, "warnings": [], "source_text": None}
    if args.ssml or args.ssml_file:
        ssml_text = _read_text_arg(args.ssml, args.ssml_file, label="ssml")
        report = SSML.validate_ssml(ssml_text, TEXT)
        if not report["ok"]:
            raise OvError("SSML failed validation: " + "; ".join(report["errors"]),
                          EXIT_USAGE, report)
        meta["mode"] = "ssml"
        meta["warnings"].extend(report["warnings"])
        meta["source_text"] = ssml_text
        segments = []
        for seg in report["segments"]:
            norm = TEXT.normalize_text(seg["text"], dictionary)
            meta["warnings"].extend(f"segment: {w}" for w in norm["warnings"])
            segments.append({"text": norm["normalized"],
                             "rate": seg["rate"],
                             "volume_db": seg["volume_db"],
                             "emphasis": seg["emphasis"],
                             "pause_before_ms": seg["pause_before_ms"]})
        return segments, meta

    raw_text = _read_text_arg(args.text, args.text_file)
    if len(raw_text) > int(cfg.get("max_text_chars", 5000)):
        raise OvError(f"input is {len(raw_text)} chars; limit is {cfg.get('max_text_chars', 5000)}. "
                      "Split with `segment` first or raise max_text_chars.", EXIT_USAGE)
    if args.normalize:
        norm = TEXT.normalize_text(raw_text, dictionary)
        spoken = norm["normalized"]
        meta["warnings"].extend(f"normalize: {w}" for w in norm["warnings"])
    else:
        spoken = raw_text.strip()
    meta["mode"] = "plain"
    meta["source_text"] = raw_text
    max_chunk = int(cfg.get("max_chunk_chars", 600))
    if len(spoken) <= max_chunk:
        return [{"text": spoken, "rate": 1.0, "volume_db": 0.0,
                 "emphasis": False, "pause_before_ms": 0}], meta
    chunks = TEXT.chunk_text(spoken, max_chars=max_chunk)
    pause_ms = int(float(preset["engine_params"].get("sentence_silence", 0.3)) * 1000)
    segments = []
    for i, chunk in enumerate(chunks):
        segments.append({"text": chunk["text"], "rate": 1.0, "volume_db": 0.0,
                         "emphasis": False,
                         "pause_before_ms": pause_ms if i else 0})
    meta["warnings"].append(f"input chunked into {len(chunks)} segments ({max_chunk} chars max)")
    return segments, meta


def _safety_gate(args):
    if not (args.real_person or args.requested_use or args.involves_minor or args.speaker_name):
        return None
    spec = {"real_person": args.real_person, "speaker_name": args.speaker_name,
            "involves_minor": args.involves_minor, "context": args.context or "general",
            "requested_use": args.requested_use or ""}
    directory = SAFETY.consent_dir_from(args.consent_dir)
    decision = SAFETY.check_request(spec, directory)
    if decision["decision"] == "deny":
        raise OvError("safety gate denied this request: " + "; ".join(decision["reasons"]),
                      EXIT_SAFETY, decision)
    if decision["decision"] == "review":
        print(json.dumps({"warning": "sensitive context: human review required before publishing",
                          "reasons": decision["reasons"]}), file=sys.stderr)
    return decision


def _synthesize_core(args, cfg: dict) -> dict:
    dictionary = TEXT.load_pronunciation()
    preset = _preset_or_default(cfg, args.preset)
    params = _engine_params(cfg, preset, args)

    safety = _safety_gate(args)

    segments, meta = _build_segments(args, cfg, preset, dictionary)
    if not segments:
        raise OvError("nothing to speak after processing", EXIT_USAGE)

    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    raw_fd, raw_name = tempfile.mkstemp(prefix="ov-raw-", suffix=".wav",
                                        dir=str(out_path.parent))
    os.close(raw_fd)
    job = {"output_wav": raw_name, "segments": segments,
           "length_scale": params.get("length_scale"),
           "noise_scale": params.get("noise_scale"),
           "noise_w": params.get("noise_w"),
           "speaker_id": args.speaker_id}
    started = time.time()
    try:
        result = PROVIDERS.synthesize(job, cfg)
    except PROVIDERS.ProviderError as exc:
        try:
            os.unlink(raw_name)
        except OSError:
            pass
        raise OvError(exc.message, EXIT_ENGINE, exc.details) from exc

    if args.no_master:
        shutil.move(raw_name, out_path)
        master_report = {"mastered": False}
        with open(out_path, "rb") as fh:
            info_wav = CORE.read_wav(out_path)
        duration_s = info_wav["frames"] / info_wav["rate"] if info_wav["channels"] else 0
    else:
        wav = CORE.read_wav(raw_name)
        mono = CORE.mixdown_mono(wav["samples"], wav["channels"])
        chain = _master_chain(mono, wav["rate"], cfg, args, preset)
        CORE.write_wav(out_path, chain["samples"], chain["rate"], channels=1)
        master_report = chain["report"]
        duration_s = len(chain["samples"]) / chain["rate"]
        try:
            os.unlink(raw_name)
        except OSError:
            pass

    final = CORE.read_wav(out_path)
    final_mono = CORE.mixdown_mono(final["samples"], final["channels"])
    stats = AUDIO.validate_signal(final_mono, final["rate"], final["channels"])

    prov_path = None
    if cfg.get("provenance", True) and not args.no_provenance:
        prov_path = _write_provenance(out_path, meta["source_text"], args, cfg, preset,
                                      params, result, master_report, safety)

    report = {
        "ok": True,
        "output": str(out_path),
        "provenance": prov_path,
        "duration_s": round(duration_s, 3),
        "sample_rate": final["rate"],
        "channels": 1,
        "engine": result.get("engine"),
        "attempts": result.get("attempts"),
        "model_quality": (result.get("quality_warning") or "ok"),
        "preset": preset["name"],
        "mode": meta["mode"],
        "segments": len(segments),
        "peak_dbfs": stats["peak_dbfs"],
        "loudness_lufs": stats["loudness_lufs"],
        "clipped_samples": stats["clipped_samples"],
        "dc_offset": stats["dc_offset"],
        "wall_time_s": round(time.time() - started, 3),
        "mastering": master_report,
        "warnings": meta["warnings"] + ([result["quality_warning"]] if result.get("quality_warning") else []),
    }
    return report


def _master_chain(samples, rate, cfg: dict, args, preset: dict):
    report = {"mastered": True, "steps": []}
    peak_target = float(getattr(args, "peak_dbfs", None) or cfg["peak_dbfs"])
    loudness_target = float(getattr(args, "loudness_target", None) or cfg["loudness_target_lufs"])

    samples, dc = AUDIO.remove_dc(samples)
    report["steps"].append({"step": "remove-dc", "dc_offset_removed": round(dc, 5)})

    if not getattr(args, "no_declick", False):
        samples, fixed = AUDIO.declick(samples)
        report["steps"].append({"step": "declick", "clicks_fixed": fixed})

    if not getattr(args, "no_compress", False):
        samples, comp = AUDIO.compress(samples, rate,
                                       threshold_db=float(cfg.get("compressor", {}).get("threshold_db", -14.0)),
                                       ratio=float(cfg.get("compressor", {}).get("ratio", 3.0)),
                                       attack_ms=float(cfg.get("compressor", {}).get("attack_ms", 5.0)),
                                       release_ms=float(cfg.get("compressor", {}).get("release_ms", 90.0)))
        report["steps"].append({"step": "compress", **comp})

    if not getattr(args, "no_trim", False):
        samples, trimmed = AUDIO.trim_silence(samples, rate,
                                              threshold_db=float(cfg["trim_silence_db"]),
                                              pad_ms=int(cfg["trim_pad_ms"]))
        report["steps"].append({"step": "trim-silence", "trimmed_ms": round(trimmed, 1)})

    samples = AUDIO.apply_fades(samples, rate, int(cfg["fade_ms"]))
    report["steps"].append({"step": "fades", "fade_ms": int(cfg["fade_ms"])})

    samples, loud = AUDIO.normalize_loudness(samples, rate, loudness_target, peak_target)
    report["steps"].append({"step": "loudness", **loud})

    samples, gain_db = AUDIO.soft_clip(samples, peak_target)
    if gain_db:
        report["steps"].append({"step": "peak-limit", "gain_db": round(gain_db, 2)})

    out_rate = int(getattr(args, "sample_rate", None) or rate)
    if out_rate != rate:
        samples = AUDIO.resample_linear(samples, rate, out_rate)
        report["steps"].append({"step": "resample", "from": rate, "to": out_rate,
                                "method": "linear (documented approximation)"})
        rate = out_rate
    report["final"] = {"duration_s": round(len(samples) / rate, 3),
                       "sample_rate": rate}
    return {"samples": samples, "rate": rate, "report": report}


def _write_provenance(out_path: Path, source_text: str, args, cfg: dict,
                      preset: dict, params: dict, result: dict,
                      master_report: dict, safety) -> str:
    model_path = Path(cfg.get("model", CORE.ASSET_MODEL))
    try:
        model_sha = CORE.sha256_file(model_path) if model_path.is_file() else None
    except OSError:
        model_sha = None
    record = {
        "schema": "ov-provenance-v1",
        "created_at": CORE.now_iso(),
        "tool_version": CORE.VERSION,
        "output_file": out_path.name,
        "output_sha256": CORE.sha256_file(out_path),
        "text_sha256": CORE.sha256_text(source_text or ""),
        "text_chars": len(source_text or ""),
        "engine": result.get("engine"),
        "model_file": model_path.name,
        "model_sha256": model_sha,
        "model_config": Path(cfg.get("model_config", CORE.ASSET_MODEL_CONFIG)).name,
        "preset": preset.get("name"),
        "engine_params": params,
        "mastering": master_report,
        "disclosure": cfg.get("disclosure"),
        "safety": (safety or {"decision": "not-requested"}),
        "licenses": {"engine": "piper-tts 1.8.0 (GPL-3.0-or-later, separate component)",
                     "runtime": "onnxruntime (MIT), numpy (BSD-3-Clause et al.)",
                     "voice_model": "en_US-lessac-medium by Rhasspy/Piper voices (MIT tag); see dependencies-notices.md"},
    }
    prov_path = out_path.with_suffix(out_path.suffix + ".provenance.json")
    prov_path.write_text(CORE.json_dump(record), encoding="utf-8")
    return str(prov_path)


# ---------- subcommands ----------

def cmd_capabilities(args, cfg):
    payload = {"ok": True, "tool": "offline-neural-voice", "version": CORE.VERSION,
               "operations": [{"command": name, "description": desc} for name, desc in CORE.OPERATIONS],
               "catalog_categories": CORE.capability_catalog()}
    _print(payload, args.human)
    return EXIT_OK


def cmd_normalize(args, cfg):
    dictionary = TEXT.load_pronunciation()
    raw = _read_text_arg(args.text, args.text_file)
    result = TEXT.normalize_text(raw, dictionary,
                                 verbalize_symbols_flag=not args.no_symbols,
                                 expand_acronyms_flag=not args.no_acronyms,
                                 apply_dictionary_flag=not args.no_dictionary)
    if args.raw:
        print(result["normalized"])
    else:
        _print({"ok": True, **result}, args.human)
    return EXIT_OK


def cmd_segment(args, cfg):
    raw = _read_text_arg(args.text, args.text_file)
    max_chars = args.max_chars or int(cfg.get("max_chunk_chars", 600))
    sentences = TEXT.segment_sentences(raw)
    chunks = TEXT.chunk_text(raw, max_chars=max_chars)
    _print({"ok": True, "sentences": sentences, "sentence_count": len(sentences),
            "chunks": chunks, "chunk_count": len(chunks), "max_chars": max_chars}, args.human)
    return EXIT_OK


def cmd_ssml_check(args, cfg):
    raw = _read_text_arg(args.text, args.text_file, label="ssml")
    report = SSML.validate_ssml(raw, TEXT)
    _print({"ok": report["ok"], **report}, args.human)
    return EXIT_OK if report["ok"] else EXIT_USAGE


def cmd_synthesize(args, cfg):
    report = _synthesize_core(args, cfg)
    _print(report, args.human)
    return EXIT_OK


def cmd_batch(args, cfg):
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_file():
        raise OvError(f"manifest not found: {manifest_path}", EXIT_IO)
    raw = manifest_path.read_text(encoding="utf-8")
    items = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
            items = parsed["items"]
    except json.JSONDecodeError:
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                items.append(json.loads(line))
    if items is None:
        raise OvError("manifest must be a JSON array, an object with an 'items' array, or JSONL",
                      EXIT_USAGE)
    if len(items) > MAX_BATCH_ITEMS:
        raise OvError(f"manifest has {len(items)} items; limit is {MAX_BATCH_ITEMS}", EXIT_USAGE)

    out_dir = Path(args.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    results, failures = [], 0
    started = time.time()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            failures += 1
            results.append({"index": index, "ok": False, "error": "item is not an object"})
            continue
        item_id = str(item.get("id") or f"item-{index:04d}")
        safe_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in item_id)[:80]
        wav_path = out_dir / f"{safe_id}.wav"
        if args.resume and wav_path.is_file() and not args.force:
            results.append({"index": index, "id": item_id, "ok": True, "status": "skipped-existing",
                            "output": str(wav_path)})
            continue
        item_args = argparse.Namespace(**{**vars(args)})
        item_args.text = item.get("text")
        item_args.text_file = item.get("text_file")
        item_args.ssml = item.get("ssml")
        item_args.ssml_file = item.get("ssml_file")
        item_args.preset = item.get("preset") or args.preset
        item_args.output = str(wav_path)
        item_args.no_provenance = getattr(args, "no_provenance", False)
        try:
            if not (item_args.text or item_args.text_file or item_args.ssml or item_args.ssml_file):
                raise OvError("item has no text/text_file/ssml/ssml_file", EXIT_USAGE)
            report = _synthesize_core(item_args, cfg)
            report.update({"index": index, "id": item_id, "status": "synthesized"})
            results.append(report)
            print(f"[batch] {index + 1}/{len(items)} {item_id}: ok", file=sys.stderr)
        except OvError as exc:
            failures += 1
            results.append({"index": index, "id": item_id, "ok": False,
                            "status": "failed", "error": exc.message})
            print(f"[batch] {index + 1}/{len(items)} {item_id}: FAILED — {exc.message}",
                  file=sys.stderr)

    summary = {"ok": failures == 0, "items": len(items), "failures": failures,
               "output_dir": str(out_dir), "wall_time_s": round(time.time() - started, 2),
               "results": results}
    report_path = out_dir / "batch-report.json"
    report_path.write_text(CORE.json_dump(summary), encoding="utf-8")
    summary["report"] = str(report_path)
    _print(summary, args.human)
    return EXIT_OK if failures == 0 else EXIT_BATCH


def cmd_validate(args, cfg):
    wav = CORE.read_wav(args.input)
    mono = CORE.mixdown_mono(wav["samples"], wav["channels"])
    kwargs = {}
    if args.for_greeting:
        acceptance = CORE.load_json_asset(CORE.ASSET_ACCEPTANCE)["required_acceptance"]
        kwargs["duration_min"] = acceptance["duration_min_s"]
        kwargs["duration_max"] = acceptance["duration_max_s"]
        kwargs["loudness_target"] = cfg["loudness_target_lufs"]
        kwargs["peak_dbfs"] = cfg["peak_dbfs"]
    if args.duration_min is not None:
        kwargs["duration_min"] = args.duration_min
    if args.duration_max is not None:
        kwargs["duration_max"] = args.duration_max
    if args.loudness_target is not None:
        kwargs["loudness_target"] = args.loudness_target
    if args.peak_dbfs is not None:
        kwargs["peak_dbfs"] = args.peak_dbfs
    report = AUDIO.validate_signal(mono, wav["rate"], wav["channels"],
                                   frames_declared=wav["frames"], **kwargs)
    report["input"] = str(Path(args.input).expanduser())
    if args.transcript or args.transcript_file:
        transcript = _read_text_arg(args.transcript, args.transcript_file, label="transcript")
        phrase = args.phrase or CORE.load_json_asset(CORE.ASSET_ACCEPTANCE)["required_acceptance"]["text"]
        report["phrase_match"] = TEXT.phrase_match(phrase, transcript)
        if args.reference or args.reference_file:
            reference = _read_text_arg(args.reference, args.reference_file, label="reference")
            report["wer"] = TEXT.word_error_rate(reference, transcript)
    _print({"ok": report["ok"], **report}, args.human)
    return EXIT_OK if report["ok"] else EXIT_USAGE


def cmd_master(args, cfg):
    wav = CORE.read_wav(args.input)
    mono = CORE.mixdown_mono(wav["samples"], wav["channels"])
    chain = _master_chain(mono, wav["rate"], cfg, args, None)
    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    CORE.write_wav(out_path, chain["samples"], chain["rate"], channels=1)
    report = {"ok": True, "input": str(Path(args.input).expanduser()), "output": str(out_path),
              **chain["report"]}
    _print(report, args.human)
    return EXIT_OK


def cmd_wer(args, cfg):
    reference = _read_text_arg(args.reference, args.reference_file, label="reference")
    hypothesis = _read_text_arg(args.hypothesis, args.hypothesis_file, label="hypothesis")
    _print({"ok": True, **TEXT.word_error_rate(reference, hypothesis)}, args.human)
    return EXIT_OK


def cmd_phrase_match(args, cfg):
    phrase = _read_text_arg(args.phrase, args.phrase_file, label="phrase")
    transcript = _read_text_arg(args.transcript, args.transcript_file, label="transcript")
    _print({"ok": True, **TEXT.phrase_match(phrase, transcript)}, args.human)
    return EXIT_OK


def cmd_provenance(args, cfg):
    args.action = args.action or args.action_pos
    if not args.action:
        raise OvError("provenance requires an action: write, verify or show (e.g. provenance verify --input out.wav)", EXIT_USAGE)
    if args.action == "write":
        wav_path = Path(args.wav).expanduser()
        if not wav_path.is_file():
            raise OvError(f"WAV not found: {wav_path}", EXIT_IO)
        source = _read_text_arg(args.text, args.text_file) if (args.text or args.text_file) else ""
        record = {
            "schema": "ov-provenance-v1",
            "created_at": CORE.now_iso(),
            "tool_version": CORE.VERSION,
            "output_file": wav_path.name,
            "output_sha256": CORE.sha256_file(wav_path),
            "text_sha256": CORE.sha256_text(source),
            "text_chars": len(source),
            "engine": args.engine or "unknown",
            "preset": args.preset or cfg.get("preset"),
            "disclosure": cfg.get("disclosure"),
            "licenses": {"engine": "piper-tts 1.8.0 (GPL-3.0-or-later)",
                         "runtime": "onnxruntime (MIT), numpy (BSD-3-Clause et al.)",
                         "voice_model": "en_US-lessac-medium by Rhasspy/Piper voices (MIT tag); see dependencies-notices.md"},
        }
        prov_path = wav_path.with_suffix(wav_path.suffix + ".provenance.json")
        prov_path.write_text(CORE.json_dump(record), encoding="utf-8")
        _print({"ok": True, "provenance": str(prov_path)}, args.human)
        return EXIT_OK

    target = Path(args.input).expanduser()
    prov_path = target if target.suffix == ".json" else target.with_suffix(target.suffix + ".provenance.json")
    if not prov_path.is_file():
        raise OvError(f"provenance sidecar not found: {prov_path}", EXIT_IO)
    record = json.loads(prov_path.read_text(encoding="utf-8"))
    if args.action == "show":
        _print({"ok": True, "provenance": record}, args.human)
        return EXIT_OK
    # verify
    checks, ok = [], True
    wav_name = record.get("output_file")
    wav_candidate = prov_path.parent / wav_name if wav_name else None
    if wav_candidate and wav_candidate.is_file():
        actual = CORE.sha256_file(wav_candidate)
        match = actual == record.get("output_sha256")
        checks.append({"check": "output_sha256", "ok": match})
        ok = ok and match
    else:
        checks.append({"check": "output_sha256", "ok": False, "note": "WAV missing next to sidecar"})
        ok = False
    for key in ("text_sha256", "engine", "disclosure"):
        present = bool(record.get(key))
        checks.append({"check": key, "ok": present})
        ok = ok and present
    _print({"ok": ok, "provenance": str(prov_path), "checks": checks}, args.human)
    return EXIT_OK if ok else EXIT_USAGE


def cmd_consent(args, cfg):
    args.action = args.action or args.action_pos
    if not args.action:
        raise OvError("consent requires an action: check, grant, list or revoke", EXIT_USAGE)
    directory = SAFETY.consent_dir_from(args.consent_dir)
    if args.action == "check":
        if args.spec:
            spec_path = Path(args.spec).expanduser()
            if not spec_path.is_file():
                raise OvError(f"spec file not found: {spec_path}", EXIT_IO)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        else:
            spec = {"real_person": args.real_person, "speaker_name": args.speaker_name,
                    "involves_minor": args.involves_minor, "context": args.context or "general",
                    "requested_use": args.requested_use or ""}
        decision = SAFETY.check_request(spec, directory)
        _print(decision, args.human)
        return EXIT_OK if decision["decision"] == "allow" else (EXIT_SAFETY if decision["decision"] == "deny" else EXIT_OK)
    if args.action == "grant":
        try:
            rec = SAFETY.grant_consent(directory, args.speaker_name, args.grantor,
                                       args.scope, args.expires, args.notes)
        except ValueError as exc:
            raise OvError(str(exc), EXIT_USAGE) from exc
        _print({"ok": True, "record": rec, "stored": str(directory / (rec['id'] + '.json'))}, args.human)
        return EXIT_OK
    if args.action == "list":
        _print({"ok": True, "consent_dir": str(directory), "records": SAFETY.list_consents(directory)}, args.human)
        return EXIT_OK
    if args.action == "revoke":
        if not args.confirm:
            raise OvError("revoke is destructive; re-run with --confirm", EXIT_USAGE)
        if not args.id:
            raise OvError("--id is required for revoke", EXIT_USAGE)
        try:
            rec = SAFETY.revoke_consent(directory, args.id)
        except FileNotFoundError as exc:
            raise OvError(str(exc), EXIT_IO) from exc
        _print({"ok": True, "record": rec}, args.human)
        return EXIT_OK
    raise OvError(f"unknown consent action {args.action!r}", EXIT_USAGE)


def cmd_benchmark(args, cfg):
    acceptance = CORE.load_json_asset(CORE.ASSET_ACCEPTANCE)["required_acceptance"]
    text = args.text or acceptance["text"]
    runs = max(1, min(10, args.runs))
    out_dir = Path(tempfile.mkdtemp(prefix="ov-bench-"))
    results = []
    try:
        for i in range(runs):
            wav_path = out_dir / f"bench-{i}.wav"
            bench_args = argparse.Namespace(**{**vars(args)})
            bench_args.output = str(wav_path)
            bench_args.text = text
            bench_args.text_file = None
            bench_args.ssml = None
            bench_args.ssml_file = None
            bench_args.no_provenance = True
            bench_args.no_trim = False
            t0 = time.time()
            report = _synthesize_core(bench_args, cfg)
            wall = time.time() - t0
            results.append({"run": i + 1, "wall_s": round(wall, 3),
                            "audio_s": report["duration_s"],
                            "rtf": round(wall / report["duration_s"], 3) if report["duration_s"] else None,
                            "loudness_lufs": report["loudness_lufs"],
                            "peak_dbfs": report["peak_dbfs"]})
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    avg_rtf = sum(r["rtf"] for r in results if r["rtf"]) / max(1, len(results))
    _print({"ok": True, "text": text, "runs": len(results), "avg_rtf": round(avg_rtf, 3),
            "results": results}, args.human)
    return EXIT_OK


def cmd_diagnose(args, cfg):
    def mem_total_mb():
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / 1024)
        except OSError:
            pass
        try:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
        except (ValueError, OSError, AttributeError):
            return None

    numpy_ok, onnxruntime_ok, piper_ok, piper_version = None, None, None, None
    try:
        import numpy  # noqa
        numpy_ok = numpy.__version__
    except Exception:
        pass
    try:
        import onnxruntime
        onnxruntime_ok = onnxruntime.__version__
    except Exception:
        pass
    try:
        import piper
        piper_ok = True
        piper_version = getattr(piper, "__version__", "unknown")
    except Exception:
        pass

    probe = PROVIDERS.engine_probe(cfg)
    checksums = CORE.verify_checksum_manifest()
    disk = shutil.disk_usage(SKILL_ROOT)
    report = {
        "ok": True,
        "tool_version": CORE.VERSION,
        "skill_root": str(SKILL_ROOT),
        "python": {"version": platform.python_version(), "executable": sys.executable,
                   "implementation": platform.python_implementation()},
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine()},
        "resources": {"cpu_count": os.cpu_count(), "memory_mb": mem_total_mb(),
                      "disk_free_mb": round(disk.free / (1024 * 1024))},
        "current_interpreter": {"numpy": numpy_ok, "onnxruntime": onnxruntime_ok,
                                "piper_tts": (piper_version if piper_ok else None)},
        "engine_probe": probe,
        "bundled_checksums_ok": checksums["ok"],
        "checksum_files": len(checksums["files"]),
        "setup_command": "python3 tools-ov-setup.py",
        "network": "not used by this tool; setup is offline",
    }
    _print(report, args.human)
    return EXIT_OK if checksums["ok"] else EXIT_DEPENDENCY


def cmd_self_test(args, cfg):
    runner = SKILL_ROOT / "tools-run-tests.py"
    if not runner.is_file():
        raise OvError("tools-run-tests.py missing", EXIT_DEPENDENCY)
    cmd = [sys.executable, str(runner)]
    if args.quick:
        cmd.append("--quick")
    proc = subprocess.run(cmd, capture_output=False, check=False)
    return proc.returncode


def cmd_version(args, cfg):
    model_cfg = Path(cfg.get("model_config", CORE.ASSET_MODEL_CONFIG))
    quality = PROVIDERS.model_quality(model_cfg)
    _print({"ok": True, "tool": "offline-neural-voice", "version": CORE.VERSION,
            "bundled_model": model_cfg.parent.name + "/" + Path(cfg.get("model", CORE.ASSET_MODEL)).name,
            "voice": "en_US-lessac-medium (MIT)", "voice_quality": quality,
            "sample_rate": 22050, "engine": "piper-tts 1.8.0 (ONNX)",
            "license": "see dependencies-notices.md"}, args.human)
    return EXIT_OK


# ---------- argument parsing ----------

def build_parser():
    p = argparse.ArgumentParser(
        prog="tools-offline-voice.py",
        description="Offline Neural Voice — CPU-first, air-gapped neural TTS Skill Tool. "
                    "Run with no arguments for help; see knowledge-readme.md for the handbook.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit codes: 0 ok · 1 partial · 2 usage/validation · 3 engine unavailable · "
               "4 safety gate blocked · 5 I/O · 6 batch failures · 7 dependency/config · 8 voice model acquisition required · 130 interrupted")
    p.add_argument("--version", action="store_true", help="print version and exit")
    p.add_argument("--config", help="path to a JSON config file (or set OV_CONFIG)")
    p.add_argument("--human", action="store_true", help="human-readable output instead of JSON")
    sub = p.add_subparsers(dest="command", metavar="command")

    sub.add_parser("capabilities", help="list all commands and the 32 catalog categories they cover")

    def add_io_args(sp, label="text"):
        sp.add_argument(f"--{label}", help=f"inline {label} string")
        sp.add_argument(f"--{label}-file", help=f"path to a UTF-8 {label} file")

    norm = sub.add_parser("normalize", help="normalize text for TTS (numbers, dates, currency, URLs, acronyms, dictionary)")
    add_io_args(norm, "text")
    norm.add_argument("--raw", action="store_true", help="print only the normalized string")
    norm.add_argument("--no-symbols", action="store_true")
    norm.add_argument("--no-acronyms", action="store_true")
    norm.add_argument("--no-dictionary", action="store_true")

    seg = sub.add_parser("segment", help="sentence segmentation and length-bounded chunking")
    add_io_args(seg, "text")
    seg.add_argument("--max-chars", type=int, help="chunk size limit (default: config max_chunk_chars)")

    ssml_p = sub.add_parser("ssml-check", help="validate SSML-lite and print the synthesis plan")
    ssml_p.add_argument("--text", help="inline SSML string")
    ssml_p.add_argument("--text-file", help="path to SSML file")
    ssml_p.add_argument("--file", dest="text_file", help=argparse.SUPPRESS)
    ssml_p.add_argument("--ssml", dest="text", help=argparse.SUPPRESS)
    ssml_p.add_argument("--ssml-file", dest="text_file", help=argparse.SUPPRESS)

    def add_synth_args(sp):
        sp.add_argument("--text")
        sp.add_argument("--text-file")
        sp.add_argument("--ssml")
        sp.add_argument("--ssml-file")
        sp.add_argument("--preset", help="preset name from asset-voice-presets.json (default: friendly-male-30s)")
        sp.add_argument("--engine", choices=["auto", "piper", "piper-custom"], help="engine selection (default: auto)")
        sp.add_argument("--model", help="path to a Piper-compatible ONNX model (for piper-custom)")
        sp.add_argument("--model-config", help="path to the model's .onnx.json config")
        sp.add_argument("--length-scale", type=float, help="override phoneme length scale (>1 slower)")
        sp.add_argument("--noise-scale", type=float, help="generator noise (expressiveness)")
        sp.add_argument("--noise-w", type=float, help="phoneme width noise (prosody variation)")
        sp.add_argument("--rate-percent", type=float, help="speaking rate delta in percent (positive = faster)")
        sp.add_argument("--speaker-id", type=int, help="speaker index for multi-speaker models")
        sp.add_argument("--loudness-target", type=float, help="integrated loudness target in LUFS (default -14)")
        sp.add_argument("--peak-dbfs", type=float, help="peak ceiling in dBFS (default -1)")
        sp.add_argument("--sample-rate", type=int, help="resample output to this rate (linear interpolation)")
        sp.add_argument("--no-master", action="store_true", help="skip the mastering chain")
        sp.add_argument("--no-trim", action="store_true", help="keep leading/trailing silence")
        sp.add_argument("--no-declick", action="store_true", help="skip de-clicking")
        sp.add_argument("--no-compress", action="store_true", help="skip the crest-factor compressor")
        sp.add_argument("--no-provenance", action="store_true", help="do not write the .provenance.json sidecar")
        sp.add_argument("--no-normalize", dest="normalize", action="store_false", help="skip text normalization (plain-text mode)")
        sp.add_argument("--real-person", action="store_true", help="request involves a real person; runs the consent gate")
        sp.add_argument("--speaker-name", help="name of the real person (with --real-person)")
        sp.add_argument("--involves-minor", action="store_true", help="subject involves a minor (always denied)")
        sp.add_argument("--context", help="context tag, e.g. general, political-advertising, medical-advice")
        sp.add_argument("--requested-use", help="free-text description of the intended use")
        sp.add_argument("--consent-dir", help="directory holding consent records (default ./consent-records)")
        sp.add_argument("--venv", help="path to the offline venv (default: <skill-root>/.ov-venv)")
        sp.add_argument("--synthesis-timeout", dest="synthesis_timeout_s", type=int, help="synthesis timeout in seconds (default 120)")
        sp.add_argument("--engine-retries", dest="engine_retries", type=int, help="synthesis retries on failure (default 1)")
    sp = sub.add_parser("synthesize", help="synthesize a single utterance to mastered WAV")
    add_synth_args(sp)
    sp.add_argument("--output", "-o", required=True, help="output WAV path")
    sp.set_defaults(normalize=True)

    sp = sub.add_parser("batch", help="manifest-driven batch synthesis with resume")
    add_synth_args(sp)
    sp.add_argument("--manifest", required=True, help="JSON array / {items:[...]} / JSONL manifest")
    sp.add_argument("--output-dir", required=True, help="directory for <id>.wav outputs + batch-report.json")
    sp.add_argument("--resume", action="store_true", help="skip items whose WAV already exists")
    sp.add_argument("--force", action="store_true", help="with --resume: re-synthesize even existing outputs")
    sp.set_defaults(normalize=True)

    sp = sub.add_parser("validate", help="signal validation of a WAV (duration, peak, LUFS, clipping, DC, silence, buzz heuristic)")
    sp.add_argument("--input", "-i", required=True)
    sp.add_argument("--duration-min", type=float)
    sp.add_argument("--duration-max", type=float)
    sp.add_argument("--loudness-target", type=float)
    sp.add_argument("--peak-dbfs", type=float)
    sp.add_argument("--for-greeting", action="store_true", help="apply the greeting acceptance bounds (2.5-4s, -14 LUFS, -1 dBFS)")
    sp.add_argument("--transcript", help="optional transcript for phrase-match/WER")
    sp.add_argument("--transcript-file")
    sp.add_argument("--phrase", help="expected phrase (default: greeting acceptance phrase)")
    sp.add_argument("--reference", help="reference text for WER")
    sp.add_argument("--reference-file")

    sp = sub.add_parser("master", help="master a WAV: DC removal, de-click, trim, fades, loudness, peak limit")
    sp.add_argument("--input", "-i", required=True)
    sp.add_argument("--output", "-o", required=True)
    sp.add_argument("--loudness-target", type=float)
    sp.add_argument("--peak-dbfs", type=float)
    sp.add_argument("--sample-rate", type=int)
    sp.add_argument("--no-trim", action="store_true")
    sp.add_argument("--no-declick", action="store_true")
    sp.add_argument("--no-compress", action="store_true")

    sp = sub.add_parser("wer", help="word-error-rate between a reference and a hypothesis")
    sp.add_argument("--reference")
    sp.add_argument("--reference-file")
    sp.add_argument("--hypothesis")
    sp.add_argument("--hypothesis-file")

    sp = sub.add_parser("phrase-match", help="check that an expected phrase appears in a transcript")
    sp.add_argument("--phrase")
    sp.add_argument("--phrase-file")
    sp.add_argument("--transcript")
    sp.add_argument("--transcript-file")

    sp = sub.add_parser("provenance", help="write / verify / show provenance sidecar JSON")
    sp.add_argument("action_pos", nargs="?", choices=["write", "verify", "show"], help="positional shorthand for --action")
    sp.add_argument("--action", choices=["write", "verify", "show"])
    sp.add_argument("--wav", help="(write) WAV to record")
    sp.add_argument("--text")
    sp.add_argument("--text-file")
    sp.add_argument("--input", help="(verify/show) WAV or sidecar path")
    sp.add_argument("--engine")
    sp.add_argument("--preset")

    sp = sub.add_parser("consent", help="voice-clone consent gate: check / grant / list / revoke")
    sp.add_argument("action_pos", nargs="?", choices=["check", "grant", "list", "revoke"], help="positional shorthand for --action")
    sp.add_argument("--action", choices=["check", "grant", "list", "revoke"])
    sp.add_argument("--spec", help="check: JSON spec file {real_person, speaker_name, ...}")
    sp.add_argument("--real-person", action="store_true")
    sp.add_argument("--speaker-name")
    sp.add_argument("--involves-minor", action="store_true")
    sp.add_argument("--context")
    sp.add_argument("--requested-use")
    sp.add_argument("--grantor", help="grant: who authorized (name/organization)")
    sp.add_argument("--scope", help="grant: scope of authorization, e.g. 'product intro videos, 2026 re-record only'")
    sp.add_argument("--expires", help="grant: expiry date YYYY-MM-DD")
    sp.add_argument("--notes")
    sp.add_argument("--id", help="revoke: consent record id")
    sp.add_argument("--confirm", action="store_true", help="revoke: required confirmation flag")
    sp.add_argument("--consent-dir")

    sp = sub.add_parser("benchmark", help="synthesis timing, real-time factor and output stats")
    add_synth_args(sp)
    sp.add_argument("--runs", type=int, default=3)
    sp.set_defaults(normalize=True, no_trim=False)

    sub.add_parser("diagnose", help="environment and dependency diagnostic (offline)")

    sp = sub.add_parser("self-test", help="run the unit/integration test suite and greeting acceptance checks")
    sp.add_argument("--quick", action="store_true", help="skip the end-to-end greeting synthesis check")

    sub.add_parser("version", help="print tool and bundled-model identity")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.version and not args.command:
        print(CORE.json_dump({"tool": "offline-neural-voice", "version": CORE.VERSION}))
        return EXIT_OK
    if not args.command:
        parser.print_help()
        return EXIT_OK

    try:
        cfg = CORE.load_config(args.config)
        # CLI overrides into config
        for key, cli_key in (("engine", "engine"), ("model", "model"), ("model_config", "model_config"),
                             ("venv", "venv"), ("synthesis_timeout_s", "synthesis_timeout_s"),
                             ("engine_retries", "engine_retries")):
            val = getattr(args, cli_key, None)
            if val is not None:
                cfg[key] = val
        handler = {
            "capabilities": cmd_capabilities, "normalize": cmd_normalize, "segment": cmd_segment,
            "ssml-check": cmd_ssml_check, "synthesize": cmd_synthesize, "batch": cmd_batch,
            "validate": cmd_validate, "master": cmd_master, "wer": cmd_wer,
            "phrase-match": cmd_phrase_match, "provenance": cmd_provenance,
            "consent": cmd_consent, "benchmark": cmd_benchmark, "diagnose": cmd_diagnose,
            "self-test": cmd_self_test, "version": cmd_version,
        }[args.command]
        return handler(args, cfg)
    except OvError as exc:
        print(CORE.json_dump(exc.to_dict()), file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print(CORE.json_dump({"ok": False, "error": "interrupted", "code": 130}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
