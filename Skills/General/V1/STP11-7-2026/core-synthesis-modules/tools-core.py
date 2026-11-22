#!/usr/bin/env python3
"""tools-core.py — shared core for the core-synthesis-modules Skill Tool.

Implements the reusable building blocks shared by the CLI, providers, audio
pipeline, and contract tests:

  * Structured Error Module (structured errors, exit codes, error registry)
  * Configuration Loader (default config + user overrides + validation)
  * Tokenizer (local vocabulary encode/decode with word spans)
  * Phonemizer (dictionary overrides + rule-based G2P fallback)
  * Voice Presets (friendly-male-30s and friends, validation)
  * Sentence Chunker (long-text chunking)
  * Prosody Planner (pace/pitch/pause/emphasis planning from preset rules)
  * Model Loader (path resolution, checksum integrity, capability report)

All paths are resolved relative to this file's directory (the future skill
root), so the folder can be copied beside a parent SKILL.md unchanged.
Standard library only. No network access.
"""

import hashlib
import json
import os
import re
from math import log10

SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))


def skill_root():
    return SKILL_ROOT


def asset_path(name):
    """Resolve an asset file name relative to the skill root."""
    return os.path.join(SKILL_ROOT, name)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Structured Error Module
# --------------------------------------------------------------------------

# Exit code standard (used by the CLI layer):
#   0 success | 1 internal | 2 usage | 3 input validation | 4 provider failure
#   5 audio validation failure | 6 acceptance failure | 7 unsupported/disabled
EXIT_CODES = {
    "SUCCESS": 0,
    "INTERNAL": 1,
    "USAGE": 2,
    "INPUT_VALIDATION": 3,
    "CONFIG": 3,
    "PROVIDER_UNAVAILABLE": 4,
    "PROVIDER_FAILURE": 4,
    "MODEL_NOT_FOUND": 4,
    "MODEL_INTEGRITY": 4,
    "TIMEOUT": 4,
    "AUDIO_INVALID": 5,
    "AUDIO_VALIDATION_FAILED": 5,
    "APPROVAL_REQUIRED": 7,
    "UNSUPPORTED": 7,
    "ACCEPTANCE_FAILED": 6,
}

ERROR_REGISTRY = {
    "INPUT_VALIDATION": {
        "description": "The supplied input is missing, malformed, or out of limits.",
        "suggestions": ["Check the value passed for this option.",
                        "See knowledge-readme.md for limits and accepted formats."],
    },
    "CONFIG": {
        "description": "The configuration file or a config value is invalid.",
        "suggestions": ["Validate with: python3 tools-voice.py config --check",
                        "Compare against asset-default-config.json."],
    },
    "PROVIDER_UNAVAILABLE": {
        "description": "A required provider (engine) is not installed or enabled.",
        "suggestions": ["Install the optional runtime (see knowledge-readme.md).",
                        "Use --provider to select an available provider.",
                        "The router can fall back to the next provider."],
    },
    "PROVIDER_FAILURE": {
        "description": "The provider ran but synthesis failed.",
        "suggestions": ["Inspect the provider error details.",
                        "Retry with a smaller text or a different provider."],
    },
    "MODEL_NOT_FOUND": {
        "description": "The model file or directory was not found.",
        "suggestions": ["Check provider.model_path in the config.",
                        "Place the ONNX model in the configured model directory."],
    },
    "MODEL_INTEGRITY": {
        "description": "Model checksum or shard integrity check failed.",
        "suggestions": ["Re-download or restore the model from a trusted source.",
                        "Run model-loader to see the expected vs actual hash."],
    },
    "TIMEOUT": {
        "description": "A synthesis or provider operation exceeded its deadline.",
        "suggestions": ["Raise limits.synthesis_timeout_seconds in the config.",
                        "Shorten the input text."],
    },
    "AUDIO_INVALID": {
        "description": "The audio file could not be read or is malformed.",
        "suggestions": ["Verify the WAV file with: python3 tools-voice.py audio --in FILE --check",
                        "Re-run synthesis to regenerate output."],
    },
    "AUDIO_VALIDATION_FAILED": {
        "description": "The audio failed a quality validation check.",
        "suggestions": ["Inspect the checks in the report.",
                        "Re-run with mastering enabled (default)."],
    },
    "APPROVAL_REQUIRED": {
        "description": "An action requires explicit approval (e.g., overwriting).",
        "suggestions": ["Pass the required approval flag (e.g., --overwrite)."],
    },
    "UNSUPPORTED": {
        "description": "The requested capability or format is not supported here.",
        "suggestions": ["See knowledge-readme.md for supported formats and limits."],
    },
    "ACCEPTANCE_FAILED": {
        "description": "One or more acceptance gate checks failed.",
        "suggestions": ["Review the acceptance report for the failed check.",
                        "Human listening checks are reported separately and cannot be automated."],
    },
    "INTERNAL": {
        "description": "An unexpected internal error occurred.",
        "suggestions": ["Re-run the command; report the traceback if it persists."],
    },
}


