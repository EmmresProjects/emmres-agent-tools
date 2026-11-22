"""ai-video-creation / tools-selftest.py

Built-in validation suite invoked by:
    python3 ai-video-creation/tools-ai-video.py selftest

Exercises every command through the real CLI plus direct module calls: valid,
invalid, and missing inputs; all 564 contracts; catalog ingestion in three
formats; changed-input behavior; artifact parsing; approvals; retries;
cancellation; cleanup; recovery/resume; and clean-skill-root portability.

No pytest dependency, no network, no shell. Returns a structured result object.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
PRIMARY = TOOL_DIR / "tools-ai-video.py"


class Suite:
    def __init__(self, ctx, args):
        self.ctx = ctx
        self.args = args
        self.tests = []
        self._tmp = tempfile.TemporaryDirectory(prefix="avc-selftest-")
        self.base = Path(self._tmp.name)
        self.cwd = self.base / "work"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.C = ctx.deps["contracts"]
        self.K = ctx.deps["catalog"]
        self.P = ctx.deps["planner"]
        self.M = ctx.deps["media"]

    def close(self):
        self._tmp.cleanup()

    def check(self, name, fn):
        try:
            detail = fn()
            self.tests.append({"name": name, "status": "pass", "detail": str(detail)[:300]})
        except Exception as exc:
            self.tests.append({"name": name, "status": "fail",
                               "detail": "%s: %s" % (type(exc).__name__, exc)})

    def cli(self, argv, cwd=None, timeout=90):
        cp = subprocess.run([sys.executable, str(PRIMARY)] + [str(a) for a in argv],
                            cwd=str(cwd or self.cwd), capture_output=True, text=True,
                            timeout=timeout, check=False)
        try:
            env = json.loads(cp.stdout)
        except json.JSONDecodeError:
            env = {"status": "unparseable", "stdout": cp.stdout[-600:], "stderr": cp.stderr[-600:]}
        return cp.returncode, env

    def ok(self, argv, expect_status=None, cwd=None):
        rc, env = self.cli(argv, cwd=cwd)
        if env.get("status") == "unparseable":
            raise AssertionError("CLI output not JSON: %s" % env)
        if expect_status and env.get("status") != expect_status:
            raise AssertionError("status %s != %s for %s: %s" %
                                 (env.get("status"), expect_status, argv[:2], env.get("errors")))
        return rc, env

    def brief(self, **overrides):
        b = self.C.load_json(self.ctx.resources["example_brief"])
        b.update(overrides)
        return b

    def write(self, name, obj):
        p = self.cwd / name
        p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    def run(self):
        s = self
        C, K, P, M = self.C, self.K, self.P, self.M
        ctx = self.ctx

        # 1. help + capability-list through the real interface
        def t_help():
            rc, env = s.ok(["help"], "ok")
            cmds = env["data"]["commands"]
            assert rc == 0 and len(cmds) == 17 and cmds[0][0] == "help"
            return "17 commands, rc=0"
        s.check("cli-help", t_help)

        def t_caps():
            rc, env = s.ok(["capabilities", "--limit", "3"], "ok")
            d = env["data"]
            assert d["total"] == 564 and d["summary"]["categories"] == 47
            assert len(d["capabilities"]) == 3 and len(d["operations"]) == 9
            return "564 capabilities / 47 categories / %d operations" % len(d["operations"])
        s.check("cli-capability-list", t_caps)

        def t_caps_text():
            cp = subprocess.run([sys.executable, str(PRIMARY), "capabilities", "--categories-only",
                                 "--format", "text"], cwd=str(s.cwd), capture_output=True, text=True, timeout=60)
            assert cp.returncode == 0 and "Catalog Governance" in cp.stdout
            return "human-readable category index rendered"
        s.check("cli-capabilities-text", t_caps_text)

        def t_filters():
            _, env = s.ok(["capabilities", "--category", "Captioning", "--limit", "5"], "ok")
            assert env["data"]["total"] == 12
            _, env2 = s.ok(["capabilities", "--query", "Subtitle Timing", "--limit", "5"], "ok")
            assert env2["data"]["total"] >= 1
            _, env3 = s.ok(["capabilities", "--family", "delivery", "--limit", "5"], "ok")
            assert env3["data"]["total"] >= 1
            return "category/query/family filters work"
        s.check("capability-filters", t_filters)

        # 2. every catalog capability is reachable and has a real contract
        def t_all_contracts():
            caps = ctx.registry["capabilities"]
            assert len(caps) == 564
            for cap in caps:
                found, _ = K.find_capability(ctx.registry, cap["slug"])
                assert found and found["id"] == cap["id"], cap["slug"]
                contract = C.build_contract(cap, ctx.cfg, ctx.policy, ctx.gates_def)
                assert contract["trigger"] and contract["inputs"]["required"]
                assert contract["outputs"]["primary"] and len(contract["quality_gates"]) == 7
                assert contract["limits"]["stage_seconds"] and contract["human_approval"]["mechanism"]
            return "564/564 capability contracts built with 7 gates each"
        s.check("all-capability-contracts", t_all_contracts)

        def t_all_categories():
            for cat in ctx.registry["categories"]:
                matches, total = K.search_capabilities(ctx.registry, category=cat["name"], limit=1)
                assert total == 12 and matches, cat["name"]
                contract = C.build_contract(matches[0], ctx.cfg, ctx.policy, ctx.gates_def)
                assert contract["category"] == cat["name"]
            return "all 47 categories reachable, 12 capabilities each"
        s.check("all-categories-reachable", t_all_categories)

        def t_describe_cli():
            _, env = s.ok(["describe", "--capability", "c17-s01"], "ok")
            assert env["data"]["contract"]["capability_id"] == "c17-s01"
            _, bad = s.cli(["describe", "--capability", "does-not-exist"])
            assert bad["status"] == "error" and bad["errors"][0]["code"] == "CAPABILITY_NOT_FOUND"
            return "describe lookup + structured not-found error"
        s.check("describe-and-not-found", t_describe_cli)

        # 3. catalog ingestion: plain text, Markdown, copied HTML, single capability
        PLAIN = """AI VIDEO CREATION SKILL CATALOG
