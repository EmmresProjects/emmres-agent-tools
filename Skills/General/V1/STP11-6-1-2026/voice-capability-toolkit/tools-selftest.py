#!/usr/bin/env python3
"""Voice Capability Toolkit - self-test and smoke suite.

Runs the real command-line interface of every tool in a temporary work
directory and asserts on the structured results. Nothing is written inside the
Skill Tool folder.

    python3 tools-selftest.py run                 # full suite
    python3 tools-selftest.py run --quick         # skip the slowest DSP checks
    python3 tools-selftest.py run --keep          # keep artifacts for inspection
    python3 tools-selftest.py list                # list test ids
    python3 tools-selftest.py capabilities        # machine-readable command index

Exit codes: 0 all tests passed, 1 failures, 2 usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load(filename, modname):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load("tools-runtime-common.py", "_voice_rt")
PROG = "tools-selftest.py"
TOOLKIT_DIR = _HERE

MINI_CATALOG_MD = """## Category 1: Voice Creation and Identity Design
1. Skill Name: Universal Voice Creator
Description: Creates original synthetic voices from a detailed brief covering age
range, pitch, resonance and pace.
2. Skill Name: Voice Reference Board Builder
Description: Converts descriptive references into a structured voice board with
targets and forbidden traits.

## Category 2: Audio Formats, Mixing, and Delivery
1. Skill Name: Delivery Package Validator
Description: Checks file integrity, duration, channels, sample rate, loudness and
naming before release.
"""

MINI_CATALOG_HTML = """
<html><body>
<h2>Category 3: Consent, Rights, Safety, and Provenance</h2>
<table class="skills" data-x="1">
  <tr><th>#</th><th>Skill Name</th><th>Description</th></tr>
  <tr><td>1</td><td>Voice Consent Verifier</td>
      <td>Records and verifies permission before cloning a recognizable voice.</td></tr>
  <tr><td>2</td><td>Usage Rights Policy Enforcer</td>
      <td>Restricts voices by project, territory, platform and commercial permissions.</td></tr>
