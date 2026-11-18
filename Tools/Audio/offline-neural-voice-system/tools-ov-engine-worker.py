#!/usr/bin/env python3
"""Synthesis worker — runs inside the offline venv (or any interpreter with piper-tts).

Reads a job JSON file, synthesizes with Piper ONNX, writes a 16-bit PCM WAV.
No network access. No shell. Bounded job size.

CLI:   python3 tools-ov-engine-worker.py --job job.json
API:   from tools-ov-engine-worker import run_job; run_job(job_dict)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

MAX_SEGMENTS = 256
MAX_TEXT_CHARS = 20000


def _silence_frames(ms: int, rate: int) -> bytes:
    frames = int(rate * ms / 1000)
    return b"\x00\x00" * frames


def run_job(job: dict) -> dict:
    started = time.time()
    try:
        model = Path(job["model"])
        model_config = Path(job.get("model_config") or (str(model) + ".json"))
        output_wav = Path(job["output_wav"])
        segments = job.get("segments") or []
        if not segments:
            text = (job.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "job has neither segments nor text"}
            segments = [{"text": text}]
        if len(segments) > MAX_SEGMENTS:
            return {"ok": False, "error": f"job has {len(segments)} segments; limit is {MAX_SEGMENTS}"}
        total_chars = sum(len(s.get("text", "")) for s in segments)
        if total_chars > MAX_TEXT_CHARS:
            return {"ok": False, "error": f"job text is {total_chars} chars; limit is {MAX_TEXT_CHARS}"}
        if not model.is_file():
            return {"ok": False, "error": f"model not found: {model}"}
        if not model_config.is_file():
            return {"ok": False, "error": f"model config not found: {model_config}"}

        from piper import PiperVoice, SynthesisConfig  # imported lazily: venv-only dependency

        voice = PiperVoice.load(str(model), config_path=str(model_config))

        default_rate = float(job.get("rate", 1.0))
        base_length = 1.0 / default_rate if default_rate else 1.0
        noise_scale = job.get("noise_scale")
        noise_w = job.get("noise_w")
        length_scale = job.get("length_scale")
        speaker_id = job.get("speaker_id")

        sample_rate = None
        pcm = bytearray()
        for index, seg in enumerate(segments):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            pause_before = int(seg.get("pause_before_ms", 0))
            if pause_before > 0 and sample_rate:
                pcm += _silence_frames(pause_before, sample_rate)
            rate_mult = float(seg.get("rate", 1.0)) or 1.0
            seg_length = (length_scale / rate_mult) if length_scale else (1.0 / rate_mult)
            if rate_mult == 1.0 and not length_scale:
                seg_length = base_length
            seg_length = max(0.5, min(2.5, seg_length))
            vol_db = float(seg.get("volume_db", 0.0))
            volume = 10.0 ** (vol_db / 20.0)
            cfg = SynthesisConfig(
                speaker_id=speaker_id if isinstance(speaker_id, int) else None,
                length_scale=seg_length,
                noise_scale=(None if noise_scale is None else float(noise_scale)),
                noise_w_scale=(None if noise_w is None else float(noise_w)),
                volume=max(0.1, min(3.0, volume)),
            )
            for chunk in voice.synthesize(text, syn_config=cfg):
                if sample_rate is None:
                    sample_rate = chunk.sample_rate
                    if pause_before > 0:
                        pcm = bytearray(_silence_frames(pause_before, sample_rate) + bytes(pcm))
                if chunk.sample_rate != sample_rate:
                    return {"ok": False, "error": "inconsistent sample rates inside model output"}
                pcm += chunk.audio_int16_bytes
        if sample_rate is None or not pcm:
            return {"ok": False, "error": "synthesis produced no audio"}

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(bytes(pcm))
        duration = len(pcm) / 2 / sample_rate
        return {"ok": True, "output_wav": str(output_wav), "sample_rate": sample_rate,
                "frames": len(pcm) // 2, "duration_s": round(duration, 3),
                "segments": len(segments),
                "wall_time_s": round(time.time() - started, 3)}
    except Exception as exc:  # report, never traceback-dump secrets
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline neural voice synthesis worker")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    args = parser.parse_args()
    job_path = Path(args.job)
    if not job_path.is_file():
        print(json.dumps({"ok": False, "error": f"job file not found: {job_path}"}))
        return 3
    try:
        with open(job_path, "r", encoding="utf-8") as fh:
            job = json.load(fh)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"job file is not valid JSON: {exc}"}))
        return 3
    if not isinstance(job, dict):
        print(json.dumps({"ok": False, "error": "job must be a JSON object"}))
        return 3
    result = run_job(job)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
