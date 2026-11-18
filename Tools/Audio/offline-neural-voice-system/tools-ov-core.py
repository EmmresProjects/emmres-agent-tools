#!/usr/bin/env python3
"""Shared core utilities for the Offline Neural Voice Skill Tool.

Imported by tools-offline-voice.py and the other tools-* modules. Not meant
to be executed directly, but safe to inspect: python3 tools-ov-core.py --selfcheck
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import wave
from pathlib import Path

VERSION = "1.0.0"
SKILL_ROOT = Path(__file__).resolve().parent
ASSET_MODEL = SKILL_ROOT / "asset-voice-lessac-medium.onnx"
ASSET_MODEL_CONFIG = SKILL_ROOT / "asset-voice-lessac-medium.onnx.json"
ASSET_PRESETS = SKILL_ROOT / "asset-voice-presets.json"
ASSET_PRONUNCIATION = SKILL_ROOT / "asset-pronunciation-dictionary.json"
ASSET_ACCEPTANCE = SKILL_ROOT / "asset-acceptance-phrases.json"
ASSET_SAFETY = SKILL_ROOT / "asset-safety-policy.json"
DEPENDENCIES_CHECKSUMS = SKILL_ROOT / "dependencies-checksums.txt"
DEPENDENCIES_LOCK = SKILL_ROOT / "dependencies-requirements-lock.txt"
DEFAULT_VENV = SKILL_ROOT / ".ov-venv"

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_USAGE = 2
EXIT_ENGINE_UNAVAILABLE = 3
EXIT_SAFETY_BLOCKED = 4
EXIT_IO = 5
EXIT_BATCH_FAILURES = 6
EXIT_DEPENDENCY = 7

AUDIO_EXTS = {".wav"}


class OvError(Exception):
    """Structured error carrying a stable exit code and machine-readable details."""

    def __init__(self, message: str, code: int = EXIT_USAGE, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = int(code)
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.message, "code": self.code, "details": self.details}


def json_dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


def emit(payload, as_json: bool = True):
    if as_json:
        print(json_dump(payload))
    else:
        print(humanize(payload))


def humanize(payload) -> str:
    lines = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
            else:
                lines.append(f"{k}: {v}")
    else:
        lines.append(str(payload))
    return "\n".join(lines)


def sha256_file(path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def load_json_asset(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise OvError(f"Required asset missing: {path.name}", EXIT_DEPENDENCY,
                      {"expected_path": str(path)}) from exc
    except json.JSONDecodeError as exc:
        raise OvError(f"Asset {path.name} is not valid JSON: {exc}", EXIT_DEPENDENCY) from exc


def default_config() -> dict:
    return {
        "engine": "auto",
        "preset": "friendly-male-30s",
        "model": str(ASSET_MODEL),
        "model_config": str(ASSET_MODEL_CONFIG),
        "loudness_target_lufs": -14.0,
        "peak_dbfs": -1.0,
        "sample_rate": 22050,
        "sentence_silence": 0.35,
        "trim_silence_db": -45.0,
        "trim_pad_ms": 40,
        "fade_ms": 6,
        "compressor": {"enabled": True, "threshold_db": -14.0, "ratio": 3.0, "attack_ms": 5.0, "release_ms": 90.0},
        "max_text_chars": 5000,
        "max_chunk_chars": 600,
        "synthesis_timeout_s": 120,
        "engine_retries": 1,
        "venv": str(DEFAULT_VENV),
        "provenance": True,
        "disclosure": "This audio was produced by a synthetic neural voice (offline, local). It is not a recording of a real person unless an authorized voice-clone consent record exists.",
    }


def load_config(config_path: str | None) -> dict:
    cfg = default_config()
    candidate = config_path or os.environ.get("OV_CONFIG")
    if candidate:
        p = Path(candidate).expanduser()
        if not p.is_file():
            raise OvError(f"Config file not found: {p}", EXIT_USAGE)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
        except json.JSONDecodeError as exc:
            raise OvError(f"Config file {p} is not valid JSON: {exc}", EXIT_USAGE) from exc
        if not isinstance(user_cfg, dict):
            raise OvError(f"Config file {p} must contain a JSON object", EXIT_USAGE)
        cfg.update(user_cfg)
        for key in ("model", "model_config", "venv"):
            val = cfg.get(key)
            if val and not Path(val).expanduser().is_absolute():
                cfg[key] = str((SKILL_ROOT / val).resolve())
    cfg["_config_source"] = candidate or "defaults"
    return cfg


# ---------- WAV I/O (16-bit PCM, mono/stereo) ----------

def read_wav(path):
    """Return dict with samples (float list, -1..1), rate, channels, width, n_frames."""
    p = Path(path)
    if not p.is_file():
        raise OvError(f"WAV file not found: {p}", EXIT_IO)
    try:
        with wave.open(str(p), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            n = w.getnframes()
            raw = w.readframes(n)
    except wave.Error as exc:
        raise OvError(f"Not a valid WAV file: {p} ({exc})", EXIT_USAGE) from exc
    if width != 2:
        raise OvError(f"Unsupported sample width {width} bytes (only 16-bit PCM supported)", EXIT_USAGE)
    import array
    arr = array.array("h")
    arr.frombytes(raw)
    if sys.byteorder == "big":
        arr.byteswap()
    scale = 32768.0
    floats = [s / scale for s in arr]
    return {"samples": floats, "rate": rate, "channels": channels, "width": width, "frames": n}


def write_wav(path, samples, rate: int, channels: int = 1):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    import array
    arr = array.array("h")
    clipped = 0
    for s in samples:
        v = int(round(s * 32768.0))
        if v > 32767:
            v, clipped = 32767, clipped + 1
        elif v < -32768:
            v, clipped = -32768, clipped + 1
        arr.append(v)
    if sys.byteorder == "big":
        arr.byteswap()
    with wave.open(str(p), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(arr.tobytes())
    return {"path": str(p), "frames": len(arr) // channels, "rate": rate,
            "channels": channels, "clipped_samples_written": clipped}


def mixdown_mono(samples, channels: int):
    if channels == 1:
        return list(samples)
    mono = []
    for i in range(0, len(samples), channels):
        frame = samples[i:i + channels]
        mono.append(sum(frame) / len(frame))
    return mono


def verify_checksum_manifest(checksums_path: Path = DEPENDENCIES_CHECKSUMS):
    """Verify bundled dependency/model files against dependencies-checksums.txt."""
    if not checksums_path.is_file():
        raise OvError(f"Checksum manifest missing: {checksums_path.name}", EXIT_DEPENDENCY)
    results, ok = [], True
    with open(checksums_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            expected, name = line.split(None, 1)
            target = SKILL_ROOT / name.strip()
            if not target.is_file():
                results.append({"file": name.strip(), "status": "missing"})
                ok = False
                continue
            actual = sha256_file(target)
            match = actual == expected.lower()
            results.append({"file": name.strip(),
                            "status": "ok" if match else "mismatch",
                            "sha256": actual})
            ok = ok and match
    return {"ok": ok, "checked": len(results), "files": results}


def capability_catalog():
    """Category-level mapping of the 32 catalog categories to this tool's commands."""
    return [
        {"category": "1. Offline Installation, Dependencies, and Environment Readiness",
         "commands": ["diagnose", "setup (tools-ov-setup.py)", "self-test"], "status": "implemented"},
        {"category": "2. Neural Engine Architecture and Provider Routing",
         "commands": ["synthesize --engine", "diagnose", "benchmark"], "status": "implemented (piper ONNX primary; PyTorch optional/unbundled)"},
        {"category": "3. Preferred Model Options and Compatibility",
         "commands": ["synthesize --model --model-config", "diagnose"], "status": "implemented for Piper-compatible ONNX; other families need separate local models"},
        {"category": "4. Model Files, Voice Assets, and Configuration",
         "commands": ["setup --check-only", "diagnose", "provenance verify"], "status": "implemented (checksum + integrity verification)"},
        {"category": "5. Friendly Male 30s Voice Preset",
         "commands": ["synthesize --preset friendly-male-30s"], "status": "implemented (asset-voice-presets.json)"},
        {"category": "6. Expressiveness, Emotion, and Style Control",
         "commands": ["synthesize --preset", "ssml-check", "normalize"], "status": "implemented via presets + SSML-lite prosody segments"},
        {"category": "7. Pace, Pitch, Pause, and Emphasis Control",
         "commands": ["ssml-check", "synthesize --rate-percent/--length-scale"], "status": "implemented (rate via length_scale; emphasis via local rate shift + pauses; no waveform pitch shifting)"},
        {"category": "8. Text Normalization and Cleanup",
         "commands": ["normalize"], "status": "implemented"},
        {"category": "9. Acronyms, Pronunciation, and Phonemes",
         "commands": ["normalize --pronunciation", "pronunciation (via asset-pronunciation-dictionary.json)"], "status": "implemented (dictionary + acronym expansion; espeak G2P in engine)"},
        {"category": "10. Sentence Segmentation and Long-Text Chunking",
         "commands": ["segment"], "status": "implemented"},
        {"category": "11. SSML and Local Direction Format",
         "commands": ["ssml-check", "synthesize --ssml"], "status": "implemented (SSML-lite subset, security filter)"},
        {"category": "12. Core Synthesis Scripts and Modules",
         "commands": ["synthesize", "batch"], "status": "implemented (this package's modules)"},
        {"category": "13. Command-Line Interface and Batch Synthesis",
         "commands": ["(all)", "batch"], "status": "implemented"},
        {"category": "14. Model Loading, Caching, and Memory Management",
         "commands": ["synthesize (in-process load; worker mode isolates memory)", "benchmark --runs"], "status": "implemented (per-process model load; no cross-run cache, deterministic)"},
        {"category": "15. Voice Style, Delivery, and Consistency",
         "commands": ["synthesize --preset", "benchmark"], "status": "implemented via presets + provenance"},
        {"category": "16. Language, Locale, and English (en-US) Handling",
         "commands": ["normalize", "synthesize (es-peak en-us voice)"], "status": "implemented (en-US primary; other locales need their own model)"},
        {"category": "17. Numbers, Dates, Units, and Symbols",
         "commands": ["normalize"], "status": "implemented"},
        {"category": "18. Audio Encoding, Containers, and Formats",
         "commands": ["synthesize", "master", "validate"], "status": "implemented (WAV/PCM16 mono in/out; other containers not bundled)"},
        {"category": "19. Audio Resampling and Sample-Rate Handling",
         "commands": ["master --sample-rate"], "status": "implemented (linear resampler + rate validation)"},
        {"category": "20. Audio Mastering and Delivery Targets",
         "commands": ["master", "synthesize (mastered by default)"], "status": "implemented (DC removal, trim, fades, loudness, -1 dBFS peak)"},
        {"category": "21. Audio Quality and Signal Validation",
         "commands": ["validate"], "status": "implemented"},
        {"category": "22. Spoken-Text Completeness and ASR Validation",
         "commands": ["wer", "phrase-match", "validate --transcript"], "status": "implemented (WER + phrase match on a supplied transcript; no ASR model is bundled)"},
        {"category": "23. Naturalness and Human Listening Review",
         "commands": ["benchmark", "validate"], "status": "tool-supported workflow; final listening decision is human, by design"},
        {"category": "24. Acceptance Test for Greetings Sample",
         "commands": ["self-test", "synthesize --text 'Greetings, friends! Welcome to my video.'"], "status": "implemented (automated gates; listening review remains human)"},
        {"category": "25. Regression Tests and Benchmarking",
         "commands": ["self-test", "benchmark", "validate"], "status": "implemented"},
        {"category": "26. Safety, Consent, and Real-Person Protection",
         "commands": ["consent check|grant|list|revoke"], "status": "implemented (policy gate + records; see asset-safety-policy.json)"},
        {"category": "27. Provenance, Metadata, and Rights Records",
         "commands": ["provenance write|verify|show"], "status": "implemented (sidecar JSON, SHA-256 of text/model/config)"},
        {"category": "28. Skill Files and Package Structure",
         "commands": ["(this folder)", "self-test"], "status": "implemented (see knowledge-readme.md)"},
        {"category": "29. Unit, Integration, and Audio Validation Tests",
         "commands": ["self-test", "tools-run-tests.py"], "status": "implemented"},
        {"category": "30. Complete Offline Installation Package",
         "commands": ["setup", "setup --check-only", "diagnose"], "status": "implemented (offline bundle with pinned wheels + model; no network required)"},
        {"category": "31. Fallbacks, Degradation, and Failure Handling",
         "commands": ["synthesize --engine", "synthesize (worker isolation, timeout, retries)"], "status": "implemented (no silent formant fallback; explicit failure + quality warnings)"},
        {"category": "32. Documentation, Troubleshooting, Handoff, and Governance",
         "commands": ["(all) --help", "capabilities"], "status": "implemented (knowledge-readme.md is the single handbook)"},
    ]