</table>
</body></html>
"""


class Runner:
    def __init__(self, work_dir, *, quick=False, verbose=False):
        self.work_dir = work_dir
        self.quick = quick
        self.verbose = verbose
        self.results = []
        self.artifacts = {}

    # -- helpers ---------------------------------------------------------
    def path(self, *parts):
        return os.path.join(self.work_dir, *parts)

    def run_tool(self, tool, *args, expect=0, timeout=300):
        command = [sys.executable, os.path.join(TOOLKIT_DIR, tool), *args,
                   "--assets-dir", TOOLKIT_DIR]
        started = time.time()
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(command, capture_output=True, text=True,
                                 timeout=timeout, cwd=self.work_dir, env=environment)
        elapsed = round(time.time() - started, 2)
        payload = None
        stdout = process.stdout.strip()
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = {"_raw_stdout": stdout[-2000:]}
        if expect is not None and process.returncode != expect:
            raise AssertionError(
                f"{tool} {' '.join(args[:4])} -> exit {process.returncode}, "
                f"expected {expect}.\nstdout: {stdout[:1200]}\n"
                f"stderr: {process.stderr[-800:]}\n")
        return payload, process, elapsed

    def record(self, test_id, description, func, requires=()):
        started = time.time()
        missing = [key for key in requires if key not in self.artifacts]
        if missing:
            self.results.append({
                "id": test_id, "description": description, "status": "skipped",
                "seconds": 0.0,
                "skip_reason": f"requires artifacts from {', '.join(missing)} "
                               "(skipped because that test did not run in this mode)",
            })
            return False
        try:
            detail = func() or {}
            self.results.append({"id": test_id, "description": description,
                                 "status": "pass",
                                 "seconds": round(time.time() - started, 2),
                                 "detail": detail})
            return True
        except AssertionError as exc:
            self.results.append({"id": test_id, "description": description,
                                 "status": "fail",
                                 "seconds": round(time.time() - started, 2),
                                 "error": str(exc)})
            return False
        except Exception as exc:  # noqa: BLE001 - test harness must catch all
            self.results.append({"id": test_id, "description": description,
                                 "status": "error",
                                 "seconds": round(time.time() - started, 2),
                                 "error": f"{type(exc).__name__}: {exc}"})
            return False


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def build_tests(runner):
    tests = []

    def test(test_id, description, quick_safe=True, requires=()):
        def decorator(func):
            tests.append((test_id, description, func, quick_safe, tuple(requires)))
            return func
        return decorator

    @test("structure.files", "Every shipped file is prefixed and no subfolders exist")
    def _():
        entries = sorted(os.listdir(TOOLKIT_DIR))
        ignored = {"__pycache__"}
        bad = [e for e in entries if e not in ignored
               and not (e.startswith("tools-") or e.startswith("asset-")
                        or e.startswith("knowledge-"))]
        assert_true(not bad, f"unprefixed files: {bad}")
        dirs = [e for e in entries if e not in ignored
                and os.path.isdir(os.path.join(TOOLKIT_DIR, e))]
        assert_true(not dirs, f"subfolders present: {dirs}")
        assert_true("SKILL.md" not in entries, "SKILL.md must not be inside the tool")
        knowledge = [e for e in entries if e == "knowledge-readme.md"]
        assert_true(len(knowledge) == 1, "exactly one knowledge-readme.md required")
        readme = open(os.path.join(TOOLKIT_DIR, "knowledge-readme.md"),
                      encoding="utf-8").read()
        referenced = set()
        for line in readme.splitlines():
            for token in line.replace("`", " ").replace("(", " ").replace(")", " ").split():
                token = token.strip(".,;:\"'")
                token = token.split("#")[0].rstrip(".,;:")
                if "*" in token or "/" in token:
                    continue
                if token.startswith(("tools-", "asset-")) and (
                        token.endswith(".py") or token.endswith(".json")
                        or token.endswith(".md")):
                    referenced.add(token)
        missing = sorted(t for t in referenced
                         if not os.path.isfile(os.path.join(TOOLKIT_DIR, t)))
        assert_true(not missing, f"knowledge-readme.md references missing files: {missing}")
        return {"files": len(entries), "referenced": len(referenced)}

    @test("help.entrypoints", "Every tool responds to --help and 'capabilities'")
    def _():
        tools = ["tools-catalog-builder.py", "tools-voice-profile.py",
                 "tools-audio-synthesis.py", "tools-audio-delivery.py",
                 "tools-audio-analysis.py", "tools-selftest.py"]
        for tool in tools:
            _, process, _ = runner.run_tool(tool, "--help")
            assert_true("usage:" in process.stdout,
                        f"{tool} --help did not print usage")
        for tool in tools[:5]:
            payload, _, _ = runner.run_tool(tool, "capabilities")
            assert_true(payload["status"] == "ok", f"{tool} capabilities failed")
            assert_true(payload["capabilities"], f"{tool} listed no capabilities")
        return {"tools": len(tools)}

    @test("schemas.validator", "The built-in schema validator accepts valid and "
                               "rejects invalid documents")
    def _():
        schema_doc = RT.load_asset_json("asset-schemas.json", TOOLKIT_DIR)[0]
        profile = RT.load_asset_json("asset-fixtures.json", TOOLKIT_DIR)[0]["voice_profile_example"]
        assert_true(not RT.validate_schema(profile, RT.schema_for(schema_doc, "voice_profile")),
                    "valid profile was rejected")
        broken = json.loads(json.dumps(profile))
        del broken["rights"]
        broken["design"]["pace_wpm"] = 900
        problems = RT.validate_schema(broken, RT.schema_for(schema_doc, "voice_profile"))
        assert_true(len(problems) >= 2, f"invalid profile produced {problems}")
        return {"problems_detected": len(problems)}

    @test("catalog.extract_markdown", "extract parses a Markdown capability list")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "extract",
                                        "--text", MINI_CATALOG_MD,
                                        "--output-catalog", runner.path("mini.json"))
        stats = payload["stats"]
        assert_true(stats["categories"] == 2, f"categories={stats['categories']}")
        assert_true(stats["capabilities"] == 3, f"capabilities={stats['capabilities']}")
        assert_true(not payload["schema_problems"], f"schema problems: {payload['schema_problems']}")
        catalog = payload["catalog"]
        assert_true(catalog["categories"][0]["skills"][0]["name"] == "Universal Voice Creator",
                    "first capability name mismatch")
        return {"categories": stats["categories"], "capabilities": stats["capabilities"]}

    @test("catalog.extract_html", "extract reads a copied HTML table and ignores tags")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "extract",
                                        "--text", MINI_CATALOG_HTML)
        catalog = payload["catalog"]
        names = [s["name"] for c in catalog["categories"] for s in c["skills"]]
        assert_true(catalog["categories"][0]["name"] ==
                    "Consent, Rights, Safety, and Provenance",
                    f"category not detected: {catalog['categories'][0]['name']}")
        assert_true("Voice Consent Verifier" in names, f"names: {names}")
        assert_true(all("<" not in n for n in names), "HTML leaked into capability names")
        return {"names": names}

    @test("catalog.binary_input_guard", "Binary/PDF input never crashes: it returns a "
                                        "structured error with a stable code")
    def _():
        fake_pdf = runner.path("fake.pdf")
        with open(fake_pdf, "wb") as handle:
            handle.write(b"%PDF-1.4\nnot a real pdf\n")
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "extract",
                                        "--input", fake_pdf, expect=None)
        assert_true(payload.get("status") == "error", f"unexpected payload: {payload}")
        assert_true(payload.get("code") in (RT.EXIT_INVALID_INPUT, RT.EXIT_UNAVAILABLE),
                    f"unexpected exit code {payload.get('code')}")
        binary = runner.path("binary.bin")
        with open(binary, "wb") as handle:
            handle.write(b"\x00\x01\x02 not text")
        guarded, _, _ = runner.run_tool("tools-catalog-builder.py", "extract",
                                       "--input", binary, expect=RT.EXIT_INVALID_INPUT)
        assert_true("binary" in guarded["message"].lower(), guarded["message"])
        return {"pdf_code": payload["code"], "binary_code": guarded["code"]}

    @test("catalog.error_missing_file", "Missing input file returns exit 4 and JSON error")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "extract",
                                       "--input", runner.path("does-not-exist.md"),
                                       expect=RT.EXIT_NOT_FOUND)
        assert_true(payload["status"] == "error", "error envelope missing")
        assert_true("not found" in payload["message"].lower(), payload["message"])
        return {"code": payload["code"]}

    @test("catalog.validate", "validate accepts a good catalog and rejects a broken one")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "extract",
                                        "--text", MINI_CATALOG_MD, "--force",
                                        "--output-catalog", runner.path("mini.json"))
        ok, _, _ = runner.run_tool("tools-catalog-builder.py", "validate",
                                   "--input", runner.path("mini.json"))
        assert_true(ok["valid"], f"valid catalog rejected: {ok['problems']}")
        broken = json.loads(json.dumps(payload["catalog"]))
        broken["categories"][0]["skills"][0]["description"] = "x" * 5000
        RT.write_json_file(runner.path("broken.json"), broken)
        bad, _, _ = runner.run_tool("tools-catalog-builder.py", "validate",
                                    "--input", runner.path("broken.json"),
                                    expect=RT.EXIT_INVALID_INPUT)
        assert_true(not bad["valid"], "broken catalog accepted")
        return {"problem_count": bad["problem_count"]}

    @test("catalog.build_umbrella", "build creates one unified blueprint per category")
    def _():
        runner.run_tool("tools-catalog-builder.py", "extract",
                        "--text", MINI_CATALOG_MD, "--force",
                        "--output-catalog", runner.path("mini.json"))
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "build",
                                        "--input", runner.path("mini.json"),
                                        "--out-dir", runner.path("blueprints"),
                                        "--format", "both")
        assert_true(len(payload["documents"]) == 2, f"documents={payload['documents']}")
        md = open(runner.path("blueprints", "asset-voice-creation-and-identity-design-blueprint.md"),
                  encoding="utf-8").read()
        for section in ("## 1. Deliverable", "## 5. Test plan", "## 7. Safety and rights gates"):
            assert_true(section in md, f"missing section {section}")
        assert_true("Universal Voice Creator" in md, "capability missing from blueprint")
        return {"documents": len(payload["documents"])}

    @test("catalog.build_filtered_per_capability", "build --skills/--emit changes the output")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "build",
                                        "--input", runner.path("mini.json"),
                                        "--skills", "Delivery Package Validator",
                                        "--emit", "per-capability",
                                        "--out-dir", runner.path("single"),
                                        "--format", "json")
        assert_true(len(payload["documents"]) == 1, f"documents={payload['documents']}")
        spec = json.loads(open(runner.path("single",
                                          f"asset-{payload['documents'][0]['slug']}.json"),
                               encoding="utf-8").read())
        assert_true(spec["capability_count"] == 1, "filter did not narrow the blueprint")
        assert_true(spec["capabilities"][0]["name"] == "Delivery Package Validator",
                    "wrong capability kept")
        return {"slug": payload["documents"][0]["slug"]}

    @test("catalog.lookup", "lookup finds capabilities by keyword and fails cleanly")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "lookup",
                                        "--catalog", runner.path("mini.json"),
                                        "--query", "validator")
        assert_true(payload["match_count"] == 1, f"matches={payload['match_count']}")
        missing, _, _ = runner.run_tool("tools-catalog-builder.py", "lookup",
                                        "--catalog", runner.path("mini.json"),
                                        "--query", "quantum-warp",
                                        expect=RT.EXIT_NOT_FOUND)
        assert_true(missing["status"] == "error", "missing query did not fail cleanly")
        return {"matches": payload["match_count"]}

    @test("catalog.shipped_catalog", "The shipped 200-capability catalog builds everywhere")
    def _():
        payload, _, _ = runner.run_tool("tools-catalog-builder.py", "build",
                                        "--input", os.path.join(TOOLKIT_DIR, "asset-catalog.json"),
                                        "--out-dir", runner.path("catalog-out"),
                                        "--format", "json")
        stats = payload["catalog_stats"]
        assert_true(stats["capabilities"] == 200, f"capabilities={stats['capabilities']}")
        assert_true(len(payload["documents"]) == 20, f"documents={len(payload['documents'])}")
        assert_true(not payload["unmapped_capabilities"],
                    f"unmapped: {payload['unmapped_capabilities'][:5]}")
        return {"documents": len(payload["documents"]), "capabilities": stats["capabilities"]}

    @test("profile.create", "create builds a valid profile from a text brief")
    def _():
        payload, _, _ = runner.run_tool("tools-voice-profile.py", "create",
                                        "--text", "warm conversational narrator 165hz en-US "
                                                  "young-adult feminine",
                                        "--name", "Selftest Host",
                                        "--out-dir", runner.path("profiles"))
        profile = payload["profile"]
        assert_true(profile["profile_id"] == "selftest-host", profile["profile_id"])
        assert_true(70 <= profile["design"]["pace_wpm"] <= 240, "pace out of range")
        assert_true(profile["rights"]["origin_license"], "rights block missing")
        path = runner.path("profiles", "asset-selftest-host-profile.json")
        assert_true(os.path.isfile(path), "profile file not written")
        runner.artifacts["profile"] = path
        valid, _, _ = runner.run_tool("tools-voice-profile.py", "validate", "--input", path)
        assert_true(valid["valid"], f"profile invalid: {valid['problems']}")
        return {"profile_id": profile["profile_id"], "pace": profile["design"]["pace_wpm"]}

    @test("profile.plan", "plan produces prosody events for a script")
    def _():
        payload, _, _ = runner.run_tool("tools-voice-profile.py", "plan",
                                        "--profile", runner.artifacts["profile"],
                                        "--text", "We shipped the first version in March. "
                                                  "It was fast, but it was not honest.",
                                        "--emotion", "concern")
        assert_true(payload["segment_count"] == 2, payload["segment_count"])
        assert_true(payload["total_duration_seconds"] > 1.0, "no planned duration")
        events = payload["segments"][0]["events"]
        assert_true(any(e["kind"] == "syllable" for e in events), "no syllable events")
        return {"segments": payload["segment_count"],
                "duration": payload["total_duration_seconds"]}

    @test("profile.variants_and_board", "variants and reference-board produce valid artifacts")
    def _():
        payload, _, _ = runner.run_tool("tools-voice-profile.py", "variants",
                                        "--input", runner.artifacts["profile"],
                                        "--axis", "age", "--count", "2",
                                        "--out-dir", runner.path("profiles"))
        assert_true(payload["count"] == 2, payload["count"])
        for variant in payload["variants"]:
            assert_true(variant["profile"]["provenance"]["previous_version"] == "selftest-host",
                        "variant provenance not recorded")
        board, _, _ = runner.run_tool("tools-voice-profile.py", "reference-board",
                                      "--input", runner.artifacts["profile"],
                                      "--output-md", runner.path("profiles", "board.md"))
        assert_true(board["board"]["measurable_targets"]["base_f0_hz"], "board has no targets")
        markdown = open(runner.path("profiles", "board.md"), encoding="utf-8").read()
        assert_true("## Forbidden traits" in markdown, "board markdown incomplete")
        return {"variants": payload["count"]}

    @test("consent.verify_and_use", "verify-consent and check-use allow or block correctly")
    def _():
        fixtures = os.path.join(TOOLKIT_DIR, "asset-fixtures.json")
        ok, _, _ = runner.run_tool("tools-voice-profile.py", "verify-consent",
                                   "--input", f"{fixtures}#consent_example",
                                   "--request", f"{fixtures}#usage_request_example")
        assert_true(ok["verified"], f"valid consent rejected: {ok['problems']}")
        request = json.load(open(fixtures, encoding="utf-8"))["usage_request_example"]
        request["use"] = "political-ad"
        RT.write_json_file(runner.path("bad-request.json"), request)
        bad, _, _ = runner.run_tool("tools-voice-profile.py", "verify-consent",
                                    "--input", f"{fixtures}#consent_example",
                                    "--request", runner.path("bad-request.json"),
                                    expect=RT.EXIT_BLOCKED)
        assert_true(not bad["verified"], "out-of-scope use was verified")
        allow, _, _ = runner.run_tool("tools-voice-profile.py", "check-use",
                                      "--profile", f"{fixtures}#voice_profile_example",
                                      "--request", f"{fixtures}#usage_request_example")
        assert_true(allow["decision"] == "allow", allow["problems"])
        impersonation = dict(request, imitate_real_person=True, target_person="A Famous Actor",
                             use="advertising")
        RT.write_json_file(runner.path("impersonation.json"), impersonation)
        blocked, _, _ = runner.run_tool("tools-voice-profile.py", "check-use",
                                        "--profile", f"{fixtures}#voice_profile_example",
                                        "--request", runner.path("impersonation.json"),
                                        expect=RT.EXIT_BLOCKED)
        assert_true(blocked["decision"] == "block", "impersonation was not blocked")
        return {"allow": allow["decision"], "blocked_decision": blocked["decision"]}

    @test("consent.enroll_and_revoke", "enroll chains records; revoke requires approval")
    def _():
        fixtures = os.path.join(TOOLKIT_DIR, "asset-fixtures.json")
        first, _, _ = runner.run_tool("tools-voice-profile.py", "enroll",
                                      "--input", f"{fixtures}#consent_example",
                                      "--store", runner.path("store"))
        assert_true(first["record"]["chain"]["previous_hash"] == "genesis",
                    "chain did not start at genesis")
        second, _, _ = runner.run_tool("tools-voice-profile.py", "enroll",
                                       "--input", f"{fixtures}#consent_example",
                                       "--store", runner.path("store"), "--force")
        assert_true(second["record"]["chain"]["previous_hash"] ==
                    first["record"]["chain"]["head_hash"], "chain did not link")
        gated, _, _ = runner.run_tool("tools-voice-profile.py", "revoke",
                                      "--input", f"{fixtures}#voice_profile_example",
                                      "--assets-root", runner.path("profiles"),
                                      expect=RT.EXIT_BLOCKED)
        assert_true("Approval required" in gated["message"], gated["message"])
        approved, _, _ = runner.run_tool("tools-voice-profile.py", "revoke",
                                         "--input", f"{fixtures}#voice_profile_example",
                                         "--assets-root", runner.path("profiles"),
                                         "--approve",
                                         "--output", runner.path("revocation.json"))
        assert_true(approved["record"]["dependents"] is not None, "no dependent scan")
        return {"dependents": len(approved["record"]["dependents"])}

    @test("synthesis.render_controls", "render produces audio whose measurements follow "
                                       "the direction and identity settings")
    def _():
        slow, _, _ = runner.run_tool("tools-audio-synthesis.py", "render",
                                     "--text", "Testing five distinct paces and levels.",
                                     "--profile", runner.artifacts["profile"],
                                     "--pace-wpm", "120", "--emotion", "calm",
                                     "--out-dir", runner.path("audio"),
                                     "--target-lufs", "-16")
        fast, _, _ = runner.run_tool("tools-audio-synthesis.py", "render",
                                     "--text", "Testing five distinct paces and levels.",
                                     "--profile", runner.artifacts["profile"],
                                     "--pace-wpm", "210", "--emotion", "urgency",
                                     "--out-dir", runner.path("audio"),
                                     "--target-lufs", "-16")
        slow_duration = slow["audio"]["metrics"]["duration_seconds"]
        fast_duration = fast["audio"]["metrics"]["duration_seconds"]
        assert_true(slow_duration > fast_duration,
                    f"pace control failed: slow={slow_duration} fast={fast_duration}")
        lufs = slow["audio"]["metrics"]["integrated_lufs"]
        assert_true(abs(lufs + 16.0) <= 1.0, f"loudness target missed: {lufs}")
        assert_true(slow["audio"]["metrics"]["true_peak_dbtp"] <= -1.0 + 0.06,
                    "true peak above the -1 dBTP ceiling: "
                    f"{slow['audio']['metrics']['true_peak_dbtp']}")
        pitch = slow["audio"]["metrics"].get("pitch_median_hz") or 0
        expected = slow["render_settings"]["base_f0_hz"]
        assert_true(abs(pitch - expected) / expected < 0.25,
                    f"identity pitch drifted: measured {pitch}, expected ~{expected}")
        runner.artifacts["slow_audio"] = slow["audio"]["path"]
        return {"slow_seconds": slow_duration, "fast_seconds": fast_duration,
                "lufs": lufs, "pitch": pitch, "expected_pitch": expected}

    @test("synthesis.determinism", "Same seed and settings produce byte-identical audio")
    def _():
        args = ["--text", "Deterministic render check.", "--seed", "41",
                "--out-dir", runner.path("determinism")]
        first, _, _ = runner.run_tool("tools-audio-synthesis.py", "render",
                                      *args,
                                      "--output-audio", runner.path("determinism", "a.wav"))
        second, _, _ = runner.run_tool("tools-audio-synthesis.py", "render",
                                       *args, "--force",
                                       "--output-audio", runner.path("determinism", "b.wav"))
        assert_true(first["audio"]["sha256"] == second["audio"]["sha256"],
                    "renders are not reproducible with the same seed")
        return {"sha256": first["audio"]["sha256"][:16]}

    @test("synthesis.formats", "render writes WAV, FLAC, OGG, MP3 and AIFF outputs")
    def _():
        written = {}
        for fmt in ("wav", "flac", "ogg", "mp3", "aiff"):
            payload, _, _ = runner.run_tool(
                "tools-audio-synthesis.py", "render", "--text",
                "Format check for delivery.",
                "--format", fmt, "--out-dir", runner.path("formats"))
            path = payload["audio"]["path"]
            assert_true(os.path.isfile(path) and os.path.getsize(path) > 512,
                        f"{fmt} output missing or too small")
            written[fmt] = os.path.getsize(path)
        return written

    @test("synthesis.brief", "render-brief renders segments, a master and a valid manifest")
    def _():
        fixtures = os.path.join(TOOLKIT_DIR, "asset-fixtures.json")
        with open(fixtures, encoding="utf-8") as handle:
            data = json.load(handle)
        RT.write_json_file(runner.path("brief.json"), data["brief_example"])
        RT.write_json_file(runner.path("profile.json"), data["voice_profile_example"])
        RT.write_json_file(runner.path("dictionary.json"), data["pronunciation_example"])
        payload, _, _ = runner.run_tool(
            "tools-audio-synthesis.py", "render-brief",
            "--input", runner.path("brief.json"),
            "--profile", runner.path("profile.json"),
            "--dictionary", runner.path("dictionary.json"),
            "--sanitize", "--per-segment-files",
            "--target-lufs", "-16",
            "--out-dir", runner.path("brief-out"))
        assert_true(os.path.isfile(payload["master"]), "master missing")
        assert_true(os.path.isfile(payload["manifest"]), "manifest missing")
        assert_true(len(payload["segments"]) == 3, payload["segments"])
        used = [s["dictionary"]["used"] for s in payload["segments"]]
        assert_true(any("Anemoi" in u for u in used), f"dictionary not applied: {used}")
        manifest = json.load(open(payload["manifest"], encoding="utf-8"))
        assert_true(manifest["files"][0]["sha256"], "manifest has no hash")
        runner.artifacts["brief_dir"] = os.path.dirname(payload["master"])
        runner.artifacts["master"] = payload["master"]
        return {"segments": len(payload["segments"]),
                "duration": payload["total_duration_seconds"]}

    @test("synthesis.text_normalization", "sanitize-text and dictionary tools behave "
                                          "predictably and idempotently")
    def _():
        payload, _, _ = runner.run_tool("tools-audio-synthesis.py", "sanitize-text",
                                        "--text", "Call 0917-555-0134 by 09/12/2026; "
                                                  "the fee is ₱2,500.00 and 12 kg.")
        text = payload["text"]
        assert_true("September" in text, f"date not normalized: {text}")
        assert_true("kilograms" in text, f"unit not normalized: {text}")
        assert_true(not any(ch.isdigit() for ch in text), f"digits remain: {text}")
        first, _, _ = runner.run_tool("tools-audio-synthesis.py", "apply-dictionary",
                                      "--input", runner.path("dictionary.json"),
                                      "--text", "Anemoi and Xylos-9 shipped.")
        second, _, _ = runner.run_tool("tools-audio-synthesis.py", "apply-dictionary",
                                       "--input", runner.path("dictionary.json"),
                                       "--text", first["text"])
        assert_true(first["text"] == second["text"],
                    "dictionary application is not idempotent")
        validate, _, _ = runner.run_tool("tools-audio-synthesis.py", "validate-dictionary",
                                         "--input", runner.path("dictionary.json"),
                                         "--text", "Anemoi and Xylos-9 shipped.")
        assert_true(validate["valid"], validate["problems"])
        return {"normalized": text[:60]}

    @test("synthesis.provider_error", "Unknown providers fail with EXIT_UNAVAILABLE")
    def _():
        payload, _, _ = runner.run_tool("tools-audio-synthesis.py", "render",
                                       "--text", "hi", "--provider", "not-a-provider",
                                       expect=RT.EXIT_UNAVAILABLE)
        assert_true("not available" in payload["message"], payload["message"])
        providers, _, _ = runner.run_tool("tools-audio-synthesis.py", "providers")
        assert_true(providers["providers"][0]["limitations"], "limitations not disclosed")
        return {"code": payload["code"]}

    @test("delivery.inspect_and_encode", "encode meets the podcast preset and inspect agrees",
          quick_safe=False)
    def _():
        payload, _, _ = runner.run_tool("tools-audio-delivery.py", "encode",
                                        "--input", runner.artifacts["master"],
                                        "--preset", "podcast",
                                        "--out-dir", runner.path("delivery"))
        assert_true(payload["status"] == "ok", payload)
        assert_true(payload["comparison"]["passed"],
                    f"preset mismatch: {payload['comparison']['preset_checks']}")
        again, _, _ = runner.run_tool("tools-audio-delivery.py", "inspect",
                                      "--input", payload["output"],
                                      "--preset", "podcast")
        assert_true(again["comparison"]["passed"], "inspect disagreed with encode")
        runner.artifacts["podcast"] = payload["output"]
        return {"output": os.path.basename(payload["output"]),
                "lufs": again["measurements"]["integrated_lufs"]}

    @test("delivery.master_and_repair", "master hits the loudness target; repair improves "
                                        "the noise floor without touching the source")
    def _():
        payload, _, _ = runner.run_tool("tools-audio-delivery.py", "master",
                                        "--input", runner.artifacts["master"],
                                        "--target-lufs", "-18",
                                        "--trim-silence", "--fade-out", "0.05",
                                        "--out-dir", runner.path("delivery"))
        assert_true(abs(payload["after"]["integrated_lufs"] + 18.0) <= 0.6,
                    f"master loudness off: {payload['after']['integrated_lufs']}")
        source_hash = RT.sha256_file(runner.artifacts["master"])
        repair, _, _ = runner.run_tool("tools-audio-delivery.py", "repair",
                                       "--input", runner.artifacts["master"],
                                       "--declick", "--denoise-db", "8",
                                       "--deesser", "--gate-db", "-50",
                                       "--out-dir", runner.path("delivery"))
        assert_true(repair["source_preserved"], "repair overwrote the source")
        assert_true(RT.sha256_file(runner.artifacts["master"]) == source_hash,
                    "source file changed")
        return {"master_lufs": payload["after"]["integrated_lufs"],
                "noise_improvement_db": repair["noise_floor_improvement_db"]}

    @test("delivery.effects_mix_concat", "effects, mix and concat produce measurable audio",
          quick_safe=False)
    def _():
        fx, _, _ = runner.run_tool("tools-audio-delivery.py", "effects",
                                    "--input", runner.artifacts["podcast"],
                                    "--effect", "telephone",
                                    "--out-dir", runner.path("delivery"))
        assert_true(os.path.getsize(fx["output"]) > 512, "effects output empty")
        import numpy as np
        bed_rate = 24000
        times = np.arange(bed_rate * 4) / bed_rate
        bed = (0.08 * np.sin(2 * np.pi * 110 * times))
        # Write a bed using the toolkit's own writer (keeps the test dependency-free).
        ENGINE = _load("tools-audio-engine.py", "_voice_engine")
        ENGINE.write_audio(runner.path("bed.wav"), bed, bed_rate)
        mix, _, _ = runner.run_tool("tools-audio-delivery.py", "mix",
                                    "--voice", runner.artifacts["podcast"],
                                    "--bed", runner.path("bed.wav"),
                                    "--duck-db", "8",
                                    "--out-dir", runner.path("delivery"))
        assert_true(mix["measurements"]["integrated_lufs"] < 0, "mix has no level")
        segments = sorted(
            os.path.join(runner.artifacts["brief_dir"], name)
            for name in os.listdir(runner.artifacts["brief_dir"])
            if name.endswith("-s01.wav") or name.endswith("-s02.wav")
            or name.endswith("-s03.wav"))
        assert_true(len(segments) == 3, f"segment files: {segments}")
        joined, _, _ = runner.run_tool("tools-audio-delivery.py", "concat",
                                       "--inputs", *segments, "--gap", "0.1",
                                       "--trim-dead-air", "--out-dir",
                                       runner.path("delivery"))
        assert_true(joined["total_duration_seconds"] > 5.0, "joined audio too short")
        return {"mix_lufs": mix["measurements"]["integrated_lufs"],
                "joined_seconds": joined["total_duration_seconds"]}

    @test("delivery.cue_sheet", "cue-sheet writes parseable SRT/CSV/JSON")
    def _():
        payload, _, _ = runner.run_tool("tools-audio-delivery.py", "cue-sheet",
                                        "--input", runner.path("brief.json"),
                                        "--out-dir", runner.path("cues"))
        assert_true(payload["cue_count"] == 3, payload["cue_count"])
        srt = open(os.path.join(runner.path("cues"), "cue-sheet.srt"),
                   encoding="utf-8").read()
        assert_true("00:00:00,000 -->" in srt, "SRT timecode malformed")
        arrow_lines = [l for l in srt.splitlines() if "-->" in l]
        assert_true(len(arrow_lines) == payload["cue_count"],
                    f"SRT has {len(arrow_lines)} cues, expected {payload['cue_count']}")
        assert_true(all("," in line for line in arrow_lines),
                    "SRT cues must use comma milliseconds")
        json_cues = json.load(open(os.path.join(runner.path("cues"), "cue-sheet.json"),
                                  encoding="utf-8"))
        assert_true(len(json_cues["cues"]) == payload["cue_count"], "JSON cue count differs")
        csv_text = open(os.path.join(runner.path("cues"), "cue-sheet.csv"),
                        encoding="utf-8").read()
        assert_true(csv_text.splitlines()[0].startswith("index,id"), csv_text[:80])
        return {"cues": payload["cue_count"],
                "max_cps": payload["max_chars_per_second"]}

    @test("delivery.package_and_validate", "package/validate-package pass on a clean set and "
                                           "fail after tampering (recovery path)",
          quick_safe=False)
    def _():
        payload, _, _ = runner.run_tool("tools-audio-delivery.py", "package",
                                        "--input", runner.path("delivery"),
                                        "--project", "selftest-delivery",
                                        "--target-lufs", "-16",
                                        "--license", "internal-original-design",
                                        "--disclosure", "required")
        assert_true(payload["file_count"] >= 2, payload["file_count"])
        ok, _, _ = runner.run_tool("tools-audio-delivery.py", "validate-package",
                                   "--input", runner.path("delivery"))
        assert_true(ok["status"] == "ok", f"clean package failed: {ok['issues'][:5]}")
        released, _, _ = runner.run_tool("tools-audio-delivery.py", "validate-package",
                                         "--input", runner.path("delivery"),
                                         "--release", expect=RT.EXIT_BLOCKED)
        assert_true("Approval required" in released["message"], released["message"])
        tampered = os.path.join(runner.path("delivery"), "tampered.wav")
        shutil.copyfile(runner.artifacts["podcast"], tampered)
        manifest_path = os.path.join(runner.path("delivery"), "selftest-delivery-manifest.json")
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        manifest["files"].append({"path": "tampered.wav",
                                  "sha256": "0" * 64,
                                  "bytes": os.path.getsize(tampered)})
        RT.write_json_file(manifest_path, manifest)
        bad, _, _ = runner.run_tool("tools-audio-delivery.py", "validate-package",
                                    "--input", runner.path("delivery"),
                                    expect=RT.EXIT_INVALID_INPUT)
        assert_true(bad["error_count"] >= 1, "tampering was not detected")
        return {"files": payload["file_count"], "tamper_errors": bad["error_count"]}

    @test("delivery.checklist", "checklist derives measured verdicts from a real file",
          requires=("podcast",))
    def _():
        payload, _, _ = runner.run_tool("tools-audio-delivery.py", "checklist",
                                        "--name", "podcast",
                                        "--input", runner.artifacts["podcast"])
        assert_true(payload["measured_checks"] > 0, "no measured checks")
        assert_true(payload["failed_checks"] == [], payload["failed_checks"])
        missing, _, _ = runner.run_tool("tools-audio-delivery.py", "checklist",
                                        "--name", "nope",
                                        "--input", runner.artifacts["podcast"],
                                        expect=RT.EXIT_NOT_FOUND)
        assert_true(missing["status"] == "error", "unknown checklist did not fail")
        return {"measured": payload["measured_checks"],
                "manual": len(payload["manual_checks"])}

    @test("analysis.metrics", "metrics computes the requested ops and rejects unknown ones")
    def _():
        payload, _, _ = runner.run_tool("tools-audio-analysis.py", "metrics",
                                        "--input", runner.artifacts["master"],
                                        "--ops", "loudness,clipping,pitch,bands")
        assert_true(set(payload) >= {"loudness", "clipping", "pitch", "bands"},
                    sorted(payload))
        assert_true(payload["clipping"]["clipped_samples"] == 0, "clip reported")
        bad, _, _ = runner.run_tool("tools-audio-analysis.py", "metrics",
                                    "--input", runner.artifacts["master"],
                                    "--ops", "telepathy", expect=RT.EXIT_INVALID_INPUT)
        assert_true(bad["code"] == RT.EXIT_INVALID_INPUT, bad)
        return {"lufs": payload["loudness"]["integrated_lufs"]}

    @test("analysis.segments_and_alignment", "vad, diarize and alignment return usable spans")
    def _():
        vad, _, _ = runner.run_tool("tools-audio-analysis.py", "vad",
                                    "--input", runner.artifacts["master"])
        assert_true(vad["segment_count"] >= 3, vad["segment_count"])
        diar, _, _ = runner.run_tool("tools-audio-analysis.py", "diarize",
                                      "--input", runner.artifacts["master"],
                                      "--speakers", "2")
        assert_true(len(diar["speakers"]) == 2, diar["speakers"])
        align, _, _ = runner.run_tool("tools-audio-analysis.py", "alignment",
                                       "--input", runner.artifacts["master"],
                                       "--text", "Aurora is here. Anemoi brings warmth. "
                                                 "Start building today.")
        assert_true(align["sentence_count"] == 3, align["sentence_count"])
        return {"vad_segments": vad["segment_count"],
                "drift": align["alignment_end_drift_seconds"]}

    @test("analysis.artifacts_environment_diagnostic", "artifact, environment and diagnostic "
                                                       "reports cite measurements",
          requires=("podcast",))
    def _():
        artifacts, _, _ = runner.run_tool("tools-audio-analysis.py", "artifacts",
                                           "--input", runner.artifacts["podcast"])
        assert_true("confidence_note" in artifacts, "no confidence note")
        environment, _, _ = runner.run_tool("tools-audio-analysis.py", "environment",
                                            "--input", runner.artifacts["podcast"])
        assert_true(environment["band_energy_db"], "no band energy")
        diagnostic, _, _ = runner.run_tool("tools-audio-analysis.py", "diagnostic",
                                           "--input", runner.artifacts["podcast"])
        assert_true(diagnostic["findings"], "no findings")
        assert_true(all("cause" in f for f in diagnostic["findings"]),
                    "findings lack causes")
        return {"artifact_findings": len(artifacts["findings"]),
                "diagnostic_findings": len(diagnostic["findings"])}

    @test("analysis.suitability_and_compare", "suitability scores a clean render highly; "
                                              "compare detects a real difference",
          requires=("podcast",))
    def _():
        suitable, _, _ = runner.run_tool("tools-audio-analysis.py", "suitability",
                                          "--input", runner.artifacts["podcast"],
                                          "--min-seconds", "5")
        assert_true(suitable["score"] >= 60, suitable)
        same, _, _ = runner.run_tool("tools-audio-analysis.py", "compare",
                                      "--input", runner.artifacts["podcast"],
                                      "--reference", runner.artifacts["podcast"])
        assert_true(same["passed"], f"self-comparison failed: {same['failed_metrics']}")
        different, _, _ = runner.run_tool("tools-audio-analysis.py", "compare",
                                           "--input", runner.artifacts["podcast"],
                                           "--reference", runner.artifacts["master"])
        assert_true(not different["passed"], "different files compared as identical")
        return {"score": suitable["score"],
                "self_pass": same["passed"],
                "cross_pass": different["passed"]}

    @test("runtime.approval_and_timeout", "Approval gate and cooperative deadline behave "
                                          "as documented")
    def _():
        deadline = RT.Deadline(0.0)
        try:
            deadline.check("unit")
        except RT.ToolError as exc:
            assert_true(exc.code == RT.EXIT_CANCELLED, exc.code)
        else:
            raise AssertionError("expired deadline did not raise")
        class FakeArgs:
            approve = False
        os.environ.pop("VOICE_TOOLKIT_APPROVAL_TOKEN", None)
        try:
            RT.require_approval(FakeArgs(), "publish", detail="selftest")
        except RT.ToolError as exc:
            assert_true(exc.code == RT.EXIT_BLOCKED, exc.code)
        else:
            raise AssertionError("approval gate did not block")
        os.environ["VOICE_TOOLKIT_APPROVAL_TOKEN"] = "selftest-token"
        try:
            assert_true(RT.require_approval(FakeArgs(), "publish"), "token approval failed")
        finally:
            os.environ.pop("VOICE_TOOLKIT_APPROVAL_TOKEN", None)
        return {"exit_codes": {"cancelled": RT.EXIT_CANCELLED,
                               "blocked": RT.EXIT_BLOCKED}}

    @test("runtime.overwrite_guard", "Existing outputs are not silently overwritten")
    def _():
        target = runner.path("delivery", "guard.wav")
        runner.run_tool("tools-audio-synthesis.py", "render", "--text",
                        "Overwrite guard check.", "--output-audio", target)
        payload, _, _ = runner.run_tool("tools-audio-synthesis.py", "render",
                                        "--text", "Overwrite guard check.",
                                        "--output-audio", target,
                                        expect=RT.EXIT_INVALID_INPUT)
        assert_true("Refusing to overwrite" in payload["message"], payload["message"])
        runner.run_tool("tools-audio-synthesis.py", "render", "--text",
                        "Overwrite guard check.", "--output-audio", target, "--force")
        return {"guarded": True}

    @test("runtime.structured_output_file", "Results written with --output are valid JSON",
          requires=("podcast",))
    def _():
        out_path = runner.path("result.json")
        runner.run_tool("tools-audio-analysis.py", "vad",
                        "--input", runner.artifacts["podcast"], "--output", out_path)
        document = json.load(open(out_path, encoding="utf-8"))
        assert_true(document["status"] == "ok", document)
        assert_true(document["toolkit"] == "voice-capability-toolkit", document)
        return {"bytes": os.path.getsize(out_path)}

    return tests


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _prepare_quick_artifacts(runner):
    """Cheaply create the podcast artifact used by the dependent tests."""
    if "master" not in runner.artifacts:
        fixtures = os.path.join(TOOLKIT_DIR, "asset-fixtures.json")
        with open(fixtures, encoding="utf-8") as handle:
            data = json.load(handle)
        RT.write_json_file(runner.path("brief.json"), data["brief_example"])
        RT.write_json_file(runner.path("profile.json"), data["voice_profile_example"])
        RT.write_json_file(runner.path("dictionary.json"), data["pronunciation_example"])
        payload, _, _ = runner.run_tool(
            "tools-audio-synthesis.py", "render-brief",
            "--input", runner.path("brief.json"),
            "--profile", runner.path("profile.json"),
            "--dictionary", runner.path("dictionary.json"),
            "--target-lufs", "-16", "--out-dir", runner.path("brief-out"))
        runner.artifacts["master"] = payload["master"]
        runner.artifacts["brief_dir"] = os.path.dirname(payload["master"])
    if "podcast" not in runner.artifacts:
        payload, _, _ = runner.run_tool(
            "tools-audio-delivery.py", "encode",
            "--input", runner.artifacts["master"], "--preset", "podcast",
            "--format", "wav", "--out-dir", runner.path("delivery"))
        runner.artifacts["podcast"] = payload["output"]


def cmd_run(args):
    work_dir = args.work_dir or tempfile.mkdtemp(prefix="voice-toolkit-selftest-")
    os.makedirs(work_dir, exist_ok=True)
    runner = Runner(work_dir, quick=args.quick, verbose=not args.quiet)
    tests = build_tests(runner)
    if args.quick and not args.only:
        # --quick runs a subset; also produce the artifacts the skipped tests
        # need so nothing is reported as skipped for a missing dependency.
        runner.quick_prerequisites = True
    if args.only:
        wanted = set(args.only)
        tests = [t for t in tests if t[0] in wanted or
                 any(t[0].startswith(prefix) for prefix in wanted)]
        if not tests:
            raise RT.ToolError(f"No tests matched --only {args.only}.",
                               code=RT.EXIT_USAGE,
                               hint="Run `python3 tools-selftest.py list` to see ids.")
    started = time.time()
    for test_id, description, func, quick_safe, requires in tests:
        if args.quick and not quick_safe:
            continue
        if requires and getattr(runner, "quick_prerequisites", False) \
                and any(key not in runner.artifacts for key in requires):
            _prepare_quick_artifacts(runner)
        runner.record(test_id, description, func, requires=requires)
        if not args.quiet:
            result = runner.results[-1]
            marker = {"pass": "PASS", "skipped": "SKIP"}.get(result["status"],
                                                             result["status"].upper())
            sys.stdout.write(f"[{marker}] {test_id} ({result['seconds']}s) - {description}\n")
            if result["status"] not in ("pass", "skipped"):
                sys.stdout.write(f"        {result.get('error', '')[:600]}\n")
            elif result["status"] == "skipped":
                sys.stdout.write(f"        {result.get('skip_reason', '')}\n")
    totals = {
        "total": len(runner.results),
        "passed": len([r for r in runner.results if r["status"] == "pass"]),
        "failed": len([r for r in runner.results if r["status"] == "fail"]),
        "errors": len([r for r in runner.results if r["status"] == "error"]),
        "skipped": len([r for r in runner.results if r["status"] == "skipped"]),
        "seconds": round(time.time() - started, 2),
    }
    report = {
        "status": "ok" if totals["failed"] == 0 and totals["errors"] == 0 else "failed",
        "mode": "quick" if args.quick else "full",
        "command": "run",
        "toolkit_dir": TOOLKIT_DIR,
        "work_dir": work_dir,
        "quick": args.quick,
        "totals": totals,
        "results": runner.results,
    }
    if not args.quiet:
        sys.stdout.write(
            f"summary: {totals['passed']}/{totals['total']} passed, "
            f"{totals['failed']} failed, {totals['errors']} errors, "
            f"{totals['skipped']} skipped in {totals['seconds']}s -> {report['status']}\n")
    RT.emit(report, output=args.output, quiet=args.quiet)
    if not args.keep and not args.work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
        if not args.quiet:
            sys.stderr.write(f"[{PROG}] removed temporary work dir {work_dir}\n")
    else:
        if not args.quiet:
            sys.stderr.write(f"[{PROG}] artifacts kept in {work_dir}\n")
    return RT.EXIT_OK if report["status"] == "ok" else RT.EXIT_INVALID_INPUT


def cmd_list(args):
    runner = Runner("/tmp", quick=args.quick)
    tests = build_tests(runner)
    RT.emit({"status": "ok", "command": "list",
             "tests": [{"id": t[0], "description": t[1], "quick_safe": t[3],
                        "requires": list(t[4])} for t in tests], "count": len(tests)},
            output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def cmd_capabilities(args):
    RT.emit(RT.capabilities_report(
        PROG,
        [{"command": "run", "summary": "Run the full self-test suite against the real CLI.",
          "inputs": ["--quick", "--only", "--keep", "--work-dir"], "outputs": ["report JSON"]},
         {"command": "list", "summary": "List test ids and quick-safety.",
          "inputs": [], "outputs": ["JSON"]},
         {"command": "capabilities", "summary": "Print this index.",
          "inputs": [], "outputs": ["JSON"]}],
        notes={"requires": "the five other tools and every asset-* file in the same folder"}),
        output=args.output, quiet=args.quiet)
    return RT.EXIT_OK


def build_parser():
    parser = RT.build_parser(
        PROG,
        "Voice Capability Toolkit - self-test suite that exercises the real CLI.",
        epilog="Examples:\n  python3 tools-selftest.py run\n"
               "  python3 tools-selftest.py run --quick --keep\n")
    sub = parser.add_subparsers(dest="command", metavar="command")
    p = sub.add_parser("run", help="Run the suite.")
    p.add_argument("--quick", action="store_true", help="Skip the slowest checks.")
    p.add_argument("--only", nargs="+", help="Run only these test ids (prefix match).")
    p.add_argument("--keep", action="store_true", help="Keep the temporary work dir.")
    p.add_argument("--work-dir", help="Use this work dir instead of a temp dir.")
    p.add_argument("--output", "-o", help="Also write the JSON report here.")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-test progress lines (the report is always printed).")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("list", help="List tests.")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--output", "-o")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("capabilities", help="Print the command index.")
    p.add_argument("--output", "-o")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_capabilities)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(RT.guard(main)())