class SkillToolError(Exception):
    """Structured error carrying a stable code, message, and optional details."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "exit_code": EXIT_CODES.get(self.code, 1),
            },
            "registry": ERROR_REGISTRY.get(self.code, {}),
        }

    def __str__(self):
        return "%s: %s" % (self.code, self.message)


# --------------------------------------------------------------------------
# Configuration Loader
# --------------------------------------------------------------------------

def deep_merge(base, override):
    """Recursively merge override into base (override wins)."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(config_path=None, overrides=None):
    """Load default config, merge a user config file, merge overrides, validate."""
    cfg = load_json(asset_path("asset-default-config.json"))
    if config_path:
        if not os.path.exists(config_path):
            raise SkillToolError("CONFIG", "config file not found: %s" % config_path)
        cfg = deep_merge(cfg, load_json(config_path))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    errors = validate_config(cfg)
    if errors:
        raise SkillToolError("CONFIG", "invalid configuration", {"errors": errors})
    return cfg


def validate_config(cfg):
    """Validate the config structure. Returns a list of error strings."""
    errors = []
    if not isinstance(cfg, dict):
        return ["config must be a JSON object"]
    for key in ("audio", "providers", "limits", "preset_default"):
        if key not in cfg:
            errors.append("missing required key: %s" % key)
    audio = cfg.get("audio", {})
    if not isinstance(audio, dict):
        errors.append("audio must be an object")
    else:
        sr = audio.get("sample_rate")
        if not isinstance(sr, int) or sr <= 0:
            errors.append("audio.sample_rate must be a positive integer")
        if audio.get("target_peak_dbfs", 0) > 0:
            errors.append("audio.target_peak_dbfs must be <= 0 dBFS")
    providers = cfg.get("providers", {})
    order = providers.get("order", [])
    for name in order:
        if name not in providers:
            errors.append("providers.order references unknown provider: %s" % name)
    for name, pcfg in providers.items():
        if isinstance(pcfg, dict) and "enabled" in pcfg and not isinstance(pcfg["enabled"], bool):
            errors.append("providers.%s.enabled must be boolean" % name)
    preset = cfg.get("preset_default")
    if preset and not isinstance(preset, str):
        errors.append("preset_default must be a string")
    return errors


# --------------------------------------------------------------------------
# Tokenizer (local vocabulary)
# --------------------------------------------------------------------------

class Tokenizer:
    """Char-level tokenizer backed by asset-vocabulary.json."""

    UNKNOWN_TOKEN = "<unk>"

    def __init__(self, vocab_path=None):
        path = vocab_path or asset_path("asset-vocabulary.json")
        data = load_json(path)
        self.tokens = data["tokens"]            # token string -> id
        self.id_to_token = {v: k for k, v in self.tokens.items()}
        self.unk_id = self.tokens.get(self.UNKNOWN_TOKEN)
        self.space_id = self.tokens.get(" ")
        self.vocab_size = len(self.tokens)

    def encode(self, text):
        """Encode text to a list of token ids. Unknown chars map to <unk>."""
        ids = []
        for ch in text:
            tid = self.tokens.get(ch, self.unk_id)
            if tid is None:
                raise SkillToolError("INPUT_VALIDATION",
                                     "vocabulary has no unknown-token fallback",
                                     {"character": ch})
            ids.append(tid)
        return ids

    def decode(self, ids):
        chars = []
        for tid in ids:
            token = self.id_to_token.get(tid)
            if token is None:
                token = self.UNKNOWN_TOKEN
            chars.append(token)
        return "".join(chars)

    def encode_with_spans(self, text):
        """Encode with word alignment.

        Returns (ids, spans) where spans[i] = (word_index, char_position).
        Words are maximal runs of non-space characters; a space token is
        attached to the word it follows (word 0 for leading spaces).
        """
        ids = []
        spans = []
        word_id = 0
        i = 0
        n = len(text)
        while i < n:
            if text[i] == " ":
                tid = self.tokens.get(" ", self.unk_id)
                ids.append(tid)
                spans.append((max(0, word_id - 1), i))
                i += 1
                continue
            while i < n and text[i] != " ":
                tid = self.tokens.get(text[i], self.unk_id)
                ids.append(tid)
                spans.append((word_id, i))
                i += 1
            word_id += 1
        return ids, spans


