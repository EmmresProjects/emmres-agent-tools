#!/usr/bin/env python3
"""tools-nvcore.py - shared core runtime for the Offline Neural Voice Skill Tool.

This module is not an entry point. It provides the facilities every tool-* file
reuses: configuration loading, sealed path guards, the offline network guard,
exit codes, structured result envelopes, atomic writes, JSON schema validation,
model/voice/tokenizer/opset inspection, WAV I/O, EBU R128 style loudness and true
peak measurement, MP3 encode/decode, keyboard cancellation and the append-only
audit journal with an interrupted-operation recovery scan.

Runtime: Python 3.9+ standard library only.
Import:  import nvcore  (the loader tools-nvcore.py registers the module name)

Command line:  python tools-nvcore.py selftest
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import struct
import sys
import tempfile
import time
import traceback
import unicodedata
import wave
import zlib
from array import array
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

__tool__ = "offline-neural-voice/nvcore"
__version__ = "1.0.0"

SKILL_SLUG = "offline-neural-voice"
SKILL_TITLE = "Offline Neural Voice"

#: Version of the configuration schema understood by this runtime.
CONFIG_SCHEMA_VERSION = 1

#: Version of the structured result envelope.
RESULT_SCHEMA_VERSION = 1

#: Exit codes (also published by `exit-codes`).
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_DEPENDENCY = 3
EXIT_INPUT = 4
EXIT_APPROVAL = 5
EXIT_CANCELLED = 130
EXIT_INTERRUPTED = 143

EXIT_NAMES = {
    EXIT_OK: "ok",
    EXIT_FAIL: "fail",
    EXIT_USAGE: "usage",
    EXIT_DEPENDENCY: "dependency",
    EXIT_INPUT: "input",
    EXIT_APPROVAL: "approval-required",
    EXIT_CANCELLED: "cancelled",
    EXIT_INTERRUPTED: "interrupted",
}

#: Files owned by this Skill Tool, used to recognise the skill root.
MARKER_FILES = ("tools-nvcore.py", "tools-nv-voice.py", "knowledge-readme.md")

#: Files that must never be accepted as a model, tokenizer or speaker asset.
MODEL_EXTENSIONS = (".onnx", ".onnx.json")

TRUE_TOKENS = {"1", "true", "yes", "on", "y", "t"}
FALSE_TOKENS = {"0", "false", "no", "off", "n", "f", ""}

DEFAULT_SAMPLE_RATE = 22050
DEFAULT_SYNTH_TIMEOUT_S = 120.0
DEFAULT_RUN_TIMEOUT_S = 900.0
DEFAULT_MAX_CHARS = 4000
DEFAULT_JOURNAL_MAX_BYTES = 2_000_000


# --------------------------------------------------------------------------- #
# Structured errors
# --------------------------------------------------------------------------- #

class NVError(Exception):
    """Base class for expected, user-visible failures."""

    code = "error"
    exit_code = EXIT_FAIL

    def __init__(self, message, *, code=None, hint=None, details=None):
        super().__init__(message)
        self.message = str(message)
        if code:
            self.code = code
        self.hint = hint
        self.details = details or {}

    def to_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }


class UsageError(NVError):
    code = "usage-error"
    exit_code = EXIT_USAGE


class InputError(NVError):
    code = "invalid-input"
    exit_code = EXIT_INPUT


class DependencyError(NVError):
    code = "missing-dependency"
    exit_code = EXIT_DEPENDENCY


class ApprovalError(NVError):
    code = "approval-required"
    exit_code = EXIT_APPROVAL


class CancelledError(NVError):
    code = "cancelled"
    exit_code = EXIT_CANCELLED


class PolicyError(NVError):
    """Raised when a rights, consent or safety gate blocks the request."""

    code = "policy-blocked"
    exit_code = EXIT_FAIL


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def utc_now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text, *, fallback="item", max_len=48):
    """Deterministic lowercase hyphenated slug for filenames."""
    norm = unicodedata.normalize("NFKD", str(text))
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    ascii_text = re.sub(r"-{2,}", "-", ascii_text)
    if not ascii_text:
        digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]
        ascii_text = "%s-%s" % (fallback, digest)
    return ascii_text[:max_len].strip("-") or fallback


def parse_bool(value, *, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    raise InputError("expected a boolean, got %r" % (value,),
                     hint="use one of: true/false, yes/no, 1/0")


def as_number(value, *, name="value", minimum=None, maximum=None, default=None):
    if value is None:
        if default is not None:
            return default
        raise InputError("%s is required" % name)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise InputError("%s must be a number, got %r" % (name, value))
    if math.isnan(num) or math.isinf(num):
        raise InputError("%s must be finite, got %r" % (name, value))
    if minimum is not None and num < minimum:
        raise InputError("%s must be >= %s, got %s" % (name, minimum, num))
    if maximum is not None and num > maximum:
        raise InputError("%s must be <= %s, got %s" % (name, maximum, num))
    return num


def human_bytes(num):
    step = 1024.0
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < step or unit == "TiB":
            return ("%d %s" % (int(value), unit)) if unit == "B" else ("%.2f %s" % (value, unit))
        value /= step
    return "%.2f TiB" % value


def clamp_word(word, max_words):
    words = word.split()
    return " ".join(words[:max_words])


def espeak_phoneme_map_note():
    return ("phoneme characters follow the espeak-ng IPA inventory; the "
            "phoneme_id_map inside the voice configuration is authoritative")


# --------------------------------------------------------------------------- #
# Loading tools-*.py modules (filenames contain hyphens)
# --------------------------------------------------------------------------- #

def skill_root():
    """Absolute path of the Skill Tool folder (the folder holding this file)."""
    return Path(__file__).resolve().parent


def load_sibling(filename, module_name):
    """Import a sibling tools-*.py file by path and register it in sys.modules."""
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    path = skill_root() / filename
    if not path.is_file():
        raise DependencyError("required runtime file is missing: %s" % filename,
                              hint="re-extract the Skill Tool folder so every tools-*.py "
                                   "file sits beside tools-nvcore.py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def import_optional(module_name, *, feature, install_hint):
    """Import a third-party module or raise a DependencyError with a fix."""
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # ImportError, OSError from native libs, ...
        raise DependencyError(
            "%s is not importable (%s)" % (module_name, exc),
            hint=install_hint,
            details={"feature": feature, "module": module_name},
        )


def dependency_report():
    """Probe every optional runtime dependency without importing side effects."""
    checks = [
        ("numpy", "waveform statistics", "pip install numpy"),
        ("onnxruntime", "ONNX CPU inference plan checks", "pip install onnxruntime"),
        ("onnx", "graph, opset and metadata inspection", "pip install onnx"),
        ("lameenc", "high quality MP3 encoding", "pip install lameenc"),
        ("soundfile", "MP3 decoding and cross-format validation", "pip install soundfile"),
        ("jsonschema", "formal JSON Schema validation", "pip install jsonschema"),
        ("faster_whisper", "optional transcript accuracy check", "pip install faster-whisper"),
        ("piper", "bundled baseline synthesis adapter", "pip install piper-tts"),
    ]
    rows = []
    for name, purpose, installer in checks:
        record = {"module": name, "purpose": purpose, "install": installer,
                  "available": False, "version": None, "error": None}
        try:
            module = importlib.import_module(name)
            record["available"] = True
            version = None
            try:
                from importlib import metadata
                version = metadata.version(name)
            except Exception:
                pass
            if not version:
                version = getattr(module, "__version__", None)
            record["version"] = str(version or "unknown")
        except Exception as exc:
            record["error"] = "%s: %s" % (type(exc).__name__, exc)
        rows.append(record)
    return rows


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# per-user state directory used by default; override with paths.state_dir
STATE_DIR_DEFAULT = "~/.local/state/offline-neural-voice"

DEFAULT_CONFIG = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "skill": {
        "name": SKILL_SLUG,
        "title": SKILL_TITLE,
        "config_version": "1.0.0",
    },
    "paths": {
        # runtime state lives outside the flat skill folder by default, because that
        # folder may only contain prefixed files (tools-*, asset-*, knowledge-*).
        "state_dir": STATE_DIR_DEFAULT,
        "models_dir": STATE_DIR_DEFAULT + "/models",
        "output_dir": STATE_DIR_DEFAULT + "/output",
        "work_dir": STATE_DIR_DEFAULT + "/work",
        "journal_path": STATE_DIR_DEFAULT + "/work/journal.jsonl",
        "evidence_dir": STATE_DIR_DEFAULT + "/output/evidence",
        "registry_path": "asset-model-registry.json",
        "presets_path": "asset-voice-presets.json",
        "policies_path": "asset-rights-policy.json",
        "lexicon_path": "asset-pronunciation-lexicon.json",
        "acceptance_fixture_path": "asset-acceptance-fixture.json",
        "result_schema_path": "asset-result-schema.json",
        "config_schema_path": "asset-config-schema.json",
        "caption_policy_path": "asset-caption-policy.json",
        "manifest_out": STATE_DIR_DEFAULT + "/output/manifest.json",
        "allowed_roots": [],
    },
    "execution": {
        "offline": True,
        "allow_network": False,
        "timeout_seconds": DEFAULT_SYNTH_TIMEOUT_S,
        "run_timeout_seconds": DEFAULT_RUN_TIMEOUT_S,
        "cancel_poll_seconds": 0.2,
        "numpy_threads": 1,
        "strict_checksum": True,
        "allow_fallback_engine": True,
        "fail_on_blocked_rights": True,
        "max_chars": DEFAULT_MAX_CHARS,
        "max_sentences": 64,
        "allow_absolute_paths": False,
        "overwrite": False,
        "script_guard": "block",
    },
    "inference": {
        "providers": ["CPUExecutionProvider"],
        "intra_op_threads": 1,
        "inter_op_threads": 1,
        "graph_optimization": "ORT_ENABLE_ALL",
        "max_memory_bytes": 1500000000,
    },
    "synthesis": {
        "length_scale": 1.0,
        "noise_scale": 0.667,
        "noise_w": 0.8,
        "seed": 1337,
        "volume": 1.0,
        "normalize_audio": False,
    },
    "mastering": {
        "target_lufs": -14.0,
        "max_true_peak_dbfs": -1.0,
        "loudness_tolerance_lu": 1.0,
        "loudness_correction_passes": 3,
        "max_limiter_drive_db": 6.0,
        "limiter_lookahead_ms": 20.0,
        "highpass_hz": 70.0,
        "resample_hz": None,
        "max_gain_db": 24.0,
    },
    "qa": {
        "min_duration_s": 2.5,
        "max_duration_s": 4.0,
        "short_ceiling_ok": True,
        "max_silence_ratio": 0.45,
        "max_clipped_samples": 0,
        "max_dc_offset": 0.02,
        "min_level_rms_dbfs": -40.0,
        "max_level_rms_dbfs": -6.0,
        "duplicate_word_repeat_limit": 2,
        "min_word_accuracy": 0.85,
        "asr": {"enabled": False, "model": "base.en", "compute_type": "int8",
                "language": "en", "download_root": STATE_DIR_DEFAULT + "/work/asr-models"},
    },
    "safety": {
        "require_consent_for_cloning": True,
        "require_approval_for_release": True,
        "require_approval_for_delete": True,
        "require_approval_for_registry_write": True,
        "disclose_fallback": True,
        "max_copy_seconds": 60.0,
    },
    "journal": {
        "enabled": True,
        "max_bytes": DEFAULT_JOURNAL_MAX_BYTES,
        "reset_requires_approval": True,
    },
    "packaging": {
        "formats": ["wav"],
        "mp3_bitrate_kbps": 192,
        "include_evidence": True,
        "include_captions": True,
        "include_manifest": True,
    },
    "accessibility": {
        "plain_language": False,
        "verbose": False,
        "color": False,
        "pause_after_sentence_ms": 0,
    },
}

ENV_PREFIX = "ONV_"


def _deep_merge(base, overlay):
    result = json.loads(json.dumps(base))
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_overrides():
    """Read ONV_* environment variables into a nested overlay dict."""
    overlay = {}
    mapping = {
        "OFFLINE": ("execution", "offline"),
        "TIMEOUT_SECONDS": ("execution", "timeout_seconds"),
        "RUN_TIMEOUT_SECONDS": ("execution", "run_timeout_seconds"),
        "ALLOW_FALLBACK_ENGINE": ("execution", "allow_fallback_engine"),
        "MAX_CHARS": ("execution", "max_chars"),
        "OVERWRITE": ("execution", "overwrite"),
        "SEED": ("synthesis", "seed"),
        "LENGTH_SCALE": ("synthesis", "length_scale"),
        "NOISE_SCALE": ("synthesis", "noise_scale"),
        "TARGET_LUFS": ("mastering", "target_lufs"),
        "TRUE_PEAK_DBFS": ("mastering", "max_true_peak_dbfs"),
        "MODELS_DIR": ("paths", "models_dir"),
        "OUTPUT_DIR": ("paths", "output_dir"),
        "JOURNAL_PATH": ("paths", "journal_path"),
    }
    for env_name, (section, key) in mapping.items():
        raw = os.environ.get(ENV_PREFIX + env_name)
        if raw is None:
            continue
        default = DEFAULT_CONFIG[section][key]
        if isinstance(default, bool):
            value = parse_bool(raw)
        elif isinstance(default, (int, float)):
            value = float(raw) if isinstance(default, float) else int(float(raw))
        else:
            value = raw
        overlay.setdefault(section, {})[key] = value
    return overlay


class Config:
    """Merged, validated configuration with sealed relative path handling."""

    def __init__(self, data, *, sources, root, allow_absolute):
        self.data = data
        self.sources = list(sources)
        self.root = Path(root).resolve()
        self.allow_absolute = bool(allow_absolute)

    # -- access ---------------------------------------------------------- #
    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def offline(self):
        return bool(self.get("execution.offline", True))

    @property
    def allow_network(self):
        return bool(self.get("execution.allow_network", False))

    # -- sealed paths ---------------------------------------------------- #
    def resolve(self, path_like, *, must_exist=False, kind=None, roots_key="paths.allowed_roots",
                from_config=False):
        """Resolve a path under the skill root.

        User supplied paths are sealed: relative only, no `..`, no escape. Paths that
        come from *configuration* (`from_config=True`) may also point at the state
        directory (`paths.state_dir`) or use `~`, because an operator configured them
        deliberately; that is how runtime state is kept out of the flat skill folder.
        """
        if path_like is None or str(path_like).strip() == "":
            raise InputError("a path is required")
        raw = str(path_like).strip().replace("\\", "/")
        candidate = Path(os.path.expanduser(raw)) if raw.startswith("~") else Path(raw)
        if candidate.is_absolute() or re.match(r"^[A-Za-z]:", raw):
            # the state directory is operator-configured and is where this tool writes
            # its own models/output/journal, so paths inside it are always trusted.
            # This also makes cfg.rel(...) round-trip cleanly back through resolve().
            state = self.state_dir()
            if state is not None and self._is_within(candidate.resolve(), state):
                return candidate.resolve()
            if from_config:
                resolved = candidate.resolve()
                state = self.state_dir()
                if state is None or not self._is_within(resolved, state):
                    raise InputError(
                        "configured path must live inside the state directory: %r" % raw,
                        hint="paths outside the skill folder are only allowed inside "
                             "paths.state_dir (%s)" % (state or "unset"))
                if kind == "file" and not resolved.is_file():
                    raise InputError("file not found: %s" % resolved)
                if kind == "dir" and not resolved.is_dir():
                    raise InputError("directory not found: %s" % resolved)
                if must_exist and not resolved.exists():
                    raise InputError("path not found: %s" % resolved)
                return resolved
            if not self.allow_absolute:
                raise InputError(
                    "absolute paths are disabled for this Skill Tool: %r" % raw,
                    hint="pass a path relative to the skill root, or set "
                         "execution.allow_absolute_paths=true for trusted operator use")
            resolved = candidate.resolve()
        else:
            parts = [p for p in raw.split("/") if p not in ("", ".")]
            if any(p == ".." for p in parts):
                raise InputError("parent-directory traversal is not allowed: %r" % raw,
                                 hint="all paths must stay inside the skill root")
            resolved = (self.root / Path(*parts)).resolve()
        allowed = [self.root]
        extra = self.get(roots_key) or []
        if isinstance(extra, str):
            extra = [extra]
        for entry in extra:
            try:
                allowed.append((self.root / str(entry)).resolve())
            except OSError:
                continue
        if not any(self._is_within(resolved, base) for base in allowed):
            raise InputError("path escapes the allowed roots: %r" % raw,
                             hint="allowed roots: %s" % ", ".join(str(b) for b in allowed))
        if kind == "file":
            if not resolved.is_file():
                raise InputError("file not found: %s" % self.rel(resolved),
                                 hint="check the exact case-sensitive filename")
        elif kind == "dir":
            if not resolved.is_dir():
                raise InputError("directory not found: %s" % self.rel(resolved))
        elif must_exist and not resolved.exists():
            raise InputError("path not found: %s" % self.rel(resolved))
        return resolved

    def state_dir(self):
        """Absolute state directory from `paths.state_dir`, or None when unset."""
        raw = self.get("paths.state_dir")
        if not raw:
            return None
        return Path(os.path.expanduser(str(raw))).resolve()

    @staticmethod
    def _is_within(path, base):
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    def rel(self, path, *, for_display=True):
        """Portable rendering of a path: skill-relative, or `~/...` inside the state dir."""
        resolved = Path(path).resolve()
        try:
            return str(resolved.relative_to(self.root)).replace(os.sep, "/")
        except ValueError:
            pass
        state = self.state_dir()
        if state is not None and self._is_within(resolved, state):
            try:
                tail = str(resolved.relative_to(state)).replace(os.sep, "/")
            except ValueError:
                tail = ""
            if Path(os.path.expanduser("~")).resolve() == state.parent:
                return "~/%s/%s" % (state.name, tail) if tail else "~/%s" % state.name
        if for_display:
            return portable_path(str(resolved))
        return str(resolved)

    def path(self, dotted, *, must_exist=False, kind=None):
        """Resolve a path stored in configuration (state-dir aware)."""
        value = self.get(dotted)
        if value is None:
            raise InputError("configuration key %s is not set" % dotted)
        return self.resolve(value, must_exist=must_exist, kind=kind, from_config=True)

    def ensure_dir(self, dotted_or_path, *, is_config=True):
        target = self.path(dotted_or_path) if is_config else self.resolve(dotted_or_path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def as_dict(self):
        return json.loads(json.dumps(self.data))

    def describe(self):
        return {"sources": self.sources, "root": ".", "allow_absolute_paths": self.allow_absolute}


def validate_config_dict(data):
    """Strict structural validation for the configuration document."""
    problems = []
    if not isinstance(data, dict):
        return ["configuration root must be an object"], {}
    schema_version = data.get("schema_version", CONFIG_SCHEMA_VERSION)
    if schema_version != CONFIG_SCHEMA_VERSION:
        problems.append("schema_version must be %d, got %r" % (CONFIG_SCHEMA_VERSION, schema_version))
    for section, defaults in DEFAULT_CONFIG.items():
        if section == "schema_version":
            continue
        if section not in data:
            problems.append("missing section: %s" % section)
            continue
        if not isinstance(data[section], dict):
            problems.append("section %s must be an object" % section)
            continue
        for key, default in defaults.items():
            if key not in data[section]:
                problems.append("missing key: %s.%s" % (section, key))
                continue
            value = data[section][key]
            if isinstance(default, bool) and not isinstance(value, bool):
                problems.append("%s.%s must be a boolean" % (section, key))
            elif isinstance(default, int) and not isinstance(default, bool):
                if not isinstance(value, int):
                    try:
                        int(value)
                    except (TypeError, ValueError):
                        problems.append("%s.%s must be an integer" % (section, key))
            elif isinstance(default, float):
                try:
                    float(value)
                except (TypeError, ValueError):
                    problems.append("%s.%s must be a number" % (section, key))
            elif isinstance(default, list) and not isinstance(value, list):
                problems.append("%s.%s must be a list" % (section, key))
            elif key == "script_guard" and value not in ("block", "warn", "off"):
                problems.append("%s.%s must be one of block, warn, off" % (section, key))
    unknown = [k for k in data if k != "schema_version" and k not in DEFAULT_CONFIG]
    warnings = ["unknown section ignored: %s" % k for k in unknown]
    return problems, {"warnings": warnings}


def load_config(config_path=None, *, root=None, overrides=None, allow_absolute=None):
    """Merge defaults <- config file <- environment <- explicit overrides."""
    root_path = Path(root).resolve() if root else skill_root()
    sources = ["builtin-defaults"]
    data = json.loads(json.dumps(DEFAULT_CONFIG))
    if config_path:
        cfg_file = Path(config_path)
        if not cfg_file.is_absolute():
            cfg_file = root_path / config_path
        if not cfg_file.is_file():
            raise InputError("configuration file not found: %s" % config_path,
                             hint="omit --config to use built-in defaults plus asset-default-config.json")
        try:
            raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError("configuration file is not valid JSON: %s" % exc,
                             hint="line %s column %s" % (exc.lineno, exc.colno))
        if not isinstance(raw, dict):
            raise InputError("configuration root must be an object")
        data = _deep_merge(data, raw)
        sources.append(str(config_path))
    env_overlay = _env_overrides()
    if env_overlay:
        data = _deep_merge(data, env_overlay)
        sources.append("environment:" + ENV_PREFIX + "*")
    if overrides:
        data = _deep_merge(data, overrides)
        sources.append("cli-overrides")
    problems, extra = validate_config_dict(data)
    if problems:
        raise InputError("configuration is invalid", details={"problems": problems},
                         hint="run `python tools-nv-config.py validate --config <file>`")
    if allow_absolute is None:
        allow_absolute = bool(data["execution"].get("allow_absolute_paths", False))
    cfg = Config(data, sources=sources, root=root_path, allow_absolute=allow_absolute)
    cfg.warnings = extra.get("warnings", [])
    return cfg


# --------------------------------------------------------------------------- #
# Offline network guard
# --------------------------------------------------------------------------- #

_NET_GUARD_STATE = {"installed": False, "blocked_attempts": []}


class NetworkBlockedError(OSError):
    """Raised when code under the offline guard opens a socket."""


def _blocked(*args, **kwargs):
    import traceback as _tb
    frame = _tb.extract_stack(limit=6)
    caller = "%s:%s" % (frame[-3].filename, frame[-3].lineno) if len(frame) >= 3 else "unknown"
    _NET_GUARD_STATE["blocked_attempts"].append({"at": utc_now(), "caller": caller})
    raise NetworkBlockedError(
        "network access blocked by the offline guard (%s); this Skill Tool runs air-gapped"
        % caller)


class offline_guard:
    """Context manager that hard-blocks socket creation and DNS lookups.

    Socket creation is blocked (not merely connect) so libraries cannot open
    lazy background connections during synthesis.
    """

    def __init__(self, enabled=True):
        self.enabled = bool(enabled)
        self._patched = []

    def __enter__(self):
        if not self.enabled:
            return self
        import socket
        self._orig_socket = socket.socket
        self._orig_create_connection = socket.create_connection
        self._orig_getaddrinfo = socket.getaddrinfo

        def guard_socket(*args, **kwargs):
            family = args[0] if args else kwargs.get("family", "?")
            if family == getattr(socket, "AF_UNIX", None):
                return self._orig_socket(*args, **kwargs)
            return _blocked()

        def guard_create_connection(*args, **kwargs):
            return _blocked()

        def guard_getaddrinfo(*args, **kwargs):
            return _blocked()

        socket.socket = guard_socket
        socket.create_connection = guard_create_connection
        socket.getaddrinfo = guard_getaddrinfo
        _NET_GUARD_STATE["installed"] = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            import socket
            socket.socket = self._orig_socket
            socket.create_connection = self._orig_create_connection
            socket.getaddrinfo = self._orig_getaddrinfo
            _NET_GUARD_STATE["installed"] = False
        return False


def network_guard_report():
    return {
        "installed_now": _NET_GUARD_STATE["installed"],
        "blocked_attempts": list(_NET_GUARD_STATE["blocked_attempts"]),
    }


def offline_probe(cfg, *, timeout=3.0):
    """Verify the air-gap by attempting one real TCP connection with the guard off.

    Returns a report; never raises for an unreachable network.
    """
    import socket
    target = ("127.0.0.1", 9, "loopback discard port")
    report = {
        "checked_at": utc_now(),
        "policy_offline": cfg.offline,
        "attempts": [],
        "network_reachable": None,
    }
    probe = [
        ("127.0.0.1", 9),
        ("1.1.1.1", 443),
    ]
    for host, port in probe:
        row = {"host": host, "port": port, "connected": False, "error": None}
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            row["connected"] = True
        except OSError as exc:
            row["error"] = "%s: %s" % (type(exc).__name__, exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        report["attempts"].append(row)
    report["network_reachable"] = any(a["connected"] for a in report["attempts"])
    report["note"] = ("a reachable network is not an error: the guard still blocks sockets "
                      "during synthesis; the report documents the environment state")
    return report


# --------------------------------------------------------------------------- #
# Atomic writes, hashing, JSON
# --------------------------------------------------------------------------- #

def sha256_file(path, *, chunk=1 << 20, cancel=None):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if cancel is not None:
                cancel.check()
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def md5_file(path, *, chunk=1 << 20):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def file_fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return {"size_bytes": stat.st_size, "mtime_epoch": int(stat.st_mtime)}


def atomic_write_bytes(path, payload, *, overwrite=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise InputError("refusing to overwrite existing file: %s" % path.name,
                         hint="pass --overwrite or set execution.overwrite=true")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def atomic_write_text(path, text, *, overwrite=True):
    return atomic_write_bytes(path, text.encode("utf-8"), overwrite=overwrite)


def atomic_write_json(path, data, *, overwrite=True):
    payload = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    return atomic_write_text(path, payload, overwrite=overwrite)


def read_json(path, *, what="JSON file"):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError("%s not found: %s" % (what, path.name))
    except json.JSONDecodeError as exc:
        raise InputError("%s is not valid JSON: %s" % (what, path.name),
                         hint="line %s column %s: %s" % (exc.lineno, exc.colno, exc.msg))


def write_csv(path, rows, header, *, overwrite=True):
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return atomic_write_text(path, buffer.getvalue(), overwrite=overwrite)


# --------------------------------------------------------------------------- #
# Minimal JSON Schema validator (draft-07 subset)
# --------------------------------------------------------------------------- #

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def json_type_name(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "string"


def validate_json(instance, schema, *, path="$"):
    """Validate a subset of JSON Schema draft-07 well enough for our assets."""
    errors = []

    def type_ok(value, expected):
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        mapped = _TYPES.get(expected)
        return mapped is None or isinstance(value, mapped)

    def walk(value, node, where):
        if node is True or node == {}:
            return
        if node is False:
            errors.append("%s: schema forbids any value" % where)
            return
        if not isinstance(node, dict):
            errors.append("%s: schema node must be an object or boolean" % where)
            return
        if "type" in node:
            expected = node["type"]
            options = expected if isinstance(expected, list) else [expected]
            if not any(type_ok(value, opt) for opt in options):
                errors.append("%s: expected type %s, got %s" % (where, expected, json_type_name(value)))
                return
        if "enum" in node and value not in node["enum"]:
            errors.append("%s: %r not one of %s" % (where, value, node["enum"]))
        if "const" in node and value != node["const"]:
            errors.append("%s: expected const %r" % (where, node["const"]))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in node and value < node["minimum"]:
                errors.append("%s: %s < minimum %s" % (where, value, node["minimum"]))
            if "maximum" in node and value > node["maximum"]:
                errors.append("%s: %s > maximum %s" % (where, value, node["maximum"]))
            if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"]:
                errors.append("%s: %s <= exclusiveMinimum %s" % (where, value, node["exclusiveMinimum"]))
            if "exclusiveMaximum" in node and value >= node["exclusiveMaximum"]:
                errors.append("%s: %s >= exclusiveMaximum %s" % (where, value, node["exclusiveMaximum"]))
            if "multipleOf" in node:
                step = node["multipleOf"]
                if step and abs(value / step - round(value / step)) > 1e-9:
                    errors.append("%s: %s is not a multiple of %s" % (where, value, step))
        if isinstance(value, str):
            if "minLength" in node and len(value) < node["minLength"]:
                errors.append("%s: shorter than minLength %s" % (where, node["minLength"]))
            if "maxLength" in node and len(value) > node["maxLength"]:
                errors.append("%s: longer than maxLength %s" % (where, node["maxLength"]))
            if "pattern" in node and not re.search(node["pattern"], value):
                errors.append("%s: %r does not match pattern %s" % (where, value, node["pattern"]))
        if isinstance(value, list):
            if "minItems" in node and len(value) < node["minItems"]:
                errors.append("%s: fewer than minItems %s" % (where, node["minItems"]))
            if "maxItems" in node and len(value) > node["maxItems"]:
                errors.append("%s: more than maxItems %s" % (where, node["maxItems"]))
            if "uniqueItems" in node and node["uniqueItems"]:
                seen = set()
                for item in value:
                    key = json.dumps(item, sort_keys=True, default=str)
                    if key in seen:
                        errors.append("%s: duplicate item %s" % (where, key))
                    seen.add(key)
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    walk(item, item_schema, "%s[%d]" % (where, idx))
            elif isinstance(item_schema, list):
                for idx, item in enumerate(value[: len(item_schema)]):
                    walk(item, item_schema[idx], "%s[%d]" % (where, idx))
            if "contains" in node:
                if not any(not validate_json(item, node["contains"], path="%s[]" % where) for item in value):
                    errors.append("%s: no item matches `contains`" % where)
        if isinstance(value, dict):
            for key in node.get("required", []):
                if key not in value:
                    errors.append("%s: missing required key %r" % (where, key))
            properties = node.get("properties", {})
            for key, sub in properties.items():
                if key in value:
                    walk(value[key], sub, "%s.%s" % (where, key))
            extra = node.get("additionalProperties", True)
            if isinstance(extra, dict):
                for key in value:
                    if key not in properties:
                        walk(value[key], extra, "%s.%s" % (where, key))
            elif extra is False:
                for key in value:
                    if key not in properties:
                        errors.append("%s: unexpected key %r" % (where, key))
            for key, sub in node.get("patternProperties", {}).items():
                for candidate in value:
                    if re.search(key, candidate):
                        walk(value[candidate], sub, "%s.%s" % (where, candidate))
            if "propertyNames" in node:
                for key in value:
                    walk(key, node["propertyNames"], "%s.<key>%s" % (where, key))
        for sub in node.get("allOf", []):
            walk(value, sub, where)
        for sub in node.get("anyOf", []):
            if all(validate_json(value, sub, path=where) for sub in node["anyOf"]):
                errors.append("%s: value matches no `anyOf` branch" % where)
        if "oneOf" in node:
            matches = sum(1 for sub in node["oneOf"] if not validate_json(value, sub, path=where))
            if matches != 1:
                errors.append("%s: matched %d `oneOf` branches (exactly 1 required)" % (where, matches))
        if "not" in node and not validate_json(value, node["not"], path=where):
            errors.append("%s: value matches a forbidden `not` schema" % where)
        if "if" in node:
            if not validate_json(value, node["if"], path=where):
                if "then" in node:
                    walk(value, node["then"], where)
            elif "else" in node:
                walk(value, node["else"], where)

    walk(instance, schema, path)
    return errors


def schema_validate_file(instance_path, schema_path):
    instance = read_json(instance_path, what="data file")
    schema = read_json(schema_path, what="schema file")
    errors = validate_json(instance, schema)
    return {"valid": not errors, "errors": errors, "instance": str(instance_path),
            "schema": str(schema_path)}


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #

class CancellationToken:
    """Cooperative cancellation.

    A cancel file (created by the user or another process) or Ctrl-C stops work
    at the next poll point. Cancellation is checked, never forced.
    """

    def __init__(self, cancel_file=None, *, poll_seconds=0.2, clock=None):
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self.poll_seconds = float(poll_seconds)
        self.deadline = None
        self._clock = clock or time.monotonic
        self._last_poll = 0.0
        self.was_cancelled = False
        self.reason = None

    def set_deadline(self, seconds):
        if seconds:
            self.deadline = self._clock() + float(seconds)
        return self

    def check(self):
        now = self._clock()
        if self.deadline is not None and now > self.deadline:
            self.was_cancelled = True
            self.reason = "deadline exceeded"
            raise CancelledError("operation exceeded its deadline",
                                 hint="raise execution.timeout_seconds or shorten the request")
        if self.cancel_file is None:
            return False
        if now - self._last_poll < self.poll_seconds:
            return False
        self._last_poll = now
        if self.cancel_file.exists():
            self.was_cancelled = True
            self.reason = "cancel file %s" % self.cancel_file.name
            raise CancelledError("operation cancelled by request file",
                                 hint="remove the cancel file before restarting")
        return False


# --------------------------------------------------------------------------- #
# Audit journal with crash recovery
# --------------------------------------------------------------------------- #

class Journal:
    """Append-only JSONL audit trail plus an intent file for recovery.

    Before a destructive or long running operation the caller writes an intent
    record; on success it is cleared. If a later run finds a stale intent, the
    operation is reported as interrupted and the target is left untouched
    (atomic writes mean a partial file never replaces a good one).
    """

    def __init__(self, path, *, enabled=True, max_bytes=DEFAULT_JOURNAL_MAX_BYTES,
                 cancel=None, actor="cli"):
        self.path = Path(path)
        self.enabled = bool(enabled)
        self.max_bytes = int(max_bytes)
        self.cancel = cancel
        self.actor = actor
        self.intent_path = self.path.with_suffix(self.path.suffix + ".intent")
        self.rotations = 0

    def _rotate_if_needed(self):
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                stamped = self.path.with_name("%s.%s.jsonl" % (self.path.stem, int(time.time())))
                os.replace(self.path, stamped)
                self.rotations += 1
                return stamped.name
        except OSError:
            pass
        return None

    def record(self, kind, **fields):
        if not self.enabled:
            return None
        rotated = self._rotate_if_needed()
        entry = {"ts": utc_now(), "kind": kind, "version": __version__, "actor": self.actor}
        entry.update(fields)
        if rotated:
            entry["rotated_previous"] = rotated
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return entry

    def begin_intent(self, operation, **fields):
        if not self.enabled:
            return None
        payload = {"operation": operation, "started_at": utc_now(), "actor": self.actor}
        payload.update(fields)
        atomic_write_json(self.intent_path, payload, overwrite=True)
        return payload

    def clear_intent(self):
        try:
            if self.intent_path.exists():
                self.intent_path.unlink()
        except OSError:
            pass

    def recover(self):
        """Report and clear stale intents from an interrupted run."""
        if not self.intent_path.exists():
            return {"interrupted": False}
        try:
            payload = read_json(self.intent_path, what="journal intent")
        except InputError:
            payload = {"operation": "unknown", "started_at": None}
        self.record("recovery", operation=payload.get("operation"), detail="stale intent cleared")
        self.clear_intent()
        return {
            "interrupted": True,
            "operation": payload.get("operation"),
            "started_at": payload.get("started_at"),
            "recovery": "intent cleared; verify outputs with the qa tool before rerunning",
        }

    def tail(self, count=20, kinds=None):
        if not self.path.exists():
            return []
        rows = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if kinds and entry.get("kind") not in kinds:
                    continue
                rows.append(entry)
        return rows[-int(count):]

    def summary(self):
        kinds = {}
        for entry in self.tail(count=100000):
            kinds[entry.get("kind")] = kinds.get(entry.get("kind"), 0) + 1
        return {"path": str(self.path.name), "entries": sum(kinds.values()), "kinds": kinds}


def build_journal(cfg, cancel=None, actor="cli"):
    try:
        path = cfg.path("paths.journal_path")
    except InputError:
        path = cfg.root / "work" / "journal.jsonl"
    return Journal(path,
                   enabled=bool(cfg.get("journal.enabled", True)),
                   max_bytes=int(cfg.get("journal.max_bytes", DEFAULT_JOURNAL_MAX_BYTES)),
                   cancel=cancel, actor=actor)


# --------------------------------------------------------------------------- #
# Text utilities
# --------------------------------------------------------------------------- #

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_SHORT_CODES = {"hrv", "srp", "sqi", "eus", "kat", "mya", "nld", "deu", "ell", "zho",
                "jpn", "kor", "fra", "spa", "por", "ita", "rus", "pol", "tur", "ara"}


def normalize_language_tag(tag):
    """Normalise en_US, en-us, EN-us, en to canonical BCP-47 (en-US / en)."""
    if tag is None:
        return None
    raw = str(tag).strip().replace("_", "-").replace(" ", "")
    if not raw:
        return None
    parts = [p for p in raw.split("-") if p]
    if not parts:
        return None
    langs = []
    for part in parts:
        low = part.lower()
        if low in _SHORT_CODES:
            langs.append(low)
        elif len(low) == 2 and low.isalpha():
            langs.append(low)
        else:
            langs.append(low)
    out = [langs[0]]
    for part in langs[1:]:
        if len(part) == 2 and part.isalpha():
            out.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            out.append(part.capitalize())
        else:
            out.append(part.upper() if len(part) == 2 else part)
    return "-".join(out)


def language_family(tag):
    norm = normalize_language_tag(tag) or ""
    return norm.split("-")[0] if norm else ""


def script_of_text(text):
    """Approximate writing system: latin, cyrillic, han, kana, hangul, arabic..."""
    counts = {"latin": 0, "cyrillic": 0, "arabic": 0, "han": 0, "kana": 0, "hangul": 0,
              "thai": 0, "devanagari": 0, "hebrew": 0, "greek": 0, "other": 0}
    for ch in str(text):
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if cp < 0x0250:
            counts["latin"] += 1
        elif 0x0400 <= cp <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0600 <= cp <= 0x06FF:
            counts["arabic"] += 1
        elif 0x4E00 <= cp <= 0x9FFF:
            counts["han"] += 1
        elif 0x3040 <= cp <= 0x30FF:
            counts["kana"] += 1
        elif 0xAC00 <= cp <= 0xD7AF:
            counts["hangul"] += 1
        elif 0x0E00 <= cp <= 0x0E7F:
            counts["thai"] += 1
        elif 0x0900 <= cp <= 0x097F:
            counts["devanagari"] += 1
        elif 0x0590 <= cp <= 0x05FF:
            counts["hebrew"] += 1
        elif 0x0370 <= cp <= 0x03FF:
            counts["greek"] += 1
        else:
            counts["other"] += 1
    best = max(counts, key=lambda key: counts[key])
    return best if counts[best] else "latin"


def count_words(text):
    return len(re.findall(r"[^\s]+", str(text)))


def split_sentences(text):
    """Deterministic sentence splitter that keeps terminal punctuation."""
    text = str(text).replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = []
    buffer = []
    abbreviations = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
                     "e.g", "i.e", "no", "fig", "inc", "ltd", "co", "approx", "dept"}
    chars = list(text)
    idx = 0
    while idx < len(chars):
        ch = chars[idx]
        buffer.append(ch)
        if ch in ".!?…":
            tail = "".join(buffer).strip()
            word = re.findall(r"([A-Za-z.]+)$", tail[:-1])
            token = word[0].lower() if word else ""
            lookahead = "".join(chars[idx + 1: idx + 4])
            is_abbrev = token in abbreviations
            is_decimal = ch == "." and idx + 1 < len(chars) and chars[idx + 1].isdigit()
            if not is_abbrev and not is_decimal and (lookahead == "" or lookahead.lstrip()[:1].isupper()
                                                     or lookahead.lstrip()[:1] in "\"'(["):
                sentence = "".join(buffer).strip()
                if sentence:
                    parts.append(sentence)
                buffer = []
        idx += 1
    remainder = "".join(buffer).strip()
    if remainder:
        parts.append(remainder)
    return [p for p in parts if p.strip()]


# --------------------------------------------------------------------------- #
# Integer WAV I/O (stdlib only)
# --------------------------------------------------------------------------- #

WAV_PCM = 1
WAV_FLOAT = 3


def read_wav_info(path):
    path = Path(path)
    if not path.is_file():
        raise InputError("WAV file not found: %s" % path.name)
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames_data = handle.readframes(frames)
        compression = handle.getcomptype()
    return {
        "path": path.name,
        "sample_rate": rate,
        "channels": channels,
        "sample_width": width,
        "bits": width * 8,
        "frames": frames,
        "duration_s": round(frames / float(rate), 6) if rate else 0.0,
        "compression": compression,
        "format": "pcm" if width in (1, 2, 3, 4) else "unknown",
        "bytes": len(frames_data),
    }


def read_wav_int16(path, *, mono=True):
    """Read PCM/float WAV as (array('h'), sample_rate)."""
    path = Path(path)
    if not path.is_file():
        raise InputError("WAV file not found: %s" % path.name)
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    samples = array("h")
    if width == 2:
        samples.frombytes(raw)
        if sys.byteorder == "big":
            samples.byteswap()
    elif width == 1:
        converted = []
        for value in raw:
            converted.append((value - 128) * 256)
        samples = array("h", converted)
    elif width == 3:
        converted = []
        for offset in range(0, len(raw) - 2, 3):
            triple = raw[offset:offset + 3]
            value = int.from_bytes(triple, "little", signed=False)
            if value & 0x800000:
                value -= 0x1000000
            converted.append(int(value / 256))
        samples = array("h", converted)
    elif width == 4:
        floats = array("f")
        floats.frombytes(raw)
        if sys.byteorder == "big":
            floats.byteswap()
        samples = array("h", [max(-32768, min(32767, int(round(v * 32767.0)))) for v in floats])
    else:
        raise InputError("unsupported WAV sample width: %s bytes" % width)
    if mono and channels > 1:
        frames = len(samples) // channels
        mono_samples = array("h")
        for frame in range(frames):
            total = 0
            base = frame * channels
            for channel in range(channels):
                total += samples[base + channel]
            mono_samples.append(int(total / channels))
        samples = mono_samples
    return samples, rate


def write_wav_int16(path, samples, rate, *, overwrite=True):
    if not 1 <= int(rate) <= 384000:
        raise InputError("sample rate out of range: %s" % rate)
    payload = samples.tobytes()
    header = _wav_header(len(payload), rate, 1, 2)
    return atomic_write_bytes(path, header + payload, overwrite=overwrite)


def _wav_header(data_bytes, rate, channels, width):
    byte_rate = rate * channels * width
    block_align = channels * width
    parts = [
        b"RIFF",
        struct.pack("<I", 36 + data_bytes),
        b"WAVE",
        b"fmt ",
        struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, block_align, width * 8),
        b"data",
        struct.pack("<I", data_bytes),
    ]
    return b"".join(parts)


def float_to_int16(values):
    out = array("h", bytes(2 * len(values)))
    for idx, value in enumerate(values):
        scaled = int(round(float(value) * 32767.0))
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out[idx] = scaled
    return out


def int16_to_float(samples):
    return [value / 32768.0 for value in samples]


# --------------------------------------------------------------------------- #
# Resampling (windowed-sinc, integer-safe, deterministic)
# --------------------------------------------------------------------------- #

def resample_int16(samples, src_rate, dst_rate, *, taps=16):
    """Band-limited resampling with a Blackman window. Pure Python, modest cost."""
    src_rate = int(src_rate)
    dst_rate = int(dst_rate)
    if src_rate <= 0 or dst_rate <= 0:
        raise InputError("sample rates must be positive")
    if src_rate == dst_rate:
        return array("h", samples)
    ratio = dst_rate / float(src_rate)
    out_len = int(round(len(samples) * ratio))
    if out_len <= 0:
        return array("h")
    cutoff = min(1.0, ratio) * 0.94
    half = taps
    window = []
    for idx in range(-half, half + 1):
        w = 0.42 + 0.5 * math.cos(math.pi * idx / half) + 0.08 * math.cos(2 * math.pi * idx / half)
        window.append(w)
    center = (half, window)
    result = array("h", bytes(2 * out_len))
    inv_ratio = 1.0 / ratio
    for out_idx in range(out_len):
        src_pos = out_idx * inv_ratio
        base = int(src_pos)
        acc = 0.0
        weight_sum = 0.0
        for tap_idx in range(-half, half + 1):
            sample_idx = base + tap_idx
            if sample_idx < 0 or sample_idx >= len(samples):
                continue
            distance = (src_pos - sample_idx) / cutoff
            if abs(distance) > half:
                continue
            sinc = 1.0 if distance == 0 else math.sin(math.pi * distance) / (math.pi * distance)
            weight = sinc * window[tap_idx + half]
            acc += samples[sample_idx] * weight
            weight_sum += weight
        value = acc / weight_sum if weight_sum else 0.0
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        result[out_idx] = int(round(value))
    return result


# --------------------------------------------------------------------------- #
# Loudness (ITU-R BS.1770-4 K-weighting + gating) and true peak (4x)
# --------------------------------------------------------------------------- #

def _biquad_coeffs(kind, rate):
    """Return (b0, b1, b2, a1, a2) for the BS.1770 pre-filter stages."""
    if rate <= 0:
        raise InputError("sample rate must be positive")
    if kind == "high_shelf":
        # Stage 1: high shelving filter (analog prototype sampled at `rate`)
        f0 = 1681.974450955533
        gain_db = 3.999843853973347
        q = 0.7071752369554196
        k = math.tan(math.pi * f0 / rate)
        vh = 10.0 ** (gain_db / 20.0)
        vb = vh ** 0.4996667741545416
        a0 = 1.0 + k / q + k * k
        b0 = (vh + vb * k / q + k * k) / a0
        b1 = 2.0 * (k * k - vh) / a0
        b2 = (vh - vb * k / q + k * k) / a0
        a1 = 2.0 * (k * k - 1.0) / a0
        a2 = (1.0 - k / q + k * k) / a0
        return b0, b1, b2, a1, a2
    if kind == "high_pass":
        # Stage 2: RLB high-pass
        f0 = 38.13547087602444
        q = 0.5003270373238773
        k = math.tan(math.pi * f0 / rate)
        a0 = 1.0 + k / q + k * k
        b0 = 1.0
        b1 = -2.0
        b2 = 1.0
        a1 = 2.0 * (k * k - 1.0) / a0
        a2 = (1.0 - k / q + k * k) / a0
        return b0 / a0, b1 / a0, b2 / a0, a1, a2
    raise InputError("unknown filter kind: %s" % kind)


def _apply_biquad(samples, coeffs):
    b0, b1, b2, a1, a2 = coeffs
    out = [0.0] * len(samples)
    x1 = x2 = y1 = y2 = 0.0
    for idx, value in enumerate(samples):
        y = b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, value
        y2, y1 = y1, y
        out[idx] = y
    return out


def measure_loudness(samples, rate):
    """Integrated loudness (LUFS), loudness range proxy and sample peak.

    Implements BS.1770-4 K-weighting, 400 ms blocks with 75 % overlap, absolute
    -70 LUFS and relative -10 LU gating.
    """
    if not samples:
        raise InputError("cannot measure loudness of an empty signal")
    floats = int16_to_float(samples)
    stage1 = _apply_biquad(floats, _biquad_coeffs("high_shelf", rate))
    weighted = _apply_biquad(stage1, _biquad_coeffs("high_pass", rate))
    block = max(1, int(round(rate * 0.4)))
    hop = max(1, int(round(block * 0.25)))
    if len(weighted) < block:
        weighted = weighted + [0.0] * (block - len(weighted))
    blocks = []
    start = 0
    while start + block <= len(weighted):
        total = 0.0
        for value in weighted[start:start + block]:
            total += value * value
        mean_square = total / block
        loudness = -0.691 + 10.0 * math.log10(mean_square) if mean_square > 0 else -math.inf
        blocks.append((loudness, mean_square))
        start += hop
    absolute = [b for b in blocks if b[0] > -70.0]
    if not absolute:
        return {"integrated_lufs": None, "loudness_range_lu": 0.0,
                "sample_peak_dbfs": _dbfs(max(abs(s) for s in samples)),
                "blocks_total": len(blocks), "blocks_gated_absolute": len(blocks),
                "blocks_used": 0,
                "note": "signal is quieter than the -70 LUFS absolute gate"}
    mean_all = sum(b[1] for b in absolute) / len(absolute)
    relative_gate = (-0.691 + 10.0 * math.log10(mean_all)) - 10.0 if mean_all > 0 else -70.0
    gated = [b for b in absolute if b[0] > relative_gate]
    if not gated:
        gated = absolute
    integrated = -0.691 + 10.0 * math.log10(sum(b[1] for b in gated) / len(gated))
    values = sorted(b[0] for b in gated)
    low_index = int(len(values) * 0.10)
    high_index = int(len(values) * 0.95) - 1
    lra = values[min(max(high_index, 0), len(values) - 1)] - values[min(max(low_index, 0), len(values) - 1)]
    peak = max(abs(s) for s in samples)
    return {
        "integrated_lufs": round(integrated, 3),
        "loudness_range_lu": round(max(lra, 0.0), 3),
        "sample_peak_dbfs": _dbfs(peak),
        "blocks_total": len(blocks),
        "blocks_gated_absolute": len(blocks) - len(absolute),
        "blocks_used": len(gated),
    }


def _dbfs(amplitude):
    if amplitude <= 0:
        return -math.inf
    return round(20.0 * math.log10(min(1.0, amplitude / 32768.0)), 4)


def true_peak_dbfs(samples, rate, *, oversample=4):
    """Sample peak plus an oversampled inter-sample peak estimate (dBFS)."""
    if not samples:
        raise InputError("cannot measure true peak of an empty signal")
    peak = max(abs(s) for s in samples)
    if oversample > 1 and len(samples) > 1:
        # linear-interpolation oversampling: cheap, conservative lower bound
        step = 1.0 / oversample
        prev = samples[0]
        for value in samples[1:]:
            for k in range(1, oversample):
                interp = prev + (value - prev) * (k * step)
                if abs(interp) > peak:
                    peak = abs(interp)
            prev = value
    return {"true_peak_dbfs": _dbfs(peak), "oversample": oversample}


# --------------------------------------------------------------------------- #
# Basic DSP
# --------------------------------------------------------------------------- #

def highpass_int16(samples, rate, cutoff_hz, *, order=2):
    if cutoff_hz <= 0 or cutoff_hz >= rate / 2.0:
        return array("h", samples)
    b0, b1, b2, a1, a2 = _highpass_coeffs(rate, cutoff_hz)
    data = int16_to_float(samples)
    for _ in range(max(1, order)):
        out = [0.0] * len(data)
        x1 = x2 = y1 = y2 = 0.0
        for idx, value in enumerate(data):
            y = b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, value
            y2, y1 = y1, y
            out[idx] = y
        data = out
    return float_to_int16(data)


def _highpass_coeffs(rate, cutoff_hz, q=0.7071067811865476):
    w0 = 2.0 * math.pi * cutoff_hz / rate
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) / 2.0
    a0 = 1.0 + alpha
    return b0 / a0, b1 / a0, b2 / a0, (-2.0 * cos_w0) / a0, (1.0 - alpha) / a0


def trim_silence(samples, rate, *, threshold_db=-55.0, keep_ms=60, edge="both"):
    """Trim leading/trailing near-silence while keeping a short natural lead."""
    if not samples:
        return array("h")
    threshold = int(32768 * (10 ** (threshold_db / 20.0)))
    keep = max(0, int(rate * keep_ms / 1000.0))
    first = 0
    last = len(samples) - 1
    if edge in ("both", "start"):
        while first < len(samples) and abs(samples[first]) < threshold:
            first += 1
    if edge in ("both", "end"):
        while last > first and abs(samples[last]) < threshold:
            last -= 1
    first = max(0, first - keep)
    last = min(len(samples) - 1, last + keep)
    return array("h", samples[first:last + 1])


def apply_fade(samples, rate, *, fade_in_ms=5.0, fade_out_ms=15.0):
    data = array("h", samples)
    count = len(data)
    fade_in = min(count // 2, int(rate * fade_in_ms / 1000.0))
    fade_out = min(count // 2, int(rate * fade_out_ms / 1000.0))
    for idx in range(fade_in):
        data[idx] = int(data[idx] * (idx / float(max(fade_in, 1))))
    for idx in range(fade_out):
        factor = idx / float(max(fade_out, 1))
        data[count - 1 - idx] = int(data[count - 1 - idx] * factor)
    return data


def insert_silence(samples, rate, *, at_ms=None, silence_ms=250.0, output_len_ms=None):
    """Insert pause(s). at_ms places a pause there; otherwise appends at the end."""
    pad = array("h", bytes(2 * max(0, int(rate * silence_ms / 1000.0))))
    if at_ms is None:
        return array("h", list(samples) + list(pad))
    index = max(0, min(len(samples), int(rate * at_ms / 1000.0)))
    return array("h", list(samples[:index]) + list(pad) + list(samples[index:]))


def apply_gain_db(samples, gain_db, *, ceiling=32767):
    factor = 10.0 ** (float(gain_db) / 20.0)
    out = array("h", bytes(2 * len(samples)))
    for idx, value in enumerate(samples):
        scaled = int(round(value * factor))
        if scaled > ceiling:
            scaled = ceiling
        elif scaled < -ceiling - 1:
            scaled = -ceiling - 1
        out[idx] = scaled
    return out


def limiter(samples, rate, *, ceiling_dbfs=-1.0, lookahead_ms=20.0, release_ms=60.0):
    """Transparent look-ahead peak limiter with short release."""
    ceiling = int(round(32767 * (10 ** (ceiling_dbfs / 20.0))))
    if not samples:
        return array("h"), {"limited_samples": 0, "max_reduction_db": 0.0}
    look = max(1, int(rate * lookahead_ms / 1000.0))
    release_step = 1.0 / max(1.0, rate * release_ms / 1000.0)
    gains = [1.0] * len(samples)
    current = 1.0
    reductions = 0
    max_reduction = 0.0
    peak_abs = [abs(v) for v in samples]
    for idx in range(len(samples)):
        horizon = idx + look
        if horizon < len(samples):
            window_peak = max(peak_abs[idx:horizon + 1])
        else:
            window_peak = max(peak_abs[idx:])
        desired = 1.0 if window_peak <= ceiling else ceiling / float(window_peak)
        if desired < current:
            current = desired
            reductions += 1
        else:
            current = min(desired, current + release_step)
        if current < 1.0:
            max_reduction = max(max_reduction, -20.0 * math.log10(current))
        gains[idx] = current
    out = array("h", bytes(2 * len(samples)))
    for idx, value in enumerate(samples):
        scaled = int(round(value * gains[idx]))
        if scaled > ceiling:
            scaled = ceiling
        elif scaled < -ceiling - 1:
            scaled = -ceiling - 1
        out[idx] = scaled
    return out, {"limited_samples": reductions, "max_reduction_db": round(max_reduction, 3)}


def deess_int16(samples, rate, *, amount=0.35, band_hz=(4500.0, 9000.0)):
    """Gentle high-band dynamic attenuation to reduce sibilance harshness."""
    if amount <= 0:
        return array("h", samples)
    high = _bandpass_energy(samples, rate, band_hz[0], band_hz[1])
    if high <= 0:
        return array("h", samples)
    gain = max(0.55, 1.0 - amount * min(1.0, high / 0.18))
    return apply_gain_db(samples, 20.0 * math.log10(max(gain, 1e-3)))


def _bandpass_energy(samples, rate, low_hz, high_hz):
    """Mean energy of the low_hz..high_hz band (used by the de-esser)."""
    if not samples:
        return 0.0
    highpassed_low = highpass_int16(samples, rate, low_hz)      # keeps >= low_hz
    highpassed_high = highpass_int16(highpassed_low, rate, high_hz)  # keeps >= high_hz
    band = int16_to_float(highpassed_low)
    above = int16_to_float(highpassed_high)
    if not band:
        return 0.0
    energy = sum((a - b) ** 2 for a, b in zip(band, above))
    return energy / len(band) / (32768.0 ** 2)


def wav_statistics(samples, rate):
    if not samples:
        return {"frames": 0, "duration_s": 0.0}
    count = len(samples)
    total = 0
    peak = 0
    clipped = 0
    silence = 0
    threshold = int(32768 * (10 ** (-50.0 / 20.0)))
    run = 0
    longest_silence = 0
    zero_crossings = 0
    prev = samples[0]
    for value in samples:
        total += abs(value)
        if abs(value) > peak:
            peak = abs(value)
        if abs(value) >= 32767:
            clipped += 1
        if abs(value) < threshold:
            run += 1
            if run > longest_silence:
                longest_silence = run
        else:
            silence += run
            run = 0
        if (value >= 0) != (prev >= 0):
            zero_crossings += 1
        prev = value
    silence += run
    dc = sum(samples) / float(count)
    return {
        "frames": count,
        "duration_s": round(count / float(rate), 4),
        "sample_rate": rate,
        "peak": peak,
        "peak_dbfs": _dbfs(peak),
        "rms_dbfs": round(20.0 * math.log10(max(1e-9, math.sqrt(sum(v * v for v in samples[: min(count, 400000)]) / min(count, 400000)) / 32768.0)), 4),
        "mean_abs": round(total / float(count), 2),
        "clipped_samples": clipped,
        "dc_offset": round(dc / 32768.0, 6),
        "silence_ratio": round(silence / float(count), 4),
        "longest_silence_s": round(longest_silence / float(rate), 3),
        "zero_cross_rate": round(zero_crossings / (count / float(rate)), 1),
    }


# --------------------------------------------------------------------------- #
# MP3 encode / decode
# --------------------------------------------------------------------------- #

def encode_mp3(samples, rate, *, bitrate_kbps=192, quality=2):
    lameenc = import_optional("lameenc", feature="mp3-encode",
                              install_hint="pip install lameenc")
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(int(bitrate_kbps))
    encoder.set_in_sample_rate(int(rate))
    encoder.set_channels(1)
    encoder.set_quality(int(quality))
    payload = encoder.encode(samples.tobytes())
    payload += encoder.flush()
    if not payload:
        raise NVError("MP3 encoder produced no data")
    return payload


def decode_audio_any(path):
    """Decode WAV or MP3 to (int16 array, rate) using stdlib or soundfile."""
    path = Path(path)
    if not path.is_file():
        raise InputError("audio file not found: %s" % path.name)
    if path.suffix.lower() == ".wav":
        return read_wav_int16(path)
    soundfile = import_optional("soundfile", feature="decode",
                                install_hint="pip install soundfile")
    data, rate = soundfile.read(str(path), dtype="int16", always_2d=True)
    channels = data.shape[1]
    samples = array("h", bytes(2 * data.shape[0]))
    for idx in range(data.shape[0]):
        total = 0
        for channel in range(channels):
            total += int(data[idx][channel])
        samples[idx] = int(total / channels)
    return samples, int(rate)


def audio_file_report(path):
    path = Path(path)
    info = {"path": path.name, "suffix": path.suffix.lower(), "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path)}
    if path.suffix.lower() == ".wav":
        info.update(read_wav_info(path))
        samples, rate = read_wav_int16(path)
        info["stats"] = wav_statistics(samples, rate)
        info["loudness"] = measure_loudness(samples, rate)
        info["true_peak"] = true_peak_dbfs(samples, rate)
    else:
        samples, rate = decode_audio_any(path)
        info["decoded"] = {"sample_rate": rate, "frames": len(samples),
                           "duration_s": round(len(samples) / float(rate), 4)}
        info["loudness"] = measure_loudness(samples, rate)
        info["true_peak"] = true_peak_dbfs(samples, rate)
    return info


# --------------------------------------------------------------------------- #
# Model / voice asset inspection
# --------------------------------------------------------------------------- #

def inspect_voice_config(config_path):
    """Parse a Piper-style voice configuration and report compatibility facts."""
    config_path = Path(config_path)
    data = read_json(config_path, what="voice configuration")
    audio = data.get("audio") or {}
    espeak = data.get("espeak") or {}
    inference = data.get("inference") or {}
    phoneme_map = data.get("phoneme_id_map") or {}
    speaker_map = data.get("speaker_id_map") or {}
    required = ["audio", "espeak", "phoneme_id_map", "num_symbols", "num_speakers"]
    missing = [key for key in required if key not in data]
    return {
        "path": config_path.name,
        "sha256": sha256_file(config_path),
        "missing_required_keys": missing,
        "sample_rate": audio.get("sample_rate"),
        "quality": audio.get("quality"),
        "espeak_voice": espeak.get("voice"),
        "num_symbols": data.get("num_symbols"),
        "num_speakers": data.get("num_speakers"),
        "speaker_count_declared": len(speaker_map) if speaker_map else int(data.get("num_speakers") or 0),
        "phoneme_count": len(phoneme_map),
        "inference_defaults": {
            "noise_scale": inference.get("noise_scale"),
            "length_scale": inference.get("length_scale"),
            "noise_w": inference.get("noise_w"),
        },
        "language": (data.get("language") or {}).get("code"),
        "piper_version": data.get("piper_version"),
        "dataset": data.get("dataset"),
        "sample_speakers": sorted(speaker_map.items(), key=lambda kv: kv[1])[:5],
    }


def inspect_onnx_model(model_path, *, config_path=None, timeout_s=30.0):
    """Inspect an ONNX file: container, graph interface, opset, structural cost.

    Uses the `onnx` package when available; falls back to a header probe so the
    tool still reports something useful on a machine without `onnx`.
    """
    model_path = Path(model_path)
    if not model_path.is_file():
        raise InputError("model file not found: %s" % model_path.name)
    report = {
        "path": model_path.name,
        "size_bytes": model_path.stat().st_size,
        "sha256": sha256_file(model_path),
        "md5": md5_file(model_path),
        "producer": None,
        "graph": {},
        "opset": None,
        "nodes": None,
        "initializer_bytes": None,
        "params_estimate": None,
        "inputs": [],
        "outputs": [],
        "metadata": {},
        "inspector": "header",
        "warnings": [],
    }
    try:
        onnx = importlib.import_module("onnx")
    except Exception:
        onnx = None
    if onnx is not None:
        report["inspector"] = "onnx"
        model = onnx.load(str(model_path), load_external_data=False)
        graph = model.graph
        report["producer"] = "%s %s" % (model.producer_name, model.producer_version)
        report["opset"] = max((o.version for o in model.opset_import), default=None)
        report["ir_version"] = model.ir_version
        report["metadata"] = {entry.key: entry.value for entry in model.metadata_props}
        report["graph"] = {"name": graph.name, "nodes": len(graph.node)}
        report["nodes"] = len(graph.node)
        init_bytes = 0
        params = 0
        for tensor in graph.initializer:
            init_bytes += len(tensor.raw_data) or 4
            count = 1
            for dim in tensor.dims:
                count *= int(dim)
            params += count
        report["initializer_bytes"] = init_bytes
        report["params_estimate"] = params
        report["float16_initializers"] = sum(1 for t in graph.initializer if t.data_type == 10)
        report["int8_initializers"] = sum(1 for t in graph.initializer if t.data_type == 3)
        def dims(value):
            return [d.dim_value if d.HasField("dim_value") else (d.dim_param or "?")
                    for d in value.type.tensor_type.shape.dim]
        report["inputs"] = [{"name": i.name, "shape": dims(i)} for i in graph.input
                            if i.name not in {t.name for t in graph.initializer}]
        report["outputs"] = [{"name": o.name, "shape": dims(o)} for o in graph.output]
        ops = {}
        for node in graph.node:
            ops[node.op_type] = ops.get(node.op_type, 0) + 1
        report["op_types"] = dict(sorted(ops.items(), key=lambda kv: -kv[1])[:15])
    else:
        head = model_path.read_bytes()[:4096]
        if b"onnx" not in head.lower():
            report["warnings"].append("file does not start with an ONNX container marker")
    if config_path is not None:
        config_path = Path(config_path)
        if config_path.is_file():
            config = inspect_voice_config(config_path)
            report["voice_config"] = {
                "path": config["path"],
                "sample_rate": config["sample_rate"],
                "num_speakers": config["num_speakers"],
                "language": config["language"],
                "phoneme_count": config["phoneme_count"],
            }
            report["sample_rate"] = config["sample_rate"]
            if report["inputs"]:
                names = " ".join(i["name"] for i in report["inputs"])
                if "speaker" in names.lower() and (config["num_speakers"] or 0) > 1:
                    report["multi_speaker_input"] = True
            if (config["num_speakers"] or 1) > 1:
                report["multi_speaker_model"] = True
    report["footprint"] = {
        "on_disk_mib": round(report["size_bytes"] / (1024.0 ** 2), 2),
        "weights_mib": round((report["initializer_bytes"] or 0) / (1024.0 ** 2), 2),
    }
    report["cpu_feasible"] = {
        "decision": "yes" if report["size_bytes"] < 1_500_000_000 else "review",
        "reason": "model fits the default 1.5 GiB memory envelope"
                  if report["size_bytes"] < 1_500_000_000 else
                  "model exceeds the default memory envelope; raise inference.max_memory_bytes",
    }
    return report


def probe_onnxruntime(model_path, *, threads=1, providers=("CPUExecutionProvider",)):
    """Create a CPU inference session and report the resolved providers."""
    ort = import_optional("onnxruntime", feature="onnx-inference",
                          install_hint="pip install onnxruntime")
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(threads)
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    available = ort.get_available_providers()
    usable = [p for p in providers if p in available]
    if not usable:
        raise DependencyError("no requested execution provider is available",
                              details={"requested": list(providers), "available": available},
                              hint="install onnxruntime (CPU) or pass inference.providers")
    started = time.monotonic()
    session = ort.InferenceSession(str(model_path), options, providers=usable)
    elapsed_ms = (time.monotonic() - started) * 1000.0
    inputs = [{"name": i.name, "shape": [d if isinstance(d, (int, str)) else "?" for d in i.shape],
               "type": i.type} for i in session.get_inputs()]
    outputs = [{"name": o.name, "shape": [d if isinstance(d, (int, str)) else "?" for d in o.shape],
                "type": o.type} for o in session.get_outputs()]
    return {
        "session_created_ms": round(elapsed_ms, 2),
        "providers": session.get_providers(),
        "available_providers": available,
        "inputs": inputs,
        "outputs": outputs,
        "opset": getattr(ort, "onnxruntime_version", None) and None,
        "onnxruntime_version": ort.__version__,
    }


# --------------------------------------------------------------------------- #
# Registry and policy assets
# --------------------------------------------------------------------------- #

def load_asset_json(cfg, dotted_key, *, what):
    path = cfg.path(dotted_key, must_exist=True, kind="file")
    return read_json(path, what=what), cfg.rel(path)


def load_registry(cfg):
    data, rel = load_asset_json(cfg, "paths.registry_path", what="model registry")
    if "voices" not in data or not isinstance(data["voices"], list):
        raise InputError("model registry must contain a `voices` list")
    by_id = {}
    for entry in data["voices"]:
        vid = entry.get("id")
        if not vid:
            raise InputError("every registry voice needs an `id`", details={"entry": entry})
        if vid in by_id:
            raise InputError("duplicate registry voice id: %s" % vid)
        by_id[vid] = entry
    return data, by_id, rel


def load_presets(cfg):
    data, rel = load_asset_json(cfg, "paths.presets_path", what="voice presets")
    presets = data.get("presets")
    if not isinstance(presets, list) or not presets:
        raise InputError("voice presets must contain a non-empty `presets` list")
    by_id = {}
    for entry in presets:
        pid = entry.get("id")
        if not pid:
            raise InputError("every preset needs an `id`")
        if pid in by_id:
            raise InputError("duplicate preset id: %s" % pid)
        by_id[pid] = entry
    return data, by_id, rel


def load_policy(cfg):
    data, rel = load_asset_json(cfg, "paths.policies_path", what="rights policy")
    return data, rel


def load_lexicon(cfg):
    data, rel = load_asset_json(cfg, "paths.lexicon_path", what="pronunciation lexicon")
    entries = [e for e in data.get("entries", []) if e.get("enabled", True)]
    data["entries"] = entries
    by_word = {}
    for entry in entries:
        words = entry.get("words") or []
        for word in words:
            by_word.setdefault(word.lower(), entry)
    return data, by_word, rel


# --------------------------------------------------------------------------- #
# Rights evaluation
# --------------------------------------------------------------------------- #

POLICY_GATE_ORDER = ["commercial_release", "public_redistribution", "voice_cloning",
                     "model_redistribution", "personal_use"]


def evaluate_rights(registry_entry, policy, *, use="personal_use", redistributing=False,
                    voice_consent=None):
    """Return a machine readable rights decision with obligations and blockers."""
    gates = policy.get("use_gates", {})
    gate = gates.get(use, gates.get("personal_use", {}))
    licence_id = (registry_entry or {}).get("license_id", "unknown")
    licences = policy.get("licenses", {})
    licence = licences.get(licence_id)
    reasons = []
    blocked = False
    decision = "allow"
    obligations = []

    if licence is None:
        decision = "review-required"
        reasons.append("licence id %r is not described by the rights policy" % licence_id)
        obligations.append("add a policy entry for %s before release" % licence_id)
        licence = {"commercial_use": None, "redistribution": None, "share_alike": None,
                   "attribution_required": None, "url": None}
    else:
        commercial = licence.get("commercial_use")
        redistribution = licence.get("redistribution")
        if use == "commercial_release" and gate.get("require_commercial_use", True) and commercial is not True:
            blocked = blocked or bool(registry_entry)
            reasons.append("licence %s does not permit commercial use" % licence_id)
        if redistributing and gate.get("require_redistribution", True) and redistribution is not True:
            blocked = blocked or bool(registry_entry)
            reasons.append("licence %s does not permit redistribution of model files" % licence_id)
        if licence.get("attribution_required"):
            obligations.append("retain attribution: %s" % (licence.get("attribution") or licence_id))
        if licence.get("share_alike"):
            obligations.append("share-alike: derived audio/model redistribution keeps licence %s" % licence_id)
        if licence.get("non_commercial") and use in ("commercial_release", "public_redistribution"):
            obligations.append("no commercial use: dataset licence %s" % licence_id)

    consent_required = gate.get("require_voice_consent", True)
    if use == "voice_cloning" and consent_required:
        consent = voice_consent or {}
        if not consent.get("affirmed"):
            blocked = True
            reasons.append("voice cloning requires an affirmed speaker consent record")
        else:
            obligations.append("consent record on file: %s" % consent.get("record_id", "unnamed"))
    if blocked:
        decision = "block"
    elif reasons:
        decision = "review-required"
    return {
        "decision": decision,
        "use": use,
        "redistributing": bool(redistributing),
        "license_id": licence_id,
        "license_url": licence.get("url"),
        "blocked": blocked,
        "reasons": reasons,
        "obligations": obligations,
        "approval_required": decision != "allow",
        "policy_version": policy.get("policy_version") or policy.get("version"),
        "decided_at": utc_now(),
    }


# --------------------------------------------------------------------------- #
# Tool metadata, results and CLI helpers
# --------------------------------------------------------------------------- #

def tool_meta(name, summary, capabilities, *, inputs=None, outputs=None, tools=None,
              safety=None, entry=True):
    return {
        "tool": name,
        "summary": summary,
        "entry_point": entry,
        "capabilities": capabilities,
        "inputs": inputs or ["--help"],
        "outputs": outputs or ["structured JSON on stdout", "exit code per exit-codes"],
        "sibling_tools": tools or [],
        "safety": safety or ["read-only", "paths sealed to the skill root"],
        "skill": SKILL_SLUG,
        "version": __version__,
    }


def result_envelope(*, ok, tool, command=None, data=None, errors=None, warnings=None,
                    artifacts=None, approval_required=False, cancelled=False,
                    started_at=None, duration_s=None):
    envelope = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "ok": bool(ok),
        "tool": tool,
        "command": command,
        "skill": SKILL_SLUG,
        "skill_version": __version__,
        "generated_at": utc_now(),
        "started_at": started_at,
        "duration_s": round(duration_s, 3) if duration_s is not None else None,
        "approval_required": bool(approval_required),
        "cancelled": bool(cancelled),
        "errors": errors or [],
        "warnings": warnings or [],
        "artifacts": artifacts or [],
        "data": data if data is not None else {},
    }
    return envelope


def print_json(payload, *, stream=None):
    stream = stream or sys.stdout
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    stream.write(text + "\n")
    stream.flush()


def portable_path(value):
    """Render an absolute state-directory path as ~/..., leaving others untouched."""
    if not isinstance(value, str) or not value:
        return value
    home = Path(os.path.expanduser("~")).resolve()
    text = value.replace(os.sep, "/")
    home_text = str(home).replace(os.sep, "/")
    if text == home_text:
        return "~"
    if text.startswith(home_text + "/"):
        return "~" + text[len(home_text):]
    return value


def human_result(envelope, *, color=False, verbose=False):
    """Render a result envelope for interactive terminals.

    Never colour-only: every line carries a plain status word so the output is
    usable by screen readers and in monochrome.
    """
    status = "OK" if envelope.get("ok") else "FAIL"
    if envelope.get("cancelled"):
        status = "CANCELLED"
    if envelope.get("approval_required"):
        status = "APPROVAL REQUIRED"
    lines = ["[%s] %s %s" % (status, envelope.get("tool"), envelope.get("command") or "")]
    for warning in envelope.get("warnings", []):
        lines.append("  WARN: %s" % warning)
    for error in envelope.get("errors", []):
        lines.append("  ERROR %s: %s" % (error.get("code"), error.get("message")))
        if error.get("hint"):
            lines.append("        hint: %s" % error["hint"])
    summary = envelope.get("data", {}).get("summary")
    if summary:
        lines.append("  %s" % summary)
    for artifact in envelope.get("artifacts", []):
        kind = artifact.get("kind", "file")
        lines.append("  %s: %s" % (kind, portable_path(artifact.get("path"))))
    if verbose:
        lines.append(json.dumps(envelope.get("data", {}), indent=2, ensure_ascii=False, default=str))
    return "\n".join(lines)


def add_common_arguments(parser, *, config=True, journal=True, json_output=True):
    if config:
        parser.add_argument("--config", metavar="FILE",
                            help="configuration JSON file (relative to the skill root)")
    if journal:
        parser.add_argument("--no-journal", action="store_true",
                            help="do not append to the audit journal")
    if json_output:
        parser.add_argument("--format", choices=["json", "text"], default="json",
                            help="stdout format (default: json)")
        parser.add_argument("--verbose", action="store_true", help="include full detail")
    parser.add_argument("--quiet", action="store_true", help="suppress stdout on success")
    return parser


def emit(envelope, args):
    if not getattr(args, "quiet", False) or not envelope.get("ok"):
        if getattr(args, "format", "json") == "text":
            sys.stdout.write(human_result(envelope, verbose=getattr(args, "verbose", False)) + "\n")
        else:
            print_json(envelope)
    return EXIT_OK if envelope.get("ok") else envelope.get("exit_code", EXIT_FAIL)


def run_cli(main, *, tool_name, argv=None, command=None):
    """Wrap a tool main() with consistent error handling and exit codes."""
    started = time.monotonic()
    try:
        code = main(argv)
        return int(code or EXIT_OK)
    except NVError as exc:
        sys.stderr.write("[FAIL] %s %s: %s\n" % (tool_name, exc.code, exc.message))
        if exc.hint:
            sys.stderr.write("       hint: %s\n" % exc.hint)
        payload = result_envelope(ok=False, tool=tool_name, command=command,
                                  errors=[exc.to_dict()],
                                  duration_s=time.monotonic() - started)
        payload["exit_code"] = exc.exit_code
        if _wants_json(argv):
            print_json(payload)
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("[CANCELLED] %s interrupted by user\n" % tool_name)
        return EXIT_CANCELLED
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # unexpected: keep a traceback on stderr only
        sys.stderr.write("[FAIL] %s internal error: %s: %s\n" % (tool_name, type(exc).__name__, exc))
        if os.environ.get("ONV_DEBUG"):
            traceback.print_exc()
        else:
            sys.stderr.write("       set ONV_DEBUG=1 for a traceback\n")
        return EXIT_FAIL


def _wants_json(argv):
    argv = argv if argv is not None else sys.argv[1:]
    return "--format=text" not in argv and not (("--format" in argv) and
                                                argv[argv.index("--format") + 1:argv.index("--format") + 2] == ["text"])


def guard_path_input(cfg, value, *, what="path"):
    """Public helper so tools validate user paths through one implementation."""
    return cfg.resolve(value, must_exist=False)


def require_approval(cfg, *, action, approval_flag, approval_token=None,
                     approved_by=None, policy_key=None, journal=None):
    """Enforce the approval gate for destructive or publishing actions."""
    need = bool(cfg.get(policy_key)) if policy_key else True
    if not need:
        return {"required": False, "approved": True, "action": action}
    if not approval_flag:
        if journal is not None:
            journal.record("approval-denied", action=action)
        raise ApprovalError(
            "action %r requires explicit approval" % action,
            hint="re-run with --approve --approved-by \"<name>\" --approval-token <token> "
                 "after a human reviews the plan",
            details={"action": action, "policy": policy_key or "safety"})
    if not approval_token:
        raise ApprovalError("--approve requires --approval-token to bind the approval to a plan",
                            hint="print the plan first, then pass the token printed in it")
    if journal is not None:
        journal.record("approval-granted", action=action, approved_by=approved_by or "unnamed",
                       token=approval_token)
    return {"required": True, "approved": True, "action": action,
            "approved_by": approved_by or "unnamed", "token": approval_token}


def plan_token(payload):
    """Short deterministic token binding an approval to an exact plan."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def environment_report():
    report = {
        "generated_at": utc_now(),
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "cwd_relative": ".",
        "skill_root_name": skill_root().name,
        "environment_variables": {k: v for k, v in os.environ.items() if k.startswith(ENV_PREFIX)},
    }
    try:
        load1, load5, load15 = os.getloadavg()
        report["load_average"] = [round(load1, 2), round(load5, 2), round(load15, 2)]
    except (OSError, AttributeError):
        report["load_average"] = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values = {}
        for line in meminfo.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                values[parts[0].rstrip(":")] = int(parts[1]) * 1024
        if "MemTotal" in values:
            report["memory"] = {
                "total_bytes": values["MemTotal"],
                "available_bytes": values.get("MemAvailable"),
                "total_human": human_bytes(values["MemTotal"]),
                "available_human": human_bytes(values.get("MemAvailable") or 0),
            }
    else:
        report["memory"] = {"note": "/proc/meminfo unavailable on this platform"}
    return report


