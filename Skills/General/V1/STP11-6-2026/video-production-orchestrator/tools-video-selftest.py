#!/usr/bin/env python3
"""Validation suite for the video-production-orchestrator Skill Tool.

Module role: prove - on the machine that will run the tool - that every command
works through the real CLI interface, that valid/invalid/missing input behave as
documented, that recovery paths work, and that real media operations produce
openable outputs. Run with `tools-video-orchestrator.py selftest`.

Tests never fabricate a result: anything that needs FFmpeg is skipped and
reported as skipped when FFmpeg is unavailable.
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
from importlib.machinery import SourceFileLoader

_HERE = Path(__file__).resolve().parent
core = SourceFileLoader("video_core", str(_HERE / "tools-video-core.py")).load_module()
media = SourceFileLoader("video_media", str(_HERE / "tools-video-media.py")).load_module()

CLI = _HERE / "tools-video-orchestrator.py"
FIXTURE = _HERE / "asset-fixture-qa-sample.mp4"
SAMPLE_CAPTIONS = "\n".join([
    "1", "00:00:00,000 --> 00:00:02,400", "This is a validation fixture.", "",
    "2", "00:00:02,400 --> 00:00:04,900", "It exercises caption retiming.", "",
    "3", "00:00:04,900 --> 00:00:06,000", "End of sample.", "",
])


class Suite:
    def __init__(self, workdir=None, keep=False, timeout=600.0):
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="vpo-selftest-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self.timeout = timeout
        self.results = []
        self.ffmpeg = media.ffmpeg_bin()

    # ------------------------------------------------------------- harness
    def run_cli(self, args, stdin_text=None, expect=0):
        cmd = [sys.executable, str(CLI)] + args
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                              input=stdin_text, cwd=str(self.workdir))
        return proc

    def record(self, name, ok, detail="", skipped=False, duration=None):
        self.results.append({"test": name, "status": "skip" if skipped else ("pass" if ok else "fail"),
                             "detail": detail, "duration_seconds": round(duration, 2) if duration else None})
        mark = "SKIP" if skipped else ("PASS" if ok else "FAIL")
        print(f"[{mark}] {name}{(' - ' + detail) if detail else ''}", flush=True)

    def check(self, name, fn):
        started = time.time()
        try:
            ok, detail = fn()
            self.record(name, ok, detail, duration=time.time() - started)
            return ok
        except AssertionError as exc:
            self.record(name, False, str(exc), duration=time.time() - started)
            return False
        except Exception as exc:  # noqa: BLE001
            self.record(name, False, f"{type(exc).__name__}: {exc}", duration=time.time() - started)
            return False

    def check_media(self, name, fn):
        if not self.ffmpeg:
            self.record(name, True, "FFmpeg unavailable - media test skipped", skipped=True)
            return True
        return self.check(name, fn)

    # --------------------------------------------------------------- tests
    def t_help(self):
        proc = self.run_cli(["help"])
        assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[:200]}"
        assert "capabilities" in proc.stdout and "selftest" in proc.stdout, "help text incomplete"
        return True, "help lists all commands"

    def t_capabilities(self):
        proc = self.run_cli(["capabilities"])
        assert proc.returncode == 0, proc.stderr[:300]
        payload = json.loads(proc.stdout)
        assert payload["catalog"]["categories"] == 25, payload["catalog"]
        assert payload["catalog"]["total_catalog_skills"] == 250, payload["catalog"]
        assert len(payload["capabilities"]) == 250, len(payload["capabilities"])
        assert all(c["command"] for c in payload["capabilities"]), "capability without a command"
        return True, "25 categories / 250 capabilities reachable"

    def t_capabilities_filter(self):
        proc = self.run_cli(["capabilities", "--category", "C15", "--format", "md"])
        assert proc.returncode == 0, proc.stderr[:200]
        assert "Final Mix and Loudness Engineer" in proc.stdout
        assert "Video Creative Director" not in proc.stdout
        proc2 = self.run_cli(["capabilities", "--category", "C15", "--phase", "1"])
        assert json.loads(proc2.stdout)["catalog"]["capabilities"] == 0, "phase filter ignored"
        return True, "category and phase filters change the result"

    def t_parse_text(self):
        text = "Motion Retiming\n- Speed Ramping Specialist - Smooths acceleration around action beats.\n- Reverse and Loop Designer: Builds seamless reversals."
        proc = self.run_cli(["parse"], stdin_text=text)
        assert proc.returncode == 0, proc.stderr[:300]
        payload = json.loads(proc.stdout)
        assert payload["skill_count"] == 2, payload
        names = [s["name"] for s in payload["skills"]]
        assert names == ["Speed Ramping Specialist", "Reverse and Loop Designer"], names
        return True, f"umbrella={payload['umbrella_name']}, 2 capabilities"

    def t_parse_html(self):
        html = ('<div class="card" data-id="7"><h3>Audio Editing, Music, SFX, Foley, and Mixing</h3>'
                '<ul><li><span class="n">Dialogue Editor</span> <em>Cleans and levels spoken audio.</em></li></ul></div>')
        proc = self.run_cli(["parse", "--request", html])
        assert proc.returncode == 0, proc.stderr[:300]
        payload = json.loads(proc.stdout)
        assert payload["source_format"] == "html", payload["source_format"]
        skill = payload["skills"][0]
        assert skill["name"] == "Dialogue Editor", skill
        assert "Cleans and levels" in skill["description"], skill
        assert payload["categories"][0]["name"].startswith("Audio Editing")
        return True, "tags, classes and attributes stripped"

    def t_parse_numbered_markdown(self):
        md = ("## Category: Graphics\n\n"
              "1. **Skill Name:** Title Sequence Designer\n   **Description:** Creates opening title systems.\n\n"
              "2. **Skill Name:** Lower Thirds Designer\n   **Description:** Builds reusable name straps.")
        proc = self.run_cli(["parse", "--request", md, "--format", "md"])
        assert proc.returncode == 0, proc.stderr[:300]
        assert "Title Sequence Designer" in proc.stdout and "Lower Thirds Designer" in proc.stdout
        assert "**1.**" not in proc.stdout, "numbering marks leaked into names"
        return True, "markdown labels and numbering removed"

    def t_search(self):
        proc = self.run_cli(["search", "colour grade LUT"])
        assert proc.returncode == 0, proc.stderr[:200]
        payload = json.loads(proc.stdout)
        assert payload["match_count"] >= 1
        ids = [r["id"] for r in payload["results"]]
        assert any(i.startswith("C14") for i in ids), ids
        return True, f"{payload['match_count']} matches, colour family first"

    def t_plan(self):
        out = self.workdir / "plan.json"
        proc = self.run_cli(["plan", "--request",
                             "Deliver a 45 second vertical explainer with captions, colour grade and a final export",
                             "--audience", "mobile viewers", "--platform", "reels-vertical",
                             "--duration", "45", "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:400]
        plan = json.loads(out.read_text())
        schema = core.read_json(_HERE / "asset-production-plan.schema.json")
        errors = core.validate_schema(plan, schema)
        assert not errors, errors[:3]
        assert plan["duration_seconds"] == 45
        assert len(plan["capabilities"]) > 5
        return True, f"{len(plan['capabilities'])} capabilities, schema-valid, phases {sorted({c['phase'] for c in plan['capabilities']})}"

    def t_plan_invalid(self):
        proc = self.run_cli(["plan", "--request", "x", "--duration", "banana"])
        assert proc.returncode == core.EXIT_USAGE, f"expected exit {core.EXIT_USAGE}, got {proc.returncode}"
        assert "duration" in proc.stderr.lower(), proc.stderr[:200]
        proc2 = self.run_cli(["plan", "--request", "grade the footage",
                              "--capability", "C99-E99"])
        assert proc2.returncode == core.EXIT_NOT_FOUND, proc2.returncode
        assert "not found" in proc2.stderr.lower(), proc2.stderr[:200]
        proc3 = self.run_cli(["plan", "--request", "grade the footage",
                              "--deliverable", "banana:masters/x.mp4"])
        assert proc3.returncode == core.EXIT_USAGE, proc3.returncode
        assert "deliverable kind" in proc3.stderr.lower(), proc3.stderr[:200]
        return True, "bad duration (2), unknown capability (3) and bad deliverable kind (2) all rejected"

    def t_plan_missing_input(self):
        proc = self.run_cli(["plan"], stdin_text="")
        assert proc.returncode == core.EXIT_USAGE, proc.returncode
        assert "no request supplied" in proc.stderr
        return True, "missing request reported as usage error"

    def t_coverage_and_brief(self):
        plan_path = self.workdir / "plan.json"
        proc = self.run_cli(["coverage", "--plan", str(plan_path)])
        assert proc.returncode == 0, proc.stderr[:300]
        report = json.loads(proc.stdout)
        assert report["requirements"] and report["status"] in ("pass", "warn", "fail")
        cap_id = report["requirements"][0]["capabilities"][0]
        proc2 = self.run_cli(["brief", "--plan", str(plan_path), "--capability", cap_id])
        assert proc2.returncode == 0, proc2.stderr[:300]
        brief = json.loads(proc2.stdout)
        assert brief["procedure"] and brief["checks"] and brief["done_when"]
        proc3 = self.run_cli(["brief", "--plan", str(plan_path), "--capability", "C99-E99"])
        assert proc3.returncode == core.EXIT_NOT_FOUND, proc3.returncode
        return True, f"coverage={report['status']}, brief for {cap_id}, unknown id -> exit 3"

    def t_plan_schema_gate(self):
        broken = self.workdir / "broken-plan.json"
        broken.write_text(json.dumps({"plan_id": "bad plan", "umbrella_name": "x", "goal": "g",
                                      "deliverables": [], "capabilities": [], "gates": []}))
        proc = self.run_cli(["coverage", "--plan", str(broken)])
        assert proc.returncode == core.EXIT_USAGE, proc.returncode
        assert "schema validation" in proc.stderr or "validation" in proc.stderr
        return True, "invalid plan rejected before use"

    def t_digest(self):
        config = self.workdir / "master-config.json"
        config.write_text(json.dumps({
            "master": {"title": "Fixture master", "fps": 30,
                       "resolution": {"width": 1080, "height": 1920}, "loudness_target_lufs": -14},
            "platforms": ["reels-vertical", "instagram-square"],
            "languages": ["en", "es"],
            "captions": {"en": "captions-en.srt", "es": "captions-es.srt"},
            "duration_limits": {"reels-vertical": 90, "instagram-square": 60},
            "compliance": ["disclose generated media"]
        }))
        out = self.workdir / "matrix.json"
        proc = self.run_cli(["digest", "--config", str(config), "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:300]
        matrix = json.loads(out.read_text())
        assert matrix["variant_count"] == 4, matrix["variant_count"]
        ids = [v["variant_id"] for v in matrix["variants"]]
        assert "reels-vertical-en" in ids and "instagram-square-es" in ids, ids
        bad = self.workdir / "bad-config.json"
        bad.write_text(json.dumps({"master": {"title": "x"}, "platforms": ["not-a-platform"]}))
        proc2 = self.run_cli(["digest", "--config", str(bad)])
        assert proc2.returncode == core.EXIT_USAGE, proc2.returncode
        return True, "2 platforms x 2 languages = 4 variants; unknown platform rejected"

    def t_variant(self):
        proc = self.run_cli(["variant", "--count", "3", "--base", "demo"])
        assert proc.returncode == 0, proc.stderr[:200]
        payload = json.loads(proc.stdout)
        names = [v["name"] for v in payload["variants"]]
        assert len(names) == len(set(names)) == 3, names
        proc2 = self.run_cli(["variant", "--count", "3", "--base", "demo"])
        same = [v["name"] for v in json.loads(proc2.stdout)["variants"]]
        assert names == same, "variant selection is not deterministic without a seed"
        proc3 = self.run_cli(["variant", "--count", "3", "--seed", "other"])
        assert [v["name"] for v in json.loads(proc3.stdout)["variants"]] != names, "seed had no effect"
        return True, f"3 distinct deterministic variants: {', '.join(names)}"

    def t_probe(self):
        out = self.workdir / "probe.json"
        proc = self.run_cli(["probe", "--input", str(FIXTURE), "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:300]
        payload = json.loads(out.read_text())
        assert payload["has_video"] and payload["has_audio"], payload
        assert payload["video"]["width"] == 640 and payload["video"]["height"] == 360
        assert 5.5 <= payload["duration_seconds"] <= 6.5, payload["duration_seconds"]
        return True, f"640x360 @ {payload['video']['fps']}fps, {payload['duration_seconds']}s"

    def t_probe_missing(self):
        proc = self.run_cli(["probe", "--input", "does-not-exist.mp4"])
        assert proc.returncode == core.EXIT_NOT_FOUND, proc.returncode
        assert "not found" in proc.stderr
        return True, "missing media reported with exit 3"

    def t_qa(self):
        out = self.workdir / "qa.json"
        proc = self.run_cli(["qa", "--input", str(FIXTURE), "--silence-min", "0.5",
                             "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:400]
        report = json.loads(out.read_text())
        codes = [f["code"] for f in report["findings"]]
        assert "black_frames" in codes, codes
        assert "silence_detected" in codes, codes
        assert report["summary"]["status"] == "fail", report["summary"]
        proc2 = self.run_cli(["qa", "--input", str(FIXTURE), "--silence-min", "0.5", "--fail-on-error"])
        assert proc2.returncode == core.EXIT_GATE, proc2.returncode
        return True, "black segment + silence detected; --fail-on-error exits 4"

    def t_captions(self):
        src = self.workdir / "captions.srt"
        src.write_text(SAMPLE_CAPTIONS)
        out = self.workdir / "captions-retimed.srt"
        proc = self.run_cli(["captions", "--input", str(src), "--offset", "1s",
                             "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:300]
        payload = json.loads(proc.stdout)
        assert payload["cue_count"] == 3, payload["cue_count"]
        assert "00:00:01,000 --> 00:00:03,400" in out.read_text(), out.read_text()[:120]
        broken = self.workdir / "bad.srt"
        broken.write_text("1\nnot a timecode\nhello\n")
        proc2 = self.run_cli(["captions", "--input", str(broken)])
        assert proc2.returncode == core.EXIT_USAGE, proc2.returncode
        assert "unparseable timing" in proc2.stderr
        proc3 = self.run_cli(["captions", "--input", str(src), "--min-duration", "3", "--fail-on-error"])
        assert proc3.returncode in (0, core.EXIT_GATE), proc3.returncode
        return True, "offset applied, malformed cue rejected with exit 2"

    def t_manifest(self):
        data = self.workdir / "delivery"
        data.mkdir(exist_ok=True)
        (data / "master.mp4").write_bytes(b"fixture-master-bytes")
        (data / "captions-en.srt").write_text(SAMPLE_CAPTIONS)
        out = self.workdir / "manifest.json"
        proc = self.run_cli(["manifest", "--root", str(data), "--title", "fixture",
                             "--entry", "master.mp4,role=master,version=v1",
                             "--entry", "captions-en.srt,role=captions,version=v1",
                             "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:300]
        manifest = json.loads(out.read_text())
        assert manifest["file_count"] == 2 and all(f["sha256"] for f in manifest["files"])
        proc2 = self.run_cli(["manifest", "--verify", str(out), "--root", str(data)])
        assert proc2.returncode == 0, proc2.stderr[:300]
        assert json.loads(proc2.stdout)["summary"]["status"] == "pass"
        (data / "master.mp4").write_bytes(b"tampered")
        proc3 = self.run_cli(["manifest", "--verify", str(out), "--root", str(data), "--fail-on-error"])
        assert proc3.returncode == core.EXIT_GATE, proc3.returncode
        assert "checksum_mismatch" in proc3.stdout, proc3.stdout[:300]
        (data / "master.mp4").write_bytes(b"fixture-master-bytes")
        return True, "checksums verified, tampering detected, restored and re-verified"

    def t_approval(self):
        proc = self.run_cli(["approve", "--action", "publish-master", "--target", "masters/final.mp4"])
        assert proc.returncode == 0, proc.stderr[:200]
        token = json.loads(proc.stdout)["token"]
        proc2 = self.run_cli(["approve", "--action", "publish-master",
                              "--target", "masters/final.mp4", "--approve", "deadbeef"])
        assert proc2.returncode == core.EXIT_GATE, proc2.returncode
        proc3 = self.run_cli(["approve", "--action", "publish-master",
                              "--target", "masters/final.mp4", "--approve", token])
        assert proc3.returncode == 0, proc3.stderr[:200]
        assert json.loads(proc3.stdout)["approval_granted"] is True
        proc4 = self.run_cli(["approve", "--action", "publish-master",
                              "--target", "other.mp4", "--approve", token])
        assert proc4.returncode == core.EXIT_GATE, "token must not unlock a different target set"
        return True, "wrong token and wrong target both blocked with exit 4"

    def t_thumbs(self):
        out_dir = self.workdir / "thumbs"
        proc = self.run_cli(["thumbs", "--input", str(FIXTURE), "--output-dir", str(out_dir),
                             "--count", "4", "--width", "160"])
        assert proc.returncode == 0, proc.stderr[:300]
        payload = json.loads(proc.stdout)
        files = sorted(out_dir.glob("thumb-*.jpg"))
        assert len(files) == 4, len(files)
        for f in files:   # every still must be a real JPEG, not an empty file
            head = f.read_bytes()[:3]
            assert head == b"\xff\xd8\xff", f"{f.name} is not a JPEG"
            assert f.stat().st_size > 100, f"{f.name} is empty"
        assert payload["thumbs"][0]["timecode"].count(":") == 3
        return True, "4 non-empty stills with timecodes"

    def t_reframe(self):
        out = self.workdir / "vertical.mp4"
        proc = self.run_cli(["reframe", "--input", str(FIXTURE), "--aspect", "9:16",
                             "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:400]
        payload = json.loads(proc.stdout)
        width, height = payload["output_size"]
        assert height == 360, payload["output_size"]
        assert abs(width / height - 9 / 16) < 0.01, payload["output_size"]
        assert payload["crop_applied"], "no crop box reported"
        probe = media.probe(out)
        assert probe["has_video"] and probe["duration_seconds"] > 5
        proc2 = self.run_cli(["reframe", "--input", str(FIXTURE), "--aspect", "banana",
                              "--output", str(self.workdir / "x.mp4")])
        assert proc2.returncode == core.EXIT_USAGE, proc2.returncode
        return True, "centre-cropped 9:16 render re-probed successfully; bad aspect rejected"

    def t_transcode(self):
        out = self.workdir / "review.mp4"
        proc = self.run_cli(["transcode", "--input", str(FIXTURE), "--max-width", "320",
                             "--crf", "30", "--output", str(out)])
        assert proc.returncode == 0, proc.stderr[:400]
        probe = media.probe(out)
        assert probe["video"]["width"] == 320, probe["video"]
        assert out.stat().st_size < FIXTURE.stat().st_size
        return True, f"downscaled to {probe['video']['width']}px, {out.stat().st_size} bytes"

    def t_stack(self):
        policy = self.workdir / "policy.json"
        policy.write_text(json.dumps({
            "name": "vertical-delivery",
            "delivery": {"aspect": "16:9", "max_duration_seconds": 10,
                         "require_audio": True, "loudness_target_lufs": -14},
            "checks": ["probe", "aspect", "duration", "audio_present", "loudness"]
        }))
        proc = self.run_cli(["stack", "--policy", str(policy), "--input", str(FIXTURE)])
        assert proc.returncode == 0, proc.stderr[:400]
        payload = json.loads(proc.stdout)
        codes = [f["code"] for f in payload["findings"]]
        assert "loudness_off_target" in codes, codes
        assert payload["summary"]["status"] == "warn", payload["summary"]
        strict = self.workdir / "strict.json"
        strict.write_text(json.dumps({"name": "strict",
                                      "delivery": {"aspect": "9:16", "max_duration_seconds": 2},
                                      "checks": ["aspect", "duration"]}))
        proc2 = self.run_cli(["stack", "--policy", str(strict), "--input", str(FIXTURE),
                              "--fail-on-error"])
        assert proc2.returncode == core.EXIT_GATE, proc2.returncode
        return True, "policy warn reported; strict policy fails the gate with exit 4"

    def t_recovery(self):
        """Recovery path: fix the reported cause and re-run the same gate."""
        policy = self.workdir / "recovery.json"
        strict = {"name": "recovery", "delivery": {"max_duration_seconds": 1},
                  "checks": ["duration"]}
        policy.write_text(json.dumps(strict))
        proc = self.run_cli(["stack", "--policy", str(policy), "--input", str(FIXTURE),
                             "--fail-on-error"])
        assert proc.returncode == core.EXIT_GATE, proc.returncode
        strict["delivery"]["max_duration_seconds"] = 10
        policy.write_text(json.dumps(strict))
        proc2 = self.run_cli(["stack", "--policy", str(policy), "--input", str(FIXTURE),
                              "--fail-on-error"])
        assert proc2.returncode == 0, proc2.stderr[:300]
        return True, "gate failed, cause fixed, same command now passes"

    def t_doctor(self):
        proc = self.run_cli(["doctor"])
        assert proc.returncode == 0, proc.stderr[:200]
        payload = json.loads(proc.stdout)
        assert payload["all_assets_present"] is True, payload["assets"]
        assert payload["catalog"]["capabilities"] == 250
        assert payload["commands"] >= 19
        return True, f"ffmpeg={payload['media_runtime']['ffmpeg_available']}, assets complete"

    def t_timecode(self):
        assert core.frames_to_tc(0, 25) == "00:00:00:00"
        assert core.frames_to_tc(25 * 3600 + 25 * 61 + 7, 25) == "01:01:01:07"
        assert core.tc_to_frames("01:01:01:07", 25) == 25 * 3600 + 25 * 61 + 7
        # 29.97 fps drop-frame: frames ;00 and ;01 are skipped every minute except
        # every tenth minute, so frame 1800 is 00:01:00;02 and an hour is 01:00:00;00.
        df = core.frames_to_tc(1800, 29.97)
        assert df == "00:01:00;02", df
        assert core.tc_to_frames(df, 29.97) == 1800, core.tc_to_frames(df, 29.97)
        hour = core.frames_to_tc(107892, 29.97)
        assert hour == "01:00:00;00", hour
        assert core.tc_to_frames(hour, 29.97) == 107892
        assert core.parse_duration("1m30s") == 90 and core.parse_duration("00:01:30") == 90
        for bad in ("", "aa:bb", "00:00:99:00"):
            try:
                core.tc_to_frames(bad, 25)
            except core.ToolError:
                continue
            raise AssertionError(f"timecode {bad!r} accepted")
        return True, "25fps, drop-frame 29.97 and duration parsing verified"

    # ---------------------------------------------------------------- suite
    def run(self):
        print(f"video-production-orchestrator selftest - workdir {self.workdir}\n")
        if not FIXTURE.exists():
            self.record("fixture present", False, f"missing {FIXTURE.name}")
        tests = [
            ("timecode maths", self.check, self.t_timecode),
            ("help", self.check, self.t_help),
            ("capabilities", self.check, self.t_capabilities),
            ("capabilities filters", self.check, self.t_capabilities_filter),
            ("parse plain text", self.check, self.t_parse_text),
            ("parse copied HTML", self.check, self.t_parse_html),
            ("parse numbered markdown", self.check, self.t_parse_numbered_markdown),
            ("search", self.check, self.t_search),
            ("plan", self.check, self.t_plan),
            ("plan rejects invalid input", self.check, self.t_plan_invalid),
            ("plan rejects missing input", self.check, self.t_plan_missing_input),
            ("plan schema gate", self.check, self.t_plan_schema_gate),
            ("coverage + brief", self.check, self.t_coverage_and_brief),
            ("digest", self.check, self.t_digest),
            ("variant", self.check, self.t_variant),
            ("approval gate", self.check, self.t_approval),
            ("probe", self.check_media, self.t_probe),
            ("probe missing file", self.check_media, self.t_probe_missing),
            ("qa scan", self.check_media, self.t_qa),
            ("captions", self.check, self.t_captions),
            ("manifest build + verify", self.check, self.t_manifest),
            ("thumbnails", self.check_media, self.t_thumbs),
            ("reframe 9:16", self.check_media, self.t_reframe),
            ("transcode", self.check_media, self.t_transcode),
            ("media stack policy", self.check_media, self.t_stack),
            ("recovery path", self.check_media, self.t_recovery),
            ("doctor", self.check, self.t_doctor),
        ]
        for name, runner, fn in tests:
            runner(name, fn)
        summary = {
            "tool": "video-production-orchestrator",
            "version": core.TOOL_VERSION,
            "ffmpeg_available": bool(self.ffmpeg),
            "workdir": str(self.workdir),
            "kept": self.keep,
            "passed": sum(1 for r in self.results if r["status"] == "pass"),
            "failed": sum(1 for r in self.results if r["status"] == "fail"),
            "skipped": sum(1 for r in self.results if r["status"] == "skip"),
            "total": len(self.results),
            "duration_seconds": round(sum(r["duration_seconds"] or 0 for r in self.results), 1),
            "results": self.results,
        }
        summary["status"] = "pass" if summary["failed"] == 0 else "fail"
        if not self.keep:
            shutil.rmtree(self.workdir, ignore_errors=True)
            summary["workdir_removed"] = True
        return summary


def render_report(summary):
    lines = [f"# Selftest report - {summary['tool']} {summary['version']}", "",
             f"- status: **{summary['status']}**",
             f"- passed: {summary['passed']} / {summary['total']} "
             f"(failed {summary['failed']}, skipped {summary['skipped']})",
             f"- FFmpeg available: {summary['ffmpeg_available']}",
             f"- duration: {summary['duration_seconds']}s", "",
             "| test | status | detail |", "| --- | --- | --- |"]
    lines += [f"| {r['test']} | {r['status']} | {r['detail']} |" for r in summary["results"]]
    return "\n".join(lines) + "\n"


def run_suite(workdir=None, keep=False, timeout=600.0):
    return Suite(workdir=workdir, keep=keep, timeout=timeout).run()


if __name__ == "__main__":
    print(render_report(run_suite(keep="--keep" in sys.argv)))
