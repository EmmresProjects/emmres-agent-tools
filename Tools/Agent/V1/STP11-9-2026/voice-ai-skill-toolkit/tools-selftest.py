#!/usr/bin/env python3
"""Built-in end-to-end self test for the voice-ai-skill-toolkit.

Runs the real CLI (tools-voice-skill-tool.py) as a subprocess against fresh
temporary fixtures: a generated sine WAV, a clipping WAV, valid and invalid
SRT transcripts, valid and invalid consent records, and scaffolded work
packages. No network, no external services, no credentials.

Usage:
  python3 tools-voice-skill-tool.py selftest [--keep] [--json]

Exits 0 when every check passes, 1 otherwise. Temporary files are removed
unless --keep is passed.
"""

import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave

sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    # Share one module instance per name via sys.modules so that classes
    # (e.g. tools_common.ToolError) keep a single identity across siblings.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CLI = os.path.join(_HERE, "tools-voice-skill-tool.py")
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print("%s %-58s %s" % ("PASS" if ok else "FAIL", name, detail))


def run_cli(args, expect_code=None, cwd=None):
    proc = subprocess.run(
        [sys.executable, CLI] + args,
        capture_output=True, text=True, cwd=cwd, timeout=180,
    )
    if expect_code is not None and proc.returncode != expect_code:
        return proc, False
    return proc, True


