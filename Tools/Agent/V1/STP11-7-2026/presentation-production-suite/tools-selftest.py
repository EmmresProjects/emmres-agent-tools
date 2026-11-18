#!/usr/bin/env python3
"""Fixture generator and acceptance test runner for the presentation suite.

Supporting tool, not the primary entry point. It builds a realistic set of
PowerPoint fixtures (clean, defect-laden, edge-case, and a declarative spec) and
then exercises the primary CLI end to end, asserting real observable outcomes.

Usage (from the suite folder):
    python tools-selftest.py              # build fixtures in a temp dir and run all tests
    python tools-selftest.py --keep       # keep the temp working directory and print its path
    python tools-selftest.py --case basic # run a single named case
    python tools-selftest.py --list       # list case names

Exit code 0 means every case passed. The runner never needs network access and
never writes outside its temporary directory (or --workdir when given).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

sys.dont_write_bytecode = True

SUITE_DIR = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(SUITE_DIR, "tools-presentation-suite.py")
PY = sys.executable


# ------------------------------------------------------------------ fixtures
def _pptx():
    from pptx import Presentation
    from pptx.util import Emu, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    return Presentation, Emu, Pt, RGBColor, MSO_SHAPE


def build_clean_deck(path: str) -> None:
    """A deck that should pass the accessibility and readability gates."""
    Presentation, Emu, Pt, RGBColor, MSO_SHAPE = _pptx()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9
    blank = prs.slide_layouts[6]

    s1 = prs.slides.add_slide(blank)
    t = s1.shapes.add_textbox(Emu(762000), Emu(1828800), Emu(10668000), Emu(1828800))
    t.name = "Title"
    t.text_frame.word_wrap = True
    t.text_frame.text = "Quarterly platform review: adoption doubled after onboarding redesign"
    t.text_frame.paragraphs[0].runs[0].font.size = Pt(40)
    t.text_frame.paragraphs[0].runs[0].font.bold = True
    sub = s1.shapes.add_textbox(Emu(762000), Emu(3657600), Emu(10668000), Emu(914400))
    sub.name = "Subtitle"
    sub.text_frame.text = "Product leadership offsite, 12 November 2025"
    sub.text_frame.paragraphs[0].runs[0].font.size = Pt(22)

    s2 = prs.slides.add_slide(blank)
    t2 = s2.shapes.add_textbox(Emu(762000), Emu(457200), Emu(10668000), Emu(1371600))
    t2.name = "Title"
    t2.text_frame.word_wrap = True
    t2.text_frame.text = "Activation improved because the first-run checklist removed setup doubt"
    t2.text_frame.paragraphs[0].runs[0].font.size = Pt(30)
    t2.text_frame.paragraphs[0].runs[0].font.bold = True
    body = s2.shapes.add_textbox(Emu(762000), Emu(1600200), Emu(6400800), Emu(3200400))
    body.name = "Body"
    body.text_frame.word_wrap = True
    for i, line in enumerate([
        "Time to first value fell from 9 days to 4 days",
        "Support tickets about setup dropped 38 percent",
        "Trial-to-paid conversion rose from 11 to 19 percent",
    ]):
        para = body.text_frame.paragraphs[0] if i == 0 else body.text_frame.add_paragraph()
        run = para.add_run()
        run.text = f"•  {line}"
        run.font.size = Pt(20)
        para.space_after = Pt(10)
    # an informative picture with alt text (added before the footnote, matching
    # the visual reading order so the clean fixture passes the reading-order check)
    from PIL import Image
    import io
    img = Image.new("RGB", (1600, 900), (31, 92, 158))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    pic = s2.shapes.add_picture(buf, Emu(8382000), Emu(4572000), width=Emu(3048000))
    pic.name = "Activation chart image"
    pic._element._nvXxPr.cNvPr.set("descr", "Bar chart of weekly activations rising from 120 to 260")
    src = s2.shapes.add_textbox(Emu(762000), Emu(5943600), Emu(6096000), Emu(457200))
    src.name = "Source Note"
    src.text_frame.text = "Source: 2025 activation cohort analysis (n=4,120)"
    src.text_frame.paragraphs[0].runs[0].font.size = Pt(14)

    s3 = prs.slides.add_slide(blank)
    t3 = s3.shapes.add_textbox(Emu(762000), Emu(2286000), Emu(10668000), Emu(2286000))
    t3.name = "Closing Statement"
    t3.text_frame.word_wrap = True
    t3.text_frame.text = "Decision requested: approve the onboarding budget for Q1"
    t3.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
    t3.text_frame.paragraphs[0].runs[0].font.bold = True

    # a clean deliverable should not carry default template metadata
    core = prs.core_properties
    core.author = ""
    core.last_modified_by = ""
    core.comments = ""
    core.title = "Quarterly platform review"
    core.subject = ""
    core.keywords = ""
    core.category = ""
    prs.save(path)


def build_defect_deck(path: str) -> None:
    """A deck seeded with one instance of every repairable defect class."""
    Presentation, Emu, Pt, RGBColor, MSO_SHAPE = _pptx()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    blank = prs.slide_layouts[6]

    s1 = prs.slides.add_slide(blank)
    # low-contrast small title
    t = s1.shapes.add_textbox(Emu(762000), Emu(457200), Emu(4572000), Emu(457200))
    t.name = "Weak Title"
    t.text_frame.text = "This is a placeholder title to replace"
    r = t.text_frame.paragraphs[0].runs[0]
    r.font.size = Pt(11)
    r.font.color.rgb = RGBColor(0xBB, 0xBB, 0xBB)

    # off-canvas object
    off = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(-1500000), Emu(2000000), Emu(1200000), Emu(600000))
    off.name = "Offslide Box"

    # overflow text frame
    ov = s1.shapes.add_textbox(Emu(762000), Emu(3000000), Emu(1828800), Emu(365760))
    ov.name = "Overflow Frame"
    ov.text_frame.word_wrap = True
    ov.text_frame.text = "This paragraph is deliberately far too long for its frame and should overflow the box " * 2
    ov.text_frame.paragraphs[0].runs[0].font.size = Pt(18)

    # picture without alt text
    from PIL import Image
    import io
    img = Image.new("RGB", (300, 200), (200, 60, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    pic = s1.shapes.add_picture(buf, Emu(6000000), Emu(1200000), width=Emu(2400000))
    pic.name = "Undescribed Image"

    # accidental shadow
    shadowed = s1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(6500000), Emu(3500000), Emu(2400000), Emu(900000))
    shadowed.name = "Shadowed Card"
    shadowed.text_frame.text = "Card with an accidental shadow"
    shadowed.text_frame.paragraphs[0].runs[0].font.size = Pt(18)

    s2 = prs.slides.add_slide(blank)
    # near-duplicate colors + near-misaligned boxes
    for i, colour in enumerate([(0x1F, 0x5C, 0x9E), (0x1E, 0x5D, 0x9F), (0x20, 0x5B, 0x9D)]):
        box = s2.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(762000 + i * 3200400 + (i * 9525)),
                                  Emu(1828800), Emu(2743200), Emu(1371600))
        box.name = f"Panel {i + 1}"
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(*colour)
        box.text_frame.text = f"Panel {i + 1} body copy"
        box.text_frame.paragraphs[0].runs[0].font.size = Pt(16)

    # sensitive content in text and notes
    s3 = prs.slides.add_slide(blank)
    leak = s3.shapes.add_textbox(Emu(762000), Emu(914400), Emu(9144000), Emu(1828800))
    leak.name = "Leaky Text"
    leak.text_frame.word_wrap = True
    leak.text_frame.text = ("Internal contact: jane.doe@example.com password=hunter2super and "
                            "key AKIAABCDEFGHIJKLMNOP see \\\\fileserver\\private\\deck.pptx")
    leak.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    s3.notes_slide.notes_text_frame.text = "TODO: remove the credential before presenting"

    # hidden slide carrying obsolete content
    s4 = prs.slides.add_slide(blank)
    old = s4.shapes.add_textbox(Emu(762000), Emu(914400), Emu(9144000), Emu(914400))
    old.text_frame.text = "Obsolete pricing from 2019 (do not present)"
    old.text_frame.paragraphs[0].runs[0].font.size = Pt(24)
    s4._element.set("show", "0")

    core = prs.core_properties
    core.author = "Jane Doe"
    core.last_modified_by = "Jane Doe"
    core.company = "Example Corp"
    core.comments = "internal draft"
    prs.save(path)


def build_edge_deck(path: str) -> None:
    """Edge cases: empty slide, zero-size shape, rotated object, RTL/CJK text."""
    Presentation, Emu, Pt, RGBColor, MSO_SHAPE = _pptx()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(9144000), Emu(6858000)  # 4:3
    blank = prs.slide_layouts[6]
    prs.slides.add_slide(blank)  # empty slide
    s2 = prs.slides.add_slide(blank)
    zero = s2.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(100000), Emu(100000), Emu(0), Emu(0))
    zero.name = "Zero Size"
    rot = s2.shapes.add_textbox(Emu(2000000), Emu(2000000), Emu(2000000), Emu(600000))
    rot.rotation = 17.5
    rot.text_frame.text = "Rotated label"
    rot.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    cjk = s2.shapes.add_textbox(Emu(1000000), Emu(4000000), Emu(6000000), Emu(900000))
    cjk.text_frame.word_wrap = True
    cjk.text_frame.text = "日本語のテキストと English mixed content 混在"
    cjk.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
    rtl = s2.shapes.add_textbox(Emu(1000000), Emu(5000000), Emu(6000000), Emu(900000))
    rtl.text_frame.word_wrap = True
    rtl.text_frame.text = "مرحبا بالعالم"
    rtl.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
    prs.save(path)


def write_spec(path: str) -> None:
    spec = {
        "title": "Activation programme review",
        "author": "Product Analytics",
        "language": "en",
        "theme": {"accent1": "1F5C9E"},
        "slides": [
            {"type": "title", "title": "Activation programme review",
             "subtitle": "Q4 results and the Q1 decision", "presenter": "Product Analytics",
             "date": "12 November 2025"},
            {"type": "content", "title": "Activation improved because onboarding doubt was removed",
             "claim": "First-run clarity is the single biggest lever we found",
             "bullets": ["Time to first value fell from 9 days to 4 days",
                         "Setup support tickets dropped 38 percent",
                         "Trial-to-paid conversion rose from 11 to 19 percent"],
             "footnote": "Source: 2025 activation cohort analysis (n=4,120)"},
            {"type": "kpi", "title": "Three metrics moved together in the same quarter",
             "cards": [{"value": "4", "unit": " days", "label": "Time to first value", "delta": "-5 days vs Q3"},
                       {"value": "19", "unit": "%", "label": "Trial to paid", "delta": "+8 pts vs Q3"},
                       {"value": "38", "unit": "%", "label": "Fewer setup tickets", "delta": "-38% vs Q3"}],
             "footnote": "Source: activation cohort analysis, October 2025"},
            {"type": "process", "title": "The redesigned first-run path has four deliberate steps",
             "steps": ["Invite the team", "Import one dataset", "Generate a first report", "Share the result"]},
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)


def write_bad_spec(path: str) -> None:
    spec = {"slides": [{"type": "content", "title": "", "bullets": []},
                       {"type": "unknown-type", "title": "X"}]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)


def write_config(path: str) -> None:
    cfg = {"profile": "large-stage", "overrides": {"min_text_pt": 22},
           "brand": {"allowed_colors": ["1F5C9E", "FFFFFF", "F2F5F9"],
                     "allowed_fonts": ["Calibri", "Arial", "Aptos"],
                     "logo_name_patterns": ["logo"],
                     "min_logo_clear_space_pt": 12},
           "limits": {"timeout_seconds": 90}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ------------------------------------------------------------------- runner
class Result:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.notes: List[str] = []
        self.failures: List[str] = []

    def check(self, condition: bool, message: str) -> None:
        if condition:
            self.notes.append(f"PASS {message}")
        else:
            self.ok = False
            self.failures.append(message)


def run_cli(args: List[str], cwd: Optional[str] = None, expect: int = 0) -> subprocess.CompletedProcess:
    proc = subprocess.run([PY, CLI] + args, cwd=cwd or SUITE_DIR, capture_output=True, text=True, timeout=600)
    return proc


def _json(proc: subprocess.CompletedProcess) -> Any:
    try:
        return json.loads(proc.stdout)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", proc.stdout, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


CASES: Dict[str, str] = {}


def case(name: str):
    def deco(fn):
        CASES[name] = fn.__doc__ or name
        return fn
    return deco


@case("env")
def case_env(wd: str, fx: Dict[str, str], r: Result) -> None:
    """selfcheck and capabilities report a ready environment"""
    p = run_cli(["selfcheck", "--json"])
    data = _json(p)
    r.check(p.returncode == 0, f"selfcheck exits 0 (got {p.returncode})")
    r.check(bool(data) and data.get("ready") is True, "selfcheck reports ready=true")
    p2 = run_cli(["capabilities", "--json"])
    cap = _json(p2)
    r.check(p2.returncode == 0 and cap and cap["engine_count"] >= 60,
            f"capabilities lists engines (got {cap['engine_count'] if cap else 'none'})")
    r.check(cap and len(cap["commands"]) >= 20, "capabilities lists >= 20 commands")
    r.check(cap and cap["catalog"]["capabilities_total"] == 324, "catalog registers all 324 capabilities")
    p3 = run_cli(["help"], expect=0)
    r.check(p3.returncode == 0 and "Repair, Recovery" not in p3.stdout, "help renders without the catalog dump")
    p4 = run_cli(["help", "repair"])
    r.check(p4.returncode == 0 and "--scope" in p4.stdout, "help repair documents --scope")
    p5 = run_cli(["help", "does-not-exist"])
    r.check(p5.returncode == 2, "help with an unknown topic exits 2")


@case("config")
def case_config(wd: str, fx: Dict[str, str], r: Result) -> None:
    """config validation accepts valid files and rejects invalid ones"""
    p = run_cli(["validate-config", "--json"])
    data = _json(p)
    r.check(p.returncode == 0 and data and data["valid"], "default config validates")
    p2 = run_cli(["validate-config", "--config", fx["config"], "--json"])
    d2 = _json(p2)
    r.check(p2.returncode == 0 and d2 and d2["valid"], "user config merges and validates")
    r.check(d2 and d2["profile"] == "large-stage", "user config switches the profile")
    bad = os.path.join(wd, "bad-config.json")
    with open(bad, "w") as fh:
        json.dump({"profile": "nope", "limits": {"max_slides": "many"}}, fh)
    p3 = run_cli(["validate-config", "--config", bad, "--json"])
    d3 = _json(p3)
    r.check(p3.returncode == 2 and d3 and not d3["valid"], "invalid config exits 2")
    r.check(d3 and any("max_slides" in e for e in d3["errors"]), "invalid config reports the type error")


@case("inspect")
def case_inspect(wd: str, fx: Dict[str, str], r: Result) -> None:
    """inspect produces a neutral model with real geometry"""
    out = os.path.join(wd, "inspect")
    p = run_cli(["inspect", "--deck", fx["clean"], "--out", out, "--json"])
    data = _json(p)
    r.check(p.returncode == 0 and data, "inspect exits 0 with JSON")
    model = json.load(open(os.path.join(out, "inspect.json")))
    r.check(model["model"]["slide_count"] == 3, f"model has 3 slides (got {model['model']['slide_count']})")
    r.check(abs(model["model"]["width_pt"] - 960) < 1, "canvas width is 960pt for 16:9")
    first = model["model"]["slides"][0]
    r.check(first["object_count"] >= 2, "first slide has its title and subtitle objects")
    texts = " ".join(o["text"] for s in model["model"]["slides"] for o in s["objects"])
    r.check("adoption doubled" in texts, "extracted text matches the source deck")
    r.check(model["meta"]["sha256"] and len(model["meta"]["sha256"]) == 64, "sha256 recorded")
    p2 = run_cli(["inspect", "--deck", os.path.join(wd, "missing.pptx"), "--out", out])
    r.check(p2.returncode == 2, "missing deck exits 2")


@case("audit-clean")
def case_audit_clean(wd: str, fx: Dict[str, str], r: Result) -> None:
    """a clean deck audits with zero errors and a high score"""
    out = os.path.join(wd, "audit-clean")
    p = run_cli(["audit", "--deck", fx["clean"], "--out", out, "--json"])
    data = _json(p)
    r.check(p.returncode == 0, f"audit on a clean deck exits 0 (got {p.returncode})")
    r.check(data and data["issue_counts"]["error"] == 0,
            f"no error findings (got {data['issue_counts']['error'] if data else '?'})")
    r.check(data and data["scores"]["overall"] >= 80, f"overall score >= 80 (got {data['scores']['overall']})")
    for name in ("audit.json", "report.md", "report.html"):
        r.check(os.path.isfile(os.path.join(out, name)), f"{name} written")
    html = open(os.path.join(out, "report.html")).read()
    r.check("<html" in html.lower() and "Scorecards" in html, "html report has content and scorecards")
    md = open(os.path.join(out, "report.md")).read()
    r.check("## Engine results" in md and "## Measurement limits" in md, "markdown report has the required sections")


@case("audit-defects")
def case_audit_defects(wd: str, fx: Dict[str, str], r: Result) -> None:
    """the defect deck is detected and gated"""
    out = os.path.join(wd, "audit-defects")
    p = run_cli(["audit", "--deck", fx["defect"], "--out", out, "--json", "--fail-on", "error"])
    data = _json(p)
    r.check(p.returncode == 1, f"audit --fail-on error exits 1 for a defective deck (got {p.returncode})")
    codes = {i["code"] for i in data["issues"]}
    expected = {"GEO_OUT_OF_BOUNDS", "TYPE_TOO_SMALL", "TYPE_OVERFLOW", "A11Y_ALT_MISSING",
                "A11Y_CONTRAST", "SEC_EMAIL", "SEC_API_KEY_ASSIGNMENT", "SEC_METADATA", "HIDDEN_SLIDE",
                "COLOR_DRIFT", "INTENT_PLACEHOLDER"}
    missing = expected - codes
    r.check(not missing, f"all seeded defect classes detected (missing: {sorted(missing)})")
    r.check(data["scores"]["overall"] < 80, f"defective deck scores below 80 (got {data['scores']['overall']})")
    r.check(len(data["defect_queue"]["queue"]) > 3, "defect queue is populated")
    top = data["defect_queue"]["queue"][0]
    r.check(top["severity"] == "error", "highest priority defect is an error")
    p2 = run_cli(["gate", "--deck", fx["defect"], "--out", out, "--json"])
    r.check(p2.returncode == 1, "gate blocks the defective deck")
    g = _json(p2)
    r.check(g and g["gate"]["passed"] is False and g["gate"]["blocking_codes"], "gate lists blocking codes")


@case("repair")
def case_repair(wd: str, fx: Dict[str, str], r: Result) -> None:
    """repair is dry-run-first, approved when needed, and verified after writing"""
    out = os.path.join(wd, "repair")
    # approval gate
    p0 = run_cli(["repair", "--deck", fx["defect"], "--out", out, "--scope", "contrast", "--apply"])
    r.check(p0.returncode == 2 and "approval required" in (p0.stdout + p0.stderr).lower(),
            "appearance-changing scope without --approve exits 2")
    # dry run writes nothing
    p1 = run_cli(["repair", "--deck", fx["defect"], "--out", out, "--scope", "bounds,type-min,overflow,alt-text"])
    r.check(p1.returncode == 0, "dry run exits 0")
    r.check("DRY RUN" in p1.stdout, "dry run is labelled")
    r.check(not any(f.endswith(".pptx") for f in os.listdir(out)) if os.path.isdir(out) else True,
            "dry run writes no deck")
    preview = json.load(open(os.path.join(out, "repair-preview.json")))
    r.check(preview["change_count"] > 0, f"dry run reports planned changes ({preview['change_count']})")
    # apply
    p2 = run_cli(["repair", "--deck", fx["defect"], "--out", out,
                  "--scope", "bounds,type-min,overflow,alt-text,effects", "--apply", "--approve", "--json"])
    data = _json(p2)
    r.check(p2.returncode == 0, f"apply exits 0 (got {p2.returncode}: {p2.stderr[:200]})")
    r.check(data and data["output"] and os.path.isfile(data["output"]), "repaired deck written")
    verify = data["verification"]
    r.check(verify["package_health_ok"], "repaired package is healthy")
    r.check(verify["after"]["error"] <= verify["before"]["error"],
            f"errors do not increase ({verify['before']['error']} -> {verify['after']['error']})")
    assert data and data["output"]
    # recheck the repaired deck with a fresh audit
    out2 = os.path.join(wd, "repair-recheck")
    p3 = run_cli(["analyze", "--deck", data["output"], "--engine",
                  "bounds,min-type,alt-text,overflow", "--json"])
    d3 = _json(p3)
    codes3 = {i["code"] for i in d3["issues"]}
    r.check("GEO_OUT_OF_BOUNDS" not in codes3, "off-canvas object was repaired")
    r.check("TYPE_TOO_SMALL" not in codes3, "small type was raised to the profile minimum")
    r.check("A11Y_ALT_MISSING" not in codes3, "missing alt text was written")
    r.check("TYPE_OVERFLOW" not in codes3, "overflow frame was grown")
    r.check(os.path.isdir(os.path.join(out, "last-known-good")), "pre-repair backup was taken")
    # idempotency: repairing the repaired deck changes nothing further
    p4 = run_cli(["repair", "--deck", data["output"], "--out", os.path.join(wd, "repair-again"),
                  "--scope", "bounds,type-min,alt-text", "--json"])
    d4 = _json(p4)
    r.check(bool(d4) and d4["change_count"] == 0,
            f"second repair run is idempotent (got {d4['change_count'] if d4 else 'no json'})")


@case("normalize")
def case_normalize(wd: str, fx: Dict[str, str], r: Result) -> None:
    """alignment, spacing and colour normalization are idempotent"""
    out = os.path.join(wd, "normalize")
    p = run_cli(["normalize", "--deck", fx["defect"], "--out", out,
                 "--scope", "align,spacing,colors", "--apply", "--approve", "--json"])
    data = _json(p)
    r.check(p.returncode == 0, f"normalize apply exits 0 (got {p.returncode}: {p.stderr[:200]})")
    r.check(data and data["change_count"] > 0, "normalize reported changes")
    output = data["output"]
    p2 = run_cli(["normalize", "--deck", output, "--out", os.path.join(wd, "normalize-again"),
                  "--scope", "align,spacing,colors", "--json", "--approve"])
    d2 = _json(p2)
    r.check(bool(d2) and d2["change_count"] == 0,
            f"second normalize run is idempotent (got {d2['change_count'] if d2 else p2.stdout[:120]})")
    p3 = run_cli(["analyze", "--deck", output, "--engine", "color-drift,alignment", "--json"])
    d3 = _json(p3)
    r.check(not any(i["code"] == "COLOR_DRIFT" for i in d3["issues"]), "colour drift was collapsed")


@case("governance")
def case_governance(wd: str, fx: Dict[str, str], r: Result) -> None:
    """governance scrubs metadata, notes and sensitive text"""
    out = os.path.join(wd, "governance")
    p = run_cli(["governance", "--deck", fx["defect"], "--out", out,
                 "--action", "scrub-metadata,strip-notes,redact", "--apply", "--approve", "--json"])
    data = _json(p)
    r.check(p.returncode == 0, f"governance exits 0 (got {p.returncode}: {p.stderr[:200]})")
    assert data
    out_deck = data["output"]
    p2 = run_cli(["analyze", "--deck", out_deck, "--engine", "sensitive,metadata", "--json"])
    d2 = _json(p2)
    codes = [i["code"] for i in d2["issues"]]
    r.check("SEC_EMAIL" not in codes and "SEC_API_KEY_ASSIGNMENT" not in codes,
            "sensitive matches were redacted")
    r.check("META_PERSONAL" not in codes, "personal metadata was cleared")
    md = json.load(open(os.path.join(out, "governance-changes.json")))
    r.check(md["change_count"] > 0, "governance changes were recorded")
    p3 = run_cli(["analyze", "--deck", out_deck, "--engine", "metadata", "--json"])
    d3 = _json(p3)
    r.check(d3["engines"][0]["data"]["slides_with_notes"] == 0, "notes were stripped")
    # hidden-slide visibility control
    p4 = run_cli(["governance", "--deck", fx["defect"], "--out", out, "--show", "4", "--apply"])
    r.check(p4.returncode == 0, "slide visibility update succeeds")
    p5 = run_cli(["governance", "--deck", fx["defect"], "--out", out, "--show", "99", "--apply"])
    r.check(p5.returncode == 2, "out-of-range slide index exits 2")


@case("author")
def case_author(wd: str, fx: Dict[str, str], r: Result) -> None:
    """author builds a deck from a spec and rejects an invalid spec"""
    out_deck = os.path.join(wd, "authored.pptx")
    p = run_cli(["author", "--spec", fx["spec"], "--out", out_deck, "--json"])
    data = _json(p)
    r.check(p.returncode == 0, f"author exits 0 (got {p.returncode}: {p.stderr[:300]})")
    r.check(os.path.isfile(out_deck), "authored deck exists")
    r.check(data and data["slides_built"] == 4, f"built 4 slides (got {data['slides_built'] if data else '?'})")
    r.check(data and data["verification"]["error"] == 0,
            f"authored deck has no blocking errors ({data['verification'] if data else ''})")
    p2 = run_cli(["inspect", "--deck", out_deck, "--json"])
    d2 = _json(p2)
    r.check(d2 and d2["meta"]["slide_count"] == 4, "authored deck reopens with 4 slides")
    p3 = run_cli(["author", "--spec", fx["bad_spec"], "--out", os.path.join(wd, "bad.pptx")])
    r.check(p3.returncode == 2, "invalid spec exits 2")
    text = p3.stdout + p3.stderr
    r.check("title" in text.lower() and "unknown-type" in text, "invalid spec explains both problems")
    r.check(not os.path.isfile(os.path.join(wd, "bad.pptx")), "invalid spec writes no file")
    p4 = run_cli(["author", "--spec", fx["spec"], "--out", out_deck])
    r.check(p4.returncode == 2 and "--force" in (p4.stdout + p4.stderr), "existing output requires --force")
    p5 = run_cli(["author", "--spec", fx["spec"], "--out", out_deck, "--force"])
    r.check(p5.returncode == 0, "--force overwrites")


@case("render")
def case_render(wd: str, fx: Dict[str, str], r: Result) -> None:
    """render produces real PNGs and a contact sheet"""
    out = os.path.join(wd, "render")
    p = run_cli(["render", "--deck", fx["clean"], "--out", out, "--width", "960", "--contact-sheet", "--safe-area"])
    r.check(p.returncode == 0, f"render exits 0 (got {p.returncode}: {p.stderr[:200]})")
    files = sorted(f for f in os.listdir(out) if f.startswith("slide-") and f.endswith(".png"))
    r.check(len(files) == 3, f"3 slide PNGs written (got {len(files)})")
    from PIL import Image
    img = Image.open(os.path.join(out, files[0]))
    r.check(img.width == 960 and img.height == 540, f"render size is 960x540 (got {img.size})")
    r.check(os.path.isfile(os.path.join(out, "contact-sheet.png")), "contact sheet written")
    sheet = Image.open(os.path.join(out, "contact-sheet.png"))
    r.check(sheet.width > 960, "contact sheet spans multiple thumbnails")
    man = json.load(open(os.path.join(out, "render.json")))
    r.check(man["render"]["count"] == 3, "render manifest counts 3 slides")
    p2 = run_cli(["contact-sheet", "--deck", fx["defect"], "--out", os.path.join(wd, "sheet.png")])
    r.check(p2.returncode == 0 and os.path.isfile(os.path.join(wd, "sheet.png")), "standalone contact-sheet works")


@case("regression")
def case_regression(wd: str, fx: Dict[str, str], r: Result) -> None:
    """compare detects real changes and ignores unchanged decks"""
    out = os.path.join(wd, "compare")
    p = run_cli(["compare", "--before", fx["clean"], "--after", fx["clean"], "--visual", "--json"])
    d = _json(p)
    r.check(p.returncode == 0 and d["structural_diff"]["change_count"] == 0, "identical decks show no changes")
    r.check(d and all(s["identical"] for s in d["visual_diff"]["slides"]), "identical decks produce identical renders")
    p2 = run_cli(["compare", "--before", fx["defect"], "--after", fx["clean"], "--visual", "--out", out, "--json"])
    d2 = _json(p2)
    r.check(d2["structural_diff"]["change_count"] > 0, "different decks show changes")
    r.check(d2["visual_diff"]["changed_slides"], "visual diff flags changed slides")
    r.check(os.path.isfile(os.path.join(out, "compare.json")), "compare output written")


@case("snapshot")
def case_snapshot(wd: str, fx: Dict[str, str], r: Result) -> None:
    """snapshot records a reproducible model and a deck copy"""
    out = os.path.join(wd, "snap")
    p = run_cli(["snapshot", "--deck", fx["clean"], "--out", out, "--copy", "--label", "before-edit"])
    r.check(p.returncode == 0, "snapshot exits 0")
    snap = json.load(open(os.path.join(out, "snapshot.json")))
    r.check(snap["label"] == "before-edit", "label recorded")
    r.check(os.path.isfile(snap["deck_copy"]), "deck copy written")
    r.check(len(snap["model_sha256"]) == 64, "model hash recorded")
    p2 = run_cli(["audit", "--deck", fx["clean"], "--baseline", os.path.join(out, "inspect.json")
                  if os.path.isfile(os.path.join(out, "inspect.json")) else os.path.join(out, "snapshot.json"),
                  "--json"])
    d2 = _json(p2)
    r.check(p2.returncode == 0 and d2, "audit accepts a snapshot as baseline")
    r.check(any(e["engine"] == "qa.regression" for e in d2["engines"]), "regression engine ran against the baseline")


@case("errors")
def case_errors(wd: str, fx: Dict[str, str], r: Result) -> None:
    """invalid inputs produce structured, non-crashing failures"""
    p = run_cli(["analyze", "--deck", fx["clean"], "--engine", "not-an-engine"])
    r.check(p.returncode == 2 and "unknown engine" in (p.stdout + p.stderr).lower(), "unknown engine exits 2")
    p2 = run_cli(["repair", "--deck", fx["clean"], "--out", wd, "--scope", "not-a-scope"])
    r.check(p2.returncode == 2 and "unknown repair scope" in (p2.stdout + p2.stderr).lower(), "unknown scope exits 2")
    corrupt = os.path.join(wd, "corrupt.pptx")
    with open(corrupt, "wb") as fh:
        fh.write(b"this is not a pptx")
    p3 = run_cli(["inspect", "--deck", corrupt])
    r.check(p3.returncode == 3 and "cannot open" in (p3.stdout + p3.stderr).lower(),
            f"corrupt deck exits 3 with a clear message (got {p3.returncode})")
    p4 = run_cli(["report", "--audit", os.path.join(wd, "no-such.json")])
    r.check(p4.returncode == 2, "missing audit file exits 2")
    p5 = run_cli(["frobnicate"])
    r.check(p5.returncode == 2, "unknown command exits 2")
    p6 = run_cli(["audit", "--deck", fx["clean"], "--profile", "nope"])
    r.check(p6.returncode == 2 and "unknown profile" in (p6.stdout + p6.stderr).lower(), "unknown profile exits 2")


@case("edge")
def case_edge(wd: str, fx: Dict[str, str], r: Result) -> None:
    """edge-case deck (empty slide, zero size, rotation, CJK/RTL) audits without crashing"""
    out = os.path.join(wd, "edge")
    p = run_cli(["audit", "--deck", fx["edge"], "--out", out, "--json"])
    data = _json(p)
    r.check(p.returncode in (0, 1), f"edge deck audits without a runtime error (got {p.returncode})")
    codes = {i["code"] for i in data["issues"]}
    r.check("CENSUS_EMPTY_SLIDE" in codes, "empty slide detected")
    r.check("HIDDEN_ZERO_SIZE" in codes, "zero-size object detected")
    r.check("GEO_ROTATED_TEXT" in codes, "rotated text detected")
    r.check("A11Y_RTL_CONTENT" in codes, "RTL content detected")
    r.check("A11Y_CJK_CONTENT" in codes, "CJK content detected")
    r.check(data["meta"]["aspect"] == "4:3", "4:3 canvas recognised")


@case("deps")
def case_deps(wd: str, fx: Dict[str, str], r: Result) -> None:
    """commands degrade cleanly when optional dependencies are absent"""
    import textwrap
    block_dir = os.path.join(wd, "blockdeps")
    os.makedirs(block_dir, exist_ok=True)
    with open(os.path.join(block_dir, "sitecustomize.py"), "w") as fh:
        fh.write(textwrap.dedent("""
            import sys
            BLOCK = ("pptx", "PIL")
            class Blocker:
                def find_spec(self, name, path=None, target=None):
                    if any(name == b or name.startswith(b + ".") for b in BLOCK):
                        raise ImportError(name + " blocked for test")
                    return None
            sys.meta_path.insert(0, Blocker())
        """))
    env = dict(os.environ, PYTHONPATH=block_dir, PPTSUITE_NO_BYTECODE="1")

    def run_no_deps(args):
        return subprocess.run([PY, CLI] + args, cwd=SUITE_DIR, capture_output=True, text=True,
                              timeout=300, env=env)

    p = run_no_deps(["selfcheck"])
    r.check(p.returncode == 3, f"selfcheck exits 3 without dependencies (got {p.returncode})")
    r.check("python-pptx: not installed" in p.stdout and "Pillow: not installed" in p.stdout,
            "selfcheck names both missing libraries")
    for cmd in (["capabilities"], ["validate-config"], ["catalog"], ["help"]):
        r.check(run_no_deps(cmd).returncode == 0, f"{cmd[0]} still works without dependencies")
    p2 = run_no_deps(["inspect", "--deck", fx["clean"]])
    r.check(p2.returncode == 3, f"inspect without python-pptx exits 3 (got {p2.returncode})")
    r.check("(runtime)" in (p2.stdout + p2.stderr) and "python-pptx is required" in (p2.stdout + p2.stderr),
            "inspect reports a clean runtime error, not a traceback")
    p3 = run_no_deps(["render", "--deck", fx["clean"], "--out", os.path.join(wd, "nodeps-render")])
    r.check(p3.returncode == 3 and "Pillow" in (p3.stdout + p3.stderr), "render without Pillow exits 3 with guidance")
    p4 = run_no_deps(["convert-units", "--values", "72", "--from", "pt", "--to", "in"])
    r.check(p4.returncode == 0 and "1 in" in p4.stdout, "convert-units needs no optional dependencies")


@case("units")
def case_units(wd: str, fx: Dict[str, str], r: Result) -> None:
    """unit conversion round-trips correctly"""
    p = run_cli(["convert-units", "--values", "72,540,1", "--from", "pt", "--to", "in", "--json"])
    d = _json(p)
    r.check(p.returncode == 0, "convert-units exits 0")
    r.check(abs(d["results"][0]["in"] - 1.0) < 1e-6, "72pt = 1in")
    r.check(abs(d["results"][1]["in"] - 7.5) < 1e-6, "540pt = 7.5in")
    r.check(d["results"][0]["emu"] == 914400, "72pt = 914400 EMU")
    p2 = run_cli(["convert-units", "--values", "abc", "--to", "in"])
    r.check(p2.returncode == 3, "non-numeric value exits 3")


@case("batch")
def case_batch(wd: str, fx: Dict[str, str], r: Result) -> None:
    """batch processes many decks and isolates failures"""
    out = os.path.join(wd, "batch")
    pattern = os.path.join(wd, "fixtures", "*.pptx")
    p = run_cli(["batch", "--deck", pattern, os.path.join(wd, "corrupt.pptx"),
                 "--out", out, "--audit", "--json"])
    d = _json(p)
    r.check(p.returncode == 1, "batch exits 1 when at least one deck fails")
    r.check(d and d["ok"] >= 3, f"at least 3 decks processed ({d['ok'] if d else '?'})")
    r.check(d and len(d["failed"]) == 1, "exactly one failed deck recorded")
    r.check(d and "error" in d["failed"][0], "failure carries a reason")
    ok_entries = [x for x in d["results"] if x.get("ok")]
    r.check(all("overall_score" in x for x in ok_entries), "each successful deck got a score")


@case("extract")
def case_extract(wd: str, fx: Dict[str, str], r: Result) -> None:
    """text extraction preserves slide/object context and notes"""
    out = os.path.join(wd, "extract")
    p = run_cli(["extract-text", "--deck", fx["defect"], "--out", out, "--with-notes", "--json"])
    d = _json(p)
    r.check(p.returncode == 0 and d["count"] > 0, "extract-text returns items")
    r.check(any("jane.doe@example.com" in i["text"] for i in d["items"]), "extracts the real text")
    md = open(os.path.join(out, "text-extract.md")).read()
    r.check("slide 3" in md.lower() or "Slide 3" in md, "markdown carries slide context")
    r.check("TODO" in md, "notes are included when requested")


@case("rescale")
def case_rescale(wd: str, fx: Dict[str, str], r: Result) -> None:
    """rescale reflows the canvas and needs approval"""
    out = os.path.join(wd, "rescaled.pptx")
    p = run_cli(["rescale", "--deck", fx["clean"], "--out", out, "--preset", "4:3", "--apply"])
    r.check(p.returncode == 2 and "approve" in (p.stdout + p.stderr).lower(), "rescale without --approve exits 2")
    p2 = run_cli(["rescale", "--deck", fx["clean"], "--out", out, "--preset", "4:3", "--apply", "--approve"])
    r.check(p2.returncode == 0, f"rescale exits 0 (got {p2.returncode}: {p2.stderr[:200]})")
    p3 = run_cli(["inspect", "--deck", out, "--json"])
    d3 = _json(p3)
    r.check(d3["meta"]["aspect"] == "4:3", f"rescaled deck is 4:3 (got {d3['meta']['aspect']})")
    r.check(abs(d3["meta"]["width_pt"] - 720) < 1, "width is 720pt")
    p4 = run_cli(["rescale", "--deck", fx["clean"], "--out", out, "--width-pt", "100", "--height-pt", "100", "--apply", "--approve"])
    r.check(p4.returncode == 2, "out-of-range canvas exits 2")


@case("plan")
def case_plan(wd: str, fx: Dict[str, str], r: Result) -> None:
    """plan produces an ordered, runnable remediation sequence"""
    p = run_cli(["plan", "--deck", fx["defect"], "--out", os.path.join(wd, "plan"), "--json"])
    d = _json(p)
    r.check(p.returncode == 0 and d and d["plan"], "plan returns steps")
    orders = [s["order"] for s in d["plan"]["plan"]]
    r.check(orders == list(range(1, len(orders) + 1)), "steps are numbered in order")
    r.check(all("tools-presentation-suite.py" not in s["command"] for s in d["plan"]["plan"]),
            "commands are relative to the suite folder")
    r.check(d["plan"]["plan"][0]["step"] == "snapshot", "snapshot comes first")


@case("a11y-profile")
def case_a11y_profile(wd: str, fx: Dict[str, str], r: Result) -> None:
    """the active profile changes the findings, as it must"""
    base = _json(run_cli(["analyze", "--deck", fx["clean"], "--engine", "min-type", "--json"]))
    strict = _json(run_cli(["--config", fx["config"], "analyze", "--deck", fx["clean"],
                            "--engine", "min-type", "--json"]))
    r.check(base and strict, "both runs produced results")
    r.check(base["engines"][0]["data"]["violation_count"] <= strict["engines"][0]["data"]["violation_count"],
            "the large-stage profile is stricter than the default profile")
    r.check(strict["engines"][0]["data"]["violation_count"] > 0,
            "large-stage profile flags small body text on the 3-slide fixture")


@case("shape")
def case_shape(wd: str, fx: Dict[str, str], r: Result) -> None:
    """the shipped folder structure satisfies the packaging rules"""
    entries = sorted(f for f in os.listdir(SUITE_DIR) if f != "__pycache__")
    r.check("SKILL.md" not in entries, "no SKILL.md inside the Skill Tool folder")
    r.check(not any(os.path.isdir(os.path.join(SUITE_DIR, e)) for e in entries), "no internal subfolders")
    prefixed = all(e.startswith(("tools-", "knowledge-", "asset-")) for e in entries)
    r.check(prefixed, f"every file carries a valid prefix (files: {entries})")
    knowledge = [e for e in entries if e.startswith("knowledge-")]
    r.check(knowledge == ["knowledge-readme.md"], f"exactly one knowledge file (got {knowledge})")
    tools = [e for e in entries if e.startswith("tools-")]
    r.check(len(tools) >= 5, f"tools files present: {tools}")
    assets = [e for e in entries if e.startswith("asset-")]
    r.check(len(assets) >= 4, f"assets present: {assets}")
    r.check(os.path.isfile(os.path.join(SUITE_DIR, "tools-presentation-suite.py")),
            "primary entry point filename is stable")
    total = len(entries)
    r.check(total <= 500, f"file count within limits ({total})")


TEST_ORDER = ["env", "shape", "deps", "config", "inspect", "audit-clean", "audit-defects", "repair", "normalize",
              "governance", "author", "render", "regression", "snapshot", "errors", "edge", "units",
              "batch", "extract", "rescale", "plan", "a11y-profile"]


def build_fixtures(wd: str) -> Dict[str, str]:
    fx_dir = os.path.join(wd, "fixtures")
    os.makedirs(fx_dir, exist_ok=True)
    paths = {
        "clean": os.path.join(fx_dir, "clean-deck.pptx"),
        "defect": os.path.join(fx_dir, "defect-deck.pptx"),
        "edge": os.path.join(fx_dir, "edge-deck.pptx"),
        "spec": os.path.join(fx_dir, "deck-spec.json"),
        "bad_spec": os.path.join(fx_dir, "bad-spec.json"),
        "config": os.path.join(fx_dir, "strict-config.json"),
    }
    build_clean_deck(paths["clean"])
    build_defect_deck(paths["defect"])
    build_edge_deck(paths["edge"])
    write_spec(paths["spec"])
    write_bad_spec(paths["bad_spec"])
    write_config(paths["config"])
    corrupt = os.path.join(wd, "corrupt.pptx")
    with open(corrupt, "wb") as fh:
        fh.write(b"PK\x03\x04 not really a zip")
    paths["corrupt"] = corrupt
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build fixtures and run the suite's acceptance tests.")
    ap.add_argument("--case", help="run a single named case")
    ap.add_argument("--list", action="store_true", help="list case names and exit")
    ap.add_argument("--keep", action="store_true", help="keep the temp working directory")
    ap.add_argument("--workdir", help="use this directory instead of a temp dir")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for name in TEST_ORDER:
            print(f"{name:14} {CASES[name]}")
        return 0

    wd = args.workdir or tempfile.mkdtemp(prefix="ppsuite-selftest-")
    os.makedirs(wd, exist_ok=True)
    print(f"working directory: {wd}")
    fixtures = build_fixtures(wd)

    names = [args.case] if args.case else TEST_ORDER
    results: List[Result] = []
    for name in names:
        if name not in CASES:
            print(f"unknown case '{name}' (use --list)")
            return 2
        res = Result(name)
        fn = globals().get(f"case_{name.replace('-', '_').replace(' ', '_')}")
        if fn is None:
            print(f"case function missing for '{name}'")
            return 2
        try:
            fn(wd, fixtures, res)
        except Exception as exc:
            res.ok = False
            res.failures.append(f"exception: {type(exc).__name__}: {exc}")
            import traceback
            if args.verbose:
                traceback.print_exc()
        results.append(res)
        print(f"[{'PASS' if res.ok else 'FAIL'}] {name}: {CASES[name]}")
        for f in res.failures:
            print(f"    FAIL {f}")
        if args.verbose:
            for n in res.notes:
                print(f"    {n}")

    passed = sum(1 for r in results if r.ok)
    total_checks = sum(len(r.notes) for r in results)
    print(f"\n{passed}/{len(results)} cases passed ({total_checks} assertions)")
    if not args.keep and not args.workdir:
        shutil.rmtree(wd, ignore_errors=True)
    else:
        print(f"kept: {wd}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