# --------------------------------------------------------------------------- #
# Self test
# --------------------------------------------------------------------------- #

def selftest(argv=None):
    parser = argparse.ArgumentParser(prog="tools-nvcore.py selftest",
                                     description="Run the core runtime self test")
    parser.add_argument("--format", choices=["json", "text"], default="text")
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

    def t_wav_roundtrip():
        samples = array("h", [0, 1000, -1000, 32767, -32768] * 100)
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "roundtrip.wav"
        write_wav_int16(path, samples, 16000)
        info = read_wav_info(path)
        back, rate = read_wav_int16(path)
        assert rate == 16000 and info["frames"] == len(samples), info
        assert list(back) == list(samples), "sample mismatch"
        shutil.rmtree(tmp, ignore_errors=True)
        return "16000 Hz mono PCM round-trip preserved %d samples" % len(samples)

    def t_loudness():
        # 1 kHz sine at -20 dBFS must read about -20 LUFS (K-weighting ~ +0.7 dB at 1 kHz)
        rate = 48000
        samples = array("h", bytes(2 * rate))
        amplitude = 32768 * (10 ** (-20.0 / 20.0))
        for idx in range(rate):
            samples[idx] = int(amplitude * math.sin(2 * math.pi * 1000 * idx / rate))
        result = measure_loudness(samples, rate)
        lufs = result["integrated_lufs"]
        # BS.1770 calibration: a 0 dBFS 1 kHz sine reads -3.01 LUFS, so a
        # -20 dBFS sine must read close to -23.01 LUFS.
        assert lufs is not None and -23.6 < lufs < -22.4, result
        return "1 kHz -20 dBFS sine measured %.2f LUFS (reference -23.01)" % lufs

    def t_true_peak():
        rate = 16000
        samples = array("h", [int(32767 * 0.5 * math.sin(2 * math.pi * 2000 * i / rate)) for i in range(4000)])
        tp = true_peak_dbfs(samples, rate)
        assert -7.0 < tp["true_peak_dbfs"] <= -3.0, tp
        return "0.5 FS 2 kHz tone true peak %.2f dBFS" % tp["true_peak_dbfs"]

    def t_resample():
        rate = 22050
        samples = array("h", [int(10000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate)])
        out = resample_int16(samples, rate, 16000)
        assert abs(len(out) - int(rate * 16000 / 22050)) <= 2, len(out)
        return "22050 -> 16000 resample produced %d frames" % len(out)

    def t_schema():
        schema = {"type": "object", "required": ["a"],
                  "properties": {"a": {"type": "integer", "minimum": 1}}}
        assert validate_json({"a": 2}, schema) == []
        assert validate_json({"a": 0}, schema) != []
        assert validate_json({}, schema) != []
        return "valid documents pass and invalid documents are rejected"

    def t_guard():
        with offline_guard(True):
            import socket
            try:
                socket.create_connection(("127.0.0.1", 9), timeout=0.2)
                raise AssertionError("guard failed to block create_connection")
            except NetworkBlockedError:
                pass
        return "offline guard blocked an outbound connection attempt"

    def t_journal():
        tmp = Path(tempfile.mkdtemp())
        journal = Journal(tmp / "journal.jsonl")
        journal.record("selftest", note="hello")
        journal.begin_intent("selftest-operation", target="x")
        recovered = journal.recover()
        assert recovered["interrupted"] is True, recovered
        rows = journal.tail(5)
        assert rows and rows[0]["kind"] == "selftest", rows
        shutil.rmtree(tmp, ignore_errors=True)
        return "journal appended, intent written and recovered"

    def t_schema_json():
        schema_path = skill_root() / "asset-result-schema.json"
        if not schema_path.is_file():
            return "no result schema present"
        schema = read_json(schema_path, what="result schema")
        sample = result_envelope(ok=True, tool=SKILL_SLUG, command="selftest",
                                 data={"summary": "sample"})
        errors = validate_json(sample, schema)
        assert not errors, errors
        bad = dict(sample)
        bad["ok"] = "yes"
        assert validate_json(bad, schema), "schema accepted a wrong type"
        return "result envelope matches the shipped schema"

    def t_text():
        assert normalize_language_tag("en_US") == "en-US"
        assert normalize_language_tag("en-gb") == "en-GB"
        sentences = split_sentences("Greetings, friends! Welcome to my video.")
        assert len(sentences) == 2, sentences
        assert slugify("Voice Preset: Warm, 30s") == "voice-preset-warm-30s", slugify("Voice Preset: Warm, 30s")
        return "language tags, sentence split and slugify behave"

    def t_config():
        cfg = load_config(None, root=skill_root())
        cfg.resolve("tools-nvcore.py", must_exist=True, kind="file")
        optional = skill_root() / "asset-default-config.json"
        if optional.is_file():
            cfg.resolve("asset-default-config.json", must_exist=True, kind="file")
        for bad in ("../etc/passwd", "/etc/passwd", "a/../../b"):
            try:
                cfg.resolve(bad)
                raise AssertionError("path %r was accepted" % bad)
            except InputError:
                pass
        return "configuration loads and sealed paths reject traversal"

    check("wav-roundtrip", t_wav_roundtrip)
    check("loudness-bs1770", t_loudness)
    check("true-peak", t_true_peak)
    check("resample", t_resample)
    check("json-schema", t_schema)
    check("result-schema", t_schema_json)
    check("offline-guard", t_guard)
    check("journal-recovery", t_journal)
    check("text-utils", t_text)
    check("config-seal", t_config)

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "tool": __tool__, "version": __version__, "checks": checks,
               "dependencies": dependency_report()}
    if args.format == "json":
        print_json(payload)
    else:
        for item in checks:
            print("[%s] %s (%sms) %s" % ("PASS" if item["ok"] else "FAIL",
                                         item["check"], item["ms"], item["detail"]))
        print("\n%s: %d/%d core checks passed" % ("PASS" if ok else "FAIL",
                                                  sum(1 for c in checks if c["ok"]), len(checks)))
    return EXIT_OK if ok else EXIT_FAIL


