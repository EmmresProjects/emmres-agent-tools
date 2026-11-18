#!/usr/bin/env python3
"""Test suite for the Offline Neural Voice Skill Tool.

Unit tests for text/SSML/audio/safety logic plus a CLI contract test and an
end-to-end greeting acceptance test (skipped when the neural engine is not
installed yet — run tools-ov-setup.py first, or pass --quick to skip it).

Run: python3 tools-run-tests.py [--quick]
"""
from __future__ import annotations

import sys
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # never litter the skill folder with __pycache__

SKILL_ROOT = Path(__file__).resolve().parent


def load_sibling(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SKILL_ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = load_sibling("tools-ov-core.py", "ov_core")
TEXT = load_sibling("tools-ov-text.py", "ov_text")
SSML = load_sibling("tools-ov-ssml.py", "ov_ssml")
AUDIO = load_sibling("tools-ov-audio.py", "ov_audio")
PROVIDERS = load_sibling("tools-ov-providers.py", "ov_providers")
SAFETY = load_sibling("tools-ov-safety.py", "ov_safety")

DICTIONARY = TEXT.load_pronunciation()


class TextNormalizeTests(unittest.TestCase):
    def test_currency_and_percent(self):
        out = TEXT.normalize_text("Pay $1,234.50 (98% sure)", DICTIONARY)["normalized"]
        self.assertIn("one thousand two hundred thirty-four dollars and fifty cents", out)
        self.assertIn("ninety-eight percent", out)

    def test_ordinal_and_date_and_time(self):
        out = TEXT.normalize_text("On 2026-11-15 at 3:45 PM, 2nd place", DICTIONARY)["normalized"]
        self.assertIn("November fifteenth, twenty twenty-six", out)
        self.assertIn("three forty-five P M", out)
        self.assertIn("second", out)

    def test_url_and_email(self):
        out = TEXT.normalize_text("See https://example.com/a?b=c or mail a@b.com", DICTIONARY)["normalized"]
        self.assertIn("example dot com", out)
        self.assertIn(" at ", out)
        self.assertNotIn("https://", out)

    def test_acronym_expansion(self):
        out = TEXT.normalize_text("The CPU and GPU report", DICTIONARY)["normalized"]
        self.assertIn("C P U", out)
        self.assertIn("G P U", out)

    def test_unicode_and_whitespace(self):
        out = TEXT.normalize_text("  Hello\u200b   \u201cworld\u201d\x00  ", DICTIONARY)["normalized"]
        self.assertEqual(out, 'Hello "world"')

    def test_integer_words(self):
        self.assertEqual(TEXT.int_to_words(2026), "two thousand twenty-six")
        self.assertEqual(TEXT.int_to_words(0), "zero")

    def test_int_to_ordinal(self):
        self.assertEqual(TEXT.int_to_ordinal(1), "first")
        self.assertEqual(TEXT.int_to_ordinal(23), "twenty-third")
        self.assertEqual(TEXT.int_to_ordinal(100), "hundredth")


class SegmentChunkTests(unittest.TestCase):
    def test_sentence_split_protects_abbreviations(self):
        sentences = TEXT.segment_sentences("Hello Dr. Smith. Welcome! Really?")
        self.assertEqual(sentences, ["Hello Dr. Smith.", "Welcome!", "Really?"])

    def test_chunk_order_and_limit(self):
        text = ("Alpha beta gamma delta. " * 8).strip()
        chunks = TEXT.chunk_text(text, max_chars=80)
        self.assertGreater(len(chunks), 1)
        for i, chunk in enumerate(chunks):
            self.assertEqual(chunk["index"], i)
            self.assertLessEqual(chunk["chars"], 80)
        reassembled = " ".join(c["text"] for c in chunks)
        self.assertIn("Alpha", reassembled)

    def test_wer(self):
        self.assertEqual(TEXT.word_error_rate("a b c d", "a b c d")["wer"], 0.0)
        self.assertEqual(TEXT.word_error_rate("a b c d", "a b c")["deletions"], 1)
        self.assertEqual(TEXT.word_error_rate("a b", "a b c d")["insertions"], 2)

    def test_phrase_match(self):
        r = TEXT.phrase_match("Greetings, friends! Welcome to my video.",
                              "greetings friends welcome to my video")
        self.assertTrue(r["complete"])
        self.assertEqual(r["missing_words"], [])


class SsmlTests(unittest.TestCase):
    GREETING = '<speak>Greetings, friends!<break time="240ms"/><emphasis level="strong">Welcome</emphasis> to my video.</speak>'

    def test_greeting_plan(self):
        segments, warnings = SSML.parse_ssml(self.GREETING, TEXT)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[1]["pause_before_ms"], 240)
        self.assertTrue(segments[1]["emphasis"])
        self.assertEqual(segments[1]["text"], "Welcome")

    def test_security_rejects_script(self):
        with self.assertRaises(SSML.SsmlError):
            SSML.parse_ssml("<speak>Hi<script>alert(1)</script></speak>", TEXT)

    def test_security_rejects_doctype(self):
        with self.assertRaises(SSML.SsmlError):
            SSML.parse_ssml('<!DOCTYPE speak><speak>Hi</speak>', TEXT)

    def test_unknown_tag_kept_as_text(self):
        segments, warnings = SSML.parse_ssml("<speak>Hello <bogus>world</bogus></speak>", TEXT)
        self.assertTrue(any("bogus" in w for w in warnings))
        self.assertIn("world", " ".join(s["text"] for s in segments))

    def test_say_as_characters(self):
        segments, _ = SSML.parse_ssml('<speak><say-as interpret-as="characters">FBI</say-as></speak>', TEXT)
        self.assertEqual(segments[0]["text"], "F B I")


