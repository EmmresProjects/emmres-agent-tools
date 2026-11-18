#!/usr/bin/env python3
"""tools-providers.py — provider implementations and routing for the
core-synthesis-modules Skill Tool.

Implements the provider capabilities from catalog category "Core Synthesis
Scripts and Modules":
  * provider_local_onnx.py — ONNX Runtime neural engine (optional dependency)
  * provider_piper.py      — local Piper engine via subprocess (optional)
  * provider_router.py     — priority, health checks, timeout, fallback
  * stub provider          — deterministic offline synthesizer for pipeline
                             validation (test fixture, NOT a neural voice;
                             always carries a quality warning)

Design rules:
  * Local-only. No network, no API calls, no first-run downloads.
  * subprocess use is bounded: allowlisted executables only, no shell=True,
    hard timeouts, and temp files cleaned up in finally blocks.
  * Providers are deterministic: same input + config -> byte-identical output.
"""

import importlib.util as _ilu
import math
import os
import shutil
import subprocess
import sys as _sys
import tempfile
import time


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

read_wav = _audio.read_wav
Tokenizer = _core.Tokenizer
SkillToolError = _core.SkillToolError
asset_path = _core.asset_path
load_config = _core.load_config

try:  # numpy is required by onnxruntime; keep the import lazy per provider
    import numpy as np
except Exception:  # pragma: no cover
    np = None


class ProviderBase:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg
        self.pcfg = cfg["providers"].get(self.name, {})
        self.sample_rate = int(cfg["audio"].get("sample_rate", 22050))

    def available(self):
        """Return (bool, reason) for this provider's health."""
        raise NotImplementedError

    def health(self):
        ok, reason = self.available()
        return {"name": self.name, "available": ok, "reason": reason}

    def synthesize(self, text, plan, preset, audio_cfg, deadline=None):
        """Synthesize text into float samples [-1, 1].

        Returns (samples, meta) where meta includes provider timing info
        used by the acceptance gate (word regions, gains, pauses).
        """
        raise NotImplementedError

    def _check_deadline(self, deadline):
        if deadline is not None:
            deadline()


