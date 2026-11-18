#!/usr/bin/env python3
"""
tools-build-catalog.py — Regenerate asset-catalog.json from the source catalog PDF
or from pre-extracted plain text.

Role in the Skill Tool
----------------------
Maintenance / provenance tool. The runtime tools (tools-production-deployment.py
and tools-deployment-engine.py) read the committed asset-catalog.json dataset and
never touch the PDF. Use this script only when the upstream catalog is republished.

Input sources (choose exactly one):
  --pdf  PATH   Extract text from the catalog PDF (requires the optional
                third-party package `pypdf`; see knowledge-readme.md).
  --text PATH   Parse a plain-text dump previously produced from the PDF
                (e.g. `python -c` with pypdf, or any external extractor).

Output:
  --output PATH Where to write the structured catalog JSON
                (default: asset-catalog.json beside this script).

The parser is deliberately strict: it fails loudly (non-zero exit, structured
error on stderr) when category or skill counts do not match the expected
integrity invariants (30 categories, 1212 skills, sequential numbering) unless
--allow-partial is passed. That keeps a corrupted extraction from silently
replacing a known-good dataset.

Exit codes: 0 success, 2 usage/validation error, 3 integrity check failed,
130 interrupted.
"""

import argparse
import json
import re
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = TOOL_DIR / "asset-catalog.json"

EXPECTED_CATEGORIES = 30
EXPECTED_SKILLS = 1212

HEADER_MARKERS = (
    "CLOUD, SERVER, AND PRODUCTION DEPLOYMENT • SKILL CATALOG",
    "Secure • Observable • Recoverable • Production-tested",
)
PAGE_RE = re.compile(r"^===PAGE \d+===\s*$")
CATEGORY_RE = re.compile(r"^(\d{1,2})\.\s+([A-Za-z].+)$")
SKILL_RE = re.compile(r"^(\d{1,2})\.\s+Skill Name:\s*(.+)$")
DESC_RE = re.compile(r"^Description:\s*(.*)$")
COUNT_RE = re.compile(r"^\d+ skill concepts")
BULLET_RE = re.compile(r"^[\u2022\u25aa\u00b7\u25cf\uf0b7\*\-]\s*")

PATTERN_RULES = [
    ("decision-matrix", lambda d: "choose a production design based on" in d),
    ("evaluation-gate", lambda d: "pass, warn, and fail thresholds" in d),
    ("runbook-definition", lambda d: "with prerequisites, accountable roles" in d),
    ("provision-from-code", lambda d: "from reviewed infrastructure code" in d),
    ("operate-service", lambda d: "through versioned configuration" in d),
]
PATTERN_DEFAULT = "focused-capability"

PATTERN_SUMMARY = {
    "focused-capability": "Focused production capability with declared inputs and outputs, IaC support, least privilege, encryption, monitoring, alerting, rollback, disaster recovery, and production acceptance criteria.",
    "provision-from-code": "Provision from reviewed infrastructure code and pinned artifacts with security and cost guardrails, verified live endpoints, and a tested rollback or teardown path.",
    "runbook-definition": "Operating runbook definition with prerequisites, accountable roles, ordered actions, approval and rollback points, escalation paths, recovery objectives, required evidence, and a verified completion condition.",
    "decision-matrix": "Weighted decision procedure across availability, latency, residency, security, operational burden, recovery targets, scalability, lock-in, and total cost, with rejected options recorded.",
    "evaluation-gate": "Evaluation gate with explicit pass, warn, and fail thresholds; retains environment, region, resource identifiers, observed evidence, risk rating, owner, remediation, and a repeatable retest procedure.",
    "operate-service": "Operated through versioned configuration, least-privilege access, encryption, quotas, health checks, observability, automated recovery, and clearly tested failure behavior.",
}


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")


def classify(description):
    for name, rule in PATTERN_RULES:
        if rule(description):
            return name
    return PATTERN_DEFAULT


def extract_text_from_pdf(pdf_path):
    try:
        import pypdf  # optional dependency, only needed for --pdf
    except ImportError:
        raise RuntimeError(
            "The --pdf option requires the optional package 'pypdf'. "
            "Install it with: python3 -m pip install pypdf  "
            "or extract the PDF to plain text with another tool and use --text."
        )
    reader = pypdf.PdfReader(str(pdf_path))
    parts = []
    for i, page in enumerate(reader.pages):
        parts.append(f"\n===PAGE {i + 1}===\n")
        parts.append(page.extract_text() or "")
    return "".join(parts)


