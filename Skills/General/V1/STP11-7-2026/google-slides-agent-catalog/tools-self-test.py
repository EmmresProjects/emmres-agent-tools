#!/usr/bin/env python3
"""Self-test harness for the google-slides-agent-catalog Skill Tool.

Runs every capability through the real command-line interface in a temporary working
directory, plus a clean-skill-root copy test and a local stub HTTP server for the API
adapter. No Google credentials are used and no live Google service is contacted.

Usage
    python3 tools-self-test.py                 # run all tests
    python3 tools-self-test.py --filter plan   # run tests whose name contains 'plan'
    python3 tools-self-test.py --json          # machine-readable summary
    python3 tools-self-test.py --keep          # keep the temporary directory

Exit codes: 0 all tests passed, 1 one or more tests failed, 2 harness error.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PRIMARY = "tools-gslides-catalog.py"
API = "tools-slides-api.py"
PY = sys.executable

TESTS = []


def test(name):
    def register(function):
        TESTS.append({"name": name, "function": function})
        return function

    return register


class Case:
    """Shared context for one test run: paths, temp dir, helpers."""

    def __init__(self, root, work):
        self.root = root
        self.work = work

    def path(self, *parts):
        return os.path.join(self.work, *parts)

    def run(self, tool, args, env=None, timeout=120, cwd=None):
        environment = dict(os.environ)
        environment.pop("GSLIDES_ACCESS_TOKEN", None)
        environment.pop("GSLIDES_SKILL_APPROVAL_TOKEN", None)
        environment.update(env or {})
        completed = subprocess.run(
            [PY, os.path.join(self.root, tool)] + list(args),
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
        return completed

    def primary(self, args, **kwargs):
        return self.run(PRIMARY, args, **kwargs)

    def api(self, args, **kwargs):
        return self.run(API, args, **kwargs)

    def write(self, name, content):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def json_of(self, completed):
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

MARKDOWN_SOURCE = """# Sample capability catalog

## Category 1: Deck Foundations
Category purpose: Establish the file before content work.

1. Skill Name: Deck Starter
Description: Creates a presentation with the approved name and folder.

2. Skill Name: Deck Opener
Description: Opens a presentation and repairs access problems.

## Category 2: Deck Delivery
Category purpose: Deliver decks to audiences safely.

1. Skill Name: Deck Publisher
Description: Publishes a presentation to approved channels.

2. Skill Name: Deck Archiver
Description: Packages an archived copy with static exports.
"""

HTML_SOURCE = """<!doctype html>
<html><head><style>.x{color:red}</style></head>
<body class="catalog" data-tenant="demo">
  <h2 class="cat">Category 1: Deck Foundations</h2>
  <p class="purpose">Category&nbsp;purpose: Establish the file before content work.</p>
  <ol>
    <li data-skill="1"><span class="n">1.</span> <b>Skill Name:</b> Deck Starter
        <p class="desc">Description: Creates a presentation with the <i>approved</i> name and folder.</p>
    </li>
    <li data-skill="2"><span class="n">2.</span> <b>Skill Name:</b> Deck Opener
        <p class="desc">Description: Opens a presentation and repairs access problems.</p>
    </li>
  </ol>
  <h2 class="cat">Category 2: Deck Delivery</h2>
  <p class="purpose">Category purpose: Deliver decks to audiences safely.</p>
  <ol>
    <li><b>Skill Name:</b> Deck Publisher
        <p>Description: Publishes a presentation to approved channels.</p></li>
    <li><b>Skill Name:</b> Deck Archiver
        <p>Description: Packages an archived copy with static exports.</p></li>
  </ol>
