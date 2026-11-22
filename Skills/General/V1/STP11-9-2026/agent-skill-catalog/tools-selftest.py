#!/usr/bin/env python3
"""Test suite for agent-skill-catalog.

Runs the real CLI surface (``tools-skillcatalog.py``) in subprocesses, in a
throw-away directory, with both absolute and relative paths. No network access
is required; the one online test is skipped unless ``--online`` is passed.

Usage:
    python3 agent-skill-catalog/tools-selftest.py [--verbose] [--keep] [--online]

Exit code 0 = every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "tools-skillcatalog.py")
PY = sys.executable
RESULTS: list[tuple[str, bool, str]] = []
VERBOSE = False


def run(args: list[str], cwd: str | None = None, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    command = [PY, TOOL] + args
    if VERBOSE:
        print("    $ (cd %s && %s)" % (cwd or HERE, " ".join(command)))
    return subprocess.run(command, cwd=cwd or HERE, capture_output=True, text=True,
                          input=stdin_text, timeout=300)


def check(label: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(condition), detail))
    return bool(condition)


def fail_detail(result: subprocess.CompletedProcess) -> str:
    return ("exit=%s stdout=%r stderr=%r" % (result.returncode, result.stdout[-400:], result.stderr[-400:]))


def main() -> int:
    global VERBOSE
    ap = argparse.ArgumentParser(description="Run the agent-skill-catalog test suite.")
    ap.add_argument("--verbose", action="store_true", help="echo every command")
    ap.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    ap.add_argument("--online", action="store_true", help="also run the bounded online link check")
    ap.add_argument("--json", action="store_true", help="emit a JSON result summary")
    args = ap.parse_args()
    VERBOSE = args.verbose

    work = tempfile.mkdtemp(prefix="skillcatalog-selftest-")
    try:
        _run_all(work, online=args.online)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("kept workspace: %s" % work)

    failed = [item for item in RESULTS if not item[1]]
    if args.json:
        print(json.dumps({
            "ok": not failed,
            "checks": len(RESULTS),
            "failed": [{"label": label, "detail": detail} for label, ok, detail in failed],
        }, indent=2))
    else:
        for label, ok, detail in RESULTS:
            print(("PASS  " if ok else "FAIL  ") + label + ((" :: " + detail) if detail and not ok else ""))
        print("\n%d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 0 if not failed else 1


def _run_all(work: str, online: bool) -> None:
    assets = os.path.join(HERE, "asset-catalog-240-skills.md")
    seed = os.path.join(HERE, "asset-reference-seed.json")
    fixture = os.path.join(HERE, "asset-fixture-library.json")

    # 1. help and capability list ------------------------------------------- #
    helped = run(["--help"])
    check("--help exits 0 and prints usage", helped.returncode == 0 and "capabilities" in helped.stdout,
          fail_detail(helped))

    caps = run(["capabilities", "--json"])
    try:
        cap_payload = json.loads(caps.stdout)
    except json.JSONDecodeError:
        cap_payload = {}
    check("capabilities --json lists every operation",
          caps.returncode == 0 and len(cap_payload.get("capabilities", [])) >= 14,
          fail_detail(caps))
    check("capabilities names the primary entry point",
          cap_payload.get("entry_point") == "tools-skillcatalog.py")

    # 2. parse the bundled catalog ------------------------------------------ #
    parsed_path = os.path.join(work, "parsed.json")
    parsed = run(["parse", "--input", assets, "--out", parsed_path, "--stats", "--json"])
    try:
        parsed_doc = json.loads(open(parsed_path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        parsed_doc = {}
    check("parse bundled catalog writes JSON with 24 categories / 240 skills",
          parsed.returncode == 0 and parsed_doc.get("category_count") == 24
          and parsed_doc.get("skill_count") == 240, fail_detail(parsed))
    check("parse reports the detected format", parsed_doc.get("format") == "catalog",
          str(parsed_doc.get("format")))

    # 3. parse messy inputs: HTML paste, Markdown list, numbered text ------- #
    html_input = os.path.join(work, "paste.html")
    with open(html_input, "w", encoding="utf-8") as handle:
        handle.write("""<html><head><style>.x{color:red}</style></head><body>
        <h1 class="title" data-kind="catalog">Pasted Catalog</h1>
        <section id="s1"><h2 class="cat" data-cat="7">1. Security and Privacy</h2>
        <ol start="1"><li class="skill" data-skill="a"><span class="n">1.</span>
        <strong>Skill Name:</strong> Threat Model Facilitator<br>
        <span class="desc"><em>Description:</em> Identify assets, trust boundaries, and attackers.</span></li>
        <li class="skill" data-skill="b"><span class="n">2.</span>
        <strong>Skill Name:</strong> Access Review Assistant<br>
        <span class="desc"><em>Description:</em> Compare roles and actual privileges, then prepare revocation.</span></li>
        </ol></section>
        <h2 class="cat">2. Quality</h2>
        <ol><li><strong>Skill Name:</strong> Repair Loop Coordinator<br>
        <span class="desc"><em>Description:</em> Isolate the smallest failure, repair once, rerun affected checks.</span></li></ol>
        </body></html>""")
    html_out = os.path.join(work, "from-html.json")
    html_parsed = run(["parse", "--input", html_input, "--out", html_out])
    try:
        html_doc = json.loads(open(html_out, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        html_doc = {}
    names = [skill["name"] for cat in html_doc.get("categories", []) for skill in cat["skills"]]
    check("HTML paste is stripped to categories and skills",
          html_parsed.returncode == 0 and html_doc.get("category_count") == 2 and len(names) == 3,
          fail_detail(html_parsed) + " names=%s" % names)
    check("HTML paste keeps clean names and descriptions",
          "Threat Model Facilitator" in names and all("<" not in name for name in names)
          and all(cat["skills"] and cat["skills"][0]["description"] for cat in html_doc.get("categories", [])))

    md_input = os.path.join(work, "paste.md")
    with open(md_input, "w", encoding="utf-8") as handle:
        handle.write("""# Skills for review work