class StubProvider(ProviderBase):
    """Deterministic offline synthesizer for pipeline validation.

    Generates formant-like audio from the prosody plan. It is NOT a neural
    voice and is clearly flagged with a quality warning. It exists so the
    full pipeline (plan -> synthesize -> master -> validate -> acceptance)
    can run and be tested offline without any model weights.
    """

    name = "stub"

    QUALITY_WARNING = (
        "stub provider output is deterministic formant-like audio for pipeline "
        "validation only; it is not a neural voice and quality is NOT acceptable "
        "for production delivery"
    )

    def available(self):
        return True, "deterministic offline synthesizer (test provider)"

    def _char_wave(self, ch, duration_ms):
        """Generate a short waveform for one character."""
        fs = self.sample_rate
        n = int(fs * duration_ms / 1000.0)
        if n <= 0:
            return []
        is_vowel = ch.lower() in "aeiouyAEIOUY"
        if is_vowel:
            # two-partial vowel-like tone, pitch follows preset
            pitch = 1.0  # base; emphasis handled by gain at word level
            f1 = 180.0 * pitch
            f2 = f1 * 2.5
            out = []
            for i in range(n):
                t = i / fs
                attack = min(1.0, i / (0.01 * fs))
                decay = max(0.0, 1.0 - i / (0.9 * n))
                env = attack * decay
                v = 0.7 * math.sin(2 * math.pi * f1 * t) + 0.3 * math.sin(2 * math.pi * f2 * t)
                out.append(v * env)
            return out
        else:
            # deterministic noise-like burst for consonants
            out = []
            h = (ord(ch) * 2654435761) & 0xFFFFFFFF
            for i in range(n):
                h = (h * 1103515245 + 12345) & 0x7FFFFFFF
                v = ((h >> 8) & 0xFF) / 127.5 - 1.0
                attack = min(1.0, i / (0.005 * fs))
                decay = max(0.0, 1.0 - i / (0.7 * n))
                out.append(v * attack * decay * 0.5)
            return out

    def synthesize(self, text, plan, preset, audio_cfg, deadline=None):
        samples = []
        word_regions = []
        cursor = 0
        for word in plan["words"]:
            self._check_deadline(deadline)
            if word["pause_before_ms"] > 0:
                samples.extend([0.0] * int(self.sample_rate * word["pause_before_ms"] / 1000.0))
                cursor += word["pause_before_ms"]
            core = "".join(c for c in word["text"] if not c.isspace())
            if not core:
                continue
            char_ms = word["duration_ms"] / max(1, len(core))
            word_samples = []
            for ch in core:
                wave_seg = self._char_wave(ch, char_ms)
                word_samples.extend(wave_seg)
            # apply emphasis gain
            if abs(word["gain"] - 1.0) > 1e-9:
                word_samples = [s * word["gain"] for s in word_samples]
            samples.extend(word_samples)
            word_regions.append({
                "word": word["text"],
                "start_ms": round(cursor, 1),
                "end_ms": round(cursor + len(word_samples) / self.sample_rate * 1000.0, 1),
                "gain": word["gain"],
                "pause_after_ms": word["pause_after_ms"],
            })
            cursor += len(word_samples) / self.sample_rate * 1000.0
            if word["pause_after_ms"] > 0:
                silence = [0.0] * int(self.sample_rate * word["pause_after_ms"] / 1000.0)
                samples.extend(silence)
                cursor += word["pause_after_ms"]
        meta = {
            "provider": self.name,
            "quality_warning": self.QUALITY_WARNING,
            "sample_rate": self.sample_rate,
            "word_regions": word_regions,
            "planned_total_ms": plan.get("total_ms"),
        }
        return samples, meta