</body></html>
"""


def make_pdf(path, lines):
    """Write a minimal, valid, uncompressed single-page PDF containing the given lines."""
    def escape(text):
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content_lines = ["BT", "/F1 10 Tf", "14 TL", "40 780 Td"]
    for line in lines:
        content_lines.append(f"({escape(line)}) Tj T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    with open(path, "wb") as handle:
        handle.write(bytes(out))
    return path


# --------------------------------------------------------------------------------------
# Stub HTTP server used by the API adapter tests
# --------------------------------------------------------------------------------------

class StubState:
    def __init__(self):
        self.requests = []
        self.fail_next = 0
        self.failure_status = 429
        self.lock = threading.Lock()


def start_stub(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _record(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            with state.lock:
                state.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": body.decode("utf-8", errors="replace"),
                        "authorization": self.headers.get("Authorization", ""),
                    }
                )

        def _should_fail(self):
            with state.lock:
                if state.fail_next > 0:
                    state.fail_next -= 1
                    return True
            return False

        def _respond(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._record()
            if self._should_fail():
                self._respond(state.failure_status, {"error": {"code": state.failure_status,
                                                               "message": "temporary failure"}})
                return
            self._respond(200, {"presentationId": "stub-presentation-1", "title": "Stub deck"})

        def do_POST(self):
            self._record()
            if self._should_fail():
                self._respond(state.failure_status, {"error": {"code": state.failure_status,
                                                               "message": "temporary failure"}})
                return
            self._respond(200, {"presentationId": "stub-presentation-2", "replies": [{}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def free_port_that_refuses_connections():
    """Return a port with nothing listening, for the network-failure test."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# --------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------

@test("help and capability list")
def test_help_and_capabilities(case):
    help_run = case.primary(["help"])
    assert help_run.returncode == 0, help_run.stderr
    assert "USAGE" in help_run.stdout and "EXIT CODES" in help_run.stdout, help_run.stdout[:400]

    caps = case.primary(["capabilities", "--json"])
    assert caps.returncode == 0, caps.stderr
    payload = case.json_of(caps)
    assert payload and len(payload["capabilities"]) >= 10, "capability list too short"
    commands = {entry["command"] for entry in payload["capabilities"]}
    for expected in ("plan", "spec", "coverage", "ingest", "approve-check", "doctor"):
        assert expected in commands, f"{expected} missing from capability list"
    return f"{len(commands)} commands advertised"


@test("doctor reports healthy environment")
def test_doctor(case):
    run = case.primary(["doctor", "--json"])
    payload = case.json_of(run)
    assert run.returncode == 0, run.stdout + run.stderr
    assert payload["healthy"] is True, payload
    return "all environment checks pass"


@test("categories and list filters")
def test_catalog_listing(case):
    cats = case.primary(["categories", "--json"])
    assert cats.returncode == 0, cats.stderr
    cat_payload = case.json_of(cats)
    assert cat_payload["counts"] == {"categories": 34, "skills": 408}, cat_payload["counts"]

    category_list = case.primary(["list", "--category", "9", "--json"])
    list_payload = case.json_of(category_list)
    assert category_list.returncode == 0, category_list.stderr
    assert list_payload and len(list_payload["skills"]) == 12, "category 9 must hold 12 skills"
    assert all(row["category_id"] == "text-boxes-and-typography" for row in list_payload["skills"])

    search = case.primary(["list", "--search", "alt text", "--json"])
    search_payload = case.json_of(search)
    assert search.returncode == 0, search.stderr
    assert 0 < len(search_payload["skills"]) < 408, "search filter did not narrow the list"
    ids = {row["skill_id"] for row in search_payload["skills"]}
    assert "image-alt-text-author" in ids, ids
    return f"34 categories / 408 skills, search returned {len(ids)}"


@test("describe skill and category, unknown skill recovery")
def test_describe(case):
    ok_run = case.primary(["describe", "--skill", "image-alt-text-author", "--json"])
    assert ok_run.returncode == 0, ok_run.stderr
    payload = case.json_of(ok_run)
    assert payload["risk"]["tier"] == "reversible-edit", payload["risk"]
    assert payload["phase"]["id"] == "build", payload["phase"]
    assert payload["neighbours"], "expected neighbouring skills"

    cat_run = case.primary(["describe", "--category", "localization", "--json"])
    assert cat_run.returncode == 0, cat_run.stderr
    assert len(case.json_of(cat_run)["skills"]) == 12

    bad = case.primary(["describe", "--skill", "crop-images-nowhere", "--json"])
    assert bad.returncode == 2, bad.returncode
    error = json.loads(bad.stderr)
    assert error["error"]["code"] == "unknown-skill"
    assert "Did you mean" in (error["error"].get("hint") or "")
    return "risk/phase resolved, unknown id produced a suggestion"


