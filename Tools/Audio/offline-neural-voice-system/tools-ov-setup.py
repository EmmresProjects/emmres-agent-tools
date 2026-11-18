#!/usr/bin/env python3
"""Offline installer / verifier for the Offline Neural Voice Skill Tool.

Creates a private venv next to this file (default <skill-root>/.ov-venv) and
installs the pinned wheels bundled in this folder. No network access is ever
used. Requires python3 >= 3.9. Non-privileged. Idempotent.

Usage:
  python3 tools-ov-setup.py [--venv-dir DIR] [--check-only] [--force] [--report FILE]
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
CHECKSUMS = SKILL_ROOT / "dependencies-checksums.txt"
LOCK = SKILL_ROOT / "dependencies-requirements-lock.txt"
DEFAULT_VENV = SKILL_ROOT / ".ov-venv"
MIN_PYTHON = (3, 9)
VALIDATED_PLATFORM = ("Linux", "x86_64")


def log(msg: str):
    print(f"[ov-setup] {msg}", file=sys.stderr)


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_checksums() -> dict:
    if not CHECKSUMS.is_file():
        return {"ok": False, "error": f"missing {CHECKSUMS.name}", "files": []}
    files, ok, missing = [], True, []
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, name = line.split(None, 1)
        target = SKILL_ROOT / name.strip()
        entry = {"file": name.strip()}
        if not target.is_file():
            entry["status"] = "missing"
            missing.append(entry["file"])
        else:
            actual = sha256_file(target)
            entry["status"] = "ok" if actual == expected.lower() else "mismatch"
            entry["sha256"] = actual
            if entry["status"] == "mismatch":
                ok = False
        files.append(entry)
    model_missing = any(m == "asset-voice-lessac-medium.onnx" for m in missing)
    return {"ok": ok, "files": files, "missing": missing, "model_missing": model_missing,
            "note": ("voice model not present; acquire it with: python3 tools-ov-fetch-model.py --yes "
                     "(one-time 63 MB download) or --from-file (air-gapped)" if model_missing else None)}


def platform_note() -> str | None:
    system, machine = platform.system(), platform.machine()
    if (system, machine) != VALIDATED_PLATFORM:
        return (f"bundled wheels target {VALIDATED_PLATFORM[0]} {VALIDATED_PLATFORM[1]} "
                f"(CPython 3.13); this host is {system} {machine}. The install may fail; "
                "see knowledge-readme.md 'Other platforms' for deterministic re-download steps.")
    return None


def venv_python(venv_dir: Path) -> Path:
    for candidate in (venv_dir / "bin" / "python", venv_dir / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    return venv_dir / "bin" / "python"


def smoke_test(python: Path) -> dict:
    code = ("import json, piper, onnxruntime, numpy; "
            "print(json.dumps({'piper_file': piper.__file__ is not None, "
            "'onnxruntime': onnxruntime.__version__, 'numpy': numpy.__version__}))")
    proc = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-500:]}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        payload = {"raw": proc.stdout.strip()[-200:]}
    payload["ok"] = True
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline installer for the Offline Neural Voice Skill Tool (no network)")
    parser.add_argument("--venv-dir", default=str(DEFAULT_VENV), help="venv location (default: <skill-root>/.ov-venv)")
    parser.add_argument("--check-only", action="store_true", help="verify bundles and report; do not create/install anything")
    parser.add_argument("--force", action="store_true", help="recreate the venv from scratch")
    parser.add_argument("--report", help="write the install report JSON to this path")
    parser.add_argument("--with-model", action="store_true",
                        help="also acquire the 63 MB voice model now (one-time network download, pinned + sha256-verified)")
    args = parser.parse_args()

    report = {"skill_root": str(SKILL_ROOT), "python": sys.version.split()[0],
              "platform": f"{platform.system()} {platform.machine()}", "steps": []}

    if sys.version_info < MIN_PYTHON:
        log(f"FATAL: python >= {'.'.join(map(str, MIN_PYTHON))} required; found {sys.version.split()[0]}")
        return 2

    note = platform_note()
    if note:
        log("WARNING: " + note)
        report["platform_warning"] = note

    log("verifying bundled wheel + model checksums...")
    verification = verify_checksums()
    report["steps"].append({"step": "checksum-verify", "ok": verification["ok"]})
    if not verification["ok"]:
        report["verification"] = verification
        log("FATAL: checksum verification failed; refusing to install tampered files.")
        for f in verification["files"]:
            if f["status"] != "ok":
                log(f"  {f['status']}: {f['file']}")
        _write_report(args.report, report)
        return 7
    log(f"checksums OK ({len(verification['files'])} files)")

    if args.check_only:
        report["verification"] = verification
        report["ready"] = not verification.get("model_missing")
        if verification.get("note"):
            log(verification["note"])
        log("check-only: bundles verified; nothing was installed.")
        _write_report(args.report, report)
        print(json.dumps(report, indent=2))
        return 0

    venv_dir = Path(args.venv_dir).expanduser()
    python = venv_python(venv_dir)

    if venv_dir.exists() and args.force:
        log(f"removing existing venv at {venv_dir} (--force)")
        shutil.rmtree(venv_dir)

    if python.is_file():
        smoke = smoke_test(python)
        if smoke.get("ok"):
            log(f"existing venv healthy at {venv_dir}; nothing to do (use --force to rebuild)")
            report["steps"].append({"step": "venv-reuse", "ok": True, "smoke": smoke})
            report["venv"] = str(venv_dir)
            model_ok = (SKILL_ROOT / "asset-voice-lessac-medium.onnx").is_file()
            report["model"] = "present" if model_ok else "missing"
            report["ready"] = model_ok
            if not model_ok:
                log("NOTE: voice model missing; run: python3 tools-ov-fetch-model.py --yes")
                report["model_note"] = "run tools-ov-fetch-model.py --yes (or --from-file for air-gapped install)"
            _write_report(args.report, report)
            print(json.dumps(report, indent=2))
            return 0 if model_ok else 8
        log("existing venv failed the smoke test; recreating")
        shutil.rmtree(venv_dir)

    log(f"creating venv at {venv_dir} ...")
    try:
        venv.EnvBuilder(with_pip=True, clear=True, symlinks=True).create(venv_dir)
    except Exception as exc:
        report["steps"].append({"step": "venv-create", "ok": False, "error": str(exc)})
        _write_report(args.report, report)
        log(f"FATAL: venv creation failed: {exc}")
        return 5
    python = venv_python(venv_dir)

    # Bundled wheels are stored with a dependencies- prefix; pip needs canonical
    # wheel filenames, so stage them under their canonical names in a temp dir.
    import tempfile
    log("staging wheels under canonical filenames and installing offline ...")
    staging = Path(tempfile.mkdtemp(prefix="ov-wheels-"))
    staged = 0
    try:
        for wheel in SKILL_ROOT.glob("dependencies-*.whl"):
            target = staging / wheel.name[len("dependencies-"):]
            shutil.copy2(wheel, target)
            staged += 1
        cmd = [str(python), "-m", "pip", "install", "--no-index",
               "--find-links", str(staging), "--require-hashes", "-r", str(LOCK)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    report["steps"].append({"step": "wheel-install", "ok": proc.returncode == 0,
                            "staged_wheels": staged,
                            "tail": (proc.stdout + proc.stderr).strip()[-800:]})
    if proc.returncode != 0:
        _write_report(args.report, report)
        log("FATAL: wheel install failed. See report tail. No network was used.")
        return 7

    smoke = smoke_test(python)
    report["steps"].append({"step": "smoke-test", "ok": smoke.get("ok", False), "smoke": smoke})
    if args.with_model and not (SKILL_ROOT / "asset-voice-lessac-medium.onnx").is_file():
        log("acquiring voice model (--with-model) ...")
        fetch = SKILL_ROOT / "tools-ov-fetch-model.py"
        proc2 = subprocess.run([sys.executable, str(fetch), "--yes"], timeout=1800)
        report["steps"].append({"step": "model-fetch", "ok": proc2.returncode == 0})
        if proc2.returncode != 0:
            _write_report(args.report, report)
            log("FATAL: model acquisition failed; see fetch output above.")
            return 8

    report["venv"] = str(venv_dir)
    model_ok = (SKILL_ROOT / "asset-voice-lessac-medium.onnx").is_file()
    report["model"] = "present" if model_ok else "missing"
    report["ready"] = bool(smoke.get("ok")) and model_ok
    _write_report(args.report, report)
    if not smoke.get("ok"):
        log(f"FATAL: installed but import smoke test failed: {smoke.get('error')}")
        return 7
    if not model_ok:
        log("OFFLINE INSTALL COMPLETE for the engine, but the voice model is not in the bundle.")
        log("One-time model acquisition (63 MB): python3 tools-ov-fetch-model.py --yes")
        log("Air-gapped alternative:        python3 tools-ov-fetch-model.py --from-file /path/to/en_US-lessac-medium.onnx --yes")
        print(json.dumps(report, indent=2))
        return 8
    log("offline install complete. Try: python3 tools-offline-voice.py synthesize --text 'Greetings, friends! Welcome to my video.' --output greeting.wav")
    print(json.dumps(report, indent=2))
    return 0


def _write_report(path, report):
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        log(f"WARNING: could not write report to {path}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