Page 6
Category 3: Text-to-Video
Generation
Category purpose: Creating original moving imagery from written descriptions.
1. Skill Name: Cinematic Text-to-Video Generator
Description: A reusable agent capability for creating original moving imagery from written
 descriptions. It accepts a brief plus relevant assets and constraints.
2. Skill Name: Short-Form Social Video Generator
Description: Creates short vertical clips for social feeds with clear captions.
Category 4: Image-to-Video and Keyframe Animation
Category purpose: Animating a supplied still image.
1. Skill Name: Keyframe Motion Planner
Description: Predicts camera movement from a keyframe and a motion brief.
Recommended Skill Contract
Trigger
State exactly when the skill should run and when it should defer to another capability.
"""
        def t_ingest_plain():
            reg, info = K.parse_catalog(PLAIN, source_name="plain.txt")
            assert K.validate_registry(reg) == [], K.validate_registry(reg)
            assert reg["categories"][0]["name"] == "Text-to-Video Generation", reg["categories"][0]["name"]
            assert "Trigger" not in reg["capabilities"][-1]["description"]
            names = [c["name"] for c in reg["capabilities"]]
            assert names == ["Cinematic Text-to-Video Generator", "Short-Form Social Video Generator",
                             "Keyframe Motion Planner"], names
            assert reg["categories"][0]["purpose"] == "Creating original moving imagery from written descriptions."
            assert "written descriptions. It accepts a brief" in reg["capabilities"][0]["description"]
            assert reg["capabilities"][0]["local_operation"] in ("contract", "compile-prompt")
            _, env = s.ok(["ingest", "--text", PLAIN, "--name", "text-to-video", "--output", "ingested.json"], "ok")
            assert env["data"]["stats"]["capabilities"] == 3
            saved = json.loads((s.cwd / "ingested.json").read_text())
            assert saved["umbrella"] == "text-to-video"
            _, reuse = s.ok(["capabilities", "--catalog", "ingested.json", "--limit", "5"], "ok")
            assert reuse["data"]["total"] == 3
            return "3 capabilities / 2 categories; registry saved and reused via --catalog"
        s.check("ingest-plain-text", t_ingest_plain)

        MARKDOWN = """# Coastal Video Skills