@test("plan produces ordered, schema-valid plan and a plan file")
def test_plan_basic(case):
    out = case.path("plan-basic.json")
    run = case.primary([
        "plan", "--request", "prepare a client review deck and share it read-only with reviewers",
        "--out", out, "--json",
    ])
    assert run.returncode == 0, run.stderr
    payload = case.json_of(run)
    plan = payload["plan"]
    assert plan["validation"]["status"] == "ok", plan["validation"]
    assert plan["matches"], "no matches"
    assert plan["approvals"], "sharing request must produce approval gates"
    assert os.path.isfile(out), "plan file was not written"

    order = [phase["id"] for phase in plan["phases"]]
    expected_order = ["prepare", "design-system", "structure", "build", "layout", "media", "interop",
                      "localize", "protect", "assure", "verify", "recover", "automate", "deliver",
                      "access", "distribute", "support", "evaluate"]
    positions = [expected_order.index(phase_id) for phase_id in order]
    assert positions == sorted(positions), f"phases not in configured order: {order}"
    for phase in plan["phases"]:
        assert phase["steps"] and phase["checkpoint"], phase

    validate = case.primary(["validate", "--plan", out])
    assert validate.returncode == 0, validate.stdout + validate.stderr
    return f"{len(plan['matches'])} matches, {len(plan['phases'])} phases, {len(plan['approvals'])} gates"


@test("changed request changes the matched capability")
def test_plan_input_sensitivity(case):
    first = case.json_of(case.primary(["plan", "--request", "crop images and write alt text", "--json"]))
    second = case.json_of(case.primary(["plan", "--request", "translate the deck into Japanese", "--json"]))
    first_top = first["plan"]["matches"][0]["category_id"]
    second_top = second["plan"]["matches"][0]["category_id"]
    assert first_top != second_top, f"same lead category for different requests: {first_top}"
    assert first_top in ("images-and-image-formatting", "accessibility-and-inclusive-design", "format-options-and-object-effects"), first_top
    assert second_top == "localization-and-multilingual-slides", second_top
    return f"{first_top} vs {second_top}"


@test("plan category mode and approval-aware exit codes")
def test_plan_modes_and_exit_codes(case):
    category_run = case.primary(["plan", "--category", "22", "--max-matches", "5", "--json"])
    assert category_run.returncode == 0, category_run.stderr
    plan = case.json_of(category_run)["plan"]
    assert all(match["category_id"] == "accessibility-and-inclusive-design" for match in plan["matches"])

    gated = case.primary([
        "plan", "--request", "publish the deck to the web", "--fail-on-approval", "--json",
    ])
    assert gated.returncode == 3, gated.returncode
    gated_plan = case.json_of(gated)["plan"]
    assert gated_plan["approvals"], "expected approval gates"
    hard = any(
        "hard gate terms" in " ".join(approval["reasons"]) for approval in gated_plan["approvals"]
    )
    assert hard, "hard gate terms were not recorded for a publish-to-web request"
    return f"category mode ok, {len(gated_plan['approvals'])} gates, exit 3 as documented"


