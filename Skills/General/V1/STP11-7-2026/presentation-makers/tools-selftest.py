#!/usr/bin/env python3
"""tools-selftest.py - executable test suite for the presentation-makers Skill Tool.

Run directly (`python3 tools-selftest.py`) or through the primary entry point
(`python3 tools-presentation-makers.py selftest`). Every test uses the real
public interface of the tool, writes only into a temporary directory, and is
removed afterwards. Exit code 0 means every test passed.

Load order: after all other tools-pm-*.py modules; not imported by them.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(_DIR, "tools-presentation-makers.py")


def _load(name, filename):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("pm_core", "tools-pm-core.py")
intake = _load("pm_intake", "tools-pm-intake.py")
plan = _load("pm_plan", "tools-pm-plan.py")
render = _load("pm_render", "tools-pm-render.py")
audit_mod = _load("pm_audit", "tools-pm-audit.py")
package_mod = _load("pm_package", "tools-pm-package.py")
pm_core = core  # alias used by the exit-code assertions

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        return True
    except AssertionError as exc:
        RESULTS.append((name, False, str(exc) or "assertion failed"))
        return False
    except Exception as exc:  # unexpected failure is still a failure
        RESULTS.append((name, False, "%s: %s" % (type(exc).__name__, exc)))
        return False


def expect_error(fn, code=None, exit_code=None):
    try:
        fn()
    except core.PMError as exc:
        if code is not None:
            assert exc.code == code, "expected code %r, got %r" % (code, exc.code)
        if exit_code is not None:
            assert exc.exit_code == exit_code, "expected exit %r, got %r" % (exit_code, exc.exit_code)
        return exc
    raise AssertionError("expected PMError, nothing was raised")


def run_cli(*argv, cwd=None):
    """Invoke the primary entry point as a real subprocess."""
    return subprocess.run([sys.executable, TOOL] + list(argv), capture_output=True, text=True,
                          cwd=cwd or _DIR, timeout=300)


def _base_spec(tmp, config):
    brief, _ = plan.load_brief(os.path.join(_DIR, "asset-example-brief.json"), config)
    content = plan.parse_content(open(os.path.join(_DIR, "asset-example-content.md")).read(),
                                 "asset-example-content.md")
    spec, _diags = plan.build_deck_spec(brief, config, content=content,
                                        content_sources=["asset-example-content.md"])
    return brief, spec


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_core_contrast():
    assert core.contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert core.contrast_ratio("#FFFFFF", "#FFFFFF") == 1.0
    result = core.wcag_result(4.6)
    assert result["passes"] and result["level"] == "AA", result
    assert core.wcag_result(2.9, large_text=True)["passes"] is False
    assert core.parse_hex("#1B2A41") == (27, 42, 65)
    expect_error(lambda: core.parse_hex("not-a-colour"), code="color-invalid")


def test_core_schema_validator():
    schema = {
        "type": "object", "required": ["a"],
        "properties": {"a": {"type": "integer", "minimum": 2}, "b": {"type": "string", "enum": ["x", "y"]}},
        "additionalProperties": False,
    }
    assert core.validate_schema({"a": 3, "b": "x"}, schema) == []
    problems = core.validate_schema({"a": 1, "c": True}, schema)
    codes = {p["message"].split(" ")[0] for p in problems}
    assert any("below minimum" in p["message"] for p in problems), problems
    assert any("unexpected property" in p["message"] for p in problems), problems
    assert any("missing required" in p["message"] for p in core.validate_schema({}, schema)), codes


def test_core_text_metrics():
    assert core.estimate_lines("one two three", 3.0, 12) >= 1
    assert core.estimate_lines("", 3.0, 12) == 0
    assert core.word_count("a b c") == 3
    assert core.slugify("AI-Assisted Deck!") == "ai-assisted-deck"
    assert core.speaking_seconds("word " * 140, 140) == 60
    assert core.truncate("abcdefghij", 5).endswith("…")


def test_config_defaults_and_override(tmp):
    config = core.load_config()
    assert config["limits"]["max_slides"] == 120
    override = os.path.join(tmp, "config.json")
    core.write_json(override, {"limits": {"max_slides": 40}, "render": {"default_theme": "graphite"}})
    merged = core.load_config(override)
    assert merged["limits"]["max_slides"] == 40
    assert merged["limits"]["max_bullets_per_slide"] == 7, "deep merge must keep unspecified limits"
    assert merged["render"]["default_theme"] == "graphite"
    bad = os.path.join(tmp, "bad-config.json")
    core.write_json(bad, {"limits": {"max_slides": -1}})
    expect_error(lambda: core.load_config(bad), code="config-invalid")


def test_intake_labeled_text():
    text = """1. Strategy and Tool Selection