# --------------------------------------------------------------------------
# Phonemizer + Pronunciation (dictionary overrides + rule-based G2P)
# --------------------------------------------------------------------------

class Phonemizer:
    """Rule-based G2P fallback plus dictionary overrides.

    Production deployments may plug in espeak-ng / phonemizer (optional
    integrations, see knowledge-readme.md). This fallback is deterministic
    and offline.
    """

    # vowel digraphs (checked before single vowels)
    DIGRAPHS = [
        ("ai", "EY"), ("ay", "EY"), ("ea", "IY"), ("ee", "IY"), ("ei", "EY"),
        ("ey", "EY"), ("oa", "OW"), ("oe", "OW"), ("oo", "UW"), ("ou", "AW"),
        ("ow", "OW"), ("oi", "OY"), ("oy", "OY"), ("au", "AO"), ("aw", "AO"),
        ("ie", "IY"), ("igh", "AY"), ("ue", "UW"), ("ui", "UW"),
    ]
    VOWELS = {"a": "AH", "e": "EH", "i": "IH", "o": "AA", "u": "AH", "y": "IH"}
    CONSONANTS = {
        "b": "B", "c": "K", "d": "D", "f": "F", "g": "G", "h": "HH",
        "j": "JH", "k": "K", "l": "L", "m": "M", "n": "N", "p": "P",
        "q": "K", "r": "R", "s": "S", "t": "T", "v": "V", "w": "W",
        "x": "K S", "z": "Z",
    }

    def __init__(self, dict_path=None):
        path = dict_path or asset_path("asset-pronunciation-dictionary.json")
        data = load_json(path)
        self.acronyms = {k.lower(): v for k, v in data.get("acronyms", {}).items()}
        self.overrides = {k.lower(): v for k, v in data.get("overrides", {}).items()}

    def apply_dictionary(self, text):
        """Expand acronyms and apply pronunciation overrides to text.

        Returns (expanded_text, applied) where applied lists the substitutions.
        """
        applied = []
        words = re.findall(r"[A-Za-z0-9]+(?:[.'-][A-Za-z0-9]+)*", text)
        lower_map = {w.lower(): w for w in words}
        for w in words:
            low = w.lower()
            if low in self.overrides:
                expanded = self.overrides[low]
                text = text.replace(w, expanded)
                applied.append({"word": w, "replacement": expanded, "rule": "override"})
            elif low in self.acronyms and len(w) > 1 and w.isalpha():
                expanded = self.acronyms[low]
                text = text.replace(w, expanded)
                applied.append({"word": w, "replacement": expanded, "rule": "acronym"})
        return text, applied

    def word_to_phonemes(self, word):
        """Convert a single word to a phoneme list (deterministic fallback G2P)."""
        word = word.lower()
        word = re.sub(r"[^a-z]", "", word)
        if not word:
            return []
        phonemes = []
        i = 0
        while i < len(word):
            ch = word[i]
            for digraph, ph in self.DIGRAPHS:
                if word[i:i + len(digraph)] == digraph:
                    phonemes.append(ph)
                    i += len(digraph)
                    break
            else:
                if ch in self.CONSONANTS:
                    phonemes.append(self.CONSONANTS[ch])
                elif ch in self.VOWELS:
                    # silent trailing e (e.g., "greetings", "welcome")
                    if ch == "e" and i == len(word) - 1 and len(word) > 2 and word[-2] in self.VOWELS:
                        pass  # silent e
                    else:
                        phonemes.append(self.VOWELS[ch])
                elif ch == "c":
                    next_ch = word[i + 1] if i + 1 < len(word) else ""
                    phonemes.append("S" if next_ch in "eiy" else "K")
                    continue
                elif ch == "g":
                    next_ch = word[i + 1] if i + 1 < len(word) else ""
                    phonemes.append("JH" if next_ch in "eiy" else "G")
                    continue
                elif ch == "s":
                    next_ch = word[i + 1] if i + 1 < len(word) else ""
                    phonemes.append("Z" if next_ch in "eiy" else "S")
                    continue
                i += 1
        return phonemes

    def phonemize(self, text):
        """Convert text to a phoneme sequence (spaces -> pause markers)."""
        text, _ = self.apply_dictionary(text)
        words = text.split()
        seq = []
        for w_i, word in enumerate(words):
            if w_i > 0:
                seq.append("_")  # word boundary pause marker
            for ph in self.word_to_phonemes(word):
                seq.append(ph)
        return seq


