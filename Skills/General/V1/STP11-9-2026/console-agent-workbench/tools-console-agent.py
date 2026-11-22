#!/usr/bin/env python3
"""tools-console-agent.py - primary entry point of the console-agent-workbench Skill Tool.

Unified, lawful console preservation, verification, and archival-research workbench
derived from the Console Agent Skill Catalog (36 categories, 491 skills).

Run ``python3 tools-console-agent.py help`` for the command list and
``python3 tools-console-agent.py capabilities`` for the machine-readable capability map.

Standard library only: Python 3.9+ is required, nothing else.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Module loading (filenames are prefixed with "tools-", so load them explicitly)
# ---------------------------------------------------------------------------
def _load(filename):
    module_name = filename[len("tools-"):-len(".py")].replace("-", "_")
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        sys.stderr.write(f"fatal: required tool module is missing: {filename}\n")
        raise SystemExit(1)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


rt = _load("tools-caw-runtime.py")
guard = _load("tools-caw-guard.py")       # imports caw_runtime
media = _load("tools-caw-media.py")       # imports caw_runtime
prov = _load("tools-caw-provenance.py")   # imports caw_runtime
report = _load("tools-caw-report.py")     # imports caw_runtime, caw_guard

# ---------------------------------------------------------------------------
# Capability map (kept in one place so `capabilities` and the docs cannot drift)
# ---------------------------------------------------------------------------
CAPABILITIES = [
    {
        "id": "discovery-and-selftest",
        "title": "Capability discovery and startup self-test",
        "catalog_skills": ["Console Capability Inventory", "Console Startup Self-Test Orchestrator",
                           "Capability Gap Recommender"],
        "commands": ["inventory", "selftest"],
        "summary": ("Probe installed tools with bounded timeouts, report the catalog's status "
                    "vocabulary with evidence, and recommend the smallest missing addition."),
        "requires_approval": False,
    },
    {
        "id": "legal-guardrails",
        "title": "Lawful use gate, restricted-material boundary, redaction",
        "catalog_skills": ["Lawful Ownership and Authorization Gate",
                           "Restricted Material Boundary Enforcer", "Private SDK Data Redactor",
                           "Artifact Minimization Policy"],
        "commands": ["gate", "scan", "redact"],
        "summary": ("Signed operator authorization per material, restricted-material scanning in "
                    "four modes, and policy-driven redaction of paths, credentials and identifiers."),
        "requires_approval": False,
    },
    {
        "id": "provenance-and-evidence",
        "title": "Provenance records, hashing, tamper-evident ledger",
        "catalog_skills": ["Preservation Provenance Recorder", "Hashing and Checksum Pipeline",
                           "DAT Provenance Manager"],
        "commands": ["provenance", "hash"],
        "summary": ("Schema-validated provenance records, one-pass crc32/md5/sha1/sha256/blake2b "
                    "manifests, and an append-only hash-chained evidence ledger."),
        "requires_approval": False,
    },
    {
        "id": "media-identification-and-verification",
        "title": "Media inspection, DAT verification, repeated-read comparison",
        "catalog_skills": ["Console Artifact Auto-Detector", "No-Intro DAT Verification Specialist",
                           "Repeated-Read Comparator", "Duplicate and Revision Classifier",
                           "Save File Format Analyst", "Memory Card Image Inspector"],
        "commands": ["inspect", "verify"],
        "summary": ("Local header/container parsing for ten console media families, verification "
                    "against an operator-supplied DAT, and block-level repeated-read comparison."),
        "requires_approval": False,
    },
    {
        "id": "planning",
        "title": "Reversible experiment planning and task decomposition",
        "catalog_skills": ["Reversible Experiment Planner", "Console Task Decomposer"],
        "commands": ["plan"],
        "summary": ("Nine-stage decomposition with baselines, stop conditions, rollback steps and "
                    "per-stage checklists, tailored by platform addenda."),
        "requires_approval": False,
    },
    {
        "id": "reporting-and-handoff",
        "title": "Preservation report rendering and archival handoff",
        "catalog_skills": ["Media Verification Report Generator",
                           "Compatibility Test Report Generator", "Technical Handoff Builder",
                           "Rights and Redistribution Register"],
        "commands": ["report"],
        "summary": ("Compose every supplied evidence file into one metadata-only Markdown, "
                    "self-contained HTML or JSON report with redaction applied."),
        "requires_approval": False,
    },
    {
        "id": "evidence-retention",
        "title": "Evidence retention, quarantine and restore",
        "catalog_skills": ["Evidence Retention Controller"],
        "commands": ["retention"],
        "summary": ("Apply retention classes to an evidence tree, quarantine due files "
                    "reversibly, restore them, or hard delete under an approval token."),
        "requires_approval": True,
    },
    {
        "id": "routing",
        "title": "Worker, hardware and official-program routing",
        "catalog_skills": ["Official Program Router", "Host and Hardware Requirement Router"],
        "commands": ["route"],
        "summary": ("Route work to Linux, Windows, macOS, GPU, FPGA, physical-lab or official "
                    "partner workers using explicit hard requirements."),
        "requires_approval": False,
    },
    {
        "id": "catalog-retrieval",
        "title": "Catalog retrieval over all 491 skills",
        "catalog_skills": ["Console Knowledge Retrieval Index"],
        "commands": ["catalog", "capabilities"],
        "summary": ("Search and filter the 36 catalog categories and 491 skills, and see which "
                    "are executed by this workbench."),
        "requires_approval": False,
    },
]

NOT_IMPLEMENTED = [
    "Executing emulators, debuggers, or FPGA toolchains (they are probed, routed, and reported, never wrapped)",
    "Reading physical media: cartridge/optical dumpers and drives are hardware, not software",
    "Parsing No-Intro/Redump/TOSEC/MAME XML DATs (a JSON DAT format is used and documented instead)",
    "Parsing proprietary container formats (PBP, XEX, SELF, RVZ, CHD) that need licensed documentation or decryption",
    "Decryption, circumvention of DRM or account locks, or interference with anti-cheat systems",
    "Network access of any kind: no downloads, no uploads, no hosted services",
]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
EPILOG = """examples (bash):
  python3 console-agent-workbench/tools-console-agent.py capabilities
  python3 console-agent-workbench/tools-console-agent.py catalog --category 17
  python3 console-agent-workbench/tools-console-agent.py inventory --priority P0
  python3 console-agent-workbench/tools-console-agent.py selftest --gaps
  python3 console-agent-workbench/tools-console-agent.py gate --action authorize \\
      --operator "A. Operator" --material media:owned-cartridge-01 --attest --out work/authz.json
  python3 console-agent-workbench/tools-console-agent.py scan --path ~/dumps --mode package
  python3 console-agent-workbench/tools-console-agent.py inspect --path ~/dumps/title.nes
  python3 console-agent-workbench/tools-console-agent.py verify --dat asset-dat-template.json --path ~/dumps
  python3 console-agent-workbench/tools-console-agent.py provenance --action create \\
      --material ~/dumps/title.nes --operator "A. Operator" --out work/record.json
  python3 console-agent-workbench/tools-console-agent.py report --input work/record.json \\
      --format html --out work/report.html

