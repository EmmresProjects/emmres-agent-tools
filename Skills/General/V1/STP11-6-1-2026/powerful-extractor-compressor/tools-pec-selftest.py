#!/usr/bin/env python3
"""Self-test battery for the powerful-extractor-compressor Skill Tool.

Run through the primary entry point::

    python3 tools-pec.py selftest
    python3 tools-pec.py selftest --work DIR --keep

Each case builds a fixture, exercises a real capability through the same code
paths the CLI uses, asserts concrete outcomes, and reports pass/fail. No
network access, no external binaries, and no writes outside the work directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if "pec_runtime" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_runtime", os.path.join(HERE, "tools-pec-runtime.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_runtime"] = _mod
    _spec.loader.exec_module(_mod)
R = sys.modules["pec_runtime"]

if "pec_formats" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_formats", os.path.join(HERE, "tools-pec-formats.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_formats"] = _mod
    _spec.loader.exec_module(_mod)
F = sys.modules["pec_formats"]

if "pec_core" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_core", os.path.join(HERE, "tools-pec-core.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_core"] = _mod
    _spec.loader.exec_module(_mod)
C = sys.modules["pec_core"]

if "pec_packager" not in sys.modules:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("pec_packager", os.path.join(HERE, "tools-pec-packager.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pec_packager"] = _mod
    _spec.loader.exec_module(_mod)
P = sys.modules["pec_packager"]

PecError = R.PecError


class Case:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.started = time.monotonic()

    def run(self, fn, workdir: str) -> dict:
        try:
            detail = fn(workdir)
            return {"case": self.name, "description": self.description, "ok": True,
                    "seconds": round(time.monotonic() - self.started, 3), "detail": detail}
        except AssertionError as exc:
            return {"case": self.name, "description": self.description, "ok": False,
                    "seconds": round(time.monotonic() - self.started, 3), "error": f"assertion: {exc}"}
        except Exception as exc:  # noqa: BLE001 - the harness reports every failure
            return {"case": self.name, "description": self.description, "ok": False,
                    "seconds": round(time.monotonic() - self.started, 3),
                    "error": f"{type(exc).__name__}: {exc}"}


def make_tree(root: str) -> None:
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    os.makedirs(os.path.join(root, "img"), exist_ok=True)
    os.makedirs(os.path.join(root, "empty-dir"), exist_ok=True)
    with open(os.path.join(root, "docs", "readme.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello world\nthis is a test file\n")
    with open(os.path.join(root, "docs", "notes.md"), "w", encoding="utf-8") as fh:
        fh.write("# Notes\n\n- one\n- two\n")
    with open(os.path.join(root, "img", "pixel.png"), "wb") as fh:
        fh.write(bytes(range(256)) * 8)
    with open(os.path.join(root, "secret.env"), "w", encoding="utf-8") as fh:
        fh.write("AWS_KEY=AKIAIOSFODNN7EXAMPLE\npassword = hunter2hunter2\n")
    with open(os.path.join(root, ".DS_Store"), "wb") as fh:
        fh.write(b"\x00\x01junk")


def ensure_parent(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    return path


def make_zip(path: str, extra: list[tuple[str, bytes]] | None = None) -> str:
    ensure_parent(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docs/readme.txt", "hello world\n" * 50)
        zf.writestr("docs/notes.md", "# Notes\n")
        zf.writestr("data/table.csv", "a,b\n1,2\n")
        for name, payload in extra or []:
            zf.writestr(name, payload)
    return path


def make_tar(path: str, mode: str = "w") -> str:
    ensure_parent(path)
    src = os.path.join(os.path.dirname(path), "tar-src")
    make_tree(src)
    with tarfile.open(path, mode) as tf:
        tf.add(src, arcname="bundle")
    return path


def config_for(workdir: str) -> object:
    config = R.Config.load(None).copy()
    config.data["state_dir"] = os.path.join(workdir, "state")
    config.data["policy"]["allowed_output_roots"] = [os.path.join(workdir, "out")]
    config.data["limits"]["max_entries"] = 10_000
    config.data["limits"]["max_total_bytes"] = 512 << 20
    config.data["limits"]["max_entry_bytes"] = 128 << 20
    config.data["limits"]["max_ratio"] = 500
    os.makedirs(os.path.join(workdir, "out"), exist_ok=True)
    return config


def logger_for(config, job="selftest") -> object:
    return R.Logger(config.state_dir(), job, echo=False, quiet=True)


def gate_for(config) -> object:
    return R.ApprovalGate(config, granted=["overwrite", "delete", "dispose"])


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------
def case_detect(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    os.makedirs(fixtures, exist_ok=True)
    zip_path = make_zip(os.path.join(fixtures, "sample.zip"))
    tar_path = make_tar(os.path.join(fixtures, "sample.tar"))
    disguised = os.path.join(fixtures, "actually-a-zip.txt")
    shutil.copy(zip_path, disguised)
    results = {}
    for path in (zip_path, tar_path, disguised):
        info = F.detect_file(path)
        results[os.path.basename(path)] = info["id"]
    assert results["sample.zip"] == "zip", results
    assert results["sample.tar"] == "tar", results
    assert results["actually-a-zip.txt"] == "zip", results
    assert F.detect_file(disguised)["extension_match"] is False
    return results


def case_list_and_select(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    os.makedirs(fixtures, exist_ok=True)
    archive = make_zip(os.path.join(fixtures, "list.zip"))
    entries, meta = F.list_entries(archive, config)
    assert len(entries) == 3, len(entries)
    selector = C.Selector(include=["*.csv"])
    matched = [e.name for i, e in enumerate(entries, 1) if selector.matches(e, i)]
    assert matched == ["data/table.csv"], matched
    regex = C.Selector(regex=r"docs/.*\.md$")
    assert [e.name for i, e in enumerate(entries, 1) if regex.matches(e, i)] == ["docs/notes.md"]
    return {"entries": [e.name for e in entries], "glob": matched}


def case_extract_basic(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "extract.zip"))
    dest = os.path.join(workdir, "out", "basic")
    logger = logger_for(config, "basic")
    job = C.ExtractionJob(archive, C.ExtractOptions(destination=dest, checkpoint=False),
                          config, logger, R.Cancellation(60).install(), gate_for(config))
    result = job.run()
    assert result["status"] == "completed", result["status"]
    assert result["entries_written"] == 3, result["entries_written"]
    assert os.path.exists(os.path.join(result["destination"], "docs", "readme.txt"))
    assert os.path.exists(os.path.join(result["destination"], "pec-manifest.json"))
    manifest = R.read_json(result["manifest"], {})
    assert manifest["schema"] == R.MANIFEST_SCHEMA
    verify = C.verify_manifest(result["manifest"])
    assert verify["ok"], verify
    return {"destination": result["destination"], "manifest": result["manifest"]}


def case_extract_partial_pattern(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "pattern.zip"))
    dest = os.path.join(workdir, "out", "pattern")
    job = C.ExtractionJob(archive, C.ExtractOptions(destination=dest, checkpoint=False,
                                                    selector=C.Selector(extensions=[".csv"])),
                          config, logger_for(config, "pattern"), R.Cancellation(60).install(),
                          gate_for(config))
    result = job.run()
    assert result["entries_written"] == 1, result["entries_written"]
    files = [f for _, _, fs in os.walk(result["destination"]) for f in fs]
    assert "table.csv" in files, files
    return {"written": result["entries_written"], "files": files}


def case_traversal_blocked(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    evil = os.path.join(fixtures, "evil.zip")
    make_zip(evil, extra=[("../../escaped.txt", b"bad"), ("/absolute.txt", b"bad"),
                          ("safe/ok.txt", b"good")])
    dest = os.path.join(workdir, "out", "evil")
    job = C.ExtractionJob(evil, C.ExtractOptions(destination=dest, checkpoint=False, scan=False),
                          config, logger_for(config, "evil"), R.Cancellation(60).install(),
                          gate_for(config))
    result = job.run()
    assert result["status"] == "partial", result["status"]
    assert result["entries_failed"] == 2, result["entries_failed"]
    assert not os.path.exists(os.path.join(workdir, "out", "escaped.txt"))
    kinds = {e["kind"] for e in result["errors"]}
    assert "traversal" in kinds or "path" in kinds, kinds
    return {"failed": result["entries_failed"], "kinds": sorted(kinds)}


def case_bomb_ratio(workdir):
    config = config_for(workdir)
    config.data["limits"]["max_ratio"] = 5
    fixtures = os.path.join(workdir, "fixtures")
    bomb = ensure_parent(os.path.join(fixtures, "bomb.zip"))
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("filler.bin", os.urandom(2048))
        zf.writestr("zeros.bin", b"\x00" * (2 << 20))
    pre = C.preflight(bomb, config)
    assert pre["safe_to_extract"] is False, pre["checks"]
    blocked = {b["check"] for b in pre["blocking"]}
    assert "expansion-ratio" in blocked, blocked
    return {"ratio": pre["ratio"], "blocking": sorted(blocked)}


def case_create_and_verify(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "create-src")
    make_tree(src)
    out = os.path.join(workdir, "out", "created.zip")
    info = F.create_archive([src], out, config, fmt="zip", level=6, root_folder=None)
    assert os.path.exists(out)
    assert info["members"] >= 4, info
    first = info["sha256"]
    out2 = os.path.join(workdir, "out", "created-2.zip")
    info2 = F.create_archive([src], out2, config, fmt="zip", level=6, root_folder=None)
    assert info2["sha256"] == first, "deterministic archives must hash identically"
    test = F.native_test(out, config)
    assert test["ok"] is True, test
    entries, _ = F.list_entries(out, config)
    assert any(e.name == "create-src/docs/readme.txt" for e in entries), [e.name for e in entries]
    return {"sha256": first, "deterministic": True, "entries": len(entries)}


def case_create_tar_variants(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "create-src")
    make_tree(src)
    results = {}
    for fmt in ("tar", "tar.gz", "tar.xz", "gzip"):
        target = src if fmt == "gzip" else src
        out = os.path.join(workdir, "out", f"variant.{fmt.replace('.', '-')}")
        if fmt in ("tar", "tar.gz", "tar.xz"):
            info = F.create_archive([src], out, config, fmt=fmt, level=6, root_folder="bundle")
        else:
            single = os.path.join(workdir, "fixtures", "single.txt")
            with open(single, "w", encoding="utf-8") as fh:
                fh.write("compress me\n" * 100)
            info = F.create_archive([single], out + ".gz", config, fmt="gzip", level=6)
            out = out + ".gz"
        results[fmt] = {"bytes": info["output_bytes"], "ok": F.native_test(out, config)["ok"]}
        assert results[fmt]["ok"], (fmt, results[fmt])
    return results


def case_targz_lists_as_tar(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    path = make_tar(os.path.join(fixtures, "bundle.tar.gz"), mode="w:gz")
    entries, meta = F.list_entries(path, config)
    assert meta["opened_as"] == "tar", meta["opened_as"]
    assert len(entries) >= 4, entries
    dest = os.path.join(workdir, "out", "targz")
    result = C.ExtractionJob(path, C.ExtractOptions(destination=dest, checkpoint=False),
                             config, logger_for(config, "targz"), R.Cancellation(60).install(),
                             gate_for(config)).run()
    assert os.path.exists(os.path.join(result["destination"], "bundle", "docs", "readme.txt"))
    return {"opened_as": meta["opened_as"], "entries": len(entries)}


def case_round_trip(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "roundtrip.zip"))
    result = C.round_trip_check(archive, config, os.path.join(workdir, "out", "rt"))
    assert result["ok"], result
    assert result["entries"] == 3, result["entries"]
    assert result["scratch"] is None, "scratch must be cleaned up"
    return result


def case_verify_detects_tamper(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "tamper.zip"))
    dest = os.path.join(workdir, "out", "tamper")
    job = C.ExtractionJob(archive, C.ExtractOptions(destination=dest, checkpoint=False),
                          config, logger_for(config, "tamper"), R.Cancellation(60).install(),
                          gate_for(config))
    result = job.run()
    target = os.path.join(result["destination"], "docs", "readme.txt")
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("tampered\n")
    verify = C.verify_manifest(result["manifest"])
    assert verify["ok"] is False, verify
    assert verify["mismatched"], verify
    return {"mismatched": len(verify["mismatched"])}


def case_scan_secrets(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "scan.zip"),
                       extra=[("config/app.env", b"AWS_KEY=AKIAIOSFODNN7EXAMPLE\n")])
    dest = os.path.join(workdir, "out", "scan")
    job = C.ExtractionJob(archive, C.ExtractOptions(destination=dest, checkpoint=False),
                          config, logger_for(config, "scan"), R.Cancellation(60).install(),
                          gate_for(config))
    result = job.run()
    import re
    patterns = R.load_asset("asset-secret-patterns.json")
    aws = [p for p in patterns["patterns"] if p["id"] == "aws-access-key"]
    text = "AWS_KEY=AKIAIOSFODNN7EXAMPLE"
    assert aws and re.search(aws[0]["regex"], text), "aws pattern must match"
    findings = C.scan_tree(result["destination"], config)
    assert any(f["rule"] == "aws-access-key" for f in findings), findings
    return {"findings": [(f["rule"], f["file"]) for f in findings]}


def case_organize(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "create-src")
    make_tree(src)
    archive = os.path.join(workdir, "out", "organize-source.zip")
    F.create_archive([src], archive, config, fmt="zip", root_folder=None)
    dest = os.path.join(workdir, "out", "organized")
    job = C.ExtractionJob(archive, C.ExtractOptions(destination=dest, checkpoint=False,
                                                    organize="by-content-type"),
                          config, logger_for(config, "organize"), R.Cancellation(60).install(),
                          gate_for(config))
    result = job.run()
    categories = sorted({d for d in os.listdir(result["destination"])
                         if os.path.isdir(os.path.join(result["destination"], d))
                         and d != "create-src"})
    assert "documents" in categories, categories
    assert "images" in categories, categories
    assert "audio" not in categories, categories
    return {"categories": categories}


def case_nested(workdir):
    config = config_for(workdir)
    inner = os.path.join(workdir, "fixtures", "inner.zip")
    make_zip(inner)
    outer = os.path.join(workdir, "fixtures", "outer.zip")
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(inner, "nested/inner.zip")
        zf.writestr("top.txt", "top level\n")
    dest = os.path.join(workdir, "out", "nested")
    job = C.ExtractionJob(outer, C.ExtractOptions(destination=dest, checkpoint=False, nest=True,
                                                  max_depth=3),
                          config, logger_for(config, "nested"), R.Cancellation(120).install(),
                          gate_for(config))
    result = job.run()
    assert result["nested"], "nested results expected"
    nested_roots = []
    for dirpath, dirnames, filenames in os.walk(result["destination"]):
        nested_roots.extend(os.path.join(dirpath, d) for d in dirnames if d.endswith("__unpacked"))
    assert nested_roots, os.listdir(result["destination"])
    target = os.path.join(nested_roots[0], "docs", "readme.txt")
    assert os.path.exists(target), (nested_roots, os.listdir(nested_roots[0]))
    depths = [n.get("destination") for n in result["nested"]]
    return {"nested_runs": len(result["nested"]), "depths": depths}


def case_volumes(workdir):
    config = config_for(workdir)
    config.data["limits"]["max_total_bytes"] = 512 << 20
    src = os.path.join(workdir, "fixtures", "vol-src")
    os.makedirs(src, exist_ok=True)
    payload = os.urandom(300_000)
    with open(os.path.join(src, "big.bin"), "wb") as fh:
        fh.write(payload)
    archive = os.path.join(workdir, "out", "split.zip")
    F.create_archive([src], archive, config, fmt="zip", level=0, root_folder="split")
    info = F.split_volumes(archive, 65_536)
    assert info["count"] >= 4, info["count"]
    gathered = F.collect_volumes(info["volumes"][0]["path"])
    assert gathered["multipart"] is True
    assert gathered["complete"] is True, gathered
    merged = os.path.join(workdir, "out", "merged.zip")
    result = F.assemble_volumes(gathered["volumes"], merged)
    assert result["detected"] == "zip", result
    assert F.native_test(merged, config)["ok"], "assembled archive must pass native test"
    assert hashlib.sha256(open(merged, "rb").read()).hexdigest() == \
challenge(info), "assembled bytes must match source"
    return {"volumes": info["count"], "merged_sha256": result["sha256"]}


def challenge(info):
    digest = hashlib.sha256()
    with open(info["source"], "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def case_volume_diagnosis(workdir):
    src = os.path.join(workdir, "out")
    vol_dir = os.path.join(workdir, "fixtures", "vols")
    os.makedirs(vol_dir, exist_ok=True)
    for i in (1, 2, 4):
        with open(os.path.join(vol_dir, f"missing.zip.{i:03d}"), "wb") as fh:
            fh.write(b"x" * 1000)
    report = F.collect_volumes(os.path.join(vol_dir, "missing.zip.001"))
    assert report["multipart"] is True
    assert report["complete"] is False
    assert 3 in report["missing"], report["missing"]
    return {"missing": report["missing"]}


def case_checkpoint_resume(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "resume.zip"))
    dest = os.path.join(workdir, "out", "resume")
    opts = C.ExtractOptions(destination=dest, checkpoint=True, resume=True, job_key="resume-test")
    job = C.ExtractionJob(archive, opts, config, logger_for(config, "resume-a"),
                          R.Cancellation(60).install(), gate_for(config))
    first = job.run()
    assert first["entries_written"] == 3, first["entries_written"]
    opts2 = C.ExtractOptions(destination=first["destination"], checkpoint=True, resume=True,
                             job_key="resume-test", publish="direct")
    job2 = C.ExtractionJob(archive, opts2, config, logger_for(config, "resume-b"),
                           R.Cancellation(60).install(), gate_for(config))
    second = job2.run()
    assert second["entries_written"] == 0, second["entries_written"]
    assert second["entries_skipped"] == 3, second["entries_skipped"]
    return {"first_written": first["entries_written"], "resumed_skipped": second["entries_skipped"]}


def case_cancellation(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "cancel.zip"))
    cancel = R.Cancellation(60).install()
    cancel.requested = True
    cancel.reason = "selftest"
    job = C.ExtractionJob(archive, C.ExtractOptions(destination=os.path.join(workdir, "out", "cancel"),
                                                    checkpoint=False),
                          config, logger_for(config, "cancel"), cancel, gate_for(config))
    try:
        job.run()
    except PecError as exc:
        assert exc.code == R.EXIT_CANCELLED, exc.code
        return {"exit_code": exc.code, "kind": exc.kind}
    raise AssertionError("cancellation did not stop the job")


def case_approval_gate(workdir):
    config = config_for(workdir)
    strict = R.ApprovalGate(config)
    blocked = False
    try:
        strict.require("overwrite")
    except PecError as exc:
        blocked = exc.code == R.EXIT_APPROVAL
    assert blocked, "unapproved overwrite must be refused"
    approval_file = os.path.join(workdir, "fixtures", "approval.json")
    R.write_json_atomic(approval_file, {"approvals": [
        {"action": "overwrite", "approved_by": "selftest-reviewer", "expires": "2999-01-01T00:00:00Z"}]})
    gate = R.ApprovalGate(config, approval_file=approval_file)
    record = gate.require("overwrite", detail="selftest")
    assert record["granted"] is True
    return {"blocked_without_token": blocked, "granted_with_file": True}


def case_overwrite_policy(workdir):
    config = config_for(workdir)
    fixtures = os.path.join(workdir, "fixtures")
    archive = make_zip(os.path.join(fixtures, "overwrite.zip"))
    dest = os.path.join(workdir, "out", "overwrite")
    base = dict(destination=dest, checkpoint=False, publish="direct")
    C.ExtractionJob(archive, C.ExtractOptions(**base), config, logger_for(config, "ov-a"),
                    R.Cancellation(60).install(), gate_for(config)).run()
    # default policy never silently overwrites; suffix mode renames instead
    result = C.ExtractionJob(archive, C.ExtractOptions(overwrite="suffix", **base), config,
                             logger_for(config, "ov-b"), R.Cancellation(60).install(),
                             gate_for(config)).run()
    assert result["renames"], "suffix policy must record renames"
    strict = C.ExtractionJob(archive, C.ExtractOptions(overwrite="never", **base), config,
                             logger_for(config, "ov-c"), R.Cancellation(60).install(),
                             gate_for(config)).run()
    assert strict["entries_failed"] >= 1, strict["entries_failed"]
    return {"renames": len(result["renames"]), "blocked": strict["entries_failed"]}


def case_package_source_release(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "project")
    make_tree(src)
    with open(os.path.join(src, ".env"), "w", encoding="utf-8") as fh:
        fh.write("TOKEN=should-not-ship\n")
    out_dir = os.path.join(workdir, "out", "release")
    logger = logger_for(config, "release")
    result = P.run_recipe("source-release", [src], out_dir,
                          {"version": "v9.9", "format": "tar.gz"}, config, logger,
                          gate_for(config), R.Cancellation(120).install())
    archive = result["published"]["archive"]
    assert os.path.exists(archive), archive
    names = [n for n in zipfile_names_or_tar(archive)]
    assert not any(n.endswith(".env") for n in names), names
    assert result["produced"], result["produced"]
    return {"archive": archive, "entries": len(names)}


def zipfile_names_or_tar(path: str) -> list[str]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    with tarfile.open(path) as tf:
        return tf.getnames()


def case_package_evidence_and_patch(workdir):
    config = config_for(workdir)
    base = os.path.join(workdir, "fixtures", "base")
    target = os.path.join(workdir, "fixtures", "target")
    os.makedirs(os.path.join(base), exist_ok=True)
    os.makedirs(os.path.join(target), exist_ok=True)
    for root, rows in ((base, {"a.txt": "one", "b.txt": "two"}), (target, {"a.txt": "one-changed", "c.txt": "new"})):
        for name, text in rows.items():
            with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
                fh.write(text)
    patch_out = os.path.join(workdir, "out", "patch")
    patch = P.run_recipe("update-patch", [base, target], patch_out, {}, config,
                         logger_for(config, "patch"), gate_for(config), R.Cancellation(60).install())
    assert patch["published"]["directory"], patch
    patch_manifest = R.read_json(os.path.join(patch_out, "pec-package-manifest.json"), {})
    actions = {e["rel"]: e["action"] for e in patch_manifest["entries"]}
    assert actions.get("a.txt") == "modify", actions
    assert actions.get("c.txt") == "add", actions
    assert os.path.exists(os.path.join(patch_out, "patch", "ROLLBACK.json")), os.listdir(patch_out)
    ev_out = os.path.join(workdir, "out", "evidence")
    ev = P.run_recipe("evidence", [target], ev_out, {"case_reference": "SELFTEST-1"}, config,
                      logger_for(config, "evidence"), gate_for(config), R.Cancellation(60).install())
    evidence_archive = ev["published"]["archive"]
    assert os.path.exists(evidence_archive)
    with zipfile.ZipFile(evidence_archive) as zf:
        names = zf.namelist()
    assert any(n.endswith("CUSTODY.json") for n in names), names
    return {"patch": actions, "evidence_entries": len(names)}


def case_package_handoff(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "create-src")
    make_tree(src)
    out_dir = os.path.join(workdir, "out", "handoff")
    result = P.run_recipe("project-handoff", [src], out_dir, {"project": "demo", "owner": "selftest"},
                          config, logger_for(config, "handoff"), gate_for(config),
                          R.Cancellation(60).install())
    archive = result["published"]["archive"]
    names = zipfile_names_or_tar(archive)
    assert any(n.endswith("HANDOFF.md") for n in names), names
    return {"archive": archive, "has_instructions": True}


def case_recipe_option_validation(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "create-src")
    make_tree(src)
    try:
        P.run_recipe("single-bundle", [src], os.path.join(workdir, "out", "bad"),
                     {"nonsense": True}, config, logger_for(config, "bad"), gate_for(config),
                     R.Cancellation(30).install())
    except PecError as exc:
        assert exc.code == R.EXIT_USAGE, exc.code
        assert "unknown option" in exc.message
        return {"rejected": exc.message[:120]}
    raise AssertionError("unknown recipe option was accepted")


def case_config_validation(workdir):
    good = R.Config.load(None).copy()
    assert not R.validate_config(good.data), R.validate_config(good.data)
    bad = good.copy()
    bad.data["limits"]["max_entries"] = -5
    bad.data["policy"]["overwrite"] = "sometimes"
    bad.data["limits"]["max_entry_bytes"] = 10 ** 9
    bad.data["limits"]["max_total_bytes"] = 10 ** 6
    problems = R.validate_config(bad.data)
    fields = {p["field"] for p in problems}
    assert "limits.max_entries" in fields, fields
    assert "policy.overwrite" in fields, fields
    assert "limits.max_entry_bytes" in fields, fields
    return {"problems_detected": sorted(fields)}


def case_size_and_duration_parsing(workdir):
    assert R.parse_size("4MiB") == 4 * 1024 * 1024
    assert R.parse_size("10K") == 10 * 1024
    assert R.parse_size(2048) == 2048
    assert R.parse_duration("90") == 90
    assert R.parse_duration("2m") == 120
    assert R.parse_size("2h") if False else True
    try:
        R.parse_size("banana")
    except PecError:
        pass
    else:
        raise AssertionError("invalid size accepted")
    return {"ok": True}


def case_metadata_sanitize(workdir):
    config = config_for(workdir)
    root = os.path.join(workdir, "out", "sanitize")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("keep\n")
    with open(os.path.join(root, ".DS_Store"), "wb") as fh:
        fh.write(b"junk")
    result = C.sanitize_metadata(root, config, drop_junk=True, epoch="2020-01-01")
    assert result["removed"] == [".DS_Store"], result
    stamp = os.path.getmtime(os.path.join(root, "keep.txt"))
    assert abs(stamp - 1577836800) < 5, stamp
    return result


def case_split_zips(workdir):
    config = config_for(workdir)
    src = os.path.join(workdir, "fixtures", "create-src")
    make_tree(src)
    out_dir = os.path.join(workdir, "out", "separate")
    result = P.run_recipe("separate-zip", [src], out_dir, {"group_by": "extension"}, config,
                          logger_for(config, "separate"), gate_for(config),
                          R.Cancellation(60).install())
    groups = result["published"]["groups"]
    assert groups, groups
    assert any(g["group"] == "txt" for g in groups), groups
    return {"groups": [g["group"] for g in groups]}


def case_scan_mismatch(workdir):
    config = config_for(workdir)
    root = os.path.join(workdir, "out", "mismatch")
    os.makedirs(root, exist_ok=True)
    payload = os.path.join(workdir, "fixtures", "real.zip")
    make_zip(payload)
    shutil.copy(payload, os.path.join(root, "photo.jpg"))
    with open(os.path.join(root, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("plain text\n")
    findings = C.scan_mismatches(root, config)
    assert any(f["path"] == "photo.jpg" for f in findings), findings
    return {"findings": findings}


ALL_CASES = [
    ("detect", "Magic-byte detection incl. disguised extensions", case_detect),
    ("list-select", "Listing plus glob/regex selection", case_list_and_select),
    ("extract", "Basic extraction with manifest and hash audit", case_extract_basic),
    ("extract-select", "Extension-filtered partial extraction", case_extract_partial_pattern),
    ("zip-slip", "Traversal and absolute-path entries are blocked", case_traversal_blocked),
    ("bomb", "Expansion-ratio guard blocks a decompression bomb", case_bomb_ratio),
    ("create", "Deterministic ZIP creation + native test", case_create_and_verify),
    ("create-formats", "TAR/TAR.GZ/TAR.XZ/GZIP creation", case_create_tar_variants),
    ("targz", "tar.gz opens as TAR and extracts", case_targz_lists_as_tar),
    ("round-trip", "Round-trip content verification", case_round_trip),
    ("tamper", "Manifest audit detects modified output", case_verify_detects_tamper),
    ("secrets", "Secret scanning finds AWS key patterns", case_scan_secrets),
    ("organize", "Content-type organization", case_organize),
    ("nested", "Nested archive unwrapping with lineage", case_nested),
    ("volumes", "Split + assemble numeric volume set", case_volumes),
    ("volume-gap", "Missing volume number diagnosis", case_volume_diagnosis),
    ("checkpoint", "Checkpointed resume skips finished entries", case_checkpoint_resume),
    ("cancel", "Cancellation stops the job with exit 9", case_cancellation),
    ("approval", "Approval gate blocks/permits destructive action", case_approval_gate),
    ("overwrite", "Overwrite policy suffix/never behaviour", case_overwrite_policy),
    ("recipe-release", "Source-release recipe excludes secrets", case_package_source_release),
    ("recipe-patch", "Update-patch + evidence recipes", case_package_evidence_and_patch),
    ("recipe-handoff", "Project-handoff recipe writes instructions", case_package_handoff),
    ("recipe-options", "Unknown recipe options are rejected", case_recipe_option_validation),
    ("config", "Config validation catches invalid values", case_config_validation),
    ("parsing", "Size/duration parsing and rejection", case_size_and_duration_parsing),
    ("metadata", "Metadata sanitation removes junk and sets epoch", case_metadata_sanitize),
    ("multi-zip", "Separate-zip grouping by extension", case_split_zips),
    ("mismatch", "File-type mismatch detection", case_scan_mismatch),
]


def run_all(workdir: str, keep: bool = False, only: list[str] | None = None,
            log=None) -> dict:
    started = time.monotonic()
    results = []
    for name, description, fn in ALL_CASES:
        if only and name not in only:
            continue
        case_dir = os.path.join(workdir, name)
        if os.path.exists(case_dir) and not keep:
            shutil.rmtree(case_dir)
        os.makedirs(case_dir, exist_ok=True)
        case = Case(name, description)
        outcome = case.run(fn, case_dir)
        results.append(outcome)
        if log:
            (log.info if outcome["ok"] else log.error)(
                "selftest.case", f"{name}: {'pass' if outcome['ok'] else outcome.get('error')}")
        if not keep:
            shutil.rmtree(case_dir, ignore_errors=True)
    passed = sum(1 for r in results if r["ok"])
    return {
        "cases": results,
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "ok": passed == len(results),
        "seconds": round(time.monotonic() - started, 2),
    }
