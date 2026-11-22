#!/usr/bin/env python3
"""code-data-reverse-engineering — primary entry point.

A unified, read-only-by-default workbench for code, data, binary analysis,
reverse engineering, and technical preservation. Standard library only.

Run:  python3 code-data-reverse-engineering/tools-retool.py --help
      python3 code-data-reverse-engineering/tools-retool.py capabilities
      python3 code-data-reverse-engineering/tools-retool.py selftest

All paths in examples are relative to the future skill root. Output is a
structured JSON envelope by default (--output-format text|md for humans).
Inputs are never modified; writes go to new outputs and destructive or
overwrite actions require --approve. Dual-use operations require --purpose.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Load sibling modules in dependency order (hyphenated filenames -> underscore module names).
_MODULES = ["tools-core", "tools-identify", "tools-formats", "tools-disasm", "tools-data",
            "tools-code", "tools-network", "tools-ops", "tools-case", "tools-selftest"]


def _load_modules():
    mods = {}
    for stem in _MODULES:
        modname = stem.replace("-", "_")
        path = HERE / f"{stem}.py"
        spec = importlib.util.spec_from_file_location(modname, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
        mods[modname] = mod
    return mods


M = _load_modules()
core = M["tools_core"]
ident = M["tools_identify"]
fmt = M["tools_formats"]
dis = M["tools_disasm"]
dat = M["tools_data"]
cod = M["tools_code"]
net = M["tools_network"]
ops = M["tools_ops"]
case = M["tools_case"]
selftest = M["tools_selftest"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools-retool.py",
        description="Unified code/data/binary analysis, reverse engineering, and preservation workbench. "
                    "Read-only by default; structured JSON output; approval gates for writes and dual-use operations.",
        epilog="See knowledge-readme.md for the full capability list, schemas, safety rules, and limits. "
               "Run 'capabilities' to list all 260 catalog capabilities and their handlers.")
    p.add_argument("--config", help="Path to a JSON config overriding asset-config.json")
    p.add_argument("--output-format", choices=["json", "text", "md"], default="json",
                   help="Output rendering (default: json structured envelope)")
    p.add_argument("--output", help="Write the rendered result to this new file (never overwrites without --approve)")
    p.add_argument("--version", action="version", version=f"code-data-reverse-engineering {core.TOOL_VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help_text):
        return sub.add_parser(name, help=help_text, description=help_text)

    # meta
    sp = add("capabilities", "List categories and the 260 catalog capabilities with their handlers")
    sp.add_argument("--category", type=int, help="Filter by category number (1-30)")
    sp.add_argument("--search", help="Search capability names/descriptions")
    sp.add_argument("--skills", action="store_true", help="List individual skills (default: category summary)")
    sp = add("selftest", "Run the built-in regression suite (fixtures + every major capability)")
    sp.add_argument("--verbose", action="store_true")
    sp = add("fixtures", "Generate the legal minimal regression fixture set into --out")
    sp.add_argument("--out", required=True)
    sp.add_argument("--approve", action="store_true")

    # case
    sp = add("intake", "Register artifacts into a case: hash, classify, immutable working copies, custody")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--case", required=True)
    sp.add_argument("--note")
    sp.add_argument("--source")
    sp = add("inventory", "Inventory a directory with hashes, types, and mismatch flags")
    sp.add_argument("path")
    sp = add("custody", "View or append chain-of-custody events")
    sp.add_argument("--case", required=True)
    sp.add_argument("--add-event", help="JSON details for a new event")
    sp.add_argument("--event", default="note")
    sp.add_argument("--actor")
    sp = add("ledger", "View or append evidence/inference ledger entries")
    sp.add_argument("--case", required=True)
    sp.add_argument("--add", nargs=2, metavar=("KIND", "TEXT"), help="KIND: fact|tool_output|interpretation|hypothesis|question")
    sp.add_argument("--ref", help="Evidence reference (path/offset/command)")
    sp = add("authorize", "Record an authorized purpose and statement for a case")
    sp.add_argument("--case", required=True)
    sp.add_argument("--purpose", required=True)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--actor")
    sp = add("verify", "Re-hash case copies and verify the custody chain (clean-state)")
    sp.add_argument("--case", required=True)
    sp = add("survey", "Combined read-only survey: identify, hash, entropy, strings, parse, toolchain hints")
    sp.add_argument("path")
    sp.add_argument("--case", help="Save the survey result into this case")
    sp = add("plan", "Generate a bounded, authorized runbook from a template")
    sp.add_argument("--template", required=True, help="Template name, or 'list'")
    sp.add_argument("--target")
    sp.add_argument("--outdir")
    sp.add_argument("--case")
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("report", "Render a Markdown evidence report from a case")
    sp.add_argument("--case", required=True)
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("preserve", "Assemble a preservation package (copies + manifest + reports); optional deterministic ZIP")
    sp.add_argument("--case", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--zip", action="store_true")
    sp.add_argument("--approve", action="store_true")
    sp = add("timeline", "Correlate custody, logs, and capture timestamps into a timeline")
    sp.add_argument("--case")
    sp.add_argument("--logs", nargs="*")
    sp.add_argument("--pcap", nargs="*")
    sp = add("compat", "Report environment, available tools, built-in parsers/archs, and limits")
    # identify
    sp = add("identify", "Identify files by signatures + structural probes; flag extension mismatches")
    sp.add_argument("paths", nargs="+")
    sp = add("entropy", "Map local entropy and classify regions (heuristic)")
    sp.add_argument("path")
    sp.add_argument("--window")
    sp.add_argument("--stride")
    sp = add("strings", "Extract ASCII/UTF-16 strings with offsets; flag encoded candidates")
    sp.add_argument("path")
    sp.add_argument("--min")
    sp.add_argument("--limit")
    sp.add_argument("--encoding", choices=["all", "ascii", "utf16le", "utf16be"], default="all")
    sp = add("hexdump", "Offset-aware hex view, optionally annotated with a layout")
    sp.add_argument("path")
    sp.add_argument("--offset")
    sp.add_argument("--length")
    sp.add_argument("--width")
    sp.add_argument("--layout", help="Layout name (asset-layouts.json) or path to a layout JSON")
    sp = add("encoding", "Detect character encoding, mojibake, newlines; optional repair plan")
    sp.add_argument("path")
    sp.add_argument("--repair", choices=["mojibake-latin1", "newlines-lf", "newlines-crlf", "add-bom", "remove-bom"])
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("unicode", "Unicode security scan: bidi controls, invisibles, confusables, normalization")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--text")
    sp = add("codec", "Decode/encode hex, base16/32/58/64/85, percent; layered auto-detect")
    sp.add_argument("--input-file")
    sp.add_argument("--text")
    sp.add_argument("--mode", help="Decode mode: hex|base32|base58|base64|base85|ascii85|percent|auto")
    sp.add_argument("--encode", help="Encode target: hex|base32|base58|base64|base85|percent")
    sp.add_argument("--layers")
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("numbers", "Decode integers/floats/fixed-point/BCD/varints or a layout spec at an offset")
    sp.add_argument("path")
    sp.add_argument("--offset")
    sp.add_argument("--types", help="Comma list, e.g. u32le,f32be,i64le")
    sp.add_argument("--varint", choices=["uleb128", "sleb128", "zigzag", "varint"])
    sp.add_argument("--spec", help="Layout name or path")
    sp.add_argument("--count")
    sp = add("xor", "Recover short repeating XOR keys or decode with a known key; rotation candidates")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--text")
    sp.add_argument("--key", help="Known key as hex")
    sp.add_argument("--max-keylen")
    sp.add_argument("--top")
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("carve", "Locate embedded files/streams by signature with bounded, validated extents")
    sp.add_argument("path")
    sp.add_argument("--out", help="Write signature-validated candidates to this directory")
    sp.add_argument("--approve", action="store_true")
    # formats
    sp = add("parse", "Parse a binary/container format (auto-detect or --format)")
    sp.add_argument("path")
    sp.add_argument("--format", default="auto", help="Input format: auto|elf|pe|macho|... (see knowledge-readme.md)")
    sp = add("sections", "Section/segment map with permissions and entropy (ELF/PE/Mach-O)")
    sp.add_argument("path")
    sp = add("symbols", "Normalized import/export/symbol report; optional demangling")
    sp.add_argument("path")
    sp.add_argument("--demangle", action="store_true")
    sp = add("disasm", "Disassemble a code window (built-in ISA subsets)")
    sp.add_argument("path")
    sp.add_argument("--arch", required=True, choices=dis.ARCHS)
    sp.add_argument("--offset")
    sp.add_argument("--length")
    sp.add_argument("--base-addr")
    sp.add_argument("--max-insns")
    sp.add_argument("--endian", choices=["le", "big", "little"])
    sp = add("cfg", "Control-flow graph, values, calling convention, frame, optional pseudocode")
    sp.add_argument("path")
    sp.add_argument("--arch", required=True, choices=dis.ARCHS)
    sp.add_argument("--offset")
    sp.add_argument("--length")
    sp.add_argument("--base-addr")
    sp.add_argument("--pseudocode", action="store_true")
    sp.add_argument("--endian", choices=["le", "big", "little"])
    sp = add("structs", "Mine structures: pointer tables, repeated layouts, or decode a layout spec")
    sp.add_argument("path")
    sp.add_argument("--stride-scan", action="store_true")
    sp.add_argument("--pointers", action="store_true")
    sp.add_argument("--layout")
    sp.add_argument("--offset")
    sp.add_argument("--count")
    # data
    sp = add("data", "Validate/decode structured & serialized data (JSON/YAML/TOML/INI/XML/CSV/protobuf/msgpack/cbor/bson/DER/SQLite/...)")
    sp.add_argument("path")
    sp.add_argument("--format", default="auto", help="Input format (auto-detect by default)")
    # code
    sp = add("code", "Source outline, dependencies, security review, algorithm hints")
    sp.add_argument("path")
    sp.add_argument("--lang", default="auto")
    sp.add_argument("--mode", choices=["all", "outline", "deps", "security", "algorithm", "encoding"], default="all")
    sp = add("regex", "Explain a regex, check ReDoS shape, and run bounded match tests")
    sp.add_argument("pattern")
    sp.add_argument("--test", nargs="*", help="Test strings")
    sp = add("sql", "Validate SQL syntax (SQLite) and review risky statements")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--statement")
    sp = add("build", "Audit build/CI/shell/IaC files; diagnose compiler errors; translate commands")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--mode", choices=["auto", "make", "cmake", "docker", "compose", "ci", "shell",
                                       "terraform", "ansible", "errors", "translate"], default="auto")
    sp.add_argument("--text", help="Inline text for errors/translate modes")
    sp.add_argument("--to", choices=["powershell", "cmd"], help="Target for translate mode")
    sp = add("secrets", "Scan for exposed secrets (redacted fingerprints); requires --purpose")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--purpose", required=True)
    sp = add("triage", "Defensive static triage: packers, anti-analysis, persistence, crypto, documents; requires --purpose")
    sp.add_argument("path")
    sp.add_argument("--purpose", required=True)
    # network
    sp = add("network", "Analyze captures and protocols: pcap/pcapng/http/tls/dns/websocket/tokens")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--mode", choices=["auto", "pcap", "pcapng", "http", "tls", "dns", "websocket", "tokens"], default="auto")
    sp.add_argument("--text")
    # ops
    sp = add("archive", "Inspect/validate/extract/repack archives with path-safety and size limits")
    sp.add_argument("path")
    sp.add_argument("--mode", choices=["inspect", "validate", "extract", "repack"], default="inspect")
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("compress", "Detect/validate/round-trip/benchmark compression streams")
    sp.add_argument("path")
    sp.add_argument("--mode", choices=["detect", "validate", "roundtrip", "benchmark"], default="detect")
    sp = add("hash", "Hash files (many algorithms), CRC/Adler, fuzzy fingerprints, CRC preset reconstruction")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--algos", help="Comma list, e.g. sha256,sha1,md5,sha3_256,blake2b")
    sp.add_argument("--fuzzy", action="store_true")
    sp.add_argument("--crc-recon", help="Expected CRC value (hex) to identify the preset")
    sp.add_argument("--width", help="CRC width for --crc-recon (16 or 32)")
    sp = add("diff", "Compare two files: bytes, strings, sections, or JSON semantics")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--mode", choices=["bytes", "strings", "sections", "json"], default="bytes")
    sp = add("duplicates", "Cluster exact and near-duplicate files in a directory")
    sp.add_argument("path")
    sp = add("signatures", "Inspect PE Authenticode / Mach-O / APK / JAR / DER signature structures (no trust-chain verification)")
    sp.add_argument("path")
    sp = add("repair", "Conservative repair (encoding/json/zip-cd/gzip-trailer); dry-run by default")
    sp.add_argument("path")
    sp.add_argument("--mode", required=True, choices=["mojibake-latin1", "newlines-lf", "newlines-crlf",
                                                      "add-bom", "remove-bom", "json", "zip-cd", "gzip-trailer"])
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("patch", "Author/apply reversible binary patches with precondition hashes; dry-run by default")
    sp.add_argument("path")
    sp.add_argument("--spec", required=True, help="Patch spec JSON")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("convert", "Convert to open formats (json/csv/hex/base64) with loss reports and reopen checks")
    sp.add_argument("path")
    sp.add_argument("--to", required=True, choices=["json", "csv", "hex", "base64"])
    sp.add_argument("--out")
    sp.add_argument("--approve", action="store_true")
    sp = add("fuzz", "Generate deterministic mutation fixtures; optional differential parser runs")
    sp.add_argument("path")
    sp.add_argument("--out")
    sp.add_argument("--count")
    sp.add_argument("--seed")
    sp.add_argument("--differential", action="store_true")
    sp.add_argument("--run-builtins", action="store_true")
    sp = add("external", "List/check/run allowlisted read-only external tools; dynamic tools are plan-only")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--tool")
    sp.add_argument("--run", help="Run the tool on this input file (read-only allowlist only)")
    sp.add_argument("--check", action="store_true", help="Probe tool version")
    sp = add("spec", "Author a format specification from a layout; optionally emit a safe parser")
    sp.add_argument("--layout", required=True, help="Layout name or path")
    sp.add_argument("--out", help="Write the Markdown spec here")
    sp.add_argument("--emit-parser", help="Write a generated bounds-checked Python reader here")
    sp.add_argument("--approve", action="store_true")
    return p


DISPATCH = {
    "selftest": (selftest, "cmd_selftest"), "fixtures": (selftest, "cmd_fixtures"),
    "intake": (case, "cmd_intake"), "inventory": (case, "cmd_inventory"), "custody": (case, "cmd_custody"),
    "ledger": (case, "cmd_ledger"), "authorize": (case, "cmd_authorize"), "verify": (case, "cmd_verify"),
    "survey": (case, "cmd_survey"), "plan": (case, "cmd_plan"), "report": (case, "cmd_report"),
    "preserve": (case, "cmd_preserve"), "timeline": (case, "cmd_timeline"), "compat": (case, "cmd_compat"),
    "identify": (ident, "cmd_identify"), "entropy": (ident, "cmd_entropy"), "strings": (ident, "cmd_strings"),
    "hexdump": (ident, "cmd_hexdump"), "encoding": (ident, "cmd_encoding"), "unicode": (ident, "cmd_unicode"),
    "codec": (ident, "cmd_codec"), "numbers": (ident, "cmd_numbers"), "xor": (ident, "cmd_xor"), "carve": (ident, "cmd_carve"),
    "parse": (fmt, "cmd_parse"), "sections": (fmt, "cmd_sections"), "symbols": (fmt, "cmd_symbols"),
    "disasm": (dis, "cmd_disasm"), "cfg": (dis, "cmd_cfg"), "structs": (fmt, "cmd_structs"),
    "data": (dat, "cmd_data"),
    "code": (cod, "cmd_code"), "regex": (cod, "cmd_regex"), "sql": (cod, "cmd_sql"), "build": (cod, "cmd_build"),
    "secrets": (cod, "cmd_secrets"), "triage": (cod, "cmd_triage"),
    "network": (net, "cmd_network"),
    "archive": (ops, "cmd_archive"), "compress": (ops, "cmd_compress"), "hash": (ops, "cmd_hash"),
    "diff": (ops, "cmd_diff"), "duplicates": (ops, "cmd_duplicates"), "signatures": (ops, "cmd_signatures"),
    "repair": (ops, "cmd_repair"), "patch": (ops, "cmd_patch"), "convert": (ops, "cmd_convert"),
    "fuzz": (ops, "cmd_fuzz"), "external": (ops, "cmd_external"), "spec": (ops, "cmd_spec"),
}


def cmd_capabilities(args, env, cfg):
    reg = core.load_json_asset("asset-capabilities.json")
    skills = reg["skills"]
    if args.category:
        skills = [s for s in skills if s["category"] == args.category]
    if args.search:
        q = args.search.lower()
        skills = [s for s in skills if q in s["name"].lower() or q in s["description"].lower()]
    if args.skills or args.category or args.search:
        env["result"] = {"umbrella": reg["umbrella"], "coverage_legend": reg["coverage_legend"],
                         "count": len(skills), "skills": skills}
    else:
        env["result"] = {"umbrella": reg["umbrella"], "source": reg["source"],
                         "category_count": len(reg["categories"]), "skill_count": len(reg["skills"]),
                         "coverage_legend": reg["coverage_legend"], "categories": reg["categories"],
                         "commands": sorted(DISPATCH.keys()) + ["capabilities"],
                         "note": "Use --skills to list all capabilities, --category N to filter, --search TERM to search."}
    return env


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    started = time.perf_counter()
    command = args.command
    try:
        cfg = core.load_config(args.config)
    except core.RetoolError as e:
        env, code = core.error_envelope(command, e)
        print(core.render(env, args.output_format))
        return code
    env = core.envelope(command, cfg)
    try:
        if command == "capabilities":
            env = cmd_capabilities(args, env, cfg)
        else:
            mod, fname = DISPATCH[command]
            env = getattr(mod, fname)(args, env, cfg)
        return core.finish(env, started, args.output, args.output_format)
    except core.RetoolError as e:
        err, code = core.error_envelope(command, e)
        print(core.render(err, args.output_format))
        return code
    except KeyboardInterrupt:
        err, code = core.error_envelope(command, core.RetoolError("cancelled", "Operation cancelled by user", exit_code=1))
        print(core.render(err, args.output_format))
        return code
    except Exception as e:  # structured unexpected error
        err, code = core.error_envelope(command, e)
        print(core.render(err, args.output_format))
        return code


if __name__ == "__main__":
    sys.exit(main())