def parse_catalog(text):
    lines = text.split("\n")
    categories = []
    current_cat = None
    current_skill = None
    trailing = {"coreProductionAcceptance": [], "designNotes": []}
    trailing_section = None  # 'core' | 'notes' | None, for the end-of-document section
    bullet_open = False      # True while a trailing-reference bullet may still receive wrapped lines

    def flush_skill():
        nonlocal current_skill
        if current_skill is not None and current_cat is not None:
            description = " ".join(" ".join(current_skill["desc_lines"]).split())
            current_skill.pop("desc_lines", None)
            current_skill["description"] = description
            current_skill["pattern"] = classify(description)
            current_cat["skills"].append(current_skill)
        current_skill = None

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip()
        i += 1
        s = raw.strip()
        if not s:
            continue
        if PAGE_RE.match(s):
            continue
        if s in HEADER_MARKERS:
            continue
        if s == "Core Production Acceptance Reference":
            flush_skill()
            trailing_section = "core"
            bullet_open = False
            continue
        if s == "Catalog Design Notes":
            flush_skill()
            trailing_section = "notes"
            bullet_open = False
            continue
        skill_m = SKILL_RE.match(s)
        if skill_m:
            if current_cat is None:
                continue  # index page noise
            flush_skill()
            current_skill = {"number": int(skill_m.group(1)), "name": skill_m.group(2).strip(), "desc_lines": []}
            continue
        desc_m = DESC_RE.match(s)
        if desc_m:
            if current_skill is not None:
                current_skill["desc_lines"].append(desc_m.group(1).strip())
            continue
        cat_m = CATEGORY_RE.match(s)
        if cat_m and "Skill Name" not in s:
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j < n and COUNT_RE.match(lines[j].strip()):
                flush_skill()
                current_cat = {"number": int(cat_m.group(1)), "name": cat_m.group(2).strip(), "skills": []}
                categories.append(current_cat)
                i = j + 1
                trailing_section = None
                continue
        if trailing_section and BULLET_RE.match(s):
            bullet = BULLET_RE.sub("", s).strip()
            if bullet:
                key = "coreProductionAcceptance" if trailing_section == "core" else "designNotes"
                trailing[key].append(bullet)
                bullet_open = True
            continue
        if trailing_section and bullet_open:
            # wrapped continuation of the previous bullet — join it
            key = "coreProductionAcceptance" if trailing_section == "core" else "designNotes"
            trailing[key][-1] = (trailing[key][-1] + " " + s).strip()
            continue
        if current_skill is not None and current_skill["desc_lines"]:
            current_skill["desc_lines"].append(s)
    flush_skill()
    return categories, trailing


def finalize(categories, trailing):
    used_ids = {}
    out_categories = []
    for cat in categories:
        cat_slug = slugify(cat["name"])
        out_skills = []
        for skill in cat["skills"]:
            skill_slug = slugify(skill["name"])
            sid = f"{cat_slug}:{skill_slug}"
            used_ids[sid] = used_ids.get(sid, 0) + 1
            out_skills.append({
                "id": sid,
                "number": skill["number"],
                "name": skill["name"],
                "description": skill["description"],
                "pattern": skill["pattern"],
            })
        out_categories.append({
            "number": cat["number"],
            "id": f"cat-{cat['number']:02d}",
            "slug": cat_slug,
            "name": cat["name"],
            "skillCount": len(out_skills),
            "skills": out_skills,
        })
    duplicates = {k: v for k, v in used_ids.items() if v > 1}
    if duplicates:
        raise RuntimeError(f"Duplicate skill ids generated: {sorted(duplicates)[:5]}")
    return out_categories, trailing


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate asset-catalog.json from the catalog PDF or a plain-text dump.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", metavar="PATH", help="Source catalog PDF (requires pypdf)")
    src.add_argument("--text", metavar="PATH", help="Plain-text dump of the catalog")
    parser.add_argument("--output", metavar="PATH", default=str(DEFAULT_OUTPUT),
                        help="Where to write asset-catalog.json (default: beside this script)")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Do not fail on category/skill count or numbering mismatches")
    args = parser.parse_args()

    try:
        if args.pdf:
            pdf_path = Path(args.pdf)
            if not pdf_path.is_file():
                parser.error(f"PDF not found: {args.pdf}")
            text = extract_text_from_pdf(pdf_path)
        else:
            text_path = Path(args.text)
            if not text_path.is_file():
                parser.error(f"Text file not found: {args.text}")
            text = text_path.read_text(encoding="utf-8", errors="replace")
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": {"kind": "dependency", "message": str(exc)}}), file=sys.stderr)
        return 2

    categories, trailing = parse_catalog(text)
    categories, trailing = finalize(categories, trailing)

    total_skills = sum(c["skillCount"] for c in categories)
    problems = []
    if len(categories) != EXPECTED_CATEGORIES:
        problems.append(f"expected {EXPECTED_CATEGORIES} categories, parsed {len(categories)}")
    if total_skills != EXPECTED_SKILLS:
        problems.append(f"expected {EXPECTED_SKILLS} skills, parsed {total_skills}")
    for cat in categories:
        nums = [s["number"] for s in cat["skills"]]
        if nums != list(range(1, len(nums) + 1)):
            problems.append(f"numbering gap in category {cat['number']} ({cat['name']})")
    if problems and not args.allow_partial:
        print(json.dumps({"status": "error", "error": {"kind": "integrity", "problems": problems}}), file=sys.stderr)
        return 3

    dataset = {
        "catalog": "Cloud, Server, and Production Deployment — Skill Catalog",
        "edition": "1.0",
        "published": "2026-11",
        "source": "Cloud_Server_and_Production_Deployment_Skill_Catalog.pdf",
        "categoryCount": len(categories),
        "skillCount": total_skills,
        "patternLegend": PATTERN_SUMMARY,
        "coreProductionAcceptance": trailing["coreProductionAcceptance"],
        "catalogDesignNotes": trailing["designNotes"],
        "categories": categories,
    }
    out_path = Path(args.output)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)  # atomic replace; no partial files left behind
    print(json.dumps({
        "status": "ok",
        "output": str(out_path),
        "categories": len(categories),
        "skills": total_skills,
        "patterns": {p: sum(1 for c in categories for s in c["skills"] if s["pattern"] == p) for p in sorted(PATTERN_SUMMARY)},
        "warnings": problems,
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": {"kind": "interrupted", "message": "Interrupted by user (Ctrl+C)."}}), file=sys.stderr)
        sys.exit(130)
