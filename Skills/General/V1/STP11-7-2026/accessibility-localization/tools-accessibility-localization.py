#!/usr/bin/env python3
"""accessibility-localization Skill Tool.

A unified, local, standard-library-only Skill Tool for the 220 capabilities in
the Emmre's Accessibility and Localization Skill Catalog. It provides:

  * a capability registry and capability-list command;
  * real static, document, media, localization, terminology, and language-quality
    analysis engines;
  * reproducible governance and test/research protocol generators;
  * structured JSON and Markdown evidence for every capability.

No SKILL.md is included. This folder is a Skill Tool component for a future
Copilot skill. It makes no network requests and executes no input code.

Usage:
  python3 tools-accessibility-localization.py --help
  python3 tools-accessibility-localization.py capabilities
  python3 tools-accessibility-localization.py run <capability-id> --input <path> --output <dir>
  python3 tools-accessibility-localization.py self-test
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import wave
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("tools_core", "tools-core.py")
templates = _load("tools_templates", "tools-templates.py")
registry = _load("tools_registry", "tools-registry.py")
engines = _load("tools_engines", "tools-engines.py")

ToolError = core.ToolError
Config = core.Config
EngineContext = engines.EngineContext


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
ACCEPTED = {
    "html": [".html", ".htm", ".xhtml", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"],
    "document": [".pdf", ".docx", ".xlsx", ".pptx", ".epub"],
    "locale": [".json", ".properties", ".csv", ".txt"],
    "source": [".html", ".htm", ".xhtml", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".json", ".properties", ".csv", ".txt"],
    "json": [".json"],
    "csv": [".csv"],
    "review": [".csv", ".json"],
    "text": [".txt", ".md", ".html"],
    "captions": [".vtt", ".srt"],
    "audio": [".wav"],
    "data": [".csv", ".json"],
    "findings": [".json"],
    "evidence": [".json", ".csv", ".md", ".txt", ".html", ".pdf", ".docx", ".xlsx", ".pptx", ".vtt", ".srt", ".wav", ".png", ".jpg"],
    "defects": [".csv", ".json"],
    "retest": [".json"],
    "vendor": [".json", ".txt", ".md", ".html"],
    "request": [".json"],
    "brief": [".json", ".txt", ".md"],
    "config": [".json"],
}


def input_kind_for(cap: dict[str, Any]) -> str:
    return cap.get("input_kind", "brief")


def validate_input(cap: dict[str, Any], input_path: str | None, config: Config) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not input_path:
        if cap.get("input_required"):
            errors.append(f"Input is required for {cap['id']} (kind: {cap['input_kind']}).")
        return None, errors
    p = Path(input_path)
    if not p.exists():
        return None, [f"Input path does not exist: {p}"]
    max_bytes = config.limit("max_bytes")
    if p.is_file() and p.stat().st_size > max_bytes:
        return None, [f"Input is {p.stat().st_size} bytes; the limit is {max_bytes} bytes."]
    info: dict[str, Any] = {"path": str(p), "kind": "directory" if p.is_dir() else core.file_kind(p),
                            "bytes": p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob('*') if f.is_file())}
    if p.is_file():
        kind = core.file_kind(p)
        accepted = ACCEPTED.get(cap["input_kind"], [".json", ".txt"])
        if kind not in ("unknown",) and p.suffix.lower() not in accepted and cap["input_kind"] not in ("brief", "config", "evidence"):
            errors.append(f"Input kind '{kind}' is not accepted by {cap['id']}; expected {accepted}.")
        if kind == "json":
            try:
                data = core.read_json(p, max_bytes)
                info["parsed"] = "json"
                if not isinstance(data, (dict, list)):
                    errors.append("JSON input must be an object or array.")
            except ToolError as exc:
                errors.append(exc.message)
        elif kind == "csv":
            try:
                rows, headers = core.read_csv(p, max_bytes)
                info["parsed"] = "csv"
                info["rows"] = len(rows)
                info["columns"] = headers
                if not rows:
                    errors.append("CSV input has no data rows.")
            except ToolError as exc:
                errors.append(exc.message)
        elif kind in ("html", "css", "javascript", "typescript", "markdown", "text"):
            text = core.read_text(p, max_bytes)
            info["parsed"] = "text"
            info["characters"] = len(text)
            if not text.strip():
                errors.append("Text input is empty.")
        elif kind == "captions":
            text = core.read_text(p, max_bytes)
            info["parsed"] = "captions"
            if not (text.lstrip().startswith("WEBVTT") or "-->" in text):
                errors.append("Caption input is not valid WebVTT or SRT.")
        elif kind == "audio":
            if p.suffix.lower() != ".wav":
                errors.append("Only WAV audio is supported by the local engine.")
        elif kind in ("pdf", "ooxml", "epub"):
            signature = p.read_bytes()[:4]
            if kind == "pdf" and not signature.startswith(b"%PDF"):
                errors.append("File is not a PDF (missing %PDF signature).")
            if kind == "ooxml" and not zipfile.is_zipfile(p):
                errors.append("File is not a valid OOXML ZIP package.")
            if kind == "epub" and not zipfile.is_zipfile(p):
                errors.append("File is not a valid EPUB ZIP package.")
    return info, errors


def validate_config(config: Config) -> list[str]:
    errors: list[str] = []
    data = config.data
    for section in ("locale", "standards", "limits", "paths", "privacy", "approval", "organization", "release_gate", "report"):
        if section not in data:
            errors.append(f"Config is missing the '{section}' section.")
    limits = data.get("limits", {})
    for key in ("max_findings", "max_files", "max_bytes", "timeout_seconds"):
        if key not in limits:
            errors.append(f"Config limits is missing '{key}'.")
        elif not isinstance(limits[key], int) or limits[key] <= 0:
            errors.append(f"Config limits.{key} must be a positive integer.")
    levels = data.get("standards", {}).get("levels", [])
    if not isinstance(levels, list) or not levels:
        errors.append("Config standards.levels must be a non-empty array.")
    return errors


def input_compatible(cap: dict[str, Any], input_path: str | None) -> tuple[bool, str]:
    if not input_path:
        if cap.get("input_required"):
            return False, "requires input"
        return True, ""
    p = Path(input_path)
    if not p.exists():
        return False, "input missing"
    if p.is_dir():
        if cap["input_kind"] in ("html", "locale", "source", "evidence", "brief"):
            return True, ""
        if not cap.get("input_required"):
            return True, "optional"
        return False, f"needs a specific {cap['input_kind']} file, not a directory"
    kind = core.file_kind(p)
    accepted = ACCEPTED.get(cap["input_kind"], [".json", ".txt"])
    if p.suffix.lower() in accepted or cap["input_kind"] == "brief":
        return True, ""
    if cap["input_kind"] == "config" and p.suffix.lower() == ".json":
        return True, ""
    return False, f"needs {accepted}, got {kind or p.suffix}"


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def build_context(cap, input_path, brief, output, config, timeout, limit) -> EngineContext:
    json_path, md_path, artifacts_dir = core.output_paths(output, cap["id"], config)
    core.ensure_dir(artifacts_dir)
    limits = {
        "max_findings": limit or config.limit("max_findings"),
        "max_files": config.limit("max_files"),
        "max_bytes": config.limit("max_bytes"),
        "timeout_seconds": timeout or config.timeout(),
        "max_rows": config.limit("max_rows"),
    }
    effective_brief = brief or ""
    if not effective_brief and input_path and cap["input_kind"] == "brief":
        p = Path(input_path)
        if p.is_file() and core.file_kind(p) in ("text", "markdown", "json"):
            try:
                data = core.read_json(p) if p.suffix.lower() == ".json" else core.read_text(p)
                if isinstance(data, dict):
                    effective_brief = data.get("brief", json.dumps(data, ensure_ascii=False))
                else:
                    effective_brief = str(data)
            except ToolError:
                effective_brief = ""
    return EngineContext(cap=cap, input_path=input_path, config=config, params=cap.get("params", {}),
                         checks=cap.get("checks", []), brief=effective_brief,
                         output_dir=Path(output), artifacts_dir=artifacts_dir, limits=limits)


def run_capability(cap, input_path, brief, output, config, timeout=None, limit=None,
                   markdown=True, write=True) -> tuple[dict[str, Any], Path | None, Path | None]:
    input_info, errors = validate_input(cap, input_path, config)
    if errors:
        first = errors[0]
        if "is required" in first:
            code, recovery = "input-required", "Pass --input with a supported file, or use a governance/plan capability."
        elif "does not exist" in first:
            code, recovery = "input-missing", "Check the path and try again."
        elif "not accepted" in first:
            code, recovery = "input-kind-mismatch", "Pass a file with one of the accepted extensions listed in the error."
        elif "Invalid JSON" in first or "not valid" in first or "not valid UTF-8" in first:
            code, recovery = "input-invalid", "Fix the file format or encoding and retry."
        else:
            code, recovery = "input-invalid", "Fix the input and retry."
        envelope = core.error_envelope(cap, ToolError(code, "; ".join(errors), recovery, str(input_path or "")), config.data)
        if write:
            core.write_outputs(envelope, output, config, markdown)
        return envelope, None, None
    ctx = build_context(cap, input_path, brief, output, config, timeout, limit)
    try:
        with core.TimeoutGuard(ctx.limits["timeout_seconds"]):
            result = engines.execute(cap, ctx)
    except ToolError as exc:
        envelope = core.error_envelope(cap, exc, config.data)
        envelope["input"] = input_info or {}
        if write:
            core.write_outputs(envelope, output, config, markdown)
        return envelope, None, None
    envelope = core.make_envelope(
        cap, result.status, result.summary, result.findings, config.data,
        input_info=input_info or {}, protocol=result.protocol, artifacts=result.artifacts,
        limitations=result.limitations, next_steps=result.next_steps, metrics=result.metrics)
    envelope["error"] = None
    if write:
        core.write_outputs(envelope, output, config, markdown)
    return envelope, None, None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_capabilities(args, reg, config) -> int:
    items = registry.search(reg, args.search, args.category, args.status)
    if args.operation:
        items = [c for c in items if c["operation"] == args.operation]
    if args.json:
        print(json.dumps([{
            "id": c["id"], "name": c["name"], "description": c["description"],
            "category": c["category"], "category_title": c["category_title"],
            "status": c["status"], "operation": c["operation"],
            "input_kind": c["input_kind"], "input_required": c["input_required"],
            "output_kind": c["output_kind"], "checks": c.get("checks", []),
            "params": c.get("params", {}),
        } for c in items], indent=2, ensure_ascii=False))
        return 0
    print(f"{len(items)} capabilities")
    print(f"{'ID':<52} {'STATUS':<9} {'CATEGORY':<28} NAME")
    for c in items:
        print(f"{c['id']:<52} {c['status']:<9} {c['category']:<28} {c['name']}")
    if args.long:
        print("\nDetails:")
        for c in items:
            print(f"\n## {c['name']} ({c['id']})")
            print(c["description"])
            print(f"Operation: {c['operation']} | Input: {c['input_kind']} | Output: {c['output_kind']}")
            if c.get("checks"):
                print(f"Checks: {', '.join(c['checks'])}")
    return 0


def cmd_categories(args, reg, config) -> int:
    cats = registry.categories(reg)
    if args.json:
        print(json.dumps(cats, indent=2, ensure_ascii=False))
        return 0
    print(f"{'SLUG':<32} {'COUNT':>5} {'AUTO':>5} {'ASSISTED':>9} TITLE")
    for c in cats:
        print(f"{c['slug']:<32} {c['count']:>5} {c['automated']:>5} {c['assisted']:>9} {c['title']}")
    print(f"\nTotal: {sum(c['count'] for c in cats)} capabilities")
    return 0


def cmd_describe(args, reg, config) -> int:
    cap = registry.find(reg, args.capability_id)
    spec = registry.describe(cap)
    if args.json:
        print(json.dumps(spec, indent=2, ensure_ascii=False))
    else:
        print(f"{cap['name']} ({cap['id']})")
        print(f"Category: {cap['category_title']}")
        print(f"Status: {cap['status']} | Operation: {cap['operation']}")
        print(f"\n{cap['description']}")
        print(f"\nInput: {spec['input']['kind']} (required={spec['input']['required']}); accepted: {', '.join(spec['input']['accepted'])}")
        print(f"Output: {spec['output']['kind']}")
        print(f"Standards: {', '.join(spec['standards'])}")
        print("\nProcedure:")
        for i, step in enumerate(spec["procedure"], 1):
            print(f"  {i}. {step}")
        print("\nPass criteria:")
        for c in spec["pass_criteria"]:
            print(f"  - {c}")
        print("\nFail criteria:")
        for c in spec["fail_criteria"]:
            print(f"  - {c}")
        print(f"\nHuman review: {spec['human_review']}")
        print(f"Evidence: {', '.join(spec['evidence'])}")
        print("\nLimitations:")
        for l in spec["limitations"]:
            print(f"  - {l}")
        print(f"\nExample:\n  {spec['example']}")
    return 0


def cmd_run(args, reg, config) -> int:
    cap = registry.find(reg, args.capability_id)
    envelope, _, _ = run_capability(cap, args.input, args.brief, args.output, config,
                                    args.timeout, args.limit, not args.no_markdown)
    if args.json:
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        print(f"Capability: {cap['name']} ({cap['id']})")
        print(f"Status: {envelope['status']}")
        print(f"Summary: {envelope['summary']}")
        counts = envelope.get("metrics", {}).get("severity_counts", {})
        print("Findings: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        if envelope.get("artifacts"):
            print("Artifacts:")
            for a in envelope["artifacts"]:
                print(f"  - {a['path']} ({a['kind']})")
        if envelope.get("error"):
            print(f"Error: {envelope['error']['message']}")
            if envelope["error"].get("recovery"):
                print(f"Recovery: {envelope['error']['recovery']}")
        print(f"Output: {args.output}")
    if envelope["status"] == "error":
        return 2
    if envelope["status"] == "failed":
        return 1
    return 0


def cmd_run_many(args, reg, config) -> int:
    category = getattr(args, "category", None)
    if category:
        cats = registry.categories(reg)
        match = next((c for c in cats if c["slug"] == category or c["title"].lower() == category.lower()), None)
        if not match:
            print(f"Unknown category: {category}", file=sys.stderr)
            print("Valid: " + ", ".join(c["slug"] for c in cats), file=sys.stderr)
            return 2
        caps = [registry.find(reg, cid) for cid in match["capabilities"]]
        label = match["slug"]
    else:
        caps = list(reg)
        label = "all"
    results = []
    skipped = []
    for cap in caps:
        ok, reason = input_compatible(cap, args.input)
        if not ok:
            skipped.append({"id": cap["id"], "name": cap["name"], "reason": reason})
            continue
        run_input = args.input
        if reason == "optional" and args.input and Path(args.input).is_dir():
            run_input = None
        out = Path(args.output) / cap["id"]
        envelope, _, _ = run_capability(cap, run_input, args.brief, out, config,
                                        args.timeout, args.limit, not args.no_markdown)
        results.append(envelope)
    counts = {s: sum(1 for e in results if e["status"] == s) for s in core.STATUSES}
    summary = {
        "scope": label, "input": args.input, "capabilities": len(results),
        "skipped": len(skipped), "status_counts": counts,
        "results": results, "skipped_capabilities": skipped,
        "generated_at": core.utcnow(),
    }
    out_dir = Path(args.output)
    core.ensure_dir(out_dir)
    core.write_json(out_dir / f"{label}-run.json", summary)
    md = core.Markdown()
    md.h(1, f"Accessibility and localization run: {label}")
    md.p(f"Input: `{args.input or 'none'}` · Capabilities run: {len(results)} · Skipped: {len(skipped)}")
    md.table(["Status", "Count"], [[s, counts[s]] for s in core.STATUSES])
    md.h(2, "Results")
    md.table(["Capability", "Status", "Findings", "Artifacts"],
             [[e["capability"]["name"], e["status"], e["metrics"].get("finding_count", 0), len(e.get("artifacts", []))] for e in results])
    failures = [e for e in results if e["status"] in ("failed", "error")]
    if failures:
        md.h(2, "Failures and errors")
        for e in failures:
            md.h(3, f"{e['capability']['name']} — {e['status']}")
            md.p(e["summary"])
            if e.get("error"):
                md.p(f"Error: {e['error'].get('message')}")
    if skipped:
        md.h(2, "Skipped capabilities")
        md.table(["Capability", "Reason"], [[s["name"], s["reason"]] for s in skipped])
    core.write_text(out_dir / f"{label}-run.md", md.render())
    print(f"Ran {len(results)} capabilities; skipped {len(skipped)}; statuses: {counts}")
    print(f"Combined report: {out_dir / (label + '-run.json')}")
    if counts.get("error"):
        return 2
    if counts.get("failed"):
        return 1
    return 0


def cmd_validate(args, reg, config) -> int:
    errors = validate_config(config)
    info = None
    if args.input:
        cap = registry.find(reg, args.capability_id) if args.capability_id else None
        if cap:
            info, input_errors = validate_input(cap, args.input, config)
            errors.extend(input_errors)
        else:
            p = Path(args.input)
            if not p.exists():
                errors.append(f"Input path does not exist: {p}")
            elif p.is_file() and p.stat().st_size > config.limit("max_bytes"):
                errors.append("Input exceeds the configured max_bytes.")
            else:
                info = {"path": str(p), "kind": core.file_kind(p) if p.is_file() else "directory"}
    payload = {"valid": not errors, "errors": errors, "config": config.data, "input": info}
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if not errors else 2


def cmd_config(args, reg, config) -> int:
    if args.init:
        out = Path(args.output)
        if out.suffix.lower() == ".json":
            target = out
        else:
            core.ensure_dir(out)
            target = out / "default-config.json"
        core.write_json(target, config.data)
        print(f"Wrote default config: {target}")
        return 0
    print(json.dumps(config.data, indent=2, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# Self-test: every engine, with fixtures and assertions
# --------------------------------------------------------------------------- #
def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_docx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("docProps/core.xml", '<?xml version="1.0"><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Quarterly report</dc:title></cp:coreProperties>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Report</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Header</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return path


def _make_wav(path: Path) -> Path:
    rate = 8000
    duration = 1.0
    frames = int(rate * duration)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", int(3000 * __import__("math").sin(2 * 3.14159 * 440 * i / rate))) for i in range(frames)))
    return path


def cmd_self_test(args, reg, config) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="accessibility-localization-selftest-"))
    out = tmp / "output"
    core.ensure_dir(out)
    assets = HERE
    try:
        html = assets / "asset-fixture-sample.html"
        locale = assets / "asset-fixture-locale.json"
        captions = assets / "asset-fixture-captions.vtt"
        review = assets / "asset-fixture-review.csv"
        termbase = assets / "asset-template-termbase.csv"
        scorecard = assets / "asset-template-review-scorecard.csv"
        for p in (html, locale, captions, review, termbase, scorecard):
            if not p.exists():
                print(f"Missing fixture: {p}", file=sys.stderr)
                return 2
        tm = _write(tmp / "tm.csv", "source,target\nSign in,Mag-sign in\nSave changes,I-save ang mga pagbabago\nSearch results,Mga resulta ng paghahanap\n")
        values = _write(tmp / "values.json", json.dumps({"values": {"date": "2026-11-07", "amount": "1234567.5", "phone": "09171234567", "name": "Juan Dela Cruz"}}, ensure_ascii=False))
        journeys = _write(tmp / "journeys.json", json.dumps({"journeys": [{"name": "Sign in", "description": "Email, password, multi-factor authentication, and account recovery."}, {"name": "Checkout", "description": "Cart, address, payment, confirmation, and error recovery."}, {"name": "View video", "description": "Captions, audio description, transcript, and player controls."}]}, ensure_ascii=False))
        scores = _write(tmp / "scores.json", json.dumps({"scores": {"Governance": 3, "Design": 2, "Engineering": 3, "Content": 2, "Testing": 2, "Procurement": 1, "Support": 2, "Culture": 2}}, ensure_ascii=False))
        exception = _write(tmp / "exception.json", json.dumps({"scope": "Legacy PDF export", "constraint": "Vendor library cannot produce a tagged PDF", "equivalent_access": "Provide an accessible HTML version on request", "duration_days": 180, "owner": "Accessibility lead"}, ensure_ascii=False))
        defects = _write(tmp / "defects.csv", "id,title,severity,journey,workaround\nD-1,Checkout button has no name,critical,checkout,none\nD-2,Focus indicator is low contrast,serious,account settings,use browser zoom\nD-3,Alt text is missing,moderate,media,read the page text\n")
        retest = _write(tmp / "retest.json", json.dumps({"findings": [{"id": "F-1", "title": "Button has no accessible name", "severity": "critical"}, {"id": "F-2", "title": "Contrast is low", "severity": "serious"}], "retest": [{"id": "F-1", "status": "passed"}, {"id": "F-2", "status": "failed", "reason": "Still 3:1"}]}, ensure_ascii=False))
        vendor = _write(tmp / "vendor.txt", "Vendor Accessibility Conformance Report\n\nOur product supports WCAG 2.1 Level AA. We tested with screen readers. Known limitations: none.\n")
        trace = _write(tmp / "trace.json", json.dumps({"findings": [{"id": "F-1", "title": "Button has no name", "severity": "critical", "requirement": "REQ-1", "owner": "Team A", "verification": "Retest with NVDA"}, {"id": "F-2", "title": "Contrast is low", "severity": "serious"}], "requirements": [{"id": "REQ-1", "title": "All controls have accessible names"}]}, ensure_ascii=False))
        chart = _write(tmp / "chart.csv", "month,value\nJan,10\nFeb,20\nMar,15\nApr,30\n")
        ad = _write(tmp / "ad.csv", "start,end,description\n1,4,A woman opens the door and smiles.\n3,5,She walks into a bright room quickly and looks around.\n")
        docx = _make_docx(tmp / "report.docx")
        wav = _make_wav(tmp / "tone.wav")
        evidence = tmp / "evidence"
        evidence.mkdir()
        _write(evidence / "audit.json", "{}")
        _write(evidence / "notes.md", "Evidence")
        # (capability id, input, expectation)
        tests = [
            ("automated-accessibility-scanner", html, {"status": "failed", "has_findings": True}),
            ("color-contrast-checker", html, {"status": "failed", "has_findings": True}),
            ("accessible-office-document-checker", docx, {"status": "attention", "has_findings": True}),
            ("alt-text-quality-checker", html, {"status": "attention", "has_findings": True}),
            ("placeholder-and-variable-validator", locale, {"status": "failed", "has_findings": True}),
            ("filipino-english-bilingual-consistency-checker", locale, {"status": "failed", "has_findings": True}),
            ("caption-quality-auditor", captions, {"status": "attention", "has_findings": True}),
            ("language-quality-scorecard", review, {"status": "failed", "has_findings": True}),
            ("terminology-database-manager", termbase, {"status": "attention", "has_findings": True}),
            ("translation-memory-manager", tm, {"status": "passed", "has_findings": False}),
            ("document-plain-language-reviewer", html, {"status": "attention", "has_findings": True}),
            ("font-coverage-for-target-languages", html, {"status": "attention", "has_findings": True}),
            ("extended-audio-description-planner", ad, {"status": "attention", "has_findings": True}),
            ("media-loudness-and-clarity-reviewer", wav, {"status": "passed", "has_findings": False}),
            ("chart-description-generator", chart, {"status": "passed", "has_artifacts": True}),
            ("accessibility-statement-generator", None, {"status": "attention", "has_artifacts": True}),
            ("human-accessibility-testing", None, {"status": "attention", "has_artifacts": True}),
            ("accessibility-risk-register-manager", json.dumps({"findings": [{"title": "Button has no name", "severity": "critical", "check": "names"}, {"title": "Low contrast", "severity": "serious", "check": "contrast"}]}), {"status": "failed", "has_artifacts": True}),
            ("accessibility-release-gatekeeper", json.dumps({"findings": [{"title": "Button has no name", "severity": "critical", "check": "names"}]}), {"status": "failed", "has_artifacts": True}),
            ("accessibility-maturity-assessor", scores, {"status": "attention", "has_artifacts": True}),
            ("accessibility-standards-crosswalk-builder", None, {"status": "passed", "has_artifacts": True}),
            ("wcag-2-2-aa-requirements-mapper", journeys, {"status": "attention", "has_artifacts": True}),
            ("accessibility-kpi-and-okr-designer", None, {"status": "passed", "has_artifacts": True}),
            ("accessibility-exception-reviewer", exception, {"status": "attention", "has_artifacts": True}),
            ("accessibility-evidence-repository-manager", evidence, {"status": "attention", "has_artifacts": True}),
            ("accessibility-defect-triage", defects, {"status": "attention", "has_artifacts": True}),
            ("accessibility-remediation-verifier", retest, {"status": "failed", "has_artifacts": True}),
            ("vpat-and-acr-reviewer", vendor, {"status": "attention", "has_findings": True}),
            ("accessibility-ci-cd-integrator", None, {"status": "passed", "has_artifacts": True}),
            ("research-finding-traceability-manager", trace, {"status": "attention", "has_artifacts": True}),
            ("pseudolocalization-test-suite", locale, {"status": "passed", "has_artifacts": True}),
            ("philippine-locale-formatter", values, {"status": "attention", "has_artifacts": True}),
        ]
        # For tests with inline JSON, write a temp file.
        results = []
        for cid, inp, expect in tests:
            cap = registry.find(reg, cid)
            input_path = None
            if inp is not None:
                if isinstance(inp, str) and inp.strip().startswith(("{", "[")):
                    p = _write(tmp / f"{cid}.json", inp)
                    input_path = p
                elif isinstance(inp, Path):
                    input_path = inp
                else:
                    input_path = inp
            out_dir = out / cid
            envelope, _, _ = run_capability(cap, str(input_path) if input_path else None, "", out_dir, config, write=True)
            problems = []
            if envelope["status"] == "error":
                problems.append("engine error: " + envelope.get("error", {}).get("message", "unknown"))
            if "status" in expect and envelope["status"] != expect["status"]:
                problems.append(f"expected status {expect['status']}, got {envelope['status']}")
            if expect.get("has_findings") and not envelope.get("findings"):
                problems.append("expected findings, got none")
            if expect.get("has_artifacts") and not envelope.get("artifacts"):
                problems.append("expected artifacts, got none")
            if not (out_dir / f"{cid}.json").exists():
                problems.append("JSON output was not written")
            results.append((cid, envelope["status"], problems))
        print(f"Self-test: {len(results)} engines exercised")
        print(f"{'CAPABILITY':<52} {'STATUS':<10} RESULT")
        failures = 0
        for cid, status, problems in results:
            print(f"{cid:<52} {status:<10} {'OK' if not problems else '; '.join(problems)}")
            if problems:
                failures += 1
        print(f"\n{len(results)-failures} passed, {failures} failed")
        print(f"Temporary test directory: {tmp}")
        if args.keep:
            print("Kept temporary directory (--keep).")
        else:
            core.cleanup(tmp)
        return 0 if failures == 0 else 1
    finally:
        pass


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="accessibility-localization",
        description="Unified accessibility and localization Skill Tool (220 capabilities, local, standard-library only).")
    parser.add_argument("--version", action="version", version=f"accessibility-localization {core.TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capabilities", help="List the 220 capabilities")
    p.add_argument("--category", help="Filter by category slug")
    p.add_argument("--search", help="Filter by text")
    p.add_argument("--status", choices=["auto", "assisted", "human"], help="Filter by status")
    p.add_argument("--operation", help="Filter by operation")
    p.add_argument("--long", action="store_true", help="Include descriptions and checks")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("categories", help="List the 18 categories")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_categories)

    p = sub.add_parser("describe", help="Describe a capability")
    p.add_argument("capability_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("run", help="Run one capability")
    p.add_argument("capability_id")
    p.add_argument("--input", help="Input file or directory")
    p.add_argument("--brief", help="Capability brief or context")
    p.add_argument("--output", default="output", help="Output directory or JSON file (default: output)")
    p.add_argument("--config", help="Config JSON path")
    p.add_argument("--timeout", type=int, help="Per-run timeout in seconds")
    p.add_argument("--limit", type=int, help="Maximum findings")
    p.add_argument("--json", action="store_true", help="Print the full JSON envelope")
    p.add_argument("--no-markdown", action="store_true", help="Do not write the Markdown report")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("run-category", help="Run every capability in a category")
    p.add_argument("category")
    p.add_argument("--input")
    p.add_argument("--brief")
    p.add_argument("--output", default="output")
    p.add_argument("--config")
    p.add_argument("--timeout", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--no-markdown", action="store_true")
    p.set_defaults(func=cmd_run_many)

    p = sub.add_parser("run-all", help="Run every capability that accepts the input")
    p.add_argument("--input")
    p.add_argument("--brief")
    p.add_argument("--output", default="output")
    p.add_argument("--config")
    p.add_argument("--timeout", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--no-markdown", action="store_true")
    p.set_defaults(func=cmd_run_many)

    p = sub.add_parser("validate", help="Validate config and input")
    p.add_argument("--input")
    p.add_argument("--capability-id")
    p.add_argument("--config")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("config", help="Show or initialize configuration")
    p.add_argument("--init", action="store_true")
    p.add_argument("--output", default="output/default-config.json")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("self-test", help="Run the built-in engine self-test")
    p.add_argument("--keep", action="store_true", help="Keep the temporary test directory")
    p.set_defaults(func=cmd_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    registry_path = HERE / registry.REGISTRY_NAME
    reg = registry.load_registry(registry_path)
    config_path = Path(args.config) if getattr(args, "config", None) else None
    config = Config.load(config_path)
    try:
        return args.func(args, reg, config)
    except ToolError as exc:
        print(json.dumps({"status": "error", "error": exc.as_dict()}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