class AudioTests(unittest.TestCase):
    def _tone(self, seconds=1.0, rate=22050, freq=220.0, amp=0.5):
        n = int(seconds * rate)
        return [amp * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]

    def test_wav_roundtrip(self):
        samples = self._tone()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.wav"
            CORE.write_wav(path, samples, 22050)
            back = CORE.read_wav(path)
            self.assertEqual(back["rate"], 22050)
            self.assertEqual(len(back["samples"]), len(samples))
            self.assertTrue(all(abs(a - b) < 1e-3 for a, b in zip(samples[::997], back["samples"][::997])))

    def test_remove_dc(self):
        samples = [s + 0.1 for s in self._tone(0.1)]
        out, mean = AUDIO.remove_dc(samples)
        self.assertAlmostEqual(mean, 0.1, places=6)
        self.assertAlmostEqual(sum(out) / len(out), 0.0, places=6)

    def test_trim_silence(self):
        rate = 22050
        silence = [0.0] * int(0.5 * rate)
        tone = self._tone(0.5, rate)
        samples = silence + tone + silence
        trimmed, trimmed_ms = AUDIO.trim_silence(samples, rate, threshold_db=-45.0, pad_ms=40)
        self.assertLess(len(trimmed), len(samples))
        self.assertGreater(trimmed_ms, 500)

    def test_soft_clip_limit(self):
        samples = [0.9, -0.95, 0.5]
        out, gain_db = AUDIO.soft_clip(samples, -1.0)
        peak_db = AUDIO.lin_to_dbfs(AUDIO.sample_peak(out))
        self.assertLessEqual(peak_db, -1.0 + 1e-6)
        self.assertLess(gain_db, 0.0)

    def test_loudness_gain_direction(self):
        samples = self._tone(1.0, 22050, 220.0, amp=0.01)
        out, report = AUDIO.normalize_loudness(samples, 22050, -14.0, -1.0)
        self.assertGreater(report["gain_db"], 0.0)
        self.assertFalse(report["limited"])

    def test_validate_detects_clipping(self):
        samples = [0.5] * 1000 + [1.0] + [0.5] * 1000
        report = AUDIO.validate_signal(samples, 22050, 1)
        self.assertTrue(any("clipped" in e for e in report["errors"]))

    def test_validate_duration_bounds(self):
        samples = self._tone(1.0)
        report = AUDIO.validate_signal(samples, 22050, 1, duration_min=2.5)
        self.assertTrue(any("duration" in e for e in report["errors"]))


