#!/usr/bin/env python3
"""Engine registry and provider routing for Offline Neural Voice.

Engines:
  auto         - piper if importable, else bundled-venv worker, else fail (exit 3)
  piper        - Piper neural ONNX (bundled model or --model path)
  piper-custom - Piper-compatible ONNX model supplied by the user (--model + --model-config)

Hard policy (catalog categories 2 and 31): NO silent formant fallback, NO
network calls, NO cloud endpoints. Absence of a neural engine is a hard,
explicit error with setup instructions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
WORKER = SKILL_ROOT / "tools-ov-engine-worker.py"
DEFAULT_VENV = SKILL_ROOT / ".ov-venv"
DEFAULT_MODEL = SKILL_ROOT / "asset-voice-lessac-medium.onnx"
DEFAULT_MODEL_CONFIG = SKILL_ROOT / "asset-voice-lessac-medium.onnx.json"
CHECKSUMS = SKILL_ROOT / "dependencies-checksums.txt"

ENGINE_QUALITY_MIN_WARNING = {"x_low", "low"}


class ProviderError(Exception):
    def __init__(self, message, code=3, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def venv_python(venv_dir) -> Path:
    base = Path(venv_dir)
    for candidate in (base / "bin" / "python", base / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    return base / "bin" / "python"


def _piper_importable() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except Exception:
        return False


def model_quality(model_config_path):
    try:
        with open(model_config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return (cfg.get("audio", {}) or {}).get("quality") or "unknown"
    except Exception:
        return "unknown"


def model_integrity(model_path: Path) -> dict:
    """Check the bundled model against dependencies-checksums.txt when applicable."""
    try:
        resolved = model_path.resolve()
    except OSError:
        resolved = model_path
    expected = None
    if CHECKSUMS.is_file() and model_path.name.startswith("asset-"):
        for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == model_path.name:
                expected = parts[0].lower()
                break
    if expected is None:
        return {"status": "untracked", "note": "custom model path; checksum not pinned by this bundle"}
    import hashlib
    h = hashlib.sha256()
    with open(resolved, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return {"status": "ok" if h.hexdigest() == expected else "mismatch", "sha256": h.hexdigest()}


def engine_probe(cfg: dict) -> dict:
    model = Path(cfg.get("model", DEFAULT_MODEL))
    model_cfg = Path(cfg.get("model_config", DEFAULT_MODEL_CONFIG))
    venv = Path(cfg.get("venv", DEFAULT_VENV))
    vp = venv_python(venv)
    report = {
        "piper_importable_in_current_interpreter": _piper_importable(),
        "venv_python": str(vp),
        "venv_python_exists": vp.is_file(),
        "model_path": str(model),
        "model_exists": model.is_file(),
        "model_config_exists": model_cfg.is_file(),
        "model_quality": model_quality(model_cfg) if model_cfg.is_file() else "missing-config",
        "model_integrity": model_integrity(model) if model.is_file() else {"status": "missing"},
        "selected": None,
        "ready": False,
    }
    engine = (cfg.get("engine") or "auto").lower()
    if engine in ("auto", "piper", "piper-custom"):
        if report["piper_importable_in_current_interpreter"]:
            report["selected"] = "piper-inprocess"
        elif report["venv_python_exists"]:
            report["selected"] = "piper-venv-worker"
    report["ready"] = bool(report["model_exists"] and report["model_config_exists"]
                            and report["selected"] is not None
                            and report["model_integrity"].get("status") in ("ok", "untracked"))
    report["quality_warning"] = quality_warning(report["model_quality"])
    return report


def quality_warning(quality: str):
    if str(quality).lower() in ENGINE_QUALITY_MIN_WARNING:
        return (f"bundled voice is the '{quality}' quality tier; expect audible artifacts. "
                "Prefer a medium/high quality model for acceptance-facing output.")
    return None


def resolve_engine(cfg: dict) -> str:
    engine = (cfg.get("engine") or "auto").lower()
    if engine == "formant":
        raise ProviderError(
            "Formant (non-neural) synthesis is disabled by policy in this Skill Tool; "
            "the catalog forbids silent degradation to non-neural voices. "
            "Install the neural engine with tools-ov-setup.py instead.",
            code=3, details={"policy": "no-silent-formant-fallback"})
    probe = engine_probe(cfg)
    if not probe["ready"]:
        missing = []
        if not probe.get("model_exists"):
            missing.append("voice model (run: python3 tools-ov-fetch-model.py --yes, or --from-file for air-gapped install)")
        if not probe.get("venv_python_exists") and not probe.get("piper_importable_in_current_interpreter"):
            missing.append("engine install (run: python3 tools-ov-setup.py)")
        raise ProviderError(
            "Neural engine is not ready. Missing: " + ("; ".join(missing) or "unknown (run: python3 tools-offline-voice.py diagnose)"),
            code=3, details=probe)
    return probe["selected"]


def _write_job(job: dict) -> str:
    fd, path = tempfile.mkstemp(prefix="ov-job-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def synthesize(job: dict, cfg: dict) -> dict:
    """Run synthesis. Returns metadata dict; raises ProviderError on failure."""
    engine = resolve_engine(cfg)
    model = Path(cfg.get("model", DEFAULT_MODEL))
    model_cfg = Path(cfg.get("model_config", DEFAULT_MODEL_CONFIG))
    timeout = int(cfg.get("synthesis_timeout_s", 120))
    retries = int(cfg.get("engine_retries", 1))
    job = dict(job)
    job["model"] = str(model)
    job["model_config"] = str(model_cfg)
    job["sample_rate"] = int(cfg.get("sample_rate", 22050))

    output_wav = Path(job["output_wav"])
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    if engine == "piper-inprocess":
        import importlib.util
        spec = importlib.util.spec_from_file_location("ov_worker", WORKER)
        worker_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker_mod)
        result = worker_mod.run_job(job)
        if not result.get("ok"):
            raise ProviderError(result.get("error", "synthesis failed"), code=3, details=result)
        result["engine"] = engine
        result["attempts"] = 1
        result["quality_warning"] = quality_warning(model_quality(model_cfg))
        return result

    # venv worker via subprocess — no shell, bounded time, JSON in/out
    venv = Path(cfg.get("venv", DEFAULT_VENV))
    python = venv_python(venv)
    job_path = _write_job(job)
    attempts, last_error = 0, None
    started = time.time()
    try:
        for attempt in range(1, retries + 2):
            attempts = attempt
            proc = subprocess.run(
                [str(python), str(WORKER), "--job", job_path],
                capture_output=True, text=True, timeout=timeout, check=False)
            stdout = proc.stdout.strip()
            try:
                result = json.loads(stdout.splitlines()[-1]) if stdout else {}
            except json.JSONDecodeError:
                result = {}
            if proc.returncode == 0 and result.get("ok"):
                result["engine"] = engine
                result["attempts"] = attempts
                result["wall_time_s"] = round(time.time() - started, 3)
                result["quality_warning"] = quality_warning(model_quality(model_cfg))
                return result
            last_error = result.get("error") or proc.stderr.strip()[-500:] or f"worker exited {proc.returncode}"
            if attempt <= retries:
                time.sleep(0.5)
        raise ProviderError(f"Neural synthesis failed after {attempts} attempt(s): {last_error}",
                            code=3, details={"attempts": attempts, "last_error": last_error})
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"Synthesis timed out after {timeout}s (increase synthesis_timeout_s)",
                            code=3, details={"timeout_s": timeout}) from exc
    finally:
        try:
            os.unlink(job_path)
        except OSError:
            pass
