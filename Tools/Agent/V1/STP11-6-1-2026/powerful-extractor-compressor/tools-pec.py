#!/usr/bin/env python3
"""PRIMARY ENTRY POINT — powerful-extractor-compressor Skill Tool.

A single, safe, scriptable interface for extracting, creating, verifying,
organizing, recovering and packaging archives.

Every other ``tools-pec-*.py`` file is a companion module loaded automatically
from this folder; only this file needs to be executed directly.

    python3 tools-pec.py --help
    python3 tools-pec.py caps
    python3 tools-pec.py inspect payload.zip
    python3 tools-pec.py extract payload.zip --output out/

Design rules enforced here:
  * arguments are validated before any work starts
  * destructive actions need an explicit ``--approve TOKEN``
  * every command returns a machine-readable envelope with ``--json``
  * no command executes archive content or runs a shell
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load(filename: str, alias: str):
    import importlib.util
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


R = _load("tools-pec-runtime.py", "pec_runtime")
F = _load("tools-pec-formats.py", "pec_formats")
C = _load("tools-pec-core.py", "pec_core")
P = _load("tools-pec-packager.py", "pec_packager")

PecError = R.PecError
EXIT_OK, EXIT_USAGE, EXIT_POLICY = R.EXIT_OK, R.EXIT_USAGE, R.EXIT_POLICY
EXIT_INTEGRITY, EXIT_PARTIAL = R.EXIT_INTEGRITY, R.EXIT_PARTIAL
EXIT_DEPENDENCY, EXIT_APPROVAL = R.EXIT_DEPENDENCY, R.EXIT_APPROVAL
EXIT_IO, EXIT_CANCELLED = R.EXIT_IO, R.EXIT_CANCELLED

CAPABILITIES = [
    {"id": "detect", "command": "detect", "title": "Format signature detection",
     "catalog": ["Format Signature Detector", "File-Type Mismatch Detector"],
     "summary": "Identify real containers from magic bytes and structure, ignoring or exposing bad extensions."},
    {"id": "inspect", "command": "inspect", "title": "Preflight inspection and resource estimate",
     "catalog": ["Preflight Archive Inspector", "Cost and Resource Estimator", "Decompression Bomb Detector",
                 "Encrypted Archive Detector", "Missing Volume Diagnoser"],
     "summary": "Entry counts, expansion size and ratio, encryption, volumes, capacity, blocking checks."},
    {"id": "list", "command": "list", "title": "Listing and selection",
     "catalog": ["Single-Item Precision Extractor", "Selective Pattern Extractor", "Range and Portion Extractor"],
     "summary": "List entries and filter by glob, regex, extension, size, date, index range or count limit."},
    {"id": "extract", "command": "extract", "title": "Safe extraction engine",
     "catalog": ["Universal Archive Extractor", "Full-Fidelity Extractor", "Zip-Slip Defense Guard",
                 "Symlink and Hardlink Safety Guard", "Quarantine Extraction Runner", "Chunked Extraction Engine",
                 "Streaming Extractor", "Entry-by-Entry Extractor", "Checkpointed Extraction Runner",
                 "Resumable Job Controller", "Low-Disk-Space Extractor", "Archive-to-Folder Organizer",
                 "Content-Type Sorter", "Timestamp Preservation Manager", "Collision-Safe Renamer",
                 "Transactional Output Publisher", "Metadata Sanitizer", "Nested Archive Unwrapper",
                 "Split-Volume Archive Assembler", "Path Normalizer", "Reserved-Name Resolver",
                 "Least-Privilege Output Controller", "Access-Policy Enforcement Gate",
                 "Archive Lineage Mapper", "Long-Path Compatibility Handler",
                 "Cross-Platform Archive Operator", "Human Approval Gate", "Live Progress Reporter",
                 "Credential Broker for Archives", "Unicode Normalization Manager",
                 "Damaged Archive Recovery Assistant", "Partial Archive Salvager"],
     "partial": ["Sparse and Huge-File Handler (large-file-safe streaming; sparse extents are not created)",
                 "Filename Encoding Resolver (NFC normalization and invalid-character repair; no legacy code-page recovery)",
                 "Permission and Ownership Mapper (Unix modes preserved; ownership/ACL mapping not performed)",
                 "Extended Metadata Preserver (times/modes only; xattrs, ADS and resource forks are not carried)",
                 "Self-Extracting Archive Handler (inspects and reports the embedded payload; never runs it)",
                 "Embedded Object Finder (finds nested archives only, not arbitrary embedded objects)"],
     "summary": "Streamed extraction with traversal defense, ratio/count/size limits, checkpoints, nesting, "
                "atomic publication and per-file manifests."},
    {"id": "create", "command": "create", "title": "Archive creation and compression",
     "catalog": ["Universal Archive Builder", "Single-Bundle Compressor", "Multi-Volume Compressor",
                 "Encrypted Archive Creator", "Removable Media Splitter", "Compression Profile Selector",
                 "Deterministic Archive Builder", "Streaming Archive Creator", "Compression Profile Manager"],
     "partial": ["Content-Aware Compressor (extension-driven store/compress decision, no content sniffing)",
                 "Parallel Compression Engine (per-archive parallelism via `batch`, single archive is serial)"],
     "summary": "Deterministic ZIP/TAR/GZ/BZ2/XZ/(7z, Zstd when installed) archives with levels, stored-media "
                "heuristics, AES-256 encryption and volume splitting."},
    {"id": "verify", "command": "verify", "title": "Integrity verification and audit",
     "catalog": ["Checksum Verification Engine", "Archive Test Runner", "Extraction Completeness Auditor",
                 "Round-Trip Compression Verifier", "Chain-of-Custody Recorder", "Partial Success Reporter",
                 "Corruption Locator"],
     "partial": ["Corruption Locator (names the failing entry or volume via native test; no byte-offset mapping)"],
     "summary": "Native archive test, SHA-256/512/BLAKE2 hashing, manifest audits, directory inventories, "
                "round-trip checks and explicit partial-success reporting."},
    {"id": "organize", "command": "organize", "title": "Organization and lineage",
     "catalog": ["Content-Type Sorter", "Collision-Safe Renamer", "Archive-to-Folder Organizer",
                 "Folder Structure Rebuilder", "Manifest and Inventory Builder", "Batch Folder Planner"],
     "summary": "Sort a tree by content type, rename collisions deterministically, rebuild manifests."},
    {"id": "scan", "command": "scan", "title": "Safety scanning and sanitation",
     "catalog": ["Secret and Sensitive-Data Scanner", "File-Type Mismatch Detector", "Metadata Sanitizer",
                 "Policy-Aware Include Exclude Engine"],
     "summary": "Detect credentials and PII, flag disguised file types, strip platform artifacts, set epochs."},
    {"id": "package", "command": "package", "title": "Packaging recipes",
     "catalog": ["Source Release Packager", "Project Handoff Packager", "Evidence Collection Packager",
                 "Dataset Delivery Packager", "Media Delivery Packager", "Update Patch Packager",
                 "Redacted Sharing Packager", "Compliance Evidence Packager", "Retention Policy Packager",
                 "Separate-Zip Compressor", "Single-Bundle Compressor", "License and Notice Collector",
                 "Job Template Manager", "Media Asset Extractor", "Incremental Backup Compressor",
                 "Retention Policy Packager"],
     "summary": "Eleven declarative recipes (release, handoff, evidence, dataset, media, patch, redacted, "
                "compliance, retention, separate-zip, single-bundle) with manifests."},
    {"id": "batch", "command": "batch", "title": "Batch and parallel extraction",
     "catalog": ["Multi-Archive Batch Extractor", "Parallel Extraction Scheduler", "Power Extraction Orchestrator",
                 "Duplicate-Work Preventer", "Failure Reproduction Packager", "Batch Folder Planner"],
     "summary": "Extract many archives with bounded workers, per-job logs, retries, duplicate-work skipping and "
                "a final summary."},
    {"id": "jobs", "command": "jobs", "title": "Job records, resume and cleanup",
     "catalog": ["Resumable Job Controller", "Temporary-State Recovery Manager", "Job Summary Report Generator",
                 "Retry Policy Engine", "Archive Disposal Workflow", "Secure Deletion Coordinator"],
     "summary": "Inspect recorded jobs, re-run/resume a job from its stored parameters, purge state with approval."},
    {"id": "config", "command": "config", "title": "Configuration and backend routing",
     "catalog": ["Configuration Validator", "Tool Capability Router", "Filesystem Capability Profiler",
                 "Adaptive Resource Governor"],
     "summary": "Show/validate/emit configuration, report backend availability and filesystem capabilities."},
    {"id": "selftest", "command": "selftest", "title": "Built-in verification battery",
     "catalog": ["Security Regression Test Suite", "Boundary and Stress Test Runner",
                 "Golden Manifest Regression Checker", "Clean-State Reproducibility Tester",
                 "Performance Regression Benchmark"],
     "summary": "Runs 28 end-to-end cases covering safety, integrity, recovery, packaging and validation."},
]


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
class Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401 - argparse integration
        raise PecError(f"{message}\n\n{self.format_usage().strip()}", EXIT_USAGE, "usage")


def add_global_overrides(parser: argparse.ArgumentParser) -> None:
    """Accept the global flags after the subcommand too (same dest, SUPPRESS default)."""
    parser.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS,
                        help="JSON configuration file merged over defaults")
    parser.add_argument("--state", metavar="DIR", default=argparse.SUPPRESS,
                        help="state directory for jobs, logs, checkpoints")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit the structured result envelope")
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="suppress progress and notice output")
    parser.add_argument("--approve", action="append", default=argparse.SUPPRESS, metavar="ACTION",
                        help="grant a gated action: DELETE, OVERWRITE, DISPOSE, DECRYPT, PUBLISH-ENCRYPTED")
    parser.add_argument("--approval-file", metavar="PATH", default=argparse.SUPPRESS,
                        help="JSON file with reviewer approval records")
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="plan only; write nothing")
    parser.add_argument("--deadline", metavar="SECONDS", default=argparse.SUPPRESS,
                        help="wall-clock budget for the command")


def add_selection(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("selection filters")
    group.add_argument("--include", action="append", metavar="GLOB", help="include entries matching a glob")
    group.add_argument("--exclude", action="append", metavar="GLOB", help="exclude entries matching a glob")
    group.add_argument("--regex", default="", metavar="RE", help="include entries matching a regular expression")
    group.add_argument("--ext", action="append", metavar=".EXT", help="include these extensions only")
    group.add_argument("--min-size", metavar="SIZE", help="minimum entry size (e.g. 10K, 4MiB)")
    group.add_argument("--max-size", metavar="SIZE", help="maximum entry size")
    group.add_argument("--since", metavar="YYYY-MM-DD", help="entries modified on/after this date")
    group.add_argument("--until", metavar="YYYY-MM-DD", help="entries modified on/before this date")
    group.add_argument("--index-range", metavar="LOW:HIGH", help="1-based inclusive entry range")
    group.add_argument("--limit", type=int, metavar="N", help="stop after N matching entries")


def add_secret_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("archive password (never logged)")
    group.add_argument("--password-env", metavar="VAR", default=None,
                       help="read the password from an environment variable")
    group.add_argument("--password-file", metavar="PATH", default=None,
                       help="read the password from the first line of a file")


def build_parser() -> Parser:
    parser = Parser(prog="tools-pec.py", description="Safe extraction, compression and packaging Skill Tool.",
                    epilog="Run 'python3 tools-pec.py caps' for the capability list and "
                           "'python3 tools-pec.py <command> --help' for command options.",
                    formatter_class=argparse.RawDescriptionHelpFormatter, add_help=True)
    parser.add_argument("--version", action="version", version=f"powerful-extractor-compressor {R.VERSION}")
    parser.add_argument("--config", metavar="PATH", help="JSON configuration file merged over defaults")
    parser.add_argument("--state", metavar="DIR", help="state directory for jobs, logs, checkpoints")
    parser.add_argument("--json", action="store_true", help="emit the structured result envelope")
    parser.add_argument("--quiet", action="store_true", help="suppress progress and notice output")
    parser.add_argument("--approve", action="append", default=[], metavar="ACTION",
                        help="grant a gated action: DELETE, OVERWRITE, DISPOSE, DECRYPT, PUBLISH-ENCRYPTED")
    parser.add_argument("--approval-file", metavar="PATH", help="JSON file with reviewer approval records")
    parser.add_argument("--dry-run", action="store_true", help="plan only; write nothing")
    parser.add_argument("--deadline", metavar="SECONDS", help="wall-clock budget for the command")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    caps = sub.add_parser("caps", help="list implemented capabilities")
    caps.add_argument("--filter", default="", metavar="TEXT", help="only show matches")
    caps.add_argument("--backends", action="store_true", help="also print backend availability")

    coverage = sub.add_parser("coverage", help="map catalog skills to this tool's commands")
    coverage.add_argument("--state-filter", choices=["implemented", "partial", "out-of-scope"],
                          default="", metavar="STATE", help="show only one status")
    coverage.add_argument("--verbose", action="store_true", help="show notes for implemented skills too")

    detect = sub.add_parser("detect", help="identify real container types from content")
    detect.add_argument("paths", nargs="+", metavar="PATH")

    inspect = sub.add_parser("inspect", help="preflight an archive before extracting")
    inspect.add_argument("paths", nargs="+", metavar="PATH")
    add_secret_options(inspect)

    lst = sub.add_parser("list", help="list archive entries")
    lst.add_argument("paths", nargs="+", metavar="PATH")
    add_selection(lst)
    lst.add_argument("--long", action="store_true", help="show sizes, timestamps and modes")
    add_secret_options(lst)

    extract = sub.add_parser("extract", help="safely extract archives")
    extract.add_argument("paths", nargs="+", metavar="PATH")
    extract.add_argument("--output", "-o", required=True, metavar="DIR", help="destination root")
    add_selection(extract)
    add_secret_options(extract)
    extract.add_argument("--merge", action="store_true", help="extract every source into one destination")
    extract.add_argument("--nest", action="store_true", help="recursively unwrap nested archives")
    extract.add_argument("--max-depth", type=int, default=None, metavar="N", help="nesting depth limit")
    extract.add_argument("--quarantine", action="store_true", help="restrictive permissions, non-executable")
    extract.add_argument("--strip-metadata", action="store_true", help="drop OS metadata files")
    extract.add_argument("--organize", choices=["none", "by-content-type"], default=None)
    extract.add_argument("--publish", choices=["atomic", "direct"], default=None,
                         help="atomic stages then renames; direct writes in place")
    extract.add_argument("--overwrite", choices=["never", "suffix", "replace"], default=None)
    extract.add_argument("--no-scan", action="store_true", help="skip the secret scan")
    extract.add_argument("--no-checkpoint", action="store_true", help="disable checkpoint records")
    extract.add_argument("--resume", action="store_true", help="resume an interrupted job with the same --job-key")
    extract.add_argument("--job-key", default="", metavar="KEY", help="stable key for resume/duplicate detection")
    extract.add_argument("--no-assemble", action="store_true", help="do not assemble numeric volume sets")

    create = sub.add_parser("create", help="create archives")
    create.add_argument("paths", nargs="+", metavar="PATH")
    create.add_argument("--output", "-o", required=True, metavar="FILE", help="archive path to create")
    create.add_argument("--format", default=None,
                        choices=sorted(F.CREATE_FORMATS), help="output format (default zip)")
    create.add_argument("--level", type=int, default=None, metavar="0-9", help="compression level")
    create.add_argument("--root-folder", default=None, metavar="NAME", help="wrap content in this top folder")
    create.add_argument("--volume-size", default=None, metavar="SIZE", help="split into volumes of this size")
    create.add_argument("--no-deterministic", action="store_true", help="use real timestamps and ordering")
    create.add_argument("--encrypt", action="store_true", help="AES-256 encrypt the archive (requires pyzipper)")
    create.add_argument("--verify", action="store_true", help="native-test and round-trip the new archive")
    add_secret_options(create)

    verify = sub.add_parser("verify", help="verify archives, manifests and directories")
    verify.add_argument("paths", nargs="*", metavar="PATH")
    verify.add_argument("--manifest", metavar="PATH", help="manifest to audit against")
    verify.add_argument("--dir", dest="directory", metavar="DIR", help="root that the manifest describes")
    verify.add_argument("--hash-alg", default=None, metavar="ALG", help="sha256 | sha512 | blake2b | sha1 | md5")
    verify.add_argument("--expect", metavar="DIGEST", help="expected digest for a file")
    verify.add_argument("--test", action="store_true", help="run the format-native integrity test")
    verify.add_argument("--round-trip", action="store_true", help="extract and re-hash the archive content")
    verify.add_argument("--write-manifest", metavar="PATH", help="write an inventory manifest for a directory")
    verify.add_argument("--depth", type=int, default=0, metavar="N", help="directory recursion depth (0 = all)")
    add_secret_options(verify)

    organize = sub.add_parser("organize", help="organize an extracted tree")
    organize.add_argument("path", metavar="DIR")
    organize.add_argument("--output", "-o", default=None, metavar="DIR",
                          help="destination (defaults to in-place)")
    organize.add_argument("--mode", choices=["by-content-type"], default="by-content-type")
    organize.add_argument("--collision", choices=["suffix", "hash"], default="suffix")
    organize.add_argument("--strip-metadata", action="store_true")
    organize.add_argument("--epoch", metavar="YYYY-MM-DD", help="normalize modification times")

    scan = sub.add_parser("scan", help="scan files for secrets, PII and disguised types")
    scan.add_argument("paths", nargs="+", metavar="PATH")
    scan.add_argument("--no-secrets", action="store_true", help="skip secret/PII pattern scanning")
    scan.add_argument("--no-mismatch", action="store_true", help="skip file-type mismatch checks")
    scan.add_argument("--min-confidence", choices=["low", "medium", "high"], default=None)
    scan.add_argument("--gate", choices=["warn", "block"], default=None,
                      help="block exits 3 when findings exist")
    scan.add_argument("--strip-metadata", action="store_true", help="remove junk files in place")
    scan.add_argument("--epoch", metavar="YYYY-MM-DD", help="normalize modification times when stripping")

    package = sub.add_parser("package", help="run a packaging recipe")
    package.add_argument("recipe", nargs="?", metavar="RECIPE")
    package.add_argument("sources", nargs="*", metavar="SOURCE")
    package.add_argument("--output", "-o", metavar="PATH", help="destination directory or archive path")
    package.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                         help="recipe option override (repeatable)")
    package.add_argument("--list", action="store_true", help="list available recipes")
    package.add_argument("--show", metavar="RECIPE", help="show one recipe's options")

    batch = sub.add_parser("batch", help="extract many archives with bounded parallelism")
    batch.add_argument("paths", nargs="+", metavar="PATH", help="archives, directories or glob patterns")
    batch.add_argument("--output", "-o", required=True, metavar="DIR")
    batch.add_argument("--workers", type=int, default=None, metavar="N", help="parallel workers")
    batch.add_argument("--nest", action="store_true")
    batch.add_argument("--quarantine", action="store_true")
    batch.add_argument("--no-scan", action="store_true")
    batch.add_argument("--fail-fast", action="store_true", help="stop at the first failed archive")
    batch.add_argument("--skip-duplicates", action="store_true",
                       help="skip archives whose fingerprint already succeeded in this state dir")
    add_secret_options(batch)

    jobs = sub.add_parser("jobs", help="inspect, resume or clean job state")
    jobs.add_argument("action", choices=["list", "show", "resume", "clean"], nargs="?", default="list")
    jobs.add_argument("--job-id", default="", metavar="ID")
    jobs.add_argument("--limit", type=int, default=20, metavar="N")
    jobs.add_argument("--older-than", default="", metavar="DAYS",
                      help="clean jobs/logs/checkpoints older than N days (default 30)")

    config = sub.add_parser("config", help="show, validate or write configuration")
    config.add_argument("action", choices=["show", "validate", "init", "backends", "fs"], nargs="?",
                        default="show")
    config.add_argument("--output", "-o", metavar="PATH", help="path to write for 'init'")
    config.add_argument("--path", metavar="DIR", help="filesystem path to profile for 'fs'")

    selftest = sub.add_parser("selftest", help="run the built-in test battery")
    selftest.add_argument("--work", metavar="DIR", help="scratch directory (defaults to state/selftest)")
    selftest.add_argument("--keep", action="store_true", help="keep fixtures for inspection")
    selftest.add_argument("--only", action="append", metavar="CASE", help="run only these cases")

    for action in sub.choices.values():
        add_global_overrides(action)
    return parser


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def read_password(args, config) -> str | None:
    env_name = getattr(args, "password_env", None)
    file_path = getattr(args, "password_file", None)
    if env_name:
        value = os.environ.get(env_name)
        if not value:
            raise PecError(f"environment variable {env_name} is empty or unset", EXIT_USAGE)
        return value
    if file_path:
        if not os.path.exists(file_path):
            raise PecError(f"password file not found: {file_path}", EXIT_USAGE)
        with open(file_path, "r", encoding="utf-8") as fh:
            value = fh.readline().rstrip("\r\n")
        if not value:
            raise PecError("password file is empty", EXIT_USAGE)
        return value
    default_env = str(config.get("extraction.default_password_env", "PEC_ARCHIVE_PASSWORD"))
    return os.environ.get(default_env) or None


def build_selector(args) -> C.Selector:
    index_range = None
    if getattr(args, "index_range", None):
        raw = args.index_range
        try:
            low, high = raw.split(":", 1)
            index_range = (int(low), int(high))
        except ValueError as exc:
            raise PecError(f"--index-range must look like LOW:HIGH, got {raw!r}", EXIT_USAGE) from exc
        if index_range[0] < 1 or index_range[1] < index_range[0]:
            raise PecError("--index-range requires 1 <= LOW <= HIGH", EXIT_USAGE)
    selector = C.Selector(
        include=list(getattr(args, "include", None) or []),
        exclude=list(getattr(args, "exclude", None) or []),
        regex=getattr(args, "regex", "") or "",
        extensions=list(getattr(args, "ext", None) or []),
        min_size=R.parse_size(args.min_size) if getattr(args, "min_size", None) else None,
        max_size=R.parse_size(args.max_size) if getattr(args, "max_size", None) else None,
        since=C.parse_date(args.since) if getattr(args, "since", None) else None,
        until=C.parse_date(args.until) if getattr(args, "until", None) else None,
        index_range=index_range,
        limit=getattr(args, "limit", None),
    )
    if selector.regex:
        try:
            import re as _re
            _re.compile(selector.regex)
        except Exception as exc:  # noqa: BLE001
            raise PecError(f"invalid --regex {selector.regex!r}: {exc}", EXIT_USAGE) from exc
    if selector.limit is not None and selector.limit < 1:
        raise PecError("--limit must be >= 1", EXIT_USAGE)
    return selector


def expand_sources(patterns: list[str], config) -> list[str]:
    """Expand directories, globs and explicit paths into archive candidates."""
    found: list[str] = []
    for pattern in patterns:
        if os.path.isdir(pattern):
            for dirpath, dirnames, filenames in os.walk(pattern):
                dirnames.sort()
                for name in sorted(filenames):
                    found.append(os.path.join(dirpath, name))
            continue
        if os.path.exists(pattern):
            found.append(pattern)
            continue
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches:
            raise PecError(f"no files matched {pattern!r}", EXIT_USAGE)
        found.extend(matches)
    unique: list[str] = []
    seen: set[str] = set()
    for path in found:
        key = os.path.abspath(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def archive_candidates(paths: list[str], config) -> list[str]:
    """Keep only files whose content is a supported archive container."""
    out = []
    for path in paths:
        try:
            info = F.detect_file(path)
        except PecError:
            continue
        if info["id"] in F.ARCHIVE_KINDS or info["id"] in {"gzip", "bzip2", "xz", "zstd", "lz4"}:
            out.append(path)
    return out


def describe_entry(entry, long: bool) -> str:
    if not long:
        return entry.name
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.mtime)) if entry.mtime else "-"
    mode = oct(entry.mode & 0o777) if entry.mode else "-"
    extra = " (encrypted)" if entry.encrypted else ""
    link = f" -> {entry.link_target.split('->', 1)[1]}" if entry.link_target and "->" in entry.link_target else ""
    return (f"{entry.name}{link}  [{R.human_bytes(entry.size)}] {stamp} {mode}{extra}")


def password_hint(config, info: dict) -> None:
    if info.get("encrypted_entries"):
        env = config.get("extraction.default_password_env", "PEC_ARCHIVE_PASSWORD")
        info["password_hint"] = f"encrypted entries present; set {env} or pass --password-env/--password-file"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_caps(args, ctx):
    caps = CAPABILITIES
    if args.filter:
        needle = args.filter.lower()
        caps = [c for c in caps if needle in json.dumps(c).lower()]
    payload = {
        "skill": R.SKILL_NAME,
        "version": R.VERSION,
        "command_count": len(CAPABILITIES),
        "capabilities": caps,
        "catalog_skills_covered": sum(len(c["catalog"]) for c in CAPABILITIES),
    }
    if args.backends:
        payload["backends"] = F.backend_status(ctx["config"])
    if ctx.get("json"):
        R.emit(payload, True)
        return EXIT_OK, payload, ""
    lines = [f"{R.SKILL_NAME} {R.VERSION}", "", "Implemented capabilities:", ""]
    for cap in caps:
        lines.append(f"  {cap['command']:<10} {cap['title']}")
        lines.append(f"             {cap['summary']}")
        lines.append(f"             catalog: {', '.join(cap['catalog'])}")
        for note in cap.get("partial", []):
            lines.append(f"             partial: {note}")
        lines.append("")
    if args.backends:
        lines.append("Backend availability:")
        for name, available in sorted(payload["backends"].items()):
            lines.append(f"  {'OK  ' if available else 'MISS'} {name}")
        lines.append("")
    lines.append("Use: python3 tools-pec.py <command> --help")
    return EXIT_OK, payload, "\n".join(lines)


def cmd_coverage(args, ctx):
    """Report which catalog skills this tool implements, partially, or not at all."""
    data = R.load_asset("asset-catalog-coverage.json")
    assert data is not None
    def bucket(row):
        if row["status"] != "implemented":
            return "out-of-scope"
        return "partial" if row.get("scope") == "partial" else "implemented"

    state = args.state_filter.lower() if args.state_filter else ""
    rows = [row for row in data["skills"] if not state or bucket(row) == state]
    counts: dict[str, int] = {"implemented": 0, "partial": 0, "out-of-scope": 0}
    for row in data["skills"]:
        counts[bucket(row)] += 1
    payload = {"coverage": data, "counts": counts, "filtered": rows, "total": len(data["skills"])}
    if ctx.get("json"):
        R.emit(payload, True)
        return EXIT_OK, payload, ""
    lines = [f"Catalog coverage: {counts['implemented']} implemented, "
             f"{counts['partial']} partial, {counts['out-of-scope']} out of scope "
             f"(of {len(data['skills'])} catalog skills)", ""]
    current = None
    for row in rows:
        if row["category"] != current:
            current = row["category"]
            lines.append(f"[{current}]")
        label = bucket(row)
        command = f" -> {row['command']}" if row.get("command") else ""
        lines.append(f"  {label:<12} {row['skill']}{command}")
        if row.get("note") and (args.verbose or label != "implemented"):
            lines.append(f"               {row['note']}")
    return EXIT_OK, payload, "\n".join(lines)


def cmd_detect(args, ctx):
    results = []
    errors = []
    for path in args.paths:
        try:
            results.append(F.detect_file(path))
        except PecError as exc:
            errors.append({"path": path, **exc.as_dict()})
    payload = {"detections": results, "errors": errors, "count": len(results)}
    if ctx["json"]:
        R.emit(payload, True)
        return (EXIT_OK if all(r["id"] != "unknown" for r in results) else EXIT_PARTIAL), payload, ""
    lines = [R.table(
        [[os.path.basename(r["path"]), r["id"], r["kind"], R.human_bytes(r.get("size", 0)),
          "yes" if r.get("extension_match") else "NO"] for r in results],
        ["file", "detected", "kind", "size", "ext matches"])]
    for r in results:
        if r.get("notes"):
            lines.append(f"  {os.path.basename(r['path'])}: {r['notes']}")
    for err in errors:
        lines.append(f"  ERROR {err['path']}: {err['message']}")
    if errors:
        code = EXIT_USAGE
    elif all(r["id"] != "unknown" for r in results):
        code = EXIT_OK
    else:
        code = EXIT_PARTIAL
    return code, payload, "\n".join(lines)


def cmd_inspect(args, ctx):
    config = ctx["config"]
    password = read_password(args, config)
    reports = []
    for path in args.paths:
        report = C.preflight(path, config, password)
        password_hint(config, report.get("type", {}) or {})
        reports.append(report)
    blocking = [r for r in reports if r.get("blocking")]
    payload = {"reports": reports, "safe_to_extract": all(r.get("safe_to_extract") for r in reports)}
    if ctx["json"]:
        R.emit(payload, True)
        return (EXIT_POLICY if blocking else EXIT_OK), payload, ""
    lines = []
    for report in reports:
        lines.append(f"{os.path.basename(report['source'])}  ({report.get('type', {}).get('id', '?')})")
        if "error" in report:
            lines.append(f"  ERROR: {report['error']['message']}")
        lines.append(f"  entries {report.get('entries')}  files {report.get('files')}  dirs {report.get('directories')}")
        lines.append(f"  compressed {report.get('compressed_human')}  expanded {report.get('expanded_human')}  "
                     f"ratio {report.get('ratio')}")
        if report.get("encrypted_entries"):
            lines.append(f"  encrypted entries: {report['encrypted_entries']}")
        if report.get("volumes", {}).get("multipart"):
            lines.append(f"  volumes: {report['volumes'].get('count')} complete={report['volumes'].get('complete')}")
        for entry in report.get("largest", [])[:3]:
            lines.append(f"  largest: {entry['name']} ({R.human_bytes(entry['size'])})")
        for warn in report.get("warnings", []):
            lines.append(f"  WARN {warn}")
        for block in report.get("blocking", []):
            lines.append(f"  BLOCK {block['check']}: {block['detail']}")
        lines.append(f"  safe to extract: {'yes' if report.get('safe_to_extract') else 'NO'}")
        lines.append("")
    return (EXIT_POLICY if blocking else EXIT_OK), payload, "\n".join(lines)


def cmd_list(args, ctx):
    config = ctx["config"]
    password = read_password(args, config)
    selector = build_selector(args)
    rows = []
    results = []
    for path in args.paths:
        entries, meta = F.list_entries(path, config, password)
        matched = [(i, e) for i, e in enumerate(entries, 1) if selector.matches(e, i)]
        if selector.limit:
            matched = matched[: selector.limit]
        results.append({
            "source": os.path.abspath(path),
            "container": meta.get("id"),
            "entries_total": len(entries),
            "entries_matched": len(matched),
            "entries": [{"index": i, **e.as_dict()} for i, e in matched],
        })
        for index, entry in matched:
            rows.append((os.path.basename(path), index, entry))
    payload = {"listings": results}
    if ctx["json"]:
        R.emit(payload, True)
        return EXIT_OK, payload, ""
    lines = []
    for result in results:
        lines.append(f"{os.path.basename(result['source'])}: {result['entries_total']} entries, "
                     f"{result['entries_matched']} matched")
        subset = [(i, e) for (n, i, e) in rows if n == os.path.basename(result["source"])]
        for index, entry in subset[:500]:
            lines.append(f"  {index:>5}  {describe_entry(entry, args.long)}")
        if len(subset) > 500:
            lines.append(f"  … {len(subset) - 500} more (use --json or tighten filters)")
        lines.append("")
    return EXIT_OK, payload, "\n".join(lines)


def cmd_extract(args, ctx):
    config = ctx["config"]
    logger, gate, cancel = ctx["logger"], ctx["gate"], ctx["cancel"]
    password = read_password(args, config)
    selector = build_selector(args)
    if args.merge and len(args.paths) > 1:
        raise PecError("--merge accepts multiple sources; each is extracted into the same destination",
                       EXIT_USAGE)
    publish = args.publish or ("direct" if args.resume else config.get("extraction.publish", "atomic"))
    overwrite = args.overwrite or config.get("policy.overwrite", "never")
    if args.dry_run:
        plans = []
        for path in args.paths:
            report = C.preflight(path, config, password)
            plans.append({"source": path, "destination": args.output, "preflight": report})
        payload = {"dry_run": True, "plans": plans}
        human = "\n".join(f"{p['source']}: {p['preflight'].get('files')} files, "
                          f"{p['preflight'].get('expanded_human')} -> {args.output}" for p in plans)
        return (EXIT_POLICY if any(p["preflight"].get("blocking") for p in plans) else EXIT_OK), payload, human
    results = []
    exit_code = EXIT_OK
    for index, path in enumerate(args.paths, start=1):
        if args.merge:
            destination = args.output
        elif len(args.paths) == 1:
            destination = args.output
        else:
            stem = os.path.splitext(os.path.basename(path))[0] or f"archive-{index}"
            destination = os.path.join(args.output, stem)
        options = C.ExtractOptions(
            destination=destination,
            selector=selector,
            nest=args.nest,
            max_depth=args.max_depth or 0,
            quarantine=args.quarantine,
            strip_metadata=args.strip_metadata,
            organize=args.organize or config.get("extraction.organize", "none"),
            publish=publish,
            checkpoint=not args.no_checkpoint,
            resume=args.resume,
            password=password,
            scan=not args.no_scan,
            assemble=not args.no_assemble,
            overwrite=overwrite,
            job_key=args.job_key,
        )
        job = C.ExtractionJob(path, options, config, logger, cancel, gate, job_id=ctx["job"].job_id)
        result = job.run()
        result["source"] = os.path.abspath(path)
        results.append(result)
        if result["status"] == "failed":
            exit_code = EXIT_INTEGRITY if any(e.get("kind") == "encryption" for e in result["errors"]) else EXIT_PARTIAL
        elif result["status"] == "partial" and exit_code == EXIT_OK:
            exit_code = EXIT_PARTIAL
    payload = {"extractions": results}
    if len(results) == 1:
        payload.update(results[0])
    if ctx["json"]:
        R.emit(payload, True)
        return exit_code, payload, ""
    lines = []
    for result in results:
        lines.append(f"{os.path.basename(result['source'])} -> {result['destination']}")
        lines.append(f"  status {result['status']}  written {result['entries_written']}  "
                     f"skipped {result['entries_skipped']}  failed {result['entries_failed']}  "
                     f"bytes {result['bytes_written_human']}  {result['seconds']}s")
        lines.append(f"  manifest {result['manifest']}")
        for warn in result["warnings"][:8]:
            lines.append(f"  WARN {warn}")
        for err in result["errors"][:8]:
            lines.append(f"  FAIL {err.get('name')}: {err.get('message')}")
    return exit_code, payload, "\n".join(lines)


def cmd_create(args, ctx):
    config = ctx["config"]
    gate = ctx["gate"]
    password = read_password(args, config)
    fmt = args.format or config.get("compression.format", "zip")
    level = args.level if args.level is not None else int(config.get("compression.level", 6))
    if level < 0 or level > 9:
        raise PecError("--level must be between 0 and 9", EXIT_USAGE)
    deterministic = not args.no_deterministic and bool(config.get("compression.deterministic", True))
    sources = expand_sources(args.paths, config)
    if not sources:
        raise PecError("no input files matched", EXIT_USAGE)
    volume_size = R.parse_size(args.volume_size) if args.volume_size else int(
        config.get("limits.max_volume_size", 0) or 0)
    if volume_size and volume_size < 65536:
        raise PecError(
            f"--volume-size must be 0 or at least 65536 bytes (got {volume_size}); "
            "smaller volumes are not useful for real archives", EXIT_USAGE)
    if args.dry_run:
        members = F._collect_members(sources, args.root_folder, deterministic)
        total = sum(m.get("size", 0) for m in members)
        payload = {"dry_run": True, "format": fmt, "members": len(members), "input_bytes": total,
                   "output": args.output, "volume_size": volume_size}
        return EXIT_OK, payload, (f"would create {args.output} ({fmt}, {len(members)} files, "
                                  f"{R.human_bytes(total)})")
    if os.path.exists(args.output):
        gate.require("overwrite", detail=f"replacing {args.output}")
        os.unlink(args.output)
    if args.encrypt:
        if fmt not in {"zip", "zip64"}:
            raise PecError("--encrypt currently supports ZIP output only (AES-256)", EXIT_USAGE)
        if not password:
            raise PecError("--encrypt needs a password via --password-env or --password-file", EXIT_USAGE)
        members = F._collect_members(sources, args.root_folder, deterministic)
        pairs = [(m["fs"], m["arc"]) for m in members if not m.get("dir")]
        info = F.create_encrypted_archive(pairs, args.output, password, config, level=level)
        gate.require("publish-encrypted", detail=f"encrypted archive {args.output}")
    else:
        info = F.create_archive(sources, args.output, config, fmt=fmt, level=level,
                                root_folder=args.root_folder, deterministic=deterministic,
                                store_extensions=config.get("compression.store_extensions", []),
                                cancellation=ctx["cancel"])
    volumes = None
    if volume_size:
        volumes = F.split_volumes(args.output, volume_size)
        if len(volumes["volumes"]) > 1:
            anchor = volumes["volumes"][0]["path"]
            os.replace(args.output, anchor)
            volumes["anchor"] = anchor
            volumes["volumes"][0]["path"] = anchor
        else:
            volumes["note"] = ("archive is smaller than one volume; it was kept whole and "
                               f"one .001 copy written ({volumes['count']} volume file)")
    verification = None
    if args.verify and os.path.exists(args.output):
        native = F.native_test(args.output, config)
        round_trip = C.round_trip_check(args.output, config,
                                        os.path.join(config.state_dir(), "tmp", "verify"))
        verification = {"native_test": native, "round_trip": round_trip}
    payload = {"created": info, "volumes": volumes, "verification": verification}
    if verification and not (verification["native_test"]["ok"] and verification["round_trip"]["ok"]):
        exit_code = EXIT_INTEGRITY
    else:
        exit_code = EXIT_OK
    if ctx["json"]:
        R.emit(payload, True)
        return exit_code, payload, ""
    lines = [f"created {info['output']} ({info['format']}, {info.get('members', 0)} files, "
             f"{R.human_bytes(info.get('input_bytes', 0))} -> {R.human_bytes(info.get('output_bytes', 0))}, "
             f"ratio {info.get('ratio', 0)}x)"]
    lines.append(f"  sha256 {info['sha256']}")
    if volumes:
        if volumes.get("note"):
            lines.append(f"  WARN {volumes['note']}")
        else:
            lines.append(f"  split into {volumes['count']} volumes of {R.human_bytes(volumes['volume_size'])}")
            lines.append(f"  set anchor (pass this path to extract): {volumes['anchor']}")
    if verification:
        lines.append(f"  native test: {'ok' if verification['native_test']['ok'] else 'FAILED'}")
        lines.append(f"  round trip: {'ok' if verification['round_trip']['ok'] else 'FAILED'}")
    if info.get("warning"):
        lines.append(f"  WARN {info['warning']}")
    return exit_code, payload, "\n".join(lines)


def cmd_verify(args, ctx):
    config = ctx["config"]
    if not (args.paths or args.manifest or args.write_manifest):
        raise PecError("verify needs at least one PATH, --manifest, or --write-manifest", EXIT_USAGE)
    if args.write_manifest and not any(os.path.isdir(p) for p in args.paths):
        raise PecError("--write-manifest needs a directory PATH", EXIT_USAGE)
    password = read_password(args, config)
    algorithm = args.hash_alg or config.get("extraction.hash_algorithm", "sha256")
    payload: dict = {"algorithm": algorithm}
    exit_code = EXIT_OK
    messages: list[str] = []
    if args.manifest:
        audit = C.verify_manifest(args.manifest, args.directory, config)
        payload["manifest_audit"] = audit
        if not audit["ok"]:
            exit_code = EXIT_INTEGRITY
        messages.append(f"manifest audit: {audit['matched']}/{audit['entries_checked']} matched, "
                        f"{len(audit['missing'])} missing, {len(audit['mismatched'])} mismatched, "
                        f"{len(audit['unexpected'])} unexpected")
    files = []
    for path in args.paths:
        if os.path.isdir(path):
            if args.write_manifest:
                inventory = build_inventory(path, config, args.depth)
                R.write_json_atomic(args.write_manifest, inventory)
                payload["inventory_written"] = os.path.abspath(args.write_manifest)
                messages.append(f"wrote inventory manifest for {inventory['count']} files -> {args.write_manifest}")
            else:
                inventory = build_inventory(path, config, args.depth)
                payload.setdefault("directories", []).append(inventory)
                messages.append(f"{path}: {inventory['count']} files, {R.human_bytes(inventory['bytes'])}")
            continue
        info = {"path": os.path.abspath(path), "size": os.path.getsize(path)}
        digest = C.hash_file(path, algorithm)
        info["digest"] = digest["digest"]
        if args.expect:
            info["expected"] = args.expect
            info["match"] = digest["digest"] == args.expect.strip().lower()
            if not info["match"]:
                exit_code = EXIT_INTEGRITY
        if args.test:
            info["native_test"] = F.native_test(path, config)
            if info["native_test"].get("ok") is False:
                exit_code = EXIT_INTEGRITY
        if args.round_trip:
            info["round_trip"] = C.round_trip_check(path, config,
                                                    os.path.join(config.state_dir(), "tmp", "roundtrip"),
                                                    password)
            if not info["round_trip"]["ok"]:
                exit_code = EXIT_INTEGRITY
        files.append(info)
        messages.append(f"{os.path.basename(path)}: {algorithm} {digest['digest']}")
        if info.get("native_test"):
            messages.append(f"  native test: {'ok' if info['native_test']['ok'] else 'FAILED'}")
        if info.get("round_trip"):
            messages.append(f"  round trip: {'ok' if info['round_trip']['ok'] else 'FAILED'}")
        if args.expect:
            messages.append(f"  expected {args.expect}: {'match' if info['match'] else 'MISMATCH'}")
    payload["files"] = files
    payload["ok"] = exit_code == EXIT_OK
    if ctx["json"]:
        R.emit(payload, True)
        return exit_code, payload, ""
    return exit_code, payload, "\n".join(messages)


def build_inventory(root: str, config, depth: int = 0) -> dict:
    root_abs = os.path.abspath(root)
    entries = []
    total = 0
    base_depth = root_abs.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root_abs):
        if depth:
            current = os.path.relpath(dirpath, root_abs).count(os.sep) + (0 if dirpath != root_abs else 0)
            if dirpath != root_abs and current >= depth:
                dirnames[:] = []
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root_abs).replace(os.sep, "/")
            try:
                info = C.hash_file(full, "sha256")
            except OSError:
                continue
            entries.append({"path": rel, "size": info["bytes"], "sha256": info["digest"]})
            total += info["bytes"]
    return {
        "schema": R.MANIFEST_SCHEMA,
        "kind": "inventory",
        "created_at": R.utcnow(),
        "root": root_abs,
        "count": len(entries),
        "bytes": total,
        "entries": entries,
    }


def cmd_organize(args, ctx):
    config = ctx["config"]
    source = args.path
    if not os.path.isdir(source):
        raise PecError(f"not a directory: {source}", EXIT_USAGE)
    destination = args.output or source
    mapping = R.load_asset(str(config.get("content_types_file", "asset-content-types.json")))
    if args.dry_run:
        plan = []
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames.sort()
            for name in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, name), source).replace(os.sep, "/")
                plan.append({"path": rel, "category": C.classify_path(rel, mapping)})
        payload = {"dry_run": True, "files": len(plan), "plan": plan[:1000]}
        human = "\n".join(f"{p['path']} -> {p['category']}" for p in plan[:50])
        return EXIT_OK, payload, human
    moved = 0
    collisions = 0
    for dirpath, dirnames, filenames in os.walk(source, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(source):
            continue
        for name in filenames:
            if name in {"pec-manifest.json", "pec-manifest.md"}:
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), source).replace(os.sep, "/")
            category = C.classify_path(rel, mapping)
            target_dir = os.path.join(destination, category)
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, name)
            if args.collision == "hash" and os.path.exists(target):
                digest = C.hash_file(os.path.join(dirpath, name), "sha256")["digest"][:8]
                stem, ext = os.path.splitext(name)
                target = os.path.join(target_dir, f"{stem}-{digest}{ext}")
            elif os.path.exists(target):
                target = C._unique_path(target)
                collisions += 1
            shutil.move(os.path.join(dirpath, name), target)
            moved += 1
        try:
            if not os.listdir(dirpath) and os.path.abspath(dirpath) != os.path.abspath(destination):
                os.rmdir(dirpath)
        except OSError:
            pass
    sanitized = None
    if args.strip_metadata or args.epoch:
        sanitized = C.sanitize_metadata(destination, config, drop_junk=args.strip_metadata,
                                        epoch=args.epoch)
    categories = sorted({d for d in os.listdir(destination)
                         if os.path.isdir(os.path.join(destination, d))})
    payload = {"root": os.path.abspath(destination), "moved": moved, "collisions": collisions,
               "categories": categories, "sanitized": sanitized}
    if ctx.get("json"):
        R.emit(payload, True)
        return EXIT_OK, payload, ""
    created = f" ({', '.join(categories)})" if categories else ""
    return EXIT_OK, payload, (f"organized {moved} files into {os.path.abspath(destination)}"
                              f"{created}; {collisions} collision renames")


def cmd_scan(args, ctx):
    config = ctx["config"]
    if args.min_confidence:
        config = config.copy()
        config.data.setdefault("scanner", {})["min_confidence"] = args.min_confidence
    findings: list[dict] = []
    mismatches: list[dict] = []
    files = expand_sources(args.paths, config) if args.paths else []
    roots = [p for p in args.paths if os.path.isdir(p)]
    if args.strip_metadata or args.epoch:
        if args.dry_run:
            payload = {"dry_run": True, "roots": roots}
            return EXIT_OK, payload, "would strip metadata from: " + ", ".join(roots)
    for root in roots:
        if not args.no_secrets:
            findings.extend(C.scan_tree(root, config))
        if not args.no_mismatch:
            mismatches.extend(C.scan_mismatches(root, config))
    for path in files:
        parent_mode = False
        try:
            info = F.detect_file(path)
        except PecError:
            parent_mode = True
            info = None
        if parent_mode:
            continue
        if not args.no_mismatch and info and info["id"] != "unknown":
            ext = info["extension"]
            expected = F.MAGIC_EXT_HINT.get(info["id"], [])
            if ext and ext not in expected and not (info.get("zip_family") and info["id"].startswith("zip")):
                mismatches.append({"path": path, "detected": info["id"], "extension": ext,
                                   "notes": info.get("notes", "")})
    sanitized = []
    if (args.strip_metadata or args.epoch) and not args.dry_run:
        for root in roots:
            sanitized.append(C.sanitize_metadata(root, config, drop_junk=args.strip_metadata,
                                                 epoch=args.epoch))
    gate_mode = args.gate or config.get("policy.secret_gate", "warn")
    exit_code = EXIT_OK
    if args.no_secrets and args.no_mismatch:
        raise PecError("nothing to scan: both --no-secrets and --no-mismatch were given", EXIT_USAGE)
    if findings and gate_mode == "block":
        exit_code = EXIT_POLICY
    payload = {"findings": findings, "mismatches": mismatches, "sanitized": sanitized,
               "gate": gate_mode}
    if ctx["json"]:
        R.emit(payload, True)
        return exit_code, payload, ""
    lines = []
    if not args.no_secrets:
        lines.append(f"secret/PII findings: {len(findings)}")
        for item in findings[:40]:
            lines.append(f"  [{item['severity']}] {item['rule']} in {item['file']}:{item['line']} "
                         f"({item['preview']})")
    if not args.no_mismatch:
        lines.append(f"file-type mismatches: {len(mismatches)}")
        for item in mismatches[:40]:
            lines.append(f"  {item['path']}: looks like {item['detected']} but has {item['extension'] or 'no extension'}")
    for item in sanitized:
        lines.append(f"metadata stripped in {item['root']}: removed {item['removed_count']}, "
                     f"timestamps set {item['timestamps_set']}")
    if not findings and not mismatches:
        lines.append("no findings")
    return exit_code, payload, "\n".join(lines)


def cmd_package(args, ctx):
    config = ctx["config"]
    if args.list:
        recipes = P.list_recipes(config)
        payload = {"recipes": recipes}
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        lines = ["Available packaging recipes:", ""]
        for recipe in recipes:
            options = ", ".join(f"{k}={v}" for k, v in (recipe["options"] or {}).items() if v is not None)
            lines.append(f"  {recipe['name']:<20} {recipe['title']}")
            lines.append(f"  {'':<20} {recipe['description']}")
            lines.append(f"  {'':<20} options: {options or '(none)'}")
            lines.append("")
        return EXIT_OK, payload, "\n".join(lines)
    if args.show:
        spec = P.get_recipe(config, args.show)
        payload = {"recipe": args.show, "spec": spec}
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        lines = [f"{args.show}: {spec.get('title', '')}", spec.get("description", ""), "", "Options:"]
        for key, meta in (spec.get("options") or {}).items():
            lines.append(f"  --set {key}={meta.get('default')}   {meta.get('help', '')}")
        lines.append("")
        lines.append(f"Produces: {spec.get('produces', '')}")
        return EXIT_OK, payload, "\n".join(lines)
    if not args.recipe:
        raise PecError("a recipe name is required (use --list to see recipes)", EXIT_USAGE)
    if not args.sources and args.recipe not in {"compliance-evidence"}:
        raise PecError(f"recipe {args.recipe!r} needs at least one SOURCE path", EXIT_USAGE)
    if not args.output:
        raise PecError("--output is required to publish a package", EXIT_USAGE)
    options = {}
    for item in args.set:
        if "=" not in item:
            raise PecError(f"--set expects KEY=VALUE, got {item!r}", EXIT_USAGE)
        key, value = item.split("=", 1)
        options[key.strip()] = _coerce(value.strip())
    password = read_password(args, config) if hasattr(args, "password_env") else None
    if password:
        options.setdefault("password", password)
    if args.dry_run:
        spec = P.get_recipe(config, args.recipe)
        entries = P.collect_files(args.sources, None, None, True, config) if args.sources else []
        payload = {"dry_run": True, "recipe": args.recipe, "files": len(entries),
                   "bytes": sum(e["size"] for e in entries), "options": options,
                   "output": args.output, "spec": spec.get("title")}
        return EXIT_OK, payload, (f"would run {args.recipe} over {len(entries)} files -> {args.output}")
    result = P.run_recipe(args.recipe, args.sources, args.output, options, config,
                          ctx["logger"], ctx["gate"], ctx["cancel"], job_id=ctx["job"].job_id)
    payload = {"package": result}
    if ctx["json"]:
        R.emit(payload, True)
        return EXIT_OK, payload, ""
    lines = [f"recipe {result['recipe']} -> {result['published'].get('archive') or result['published'].get('directory')}"]
    for produced in result["produced"]:
        lines.append(f"  {produced.get('path')}  {R.human_bytes(produced.get('bytes', 0))}  "
                     f"{produced.get('sha256', '')[:16]}")
    for note in result["notes"]:
        lines.append(f"  note: {note}")
    for warn in result["warnings"]:
        lines.append(f"  WARN {warn}")
    return EXIT_OK, payload, "\n".join(lines)


def _coerce(value: str):
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"none", "null"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def cmd_batch(args, ctx):
    config = ctx["config"]
    gate = ctx["gate"]
    password = read_password(args, config)
    sources = archive_candidates(expand_sources(args.paths, config), config)
    if not sources:
        raise PecError("no archive-like files matched the batch input", EXIT_USAGE)
    workers = args.workers or int(config.get("limits.max_workers", 4))
    workers = max(1, min(workers, 16, len(sources)))
    os.makedirs(args.output, exist_ok=True)
    if args.dry_run:
        payload = {"dry_run": True, "archives": sources, "workers": workers, "output": args.output}
        return EXIT_OK, payload, f"would extract {len(sources)} archives into {args.output} with {workers} workers"
    completed_index = _load_job_index(config)
    runnable = []
    skipped = []
    for source in sources:
        fingerprint = R.compute_fingerprint([source], {"op": "batch"})
        if args.skip_duplicates and fingerprint in completed_index:
            skipped.append({"source": os.path.abspath(source), "fingerprint": fingerprint,
                            "reason": "already extracted successfully",
                            "record": completed_index[fingerprint]})
            continue
        runnable.append((source, fingerprint))

    batch_config = config.copy()
    batch_config.data["quiet"] = True

    def work(item):
        source, fingerprint = item
        job_id = R.new_job_id()
        logger = R.Logger(config.state_dir(), job_id, echo=False, quiet=True)
        cancel = ctx["cancel"]
        stem = os.path.splitext(os.path.basename(source))[0] or "archive"
        destination = os.path.join(args.output, stem)
        options = C.ExtractOptions(destination=destination, nest=args.nest, quarantine=args.quarantine,
                                    scan=not args.no_scan, password=password,
                                    job_key=f"batch:{fingerprint}")
        job = C.ExtractionJob(source, options, batch_config, logger, cancel, gate, job_id=job_id)
        try:
            result = job.run()
            return {"source": os.path.abspath(source), "ok": result["status"] == "completed",
                    "status": result["status"], "destination": result["destination"],
                    "entries": result["entries_written"], "failed_entries": result["entries_failed"],
                    "bytes": result["bytes_written"], "fingerprint": fingerprint, "job_id": job_id,
                    "manifest": result["manifest"]}
        except PecError as exc:
            return {"source": os.path.abspath(source), "ok": False, "status": "failed",
                    "error": exc.as_dict(), "job_id": job_id, "fingerprint": fingerprint}

    results = []
    retries = int(config.get("limits.retries", 0))
    backoff = float(config.get("limits.retry_backoff_seconds", 0.5))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, item): item for item in runnable}
        for future in as_completed(futures):
            source, _ = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - batch must never lose a job
                result = {"source": os.path.abspath(source), "ok": False, "status": "crashed",
                          "error": R.diagnose_exception(exc)}
            results.append(result)
            if not result.get("ok") and args.fail_fast:
                for pending in futures:
                    pending.cancel()
                break
    results.sort(key=lambda r: r["source"])
    succeeded = sum(1 for r in results if r.get("ok"))
    payload = {"results": results, "skipped": skipped, "succeeded": succeeded,
               "failed": len(results) - succeeded, "workers": workers,
               "archives": len(sources)}
    exit_code = EXIT_OK if succeeded == len(results) else (EXIT_PARTIAL if succeeded else EXIT_INTEGRITY)
    if ctx["json"]:
        R.emit(payload, True)
        return exit_code, payload, ""
    lines = [f"batch: {succeeded}/{len(results)} archives extracted with {workers} workers"]
    for result in results:
        marker = "ok  " if result.get("ok") else "FAIL"
        detail = (f"{result.get('entries', 0)} files" if result.get("ok")
                  else result.get("error", {}).get("message", "failed"))
        lines.append(f"  {marker} {os.path.basename(result['source'])}: {detail}")
    for item in skipped:
        lines.append(f"  skip {os.path.basename(item['source'])}: {item['reason']}")
    return exit_code, payload, "\n".join(lines)


def _load_job_index(config) -> dict:
    index: dict[str, str] = {}
    path = os.path.join(config.state_dir(), "jobs", "index.jsonl")
    for record in R.read_jsonl(path):
        if record.get("status") == "completed" and record.get("fingerprint"):
            index[record["fingerprint"]] = record.get("record", "")
    return index


def cmd_jobs(args, ctx):
    config = ctx["config"]
    state = config.state_dir()
    jobs_dir = os.path.join(state, "jobs")
    if args.action == "list":
        records = []
        index = os.path.join(jobs_dir, "index.jsonl")
        for record in R.read_jsonl(index)[-args.limit:]:
            records.append(record)
        payload = {"jobs": records, "state_dir": state}
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        if not records:
            return EXIT_OK, payload, f"no recorded jobs in {state}"
        rows = [[r.get("ts", ""), r.get("command", ""), r.get("status", ""), str(r.get("exit_code")),
                 r.get("job_id", ""), (r.get("fingerprint") or "")[:19]] for r in records]
        return EXIT_OK, payload, R.table(rows, ["when", "command", "status", "exit", "job id", "fingerprint"])
    if args.action == "show":
        if not args.job_id:
            raise PecError("jobs show requires --job-id", EXIT_USAGE)
        path = os.path.join(jobs_dir, f"{args.job_id}.json")
        payload = R.read_json(path, None)
        if payload is None:
            raise PecError(f"no such job: {args.job_id}", EXIT_USAGE)
        if ctx["json"]:
            R.emit({"job": payload}, True)
            return EXIT_OK, payload, ""
        keys = ["job_id", "command", "status", "exit_code", "started_at", "finished_at",
                "duration_seconds", "fingerprint"]
        human = "\n".join(f"{k}: {payload.get(k)}" for k in keys)
        return EXIT_OK, payload, human + "\n\n" + json.dumps(payload.get("result", {}), indent=2)[:4000]
    if args.action == "resume":
        if not args.job_id:
            raise PecError("jobs resume requires --job-id", EXIT_USAGE)
        record = R.read_json(os.path.join(jobs_dir, f"{args.job_id}.json"), None)
        if record is None:
            raise PecError(f"no such job: {args.job_id}", EXIT_USAGE)
        command = record.get("command", "")
        params = record.get("params") or {}
        if command != "extract":
            raise PecError(f"resume is only supported for extract jobs (this is {command!r})",
                           EXIT_USAGE)
        if not params.get("paths") or not params.get("output"):
            raise PecError("the stored job does not contain resumable parameters", EXIT_USAGE)
        defaults = {
            "paths": [], "output": None, "include": None, "exclude": None, "regex": "",
            "ext": None, "min_size": None, "max_size": None, "since": None, "until": None,
            "index_range": None, "limit": None, "nest": False, "max_depth": None,
            "quarantine": False, "strip_metadata": False, "organize": None, "publish": None,
            "overwrite": None, "no_scan": False, "no_checkpoint": False, "resume": True,
            "job_key": "", "no_assemble": False, "merge": False, "dry_run": False,
            "password_env": None, "password_file": None,
        }
        defaults.update(params)
        defaults["resume"] = True
        defaults["dry_run"] = False
        resumed_args = argparse.Namespace(**defaults)
        if not resumed_args.paths or not resumed_args.output:
            raise PecError("the stored job does not contain resumable paths and output", EXIT_USAGE)
        resume_ctx = {**ctx, "json": False, "job": R.Job(command="extract")}
        resume_logger = R.Logger(ctx["config"].state_dir(),
                                 resume_ctx["job"].job_id, echo=not args.quiet, quiet=bool(args.quiet))
        resume_ctx["logger"] = resume_logger
        return cmd_extract(resumed_args, resume_ctx)
    if args.action == "clean":
        days = 30
        if args.older_than:
            try:
                days = int(args.older_than)
            except ValueError as exc:
                raise PecError("--older-than must be a whole number of days", EXIT_USAGE) from exc
        cutoff = time.time() - days * 86400
        targets: list[str] = []
        for sub in ("jobs", "logs", "checkpoints", "tmp"):
            directory = os.path.join(state, sub)
            if not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                path = os.path.join(directory, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        targets.append(path)
                except OSError:
                    continue
        if args.dry_run:
            payload = {"dry_run": True, "would_delete": targets, "older_than_days": days}
            return EXIT_OK, payload, "\n".join(targets) or "nothing to delete"
        ctx["gate"].require("delete", detail=f"purging {len(targets)} state entries older than {days} days")
        removed = 0
        for path in targets:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                removed += 1
            except OSError:
                continue
        payload = {"removed": removed, "older_than_days": days, "state_dir": state}
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        return EXIT_OK, payload, f"removed {removed} state entries older than {days} days"
    raise PecError(f"unknown jobs action {args.action!r}", EXIT_USAGE)


def cmd_config(args, ctx):
    config = ctx["config"]
    if args.action == "show":
        payload = {"config": config.data, "sources": config.source_chain,
                   "state_dir": config.state_dir()}
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        return EXIT_OK, payload, json.dumps(payload, indent=2, ensure_ascii=False)
    if args.action == "validate":
        problems = R.validate_config(config.data)
        payload = {"valid": not problems, "problems": problems, "sources": config.source_chain}
        if ctx["json"]:
            R.emit(payload, True)
            return (EXIT_OK if not problems else EXIT_USAGE), payload, ""
        if not problems:
            return EXIT_OK, payload, f"configuration valid ({', '.join(config.source_chain)})"
        lines = [f"{len(problems)} problem(s):"]
        lines += [f"  {p['field']}: {p['issue']} (expected {p.get('expected')})" for p in problems]
        return EXIT_USAGE, payload, "\n".join(lines)
    if args.action == "init":
        if not args.output:
            raise PecError("config init requires --output PATH", EXIT_USAGE)
        if os.path.exists(args.output):
            ctx["gate"].require("overwrite", detail=f"replacing {args.output}")
        R.write_json_atomic(args.output, R.BUILTIN_DEFAULTS)
        payload = {"written": os.path.abspath(args.output)}
        return EXIT_OK, payload, f"wrote default configuration to {args.output}"
    if args.action == "backends":
        status = F.backend_status(config)
        payload = {"backends": status}
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        rows = [[name, "available" if ok else "missing"] for name, ok in sorted(status.items())]
        return EXIT_OK, payload, R.table(rows, ["backend", "status"])
    if args.action == "fs":
        target = args.path or os.getcwd()
        payload = profile_filesystem(target)
        if ctx["json"]:
            R.emit(payload, True)
            return EXIT_OK, payload, ""
        return EXIT_OK, payload, json.dumps(payload, indent=2)
    raise PecError(f"unknown config action {args.action!r}", EXIT_USAGE)


def profile_filesystem(path: str) -> dict:
    path = os.path.abspath(path)
    probe_dir = path if os.path.isdir(path) else os.path.dirname(path)
    os.makedirs(probe_dir, exist_ok=True)
    usage = shutil.disk_usage(probe_dir)
    probe = os.path.join(probe_dir, f".pec-probe-{os.getpid()}")
    atomic = False
    sparse = False
    case_sensitive = True
    try:
        with open(probe, "wb") as fh:
            fh.write(b"probe")
        os.replace(probe, probe + ".renamed")
        atomic = True
        os.unlink(probe + ".renamed")
        mixed = os.path.join(probe_dir, f".pec-Case-{os.getpid()}")
        with open(mixed, "wb") as fh:
            fh.write(b"x")
        case_sensitive = not os.path.exists(os.path.join(probe_dir, os.path.basename(mixed).lower()))
        os.unlink(mixed)
        sparse_path = os.path.join(probe_dir, f".pec-sparse-{os.getpid()}")
        with open(sparse_path, "wb") as fh:
            fh.seek(4 * 1024 * 1024)
            fh.write(b"x")
        sparse = os.path.getsize(sparse_path) > 0 and disk_allocated(sparse_path) < 1024 * 1024
        os.unlink(sparse_path)
    except OSError:
        pass
    finally:
        for leftover in (probe, probe + ".renamed"):
            if os.path.exists(leftover):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
    return {
        "path": probe_dir,
        "exists": os.path.exists(path),
        "free_bytes": usage.free,
        "free_human": R.human_bytes(usage.free),
        "total_bytes": usage.total,
        "atomic_rename": atomic,
        "case_sensitive": case_sensitive,
        "sparse_files": sparse,
        "max_path_length": 4096 if os.name != "nt" else 260,
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }


def disk_allocated(path: str) -> int:
    try:
        st = os.stat(path)
        return getattr(st, "st_blocks", 0) * 512
    except OSError:
        return os.path.getsize(path)


def cmd_selftest(args, ctx):
    ST = _load("tools-pec-selftest.py", "pec_selftest")
    workdir = args.work or os.path.join(ctx["config"].state_dir(), "selftest")
    os.makedirs(workdir, exist_ok=True)
    result = ST.run_all(workdir, keep=args.keep, only=args.only, log=ctx["logger"])
    result["work_dir"] = os.path.abspath(workdir)
    payload = {"selftest": result}
    exit_code = EXIT_OK if result["ok"] else EXIT_PARTIAL
    if ctx["json"]:
        R.emit(payload, True)
        return exit_code, payload, ""
    lines = [f"selftest: {result['passed']}/{result['total']} passed in {result['seconds']}s"]
    for case in result["cases"]:
        lines.append(f"  {'PASS' if case['ok'] else 'FAIL'}  {case['case']:<16} {case['description']}"
                     + ("" if case["ok"] else f"  -> {case.get('error')}"))
    if not args.keep:
        lines.append(f"  fixtures kept in {result['work_dir']} is disabled; re-run with --keep to inspect")
    return exit_code, payload, "\n".join(lines)


COMMANDS = {
    "caps": cmd_caps, "coverage": cmd_coverage, "detect": cmd_detect, "inspect": cmd_inspect, "list": cmd_list,
    "extract": cmd_extract, "create": cmd_create, "verify": cmd_verify, "organize": cmd_organize,
    "scan": cmd_scan, "package": cmd_package, "batch": cmd_batch, "jobs": cmd_jobs,
    "config": cmd_config, "selftest": cmd_selftest,
}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stdout.write(R.MAIN_HELP)
        return EXIT_OK
    parser = build_parser()
    job = R.Job(command="unknown")
    ctx: dict = {}
    try:
        args = parser.parse_args(argv)
        if not args.command:
            sys.stdout.write(R.MAIN_HELP)
            return EXIT_OK
        job.command = args.command
        config = R.Config.load(args.config)
        if args.state:
            config.data["state_dir"] = args.state
        if args.quiet:
            config.data["quiet"] = True
        state_dir = config.state_dir()
        logger = R.Logger(state_dir, job.job_id, echo=not args.quiet, quiet=bool(args.quiet))
        gate = R.ApprovalGate(config, granted=[a.lower() for a in (args.approve or [])],
                              approval_file=args.approval_file, dry_run=args.dry_run)
        deadline = R.parse_duration(args.deadline) if args.deadline else None
        cancel = R.Cancellation(deadline).install()
        job.state_dir = state_dir
        job.log_path = logger.path
        # Commands build payloads and human text; the envelope is emitted once, centrally.
        ctx = {"config": config, "logger": logger, "gate": gate, "cancel": cancel,
               "json": False, "emit_json": args.json, "job": job}
        handler = COMMANDS.get(args.command)
        if handler is None:
            raise PecError(f"unknown command {args.command!r}", EXIT_USAGE)
        try:
            code, payload, human = handler(args, ctx)
        finally:
            cancel.restore()
        job.status = "completed" if code == EXIT_OK else ("partial" if code == EXIT_PARTIAL else "failed")
        if args.command == "selftest" and isinstance(payload, dict) and payload.get("selftest"):
            inner = payload["selftest"]
            if not inner.get("ok"):
                job.status = "failed"
        job.result = payload
        job.artifacts = _collect_artifacts(payload)
        job.finished_at = R.utcnow()
        job.duration_seconds = time.monotonic() - cancel.started
        job.params = _safe_params(args)
        job.fingerprint = _first_fingerprint(payload)
        envelope = job.envelope(code)
        if args.command != "selftest" or os.environ.get("PEC_RECORD_SELFTEST"):
            job.persist(state_dir, envelope)
        if args.json:
            json.dump(envelope, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        elif human:
            R.emit({"ok": code == EXIT_OK}, False, human)
        return code
    except PecError as exc:
        return _finish_error(job, ctx, exc.code, exc.as_dict(), exc.message)
    except KeyboardInterrupt:
        return _finish_error(job, ctx, EXIT_CANCELLED,
                             {"kind": "cancelled", "message": "interrupted by user"},
                             "cancelled by user")
    except Exception as exc:  # noqa: BLE001 - top-level safety net
        detail = R.diagnose_exception(exc)
        return _finish_error(job, ctx, R.EXIT_INTERNAL,
                             {"kind": "internal", "message": str(exc), "detail": detail},
                             f"unexpected failure: {exc}")


def _collect_artifacts(payload) -> list[str]:
    out: list[str] = []
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"manifest", "archive", "output", "destination", "directory", "written",
                           "inventory_written"} and isinstance(item, str):
                    out.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value[:50]:
                walk(item)
    walk(payload)
    seen: list[str] = []
    for path in out:
        if path and path not in seen:
            seen.append(path)
    return seen[:50]


def _first_fingerprint(payload) -> str:
    if isinstance(payload, dict):
        if payload.get("fingerprint"):
            return payload["fingerprint"]
        for value in payload.values():
            found = _first_fingerprint(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _first_fingerprint(value)
            if found:
                return found
    return ""


def _safe_params(args) -> dict:
    keep = ("paths", "sources", "recipe", "output", "format", "level", "nest", "quarantine",
            "organize", "publish", "overwrite", "include", "exclude", "regex", "ext",
            "limit", "job_key", "resume", "merge", "workers", "password_env", "password_file",
            "action", "job_id", "manifest", "directory", "hash_alg", "mode", "gate",
            "min_confidence", "volume_size", "encrypt")
    params = {}
    for name in keep:
        if hasattr(args, name):
            value = getattr(args, name)
            if value not in (None, "", [], False):
                params[name] = value
    return params


def _finish_error(job: R.Job, ctx: dict, code: int, error: dict, human: str) -> int:
    job.status = "failed" if code != EXIT_CANCELLED else "cancelled"
    job.finished_at = R.utcnow()
    job.errors.append(error)
    envelope = job.envelope(code)
    if ctx.get("emit_json") or ctx.get("json"):
        json.dump(envelope, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stderr.write(f"error: {human}\n")
        if code == EXIT_APPROVAL and isinstance(error.get("detail"), dict):
            token = error["detail"].get("token")
            if token:
                sys.stderr.write(f"hint: re-run with --approve {token} after a reviewer confirms\n")
    if ctx.get("config") is not None:
        try:
            job.persist(ctx["config"].state_dir(), envelope)
        except OSError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
