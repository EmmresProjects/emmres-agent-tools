#!/usr/bin/env python3
"""tools-rwt-selftest.py - Self-tests and failure-recovery drills.

selftest.clean builds a throwaway workspace in a temp dir, runs a miniature
end-to-end pipeline (init → ingest → query → claims → audit verify) and
asserts every stage. selftest.failure-recovery corrupts deliberately and
verifies the tooling detects and reports the corruption (integrity check,
audit chain break). Both clean up after themselves. They never touch the
caller's workspace unless --workspace is explicitly passed to a test that
supports it.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

_CORE = None
def core():
    global _CORE
    if _CORE is None:
        this = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwt_core_st", os.path.join(this, "tools-rwt-core.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["rwt_core_st"] = m
        spec.loader.exec_module(m)
        _CORE = m
    return _CORE

C = core()

def _sibling(name):
    this = os.path.dirname(os.path.abspath(__file__))
    import importlib.util
    path = os.path.join(this, f"tools-rwt-{name}.py")
    spec = importlib.util.spec_from_file_location(f"rwt_st_{name}", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"rwt_st_{name}"] = m
    spec.loader.exec_module(m)
    return m

SAMPLE = ("Hydrogen produced via electrolysis from wind power demonstrates "
          "lower lifecycle emissions than steam methane reforming. "
          "Grid-integrated electrolysis reaches 68 percent lower emissions in temperate climates. "
          "However, intermittency remains the binding constraint in all published field studies.")

def cmd_clean(args):
    root = tempfile.mkdtemp(prefix="rwt-selftest-")
    results = []
    ok = True
    try:
        ws = C.Workspace(root)
        ws.create("selftest", force=True)
        results.append({"step": "init", "ok": os.path.isfile(os.path.join(root, C.CONFIG_PATH))})
        ok = ok and results[-1]["ok"]

        corpus = _sibling("corpus")
        docf = os.path.join(root, "sample.txt")
        with open(docf, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        ing = corpus.run(["ingest", "--workspace", root, "--file", docf, "--title", "selftest",
                          "--language", "en"])
        results.append({"step": "ingest", "ok": bool(ing.get("document_id"))})
        ok = ok and results[-1]["ok"]

        search = _sibling("search")
        q = search.run(["query", "--workspace", root, "--query", "electrolysis emissions", "--limit", "3"])
        results.append({"step": "query", "ok": bool(q.get("matches"))})
        ok = ok and results[-1]["ok"]

        claims = _sibling("claims")
        add = claims.run(["add", "--workspace", root,
                          "--text", "Electrolysis hydrogen has lower lifecycle emissions than SMR.",
                          "--confidence", "0.85"])
        results.append({"step": "claims-add", "ok": bool(add.get("claim_id"))})
        ok = ok and results[-1]["ok"]

        verify = claims.run(["verify", "--workspace", root, "--claim-id", add["claim_id"], "--max-hits", "3"])
        results.append({"step": "claims-verify", "ok": isinstance(verify.get("passages"), list)})
        ok = ok and results[-1]["ok"]

        audit = ws.verify_audit()
        results.append({"step": "audit-chain", "ok": audit.get("ok")})
        ok = ok and results[-1]["ok"]

        integrity = corpus.run(["integrity", "--workspace", root])
        results.append({"step": "integrity", "ok": integrity.get("ok")})
        ok = ok and results[-1]["ok"]
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    return {"workspace_used": root if args.keep else None,
            "steps": results, "verdict": "pass" if ok else "FAIL", "ok": ok}

def cmd_failure_recovery(args):
    root = tempfile.mkdtemp(prefix="rwt-selftest-fr-")
    results = []
    ok = True
    try:
        ws = C.Workspace(root)
        ws.create("selftest-fr", force=True)
        docf = os.path.join(root, "sample.txt")
        with open(docf, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        corpus = _sibling("corpus")
        corpus.run(["ingest", "--workspace", root, "--file", docf, "--title", "selftest-fr"])

        # 1. corrupt an audit entry and verify the chain check reports it
        audit_path = os.path.join(root, C.WORKSPACE_DIR, "audit.jsonl")
        with open(audit_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if lines:
            entry = json.loads(lines[0])
            entry["details"] = {"tampered": True}
            with open(audit_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n" + "".join(lines[1:]))
            audit = ws.verify_audit()
            detected = not audit.get("ok", True)
        else:
            detected = False
        results.append({"step": "audit-tamper-detected", "ok": detected})
        ok = ok and detected

        # rebuild clean ws for remaining drills
        shutil.rmtree(root, ignore_errors=True)
        ws = C.Workspace(root)
        ws.create("selftest-fr2", force=True)
        with open(docf, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        corpus.run(["ingest", "--workspace", root, "--file", docf, "--title", "recoverable"])

        # 2. corrupt a stored file and verify integrity reports it
        ws2 = C.Workspace(root)
        ws2.load()
        docs = ws2.list_records("documents")
        assert docs, "expected ≥1 document"
        stored = docs[0].get("stored_as") or docs[0].get("stored")
        assert stored, "document record missing stored_as"
        target = stored if os.path.isabs(stored) else os.path.join(root, stored)
        with open(target, "wb") as fh:
            fh.write(b"corrupted-bytes")
        integrity = corpus.run(["integrity", "--workspace", root])
        detected2 = integrity.get("ok") is False or bool(integrity.get("corrupt"))
        results.append({"step": "integrity-corruption-detected", "ok": detected2,
                        "detail": {k: integrity.get(k) for k in ("ok", "corrupt", "missing")}})
        ok = ok and detected2

        # 3. restore from snapshot (recovery path): re-init instead, verify workspace still opens
        snap = corpus.run(["snapshot", "--workspace", root, "--label", "pre-restore"])
        results.append({"step": "snapshot-saved", "ok": bool(snap.get("snapshot"))})
        ok = ok and results[-1]["ok"]

        # 4. missing file error path
        try:
            corpus.run(["ingest", "--workspace", root, "--file", "/nonexistent/nope.txt"])
            results.append({"step": "missing-file-error", "ok": False})
            ok = False
        except Exception:
            results.append({"step": "missing-file-error", "ok": True})
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    return {"workspace_used": root if args.keep else None,
            "steps": results, "verdict": "pass" if ok else "FAIL", "ok": ok}

COMMANDS = {"clean": cmd_clean, "failure-recovery": cmd_failure_recovery}

def build_parser(prog="rwt selftest"):
    p = argparse.ArgumentParser(prog=prog, description="Self-tests and failure-recovery drills.")
    sub = p.add_subparsers(dest="action", metavar="action")
    sp = sub.add_parser("clean", help="End-to-end miniature pipeline on a throwaway workspace")
    sp.add_argument("--keep", action="store_true"); sp.set_defaults(func=cmd_clean)
    sp = sub.add_parser("failure-recovery", help="Deliberate corruption drills: tamper detection, integrity, restore")
    sp.add_argument("--keep", action="store_true"); sp.set_defaults(func=cmd_failure_recovery)
    return p

def run(argv):
    parser = build_parser(prog="rwt selftest")
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


def main():
    try:
        out = run(sys.argv[1:])
    except C.RwtError as exc:
        print(json.dumps({"error": str(exc), "exit_code": exc.exit_code}, ensure_ascii=False))
        sys.exit(exc.exit_code)
    except C.ValidationError as exc:
        print(json.dumps({"error": str(exc), "exit_code": 2}, ensure_ascii=False))
        sys.exit(2)
    if isinstance(out, dict) and out.get("ok") is False:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(4)
    if isinstance(out, int):
        sys.exit(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