TOOL_SELFTESTS = [
    ("tools-nvcore.py", ["selftest", "--format", "json"]),
    ("tools-nv-engine.py", ["selftest", "--format", "json"]),
    ("tools-nv-text.py", ["selftest", "--format", "json"]),
    ("tools-nv-master.py", ["selftest", "--format", "json"]),
    ("tools-nv-qa.py", ["selftest", "--format", "json"]),
    ("tools-nv-ops.py", ["selftest", "--format", "json"]),
    ("tools-nv-config.py", ["selftest"]),
]


def selftest_all(argv=None):
    """Run every tools-*.py self test in loading order and aggregate the result."""
    import subprocess
    parser = argparse.ArgumentParser(prog="tools-nvcore.py selftest-all")
    parser.add_argument("--model", help="optional .onnx model for the engine run test")
    parser.add_argument("--config-file", help="optional .onnx.json for that model")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args(argv)
    rows = []
    for filename, extra in TOOL_SELFTESTS:
        argv_cmd = [sys.executable, "-B", str(skill_root() / filename)] + list(extra)
        if filename == "tools-nv-engine.py" and args.model:
            argv_cmd += ["--model", args.model]
            if args.config_file:
                argv_cmd += ["--config-file", args.config_file]
        started = time.monotonic()
        proc = subprocess.run(argv_cmd, cwd=str(skill_root()), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=600)
        elapsed = round((time.monotonic() - started) * 1000, 2)
        detail = ""
        try:
            payload = json.loads(proc.stdout.decode("utf-8", "replace"))
            failed = [c for c in payload.get("checks", []) if not c.get("ok")]
            detail = "%d/%d checks" % (len(payload.get("checks", [])) - len(failed),
                                       len(payload.get("checks", [])))
            if failed:
                detail += "; failed: " + ", ".join(c["check"] for c in failed)
        except Exception:
            tail = (proc.stdout.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
            detail = tail or (proc.stderr.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
        rows.append({"tool": filename, "exit_code": proc.returncode,
                     "ok": proc.returncode == 0, "ms": elapsed, "detail": detail})
    ok = all(row["ok"] for row in rows)
    payload = {"ok": ok, "tool": __tool__, "version": __version__, "runs": rows}
    if args.format == "json":
        print_json(payload)
    else:
        for row in rows:
            print("[%s] %-24s %7sms  %s" % ("PASS" if row["ok"] else "FAIL", row["tool"],
                                            row["ms"], row["detail"]))
        print("\n%s: %d/%d tool self tests passed" % (
            "PASS" if ok else "FAIL", sum(1 for r in rows if r["ok"]), len(rows)))
    return EXIT_OK if ok else EXIT_FAIL


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        print("Usage: python tools-nvcore.py <command> [options]")
        print("\nCommands:")
        print("  selftest      run the core runtime self test")
        print("  selftest-all  run every tools-*.py self test and aggregate the result")
        print("\nThis file is a shared runtime. Run the entry point instead:")
        print("  python tools-nv-voice.py --help")
        print("  python tools-nv-voice.py list-tools")
        return EXIT_OK
    command = argv[0]
    if command == "selftest":
        return selftest(argv[1:])
    if command == "selftest-all":
        return selftest_all(argv[1:])
    sys.stderr.write("unknown command %r\n" % command)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
