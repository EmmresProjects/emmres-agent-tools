"""Built-in end-to-end regression suite.

The suite drives the *real* command line interface as a subprocess against a
throw-away workspace, so every assertion exercises the same entry point an
operator uses. Fixtures are generated locally with ffmpeg ``lavfi`` sources;
no network access and no model weights are needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


class Case:
    def __init__(self, name, expect_codes, summary):
        self.name = name
        self.expect_codes = expect_codes if isinstance(expect_codes, (list, tuple)) else [expect_codes]
        self.summary = summary
        self.code = None
        self.stdout = ""
        self.stderr = ""
        self.detail = ""
        self.payload = None
        self.passed = False

    def record(self):
        entry = {
            "name": self.name,
            "summary": self.summary,
            "expected_exit": self.expect_codes,
            "exit_code": self.code,
            "passed": self.passed,
            "detail": self.detail,
        }
        return entry


class Harness:
    def __init__(self, ctx, root, timeout_s):
        self.ctx = ctx
        self.root = Path(root)
        self.timeout_s = timeout_s
        self.started = time.time()
        self.tool = str(Path(__file__).resolve().parent / "tools-onv.py")
        self.cases = []

    def run_cli(self, args, expect_codes, name, summary=""):
        case = Case(name, expect_codes, summary)
        if time.time() - self.started > self.timeout_s:
            case.detail = "skipped: overall selftest timeout reached"
            self.cases.append(case)
            return case
        argv = [os.environ.get("PYTHON", "python3"), self.tool] + [str(a) for a in args] + ["--json", "--workdir", str(self.root)]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=max(30, min(300, self.timeout_s)),
                cwd=str(self.root),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired:
            case.code = "timeout"
            case.detail = "command exceeded the subprocess timeout"
            self.cases.append(case)
            return case
        case.code = proc.returncode
        case.stdout = proc.stdout
        case.stderr = proc.stderr
        try:
            case.payload = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
        except ValueError:
            case.payload = None
        case.passed = proc.returncode in case.expect_codes
        if not case.passed:
            case.detail = "exit %s (expected %s); stderr=%s" % (
                proc.returncode,
                case.expect_codes,
                (proc.stderr or proc.stdout).strip()[:600],
            )
        self.cases.append(case)
        return case

    def fixture_video(self, name="fixture.mp4", duration=3, size="320x180", fps=24, audio=True):
        path = self.root / name
        argv = [
            self.ctx.config.get("ffmpeg", "ffmpeg"), "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=%s:rate=%s:duration=%ss" % (size, fps, duration),
        ]
        if audio:
            argv += ["-f", "lavfi", "-i", "sine=frequency=440:duration=%ss" % duration, "-c:a", "aac", "-b:a", "96k"]
        argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-t", str(duration), str(path)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError("fixture generation failed: %s" % proc.stderr[-800:])
        return path

    def fixture_image(self, name="reference.png", size="640x360"):
        path = self.root / name
        argv = [
            self.ctx.config.get("ffmpeg", "ffmpeg"), "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "gradients=size=%s:duration=1:rate=1" % size,
            "-frames:v", "1", str(path),
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError("image fixture generation failed: %s" % proc.stderr[-800:])
        return path

    def fixture_audio(self, name="voice.wav", duration=3):
        path = self.root / name
        argv = [
            self.ctx.config.get("ffmpeg", "ffmpeg"), "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=%ss" % duration, str(path),
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError("audio fixture generation failed: %s" % proc.stderr[-800:])
        return path

    def fixture_srt(self, name="subs.srt"):
        path = self.root / name
        path.write_text(
            "1\n00:00:00,200 --> 00:00:01,200\nfirst selftest caption\n\n"
            "2\n00:00:01,300 --> 00:00:02,500\nsecond selftest caption\n\n",
            encoding="utf-8",
        )
        return path

    def write_spec(self, name, spec):
        path = self.root / name
        path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return path

    def expect(self, case, condition, message):
        if not condition:
            case.passed = False
            case.detail = (case.detail + " | " if case.detail else "") + message
        return case.passed


def base_spec(job_id, **overrides):
    spec = {
        "spec_version": "1.0",
        "job_id": job_id,
        "capability": "offline-image-to-video-engine",
        "mode": "proxy",
        "preset": "draft",
        "prompt": "selftest animatic",
        "inputs": [{"role": "image", "path": "reference.png"}],
        "params": {"width": 320, "height": 180, "fps": 12, "duration_s": 2.0, "seed": 7},
        "output": {"dir": "outputs"},
        "delivery": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac", "package": True},
        "rights": {"require_consent": False, "allowed_use": "internal-preview", "disclosure_required": True},
        "limits": {"timeout_s": 900, "max_frames": 240, "max_output_mb": 256},
        "fallback": {"on_backend_unavailable": "fail"},
    }
    spec.update(overrides)
    return spec


def run_selftest(ctx, keep=False, timeout_s=600):
    common = ctx.common
    root = Path(tempfile.mkdtemp(prefix="onv-selftest-", dir=str(ctx.workdir)))
    harness = Harness(ctx, root, timeout_s)
    started = time.time()
    notes = []
    try:
        harness.fixture_image()
        harness.fixture_video()
        harness.fixture_audio()
        harness.fixture_srt()

        # 1. help / capability listing -----------------------------------
        case = harness.run_cli(["help"], 0, "help", "help exits 0")
        harness.expect(case, "capabilities" in case.stdout and "selftest" in case.stdout, "help text lists the command set")

        case = harness.run_cli(["capabilities"], 0, "capabilities", "catalog summary")
        if case.payload:
            result = case.payload.get("result", {})
            harness.expect(case, result.get("category_count") == 28, "expected 28 categories, got %s" % result.get("category_count"))
            harness.expect(case, result.get("concept_count") == 1235, "expected 1235 concepts, got %s" % result.get("concept_count"))

        case = harness.run_cli(["capabilities", "--category", "1"], 0, "capabilities-category", "category drill-down")
        if case.payload:
            harness.expect(case, case.payload["result"]["concept_count"] == 40, "category 1 should hold 40 concepts")

        case = harness.run_cli(["capabilities", "--search", "lip sync"], 0, "capabilities-search", "free-text search")
        if case.payload:
            harness.expect(case, case.payload["result"]["match_count"] >= 1, "search should return at least one concept")

        case = harness.run_cli(["capabilities", "--op", "interpolate"], 0, "capabilities-op", "operation filter")
        if case.payload:
            harness.expect(case, case.payload["result"]["concept_count"] >= 1, "interpolate op should have concepts")

        case = harness.run_cli(["describe", "offline-image-to-video-engine"], 0, "describe", "describe resolves routing")
        if case.payload:
            routing = case.payload["result"]["routing"]
            harness.expect(case, routing.get("model_id") is not None, "routing should pick a model")
            harness.expect(case, bool(case.payload["result"]["proxy"]), "proxy behaviour should be described")

        case = harness.run_cli(["describe", "no-such-capability"], 2, "describe-unknown", "unknown capability -> exit 2")
        if case.payload:
            harness.expect(case, case.payload["error"]["code"] == "E_SPEC", "unknown capability must be E_SPEC")

        case = harness.run_cli(["doctor"], 0, "doctor", "environment report")
        if case.payload:
            ffmpeg = case.payload["result"]["ffmpeg"]
            harness.expect(case, bool(ffmpeg.get("path")), "ffmpeg should be detected")
            harness.expect(case, bool(ffmpeg["filters"].get("minterpolate")), "minterpolate filter should exist")

        # 2. spec-init + plan --------------------------------------------
        case = harness.run_cli(
            ["spec-init", "--out", "spec-template.json", "--job-id", "selftest-init", "--capability", "offline-image-to-video-engine"],
            0,
            "spec-init",
            "spec template written",
        )
        harness.expect(case, (root / "spec-template.json").exists(), "spec-init must write the file")

        spec_a = harness.write_spec("spec-a.json", base_spec("selftest-a"))
        case = harness.run_cli(["plan", "--spec", "spec-a.json"], 0, "plan", "plan built for proxy job")
        plan_a = None
        if case.payload:
            result = case.payload["result"]
            plan_a = result
            harness.expect(case, result["operation"] == "generate", "operation should resolve to generate")
            harness.expect(case, [s["id"] for s in result["stages"]].count("neural-generate") == 1, "one neural-generate stage expected")
            harness.expect(case, any(s["executor"] == "ffmpeg-proxy" for s in result["stages"]), "proxy executor expected in mode=proxy")
            harness.expect(case, (root / ".onv-work/selftest-a/plan.json").exists(), "plan file should be written")

        # invalid spec: bad capability
        spec_bad = harness.write_spec("spec-bad.json", base_spec("selftest-bad", capability="not-a-real-capability"))
        case = harness.run_cli(["plan", "--spec", "spec-bad.json"], 2, "plan-invalid-capability", "invalid capability -> exit 2")
        if case.payload:
            harness.expect(case, case.payload["error"]["code"] == "E_SPEC", "invalid capability must be E_SPEC")

        # invalid spec: missing job_id
        missing = harness.write_spec("spec-missing.json", {"capability": "offline-image-to-video-engine"})
        case = harness.run_cli(["plan", "--spec", "spec-missing.json"], 2, "plan-missing-field", "missing job_id -> exit 2")
        if case.payload:
            harness.expect(case, "job_id" in case.payload["error"]["message"], "error should name the missing field")

        # missing spec file
        case = harness.run_cli(["plan", "--spec", "nope.json"], 2, "plan-missing-spec", "missing spec file -> exit 2")
        if case.payload:
            harness.expect(case, case.payload["error"]["code"] == "E_NOT_FOUND", "missing spec must be E_NOT_FOUND")

        # 3. preflight ----------------------------------------------------
        case = harness.run_cli(["preflight", "--spec", "spec-a.json"], 0, "preflight-ok", "preflight passes for good spec")
        if case.payload:
            harness.expect(case, case.payload["result"]["ready"] is True, "preflight should be ready")

        spec_missing_input = harness.write_spec("spec-missing-input.json", base_spec("selftest-missing-input", inputs=[{"role": "image", "path": "absent.png"}]))
        case = harness.run_cli(["preflight", "--spec", "spec-missing-input.json"], 4, "preflight-missing-input", "missing input blocks at preflight")
        if case.payload:
            harness.expect(case, case.payload["result"]["ready"] is False, "preflight must report not ready")

        # 4. ledger + consent --------------------------------------------
        ledger = "ledger/rights-ledger.json"
        case = harness.run_cli(
            ["ledger", "add", "--file", ledger, "--subject", "actor-001", "--status", "granted",
             "--scope", "internal-preview", "--evidence", "consent-2026-05-01.pdf", "--granted-by", "producer"],
            4,
            "ledger-add-needs-approval",
            "ledger write without --approve is refused",
        )
        if case.payload:
            harness.expect(case, case.payload["error"]["code"] == "E_APPROVAL", "ledger write must require approval")

        case = harness.run_cli(
            ["ledger", "add", "--file", ledger, "--subject", "actor-001", "--status", "granted",
             "--scope", "internal-preview", "--evidence", "consent-2026-05-01.pdf", "--granted-by", "producer", "--approve"],
            0,
            "ledger-add",
            "ledger record written with approval",
        )
        case = harness.run_cli(["ledger", "check", "--file", ledger, "--subject", "actor-001", "--scope", "internal-preview"], 0, "ledger-check", "consent check passes")
        case = harness.run_cli(["ledger", "list", "--file", ledger], 0, "ledger-list", "ledger listing")

        case = harness.run_cli(
            ["ledger", "add", "--file", ledger, "--subject", "actor-002", "--status", "denied",
             "--scope", "internal-preview", "--evidence", "withdrawal-2026-06-02.pdf", "--approve"],
            0,
            "ledger-add-denied",
            "denied consent recorded",
        )
        case = harness.run_cli(["ledger", "check", "--file", ledger, "--subject", "actor-002", "--scope", "internal-preview"], 4, "ledger-check-denied", "denied consent blocks")

        spec_consent = harness.write_spec(
            "spec-consent.json",
            base_spec(
                "selftest-consent",
                rights={"ledger": ledger, "require_consent": True, "subject_ids": ["actor-002"],
                        "allowed_use": "internal-preview", "disclosure_required": True},
            ),
        )
        case = harness.run_cli(["run", "--spec", "spec-consent.json", "--approve"], 4, "run-consent-blocked", "consent gate blocks the run")
        if case.payload:
            harness.expect(
                case,
                any(r.get("stage") == "governance" and r.get("status") == "failed" for r in case.payload["result"]["stages"]),
                "governance stage should fail for a denied subject",
            )

        # 5. end-to-end proxy run ----------------------------------------
        case = harness.run_cli(["run", "--spec", "spec-a.json", "--approve"], 0, "run-proxy", "end-to-end proxy run")
        output_a = root / "outputs" / "selftest-a.mp4"
        harness.expect(case, output_a.exists(), "delivery file should exist at %s" % output_a)
        archive_a = root / "packages" / "selftest-a-delivery.zip"
        harness.expect(case, archive_a.exists(), "delivery package should exist at %s" % archive_a)
        if case.payload:
            result = case.payload["result"]
            harness.expect(case, result["status"] == "succeeded", "run status should be succeeded, got %s" % result["status"])
            harness.expect(case, result["validation_passed"] is True, "validation should pass")
            harness.expect(case, result["proxy_path_used"] is True, "proxy path should be recorded")

        case = harness.run_cli(["validate", "--file", "outputs/selftest-a.mp4"], 0, "validate-output", "validate the delivered file")
        if case.payload:
            checks = {c["id"]: c for c in case.payload["result"]["checks"]}
            harness.expect(case, checks["decode"]["status"] == "pass", "decode check should pass")
            harness.expect(case, checks["resolution"]["status"] == "pass", "resolution check should pass")

        case = harness.run_cli(["archive-verify", "--archive", "packages/selftest-a-delivery.zip"], 0, "archive-verify", "delivery archive is valid")
        if case.payload:
            names = [f["name"] for f in case.payload["result"]["files"]]
            harness.expect(case, "provenance.json" in names, "archive should contain provenance.json")
            harness.expect(case, "disclosure.md" in names, "archive should contain disclosure.md")

        # 6. resume -------------------------------------------------------
        case = harness.run_cli(["run", "--spec", "spec-a.json", "--approve", "--resume"], 0, "run-resume", "resume reuses previous stages")
        if case.payload:
            stages = case.payload["result"]["stages"]
            skipped = [s for s in stages if s.get("status") == "skipped"]
            harness.expect(case, len(skipped) >= 3, "resume should skip completed stages, skipped=%d" % len(skipped))

        # 7. backend approval + proxy recovery ----------------------------
        spec_backend_doc = base_spec(
            "selftest-backend",
            mode="auto",
            backend={"command": "python3 -m onv_missing_runner --out {output}", "timeout_s": 120, "retries": 0},
        )
        harness.write_spec("spec-backend.json", spec_backend_doc)
        case = harness.run_cli(["run", "--spec", "spec-backend.json"], 4, "run-backend-approval", "backend dispatch requires --approve")
        if case.payload:
            harness.expect(
                case,
                any(r.get("error", {}).get("code") == "E_APPROVAL" for r in case.payload["result"]["stages"]),
                "expected E_APPROVAL for the backend stage",
            )

        spec_backend_doc["fallback"] = {"on_backend_unavailable": "proxy"}
        harness.write_spec("spec-backend-fallback.json", spec_backend_doc)
        case = harness.run_cli(["run", "--spec", "spec-backend-fallback.json", "--approve", "--overwrite"], 0, "run-backend-fallback", "proxy recovery after backend failure")
        if case.payload:
            result = case.payload["result"]
            harness.expect(case, result["status"] == "succeeded", "fallback run should succeed, got %s" % result["status"])
            neural = [r for r in result["stages"] if r.get("stage") == "neural-generate"]
            harness.expect(case, bool(neural) and neural[0].get("facts", {}).get("fallback") == "proxy", "neural stage should record the proxy fallback")

        # 8. input changes the result -------------------------------------
        spec_long = dict(base_spec("selftest-long"))
        spec_long["params"] = {"width": 320, "height": 180, "fps": 12, "duration_s": 4.0, "seed": 7}
        harness.write_spec("spec-long.json", spec_long)
        case = harness.run_cli(["run", "--spec", "spec-long.json", "--approve"], 0, "run-variant", "different duration changes the output")
        if case.payload:
            validation_report = root / ".onv-work" / "selftest-long" / "reports" / "validation-selftest-long.json"
            harness.expect(case, validation_report.exists(), "variant run should produce its own validation report")
            if validation_report.exists():
                doc = json.loads(validation_report.read_text(encoding="utf-8"))
                duration = {c["id"]: c for c in doc["checks"]}.get("duration", {})
                measured = duration.get("measured", {}).get("duration_s")
                harness.expect(case, measured is not None and measured > 3.0, "variant output should be longer than 3s, got %s" % measured)

        # 9. validation failure path --------------------------------------
        broken = root / "broken.mp4"
        broken.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"garbage" * 64)
        case = harness.run_cli(["validate", "--file", "broken.mp4"], [2, 3], "validate-broken", "corrupt file is rejected")

        # 10. dry run -----------------------------------------------------
        case = harness.run_cli(["run", "--spec", "spec-a.json", "--dry-run"], 0, "run-dry-run", "dry run lists stages only")

        # 11. validate-only mode ------------------------------------------
        spec_vo = harness.write_spec("spec-validate-only.json", base_spec("selftest-vo", mode="validate-only"))
        case = harness.run_cli(["run", "--spec", "spec-validate-only.json", "--approve", "--validate-only"], [0, 3, 4], "run-validate-only", "validate-only mode is reachable")

        # 12. local operation families --------------------------------
        audio_spec = base_spec(
            "selftest-audio",
            capability="offline-mix-audio-py",
            inputs=[{"role": "video", "path": "fixture.mp4"}, {"role": "audio", "path": "voice.wav"}],
            audio={"mode": "normalize", "target_lufs": -16.0},
            params={"width": 320, "height": 180, "fps": 24, "duration_s": 3.0},
        )
        harness.write_spec("spec-audio.json", audio_spec)
        case = harness.run_cli(["run", "--spec", "spec-audio.json", "--approve"], 0, "run-audio", "audio loudness normalisation")
        if case.payload:
            stages = {r["stage"]: r for r in case.payload["result"]["stages"]}
            harness.expect(case, stages.get("audio", {}).get("status") == "succeeded", "audio stage should succeed")
            report_path = Path(case.payload["result"]["validation_report"]) if case.payload["result"].get("validation_report") else None
        loudness_report = root / ".onv-work" / "selftest-audio" / "reports" / "validation-selftest-audio.json"
        if loudness_report.exists():
            doc = json.loads(loudness_report.read_text(encoding="utf-8"))
            loudness = {c["id"]: c for c in doc["checks"]}.get("loudness", {})
            harness.expect(case, loudness.get("status") == "pass", "loudness check should pass, got %s" % loudness.get("status"))

        caption_spec = base_spec(
            "selftest-caption",
            capability="subtitle-generation",
            inputs=[{"role": "video", "path": "fixture.mp4"}],
            captions={"srt": "subs.srt", "mode": "burn"},
            params={"width": 320, "height": 180, "fps": 24, "duration_s": 3.0},
        )
        harness.write_spec("spec-caption.json", caption_spec)
        case = harness.run_cli(["run", "--spec", "spec-caption.json", "--approve"], 0, "run-caption", "caption burn-in")

        compose_spec = base_spec(
            "selftest-compose",
            capability="automated-scene-selection",
            inputs=[{"role": "video", "path": "fixture.mp4"}],
            compose={"clips": ["fixture.mp4", "fixture.mp4"], "mode": "concat"},
            params={"width": 320, "height": 180, "fps": 24, "duration_s": 3.0},
        )
        harness.write_spec("spec-compose.json", compose_spec)
        case = harness.run_cli(["run", "--spec", "spec-compose.json", "--approve"], 0, "run-compose", "two-clip programme assembly")
        if case.payload:
            stages = {r["stage"]: r for r in case.payload["result"]["stages"]}
            harness.expect(case, stages.get("compose", {}).get("status") == "succeeded", "compose stage should succeed")

        interp_spec = base_spec(
            "selftest-interp",
            capability="rife-frame-interpolation",
            inputs=[{"role": "video", "path": "fixture.mp4"}],
            params={"width": 320, "height": 180, "fps": 48, "duration_s": 3.0},
        )
        harness.write_spec("spec-interp.json", interp_spec)
        case = harness.run_cli(["run", "--spec", "spec-interp.json", "--approve"], 0, "run-interpolate", "minterpolate retime to 48 fps")
        if case.payload:
            fps_check = None
            report = root / ".onv-work" / "selftest-interp" / "reports" / "validation-selftest-interp.json"
            if report.exists():
                doc = json.loads(report.read_text(encoding="utf-8"))
                fps_check = {c["id"]: c for c in doc["checks"]}.get("fps", {})
            harness.expect(case, fps_check and fps_check.get("status") == "pass", "48 fps retime should validate")

        restore_spec = base_spec(
            "selftest-restore",
            capability="offline-video-denoising-model",
            inputs=[{"role": "video", "path": "fixture.mp4"}],
            params={"width": 640, "height": 360, "fps": 24, "duration_s": 3.0},
        )
        harness.write_spec("spec-restore.json", restore_spec)
        case = harness.run_cli(["run", "--spec", "spec-restore.json", "--approve"], 0, "run-restore", "denoise/deflicker/upscale proxy")
        if case.payload:
            stages = {r["stage"]: r for r in case.payload["result"]["stages"]}
            harness.expect(case, stages.get("neural-restore", {}).get("status") == "succeeded", "restore stage should succeed")

        # 13. policy failure and overwrite protection -------------------
        case = harness.run_cli(["validate", "--file", "outputs/selftest-interp.mp4", "--spec", "spec-restore.json"], 3, "validate-policy-failure", "resolution mismatch fails the policy")
        case = harness.run_cli(["run", "--spec", "spec-a.json", "--approve"], 4, "run-overwrite-guard", "existing output is protected without --overwrite")
        case = harness.run_cli(["run", "--spec", "spec-a.json", "--approve", "--overwrite"], 0, "run-overwrite-approved", "approved overwrite succeeds")

        # 14. CLI surface ------------------------------------------------
        case = harness.run_cli(["plan", "--spec", "spec-a.json", "--show-command"], 0, "plan-show-command", "plan renders the backend command")
        config_path = root / "onv-config.json"
        config_path.write_text(json.dumps({"paths": {"work_root": ".custom-work", "package_root": "deliveries"}}), encoding="utf-8")
        case = harness.run_cli(["--config", "onv-config.json", "plan", "--spec", "spec-a.json"], 0, "config-file", "config file relocates runtime paths")
        harness.expect(case, (root / ".custom-work" / "selftest-a" / "plan.json").exists(), "custom work root should be honoured")

        passed = [c for c in harness.cases if c.passed]
        failed = [c for c in harness.cases if not c.passed]
        report = {
            "suite": "offline-neural-video-pipeline selftest",
            "tool_version": common.TOOL_VERSION,
            "started_at": common.utcnow(),
            "duration_s": round(time.time() - started, 1),
            "workspace": str(root),
            "total": len(harness.cases),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "passed": not failed,
            "cases": [c.record() for c in harness.cases],
            "notes": notes,
        }
        return report
    finally:
        if keep:
            print("selftest workspace kept at: %s" % root)
        else:
            shutil.rmtree(str(root), ignore_errors=True)


def selftest_lines(report):
    lines = [
        "Selftest: %d/%d cases passed in %ss" % (report["passed_count"], report["total"], report["duration_s"]),
        "-" * 78,
    ]
    for case in report["cases"]:
        mark = "PASS" if case["passed"] else "FAIL"
        lines.append("  [%s] %-26s exit=%s" % (mark, case["name"], case["exit_code"]))
        if not case["passed"] and case.get("detail"):
            lines.append("         %s" % case["detail"])
    lines.append("-" * 78)
    lines.append("Result: %s" % ("ALL CASES PASSED" if report["passed"] else "%d CASE(S) FAILED" % report["failed_count"]))
    return lines
