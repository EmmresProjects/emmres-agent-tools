#!/usr/bin/env python3
"""End-to-end self-test for the word-document-agent skill tool.

Every test drives the real CLI (``tools-word-doc-agent.py``) as a
subprocess and inspects the JSON envelope plus the written artefacts, so
the suite proves the packaged files work together without importing
private helpers.

Usage
-----
    python3 tools-selftest.py                 # run everything
    python3 tools-selftest.py --verbose       # show every assertion
    python3 tools-selftest.py --json          # machine-readable report
    python3 tools-selftest.py --workdir DIR   # keep artefacts in DIR

Exit code is 0 only when every test passes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "tools-word-doc-agent.py")
PY = sys.executable

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAIAQMAAAD+wSzIAAAABlBMVEX///+/v7+jQ3Y5AAAADklEQVQI12P4AIX8EAgALgAD/aNpbtEAAAAASUVORK5CYII=")

MARKDOWN = """# Field Guide

Intro paragraph with **bold**, *italic*, `code` and a [link](https://example.org/guide).

## Sections

- first item
- second item

## Data

| Region | Revenue |
| ------ | ------- |
| North  | 1200    |
| South  | 980     |

1. step one
2. step two

> A quoted note.

### Tail

Ship the {{quarter}} report. TODO: confirm owners.
"""


class Report:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.results = []
        self.started = time.time()

    def add(self, group, name, ok, detail=""):
        self.results.append({"group": group, "test": name, "ok": bool(ok),
                             "detail": str(detail)[:400]})
        if self.verbose or not ok:
            sys.stdout.write("  [%s] %-46s %s\n" % ("PASS" if ok else "FAIL", name,
                                                    detail if not ok else ""))
            sys.stdout.flush()

    @property
    def failures(self):
        return [r for r in self.results if not r["ok"]]

    def summary(self):
        groups = {}
        for result in self.results:
            total, failed = groups.get(result["group"], (0, 0))
            groups[result["group"]] = (total + 1, failed + (0 if result["ok"] else 1))
        return groups


def run(args, cwd, timeout=180):
    started = time.time()
    completed = subprocess.run([PY, ENTRY] + args, capture_output=True, text=True,
                               cwd=cwd, timeout=timeout)
    elapsed = int((time.time() - started) * 1000)
    envelope = None
    stdout = completed.stdout.strip()
    if stdout.startswith("{"):
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            envelope = None
    return {"code": completed.returncode, "stdout": stdout,
            "stderr": completed.stderr.strip(), "envelope": envelope,
            "elapsed_ms": elapsed}


def payload(result):
    if not result["envelope"]:
        return {}
    return result["envelope"].get("result") or {}


def error_code(result):
    if result["envelope"] and not result["envelope"].get("ok", True):
        return (result["envelope"].get("error") or {}).get("code")
    if result["code"] and "error [" in result["stderr"]:
        match = re.search(r"error \[([A-Z_]+)\]", result["stderr"])
        return match.group(1) if match else None
    return None


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def make_fixtures(work):
    fixtures = {}
    fixtures["markdown"] = os.path.join(work, "guide.md")
    with open(fixtures["markdown"], "w", encoding="utf-8") as fh:
        fh.write(MARKDOWN)
    fixtures["csv"] = os.path.join(work, "data.csv")
    with open(fixtures["csv"], "w", encoding="utf-8") as fh:
        fh.write("Region,Revenue,Notes\nNorth,1200,ok\nSouth,980,watch\nEast,1500,\n")
    fixtures["records"] = os.path.join(work, "records.json")
    with open(fixtures["records"], "w", encoding="utf-8") as fh:
        json.dump([{"id": "A-1", "name": "Ada Lovelace", "role": "Analyst"},
                   {"id": "B-2", "name": "Grace Hopper", "role": "Engineer"}], fh)
    fixtures["form"] = os.path.join(work, "form.json")
    with open(fixtures["form"], "w", encoding="utf-8") as fh:
        json.dump({"title": "Intake", "controls": [
            {"type": "text", "alias": "Full name", "tag": "name", "value": "Jane Doe"},
            {"type": "date", "alias": "Start date", "tag": "start"},
            {"type": "dropdown", "alias": "Team", "tag": "team",
             "items": ["Ops", "Eng"], "value": "Ops"},
            {"type": "checkbox", "alias": "Approved", "tag": "approved", "checked": True}]}, fh)
    fixtures["png"] = os.path.join(work, "logo.png")
    with open(fixtures["png"], "wb") as fh:
        fh.write(PNG_1PX)
    fixtures["config"] = os.path.join(work, "tight-config.json")
    with open(fixtures["config"], "w", encoding="utf-8") as fh:
        json.dump({"limits": {"max_replacements": 3, "max_merge_records": 5},
                   "defaults": {"sanitized_author": "Release Team"}}, fh)
    fixtures["broken"] = os.path.join(work, "broken.docx")
    with open(fixtures["broken"], "wb") as fh:
        fh.write(b"this is not a zip package")
    fixtures["legacy"] = os.path.join(work, "old.doc")
    with open(fixtures["legacy"], "wb") as fh:
        fh.write(b"legacy binary")
    fixtures["plain"] = os.path.join(work, "notes.txt")
    with open(fixtures["plain"], "w", encoding="utf-8") as fh:
        fh.write("plain text")
    return fixtures


def craft_tracked_changes(source, target):
    """Copy a package and inject tracked insertions/deletions plus hidden text."""
    injected = (
        '<w:p><w:r><w:t xml:space="preserve">Draft text </w:t></w:r>'
        '<w:ins w:id="901" w:author="Reviewer A" w:date="2026-02-01T10:00:00Z">'
        '<w:r><w:t>inserted clause</w:t></w:r></w:ins>'
        '<w:del w:id="902" w:author="Reviewer B" w:date="2026-02-02T11:00:00Z">'
        '<w:r><w:delText>deleted clause</w:delText></w:r></w:del>'
        '<w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden note</w:t></w:r></w:p>')
    with zipfile.ZipFile(source) as zin:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    xml = data.decode("utf-8")
                    xml = xml.replace("<w:sectPr", injected + "<w:sectPr", 1)
                    data = xml.encode("utf-8")
                zout.writestr(item, data)
    return target


# --------------------------------------------------------------------------
# test suite
# --------------------------------------------------------------------------

def suite(report, work, fixtures):
    doc = os.path.join(work, "guide.docx")

    def check(group, name, condition, detail=""):
        report.add(group, name, condition, detail)
        return condition

    # --- 1. CLI surface ---------------------------------------------------
    res = run(["capabilities", "--json"], work)
    payload_caps = payload(res)
    check("cli", "capabilities exits 0", res["code"] == 0, res["stderr"])
    check("cli", "capabilities lists operations",
          payload_caps.get("operation_count", 0) >= 29, payload_caps.get("operation_count"))
    check("cli", "catalog totals are 53/636",
          payload_caps.get("catalog", {}).get("categories") == 53 and
          payload_caps.get("catalog", {}).get("skills") == 636,
          payload_caps.get("catalog"))
    raw = run(["--help"], work)
    check("cli", "--help prints usage", "usage:" in raw["stdout"].lower(), raw["stdout"][:120])
    res = run(["--version"], work)
    check("cli", "--version prints a version", "word-document-agent" in res["stdout"])
    res = run(["capabilities", "--param", "all=true", "--json"], work)
    all_catalog = payload(res).get("catalog", {})
    skills = all_catalog.get("skills") or []
    check("cli", "capabilities --param all lists 636 skills", len(skills) == 636, len(skills))
    check("cli", "every catalog skill maps to an operation",
          all(s.get("op") for s in skills))

    # --- 2. create --------------------------------------------------------
    res = run(["create", "--output", doc, "--param", "markdown-file=" + fixtures["markdown"],
               "--param", "title=Field Guide", "--json"], work)
    created = payload(res)
    check("create", "create from markdown exits 0", res["code"] == 0, res["stderr"])
    check("create", "package validates", created.get("self_check", {}).get("valid") is True,
          created.get("self_check"))
    check("create", "writes a file", os.path.isfile(doc) and os.path.getsize(doc) > 2000)
    kinds = [b["type"] for b in created.get("blocks") or []]
    check("create", "renders headings, lists and tables",
          {"heading", "list", "table"} <= set(kinds), kinds)
    with zipfile.ZipFile(doc) as zf:
        names = zf.namelist()
        document_xml = zf.read("word/document.xml").decode("utf-8")
    check("create", "package contains required parts",
          {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
           "word/styles.xml"} <= set(names), names)
    check("create", "table markup present", "<w:tbl>" in document_xml)
    check("create", "hyperlinks become relationships", "hyperlink" in document_xml)
    check("create", "refuses to overwrite", run(["create", "--output", doc,
                                                 "--param", "markdown=hi"], work)["code"] == 2)

    # --- 3. inspect -------------------------------------------------------
    res = run(["inspect", "--input", doc, "--json"], work)
    profile = payload(res)
    check("inspect", "inspect exits 0", res["code"] == 0, res["stderr"])
    check("inspect", "counts headings", len(profile.get("content", {}).get("outline", [])) >= 4,
          len(profile.get("content", {}).get("outline", [])))
    check("inspect", "counts tables", len(profile.get("content", {}).get("tables", [])) == 1)
    check("inspect", "reports properties", profile.get("properties", {}).get("core", {}).get("title")
          == "Field Guide", profile.get("properties", {}).get("core"))
    check("inspect", "reports protection state",
          profile.get("properties", {}).get("protection", {}).get("protected") is False)
    check("inspect", "detects placeholders through proofing",
          profile.get("proofing", {}).get("counts", {}).get("placeholder-leftover") == 1,
          profile.get("proofing", {}).get("counts"))
    text = run(["inspect", "--input", doc], work)["stdout"]
    check("inspect", "human summary is readable", "format=docx" in text and "words=" in text, text)

    # --- 4. proofing / a11y / qa -----------------------------------------
    res = run(["proof", "--input", doc, "--json"], work)
    proof = payload(res)
    check("proof", "finds the placeholder", proof.get("counts", {}).get("placeholder-leftover") == 1)
    check("proof", "finds the TODO marker", proof.get("counts", {}).get("todo-marker") == 1)
    res = run(["a11y", "--input", doc, "--json"], work)
    a11y = payload(res)
    check("a11y", "clean document scores high", a11y.get("score", 0) >= 90, a11y.get("score"))
    res = run(["qa", "--input", doc, "--json"], work)
    check("qa", "gate blocks the unfinished document", res["code"] == 8, res["code"])
    check("qa", "gate failure is structured", error_code(res) == "POLICY", error_code(res))
    res = run(["qa", "--input", doc, "--param", "report_only=true", "--json"], work)
    gate = payload(res)
    check("qa", "report-only gate returns a verdict",
          res["code"] == 0 and gate.get("verdict", "").endswith("fail"), gate.get("verdict"))
    check("qa", "gate lists next actions", isinstance(gate.get("next_actions"), list))

    # --- 5. replace -------------------------------------------------------
    fixed = os.path.join(work, "guide-fixed.docx")
    res = run(["replace", "--input", doc, "--output", fixed, "--param", "find={{quarter}}",
               "--param", "replace=Q3", "--json"], work)
    replaced = payload(res)
    check("replace", "replaces the placeholder", replaced.get("replacements") == 1,
          replaced.get("replacements"))
    res = run(["proof", "--input", fixed, "--json"], work)
    check("replace", "placeholder removed from the copy",
          payload(res).get("counts", {}).get("placeholder-leftover") in (None, 0),
          payload(res).get("counts"))
    res = run(["replace", "--input", fixed, "--output", os.path.join(work, "dry.docx"),
               "--param", "find=(\\w+) report", "--param", "regex=true",
               "--param", "replace=\\1 summary", "--param", "dry_run=true", "--json"], work)
    check("replace", "regex dry-run reports without writing",
          payload(res).get("dry_run") is True and not os.path.exists(os.path.join(work, "dry.docx")))
    res = run(["replace", "--input", fixed, "--output", os.path.join(work, "lim.docx"),
               "--config", fixtures["config"], "--param", "find=a", "--json"], work)
    check("replace", "enforces the replacement limit", error_code(res) == "LIMIT", error_code(res))
    res = run(["replace", "--input", fixed, "--output", os.path.join(work, "bad.docx"),
               "--param", "find=[", "--param", "regex=true", "--json"], work)
    check("replace", "invalid regex is a usage error", error_code(res) == "USAGE")
    res = run(["replace", "--input", fixed, "--in-place", "--param", "find=x", "--json"], work)
    check("replace", "in-place needs approval", error_code(res) == "APPROVAL_REQUIRED")

    # --- 6. metadata / styles / fields -----------------------------------
    meta_out = os.path.join(work, "meta.docx")
    res = run(["metadata", "--input", fixed, "--output", meta_out,
               "--param", "set_keywords=q3,ops", "--param", "set_subject=Quarterly",
               "--param", "clear_personal=true", "--json"], work)
    changed = payload(res)
    check("metadata", "sets multiple properties",
          set(["keywords", "subject", "creator"]) <= set(changed.get("changed") or []),
          changed.get("changed"))
    res = run(["inspect", "--input", meta_out, "--json"], work)
    props = payload(res).get("properties", {})
    check("metadata", "keywords persisted",
          props.get("core", {}).get("keywords") == "q3,ops", props.get("core"))
    res = run(["metadata", "--input", meta_out, "--output", os.path.join(work, "meta2.docx"),
               "--param", "add_custom=Cost centre=42", "--json"], work)
    check("metadata", "adds a custom property",
          "custom:Cost centre" in (payload(res).get("changed") or []),
          payload(res).get("changed"))
    res = run(["styles", "--input", fixed, "--param", "mode=list", "--json"], work)
    styles = payload(res)
    check("styles", "lists styles", styles.get("defined", 0) > 10, styles.get("defined"))
    check("styles", "reports used styles", "Heading1" in (styles.get("used") or {}))
    styled = os.path.join(work, "styled.docx")
    res = run(["styles", "--input", fixed, "--output", styled, "--param", "mode=apply",
               "--param", "style=Heading3", "--param", "match=first item", "--json"], work)
    check("styles", "applies a named style", payload(res).get("paragraphs_styled") == 1,
          payload(res))
    res = run(["styles", "--input", fixed, "--output", os.path.join(work, "s2.docx"),
               "--param", "mode=apply", "--param", "style=NoSuchStyle", "--json"], work)
    check("styles", "unknown style is rejected", error_code(res) == "NOT_FOUND")
    res = run(["fields", "--input", styled, "--output", os.path.join(work, "fields.docx"),
               "--param", "mode=insert", "--param", "code=PAGE", "--json"], work)
    check("fields", "inserts a field", payload(res).get("type") == "PAGE", payload(res))
    res = run(["fields", "--input", fixed, "--json", "--param", "mode=list"], work)
    check("fields", "lists fields", "total" in payload(res))

    # --- 7. toc / headerfooter / watermark / image / form ----------------
    toc_out = os.path.join(work, "toc.docx")
    res = run(["toc", "--input", fixed, "--output", toc_out, "--param", "depth=3", "--json"], work)
    toc_payload = payload(res)
    check("toc", "builds a TOC field", toc_payload.get("mode") == "insert" and
          toc_payload.get("headings_found", 0) >= 4, toc_payload.get("headings_found"))
    hf_out = os.path.join(work, "hf.docx")
    res = run(["headerfooter", "--input", toc_out, "--output", hf_out,
               "--param", "footer_text=Confidential",
               "--param", "footer_page_numbers=true", "--json"], work)
    check("headerfooter", "writes footer with page numbers",
          "footer" in payload(res), payload(res))
    with zipfile.ZipFile(hf_out) as zf:
        header_parts = [n for n in zf.namelist() if "footer" in n]
        footer_xml = zf.read(header_parts[0]).decode("utf-8") if header_parts else ""
    check("headerfooter", "PAGE and NUMPAGES fields emitted",
          "PAGE" in footer_xml and "NUMPAGES" in footer_xml)
    wm_out = os.path.join(work, "wm.docx")
    res = run(["watermark", "--input", hf_out, "--output", wm_out, "--param", "text=DRAFT",
               "--approve", "--json"], work)
    check("watermark", "adds a watermark", payload(res).get("text") == "DRAFT", payload(res))
    res = run(["inspect", "--input", wm_out, "--json"], work)
    check("watermark", "watermark does not count as a content image",
          payload(res).get("content", {}).get("images") == [],
          payload(res).get("content", {}).get("images"))
    res = run(["watermark", "--input", wm_out, "--output", os.path.join(work, "wm-off.docx"),
               "--param", "mode=remove", "--json"], work)
    check("watermark", "removal needs approval", error_code(res) == "APPROVAL_REQUIRED")
    res = run(["watermark", "--input", wm_out, "--output", os.path.join(work, "wm-off.docx"),
               "--param", "mode=remove", "--approve", "--json"], work)
    check("watermark", "removes the watermark", payload(res).get("removed") == 1, payload(res))
    img_out = os.path.join(work, "img.docx")
    res = run(["image", "--input", fixed, "--output", img_out,
               "--param", "image=" + fixtures["png"], "--param", "alt=Company logo",
               "--param", "width_cm=3", "--json"], work)
    check("image", "inserts an image with alt text",
          payload(res).get("alt_text_present") is True, payload(res))
    with zipfile.ZipFile(img_out) as zf:
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
    check("image", "media part embedded", len(media) == 1, media)
    res = run(["a11y", "--input", img_out, "--json"], work)
    check("image", "alt text satisfies the a11y rule",
          not any(f["rule"] == "image-no-alt" for f in payload(res).get("findings", [])))
    form_out = os.path.join(work, "form.docx")
    res = run(["form", "--input", fixed, "--output", form_out,
               "--param", "spec-file=" + fixtures["form"], "--json"], work)
    check("form", "creates four content controls",
          len(payload(res).get("controls") or []) == 4, payload(res).get("controls"))
    res = run(["inspect", "--input", form_out, "--json"], work)
    controls = payload(res).get("content", {}).get("controls") or []
    check("form", "controls are visible to the inspector", len(controls) == 4, controls)
    check("form", "controls have tags and titles",
          all(c["alias"] and c["tag"] for c in controls), controls)
    res = run(["form", "--input", fixed, "--output", os.path.join(work, "form-bad.docx"),
               "--param", "type=nonsense", "--json"], work)
    check("form", "unknown control type is rejected", res["code"] != 0, res["code"])

    # --- 7b. lists --------------------------------------------------------
    list_out = os.path.join(work, "list.docx")
    res = run(["list", "--input", fixed, "--output", list_out,
               "--param", "items=alpha;beta;gamma", "--param", "ordered=true", "--json"], work)
    check("list", "creates a numbered list", payload(res).get("items") == 3, payload(res))
    res = run(["list", "--input", list_out, "--param", "mode=report", "--json"], work)
    listing = payload(res)
    check("list", "reports list paragraphs", listing.get("list_paragraphs", 0) >= 5,
          listing.get("list_paragraphs"))
    check("list", "reports numbering definitions",
          len(listing.get("definitions") or []) >= 2, listing.get("definitions"))
    res = run(["list", "--input", list_out, "--output", os.path.join(work, "list2.docx"),
               "--param", "mode=restart", "--param", "num_id=2", "--json"], work)
    check("list", "restarts numbering", payload(res).get("start_override") is True, payload(res))
    res = run(["list", "--input", list_out, "--output", os.path.join(work, "list3.docx"),
               "--param", "mode=nonsense", "--json"], work)
    check("list", "unknown mode is rejected", error_code(res) == "USAGE")

    # --- 8. tables --------------------------------------------------------
    table_out = os.path.join(work, "table.docx")
    res = run(["table", "--input", fixed, "--output", table_out,
               "--param", "data-file=" + fixtures["csv"], "--json"], work)
    check("table", "creates a table from CSV", payload(res).get("rows") == 3, payload(res))
    res = run(["table", "--input", table_out, "--output", os.path.join(work, "t2.docx"),
               "--param", "mode=sort", "--param", "column=1", "--param", "table_index=1",
               "--json"], work)
    check("table", "sorts a table", payload(res).get("rows_sorted") == 3, payload(res))
    res = run(["table", "--input", table_out, "--output", os.path.join(work, "t3.docx"),
               "--param", "mode=structure", "--json"], work)
    tables = payload(res).get("tables") or []
    check("table", "reports structure", len(tables) == 2 and all(t["repeat_header"] for t in tables),
          tables)
    res = run(["table", "--input", table_out, "--output", os.path.join(work, "t4.docx"),
               "--param", "mode=formula", "--param", "table_index=1", "--json"], work)
    check("table", "appends a total row with a SUM field",
          payload(res).get("sum") == 3680 and payload(res).get("total_row_added") is True,
          payload(res))
    res = run(["table", "--input", fixed, "--output", os.path.join(work, "t5.docx"),
               "--param", "data-file=" + os.path.join(work, "missing.csv"), "--json"], work)
    check("table", "missing data file is reported", error_code(res) == "NOT_FOUND")

    # --- 9. structure: insert / format / convert / copy ------------------
    cover_out = os.path.join(work, "cover.docx")
    res = run(["insert", "--input", fixed, "--output", cover_out, "--param", "type=cover",
               "--param", "title=Quarterly Report", "--json"], work)
    check("structure", "inserts a cover page", payload(res).get("type") == "cover", payload(res))
    res = run(["insert", "--input", cover_out, "--output", os.path.join(work, "bm.docx"),
               "--param", "type=bookmark", "--param", "name=Data_Table", "--json"], work)
    check("structure", "inserts a bookmark", res["code"] == 0, res["stderr"])
    res = run(["insert", "--input", cover_out, "--output", os.path.join(work, "bm2.docx"),
               "--param", "type=bookmark", "--param", "name=Data_Table", "--json"], work)
    check("structure", "duplicate bookmark rejected", res["code"] == 0)
    geo_out = os.path.join(work, "geo.docx")
    res = run(["format", "--input", fixed, "--output", geo_out, "--param", "scope=page",
               "--param", "page_size=letter", "--param", "orientation=landscape",
               "--param", "margins=top=2cm,left=2cm,right=2cm,bottom=2cm", "--json"], work)
    check("structure", "sets page geometry", len(payload(res).get("changes") or []) >= 6,
          payload(res).get("changes"))
    res = run(["inspect", "--input", geo_out, "--json"], work)
    section = (payload(res).get("content", {}).get("sections") or [{}])[0]
    check("structure", "landscape geometry persisted",
          section.get("orientation") == "landscape" and
          section.get("page_width_twips", 0) > section.get("page_height_twips", 0), section)
    res = run(["format", "--input", geo_out, "--output", os.path.join(work, "cols.docx"),
               "--param", "scope=columns", "--param", "columns=2", "--json"], work)
    check("structure", "sets two columns", payload(res).get("columns") == 2)
    res = run(["format", "--input", geo_out, "--output", os.path.join(work, "badgeo.docx"),
               "--param", "scope=page", "--param", "page_size=nonsense", "--json"], work)
    check("structure", "unknown page size rejected", error_code(res) == "USAGE")
    res = run(["format", "--input", geo_out, "--output", os.path.join(work, "many.docx"),
               "--param", "scope=columns", "--param", "columns=40", "--json"], work)
    check("structure", "column limit enforced", error_code(res) == "LIMIT")
    copy_out = os.path.join(work, "copy.docx")
    res = run(["copy", "--input", fixed, "--output", copy_out, "--json"], work)
    check("structure", "save-a-copy writes a branch", os.path.isfile(copy_out) and
          payload(res).get("sha256"), payload(res))
    dotx = os.path.join(work, "template.dotx")
    res = run(["convert", "--input", copy_out, "--output", dotx, "--param", "format=dotx",
               "--json"], work)
    check("structure", "converts to dotx", payload(res).get("to") == "dotx", payload(res))
    res = run(["inspect", "--input", dotx, "--json"], work)
    check("structure", "dotx format detected",
          payload(res).get("file", {}).get("format") == "dotx",
          payload(res).get("file", {}).get("format"))
    res = run(["convert", "--input", copy_out, "--output", os.path.join(work, "bad.bin"),
               "--param", "format=docx", "--json"], work)
    check("structure", "wrong extension rejected", error_code(res) == "USAGE")

    # --- 10. review: comments, revisions, sanitize -----------------------
    tracked = craft_tracked_changes(fixed, os.path.join(work, "tracked.docx"))
    res = run(["revisions", "--input", tracked, "--param", "mode=report", "--json"], work)
    revisions = payload(res)
    check("review", "detects tracked changes", revisions.get("total") == 2, revisions.get("total"))
    check("review", "names the reviewers", len(revisions.get("authors") or {}) == 2,
          revisions.get("authors"))
    res = run(["revisions", "--input", tracked, "--output", os.path.join(work, "acc.docx"),
               "--param", "mode=accept-all", "--json"], work)
    check("review", "accept-all needs approval", error_code(res) == "APPROVAL_REQUIRED")
    res = run(["revisions", "--input", tracked, "--output", os.path.join(work, "acc.docx"),
               "--param", "mode=accept-all", "--approve", "--json"], work)
    accepted = payload(res)
    check("review", "accept-all clears revisions", accepted.get("after", {}).get("insertions") == 0
          and accepted.get("after", {}).get("deletions") == 0, accepted)
    res = run(["revisions", "--input", tracked, "--output", os.path.join(work, "rej.docx"),
               "--param", "mode=reject-all", "--approve", "--json"], work)
    check("review", "reject-all clears revisions",
          payload(res).get("after", {}).get("insertions") == 0, payload(res))
    with zipfile.ZipFile(os.path.join(work, "acc.docx")) as zf:
        accepted_xml = zf.read("word/document.xml").decode("utf-8")
    with zipfile.ZipFile(os.path.join(work, "rej.docx")) as zf:
        rejected_xml = zf.read("word/document.xml").decode("utf-8")
    check("review", "accept keeps inserted text and drops deleted text",
          "inserted clause" in accepted_xml and "deleted clause" not in accepted_xml)
    check("review", "reject keeps deleted text and drops inserted text",
          "deleted clause" in rejected_xml and "inserted clause" not in rejected_xml)
    res = run(["comments", "--input", tracked, "--output", os.path.join(work, "cm.docx"),
               "--param", "mode=add", "--param", "comment=Verify the revenue figure",
               "--param", "anchor_text=Sections", "--json"], work)
    check("review", "adds a comment", payload(res).get("id") is not None, payload(res))
    res = run(["comments", "--input", os.path.join(work, "cm.docx"), "--json"], work)
    check("review", "lists the comment", payload(res).get("count") == 1, payload(res))
    sanitized = os.path.join(work, "sanitized.docx")
    res = run(["sanitize", "--input", tracked, "--output", sanitized, "--dry-run", "--json"], work)
    check("review", "sanitize dry-run lists findings",
          res["code"] == 0 and len(payload(res).get("findings") or []) >= 3,
          payload(res).get("findings"))
    res = run(["sanitize", "--input", tracked, "--output", sanitized, "--json"], work)
    check("review", "sanitize needs approval", error_code(res) == "APPROVAL_REQUIRED")
    res = run(["sanitize", "--input", tracked, "--output", sanitized, "--approve", "--json"], work)
    sanitize_result = payload(res)
    check("review", "sanitize applies policy actions", sanitize_result.get("applied") is True)
    res = run(["inspect", "--input", sanitized, "--json"], work)
    cleaned = payload(res)
    check("review", "sanitized copy has no revisions/comments/hidden text",
          cleaned.get("review", {}).get("revisions", {}).get("total") == 0 and
          cleaned.get("review", {}).get("comments", {}).get("count") == 0 and
          cleaned.get("hidden", {}).get("hidden_text_count") == 0, cleaned.get("review"))
    check("review", "sanitize reports remaining risk",
          isinstance(sanitize_result.get("remaining_risk"), dict),
          sanitize_result.get("remaining_risk"))

    # --- 11. protection ---------------------------------------------------
    protected = os.path.join(work, "protected.docx")
    res = run(["protect", "--input", fixed, "--output", protected, "--param", "mode=readonly",
               "--json"], work)
    check("protection", "protection needs approval", error_code(res) == "APPROVAL_REQUIRED")
    res = run(["protect", "--input", fixed, "--output", protected, "--param", "mode=readonly",
               "--approve", "--json"], work)
    check("protection", "applies read-only protection", payload(res).get("edit") == "readOnly",
          payload(res))
    res = run(["inspect", "--input", protected, "--json"], work)
    state = payload(res).get("properties", {}).get("protection", {})
    check("protection", "protection is visible to the inspector",
          state.get("protected") is True and state.get("enforced") is True, state)
    res = run(["protect", "--input", fixed, "--output", os.path.join(work, "pw.docx"),
               "--param", "mode=comments", "--param", "password=correct horse battery",
               "--approve", "--json"], work)
    check("protection", "writes a password hash",
          payload(res).get("password_set") is True, payload(res))
    res = run(["unprotect", "--input", protected, "--output", os.path.join(work, "open.docx"),
               "--approve", "--json"], work)
    check("protection", "removes protection", payload(res).get("removed") is True, payload(res))
    res = run(["inspect", "--input", os.path.join(work, "open.docx"), "--json"], work)
    check("protection", "unprotected state persisted",
          payload(res).get("properties", {}).get("protection", {}).get("protected") is False)

    # --- 12. mail merge ---------------------------------------------------
    template = os.path.join(work, "offer.docx")
    run(["create", "--output", template, "--param",
         "markdown=# Offer for {{name}}\n\nHello {{name}}, your role is {{role}}. ID {{id}}.\n"],
        work)
    merged_dir = os.path.join(work, "merged")
    res = run(["merge", "--input", template, "--output", merged_dir,
               "--param", "data_file=" + fixtures["records"], "--json"], work)
    merged = payload(res)
    written = merged.get("files_written") or []
    check("merge", "renders one document per record", len(written) == 2, merged)
    check("merge", "merged files exist", all(os.path.isfile(p) for p in written), written)
    check("merge", "fills every placeholder",
          all(not d["unfilled"] for d in merged.get("detail") or []), merged.get("detail"))
    res = run(["inspect", "--input", written[0], "--json"], work)
    check("merge", "merged output is a valid package",
          payload(res).get("file", {}).get("format") == "docx")
    combined_dir = os.path.join(work, "merged-combined")
    res = run(["merge", "--input", template, "--output", combined_dir,
               "--param", "data_file=" + fixtures["records"], "--param", "combine=true",
               "--json"], work)
    check("merge", "combined merge writes one file",
          len(payload(res).get("files_written") or []) == 1, payload(res))
    res = run(["merge", "--input", template, "--output", os.path.join(work, "m2"),
               "--param", "data_file=" + fixtures["records"], "--config", fixtures["config"],
               "--json"], work)
    check("merge", "respects max_merge_records", res["code"] == 0)
    res = run(["merge", "--input", template, "--output", os.path.join(work, "m3"),
               "--param", "data_file=" + os.path.join(work, "missing.json"), "--json"], work)
    check("merge", "missing record set reported", error_code(res) == "NOT_FOUND")

    # --- 13. export -------------------------------------------------------
    html_out = os.path.join(work, "guide.html")
    res = run(["export", "--input", img_out, "--output", html_out, "--json"], work)
    check("export", "exports html", payload(res).get("format") == "html", payload(res))
    html_text = open(html_out, encoding="utf-8").read() if os.path.isfile(html_out) else ""
    check("export", "html is a complete document",
          html_text.startswith("<!DOCTYPE html>") and "</html>" in html_text)
    check("export", "html embeds images as data URIs", "data:image/" in html_text)
    check("export", "html declares a language", '<html lang="' in html_text)
    md_out = os.path.join(work, "guide.md.out")
    md_out = os.path.join(work, "roundtrip.md")
    res = run(["export", "--input", img_out, "--output", md_out, "--json"], work)
    md_text = open(md_out, encoding="utf-8").read() if os.path.isfile(md_out) else ""
    check("export", "exports markdown", payload(res).get("format") == "md")
    check("export", "markdown keeps headings and tables",
          md_text.startswith("# Field Guide") and "| Region |" in md_text, md_text[:120])
    txt_out = os.path.join(work, "guide.txt")
    res = run(["export", "--input", img_out, "--output", txt_out, "--json"], work)
    check("export", "exports plain text", payload(res).get("format") == "txt")
    json_out = os.path.join(work, "guide.json")
    res = run(["export", "--input", img_out, "--output", json_out, "--json"], work)
    with open(json_out, encoding="utf-8") as fh:
        model = json.load(fh)
    check("export", "json model is loadable", isinstance(model.get("blocks"), list) and
          len(model["blocks"]) > 5, len(model.get("blocks") or []))
    res = run(["export", "--input", img_out, "--output", os.path.join(work, "guide.pdf"),
               "--json"], work)
    check("export", "pdf export fails cleanly without a converter",
          error_code(res) == "EXTERNAL_UNAVAILABLE", error_code(res))
    res = run(["export", "--input", img_out, "--output", os.path.join(work, "guide.pdf"),
               "--param", "converter=/usr/bin/false", "--param", "allow_subprocess=true",
               "--json"], work)
    check("export", "configured converter is reported honestly",
          error_code(res) in ("EXTERNAL_UNAVAILABLE", "INTEGRITY"), error_code(res))
    res = run(["export", "--input", img_out, "--output", os.path.join(work, "x.docx"),
               "--json"], work)
    check("export", "refuses to export Word formats", error_code(res) == "USAGE")

    # --- 14. backup / restore --------------------------------------------
    res = run(["backup", "--input", sanitized, "--param", "label=release", "--json"], work)
    backup_result = payload(res)
    check("backup", "creates a backup with a hash",
          bool(backup_result.get("sha256")) and os.path.isfile(backup_result.get("backup") or ""),
          backup_result)
    res = run(["backup", "--input", sanitized, "--param", "mode=list", "--json"], work)
    check("backup", "lists backups", payload(res).get("count") == 1, payload(res).get("count"))
    restored = os.path.join(work, "restored.docx")
    res = run(["restore", "--input", sanitized, "--output", restored, "--json"], work)
    check("restore", "restore needs approval", error_code(res) == "APPROVAL_REQUIRED")
    res = run(["restore", "--input", sanitized, "--output", restored,
               "--param", "id=" + str(backup_result.get("id")), "--approve", "--json"], work)
    check("restore", "restores a verified backup", payload(res).get("verified") is True,
          payload(res))
    with open(restored, "rb") as fh, open(sanitized, "rb") as src:
        check("restore", "restored bytes match the backup", fh.read() == src.read())

    # --- 15. routing / planning / scoring --------------------------------
    res = run(["route", "--param", "query=pause page numbering", "--json"], work)
    candidates = payload(res).get("candidates") or []
    check("route", "routes a request to catalog skills", len(candidates) > 0, payload(res))
    check("route", "each candidate names an operation",
          all(c.get("op") for c in candidates), candidates[:1])
    res = run(["plan", "--param", "skill=Blank Document Creator", "--json"], work)
    plan = payload(res)
    check("plan", "plans a specific skill", plan.get("skill") == "Blank Document Creator", plan.get("skill"))
    check("plan", "plan names the implementing operation",
          plan.get("implementing_operation") == "create", plan.get("implementing_operation"))
    check("plan", "plan includes steps, verification and rollback",
          bool(plan.get("steps")) and bool(plan.get("verification")) and bool(plan.get("rollback")))
    res = run(["plan", "--param", "skill=" + "Forms, Content Controls, and XML Mapping", "--json"], work)
    check("plan", "plans a whole category", payload(res).get("kind") == "category", payload(res).get("kind"))
    res = run(["score", "--input", img_out, "--json"], work)
    score = payload(res)
    check("score", "scores all 53 categories", score.get("categories") == 53, score.get("categories"))
    check("score", "scores all 636 skills", score.get("skills") == 636, score.get("skills"))
    check("score", "reports document evidence",
          any(c["document_evidence"] for c in score.get("detail") or []))

    # --- 16. error handling / recovery -----------------------------------
    res = run(["inspect", "--input", fixtures["broken"], "--json"], work)
    check("errors", "corrupt package is an integrity error", error_code(res) == "INTEGRITY")
    res = run(["inspect", "--input", os.path.join(work, "nope.docx"), "--json"], work)
    check("errors", "missing file is NOT_FOUND", error_code(res) == "NOT_FOUND")
    res = run(["inspect", "--input", fixtures["plain"], "--json"], work)
    check("errors", "wrong extension is rejected", error_code(res) == "USAGE")
    res = run(["inspect", "--input", fixtures["legacy"], "--json"], work)
    check("errors", "legacy .doc is rejected", error_code(res) == "USAGE")
    res = run(["nonsense-op", "--json"], work)
    check("errors", "unknown operation is a usage error", error_code(res) == "USAGE")
    res = run(["inspect", "--param", "novalue", "--input", doc, "--json"], work)
    check("errors", "malformed --param is rejected", error_code(res) == "USAGE")
    res = run(["inspect", "--json"], work)
    check("errors", "missing input is rejected", error_code(res) == "USAGE")
    res = run(["create", "--output", os.path.join(work, "cfg.docx"), "--config",
               os.path.join(work, "absent.json"), "--json"], work)
    check("errors", "missing config is NOT_FOUND", error_code(res) == "NOT_FOUND")
    report_file = os.path.join(work, "report.json")
    res = run(["inspect", "--input", doc, "--json", "--report-file", report_file], work)
    check("errors", "report file is written",
          os.path.isfile(report_file) and json.load(open(report_file, encoding="utf-8"))["ok"])
    # recovery path: backup -> damage -> restore
    victim = os.path.join(work, "victim.docx")
    shutil.copyfile(fixed, victim)
    res = run(["backup", "--input", victim, "--json"], work)
    victim_backup = payload(res)
    with open(victim, "wb") as fh:
        fh.write(b"corrupted by a failed copy")
    res = run(["inspect", "--input", victim, "--json"], work)
    check("recovery", "damaged file is detected", error_code(res) == "INTEGRITY")
    res = run(["restore", "--input", victim, "--output", os.path.join(work, "recovered.docx"),
               "--param", "id=" + str(victim_backup.get("id")), "--approve", "--json"], work)
    check("recovery", "recovery from backup succeeds", payload(res).get("verified") is True)
    res = run(["inspect", "--input", os.path.join(work, "recovered.docx"), "--json"], work)
    check("recovery", "recovered file opens and validates",
          payload(res).get("file", {}).get("format") == "docx" and res["code"] == 0)

    # --- 17. portability: clean skill root with a test SKILL.md ----------
    root = tempfile.mkdtemp(prefix="skillroot-", dir=work)
    shutil.copytree(HERE, os.path.join(root, "word-document-agent"),
                    ignore=shutil.ignore_patterns(".word-document-agent-state", "__pycache__"))
    with open(os.path.join(root, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: test-skill\n---\n\nTest parent skill.\n")
    relative_entry = os.path.join("word-document-agent", "tools-word-doc-agent.py")
    completed = subprocess.run([PY, relative_entry, "create", "--output", "clean.docx",
                                "--param", "markdown=# Clean root", "--json"],
                               cwd=root, capture_output=True, text=True)
    check("portability", "runs from a clean skill root with relative paths",
          completed.returncode == 0 and os.path.isfile(os.path.join(root, "clean.docx")),
          (completed.stderr or completed.stdout)[:200])
    completed = subprocess.run([PY, relative_entry, "inspect", "--input", "clean.docx", "--json"],
                               cwd=root, capture_output=True, text=True)
    check("portability", "inspect works from the clean root",
          completed.returncode == 0 and '"docx"' in completed.stdout,
          completed.stdout[:160])

    # --- 18. package shape ------------------------------------------------
    entries = sorted(os.listdir(HERE))
    check("package", "no SKILL.md inside the skill tool folder", "SKILL.md" not in entries, entries)
    subdirs = [e for e in entries
               if os.path.isdir(os.path.join(HERE, e)) and e not in
               (".word-document-agent-state", "__pycache__")]
    check("package", "no subfolders", not subdirs, subdirs)
    prefixed = all(e.startswith(("tools-", "knowledge-", "asset-")) for e in entries
                   if os.path.isfile(os.path.join(HERE, e)))
    check("package", "every file uses a required prefix", prefixed,
          [e for e in entries if not e.startswith(("tools-", "knowledge-", "asset-"))])
    knowledge = [e for e in entries if e.startswith("knowledge-")]
    check("package", "exactly one knowledge file", len(knowledge) == 1, knowledge)
    tools = [e for e in entries if e.startswith("tools-") and e.endswith(".py")]
    check("package", "at least one runnable tools entry point",
          "tools-word-doc-agent.py" in tools, tools)
    missing = [e for e in entries if os.path.isfile(os.path.join(HERE, e)) and
               not os.access(os.path.join(HERE, e), os.R_OK)]
    check("package", "all files readable", not missing, missing)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Self-test for word-document-agent")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--workdir", help="keep artefacts in this directory")
    parser.add_argument("--keep", action="store_true", help="do not delete the temp workdir")
    args = parser.parse_args(argv)

    work = os.path.abspath(args.workdir) if args.workdir else tempfile.mkdtemp(prefix="wda-selftest-")
    os.makedirs(work, exist_ok=True)
    report = Report(verbose=args.verbose or not args.json)
    print("word-document-agent self-test")
    print("entry point: %s" % ENTRY)
    print("work dir:    %s" % work)
    print("python:      %s" % sys.version.split()[0])
    print("")
    try:
        fixtures = make_fixtures(work)
        suite(report, work, fixtures)
    except Exception as exc:
        report.add("harness", "suite completed", False, "%s: %s" % (type(exc).__name__, exc))
    failures = report.failures
    groups = report.summary()
    print("")
    for group, (total, failed) in sorted(groups.items()):
        print("  %-12s %2d test(s)  %s" % (group, total, "ok" if not failed else
                                           "%d failed" % failed))
    print("")
    print("%d test(s), %d failure(s) in %.1fs" % (len(report.results), len(failures),
                                                  time.time() - report.started))
    if args.json:
        print(json.dumps({"ok": not failures, "tests": report.results,
                          "failures": len(failures), "workdir": work}, indent=1))
    if failures:
        print("")
        for failure in failures:
            print("FAIL %s / %s: %s" % (failure["group"], failure["test"], failure["detail"]))
    if not args.keep and not args.workdir:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