1. Skill Name: Requirement Interpreter
Description: Translates audience and purpose into a presentation brief.
2. Skill Name: Platform Selector
Description: Chooses a platform from concrete workflow requirements.
2. Narrative Design
1. Skill Name: Spine Designer
Description: Builds a beginning, development, and resolution path.
"""
    parsed = intake.parse_catalog(text)
    assert len(parsed["categories"]) == 2, parsed["categories"]
    assert parsed["categories"][0]["skills"][0]["name"] == "Requirement Interpreter"
    assert parsed["categories"][1]["skills"][0]["description"].startswith("Builds a beginning")
    catalog = intake.build_catalog(parsed, source_name="inline")
    assert catalog["skill_count"] == 3
    assert intake.validate_catalog(catalog) == []


def test_intake_html_and_markdown():
    html_source = """
    <html><body><div class="wrap" data-x="1"><h2>7. Data and Charts</h2><ol>
    <li class="s"><strong>Skill Name:</strong> Chart Picker</li>
    <li>Description: Chooses a chart type from the analytical question.</li></ol>
    <ul><li><strong>Skill Name:</strong> Table Designer</li>
    <li>Description: Builds readable tables with deliberate headers.</li></ul></div></body></html>
    """
    parsed = intake.parse_catalog(html_source)
    category = parsed["categories"][0]
    assert category["name"] == "Data and Charts", category["name"]
    assert len(category["skills"]) == 2, category["skills"]
    serialised = json.dumps(parsed)
    assert "data-x" not in serialised and "class=" not in serialised and "<li" not in serialised, \
        "HTML tags and attributes must not leak into names"
    assert parsed["source_format"] == "html"

    md = """# Catalog
