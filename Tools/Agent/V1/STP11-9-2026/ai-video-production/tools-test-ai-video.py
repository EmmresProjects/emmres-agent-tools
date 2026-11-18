#!/usr/bin/env python3
"""Standard-library regression tests; run from any working directory."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

sys.dont_write_bytecode = True  # Do not create cache folders inside the Skill Tool.
ROOT = Path(__file__).resolve().parent
core = SourceFileLoader("test_video_core", str(ROOT / "tools-video-core.py")).load_module()
CLI = ROOT / "tools-ai-video.py"
EXAMPLE = ROOT / "asset-example-brief.json"


class CatalogAndPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = core.load_catalog()
        cls.config = core.load_config()
        cls.brief = core.normalize_brief(core.read_json(EXAMPLE), cls.config)

    def test_complete_catalog(self):
        self.assertEqual(len(self.catalog["categories"]), 47)
        self.assertEqual(sum(len(c["skills"]) for c in self.catalog["categories"]), 564)
        self.assertTrue(all(len(c["skills"]) == 12 for c in self.catalog["categories"]))

    def test_example_plan(self):
        plan = core.create_plan(self.brief, self.catalog)
        self.assertEqual(len(plan["shots"]), 3)
        self.assertEqual(plan["duration_seconds"], 12)
        self.assertEqual(plan["capability_count"], 60)
        self.assertTrue(all(c["expected_artifacts"] for c in plan["capabilities"]))

    def test_every_capability_is_reachable(self):
        for category in self.catalog["categories"]:
            brief = dict(self.brief)
            brief.update(categories=[category["id"]], capabilities=[])
            plan = core.create_plan(brief, self.catalog)
            self.assertEqual([c["capability_id"] for c in plan["capabilities"]], [s["id"] for s in category["skills"]])

    def test_invalid_and_missing_fields(self):
        with self.assertRaises(core.ToolError) as caught:
            core.normalize_brief({"project_title": "Missing prompt"}, self.config)
        self.assertEqual(caught.exception.code, "INVALID_BRIEF")
        bad = dict(self.brief, duration_seconds=-2)
        with self.assertRaises(core.ToolError):
            core.normalize_brief(bad, self.config)

    def test_changed_input_changes_plan(self):
        changed = dict(self.brief, project_title="Changed", duration_seconds=9)
        changed["shots"] = [dict(s) for s in self.brief["shots"]]
        changed["shots"].pop()
        first, second = core.create_plan(self.brief, self.catalog), core.create_plan(changed, self.catalog)
        self.assertNotEqual(first["project_title"], second["project_title"])
        self.assertNotEqual(first["shots"], second["shots"])

    def test_caption_formats(self):
        captions = core.captions_from_plan(core.create_plan(self.brief, self.catalog))
        srt, vtt = core.format_srt(captions), core.format_vtt(captions)
        self.assertIn("00:00:00,000 --> 00:00:04,000", srt)
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:00.000"))
        self.assertEqual(len(captions), 3)

    def test_preview_is_self_contained_and_escapes_html(self):
        brief = dict(self.brief, project_title="<script>alert(1)</script>")
        preview = core.render_html(core.create_plan(brief, self.catalog))
        self.assertIn("&lt;script&gt;", preview)
        self.assertNotIn("<script>alert(1)</script>", preview)
        self.assertIn("requestAnimationFrame", preview)
        self.assertNotIn("https://", preview)

    def test_evaluation_and_approval_gate(self):
        plan = core.create_plan(self.brief, self.catalog)
        report = core.evaluate_plan(plan)
        self.assertIn(report["status"], ("pass", "review-required"))
        self.assertTrue(any(c["check"] == "continuous_timeline" and c["passed"] for c in report["checks"]))

    def test_atomic_recovery_for_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan.json"
            target.write_text("preserve me", encoding="utf-8")
            with self.assertRaises(core.ToolError) as caught:
                core.write_json(target, {"replacement": True})
            self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
            self.assertEqual(target.read_text(), "preserve me")


class CommandTests(unittest.TestCase):
    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_help_and_capabilities(self):
        self.assertIn("capabilities", self.run_cli("--help").stdout)
        result = self.run_cli("capabilities", "--category", "17", "--format", "json")
        data = json.loads(result.stdout)
        self.assertEqual(data["capability_count"], 12)

    def test_html_request_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plan.json"
            request = '<ol><li class="x">Translation and Video Localization</li></ol>'
            result = self.run_cli("plan", "--input", EXAMPLE, "--output", target, "--request", request)
            self.assertEqual(json.loads(result.stdout)["capability_count"], 72)  # 60 brief + 12 newly selected

    def test_full_workflow_and_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifacts"
            result = self.run_cli("run", "--input", EXAMPLE, "--output-dir", output)
            self.assertEqual(json.loads(result.stdout)["status"], "completed-local-artifacts")
            for name in ("plan.json", "preview.html", "captions.srt", "captions.vtt", "evaluation.json"):
                self.assertTrue((output / name).is_file(), name)
                self.assertGreater((output / name).stat().st_size, 20)
            json.loads((output / "plan.json").read_text())
            json.loads((output / "evaluation.json").read_text())
            self.assertIn("<html", (output / "preview.html").read_text())
            self.run_cli("evaluate", "--input", output / "plan.json", expected=2)

    def test_structured_missing_input_error(self):
        result = self.run_cli("validate", "--input", "does-not-exist.json", expected=2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "INPUT_NOT_FOUND")

    def test_render_requires_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            self.run_cli("plan", "--input", EXAMPLE, "--output", plan)
            result = self.run_cli("render", "--input", plan, "--output", Path(directory) / "out.mp4", expected=2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "APPROVAL_REQUIRED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