class OnnxProvider(ProviderBase):
    """Local ONNX Runtime neural engine.

    Uses the fixture model asset (asset-test-model.onnx) by default. Production
    deployments point provider_local_onnx.model_path at a real model (e.g.,
    Kokoro / Piper ONNX export) with verified license and checksum.
    """

    name = "onnx"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._ort = None
        self._session = None
        self._input_len = int(self.pcfg.get("input_tokens", 256))
        self._frames_per_token = int(self.pcfg.get("frames_per_token", 1102))

    def available(self):
        if not self.pcfg.get("enabled", True):
            return False, "provider disabled in config"
        if np is None:
            return False, "numpy not installed (required by onnxruntime)"
        try:
            import onnxruntime  # noqa: F401
        except Exception as exc:
            return False, "onnxruntime not installed: %s" % exc
        model_path = self._model_path()
        if not os.path.exists(model_path):
            return False, "model file not found: %s" % model_path
        return True, "onnxruntime available, model found"

    def _model_path(self):
        model_path = self.pcfg.get("model_path")
        if not model_path:
            raise SkillToolError("MODEL_NOT_FOUND", "onnx provider has no model_path")
        if not os.path.isabs(model_path):
            model_path = asset_path(model_path)
        return model_path

    def _ensure_session(self):
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise SkillToolError("PROVIDER_UNAVAILABLE",
                                 "onnxruntime not installed: %s" % exc)
        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = int(self.cfg.get("limits", {}).get(
            "onnx_intra_op_threads", 1))
        self._session = ort.InferenceSession(self._model_path(), sess_opts)
        self._ort = ort

    def synthesize(self, text, plan, preset, audio_cfg, deadline=None):
        self._ensure_session()
        tokenizer = Tokenizer()

        # Build the token sequence: chars + word-separator space token.
        ids = []
        spans = []  # (word_index, position_in_word)
        for word in plan["words"]:
            self._check_deadline(deadline)
            core = "".join(c for c in word["text"] if not c.isspace())
            for ch in core:
                tid = tokenizer.tokens.get(ch, tokenizer.unk_id)
                ids.append(tid)
                spans.append(word["word_index"])
            if ids and tokenizer.space_id is not None:
                ids.append(tokenizer.space_id)
                spans.append(word["word_index"])

        n_tokens = len(ids)
        if n_tokens == 0:
            raise SkillToolError("INPUT_VALIDATION", "plan produced no tokens")
        max_len = min(self._input_len, max(n_tokens, 1))
        if n_tokens > self._input_len:
            raise SkillToolError("INPUT_VALIDATION",
                                 "text exceeds model input capacity (%d > %d tokens)"
                                 % (n_tokens, self._input_len),
                                 {"hint": "split the text with the chunk command"})
        arr = np.zeros(self._input_len, dtype=np.int64)
        arr[:n_tokens] = ids
        input_name = self._session.get_inputs()[0].name
        t0 = time.monotonic()
        outputs = self._session.run(None, {input_name: arr})
        elapsed = time.monotonic() - t0
        out = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        frames_per_token = self._frames_per_token

        # Assemble audio: per-word segments (with emphasis gain) + pauses.
        samples = []
        word_regions = []
        cursor_ms = 0.0
        token_pos = 0
        for word in plan["words"]:
            self._check_deadline(deadline)
            core = "".join(c for c in word["text"] if not c.isspace())
            n_chars = len(core) + 1  # + space separator token
            seg = out[token_pos * frames_per_token:(token_pos + n_chars) * frames_per_token]
            token_pos += n_chars
            seg = seg.astype(float).tolist()
            if abs(word["gain"] - 1.0) > 1e-9:
                seg = [s * word["gain"] for s in seg]
            samples.extend(seg)
            seg_ms = len(seg) / self.sample_rate * 1000.0
            word_regions.append({
                "word": word["text"],
                "start_ms": round(cursor_ms, 1),
                "end_ms": round(cursor_ms + seg_ms, 1),
                "gain": word["gain"],
                "pause_after_ms": word["pause_after_ms"],
            })
            cursor_ms += seg_ms
            if word["pause_after_ms"] > 0:
                silence = [0.0] * int(self.sample_rate * word["pause_after_ms"] / 1000.0)
                samples.extend(silence)
                cursor_ms += word["pause_after_ms"]
        meta = {
            "provider": self.name,
            "quality_warning": None,
            "sample_rate": self.sample_rate,
            "model_name": self.pcfg.get("model_name", "onnx-model"),
            "tokens": n_tokens,
            "inference_seconds": round(elapsed, 4),
            "word_regions": word_regions,
            "planned_total_ms": plan.get("total_ms"),
        }
        return samples, meta