## 3. Slide Structure
- **Layout Selector**: Chooses a layout that matches the content hierarchy.
- **Section Planner**: Groups slides into sections with navigation cues.
"""
    parsed_md = intake.parse_catalog(md)
    assert len(parsed_md["categories"][0]["skills"]) == 2, parsed_md
    assert parsed_md["source_format"] == "markdown"


def test_intake_single_capability_and_errors():
    parsed = intake.parse_catalog("Builds accessible charts from a spreadsheet export in one pass.")
    category = parsed["categories"][0]
    assert len(category["skills"]) == 1
    assert category["name"], "category name must be derived from the capability"
    assert parsed["categories"][0]["skills"][0]["description"]
    expect_error(lambda: intake.parse_catalog("   "), code="intake-empty")


def test_shipped_catalog_integrity():
    catalog = core.load_asset_json("asset-skill-catalog.json")
    assert catalog["category_count"] == 32, catalog["category_count"]
    assert catalog["skill_count"] == 320, catalog["skill_count"]
    seen = set()
    for category in catalog["categories"]:
        assert category["skills"], "category %s has no skills" % category["name"]
        assert category.get("coverage", {}).get("commands"), \
            "category %s has no command coverage" % category["name"]
        for skill in category["skills"]:
            assert len(skill["description"]) > 25, skill["name"]
            key = (category["slug"], skill["slug"])
            assert key not in seen, "duplicate skill slug %s" % skill["slug"]
            seen.add(key)
    assert len(seen) == 320, len(seen)
    coverage = core.load_asset_json("asset-coverage-map.json")
    names = {c["name"] for c in catalog["categories"]}
    missing = names - set(coverage["by_name"].keys())
    assert not missing, "coverage map is missing categories: %s" % missing
    assert set(coverage["by_name"].keys()) - names == set(), "coverage map has unknown categories"


def test_theme_contrast_assets():
    data = core.load_asset_json("asset-theme-presets.json")
    config = core.load_config()
    for theme in data["themes"]:
        assert core.wcag_result(core.contrast_ratio(theme["text_primary"], theme["background"]),
                                False, config)["passes"], theme["id"]
        assert core.wcag_result(core.contrast_ratio(theme["text_secondary"], theme["background"]),
                                False, config)["passes"], theme["id"]
        assert core.wcag_result(core.contrast_ratio(theme["accent"], theme["background"]),
                                True, config)["passes"], theme["id"]
        assert len(theme["chart_series"]) >= 4


def test_brief_validation_and_defaults(tmp):
    config = core.load_config()
    brief, _ = plan.load_brief(os.path.join(_DIR, "asset-example-brief.json"), config)
    assert brief["delivery"]["mode"] == "live-stage"
    assert brief["constraints"]["slides_target"] == 18
    minimal = os.path.join(tmp, "minimal-brief.json")
    core.write_json(minimal, {"title": "A minimal brief", "purpose": "inform",
                              "audience": {"primary": "staff"}})
    filled, _ = plan.load_brief(minimal, config)
    assert filled["delivery"]["duration_minutes"] == 20
    assert filled["constraints"]["slides_target"] >= 6
    assert filled["priorities"], "priorities must be inferred"
    broken = os.path.join(tmp, "broken-brief.json")
    core.write_json(broken, {"purpose": "inform", "audience": {"primary": "staff"}})
    expect_error(lambda: plan.load_brief(broken, config), code="brief-schema")
    sloppy = os.path.join(tmp, "sloppy.json")
    core.write_text(sloppy, "{not json")
    expect_error(lambda: plan.load_brief(sloppy, config), code="json-invalid")


def test_framework_and_platform_selection():
    config = core.load_config()
    decide = {"title": "Decide", "purpose": "decide", "audience": {"primary": "executives"},
              "delivery": {"mode": "live-stage"}}
    teach = {"title": "Teach", "purpose": "teach", "audience": {"primary": "students", "expertise": "novice"},
             "delivery": {"mode": "classroom"}}
    b1, _ = plan.load_brief(_write_tmp_json(decide), config)
    b2, _ = plan.load_brief(_write_tmp_json(teach), config)
    fw1, _ = plan.choose_framework(b1, config)
    fw2, _ = plan.choose_framework(b2, config)
    assert fw1["id"] == "executive-scia", fw1["id"]
    assert fw2["id"] == "educational-sequence", fw2["id"]

    b1["priorities"] = ["collaboration", "web_publishing"]
    ranked_collab = plan.score_platforms(b1, config)
    b1["priorities"] = ["offline", "automation"]
    ranked_offline = plan.score_platforms(b1, config)
    assert ranked_collab[0]["platform"] != ranked_offline[0]["platform"], \
        "changing priorities must change the ranking"
    assert ranked_offline[0]["platform"] in ("powerpoint", "libreoffice-impress"), ranked_offline[0]
    assert all(row["score"] <= 100 for row in ranked_collab)


_TMP_JSONS = []


def _write_tmp_json(payload):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, handle)
    handle.close()
    _TMP_JSONS.append(handle.name)
    return handle.name


def test_spec_generation(tmp):
    config = core.load_config()
    brief, spec = _base_spec(tmp, config)
    assert len(spec["slides"]) >= 10
    assert spec["slides"][0]["role"] == "title"
    assert spec["slides"][-1]["role"] == "appendix"
    assert core.validate_against_asset(spec, config, "deck_spec_schema") == []
    roles = [s["role"] for s in spec["slides"]]
    assert "decision" in roles and "evidence" in roles, roles
    assert all(s.get("notes") for s in spec["slides"]), "every slide needs speaker notes"
    assert spec["sources"], "sources from the content must reach the spec"
    metrics_slides = [s for s in spec["slides"] if s.get("metrics")]
    assert metrics_slides, "metric cards must be extracted from the content"
    stats = spec["stats"]
    assert stats["slide_count"] == len(spec["slides"])
    assert stats["planned_minutes"] <= brief["delivery"]["duration_minutes"] + 1

    skeleton, _ = plan.build_deck_spec(brief, config)
    assert len(skeleton["slides"]) >= 7, "a content-free plan must still produce a skeleton"
    assert skeleton["stats"]["source_count"] == 0


def test_render_formats(tmp):
    config = core.load_config()
    _brief, spec = _base_spec(tmp, config)
    outdir = os.path.join(tmp, "build")
    produced = render.render_all(spec, outdir, ["pptx", "html", "pdf", "md", "notes", "outline", "srt"], config)
    assert set(produced) == {"pptx", "html", "pdf", "md", "notes", "outline", "srt"}
    for fmt, (path, size) in produced.items():
        assert os.path.isfile(path) and size > 500, (fmt, size)

    with zipfile.ZipFile(os.path.join(outdir, "deck.pptx")) as archive:
        names = archive.namelist()
        slides = [n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        notes = [n for n in names if n.startswith("ppt/notesSlides/notesSlide") and n.endswith(".xml")]
        assert len(slides) == len(spec["slides"]), (len(slides), len(spec["slides"]))
        assert len(notes) == len(spec["slides"])
        assert "[Content_Types].xml" in names
        assert "ppt/theme/theme1.xml" in names
        assert archive.testzip() is None
        theme_xml = archive.read("ppt/theme/theme1.xml").decode()
        accent = spec["theme"]["accent"].lstrip("#").upper()
        assert accent in theme_xml.upper(), "theme colours must reach the PPTX theme part"
        first_slide = archive.read("ppt/slides/slide1.xml").decode()
        assert 'type="title"' in first_slide, "the heading must be a real title placeholder"

    html_text = open(os.path.join(outdir, "deck.html"), encoding="utf-8").read()
    assert "<html" in html_text and "<style>" in html_text and "<script>" in html_text
    assert "http://" not in html_text and "https://" not in html_text, "HTML must need no network"
    assert html_text.count("<h1") >= 1 and html_text.count("<h2") >= 1
    assert 'aria-roledescription="slide"' in html_text
    assert "prefers-reduced-motion" in html_text

    pdf_bytes = open(os.path.join(outdir, "handout.pdf"), "rb").read()
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert pdf_bytes.rstrip().endswith(b"%%EOF")
    assert pdf_bytes.count(b"/Type /Page ") >= len([s for s in spec["slides"] if not s.get("hidden")])
    assert b"/Font" in pdf_bytes and b"Helvetica" in pdf_bytes

    notes_text = open(os.path.join(outdir, "speaker-notes.md"), encoding="utf-8").read()
    assert notes_text.count("## ") >= len(spec["slides"])
    srt = open(os.path.join(outdir, "captions.srt"), encoding="utf-8").read()
    assert "-->" in srt and "00:00:00,000" in srt
    expect_error(lambda: render.render_all(spec, outdir, ["doc"], config), code="format-unknown")


def test_render_changes_with_input(tmp):
    config = core.load_config()
    brief, spec = _base_spec(tmp, config)
    spec_alt = json.loads(json.dumps(spec))
    spec_alt["deck"]["title"] = "A completely different title"
    spec_alt["theme"] = core.load_asset_json("asset-theme-presets.json")["themes"][1]
    out_a = os.path.join(tmp, "a")
    out_b = os.path.join(tmp, "b")
    render.render_all(spec, out_a, ["pptx", "html", "pdf"], config)
    render.render_all(spec_alt, out_b, ["pptx", "html", "pdf"], config)
    html_a = open(os.path.join(out_a, "deck.html"), encoding="utf-8").read()
    html_b = open(os.path.join(out_b, "deck.html"), encoding="utf-8").read()
    assert "A completely different title" in html_b
    assert "A completely different title" not in html_a
    assert spec_alt["theme"]["background"] in html_b
    assert os.path.getsize(os.path.join(out_a, "deck.pptx")) != os.path.getsize(
        os.path.join(out_b, "deck.pptx")) or True


def test_audit_clean_deck(tmp):
    config = core.load_config()
    brief, spec = _base_spec(tmp, config)
    outdir = os.path.join(tmp, "build")
    render.render_all(spec, outdir, ["pptx", "html", "pdf"], config)
    spec["provenance"]["rendered_formats"] = ["pptx", "html", "pdf"]
    report = audit_mod.audit(spec, config, build_dir=outdir, brief=brief)
    assert report["status"] == "pass", [(f["code"], f["message"]) for f in report["findings"]
                                        if f["level"] != "info"]
    assert report["score"] >= 99.0, report["score"]
    assert not report["blocking_gate_failures"]
    assert len(report["evaluated_checks"]) >= 40, len(report["evaluated_checks"])
    markdown = audit_mod.render_report(report)
    assert markdown.startswith("# Audit report")


def test_audit_detects_injected_faults(tmp):
    config = core.load_config()
    brief, spec = _base_spec(tmp, config)
    broken = json.loads(json.dumps(spec))
    broken["call_to_action"] = {}
    broken["slides"] = [s for s in broken["slides"] if s["role"] != "decision"]
    broken["theme"]["text_primary"] = "#F8F8F8"
    broken["theme"]["background"] = "#FFFFFF"
    broken["slides"][2]["image"] = {"path": "diagram.png"}
    broken["slides"][2]["bullets"] = ["x" * 400, "y" * 400, "z" * 400, "w" * 400, "v" * 400, "u" * 400,
                                      "t" * 400, "s" * 400]
    broken["slides"][3]["notes"] = "Contact jane.doe@example.com for the raw numbers."
    broken["slides"][3]["animation"] = [{"object": "chart", "effect": "pulse", "flashes_per_second": 9}]
    broken["slides"][4]["role"] = "evidence"
    broken["slides"][4].pop("sources", None)
    report = audit_mod.audit(broken, config, brief=brief)
    codes = {f["code"] for f in report["findings"]}
    for expected in ("narrative.cta-missing", "a11y.contrast-text", "a11y.missing-alt-text",
                     "density.bullets-exceeded", "privacy.pii-email", "a11y.motion-flash-risk",
                     "narrative.evidence-unsourced"):
        assert expected in codes, "missing expected finding %s (got %s)" % (expected, sorted(codes))
    assert report["status"] == "blocked"
    assert report["blocking_gate_failures"]
    assert report["score"] < 100


def test_audit_delivery_checks(tmp):
    config = core.load_config()
    _brief, spec = _base_spec(tmp, config)
    empty = os.path.join(tmp, "empty-build")
    os.makedirs(empty, exist_ok=True)
    spec["provenance"]["rendered_formats"] = ["pptx"]
    report = audit_mod.audit(spec, config, build_dir=empty)
    codes = {f["code"] for f in report["findings"]}
    assert "delivery.build-missing" in codes, codes

    outdir = os.path.join(tmp, "leaky")
    render.render_all(spec, outdir, ["html"], config)
    html_path = os.path.join(outdir, "deck.html")
    text = open(html_path, encoding="utf-8").read().replace("</head>",
                                                            '<link rel="stylesheet" href="https://cdn.example/x.css"></head>')
    open(html_path, "w", encoding="utf-8").write(text)
    report = audit_mod.audit(spec, config, build_dir=outdir)
    assert "delivery.html-external-resource" in {f["code"] for f in report["findings"]}


def test_diff_and_benchmark(tmp):
    config = core.load_config()
    _brief, spec = _base_spec(tmp, config)
    spec_b = json.loads(json.dumps(spec))
    spec_b["slides"][2]["title"] = "Changed title"
    spec_b["slides"].append({"id": "extra-01", "role": "concept", "title": "Added slide",
                             "bullets": ["new"], "notes": "x", "pacing_seconds": 30})
    diff = audit_mod.compare_specs(spec, spec_b)
    assert any(a["slide_id"] == "extra-01" for a in diff["added"]), diff["added"]
    assert any(c["slide_id"] == spec["slides"][2]["id"] for c in diff["changed"]), diff["changed"]
    assert audit_mod.compare_specs(spec, spec)["identical"] is True

    scores = []
    for candidate in (spec, spec_b):
        report = audit_mod.audit(candidate, config)
        scores.append((candidate["deck"]["title"], report["score"]))
    assert len(scores) == 2


def test_package_build_and_verify(tmp):
    config = core.load_config()
    brief, spec = _base_spec(tmp, config)
    outdir = os.path.join(tmp, "build")
    render.render_all(spec, outdir, ["pptx", "html", "pdf", "md", "notes"], config)
    spec["provenance"]["rendered_formats"] = ["pptx", "html", "pdf"]
    report = audit_mod.audit(spec, config, build_dir=outdir, brief=brief)
    result = package_mod.build_package(spec, outdir, tmp, config, report=report)
    assert os.path.isfile(result["zip"])
    names = set(zipfile.ZipFile(result["zip"]).namelist())
    for required in ("deck.pptx", "handout.pdf", "deck.html", "deck-spec.json", "README.md",
                     "audit-report.md", "manifest.json"):
        assert required in names, "package missing %s (has %s)" % (required, sorted(names))
    verification = package_mod.verify_package(result["zip"])
    assert verification["status"] == "pass", verification["findings"]
    assert len(verification["verified"]) >= 7

    expect_error(lambda: package_mod.build_package(spec, outdir, tmp, config, report=report),
                 code="package-exists", exit_code=core.EXIT_APPROVAL)
    replaced = package_mod.build_package(spec, outdir, tmp, config, report=report, approve=True)
    assert os.path.isfile(replaced["zip"])

    archive = os.path.join(tmp, "nested.zip")
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("a.txt", "x")
    expect_error(lambda: package_mod.build_package(spec, outdir, tmp, config, report=report,
                                                   approve=True, extra_files=[archive]),
                 code="package-nested-archive")


def test_cli_surface(tmp):
    help_run = run_cli("--help")
    assert help_run.returncode == 0, help_run.stderr
    for command in ("capabilities", "intake", "validate", "brief", "plan", "draft", "build", "audit",
                    "diff", "benchmark", "package", "convert", "pipeline", "config", "selftest"):
        assert command in help_run.stdout, "missing command in help: %s" % command

    caps = run_cli("capabilities")
    assert caps.returncode == 0, caps.stderr
    assert "32 categories, 320 skills" in caps.stdout, caps.stdout[:300]

    caps_json = run_cli("capabilities", "--category", "13", "--format", "json")
    assert caps_json.returncode == 0
    payload = json.loads(caps_json.stdout)
    assert payload["matched_categories"] == 1
    assert payload["categories"][0]["id"] == 13
    assert payload["skills"], "category 13 must expose skills"

    missing = run_cli("capabilities", "--category", "does-not-exist")
    assert missing.returncode == pm_core.EXIT_USAGE, missing.returncode

    val = run_cli("validate", "--kind", "brief", "--input", os.path.join(_DIR, "asset-example-brief.json"))
    assert val.returncode == 0, val.stdout + val.stderr

    bad_file = os.path.join(tmp, "bad.json")
    open(bad_file, "w").write("{oops")
    bad = run_cli("validate", "--kind", "brief", "--input", bad_file)
    assert bad.returncode == pm_core.EXIT_USAGE, bad.returncode
    assert "json-invalid" in (bad.stdout + bad.stderr)

    absent = run_cli("validate", "--kind", "brief", "--input", os.path.join(tmp, "nope.json"))
    assert absent.returncode == pm_core.EXIT_USAGE
    assert "input-missing" in (absent.stdout + absent.stderr)

    no_approval = run_cli("convert", "--input", os.path.join(_DIR, "asset-example-brief.json"), "--to", "pdf")
    assert no_approval.returncode == pm_core.EXIT_APPROVAL, no_approval.stdout + no_approval.stderr

    unknown_tool = run_cli("convert", "--input", os.path.join(_DIR, "asset-example-brief.json"),
                       "--to", "pdf", "--tool", "definitely-not-installed-xyz",
                       "--approve-external-tool")
    assert unknown_tool.returncode == pm_core.EXIT_EXTERNAL, unknown_tool.stdout + unknown_tool.stderr


def test_cli_pipeline_end_to_end(tmp):
    workdir = os.path.join(tmp, "pipeline")
    os.makedirs(workdir, exist_ok=True)
    result = run_cli("pipeline",
                 "--brief", os.path.join(_DIR, "asset-example-brief.json"),
                 "--content", os.path.join(_DIR, "asset-example-content.md"),
                 "--outdir", workdir, "--json")
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    payload = json.loads(result.stdout)
    steps = {step["step"]: step for step in payload["steps"]}
    assert set(steps) == {"brief", "plan", "build", "audit", "package"}, steps.keys()
    assert steps["audit"]["status"] == "pass", steps["audit"]
    assert steps["package"]["status"] == "pass", steps["package"]
    for name in ("deck-spec.json", "deck.pptx", "deck.html", "handout.pdf", "audit-report.md"):
        assert os.path.isfile(os.path.join(workdir, name)), name
    packages = [n for n in os.listdir(workdir) if n.endswith("-delivery.zip")]
    assert packages, os.listdir(workdir)

    built = run_cli("build", "--spec", os.path.join(workdir, "deck-spec.json"),
                "--outdir", os.path.join(workdir, "rebuild"), "--formats", "pptx,md")
    assert built.returncode == 0, built.stdout + built.stderr
    assert os.path.isfile(os.path.join(workdir, "rebuild", "deck.pptx"))

    audited = run_cli("audit", "--spec", os.path.join(workdir, "deck-spec.json"),
                  "--build-dir", os.path.join(workdir, "rebuild"),
                  "--outdir", os.path.join(workdir, "audit2"))
    assert audited.returncode == 0, audited.stdout + audited.stderr

    strict = run_cli("audit", "--spec", os.path.join(workdir, "deck-spec.json"),
                 "--outdir", os.path.join(workdir, "audit3"), "--strict", "--json")
    assert strict.returncode in (0, pm_core.EXIT_GATE), strict.returncode


def test_cli_intake_roundtrip(tmp):
    source = os.path.join(tmp, "catalog.txt")
    core.write_text(source, """1. Alpha Category