Exit codes: 0 ok, 2 invalid input, 3 policy block, 4 approval required/invalid,
5 missing path/resource, 6 checks failed (inventory gaps, verify mismatch).
"""


def build_parser():
    # The parent parser suppresses defaults so that a global option given before the
    # subcommand is not silently overwritten by the subparser default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=("json", "text"), default=argparse.SUPPRESS,
                        help="output format (default: json; json is the stable machine interface)")
    common.add_argument("--workdir", default=argparse.SUPPRESS,
                        help="working directory for evidence, ledgers and approvals (default: caw-work)")
    common.add_argument("--policy", default=argparse.SUPPRESS,
                        help="path to a guard policy JSON (default: packaged asset-guard-policy.json)")
    common.add_argument("--catalog", default=argparse.SUPPRESS,
                        help="path to a capability catalog JSON (default: packaged asset-capability-catalog.json)")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="overall operation timeout in seconds")
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="log progress to stderr")

    parser = argparse.ArgumentParser(
        prog="tools-console-agent.py",
        description=("console-agent-workbench: lawful console preservation, verification and "
                     "archival-research workbench (36 catalog categories, 491 skills)."),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    parser.add_argument("--version", action="version",
                        version=f"console-agent-workbench {rt.VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    parser.sub_map = {}

    def add(name, help_text):
        subparser = sub.add_parser(name, help=help_text, description=help_text, parents=[common],
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.sub_map[name] = subparser
        return subparser

    p = add("help", "Show this help, or detailed help for one command.")
    p.add_argument("topic", nargs="?", default=None, help="command name for detailed help")

    p = add("capabilities", "List the implemented capability families (machine-readable).")
    p.add_argument("--include-unimplemented", action="store_true",
                   help="also list capability areas this workbench deliberately does not implement")

    p = add("catalog", "Search or list the 36 catalog categories and their 491 skills.")
    p.add_argument("--list-categories", action="store_true", help="list categories with counts")
    p.add_argument("--category", action="append", default=None,
                   help="category index or name substring (repeatable)")
    p.add_argument("--search", default=None, help="search skill names and descriptions")
    p.add_argument("--skill", default=None, help="match a skill name (case-insensitive substring)")
    p.add_argument("--routing", default=None, help="filter by routing class")
    p.add_argument("--implemented-only", action="store_true",
                   help="only skills executed by this workbench")
    p.add_argument("--limit", type=int, default=50, help="maximum results (default 50)")

    p = add("inventory", "Probe local tools, device classes and licenses; report catalog statuses.")
    p.add_argument("--probe-manifest", default=None, help="override asset-probe-manifest.json")
    p.add_argument("--priority", action="append", default=None,
                   help="restrict to priorities, e.g. --priority P0 (repeatable)")
    p.add_argument("--max-probes", type=int, default=None, help="cap the number of probes run")
    p.add_argument("--no-hardware", action="store_true", help="skip device-class probing")
    p.add_argument("--licenses", default=None,
                   help="JSON map of capability_id -> license state supplied by the operator")
    p.add_argument("--out", default=None, help="also write the result JSON here")

    p = add("selftest", "Run local smoke checks and write an evidence file.")
    p.add_argument("--out", default=None, help="evidence file path (default: <workdir>/evidence/)")
    p.add_argument("--gaps", action="store_true", help="include the smallest-fix recommendations")
    p.add_argument("--full-probes", action="store_true",
                   help="probe every manifest entry instead of the P0 subset")

    p = add("route", "Route work to a worker profile or an official partner program.")
    p.add_argument("--platform", default=None, help="platform key, e.g. ps5, nes, switch-2")
    p.add_argument("--require", action="append", default=None,
                   help="required capability class, e.g. --require cartridge-dumping (repeatable)")
    p.add_argument("--official", action="store_true", help="only consider official program workers")
    p.add_argument("--list-profiles", action="store_true", help="list worker profiles")
    p.add_argument("--worker-profiles", default=None, help="override asset-worker-profiles.json")
    p.add_argument("--out", default=None, help="also write the routing result JSON here")

    p = add("gate", "Authorization records and approval tokens for gated actions.")
    p.add_argument("--action", required=True,
                   choices=("authorize", "verify", "list", "approve", "check-approval"),
                   help="gate sub-action")
    p.add_argument("--operator", default=None, help="operator name (authorize/approve)")
    p.add_argument("--material", action="append", default=None,
                   help="material in scope as type:reference, e.g. media:cartridge-01 (repeatable)")
    p.add_argument("--statement", default=None, help="free-text authorization statement")
    p.add_argument("--requires", action="append", default=None,
                   help="material type that must be covered during verify (repeatable)")
    p.add_argument("--attest", action="store_true",
                   help="operator explicitly attests lawful ownership (required to authorize)")
    p.add_argument("--ttl-hours", type=int, default=24, help="authorization lifetime (default 24)")
    p.add_argument("--ttl-minutes", type=int, default=30, help="approval lifetime (default 30)")
    p.add_argument("--gated-action", default=None,
                   help="action name for approve/check-approval, e.g. retention-apply")
    p.add_argument("--scope", action="append", default=None,
                   help="path in the approval scope (repeatable)")
    p.add_argument("--approval", default=None, help="approval token file for check-approval")
    p.add_argument("--record", default=None, help="authorization record for verify")
    p.add_argument("--dir", default=None, help="directory of authorization records for list")
    p.add_argument("--out", default=None, help="write the new record/token here")

    p = add("scan", "Scan paths for restricted material under the guard policy.")
    p.add_argument("--path", action="append", required=True, help="path to scan (repeatable)")
    p.add_argument("--mode", choices=("bundle", "redistribute", "report", "package"),
                   default="bundle", help="scan mode (default: bundle)")
    p.add_argument("--max-files", type=int, default=None, help="file visit limit")
    p.add_argument("--max-depth", type=int, default=None, help="directory depth limit")
    p.add_argument("--max-read-bytes", type=int, default=None,
                   help="bytes read per file for content rules (default 65536)")
    p.add_argument("--follow-symlinks", action="store_true", help="follow symlinks (off by default)")
    p.add_argument("--out", default=None, help="write findings JSON here")

    p = add("redact", "Redact paths, credentials and identifiers from a text or JSON file.")
    p.add_argument("--in", dest="infile", required=True, help="input file (UTF-8 text or JSON)")
    p.add_argument("--out", default=None, help="output file (required unless --check)")
    p.add_argument("--check", action="store_true",
                   help="report hits without writing; exit 3 when anything would be redacted")

    p = add("hash", "Compute hashes and a manifest for files or directories.")
    p.add_argument("--path", action="append", required=True, help="file or directory (repeatable)")
    p.add_argument("--algorithm", action="append", default=None,
                   help=f"hash algorithm, repeatable (default crc32+md5+sha1+sha256; "
                        f"available: {', '.join(rt.ALGORITHMS)})")
    p.add_argument("--no-recursive", action="store_true", help="do not descend into directories")
    p.add_argument("--ext", action="append", default=None, help="only these extensions (repeatable)")
    p.add_argument("--max-files", type=int, default=None, help="file limit")
    p.add_argument("--hash-cap", type=int, default=None,
                   help="skip content hashing above this many bytes (0 = no cap)")
    p.add_argument("--out", default=None, help="write the manifest JSON here")

    p = add("inspect", "Inspect console media and container headers without modifying them.")
    p.add_argument("--path", action="append", required=True, help="file to inspect (repeatable)")
    p.add_argument("--no-hash", action="store_true", help="skip content hashing")
    p.add_argument("--hash-cap", type=int, default=None, help="skip hashing above this size")
    p.add_argument("--out", default=None, help="write the result JSON here")

    p = add("verify", "Verify files against a trusted DAT, or cross-check repeated dumps.")
    p.add_argument("--path", action="append", required=True, help="file or directory (repeatable)")
    p.add_argument("--dat", default=None,
                   help="trusted DAT JSON (default when omitted and not cross-checking: "
                        "asset-dat-template.json)")
    p.add_argument("--crosscheck", action="store_true",
                   help="compare two or more independent dumps byte by byte")
    p.add_argument("--require-match", action="store_true",
                   help="exit 6 unless every file verifies against the DAT")
    p.add_argument("--block-size", type=int, default=1 << 20, help="cross-check block size")
    p.add_argument("--out", default=None, help="write the result JSON here")

    p = add("provenance", "Create, validate and append provenance records to a hash-chained ledger.")
    p.add_argument("--action", required=True,
                   choices=("create", "validate", "append", "verify-ledger", "list"),
                   help="provenance sub-action")
    p.add_argument("--material", default=None, help="material file to record (create)")
    p.add_argument("--operator", default=None, help="operator name")
    p.add_argument("--media-type", default="unknown",
                   choices=("cartridge", "optical-disc", "magnetic-media", "card", "save-memory",
                            "firmware", "executable", "archive", "unknown"),
                   help="media type of the material (default: unknown)")
    p.add_argument("--region", default="unknown", help="region")
    p.add_argument("--revision", default="unknown", help="revision")
    p.add_argument("--ownership", default="owned",
                   choices=("owned", "authorized", "public-domain", "licensed-archive", "unknown"),
                   help="ownership basis (default: owned)")
    p.add_argument("--method", default="", help="dump or acquisition method")
    p.add_argument("--reader", default="", help="reader or drive model")
    p.add_argument("--hardware", default="", help="host console or hardware model")
    p.add_argument("--firmware", default="", help="firmware version")
    p.add_argument("--reads", type=int, default=1, help="number of independent reads")
    p.add_argument("--source", default="", help="provenance source")
    p.add_argument("--notes", default="", help="operator notes")
    p.add_argument("--redistribution", default="none",
                   choices=("none", "unknown", "permitted", "embargoed"),
                   help="redistribution rights basis (default: none)")
    p.add_argument("--attribution", default="", help="attribution or credit line")
    p.add_argument("--software-version", action="append", default=None,
                   help="tool=version pair recorded in the dump section (repeatable)")
    p.add_argument("--record", default=None, help="record file for validate/append")
    p.add_argument("--ledger", default=None, help="ledger file (.jsonl) for append/verify-ledger/list")
    p.add_argument("--schema", default=None, help="override asset-provenance-schema.json")
    p.add_argument("--out", default=None, help="write the created record here")

    p = add("plan", "Build a reversible experiment plan or a nine-stage decomposition.")
    p.add_argument("--goal", default=None, help="what the operator wants to achieve")
    p.add_argument("--platform", default=None, help="platform addendum key, e.g. nes, ps5, optical")
    p.add_argument("--risk", default="medium", choices=("low", "medium", "high"), help="risk level")
    p.add_argument("--stage", action="append", default=None, help="restrict to these stages")
    p.add_argument("--operator", default=None, help="operator name")
    p.add_argument("--playbook", default=None, help="override asset-plan-playbook.json")
    p.add_argument("--decompose", action="store_true",
                   help="print only the nine-stage decomposition")
    p.add_argument("--out", default=None, help="write the plan JSON here")

    p = add("report", "Render one report from supplied evidence files.")
    p.add_argument("--input", action="append", default=None,
                   help="evidence JSON produced by another command (repeatable)")
    p.add_argument("--out", default=None, help="output path (default: <workdir>/report.<ext>)")
    p.add_argument("--report-format", choices=("md", "html", "json"), default="md",
                   help="report format (default md)")
    p.add_argument("--template", default=None, help="override asset-report-template.md")
    p.add_argument("--title", default=None, help="report title")
    p.add_argument("--operator", default=None, help="operator name")
    p.add_argument("--goal", default=None, help="goal statement")
    p.add_argument("--rights", default=None,
                   help="redistribution basis asserted by the operator")
    p.add_argument("--allow-content", action="store_true",
                   help="state that content inclusion is permitted (default: metadata only)")
    p.add_argument("--limitation", action="append", default=None, help="extra limitation note")
    p.add_argument("--next-step", action="append", default=None, help="extra next step")

    p = add("retention", "Evaluate, quarantine, restore or delete evidence under a retention policy.")
    p.add_argument("--action", required=True, choices=("evaluate", "apply", "restore", "policy"),
                   help="retention sub-action")
    p.add_argument("--dir", default=None, help="evidence root (or quarantine dir for restore)")
    p.add_argument("--approval", default=None, help="approval token (required for apply)")
    p.add_argument("--operator", default=None, help="operator name")
    p.add_argument("--hard-delete", action="store_true",
                   help="delete instead of quarantine (needs a retention-hard-delete approval)")
    p.add_argument("--now", default=None, help="evaluate as of this ISO-8601 time (testing aid)")
    p.add_argument("--out", default=None, help="write the result JSON here")

    return parser


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
class Context:
    def __init__(self, args):
        self.args = args
        self._policy = None
        self._catalog = None
        self.verbose = getattr(args, "verbose", False)
        self.workdir = getattr(args, "workdir", "caw-work")
        self.timeout = getattr(args, "timeout", None)

    @property
    def policy(self):
        if self._policy is None:
            self._policy = guard.load_policy(self.args.policy)
        return self._policy

    @property
    def catalog(self):
        if self._catalog is None:
            path = self.args.catalog or rt.resource_default("asset-capability-catalog.json")
            self._catalog = rt.load_json(path, "capability catalog")
            if "categories" not in self._catalog:
                raise rt.ValidationError("capability catalog has no 'categories' array")
        return self._catalog

    def evidence(self, name):
        return os.path.join(rt.workdir(self.workdir, "evidence"), name)

    def limits(self):
        base = dict(rt.DEFAULT_LIMITS)
        base["overall_timeout_seconds"] = self.timeout or base["overall_timeout_seconds"]
        return base


def _deadline(ctx):
    return time.monotonic() + float(ctx.timeout or rt.DEFAULT_LIMITS["overall_timeout_seconds"])


# ---------------------------------------------------------------------------
# Command: help
# ---------------------------------------------------------------------------
def cmd_help(args, ctx):
    parser = build_parser()
    if args.topic:
        sub = parser.sub_map.get(args.topic)
        if sub is None:
            raise rt.ValidationError(
                f"unknown command {args.topic!r}",
                ["commands: " + ", ".join(sorted(parser.sub_map))])
        text = sub.format_help()
        return ({"topic": args.topic, "usage": text},
                text, rt.EXIT_OK)
    text = parser.format_help() + "\n" + EPILOG
    return ({"topic": None, "commands": sorted(parser.sub_map),
             "usage": parser.format_help()}, text, rt.EXIT_OK)


# ---------------------------------------------------------------------------
# Command: capabilities
# ---------------------------------------------------------------------------
def cmd_capabilities(args, ctx):
    catalog = ctx.catalog["catalog"]
    data = {
        "command_count": len(CAPABILITIES),
        "implemented_families": CAPABILITIES,
        "catalog": catalog,
        "delivery": {
            "entry_point": "tools-console-agent.py",
            "invocation": "python3 console-agent-workbench/tools-console-agent.py <command>",
            "help": "python3 console-agent-workbench/tools-console-agent.py help",
        },
    }
    if args.include_unimplemented:
        data["not_implemented"] = NOT_IMPLEMENTED
    lines = [f"console-agent-workbench {rt.VERSION} - {len(CAPABILITIES)} capability families",
             f"catalog: {catalog['category_count']} categories, {catalog['skill_count']} skills", ""]
    for fam in CAPABILITIES:
        gate_note = " [approval gated]" if fam["requires_approval"] else ""
        lines.append(f"- {fam['id']}{gate_note}: {fam['title']}")
        lines.append(f"    commands: {', '.join(fam['commands'])}")
    if args.include_unimplemented:
        lines.append("")
        lines.append("deliberately not implemented:")
        lines.extend(f"  - {item}" for item in NOT_IMPLEMENTED)
    return data, "\n".join(lines), rt.EXIT_OK


# ---------------------------------------------------------------------------
# Command: catalog
# ---------------------------------------------------------------------------
def cmd_catalog(args, ctx):
    catalog = ctx.catalog
    routing_model = catalog.get("routing_model", {})
    limit = rt.clamp_int(args.limit, "limit", 1, 5000)

    def skill_view(cat, skill):
        view = {
            "category_index": cat["index"],
            "category": cat["name"],
            "routing": cat["routing"],
            "name": skill["name"],
            "description": skill["description"],
            "implemented_by": skill.get("implemented_by"),
        }
        if skill.get("implementation_scope"):
            view["implementation_scope"] = skill["implementation_scope"]
        return view

    if args.list_categories:
        rows = [{"index": c["index"], "name": c["name"], "skill_count": c["skill_count"],
                 "routing": c["routing"],
                 "implemented_skills": sum(1 for s in c["skills"] if s.get("implemented_by"))}
                for c in catalog["categories"]]
        data = {"categories": rows[:limit], "total": len(rows),
                "routing_model": routing_model}
        lines = [f"{'#':>3}  {'skills':>6}  {'impl':>4}  {'routing':<36}  name"]
        for r in rows[:limit]:
            lines.append(f"{r['index']:>3}  {r['skill_count']:>6}  {r['implemented_skills']:>4}"
                         f"  {r['routing']:<36}  {r['name']}")
        return data, "\n".join(lines), rt.EXIT_OK

    matches = []
    if args.category:
        wanted = []
        for token in args.category:
            token = str(token).strip()
            if token.isdigit():
                idx = int(token)
                if not 1 <= idx <= len(catalog["categories"]):
                    raise rt.ValidationError(f"category index {idx} is out of range 1-{len(catalog['categories'])}")
                wanted.append(idx)
            else:
                hits = [c["index"] for c in catalog["categories"]
                        if token.lower() in c["name"].lower()]
                if not hits:
                    raise rt.ValidationError(f"no category name contains {token!r}")
                wanted.extend(hits)
        for cat in catalog["categories"]:
            if cat["index"] in wanted:
                matches.extend(skill_view(cat, s) for s in cat["skills"])
    if args.skill:
        needle = args.skill.strip().lower()
        hits = [skill_view(c, s) for c in catalog["categories"] for s in c["skills"]
                if needle in s["name"].lower()]
        if not hits:
            raise rt.ValidationError(f"no skill name contains {args.skill!r}")
        matches.extend(hits)
    if args.search:
        needle = args.search.strip().lower()
        matches.extend(skill_view(c, s) for c in catalog["categories"] for s in c["skills"]
                       if needle in s["name"].lower() or needle in s["description"].lower())
    if args.routing:
        if args.routing not in routing_model:
            raise rt.ValidationError(f"unknown routing class {args.routing!r}",
                                     ["valid: " + ", ".join(routing_model)])
        matches.extend(skill_view(c, s) for c in catalog["categories"] for s in c["skills"]
                       if c["routing"] == args.routing)
    if args.implemented_only:
        matches.extend(skill_view(c, s) for c in catalog["categories"] for s in c["skills"]
                       if s.get("implemented_by"))

    if not (args.category or args.skill or args.search or args.routing or args.implemented_only):
        by_routing = {}
        for c in catalog["categories"]:
            by_routing[c["routing"]] = by_routing.get(c["routing"], 0) + c["skill_count"]
        implemented = [skill_view(c, s) for c in catalog["categories"] for s in c["skills"]
                       if s.get("implemented_by")]
        data = {
            "catalog": catalog["catalog"],
            "categories": len(catalog["categories"]),
            "skills": sum(c["skill_count"] for c in catalog["categories"]),
            "by_routing": by_routing,
            "by_category": [{"index": c["index"], "name": c["name"], "skill_count": c["skill_count"]}
                            for c in catalog["categories"]],
            "implemented_count": len(implemented),
            "implemented": implemented[:limit],
            "routing_model": routing_model,
            "hint": "use --list-categories, --category N, --search TEXT, --skill NAME or --routing CLASS",
        }
        lines = [f"{data['categories']} categories, {data['skills']} skills, "
                 f"{data['implemented_count']} executed by this workbench", ""]
        lines.extend(f"{k}: {v} skills" for k, v in sorted(by_routing.items()))
        lines.append("")
        lines.append("use --list-categories or --search TEXT for details")
        return data, "\n".join(lines), rt.EXIT_OK

    seen, unique = set(), []
    for m in matches:
        key = (m["category_index"], m["name"])
        if key not in seen:
            seen.add(key)
            unique.append(m)
    data = {"matches": unique[:limit], "match_count": len(unique),
            "truncated": max(0, len(unique) - limit), "routing_model": routing_model}
    lines = []
    for m in unique[:limit]:
        impl = f"  -> {m['implemented_by']}" if m.get("implemented_by") else ""
        lines.append(f"[{m['category_index']:>2}] {m['name']}{impl}")
        lines.append(f"     {m['description']}")
    if not lines:
        lines = ["no matches"]
    return data, "\n".join(lines), rt.EXIT_OK


# ---------------------------------------------------------------------------
# Command: inventory
# ---------------------------------------------------------------------------
def _load_manifest(args):
    path = getattr(args, "probe_manifest", None) or rt.resource_default("asset-probe-manifest.json")
    return rt.load_json(path, "probe manifest")


def cmd_inventory(args, ctx):
    manifest = _load_manifest(args)
    priorities = {p.upper() for p in (args.priority or [])}
    licenses = rt.load_json(args.licenses, "license map") if args.licenses else {}
    specs = manifest.get("probes", [])
    if priorities:
        specs = [s for s in specs if s.get("priority", "P2").upper() in priorities]
    if args.max_probes:
        specs = specs[: rt.clamp_int(args.max_probes, "max-probes", 1, 500)]
    timeout = float(ctx.timeout or rt.DEFAULT_LIMITS["command_timeout_seconds"])
    capabilities = []
    for spec in specs:
        record = rt.probe_binary(spec, timeout=timeout)
        if licenses.get(record["capability_id"]):
            record["license_state"] = licenses[record["capability_id"]]
        if record["status"] == "FOUND":
            record["evidence_path"] = ""
        capabilities.append(record)
    hardware = None
    if not args.no_hardware:
        hardware = rt.hardware_probe()
        for cls in manifest.get("hardware_classes", []):
            present = bool(hardware.get(cls["probe"]))
            capabilities.append({
                "capability_id": f"hardware:{cls['id']}",
                "status": "FOUND" if present else cls.get("status_when_missing", "PHYSICAL HARDWARE REQUIRED"),
                "command_or_probe": f"device-class probe: {cls['probe']}",
                "detected_version": "" if present else "not detected",
                "host_type": rt.host_os(),
                "architecture": rt.host_arch(),
                "license_state": "unknown",
                "hardware_state": "present" if present else "absent",
                "required_user_material": "",
                "test_result": cls.get("note", ""),
                "evidence_path": "",
                "recommended_action": "" if present else f"connect or install: {cls['id']}",
                "priority": "P1",
                "capability_class": "hardware",
                "duration_ms": 0,
            })
    summary = {}
    for cap in capabilities:
        summary[cap["status"]] = summary.get(cap["status"], 0) + 1
    summary = dict(sorted(summary.items()))
    data = {
        "host": rt.host_info(),
        "detection_scope": ("version commands and device-node presence only; licenses and physical "
                            "device identity must be confirmed by the operator"),
        "capabilities": capabilities,
        "summary": summary,
        "total": len(capabilities),
        "licenses_source": args.licenses or "not supplied (license_state=unknown)",
    }
    if args.out:
        data["written_to"] = rt.write_json(args.out, rt.envelope("inventory", data))
    lines = [f"host: {data['host']['os']}/{data['host']['architecture']} python "
             f"{data['host']['python']}", ""]
    for cap in capabilities:
        version = f"  {cap['detected_version'][:60]}" if cap["detected_version"] else ""
        lines.append(f"{cap['status']:<36} {cap['capability_id']}{version}")
    lines.append("")
    lines.append("summary: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    code = rt.EXIT_OK if summary.get("FOUND") else rt.EXIT_CHECK_FAILED
    return data, "\n".join(lines), code


# ---------------------------------------------------------------------------
# Command: selftest
# ---------------------------------------------------------------------------
def _check(check_id, fn):
    started = time.monotonic()
    try:
        outcome = fn()
        if isinstance(outcome, tuple):
            detail, extra = outcome
        else:
            detail, extra = outcome, {}
        status = "pass"
    except AssertionError as exc:
        detail, extra = f"assertion failed: {exc}", {}
        status = "fail"
    except Exception as exc:                     # noqa: BLE001 - reported, never fatal
        detail, extra = f"{type(exc).__name__}: {exc}", {}
        status = "fail"
    return {"check_id": check_id, "status": status, "detail": detail,
            "duration_ms": int((time.monotonic() - started) * 1000), **(extra or {})}


def cmd_selftest(args, ctx):
    import hashlib
    import tempfile

    checks = []

    def check_runtime():
        major, minor = sys.version_info[:2]
        assert (major, minor) >= (3, 9), f"python {major}.{minor} is older than 3.9"
        return f"python {sys.version.split()[0]}", {"python": sys.version.split()[0]}

    def check_json():
        payload = {"a": [1, 2, 3], "b": {"c": "ü"}}
        assert json.loads(json.dumps(payload)) == payload
        assert rt.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
        return "json round-trip and canonical form stable"

    def check_hashing():
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"123456789")
            path = fh.name
        try:
            info = rt.hash_file(path, ("crc32", "md5", "sha1", "sha256", "blake2b"))
            assert info["hashes"]["crc32"] == "cbf43926", info["hashes"]["crc32"]
            assert info["hashes"]["sha256"] == hashlib.sha256(b"123456789").hexdigest()
            assert info["hashes"]["sha1"] == hashlib.sha1(b"123456789").hexdigest()
        finally:
            os.unlink(path)
        return "crc32/sha1/sha256 match reference digests for the check vector", {
            "vector": "b'123456789'", "crc32": "cbf43926"}

    def check_catalog():
        cat = ctx.catalog
        assert len(cat["categories"]) == 36, len(cat["categories"])
        total = sum(c["skill_count"] for c in cat["categories"])
        assert total == 491, total
        impl = sum(1 for c in cat["categories"] for s in c["skills"] if s.get("implemented_by"))
        return f"36 categories, 491 skills, {impl} mapped to workbench commands", {"implemented": impl}

    def check_policy():
        summary = guard.policy_summary(ctx.policy)
        assert summary["file_rules"] >= 5
        assert summary["redaction_rules"] >= 5
        return (f"policy {summary['policy_id']} with {summary['file_rules']} file rules, "
                f"{summary['content_rules']} content rules, {summary['redaction_rules']} redaction rules")

    def check_dat():
        dat = media.load_dat(rt.resource_default("asset-dat-template.json"))
        return (f"DAT {dat['dat_id']} schema loaded with {len(dat['entries'])} entries "
                f"(template is intentionally empty)")

    def check_redaction():
        # Trigger-shaped strings are assembled at runtime so this file never ships a literal
        # credential-shaped token of its own.
        fake_token = "tok" + "en=" + "abcd" + "12345678"
        sample = ("contact a.operator@example.test from /home/operator/sdk/tools "
                  + fake_token + " 10.0.0.7")
        clean, hits = guard.redact_text(sample, ctx.policy)
        assert "a.operator@example.test" not in clean, clean
        assert "/home/operator" not in clean, clean
        assert "abcd" + "12345678" not in clean, clean
        assert "10.0.0.7" not in clean, clean
        return "email, home path, credential value and IPv4 address were redacted", {"rules": hits}

    def check_ledger():
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "ledger.jsonl")
            prov.ledger_append(ledger, {"record_type": "provenance", "record_id": "r1"}, "selftest")
            prov.ledger_append(ledger, {"record_type": "provenance", "record_id": "r2"}, "selftest")
            result = prov.ledger_verify(ledger)
            assert result["valid"], result["problems"]
            assert result["entries"] == 2
        return "two-entry ledger chains and verifies"

    def check_report():
        built = report.build([], ctx.policy, {"title": "selftest", "goal": "render"})
        rendered = report.render(built, ctx.policy, "html")
        assert rendered["html"].startswith("<!DOCTYPE html>")
        assert "{{" not in rendered["markdown"]
        return "markdown and html render without unresolved placeholders"

    def check_plan():
        playbook = prov.load_playbook()
        plan = prov.build_plan("selftest goal", playbook, platform="nes")
        assert len(plan["stages"]) == 9, len(plan["stages"])
        assert plan["rollback"]
        return "nine-stage plan with rollback and stop conditions"

    def check_media_parsers():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.nes")
            header = bytearray(b"NES\x1a" + bytes([2, 1]) + bytes([0x01, 0x08]) + bytes(8))
            with open(path, "wb") as fh:
                fh.write(bytes(header) + bytes(2 * 16384 + 8192))
            result = media.inspect_file(path)
            assert result["detected_format"] == "ines", result["detected_format"]
            assert result["details"]["mapper"] == 0
            assert result["details"]["size_consistent"] is True
        return "iNES detector parses mapper, banks and size consistency", {
            "format": result["detected_format"]}

    def check_inventory_subset():
        manifest = _load_manifest(args)
        specs = [s for s in manifest["probes"] if s.get("priority") == "P0"]
        if not args.full_probes:
            specs = specs[:4]
        records = [rt.probe_binary(s, timeout=5.0) for s in specs]
        found = sum(1 for r in records if r["status"] == "FOUND")
        return (f"probed {len(records)} entries, {found} FOUND "
                f"({', '.join(r['capability_id'] for r in records if r['status'] == 'FOUND')})",
                {"probes": [{k: r[k] for k in ("capability_id", "status", "detected_version")}
                            for r in records]})

    checks.append(_check("python-runtime", check_runtime))
    checks.append(_check("json-contract", check_json))
    checks.append(_check("hashing-correctness", check_hashing))
    checks.append(_check("catalog-load", check_catalog))
    checks.append(_check("policy-load", check_policy))
    checks.append(_check("dat-load", check_dat))
    checks.append(_check("redaction-rules", check_redaction))
    checks.append(_check("ledger-chain", check_ledger))
    checks.append(_check("report-render", check_report))
    checks.append(_check("plan-build", check_plan))
    checks.append(_check("media-parsers", check_media_parsers))
    checks.append(_check("inventory-subset", check_inventory_subset))

    failed = [c for c in checks if c["status"] != "pass"]
    evidence_path = args.out or ctx.evidence(f"selftest-{rt.new_id('run')}.json")
    payload = {
        "host": rt.host_info(),
        "checks": checks,
        "summary": {"total": len(checks), "passed": len(checks) - len(failed),
                    "failed": len(failed)},
        "status_vocabulary": list(rt.STATUS_VALUES),
    }
    payload["evidence_path"] = rt.write_json(evidence_path, rt.envelope("selftest", payload))
    if args.gaps:
        gaps = []
        for cap in payload["checks"]:
            if cap["status"] != "pass":
                gaps.append(f"{cap['check_id']} failed: {cap['detail']} — "
                            f"fix this before relying on dependent capabilities")
        for probe in next((c.get("probes", []) for c in checks
                           if c["check_id"] == "inventory-subset"), []):
            if probe["status"] != "FOUND":
                gaps.append(f"{probe['capability_id']} is {probe['status']}: install it or route "
                            f"that capability to another worker")
        payload["recommendations"] = gaps
    data = payload
    lines = [f"selftest: {data['summary']['passed']}/{data['summary']['total']} checks passed",
             f"evidence: {data['evidence_path']}", ""]
    for c in checks:
        lines.append(f"{c['status']:<5} {c['check_id']:<22} {c['detail']}")
    if args.gaps and data.get("recommendations"):
        lines.append("")
        lines.extend(f"- {g}" for g in data["recommendations"])
    code = rt.EXIT_OK if not failed else rt.EXIT_CHECK_FAILED
    return data, "\n".join(lines), code


# ---------------------------------------------------------------------------
# Command: route
# ---------------------------------------------------------------------------
def cmd_route(args, ctx):
    path = args.worker_profiles or rt.resource_default("asset-worker-profiles.json")
    table = rt.load_json(path, "worker profile table")
    workers = table["workers"]
    labels = table.get("requirement_labels", {})
    if args.list_profiles:
        data = {"profiles": workers, "requirement_labels": labels}
        lines = [f"{w['profile_id']:<32} {w.get('host_type', 'any'):<8} "
                 f"requires: {', '.join(w.get('requires', [])) or 'nothing'}" for w in workers]
        return data, "\n".join(lines), rt.EXIT_OK
    requirements = [r.strip().lower() for r in (args.require or [])]
    hardware = rt.hardware_probe()
    host = rt.host_os()
    platform = (args.platform or "").lower() or None
    candidates = list(workers)
    if args.official:
        candidates = [w for w in candidates if w.get("official_program")]
        if not candidates:
            raise rt.ValidationError("no official program workers are defined in the profile table")

    r_table = table.get("platform_routing", {})
    preferred_id = r_table.get(platform) if platform else None
    if platform and not preferred_id and requirements:
        preferred_id = None
    elif platform and not preferred_id:
        raise rt.ValidationError(
            f"platform {platform!r} is not in the routing table",
            ["known platforms: " + ", ".join(sorted(r_table))])

    scored = []
    for w in candidates:
        score = 0
        if w["profile_id"] == preferred_id:
            score += 100
        provides = set(w.get("provides", []))
        score += 10 * len(provides & set(requirements))
        if not requirements and not preferred_id:
            score += 1 if w["profile_id"] == "linux-x86_64-core" else 0
        scored.append((score, w))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["profile_id"]))
    best = scored[0][1] if scored and scored[0][0] > 0 else None
    if best is None:
        raise rt.ValidationError(
            "no worker profile matches the requested requirements",
            ["available capability classes: " + ", ".join(sorted(
                {p for w in workers for p in w.get("provides", [])}))])

    def unmet_for(worker):
        unmet = []
        for req in worker.get("requires", []):
            if req in ("windows-host", "macos-host"):
                if host != req.split("-")[0]:
                    unmet.append({"requirement": req, "status": labels.get(req, req.upper())})
            elif req == "gpu":
                if not hardware["gpu_present"]:
                    unmet.append({"requirement": req, "status": labels.get(req, "GPU REQUIRED")})
            elif req in ("cartridge-reader", "optical-drive", "capture-device", "user-media"):
                key = {"cartridge-reader": "serial_reader_present",
                       "optical-drive": "optical_drive_present",
                       "capture-device": "usb_device_count",
                       "user-media": None}[req]
                present = bool(hardware.get(key)) if key else False
                if not present:
                    unmet.append({"requirement": req,
                                  "status": labels.get(req, "PHYSICAL HARDWARE REQUIRED")})
            else:
                unmet.append({"requirement": req,
                              "status": labels.get(req, req.replace("-", " ").upper())})
        return unmet

    unmet = unmet_for(best)
    missing_from_profile = []
    for req in requirements:
        if req not in best.get("provides", []):
            missing_from_profile.append(req)
    official_required = bool(platform and platform in table.get("modern_official_platforms", []))
    if official_required and not best.get("official_program"):
        official_required = True
    data = {
        "request": {"platform": platform, "requires": requirements, "official_only": args.official},
        "host": rt.host_info(),
        "hardware": hardware,
        "selected_profile": best,
        "status": "ready" if not unmet else "requirements-unmet",
        "unmet_requirements": unmet,
        "requirements_not_provided": missing_from_profile,
        "official_program_required": official_required,
        "alternatives": [{"profile_id": w["profile_id"], "score": s}
                         for s, w in scored[1:4]],
        "approval_notes": [
            "official program access, licensed SDKs and devkits are granted per account and never substituted",
            "confirm device identity yourself; presence of a device node does not prove which console is attached",
        ],
    }
    if args.out:
        data["written_to"] = rt.write_json(args.out, rt.envelope("route", data))
    lines = [f"selected: {best['profile_id']} ({best['name']})"]
    if best.get("official_program"):
        lines.append(f"official program: {best['official_program']}")
    if unmet:
        lines.append("unmet requirements:")
        lines.extend(f"  - {u['requirement']}: {u['status']}" for u in unmet)
    if data["alternatives"]:
        lines.append("alternatives: " + ", ".join(a["profile_id"] for a in data["alternatives"]))
    lines.append(best.get("notes", ""))
    code = rt.EXIT_OK if not unmet else rt.EXIT_CHECK_FAILED
    return data, "\n".join(lines), code


# ---------------------------------------------------------------------------
# Command: gate
# ---------------------------------------------------------------------------
def _scope_entries(paths):
    entries = []
    for raw in paths or []:
        resolved = rt.require_existing(raw, "approval scope path")
        if os.path.isfile(resolved):
            rel = os.path.basename(resolved)
            entries.append({"path": rel, "size": os.path.getsize(resolved)})
        else:
            base = resolved
            for abs_path, rel, _ in rt.walk_bounded(base):
                if os.path.isfile(abs_path):
                    entries.append({"path": rel.replace(os.sep, "/"),
                                    "size": os.path.getsize(abs_path)})
    return entries


def cmd_gate(args, ctx):
    policy = ctx.policy
    if args.action == "authorize":
        record = guard.create_authorization(
            operator=args.operator, items=args.material, statement=args.statement,
            ttl_hours=args.ttl_hours, notes="", attest=args.attest)
        path = args.out or os.path.join(rt.workdir(ctx.workdir, "authz"),
                                        f"{record['record_id']}.json")
        written = rt.write_json(path, record)
        data = {"record": record, "written_to": written,
                "next": "pass --record to `gate --action verify` when another step needs it"}
        lines = [f"authorization {record['record_id']} for {record['operator']}",
                 f"materials: {', '.join(i['material'] + ':' + i['reference'] for i in record['items'])}",
                 f"expires: {record['expires_at']}", f"written: {written}"]
        return data, "\n".join(lines), rt.EXIT_OK
    if args.action == "verify":
        if not args.record:
            raise rt.ValidationError("--record is required for gate --action verify")
        record = rt.load_json(args.record, "authorization record")
        result = guard.verify_authorization(record, required=args.requires)
        data = result
        lines = [f"authorization valid: {result['valid']}"]
        if result.get("operator"):
            lines.append(f"operator: {result['operator']}  expires: {result.get('expires_at')}")
        lines.extend(f"  - {p}" for p in result["problems"])
        return data, "\n".join(lines), (rt.EXIT_OK if result["valid"] else rt.EXIT_POLICY_BLOCK)
    if args.action == "list":
        directory = rt.require_existing(args.dir or rt.workdir(ctx.workdir, "authz"),
                                        "record directory", kind="dir")
        records = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            try:
                rec = rt.load_json(os.path.join(directory, name), "record")
            except rt.ValidationError:
                continue
            records.append({"file": name, "record_type": rec.get("record_type"),
                            "record_id": rec.get("record_id") or rec.get("approval_id"),
                            "operator": rec.get("operator"),
                            "expires_at": rec.get("expires_at"),
                            "valid": (guard.verify_authorization(rec)["valid"]
                                      if rec.get("record_type") == "authorization" else None)})
        data = {"directory": directory, "records": records, "count": len(records)}
        lines = [f"{directory}: {len(records)} record(s)"] + [
            f"  {r['record_type']:<14} {r['record_id']} {r['operator']} exp={r['expires_at']}"
            for r in records]
        return data, "\n".join(lines), rt.EXIT_OK
    entries = _scope_entries(args.scope)
    if args.action == "approve":
        if not args.gated_action:
            raise rt.ValidationError("--gated-action is required for approve")
        record = guard.create_approval(policy, args.gated_action, entries, args.operator,
                                       ttl_minutes=args.ttl_minutes)
        path = args.out or os.path.join(rt.workdir(ctx.workdir, "approvals"),
                                        f"{record['approval_id']}.json")
        written = rt.write_json(path, record)
        data = {"approval": record, "written_to": written, "scope_entries": len(entries)}
        lines = [f"approval {record['approval_id']} for {record['action']} over {len(entries)} path(s)",
                 f"operator: {record['operator']}  expires: {record['expires_at']}",
                 f"written: {written}"]
        return data, "\n".join(lines), rt.EXIT_OK

    # check-approval
    if not args.approval or not args.gated_action:
        raise rt.ValidationError("--approval and --gated-action are required for check-approval")
    record = rt.load_json(args.approval, "approval token")
    result = guard.verify_approval(policy, record, args.gated_action, entries)
    data = result
    lines = [f"approval valid: {result['valid']}"]
    lines.extend(f"  - {p}" for p in result["problems"])
    return data, "\n".join(lines), (rt.EXIT_OK if result["valid"] else rt.EXIT_APPROVAL)


# ---------------------------------------------------------------------------
# Command: scan / redact
# ---------------------------------------------------------------------------
def cmd_scan(args, ctx):
    limits = ctx.limits()
    limits.update({k: v for k, v in {
        "max_files": args.max_files, "max_depth": args.max_depth,
        "max_read_bytes": args.max_read_bytes,
    }.items() if v is not None})
    limits["follow_symlinks"] = bool(args.follow_symlinks)
    policy = ctx.policy
    if args.max_files:
        rt.clamp_int(args.max_files, "max-files", 1, 1000000)
    result = guard.scan(args.path, policy, mode=args.mode, limits=limits,
                        timeout_seconds=ctx.timeout)
    if args.out:
        result["written_to"] = rt.write_json(args.out, rt.envelope("scan", result))
    lines = [f"mode {result['mode']}: verdict {result['verdict'].upper()} "
             f"({result['files_scanned']} files scanned, {result['finding_count']} findings)"]
    for f in result["findings"][:40]:
        lines.append(f"  [{f['severity']:<8}] {f['rule_id']:<28} {f['path']}")
    if result["finding_count"] > 40:
        lines.append(f"  ... {result['finding_count'] - 40} more (see JSON output)")
    if result["skipped"]:
        lines.append(f"  {len(result['skipped'])} file(s) skipped (limits or unreadable)")
    code = rt.EXIT_POLICY_BLOCK if result["verdict"] == "block" else rt.EXIT_OK
    warnings = []
    if result["verdict"] == "review":
        warnings.append("verdict is 'review': the operator must decide before packaging or sharing")
    return result, "\n".join(lines), code, warnings


def cmd_redact(args, ctx):
    if not args.check and not args.out:
        raise rt.ValidationError("--out is required unless --check is used")
    result = guard.redact_file(args.infile, args.out, ctx.policy, check_only=args.check)
    lines = [f"{'would redact' if args.check else 'redacted'}: {result['total_hits']} hit(s) "
             f"in {result['characters']} characters"]
    for rule, count in sorted(result["hits"].items()):
        lines.append(f"  {rule}: {count}")
    if result.get("output"):
        lines.append(f"written: {result['output']}")
    code = rt.EXIT_OK if result["clean"] or not args.check else rt.EXIT_POLICY_BLOCK
    return result, "\n".join(lines), code


# ---------------------------------------------------------------------------
# Command: hash / inspect / verify
# ---------------------------------------------------------------------------
def cmd_hash(args, ctx):
    algorithms = tuple(args.algorithm) if args.algorithm else ("crc32", "md5", "sha1", "sha256")
    for algo in algorithms:
        if algo.lower() not in rt.ALGORITHMS:
            raise rt.ValidationError(f"unknown algorithm {algo!r}",
                                     ["available: " + ", ".join(rt.ALGORITHMS)])
    result = media.manifest(
        args.path, algorithms=tuple(a.lower() for a in algorithms),
        recursive=not args.no_recursive, extensions=args.ext, limits=ctx.limits(),
        hash_cap=args.hash_cap, deadline=_deadline(ctx))
    if args.out:
        result["written_to"] = rt.write_json(args.out, rt.envelope("hash", result))
    lines = [f"{result['file_count']} file(s), {result['total_human']}, "
             f"manifest sha256 {result['manifest_sha256'][:16]}"]
    for entry in result["entries"][:25]:
        digest = entry["hashes"].get("sha256") or "(not hashed)"
        lines.append(f"  {digest[:16]}  {entry['size']:>12}  {entry['path']}")
    if result["file_count"] > 25:
        lines.append(f"  ... {result['file_count'] - 25} more")
    if result["skipped"]:
        lines.append(f"  {len(result['skipped'])} skipped")
    return result, "\n".join(lines), rt.EXIT_OK


def cmd_inspect(args, ctx):
    hash_cap = media.DEFAULT_HASH_CAP if args.hash_cap is None else args.hash_cap
    artifacts = []
    for raw in args.path:
        artifacts.append(media.inspect_file(raw, hash_cap=hash_cap,
                                            do_hash=not args.no_hash))
    data = {"artifacts": artifacts, "count": len(artifacts),
            "confidence_note": ("confidence is the detector's own certainty; 'unknown' means no "
                                "documented header matched")}
    if args.out:
        data["written_to"] = rt.write_json(args.out, rt.envelope("inspect", data))
    lines = []
    for art in artifacts:
        lines.append(f"{art['file_name']}: {art['detected_format']} / {art['platform']} "
                     f"(confidence {art['confidence']}) {art['size_human']}")
        for key, value in art["details"].items():
            if isinstance(value, (dict, list)):
                continue
            lines.append(f"    {key}: {value}")
        for warn in art["warnings"]:
            lines.append(f"    ! {warn}")
        for step in art["next_steps"]:
            lines.append(f"    > {step}")
    unknown = [a for a in artifacts if a["detected_format"] == "unknown"]
    code = rt.EXIT_OK if not unknown else rt.EXIT_CHECK_FAILED
    warnings = [f"{len(unknown)} artifact(s) could not be identified"] if unknown else []
    return data, "\n".join(lines), code, warnings


def cmd_verify(args, ctx):
    if args.crosscheck:
        if args.dat:
            raise rt.ValidationError("--crosscheck and --dat are mutually exclusive")
        result = media.crosscheck(args.path, block_size=args.block_size)
        result = dict(result, mode="crosscheck")
        code = rt.EXIT_OK if result["consistent"] else rt.EXIT_CHECK_FAILED
        lines = [f"cross-check: {'consistent' if result['consistent'] else 'DIVERGENT'} "
                 f"across {len(result['files'])} read(s)"]
        for cmp in result["comparisons"]:
            lines.append(f"  {cmp['candidate']} vs {cmp['reference']}: "
                         f"{'identical' if cmp['identical'] else 'differs'} "
                         f"(first diff at {cmp['first_difference_offset']}, "
                         f"{cmp['differing_blocks']} differing block(s))")
        lines.append(result["interpretation"])
    else:
        dat_path = args.dat or rt.resource_default("asset-dat-template.json")
        dat = media.load_dat(dat_path)
        result = media.verify_against_dat(args.path, dat, limits=ctx.limits())
        result = dict(result, mode="dat")
        failed = [r for r in result["results"] if not r["status"].startswith("verified")]
        code = rt.EXIT_OK if (not failed or not args.require_match) else rt.EXIT_CHECK_FAILED
        lines = [f"DAT {result['dat_name']} ({result['dat_version']}): "
                 f"{result['verified']}/{result['files_checked']} verified, "
                 f"{result['unknown']} unknown, {result['problems']} problem(s)"]
        for rec in result["results"][:40]:
            name = (rec.get("match") or {}).get("name", "")
            lines.append(f"  {rec['status']:<16} {rec['path']}{(' -> ' + name) if name else ''}")
        if not dat["entries"]:
            lines.append("  note: the packaged DAT template has no entries; add your own trusted "
                         "hashes before relying on verification results")
    if args.out:
        result["written_to"] = rt.write_json(args.out, rt.envelope("verify", result))
    return result, "\n".join(lines), code


# ---------------------------------------------------------------------------
# Command: provenance / plan
# ---------------------------------------------------------------------------
def cmd_provenance(args, ctx):
    schema = prov.load_schema(args.schema)
    if args.action == "create":
        if not args.material:
            raise rt.ValidationError("--material is required for provenance create")
        if not args.operator:
            raise rt.ValidationError("--operator is required for provenance create")
        software = rt.parse_pairs(args.software_version, "software-version")
        inspect = media.inspect_file(args.material, do_hash=False)
        record = prov.build_record(
            operator=args.operator, material_path=args.material, media_type=args.media_type,
            region=args.region, revision=args.revision, ownership=args.ownership,
            method=args.method, reader=args.reader, hardware=args.hardware,
            firmware=args.firmware, software_versions=software, reads=args.reads,
            source=args.source, notes=args.notes, redistribution=args.redistribution,
            attribution=args.attribution, material_inspect=inspect)
        problems = prov.validate(record, schema)
        if problems:
            raise rt.ValidationError("built record failed schema validation", [
                f"{p['path']}: {p['message']}" for p in problems])
        path = args.out or os.path.join(rt.workdir(ctx.workdir, "records"),
                                        f"{record['record_id']}.json")
        written = rt.write_json(path, record)
        data = {"record": record, "written_to": written, "schema_valid": True}
        appended = None
        if args.ledger:
            appended = prov.ledger_append(args.ledger, record, args.operator)
            data["ledger"] = appended
        lines = [f"record {record['record_id']} for {record['material']['reference']}",
                 f"sha256 {record['integrity']['sha256']}  size {record['integrity']['size']}",
                 f"detected: {record['material'].get('detected_format')} "
                 f"({record['material'].get('platform')})",
                 f"written: {written}"]
        if appended:
            lines.append(f"ledger: {appended['ledger']} seq {appended['seq']}")
        return data, "\n".join(lines), rt.EXIT_OK

    if args.action == "validate":
        if not args.record:
            raise rt.ValidationError("--record is required for provenance validate")
        record = rt.load_json(args.record, "provenance record")
        problems = prov.validate(record, schema)
        data = {"record": rt.resolve_input(args.record), "valid": not problems,
                "problems": problems}
        lines = [f"{'valid' if not problems else 'INVALID'}: {args.record}"]
        lines.extend(f"  {p['path']}: {p['message']}" for p in problems)
        return data, "\n".join(lines), (rt.EXIT_OK if not problems else rt.EXIT_USAGE)

    if args.action == "append":
        if not args.record or not args.ledger:
            raise rt.ValidationError("--record and --ledger are required for provenance append")
        record = rt.load_json(args.record, "provenance record")
        problems = prov.validate(record, schema)
        if problems:
            raise rt.ValidationError("record failed schema validation; refusing to append", [
                f"{p['path']}: {p['message']}" for p in problems])
        result = prov.ledger_append(args.ledger, record, args.operator or "unspecified")
        lines = [f"appended seq {result['seq']} to {result['ledger']}",
                 f"entry {result['entry_sha256'][:16]} prev {result['prev_sha256'][:16]}"]
        return result, "\n".join(lines), rt.EXIT_OK

    if args.action == "verify-ledger":
        if not args.ledger:
            raise rt.ValidationError("--ledger is required for provenance verify-ledger")
        result = prov.ledger_verify(args.ledger)
        lines = [f"{result['entries']} entries, chain {'valid' if result['valid'] else 'BROKEN'}"]
        lines.extend(f"  seq {p['seq']}: {p['problem']}" for p in result["problems"])
        return result, "\n".join(lines), (rt.EXIT_OK if result["valid"] else rt.EXIT_CHECK_FAILED)

    if not args.ledger:
        raise rt.ValidationError("--ledger is required for provenance list")
    result = prov.ledger_verify(args.ledger)
    lines = [f"{result['entries']} record(s) in {result['ledger']}"]
    for rec in result["records"]:
        lines.append(f"  {rec['seq']:>3} {rec['record_type']:<12} {rec['record_id']} "
                     f"{str(rec['sha256'])[:16]}")
    return result, "\n".join(lines), (rt.EXIT_OK if result["valid"] else rt.EXIT_CHECK_FAILED)


def cmd_plan(args, ctx):
    playbook = prov.load_playbook(args.playbook)
    if args.decompose:
        if not args.goal:
            raise rt.ValidationError("--goal is required with --decompose")
        data = {
            "goal": args.goal,
            "stages": [{"stage": s["id"], "objective": s["objective"],
                        "checklist": s["checklist"]} for s in playbook["stages"]],
            "decomposition": list(prov.DECOMPOSITION_STAGES),
        }
        lines = [f"decomposition of: {args.goal}"]
        for stage in data["stages"]:
            lines.append(f"\n{stage['stage'].upper()} - {stage['objective']}")
            lines.extend(f"  - {c}" for c in stage["checklist"])
        return data, "\n".join(lines), rt.EXIT_OK
    plan = prov.build_plan(args.goal, playbook, platform=args.platform, operator=args.operator,
                           risk=args.risk, stages=args.stage)
    if args.out:
        plan["written_to"] = rt.write_json(args.out, rt.envelope("plan", plan))
    lines = [f"plan {plan['plan_id']}: {plan['goal']} (risk {plan['risk']})"]
    for stage in plan["stages"]:
        gate_note = " [approval]" if stage["approval_required"] else ""
        lines.append(f"\n{stage['stage'].upper()}{gate_note} - {stage['objective']}")
        lines.extend(f"  - {c}" for c in stage["checklist"])
        if stage.get("tool_hint"):
            lines.append(f"  tool: {stage['tool_hint']}")
    if plan["platform_notes"]:
        lines.append(f"\nplatform notes: {plan['platform_notes']}")
    lines.append("\nstop conditions:")
    lines.extend(f"  - {s}" for s in plan["stop_conditions"])
    lines.append("rollback:")
    lines.extend(f"  - {s}" for s in plan["rollback"])
    return plan, "\n".join(lines), rt.EXIT_OK


# ---------------------------------------------------------------------------
# Command: report / retention
# ---------------------------------------------------------------------------
def cmd_report(args, ctx):
    policy = ctx.policy
    meta = {
        "title": args.title,
        "operator": args.operator,
        "goal": args.goal,
        "rights": args.rights,
        "minimal": not args.allow_content,
        "limitations": args.limitation or [],
        "next_steps": args.next_step or [],
    }
    built = report.build(args.input or [], policy, meta)
    rendered = report.render(built, policy, fmt=args.report_format, template_path=args.template)
    extension = {"md": "md", "html": "html", "json": "json"}[args.report_format]
    out = args.out or os.path.join(rt.workdir(ctx.workdir, "reports"),
                                   f"{built['report_id']}.{extension}")
    if args.report_format == "json":
        payload = rendered["report"]
        payload["render"] = {"format": "json", "redactions": rendered["redactions"]}
        written = rt.write_json(out, payload)
    else:
        body = rendered["markdown"] if args.report_format == "md" else rendered["html"]
        written = rt.write_text(out, body)
    data = {
        "report_id": built["report_id"],
        "format": args.report_format,
        "written_to": written,
        "sections": [k for k, v in built["sections"].items() if v],
        "sources": built["sources"],
        "redactions": rendered["redactions"],
        "markdown": rendered["markdown"] if args.report_format != "json" else None,
    }
    lines = [f"report {built['report_id']} ({args.report_format}) -> {written}",
             f"sections: {', '.join(data['sections']) or 'none'}",
             f"inputs: {len(built['sources'])}"]
    if rendered["redactions"]:
        lines.append("redactions: " + ", ".join(f"{k}={v}" for k, v in rendered["redactions"].items()))
    return data, "\n".join(lines), rt.EXIT_OK


def cmd_retention(args, ctx):
    policy = ctx.policy
    if args.action == "policy":
        data = {"retention": policy["retention"],
                "approval_required_actions": policy["approval_required_actions"]}
        lines = ["retention classes:"]
        for cls in policy["retention"]["classes"]:
            lines.append(f"  {cls['id']:<10} {cls['days']:>5} days  {', '.join(cls['extensions'])}")
        lines.append(f"  default    {policy['retention']['default_days']:>5} days")
        return data, "\n".join(lines), rt.EXIT_OK

    if args.action == "restore":
        if not args.dir:
            raise rt.ValidationError("--dir is required for retention restore")
        result = prov.retention_restore(args.dir)
        lines = [f"restored {result['restored_count']} file(s) to {result['root']}"]
        lines.extend(f"  ! {p['path']}: {p['problem']}" for p in result["problems"])
        return result, "\n".join(lines), (rt.EXIT_OK if not result["problems"] else rt.EXIT_CHECK_FAILED)

    if not args.dir:
        raise rt.ValidationError("--dir is required for retention evaluate/apply")
    now = rt.parse_iso(args.now) if args.now else rt.utc_now()
    evaluation = prov.retention_evaluate(args.dir, policy, now=now)
    if args.action == "evaluate":
        if args.out:
            evaluation["written_to"] = rt.write_json(args.out, rt.envelope("retention", evaluation))
        lines = [f"{evaluation['file_count']} file(s) evaluated, {evaluation['due_count']} due "
                 f"({rt.human_bytes(evaluation['due_bytes'])}) as of {evaluation['evaluated_at']}"]
        for f in evaluation["files"][:40]:
            lines.append(f"  {f['status']:<5} {f['class']:<10} {f['age_days']:>5}d "
                         f"/{f['retention_days']}d  {f['path']}")
        return evaluation, "\n".join(lines), rt.EXIT_OK

    # apply
    action = "retention-hard-delete" if args.hard_delete else "retention-apply"
    entries = [{"path": f["path"], "size": f["size"]}
               for f in evaluation["files"] if f["status"] == "due"]
    approval = guard.require_approval(policy, action, args.approval, entries)
    result = prov.retention_apply(args.dir, evaluation, policy, hard_delete=args.hard_delete)
    result["approval"] = approval
    if args.out:
        result["written_to"] = rt.write_json(args.out, rt.envelope("retention", result))
    lines = [f"{result['mode']}: {result['moved_count']} file(s) "
             f"({rt.human_bytes(result['moved_bytes'])})"]
    if result.get("quarantine"):
        lines.append(f"quarantine: {result['quarantine']} (restore with "
                     f"retention --action restore --dir {result['quarantine']})")
    return result, "\n".join(lines), rt.EXIT_OK


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
HANDLERS = {
    "help": cmd_help,
    "capabilities": cmd_capabilities,
    "catalog": cmd_catalog,
    "inventory": cmd_inventory,
    "selftest": cmd_selftest,
    "route": cmd_route,
    "gate": cmd_gate,
    "scan": cmd_scan,
    "redact": cmd_redact,
    "hash": cmd_hash,
    "inspect": cmd_inspect,
    "verify": cmd_verify,
    "provenance": cmd_provenance,
    "plan": cmd_plan,
    "report": cmd_report,
    "retention": cmd_retention,
}


GLOBAL_DEFAULTS = {
    "format": "json",
    "workdir": "caw-work",
    "policy": None,
    "catalog": None,
    "timeout": None,
    "verbose": False,
}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    for key, value in GLOBAL_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if not args.command:
        parser.print_help()
        return rt.EXIT_USAGE
    handler = HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        return rt.EXIT_USAGE
    ctx = Context(args)
    fmt = "text" if args.command == "help" else getattr(args, "format", "json")
    started = time.monotonic()
    try:
        outcome = handler(args, ctx)
        if len(outcome) == 4:
            data, human, code, warnings = outcome
        else:
            data, human, code = outcome
            warnings = []
        body = rt.envelope(args.command, data, code=code, warnings=warnings)
        body["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return rt.emit(body, human, fmt=fmt)
    except rt.ValidationError as exc:
        body = rt.envelope(args.command, {}, code=rt.EXIT_USAGE,
                           errors=[exc.message] + list(exc.details))
        return rt.emit(body, f"invalid input: {exc.message}", fmt=fmt)
    except rt.NotFoundError as exc:
        body = rt.envelope(args.command, {}, code=rt.EXIT_NOT_FOUND, errors=[str(exc)])
        return rt.emit(body, f"not found: {exc}", fmt=fmt)
    except rt.ApprovalRequired as exc:
        body = rt.envelope(args.command, {"gated_action": exc.action,
                                          "problems": exc.problems},
                           code=rt.EXIT_APPROVAL, errors=[exc.message])
        return rt.emit(body, f"approval required: {exc.message}", fmt=fmt)
    except KeyboardInterrupt:
        body = rt.envelope(args.command, {}, code=130, errors=["cancelled by operator"])
        return rt.emit(body, "cancelled", fmt=fmt)
    except Exception as exc:                     # noqa: BLE001 - never leak a traceback by default
        import traceback
        detail = traceback.format_exc() if getattr(args, "verbose", False) else str(exc)
        body = rt.envelope(args.command, {}, code=rt.EXIT_UNEXPECTED,
                           errors=[f"{type(exc).__name__}: {detail}"])
        return rt.emit(body, f"unexpected error: {type(exc).__name__}: {exc}", fmt=fmt)


if __name__ == "__main__":
    sys.exit(main())