def write_wav(path, freq=440.0, seconds=1.0, rate=16000, channels=1, amplitude=0.5, clip=False):
    frames = int(seconds * rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        buf = bytearray()
        for i in range(frames):
            if clip:
                sample = 32767
            else:
                sample = int(amplitude * 32767 * math.sin(2.0 * math.pi * freq * i / rate))
            for _ in range(channels):
                buf += int(sample).to_bytes(2, "little", signed=True)
        wf.writeframes(bytes(buf))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    keep = "--keep" in argv
    tmp = tempfile.mkdtemp(prefix="voice-tool-selftest-")
    try:
        tone = os.path.join(tmp, "tone.wav")
        clip_wav = os.path.join(tmp, "clip.wav")
        write_wav(tone, freq=440.0, seconds=1.0, rate=16000, channels=1, amplitude=0.5)
        write_wav(clip_wav, clip=True, seconds=0.25, rate=16000, channels=1)

        srt_ok = os.path.join(tmp, "ok.srt")
        with open(srt_ok, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:02,000\nHello world this is a narration test.\n\n"
                     "2\n00:00:02,500 --> 00:00:04,500\nSecond cue follows the first cleanly.\n\n")
        srt_bad = os.path.join(tmp, "bad.srt")
        with open(srt_bad, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:03,000 --> 00:00:01,000\nBackwards in time.\n\n")

        consent_ok = os.path.join(tmp, "consent-ok.json")
        with open(consent_ok, "w", encoding="utf-8") as fh:
            json.dump({
                "record_id": "VC-TEST-1",
                "voice_owner_name": "Test Speaker",
                "voice_owner_contact": "speaker@example.com",
                "relationship_to_voice_owner": "self",
                "signed_by": "Test Speaker",
                "signature_date": "2026-11-01",
                "scope": {"use_cases": ["narration"], "languages": ["en"], "territory": "PH",
                          "term_start": "2026-11-01", "term_end": "2027-11-01",
                          "exclusivity": "non-exclusive"},
                "disclosures": {"ai_generated_voice_disclosed": True,
                                "synthesis_or_cloning_disclosed": True,
                                "redistribution_terms_disclosed": True,
                                "compensation_terms_disclosed": True},
                "data_handling": {"storage_description": "encrypted volume", "retention_days": 180,
                                  "encryption_required": True, "deletion_on_revocation": True,
                                  "third_party_sharing": "none"},
                "revocation": {"contact": "legal@example.com", "process": "email with ID check",
                               "lead_time_days": 30},
                "witness": {"name": "Witness One", "role": "producer", "date": "2026-11-01"},
            }, fh)
        consent_bad = os.path.join(tmp, "consent-bad.json")
        with open(consent_bad, "w", encoding="utf-8") as fh:
            json.dump({"signed_by": "", "signature_date": "not-a-date"}, fh)

        # --- interface checks -------------------------------------------------
        proc, ok = run_cli(["help"])
        record("help exits 0 and lists commands", ok and "capabilities" in proc.stdout)

        proc, ok = run_cli(["capabilities", "--json"])
        caps = json.loads(proc.stdout) if ok else {}
        record("capabilities --json parses", ok and len(caps.get("capabilities", [])) >= 14)

        proc, ok = run_cli(["categories", "--json"])
        cats = json.loads(proc.stdout).get("categories", []) if ok else []
        record("categories --json returns 50", ok and len(cats) == 50)

        proc, ok = run_cli(["list", "--category", "2", "--json"])
        skills = json.loads(proc.stdout).get("skills", []) if ok else []
        record("list --category 2 returns 10 skills", ok and len(skills) == 10)

        proc, ok = run_cli(["search", "lexicon", "--json"])
        hits = json.loads(proc.stdout).get("hits", []) if ok else []
        record("search 'lexicon' finds skills", ok and len(hits) >= 1)

        proc, ok = run_cli(["show", "number-verbalization-engine", "--json"])
        record("show resolves by slug", ok and json.loads(proc.stdout)["skill"]["number"] == "2.3")

        proc, ok = run_cli(["show", "2.3", "--json"])
        record("show resolves by catalog number", ok and json.loads(proc.stdout)["skill"]["id"] == "number-verbalization-engine")

        proc, ok = run_cli(["show", "definitely-not-a-skill-xyz"], expect_code=2)
        record("show rejects unknown skill with exit 2", ok)

        brief_path = os.path.join(tmp, "brief.md")
        proc, ok = run_cli(["brief", "--skill", "2.3", "--preset", "calm-narration",
                            "--delivery-profile", "ivr-telephony", "--requester", "selftest",
                            "--output", brief_path])
        brief_ok = ok and os.path.isfile(brief_path) and "{{" not in open(brief_path).read()
        record("brief renders with no unresolved tokens", brief_ok)

        # --- audio checks ------------------------------------------------------
        proc, ok = run_cli(["audio-check", tone, "--json"])
        a = json.loads(proc.stdout) if ok else {}
        record("audio-check tone passes", ok and a.get("verdict") == "pass")

        proc, ok = run_cli(["audio-check", clip_wav, "--json"])
        a = json.loads(proc.stdout) if ok else {}
        record("audio-check detects clipping (fail)", ok and a.get("verdict") == "fail")

        proc, ok = run_cli(["audio-check", tone, "--profile", "ivr-telephony", "--json"])
        a = json.loads(proc.stdout) if ok else {}
        rate_check = next((c for c in a.get("checks", []) if c["name"] == "sample_rate"), {})
        record("profile conformance flags 16 kHz vs 8 kHz target",
               ok and a.get("verdict") == "fail" and rate_check.get("status") == "fail")

        proc, ok = run_cli(["audio-check", tone + ".missing"], expect_code=2)
        record("audio-check rejects missing file (exit 2)", ok)

        # --- transcripts / consent ----------------------------------------------
        proc, ok = run_cli(["transcript-check", srt_ok, "--json"])
        t = json.loads(proc.stdout) if ok else {}
        record("transcript-check accepts valid SRT", ok and t.get("verdict") == "pass")

        proc, ok = run_cli(["transcript-check", srt_bad, "--json"])
        t = json.loads(proc.stdout) if ok else {}
        record("transcript-check rejects backwards cue (fail)", ok and t.get("verdict") == "fail")

        proc, ok = run_cli(["consent-check", consent_ok, "--json"])
        c = json.loads(proc.stdout) if ok else {}
        record("consent-check accepts complete record", ok and c.get("verdict") == "pass")

        proc, ok = run_cli(["consent-check", consent_bad, "--json"])
        c = json.loads(proc.stdout) if ok else {}
        record("consent-check rejects incomplete record (fail)", ok and c.get("verdict") == "fail")

        # --- package workflow ---------------------------------------------------
        pkg = os.path.join(tmp, "pkg-standard")
        proc, ok = run_cli(["init-package", "--skill", "2.3", "--dir", pkg,
                            "--requester", "selftest", "--title", "Selftest package"])
        record("init-package scaffolds standard package", ok and os.path.isfile(os.path.join(pkg, "work-package.json")))

        pkg_el = os.path.join(tmp, "pkg-elevated")
        proc, ok = run_cli(["init-package", "--skill", "10.1", "--dir", pkg_el,
                            "--requester", "selftest"])
        record("init-package adds consent record for elevated tier",
               ok and os.path.isfile(os.path.join(pkg_el, "consent-record.json")))

        # fresh scaffold must FAIL validation (manifest has empty outputs, no approvals)
        proc, ok = run_cli(["validate-package", pkg, "--json"])
        v = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        record("validate-package fails on empty draft (expected)",
               proc.returncode in (0, 3) and v.get("verdict") in ("warn", "fail"))

        # complete the standard package with real artifacts
        shutil.copy(tone, os.path.join(pkg, "output.wav"))
        shutil.copy(srt_ok, os.path.join(pkg, "output.srt"))
        manifest = json.load(open(os.path.join(pkg, "work-package.json")))
        manifest["outputs"] = [
            {"name": "narration", "kind": "audio", "path": "output.wav"},
            {"name": "transcript", "kind": "transcript", "path": "output.srt"},
        ]
        manifest["approvals"] = [
            {"role": "reviewer", "approver": "Selftest Reviewer", "date": "2026-11-09", "decision": "approved"}
        ]
        manifest["status"] = "approved"
        with open(os.path.join(pkg, "work-package.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

        proc, ok = run_cli(["validate-package", pkg, "--json"])
        v = json.loads(proc.stdout) if ok else {}
        failing = [c for c in v.get("checks", []) if c["status"] == "fail"]
        record("validate-package passes completed package",
               proc.returncode == 0 and v.get("verdict") == "pass" and not failing)

        report_path = os.path.join(tmp, "qa-report.md")
        proc, ok = run_cli(["qa-report", pkg, "--output", report_path])
        report_ok = ok and os.path.isfile(report_path) and "Verdict" in open(report_path).read()
        record("qa-report renders Markdown sign-off", report_ok)

        # elevated package without consent must fail validation
        proc, _ = run_cli(["validate-package", pkg_el, "--json"])
        v = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        consent_check = next((c for c in v.get("checks", []) if c["name"] == "consent"), {})
        record("validate-package enforces elevated-tier consent gate",
               v.get("verdict") == "fail" and consent_check.get("status") == "fail")

    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("selftest artifacts preserved at: %s" % tmp)

    failed = [r for r in RESULTS if not r["ok"]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