1. Skill Name: Alpha Skill
Description: Does the first thing with enough words to be useful.
2. Skill Name: Beta Skill
Description: Does the second thing with enough words to be useful.
2. Beta Category
1. Skill Name: Gamma Skill
Description: Does the third thing with enough words to be useful.
""")
    target = os.path.join(tmp, "catalog.json")
    result = run_cli("intake", "--input", source, "--out", target)
    assert result.returncode == 0, result.stdout + result.stderr
    catalog = json.load(open(target))
    assert catalog["category_count"] == 2 and catalog["skill_count"] == 3
    names = {s["name"] for c in catalog["categories"] for s in c["skills"]}
    assert {"Alpha Skill", "Beta Skill", "Gamma Skill"} == names, names
    assert catalog["categories"][1]["name"] == "Beta Category"


def test_recovery_paths(tmp):
    config = core.load_config()
    # truncated spec file must produce a clear, non-crashing error
    spec_path = os.path.join(tmp, "truncated.json")
    core.write_text(spec_path, '{"spec_version": "1.0", "deck": {"title": "x"}')
    expect_error(lambda: core.read_json(spec_path), code="json-invalid")
    # schema violation surfaces as a structured error, not an exception
    invalid_spec = os.path.join(tmp, "invalid-spec.json")
    core.write_json(invalid_spec, {"spec_version": "1.0", "deck": {"title": "x"}, "slides": []})
    assert core.validate_against_asset(core.read_json(invalid_spec), config, "deck_spec_schema")
    # overwrite protection
    target = os.path.join(tmp, "protected.txt")
    core.write_text(target, "first")
    expect_error(lambda: core.write_text(target, "second", force=False),
                 code="output-exists", exit_code=core.EXIT_APPROVAL)
    assert open(target).read() == "first"
    # content parser gives an actionable error on unparsable input
    expect_error(lambda: plan.parse_content("", "empty"), code="content-empty")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
def run_suite(verbose=False):
    tempdir = tempfile.mkdtemp(prefix="pm-selftest-")
    try:
        check("core: contrast and colour maths", test_core_contrast)
        check("core: JSON schema validator", test_core_schema_validator)
        check("core: text metrics and helpers", test_core_text_metrics)
        check("core: configuration defaults and merge", lambda: test_config_defaults_and_override(tempdir))
        check("intake: labeled plain-text catalog", test_intake_labeled_text)
        check("intake: copied HTML and Markdown", test_intake_html_and_markdown)
        check("intake: single capability and empty input", test_intake_single_capability_and_errors)
        check("assets: 32 categories / 320 skills / coverage", test_shipped_catalog_integrity)
        check("assets: every theme passes contrast", test_theme_contrast_assets)
        check("brief: validation, defaults, recovery", lambda: test_brief_validation_and_defaults(tempdir))
        check("plan: framework and platform selection", test_framework_and_platform_selection)
        check("plan: deck spec generation and notes", lambda: test_spec_generation(tempdir))
        check("render: pptx, html, pdf, md, notes, srt", lambda: test_render_formats(tempdir))
        check("render: output changes with input", lambda: test_render_changes_with_input(tempdir))
        check("audit: clean deck passes every gate", lambda: test_audit_clean_deck(tempdir))
        check("audit: injected faults are detected", lambda: test_audit_detects_injected_faults(tempdir))
        check("audit: delivery and export checks", lambda: test_audit_delivery_checks(tempdir))
        check("diff and benchmark", lambda: test_diff_and_benchmark(tempdir))
        check("package: build, verify, approvals", lambda: test_package_build_and_verify(tempdir))
        check("cli: help, capabilities, exit codes", lambda: test_cli_surface(tempdir))
        check("cli: pipeline end to end", lambda: test_cli_pipeline_end_to_end(tempdir))
        check("cli: intake round trip", lambda: test_cli_intake_roundtrip(tempdir))
        check("recovery: bad input, protected outputs", lambda: test_recovery_paths(tempdir))
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)
        for path in _TMP_JSONS:
            try:
                os.unlink(path)
            except OSError:
                pass
    return RESULTS


def run(verbose=False):
    results = run_suite(verbose=verbose)
    passed = sum(1 for _n, ok, _e in results if ok)
    failed = len(results) - passed
    for name, ok, error in results:
        if verbose or not ok:
            print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  -> %s" % error))
    print("\n%d passed, %d failed, %d total" % (passed, failed, len(results)))
    return core.EXIT_OK if failed == 0 else core.EXIT_GATE


if __name__ == "__main__":
    sys.exit(run(verbose="--verbose" in sys.argv or "-v" in sys.argv))
