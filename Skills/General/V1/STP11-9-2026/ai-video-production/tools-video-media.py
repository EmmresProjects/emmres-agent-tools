#!/usr/bin/env python3
"""Optional, bounded FFmpeg animatic renderer. Imported by tools-ai-video.py."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from importlib.machinery import SourceFileLoader
_core = SourceFileLoader("video_core", str(Path(__file__).with_name("tools-video-core.py"))).load_module()
ToolError = _core.ToolError


def executable_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ToolError("INVALID_EXECUTABLE", "Executable name is invalid")
    path = shutil.which(name)
    if not path:
        raise ToolError("RUNTIME_UNAVAILABLE", f"Required executable was not found: {name}", {"recovery": "Install FFmpeg, or set runtime.ffmpeg_executable and runtime.ffprobe_executable in a copied configuration file."})
    return path


def probe(config: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in ("ffmpeg_executable", "ffprobe_executable"):
        name = config["runtime"].get(key, key.replace("_executable", ""))
        result[key] = {"requested": name, "path": shutil.which(name), "available": shutil.which(name) is not None}
    return result


def run_bounded(argv: list[str], timeout: float, retries: int, backoff: float, cancel_check: Callable[[], bool] | None = None) -> subprocess.CompletedProcess[str]:
    """Run an argument vector without a shell; kill the process group on timeout/cancel."""
    last = None
    for attempt in range(retries + 1):
        if cancel_check and cancel_check():
            raise ToolError("CANCELLED", "Operation cancelled before process start")
        start = time.monotonic()
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, max(0.01, timeout - (time.monotonic() - start))))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - start >= timeout:
                        os.killpg(process.pid, 15)
                        try:
                            process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, 9)
                            process.communicate()
                        raise ToolError("TIMEOUT", f"External process exceeded {timeout} seconds", {"attempt": attempt + 1})
                    if cancel_check and cancel_check():
                        os.killpg(process.pid, 15)
                        process.communicate(timeout=2)
                        raise ToolError("CANCELLED", "External process cancelled")
            last = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            if process.returncode == 0:
                return last
            # Invalid inputs are deterministic and should not be retried.
            if process.returncode == 1:
                break
        except ToolError:
            if attempt >= retries:
                raise
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise ToolError("RENDER_FAILED", "FFmpeg exited unsuccessfully", {"returncode": last.returncode if last else None, "stderr": (last.stderr if last else "")[-8000:], "attempts": retries + 1})


def render_animatic(plan: dict[str, Any], output: str, config: dict[str, Any], approved: bool, overwrite: bool, cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    if not approved:
        raise ToolError("APPROVAL_REQUIRED", "Rendering starts an external media process and requires --approve")
    target = Path(output)
    if target.suffix.lower() != ".mp4":
        raise ToolError("INVALID_OUTPUT", "The built-in animatic renderer supports .mp4 output")
    if target.exists() and not overwrite:
        raise ToolError("OUTPUT_EXISTS", f"Output exists: {target}", {"recovery": "Choose another output or pass --overwrite with --approve."})
    if not plan.get("shots"):
        raise ToolError("INVALID_PLAN", "Plan has no shots")
    ffmpeg = executable_path(config["runtime"]["ffmpeg_executable"])
    ffprobe = executable_path(config["runtime"]["ffprobe_executable"])
    width, height = plan["brief"]["resolution"]
    fps = plan["brief"]["fps"]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = target.with_name(target.stem + ".partial.mp4")
    temporary_output.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="ai-video-render-") as directory:
        filter_path = Path(directory) / "filters.txt"
        filters, labels = [], []
        for index, shot in enumerate(plan["shots"]):
            duration = float(shot["duration_seconds"])
            if duration <= 0:
                raise ToolError("INVALID_PLAN", f"Shot {index + 1} has non-positive duration")
            color = config["defaults"]["background_colors"][index % len(config["defaults"]["background_colors"])]
            # Escape the only dynamic color value conservatively; visual text remains in the HTML/SRT artifacts.
            if not isinstance(color, str) or not color.startswith("#") or len(color) not in (7, 9):
                color = "0x111827"
            else:
                color = "0x" + color[1:]
            filters.append(f"color=c={color}:s={width}x{height}:r={fps}:d={duration:.3f},format=yuv420p,setsar=1[v{index}]")
            labels.append(f"[v{index}]")
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[outv]")
        filter_path.write_text(";\n".join(filters), encoding="utf-8")
        argv = [ffmpeg, "-hide_banner", "-nostdin", "-y" if overwrite else "-n", "-filter_complex_script", str(filter_path), "-map", "[outv]", "-an", "-c:v", plan["brief"]["delivery"].get("codec", "libx264"), "-movflags", "+faststart", str(temporary_output)]
        try:
            completed = run_bounded(argv, float(config["runtime"]["timeout_seconds"]), int(config["runtime"]["retries"]), float(config["runtime"]["retry_backoff_seconds"]), cancel_check)
            inspection = run_bounded([ffprobe, "-v", "error", "-show_entries", "format=duration,size:stream=codec_name,width,height,r_frame_rate", "-of", "json", str(temporary_output)], 30, 0, 0, cancel_check)
            metadata = json.loads(inspection.stdout)
            os.replace(temporary_output, target)
            return {"status": "rendered", "output": str(target), "media": metadata, "renderer": "FFmpeg color-panel animatic", "note": "This output contains color panels only. Use preview.html for editable text/caption visuals; no neural footage was generated.", "ffmpeg_stderr_tail": completed.stderr[-2000:]}
        finally:
            temporary_output.unlink(missing_ok=True)
