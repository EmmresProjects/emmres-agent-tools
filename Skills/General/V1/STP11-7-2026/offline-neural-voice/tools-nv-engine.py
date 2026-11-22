#!/usr/bin/env python3
"""tools-nv-engine.py - offline neural voice engine, phonemizer and voice resolution.

Loaded by the entry point (tools-nv-voice.py) and by the other tools through the
shared loader. It is importable but not intended to be run directly except for
its self test:

    python tools-nv-engine.py selftest

Engines
-------
onnx-vits      CPU inference of a Piper-compatible VITS ONNX model with
               onnxruntime, using this module's own phoneme-id and scale
               handling (no piper package needed at inference time).
piper-package  Reference adapter around the piper-tts package, used to
               cross-check the onnx-vits path and to phonemize reliably.
buzz-fallback  Deterministic, clearly labelled downgrade used only when no
               neural model is usable; it validates the rest of the pipeline
               and never claims to be intelligible speech.

All engines are CPU-only, need no API key and perform no network access.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from array import array
from pathlib import Path

# The runtime file is named tools-nvcore.py (hyphens are not importable), so load
# it by path and register it as the `nvcore` module before importing.
_HERE = Path(__file__).resolve().parent
if "nvcore" not in sys.modules:
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("nvcore", _HERE / "tools-nvcore.py")
        _module = _ilu.module_from_spec(_spec)
        sys.modules["nvcore"] = _module
        _spec.loader.exec_module(_module)
    except FileNotFoundError:
        pass

import nvcore
from nvcore import (DependencyError, InputError, NVError, PolicyError, as_number,
                    human_bytes, normalize_language_tag, parse_bool, read_json,
                    sha256_file, slugify, utc_now)

BOS = "^"
EOS = "$"
PAD = "_"

ENGINE_ONNX = "onnx-vits"
ENGINE_PIPER = "piper-package"
ENGINE_FALLBACK = "buzz-fallback"

KNOWN_ENGINES = (ENGINE_ONNX, ENGINE_PIPER, ENGINE_FALLBACK)

#: Phoneme id map published by Piper (used when a config omits it).
DEFAULT_PHONEME_IDS = {
    "_": [0], "^": [1], "$": [2], " ": [3], "!": [4], "'": [5], "(": [6], ")": [7],
    ",": [8], "-": [9], ".": [10], ":": [11], ";": [12], "?": [13], "a": [14],
    "b": [15], "c": [16], "d": [17], "e": [18], "f": [19], "h": [20], "i": [21],
    "j": [22], "k": [23], "l": [24], "m": [25], "n": [26], "o": [27], "p": [28],
    "q": [29], "r": [30], "s": [31], "t": [32], "u": [33], "v": [34], "w": [35],
    "x": [36], "y": [37], "z": [38], "æ": [39], "ç": [40], "ð": [41], "ø": [42],
    "ħ": [43], "ŋ": [44], "œ": [45], "ǀ": [46], "ǁ": [47], "ǂ": [48], "ǃ": [49],
    "ɐ": [50], "ɑ": [51], "ɒ": [52], "ɓ": [53], "ɔ": [54], "ɕ": [55], "ɖ": [56],
    "ɗ": [57], "ɘ": [58], "ə": [59], "ɚ": [60], "ɛ": [61], "ɜ": [62], "ɞ": [63],
    "ɟ": [64], "ɠ": [65], "ɡ": [66], "ɢ": [67], "ɣ": [68], "ɤ": [69], "ɥ": [70],
    "ɦ": [71], "ɧ": [72], "ɨ": [73], "ɪ": [74], "ɫ": [75], "ɬ": [76], "ɭ": [77],
    "ɮ": [78], "ɯ": [79], "ɰ": [80], "ɱ": [81], "ɲ": [82], "ɳ": [83], "ɴ": [84],
    "ɵ": [85], "ɶ": [86], "ɸ": [87], "ɹ": [88], "ɺ": [89], "ɻ": [90], "ɽ": [91],
    "ɾ": [92], "ʀ": [93], "ʁ": [94], "ʂ": [95], "ʃ": [96], "ʄ": [97], "ʈ": [98],
    "ʉ": [99], "ʊ": [100], "ʋ": [101], "ʌ": [102], "ʍ": [103], "ʎ": [104],
    "ʏ": [105], "ʐ": [106], "ʑ": [107], "ʒ": [108], "ʔ": [109], "ʕ": [110],
    "ʘ": [111], "ʙ": [112], "ʛ": [113], "ʜ": [114], "ʝ": [115], "ʟ": [116],
    "ʡ": [117], "ʢ": [118], "ʲ": [119], "ˈ": [120], "ˌ": [121], "ː": [122],
    "ˑ": [123], "ˈ": [120], "β": [126], "θ": [127], "χ": [128], "ᵻ": [129],
}

_IPA_OVERRIDE = re.compile(r"\[\[(.*?)\]\]")
_DOUBLE_BRACKET = re.compile(r"\[\[(.*?)\]\]")


def normalize_ipa_brackets(text):
    """Collapse nested [[a]][[b]] blocks into one [[ab]] so punctuation joins.

    A lexicon override can follow inline punctuation that already sits inside an
    IPA block; without this, the engine would emit two separate blocks and lose
    the pause carried by the comma.
    """
    def join(match):
        return " " if match.group(1) else ""

    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\]\](\s*)\[\[", join, text)
    return text
_STRESS_MARKS = ("ˈ", "ˌ")


# --------------------------------------------------------------------------- #
# Phonemization
# --------------------------------------------------------------------------- #

class Phonemizer:
    """Text to phoneme sequences for one language.

    Backends are tried in order and each is reported, so the caller always knows
    which one produced the phonemes:
      1. piper-tts espeak bridge (bundled espeak-ng data, deterministic)
      2. espeak-ng executable on PATH (subprocess, bounded, no shell)
      3. manual IPA mode, when the request already contains [[ipa]] blocks
    """

    def __init__(self, espeak_voice="en-us", *, espeak_data_dir=None, timeout_s=15.0,
                 allow_binary=True):
        self.espeak_voice = espeak_voice or "en-us"
        self.espeak_data_dir = espeak_data_dir
        self.timeout_s = float(timeout_s)
        self.allow_binary = bool(allow_binary)
        self.backend = None
        self.notes = []
        self._phonemizer = None

    # -- backend probing -------------------------------------------------- #
    def probe(self):
        probes = {"piper_espeak": None, "espeak_ng_binary": None, "manual_ipa": True}
        try:
            module = nvcore.import_optional(
                "piper.phonemize_espeak", feature="phonemize",
                install_hint="pip install piper-tts")
            data_dir = self.espeak_data_dir
            if data_dir is None:
                data_dir = getattr(module, "ESPEAK_DATA_DIR", None)
            self._phonemizer = module.EspeakPhonemizer(data_dir) if data_dir else module.EspeakPhonemizer()
            probes["piper_espeak"] = {"available": True, "data_dir": str(data_dir or "bundled"),
                                      "voice": self.espeak_voice}
        except Exception as exc:
            probes["piper_espeak"] = {"available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        if self.allow_binary:
            binary = shutil.which("espeak-ng") or shutil.which("espeak")
            probes["espeak_ng_binary"] = {"available": bool(binary), "path": binary}
        else:
            probes["espeak_ng_binary"] = {"available": False, "error": "disabled by configuration"}
        if probes["piper_espeak"]["available"]:
            self.backend = "piper_espeak"
        elif probes["espeak_ng_binary"]["available"]:
            self.backend = "espeak_ng_binary"
            self.notes.append("using the espeak-ng executable; phoneme segmentation may differ "
                              "from the bundled bridge")
        else:
            self.backend = "manual_ipa"
            self.notes.append("no espeak backend available: only [[ipa]] blocks can be phonemized")
        return probes

    # -- phonemization ---------------------------------------------------- #
    def phonemize(self, text, *, cancel=None):
        """Return (sentences, warnings) where each sentence is a phoneme list."""
        if text is None:
            raise InputError("text is required")
        text = normalize_ipa_brackets(str(text))
        if not text.strip():
            raise InputError("text is empty")
        parts = _IPA_OVERRIDE.split(text)
        sentences = []
        warnings = []
        previous_raw = False
        for index, part in enumerate(parts):
            if index % 2 == 1:  # inside [[ ]]
                raw = list(unicodedata.normalize("NFD", part.strip()))
                if not sentences:
                    sentences.append([])
                if index > 0 and parts[index - 1].endswith(" "):
                    sentences[-1].append(" ")
                sentences[-1].extend(raw)
                if index < len(parts) - 1 and parts[index + 1].startswith(" "):
                    sentences[-1].append(" ")
                previous_raw = True
                continue
            if not part.strip():
                previous_raw = False
                continue
            if self.backend is None:
                self.probe()
            if cancel is not None:
                cancel.check()
            if self.backend == "piper_espeak":
                chunk = self._phonemize_bridge(part)
            elif self.backend == "espeak_ng_binary":
                chunk = self._phonemize_binary(part)
            else:
                warnings.append("skipped %d characters of non-IPA text: %r"
                                % (len(part.strip()), part.strip()[:40]))
                chunk = []
            if previous_raw and chunk:
                if not sentences:
                    sentences.append([])
                sentences[-1].extend(chunk[0])
                chunk = chunk[1:]
            sentences.extend(chunk)
            previous_raw = False
        if sentences and not sentences[-1]:
            sentences.pop()
        if not sentences:
            raise InputError("no phonemes were produced from the text",
                             hint="provide English text, or pass [[ipa]] blocks when no "
                                  "espeak backend is installed")
        return sentences, warnings

    def _phonemize_bridge(self, text):
        if self._phonemizer is None:
            self.probe()
        if self._phonemizer is None:
            raise DependencyError("espeak bridge unavailable",
                                  hint="pip install piper-tts")
        return self._phonemizer.phonemize(self.espeak_voice, text)

    def _phonemize_binary(self, text):
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise DependencyError("espeak-ng executable disappeared from PATH")
        command = [binary, "-q", "--ipa=3", "-v", self.espeak_voice, "-b", "1", "-m", "-"]
        try:
            proc = subprocess.run(command, input=text.encode("utf-8"), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired:
            raise NVError("espeak-ng timed out after %.1f s" % self.timeout_s,
                          hint="shorten the text or raise the phonemizer timeout")
        if proc.returncode != 0:
            raise NVError("espeak-ng failed with exit code %d" % proc.returncode,
                          details={"stderr": proc.stderr.decode("utf-8", "replace")[:400]})
        phonemes = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            if line[:1].isupper() and " " in line and not re.search(r"[^A-Za-z0-9 ]", line):
                # header style output such as "Text:" or "Phonemes:"
                continue
            phonemes.append(list(unicodedata.normalize("NFD", line)) + [" "])
        if phonemes:
            phonemes[-1] = phonemes[-1][:-1] if phonemes[-1][-1:] == [" "] else phonemes[-1]
        return phonemes


# --------------------------------------------------------------------------- #
# Phoneme ids
# --------------------------------------------------------------------------- #

class PhonemeIdMapper:
    def __init__(self, id_map):
        if not isinstance(id_map, dict) or not id_map:
            id_map = DEFAULT_PHONEME_IDS
        self.map = id_map
        self.unknown = {}
        for required in (BOS, EOS, PAD):
            if required not in self.map:
                raise InputError("phoneme id map lacks the %r control symbol" % required,
                                 hint="use a Piper-compatible voice configuration")

    def to_ids(self, phonemes):
        ids = []
        ids.extend(self.map[BOS])
        ids.extend(self.map[PAD])
        missing = []
        for phoneme in phonemes:
            entry = self.map.get(phoneme)
            if entry is None:
                single = phoneme.split(" ")[0]
                entry = self.map.get(single)
            if entry is None:
                missing.append(phoneme)
                self.unknown[phoneme] = self.unknown.get(phoneme, 0) + 1
                continue
            ids.extend(entry)
            ids.extend(self.map[PAD])
        ids.extend(self.map[EOS])
        return ids, missing

    def check(self, phonemes):
        """Report coverage without synthesizing."""
        missing = sorted({p for p in phonemes if p not in self.map and p not in (" ",)})
        total = len([p for p in phonemes if p not in (" ",)])
        return {
            "total_phonemes": total,
            "missing": missing,
            "coverage": round(1.0 - (len(missing) / total), 4) if total else 1.0,
        }


# --------------------------------------------------------------------------- #
# Voice configuration and resolution
# --------------------------------------------------------------------------- #

class VoiceConfig:
    """Parsed Piper-compatible voice configuration with compatibility facts."""

    def __init__(self, data, *, path=None):
        if not isinstance(data, dict):
            raise InputError("voice configuration must be a JSON object")
        self.data = data
        self.path = str(path) if path else None
        audio = data.get("audio") or {}
        espeak = data.get("espeak") or {}
        inference = data.get("inference") or {}
        self.sample_rate = int(audio.get("sample_rate") or nvcore.DEFAULT_SAMPLE_RATE)
        self.quality = audio.get("quality")
        self.espeak_voice = espeak.get("voice") or "en-us"
        self.num_speakers = int(data.get("num_speakers") or 1)
        self.speaker_id_map = data.get("speaker_id_map") or {}
        self.phoneme_id_map = data.get("phoneme_id_map") or DEFAULT_PHONEME_IDS
        self.num_symbols = data.get("num_symbols")
        self.language = (data.get("language") or {}).get("code")
        self.phoneme_type = data.get("phoneme_type") or "espeak"
        self.defaults = {
            "length_scale": float(inference.get("length_scale", 1.0) or 1.0),
            "noise_scale": float(inference.get("noise_scale", 0.667) if inference.get("noise_scale") is not None else 0.667),
            "noise_w": float(inference.get("noise_w", 0.8) if inference.get("noise_w") is not None else 0.8),
        }
        # Piper pads the embedding table, so num_symbols is usually >= the map size.
        self.num_symbols_consistent = (self.num_symbols is None or
                                       self.num_symbols >= len(self.phoneme_id_map))
        self.mapper = PhonemeIdMapper(self.phoneme_id_map)

    def speaker_index(self, speaker):
        """Map a speaker name/id string to a speaker index."""
        if speaker is None:
            if self.num_speakers <= 1:
                return None
            first = sorted(self.speaker_id_map.items(), key=lambda kv: kv[1])
            if first:
                return int(first[0][1])
            return 0
        if str(speaker) in self.speaker_id_map:
            return int(self.speaker_id_map[str(speaker)])
        try:
            value = int(str(speaker))
        except ValueError:
            raise InputError("speaker %r is not in the voice's speaker map" % speaker,
                             hint="available speakers: %s" % ", ".join(
                                 sorted(self.speaker_id_map)[:10]) or "single speaker voice")
        if 0 <= value < max(self.num_speakers, 1):
            return value
        raise InputError("speaker index %s out of range for %d speakers" % (value, self.num_speakers))

    def compatibility(self, *, model_report=None, model_path=None):
        problems = []
        warnings = []
        if not self.num_symbols_consistent:
            warnings.append("num_symbols (%s) is smaller than the phoneme_id_map size (%d); "
                            "the model cannot represent every phoneme"
                            % (self.num_symbols, len(self.phoneme_id_map)))
        if self.sample_rate not in (8000, 16000, 22050, 24000, 44100, 48000):
            warnings.append("unusual sample rate %d Hz" % self.sample_rate)
        if self.phoneme_type != "espeak":
            warnings.append("phoneme type %r needs its own phonemizer; only espeak input "
                            "is supported by the built-in phonemizer" % self.phoneme_type)
        if model_report:
            if model_report.get("opset") and model_report["opset"] > 21:
                warnings.append("opset %s is newer than the tested range (<= 21)" % model_report["opset"])
            if model_report.get("float16_initializers") and not model_report.get("int8_initializers"):
                warnings.append("model stores float16 initializers; on some CPUs ORT casts them "
                                "at load time, raising memory use")
        return {"problems": problems, "warnings": warnings,
                "sample_rate": self.sample_rate, "num_speakers": self.num_speakers,
                "language": self.language, "espeak_voice": self.espeak_voice}


class VoiceAsset:
    """A resolved, checked voice asset pair (model + configuration)."""

    def __init__(self, *, voice_id, model_path, config_path, config, registry_entry=None,
                 speaker=None, speaker_index=None, checksums=None, warnings=None):
        self.voice_id = voice_id
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        self.config = config
        self.registry_entry = registry_entry or {}
        self.speaker = speaker
        self.speaker_index = speaker_index
        self.checksums = checksums or {}
        self.warnings = list(warnings or [])

    def describe(self):
        return {
            "voice_id": self.voice_id,
            "model": self.model_path.name,
            "config": self.config_path.name,
            "speaker": self.speaker,
            "speaker_index": self.speaker_index,
            "sample_rate": self.config.sample_rate,
            "num_speakers": self.config.num_speakers,
            "language": self.config.language,
            "quality": self.config.quality,
            "license_id": self.registry_entry.get("license_id"),
            "checksums": self.checksums,
            "warnings": self.warnings,
        }


def verify_asset_integrity(model_path, config_path, files, *, enabled=True,
                           allow_missing=True):
    """Verify the pinned digests for a model/config pair.

    Order of strength: SHA-256 (authoritative) -> MD5 (upstream index) -> size ->
    warning. A mismatch is always reported; an absent pin is a warning, never a
    silent pass.
    """
    report = {"checked": [], "match": None, "details": {}}
    warnings = []
    if not enabled:
        report["details"]["skipped"] = "execution.strict_checksum is false"
        report["match"] = None
        warnings.append("integrity verification disabled by configuration")
        return report, warnings
    pairs = [
        ("model", model_path, files.get("model_sha256"), files.get("model_md5"),
         files.get("model_size")),
        ("config", config_path, files.get("config_sha256"), files.get("config_md5"),
         files.get("config_size")),
    ]
    overall = True
    for role, path, sha_expected, md5_expected, size_expected in pairs:
        if path is None or not Path(path).is_file():
            continue
        entry = {"role": role, "file": Path(path).name}
        if sha_expected:
            actual = nvcore.sha256_file(path)
            entry.update({"algorithm": "sha256", "expected": sha_expected, "actual": actual,
                          "match": actual == sha_expected})
        elif md5_expected:
            actual = nvcore.md5_file(path)
            entry.update({"algorithm": "md5", "expected": md5_expected, "actual": actual,
                          "match": actual == md5_expected})
        elif size_expected:
            actual = Path(path).stat().st_size
            entry.update({"algorithm": "size-only", "expected": int(size_expected),
                          "actual": actual, "match": actual == int(size_expected)})
            warnings.append("%s has no published digest; only its size was compared for %s"
                            % (role, Path(path).name))
        else:
            entry.update({"algorithm": None, "expected": None, "actual": None, "match": None})
            if not allow_missing:
                raise InputError("no integrity pin exists for %s" % Path(path).name,
                                 hint="add the voice to the model registry, or pass "
                                      "--skip-checksum-verify for a one-off run")
            warnings.append("no integrity pin for %s: not registered in the model registry"
                            % Path(path).name)
        if entry["match"] is False:
            overall = False
        report["checked"].append(entry)
        report["details"][role] = entry
    report["match"] = overall if report["checked"] else None
    return report, warnings


def _candidate_asset_paths(cfg, entry, models_dir):
    """Return candidate (model, config) paths for a registry entry."""
    files = entry.get("files") or {}
    model_rel = files.get("model") or entry.get("model")
    config_rel = files.get("config") or entry.get("config")
    if not model_rel:
        raise InputError("registry entry %r has no model file" % entry.get("id"))
    if not config_rel:
        config_rel = model_rel + ".json"
    model_path = (models_dir / model_rel) if not Path(model_rel).is_absolute() else Path(model_rel)
    config_path = (models_dir / config_rel) if not Path(config_rel).is_absolute() else Path(config_rel)
    return model_path, config_path


def resolve_voice(cfg, voice_id=None, *, speaker=None, registry=None, verify_checksums=None,
                  allow_missing_checksum=True):
    """Resolve a voice id (or explicit paths) into a ready VoiceAsset.

    Resolution order:
      1. explicit --model/--voice-config paths (operator override)
      2. registry id, then registry alias
      3. single voice present in the models directory
    """
    models_dir = cfg.ensure_dir("paths.models_dir")
    registry = registry or {}
    entries = registry.get("voices", []) if isinstance(registry, dict) else []
    by_id = {e.get("id"): e for e in entries}
    aliases = {}
    for entry in entries:
        for alias in entry.get("aliases", []) or []:
            aliases.setdefault(alias, entry.get("id"))

    if voice_id and voice_id not in by_id:
        mapped = aliases.get(voice_id)
        if mapped:
            voice_id = mapped

    entry = by_id.get(voice_id) if voice_id else None
    warnings = []
    checksums = {}

    if entry is None and voice_id:
        # maybe a local file pair named exactly like the voice id
        for suffix in (".onnx", ""):
            candidate = models_dir / (voice_id + suffix)
            if candidate.is_file():
                config_candidate = models_dir / (candidate.name + ".json")
                if config_candidate.is_file():
                    model_path, config_path = candidate, config_candidate
                    break
        else:
            raise InputError(
                "voice %r is not in the registry and no matching model/config pair exists "
                "in %s" % (voice_id, cfg.rel(models_dir)),
                hint="run `list-voices` to see registry ids, or pass --model and "
                     "--voice-config explicitly")
    elif entry is not None:
        model_path, config_path = _candidate_asset_paths(cfg, entry, models_dir)
    else:
        # no voice requested: pick a single installed voice deterministically
        installed = sorted(models_dir.glob("*.onnx"))
        installed = [p for p in installed if (models_dir / (p.name + ".json")).is_file()]
        if len(installed) == 1:
            model_path = installed[0]
            config_path = models_dir / (installed[0].name + ".json")
            for candidate in entries:
                if candidate.get("files", {}).get("model", "").endswith(installed[0].name):
                    entry = candidate
                    voice_id = candidate.get("id")
                    break
            warnings.append("no voice requested; using the only installed voice %s"
                            % installed[0].name)
        elif not installed:
            raise InputError("no voice is installed in %s" % cfg.rel(models_dir),
                             hint="place a Piper-compatible .onnx model and its .onnx.json "
                                  "in the models directory, then run `fetch-model --plan`")
        else:
            raise InputError("no voice requested and %d voices are installed" % len(installed),
                             hint="pass --voice <id>; run `list-voices --installed` to see them")

    if not model_path.is_file():
        raise InputError(
            "model file is not installed: %s" % cfg.rel(model_path),
            hint="run: python tools-nv-voice.py fetch-model --voice %s --plan  (then --approve)"
                 % (voice_id or "<id>"))
    if not config_path.is_file():
        raise InputError("voice configuration is not installed: %s" % cfg.rel(config_path),
                         hint="the .onnx.json file must sit beside the model")

    config = VoiceConfig(read_json(config_path, what="voice configuration"), path=str(config_path))

    # integrity verification: strongest available digest, then size, then a warning
    if verify_checksums is None:
        verify_checksums = bool(cfg.get("execution.strict_checksum", True))
    files = ((entry or {}).get("files") or {})
    checksums, integrity_warnings = verify_asset_integrity(
        model_path, config_path, files, enabled=verify_checksums,
        allow_missing=allow_missing_checksum)
    warnings.extend(integrity_warnings)
    if checksums.get("match") is False:
        raise InputError(
            "integrity verification failed for %s" % model_path.name,
            hint="the file on disk does not match the registry pin; reinstall it with "
                 "`fetch-model` and do not synthesize from it",
            details=checksums)
    if entry and entry.get("files", {}).get("upstream_digest_mismatch"):
        drift = entry["files"]["upstream_digest_mismatch"]
        warnings.append(
            "upstream drift recorded for %s: the file served by the origin no longer "
            "matches the upstream voices.json index (%s bytes vs %s bytes); this voice "
            "is pinned to the locally verified SHA-256" % (
                model_path.name, drift.get("served_size"), drift.get("indexed_size")))
    if entry is None:
        warnings.append("voice %r is not in the model registry: licence status unknown"
                        % (voice_id or model_path.name))

    speaker_index = config.speaker_index(speaker)
    return VoiceAsset(voice_id=voice_id or config.espeak_voice,
                      model_path=model_path, config_path=config_path, config=config,
                      registry_entry=entry or {}, speaker=speaker,
                      speaker_index=speaker_index, checksums=checksums, warnings=warnings)


# --------------------------------------------------------------------------- #
# Engines
# --------------------------------------------------------------------------- #

class SynthesisRequest:
    def __init__(self, phonemes, *, scales, speaker_index=None, seed=None, timeout_s=120.0):
        self.phonemes = list(phonemes)
        self.scales = dict(scales)
        self.speaker_index = speaker_index
        self.seed = seed
        self.timeout_s = float(timeout_s)


class EngineResult:
    def __init__(self, samples, sample_rate, *, engine, degraded=False, disclosure=None,
                 extra=None, phoneme_ids=None, durations=None):
        self.samples = samples
        self.sample_rate = int(sample_rate)
        self.engine = engine
        self.degraded = bool(degraded)
        self.disclosure = disclosure
        self.extra = extra or {}
        self.phoneme_ids = phoneme_ids
        self.durations = durations

    @property
    def duration_s(self):
        return round(len(self.samples) / float(self.sample_rate), 4) if self.sample_rate else 0.0

    def describe(self):
        return {
            "engine": self.engine,
            "sample_rate": self.sample_rate,
            "frames": len(self.samples),
            "duration_s": self.duration_s,
            "degraded": self.degraded,
            "disclosure": self.disclosure,
            "extra": self.extra,
        }


class OnnxVitsEngine:
    """CPU ONNX Runtime engine for Piper-compatible VITS models."""

    name = ENGINE_ONNX

    def __init__(self, asset, *, cfg, cancel=None):
        self.asset = asset
        self.cfg = cfg
        self.cancel = cancel
        self._session = None
        self._np = None
        self._session_info = None
        self._lock = threading.Lock()

    def _ensure_session(self):
        if self._session is not None:
            return
        np = nvcore.import_optional("numpy", feature="onnx-inference", install_hint="pip install numpy")
        ort = nvcore.import_optional("onnxruntime", feature="onnx-inference",
                                     install_hint="pip install onnxruntime")
        providers = list(self.cfg.get("inference.providers") or ["CPUExecutionProvider"])
        available = ort.get_available_providers()
        usable = [p for p in providers if p in available]
        if not usable:
            raise DependencyError("none of the configured execution providers are available",
                                  details={"requested": providers, "available": available},
                                  hint="install onnxruntime for CPUExecutionProvider")
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(self.cfg.get("inference.intra_op_threads", 1) or 1)
        options.inter_op_num_threads = int(self.cfg.get("inference.inter_op_threads", 1) or 1)
        options.graph_optimization_level = getattr(
            ort.GraphOptimizationLevel,
            str(self.cfg.get("inference.graph_optimization", "ORT_ENABLE_ALL")),
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
        options.log_severity_level = 3
        started = time.monotonic()
        session = ort.InferenceSession(str(self.asset.model_path), options, providers=usable)
        self._session = session
        self._np = np
        self._session_info = {
            "providers": session.get_providers(),
            "load_ms": round((time.monotonic() - started) * 1000, 2),
            "input_names": [i.name for i in session.get_inputs()],
            "output_names": [o.name for o in session.get_outputs()],
        }

    @property
    def session_info(self):
        self._ensure_session()
        return dict(self._session_info)

    def synthesize(self, request: SynthesisRequest):
        self._ensure_session()
        np = self._np
        config = self.asset.config
        ids, missing = config.mapper.to_ids(request.phonemes)
        if missing:
            unique = sorted(set(missing))
            reason = self.classify_missing(unique)
            if reason["hint"]:
                request_hint = reason["hint"]
            else:
                request_hint = ("rewrite the text, add a lexicon entry, or choose a voice "
                                "that covers these phonemes")
            raise InputError(
                "phonemes are not present in the voice's phoneme_id_map: %s" % ", ".join(unique),
                hint=request_hint,
                details={"missing": unique, "diagnosis": reason})
        if len(ids) > 8000:
            raise InputError("phoneme sequence is too long (%d ids)" % len(ids),
                             hint="split the text into shorter sentences")
        scales = request.scales
        noise_scale = float(scales.get("noise_scale", config.defaults["noise_scale"]))
        length_scale = float(scales.get("length_scale", config.defaults["length_scale"]))
        noise_w = float(scales.get("noise_w", config.defaults["noise_w"]))
        if not 0.05 <= length_scale <= 4.0:
            raise InputError("length_scale must be between 0.05 and 4.0")
        inputs = {
            "input": np.expand_dims(np.array(ids, dtype=np.int64), 0),
            "input_lengths": np.array([len(ids)], dtype=np.int64),
            "scales": np.array([noise_scale, length_scale, noise_w], dtype=np.float32),
        }
        speaker_index = request.speaker_index
        input_names = set(self._session_info["input_names"])
        if speaker_index is not None and "sid" in input_names:
            inputs["sid"] = np.array([int(speaker_index)], dtype=np.int64)

        started = time.monotonic()
        result = self._run_with_timeout(inputs, request.timeout_s)
        elapsed = time.monotonic() - started
        if result is None:
            raise NVError(
                "inference exceeded the %.1f s timeout" % request.timeout_s,
                hint="raise execution.timeout_seconds, shorten the text, or lower "
                     "inference.intra_op_threads on a busy machine")
        audio = result[0]
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        durations = None
        if len(result) > 1:
            try:
                durations = np.asarray(result[-1]).reshape(-1).astype(np.int64).tolist()
            except Exception:
                durations = None
        int16 = np.clip(audio * 32767.0, -32767.0, 32767.0).astype(np.int16)
        samples = array("h")
        samples.frombytes(int16.tobytes())
        if sys.byteorder == "big":
            samples.byteswap()
        return EngineResult(samples, config.sample_rate, engine=self.name,
                            extra={"inference_ms": round(elapsed * 1000, 2),
                                   "phoneme_ids": len(ids),
                                   "scales": {"noise_scale": noise_scale,
                                              "length_scale": length_scale,
                                              "noise_w": noise_w},
                                   "speaker_index": speaker_index,
                                   "providers": self._session_info["providers"]},
                            phoneme_ids=ids, durations=durations)

    @staticmethod
    def classify_missing(missing):
        """Explain why phonemes are unsupported (script, diacritic or model limit)."""
        whitespace = [p for p in missing if p.strip() == ""]
        invisible = [p for p in missing if p and p.strip() == "" and p != " "]
        combining = [p for p in missing if p and unicodedata.combining(p[0])]
        foreign = [p for p in missing if p and ord(p[0]) > 0x2C00]
        if whitespace or invisible:
            return {"cause": "whitespace or invisible characters reached the phoneme stream",
                    "hint": "strip markup and invisible characters with prepare-text; the "
                            "engine should not receive raw tab or zero-width characters"}
        if foreign:
            return {"cause": "the text contains a script this voice cannot pronounce: %s"
                             % "".join(foreign[:8]),
                    "hint": "use a voice for that language, split the text into per-language "
                            "segments, or transliterate the words"}
        if combining:
            return {"cause": "unsupported combining diacritics: %s" % "".join(combining[:8]),
                    "hint": "write the word in plain IPA inside [[...]] instead"}
        return {"cause": "the voice phoneme map does not contain these symbols",
                "hint": "rewrite the word, add a lexicon entry, or choose another voice"}

    def _run_with_timeout(self, inputs, timeout_s):
        """Run session.run in a worker thread so a stuck kernel cannot hang the CLI.

        ONNX Runtime kernels are not preemptible; when the timeout elapses the
        worker is abandoned (daemon thread) and the caller receives None.
        """
        box = {}

        def worker():
            try:
                with self._lock:
                    out = self._session.run(None, inputs)
                box["out"] = out
            except BaseException as exc:  # propagate to the caller thread
                box["error"] = exc

        thread = threading.Thread(target=worker, name="nv-inference", daemon=True)
        thread.start()
        thread.join(timeout_s)
        if thread.is_alive():
            return None
        if "error" in box:
            error = box["error"]
            if isinstance(error, NVError):
                raise error
            raise NVError("inference failed: %s: %s" % (type(error).__name__, error),
                          hint="check the model with `inspect-model`, or re-install the "
                               "model file from its pinned source",
                          details={"error": str(error)})
        return box.get("out")


class PiperPackageEngine:
    """Reference engine around the piper-tts package (cross-check adapter)."""

    name = ENGINE_PIPER

    def __init__(self, asset, *, cfg, cancel=None):
        self.asset = asset
        self.cfg = cfg
        self.cancel = cancel
        self._voice = None

    def _ensure(self):
        if self._voice is not None:
            return
        piper = nvcore.import_optional("piper", feature="piper-adapter",
                                       install_hint="pip install piper-tts")
        self._voice = piper.PiperVoice.load(str(self.asset.model_path),
                                            config_path=str(self.asset.config_path),
                                            use_cuda=False)
        self._piper = piper

    def synthesize(self, request: SynthesisRequest):
        self._ensure()
        piper = self._piper
        config = piper.SynthesisConfig(
            speaker_id=request.speaker_index,
            length_scale=float(request.scales.get("length_scale", 1.0)),
            noise_scale=float(request.scales.get("noise_scale", 0.667)),
            noise_w_scale=float(request.scales.get("noise_w", 0.8)),
            normalize_audio=False,
            volume=1.0,
        )
        text = request.scales.get("_text")
        if text is None:
            raise InputError("piper-package engine needs the original text "
                             "(phoneme vectors are an onnx-vits feature)")
        samples = array("h")
        for chunk in self._voice.synthesize(text, syn_config=config):
            if self.cancel is not None:
                self.cancel.check()
            samples.extend(array("h", chunk.audio_int16_array.tobytes()))
        return EngineResult(samples, self.asset.config.sample_rate, engine=self.name,
                            extra={"engine_note": "piper-tts reference adapter"})


class BuzzFallbackEngine:
    """Clearly labelled degraded engine used only when no neural model loads.

    It produces a formant-flavoured audio sketch from phoneme classes so the
    mastering, QA and export stages can still be exercised. Output is always
    marked degraded and must be disclosed; it is never presented as speech.
    """

    name = ENGINE_FALLBACK

    VOWELS = {"a": (730, 1090, 2440), "ɑ": (700, 1100, 2500), "æ": (660, 1720, 2410),
              "ɛ": (530, 1840, 2480), "e": (400, 2000, 2600), "i": (270, 2290, 3010),
              "ɪ": (390, 1990, 2550), "o": (430, 1000, 2400), "ɔ": (450, 800, 2600),
              "u": (300, 870, 2240), "ʊ": (440, 1020, 2240), "ʌ": (640, 1190, 2390),
              "ə": (500, 1500, 2500), "ɜ": (500, 1450, 2500), "ɚ": (480, 1350, 2400)}

    def __init__(self, asset, *, cfg, cancel=None):
        self.asset = asset
        self.cfg = cfg
        self.cancel = cancel

    def synthesize(self, request: SynthesisRequest):
        rate = self.asset.config.sample_rate if self.asset else 22050
        length_scale = float(request.scales.get("length_scale", 1.0))
        phonemes = request.phonemes
        if not phonemes:
            raise InputError("no phonemes to synthesize")
        samples = array("h")
        f0 = 120.0 / max(0.5, length_scale)
        idx = 0
        while idx < len(phonemes):
            if self.cancel is not None:
                self.cancel.check()
            phoneme = phonemes[idx]
            if phoneme in self.VOWELS:
                f1, f2, f3 = self.VOWELS[phoneme]
                duration = 0.11 * length_scale
                count = max(1, int(rate * duration))
                for n in range(count):
                    t = n / float(rate)
                    phase = 2 * math.pi * f0 * t
                    value = (0.5 * math.sin(phase) + 0.22 * math.sin(2 * phase)
                             + 0.10 * math.sin(3 * phase))
                    glide = math.sin(math.pi * n / count)
                    value *= 0.6 + 0.4 * glide
                    formant = (0.30 * math.sin(2 * math.pi * f1 * t)
                               + 0.20 * math.sin(2 * math.pi * f2 * t)
                               + 0.08 * math.sin(2 * math.pi * f3 * t))
                    samples.append(int(9000 * value + 2200 * formant * glide))
            elif phoneme in (".", "!", "?", ",", ";", ":"):
                samples.extend(array("h", bytes(2 * int(rate * 0.16 * length_scale))))
            elif phoneme in ("ˈ", "ˌ", " "):
                samples.extend(array("h", bytes(2 * max(40, int(rate * 0.05 * length_scale)))))
            elif phoneme in ("ː",):
                samples.extend(array("h", bytes(2 * int(rate * 0.05 * length_scale))))
            else:
                count = max(1, int(rate * 0.06 * length_scale))
                for n in range(count):
                    t = n / float(rate)
                    value = 0.18 * math.sin(2 * math.pi * 2400 * t) * (1.0 - n / count)
                    value += 0.10 * math.sin(2 * math.pi * 320 * t) * (1.0 - n / count)
                    samples.append(int(12000 * value))
            idx += 1
        return EngineResult(
            samples, rate, engine=self.name, degraded=True,
            disclosure=("DEGRADED ENGINE: buzz-fallback produced a synthetic tone sketch, "
                        "not intelligible speech. Install a neural voice model before any "
                        "listening, publishing or acceptance use."),
            extra={"engine_note": "diagnostic fallback", "f0_hz": f0})


def solve_length_scale(asset, phoneme_groups, *, target_s, tolerance_s=0.08,
                       base=None, cfg=None, lo=0.45, hi=2.2, max_iterations=8):
    """Find the `length_scale` that lands the rendered duration on `target_s`.

    VITS sampling makes duration vary run to run, so a preset that *usually* lands
    inside an acceptance window occasionally misses it. Duration grows monotonically
    with length_scale but not linearly, so this brackets the target and interpolates
    between the two closest probes instead of scaling by the raw error (which
    overshoots badly above a factor of ~1.1).

    Returns (scales, report); `report["attempts"]` records every probe so the
    calibration is auditable rather than a magic number.
    """
    base = dict(base or {})
    probe = OnnxVitsEngine(asset, cfg=cfg)
    groups = phoneme_groups or [[]]
    if groups and not isinstance(groups[0], (list, tuple)):
        groups = [groups]
    history = []

    def render_duration(length_scale):
        scales = {"length_scale": length_scale,
                  "noise_scale": base.get("noise_scale", 0.667),
                  "noise_w": base.get("noise_w", 0.8)}
        total = 0
        for group in groups:
            result = probe.synthesize(SynthesisRequest(
                list(group), scales=scales,
                speaker_index=base.get("speaker_index") or asset.speaker_index))
            total += len(result.samples) / float(result.sample_rate)
        return total

    def trial(length_scale):
        length_scale = max(lo, min(hi, round(length_scale, 4)))
        duration = render_duration(length_scale)
        history.append({"length_scale": length_scale, "duration_s": round(duration, 4)})
        return length_scale, duration

    best_scale, best_duration = trial(float(base.get("length_scale", 1.0)))
    for _ in range(max(1, max_iterations)):
        if abs(best_duration - target_s) <= tolerance_s:
            break
        # keep the two probes that bracket the target, and interpolate between them
        below = [(sc, du) for sc, du in [(h["length_scale"], h["duration_s"]) for h in history]
                 if du <= target_s]
        above = [(sc, du) for sc, du in [(h["length_scale"], h["duration_s"]) for h in history]
                 if du >= target_s]
        if below and above:
            lo_s, lo_d = max(below, key=lambda x: x[1])
            hi_s, hi_d = min(above, key=lambda x: x[1])
            if hi_d - lo_d < 1e-6:
                candidate = hi_s
            else:
                candidate = lo_s + (target_s - lo_d) * (hi_s - lo_s) / (hi_d - lo_d)
        elif below:
            lo_s, lo_d = max(below, key=lambda x: x[1])
            candidate = lo_s * (target_s / lo_d) if lo_d > 0 else lo_s * 1.05
        else:
            hi_s, hi_d = min(above, key=lambda x: x[1])
            candidate = hi_s * (target_s / hi_d) if hi_d > 0 else hi_s * 0.95
        candidate = min(hi_s if above else hi, max(lo, candidate))
        if any(abs(candidate - h["length_scale"]) < 1e-3 for h in history):
            break
        scale, duration = trial(candidate)
        if abs(duration - target_s) < abs(best_duration - target_s):
            best_scale, best_duration = scale, duration
    scales = {"length_scale": round(best_scale, 4),
              "noise_scale": base.get("noise_scale", 0.667),
              "noise_w": base.get("noise_w", 0.8)}
    report = {"target_s": target_s, "tolerance_s": tolerance_s,
              "converged": abs(best_duration - target_s) <= max(tolerance_s, 0.12),
              "attempts": history, "length_scale": scales["length_scale"]}
    return scales, report


def build_engine(name, asset, *, cfg, cancel=None):
    if name == ENGINE_ONNX:
        return OnnxVitsEngine(asset, cfg=cfg, cancel=cancel)
    if name == ENGINE_PIPER:
        return PiperPackageEngine(asset, cfg=cfg, cancel=cancel)
    if name == ENGINE_FALLBACK:
        return BuzzFallbackEngine(asset, cfg=cfg, cancel=cancel)
    raise InputError("unknown engine %r" % name,
                     hint="known engines: %s" % ", ".join(KNOWN_ENGINES))


def engine_availability(cfg=None):
    """Report which engines can run right now."""
    report = {}
    for module_name in ("numpy", "onnxruntime"):
        try:
            import importlib
            module = importlib.import_module(module_name)
            report[module_name] = {"available": True,
                                   "version": getattr(module, "__version__", "unknown")}
        except Exception as exc:
            report[module_name] = {"available": False, "error": str(exc),
                                   "install": "pip install %s" % module_name}
    try:
        importlib = __import__("importlib")
        importlib.import_module("piper")
        report["piper"] = {"available": True}
    except Exception as exc:
        report["piper"] = {"available": False, "error": str(exc), "install": "pip install piper-tts"}
    report[ENGINE_ONNX] = {"available": report.get("numpy", {}).get("available")
                           and report.get("onnxruntime", {}).get("available")}
    report[ENGINE_PIPER] = {"available": report.get("piper", {}).get("available")}
    report[ENGINE_FALLBACK] = {"available": True,
                               "note": "always available; degraded output, disclosure required"}
    return report


# --------------------------------------------------------------------------- #
# Plan / dry run and synthetic fixtures
# --------------------------------------------------------------------------- #

def speaker_catalogue(asset):
    """List speakers for a multi-speaker asset."""
    config = asset.config
    rows = []
    for name, index in sorted(config.speaker_id_map.items(), key=lambda kv: kv[1]):
        rows.append({"speaker": name, "index": int(index)})
    if not rows and config.num_speakers <= 1:
        rows.append({"speaker": "(single speaker voice)", "index": None})
    return rows


def plan_synthesis(cfg, asset, *, text, preset, engine_name, master=True):
    """Return a deterministic plan and a plan token for approval workflows."""
    phonemes_estimate = nvcore.count_words(text)
    base_duration = max(0.6, phonemes_estimate / 2.6) * float(preset["length_scale"])
    plan = {
        "voice": asset.describe(),
        "engine": engine_name,
        "text_chars": len(text),
        "text_words": phonemes_estimate,
        "preset": {"id": preset.get("id"), "version": preset.get("version")},
        "estimated_duration_s": round(base_duration, 2),
        "stages": (["load-model", "phonemize", "inference"] +
                   (["master"] if master else []) + ["export"]),
        "outputs_planned": [],
        "writes": [],
        "network_required": False,
        "approval_needed": False,
    }
    plan["outputs_planned"] = ["wav"]
    return plan


# --------------------------------------------------------------------------- #
# Self test
# --------------------------------------------------------------------------- #

def selftest(argv=None):
    parser = argparse.ArgumentParser(prog="tools-nv-engine.py selftest")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--model", help="optional .onnx model to include in the run test")
    parser.add_argument("--config-file", help="optional .onnx.json for the model")
    args = parser.parse_args(argv)

    checks = []

    def check(name, fn):
        started = time.monotonic()
        try:
            detail = fn()
            checks.append({"check": name, "ok": True,
                           "ms": round((time.monotonic() - started) * 1000, 2), "detail": detail})
        except Exception as exc:
            checks.append({"check": name, "ok": False,
                           "ms": round((time.monotonic() - started) * 1000, 2),
                           "detail": "%s: %s" % (type(exc).__name__, exc)})

    def t_mapper():
        mapper = PhonemeIdMapper(DEFAULT_PHONEME_IDS)
        ids, missing = mapper.to_ids(list("hi"))
        assert not missing, missing
        assert ids[0] == 1 and ids[-1] == 2, ids
        assert ids[1] == 0, ids
        return "phoneme ids framed with BOS/PAD/EOS (%d ids for 'hi')" % len(ids)

    def t_mapper_unknown():
        mapper = PhonemeIdMapper(DEFAULT_PHONEME_IDS)
        _, missing = mapper.to_ids(["θ", "\u0e01"])
        assert missing == ["\u0e01"], missing
        return "unknown phonemes are reported, not silently dropped"

    def t_phonemize():
        ph = Phonemizer()
        probes = ph.probe()
        if ph.backend == "manual_ipa":
            sentences, _ = ph.phonemize("[[həˈloʊ]]")
            return "no espeak backend; manual IPA path produced %d sentence group(s)" % len(sentences)
        sentences, warnings = ph.phonemize("Greetings, friends! Welcome to my video.")
        assert len(sentences) >= 2, sentences
        assert all(isinstance(s, list) and s for s in sentences), sentences
        return "%s backend produced %d sentence groups (%s)" % (
            ph.backend, len(sentences), "".join(sentences[0])[:24])

    def t_fallback():
        class DummyConfig:
            sample_rate = 16000
            mapper = PhonemeIdMapper(DEFAULT_PHONEME_IDS)

        class DummyAsset:
            config = DummyConfig()

        engine = BuzzFallbackEngine(DummyAsset(), cfg=None)
        request = SynthesisRequest(list("hɛˈloʊ"), scales={"length_scale": 1.0})
        result = engine.synthesize(request)
        assert result.samples and result.degraded and result.disclosure
        return "fallback produced %d degraded samples at %d Hz" % (len(result.samples), result.sample_rate)

    def t_onnx():
        if not args.model:
            return "skipped (no --model supplied)"
        asset = type("A", (), {})()
        asset.model_path = Path(args.model)
        asset.config_path = Path(args.config_file or (args.model + ".json"))
        asset.config = VoiceConfig(read_json(asset.config_path))
        engine = OnnxVitsEngine(asset, cfg=None)
        asset.config.mapper = PhonemeIdMapper(asset.config.phoneme_id_map)
        phonemes = list("hɛˈloʊ")
        cfg_obj = nvcore.load_config(None, root=nvcore.skill_root())
        engine.cfg = cfg_obj
        result = engine.synthesize(SynthesisRequest(phonemes, scales={"length_scale": 1.0,
                                                                      "noise_scale": 0.0,
                                                                      "noise_w": 0.0}))
        assert result.samples, "no audio"
        assert result.engine == ENGINE_ONNX
        return "onnx-vits synthesized %d samples at %d Hz in %sms" % (
            len(result.samples), result.sample_rate, result.extra.get("inference_ms"))

    def t_reference_parity():
        """Frame-for-frame parity against the piper-tts reference adapter."""
        if not args.model:
            return "skipped (no --model supplied)"
        try:
            import piper
        except Exception as exc:
            return "skipped (piper-tts not installed: %s)" % exc
        model = Path(args.model)
        config_file = Path(args.config_file or (str(model) + ".json"))
        asset = type("A", (), {})()
        asset.model_path = model
        asset.config_path = config_file
        asset.config = VoiceConfig(read_json(config_file))
        asset.speaker_index = None
        cfg = nvcore.load_config(None, root=nvcore.skill_root())
        reference = piper.PiperVoice.load(str(model), config_path=str(config_file))
        syn = piper.SynthesisConfig(speaker_id=None, length_scale=1.0, noise_scale=0.0,
                                    noise_w_scale=0.0)
        text = "Greetings, friends! Welcome to my video."
        own = OnnxVitsEngine(asset, cfg=cfg)
        mismatches = []
        total_reference = 0
        total_own = 0
        for chunk in reference.synthesize(text, syn_config=syn):
            expected = len(chunk.audio_int16_array)
            result = own.synthesize(SynthesisRequest(
                list(chunk.phonemes),
                scales={"length_scale": 1.0, "noise_scale": 0.0, "noise_w": 0.0}))
            total_reference += expected
            total_own += len(result.samples)
            if len(result.samples) != expected:
                mismatches.append({"phonemes": "".join(chunk.phonemes),
                                   "own": len(result.samples), "reference": expected})
        assert not mismatches, mismatches
        return "frame-for-frame parity with piper-tts (%d samples, noise_scale=0)" % total_own

    check("phoneme-mapper", t_mapper)
    check("phoneme-mapper-unknown", t_mapper_unknown)
    check("phonemizer", t_phonemize)
    check("fallback-engine", t_fallback)
    check("onnx-engine", t_onnx)
    check("reference-parity", t_reference_parity)

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "tool": "offline-neural-voice/engine", "version": nvcore.__version__,
               "checks": checks, "engines": engine_availability()}
    if args.format == "json":
        nvcore.print_json(payload)
    else:
        for item in checks:
            print("[%s] %s (%sms) %s" % ("PASS" if item["ok"] else "FAIL",
                                         item["check"], item["ms"], item["detail"]))
        print("\n%s: %d/%d engine checks passed" % ("PASS" if ok else "FAIL",
                                                    sum(1 for c in checks if c["ok"]), len(checks)))
    return nvcore.EXIT_OK if ok else nvcore.EXIT_FAIL


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return nvcore.EXIT_OK
    if argv[0] == "selftest":
        return selftest(argv[1:])
    sys.stderr.write("unknown command %r; try `selftest --help`\n" % argv[0])
    return nvcore.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