## 3. Review helpers

- **Code Review Specialist** — Review correctness, security and maintainability.
- **Test Strategy Designer** — Build unit, integration and system test plans.
- **Release Readiness Inspector**: Check builds, migrations, rollback and documentation.
""")
    md_out = os.path.join(work, "from-md.json")
    md_parsed = run(["parse", "--input", md_input, "--out", md_out])
    try:
        md_doc = json.loads(open(md_out, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        md_doc = {}
    md_names = [skill["name"] for cat in md_doc.get("categories", []) for skill in cat["skills"]]
    check("Markdown bullet catalog is parsed into 3 skills",
          md_parsed.returncode == 0 and len(md_names) == 3, fail_detail(md_parsed) + " %s" % md_names)
    check("Markdown category name is preserved",
          md_doc.get("categories", [{}])[0].get("name") == "Review helpers", str(md_doc.get("categories", [{}])[0].get("name")))

    # 4. invalid and missing input ----------------------------------------- #
    empty = os.path.join(work, "empty.txt")
    open(empty, "w").close()
    empty_run = run(["parse", "--input", empty])
    check("empty input fails with exit 2", empty_run.returncode == 2, fail_detail(empty_run))
    check("empty input error message is actionable", "empty" in empty_run.stderr.lower(), empty_run.stderr[:200])
    missing_run = run(["parse", "--input", os.path.join(work, "does-not-exist.txt")])
    check("missing input file fails with exit 2", missing_run.returncode == 2, fail_detail(missing_run))
    prose = os.path.join(work, "prose.txt")
    with open(prose, "w", encoding="utf-8") as handle:
        handle.write("Just some prose without any catalog structure.\nNothing here looks like a skill.\n")
    prose_run = run(["parse", "--input", prose])
    check("unparseable prose fails with exit 2 and guidance",
          prose_run.returncode == 2 and "skill" in prose_run.stderr.lower(), fail_detail(prose_run))

    # 5. validate ----------------------------------------------------------- #
    validated = run(["validate", "--json"])
    try:
        validate_doc = json.loads(validated.stdout)
    except json.JSONDecodeError:
        validate_doc = {}
    check("bundled catalog validates clean (240 skills, no errors)",
          validated.returncode == 0 and validate_doc.get("ok") is True
          and validate_doc.get("counts", {}).get("skills") == 240, fail_detail(validated))

    broken = os.path.join(work, "broken.md")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("""## 1. Duplicates