class SafetyTests(unittest.TestCase):
    def test_general_request_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            decision = SAFETY.check_request({"real_person": False, "context": "general"}, Path(td))
        self.assertEqual(decision["decision"], "allow")

    def test_minor_denied(self):
        with tempfile.TemporaryDirectory() as td:
            decision = SAFETY.check_request({"involves_minor": True}, Path(td))
        self.assertEqual(decision["decision"], "deny")

    def test_real_person_requires_consent(self):
        with tempfile.TemporaryDirectory() as td:
            decision = SAFETY.check_request(
                {"real_person": True, "speaker_name": "Jane Doe", "context": "general"}, Path(td))
            self.assertEqual(decision["decision"], "deny")
            rec = SAFETY.grant_consent(Path(td), "Jane Doe", "Jane Doe herself",
                                       "product intro videos", None, None)
            allowed = SAFETY.check_request(
                {"real_person": True, "speaker_name": "Jane Doe", "context": "general"}, Path(td))
            self.assertEqual(allowed["decision"], "allow")
            SAFETY.revoke_consent(Path(td), rec["id"])
            revoked = SAFETY.check_request(
                {"real_person": True, "speaker_name": "Jane Doe"}, Path(td))
            self.assertEqual(revoked["decision"], "deny")

    def test_prohibited_use_denied(self):
        with tempfile.TemporaryDirectory() as td:
            decision = SAFETY.check_request(
                {"requested_use": "non-consensual voice cloning of my neighbor"}, Path(td))
        self.assertEqual(decision["decision"], "deny")


class CliTests(unittest.TestCase):
    CLI = SKILL_ROOT / "tools-offline-voice.py"

    def run_cli(self, *argv):
        return subprocess.run([sys.executable, str(self.CLI), *argv],
                              capture_output=True, text=True, timeout=120)

    def test_help(self):
        proc = self.run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("synthesize", proc.stdout)

    def test_capabilities_lists_32_categories(self):
        proc = self.run_cli("capabilities")
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload["catalog_categories"]), 32)
        self.assertTrue(any(op["command"] == "synthesize" for op in payload["operations"]))

    def test_invalid_command_exits_2(self):
        proc = self.run_cli("definitely-not-a-command")
        self.assertEqual(proc.returncode, 2)

    def test_normalize_changes_output(self):
        proc = self.run_cli("normalize", "--text", "Pay $5 tomorrow", "--raw")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("five dollars", proc.stdout)

    def test_ssml_check_greeting(self):
        proc = self.run_cli("ssml-check", "--text",
                            '<speak>Greetings, friends!<break time="240ms"/>Welcome.</speak>')
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])

    def test_validate_on_generated_wav(self):
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "t.wav"
            rate = 22050
            samples = [0.3 * math.sin(2 * math.pi * 220 * i / rate) for i in range(rate)]
            CORE.write_wav(wav_path, samples, rate)
            proc = self.run_cli("validate", "--input", str(wav_path))
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["frames"], rate)


def engine_available():
    cfg = CORE.default_config()
    probe = PROVIDERS.engine_probe(cfg)
    return probe.get("ready", False)


@unittest.skipIf("--quick" in sys.argv, "quick mode: skipping end-to-end greeting test")
class GreetingAcceptanceTests(unittest.TestCase):
    def test_greeting_acceptance(self):
        if not engine_available():
            self.skipTest("neural engine not installed yet; run tools-ov-setup.py")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "greeting.wav"
            ssml = CORE.load_json_asset(CORE.ASSET_ACCEPTANCE)["required_acceptance"]["ssml"]
            proc = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "tools-offline-voice.py"), "synthesize",
                 "--ssml", ssml, "--output", str(out), "--no-provenance"],
                capture_output=True, text=True, timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            report = json.loads(proc.stdout)
            self.assertGreaterEqual(report["duration_s"], 2.5)
            self.assertLessEqual(report["duration_s"], 4.0)
            self.assertLessEqual(report["peak_dbfs"], -1.0 + 0.05)
            self.assertEqual(report["clipped_samples"], 0)
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    print("Offline Neural Voice — self-test", file=sys.stderr)
    verbosity = 2 if "--verbose" in sys.argv else 1
    argv = [a for a in sys.argv[1:] if not a.startswith("--quick") and not a.startswith("--verbose")]
    unittest.main(argv=[sys.argv[0], "-v" if verbosity > 1 else "-q"], exit=True)