class PiperProvider(ProviderBase):
    """Local Piper engine via subprocess (optional integration).

    Bounded execution: allowlisted executable only, no shell, hard timeout,
    temp working directory cleaned up afterwards.
    """

    name = "piper"

    def available(self):
        if not self.pcfg.get("enabled", True):
            return False, "provider disabled in config"
        executable = self.pcfg.get("executable", "piper")
        allowlist = self.pcfg.get("allowed_executables", [executable])
        if os.path.sep in executable or executable in (".", "..") or \
                os.path.basename(executable) != executable:
            return False, "piper executable must be a bare command name on the allowlist"
        if executable not in allowlist:
            return False, "piper executable not on allowlist"
        path = shutil.which(executable)
        if not path:
            return False, "piper executable not found on PATH: %s" % executable
        return True, "piper executable found: %s" % path

    def synthesize(self, text, plan, preset, audio_cfg, deadline=None):
        ok, reason = self.available()
        if not ok:
            raise SkillToolError("PROVIDER_UNAVAILABLE",
                                 "piper unavailable: %s" % reason)
        executable = self.pcfg.get("executable", "piper")
        model_path = self.pcfg.get("model_path")
        if not model_path:
            raise SkillToolError("MODEL_NOT_FOUND", "piper provider has no model_path")
        if not os.path.isabs(model_path):
            model_path = asset_path(model_path)
        if not os.path.exists(model_path):
            raise SkillToolError("MODEL_NOT_FOUND", "piper model not found: %s" % model_path)
        timeout = float(self.pcfg.get("timeout_seconds", 60))
        tmpdir = tempfile.mkdtemp(prefix="voice-piper-")
        tmp_out = os.path.join(tmpdir, "out.wav")
        try:
            args = [executable, "-m", model_path, "-f", tmp_out]
            proc = subprocess.run(args, input=text.encode("utf-8"),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout, check=False)
            if proc.returncode != 0:
                raise SkillToolError("PROVIDER_FAILURE",
                                     "piper exited with code %d" % proc.returncode,
                                     {"stderr": proc.stderr.decode("utf-8", "replace")[:500]})
            if not os.path.exists(tmp_out):
                raise SkillToolError("PROVIDER_FAILURE", "piper produced no output file")
            data = read_wav(tmp_out)
            meta = {
                "provider": self.name,
                "quality_warning": None,
                "sample_rate": data["sample_rate"],
                "word_regions": [],
                "planned_total_ms": plan.get("total_ms"),
            }
            return data["samples"], meta
        except subprocess.TimeoutExpired:
            raise SkillToolError("TIMEOUT", "piper exceeded %ss timeout" % timeout)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


PROVIDER_CLASSES = {
    "onnx": OnnxProvider,
    "piper": PiperProvider,
    "stub": StubProvider,
}


class ProviderRouter:
    """Priority, health checks, timeout/deadline, fallback, and recovery."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.order = list(self.cfg["providers"].get("order", ["stub"]))
        self.providers = {}
        for name in self.order:
            cls = PROVIDER_CLASSES.get(name)
            if cls is None:
                continue
            self.providers[name] = cls(self.cfg)

    def health(self):
        return {name: p.health() for name, p in self.providers.items()}

    def ordered(self, preference=None):
        """Order providers: explicit preference first, then config order."""
        names = [preference] if preference else []
        for name in self.order:
            if name not in names:
                names.append(name)
        return [n for n in names if n in self.providers]

    def synthesize(self, text, plan, preset, audio_cfg, preference=None,
                   deadline=None):
        """Synthesize with fallback. Returns (samples, meta, used, warnings).

        Raises SkillToolError(PROVIDER_FAILURE) if every provider fails.
        """
        warnings = []
        errors = []
        for name in self.ordered(preference):
            provider = self.providers[name]
            try:
                ok, reason = provider.available()
                if not ok:
                    errors.append({"provider": name, "error": "unavailable",
                                   "reason": reason})
                    continue
                samples, meta = provider.synthesize(text, plan, preset, audio_cfg,
                                                   deadline=deadline)
                meta["fallback_chain"] = errors
                if meta.get("quality_warning"):
                    warnings.append(meta["quality_warning"])
                return samples, meta, name, warnings
            except SkillToolError as exc:
                errors.append({"provider": name, "error": exc.code,
                               "message": exc.message})
                continue
            except Exception as exc:  # provider runtime errors -> structured failure
                errors.append({"provider": name, "error": "PROVIDER_FAILURE",
                               "message": str(exc)})
                continue
        raise SkillToolError("PROVIDER_FAILURE",
                             "all providers failed; see details", {"attempts": errors})

    @staticmethod
    def make_deadline(seconds):
        """Create a deadline checker closure."""
        start = time.monotonic()
        limit = float(seconds) if seconds else None

        def check():
            if limit is not None and time.monotonic() - start > limit:
                raise SkillToolError("TIMEOUT", "synthesis exceeded %ss deadline" % limit)
        return check
