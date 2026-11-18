"""Built-in validation suite for the accessibility-and-localization Skill Tool.

Runs every registered operation through the real CLI (subprocess), plus the
command surface, error paths, approval gates, determinism, recovery and output
artifacts. Fixtures are generated into a temporary directory and removed
afterwards unless --keep is passed.

Invoked by:  python3 tools-a11yl10n.py self-test [--verbose] [--keep]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

S = None
DEPS = None


def init(deps):
    global S, DEPS
    DEPS = deps
    S = deps["support"]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
BAD_HTML = """<!DOCTYPE html>
<html>
<head>
<title></title>
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<style>
.quiet { color: #cccccc; background-color: #ffffff; font-size: 11px; }
button:focus, a:focus { outline: none; }
.wide { width: 500px; }
.spin { animation: spin 0.2s infinite; }
@keyframes flash { 0% { opacity: 0 } 20% { opacity: 1 } 40% { opacity: 0 } 60% { opacity: 1 } 80% { opacity: 0 } 100% { opacity: 1 } }
</style>
</head>
<body>
<h1>Broken page</h1>
<h4>Skipped heading level</h4>
<img src="logo.png">
<img src="photo-of-cat.jpg" alt="image of a cat">
<a href="/x">click here</a>
<a href="/y"></a>
<a href="/tiny" style="width:16px; height:16px">x</a>
<button aria-label="Close"><svg></svg></button>
<input type="text" id="nm" placeholder="Name">
<input type="text" id="nm">
<label for="ghost">Ghost label</label>
<div onclick="doIt()">Delete account</div>
<div role="button" tabindex="0"></div>
<div aria-hidden="true"><button>Hidden but focusable</button></div>
<div id="live" aria-live="yes"></div>
<span id="dup"></span><span id="dup"></span>
<video src="promo.mp4" autoplay></video>
<p class="quiet wide" style="height:20px; overflow:hidden;">Low contrast text that is clipped by a fixed height and never wraps properly.</p>
<table><tr><td>January</td><td>500</td></tr></table>
<form>
  <input type="text" name="email">
  <input type="radio" name="pick" value="1"><input type="radio" name="pick" value="2">
  <input type="password" name="pw" onpaste="return false">
  <button>Send</button>
</form>
<p>Your session will expire in 2 minutes. Save your work.</p>
<marquee>moving text</marquee>
<meta http-equiv="refresh" content="30">
<a href="/tiny" style="width:10px; height:10px;">tiny target</a>
<iframe src="/frame"></iframe>
</body>
</html>
"""

GOOD_HTML = """<!DOCTYPE html>
<html lang="en-PH">
<head>
<meta charset="utf-8">
<title>Accounts overview</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body { color: #111111; background-color: #ffffff; font-size: 16px; line-height: 1.6; }
a { text-decoration: underline; }
.skip:focus { outline: 2px solid #111111; }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
</head>
<body>
<a class="skip" href="#main">Skip to main content</a>
<header>
  <nav aria-label="Primary">
    <ul><li><a href="/">Home</a></li><li><a href="/billing">Billing</a></li></ul>
  </nav>
</header>
<main id="main">
  <h1>Accounts overview</h1>
  <h2>Current balance</h2>
  <p>Your balance is PHP 1,234.56 as of 7 November 2026.</p>
  <img src="chart.png" alt="Bar chart showing the balance rising from PHP 900.00 to PHP 1,234.56 across three months.">
  <h2>Pay your bill</h2>
  <form>
    <label for="acct">Account number</label>
    <input id="acct" name="acct" autocomplete="off">
    <button type="submit">Pay bill</button>
  </form>
  <table>
    <caption>Recent payments</caption>
    <thead><tr><th scope="col">Date</th><th scope="col">Amount</th></tr></thead>
    <tbody><tr><td>7 November 2026</td><td>PHP 500.00</td></tr></tbody>
  </table>
  <video controls>
    <track kind="captions" srclang="en-PH" src="c.vtt" default>
    <track kind="descriptions" srclang="en-PH" src="d.vtt">
  </video>
</main>
<footer><p>Need help? <a href="/help">Contact support</a></p></footer>
</body>
</html>
"""

RTL_HTML = """<!DOCTYPE html>
<html lang="ar">
<head><style>
.sidebar { margin-left: 16px; text-align: left; float: left; }
</style></head>
<body dir="rtl"><main><h1>مرحبا</h1><img class="arrow-back" src="arrow-left.svg" alt="رجوع"></main></body>
</html>
"""

TYPO_HTML = """<!DOCTYPE html>
<html lang="en-PH"><head><style>
p { line-height: 1.0; letter-spacing: -0.02px; text-align: justify; }
.shout { text-transform: uppercase; }
</style></head>
<body><main><h1>Typography</h1>
<p>THIS AGREEMENT IS MADE AND ENTERED INTO BY AND BETWEEN THE PARTIES AND THE TERMS ARE FULLY SET OUT BELOW FOR REVIEW BY ALL CONCERNED PARTIES IN THIS DOCUMENT.</p>
</main></body></html>
"""

STRINGS_SRC = {
    "greeting": "Hello {name}",
    "items": "{count, plural, one {# item} other {# items}}",
    "balance": "Your balance is {amount}",
    "cta": "Pay bill",
    "help": "Need help?"
}

STRINGS_FIL = {
    "greeting": "Kumusta, {name}",
    "items": "{count, plural, one {# item} other {# mga item}}",
    "balance": "Ang balanse mo ay {amount}",
    "cta": "I-click ang bayad",
    "help": "",
    "broken": "Kabuuan: {amount",
    "orphan": "{unknown, plural, one {isa}}",
    "note": "Gamitin ang etc. sa dulo ng listahan ng dokumento at iba pa."
}

STRINGS_PSEUDO = {
    "greeting": "Kumusta, {nmae}",
    "items": "{count, plural, one {# item} other {# mga item}}",
    "balance": "Ang balanse mo ay {amount}",
    "cta": "Pindutin",
    "orphan": "{unknown, plural, one {isa}}",
    "note": "Gamitin ang atbp."
}

PAIRS = {
    "pairs": [
        {"key": "greeting", "source": "Hello {name}", "target": "Kumusta, {nmae}"},
        {"key": "balance", "source": "Your balance is {amount}", "target": "Ang balanse mo ay {amount}"},
        {"key": "cta", "source": "Pay bill", "target": "Pay bill"},
        {"key": "items", "source": "{count, plural, one {# item} other {# items}}",
         "target": "{count, plural, one {# item} other {# mga item}}"},
        {"key": "help", "source": "Need help?", "target": ""}
    ],
    "words": 120
}

CAPTIONS_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:02.000
This cue is far too long to read comfortably in one second of screen time.

2
00:00:01.500 --> 00:00:06.000
Second cue overlapping the first one by half a second.

3
00:00:06.500 --> 00:00:07.000
Short.

NOTE this is a comment block
that spans two lines.
"""

TMX = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header creationtool="fixture" creationtoolversion="1.0" segtype="sentence" o-tmf="plain"
          adminlang="en-PH" srclang="en-PH" datatype="plaintext"/>
  <body>
    <tu tuid="1"><tuv xml:lang="en-PH"><seg>Pay bill</seg></tuv>
      <tuv xml:lang="fil-PH"><seg>Bayaran ang bill</seg></tuv></tu>
    <tu tuid="2"><tuv xml:lang="en-PH"><seg>Need help?</seg></tuv>
      <tuv xml:lang="fil-PH"><seg>Need help?</seg></tuv></tu>
    <tu tuid="3"><tuv xml:lang="en-PH"><seg>Pay bill</seg></tuv>
      <tuv xml:lang="fil-PH"><seg>Bayaran ang bill</seg></tuv></tu>
    <tu tuid="4"><tuv xml:lang="en-PH"><seg>Orphan source</seg></tuv></tu>
  </body>
</tmx>
"""

TERMBASE = {
    "terms": [
        {"source": "accessibility", "target": "aksesibilidad", "status": "approved"},
        {"source": "accessibility", "target": "pagiging accessible", "status": "approved"},
        {"source": "screen reader", "target": "", "status": "approved"},
        {"source": "password", "target": "kodal", "status": "approved", "forbidden": ["kodal"]},
        {"source": "consent", "target": "pahintulot", "status": "approved", "context": "legal"}
    ]
}

SEGMENTS = {"segments": ["Pay bill", "Need help?", "Your balance is PHP 1,234.56", "Something very new"],
            "tm_path": ""}

NOTES = [
    {"session_id": "S1", "at": "NVDA 2026.1", "artifact": "checkout", "wcag": "2.4.3",
     "expected": "focus moves to the dialog heading", "actual": "focus stays behind the dialog",
     "severity": "high", "environment": "Windows 11, Firefox 130"},
    {"session_id": "S2", "at": "TalkBack", "artifact": "checkout", "wcag": "1.3.1",
     "expected": "cart total announced", "actual": "cart total announced twice"},
    {"session_id": "S3", "at": "Keyboard", "artifact": "checkout", "expected": "tab reaches the pay button",
     "actual": ""}
]

TREE = {"platform": "android", "elements": [
    {"id": "root", "role": "generic", "children": [
        {"id": "title", "role": "text", "label": "Accounts", "clickable": False},
        {"id": "pay", "role": "button", "clickable": True, "width": 20, "height": 20},
        {"id": "avatar", "role": "image", "label": "", "clickable": False},
        {"id": "close", "role": "button", "clickable": True, "width": 48, "height": 48},
        {"id": "row", "role": "button", "clickable": False, "label": "Row"}
    ]}
]}

TOKENS = {
    "tokens": {
        "color": {"text.primary": "#999999", "surface": "#ffffff", "text.muted": "#bbbbbb",
                  "brand": "#0a5c36"},
        "typography": {"body": {"size": "10px"}, "heading": {"size": "28px"}},
        "space": {"xs": 2, "sm": 8}
    },
    "pairings": [
        {"fg": "text.primary", "bg": "surface"},
        {"fg": "brand", "bg": "surface"},
        {"fg": "text.muted", "bg": "surface"}
    ]
}

CHARTS = {"items": [
    {"id": "revenue", "title": "", "series": ["Q1", "Q2", "Q3"]},
    {"id": "costs", "name": "Costs by quarter", "series": ["Q1", "Q2"], "data_table": "table#costs"}
]}

SCENES = {"scenes": [
    {"start": 0, "end": 2, "visual": "A woman walks into a bank lobby", "dialogue": False},
    {"start": 2, "end": 3, "visual": "She waits in a short queue", "dialogue": False},
    {"start": 3, "end": 12, "visual": "Dialogue scene between two characters discussing a transfer", "dialogue": True},
    {"start": 12, "end": 13, "visual": "A receipt printer prints a long confirmation slip with amounts", "dialogue": False}
]}

FINDINGS = {"schema": "a11yl10n.result/1", "skill": {"name": "Mixed evidence", "id": "C00-S00"},
            "summary": {"by_severity": {"critical": 2, "high": 2, "medium": 1, "low": 0, "info": 0},
                        "criteria_affected": {"1.1.1": 1, "1.4.3": 1, "2.4.3": 1, "pdf-tagged": 1,
                                              "3.3.2": 1, "i18n-hardcoded": 1}},
            "findings": [
                {"id": "F0001", "rule": "image-alt", "severity": "critical", "wcag": "1.1.1",
                 "message": "Image without alt", "locator": "line 12", "impact": "blocked", "no_workaround": True,
                 "owner": "frontend", "effort": 1,
                 "remediation": "Add alt text.", "retest": "Re-run the scan."},
                {"id": "F0002", "rule": "contrast-text", "severity": "high", "wcag": "1.4.3",
                 "message": "Contrast 2.1:1", "locator": "line 20", "impact": "degraded",
                 "owner": "design", "effort": 2, "remediation": "Darken the text.",
                 "retest": "Re-run the contrast check."},
                {"id": "F0003", "rule": "focus-order", "severity": "high", "wcag": "2.4.3",
                 "message": "Focus order jumps", "locator": "line 30", "impact": "degraded",
                 "owner": "frontend", "effort": 3, "remediation": "Remove positive tabindex.",
                 "retest": "Re-tab through the page."},
                {"id": "F0004", "rule": "pdf-tagged", "severity": "critical", "wcag": "1.3.1",
                 "message": "PDF not tagged", "locator": "report.pdf", "impact": "blocked", "no_workaround": True,
                 "owner": "content", "effort": 1, "remediation": "Re-export as tagged PDF.",
                 "retest": "Re-run the document audit."},
                {"id": "F0005", "rule": "i18n-hardcoded", "severity": "medium", "wcag": "locale-architecture",
                 "message": "Hard-coded string", "locator": "key cta", "impact": "friction",
                 "owner": "frontend", "effort": 1, "remediation": "Externalise the string.",
                 "retest": "Re-run hardcoded scanning."}
            ]}

MQM_ERRORS = {"words": 800, "errors": [
    {"category": "mistranslation", "severity": "critical", "count": 1, "note": "Reversed meaning of the payment amount"},
    {"category": "terminology", "severity": "major", "count": 2, "note": "Approved term not used"},
    {"category": "grammar", "severity": "minor", "count": 3},
    {"category": "nonsense-category", "severity": "minor", "count": 1}
]}

OBSERVATIONS = {"observations": [
    {"participant": "P1", "theme": "focus lost after dialog closes", "severity": "high", "wcag": "2.4.3"},
    {"participant": "P2", "theme": "focus lost after dialog closes", "severity": "high", "wcag": "2.4.3"},
    {"participant": "P3", "theme": "caption text too fast", "severity": "medium", "wcag": "1.2.2"},
    {"participant": "P1", "theme": "error message not announced", "severity": "critical", "wcag": "3.3.1"}
]}

SUS = {"responses": [[5, 1, 5, 1, 5, 1, 5, 1, 5, 1], [4, 2, 4, 2, 4, 2, 4, 2, 4, 2],
                     [3, 3, 3, 3, 3, 3, 3, 3, 3, 3]]}

COMPONENTS = {"components": [
    {"name": "PrimaryButton", "type": "button", "role": "button", "accessible_name": True,
     "name_source": "children text", "keyboard": ["Enter", "Space"], "states": ["disabled"],
     "localization": "text via children; expansion tolerated"},
    {"name": "AccountDialog", "type": "dialog", "role": "dialog", "accessible_name": True,
     "keyboard": ["Escape"], "states": ["modal"]},
    {"name": "MysteryWidget", "type": "carousel", "role": "region"}
]}

SUPPLIERS = {"suppliers": [
    {"name": "Vendor A", "scores": {"conformance": 90, "evidence_quality": 80, "locale_coverage": 70,
                                    "remediation": 60, "contract_terms": 50},
     "evidence": ["VPAT 2.5 dated 2026-04-01"]},
    {"name": "Vendor B", "scores": {"conformance": 40, "locale_coverage": 20},
     "evidence": []}
]}

REPORTS = {"reports": [
    {"id": "T-1", "title": "Cannot submit the form with a screen reader", "locale": "fil-PH",
     "channel": "phone relay", "at": "NVDA"},
    {"id": "T-2", "title": "Filipino translation shows English month names", "locale": "fil-PH",
     "locale_issue": True},
    {"id": "T-3", "title": "Date format looks confusing"}
]}

CLAIMS = {"claims": [
    {"claim": "Automated evidence for the checkout template", "evidence": ["fixture_findings.json"]},
    {"claim": "Screen reader evidence", "evidence": ["missing-session-notes.json"]}
]}

CASE_WORKFLOW = {"state": "requested", "requester": "payments", "criterion": "1.4.3",
                 "justification": "Brand colour is fixed by marketing", "age_days": 45,
                 "events": [{"from": "requested", "to": "closed", "at": "2026-11-01"}]}

CLAIM = {"standard": "WCAG 2.2 Level AA", "date": "2026-11-07",
         "methods": ["automated", "manual keyboard"], "text": "We are fully conformant."}

AI_OUTPUT = {"records": [
    {"locale": "fil-PH", "output": "Ang larawan ay nagpapakita ng isang bangko kung saan naghihintay ang isang babae. "
                                   "Ang transaksyong ito ay nagsasangkot ng komplikadong dokumentasyon hinggil sa "
                                   "institusyonalisasyon ng pondo at pagsasaalang-alang sa pagpapatupad."},
    {"locale": "fil-PH", "output": ""},
    {"locale": "en-PH", "asset_kind": "alt-text", "output": "image of a cat"}
]}

MT_RECORDS = {"pairs": [
    {"key": "greeting", "source": "Hello {name}", "mt": "Kumusta, {nmae}", "back_translation": "Hello name"},
    {"key": "cta", "source": "Pay bill", "mt": "Pay bill"},
    {"key": "amount", "source": "Balance due", "mt": "Balanse", "back_translation": "Balance due today for the account"}
]}

CHAT_SPEC = {"transcript_role": "log", "input_label": "", "auto_scroll": True,
             "keyboard_send": "Enter", "platform": "web"}

DELEGATION = {"languages": ["fil-PH", "ceb-PH", "ar"], "coverage": {"fil-PH": 98, "ceb-PH": 60},
              "rtl_supported": False, "reviewer_fil-PH": True}

READINESS_TEXT = ("Ang bayad ay 1.234,56 at 7/11/2026. Pindutin ang I-click para magpatuloy. "
                  "Para sa karagdagang impormasyon, makipag-ugnayan sa etc. "
                  "Ang mga handicapped ay dapat tawaging crippled.")

LONG_TEXT = ("The implementation of the aforementioned provision shall be undertaken by the responsible "
             "officer of the department in order to facilitate the promulgation of the institutionalisation "
             "framework which notwithstanding prior arrangements must be commenced prior to the conclusion of "
             "the fiscal period. Utilise the leverage of the platform to ascertain synergy across teams. "
             "This sentence is deliberately constructed so that it exceeds the maximum permitted number of "
             "words for a single sentence in plain language guidance and therefore must be flagged.")

INCLUSIVE_TEXT = ("The disabled and the handicapped can use the chairman's portal. Guys, simply click the "
                  "button and obviously it will work. The elderly need help from a young and tech-savvy user.")

CULTURE_TEXT = ("Hit the ground running with our low-hanging fruit. Break a leg! Enter your ZIP code and SSN, "
                "then pay in USD within 30 miles. Thanks for joining our Black Friday sale before Thanksgiving.")

BIDI_TEXT = ("\u2066unbalanced isolate text \u202bمرحبا بك في بوابة الدفع\u202c ثم \u202aContinue\u202a ثم 2026-11-07.\n"
             "مرحبا، هذه فقرة عربية بدون إغلاق العزل.")


# --------------------------------------------------------------------------- #
# Fixture writers
# --------------------------------------------------------------------------- #
def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, indent=2, ensure_ascii=False))
    return path


def write_docx(path):
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""
    core = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Quarterly report</dc:title>
</cp:coreProperties>"""
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Quarterly report</w:t></w:r></w:p>
<w:p><w:r><w:t>Body paragraph without a language declaration.</w:t></w:r></w:p>
<w:p><w:r><wp:docPr id="1" name="Picture 1"/></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Amount</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("word/document.xml", document)
    return path


def write_pdf(path):
    body = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im1 4 0 R >> >> >> endobj
4 0 obj << /Type /XObject /Subtype /Image /Width 100 /Height 100 >> endobj
trailer << /Root 1 0 R /Info << /Producer (fixture) >> >>
%%EOF
"""
    with open(path, "wb") as fh:
        fh.write(body)
    return path


def write_epub(path):
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Fixture book</dc:title></metadata>
<manifest><item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="ch1"/></spine></package>"""
    ch1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>
<body><h1>Chapter one</h1><img src="figure.png"/><p>Text without a language declaration.</p></body></html>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", ch1)
    return path


def build_fixtures(base):
    fx = {}
    d = os.path.join(base, "fixtures")
    os.makedirs(d, exist_ok=True)
    fx["bad.html"] = write_text(os.path.join(d, "bad.html"), BAD_HTML)
    fx["good.html"] = write_text(os.path.join(d, "good.html"), GOOD_HTML)
    fx["rtl.html"] = write_text(os.path.join(d, "rtl.html"), RTL_HTML)
    fx["typo.html"] = write_text(os.path.join(d, "typo.html"), TYPO_HTML)
    fx["strings-src.json"] = write_json(os.path.join(d, "strings-src.json"), STRINGS_SRC)
    fx["strings-fil.json"] = write_json(os.path.join(d, "strings-fil.json"), STRINGS_FIL)
    fx["strings-pseudo.json"] = write_json(os.path.join(d, "strings-pseudo.json"), STRINGS_PSEUDO)
    fx["pairs.json"] = write_json(os.path.join(d, "pairs.json"), PAIRS)
    fx["captions.vtt"] = write_text(os.path.join(d, "captions.vtt"), CAPTIONS_VTT)
    fx["memory.tmx"] = write_text(os.path.join(d, "memory.tmx"), TMX)
    fx["termbase.json"] = write_json(os.path.join(d, "termbase.json"), TERMBASE)
    segments = dict(SEGMENTS)
    segments["tm_path"] = fx["memory.tmx"]
    fx["segments.json"] = write_json(os.path.join(d, "segments.json"), segments)
    fx["notes.json"] = write_json(os.path.join(d, "notes.json"), NOTES)
    fx["tree.json"] = write_json(os.path.join(d, "tree.json"), TREE)
    fx["tokens.json"] = write_json(os.path.join(d, "tokens.json"), TOKENS)
    fx["charts.json"] = write_json(os.path.join(d, "charts.json"), CHARTS)
    fx["scenes.json"] = write_json(os.path.join(d, "scenes.json"), SCENES)
    fx["fixture_findings.json"] = write_json(os.path.join(d, "fixture_findings.json"), FINDINGS)
    baseline = json.loads(json.dumps(FINDINGS))
    baseline["findings"] = [f for f in baseline["findings"] if f["id"] != "F0005"]
    fx["fixture_baseline.json"] = write_json(os.path.join(d, "fixture_baseline.json"), baseline)
    fx["mqm.json"] = write_json(os.path.join(d, "mqm.json"), MQM_ERRORS)
    fx["observations.json"] = write_json(os.path.join(d, "observations.json"), OBSERVATIONS)
    fx["sus.json"] = write_json(os.path.join(d, "sus.json"), SUS)
    fx["components.json"] = write_json(os.path.join(d, "components.json"), COMPONENTS)
    fx["suppliers.json"] = write_json(os.path.join(d, "suppliers.json"), SUPPLIERS)
    fx["reports.json"] = write_json(os.path.join(d, "reports.json"), REPORTS)
    fx["claims.json"] = write_json(os.path.join(d, "claims.json"), CLAIMS)
    fx["workflow.json"] = write_json(os.path.join(d, "workflow.json"), CASE_WORKFLOW)
    fx["claim.json"] = write_json(os.path.join(d, "claim.json"), CLAIM)
    fx["ai.json"] = write_json(os.path.join(d, "ai.json"), AI_OUTPUT)
    fx["mt.json"] = write_json(os.path.join(d, "mt.json"), MT_RECORDS)
    fx["chat-spec.json"] = write_json(os.path.join(d, "chat-spec.json"), CHAT_SPEC)
    fx["readiness.json"] = write_json(os.path.join(d, "readiness.json"), DELEGATION)
    fx["readiness.txt"] = write_text(os.path.join(d, "readiness.txt"), READINESS_TEXT)
    fx["longtext.txt"] = write_text(os.path.join(d, "longtext.txt"), LONG_TEXT)
    fx["inclusive.txt"] = write_text(os.path.join(d, "inclusive.txt"), INCLUSIVE_TEXT)
    fx["culture.txt"] = write_text(os.path.join(d, "culture.txt"), CULTURE_TEXT)
    fx["bidi.txt"] = write_text(os.path.join(d, "bidi.txt"), BIDI_TEXT)
    fx["script.txt"] = write_text(os.path.join(d, "script.txt"),
                                  "Cafe\u0301 au lait with a private use character \ue000 and Arabic \u0645\u0631\u062d\u0628\u0627.\n")
    fx["docx"] = write_docx(os.path.join(d, "report.docx"))
    fx["pdf"] = write_pdf(os.path.join(d, "report.pdf"))
    fx["epub"] = write_epub(os.path.join(d, "book.epub"))
    fx["broken.json"] = write_text(os.path.join(d, "broken.json"), "{ this is not json ")
    fx["scanner.json"] = write_json(os.path.join(d, "scanner.json"), {
        "violations": [
            {"id": "image-alt", "impact": "critical", "help": "Images must have alternate text",
             "description": "Provide an alt attribute",
             "tags": ["wcag2a", "wcag111"],
             "nodes": [{"target": ["img.logo"], "html": "<img class=\"logo\">",
                        "failureSummary": "Fix: add alt"}]},
            {"id": "label", "impact": "serious", "help": "Form elements must have labels",
             "tags": ["wcag2a", "wcag131"],
             "nodes": [{"target": ["#email"], "html": "<input id=\"email\">"},
                       {"target": ["#email"], "html": "<input id=\"email\">"}]}
        ]})
    font_source = None
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/usr/lib/R/library/grDevices/fonts/Roboto/Roboto-Medium.ttf"):
        if os.path.exists(candidate):
            font_source = candidate
            break
    if font_source:
        dest = os.path.join(d, "fixture-font.ttf")
        shutil.copyfile(font_source, dest)
        fx["font"] = dest
    return fx


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #
ALL_GROUPS = ("structure,names,images,aria,keyboard,pointer,visual,motion,forms,tables,media,"
              "messaging,i18n,consistency,meta")

CASES = [
    # --- category 2/5/6/11 web engine -------------------------------------- #
    {"name": "web-scan detects structural and naming failures", "op": "web-scan", "input": "bad.html",
     "set": ["include_groups=" + ALL_GROUPS, "max_rule_groups=40"],
     "expect": {"exit": 0, "status": "ok", "min_findings": 15,
                "rules_include": ["html-lang", "page-title", "heading-order", "image-alt", "alt-quality",
                                  "link-name", "link-purpose", "input-label", "duplicate-id",
                                  "aria-hidden-focus", "live-region-valid", "table-headers",
                                  "video-captions", "contrast-text", "target-size",
                                  "timeout-warning"]}},
    {"name": "web-scan honours the AAA conformance target", "op": "web-scan", "input": "bad.html",
     "set": ["include_groups=motion,visual", "max_rule_groups=40", "conformance_level=aaa"],
     "expect": {"exit": 0, "rules_include": ["reduced-motion"]}},
    {"name": "web-scan defaults to a broad sweep for WCAG scanner skills", "op": "web-scan",
     "input": "bad.html", "skill": "C02-S01",
     "expect": {"exit": 0, "min_findings": 20,
                "rules_include": ["image-alt", "contrast-text", "heading-order", "video-captions"]}},
    {"name": "web-scan passes a clean page", "op": "web-scan", "input": "good.html",
     "set": ["include_groups=" + ALL_GROUPS, "max_rule_groups=40"],
     "expect": {"exit": 0, "status": "ok", "max_findings": 2, "rules_exclude": ["html-lang", "image-alt",
                                                                               "skip-link", "table-headers"]}},
    {"name": "web-scan keyword routing narrows rule groups", "op": "web-scan", "input": "bad.html",
     "skill": "Duplicate ID Scanner",
     "expect": {"exit": 0, "rules_include": ["duplicate-id"], "rules_exclude": ["contrast-text"]}},
    {"name": "web-scan honours conformance level filter", "op": "web-scan", "input": "bad.html",
     "set": ["include_groups=structure,names", "conformance_level=a"],
     "expect": {"exit": 0, "rules_exclude": ["lang-parts"]}},
    {"name": "harness-config emits a runner configuration", "op": "harness-config", "input": "good.html",
     "set": ["runner=pa11y"],
     "expect": {"exit": 0, "extras_equals": {"runner": "pa11y", "config_filename": ".pa11yci.json"}}},
    {"name": "dedupe merges duplicate scanner findings", "op": "dedupe", "input": "scanner.json",
     "expect": {"exit": 0, "min_findings": 1,
                "extras_min": {"input_findings": 3, "duplicates_merged": 1, "unique_findings": 2}}},
    {"name": "contrast-check classifies explicit pairs", "op": "contrast-check", "input": "good.html",
     "set": ['pairs=[{"fg":"#999999","bg":"#ffffff","label":"muted"},{"fg":"#000000","bg":"#ffffff","label":"body"}]'],
     "expect": {"exit": 0, "rules_include": ["contrast"], "extras_max": {"worst_ratio": 3.0}}},
    {"name": "motion-audit flags infinite animation", "op": "motion-audit", "input": "bad.html",
     "expect": {"exit": 0, "rules_include": ["motion-loop"]}},
    {"name": "token-audit fails low contrast tokens", "op": "token-audit", "input": "tokens.json",
     "expect": {"exit": 0, "rules_include": ["token-contrast", "token-typography", "token-spacing",
                                             "token-focus"]}},
    {"name": "baseline reports regressions against a stored baseline", "op": "baseline",
     "input": "fixture_findings.json", "reference": "fixture_baseline.json",
     "expect": {"exit": 0, "min_findings": 1, "extras_equals": {"new": 1, "fixed": 0, "persisting": 4}}},
    {"name": "register appends a chained governance entry", "op": "register",
     "input": "fixture_findings.json",
     "set": ["mode=append", "register_path=selftest-register.jsonl",
             'entries=[{"id":"R-1","title":"Contrast on checkout","owner":"design","status":"open"}]'],
     "approve": True,
     "expect": {"exit": 0, "extras_equals": {"entries_written": 1, "chain_verified": True},
                "file_exists": "selftest-register.jsonl"}},
    {"name": "ci-gate fails on blocking findings", "op": "ci-gate", "input": "fixture_findings.json",
     "set": ["fail_on=high"], "expect": {"exit": 0, "extras_equals": {"decision": "fail"}}},

    # --- category 3/4/12 assistive technology ------------------------------ #
    {"name": "at-probe derives expected announcements", "op": "at-probe", "input": "bad.html",
     "expect": {"exit": 0, "rules_include": ["at-name-missing"], "extras_min": {"probe_count": 5}}},
    {"name": "at-script generates a pinned AT script", "op": "at-script", "input": "good.html",
     "set": ["at=voiceover-ios", "tasks=5"],
     "expect": {"exit": 0, "extras_min": {"task_count": 3}}},
    {"name": "at-matrix returns core combinations", "op": "at-matrix", "input": "good.html",
     "set": ["scope=core"], "expect": {"exit": 0, "extras_min": {"combination_count": 4}}},
    {"name": "at-findings normalises session notes", "op": "at-findings", "input": "notes.json",
     "expect": {"exit": 0, "min_findings": 2, "extras_equals": {"pairs": None}}},
    {"name": "element-tree-audit flags missing names and small targets", "op": "element-tree-audit",
     "input": "tree.json", "set": ["platform=android"],
     "expect": {"exit": 0, "rules_include": ["element-name", "element-target-size"],
                "extras_min": {"element_count": 5}}},
    {"name": "test-matrix budgets platform and locale combinations", "op": "test-matrix",
     "input": "good.html", "set": ["locales=fil-PH,en-PH", "budget_minutes=100"],
     "expect": {"exit": 0, "extras_equals": {"combinations": 8, "within_budget": False},
                "rules_include": ["matrix-budget"]}},

    # --- category 8/9/10 media and documents ------------------------------- #
    {"name": "alt-text-audit flags missing and bad alt", "op": "alt-text-audit", "input": "bad.html",
     "expect": {"exit": 0, "rules_include": ["alt-missing", "alt-redundant"]}},
    {"name": "chart-accessibility requires a data alternative", "op": "chart-accessibility",
     "input": "charts.json", "expect": {"exit": 0, "rules_include": ["chart-name", "chart-alternative"]}},
    {"name": "captions-audit validates WebVTT", "op": "captions-audit", "input": "captions.vtt",
     "set": ["media_duration_seconds=20", "min_coverage_pct=60"],
     "expect": {"exit": 0, "rules_include": ["caption-reading-rate", "caption-overlap",
                                             "caption-coverage", "caption-min-duration"],
                "extras_equals": {"caption_file_kind": "vtt"}}},
    {"name": "caption-quality-gate fails bad captions", "op": "caption-quality-gate", "input": "captions.vtt",
     "expect": {"exit": 0, "extras_equals": {"decision": "fail"}}},
    {"name": "audio-description-script fits descriptions to gaps", "op": "audio-description-script",
     "input": "scenes.json", "expect": {"exit": 0, "rules_include": ["ad-overrun"],
                                        "extras_min": {"descriptions": 1}}},
    {"name": "media-player-audit flags missing caption tracks", "op": "media-player-audit", "input": "bad.html",
     "expect": {"exit": 0, "rules_include": ["media-captions", "media-descriptions"]}},
    {"name": "document-audit reads DOCX structure", "op": "document-audit", "input": "docx",
     "expect": {"exit": 0, "rules_include": ["doc-language", "doc-alt"]}},
    {"name": "document-audit reads PDF structure", "op": "document-audit", "input": "pdf",
     "expect": {"exit": 0, "rules_include": ["pdf-tagged", "pdf-language", "pdf-title", "pdf-alt"]}},
    {"name": "document-audit reads EPUB structure", "op": "document-audit", "input": "epub",
     "expect": {"exit": 0, "rules_include": ["epub-language", "epub-alt"]}},
    {"name": "document-remediation-plan orders document fixes", "op": "document-remediation-plan",
     "input": "fixture_findings.json", "set": ["tool=word"],
     "expect": {"exit": 0, "extras_min": {"steps": 3}}},

    # --- category 13-19 localization --------------------------------------- #
    {"name": "i18n-audit detects empty and untranslated values", "op": "i18n-audit",
     "input": "strings-fil.json", "set": ["base_path=fixtures/strings-src.json"],
     "expect": {"exit": 0, "rules_include": ["i18n-empty-value", "i18n-icu-syntax"],
                "extras_max": {"coverage_pct": 95}}},
    {"name": "icu-validate rejects a plural without other", "op": "icu-validate", "input": "strings-fil.json",
     "expect": {"exit": 0, "rules_include": ["icu-missing-other"]}},
    {"name": "hardcoded-scan finds literal UI strings", "op": "hardcoded-scan", "input": "good.html",
     "expect": {"exit": 0, "rules_include": ["i18n-hardcoded"]}},
    {"name": "locale-format-audit flags wrong separators", "op": "locale-format-audit",
     "input": "readiness.txt", "set": ["locale=fil-PH"],
     "expect": {"exit": 0, "rules_include": ["locale-number-grouping"]}},
    {"name": "expansion-budget flags over-budget strings", "op": "expansion-budget", "input": "pairs.json",
     "set": ["budget_pct=10"],
     "expect": {"exit": 0, "rules_include": ["i18n-expansion"], "extras_min": {"strings": 4}}},
    {"name": "tm-audit validates TMX units", "op": "tm-audit", "input": "memory.tmx",
     "set": ["source_lang=en-PH", "target_lang=fil-PH"],
     "expect": {"exit": 0, "rules_include": ["tm-duplicate", "tm-copy-source", "tm-incomplete-unit"],
                "extras_equals": {"translation_units": 4}}},
    {"name": "term-audit finds inconsistent and forbidden terms", "op": "term-audit",
     "input": "termbase.json", "set": ["target_lang=fil-PH"],
     "expect": {"exit": 0, "rules_include": ["term-inconsistent", "term-missing-target", "term-forbidden"]}},
    {"name": "tm-match reports leverage against a memory", "op": "tm-match", "input": "segments.json",
     "reference": "memory.tmx",
     "expect": {"exit": 0, "extras_min": {"leverage_pct": 1}, "rules_include": ["tm-no-match"]}},
    {"name": "translation-qa catches placeholder drift", "op": "translation-qa", "input": "pairs.json",
     "set": ["target_lang=fil-PH"],
     "expect": {"exit": 0, "rules_include": ["qa-placeholder", "qa-untranslated", "qa-empty-target"]}},
    {"name": "mqm-score fails a sample with critical errors", "op": "mqm-score", "input": "mqm.json",
     "expect": {"exit": 0, "extras_equals": {"verdict": "fail"},
                "rules_include": ["mqm-critical", "mqm-unknown-category"]}},
    {"name": "review-plan sizes the human review sample", "op": "review-plan", "input": "pairs.json",
     "set": ["words=4000"], "expect": {"exit": 0, "extras_min": {"sample_segments": 20}}},
    {"name": "translation-brief builds a reviewer brief", "op": "translation-brief",
     "input": "strings-fil.json", "set": ["locale=fil-PH", "register=formal"],
     "expect": {"exit": 0, "extras_min": {"brief": 1}}},
    {"name": "filipino-review detects orthography and terminology", "op": "filipino-review",
     "input": "readiness.txt", "set": ["register=formal"],
     "expect": {"exit": 0, "rules_include": ["fil-orthography", "fil-terminology",
                                             "fil-inclusive-language"]}},
    {"name": "philippine-locale-check enforces local conventions", "op": "philippine-locale-check",
     "input": "readiness.txt", "set": ["locale=fil-PH"],
     "expect": {"exit": 0, "rules_include": ["ph-number-grouping"]}},
    {"name": "language-readiness assesses locales", "op": "language-readiness", "input": "readiness.json",
     "expect": {"exit": 0, "rules_include": ["lang-readiness"],
                "extras_min": {"go": 1}}},
    {"name": "bidi-audit detects unbalanced isolates", "op": "bidi-audit", "input": "bidi.txt",
     "set": ["expected_direction=rtl"], "expect": {"exit": 0, "rules_include": ["bidi-isolate"]}},
    {"name": "rtl-layout-audit flags physical CSS properties", "op": "rtl-layout-audit", "input": "rtl.html",
     "expect": {"exit": 0, "rules_include": ["rtl-physical-property", "rtl-icon-mirror"]}},
    {"name": "script-rendering flags private use and normalisation", "op": "script-rendering",
     "input": "script.txt", "expect": {"exit": 0, "rules_include": ["script-private-use",
                                                                   "script-normalisation"]}},
    {"name": "typography-audit flags tight line height", "op": "typography-audit", "input": "typo.html",
     "expect": {"exit": 0, "rules_include": ["type-line-height", "type-justified", "type-all-caps"]}},
    {"name": "pseudolocalize generates a variant bundle", "op": "pseudolocalize",
     "input": "strings-src.json", "set": ["mode=expanded", "expansion=1.4"],
     "expect": {"exit": 0, "extras_min": {"changed": 4}}},
    {"name": "pseudo-audit detects placeholder corruption", "op": "pseudo-audit",
     "input": "strings-pseudo.json", "set": ["source_path=fixtures/strings-src.json"],
     "expect": {"exit": 0, "rules_include": ["pseudo-placeholder-corrupt", "pseudo-missing-key"]}},
    {"name": "font-coverage proves glyph coverage", "op": "font-coverage", "input": "font",
     "set": ["locale=fil-PH"], "expect": {"exit": 0, "extras_min": {"coverage_pct": 99}}},
    {"name": "font-license reads embedding permissions", "op": "font-license", "input": "font",
     "set": ["intended_use=web"], "expect": {"exit": 0, "extras_min": {"font_metadata": 1}}},

    # --- category 7/20/21 content ------------------------------------------ #
    {"name": "plain-language-audit flags long sentences and jargon", "op": "plain-language-audit",
     "input": "longtext.txt", "expect": {"exit": 0, "rules_include": ["plain-sentence-length",
                                                                      "plain-jargon", "plain-passive-voice"]}},
    {"name": "cognitive-support-audit checks cognitive load", "op": "cognitive-support-audit",
     "input": "bad.html", "expect": {"exit": 0, "rules_include": ["coga-help", "coga-timeout"]}},
    {"name": "inclusive-language-audit flags non-inclusive wording", "op": "inclusive-language-audit",
     "input": "inclusive.txt", "expect": {"exit": 0, "rules_include": ["inclusive-disability",
                                                                       "inclusive-condescending"]}},
    {"name": "cultural-adaptation-audit flags US-centric content", "op": "cultural-adaptation-audit",
     "input": "culture.txt", "set": ["target_locale=fil-PH"],
     "expect": {"exit": 0, "rules_include": ["culture-us_centric", "culture-idioms"]}},
    {"name": "ai-output-audit gates generated content", "op": "ai-output-audit", "input": "ai.json",
     "set": ["asset_kind=alt-text"],
     "expect": {"exit": 0, "rules_include": ["ai-empty-output", "ai-alt-redundant"]}},
    {"name": "ai-translation-audit gates machine translation", "op": "ai-translation-audit",
     "input": "mt.json", "set": ["target_locale=fil-PH"],
     "expect": {"exit": 0, "extras_equals": {"decision": "fail"},
                "rules_include": ["mt-placeholder", "mt-untranslated"]}},
    {"name": "conversational-interface-audit checks chat spec", "op": "conversational-interface-audit",
     "input": "chat-spec.json", "set": ["platform=web"],
     "expect": {"exit": 0, "rules_include": ["chat-streaming-announcement", "chat-input-label",
                                             "chat-focus-steal", "chat-escalation"]}},

    # --- category 1 governance --------------------------------------------- #
    {"name": "standards-map maps findings to standards", "op": "standards-map",
     "input": "fixture_findings.json",
     "expect": {"exit": 0, "extras_min": {"findings": 5}, "rules_include": ["standards-unmapped"]}},
    {"name": "conformance-report drafts a VPAT-style report", "op": "conformance-report",
     "input": "fixture_findings.json",
     "set": ["product=Example", "version=1.0", "conformance_level=aa", "mode=draft"],
     "expect": {"exit": 0, "extras_min": {"criteria_in_scope": 50}}},
    {"name": "policy-document builds an accessibility statement", "op": "policy-document",
     "input": "fixture_findings.json", "set": ["kind=statement", "organisation=Example Org"],
     "expect": {"exit": 0, "extras_equals": {"kind": "statement"}}},
    {"name": "workflow rejects an illegal transition", "op": "workflow", "input": "workflow.json",
     "set": ["workflow=exception"],
     "expect": {"exit": 0, "rules_include": ["workflow-invalid-transition", "workflow-escalation"]}},
    {"name": "severity-classify scores impact", "op": "severity-classify", "input": "fixture_findings.json",
     "expect": {"exit": 0, "rules_include": ["severity-critical"],
                "extras_min": {"classified": 5}}},
    {"name": "remediation-plan waves findings", "op": "remediation-plan", "input": "fixture_findings.json",
     "set": ["capacity_per_week=2"], "expect": {"exit": 0, "extras_min": {"waves": 2}}},
    {"name": "maturity-assessment grades dimensions", "op": "maturity-assessment", "input": "fixture_findings.json",
     "set": ['responses={"governance":3,"design":1,"engineering":2,"testing":1,"content":2,"operations":1,"localization":1}'],
     "expect": {"exit": 0, "extras_min": {"assessment": 1}, "rules_include": ["maturity-gap"]}},
    {"name": "claim-validate blocks an unverifiable claim", "op": "claim-validate", "input": "claim.json",
     "expect": {"exit": 0, "extras_equals": {"verdict": "not-publishable"},
                "rules_include": ["claim-scope", "claim-contact", "claim-absolute"]}},
    {"name": "risk-register-seed seeds governance risks", "op": "risk-register-seed",
     "input": "fixture_findings.json", "expect": {"exit": 0, "extras_min": {"seed_count": 5}}},

    # --- category 22 research ---------------------------------------------- #
    {"name": "research-plan sizes participatory sessions", "op": "research-plan", "input": "fixture_findings.json",
     "set": ["users=4", "profiles=screen-reader,keyboard-only,cognitive"],
     "expect": {"exit": 0, "extras_equals": {"sessions_total": 12}}},
    {"name": "sus-score computes usability scores", "op": "sus-score", "input": "sus.json",
     "expect": {"exit": 0, "extras_equals": {"participants": 3},
                "extras_min": {"mean": 60}}},
    {"name": "findings-synthesis ranks research themes", "op": "findings-synthesis",
     "input": "observations.json",
     "expect": {"exit": 0, "extras_equals": {"observations": 4},
                "rules_include": ["research-theme"]}},

    # --- category 23 QA/metrics -------------------------------------------- #
    {"name": "test-plan generates a release plan", "op": "test-plan", "input": "fixture_findings.json",
     "set": ["release=2026.11", "locales=fil-PH,en-PH"],
     "expect": {"exit": 0, "extras_equals": {"locales": ["fil-PH", "en-PH"]}}},
    {"name": "coverage-matrix reports criterion gaps", "op": "coverage-matrix",
     "input": "fixture_findings.json", "set": ["conformance_level=aa"],
     "expect": {"exit": 0, "rules_include": ["coverage-gap"],
                "extras_max": {"coverage_pct": 10}}},
    {"name": "metrics-report aggregates results into a gate", "op": "metrics-report",
     "input": "fixture_findings.json", "set": ["fail_on=high"],
     "expect": {"exit": 0, "extras_equals": {"decision": "fail"}}},
    {"name": "evidence-package hashes claim evidence", "op": "evidence-package", "input": "claims.json",
     "expect": {"exit": 0, "rules_include": ["evidence-missing"], "extras_min": {"claims": 2}}},
    {"name": "retention-schedule classifies artifacts", "op": "retention-schedule",
     "input": "fixture_findings.json", "set": ["paths=fixtures/fixture_findings.json", "retention_days=1"],
     "expect": {"exit": 0, "extras_min": {"schedule": 1}}},

    # --- category 24 design systems ---------------------------------------- #
    {"name": "component-contract-audit validates component specs", "op": "component-contract-audit",
     "input": "components.json",
     "expect": {"exit": 0, "rules_include": ["component-keyboard", "component-unknown-type",
                                             "component-localization"]}},
    {"name": "lint-config emits linter configuration", "op": "lint-config", "input": "good.html",
     "set": ["stack=react"], "expect": {"exit": 0, "extras_min": {"rules": 20}}},
    {"name": "code-review-checklist renders a checklist", "op": "code-review-checklist",
     "input": "good.html", "set": ["focus=keyboard,i18n"],
     "expect": {"exit": 0, "extras_equals": {"areas": ["keyboard", "i18n"]}}},

    # --- category 25 operations -------------------------------------------- #
    {"name": "procurement-score ranks suppliers", "op": "procurement-score", "input": "suppliers.json",
     "expect": {"exit": 0, "extras_min": {"results": 2}, "rules_include": ["procurement-missing-criteria"]}},
    {"name": "operational-runbook emits an SLA matrix", "op": "operational-runbook",
     "input": "fixture_findings.json", "set": ["support_channel=helpdesk"],
     "expect": {"exit": 0, "extras_min": {"stages": 5}}},
    {"name": "lifecycle-schedule computes maintenance dates", "op": "lifecycle-schedule",
     "input": "fixture_findings.json",
     "set": ["start_date=2026-11-07", "horizon_months=6"],
     "expect": {"exit": 0, "extras_min": {"occurrences": 3}}},
    {"name": "intake-triage prioritises reports", "op": "intake-triage", "input": "reports.json",
     "expect": {"exit": 0, "extras_equals": {"reports": 3}, "rules_include": ["intake-critical"]}},
]

CLI_CASES = [
    {"name": "help exits cleanly", "argv": ["--help"], "expect": {"exit": 0}},
    {"name": "capabilities lists 25 categories", "argv": ["capabilities"],
     "expect": {"exit": 0, "stdout_contains": ["25 categories", "970 skill concepts"]}},
    {"name": "capabilities --json is machine readable", "argv": ["capabilities", "--json"],
     "expect": {"exit": 0, "json_equals": {"category_count": 25, "skill_count": 970}}},
    {"name": "list --category 9 shows caption skills", "argv": ["list", "--category", "9"],
     "expect": {"exit": 0, "stdout_contains": ["Captions and Subtitle Engine", "C09-S01"]}},
    {"name": "list --op captions-audit filters by operation", "argv": ["list", "--op", "captions-audit", "--json"],
     "expect": {"exit": 0, "json_min_len": 3}},
    {"name": "describe resolves a skill id", "argv": ["describe", "C02-S25", "--json"],
     "expect": {"exit": 0, "json_equals": {"operation": "web-scan"}}},
    {"name": "describe resolves a list number", "argv": ["describe", "5.1", "--json"],
     "expect": {"exit": 0, "json_equals": {"implemented": True}}},
    {"name": "describe resolves an operation", "argv": ["describe", "op:captions-audit"],
     "expect": {"exit": 0, "stdout_contains": ["Accepts: captions"]}},
    {"name": "search finds caption skills", "argv": ["search", "caption"],
     "expect": {"exit": 0, "stdout_contains": ["captions-audit"]}},
    {"name": "doctor passes all environment checks", "argv": ["doctor", "--out", "doctor-out"],
     "expect": {"exit": 0, "json_equals": {"ok": True}}},
    {"name": "routing maps every skill to an implemented operation", "argv": ["routing", "--unimplemented", "--json"],
     "expect": {"exit": 0, "json_min_len": 0, "json_len": 0}},
    {"name": "validate accepts a supported input", "argv": ["validate", "--skill", "2.25", "--input", "fixtures/bad.html"],
     "expect": {"exit": 0, "json_equals": {"ok": True}}},
    {"name": "unknown skill returns 4", "argv": ["run", "--skill", "no-such-skill", "--input", "fixtures/bad.html"],
     "expect": {"exit": 4}},
    {"name": "missing input returns 2", "argv": ["run", "--skill", "2.25", "--input", "fixtures/absent.html"],
     "expect": {"exit": 2}},
    {"name": "unknown parameter returns 2",
     "argv": ["run", "--skill", "2.25", "--input", "fixtures/bad.html", "--set", "nope=1", "--no-write"],
     "expect": {"exit": 2}},
    {"name": "bad enum value returns 2",
     "argv": ["run", "--skill", "9.1", "--input", "fixtures/captions.vtt", "--set", "mode=sideways", "--no-write"],
     "expect": {"exit": 2}},
    {"name": "input type mismatch returns 2",
     "argv": ["run", "--skill", "10.1", "--input", "fixtures/bad.html", "--no-write"],
     "expect": {"exit": 2}},
    {"name": "invalid JSON input returns 2 and is recoverable",
     "argv": ["run", "--skill", "C23-S01", "--input", "fixtures/broken.json", "--no-write"],
     "expect": {"exit": 2}},
    {"name": "recovery run succeeds after fixing the input",
     "argv": ["run", "--skill", "C23-S01", "--input", "fixtures/fixture_findings.json",
              "--no-write", "--quiet"],
     "expect": {"exit": 0}},
    {"name": "state-changing register write requires approval",
     "argv": ["run", "--skill", "C01-S27", "--input", "fixtures/fixture_findings.json",
              "--set", "mode=append", "--set", "register_path=register.jsonl",
              "--set", 'entries=[{"id":"R-1","title":"Contrast on checkout","owner":"design","status":"open"}]',
              "--no-write"],
     "expect": {"exit": 3}},
    {"name": "approved register write persists and chains",
     "argv": ["run", "--skill", "C01-S27", "--input", "fixtures/fixture_findings.json",
              "--set", "mode=append", "--set", "register_path=register.jsonl",
              "--set", 'entries=[{"id":"R-1","title":"Contrast on checkout","owner":"design","status":"open"}]',
              "--approve", "--no-write", "--quiet"],
     "expect": {"exit": 0, "file_exists": "register.jsonl"}},
    {"name": "register read mode reports the persisted entry",
     "argv": ["run", "--skill", "C01-S27", "--input", "fixtures/fixture_findings.json",
              "--set", "mode=read", "--set", "register_path=register.jsonl", "--no-write", "--quiet"],
     "expect": {"exit": 0}},
    {"name": "timeout returns 6 with a recoverable status",
     "argv": ["run", "--skill", "2.25", "--input", "fixtures/bad.html", "--timeout", "0", "--no-write"],
     "expect": {"exit": 6}},
    {"name": "results are written as JSON and Markdown",
     "argv": ["run", "--skill", "9.1", "--input", "fixtures/captions.vtt", "--out", "out", "--format", "both"],
     "expect": {"exit": 0, "files": ["out/*.json", "out/*.md"]}},
    {"name": "SARIF export is valid JSON",
     "argv": ["run", "--skill", "C02-S01", "--input", "fixtures/bad.html", "--out", "out-sarif",
              "--format", "sarif"],
     "expect": {"exit": 0, "files": ["out-sarif/*.sarif"], "sarif_valid": "out-sarif"},
     },
    {"name": "JUnit export is well-formed XML",
     "argv": ["run", "--skill", "C02-S01", "--input", "fixtures/bad.html", "--out", "out-junit",
              "--format", "junit"],
     "expect": {"exit": 0, "junit_valid": "out-junit"}},
    {"name": "fail-on-findings exits 7 for blocking findings",
     "argv": ["run", "--skill", "C02-S01", "--input", "fixtures/bad.html", "--fail-on-findings", "--no-write"],
     "expect": {"exit": 7}},
    {"name": "dry run does not write the pseudolocale bundle",
     "argv": ["run", "--skill", "19.2", "--input", "fixtures/strings-src.json",
              "--set", "output_path=pseudo.json", "--dry-run", "--no-write", "--quiet"],
     "expect": {"exit": 0, "file_absent": "pseudo.json"}},
]


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def _cli(ROOT, args, cwd):
    cmd = [sys.executable, os.path.join(ROOT, "tools-a11yl10n.py")] + args
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc


def _skill_for_op(catalog, op_name):
    for sk in catalog.skills:
        if catalog.op_for(sk)[0] == op_name:
            return sk["id"], sk["name"]
    return None, None


def _check(expect, proc, payload, case, cwd, failures):
    if not isinstance(payload, dict):
        payload = {}
    if "exit" in expect and proc.returncode != expect["exit"]:
        failures.append("exit %s != expected %s (stderr: %s)" % (
            proc.returncode, expect["exit"], proc.stderr.strip()[:400]))
    if "status" in expect:
        got = (payload or {}).get("run", {}).get("status")
        if got != expect["status"]:
            failures.append("status %r != expected %r" % (got, expect["status"]))
    findings = (payload or {}).get("findings") or []
    rules = {f.get("rule") for f in findings}
    if "min_findings" in expect and len(findings) < expect["min_findings"]:
        failures.append("%d findings < expected %d" % (len(findings), expect["min_findings"]))
    if "max_findings" in expect and len(findings) > expect["max_findings"]:
        failures.append("%d findings > allowed %d: %s" % (
            len(findings), expect["max_findings"], sorted(rules)[:12]))
    for rule in expect.get("rules_include", []):
        if rule not in rules:
            failures.append("missing expected rule %r (got %s)" % (rule, sorted(rules)))
    for rule in expect.get("rules_exclude", []):
        if rule in rules:
            failures.append("unexpected rule %r present" % rule)
    extras = (payload or {}).get("extras") or {}
    for key, value in (expect.get("extras_equals") or {}).items():
        if extras.get(key) != value:
            failures.append("extras[%r]=%r != %r" % (key, extras.get(key), value))
    for key, value in (expect.get("extras_min") or {}).items():
        actual = extras.get(key)
        if isinstance(actual, (int, float)) and isinstance(value, (int, float)):
            if actual < value:
                failures.append("extras[%r]=%r < %r" % (key, actual, value))
        elif not actual:
            failures.append("extras[%r] missing/empty (needed %r)" % (key, value))
    for key, value in (expect.get("extras_max") or {}).items():
        actual = extras.get(key)
        if isinstance(actual, (int, float)) and isinstance(value, (int, float)) and actual > value:
            failures.append("extras[%r]=%r > %r" % (key, actual, value))
    for token in expect.get("stdout_contains", []):
        if token not in proc.stdout:
            failures.append("stdout missing %r" % token)
    if "json_equals" in expect:
        try:
            data = json.loads(proc.stdout)
        except (ValueError, TypeError):
            failures.append("stdout is not JSON")
            data = {}
        for key, value in expect["json_equals"].items():
            if data.get(key) != value:
                failures.append("json[%r]=%r != %r" % (key, data.get(key), value))
    if "json_min_len" in expect:
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            data = []
            failures.append("stdout is not JSON")
        if len(data) < expect["json_min_len"]:
            failures.append("json list length %d < %d" % (len(data), expect["json_min_len"]))
        if "json_len" in expect and len(data) != expect["json_len"]:
            failures.append("json list length %d != %d" % (len(data), expect["json_len"]))
    for pattern in expect.get("files", []):
        directory, _, name = pattern.partition("/")
        target = os.path.join(cwd, directory)
        if not os.path.isdir(target):
            failures.append("output directory %r missing" % directory)
            continue
        matches = [f for f in os.listdir(target) if f.endswith(name.replace("*", ""))]
        if not matches:
            failures.append("no %r produced in %r" % (name, directory))
    if "file_exists" in expect and not os.path.exists(os.path.join(cwd, expect["file_exists"])):
        failures.append("expected file %r was not written" % expect["file_exists"])
    if "file_absent" in expect and os.path.exists(os.path.join(cwd, expect["file_absent"])):
        failures.append("file %r should not have been written" % expect["file_absent"])
    if "sarif_valid" in expect:
        directory = os.path.join(cwd, expect["sarif_valid"])
        files = [f for f in os.listdir(directory) if f.endswith(".sarif")] if os.path.isdir(directory) else []
        if not files:
            failures.append("no SARIF file produced")
        else:
            with open(os.path.join(directory, files[0]), encoding="utf-8") as fh:
                doc = json.load(fh)
            if doc.get("version") != "2.1.0" or not doc.get("runs"):
                failures.append("SARIF document is not 2.1.0 with runs")
    if "junit_valid" in expect:
        from xml.etree import ElementTree as ET
        directory = os.path.join(cwd, expect["junit_valid"])
        files = [f for f in os.listdir(directory) if f.endswith(".xml")] if os.path.isdir(directory) else []
        if not files:
            failures.append("no JUnit XML produced")
        else:
            try:
                root = ET.parse(os.path.join(directory, files[0])).getroot()
                if root.tag != "testsuite":
                    failures.append("JUnit root element is %r" % root.tag)
            except ET.ParseError as exc:
                failures.append("JUnit XML unparsable: %s" % exc)


def run(deps, OPS, ROOT, verbose=False, keep=False):
    catalog = deps["catalog"].load(ROOT)
    base = tempfile.mkdtemp(prefix="a11yl10n-selftest-")
    started = time.time()
    results = []
    try:
        fixtures = build_fixtures(base)
        covered_ops = set()
        for case in CASES:
            name = case["name"]
            op_name = case["op"]
            if op_name not in OPS:
                results.append({"name": name, "ok": False, "failures": ["operation %r not registered" % op_name],
                                "seconds": 0})
                continue
            if "input" in case and case["input"] not in fixtures:
                results.append({"name": name, "ok": False, "seconds": 0,
                                "failures": ["fixture %r unavailable in this environment" % case["input"]],
                                "skipped": True})
                continue
            skill_id = case.get("skill")
            if skill_id:
                skill = catalog.resolve(skill_id)
                skill_id = skill["id"]
            else:
                skill_id, _ = _skill_for_op(catalog, op_name)
            if not skill_id:
                results.append({"name": name, "ok": False, "seconds": 0,
                                "failures": ["no catalog skill routes to %r (orphan operation)" % op_name]})
                continue
            args = ["run", "--skill", skill_id, "--no-write", "--json"]
            if "input" in case:
                args += ["--input", fixtures[case["input"]]]
            if case.get("reference"):
                args += ["--reference", fixtures[case["reference"]]]
            for pair in case.get("set", []):
                args += ["--set", pair]
            if case.get("approve"):
                args += ["--approve"]
            t0 = time.time()
            proc = _cli(ROOT, args, base)
            elapsed = round(time.time() - t0, 2)
            payload = None
            try:
                payload = json.loads(proc.stdout)
            except ValueError:
                if proc.returncode == 0:
                    payload = None
            failures = []
            _check(case.get("expect", {}), proc, payload, case, base, failures)
            if proc.returncode == 0 and not payload:
                failures.append("no JSON result on stdout")
            if payload and payload.get("error") and case.get("expect", {}).get("exit", 0) == 0:
                failures.append("unexpected error: %s" % payload["error"])
            covered_ops.add(op_name)
            results.append({"name": name, "op": op_name, "skill": skill_id, "ok": not failures,
                            "failures": failures, "seconds": elapsed,
                            "findings": len((payload or {}).get("findings") or [])})
        for case in CLI_CASES:
            t0 = time.time()
            proc = _cli(ROOT, case["argv"], base)
            failures = []
            payload = None
            try:
                payload = json.loads(proc.stdout)
            except ValueError:
                payload = None
            _check(case.get("expect", {}), proc, payload, case, base, failures)
            results.append({"name": case["name"], "op": "cli", "ok": not failures,
                            "failures": failures, "seconds": round(time.time() - t0, 2)})
        # determinism: identical inputs must produce identical rule sets
        t0 = time.time()
        a = _cli(ROOT, ["run", "--skill", "C02-S01", "--input", fixtures["bad.html"], "--no-write", "--json"], base)
        b = _cli(ROOT, ["run", "--skill", "C02-S01", "--input", fixtures["bad.html"], "--no-write", "--json"], base)
        try:
            ra = [(f["rule"], f["locator"]) for f in json.loads(a.stdout)["findings"]]
            rb = [(f["rule"], f["locator"]) for f in json.loads(b.stdout)["findings"]]
        except (ValueError, KeyError):
            ra, rb = ["unparsable"], ["different"]
        results.append({"name": "repeat runs are deterministic", "op": "cli", "ok": ra == rb,
                        "failures": [] if ra == rb else ["rule sets differ between identical runs"],
                        "seconds": round(time.time() - t0, 2)})
        # determinism of a second engine (catalog routing consistency)
        t0 = time.time()
        r1 = _cli(ROOT, ["describe", "C09-S01", "--json"], base)
        r2 = _cli(ROOT, ["describe", "C09-S01", "--json"], base)
        results.append({"name": "routing is stable across invocations", "op": "cli",
                        "ok": r1.stdout == r2.stdout and r1.returncode == 0,
                        "failures": [] if r1.stdout == r2.stdout else ["describe output changed"],
                        "seconds": round(time.time() - t0, 2)})
        # every registered operation must be exercised by this suite
        missing_ops = sorted(set(OPS) - covered_ops)
        results.append({"name": "every registered operation is covered by the suite", "op": "coverage",
                        "ok": not missing_ops,
                        "failures": ["untested operations: %s" % ", ".join(missing_ops)] if missing_ops else [],
                        "seconds": 0})
    finally:
        if not keep:
            shutil.rmtree(base, ignore_errors=True)

    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    skipped = [r for r in results if r.get("skipped")]
    print("")
    print("accessibility-and-localization self-test")
    print("=" * 78)
    for r in results:
        status = "SKIP" if r.get("skipped") else ("PASS" if r["ok"] else "FAIL")
        label = r.get("op", "")
        print("%-4s %-56s %-20s %5.2fs" % (status, r["name"][:56], str(label)[:20], r.get("seconds") or 0))
        if not r["ok"] and (verbose or True):
            for f in r["failures"][:6]:
                print("       ! %s" % f)
    print("-" * 78)
    print("total %d | passed %d | failed %d | skipped %d | %.1fs" % (
        len(results), len(passed), len(failed), len(skipped), time.time() - started))
    print("operations exercised: %d of %d registered" % (len(set(OPS) - {o for o in OPS} | {r.get("op") for r in results if r.get("op") in OPS}), len(OPS)))
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0