# --------------------------------------------------------------------------
# Voice Presets
# --------------------------------------------------------------------------

class VoicePresets:
    """Loads and validates voice presets from asset-voice-presets.json."""

    def __init__(self, presets_path=None):
        path = presets_path or asset_path("asset-voice-presets.json")
        data = load_json(path)
        self.presets = data["presets"]

    def list(self):
        return sorted(self.presets.keys())

    def get(self, name):
        if name not in self.presets:
            raise SkillToolError("INPUT_VALIDATION", "unknown voice preset: %s" % name,
                                 {"available": self.list()})
        return self.presets[name]

    def validate(self, preset):
        """Validate a preset against its own validation ranges. Returns errors."""
        errors = []
        v = preset.get("voice", {})
        for key in ("gender", "age_style", "tone", "emotion", "delivery"):
            if not v.get(key):
                errors.append("preset voice.%s is required" % key)
        validation = preset.get("validation", {})
        prosody = preset.get("prosody", {})
        rate = prosody.get("rate")
        if rate is not None:
            lo = validation.get("min_rate", 0.0)
            hi = validation.get("max_rate", 2.0)
            if not (lo <= rate <= hi):
                errors.append("prosody.rate %s outside [%s, %s]" % (rate, lo, hi))
        pitch = prosody.get("pitch")
        if pitch is not None:
            lo = validation.get("min_pitch", 0.0)
            hi = validation.get("max_pitch", 2.0)
            if not (lo <= pitch <= hi):
                errors.append("prosody.pitch %s outside [%s, %s]" % (pitch, lo, hi))
        smile = v.get("smile_effect")
        if smile is not None:
            lo, hi = validation.get("smile_range", [0.0, 1.0])
            if not (lo <= smile <= hi):
                errors.append("voice.smile_effect %s outside [%s, %s]" % (smile, lo, hi))
        return errors


# --------------------------------------------------------------------------
# Sentence Chunker
# --------------------------------------------------------------------------

def chunk_text(text, max_chars=None, max_tokens=None, overlap_chars=0):
    """Split long text into punctuation-aware chunks.

    Chunks break at sentence boundaries (. ! ?) when possible; otherwise at
    commas; otherwise at word boundaries. Returns a list of chunk strings.
    """
    if max_chars is None and max_tokens is None:
        return [text]
    max_chars = max_chars or 10 ** 9
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be in [0, max_chars)")

    text = text.replace("\r\n", "\n").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + (1 if current else 0) <= max_chars:
            current = (current + " " + sent).strip()
            continue
        if current:
            chunks.append(current)
            carry = current[-overlap_chars:] if overlap_chars else ""
            current = (carry + " " + sent).strip()
        else:
            # sentence alone too long: split at commas/words
            pieces = _split_long(sent, max_chars)
            chunks.extend(pieces[:-1])
            current = pieces[-1]
    if current:
        chunks.append(current)
    if max_tokens is not None:
        filtered = []
        for c in chunks:
            if len(c.split()) <= max_tokens:
                filtered.append(c)
            else:
                filtered.extend(_split_long(c, max_chars))
        chunks = filtered
    return chunks


def _split_long(text, max_chars):
    pieces = []
    while len(text) > max_chars:
        window = text[:max_chars]
        cut = max(window.rfind(","), window.rfind(" "))
        if cut <= 0:
            cut = max_chars
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


# --------------------------------------------------------------------------
# Prosody Planner (pace, pitch, pause, emphasis)
# --------------------------------------------------------------------------

