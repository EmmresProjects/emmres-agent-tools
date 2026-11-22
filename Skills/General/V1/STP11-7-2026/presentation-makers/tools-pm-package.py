#!/usr/bin/env python3
"""tools-pm-package.py - delivery package builder and clean-state verifier.

Load order: last of the libraries, after tools-pm-core.py, tools-pm-render.py
and tools-pm-audit.py; used by the primary entry point's 'package' command.

build_package()  assembles a flat delivery folder + zip: editable spec, rendered
                 outputs, audit evidence, sources, and operating instructions.
verify_package() extracts the zip into a fresh temporary directory and re-opens
                 every file, which is the "clean-state" check before delivery.

Overwriting an existing package requires explicit approval (approve=True); the
primary tool maps that to the --force flag and exit code 5.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

for _name, _file in (("pm_core", "tools-pm-core.py"), ("pm_intake", "tools-pm-intake.py"),
                     ("pm_plan", "tools-pm-plan.py"), ("pm_render", "tools-pm-render.py"),
                     ("pm_audit", "tools-pm-audit.py")):
    if _name not in sys.modules:  # pragma: no cover - import shim
        _dir = os.path.dirname(os.path.abspath(__file__))
        _spec = importlib.util.spec_from_file_location(_name, os.path.join(_dir, _file))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _mod
        _spec.loader.exec_module(_mod)
import pm_core  # noqa: E402
pm_audit = sys.modules["pm_audit"]  # bound for render_report()

FORBIDDEN_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz")


def package_name(spec):
    title = (spec.get("deck") or {}).get("title") or "presentation"
    return "%s-delivery" % pm_core.slugify(title)[:60]


def _readme(spec, report, files):
    deck = spec.get("deck", {})
    lines = [
        "# %s - delivery package" % deck.get("title", "Presentation"),
        "",
        "Prepared %s by the presentation-makers Skill Tool (v%s)." % (pm_core.now_iso(), pm_core.TOOL_VERSION),
        "",
        "## What is in this package",
        "",
    ]
    for name, description in files:
        lines.append("- `%s` - %s" % (name, description))
    lines += [
        "",
        "## How to open it",
        "",
        "1. Open `deck.pptx` in PowerPoint, Keynote, or LibreOffice Impress. It is a 16:9 deck with "
        "speaker notes on every slide.",
        "2. If the opener cannot render the fonts, `handout.pdf` is the visual fallback and "
        "`deck.html` runs in any browser with no network connection.",
        "3. `speaker-notes.md` holds the narration and timing; `outline.md` is the one-page route.",
        "4. `audit-report.md` lists every check that ran, its status, and the fix for anything flagged.",
        "",
        "## Editing",
        "",
        "- `deck-spec.json` is the editable source of truth. Change it, then re-run "
        "`build` and `audit` to regenerate every format consistently.",
        "- Fonts are referenced by family name and are not redistributed. Substitute an approved "
        "family with similar metrics and re-check the layout.",
        "",
        "## Audit status at packaging",
        "",
        "- Status: %s" % report.get("status", "unknown"),
        "- Score: %s / 100" % report.get("score", "n/a"),
        "- Errors: %s, warnings: %s" % (report.get("counts", {}).get("error", 0),
                                        report.get("counts", {}).get("warning", 0)),
        "",
        "## Approvals still required",
        "",
    ]
    checkpoints = spec.get("review_checkpoints") or []
    if checkpoints:
        for checkpoint in checkpoints:
            if checkpoint.get("required", True) and checkpoint.get("status", "pending") != "approved":
                lines.append("- [ ] %s (%s)" % (checkpoint.get("stage"), checkpoint.get("approver")))
    else:
        lines.append("- [ ] No checkpoints were recorded; confirm the owner list before sending.")
    lines += [
        "",
        "## Limits of this package",
        "",
        "- No fonts, music, or stock media are included; only files you supplied are referenced.",
        "- Video and GIF export are not bundled. Use an approved local converter when one is installed.",
        "- Distribution rights for supplied imagery remain with the supplier named in the deck spec.",
        "",
    ]
    return "\n".join(lines)


def build_package(spec, build_dir, out_dir, config, report=None, approve=False, extra_files=None):
    """Assemble the delivery folder and zip. Returns a structured result."""
    out_dir = pm_core.resolve_path(out_dir)
    build_dir = pm_core.resolve_path(build_dir)
    name = package_name(spec)
    folder = os.path.join(out_dir, name)
    zip_path = os.path.join(out_dir, "%s.zip" % name)

    if os.path.exists(zip_path) and not approve:
        raise pm_core.PMError(
            "Refusing to overwrite existing package: %s" % os.path.basename(zip_path),
            code="package-exists",
            exit_code=pm_core.EXIT_APPROVAL,
            hint="Re-run with --force to replace the existing delivery package.",
        )

    staging = tempfile.mkdtemp(prefix="pm-package-")
    diagnostics = []
    included = []
    try:
        candidates = [
            ("deck.pptx", "Editable PowerPoint deck with speaker notes"),
            ("handout.pdf", "Static PDF handout of the visible slides"),
            ("deck.html", "Self-contained browser deck, no network needed"),
            ("handout.md", "Markdown handout of every visible slide"),
            ("speaker-notes.md", "Narration, timing, and per-slide notes"),
            ("outline.md", "One-page structural outline"),
            ("captions.srt", "Caption file drafted from the speaker notes"),
        ]
        for filename, description in candidates:
            source = os.path.join(build_dir, filename)
            if not os.path.isfile(source):
                continue
            size = os.path.getsize(source)
            if size > config["limits"].get("max_file_bytes", 26214400):
                diagnostics.append(pm_core.diag(
                    "warning", "package-file-large",
                    "%s is %.1f MB and will slow delivery" % (filename, size / 1048576.0)))
            shutil.copy2(source, os.path.join(staging, filename))
            included.append((filename, description))

        pm_core.write_json(os.path.join(staging, "deck-spec.json"), spec)
        included.append(("deck-spec.json", "Editable deck specification (source of truth)"))

        if spec.get("sources"):
            pm_core.write_text(os.path.join(staging, "sources.md"),
                               "# Sources\n\n" + "\n".join("- %s" % s for s in spec["sources"]) + "\n")
            included.append(("sources.md", "Every source cited across the deck"))

        if report:
            pm_core.write_text(os.path.join(staging, "audit-report.md"), pm_audit.render_report(report))
            pm_core.write_json(os.path.join(staging, "audit-report.json"), report)
            included.append(("audit-report.md", "Human-readable audit findings and gate scores"))
            included.append(("audit-report.json", "Machine-readable audit report"))

        for path in extra_files or []:
            if not os.path.isfile(path):
                diagnostics.append(pm_core.diag("warning", "package-extra-missing",
                                                "Extra file not found: %s" % path))
                continue
            if os.path.splitext(path)[1].lower() in FORBIDDEN_SUFFIXES:
                raise pm_core.PMError(
                    "Nested archives are not allowed in a delivery package: %s" % os.path.basename(path),
                    code="package-nested-archive",
                    hint="Extract the archive and add its files individually.")
            shutil.copy2(path, os.path.join(staging, os.path.basename(path)))
            included.append((os.path.basename(path), "Supplied extra file"))

        pm_core.write_text(os.path.join(staging, "README.md"),
                           _readme(spec, report or {}, included))
        included.append(("README.md", "Operating instructions and approval checklist"))

        manifest = {
            "package": name,
            "generated_at": pm_core.now_iso(),
            "generated_by": "%s %s" % (pm_core.TOOL_NAME, pm_core.TOOL_VERSION),
            "deck_title": spec.get("deck", {}).get("title", ""),
            "audit_status": (report or {}).get("status", "not-audited"),
            "audit_score": (report or {}).get("score"),
            "files": [],
        }
        for filename in sorted(os.listdir(staging)):
            path = os.path.join(staging, filename)
            if os.path.isfile(path):
                manifest["files"].append({"name": filename, "bytes": os.path.getsize(path)})
        pm_core.write_json(os.path.join(staging, "manifest.json"), manifest)

        pm_core.ensure_dir(out_dir)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        shutil.copytree(staging, folder)

        if os.path.exists(zip_path):
            os.unlink(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename in sorted(os.listdir(folder)):
                archive.write(os.path.join(folder, filename), arcname=filename)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "status": "ok",
        "name": name,
        "folder": folder,
        "zip": zip_path,
        "zip_bytes": os.path.getsize(zip_path),
        "files": [{"name": n, "purpose": d} for n, d in included],
        "diagnostics": diagnostics,
    }


def verify_package(zip_path):
    """Extract into a clean temp dir and re-open every file (clean-state check)."""
    zip_path = pm_core.resolve_path(zip_path)
    if not os.path.isfile(zip_path):
        raise pm_core.PMError("Package not found: %s" % zip_path, code="package-missing")
    findings = []
    verified = []
    tempdir = tempfile.mkdtemp(prefix="pm-verify-")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if not names:
                raise pm_core.PMError("Package archive is empty", code="package-empty")
            for name in names:
                if name.startswith("/") or ".." in name.split("/"):
                    findings.append(pm_core.diag("error", "package-unsafe-path",
                                                 "Unsafe path inside archive: %s" % name))
                if os.path.splitext(name)[1].lower() in FORBIDDEN_SUFFIXES:
                    findings.append(pm_core.diag("error", "package-nested-archive",
                                                 "Nested archive inside package: %s" % name))
            bad = archive.testzip()
            if bad:
                findings.append(pm_core.diag("error", "package-corrupt",
                                             "CRC check failed for %s" % bad))
            archive.extractall(tempdir)
        for name in sorted(names):
            path = os.path.join(tempdir, name)
            if not os.path.isfile(path):
                continue
            ok, detail = _verify_file(path)
            if ok:
                verified.append({"name": name, "check": detail, "bytes": os.path.getsize(path)})
            else:
                findings.append(pm_core.diag("error", "package-file-invalid",
                                             "%s failed verification: %s" % (name, detail)))
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)

    counts = pm_core.count_diagnostics(findings)
    return {
        "status": "pass" if counts["error"] == 0 else "fail",
        "zip": zip_path,
        "file_count": len(verified) + counts["error"],
        "verified": verified,
        "findings": findings,
        "counts": counts,
    }


def _verify_file(path):
    name = os.path.basename(path)
    if name.endswith(".pptx"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                slides = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
                notes = [n for n in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]
                if "[Content_Types].xml" not in names:
                    return False, "missing [Content_Types].xml"
                if not slides:
                    return False, "no slide parts"
                return True, "%d slides, %d notes pages" % (len(slides), len(notes))
        except zipfile.BadZipFile:
            return False, "not a zip archive"
    if name.endswith(".pdf"):
        with open(path, "rb") as fh:
            data = fh.read()
        if not data.startswith(b"%PDF-"):
            return False, "missing PDF header"
        if b"%%EOF" not in data[-2048:]:
            return False, "missing EOF marker"
        pages = data.count(b"/Type /Page ") + data.count(b"/Type /Page/")
        return True, "PDF with %d page object(s)" % pages
    if name.endswith(".html"):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if "<html" not in text.lower():
            return False, "no html root"
        refs = re.findall(r"""(?:src|href)\s*=\s*["'](?P<url>[^"'#][^"']*)["']""", text, re.I)
        external = [r for r in refs if not r.startswith("data:")]
        if external:
            return False, "%d external resource reference(s)" % len(external)
        return True, "self-contained HTML deck"
    if name.endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            json.load(fh)
        return True, "valid JSON"
    if name.endswith((".md", ".srt", ".txt")):
        size = os.path.getsize(path)
        return (size > 0), "%d bytes of text" % size
    return True, "no deep check available"


if __name__ == "__main__":  # pragma: no cover - module smoke test
    print("tools-pm-package.py is a library module; use tools-presentation-makers.py package")