@test("plan and validate reject bad input with recovery hints")
def test_plan_invalid_inputs(case):
    missing = case.primary(["plan", "--json"])
    assert missing.returncode == 2, missing.returncode
    assert json.loads(missing.stderr)["error"]["code"] == "missing-request"

    bad_category = case.primary(["plan", "--request", "crop images", "--category", "99", "--json"])
    assert bad_category.returncode == 2, bad_category.returncode
    error = json.loads(bad_category.stderr)["error"]
    assert error["code"] == "unknown-category"
    assert "categories" in (error.get("hint") or "")

    stopwords = case.primary(["plan", "--request", "the and of to", "--json"])
    assert stopwords.returncode == 2, stopwords.returncode
    assert json.loads(stopwords.stderr)["error"]["code"] == "empty-request"

    missing_plan = case.primary(["validate", "--plan", case.path("does-not-exist.json"), "--json"])
    assert missing_plan.returncode == 2, missing_plan.returncode
    assert json.loads(missing_plan.stderr)["error"]["code"] == "file-not-found"

    broken = case.write("broken-plan.json", "{not json")
    bad_json = case.primary(["validate", "--plan", broken, "--json"])
    assert bad_json.returncode == 2, bad_json.returncode
    assert json.loads(bad_json.stderr)["error"]["code"] == "json-invalid"

    schema_fail = case.write("wrong-plan.json", json.dumps({"plan_version": "1.0"}))
    failed = case.primary(["validate", "--plan", schema_fail])
    assert failed.returncode == 5, failed.returncode
    assert "missing required property" in failed.stdout, failed.stdout
    return "missing request, bad category, stopwords, missing file, bad JSON and schema failure all handled"