## Category: Prompt Engineering and Creative Briefing
Category purpose: Turning creative intent into precise instructions.
1. **Video Prompt Architect** — Builds a structured shot prompt from a creative brief.
2. **Negative Prompt Designer** — Defines exclusions and artifact guards for generation.
## Category: Captioning, Subtitles, and Accessibility
Category purpose: Making video understandable without sound.
2. **Subtitle Timing Aligner** — Aligns caption cue boundaries to shot edits.
"""
        def t_ingest_md():
            reg, info = K.parse_catalog(MARKDOWN, source_name="catalog.md")
            assert not K.validate_registry(reg), K.validate_registry(reg)
            assert len(reg["categories"]) == 2 and len(reg["capabilities"]) == 3
            names = [c["name"] for c in reg["capabilities"]]
            assert "Video Prompt Architect" in names and "Subtitle Timing Aligner" in names
            assert reg["capabilities"][2]["local_operation"] == "captions"
            return "markdown headings, bold names, numbering, and descriptions normalized"
        s.check("ingest-markdown", t_ingest_md)

        def t_ingest_html():
            raw = ctx.resource_text("catalog_fixture_html")
            clean = K.strip_markup(raw)
            assert "class=" not in clean and "data-index" not in clean and "ignored" not in clean
            reg, info = K.parse_catalog(raw, source_name="copy.html")
            assert not K.validate_registry(reg), K.validate_registry(reg)
            assert len(reg["categories"]) == 2 and len(reg["capabilities"]) == 4
            names = [c["name"] for c in reg["capabilities"]]
            assert "Subtitle Timing Aligner" in names and "Rights and Release Tracker" in names
            assert all("skill" not in c["description"].lower()[:12] for c in reg["capabilities"])
            _, env = s.ok(["ingest", "--input", str(C.resolve_resource(ctx.resources["catalog_fixture_html"])),
                           "--output", "html-registry.json"], "ok")
            assert env["data"]["stats"]["capabilities"] == 4
            return "HTML tags/classes/data-attrs/script text ignored; 4 capabilities extracted"
        s.check("ingest-copied-html", t_ingest_html)

        def t_ingest_single():
            text = "Skill Name: Prompt Ambiguity Auditor\nDescription: Flags vague creative language in a brief."
            reg, info = K.parse_catalog(text, umbrella="single-capability", source_name="inline")
            assert reg["umbrella"] == "single-capability" and len(reg["capabilities"]) == 1
            assert reg["capabilities"][0]["name"] == "Prompt Ambiguity Auditor"
            return "single capability unified under a lowercase-hyphenated umbrella"
        s.check("ingest-single-capability", t_ingest_single)

        # 4. validation: valid, invalid, missing, structured errors
        def t_validate():
            brief_path = str(C.resolve_resource(ctx.resources["example_brief"]))
            _, good = s.ok(["validate", "--input", brief_path, "--output", "valid-report.json"], "ok")
            assert good["data"]["status"] == "pass" and good["data"]["error_count"] == 0
            bad = {"title": "", "objective": "x", "fps": 77, "resolution": "wide",
                   "captions_source": "../../etc/passwd", "api_key": "should-not-be-embedded"}
            _, env = s.cli(["validate", "--text", json.dumps(bad)])
            codes = {e["code"] for e in env.get("errors", [])}
            assert env["status"] == "error" and {"EMBEDDED_SECRET", "UNSAFE_PATH", "TYPE_MISMATCH", "ENUM_MISMATCH"} & codes
            _, missing = s.cli(["validate", "--input", "no-such-file.json"])
            assert missing["status"] == "error" and missing["errors"][0]["code"] == "INPUT_NOT_FOUND"
            _, nonobj = s.cli(["validate", "--text", "[1, 2, 3]"])
            assert nonobj["status"] == "error" and nonobj["errors"][0]["code"] == "TYPE_MISMATCH"
            report = json.loads((s.cwd / "valid-report.json").read_text())
            assert report["status"] == "pass"
            return "valid pass, %d structured error codes, missing input and wrong-type recovery" % len(codes)
        s.check("validate-valid-invalid-missing", t_validate)

        # 5. changed input changes output
        def t_prompt_change():
            a = P.compile_prompt(s.brief(), ctx.style, ctx.cfg, variants=2)
            b_brief = s.brief(objective="A neon-lit product close-up of a reusable water bottle.",
                              style_preset="product", title="Bottle spot", seed=999)
            b = P.compile_prompt(b_brief, ctx.style, ctx.cfg, variants=2)
            assert a["variants"][0]["positive_prompt"] != b["variants"][0]["positive_prompt"]
            assert len(a["variants"]) == len(b["variants"]) == 2
            assert a["variants"][0]["seed"] != b["variants"][0]["seed"]
            vague = s.brief(style="something nice and good")
            audit = P.compile_prompt(vague, ctx.style, ctx.cfg)["ambiguity_audit"]
            assert audit["status"] == "needs-human-review" and audit["vague_terms"]
            _, env = s.ok(["prompt", "--text", json.dumps(b_brief), "--variants", "2",
                           "--output", "prompt.json", "--markdown", "prompt.md"], "ok")
            assert env["data"]["variants"] == 2
            assert "Positive" in (s.cwd / "prompt.md").read_text()
            assert json.loads((s.cwd / "prompt.json").read_text())["variants"][0]["positive_prompt"]
            return "different briefs -> different prompts/seeds; ambiguity audit; JSON+MD artifacts"
        s.check("prompt-change-input", t_prompt_change)

        def t_shotlist():
            sl = P.shotlist(s.brief(), ctx.style, ctx.cfg)
            assert sl["shot_count"] == 5 and sl["total_duration_seconds"] == 24
            assert sl["shots"][0]["in_timecode"] == "00:00:00:00"
            assert sl["shots"][-1]["out_timecode"].startswith("00:00:24:")
            assert "FROM CLIP NAME" in sl["edl"]
            _, env = s.ok(["shotlist", "--text", json.dumps(s.brief()), "--output", "shotlist.json",
                           "--markdown", "shotlist.md"], "ok")
            assert env["data"]["shot_count"] == 5
            md = (s.cwd / "shotlist.md").read_text()
            assert "| Shot |" in md and "Edit decision list" in md
            return "5 shots, ordered timecodes, EDL, JSON+MD artifacts"
        s.check("shotlist-and-edl", t_shotlist)

        # 6. captions: parse, validate overlap, normalize, reparse
        def t_captions():
            raw = ctx.resource_text("example_captions")
            cues, fmt = M.parse_captions(raw)
            assert fmt == "srt" and len(cues) == 4
            rep = M.validate_captions(cues, ctx.cfg)
            assert rep["status"] == "pass", rep["issues"]
            overlap = "1\n00:00:00,000 --> 00:00:05,000\nFirst cue.\n\n2\n00:00:03,000 --> 00:00:08,000\nOverlapping cue.\n"
            cues2, _ = M.parse_captions(overlap)
            rep2 = M.validate_captions(cues2, ctx.cfg)
            assert any(i["code"] == "OVERLAP" for i in rep2["issues"])
            vtt = M.to_vtt(cues)
            assert vtt.startswith("WEBVTT")
            assert len(M.parse_captions(vtt)[0]) == 4
            assert len(M.parse_captions(M.to_srt(cues))[0]) == 4
            _, env = s.cli(["captions", "--text", overlap, "--output", "cap-report.json",
                            "--srt", "captions.srt", "--vtt", "captions.vtt"])
            assert env["status"] == "error" and (s.cwd / "captions.srt").is_file()
            _, env2 = s.ok(["captions", "--input", str(C.resolve_resource(ctx.resources["example_captions"])),
                            "--output", "cap-report-good.json", "--srt", "captions-good.srt"], "ok")
            assert env2["data"]["cue_count"] == 4
            return "SRT/VTT/JSON roundtrip, overlap detection, normalized outputs"
        s.check("captions-parse-validate", t_captions)

        # 7. quality gates pass and fail
        def t_gates():
            brief = s.brief()
            job = {"brief": brief, "provider": "local"}
            artifacts = {
                "prompt": P.compile_prompt(brief, ctx.style, ctx.cfg, variants=1),
                "shotlist": P.shotlist(brief, ctx.style, ctx.cfg),
                "captions": M.validate_captions(M.parse_captions(ctx.resource_text("example_captions"))[0], ctx.cfg),
                "validation": {"status": "pass"},
            }
            rep = M.evaluate_gates(job, ctx.gates_def, ctx.cfg, artifacts, {"verified": True})
            assert rep["status"] == "pass", [g for g in rep["gates"] if g["status"] == "fail"]
            job2 = {"brief": {**brief, "captions_required": True, "fps": 77}, "provider": "local"}
            rep2 = M.evaluate_gates(job2, ctx.gates_def, ctx.cfg, {k: v for k, v in artifacts.items() if k != "captions"})
            assert rep2["status"] == "fail" and "accessibility" in rep2["failed"]
            # CLI path with real artifact files
            s.ok(["prompt", "--text", json.dumps(brief), "--output", "gate-prompt.json"], "ok")
            s.ok(["shotlist", "--text", json.dumps(brief), "--output", "gate-shotlist.json"], "ok")
            s.ok(["captions", "--input", str(C.resolve_resource(ctx.resources["example_captions"])),
                  "--output", "gate-captions.json"], "ok")
            s.ok(["validate", "--text", json.dumps(brief), "--output", "gate-validation.json"], "ok")
            _, env = s.ok(["gates", "--text", json.dumps(job), "--artifact-prompt", "gate-prompt.json",
                           "--artifact-shotlist", "gate-shotlist.json", "--artifact-captions", "gate-captions.json",
                           "--artifact-validation", "gate-validation.json", "--output", "gate-report.json"], "ok")
            assert env["data"]["status"] == "pass"
            return "7 gates evaluated; pass and fail paths plus CLI artifact wiring"
        s.check("quality-gates", t_gates)

        # 8. plan + routing
        def t_plan():
            brief = s.brief()
            rows, meta = P.route(ctx.registry, brief, ctx.cfg, brief.get("capabilities"))
            assert rows and rows[0]["score"] >= rows[-1]["score"]
            plan = P.build_plan(ctx.registry, brief, ctx.cfg, ctx.policy, rows, gates=ctx.gates_def)
            assert len(plan["stage_order"]) == 9 and plan["stage_order"][0] == "intake"
            assert plan["job_id"] and plan["limits"]["job_seconds"] == 180
            _, env = s.ok(["plan", "--input", str(C.resolve_resource(ctx.resources["example_brief"])),
                           "--output", "plan.json"], "ok")
            assert env["data"]["plan"]["job_id"] == plan["job_id"]
            _, routed = s.ok(["route", "--text", json.dumps(brief), "--output", "route.json"], "ok")
            assert routed["data"]["matches"]
            return "deterministic 9-stage plan, ranked routing, JSON artifacts"
        s.check("route-and-plan", t_plan)

        # 9. run: dry-run, execute, artifacts, report
        def t_run_dry():
            _, env = s.ok(["run", "--input", "plan.json", "--output-dir", "run-dry", "--mode", "dry-run"], "ok")
            assert env["data"]["status"] == "simulated" and len(env["data"]["simulated"]) == 9
            assert not list((s.cwd / "run-dry").glob("stage-*.json"))
            assert (s.cwd / "run-dry" / "checkpoint.json").is_file()
            return "9 simulated stages; no stage artifacts written in dry-run"
        s.check("run-dry-run", t_run_dry)

        def t_run_execute():
            _, env = s.ok(["run", "--input", "plan.json", "--output-dir", "run-local",
                           "--mode", "execute", "--provider", "local"], "ok")
            d = env["data"]
            assert d["status"] == "completed" and len(d["completed"]) == 9, d
            outdir = s.cwd / "run-local"
            expected = ["stage-plan.json", "stage-prompt.json", "stage-prompt.md", "stage-shotlist.json",
                        "stage-shotlist.md", "stage-contract.json", "stage-captions.json",
                        "stage-captions.srt", "stage-captions.vtt", "stage-validation.json",
                        "stage-gates.json", "stage-approval.json", "stage-delivery.json",
                        "checkpoint.json", "run-report.json"]
            for name in expected:
                p = outdir / name
                assert p.is_file(), name
                if p.suffix == ".json":
                    json.loads(p.read_text())
            man = json.loads((outdir / "stage-delivery.json").read_text())
            for f in man["files"]:
                data = (outdir / f["path"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == f["sha256"]
            _, rep = s.ok(["report", "--run-dir", "run-local", "--output", "report.json"], "ok")
            assert rep["data"]["summary"]["status"] == "completed"
            return "9 stages executed, %d artifacts parsed, checksums verified, report rendered" % len(expected)
        s.check("run-execute-local", t_run_execute)

        def t_run_single_capability():
            # ANY of the 564 capabilities can be executed as a one-stage job.
            _, gen = s.ok(["run", "--capability", "c03-s01", "--output-dir", "single-gen",
                           "--mode", "execute", "--provider", "local"], "ok")
            assert gen["data"]["status"] == "completed" and gen["data"]["completed"] == ["capability"]
            env = json.loads((s.cwd / "single-gen" / "stage-contract.json").read_text())
            assert env["status"] == "spec_ready" and "Cinematic" in env["contract"]["capability_name"]
            _, cap_run = s.ok(["run", "--capability", "c17-s01", "--output-dir", "single-caps",
                               "--mode", "execute", "--provider", "local"], "ok")
            assert cap_run["data"]["status"] == "completed"
            assert (s.cwd / "single-caps" / "stage-captions.srt").is_file()
            changed = {"title": "Variant brief for a single capability",
                       "objective": "Generate a different short clip for comparison.",
                       "duration_seconds": 8, "fps": 24, "resolution": "1920x1080",
                       "provider": "local"}
            _, changed_run = s.ok(["run", "--capability", "c03-s01", "--text", json.dumps(changed),
                                   "--output-dir", "single-gen-b", "--mode", "execute",
                                   "--provider", "local"], "ok")
            assert changed_run["data"]["job_id"] != gen["data"]["job_id"]
            _, blocked = s.cli(["run", "--capability", "c40-s01", "--output-dir", "single-sensitive",
                                "--mode", "execute", "--provider", "local"])
            assert blocked["status"] == "needs_approval"
            _, rec = s.ok(["approve", "--confirm", "--capability", "all",
                           "--reason", "sensitive single capability reviewed", "--store", "single-appr.json"], "ok")
            _, resumed = s.ok(["run", "--capability", "c40-s01", "--output-dir", "single-sensitive",
                               "--mode", "execute", "--provider", "local", "--approval-token",
                               rec["data"]["result"]["token"], "--store", "single-appr.json", "--resume"], "ok")
            assert resumed["data"]["status"] == "completed"
            return "direct execution for generation/caption/safety capabilities; changed brief changes job_id"
        s.check("run-single-capability", t_run_single_capability)

        def t_deliver():
            _, env = s.ok(["deliver", "--text", json.dumps(s.brief()), "--artifacts-dir", "run-local",
                           "--output", "delivery.json"], "ok")
            man = json.loads((s.cwd / "delivery.json").read_text())
            assert man["file_count"] >= 10 and man["total_bytes"] > 0
            assert man["encoding"]["container"] == "mp4"
            assert man["captions_present"]
            _, env2 = s.ok(["deliver", "--text", json.dumps(s.brief(delivery_preset="shorts-vertical")),
                            "--artifacts-dir", "run-local"], "ok")
            assert env2["data"]["manifest"]["preset"] == "shorts-vertical"
            return "manifest checksums + changed delivery preset"
        s.check("delivery-manifest", t_deliver)

        # 10. approval gate + recovery
        def t_approval():
            pub = s.brief(publish=True, title="Publish approval drill")
            rows, _ = P.route(ctx.registry, pub, ctx.cfg, pub.get("capabilities"))
            plan = P.build_plan(ctx.registry, pub, ctx.cfg, ctx.policy, rows, gates=ctx.gates_def)
            p = s.write("plan-publish.json", plan)
            blocked = [st for st in plan["stages"] if st["approval_required"]]
            assert blocked, "expected sensitive stages"
            _, env = s.cli(["run", "--input", str(p), "--output-dir", "run-publish",
                            "--mode", "execute", "--provider", "local"])
            assert env["status"] == "needs_approval", env
            assert env["data"]["completed"], "earlier successful stages must be preserved"
            assert env["recovery"] and "approve" in env["recovery"]
            # scoped token for an unrelated capability must be rejected
            _, other = s.ok(["approve", "--confirm", "--capability", "c01-s01", "--reason", "wrong scope",
                             "--store", "appr.json"], "ok")
            _, rejected = s.cli(["run", "--input", str(p), "--output-dir", "run-publish", "--mode", "execute",
                                 "--provider", "local", "--approval-token", other["data"]["result"]["token"],
                                 "--store", "appr.json", "--resume"])
            assert rejected["status"] == "needs_approval"
            _, rec = s.ok(["approve", "--confirm", "--capability", "all", "--reason", "reviewed publish scope",
                           "--store", "appr.json"], "ok")
            token = rec["data"]["result"]["token"]
            _, resumed = s.ok(["run", "--input", str(p), "--output-dir", "run-publish", "--mode", "execute",
                               "--provider", "local", "--approval-token", token, "--store", "appr.json",
                               "--resume"], "ok")
            assert resumed["data"]["status"] == "completed" and resumed["data"]["skipped"]
            _, listing = s.ok(["approve", "--list", "--store", "appr.json"], "ok")
            assert len(listing["data"]["result"]["approvals"]) == 2
            return "approval gate blocked %d sensitive stages; scope enforced; wildcard+resume completed" % len(blocked)
        s.check("approval-and-resume", t_approval)

        # 11. provider failure -> recovery
        def t_provider_recovery():
            _, env = s.cli(["run", "--input", "plan.json", "--output-dir", "run-provider",
                            "--mode", "execute", "--provider", "remote-x"])
            assert env["data"]["failed"]["code"] == "PROVIDER_UNAVAILABLE"
            assert env["recovery"] and "--resume" in env["recovery"]
            _, fixed = s.ok(["run", "--input", "plan.json", "--output-dir", "run-provider",
                             "--mode", "execute", "--provider", "local", "--resume"], "ok")
            assert fixed["data"]["status"] == "completed"
            cp = json.loads((s.cwd / "run-provider" / "checkpoint.json").read_text())
            assert cp["earliest_failure"]["code"] == "PROVIDER_UNAVAILABLE" and cp["status"] == "completed"
            return "unconfigured provider failed safely; changed provider + resume completed"
        s.check("recovery-provider-unavailable", t_provider_recovery)

        # 12. retry / fault injection / cleanup / resume
        def t_retry_recovery():
            cfg = {"testing": {"allow_fault_injection": True}}
            cfgp = s.write("test-config.json", cfg)
            _, env = s.cli(["run", "--input", "plan.json", "--output-dir", "run-fault", "--mode", "execute",
                            "--provider", "local", "--config", str(cfgp), "--test-mode",
                            "--fail-stage", "assurance"])
            assert env["data"]["failed"]["code"] == "INJECTED_STAGE_ERROR"
            assert env["data"]["failed"]["attempts"] == 3
            assert not list((s.cwd / "run-fault").glob("*.partial"))
            done_before = len(env["data"]["completed"])
            assert done_before == 6, done_before
            _, resumed = s.ok(["run", "--input", "plan.json", "--output-dir", "run-fault", "--mode", "execute",
                               "--provider", "local", "--config", str(cfgp), "--resume"], "ok")
            assert resumed["data"]["status"] == "completed" and len(resumed["data"]["skipped"]) == 6
            return "3 bounded attempts, .partial cleanup, 6 stages preserved on resume"
        s.check("recovery-retry-cleanup", t_retry_recovery)

        # 13. cancellation
        def t_cancel():
            d = s.cwd / "run-cancel"
            d.mkdir(parents=True, exist_ok=True)
            (d / "CANCEL").write_text("stop", encoding="utf-8")
            _, env = s.cli(["run", "--input", "plan.json", "--output-dir", "run-cancel",
                            "--mode", "execute", "--provider", "local"])
            assert env["data"]["cancelled"] and env["recovery"]
            (d / "CANCEL").unlink()
            _, resumed = s.ok(["run", "--input", "plan.json", "--output-dir", "run-cancel",
                               "--mode", "execute", "--provider", "local", "--resume"], "ok")
            assert resumed["data"]["status"] == "completed"
            return "cancellation honored; removing CANCEL and resuming completed"
        s.check("cancellation-and-resume", t_cancel)

        # 14. doctor + folder structure + clean skill root portability
        def t_doctor():
            _, env = s.ok(["doctor"], "ok")
            assert env["data"]["status"] == "ok", [c for c in env["data"]["checks"] if c["status"] == "fail"]
            return "%d environment checks passed" % len(env["data"]["checks"])
        s.check("doctor", t_doctor)

        def t_structure():
            folder = TOOL_DIR
            files = sorted(p.name for p in folder.iterdir() if p.is_file())
            subs = [p.name for p in folder.iterdir() if p.is_dir()]
            assert not subs, subs
            assert "SKILL.md" not in files
            knowledge = [f for f in files if f.startswith("knowledge-")]
            assert knowledge == ["knowledge-readme.md"], knowledge
            assert all(f.startswith(("tools-", "knowledge-", "asset-")) for f in files), files
            assert not [f for f in files if f.lower().endswith((".zip", ".tar", ".gz", ".7z"))]
            docs = (folder / "knowledge-readme.md").read_text(encoding="utf-8")
            for ref in ctx.cfg["resources"].values():
                assert ref in docs, "doc missing reference %s" % ref
                assert C.resolve_resource(ref).is_file(), ref
            return "%d flat prefixed files; 1 knowledge file; all %d resource refs resolve" % (
                len(files), len(ctx.cfg["resources"]))
        s.check("folder-structure-and-references", t_structure)

        def t_clean_root():
            root = s.base / "clean-skill-root"
            root.mkdir(parents=True, exist_ok=True)
            (root / "SKILL.md").write_text("# Test parent skill\n", encoding="utf-8")
            dest = root / TOOL_DIR.name
            import shutil
            shutil.copytree(TOOL_DIR, dest)
            assert (root / "SKILL.md").is_file() and not (dest / "SKILL.md").exists()
            _, env = s.ok(["doctor"], "ok", cwd=root)
            assert env["data"]["status"] == "ok"
            cp = subprocess.run([sys.executable, str(Path(TOOL_DIR.name) / "tools-ai-video.py"),
                                 "capabilities", "--limit", "1"], cwd=str(root), capture_output=True,
                                text=True, timeout=60)
            env2 = json.loads(cp.stdout)
            assert cp.returncode == 0 and env2["data"]["total"] == 564
            _, env3 = s.ok(["describe", "--capability", "c47-s12", "--root", str(dest)], "ok", cwd=root)
            assert env3["data"]["contract"]["capability_name"] == "Catalog Gap Detector"
            return "copied beside one test SKILL.md; relative CLI + --root both work"
        s.check("clean-skill-root-portability", t_clean_root)

        passed = sum(1 for t in self.tests if t["status"] == "pass")
        failed = len(self.tests) - passed
        result = {"kind": "selftest-suite", "passed": passed, "failed": failed,
                  "python": sys.version.split()[0], "tests": self.tests}
        self.close()
        return result


def run_all(ctx, args):
    suite = Suite(ctx, args)
    return suite.run()