def plan_utterance(text, preset):
    """Plan an utterance from text + preset.

    Returns a plan dict:
      words: [{text, word_index, gain, duration_ms, pause_before_ms, pause_after_ms,
               start_ms, end_ms}]
      total_ms
    Pause and emphasis rules from the preset (e.g., greeting pause after
    "friends", emphasis on "welcome") are applied.
    """
    prosody = preset.get("prosody", {})
    char_ms = float(prosody.get("char_ms", 50.0))
    word_gap_ms = float(prosody.get("word_gap_ms", 40.0))
    min_word_ms = float(prosody.get("min_word_ms", 120.0))
    comma_pause = float(prosody.get("pause_after_comma_ms", 150.0))
    sentence_pause = float(prosody.get("pause_after_sentence_ms", 300.0))

    rules = preset.get("rules", [])
    text = " ".join(str(text).split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    words_out = []
    cursor_ms = 0.0
    word_idx = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        for w_i, word in enumerate(re.findall(r"\S+", sent)):
            lower = word.lower().strip(".,!?")
            gain = 1.0
            pause_after = 0.0
            if word.endswith(","):
                pause_after = comma_pause
            if re.search(r"[.!?]$", word):
                pause_after = max(pause_after, sentence_pause)
            for rule in rules:
                trigger = str(rule.get("trigger_word", "")).lower()
                if trigger and trigger in lower:
                    action = rule.get("action")
                    if action == "pause":
                        pause_after = max(pause_after, float(rule.get("duration_ms", 200)))
                    elif action == "emphasis":
                        gain = max(gain, float(rule.get("gain", 1.2)))
            core = re.sub(r"[^A-Za-z0-9]", "", word) or word
            duration = max(min_word_ms, char_ms * len(core)) + word_gap_ms
            entry = {
                "text": word,
                "word_index": word_idx,
                "gain": round(gain, 3),
                "duration_ms": round(duration, 1),
                "pause_before_ms": 0.0,
                "pause_after_ms": round(pause_after, 1),
                "start_ms": round(cursor_ms, 1),
                "end_ms": round(cursor_ms + duration, 1),
            }
            words_out.append(entry)
            word_idx += 1
            cursor_ms += duration + pause_after

    total_ms = cursor_ms
    return {"words": words_out, "total_ms": round(total_ms, 1),
            "text": text, "preset": preset.get("display_name", "unnamed")}


# --------------------------------------------------------------------------
# Model Loader
# --------------------------------------------------------------------------

class ModelLoader:
    """Resolves, validates, and describes model assets."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.providers_cfg = self.cfg["providers"]

    def resolve(self, provider):
        """Resolve a provider's model path. Returns dict with path + metadata."""
        pcfg = self.providers_cfg.get(provider)
        if pcfg is None:
            raise SkillToolError("INPUT_VALIDATION", "unknown provider: %s" % provider)
        if not pcfg.get("enabled", True):
            raise SkillToolError("UNSUPPORTED", "provider disabled in config: %s" % provider)
        model_path = pcfg.get("model_path")
        if not model_path:
            raise SkillToolError("MODEL_NOT_FOUND", "provider %s has no model_path" % provider)
        if not os.path.isabs(model_path):
            model_path = asset_path(model_path)
        if not os.path.exists(model_path):
            raise SkillToolError("MODEL_NOT_FOUND", "model file not found: %s" % model_path,
                                 {"path": model_path})
        info = {
            "provider": provider,
            "path": model_path,
            "name": pcfg.get("model_name", os.path.basename(model_path)),
            "size_bytes": os.path.getsize(model_path),
            "expected_sha256": pcfg.get("expected_sha256"),
        }
        return info

    def verify(self, info):
        """Verify checksum integrity. Returns dict with verified + actual hash."""
        actual = sha256_file(info["path"])
        expected = info.get("expected_sha256")
        verified = bool(expected) and actual == expected
        return {
            "path": info["path"],
            "actual_sha256": actual,
            "expected_sha256": expected,
            "verified": verified,
            "ok": verified or expected is None,
        }

    def capabilities(self):
        """Report what each configured provider can do right now."""
        report = {}
        for name, pcfg in self.providers_cfg.items():
            if not isinstance(pcfg, dict):
                continue  # e.g. the "order" list
            if not pcfg.get("enabled", True):
                report[name] = {"enabled": False}
                continue
            entry = {"enabled": True}
            if "model_path" in pcfg:
                try:
                    info = self.resolve(name)
                    entry["model"] = {
                        "name": info["name"],
                        "found": True,
                        "size_bytes": info["size_bytes"],
                    }
                    entry["integrity"] = self.verify(info)["ok"]
                except SkillToolError as exc:
                    entry["model"] = {"found": False, "reason": exc.message}
                    entry["integrity"] = None
            report[name] = entry
        return report