@test("stdin request and plan from file")
def test_plan_stdin(case):
    completed = subprocess.run(
        [PY, os.path.join(case.root, PRIMARY), "plan", "--request", "-", "--json"],
        input="reorder slides and clean up transitions",
        capture_output=True, text=True, timeout=120, cwd=case.root,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["plan"]["request"]["text"].startswith("reorder slides"), payload["plan"]["request"]

    request_file = case.write("request.txt", "print handouts for a board meeting\n")
    from_file = case.primary(["plan", "--request-file", request_file, "--json"])
    assert from_file.returncode == 0, from_file.stderr
    assert "handouts" in json.loads(from_file.stdout)["plan"]["request"]["text"]
    return "stdin and file based requests both parsed"


@test("spec renders all template sections for any skill")
def test_spec(case):
    out = case.path("spec.md")
    run = case.primary(["spec", "--skill", "public-publishing-gate", "--out", out, "--json"])
    assert run.returncode == 0, run.stderr
    payload = case.json_of(run)["specification"]
    assert payload["sections_rendered"] == 12, payload
    document = open(out, encoding="utf-8").read()
    for number in range(1, 13):
        assert f"## {number}." in document, f"section {number} missing"
    assert "security-sensitive" in document and "public-publishing-gate" in document
    assert "Decide:" in document, "draft review prompts missing"

    second_out = case.path("spec2.md")
    case.primary(["spec", "--skill", "image-alt-text-author", "--out", second_out])
    second = open(second_out, encoding="utf-8").read()
    assert "image-alt-text-author" in second and "reversible-edit" in second
    assert document != second, "spec output did not change with the skill"

    missing = case.primary(["spec", "--skill", "no-such-skill", "--json"])
    assert missing.returncode == 2
    return "12/12 sections, output differs per skill, unknown skill rejected"


@test("ingest plain text, Markdown, HTML and PDF sources")
def test_ingest(case):
    text_source = case.write("catalog.txt", MARKDOWN_SOURCE)
    text_run = case.primary(["ingest", "--source", text_source, "--json"])
    assert text_run.returncode == 0, text_run.stderr
    counts = case.json_of(text_run)["catalog"]["counts"]
    assert counts == {"categories": 2, "skills": 4}, counts

    md_source = case.write("catalog.md", MARKDOWN_SOURCE)
    md_out = case.path("md-catalog.json")
    md_run = case.primary(["ingest", "--source", md_source, "--out", md_out, "--validate"])
    assert md_run.returncode == 0, md_run.stdout + md_run.stderr
    parsed = json.load(open(md_out, encoding="utf-8"))
    assert parsed["counts"] == {"categories": 2, "skills": 4}
    assert parsed["categories"][0]["skills"][0]["id"] == "deck-starter"
    assert parsed["categories"][0]["purpose"].startswith("Establish the file")

    html_source = case.write("catalog.html", HTML_SOURCE)
    html_run = case.primary(["ingest", "--source", html_source, "--json"])
    assert html_run.returncode == 0, html_run.stderr
    html_payload = case.json_of(html_run)
    assert html_payload["catalog"]["counts"] == {"categories": 2, "skills": 4}, html_payload["catalog"]
    assert html_payload["source"]["format"] == "html"

    pdf_path = case.path("catalog.pdf")
    make_pdf(pdf_path, [
        "Category 1: Deck Foundations",
        "Category purpose: Establish the file before content work.",
        "1. Skill Name: Deck Starter",
        "Description: Creates a presentation with the approved name and folder.",
        "2. Skill Name: Deck Opener",
        "Description: Opens a presentation and repairs access problems.",
    ])
    pdf_run = case.primary(["ingest", "--source", pdf_path, "--json"])
    if pdf_run.returncode == 0:
        pdf_counts = case.json_of(pdf_run)["catalog"]["counts"]
        pdf_detail = f"pdf parsed to {pdf_counts['categories']} categories / {pdf_counts['skills']} skills"
        assert pdf_counts["skills"] == 2, pdf_counts
    else:
        error = json.loads(pdf_run.stderr)["error"]
        assert error["code"] == "pdf-support-missing", error
        pdf_detail = "pdf support unavailable (optional pypdf not installed) - documented limitation"

    missing = case.primary(["ingest", "--source", case.path("nope.txt"), "--json"])
    assert missing.returncode == 2
    empty = case.write("empty.txt", "")
    empty_run = case.primary(["ingest", "--source", empty, "--json"])
    assert empty_run.returncode == 2
    assert json.loads(empty_run.stderr)["error"]["code"] == "source-empty"
    return f"txt/md/html parsed, {pdf_detail}, empty and missing sources rejected"


@test("coverage report, threshold and unknown-id recovery")
def test_coverage(case):
    manifest = case.write("manifest.json", json.dumps({
        "skills": {
            "presentation-creation-router": "implemented",
            "open-and-recovery-handler": "implemented",
            "slide-import-orchestrator": "partial",
            "image-crop-specialist": "implemented",
            "public-publishing-gate": "planned",
            "presentation-playback-walkthrough": "absent",
        }
    }))
    run = case.primary(["coverage", "--manifest", manifest, "--min-coverage", "0.005", "--json"])
    assert run.returncode == 0, run.stderr
    report = case.json_of(run)["report"]
    assert report["totals"]["implemented"] == 3, report["totals"]
    assert report["totals"]["partial"] == 1
    assert report["totals"]["unmapped"] == 402, report["totals"]
    assert 0 < report["overall_coverage"] < 1
    assert len(report["categories"]) == 34
    assert any(gap["status"] == "absent" for gap in report["gaps"])

    strict = case.primary(["coverage", "--manifest", manifest, "--min-coverage", "0.9"])
    assert strict.returncode == 5, strict.returncode
    assert "FAIL" in strict.stdout

    bad_manifest = case.write("bad-manifest.json", json.dumps({
        "skills": {"presentation-creation-routr": "implemented"}
    }))
    bad = case.primary(["coverage", "--manifest", bad_manifest, "--json"])
    assert bad.returncode == 2, bad.returncode
    error = json.loads(bad.stderr)["error"]
    assert error["code"] == "unknown-skill"
    assert error["details"]["suggestions"][0]["suggestion"] == "presentation-creation-router"

    fixed = case.write("fixed-manifest.json", json.dumps({
        "skills": {"presentation-creation-router": "implemented"}
    }))
    recovered = case.primary(["coverage", "--manifest", fixed])
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr

    bad_status = case.write("bad-status.json", json.dumps({"skills": {"presentation-creation-router": "done"}}))
    rejected = case.primary(["coverage", "--manifest", bad_status, "--json"])
    assert rejected.returncode == 2
    assert "Unknown status" in json.loads(rejected.stderr)["error"]["message"]
    return "coverage scored, threshold enforced, typo suggested and recovered"


@test("approval record flow blocks, approves and detects tampering")
def test_approval_flow(case):
    plan_path = case.path("gated-plan.json")
    plan_run = case.primary([
        "plan", "--request", "delete four stale slides and export the deck to PDF", "--out", plan_path,
    ])
    assert plan_run.returncode == 0, plan_run.stderr
    plan = json.load(open(plan_path, encoding="utf-8"))
    assert plan["approvals"], "expected gated steps"

    record_path = case.path("record.json")
    request_run = case.primary(["approve-request", "--plan", plan_path, "--out", record_path])
    assert request_run.returncode == 0, request_run.stderr
    record = json.load(open(record_path, encoding="utf-8"))
    assert record["status"] == "pending-approval"
    assert record["plan_fingerprint"].startswith("sha256:")

    unsigned = case.primary(["approve-check", "--plan", plan_path, "--record", record_path, "--json"])
    assert unsigned.returncode == 6, unsigned.returncode
    assert case.json_of(unsigned)["decision"] == "denied"

    record["status"] = "approved"
    record["approver"] = "Test Operator"
    record["approved_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record["acknowledged_skills"] = [item["skill_id"] for item in record["approval_requirements"]]
    json.dump(record, open(record_path, "w", encoding="utf-8"), indent=2)

    approved = case.primary(["approve-check", "--plan", plan_path, "--record", record_path, "--json"])
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert case.json_of(approved)["decision"] == "approved"

    plan["request"]["text"] = "delete every slide and export the deck to PDF"
    json.dump(plan, open(plan_path, "w", encoding="utf-8"), indent=2)
    tampered = case.primary(["approve-check", "--plan", plan_path, "--record", record_path, "--json"])
    assert tampered.returncode == 6, tampered.returncode
    problems = case.json_of(tampered)["problems"]
    assert any("fingerprint" in problem for problem in problems), problems

    stale = case.path("stale-record.json")
    record["approved_at"] = "2020-01-01T00:00:00Z"
    json.dump(record, open(stale, "w", encoding="utf-8"), indent=2)
    expired = case.primary(["approve-check", "--plan", plan_path, "--record", stale, "--json"])
    assert expired.returncode == 6
    assert any("hour limit" in problem for problem in case.json_of(expired)["problems"])
    return "unsigned denied, signed approved, tampering and expiry both blocked"


@test("API adapter allowlist, approval gate and dry run")
def test_api_static(case):
    ops = case.api(["--list-ops", "--json"])
    assert ops.returncode == 0, ops.stderr
    table = case.json_of(ops)
    assert len(table["operations"]) == 6, table["operations"]
    assert "deleteObject" in table["destructive_batch_requests"]

    dry = case.api(["get-presentation", "--presentation-id", "stub-presentation-1", "--dry-run", "--json"], env={})
    assert dry.returncode == 0, dry.stderr
    payload = case.json_of(dry)
    assert payload["dry_run"] is True
    assert payload["request"]["headers"]["Authorization"] == "<redacted>"
    assert "stub-presentation-1" in payload["request"]["url"]

    no_approval = case.api([
        "create-presentation", "--title", "Test deck", "--dry-run", "--json",
    ], env={"GSLIDES_ACCESS_TOKEN": "test-token"})
    assert no_approval.returncode == 2, no_approval.returncode
    assert json.loads(no_approval.stderr)["error"]["code"] == "approval-required"

    bad_kind = case.write("bad-requests.json", json.dumps([{"runArbitraryCode": {}}]))
    rejected = case.api([
        "batch-update", "--presentation-id", "stub-presentation-1", "--requests-file", bad_kind,
        "--approve", "token-value", "--dry-run", "--json",
    ])
    assert rejected.returncode == 2, rejected.returncode
    assert json.loads(rejected.stderr)["error"]["code"] == "requests-not-allowed"

    destructive_requests = case.write("destructive.json", json.dumps([{"deleteObject": {"objectId": "s1"}}]))
    needs_confirm = case.api([
        "batch-update", "--presentation-id", "stub-presentation-1",
        "--requests-file", destructive_requests, "--approve", "token-value", "--dry-run", "--json",
    ])
    assert needs_confirm.returncode == 2, needs_confirm.returncode
    assert json.loads(needs_confirm.stderr)["error"][
        "code"] == "destructive-confirmation-required"

    confirmed = case.api([
        "batch-update", "--presentation-id", "stub-presentation-1",
        "--requests-file", destructive_requests, "--approve", "token-value",
        "--confirm-destructive", "--dry-run", "--json",
    ])
    assert confirmed.returncode == 0, confirmed.stderr

    insecure = case.api([
        "get-presentation", "--presentation-id", "stub-presentation-1",
        "--slides-base-url", "http://example.com/v1", "--dry-run", "--json",
    ])
    assert insecure.returncode == 2
    assert json.loads(insecure.stderr)["error"]["code"] == "insecure-base-url"

    missing_token = case.api(["get-presentation", "--presentation-id", "stub-presentation-1", "--json"])
    assert missing_token.returncode == 2
    assert json.loads(missing_token.stderr)["error"]["code"] == "missing-credentials"
    return "6 operations, allowlist, approval and destructive gates, dry run, URL and token checks"


@test("API adapter live transport against a local stub server")
def test_api_stub_transport(case):
    state = StubState()
    server, thread = start_stub(state)
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    env = {"GSLIDES_ACCESS_TOKEN": "stub-token", "GSLIDES_SKILL_APPROVAL_TOKEN": "approval-secret"}
    try:
        read = case.api([
            "get-presentation", "--presentation-id", "stub-presentation-1",
            "--slides-base-url", base, "--drive-base-url", base, "--json",
        ], env=env, timeout=60)
        assert read.returncode == 0, read.stdout + read.stderr
        payload = case.json_of(read)
        assert payload["http_status"] == 200 and payload["response"]["presentationId"] == "stub-presentation-1"
        assert state.requests[-1]["authorization"] == "Bearer stub-token"
        assert "stub-token" not in read.stdout, "access token leaked into output"

        requests_file = case.write("ok-requests.json", json.dumps([
            {"replaceAllText": {"containsText": {"text": "Old"}, "replaceText": "New"}}
        ]))
        write = case.api([
            "batch-update", "--presentation-id", "stub-presentation-1",
            "--requests-file", requests_file, "--slides-base-url", base,
            "--approve", "approval-secret", "--json",
        ], env=env, timeout=60)
        assert write.returncode == 0, write.stdout + write.stderr
        assert json.loads(state.requests[-1]["body"])["requests"][0]["replaceAllText"]["replaceText"] == "New"

        wrong_token = case.api([
            "batch-update", "--presentation-id", "stub-presentation-1",
            "--requests-file", requests_file, "--slides-base-url", base,
            "--approve", "not-the-token", "--json",
        ], env=env, timeout=60)
        assert wrong_token.returncode == 2, wrong_token.returncode
        assert json.loads(wrong_token.stderr)["error"]["code"] == "approval-mismatch"

        state.fail_next = 1
        before = len(state.requests)
        retried = case.api([
            "get-presentation", "--presentation-id", "stub-presentation-1",
            "--slides-base-url", base, "--retries", "2", "--json",
        ], env=env, timeout=60)
        assert retried.returncode == 0, retried.stdout + retried.stderr
        payload = case.json_of(retried)
        assert payload["attempts"] == 2, payload["attempts"]
        assert len(state.requests) - before == 2, "retry did not re-send the request"

        dead_port = free_port_that_refuses_connections()
        failed = case.api([
            "get-presentation", "--presentation-id", "stub-presentation-1",
            "--slides-base-url", f"http://127.0.0.1:{dead_port}/v1",
            "--retries", "1", "--timeout", "2", "--json",
        ], env=env, timeout=90)
        assert failed.returncode == 8, failed.returncode
        assert case.json_of(failed)["error"]["code"] == "network"
        return "read, approved write, token mismatch, 429 retry and network failure all exercised"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@test("clean skill root with a parent SKILL.md, relative paths only")
def test_clean_skill_root(case):
    root = case.path("clean-root")
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, "google-slides-agent-catalog")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(case.root, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(root, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("# Test parent skill\n\nUses google-slides-agent-catalog as a Skill Tool.\n")

    assert not os.path.exists(os.path.join(target, "SKILL.md")), "Skill Tool folder must not contain SKILL.md"
    entries = sorted(os.listdir(target))
    for entry in entries:
        assert entry.startswith(("tools-", "knowledge-", "asset-")), f"unclassified file: {entry}"
        assert os.path.isfile(os.path.join(target, entry)), f"unexpected subfolder: {entry}"

    run = subprocess.run(
        [PY, "google-slides-agent-catalog/" + PRIMARY, "plan",
         "--request", "apply the brand theme and fix text overflow",
         "--out", "plan-out.json", "--json"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert os.path.isfile(os.path.join(root, "plan-out.json")), "relative --out path not honoured"
    payload = json.loads(run.stdout)
    assert payload["plan"]["matches"], "no matches from the clean root"

    spec = subprocess.run(
        [PY, "google-slides-agent-catalog/" + PRIMARY, "spec", "--skill", "theme-font-mapper",
         "--out", "spec-out.md"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert spec.returncode == 0, spec.stdout + spec.stderr
    assert os.path.isfile(os.path.join(root, "spec-out.md"))

    doctor = subprocess.run(
        [PY, "google-slides-agent-catalog/" + PRIMARY, "doctor"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    return f"{len(entries)} prefixed files, no SKILL.md, relative paths verified"


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    keep = "--keep" in argv
    verbose = "--verbose" in argv
    name_filter = None
    if "--filter" in argv:
        position = argv.index("--filter")
        if position + 1 < len(argv):
            name_filter = argv[position + 1].lower()

    if not os.path.isfile(os.path.join(HERE, PRIMARY)):
        sys.stderr.write(f"Harness error: {PRIMARY} not found beside this file.\n")
        return 2

    selected = [entry for entry in TESTS if not name_filter or name_filter in entry["name"].lower()]
    work = tempfile.mkdtemp(prefix="gslides-catalog-selftest-")
    case = Case(HERE, work)
    results = []
    started = time.time()
    if not as_json:
        print(f"Running {len(selected)} test group(s) in {work}\n")
    for entry in selected:
        group_start = time.time()
        try:
            detail = entry["function"](case)
            results.append({"test": entry["name"], "status": "pass", "detail": detail,
                            "seconds": round(time.time() - group_start, 2)})
            if not as_json:
                print(f"PASS  {entry['name']}  ({detail})")
        except AssertionError as error:
            results.append({"test": entry["name"], "status": "fail", "detail": str(error),
                            "seconds": round(time.time() - group_start, 2)})
            if not as_json:
                print(f"FAIL  {entry['name']}  -> {error}")
                if verbose:
                    traceback.print_exc()
        except Exception as error:  # noqa: BLE001 - the harness must report, not crash
            results.append({"test": entry["name"], "status": "error",
                            "detail": f"{type(error).__name__}: {error}",
                            "seconds": round(time.time() - group_start, 2)})
            if not as_json:
                print(f"ERROR {entry['name']}  -> {type(error).__name__}: {error}")
                if verbose:
                    traceback.print_exc()

    passed = sum(1 for result in results if result["status"] == "pass")
    failed = [result for result in results if result["status"] != "pass"]
    summary = {
        "passed": passed,
        "failed": len(failed),
        "total": len(results),
        "seconds": round(time.time() - started, 2),
        "results": results,
    }
    if as_json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print()
        print(f"{passed}/{len(results)} test groups passed in {summary['seconds']}s")
        for result in failed:
            print(f"  - {result['status'].upper()}: {result['test']}: {result['detail']}")
    if keep:
        print(f"Temporary directory kept: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
