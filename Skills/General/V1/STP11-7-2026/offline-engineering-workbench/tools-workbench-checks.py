#!/usr/bin/env python3
"""Offline engineering checks for the Offline Engineering Workbench.

Runs deterministic, local-only audits over a project tree. No network access,
no subprocess execution, no writes to the audited tree.

Rule families map back to the catalog categories in asset-skill-catalog.json:
  security/*                  -> category 29 (Security, Vulnerability, Secrets, SBOM)
  deps/*                      -> categories 1, 10 (supply chain, reproducible builds)
  build/*                     -> categories 1, 10, 17 (offline build, containers)
  tests/*                     -> category 25 (Unit, Integration, and System Testing)
  data/*                      -> categories 15, 16, 24 (pipelines, quality, domain data)
  repro/*                     -> categories 10, 30 (reproducible builds, quality gates)
  supply-chain/*              -> categories 1, 11, 29
  docs/*                      -> category 31 (Documentation and developer experience)
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

def _load_core():
    """Import the sibling core module without relying on package installation."""
    existing = sys.modules.get("wb_core")
    if existing is not None and getattr(existing, "VERSION", None):
        return existing
    path = Path(__file__).resolve().parent / "tools-workbench-core.py"
    spec = importlib.util.spec_from_file_location("wb_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load tools-workbench-core.py at %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wb_core"] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


SEVERITIES = core.SEVERITIES
SEVERITY_RANK = core.SEVERITY_RANK
WorkbenchError = core.WorkbenchError
EXIT_USAGE = core.EXIT_USAGE

# Re-export the shared helpers used throughout the rule implementations.
DATA_EXTENSIONS = core.DATA_EXTENSIONS
SOURCE_EXTENSIONS = core.SOURCE_EXTENSIONS
MANIFEST_NAMES = core.MANIFEST_NAMES
LOCKFILES = core.LOCKFILES
redact = core.redact
format_bytes = core.format_bytes
sha256_bytes = core.sha256_bytes
build_environment = core.build_environment
load_catalog = core.load_catalog
now_iso = core.now_iso
read_text = core.read_text
is_text_candidate = core.is_text_candidate
iter_project_files = core.iter_project_files
has_test_signal = core.has_test_signal


# --------------------------------------------------------------------------- #
# Rule definitions
# --------------------------------------------------------------------------- #

def _rule(rule_id: str, title: str, severity: str, category: str, catalog: Sequence[int],
          applies: Sequence[str], pattern: str, message: str, remediation: str,
          exclude_path: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": rule_id,
        "title": title,
        "severity": severity,
        "category": category,
        "catalog_categories": list(catalog),
        "applies": list(applies),
        "pattern": re.compile(pattern),
        "message": message,
        "remediation": remediation,
        "exclude_path": re.compile(exclude_path) if exclude_path else None,
    }


BUILD_FILES = ["*.sh", "*.bash", "*.zsh", "*.ps1", "*.bat", "*.cmd", "Dockerfile*",
               "*.dockerfile", "Makefile*", "*.mk", "*.yml", "*.yaml", "*.toml",
               "*.cfg", "*.ini", "*.conf"]
CONFIG_FILES = ["*.py", "*.js", "*.ts", "*.json", "*.yml", "*.yaml", "*.toml",
                "*.ini", "*.cfg", "*.conf", "*.sh", "*.ps1"]

PATTERN_RULES: List[Dict[str, Any]] = [
    _rule(
        "security/embedded-secret", "Embedded credential in a tracked file", "critical",
        "security", [29], ["*"],
        r"(?i)\b(?:aws_access_key_id|aws_secret_access_key|api[_-]?key|secret[_-]?key|"
        r"password|passwd|access_token|auth_token|client_secret)\b\s*[:=]\s*[\"'][^\"']{6,}[\"']",
        "A credential-looking literal is committed in plaintext.",
        "Remove the literal, rotate the credential if it was ever real, and load it from an "
        "offline secret store or an ignored local env file.",
        exclude_path=r"(?:\.example|\.sample|\.template|fixture|testdata|test_data|docs?/)",
    ),
    _rule(
        "security/private-key-material", "Private key material in the project", "critical",
        "security", [29], ["*"],
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
        "A PEM private key block appears to be stored in the project.",
        "Move the key to a protected offline secret store, remove it from history, and issue a "
        "replacement key if it may have been exposed.",
        exclude_path=r"(?:\.example|\.sample|\.template|fixture|testdata|test_data)",
    ),
    _rule(
        "security/weak-hash", "Weak cryptographic hash", "medium", "security", [29],
        ["*.py", "*.js", "*.ts", "*.java", "*.cs", "*.go", "*.rs", "*.rb", "*.php"],
        r"(?i)\bhashlib\.(?:md5|sha1)\b|\bmd5\s*\(|\bsha1\s*\(",
        "MD5 or SHA-1 is used; both are unsuitable for integrity or security decisions.",
        "Use SHA-256 or stronger from the standard library or a reviewed local library.",
    ),
    _rule(
        "security/dynamic-code-execution", "Dynamic code execution", "high", "security", [29],
        ["*.py", "*.js", "*.ts", "*.php", "*.rb"],
        r"(?i)(?:\beval\s*\(|\bexec\s*\(|new\s+Function\s*\()",
        "eval/exec-style dynamic execution can run untrusted project data as code.",
        "Replace with an explicit parser, dispatch table, or allowlisted operation map.",
    ),
    _rule(
        "security/debug-enabled", "Debug mode enabled by default", "low", "security", [29],
        CONFIG_FILES,
        r"(?i)(?:DEBUG\s*=\s*True|debug\s*:\s*true|debug\s*=\s*1\b)",
        "Debug mode is enabled, which can leak stack traces or internal paths.",
        "Default debug to false and enable it only through an explicit local override.",
    ),
    _rule(
        "build/network-fetch", "Network fetch in build or install path", "high", "build", [1, 10],
        BUILD_FILES,
        r"(?i)(?:\b(?:curl|wget)\b[^\n]{0,90}https?://|"
        r"\b(?:pip3?|pipx|npm|pnpm|yarn|conda|mamba)\s+(?:install|add)\b|"
        r"\bapt-get\s+install\b|\bgit\s+clone\s+https?://)",
        "A build or install step reaches the network, which breaks air-gapped reproducibility.",
        "Consume a local mirror or wheelhouse and pin exact versions; add a clean-state install "
        "test that runs with networking disabled.",
        exclude_path=r"(?:docs?/|README|\.md$)",
    ),
    _rule(
        "build/absolute-path", "Machine-specific absolute path", "medium", "build", [1, 10],
        BUILD_FILES + CONFIG_FILES,
        r"(?:^|[\s\"'=])(?:/(?:home|Users|root|opt|var|tmp|etc)/|[A-Za-z]:\\\\)",
        "An absolute host path makes the build non-portable across offline workstations.",
        "Resolve paths from the project root or a documented environment variable.",
        exclude_path=r"(?:^|/)docs?/",
    ),
    _rule(
        "build/destructive-command", "Destructive command in automation", "critical", "build", [8, 10],
        ["*.sh", "*.bash", "*.zsh", "*.ps1", "*.bat", "*.cmd", "Makefile*", "*.mk", "*.yml", "*.yaml"],
        r"(?i)(?:\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b|\bmkfs(?:\.[a-z0-9]+)?\b|\bdd\s+if=|"
        r"\bshutdown\b|\breboot\b|\bchmod\s+777\b)",
        "Automation contains an irreversible or privilege-broadening command.",
        "Scope the command to a temporary directory, add a confirmation gate, and test the "
        "rollback path before use.",
    ),
    _rule(
        "build/privilege-escalation", "Privilege escalation in automation", "high", "build", [8],
        ["*.sh", "*.bash", "*.zsh", "*.ps1", "Makefile*", "*.mk", "*.yml", "*.yaml"],
        r"(?i)(?:^|\s)sudo\s",
        "Automation invokes sudo, which couples the build to host privileges.",
        "Run the step as an unprivileged user with a narrowly scoped local service account.",
    ),
    _rule(
        "build/download-pipe", "Download piped into a shell", "critical", "build", [1, 29],
        ["*.sh", "*.bash", "*.zsh", "*.ps1", "Dockerfile*", "*.dockerfile", "*.yml", "*.yaml"],
        r"(?i)(?:curl|wget)[^\n|]{0,140}\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b",
        "A remote script is piped directly into a shell, so its content is unverified.",
        "Download to a pinned, hash-verified local artifact first, then execute it after review.",
    ),
    _rule(
        "repro/nondeterministic-time", "Wall-clock time in build or data code", "low", "repro", [10, 30],
        ["*.py", "*.js", "*.ts", "*.java", "*.cs", "*.go", "*.rs", "*.sh", "*.ps1"],
        r"(?i)(?:datetime\.now\(|datetime\.utcnow\(|time\.time\(|Date\.now\()",
        "Wall-clock reads make output non-reproducible unless the value is injected.",
        "Inject a fixed timestamp or SOURCE_DATE_EPOCH for builds and record it in provenance.",
        exclude_path=r"(?:tests?/|test_|_test\.|conftest)",
    ),
    _rule(
        "supply-chain/unpinned-action", "CI action not pinned to a commit", "medium",
        "supply-chain", [1, 11, 29], ["*.yml", "*.yaml"],
        r"(?i)^\s*-\s*uses:\s*(?P<action>[^\s@]+)@(?P<ref>[^\s]+)$",
        "A CI action reference is a tag or branch rather than an immutable commit.",
        "Pin the action to a full 40-character commit SHA available in the local mirror.",
        exclude_path=r"(?:^|/)(?:docs?|examples?)/",
    ),
]

AGGREGATE_RULES = [
    "deps/missing-lockfile", "deps/unpinned-python", "deps/unpinned-node",
    "tests/missing-test-suite", "docs/missing-readme", "docs/missing-license",
    "data/missing-provenance", "data/large-file", "supply-chain/missing-sbom",
    "build/unpinned-base-image", "config/committed-env-file",
    "security/private-key-file", "repro/unseeded-random",
]

RULE_METADATA: Dict[str, Dict[str, Any]] = {
    "deps/missing-lockfile": {
        "title": "Dependency ecosystem without a lockfile", "severity": "high",
        "category": "deps", "catalog_categories": [1, 10],
        "message": "A dependency manifest exists but no lockfile pins the resolved graph.",
        "remediation": "Generate a lockfile from the local mirror, commit it, and verify a "
                        "clean-state install against it.",
    },
    "deps/unpinned-python": {
        "title": "Unpinned Python dependency", "severity": "high",
        "category": "deps", "catalog_categories": [1, 2],
        "message": "A Python requirement is not pinned to an exact version.",
        "remediation": "Pin with '==' to a version available in the local wheelhouse and record "
                        "the wheel hash.",
    },
    "deps/unpinned-node": {
        "title": "Unpinned Node.js dependency", "severity": "medium",
        "category": "deps", "catalog_categories": [1, 6],
        "message": "A package.json dependency uses a range or tag instead of an exact version.",
        "remediation": "Pin exact versions in package.json and commit the offline lockfile.",
    },
    "tests/missing-test-suite": {
        "title": "Source code without a test suite", "severity": "medium",
        "category": "tests", "catalog_categories": [25],
        "message": "Source files exist but no test files or test directories were found.",
        "remediation": "Add unit and integration tests that run from the local toolchain with no "
                        "network access, and record the exact test command.",
    },
    "docs/missing-readme": {
        "title": "Missing README", "severity": "low", "category": "docs",
        "catalog_categories": [31],
        "message": "No README file explains how to build, test, and run the project offline.",
        "remediation": "Add a README with setup, exact commands, inputs, outputs, and recovery steps.",
    },
    "docs/missing-license": {
        "title": "Missing license", "severity": "low", "category": "docs",
        "catalog_categories": [31, 32],
        "message": "No license or copying file declares the terms of use.",
        "remediation": "Add an OSI-approved license and record it in the SBOM and handoff guide.",
    },
    "data/missing-provenance": {
        "title": "Data without a provenance manifest", "severity": "medium",
        "category": "data", "catalog_categories": [15, 16, 24],
        "message": "Data files exist but no checksum or provenance manifest was found.",
        "remediation": "Generate a SHA-256 manifest for every data file and record the source, "
                        "snapshot date, and transformation version.",
    },
    "data/large-file": {
        "title": "Large file outside large-file storage", "severity": "medium",
        "category": "data", "catalog_categories": [9, 32],
        "message": "A file exceeds the size threshold and is not covered by a Git LFS attribute.",
        "remediation": "Store the artifact in the local artifact repository or track it with "
                        "Git LFS, and record its checksum in the manifest.",
    },
    "supply-chain/missing-sbom": {
        "title": "Dependency manifest without an SBOM", "severity": "low",
        "category": "supply-chain", "catalog_categories": [29],
        "message": "Dependencies are declared but no SBOM was found for offline vulnerability review.",
        "remediation": "Generate an SPDX or CycloneDX SBOM from the local mirror and store it "
                        "beside the release artifacts.",
    },
    "build/unpinned-base-image": {
        "title": "Container base image not pinned", "severity": "medium",
        "category": "build", "catalog_categories": [1, 17],
        "message": "A Dockerfile FROM instruction uses 'latest' or omits a tag.",
        "remediation": "Pin the base image to an exact version and digest available in the local "
                        "registry mirror.",
    },
    "config/committed-env-file": {
        "title": "Committed .env file", "severity": "critical", "category": "security",
        "catalog_categories": [29],
        "message": "A .env file is tracked in the project and may contain local secrets.",
        "remediation": "Remove the file from version control, add it to .gitignore, and keep only "
                        "a reviewed .env.example template.",
    },
    "security/private-key-file": {
        "title": "Private key file in the project", "severity": "critical",
        "category": "security", "catalog_categories": [29],
        "message": "A private key file (PEM, KEY, or OpenSSH identity) is stored in the project.",
        "remediation": "Move the key to a protected offline store, rotate it, and keep only a "
                        "public key or a placeholder in the repository.",
    },
    "repro/unseeded-random": {
        "title": "Randomness without a visible seed", "severity": "low",
        "category": "repro", "catalog_categories": [10, 20, 21, 22, 30],
        "message": "A file uses a random generator without a visible seed in the same file.",
        "remediation": "Seed every generator from an explicit configuration value and record the "
                        "seed in the experiment manifest.",
    },
}

for _pattern_rule in PATTERN_RULES:
    RULE_METADATA[_pattern_rule["id"]] = {
        "title": _pattern_rule["title"],
        "severity": _pattern_rule["severity"],
        "category": _pattern_rule["category"],
        "catalog_categories": _pattern_rule["catalog_categories"],
        "message": _pattern_rule["message"],
        "remediation": _pattern_rule["remediation"],
    }


# --------------------------------------------------------------------------- #
# Rule matching helpers
# --------------------------------------------------------------------------- #

def _applies(rule_applies: Sequence[str], rel: str, name: str) -> bool:
    for pattern in rule_applies:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _finding(rule_id: str, path: str, line: Optional[int], evidence: str,
             message: Optional[str] = None, remediation: Optional[str] = None) -> Dict[str, Any]:
    meta = RULE_METADATA.get(rule_id, {})
    return {
        "rule_id": rule_id,
        "title": meta.get("title", rule_id),
        "severity": meta.get("severity", "medium"),
        "category": meta.get("category", "general"),
        "catalog_categories": meta.get("catalog_categories", []),
        "path": path,
        "line": line,
        "evidence": evidence,
        "message": message or meta.get("message", ""),
        "remediation": remediation or meta.get("remediation", ""),
    }


def _load_json_member(root: Path, rel: str) -> Optional[Any]:
    target = root / rel
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Aggregate checks
# --------------------------------------------------------------------------- #

def _check_lockfiles(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    names = {name.lower() for name in ctx["names"]}
    findings: List[Dict[str, Any]] = []
    if any(name in names for name in ("pyproject.toml", "Pipfile")) or \
            any(name.startswith("requirements") and name.endswith(".txt") for name in names):
        if not any(name.lower() in names for name in core.LOCKFILES["python"]):
            findings.append(_finding("deps/missing-lockfile", ".", None,
                                     "python dependency manifest present; no poetry.lock, uv.lock, "
                                     "Pipfile.lock, or constraints file"))
    if "package.json" in names and not any(name.lower() in names for name in core.LOCKFILES["node"]):
        findings.append(_finding("deps/missing-lockfile", "package.json", None,
                                 "package.json present; no package-lock.json, pnpm-lock.yaml, "
                                 "or yarn.lock"))
    if "cargo.toml" in names and "cargo.lock" not in names:
        findings.append(_finding("deps/missing-lockfile", "Cargo.toml", None,
                                 "Cargo.toml present; no Cargo.lock"))
    if "go.mod" in names and "go.sum" not in names:
        findings.append(_finding("deps/missing-lockfile", "go.mod", None,
                                 "go.mod present; no go.sum"))
    if any(name.endswith(".csproj") for name in names) and "packages.lock.json" not in names:
        findings.append(_finding("deps/missing-lockfile", "*.csproj", None,
                                 "C# project present; no packages.lock.json"))
    return findings


def _check_python_pins(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rel, text in ctx["texts"]:
        name = Path(rel).name
        if not (name.startswith("requirements") and name.endswith(".txt")):
            continue
        for line_no, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            spec = line.split("#", 1)[0].split(";", 1)[0].strip()
            if not spec:
                continue
            if "==" not in spec:
                findings.append(_finding(
                    "deps/unpinned-python", rel, line_no, redact(spec),
                    "Requirement %r is not pinned with '=='." % spec,
                    "Pin to an exact version available in the local wheelhouse."))
    return findings


def _check_node_pins(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rel, text in ctx["texts"]:
        if Path(rel).name != "package.json":
            continue
        data = _load_json_member(root, rel)
        if not isinstance(data, dict):
            continue
        sections = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
        for section in sections:
            block = data.get(section)
            if not isinstance(block, dict):
                continue
            for package, version in sorted(block.items()):
                if not isinstance(version, str):
                    continue
                exact = re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?", version)
                if exact:
                    continue
                findings.append(_finding(
                    "deps/unpinned-node", rel, None, "%s@%s" % (package, version),
                    "%s dependency %r is not an exact version." % (section, package),
                    "Pin an exact version and commit the offline lockfile."))
    return findings


def _check_tests(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    if ctx["source_files"] and not ctx["has_tests"]:
        return [_finding("tests/missing-test-suite", ".", None,
                         "%d source file(s); no test file or test directory found"
                         % len(ctx["source_files"]))]
    return []


def _check_readme(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    if any(name.startswith("readme") for name in ctx["names"]):
        return []
    return [_finding("docs/missing-readme", ".", None,
                     "no README, README.md, or README.rst at any level")]


def _check_license(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    if any(name.startswith(("license", "licence", "copying")) for name in ctx["names"]):
        return []
    return [_finding("docs/missing-license", ".", None,
                     "no LICENSE, LICENCE, or COPYING file")]


def _check_provenance(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    data_files = [rel for rel in ctx["files"] if Path(rel).suffix.lower() in core.DATA_EXTENSIONS]
    if not data_files or any(name.lower() in {n.lower() for n in ctx["names"]}
                             for name in core.MANIFEST_NAMES):
        return []
    return [_finding(
        "data/missing-provenance", data_files[0], None,
        "%d data file(s) with no manifest.json, provenance.json, or checksums file"
        % len(data_files),
        "Data is present without a checksum or provenance manifest.",
        "Write a SHA-256 manifest and record source, snapshot date, and transformation version.")]


def _check_large_files(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    threshold = int(ctx["policy"].get("thresholds", {}).get("max_file_bytes", 52428800))
    gitattributes = root / ".gitattributes"
    lfs = False
    if gitattributes.is_file():
        try:
            lfs = "filter=lfs" in gitattributes.read_text(encoding="utf-8", errors="replace")
        except OSError:
            lfs = False
    if lfs:
        return []
    findings: List[Dict[str, Any]] = []
    for entry in ctx["entries"]:
        if entry["size"] > threshold:
            findings.append(_finding(
                "data/large-file", entry["rel"], None,
                "%s (%s) exceeds the %s threshold"
                % (entry["rel"], format_bytes(entry["size"]), format_bytes(threshold)),
                "Large artifact is stored directly in the project tree.",
                "Move it to the local artifact repository or track it with Git LFS."))
    return findings


def _check_sbom(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    manifests = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml",
                 "build.gradle", "build.gradle.kts")
    dependency_manifests = [name for name in ctx["names"] if name in manifests or
                            (name.startswith("requirements") and name.endswith(".txt"))]
    sbom_names = ("sbom.json", "sbom.spdx.json", "sbom.cdx.json", "bom.json",
                  "*.spdx.json", "*.cdx.json")
    has_sbom = any(name.lower() in {n.lower() for n in ctx["names"]} for name in sbom_names) or \
        any(fnmatch.fnmatch(name, pattern) for name in ctx["names"] for pattern in ("*.spdx.json", "*.cdx.json"))
    if dependency_manifests and not has_sbom:
        return [_finding("supply-chain/missing-sbom", dependency_manifests[0], None,
                         "dependency manifest present; no SPDX or CycloneDX SBOM found")]
    return []


def _check_base_images(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rel, text in ctx["texts"]:
        if Path(rel).name.lower() not in {"dockerfile", "containerfile"} and \
                not rel.lower().endswith((".dockerfile", ".containerfile")):
            continue
        for line_no, raw in enumerate(text.splitlines(), 1):
            match = re.match(r"^\s*FROM\s+(\S+)(?:\s+AS\s+\S+)?\s*$", raw, re.IGNORECASE)
            if not match:
                continue
            image = match.group(1)
            if image.lower() == "scratch":
                continue
            if ":" not in image.rsplit("/", 1)[-1] or image.endswith(":latest"):
                findings.append(_finding(
                    "build/unpinned-base-image", rel, line_no, image,
                    "Base image %r is not pinned to an exact version." % image,
                    "Pin an exact tag and digest available in the local registry mirror."))
    return findings


def _check_env_files(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rel in ctx["files"]:
        name = Path(rel).name
        if not (name == ".env" or (name.startswith(".env.") and
                                   not name.endswith((".example", ".sample", ".template")))):
            continue
        findings.append(_finding("config/committed-env-file", rel, None,
                                 "environment file %s is tracked in the project" % name))
    return findings


def _check_private_key_files(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rel in ctx["files"]:
        name = Path(rel).name.lower()
        if name.endswith((".example", ".sample", ".template", ".pub")):
            continue
        if name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"} or \
                name.endswith((".pem", ".key")):
            findings.append(_finding("security/private-key-file", rel, None,
                                     "private key file %s is stored in the project" % name))
    return findings


def _check_unseeded_random(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    pattern = re.compile(r"(?i)(?:np\.random\.|numpy\.random\.|random\.(?:random|randint|choice|shuffle|sample)\()")
    seed_pattern = re.compile(r"(?i)(?:seed|random_state|rng\b)")
    findings: List[Dict[str, Any]] = []
    for rel, text in ctx["texts"]:
        if not pattern.search(text) or seed_pattern.search(text):
            continue
        for line_no, raw in enumerate(text.splitlines(), 1):
            if pattern.search(raw):
                findings.append(_finding(
                    "repro/unseeded-random", rel, line_no, redact(raw.strip()),
                    "Random generator used without a seed in this file.",
                    "Seed from an explicit configuration value and record it in the manifest."))
                break
    return findings


AGGREGATE_CHECKS = {
    "deps/missing-lockfile": _check_lockfiles,
    "deps/unpinned-python": _check_python_pins,
    "deps/unpinned-node": _check_node_pins,
    "tests/missing-test-suite": _check_tests,
    "docs/missing-readme": _check_readme,
    "docs/missing-license": _check_license,
    "data/missing-provenance": _check_provenance,
    "data/large-file": _check_large_files,
    "supply-chain/missing-sbom": _check_sbom,
    "build/unpinned-base-image": _check_base_images,
    "config/committed-env-file": _check_env_files,
    "security/private-key-file": _check_private_key_files,
    "repro/unseeded-random": _check_unseeded_random,
}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def rule_catalog() -> List[Dict[str, Any]]:
    """Describe every rule for `workbench.py rules` and documentation."""
    rules: List[Dict[str, Any]] = []
    for rule in PATTERN_RULES:
        rules.append({
            "id": rule["id"], "title": rule["title"], "severity": rule["severity"],
            "category": rule["category"], "catalog_categories": rule["catalog_categories"],
            "kind": "pattern", "applies": rule["applies"],
            "message": rule["message"], "remediation": rule["remediation"],
        })
    for rule_id in AGGREGATE_RULES:
        meta = RULE_METADATA[rule_id]
        rules.append({
            "id": rule_id, "title": meta["title"], "severity": meta["severity"],
            "category": meta["category"], "catalog_categories": meta["catalog_categories"],
            "kind": "aggregate", "applies": [],
            "message": meta["message"], "remediation": meta["remediation"],
        })
    return rules


def _rule_selected(rule_id: str, only: Sequence[str], exclude: Sequence[str]) -> bool:
    if exclude and any(rule_id == item or rule_id.startswith(item.rstrip("/")) or
                       item in rule_id for item in exclude):
        return False
    if only and not any(rule_id == item or rule_id.startswith(item.rstrip("/")) or
                        item in rule_id for item in only):
        return False
    return True


def run_checks(root: Path, policy: Dict[str, Any], only: Optional[Sequence[str]] = None,
               exclude: Optional[Sequence[str]] = None,
               catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run every enabled rule and return a structured, serializable result."""
    started = core.now_iso()
    only = list(only or [])
    exclude = list(exclude or [])
    max_text = int(policy.get("thresholds", {}).get("max_text_bytes", 2097152))
    per_rule = int(policy.get("thresholds", {}).get("max_findings_per_rule", 5))
    total_cap = int(policy.get("thresholds", {}).get("max_findings_total", 200))
    disabled = set(policy.get("disabled_rules", []))
    allow_paths = list(policy.get("allow_paths", []))

    entries = list(core.iter_project_files(root, policy))
    texts: List[Tuple[str, str]] = []
    files: List[str] = []
    names: set = set()
    source_files: List[str] = []
    binary_or_skipped = 0
    bytes_scanned = 0
    for entry in entries:
        rel = entry["rel"]
        files.append(rel)
        names.add(Path(rel).name.lower())
        if entry["path"].suffix.lower() in core.SOURCE_EXTENSIONS:
            source_files.append(rel)
        if entry["too_large"] or not core.is_text_candidate(entry["path"]):
            binary_or_skipped += 1
            continue
        text = core.read_text(entry["path"], max_text)
        if text is None:
            binary_or_skipped += 1
            continue
        texts.append((rel, text))
        bytes_scanned += len(text.encode("utf-8", errors="replace"))

    ctx = {
        "root": root, "policy": policy, "entries": entries, "files": files,
        "names": names, "texts": texts, "source_files": source_files,
        "has_tests": core.has_test_signal(root),
    }

    findings: List[Dict[str, Any]] = []
    truncated_rules: List[str] = []
    rules_run: List[str] = []
    for rule in PATTERN_RULES:
        rule_id = rule["id"]
        if rule_id in disabled or not _rule_selected(rule_id, only, exclude):
            continue
        rules_run.append(rule_id)
        count = 0
        for rel, text in texts:
            name = Path(rel).name
            if not any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern)
                       for pattern in rule["applies"]):
                continue
            if any(fnmatch.fnmatch(rel, pattern) for pattern in allow_paths):
                continue
            if rule["exclude_path"] and rule["exclude_path"].search(rel):
                continue
            for line_no, raw in enumerate(text.splitlines(), 1):
                if not rule["pattern"].search(raw):
                    continue
                if count >= per_rule:
                    truncated_rules.append(rule_id)
                    break
                findings.append(_finding(rule_id, rel, line_no, redact(raw.strip())))
                count += 1
                if len(findings) >= total_cap:
                    break
            if len(findings) >= total_cap or count >= per_rule:
                break
        if len(findings) >= total_cap:
            break

    for rule_id in AGGREGATE_RULES:
        if rule_id in disabled or not _rule_selected(rule_id, only, exclude):
            continue
        rules_run.append(rule_id)
        for finding in AGGREGATE_CHECKS[rule_id](root, ctx):
            if len(findings) < total_cap:
                findings.append(finding)

    findings.sort(key=lambda item: (SEVERITY_RANK.get(item["severity"], 99), item["path"], item["line"] or 0))
    by_severity = {severity: 0 for severity in SEVERITIES}
    by_category: Dict[str, int] = {}
    catalog_hits: set = set()
    for finding in findings:
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
        by_category[finding["category"]] = by_category.get(finding["category"], 0) + 1
        catalog_hits.update(finding.get("catalog_categories", []))

    fail_on = list(policy.get("quality_gate", {}).get("fail_on", []))
    failing = [f for f in findings if f["severity"] in fail_on]
    catalog = catalog or core.load_catalog()
    category_names = {c["number"]: c["name"] for c in catalog["categories"]}
    policy_digest = sha256_bytes(
        json.dumps({k: v for k, v in policy.items() if k != "_path"},
                   sort_keys=True, ensure_ascii=False).encode("utf-8"))

    return {
        "status": "fail" if failing else "pass",
        "gate": "fail" if failing else "pass",
        "tool": core.TOOL_NAME,
        "tool_version": core.VERSION,
        "root": str(root),
        "generated_at": started,
        "policy": {"path": policy.get("_path", ""), "sha256": policy_digest,
                   "fail_on": fail_on, "disabled_rules": sorted(disabled)},
        "environment": core.build_environment(),
        "summary": {
            "files_total": len(entries),
            "files_text": len(texts),
            "files_binary_or_skipped": binary_or_skipped,
            "bytes_scanned": bytes_scanned,
            "rules_run": len(rules_run),
            "rules_available": len(PATTERN_RULES) + len(AGGREGATE_RULES),
            "findings": len(findings),
            "failing_findings": len(failing),
            "by_severity": by_severity,
            "by_category": by_category,
            "truncated_rules": sorted(set(truncated_rules)),
            "truncated_total": len(findings) >= total_cap,
        },
        "catalog_categories_applied": [
            {"number": number, "name": category_names.get(number, "unknown")}
            for number in sorted(catalog_hits)
        ],
        "findings": findings,
    }


def format_findings(result: Dict[str, Any]) -> str:
    lines = []
    for finding in result.get("findings", []):
        location = finding["path"] + (":%s" % finding["line"] if finding.get("line") else "")
        lines.append("[%s] %s (%s) at %s" % (finding["severity"].upper(), finding["title"],
                                            finding["rule_id"], location))
        if finding.get("evidence"):
            lines.append("    evidence: %s" % finding["evidence"])
        if finding.get("remediation"):
            lines.append("    fix: %s" % finding["remediation"])
    return "\n".join(lines)