OPERATIONS = [
    ("capabilities", "List every unified capability, its command, and implementation status"),
    ("synthesize", "Synthesize a single utterance to mastered WAV (neural ONNX via Piper)"),
    ("batch", "Manifest-driven batch synthesis with resume and a JSON report"),
    ("normalize", "Text normalization: unicode, whitespace, numbers, dates, currency, units, URLs, acronyms, pronunciation dictionary"),
    ("segment", "Sentence segmentation and length-bounded chunking for long text"),
    ("ssml-check", "Parse/validate SSML-lite, report unsupported markup, emit a synthesis plan"),
    ("validate", "Signal validation of a WAV: duration, peak, loudness, clipping, DC, silence, buzz heuristic, NaN"),
    ("master", "Master a WAV: DC removal, de-click, trim, fades, loudness to target, peak limit"),
    ("wer", "Word-error-rate between reference and transcript"),
    ("phrase-match", "Expected-phrase matcher; lists missing words"),
    ("provenance", "Write / verify / show provenance sidecar JSON (text hash, model hash, engine, disclosure)"),
    ("consent", "Consent gate: check a request against the safety policy; grant/list/revoke voice-clone consent records"),
    ("benchmark", "Synthesis timing, real-time factor, and output report"),
    ("diagnose", "Environment and dependency diagnostic (python, CPU, RAM, disk, onnxruntime/piper/model integrity)"),
    ("self-test", "Run the unit/integration suite and the greeting acceptance checks"),
    ("version", "Print tool version and bundled model identity"),
]


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        print(json_dump({"ok": True, "version": VERSION, "skill_root": str(SKILL_ROOT)}))
        sys.exit(0)
    print(json_dump({"operations": OPERATIONS}))