1. Skill Name: Same Name
Description: First description that is long enough to pass the length rule.

2. Skill Name: Same Name
Description: Second description that is long enough to pass the length rule.

3. Skill Name: Thin
Description: short
""")
    broken_run = run(["validate", "--catalog", broken, "--strict", "--json"])
    try:
        broken_doc = json.loads(broken_run.stdout)
    except json.JSONDecodeError:
        broken_doc = {}
    codes = {item["code"] for item in broken_doc.get("errors", [])}
    check("broken catalog fails validation when strict", broken_run.returncode == 1, fail_detail(broken_run))
    check("duplicate skill names are reported", "SKILL_DUPLICATE" in codes, str(sorted(codes)))

    # recovery path: fix the broken catalog and re-validate
    fixed = os.path.join(work, "fixed.md")
    with open(fixed, "w", encoding="utf-8") as handle:
        handle.write("""## 1. Duplicates

1. Skill Name: Same Name
Description: First description that is long enough to pass the length rule.

2. Skill Name: Different Name
Description: Second description that is long enough to pass the length rule.

3. Skill Name: Third Name
Description: Third description that is long enough to pass the length rule.
""")
    fixed_run = run(["validate", "--catalog", fixed])
    check("recovery: corrected catalog validates with exit 0", fixed_run.returncode == 0, fail_detail(fixed_run))

    # 6. search: same query twice, then a changed query --------------------- #
    first = run(["search", "--query", "threat model", "--limit", "5", "--json"])
    try:
        first_doc = json.loads(first.stdout)
    except json.JSONDecodeError:
        first_doc = {}
    first_names = [item["name"] for item in first_doc.get("matches", [])]
    check("search returns Threat Model Facilitator first",
          first.returncode == 0 and first_names and first_names[0] == "Threat Model Facilitator", str(first_names[:3]))
    second = run(["search", "--query", "link health", "--limit", "5", "--json"])
    second_doc = json.loads(second.stdout or "{}")
    second_names = [item["name"] for item in second_doc.get("matches", [])]
    check("changed query changes the result set", second_names and second_names != first_names, str(second_names[:3]))
    check("search --category restricts results to that category",
          all(item["category_index"] == 9 for item in json.loads(
              run(["search", "--query", "review access", "--category", "9", "--limit", "5", "--json"]).stdout or "{}"
          ).get("matches", [])))
    no_match = run(["search", "--query", "zzzzqqqq"])
    check("no-match search exits 1 with a hint", no_match.returncode == 1 and "no matches" in no_match.stdout,
          fail_detail(no_match))

    # 7. routing ------------------------------------------------------------ #
    routed = run(["route", "--intent", "triage our vulnerability findings and check who has excess access", "--top", "3", "--json"])
    try:
        route_doc = json.loads(routed.stdout)
    except json.JSONDecodeError:
        route_doc = {}
    route_names = [item["name"] for item in route_doc.get("matches", [])]
    check("route maps a security intent to security skills",
          routed.returncode == 0 and route_names and "Vulnerability Triage Analyst" in route_names,
          str(route_names))
    check("route reports the signals it matched", bool(route_doc.get("reasons")), str(route_doc.get("reasons"))[:200])
    routed_2 = json.loads(run(["route", "--intent", "our invoice from the vendor does not match the purchase order",
                               "--top", "3", "--json"]).stdout or "{}")
    route_2_names = [item["name"] for item in routed_2.get("matches", [])]
    check("a finance intent routes to finance skills",
          any(name in route_2_names for name in ("Invoice Validation Assistant", "Vendor Proposal Comparator")),
          str(route_2_names))
    nonsense = run(["route", "--intent", "apples oranges"])
    check("a nonsense intent falls below the confidence floor (exit 1)",
          nonsense.returncode == 1 and "confidence floor" in nonsense.stdout, fail_detail(nonsense))

    # 8. plan --------------------------------------------------------------- #
    plan_path = os.path.join(work, "plan-threat-model.md")
    planned = run(["plan", "--skill", "Threat Model Facilitator", "--out", plan_path])
    plan_text = open(plan_path, encoding="utf-8").read() if os.path.exists(plan_path) else ""
    check("plan writes a brief for one skill", planned.returncode == 0 and "Threat Model Facilitator" in plan_text,
          fail_detail(planned))
    check("brief carries trigger phrases, procedure, verification and limits",
          all(section in plan_text for section in ("## Trigger phrases", "## Procedure", "## Verification checklist", "## Limits")))
    check("brief leaves no unresolved template placeholders", "{{" not in plan_text)
    check("brief derives from the catalog description",
          "trust boundaries" in plan_text.lower(), plan_text[:200])

    risky = os.path.join(work, "plan-redaction.md")
    run(["plan", "--skill", "Redaction and Privacy Reviewer", "--out", risky])
    risky_text = open(risky, encoding="utf-8").read() if os.path.exists(risky) else ""
    check("approval gates are derived for a sensitive capability",
          "personal-data" in risky_text and "Approval captured" in risky_text, risky_text[:200])

    category_dir = os.path.join(work, "plans-cat1")
    category_run = run(["plan", "--category", "1", "--out", category_dir, "--json"])
    try:
        cat_doc = json.loads(category_run.stdout)
    except json.JSONDecodeError:
        cat_doc = {}
    check("plan --category writes one brief per skill",
          category_run.returncode == 0 and cat_doc.get("count") == 10
          and len([n for n in os.listdir(category_dir) if n.endswith(".md")]) == 10, fail_detail(category_run))

    unknown = run(["plan", "--skill", "There Is No Such Skill"])
    check("unknown skill exits 2", unknown.returncode == 2, fail_detail(unknown))
    overwrite = run(["plan", "--skill", "Threat Model Facilitator", "--out", plan_path])
    check("existing output is protected without --force (exit 3)", overwrite.returncode == 3, fail_detail(overwrite))
    forced = run(["plan", "--skill", "Threat Model Facilitator", "--out", plan_path, "--force"])
    check("--force replaces the existing output", forced.returncode == 0, fail_detail(forced))

    # 9. coverage and gaps -------------------------------------------------- #
    coverage = json.loads(run(["coverage", "--json"]).stdout or "{}")
    check("coverage reports 24 categories and 240 skills",
          coverage.get("categories") == 24 and coverage.get("skills") == 240, str(coverage)[:200])
    gap_doc = json.loads(run(["coverage", "--goal", "measure quantum decoherence in trapped ions", "--json"]).stdout or "{}")
    gap_terms = [gap["term"] for gap in gap_doc.get("gaps", [])]
    check("goal gap analysis surfaces terms the catalog lacks",
          gap_doc.get("ok") and gap_terms and ("quantum" in gap_terms or "ion" in gap_terms), str(gap_terms))

    # 10. reference library: build, audit, dedupe, package ------------------ #
    library = os.path.join(work, "library")
    built = run(["library", "build", "--entries", seed, "--out", library, "--today", "2026-11-09", "--json"])
    try:
        built_doc = json.loads(built.stdout)
    except json.JSONDecodeError:
        built_doc = {}
    expected_files = {"general.md", "archive-index.md", "source-schema.md"}
    check("library build writes category files plus index and schema",
          built.returncode == 0 and expected_files.issubset(set(built_doc.get("files", []))),
          fail_detail(built))
    recovered = run(["library", "build", "--entries", seed, "--out", library])
    check("library build refuses to overwrite without approval (exit 3)", recovered.returncode == 3, fail_detail(recovered))

    audited = run(["library", "audit", "--library", library, "--today", "2026-11-09",
                   "--max-age-days", "365", "--json"])
    try:
        audit_doc = json.loads(audited.stdout)
    except json.JSONDecodeError:
        audit_doc = {}
    audit_codes = {item["code"] for item in audit_doc.get("findings", [])}
    check("audit flags stale entries and keeps inactive history",
          audited.returncode == 0 and "STALE_ENTRY" in audit_codes and "STATUS_INACTIVE" in audit_codes,
          str(sorted(audit_codes)))
    check("audit is offline by default", audit_doc.get("online", {}).get("enabled") is False)
    report_path = os.path.join(work, "audit.md")
    report_run = run(["library", "audit", "--library", library, "--today", "2026-11-09", "--out", report_path])
    check("audit writes a Markdown repair-queue report",
          report_run.returncode == 0 and os.path.exists(report_path)
          and "## Repair queue" in open(report_path, encoding="utf-8").read())

    if online:
        online_run = run(["library", "audit", "--library", library, "--today", "2026-11-09",
                          "--online", "--max-urls", "2", "--timeout", "5", "--retries", "0", "--json"])
        try:
            online_doc = json.loads(online_run.stdout)
        except json.JSONDecodeError:
            online_doc = {}
        checked = online_doc.get("online", {}).get("checked", 0)
        check("bounded online audit ran at most --max-urls checks", checked <= 2, str(checked))
        check("online audit reports per-URL state", all("state" in item for item in
                                                       online_doc.get("online", {}).get("results", [])))

    invalid_entries = os.path.join(work, "invalid-entries.json")
    with open(invalid_entries, "w", encoding="utf-8") as handle:
        json.dump({"entries": [{"id": "bad-1", "title": "", "url": "not a url", "category": "",
                                "source-type": "", "authority": "bogus", "date-added": "31-12-2024",
                                "status": "weird", "annotation": ""}]}, handle)
    invalid_build = run(["library", "build", "--entries", invalid_entries,
                         "--out", os.path.join(work, "invalid-library"), "--today", "2026-11-09"])
    check("library build rejects invalid entries with exit 1 and writes nothing",
          invalid_build.returncode == 1 and not os.path.exists(os.path.join(work, "invalid-library")),
          fail_detail(invalid_build))

    fixture_dir = os.path.join(work, "fixture-library")
    fixture_build = run(["library", "build", "--entries", fixture, "--out", fixture_dir, "--today", "2026-11-09"])
    check("fixture library with stale and duplicate entries still builds",
          fixture_build.returncode == 0 and os.path.isdir(fixture_dir), fail_detail(fixture_build))

    dry = json.loads(run(["library", "dedupe", "--library", fixture_dir, "--json"]).stdout or "{}")
    check("dedupe finds the canonical duplicate group", dry.get("duplicate_groups", 0) >= 1, str(dry)[:300])
    check("dedupe dry run writes nothing", dry.get("applied") is False)
    blocked = run(["library", "dedupe", "--library", fixture_dir, "--apply"])
    check("dedupe --apply without --approve is blocked (exit 3)", blocked.returncode == 3, fail_detail(blocked))
    applied = json.loads(run(["library", "dedupe", "--library", fixture_dir, "--apply", "--approve", "--json"]).stdout or "{}")
    check("dedupe --apply --approve records the merge", applied.get("applied") is True, str(applied)[:200])
    merged_text = open(os.path.join(fixture_dir, "technology.md"), encoding="utf-8").read()
    check("merged records are tombstoned, not deleted", "status: merged" in merged_text and
          any(marker in merged_text for marker in ("Example dup A", "Example dup B")))

    pack_dir = os.path.join(work, "pack")
    packed = json.loads(run(["library", "package", "--library", library, "--name", "security-review",
                             "--category", "health-safety", "--category", "legal-policy",
                             "--out", pack_dir, "--json"]).stdout or "{}")
    pack_index = os.path.join(pack_dir, "security-review-index.md")
    check("topic pack writes an index with loading instructions",
          packed.get("ok") and os.path.exists(pack_index)
          and "progressive loading" in open(pack_index, encoding="utf-8").read(), str(packed)[:200])
    check("topic pack honours the category filter",
          set(packed.get("categories", [])) <= {"health-safety", "legal-policy"}, str(packed.get("categories")))

    # 11. evidence pre-screen ----------------------------------------------- #
    claims_path = os.path.join(work, "claims.json")
    with open(claims_path, "w", encoding="utf-8") as handle:
        json.dump([{"id": "C1", "claim": "arXiv hosts preprints that are not peer reviewed"},
                   "Creative Commons licences define attribution obligations"], handle)
    matrix_out = os.path.join(work, "matrix.md")
    matrix_csv = os.path.join(work, "matrix.csv")
    evidence = run(["evidence", "--claims", claims_path, "--library", library,
                    "--out", matrix_out, "--csv", matrix_csv, "--json"])
    try:
        evidence_doc = json.loads(evidence.stdout)
    except json.JSONDecodeError:
        evidence_doc = {}
    check("evidence builds a claim-by-source matrix",
          evidence.returncode == 0 and evidence_doc.get("claims") == 2 and evidence_doc.get("row_count", 0) >= 2,
          fail_detail(evidence))
    check("evidence keeps judgement columns for a human",
          "human must confirm" in evidence_doc.get("disclaimer", "").lower())
    check("evidence writes Markdown and CSV", os.path.exists(matrix_out) and os.path.exists(matrix_csv)
          and open(matrix_csv, encoding="utf-8").read().startswith("claim_id,claim"))
    no_library = run(["evidence", "--claims", claims_path, "--library", os.path.join(work, "nope")])
    check("evidence with a missing library fails cleanly (exit 2)", no_library.returncode == 2, fail_detail(no_library))

    # 12. scaffold, run the generated tool, check the package --------------- #
    build_dir = os.path.join(work, "buildout")
    scaffold_zip = os.path.join(work, "topic-triage.zip")
    scaffolded = run(["scaffold", "--name", "topic-triage", "--from-skill", "Support Intake Triage",
                      "--out", build_dir, "--zip", scaffold_zip, "--json"])
    try:
        scaffold_doc = json.loads(scaffolded.stdout)
    except json.JSONDecodeError:
        scaffold_doc = {}
    folder = os.path.join(build_dir, "topic-triage")
    check("scaffold creates a working folder with expected files",
          scaffolded.returncode == 0 and os.path.isdir(folder)
          and {"tools-topic-triage.py", "knowledge-readme.md", "asset-config.json", "tools-selftest.py"}
          == set(os.listdir(folder)), fail_detail(scaffolded) + str(os.listdir(folder) if os.path.isdir(folder) else []))
    check("scaffold reports a clean structure check", scaffold_doc.get("structure_ok") is True)
    check("scaffold zip is valid and holds the folder at its root",
          scaffold_doc.get("zip_ok") is True and scaffold_doc.get("zip_members", 0) >= 4, str(scaffold_doc)[:200])

    generated_tool = os.path.join(folder, "tools-topic-triage.py")
    gen_help = subprocess.run([PY, generated_tool, "--help"], capture_output=True, text=True, timeout=120)
    check("generated tool --help exits 0", gen_help.returncode == 0 and "capabilities" in gen_help.stdout,
          gen_help.stderr[:200])
    gen_caps = subprocess.run([PY, generated_tool, "capabilities"], capture_output=True, text=True, timeout=120)
    check("generated tool exposes a capability list",
          gen_caps.returncode == 0 and json.loads(gen_caps.stdout or "{}").get("capabilities"))
    gen_verify = subprocess.run([PY, generated_tool, "verify"], capture_output=True, text=True, timeout=120)
    check("generated tool verifies its own structure", gen_verify.returncode == 0, gen_verify.stdout[-200:])
    gen_missing = subprocess.run([PY, generated_tool, "run"], capture_output=True, text=True, timeout=120)
    check("generated tool rejects a missing --input (exit 2)", gen_missing.returncode == 2, gen_missing.stdout[-200:])
    gen_test = subprocess.run([PY, os.path.join(folder, "tools-selftest.py")],
                              capture_output=True, text=True, timeout=180)
    check("generated self-test passes", gen_test.returncode == 0, gen_test.stdout[-300:])

    checked = run(["check", "--path", folder, "--json"])
    try:
        checked_doc = json.loads(checked.stdout)
    except json.JSONDecodeError:
        checked_doc = {}
    check("check validates the scaffolded folder", checked.returncode == 0 and checked_doc.get("ok") is True,
          fail_detail(checked))
    zip_checked = run(["check", "--zip", scaffold_zip, "--json"])
    try:
        zip_doc = json.loads(zip_checked.stdout)
    except json.JSONDecodeError:
        zip_doc = {}
    check("check validates the ZIP and finds one top-level folder",
          zip_checked.returncode == 0 and zip_doc.get("roots") == ["topic-triage"], fail_detail(zip_checked))

    # 13. deliberate packaging violations ----------------------------------- #
    bad = os.path.join(work, "bad-skill")
    os.makedirs(os.path.join(bad, "sub"), exist_ok=True)
    open(os.path.join(bad, "SKILL.md"), "w").write("# nope\n")
    open(os.path.join(bad, "tools-bad.py"), "w").write("print('x')\n")
    open(os.path.join(bad, "notes.txt"), "w").write("unprefixed\n")
    open(os.path.join(bad, "sub", "asset-deep.json"), "w").write("{}\n")
    bad_run = run(["check", "--path", bad, "--json"])
    try:
        bad_doc = json.loads(bad_run.stdout)
    except json.JSONDecodeError:
        bad_doc = {}
    bad_codes = {item["code"] for item in bad_doc.get("errors", [])}
    check("check rejects SKILL.md, subfolders and unprefixed files",
          bad_run.returncode == 1 and {"SKILL_MD_PRESENT", "SUBFOLDER_FORBIDDEN", "FILENAME_PREFIX"} <= bad_codes,
          str(sorted(bad_codes)))
    check("check rejects a folder without knowledge-readme.md", "KNOWLEDGE_FILE" in bad_codes, str(sorted(bad_codes)))
    check("this Skill Tool folder passes its own check",
          run(["check", "--path", HERE]).returncode == 0)

    # 14. clean-room run: folder beside a root SKILL.md, relative paths only - #
    clean_root = os.path.join(work, "clean-skill")
    os.makedirs(clean_root, exist_ok=True)
    open(os.path.join(clean_root, "SKILL.md"), "w").write(
        "---\nname: clean-test-skill\ndescription: test container\n---\n\nRoot control plane for the test.\n")
    shutil.copytree(HERE, os.path.join(clean_root, "agent-skill-catalog"))
    relative = ["python3", "agent-skill-catalog/tools-skillcatalog.py"]
    clean_caps = subprocess.run(relative + ["capabilities"], cwd=clean_root, capture_output=True, text=True, timeout=180)
    check("clean room: capabilities runs from a root with SKILL.md",
          "agent-skill-catalog" in clean_caps.stdout, clean_caps.stderr[-300:])
    clean_search = subprocess.run(relative + ["search", "--query", "boundary cases", "--limit", "1", "--json"],
                                  cwd=clean_root, capture_output=True, text=True, timeout=180)
    try:
        clean_doc = json.loads(clean_search.stdout)
    except json.JSONDecodeError:
        clean_doc = {}
    check("clean room: search works with relative paths only",
          clean_doc.get("matches") and clean_doc["matches"][0]["name"] == "Boundary Case Generator",
          clean_search.stdout[:200])
    clean_check = subprocess.run(relative + ["check", "--path", "agent-skill-catalog"],
                                 cwd=clean_root, capture_output=True, text=True, timeout=180)
    check("clean room: package check passes via relative path", clean_check.returncode == 0, clean_check.stdout[-300:])

    # 15. ZIP round trip ---------------------------------------------------- #
    with zipfile.ZipFile(scaffold_zip) as archive:
        names = archive.namelist()
        extracted = os.path.join(work, "extracted")
        archive.extractall(extracted)
    check("zip contains the folder directly at its root and no nested archive",
          all(name.startswith("topic-triage/") for name in names)
          and not any(name.lower().endswith((".zip", ".tar", ".gz")) for name in names), str(names[:5]))
    extracted_tool = os.path.join(extracted, "topic-triage", "tools-topic-triage.py")
    extracted_run = subprocess.run([PY, extracted_tool, "verify"], capture_output=True, text=True, timeout=120)
    check("extracted zip re-runs the generated tool", extracted_run.returncode == 0, extracted_run.stdout[-200:])


if __name__ == "__main__":
    sys.exit(main())
