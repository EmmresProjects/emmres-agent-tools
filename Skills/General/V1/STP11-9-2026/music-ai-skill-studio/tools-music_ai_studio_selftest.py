#!/usr/bin/env python3
"""
tools-music_ai_studio_selftest.py  --  runnable end-to-end test suite.

Exercises the real command-line interface of music-ai-skill-studio (not internal
functions only), including valid, invalid, missing and extreme inputs, structured
output, approval gating, WAV QC, archiving and the "clean skill root" packaging
check that a future Copilot skill requires.

Run from any directory:
    python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py
    python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py --keep   # keep temp files
    python3 music-ai-skill-studio/tools-music_ai_studio_selftest.py --json   # machine-readable report

Exit code 0 = every test passed, 1 = at least one failure.
Standard library only. Python 3.9+.
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
TOOL_DIR_NAME = os.path.basename(HERE)
CLI = os.path.join(HERE, "tools-music_ai_studio_cli.py")
CATALOG = os.path.join(HERE, "asset-catalog-music-ai-400.md")
INTAKE = os.path.join(HERE, "asset-intake-example.json")
# Optional: a PDF-derived text export supplied by the tester (see --pdf-text).
PDF_TEXT = os.environ.get("MUSIC_AI_PDF_TEXT", "")

RESULTS = []


def run(cmd, *, cwd, stdin=None, env=None, timeout=180):
    full_env = dict(os.environ)
    full_env.pop("MUSIC_AI_DEBUG", None)
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True,
                          timeout=timeout, env=full_env)


def check(name, condition, detail=""):
    RESULTS.append({"test": name, "ok": bool(condition), "detail": detail})
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {name}"
    if detail and not condition:
        line += f"\n        {detail}"
    print(line)
    return condition


def cli(*args, cwd, **kw):
    """Invoke the CLI with a relative script path from `cwd` when possible."""
    script = os.path.relpath(CLI, cwd) if os.path.isdir(cwd) else CLI
    return run([sys.executable, script, *args], cwd=cwd, **kw)


def load_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="music-ai-skill-studio self test")
    ap.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    ap.add_argument("--json", action="store_true", help="print a JSON report")
    ap.add_argument("--pdf-text", default="", metavar="FILE",
                    help="also parse this PDF-extracted text and compare it with the shipped asset")
    args = ap.parse_args()
    if args.pdf_text:
        os.environ["MUSIC_AI_PDF_TEXT"] = os.path.abspath(args.pdf_text)
    elif os.environ.get("MUSIC_AI_PDF_TEXT"):
        os.environ["MUSIC_AI_PDF_TEXT"] = os.path.abspath(os.environ["MUSIC_AI_PDF_TEXT"])

    tmp = tempfile.mkdtemp(prefix="music-ai-studio-test-")
    print(f"temp workspace: {tmp}\n")
    try:
        _run_suite(tmp)
    finally:
        failures = [r for r in RESULTS if not r["ok"]]
        print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
        if failures:
            print("failures:")
            for f in failures:
                print(f"  - {f['test']}: {f['detail']}")
        if args.json:
            print(json.dumps({"ok": not failures, "total": len(RESULTS),
                              "failed": len(failures), "results": RESULTS}, indent=2))
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"kept: {tmp}")
    return 0 if not [r for r in RESULTS if not r["ok"]] else 1


def _run_suite(tmp: str) -> None:
    # ---------------------------------------------------------------- 1. help
    r = cli("--help", cwd=tmp)
    check("help runs", r.returncode == 0 and "music-ai-skill-studio" in r.stdout,
          r.stderr[-400:])

    r = cli("capabilities", cwd=tmp, env={})
    check("capabilities text", r.returncode == 0 and "plan" in r.stdout and "qc-audio" in r.stdout,
          r.stderr[-400:])
    rj = cli("capabilities", "--json", cwd=tmp)
    cap = load_json(rj.stdout)
    check("capabilities json shape",
          rj.returncode == 0 and cap and cap["ok"] and len(cap["capabilities"]) == 9,
          rj.stdout[:300])

    # --------------------------------------------------------- 2. catalog parse
    r = cli("list", "--source", CATALOG, cwd=tmp)
    check("list parses the 40x400 catalog",
          r.returncode == 0 and "categories: 40" in r.stdout and "capabilities: 400" in r.stdout,
          r.stdout[:400] + r.stderr[-300:])

    rj = cli("list", "--source", CATALOG, "--json", cwd=tmp)
    doc = load_json(rj.stdout) or {}
    names = [c["name"] for c in (doc.get("catalog") or {}).get("categories", [])]
    check("list json reports 40 categories with names",
          len(names) == 40 and names[0] == "Music Intelligence Foundations"
          and names[-1] == "Experimental, Generative, and Future Music", str(names[:3]))

    # Markdown, HTML and inline text must all reach the same result.
    md = os.path.join(tmp, "mini.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# Mixing\n\n1. Skill Name: Bus Gain Reviewer\n"
                 "Description: Reviews gain staging across buses.\n"
                 "2. Skill Name: Reference Match Checker\n")
    html = os.path.join(tmp, "mini.html")
    with open(html, "w", encoding="utf-8") as fh:
        fh.write('<html><body><div class="cat"><h2 style="x">Mixing</h2>'
                 '<ul><li data-id="1"><span>1.</span> <strong>Skill Name:</strong> '
                 'Bus Gain Reviewer</li>'
                 '<li>&#8226; Reference&nbsp;Match Checker</li></ul></div></body></html>')
    r_md = cli("list", "--source", md, "--json", cwd=tmp)
    r_html = cli("list", "--source", html, "--json", cwd=tmp)
    r_txt = cli("list", "--text", "Mixing\n\n- Bus Gain Reviewer\n- Reference Match Checker",
                "--json", cwd=tmp)
    ok_md = load_json(r_md.stdout) or {}
    ok_html = load_json(r_html.stdout) or {}
    ok_txt = load_json(r_txt.stdout) or {}
    got = lambda d: sorted(k["name"] for c in (d.get("catalog") or {}).get("categories", [])
                           for k in c["capabilities"])
    check("markdown equals html equals text parse",
          got(ok_md) == got(ok_html) == got(ok_txt) == ["Bus Gain Reviewer", "Reference Match Checker"],
          f"md={got(ok_md)} html={got(ok_html)} txt={got(ok_txt)}")

    # PDF-extracted text (page headers, hard wraps, "Skill Name:" labels, an index
    # listing and an appendix) must produce the same 400 names as the shipped asset.
    if os.path.exists(PDF_TEXT):
        rp = cli("list", "--source", PDF_TEXT, "--json", cwd=tmp)
        dp = load_json(rp.stdout) or {}
        names_pdf = [k["name"] for c in (dp.get("catalog") or {}).get("categories", [])
                     for k in c["capabilities"]]
        rm = cli("list", "--source", CATALOG, "--json", cwd=tmp)
        dm = load_json(rm.stdout) or {}
        names_md = [k["name"] for c in (dm.get("catalog") or {}).get("categories", [])
                    for k in c["capabilities"]]
        check("PDF-derived text parses to the same 400 capabilities as the markdown asset",
              names_pdf == names_md and len(names_md) == 400,
              f"pdf={len(names_pdf)} md={len(names_md)}")

    # change the input -> the result changes
    r2 = cli("list", "--source", CATALOG, "--category", "20", "--json", cwd=tmp)
    d2 = load_json(r2.stdout) or {}
    cats2 = (d2.get("catalog") or {}).get("categories", [])
    check("category filter changes the result",
          len(cats2) == 1 and cats2[0]["name"].startswith("Mastering"), str(cats2[:1])[:200])

    # ------------------------------------------------------------- 3. errors
    r = cli("list", "--text", "nothing parseable here at all", "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("unparseable source -> structured error, exit 3",
          r.returncode == 3 and err.get("error", {}).get("code") == "no_capabilities_found",
          r.stdout[:300])

    r = cli("list", "--source", "missing-file.md", "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("missing source file -> file_not_found, exit 3",
          r.returncode == 3 and err["error"]["code"] == "file_not_found", r.stdout[:300])

    r = cli("list", "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("no source at all -> missing_input, exit 3",
          r.returncode == 3 and err["error"]["code"] == "missing_input", r.stdout[:300])

    r = cli("plan", "--source", CATALOG, "--category", "999", "--json",
            "--intake-file", INTAKE, cwd=tmp)
    err = load_json(r.stdout) or {}
    check("unknown category -> category_not_found with available list",
          r.returncode == 3 and err["error"]["code"] == "category_not_found"
          and "available" in (err["error"].get("details") or {}), r.stdout[:300])

    r = cli("plan", "--source", CATALOG, "--category", "19", "--json",
            "--intake", '{"goal": "", "inputs": ["x"]}', cwd=tmp)
    err = load_json(r.stdout) or {}
    check("empty goal rejected",
          r.returncode == 3 and err["error"]["code"] == "validation_error", r.stdout[:300])

    r = cli("plan", "--source", CATALOG, "--category", "19", "--json",
            "--intake", '{"goal": "mix it"}', cwd=tmp)
    err = load_json(r.stdout) or {}
    check("missing inputs rejected",
          r.returncode == 3 and "inputs" in err["error"]["message"], r.stdout[:300])

    r = cli("plan", "--source", CATALOG, "--category", "19", "--json",
            "--intake", '{"goal": "mix it", "inputs": ["a"], "tempo_bpm": 900}', cwd=tmp)
    err = load_json(r.stdout) or {}
    check("out-of-range tempo rejected",
          r.returncode == 3 and err["error"]["code"] == "validation_error", r.stdout[:300])

    r = cli("plan", "--source", CATALOG, "--category", "19", "--json",
            "--intake", "{not json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("malformed inline JSON rejected",
          r.returncode == 3 and err["error"]["code"] == "invalid_json", r.stdout[:300])

    # limit guard: shrink the limit through a workflow file
    small_wf = os.path.join(tmp, "wf-small.json")
    with open(small_wf, "w", encoding="utf-8") as fh:
        json.dump({"limits": {"max_input_bytes": 2048}}, fh)
    r = cli("list", "--source", CATALOG, "--workflow", small_wf, "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("byte limit enforced -> exit 4",
          r.returncode == 4 and err["error"]["code"] == "limit_exceeded", r.stdout[:300])

    bad_wf = os.path.join(tmp, "wf-bad.json")
    with open(bad_wf, "w", encoding="utf-8") as fh:
        json.dump({"retries": 99}, fh)
    r = cli("list", "--source", CATALOG, "--workflow", bad_wf, "--json", cwd=tmp)
    check("invalid workflow config rejected", r.returncode == 3, r.stdout[:300])

    # -------------------------------------------------------------- 4. planning
    out1 = os.path.join(tmp, "case-mixing")
    r = cli("plan", "--source", CATALOG, "--category", "19",
            "--intake-file", INTAKE, "--out", out1,
            "--title", "Salt Harbor Mix", "--timestamp", "2026-11-09T00:00:00Z",
            "--case-id", "salt-harbor-mix", "--approve", "APPROVE-RELEASE", "--json", cwd=tmp)
    plan_doc = load_json(r.stdout) or {}
    files = sorted(os.listdir(out1)) if os.path.isdir(out1) else []
    check("plan writes brief, plan, checklist, handoff and zip",
          r.returncode == 0 and len(files) == 5
          and any(f.endswith("-brief.md") for f in files)
          and any(f.endswith("-checklist.csv") for f in files)
          and any(f.endswith("-handoff.csv") for f in files)
          and any(f.endswith(".zip") for f in files), f"{files} {r.stderr[-300:]}")
    check("plan covers all 10 mixing capabilities",
          plan_doc.get("counts", {}).get("capabilities") == 10,
          str(plan_doc.get("counts")))

    brief = os.path.join(out1, "salt-harbor-mix-brief.md")
    brief_text = open(brief, encoding="utf-8").read() if os.path.exists(brief) else ""
    check("brief renders intake values",
          "Salt Harbor Mix" in brief_text and "104" in brief_text
          and "D major" in brief_text and "## 7. Decision log" in brief_text,
          brief_text[:300])
    check("brief carries approval status",
          "APPROVE-RELEASE" in brief_text and "approved" in brief_text, brief_text[-500:])

    csv_path = os.path.join(out1, "salt-harbor-mix-checklist.csv")
    csv_rows = open(csv_path, encoding="utf-8").read().splitlines() if os.path.exists(csv_path) else []
    check("checklist csv has rows and fixed columns",
          csv_rows and csv_rows[0] == "block,item,owner,status,evidence,notes"
          and len(csv_rows) > 12, f"{len(csv_rows)} rows")

    zpath = os.path.join(out1, "salt-harbor-mix-delivery.zip")
    ok_zip = False
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        ok_zip = (len(names) == 4 and all(n.startswith("salt-harbor-mix/") for n in names)
                  and zf.testzip() is None)
    check("delivery zip holds the four case files under one slug folder", ok_zip, str(names))

    # same input + fixed timestamp -> identical bytes (determinism)
    out1b = os.path.join(tmp, "case-mixing-repeat")
    cli("plan", "--source", CATALOG, "--category", "19", "--intake-file", INTAKE,
        "--out", out1b, "--title", "Salt Harbor Mix", "--timestamp", "2026-11-09T00:00:00Z",
        "--case-id", "salt-harbor-mix", "--approve", "APPROVE-RELEASE", "--quiet", cwd=tmp)
    same = (open(zpath, "rb").read() == open(os.path.join(out1b, "salt-harbor-mix-delivery.zip"), "rb").read())
    check("identical inputs produce an identical delivery zip (deterministic)", same, "")

    # category plan vs capability plan -> different coverage
    out2 = os.path.join(tmp, "case-capability")
    r = cli("plan", "--source", CATALOG, "--capability", "Loudness Measurement Analyst",
            "--intake-file", INTAKE, "--out", out2, "--json",
            "--approve", "APPROVE-RELEASE", cwd=tmp)
    d = load_json(r.stdout) or {}

    check("single-capability case covers exactly one capability",
          d.get("counts", {}).get("capabilities") == 1
          and (d.get("plan", {}).get("capability") or "") == "Loudness Measurement Analyst"
          and d["plan"]["profiles"][0]["family"] == "analysis",
          json.dumps(d.get("counts")) + (r.stderr[-200:] if r.returncode else ""))

    out2b = os.path.join(tmp, "case-two-caps")
    r = cli("plan", "--source", CATALOG, "--capability", "Mix Bus Balance Reviewer",
            "--capability", "Loudness Measurement Analyst", "--intake-file", INTAKE,
            "--out", out2b, "--json", "--approve", "APPROVE-RELEASE", cwd=tmp)
    d = load_json(r.stdout) or {}

    check("several capabilities unify into one case with mixed families",
          d.get("counts", {}).get("capabilities") == 2
          and {p["family"] for p in d["plan"]["profiles"]} == {"audio_processing", "analysis"},
          str(d.get("counts")) + str(d.get("coverage")))

    # ad-hoc case, no catalog needed
    out3 = os.path.join(tmp, "case-adhoc")
    r = cli("plan", "--title", "Quarterly Release Sequencing", "--json",
            "--intake", '{"goal":"Sequence the 10-track album","inputs":["10 approved masters"]}',
            "--out", out3, "--approve", "APPROVE-RELEASE", "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    check("ad-hoc case works without a catalog and uses the generic family",
          r.returncode == 0 and d.get("plan", {}).get("kind") == "ad_hoc"
          and d["plan"]["profiles"][0]["family"] == "marketing", r.stdout[:300] + r.stderr[-300:])
    check("ad-hoc case intent shows up in the plan title",
          (d.get("plan", {}).get("title") or "") == "Quarterly Release Sequencing", str(d.get("plan", {}).get("title")))

    # ------------------------------------------------- 5. blocking + approvals
    out4 = os.path.join(tmp, "case-blocked")
    r = cli("plan", "--category", "20", "--source", CATALOG, "--out", out4, "--json",
            "--intake", '{"goal":"Master the single","inputs":["mix.wav"],'
                        '"rights_status":"unknown","voice_consent":"missing"}', cwd=tmp)
    d = load_json(r.stdout) or {}
    flags = d.get("blocking_flags") or []
    check("unknown rights and missing consent raise blocking flags",
          r.returncode == 0 and len(flags) == 2, str(flags))

    r = cli("plan", "--category", "19", "--source", CATALOG, "--json", "--dry-run",
            "--intake", '{"goal":"publish","inputs":["master.wav"],"actions":["release_publish"]}',
            cwd=tmp)
    err = load_json(r.stdout) or {}
    check("publishing without approval token -> exit 5",
          r.returncode == 5 and err["error"]["code"] == "approval_required"
          and err["error"]["details"]["tokens_expected"] == ["APPROVE-RELEASE"], r.stdout[:400])

    r = cli("verify-intake", "--json", "--approve", "APPROVE-RELEASE",
            "--intake", '{"goal":"publish","inputs":["master.wav"],"actions":["release_publish"]}',
            cwd=tmp)
    d = load_json(r.stdout) or {}
    check("verify-intake accepts the approval token",
          r.returncode == 0 and d["approvals"]["actions"][0]["approved"] is True, r.stdout[:300])

    r = cli("verify-intake", "--json",
            "--intake", '{"goal":"pay","inputs":["x"],"actions":["payment"]}', cwd=tmp)
    check("payment action is gated too", r.returncode == 5, r.stdout[:300])

    r = cli("plan", "--category", "19", "--source", CATALOG, "--json", "--dry-run",
            "--intake-file", INTAKE, "--approve", "APPROVE-RELEASE", cwd=tmp)
    check("dry-run writes nothing and reports the plan",
          r.returncode == 0 and (load_json(r.stdout) or {}).get("dry_run") is True
          and not os.path.exists(os.path.join(tmp, "out", "salt-harbor-mix-line")), r.stdout[:300])

    # --------------------------------------------------------------- 6. batch
    jobs = os.path.join(HERE, "asset-batch-jobs-example.json")
    with open(jobs, encoding="utf-8") as fh:
        jobs_doc = json.load(fh)
    rel_jobs = os.path.join(tmp, "jobs.json")
    with open(rel_jobs, "w", encoding="utf-8") as fh:
        json.dump(jobs_doc, fh)
    bout = os.path.join(tmp, "batch")
    r = cli("batch", "--jobs", rel_jobs, "--source-root", HERE, "--workers", "2",
            "--out", bout, "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    check("batch renders the valid jobs and isolates the invalid ones",
          d.get("jobs_total") == 5 and d.get("jobs_ok") == 2 and d.get("jobs_failed") == 3,
          str({k: d.get(k) for k in ("jobs_total", "jobs_ok", "jobs_failed")}))
    by_index = {x["index"]: x for x in d.get("results", [])}
    check("a job missing its goal fails validation without crashing the batch",
          by_index.get(4, {}).get("ok") is False
          and by_index[4]["error"]["code"] == "validation_error", str(by_index.get(4))[:300])
    check("batch exits 3 when any job failed", r.returncode == 3, str(r.returncode))
    check("a job with malformed intake JSON fails without crashing the batch",
          by_index.get(5, {}).get("ok") is False
          and by_index[5]["error"]["code"] in ("validation_error", "intake_not_object"),
          str(by_index.get(5))[:300])
    check("the unapproved publish job is gated inside the batch",
          by_index.get(3, {}).get("error", {}).get("code") == "approval_required",
          str(by_index.get(3))[:300])
    check("batch report file exists and parses",
          os.path.exists(os.path.join(bout, "batch-report.json")) and
          bool(load_json(open(os.path.join(bout, "batch-report.json"), encoding="utf-8").read())),
          "")

    # ------------------------------------------------------------ 7. WAV QC
    sys.path.insert(0, HERE)
    import importlib
    core = importlib.import_module("tools-music_ai_studio")
    good = os.path.join(tmp, "good.wav")
    core.make_test_wav(good, seconds=2.0, freq=440.0, amplitude=0.5)
    clipped = os.path.join(tmp, "clipped.wav")
    core.make_test_wav(clipped, seconds=0.5, freq=220.0, amplitude=1.0)
    silent = os.path.join(tmp, "silent.wav")
    core.make_test_wav(silent, seconds=2.0, amplitude=0.0)

    r = cli("qc-audio", "--audio", good, "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    m = (d.get("reports") or [{}])[0].get("measurements", {})
    check("qc-audio passes a clean 2s 48k stereo file",
          r.returncode == 0 and d.get("files_failed") == 0
          and abs(m.get("duration_seconds", 0) - 2.0) < 0.01
          and m.get("sample_rate") == 48000 and m.get("channels") == 2,
          json.dumps(d)[:400])

    r = cli("qc-audio", "--audio", silent, "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    issues = (d.get("reports") or [{}])[0].get("verdict", {}).get("issues", [])
    check("qc-audio fails a silent file",
          r.returncode == 3 and any("silent" in i for i in issues), str(issues))

    r = cli("qc-audio", "--audio", clipped, "--json",
            "--qc-profile", os.path.join(HERE, "asset-qc-profiles.json"),
            "--qc-profile-name", "strict-master", cwd=tmp)
    d = load_json(r.stdout) or {}
    issues = (d.get("reports") or [{}])[0].get("verdict", {}).get("issues", [])
    check("qc-audio with the strict-master profile rejects a 0.5s hot file",
          r.returncode == 3 and any("minimum_duration" in i for i in issues)
          and any("peak" in i or "clip" in i for i in issues), str(issues))

    r = cli("qc-audio", "--audio", clipped, "--json",
            "--qc-profile", os.path.join(HERE, "asset-qc-profiles.json"),
            "--qc-profile-name", "not-a-profile", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("unknown QC profile name -> structured config error",
          r.returncode == 3 and err["error"]["code"] == "invalid_config", r.stdout[:300])

    bad = os.path.join(tmp, "fake.mp3")
    with open(bad, "wb") as fh:
        fh.write(b"ID3\x04\x00\x00\x00\x00\x00\x00")
    r = cli("qc-audio", "--audio", bad, "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("non-WAV audio -> not_supported, exit 6",
          r.returncode == 6 and err["error"]["code"] == "unsupported_audio_format", r.stdout[:300])

    r = cli("qc-audio", "--audio", os.path.join(tmp, "nope.wav"), "--json", cwd=tmp)
    check("missing audio file -> exit 3", r.returncode == 3, r.stdout[:200])

    # damaged file: header says 10s, file is 200 bytes
    trunc = os.path.join(tmp, "truncated.wav")
    with open(good, "rb") as fh:
        data = fh.read()
    with open(trunc, "wb") as fh:
        fh.write(data[:200])
    r = cli("qc-audio", "--audio", trunc, "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    issues = (d.get("reports") or [{}])[0].get("verdict", {}).get("issues", [])
    check("truncated wav is reported as an integrity failure", r.returncode == 3 and issues,
          str(issues)[:300])

    # -------------------------------------------------------- 8. trim silence
    pad = os.path.join(tmp, "padded.wav")
    core.make_test_wav(pad, seconds=2.0, freq=330.0, amplitude=0.4, leading_s=0.5)
    trimmed = os.path.join(tmp, "trimmed.wav")
    r = cli("trim-silence", "--audio", pad, "--out-file", trimmed,
            "--threshold-dbfs", "-50", "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    ok = os.path.exists(trimmed)
    after = core.wav_inspect(trimmed)["measurements"]["duration_seconds"] if ok else None
    check("trim-silence shortens the file and keeps it valid",
          r.returncode == 0 and ok and after is not None and after < 1.7,
          f"{d} after={after}")

    r = cli("trim-silence", "--audio", pad, "--out-file", pad, "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("trim-silence refuses to overwrite its input",
          r.returncode == 3 and err["error"]["code"] == "unsafe_overwrite", r.stdout[:300])

    # ------------------------------------------------------- 9. pack / unpack
    pk = os.path.join(tmp, "pack-src")
    os.makedirs(pk, exist_ok=True)
    shutil.copy(good, os.path.join(pk, "master.wav"))
    with open(os.path.join(pk, "notes.md"), "w", encoding="utf-8") as fh:
        fh.write("# delivery notes\n")
    archive = os.path.join(tmp, "delivery.zip")
    manifest = os.path.join(tmp, "manifest.csv")
    r = cli("pack", "--input", pk, "--archive", archive, "--name", "salt-harbor",
            "--out-manifest", manifest, "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    check("pack creates a validated zip with a manifest",
          r.returncode == 0 and d.get("entries") == 2 and os.path.exists(manifest)
          and d["names"] == ["salt-harbor/master.wav", "salt-harbor/notes.md"], str(d)[:400])

    dest = os.path.join(tmp, "unpacked")
    r = cli("unpack", "--archive", archive, "--dest", dest, "--json", cwd=tmp)
    d = load_json(r.stdout) or {}
    check("unpack restores both files",
          r.returncode == 0 and d.get("count") == 2
          and os.path.exists(os.path.join(dest, "salt-harbor", "master.wav")), str(d)[:300])

    # nested folder must be refused by pack
    nested = os.path.join(tmp, "pack-nested")
    os.makedirs(os.path.join(nested, "sub"), exist_ok=True)
    with open(os.path.join(nested, "sub", "x.txt"), "w") as fh:
        fh.write("x")
    r = cli("pack", "--input", nested, "--archive", os.path.join(tmp, "nested.zip"), "--json", cwd=tmp)
    err = load_json(r.stdout) or {}
    check("pack refuses nested folders",
          r.returncode == 3 and err["error"]["code"] == "bad_structure", r.stdout[:300])

    # traversal and prefix guards, exercised through the real archive writer
    for bad_name, expect in (("../evil/brief.md", "unsafe_path"),
                             ("pkg/SKILL.md", "forbidden_file"),
                             ("pkg/readme.md", "bad_prefix"),
                             ("pkg/nested/brief.md", "bad_structure"),
                             ("pkg/inner.zip", "nested_archive")):
        try:
            core.write_archive(os.path.join(tmp, "guard.zip"), [(bad_name, b"x")])
            code = "accepted"
        except core.StudioError as exc:
            code = exc.code
        check(f"archive guard rejects {bad_name}", code == expect, f"got {code}")

    # a malicious zip must not escape the destination
    evil = os.path.join(tmp, "evil.zip")
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.txt", "boom")
    r = cli("unpack", "--archive", evil, "--dest", os.path.join(tmp, "evil-out"), "--json", cwd=tmp)
    check("unpack rejects a traversal archive",
          r.returncode == 3 and not os.path.exists(os.path.join(tmp, "escape.txt")),
          r.stdout[:300])

    # ----------------------------------- 10. clean skill root + relative paths
    clean = os.path.join(tmp, "clean-skill-root")
    os.makedirs(clean, exist_ok=True)
    with open(os.path.join(clean, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: test-parent-skill\ndescription: test fixture\n---\n")
    shutil.copytree(HERE, os.path.join(clean, TOOL_DIR_NAME),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    r = run([sys.executable, f"{TOOL_DIR_NAME}/tools-music_ai_studio_cli.py",
             "list", "--source", f"{TOOL_DIR_NAME}/asset-catalog-music-ai-400.md",
             "--json"], cwd=clean)
    d = load_json(r.stdout) or {}
    check("runs beside a parent SKILL.md using only relative paths",
          r.returncode == 0 and d.get("stats", {}).get("capabilities") == 400, r.stderr[-300:])

    r = run([sys.executable, f"{TOOL_DIR_NAME}/tools-music_ai_studio_cli.py", "plan",
             "--source", f"{TOOL_DIR_NAME}/asset-catalog-music-ai-400.md", "--category", "27",
             "--intake-file", f"{TOOL_DIR_NAME}/asset-intake-example.json",
             "--out", "out/rights-case", "--json",
             "--approve", "APPROVE-RELEASE"], cwd=clean)
    d = load_json(r.stdout) or {}
    check("category 27 (AI safety/ethics) case renders from the relative path",
          r.returncode == 0 and d.get("counts", {}).get("capabilities") == 10,
          r.stderr[-400:] + r.stdout[:200])
    check("relative-path case wrote its files inside the clean root",
          os.path.exists(os.path.join(clean, "out", "rights-case")), "")

    # structural rules for the package itself
    entries = sorted(os.listdir(HERE))
    bad_dirs = [e for e in entries if os.path.isdir(os.path.join(HERE, e))
                and e not in ("__pycache__",)]
    bad_names = [e for e in entries if e != "__pycache__"
                 and not (e.startswith("tools-") or e.startswith("knowledge-")
                          or e.startswith("asset-"))]
    knowledge = [e for e in entries if e.startswith("knowledge-")]
    check("no subfolders and no unprefixed files in the Skill Tool folder",
          not bad_dirs and not bad_names, f"dirs={bad_dirs} bad={bad_names}")
    check("exactly one knowledge file", knowledge == ["knowledge-readme.md"], str(knowledge))
    check("no SKILL.md inside the Skill Tool folder",
          "SKILL.md" not in entries and "skill.md" not in [e.lower() for e in entries], str(entries))
    check("primary entry point present",
          "tools-music_ai_studio_cli.py" in entries, str(entries))


if __name__ == "__main__":
    sys.exit(main())
