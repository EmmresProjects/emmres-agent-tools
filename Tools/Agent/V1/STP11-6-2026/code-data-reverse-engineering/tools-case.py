"""Case management, evidence ledgers, reproducible planning, reporting,
preservation packaging, timelines, and compatibility matrices."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, load_json_asset, read_bytes, validate_input_file,
                        hash_file, hash_bytes, shannon_entropy, utcnow, case_paths, load_case, save_case,
                        append_jsonl, read_jsonl, custody_event, verify_custody_chain, envelope, TOOL_VERSION)
from tools_identify import identify_bytes, extract_strings, detect_encoding
from tools_formats import detect_format, cmd_parse, parse_elf, parse_pe, parse_macho
from tools_core import load_config


def cmd_intake(args, env, cfg):
    case_dir = Path(args.case)
    cp = case_paths(case_dir)
    if cp["case"].exists():
        case = load_case(args.case)
    else:
        case = {"schema": "retool.case.v1", "created_at": utcnow(), "tool_version": TOOL_VERSION,
                "purpose": None, "authorization": None, "artifacts": [], "notes": []}
        cp["artifacts"].mkdir(parents=True, exist_ok=True)
        cp["results"].mkdir(parents=True, exist_ok=True)
    if args.note:
        case["notes"].append({"at": utcnow(), "note": args.note})
    added = []
    for p in args.paths:
        src = validate_input_file(p, cfg)
        h = hash_file(src, ["sha256", "sha1", "md5"])
        data = read_bytes(src, cfg, 0, min(src.stat().st_size, cfg["limits"]["max_read_bytes"]))
        ident = identify_bytes(data, src.name)
        # immutable working copy named by hash to avoid collisions
        dest_name = f"{h['sha256'][:16]}-{src.name}"
        dest = cp["artifacts"] / dest_name
        if not dest.exists():
            shutil.copy2(src, dest)
            try:
                dest.chmod(0o444)
            except OSError:
                pass
        copy_hash = hash_file(dest, ["sha256"])["sha256"]
        if copy_hash != h["sha256"]:
            raise RetoolError("copy-mismatch", "Working copy hash does not match original; intake aborted.")
        artifact = {"original_path": str(src), "original_name": src.name, "copy": str(dest.relative_to(case_dir)),
                    "size": h["size"], "sha256": h["sha256"], "sha1": h["sha1"], "md5": h["md5"],
                    "identification": ident["best_match"], "intake_at": utcnow(),
                    "source_note": args.source or "operator-supplied"}
        case["artifacts"].append(artifact)
        added.append(artifact)
        custody_event(case_dir, "intake", {"artifact": dest_name, "sha256": h["sha256"],
                                           "original": str(src), "source": args.source or "operator-supplied"})
    save_case(case_dir, case)
    env["result"] = {"case": str(case_dir), "artifacts_added": len(added), "artifacts": added,
                     "total_artifacts": len(case["artifacts"]),
                     "note": "Originals were not modified. Read-only working copies are stored under artifacts/ and hash-verified."}
    return env


def cmd_inventory(args, env, cfg):
    root = Path(args.path)
    if not root.is_dir():
        raise RetoolError("not-dir", "inventory requires a directory", exit_code=EXIT_USAGE)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(p)
        if len(files) >= cfg["limits"]["max_inventory_files"]:
            break
    rows = []
    type_census = {}
    for p in files:
        if p.stat().st_size > cfg["limits"]["max_file_bytes"]:
            rows.append({"path": str(p.relative_to(root)), "size": p.stat().st_size, "skipped": "too large"})
            continue
        h = hash_file(p, ["sha256"])
        data = read_bytes(p, cfg, 0, min(p.stat().st_size, 65536))
        ident = identify_bytes(data, p.name)
        fam = ident["best_match"]["family"]
        type_census[fam] = type_census.get(fam, 0) + 1
        rows.append({"path": str(p.relative_to(root)), "size": h["size"], "sha256": h["sha256"],
                     "type": ident["best_match"]["name"], "family": fam,
                     "extension_mismatch": bool(ident["extension_mismatch"])})
    env["result"] = {"root": str(root), "file_count": len(rows), "files": rows,
                     "family_census": type_census,
                     "mismatch_count": sum(1 for r in rows if r.get("extension_mismatch"))}
    return env


def cmd_custody(args, env, cfg):
    case_dir = Path(args.case)
    load_case(args.case)  # validate exists
    if args.add_event:
        try:
            details = json.loads(args.add_event)
        except json.JSONDecodeError as e:
            raise RetoolError("bad-event", f"--add-event must be valid JSON: {e}", exit_code=EXIT_USAGE)
        ev = custody_event(case_dir, args.event or "note", details, actor=args.actor or "operator")
        env["result"] = {"appended": ev}
        return env
    events = read_jsonl(case_paths(case_dir)["custody"])
    chain = verify_custody_chain(case_dir)
    env["result"] = {"case": str(case_dir), "events": events, "chain": chain}
    return env


def cmd_ledger(args, env, cfg):
    case_dir = Path(args.case)
    load_case(args.case)
    cp = case_paths(case_dir)
    if args.add:
        if len(args.add) != 2:
            raise RetoolError("bad-ledger", "--add requires KIND and TEXT", exit_code=EXIT_USAGE)
        kind, text = args.add
        allowed = ("fact", "tool_output", "interpretation", "hypothesis", "question")
        if kind not in allowed:
            raise RetoolError("bad-kind", f"Ledger kind must be one of {allowed}", exit_code=EXIT_USAGE)
        rec = {"seq": len(read_jsonl(cp["ledger"])) + 1, "at": utcnow(), "kind": kind, "text": text,
               "evidence_ref": args.ref}
        append_jsonl(cp["ledger"], rec)
        env["result"] = {"appended": rec}
        return env
    entries = read_jsonl(cp["ledger"])
    by_kind = {}
    for e in entries:
        by_kind.setdefault(e.get("kind", "?"), []).append(e)
    env["result"] = {"case": str(case_dir), "entry_count": len(entries), "entries": entries,
                     "by_kind_counts": {k: len(v) for k, v in by_kind.items()}}
    return env


def cmd_authorize(args, env, cfg):
    case_dir = Path(args.case)
    case = load_case(args.case)
    allowed = load_json_asset("asset-config.json")["safety"]["allowed_purposes"]
    if args.purpose not in allowed:
        raise RetoolError("bad-purpose", f"Purpose must be one of {allowed}", exit_code=EXIT_USAGE)
    if not args.statement or len(args.statement) < 10:
        raise RetoolError("bad-statement", "Provide an authorization statement (>= 10 chars) describing ownership/authorization.", exit_code=EXIT_USAGE)
    case["purpose"] = args.purpose
    case["authorization"] = {"purpose": args.purpose, "statement": args.statement, "at": utcnow(),
                             "actor": args.actor or "operator"}
    save_case(case_dir, case)
    custody_event(case_dir, "authorization", case["authorization"])
    env["result"] = {"case": str(case_dir), "authorization": case["authorization"]}
    return env


def cmd_verify(args, env, cfg):
    case_dir = Path(args.case)
    case = load_case(args.case)
    cp = case_paths(case_dir)
    checks = []
    for art in case["artifacts"]:
        copy = case_dir / art["copy"]
        if not copy.exists():
            checks.append({"artifact": art["copy"], "ok": False, "error": "missing copy"})
            continue
        actual = hash_file(copy, ["sha256"])["sha256"]
        checks.append({"artifact": art["copy"], "ok": actual == art["sha256"],
                       "expected": art["sha256"], "actual": actual})
    chain = verify_custody_chain(case_dir)
    all_ok = all(c["ok"] for c in checks) and chain["valid"]
    env["result"] = {"case": str(case_dir), "artifacts_checked": len(checks), "checks": checks,
                     "custody_chain": chain, "all_ok": all_ok,
                     "clean_state_note": "Verification re-hashes working copies and the custody chain from the current case directory; no cached state is used."}
    if not all_ok:
        env["errors"].append({"code": "verification-failed", "message": "One or more artifact hashes or custody links do not match."})
    return env


def cmd_survey(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    ident = identify_bytes(data, path.name)
    hashes = hash_file(path, cfg["output"]["hash_algorithms"])
    strings = extract_strings(data, 6, 200, "all")
    fmt = detect_format(data, path.name)
    parsed = None
    parse_error = None
    import types
    ns = types.SimpleNamespace(path=str(path), format="auto")
    e = envelope("parse", cfg)
    try:
        parsed = cmd_parse(ns, e, cfg)["result"]
    except RetoolError as ex:
        parse_error = {"code": ex.code, "message": ex.message}
    # toolchain fingerprints from strings
    text_blob = "\n".join(s["value"] for s in strings)
    fingerprints = []
    for pat, label in [(r"GCC:?\s*\(?([\w.\- ]+)\)?\s*\d+\.\d+\.\d+", "GCC"),
                       (r"clang version ([\d.]+)", "clang"), (r"Microsoft \(R\).*Version ([\d.]+)", "MSVC"),
                       (r"rustc ([\d.]+)", "rustc"), (r"go1\.\d+", "Go"), (r"Python ([\d.]+)", "Python")]:
        m = re.search(pat, text_blob)
        if m:
            fingerprints.append({"toolchain": label, "version_hint": m.group(1) if m.groups() else m.group(0), "confidence": "medium"})
    result = {"file": str(path), "size": path.stat().st_size, "identification": ident,
              "hashes": hashes, "entropy": shannon_entropy(data[:65536]),
              "format": fmt, "parse": parsed, "parse_error": parse_error,
              "strings_sample": strings[:100], "string_count_sample": len(strings),
              "encoding": detect_encoding(data) if ident["best_match"]["family"] == "text" else None,
              "toolchain_fingerprints": fingerprints}
    env["result"] = result
    if args.case:
        case_dir = Path(args.case)
        if case_paths(case_dir)["case"].exists():
            cp = case_paths(case_dir)
            out = cp["results"] / f"survey-{hashes['sha256'][:12]}.json"
            out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            custody_event(case_dir, "survey", {"artifact": path.name, "result": str(out.relative_to(case_dir))})
            result["saved_to_case"] = str(out)
    return env


def cmd_plan(args, env, cfg):
    plans = load_json_asset("asset-plans.json")["templates"]
    name = args.template
    if name == "list":
        env["result"] = {"templates": [{"name": k, "title": v["title"], "purpose": v["purpose"]} for k, v in plans.items()]}
        return env
    if name not in plans:
        raise RetoolError("unknown-template", f"Unknown plan template '{name}'. Use --template list. Available: {', '.join(plans)}", exit_code=EXIT_USAGE)
    tpl = plans[name]
    target = args.target or "<target>"
    outdir = args.outdir or "case-output"
    steps = []
    for s in tpl["steps"]:
        steps.append({**s, "command": s["command"].replace("{target}", target).replace("{outdir}", outdir)})
    runbook = {"template": name, "title": tpl["title"], "purpose": tpl["purpose"],
               "generated_at": utcnow(), "authorization": tpl["authorization"],
               "prerequisites": tpl["prerequisites"], "steps": steps,
               "limits": tpl["limits"], "verification": tpl["verification"],
               "execution_policy": "This tool generates the plan but does not execute dynamic steps. Operator approval and an isolated environment are required for any dynamic action."}
    # markdown rendering
    md = [f"# Runbook: {tpl['title']}", "", f"- Template: `{name}`", f"- Purpose: {tpl['purpose']}",
          f"- Generated: {runbook['generated_at']}", "", "## Authorization", tpl["authorization"], "",
          "## Prerequisites"] + [f"- {p}" for p in tpl["prerequisites"]] + ["", "## Steps", ""]
    for i, s in enumerate(steps, 1):
        md += [f"{i}. **{s['tool']}** — {s.get('expected', '')}", f"   ```bash", f"   {s['command']}", "   ```",
               f"   Checkpoint: {s.get('checkpoint', '')}", ""]
    md += ["## Limits", tpl["limits"], "", "## Verification", tpl["verification"], "",
           "> Dynamic steps are operator-driven in an authorized isolated environment; this tool never executes them."]
    runbook["markdown"] = "\n".join(md)
    if args.out:
        from tools_core import write_new_bytes
        write_new_bytes(Path(args.out), runbook["markdown"].encode("utf-8"), approve=getattr(args, "approve", False))
        runbook["written"] = args.out
    if args.case:
        case_dir = Path(args.case)
        if case_paths(case_dir)["case"].exists():
            cp = case_paths(case_dir)
            out = cp["results"] / f"runbook-{name}.md"
            out.write_text(runbook["markdown"], encoding="utf-8")
            custody_event(case_dir, "plan", {"template": name, "result": str(out.relative_to(case_dir))})
            runbook["saved_to_case"] = str(out)
    env["result"] = runbook
    return env


def cmd_report(args, env, cfg):
    case_dir = Path(args.case)
    case = load_case(args.case)
    cp = case_paths(case_dir)
    tpl = (Path(__file__).parent / "asset-report-template.md").read_text(encoding="utf-8")
    inventory_rows = ["| Artifact | Size | SHA-256 | Type |", "|---|---|---|---|"]
    for a in case["artifacts"]:
        inventory_rows.append(f"| {a['original_name']} | {a['size']} | `{a['sha256'][:16]}…` | {a['identification']['name']} |")
    ledger = read_jsonl(cp["ledger"])
    ledger_rows = ["| # | Kind | Statement | Ref |", "|---|---|---|---|"]
    for e in ledger:
        ledger_rows.append(f"| {e.get('seq')} | {e.get('kind')} | {e.get('text', '')[:200]} | {e.get('evidence_ref') or ''} |")
    custody = read_jsonl(cp["custody"])
    custody_rows = ["| Seq | At | Event | Details |", "|---|---|---|---|"]
    for c in custody:
        custody_rows.append(f"| {c.get('seq')} | {c.get('at')} | {c.get('event')} | `{json.dumps(c.get('details', {}))[:120]}` |")
    # gather saved results
    findings = []
    for f in sorted(cp["results"].glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            findings.append(f"### {f.name}\n\n```json\n{json.dumps(r, indent=2, default=str)[:4000]}\n```")
        except Exception:
            pass
    for f in sorted(cp["results"].glob("*.md")):
        findings.append(f"### {f.name}\n\n{f.read_text(encoding='utf-8')[:4000]}")
    auth = case.get("authorization")
    auth_text = (f"Purpose: {auth['purpose']}\n\nStatement: {auth['statement']}\n\nRecorded: {auth['at']}"
                 if auth else "No authorization recorded. Run authorize before dual-use operations.")
    repl = {
        "{{title}}": f"Analysis Report — {case_dir.name}",
        "{{generated_at}}": utcnow(), "{{tool_version}}": TOOL_VERSION, "{{case_dir}}": str(case_dir),
        "{{purpose}}": case.get("purpose") or "unspecified",
        "{{authorization}}": auth_text,
        "{{inventory_table}}": "\n".join(inventory_rows),
        "{{identification_table}}": "\n".join(inventory_rows),
        "{{hash_table}}": "\n".join(inventory_rows),
        "{{ledger_table}}": "\n".join(ledger_rows) if ledger else "_No ledger entries._",
        "{{findings}}": "\n\n".join(findings) if findings else "_No saved results. Run survey/parse/etc. with --case to populate._",
        "{{custody_table}}": "\n".join(custody_rows),
        "{{runbook}}": "See runbook-*.md in results/ (generate with plan --case).",
        "{{limitations}}": "- Built-in parsers cover documented subsets; see knowledge-readme.md.\n- Signature trust chains are not cryptographically verified.\n- Dynamic analysis is plan-only.",
        "{{next_steps}}": "- Review hypotheses in the ledger.\n- Collect additional evidence for unresolved questions.\n- Run verify --case to confirm integrity.",
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    result = {"case": str(case_dir), "markdown": tpl, "artifact_count": len(case["artifacts"]),
              "ledger_entries": len(ledger), "custody_events": len(custody)}
    if args.out:
        from tools_core import write_new_bytes
        write_new_bytes(Path(args.out), tpl.encode("utf-8"), approve=getattr(args, "approve", False))
        result["written"] = args.out
    env["result"] = result
    return env


def cmd_preserve(args, env, cfg):
    case_dir = Path(args.case)
    case = load_case(args.case)
    cp = case_paths(case_dir)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        from tools_core import require_approval
        require_approval(args, env, "repack-overwrite")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "retool.preservation.v1", "created_at": utcnow(), "tool_version": TOOL_VERSION,
                "case": str(case_dir), "purpose": case.get("purpose"), "files": []}
    # copy case files
    for src in sorted(case_dir.rglob("*")):
        if src.is_file():
            rel = src.relative_to(case_dir)
            dest = out / "case" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            manifest["files"].append({"path": f"case/{rel}", "sha256": hash_file(src, ["sha256"])["sha256"],
                                      "size": src.stat().st_size})
    # write manifest + readme
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    readme = ["# Technical Preservation Package", "", f"Created: {manifest['created_at']}",
              f"Tool: code-data-reverse-engineering v{TOOL_VERSION}", "",
              "## Contents", "- `case/` — verified working copies, custody chain, evidence ledger, results",
              "- `manifest.json` — SHA-256 manifest of every packaged file", "",
              "## Verification", "Re-hash every file against manifest.json; run `verify --case case/`.", "",
              "## Provenance", f"Purpose: {case.get('purpose') or 'unspecified'}",
              f"Authorization: {json.dumps(case.get('authorization'))}", "",
              "Originals were not modified. All artifacts are hash-verified copies."]
    (out / "README.md").write_text("\n".join(readme), encoding="utf-8")
    manifest["files"].append({"path": "README.md", "sha256": hash_bytes("\n".join(readme).encode())["sha256"],
                              "size": len("\n".join(readme).encode())})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result = {"package": str(out), "file_count": len(manifest["files"]), "manifest": "manifest.json"}
    if args.zip:
        zpath = out.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    zi = zipfile.ZipInfo(str(f.relative_to(out)), date_time=(1980, 1, 1, 0, 0, 0))
                    z.writestr(zi, f.read_bytes())
        result["zip"] = str(zpath)
        result["zip_sha256"] = hash_file(zpath, ["sha256"])["sha256"]
    custody_event(case_dir, "preserve", {"package": str(out), "files": len(manifest["files"])})
    env["result"] = result
    return env


def cmd_timeline(args, env, cfg):
    events = []
    if args.case:
        case_dir = Path(args.case)
        load_case(args.case)
        for c in read_jsonl(case_paths(case_dir)["custody"]):
            events.append({"at": c.get("at"), "source": "custody", "event": c.get("event"),
                           "details": c.get("details")})
        case = load_case(args.case)
        for a in case["artifacts"]:
            events.append({"at": a.get("intake_at"), "source": "intake", "event": "artifact-intake",
                           "details": {"name": a["original_name"], "sha256": a["sha256"]}})
    for lp in (args.logs or []):
        path = validate_input_file(lp, cfg)
        text = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"])).decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            m = re.search(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)", line)
            if m:
                events.append({"at": m.group(1), "source": f"log:{path.name}", "event": "log-line",
                               "details": {"line": i, "text": line[:200]}})
            if len(events) > 5000:
                break
    for pp in (args.pcap or []):
        path = validate_input_file(pp, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        from tools_network import parse_pcap, parse_pcapng, PCAP_MAGICS
        try:
            r = parse_pcap(data, 2000) if data[:4] in PCAP_MAGICS else parse_pcapng(data, 2000)
            for pkt in r.get("packets", [])[:2000]:
                ts = pkt.get("timestamp")
                if ts:
                    iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    dec = pkt.get("decoded", {})
                    ip = dec.get("ipv4") or dec.get("ipv6") or {}
                    events.append({"at": iso, "source": f"pcap:{path.name}", "event": "packet",
                                   "details": {"src": ip.get("src"), "dst": ip.get("dst"), "len": pkt.get("original_len")}})
        except RetoolError:
            pass
    def sort_key(e):
        return e.get("at") or ""
    events.sort(key=sort_key)
    env["result"] = {"event_count": len(events), "events": events[:2000],
                     "note": "Timestamps are normalized to UTC strings where possible; log timestamps are extracted by pattern and may need timezone confirmation."}
    return env


def cmd_compat(args, env, cfg):
    tools = cfg["external_tools"]
    tool_rows = []
    for name in tools:
        tool_rows.append({"tool": name, "available": shutil.which(name) is not None,
                          "dynamic": tools[name]["dynamic"]})
    parsers = ["elf", "pe", "macho", "ar", "class", "dex", "wasm", "zip", "tar", "gzip", "png", "jpeg",
               "gif", "riff", "mp4", "pdf", "cfb", "dtb", "mbr", "gpt", "bootimg", "squashfs", "ext",
               "uefi", "rom", "disk", "axml", "arsc", "gguf", "safetensors", "pickle", "spirv", "dxbc",
               "sourcemap", "wad", "pak", "unity", "rpm", "plist", "oci", "minidump"]
    archs = ["x86", "x64", "arm64", "riscv32", "riscv64", "mips32", "6502", "evm"]
    env["result"] = {
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                     "python": sys.version.split()[0]},
        "tool_version": TOOL_VERSION,
        "external_tools": tool_rows,
        "builtin_parsers": parsers,
        "builtin_disasm_archs": archs,
        "stdlib_modules": {m: True for m in ("zlib", "bz2", "lzma", "sqlite3", "plistlib", "hashlib", "zipfile", "tarfile")},
        "optional_unavailable": {"tomllib": sys.version_info < (3, 11),
                                 "note": "tomllib requires Python 3.11+; PyYAML/capstone/libzstd are not bundled."},
        "limits": cfg["limits"],
    }
    return env
