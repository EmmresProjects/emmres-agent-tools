#!/usr/bin/env python3
"""android-artifact-recovery -- primary entry point.

A capability-preserving toolkit for lawful Android artifact recovery and
compatibility analysis. It works in two lanes:

  Lane A (always available, pure standard library)
      identify, badging, manifest, arsc, dex, elf, abi-matrix, page16k,
      zip-health, entry, aab-inspect, exported-audit, compat-scan,
      certificate (v1 fallback), triage, doctor, self-test, capabilities

  Lane B (optional, discovered from the host)
      apktool decode/build, zipalign, apksigner sign/verify,
      aapt badging cross-check. Missing tools are reported with an explicit
      status code -- never simulated.

Every command returns one JSON envelope (default) or a human-readable report,
writes nothing outside --out, and refuses privileged or destructive actions
without --confirm.

Run ``tools-android-artifact-recovery.py capabilities`` for the command list and
``tools-android-artifact-recovery.py self-test`` for tool status evidence.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import importlib.util as _ilu
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TOOL_VERSION = "1.0.0"
PROG = "android-artifact-recovery"
SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(stem: str, alias: str):
    """Import a hyphen-prefixed sibling module (``tools-ax-parsers.py``).

    Python module names cannot contain hyphens, so packaged files are loaded by
    path and registered under a normal name. This keeps the required ``tools-``
    filename prefix without renaming anything at install time.
    """
    existing = sys.modules.get(alias)
    if existing is not None:
        return existing
    path = SCRIPT_DIR / f"{stem}.py"
    if not path.is_file():
        raise SystemExit(f"packaged module missing: {path.name} "
                         f"(expected beside {Path(__file__).name})")
    spec = _ilu.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot create an import spec for {path.name}")
    module = _ilu.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


P = _load_sibling("tools-ax-parsers", "ax_parsers")
# tools-ax-formats imports ``tools_ax_parsers``; expose the alias before loading.
sys.modules.setdefault("tools_ax_parsers", P)
F = _load_sibling("tools-ax-formats", "ax_formats")
ParseError = P.ParseError

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INVALID_INPUT = 2
EXIT_MISSING_DEPENDENCY = 3
EXIT_APPROVAL_REQUIRED = 4
EXIT_PARTIAL = 5
EXIT_LIMIT = 6

STATUS_FOUND = "FOUND"
STATUS_MISSING = "MISSING"
STATUS_WRONG_VERSION = "WRONG VERSION"
STATUS_BROKEN = "BROKEN"
STATUS_LICENSE = "LICENSE REQUIRED"
STATUS_DEVICE = "DEVICE REQUIRED"
STATUS_NETWORK = "NETWORK REQUIRED"
STATUS_HOST_FEATURE = "HOST FEATURE REQUIRED"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

DEFAULT_CONFIG: dict[str, Any] = {
    "paths": {"apktool_jar": "", "build_tools_dir": "", "android_home": "",
              "assets_dir": "", "work_dir": ""},
    "limits": {"max_parse_bytes": 268435456, "max_entries_listed": 5000,
               "max_artifacts_per_batch": 200, "subprocess_timeout_seconds": 600,
               "report_max_findings": 400},
    "signing": {"keystore_env": "ANDROID_TOOLKIT_KS_PASS",
                "keystore_alias_env": "ANDROID_TOOLKIT_KS_ALIAS",
                "min_sdk_for_scheme": 23, "refuse_to_overwrite_input": True},
    "policy": {"min_target_sdk_for_store_upload": 35, "require_exported_from_api": 31,
               "page_size_bytes": 16384, "treat_warnings_as_failure": False},
}


# --------------------------------------------------------------------------
# errors / envelope
# --------------------------------------------------------------------------


class ToolkitError(Exception):
    """Operational error carrying an exit code, a hint and structured details."""

    def __init__(self, message: str, code: int = EXIT_INVALID_INPUT, hint: str = "",
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.details = details or {}


class ApprovalRequired(ToolkitError):
    def __init__(self, action: str, reason: str):
        super().__init__(
            f"approval required for action: {action}", code=EXIT_APPROVAL_REQUIRED,
            hint=f"re-run with --confirm to approve. Reason approval is required: {reason}",
            details={"action": action, "reason": reason})


@dataclass
class Finding:
    rule_id: str
    severity: str
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: str = ""
    component: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "severity": self.severity, "title": self.title,
                "detail": self.detail, "component": self.component,
                "evidence": self.evidence, "remediation": self.remediation}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Envelope:
    """Collects findings/artifacts and renders the structured result."""

    def __init__(self, command: str, args: argparse.Namespace):
        self.command = command
        self.args = args
        self.started = time.time()
        self.data: dict[str, Any] = {}
        self.findings: list[Finding] = []
        self.artifacts: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.errors: list[dict[str, Any]] = []
        self.tool_status: dict[str, Any] = {}
        self.limitations: list[str] = []

    def add_finding(self, rule_id: str, severity: str, title: str, detail: str,
                    evidence: dict[str, Any] | None = None, remediation: str = "",
                    component: str = "") -> None:
        self.findings.append(Finding(rule_id, severity, title, detail, evidence or {},
                                     remediation, component))

    def add_artifact(self, path: Path | str, role: str, **extra: Any) -> None:
        target = Path(path)
        record: dict[str, Any] = {"path": str(target), "role": role}
        if target.is_file():
            record["size_bytes"] = target.stat().st_size
            record["sha256"] = F.sha256_file(target)
        record.update(extra)
        self.artifacts.append(record)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def limit(self, message: str) -> None:
        if message not in self.limitations:
            self.limitations.append(message)

    def to_dict(self, ok: bool, exit_code: int) -> dict[str, Any]:
        self.findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
        return {"ok": ok, "command": self.command,
                "tool": {"name": PROG, "version": TOOL_VERSION},
                "generated_at": utc_now(),
                "duration_seconds": round(time.time() - self.started, 3),
                "exit_code": exit_code, "data": self.data,
                "findings": [f.to_dict() for f in self.findings],
                "artifacts": self.artifacts, "tool_status": self.tool_status,
                "warnings": self.warnings, "errors": self.errors,
                "limitations": self.limitations}


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | None) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    candidate = Path(path) if path else SCRIPT_DIR / "asset-default-config.json"
    if candidate.is_file():
        try:
            user = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolkitError(
                f"config file {candidate} is not valid JSON: {exc}",
                hint="fix the JSON or pass --config with a valid file")
        if not isinstance(user, dict):
            raise ToolkitError(f"config file {candidate} must contain a JSON object")
        cfg = deep_merge(cfg, user)
    return cfg


def load_json_asset(name: str, required: bool = True) -> Any:
    path = SCRIPT_DIR / name
    if not path.is_file():
        if required:
            raise ToolkitError(
                f"packaged asset missing: {name}",
                hint="re-extract the Skill Tool folder; assets must sit beside the tool")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolkitError(f"packaged asset {name} is invalid JSON: {exc}")


def describe_assets() -> list[dict[str, Any]]:
    """Report the packaged assets this tool actually consumes."""
    spec = [
        ("asset-skill-catalog.json",
         "Catalog of 24 categories and 324 skills mapped to commands."),
        ("asset-compat-rules.json", "Rule set evaluated by compat-scan."),
        ("asset-default-config.json", "Default limits, paths and policy values."),
        ("asset-self-test-plan.json", "Tool probe plan used by self-test."),
        ("asset-agent-prompts.json", "Operating procedures for a host agent."),
    ]
    out = []
    for name, purpose in spec:
        path = SCRIPT_DIR / name
        out.append({"file": name, "present": path.is_file(), "purpose": purpose,
                    "bytes": path.stat().st_size if path.is_file() else 0})
    return out


# --------------------------------------------------------------------------
# tool discovery
# --------------------------------------------------------------------------


def env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name)
        if value and Path(value).exists():
            return Path(value)
    return None


def parse_version(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:3]) or (0,)


def version_of(tool: Path, args: list[str], timeout: float = 20.0,
               source_properties: Path | None = None) -> tuple[str, str]:
    """Probe a tool with ``args`` and return ``(version_string, status)``.

    Several Android CLI tools exit non-zero for ``-h``/``--version`` or print the
    banner on stderr, so a non-zero exit is only treated as BROKEN when the
    process produced no output at all. ``source_properties`` (the build-tools
    metadata file) supplies the authoritative revision when the binary reports
    nothing useful -- ``zipalign`` is the common case.
    """
    try:
        proc = subprocess.run([str(tool), *args], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return "", STATUS_BROKEN
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    match = re.search(r"(\d+\.\d+(?:\.\d+)?(?:[-\w.]+)?)", blob)
    if match:
        return match.group(1), STATUS_FOUND
    if source_properties and source_properties.is_file():
        prop = re.search(r"Pkg\.Revision=(\d+\.\d+(?:\.\d+)?)",
                         source_properties.read_text(errors="replace"))
        if prop:
            return prop.group(1), STATUS_FOUND if blob else STATUS_BROKEN
    if proc.returncode != 0 and not blob:
        return "", STATUS_BROKEN
    first = blob.splitlines()[0][:60] if blob else ""
    return first, STATUS_FOUND if first else STATUS_BROKEN


@dataclass
class Tool:
    name: str
    path: Path | None
    version: str = ""
    status: str = STATUS_MISSING
    detail: str = ""
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": str(self.path) if self.path else None,
                "version": self.version, "status": self.status,
                "required": self.required, "detail": self.detail}


def find_build_tools(cfg: dict[str, Any], explicit: str | None = None) -> Path | None:
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    configured = cfg["paths"].get("build_tools_dir")
    if configured and Path(configured).is_dir():
        return Path(configured)
    home = env_path("ANDROID_HOME", "ANDROID_SDK_ROOT")
    if home is None and cfg["paths"].get("android_home"):
        home = Path(cfg["paths"]["android_home"]) if Path(
            cfg["paths"]["android_home"]).is_dir() else None
    if home:
        build_tools = home / "build-tools"
        if build_tools.is_dir():
            versions = sorted((d for d in build_tools.iterdir() if d.is_dir()),
                              key=lambda d: parse_version(d.name), reverse=True)
            if versions:
                return versions[0]
    return None


def find_apktool_jar(cfg: dict[str, Any], explicit: str | None = None) -> Path | None:
    for candidate in (explicit, os.environ.get("APKTOOL_JAR"),
                      cfg["paths"].get("apktool_jar")):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    for name in ("apktool.jar", "apktool_2.9.3.jar", "apktool_2.10.0.jar"):
        for base in (SCRIPT_DIR, SCRIPT_DIR.parent, Path.cwd()):
            path = base / name
            if path.is_file():
                return path
    return None


def resolve_tools(cfg: dict[str, Any], args: argparse.Namespace | None = None) -> dict[str, Tool]:
    build_tools = find_build_tools(
        cfg, getattr(args, "build_tools_dir", None) if args else None)
    java = shutil.which("java")
    tools: dict[str, Tool] = {
        "python3": Tool("python3", Path(sys.executable), sys.version.split()[0],
                        STATUS_FOUND, required=True),
        "java": Tool("java", Path(java) if java else None, required=True),
    }
    if java:
        version, status = version_of(Path(java), ["-version"])
        tools["java"].version, tools["java"].status = version, status
        tools["java"].detail = "probe: java -version"
    props = (build_tools / "source.properties") if build_tools else None
    probes = (("aapt", ["version"]), ("aapt2", ["version"]), ("apksigner", ["--version"]),
              ("zipalign", []), ("dexdump", ["--version"]), ("adb", ["version"]),
              ("fastboot", ["--version"]), ("bundletool", ["version"]))
    for name, version_args in probes:
        path = (build_tools / name) if build_tools and (build_tools / name).exists() else None
        if path is None:
            found = shutil.which(name)
            path = Path(found) if found else None
        if path is None:
            tools[name] = Tool(name, None, "", STATUS_MISSING,
                               "not found on PATH or in build-tools")
            continue
        version, status = version_of(path, version_args, source_properties=props)
        tools[name] = Tool(name, path, version, status,
                           f"probe: {name} {' '.join(version_args) or '(no args)'}")
    jar = find_apktool_jar(cfg, getattr(args, "apktool_jar", None) if args else None)
    if jar and java:
        version, status = version_of(Path(java), ["-jar", str(jar), "--version"], timeout=60)
        tools["apktool"] = Tool("apktool", jar, version, status, f"jar at {jar}")
    else:
        tools["apktool"] = Tool("apktool", jar, "", STATUS_MISSING,
                                "apktool jar not found; pass --apktool-jar or set APKTOOL_JAR")
    patch = shutil.which("patch")
    tools["patch"] = Tool("patch", Path(patch) if patch else None, "",
                          STATUS_FOUND if patch else STATUS_MISSING)
    tools["kvm"] = Tool("kvm", Path("/dev/kvm") if Path("/dev/kvm").exists() else None,
                        "", STATUS_FOUND if Path("/dev/kvm").exists() else STATUS_HOST_FEATURE,
                        "hardware acceleration for emulator lanes")
    tools["build_tools_dir"] = Tool("build-tools", build_tools, str(build_tools) if build_tools
                                    else "", STATUS_FOUND if build_tools else STATUS_MISSING)
    return tools


def require_tool(tools: dict[str, Tool], name: str) -> Tool:
    tool = tools.get(name)
    if not tool or not tool.path or tool.status != STATUS_FOUND:
        status = tool.status if tool else STATUS_MISSING
        raise ToolkitError(
            f"{name} is {status}: {tool.detail if tool else 'unknown'}",
            code=EXIT_MISSING_DEPENDENCY,
            hint="install Android build-tools and pass --build-tools-dir, or set "
                 "ANDROID_HOME. See knowledge-readme.md section 'Setup'.",
            details={"tool": name, "status": status})
    return tool


def zipalign_supports_page_flag(tools: dict[str, Tool]) -> bool:
    """``zipalign -P`` (explicit page alignment) exists from build-tools 35.

    Older releases only expose ``-p`` (page-align shared libraries with the
    4096-byte conventions of their era), so the rebuild pipeline must degrade
    visibly instead of pretending the 16 KB flag was honoured.
    """
    tool = tools.get("zipalign")
    if not tool or not tool.path:
        return False
    try:
        probe = subprocess.run([str(tool.path)], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "-P" in (probe.stdout or "") + (probe.stderr or "")


def apksigner_command(tools: dict[str, Tool]) -> list[str]:
    """Return a runnable apksigner prefix.

    Build-tools ship ``apksigner`` as a shell wrapper around ``lib/apksigner.jar``.
    The wrapper is preferred (it sets up the classpath); when only a jar is present
    the command falls back to ``java -jar``.
    """
    tool = tools.get("apksigner")
    if not tool or not tool.path:
        raise ToolkitError("apksigner is MISSING", code=EXIT_MISSING_DEPENDENCY,
                           hint="install Android build-tools and pass --build-tools-dir")
    path = tool.path
    if path.suffix == ".jar":
        java = tools.get("java")
        if not java or not java.path:
            raise ToolkitError("java is MISSING but needed for apksigner.jar",
                               code=EXIT_MISSING_DEPENDENCY)
        return [str(java.path), "-jar", str(path)]
    if os.access(path, os.X_OK):
        return [str(path)]
    sibling = path.parent / "lib" / "apksigner.jar"
    java = tools.get("java")
    if sibling.is_file() and java and java.path:
        return [str(java.path), "-jar", str(sibling)]
    raise ToolkitError(f"apksigner at {path} is neither executable nor a jar",
                       code=EXIT_MISSING_DEPENDENCY,
                       hint="re-extract build-tools; the wrapper lost its exec bit")


def run_cmd(cmd: list[str], timeout: float, cwd: Path | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                              timeout=timeout, cwd=str(cwd) if cwd else None,
                              env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (
            (exc.stdout or b"").decode("utf-8", "replace"))
        raise ToolkitError(
            f"command timed out after {timeout:.0f}s: {' '.join(map(str, cmd[:3]))} ...",
            code=EXIT_LIMIT, hint="raise --timeout or reduce the artifact size",
            details={"stdout_tail": (stdout or "")[-2000:]})
    except OSError as exc:
        raise ToolkitError(f"failed to start command: {exc}",
                           code=EXIT_MISSING_DEPENDENCY)


# --------------------------------------------------------------------------
# shared analysis helpers
# --------------------------------------------------------------------------


def resolve_input(path_str: str, must_exist: bool = True) -> Path:
    if not path_str:
        raise ToolkitError("no input path supplied",
                           hint="pass a positional artifact path")
    path = Path(path_str).expanduser()
    if must_exist and not path.exists():
        raise ToolkitError(f"input not found: {path}",
                           hint="check the path and try again",
                           details={"path": str(path)})
    return path


def guard_size(path: Path, max_bytes: int) -> None:
    if path.is_file() and path.stat().st_size > max_bytes:
        raise ToolkitError(
            f"{path.name} is {path.stat().st_size} bytes, above the configured limit "
            f"{max_bytes}", code=EXIT_LIMIT,
            hint="raise limits.max_parse_bytes in the config or pick a smaller artifact",
            details={"path": str(path)})


def open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ToolkitError(
            f"{path.name} is not a readable ZIP container: {exc}",
            hint="run the 'zip-health' command for a structural diagnosis",
            details={"path": str(path)})
    except OSError as exc:
        raise ToolkitError(f"cannot read {path.name}: {exc}")


def manifest_bytes(path: Path, member: str = "AndroidManifest.xml") -> bytes:
    with open_zip(path) as zf:
        try:
            info = zf.getinfo(member)
        except KeyError:
            raise ToolkitError(
                f"{path.name} has no {member}",
                hint="this does not look like an APK; run 'identify' first",
                details={"member": member})
        if info.file_size > 64 * 1024 * 1024:
            raise ToolkitError(f"{member} is unreasonably large ({info.file_size} bytes)",
                               code=EXIT_LIMIT)
        return zf.read(member)


def load_manifest(path: Path, member: str = "AndroidManifest.xml"):
    raw = manifest_bytes(path, member)
    if raw[:2] == b"PK":
        raise ToolkitError("nested ZIP where a manifest was expected")
    doc = P.parse_axml(raw)
    if not doc.manifest():
        raise ToolkitError(
            "binary XML parsed but no <manifest> root element was found",
            hint="the manifest may use an unsupported chunk layout; run "
                 "'apktool decode' for a cross-check")
    return doc, raw


def element_attr(el: P.AxmlElement | None, name: str) -> Any:
    if el is None:
        return None
    attribute = el.attr(name)
    if attribute is None:
        return None
    if attribute.raw_value not in (None, ""):
        return attribute.raw_value
    return attribute.typed_value


def truthy(value: Any) -> bool:
    return str(value).lower() in ("true", "1", "yes")


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return default


COMPONENT_TAGS = ("activity", "activity-alias", "service", "receiver", "provider")


def apk_facts(path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Core facts shared by badging, report, compat-scan and exported-audit."""
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    doc, raw_manifest = load_manifest(path)
    root = doc.manifest()
    assert root is not None
    all_elements = doc.all_elements()
    uses_sdk = next((e for e in all_elements if e.tag == "uses-sdk"), None)
    application = next((e for e in all_elements if e.tag == "application"), None)
    facts: dict[str, Any] = {
        "package": element_attr(root, "package"),
        "version_code": element_attr(root, "versionCode"),
        "version_name": element_attr(root, "versionName"),
        "compile_sdk": element_attr(root, "compileSdkVersion"),
        "platform_build_version": element_attr(root, "platformBuildVersionCode"),
        "min_sdk": as_int(element_attr(uses_sdk, "minSdkVersion")),
        "target_sdk": as_int(element_attr(uses_sdk, "targetSdkVersion")),
        "max_sdk": as_int(element_attr(uses_sdk, "maxSdkVersion")),
        "install_location": element_attr(root, "installLocation"),
        "shared_user_id": element_attr(root, "sharedUserId"),
        "split": element_attr(root, "split"),
        "permissions": [], "requested_features": [], "components": [],
        "launchable_activities": [], "application": {},
        "manifest_problems": doc.problems, "axml_string_count": doc.string_count,
        "raw_manifest_sha256": __import__("hashlib").sha256(raw_manifest).hexdigest(),
    }
    if application is not None:
        facts["application"] = {
            "name": element_attr(application, "name"),
            "debuggable": truthy(element_attr(application, "debuggable")),
            "allow_backup": truthy(element_attr(application, "allowBackup")),
            "extract_native_libs": element_attr(application, "extractNativeLibs"),
            "uses_cleartext_traffic": element_attr(application, "usesCleartextTraffic"),
            "network_security_config": element_attr(application, "networkSecurityConfig"),
            "request_legacy_external_storage": element_attr(
                application, "requestLegacyExternalStorage"),
            "app_component_factory": element_attr(application, "appComponentFactory"),
            "theme": element_attr(application, "theme"),
            "label": element_attr(application, "label"),
            "icon": element_attr(application, "icon"),
        }
    for element in all_elements:
        if element.tag == "uses-permission":
            facts["permissions"].append(element_attr(element, "name"))
        elif element.tag == "uses-permission-sdk-23":
            facts["permissions"].append(f"{element_attr(element, 'name')} (sdk23+)")
        elif element.tag == "uses-feature":
            name = element_attr(element, "name") or element_attr(element, "glEsVersion")
            required = element_attr(element, "required")
            facts["requested_features"].append(
                {"name": name, "required": not (required is not None and not truthy(required))})
        elif element.tag in COMPONENT_TAGS:
            component = {"type": element.tag, "name": element_attr(element, "name"),
                         "exported": element_attr(element, "exported"),
                         "permission": element_attr(element, "permission"),
                         "process": element_attr(element, "process"),
                         "enabled": element_attr(element, "enabled"),
                         "authorities": element_attr(element, "authorities"),
                         "intent_filters": []}
            for child in element.children:
                if child.tag != "intent-filter":
                    continue
                component["intent_filters"].append({
                    "actions": [element_attr(a, "name") for a in child.children
                                if a.tag == "action"],
                    "categories": [element_attr(c, "name") for c in child.children
                                   if c.tag == "category"],
                })
            facts["components"].append(component)
            if element.tag in ("activity", "activity-alias"):
                for intent_filter in component["intent_filters"]:
                    if ("android.intent.action.MAIN" in intent_filter["actions"]
                            and "android.intent.category.LAUNCHER"
                            in intent_filter["categories"]):
                        facts["launchable_activities"].append(component["name"])
    with open_zip(path) as zf:
        names = zf.namelist()
    facts["entry_count"] = len(names)
    facts["dex_files"] = [n for n in names if re.fullmatch(r"classes\d*\.dex", n)]
    facts["abi_dirs"] = sorted({n.split("/")[1] for n in names
                                if n.startswith("lib/") and n.count("/") >= 2})
    facts["has_arsc"] = "resources.arsc" in names
    facts["asset_count"] = sum(1 for n in names if n.startswith("assets/"))
    facts["obb_like"] = [n for n in names if n.lower().endswith(".obb")][:10]
    facts["signature_files"] = [n for n in names
                                if re.match(r"META-INF/[^/]+\.(RSA|DSA|EC)$", n, re.I)]
    facts["is_split_apk"] = bool(facts["split"]) or "split_" in path.name
    facts["sdk_declared"] = facts["min_sdk"] is not None
    return facts


def arsc_summary(path: Path) -> dict[str, Any]:
    """Parse ``resources.arsc`` inside an APK and summarise locales/densities."""
    with open_zip(path) as zf:
        try:
            info = zf.getinfo("resources.arsc")
        except KeyError:
            return {"present": False}
        if info.file_size > F.MAX_PARSE_BYTES:
            return {"present": True, "error": "resources.arsc exceeds the parser size limit"}
        raw = zf.read("resources.arsc")
    with tempfile.TemporaryDirectory(prefix="aar-arsc-") as temp_dir:
        temp_path = Path(temp_dir) / "resources.arsc"
        temp_path.write_bytes(raw)
        try:
            table = F.parse_arsc(temp_path)
        except P.ParseError as exc:
            return {"present": True, "error": str(exc)}
    configs = sorted({entry.config for entry in table.entries})
    locales: set[str] = set()
    densities: set[str] = set()
    for config in configs:
        for part in config.split("-"):
            if re.fullmatch(r"[a-z]{2,3}(-r[A-Z0-9]{2,3})?", part):
                locales.add(part)
            elif part == "anydpi" or part == "nodpi" or part.endswith("dpi"):
                densities.add(part)
    types: dict[str, int] = {}
    for entry in table.entries:
        types[entry.type_name] = types.get(entry.type_name, 0) + 1
    return {"present": True, "package": table.package_name,
            "package_id": f"0x{table.package_id:02x}",
            "entry_count": len(table.entries), "config_count": table.config_count,
            "string_count": table.string_count, "types": dict(sorted(types.items())),
            "locales": sorted(locales), "densities": sorted(densities),
            "problems": table.problems}


def text_report(env: Envelope) -> str:
    lines = [f"{PROG} {TOOL_VERSION} :: {env.command}", "=" * 68, ""]
    data = env.data
    if env.command == "badging" and "facts" in data:
        facts = data["facts"]
        lines += [
            f"package            : {facts.get('package')}",
            f"versionName        : {facts.get('version_name')} "
            f"(versionCode {facts.get('version_code')})",
            f"minSdk / targetSdk : {facts.get('min_sdk')} / {facts.get('target_sdk')}"
            f"   compileSdk {facts.get('compile_sdk')}",
            f"ABIs               : {', '.join(facts.get('abi_dirs') or []) or 'none'}",
            f"dex files          : {', '.join(facts.get('dex_files') or []) or 'none'}",
            f"launchable         : "
            f"{', '.join(facts.get('launchable_activities') or []) or 'none'}",
            f"permissions        : {len(facts.get('permissions') or [])}",
            f"components         : {len(facts.get('components') or [])}",
            "",
        ]
    for key, value in data.items():
        if key in ("facts", "aapt_crosscheck", "raw_manifest_sha256"):
            continue
        rendered = value if isinstance(value, str) else json.dumps(value, indent=2,
                                                                   default=str)
        if len(rendered) > 4000:
            rendered = rendered[:4000] + "\n... (truncated; use --format json for everything)"
        lines += [f"[{key}]", rendered, ""]
    if env.findings:
        lines += ["FINDINGS", "-" * 68]
        for finding in env.findings:
            lines.append(f"[{finding.severity.upper():8}] {finding.rule_id}: {finding.title}")
            lines.append(f"           {finding.detail}")
            if finding.remediation:
                lines.append(f"           fix: {finding.remediation}")
        lines.append("")
    if env.warnings:
        lines += ["WARNINGS"] + [f"  - {w}" for w in env.warnings] + [""]
    if env.limitations:
        lines += ["LIMITATIONS"] + [f"  - {l}" for l in env.limitations] + [""]
    return "\n".join(lines)


def emit(env: Envelope, args: argparse.Namespace, ok: bool = True, code: int = EXIT_OK) -> int:
    payload = env.to_dict(ok, code)
    fmt = getattr(args, "format", "json") or "json"
    out = getattr(args, "out", None)
    text = (json.dumps(payload, indent=2, default=str) if fmt == "json"
            else text_report(env))
    if out:
        out_path = Path(out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        if fmt != "json":
            print(f"report written to {out_path}")
    else:
        print(text)
    return code


# --------------------------------------------------------------------------
# commands: discovery
# --------------------------------------------------------------------------


COMMAND_HELP: dict[str, str] = {
    "capabilities": "List every command, its lane (core/optional), and the catalog categories it serves.",
    "doctor": "Host capability profile: CPU, memory, kernel, JDKs, Android tools, SDK roots, KVM, disk.",
    "self-test": "Run tool presence/version/smoke probes and report FOUND/MISSING/WRONG VERSION/BROKEN/...",
    "identify": "Detect the real format of an artifact regardless of its extension.",
    "zip-health": "ZIP central directory, duplicates, traversal, CRCs, EOCD/trailing bytes, alignment.",
    "badging": "AAPT-equivalent package summary: identity, SDK levels, components, permissions, ABIs.",
    "manifest": "Decode binary AndroidManifest.xml and emit attributes, components and the element tree.",
    "arsc": "Parse resources.arsc: packages, types, configs, locales, densities, entry values.",
    "dex": "Inspect classes*.dex: header, checksums, counts, class-name inventory, package histogram.",
    "elf": "Inspect native libraries: ABI, PT_LOAD alignment, DT_NEEDED, 16 KB page readiness.",
    "abi-matrix": "Summarise ABI coverage of an APK across 32/64-bit lanes with per-ABI page readiness.",
    "page16k": "16 KB page-size readiness report for every packaged 64-bit native library.",
    "entry": "List ZIP entries with compression, alignment and optional bounded extraction.",
    "aab-inspect": "Decode an Android App Bundle: modules, manifests, dex, native libs, BundleConfig.",
    "exported-audit": "Audit exported components against android:exported rules for the declared targetSdk.",
    "compat-scan": "Rule-based compatibility scan (targetSdk, exported, cleartext, page size, ABI, backup).",
    "apktool": "Decode or rebuild an APK with a versioned apktool JAR (state file, explicit workspace).",
    "sign": "Development sign an APK with apksigner (approval required; never overwrites the input).",
    "verify": "Verify APK signatures with apksigner and report the schemes actually used.",
    "certificate": "Report signing certificate identity via apksigner, falling back to META-INF v1 data.",
    "rebuild": "Authorized pipeline: preserve -> decode -> patch -> build -> align -> sign -> verify.",
    "preserve": "Copy originals read-only with hashes and a chain-of-custody JSON record.",
    "report": "Produce a Markdown + JSON evidence report for one artifact.",
    "batch": "Analyse a directory of artifacts, isolate failures, and write CSV+JSON indexes.",
    "triage": "Classify a failure log/string and route it to the earliest actionable evidence step.",
    "strings-scan": "Bounded strings scan of an artifact for toolchain/SDK breadcrumbs.",
}

# Commands whose real work needs host binaries. "self-test" is not here: it runs and
# reports (with MISSING statuses) using nothing but Python.
OPTIONAL_COMMANDS = {"apktool", "sign", "verify", "certificate", "rebuild"}
APPROVAL_COMMANDS = ["sign", "rebuild", "preserve"]


def cmd_capabilities(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("capabilities", args)
    catalog = load_catalog()
    env.data = {
        "tool": {"name": PROG, "version": TOOL_VERSION},
        "commands": [{"command": name, "description": description,
                      "lane": "optional" if name in OPTIONAL_COMMANDS else "core",
                      "runtime": "host binaries" if name in OPTIONAL_COMMANDS
                      else "python standard library",
                      "degrades_without_tools": name in ("self-test", "doctor",
                                                         "certificate", "verify")}
                     for name, description in COMMAND_HELP.items()],
        "catalog": {"source": catalog.get("source"),
                    "categories": catalog.get("category_count"),
                    "skills": catalog.get("skill_count"),
                    "categories_index": [
                        {"number": c["number"], "name": c["name"],
                         "skills": len(c["skills"]),
                         "primary_commands": c["primary_commands"],
                         "implementation_note": c.get("implementation_note", "")}
                        for c in catalog.get("categories", [])]},
        "assets": describe_assets(),
        "approval_required_commands": APPROVAL_COMMANDS,
        "exit_codes": {"0": "success", "1": "usage error",
                       "2": "invalid input / parse failure", "3": "missing dependency",
                       "4": "approval required", "5": "partial success",
                       "6": "limit or timeout reached", "130": "interrupted"},
        "status_vocabulary": [STATUS_FOUND, STATUS_MISSING, STATUS_WRONG_VERSION,
                              STATUS_BROKEN, STATUS_LICENSE, STATUS_DEVICE,
                              STATUS_NETWORK, STATUS_HOST_FEATURE],
    }
    env.limit("Core lane is pure Python; the optional lane needs Java and Android build-tools.")
    env.limit("No command downloads anything or contacts a network service.")
    return env


def load_catalog() -> dict[str, Any]:
    data = load_json_asset("asset-skill-catalog.json", required=False)
    if not data:
        return {"source": "asset-skill-catalog.json (missing)", "category_count": 0,
                "skill_count": 0, "categories": []}
    return data


def cmd_doctor(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("doctor", args)
    uname = os.uname() if hasattr(os, "uname") else None
    host: dict[str, Any] = {
        "platform": sys.platform,
        "machine": uname.machine if uname else "",
        "kernel": uname.release if uname else "",
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "kvm_present": Path("/dev/kvm").exists(),
        "java_home": os.environ.get("JAVA_HOME", ""),
        "android_home": os.environ.get("ANDROID_HOME")
        or os.environ.get("ANDROID_SDK_ROOT") or "",
    }
    try:
        host["uid"] = os.geteuid()
        host["root"] = os.geteuid() == 0
    except AttributeError:  # pragma: no cover - non-POSIX
        pass
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            memory[key] = value.strip()
        host["memory"] = {"total": memory.get("MemTotal"),
                          "available": memory.get("MemAvailable")}
    except OSError:
        pass
    try:
        stats = os.statvfs("/")
        host["disk"] = {"free_gb": round(stats.f_bavail * stats.f_frsize / 1e9, 2),
                        "total_gb": round(stats.f_blocks * stats.f_frsize / 1e9, 2)}
    except (OSError, AttributeError):  # pragma: no cover - non-POSIX
        pass
    candidates = [p for p in (host["android_home"], cfg["paths"].get("android_home")) if p]
    host["sdk_roots_found"] = [p for p in candidates if Path(p).is_dir()]
    if host["sdk_roots_found"]:
        sdk = Path(host["sdk_roots_found"][0])
        layout = {}
        for sub in ("platforms", "build-tools", "platform-tools", "system-images", "ndk"):
            directory = sdk / sub
            layout[sub] = (sorted(d.name for d in directory.iterdir() if d.is_dir())[:20]
                           if directory.is_dir() else [])
        host["sdk_layout"] = layout
    env.data = {"host": host, "tools": {k: v.to_dict() for k, v in tools.items()}}
    env.tool_status = {k: v.to_dict() for k, v in tools.items()}
    if not host["kvm_present"]:
        env.warn("KVM is unavailable: hardware-accelerated emulators cannot run on this "
                 f"host ({STATUS_HOST_FEATURE} for emulator lanes).")
    if not tools["adb"].path:
        env.warn(f"adb is {STATUS_MISSING}: device and install lanes are unavailable "
                 f"({STATUS_DEVICE}).")
    if not host["sdk_roots_found"]:
        env.warn("No Android SDK root detected; SDK package inventory is unavailable. "
                 "Pass --build-tools-dir to use locally extracted build-tools.")
    env.limit("This command reports the host it runs on; it does not install anything.")
    return env


SELF_TEST_FALLBACK = [
    {"id": "python-runtime", "tool": "python3", "args": ["--version"], "required": True},
    {"id": "java-runtime", "tool": "java", "args": ["-version"], "required": True},
    {"id": "aapt-badging", "tool": "aapt", "args": ["version"], "required": False},
    {"id": "aapt2-compile", "tool": "aapt2", "args": ["version"], "required": False},
    {"id": "apksigner", "tool": "apksigner", "args": ["--version"], "required": False},
    {"id": "zipalign", "tool": "zipalign", "args": [], "required": False},
    {"id": "apktool", "tool": "apktool", "args": ["--version"], "required": False},
    {"id": "adb", "tool": "adb", "args": ["version"], "required": False, "needs": "device"},
    {"id": "kvm", "tool": "kvm", "args": [], "required": False, "needs": "host_feature"},
]


def load_self_test_plan() -> list[dict[str, Any]]:
    data = load_json_asset("asset-self-test-plan.json", required=False)
    if data and isinstance(data.get("checks"), list) and data["checks"]:
        return data["checks"]
    return SELF_TEST_FALLBACK


def cmd_self_test(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("self-test", args)
    plan = load_self_test_plan()
    results = []
    failures = 0
    for check in plan:
        name = check["tool"]
        tool = tools.get(name)
        status, detail, version = STATUS_MISSING, "", ""
        if tool and tool.path:
            version, status, detail = tool.version, tool.status, tool.detail
            minimum = check.get("min_version") or check.get("minimum_major")
            if minimum and parse_version(version) < parse_version(str(minimum)):
                status = STATUS_WRONG_VERSION
                detail = f"found {version}, need >= {minimum}"
            if check.get("needs") == "device" and status == STATUS_FOUND:
                probe = run_cmd([str(tool.path), "devices"], timeout=30)
                if "device" not in probe.stdout or probe.stdout.count("\n") <= 1:
                    status = STATUS_DEVICE
                    detail = "adb present but no authorized device is attached"
        elif check.get("needs") == "host_feature":
            status = STATUS_HOST_FEATURE
            detail = "host feature probe failed (see doctor)"
        required = bool(check.get("required"))
        if required and status != STATUS_FOUND:
            failures += 1
        results.append({"id": check["id"], "tool": name, "required": required,
                        "status": status, "version": version,
                        "path": str(tool.path) if tool and tool.path else None,
                        "detail": detail})
    env.data = {"checks": results,
                "summary": {"total": len(results),
                            "found": sum(1 for r in results if r["status"] == STATUS_FOUND),
                            "not_found": sum(1 for r in results
                                             if r["status"] == STATUS_MISSING),
                            "required_failures": failures,
                            "statuses": sorted({r["status"] for r in results})},
                "verdict": "PASS" if not failures else "FAIL (required tools)",
                "_exit": EXIT_OK if not failures else EXIT_MISSING_DEPENDENCY}
    env.tool_status = {r["tool"]: r for r in results}
    if failures:
        env.warn(f"{failures} required tool(s) are not usable; core commands still work.")
    return env


# --------------------------------------------------------------------------
# commands: artifact analysis (core lane)
# --------------------------------------------------------------------------


def cmd_identify(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("identify", args)
    targets = [Path(p).expanduser() for p in args.inputs]
    if not targets:
        raise ToolkitError("no inputs supplied", hint="pass one or more file paths")
    results = []
    archive_kinds = {"apk", "aab", "zip", "jar", "apks", "xapk", "apkm", "obb-zip",
                     "malformed-zip", "arsc-container", "android-library-archive"}
    for target in targets:
        info = P.detect_artifact(target)
        if target.is_file() and info["kind"] in archive_kinds:
            try:
                layout = P.parse_zip_layout(target)
                info["zip_entries"] = len(layout.entries)
                info["zip_problems"] = layout.problems
                info["apk_signing_block"] = layout.sign_block_present
            except (OSError, ValueError, P.ParseError) as exc:
                info["zip_problems"] = [str(exc)]
        results.append(info)
        if info["kind"] == "malformed-zip":
            env.add_finding("ART-001", "high", "Malformed ZIP container",
                            f"{target.name} looks like an archive but its central "
                            "directory is unreadable.", evidence={"path": str(target)},
                            remediation="Re-download or re-copy the artifact, then run "
                                        "'zip-health' for the structural diagnosis.")
        if any("mismatch" in note for note in info.get("notes", [])):
            env.add_finding("ART-002", "low", "Extension does not match content",
                            f"{target.name}: content is {info['kind']} but the suffix "
                            "suggests something else.",
                            evidence={"path": str(target), "kind": info["kind"]},
                            remediation="Trust the detected kind, not the filename.")
    missing = [r for r in results if not r["exists"]]
    if missing:
        env.errors.append({"code": "input-missing",
                           "message": "some inputs do not exist",
                           "paths": [r["path"] for r in missing]})
    env.data = {"artifacts": results, "count": len(results)}
    return env


def cmd_zip_health(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("zip-health", args)
    path = resolve_input(args.input)
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    layout = P.parse_zip_layout(path)
    page = int(cfg["policy"]["page_size_bytes"])
    duplicates = layout.duplicates()
    traversal = layout.traversal_names()
    native_libs = layout.native_libs()
    misaligned = layout.misaligned_native_libs(page)
    env.data = {
        "path": str(path),
        "entry_count": len(layout.entries),
        "central_directory_offset": layout.central_directory_offset,
        "central_directory_size": layout.central_directory_size,
        "comment_length": layout.comment_len,
        "zip64": layout.zip64,
        "apk_signing_block": layout.sign_block_present,
        "trailing_bytes": layout.trailing_bytes,
        "problems": layout.problems,
        "duplicate_names": duplicates,
        "path_traversal_names": traversal,
        "native_libs": len(native_libs),
        "native_libs_misaligned_page": [
            {"name": e.name, "offset": e.data_offset, "mod_page": e.data_offset % page}
            for e in misaligned[:50]],
        "entries_misaligned_4": sum(1 for e in layout.entries if e.data_offset % 4 != 0),
        "stored_entries": sum(1 for e in layout.entries if e.is_stored),
        "compressed_entries": sum(1 for e in layout.entries if not e.is_stored),
        "methods": sorted({e.method for e in layout.entries}),
    }
    for problem in layout.problems:
        severe = "truncated" in problem or "overrun" in problem or "traversal" in problem
        env.add_finding("ZIP-001", "high" if severe else "medium",
                        "ZIP structural problem", problem,
                        evidence={"path": str(path)},
                        remediation="Re-download or rebuild; a damaged central directory "
                                    "invalidates installs.")
    if duplicates:
        env.add_finding("ZIP-002", "high", "Duplicate ZIP entries",
                        f"{len(duplicates)} duplicate path(s); the last one wins at "
                        "install time.", evidence={"duplicates": duplicates},
                        remediation="Remove duplicates before signing; Android rejects or "
                                    "silently overrides them.")
    if traversal:
        env.add_finding("ZIP-003", "critical", "Path traversal entry names",
                        "Entry names escape the archive root.",
                        evidence={"names": traversal[:20]},
                        remediation="Reject the artifact; do not extract it with naive tools.")
    if misaligned:
        env.add_finding("ZIP-004", "medium", f"Native libraries not {page}-byte aligned",
                        f"{len(misaligned)} native libraries are misaligned for the page "
                        f"size ({page} bytes); the loader cannot mmap them from the APK.",
                        evidence={"examples": [e.name for e in misaligned[:5]]},
                        remediation="Rebuild, then zipalign -P 16 -f 4 <in> <out>, then "
                                    "apksigner sign.")
    if layout.trailing_bytes:
        env.add_finding("ZIP-005", "low", "Bytes after the ZIP end record",
                        f"{layout.trailing_bytes} trailing bytes were found.",
                        evidence={"bytes": layout.trailing_bytes},
                        remediation="Verify with apksigner; appended data breaks some strict "
                                    "parsers.")
    env.limit("Alignment is measured on data offsets; APK Signature Scheme v2/v3 blocks "
              "are detected but not cryptographically verified by this command.")
    return env


def cmd_badging(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("badging", args)
    path = resolve_input(args.input)
    detected = P.detect_artifact(path)
    if detected["kind"] != "apk":
        raise ToolkitError(
            f"{path.name} is not an APK (detected kind: {detected['kind']})",
            hint="run 'identify' to see what the file really is; a ZIP container "
                 "without AndroidManifest.xml cannot be badged",
            details={"detected_kind": detected["kind"], "notes": detected["notes"]})
    if args.aapt_crosscheck:
        if tools.get("aapt") and tools["aapt"].path:
            probe = run_cmd([str(tools["aapt"].path), "dump", "badging", str(path)],
                            timeout=120)
            env.data["aapt_crosscheck"] = {
                "available": True, "returncode": probe.returncode,
                "first_lines": probe.stdout.splitlines()[:12],
                "stderr": (probe.stderr or "")[:500]}
            env.tool_status = {"aapt": tools["aapt"].to_dict()}
        else:
            env.data["aapt_crosscheck"] = {"available": False, "status": STATUS_MISSING}
            env.warn(f"aapt is {STATUS_MISSING}; badging came from the built-in AXML "
                     "parser only.")
    facts = apk_facts(path, cfg)
    env.data["facts"] = facts
    env.data["summary"] = {
        "package": facts["package"],
        "version": f"{facts['version_name']} ({facts['version_code']})",
        "sdk": {"min": facts["min_sdk"], "target": facts["target_sdk"],
                "compile": facts["compile_sdk"]},
        "permission_count": len(facts["permissions"]),
        "component_count": len(facts["components"]),
        "launchable": facts["launchable_activities"], "abis": facts["abi_dirs"]}
    if not facts["sdk_declared"]:
        env.add_finding("MAN-001", "medium", "No uses-sdk element",
                        "The manifest declares no minSdkVersion; installability falls back "
                        "to platform defaults.",
                        remediation="Add <uses-sdk android:minSdkVersion=...> explicitly.")
    if not facts["launchable_activities"]:
        env.add_finding("MAN-002", "low", "No launcher activity",
                        "No activity declares MAIN+LAUNCHER; the app cannot be started from "
                        "a launcher.",
                        remediation="Confirm this is a library or split APK, or fix the "
                                    "intent-filter.")
    if facts["manifest_problems"]:
        env.warn("AXML parser reported issues: "
                 + "; ".join(facts["manifest_problems"][:3]))
    return env


def axml_to_xml(tree: dict[str, Any], indent: int = 0) -> str:
    """Render a decoded AXML tree as plain XML text."""
    if not tree:
        return ""
    pad = "  " * indent
    attributes = ""
    for key, value in tree.get("attributes", {}).items():
        raw = value.get("value") if isinstance(value, dict) else value
        escaped = str(raw).replace("&", "&amp;").replace('"', "&quot;")
        attributes += f' {key}="{escaped}"'
    tag = tree.get("tag", "unknown")
    children = tree.get("children", [])
    if not children:
        return f"{pad}<{tag}{attributes}/>\n"
    body = "".join(axml_to_xml(child, indent + 1) for child in children)
    return f"{pad}<{tag}{attributes}>\n{body}{pad}</{tag}>\n"


def cmd_manifest(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("manifest", args)
    path = resolve_input(args.input)
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    member = args.member or "AndroidManifest.xml"
    doc, raw = load_manifest(path, member)
    root = doc.manifest()
    env.data = {
        "path": str(path), "member": member,
        "raw_manifest_sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "axml": {"string_count": doc.string_count,
                 "resource_map_entries": len(doc.resource_map),
                 "elements_parsed": len(doc.all_elements()),
                 "problems": doc.problems},
        "attributes": {a.name or f"0x{a.resource_id:08x}":
                       (a.raw_value if a.raw_value not in (None, "") else a.typed_value)
                       for a in (root.attributes if root else [])},
        "components": [
            {"type": e.tag, "name": element_attr(e, "name"),
             "exported": element_attr(e, "exported"),
             "permission": element_attr(e, "permission")}
            for e in doc.all_elements() if e.tag in COMPONENT_TAGS],
        "intent_filters": [
            {"component": element_attr(e, "name"),
             "actions": [element_attr(a, "name") for a in f.children if a.tag == "action"],
             "categories": [element_attr(c, "name") for c in f.children
                            if c.tag == "category"],
             "data": [{"scheme": element_attr(d, "scheme"),
                       "host": element_attr(d, "host"),
                       "mimeType": element_attr(d, "mimeType")}
                      for d in f.children if d.tag == "data"]}
            for e in doc.all_elements() for f in e.children if f.tag == "intent-filter"],
    }
    if args.tree and root is not None:
        env.data["tree"] = root.to_dict()
    if args.xml_out:
        out = Path(args.xml_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(axml_to_xml(root.to_dict() if root else {}), encoding="utf-8")
        env.add_artifact(out, "decoded-manifest")
    if doc.problems:
        env.warn("parser problems: " + "; ".join(doc.problems[:5]))
    return env


def cmd_arsc(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("arsc", args)
    path = resolve_input(args.input)
    if path.is_dir():
        arsc_path = path / "resources.arsc"
        if not arsc_path.is_file():
            raise ToolkitError(f"no resources.arsc inside {path}")
        table = F.parse_arsc(arsc_path)
        source = str(arsc_path)
    elif path.name == "resources.arsc":
        table = F.parse_arsc(path)
        source = str(path)
    else:
        summary = arsc_summary(path)
        env.data = {"source": str(path), "summary": summary, "entries": [], "shown": 0}
        if summary.get("error"):
            env.add_finding("RES-001", "high", "Resource table could not be parsed",
                            summary["error"],
                            remediation="Rebuild resources; a corrupt resources.arsc breaks "
                                        "resource lookups at runtime.")
        if args.filter:
            env.warn("--filter applies when parsing a resources.arsc file or a decoded "
                     "directory; package summaries report aggregates only.")
        return env
    env.data = {"source": source, "summary": table.summary()}
    rows = []
    pattern = args.filter.lower() if args.filter else None
    limit = cfg["limits"]["max_entries_listed"]
    for entry in table.entries:
        label = f"{entry.type_name}/{entry.key_name}"
        if pattern and pattern not in label.lower() and pattern not in entry.config.lower():
            continue
        rows.append({"id": f"0x{entry.resource_id:08x}", "type": entry.type_name,
                     "key": entry.key_name, "config": entry.config,
                     "value": entry.value, "complex": entry.is_complex})
        if len(rows) >= limit:
            env.limit(f"entry listing truncated at {limit}")
            break
    env.data["entries"] = rows
    env.data["shown"] = len(rows)
    if table.problems:
        env.warn("resource table problems: " + "; ".join(table.problems[:5]))
    return env


def cmd_dex(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("dex", args)
    path = resolve_input(args.input)
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    infos: list[dict[str, Any]] = []
    histogram: dict[str, int] = {}

    def add(dex_name: str, buf: bytes) -> None:
        infos.append(F.parse_dex(dex_name, buf).to_dict())
        if args.classes:
            for descriptor in F.dex_class_names(buf):
                package = (descriptor[1:].split("/")[0]
                           if descriptor.startswith("L") and "/" in descriptor
                           else "(default)")
                histogram[package] = histogram.get(package, 0) + 1

    if path.is_dir():
        dex_files = sorted(path.glob("*.dex"))
        if not dex_files:
            raise ToolkitError(f"no .dex files in {path}",
                               hint="decode the APK first, or pass the APK itself")
        for dex_file in dex_files:
            add(dex_file.name, dex_file.read_bytes())
    elif path.suffix == ".dex":
        add(path.name, path.read_bytes())
    elif P.detect_artifact(path)["kind"] == "apk":
        with open_zip(path) as zf:
            names = [n for n in zf.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
            if not names:
                raise ToolkitError(f"{path.name} contains no classes*.dex")
            for name in sorted(names, key=lambda s: (len(s), s)):
                add(name, zf.read(name))
    else:
        raise ToolkitError("dex expects a .dex file, an APK, or a directory of .dex files")
    env.data = {
        "source": str(path), "dex_files": infos,
        "totals": {"files": len(infos),
                   "classes": sum(i["counts"]["classes"] for i in infos),
                   "methods": sum(i["counts"]["methods"] for i in infos),
                   "multi_dex": len(infos) > 1}}
    if args.classes:
        env.data["package_histogram"] = dict(
            sorted(histogram.items(), key=lambda kv: -kv[1])[:20])
    for info in infos:
        if info["problems"]:
            env.add_finding("DEX-001", "medium", f"DEX integrity issue in {info['name']}",
                            "; ".join(info["problems"]),
                            evidence={"file": info["name"],
                                      "checksum_ok": info["checksum_ok"]},
                            remediation="Re-extract the DEX from a trusted original; a "
                                        "checksum mismatch means bytes changed after "
                                        "compilation.")
        if info["version"] and info["version"] not in ("035", "037", "038", "039", "040"):
            env.add_finding("DEX-002", "low", f"Unusual DEX version {info['version']}",
                            f"{info['name']} declares DEX format {info['version']}.",
                            remediation="Confirm the API floor supports this DEX version.")
    return env


def _elf_records_from_apk(path: Path, cfg: dict) -> list[dict[str, Any]]:
    records = []
    with open_zip(path) as zf:
        names = [n for n in zf.namelist() if n.startswith("lib/") and n.endswith(".so")]
        if not names:
            raise ToolkitError(f"{path.name} contains no native libraries")
        cap = cfg["limits"]["max_artifacts_per_batch"] * 5
        if len(names) > cap:
            raise ToolkitError(f"{len(names)} libraries is above the inspection limit "
                               f"({cap})", code=EXIT_LIMIT)
        for name in names:
            record = F.parse_elf(name, zf.read(name)).to_dict()
            record["container"] = path.name
            records.append(record)
    return records


def cmd_elf(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("elf", args)
    path = resolve_input(args.input)
    results: list[dict[str, Any]] = []
    if path.is_dir():
        targets = sorted(p for p in path.rglob("*.so") if p.is_file())[:200]
        if not targets:
            raise ToolkitError(f"no .so files under {path}")
        for target in targets:
            guard_size(target, cfg["limits"]["max_parse_bytes"])
            buf = target.read_bytes()
            record = F.parse_elf(str(target), buf).to_dict()
            if args.strings:
                record["toolchain_strings"] = F.elf_strings_scan(buf)
            results.append(record)
    elif path.suffix == ".so" or P.detect_artifact(path)["kind"] == "elf":
        guard_size(path, cfg["limits"]["max_parse_bytes"])
        buf = path.read_bytes()
        record = F.parse_elf(path.name, buf).to_dict()
        if args.strings:
            record["toolchain_strings"] = F.elf_strings_scan(buf)
        results.append(record)
    elif P.detect_artifact(path)["kind"] == "apk":
        results = _elf_records_from_apk(path, cfg)
        if args.strings:
            with open_zip(path) as zf:
                for record in results:
                    record["toolchain_strings"] = F.elf_strings_scan(zf.read(record["name"]))
    else:
        raise ToolkitError("elf expects a .so file, a directory of .so files, or an APK")
    env.data = {"source": str(path), "libraries": results, "count": len(results)}
    for library in results:
        if not library["page_16k_ready"]:
            env.add_finding("ELF-001", "high",
                            f"{library['name']}: not 16 KB page compatible",
                            "; ".join(library["page_16k_notes"])
                            or "PT_LOAD alignment below 16 KB",
                            evidence={"abi": library["abi"],
                                      "segments": library["load_segments"]},
                            remediation="Rebuild with an NDK that links 16 KB-aligned "
                                        "libraries (NDK r28+ recommended) or update the "
                                        "third-party dependency.")
        if library["problems"]:
            env.warn(f"{library['name']}: " + "; ".join(library["problems"]))
    return env


ABI_LANES = {"arm64-v8a": "64-bit ARM", "armeabi-v7a": "32-bit ARM",
             "x86_64": "64-bit x86", "x86": "32-bit x86", "riscv64": "64-bit RISC-V"}
ABI_64 = ("arm64-v8a", "x86_64", "riscv64")
ABI_32 = ("armeabi-v7a", "x86")


def abi_matrix_data(path: Path) -> dict[str, Any]:
    matrix: dict[str, Any] = {}
    warnings: list[str] = []
    with open_zip(path) as zf:
        for name in zf.namelist():
            if not (name.startswith("lib/") and name.endswith(".so")):
                continue
            parts = name.split("/")
            if len(parts) < 3:
                continue
            abi = parts[1]
            try:
                info = F.parse_elf(name, zf.read(name))
            except P.ParseError as exc:
                warnings.append(f"{name}: {exc}")
                continue
            ready, _notes = info.page16k_ready()
            entry = matrix.setdefault(abi, {"lane": ABI_LANES.get(abi, "unknown ABI"),
                                            "libraries": [], "all_16k_ready": True})
            entry["libraries"].append({"name": parts[-1], "size": info.size,
                                       "page_16k_ready": ready,
                                       "max_align": info.max_load_align,
                                       "needed": info.needed})
            if abi in ABI_64 and not ready:
                entry["all_16k_ready"] = False
    has_64 = any(abi in matrix for abi in ABI_64)
    has_32 = any(abi in matrix for abi in ABI_32)
    return {"abis_present": sorted(matrix), "carries_64bit": has_64,
            "carries_32bit": has_32, "matrix": matrix, "warnings": warnings,
            "device_capability_note": (
                "64-bit-only devices can install this package" if has_64 and not has_32
                else "package ships both 32-bit and 64-bit lanes" if has_64 and has_32
                else "package is 32-bit only and will not install on 64-bit-only devices"
                if has_32 else "package ships no native libraries")}


def cmd_abi_matrix(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("abi-matrix", args)
    path = resolve_input(args.input)
    if not P.detect_artifact(path)["kind"] == "apk":
        raise ToolkitError("abi-matrix expects an APK",
                           hint="run 'identify' to confirm the artifact kind")
    data = abi_matrix_data(path)
    for warning in data.pop("warnings", []):
        env.warn(warning)
    env.data = {"package": apk_facts(path, cfg)["package"], **data}
    if not data["carries_64bit"]:
        env.add_finding("ABI-001", "high", "No 64-bit native libraries",
                        "The package ships only 32-bit native code; 64-bit-only devices "
                        "cannot run it.",
                        evidence={"abis": data["abis_present"]},
                        remediation="Ship a genuine arm64-v8a build. The NDK cannot convert "
                                    "a closed-source 32-bit library into 64-bit code by "
                                    "re-packaging.")
    for abi, entry in data["matrix"].items():
        if abi in ABI_64 and not entry["all_16k_ready"]:
            env.add_finding("ABI-002", "high",
                            f"{abi} libraries fail 16 KB page readiness",
                            "One or more libraries are not aligned for 16 KB pages.",
                            evidence={"abi": abi},
                            remediation="Rebuild with a 16 KB-aware NDK; run 'page16k' "
                                        "for per-library detail.")
    env.limit("ARM-only or x86-only evidence cannot be extrapolated to other ABIs; no "
              "device was used to verify runtime loading.")
    return env


def cmd_page16k(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("page16k", args)
    path = resolve_input(args.input)
    page = int(cfg["policy"]["page_size_bytes"])
    checks: list[dict[str, Any]] = []
    if P.detect_artifact(path)["kind"] == "apk":
        layout = P.parse_zip_layout(path)
        with open_zip(path) as zf:
            for entry in layout.native_libs():
                try:
                    info = F.parse_elf(entry.name, zf.read(entry.name))
                except P.ParseError as exc:
                    checks.append({"library": entry.name, "error": str(exc), "status": "ERROR"})
                    continue
                ready, notes = info.page16k_ready()
                zip_aligned = entry.data_offset % page == 0
                checks.append({
                    "library": entry.name, "abi": info.abi,
                    "elf_alignment": info.max_load_align,
                    "zip_offset_mod_page": entry.data_offset % page,
                    "zip_aligned": zip_aligned, "elf_16k_ready": ready, "notes": notes,
                    "status": "PASS" if ready and zip_aligned else "FAIL"})
    elif path.suffix == ".so":
        info = F.parse_elf(path.name, path.read_bytes())
        ready, notes = info.page16k_ready()
        checks.append({"library": path.name, "abi": info.abi,
                       "elf_alignment": info.max_load_align, "elf_16k_ready": ready,
                       "notes": notes, "status": "PASS" if ready else "FAIL"})
    else:
        raise ToolkitError("page16k expects an APK or a .so file")
    failures = [c for c in checks if c.get("status") == "FAIL"]
    if not checks:
        verdict = "NOT APPLICABLE"
        env.limit("No native libraries were found, so the 16 KB rule does not apply.")
    else:
        verdict = "FAIL" if failures else "PASS"
    env.data = {"policy_page_size": page, "libraries_checked": len(checks),
                "failures": len(failures), "verdict": verdict, "checks": checks}
    for check in failures:
        reasons = []
        if check.get("elf_16k_ready") is False:
            reasons.append("ELF PT_LOAD p_align < 16384")
        if check.get("zip_aligned") is False:
            reasons.append(f"ZIP data offset not {page}-byte aligned")
        env.add_finding("P16-001", "high", f"{check['library']}: 16 KB page-size risk",
                        "; ".join(reasons) or "failed 16 KB checks", evidence=check,
                        remediation="Rebuild with NDK r28+ (or align at 16 KB), then "
                                    "zipalign -P 16 -f 4 and re-sign.")
    env.limit("Static alignment checks predict installability on 16 KB devices but do not "
              "replace a run on real hardware.")
    return env


def cmd_entry(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("entry", args)
    path = resolve_input(args.input)
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    layout = P.parse_zip_layout(path)
    limit = cfg["limits"]["max_entries_listed"]
    pattern = args.filter.lower() if args.filter else None
    selected = [e for e in layout.entries
                if not pattern or fnmatch.fnmatch(e.name.lower(), f"*{pattern}*")]
    if not args.extract:
        rows = [{"name": e.name, "size": e.file_size, "compressed": e.compressed_size,
                 "method": "stored" if e.is_stored else f"deflate({e.method})",
                 "offset": e.data_offset, "align4": e.data_offset % 4,
                 "crc32": f"{e.crc:08x}"} for e in selected[:limit]]
        if len(selected) > limit:
            env.limit(f"entry listing truncated at {limit}; use --filter to narrow")
        env.data = {"source": str(path), "entries": rows, "shown": len(rows),
                    "total_entries": len(layout.entries),
                    "matching_entries": len(selected)}
        return env
    outdir = Path(args.extract).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    root = outdir.resolve()
    traversal = set(layout.traversal_names())
    extracted: list[str] = []
    with open_zip(path) as zf:
        for entry in selected:
            if entry.name in traversal:
                env.add_finding("ENT-001", "critical", "Refused to extract traversal entry",
                                entry.name, remediation="Reject the artifact.")
                continue
            target = (outdir / entry.name).resolve()
            if root != target and root not in target.parents:
                env.add_finding("ENT-002", "critical", "Extraction escape blocked",
                                entry.name)
                continue
            if len(extracted) >= limit:
                env.limit(f"extraction truncated at {limit} entries")
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(entry.name))
            extracted.append(str(target))
    env.data = {"source": str(path), "extracted": extracted, "count": len(extracted)}
    return env


# --------------------------------------------------------------------------
# commands: AAB and compatibility audits (core lane)
# --------------------------------------------------------------------------


def analyse_aab_path(path: Path) -> Any:
    """Analyse an AAB while its ZIP handle is guaranteed open."""
    with open_zip(path) as zf:
        names = list(zf.namelist())
        manifest_members = [n for n in names
                            if n.endswith("/manifest/AndroidManifest.xml")
                            or n == "BundleConfig.pb"
                            or (n.startswith("asset_pack/") and n.endswith(".pb"))]
        total = 0
        for info in zf.infolist():
            if info.filename in manifest_members:
                total += info.file_size
        if total > 32 * 1024 * 1024:
            raise ToolkitError("AAB manifests exceed the inspection limit",
                               code=EXIT_LIMIT)
        cache: dict[str, bytes] = {}
        for member in manifest_members:
            try:
                cache[member] = zf.read(member)
            except KeyError:
                continue

        def reader(member: str) -> bytes:
            if member in cache:
                return cache[member]
            return zf.read(member)

        return F.analyse_aab(names, reader)


def cmd_aab_inspect(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("aab-inspect", args)
    path = resolve_input(args.input)
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    if not P.detect_artifact(path)["kind"] == "aab":
        raise ToolkitError(f"{path.name} is not an Android App Bundle",
                           hint="run 'identify' to confirm the artifact kind")
    structure = analyse_aab_path(path)
    module_manifests: dict[str, Any] = {}
    with open_zip(path) as zf:
        for member in sorted(n for n in zf.namelist()
                             if n.endswith("/manifest/AndroidManifest.xml")):
            module = member.split("/")[0]
            try:
                manifest = F.decode_aab_manifest(zf.read(member))
            except P.ParseError as exc:
                module_manifests[module] = {"error": str(exc)}
                continue
            module_manifests[module] = {
                "package": manifest.package,
                "versionCode": manifest.version_code,
                "versionName": manifest.version_name,
                "split": manifest.split,
                "uses_sdk": [{k: v for k, v in e["attributes"].items()
                              if "SdkVersion" in k}
                             for e in manifest.walk() if e["tag"] == "uses-sdk"],
                "permissions": [e["attributes"].get("name") for e in manifest.walk()
                                if e["tag"] == "uses-permission"],
                "activities": [e["attributes"].get("name") for e in manifest.walk()
                               if e["tag"] == "activity"],
                "application": next((e["attributes"] for e in manifest.walk()
                                     if e["tag"] == "application"), {}),
                "problems": manifest.problems}
    layout = P.parse_zip_layout(path)
    env.data = {"source": str(path), "structure": structure.to_dict(),
                "module_manifests": module_manifests,
                "zip": {"entries": len(layout.entries), "problems": layout.problems},
                "signing_hint": "AAB signatures are JAR (v1) signatures; Play re-signs the "
                                "generated APKs with the upload key."}
    for module, entry in module_manifests.items():
        if isinstance(entry, dict) and entry.get("problems"):
            env.warn(f"{module}: " + "; ".join(entry["problems"]))
    if not structure.dex_files:
        env.add_finding("AAB-001", "medium", "Bundle has no classes.dex",
                        "No DEX payload was found in any module; the bundle would produce "
                        "an APK with no code (valid only for resource-only bundles).")
    env.limit("bundletool is not bundled. Structure and manifests are decoded here; use an "
              "externally installed bundletool for build-apks/generate-apks.")
    return env


EXPORT_RULES = {
    29: "Android 10 (API 29) introduced scoped storage; component export rules are "
        "unchanged from the pre-31 implicit default.",
    31: "Android 12 (API 31)+ requires an explicit android:exported on any component with "
        "an intent-filter, or the package fails to install.",
    33: "Android 13 (API 33)+ keeps the explicit android:exported requirement and adds the "
        "runtime POST_NOTIFICATIONS permission.",
    34: "Android 14 (API 34)+ adds foreground service types and tightens receiver export "
        "rules.",
}


def cmd_exported_audit(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("exported-audit", args)
    path = resolve_input(args.input)
    facts = apk_facts(path, cfg)
    target = facts["target_sdk"] or 0
    threshold = int(cfg["policy"]["require_exported_from_api"])
    rows = []
    risky = 0
    for component in facts["components"]:
        has_filter = bool(component["intent_filters"])
        exported = component["exported"]
        exported_bool = None if exported in (None, "") else truthy(exported)
        implicit = None
        if exported_bool is None:
            implicit = bool(has_filter)  # pre-31 default: exported when filtered
        status, severity = "ok", "info"
        if exported_bool is True and not component["permission"]:
            status = "exported-unprotected"
            severity = "high" if component["type"] in ("service", "receiver", "provider") \
                else "medium"
        elif exported_bool is None and has_filter and target >= threshold:
            status, severity = "missing-explicit-exported", "critical"
        elif exported_bool is None and has_filter:
            status, severity = "implicit-exported", "medium"
        elif exported_bool is None and not has_filter:
            status = "implicit-not-exported"
        rows.append({"type": component["type"], "name": component["name"],
                     "exported": exported,
                     "effective_exported": (exported_bool if exported_bool is not None
                                            else bool(implicit)),
                     "has_intent_filter": has_filter,
                     "permission": component["permission"],
                     "status": status, "severity": severity})
        if status in ("missing-explicit-exported", "exported-unprotected"):
            risky += 1
            env.add_finding(
                "EXP-001" if status == "missing-explicit-exported" else "EXP-002", severity,
                f"{component['type']} {component['name']} {status.replace('-', ' ')}",
                ("Component has an intent-filter but no explicit android:exported."
                 if status == "missing-explicit-exported"
                 else "Component is exported without a permission guard."),
                component=str(component["name"]),
                evidence={"intent_filters": component["intent_filters"], "target": target},
                remediation="Set android:exported=\"false\" unless external access is "
                            "intended; otherwise protect the component with a signature "
                            "permission.")
    env.data = {"package": facts["package"], "target_sdk": target,
                "rule_threshold_api": threshold,
                "rule": EXPORT_RULES.get(threshold, EXPORT_RULES[31]),
                "components": rows, "risky_count": risky}
    if target and target < threshold:
        env.warn(f"targetSdk {target} is below API {threshold}: the platform does not "
                 "enforce explicit android:exported yet, but store policy may.")
    env.limit("This is a static manifest audit. Runtime component reachability also depends "
              "on permissions, package visibility and OS version.")
    return env


def load_rules() -> dict[str, Any]:
    data = load_json_asset("asset-compat-rules.json", required=False)
    if not data:
        return {"version": None, "compat_rules": [],
                "_note": "asset-compat-rules.json missing; no rules could be evaluated"}
    return data


def cmd_compat_scan(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("compat-scan", args)
    path = resolve_input(args.input)
    facts = apk_facts(path, cfg)
    rules = load_rules()
    target = facts["target_sdk"] or 0
    min_sdk = facts["min_sdk"] or 1
    abis = set(facts["abi_dirs"])
    evaluated = 0
    skipped: list[str] = []
    for rule in rules.get("compat_rules", []):
        when = rule.get("when", {})
        if "target_sdk_lt" in when and not (target and target < when["target_sdk_lt"]):
            skipped.append(rule.get("id", "?"))
            continue
        if "target_sdk_gte" in when and not target >= when["target_sdk_gte"]:
            skipped.append(rule.get("id", "?"))
            continue
        if "min_sdk_lt" in when and not min_sdk < when["min_sdk_lt"]:
            skipped.append(rule.get("id", "?"))
            continue
        if "abi_missing_all" in when and any(a in abis for a in when["abi_missing_all"]):
            skipped.append(rule.get("id", "?"))
            continue
        fact_path = rule.get("fact")
        if fact_path:
            actual: Any = facts
            for part in fact_path.split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            expected = rule.get("expect")
            if expected == "truthy":
                ok = bool(actual)
            elif expected == "falsey":
                ok = not actual
            else:
                ok = actual == expected
            evaluated += 1
            if not ok:
                env.add_finding(rule["id"], rule.get("severity", "medium"),
                                rule.get("title", rule["id"]),
                                rule.get("detail", "").replace("{target}", str(target)),
                                evidence={"fact": fact_path, "actual": actual,
                                          "expected": expected},
                                remediation=rule.get("remediation", ""))
            continue
        if rule.get("check") == "exported_missing":
            gate = int(rule.get("when", {}).get("target_sdk_gte", 31))
            offenders = [c for c in facts["components"]
                         if c["intent_filters"] and c["exported"] in (None, "")
                         and target >= gate]
            evaluated += 1
            if offenders:
                env.add_finding(
                    rule["id"], rule.get("severity", "critical"),
                    rule.get("title", "Missing explicit android:exported"),
                    rule.get("detail", "").replace("{count}", str(len(offenders))),
                    evidence={"components": [c["name"] for c in offenders[:10]],
                              "count": len(offenders)},
                    remediation=rule.get("remediation", ""))
            continue
        env.warn(f"rule {rule.get('id', '?')} uses an unsupported check and was ignored")
    env.data = {
        "package": facts["package"],
        "facts": {"min_sdk": min_sdk, "target_sdk": target,
                  "compile_sdk": facts["compile_sdk"], "abis": sorted(abis),
                  "debuggable": facts["application"].get("debuggable"),
                  "allow_backup": facts["application"].get("allow_backup"),
                  "cleartext": facts["application"].get("uses_cleartext_traffic"),
                  "network_security_config":
                      facts["application"].get("network_security_config")},
        "rules_evaluated": evaluated, "rules_version": rules.get("version"),
        "rules_file": "asset-compat-rules.json",
        "findings_count": len(env.findings), "skipped_rules": skipped}
    if rules.get("_note"):
        env.warn(rules["_note"])
    env.limit("Rules encode documented platform behaviour; they flag risk, not guaranteed "
              "runtime failure. Verify on the target API lane.")
    return env


# --------------------------------------------------------------------------
# commands: optional lane (host tools)
# --------------------------------------------------------------------------


def tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else "..." + text[-limit:]


def cmd_apktool(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("apktool", args)
    tool = require_tool(tools, "apktool")
    java = require_tool(tools, "java")
    path = resolve_input(args.input) if args.operation == "decode" else None
    workdir = Path(args.workdir or cfg["paths"].get("work_dir")
                   or Path(tempfile.gettempdir()) / "aar-work").expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    state_path = workdir / "apktool-state.json"
    timeout = min(args.timeout, cfg["limits"]["subprocess_timeout_seconds"] * 4)
    if args.operation == "decode":
        assert path is not None
        target = (Path(args.outdir).expanduser() if args.outdir
                  else workdir / f"{path.stem}-decoded")
        if target.exists() and any(target.iterdir()) and not args.force:
            raise ToolkitError(f"{target} already exists and is not empty",
                               hint="pass --force to overwrite, or choose another --outdir")
        cmd = [java.path, "-jar", tool.path, "d", "-f", str(path), "-o", str(target)]
        if args.no_res:
            cmd.insert(4, "-r")
        if args.no_src:
            cmd.insert(4, "-s")
        proc = run_cmd(cmd, timeout=timeout)
        state = {"operation": "decode", "input": str(path.resolve()),
                 "input_sha256": F.sha256_file(path),
                 "output_dir": str(target.resolve()), "apktool_version": tool.version,
                 "java_version": java.version, "returncode": proc.returncode,
                 "started_at": utc_now(), "command": [str(c) for c in cmd]}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        env.data = {"operation": "decode", "output_dir": str(target.resolve()),
                    "returncode": proc.returncode, "stdout_tail": tail(proc.stdout),
                    "stderr_tail": tail(proc.stderr), "state_file": str(state_path),
                    "decoded_manifest": str(target / "AndroidManifest.xml")}
        env.add_artifact(state_path, "apktool-state")
        if proc.returncode != 0:
            env.errors.append({"code": "apktool-decode-failed",
                               "returncode": proc.returncode,
                               "stderr": tail(proc.stderr, 1200)})
            return env
        if not (target / "AndroidManifest.xml").exists():
            env.warn("decode succeeded but AndroidManifest.xml is missing; try without "
                     "--no-res")
        env.data["counts"] = {
            "smali_dirs": sum(1 for p in target.iterdir()
                              if p.is_dir() and p.name.startswith("smali")),
            "files": sum(1 for p in target.rglob("*") if p.is_file())}
        return env
    if args.operation == "build":
        src = Path(args.srcdir).expanduser() if args.srcdir else None
        if not src or not src.is_dir():
            raise ToolkitError(f"--srcdir {src} is not a directory",
                               hint="run 'apktool decode' first")
        out = Path(args.output).expanduser()
        if out.exists() and not args.force:
            raise ToolkitError(f"output {out} exists", hint="pass --force or a new --output")
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [java.path, "-jar", tool.path, "b", str(src), "-o", str(out)]
        if args.use_aapt2:
            cmd.append("--use-aapt2")
        proc = run_cmd(cmd, timeout=timeout)
        log_dir = workdir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"apktool-build-{int(time.time())}.log"
        log_path.write_text(f"$ {' '.join(str(c) for c in cmd)}\n\n{proc.stdout}\n"
                            f"{proc.stderr}", encoding="utf-8")
        state = {"operation": "build", "source_dir": str(src.resolve()),
                 "output": str(out.resolve()) if out.exists() else None,
                 "returncode": proc.returncode, "apktool_version": tool.version,
                 "started_at": utc_now()}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        env.data = {"operation": "build", "output": str(out),
                    "returncode": proc.returncode,
                    "stdout_tail": tail(proc.stdout, 2000),
                    "stderr_tail": tail(proc.stderr), "log": str(log_path),
                    "state_file": str(state_path)}
        env.add_artifact(log_path, "apktool-build-log")
        if out.exists():
            env.add_artifact(out, "unsigned-rebuilt-apk")
        if proc.returncode != 0:
            env.add_finding("APT-001", "high", "apktool build failed",
                            "The rebuild failed; see stdout_tail/stderr_tail and the log.",
                            remediation="Fix the reported resource or smali error, then "
                                        "rebuild. Resource errors usually need matching "
                                        "framework files.")
            env.errors.append({"code": "apktool-build-failed",
                               "returncode": proc.returncode})
        return env
    raise ToolkitError(f"unknown apktool operation {args.operation}")


def keystore_from_args(args: argparse.Namespace, cfg: dict) -> tuple[Path, str, str]:
    if not args.keystore:
        raise ToolkitError("--keystore is required for signing",
                           hint="use a keystore you own; production keys stay outside the "
                                "workspace")
    keystore = Path(args.keystore).expanduser()
    if not keystore.is_file():
        raise ToolkitError(f"keystore not found: {keystore}")
    password_env = cfg["signing"]["keystore_env"]
    alias_env = cfg["signing"]["keystore_alias_env"]
    password = args.ks_pass or os.environ.get(password_env, "")
    alias = args.ks_alias or os.environ.get(alias_env, "")
    if not password:
        raise ToolkitError(
            "keystore password not provided",
            hint=f"set environment variable {password_env} or pass --ks-pass. Passwords "
                 "are read from the environment to keep them out of process listings.")
    return keystore, password, alias


def parse_apksigner_schemes(stdout: str) -> dict[str, Any]:
    """Extract scheme verification results from apksigner --verbose output."""
    result: dict[str, Any] = {"v1": None, "v2": None, "v3": None, "signers": 0}
    for scheme, marker in (("v1", "v1 scheme (JAR signing):"),
                           ("v2", "v2 scheme (APK Signature Scheme v2):"),
                           ("v3", "v3 scheme (APK Signature Scheme v3):")):
        if marker in stdout:
            result[scheme] = stdout.split(marker, 1)[1].strip().lower().startswith("true")
    signer_match = re.search(r"Number of signers:\s*(\d+)", stdout)
    if signer_match:
        result["signers"] = int(signer_match.group(1))
    else:
        result["signers"] = len(set(re.findall(r"Signer #(\d+)", stdout)))
    return result


def cmd_sign(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("sign", args)
    if not args.confirm:
        raise ApprovalRequired("sign",
                               "signing changes package identity and can make an installed "
                               "app un-updatable")
    require_tool(tools, "apksigner")
    signer = apksigner_command(tools)
    path = resolve_input(args.input)
    out = Path(args.output).expanduser()
    if cfg["signing"]["refuse_to_overwrite_input"] and out.resolve() == path.resolve():
        raise ToolkitError("refusing to overwrite the input APK",
                           hint="sign to a new --output path; preserve the original")
    if out.exists() and not args.force:
        raise ToolkitError(f"output {out} exists", hint="pass --force to replace it")
    keystore, password, alias = keystore_from_args(args, cfg)
    cmd = [*signer, "sign", "--ks", str(keystore),
           "--ks-pass", "env:APKSIGNER_KS_PASS", "--out", str(out)]
    if alias:
        cmd += ["--ks-key-alias", alias]
    if args.v1 and args.v2:
        cmd += ["--v1-signing-enabled", "true", "--v2-signing-enabled", "true"]
    if args.min_sdk:
        cmd += ["--min-sdk-version", str(args.min_sdk)]
    cmd.append(str(path))
    out.parent.mkdir(parents=True, exist_ok=True)
    env_vars = dict(os.environ)
    env_vars["APKSIGNER_KS_PASS"] = password
    proc = run_cmd(cmd, timeout=args.timeout, env=env_vars)
    verify = (run_cmd([*signer, "verify", "--verbose", str(out)], timeout=120)
              if out.exists() else None)
    verification = parse_apksigner_schemes((verify.stdout if verify else "") or "")
    verification["returncode"] = verify.returncode if verify else None
    env.data = {"input": str(path), "output": str(out), "keystore": str(keystore),
                "alias": alias or "(default)", "returncode": proc.returncode,
                "stdout_tail": tail(proc.stdout, 1500),
                "stderr_tail": tail(proc.stderr, 2000),
                "verification": verification, "original_preserved": str(path)}
    if out.exists():
        env.add_artifact(out, "signed-apk")
        env.add_artifact(path, "original-input")
    if proc.returncode != 0 or (verify and verify.returncode != 0):
        env.errors.append({"code": "signing-failed", "returncode": proc.returncode})
        env.add_finding("SIG-003", "high", "Signing did not produce a verified APK",
                        "apksigner sign or verify failed; do not distribute the output.",
                        remediation="Inspect stderr_tail for keystore or algorithm errors.")
    env.limit("Development signing only. Publishing or updating an installed app requires "
              "the original signing identity; changing keys cannot preserve update "
              "compatibility.")
    return env


def cmd_verify(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("verify", args)
    require_tool(tools, "apksigner")
    signer = apksigner_command(tools)
    path = resolve_input(args.input)
    proc = run_cmd([*signer, "verify", "--verbose", str(path)], timeout=args.timeout)
    schemes = parse_apksigner_schemes(proc.stdout or "")
    env.data = {"input": str(path),
                "verdict": "VERIFIED" if proc.returncode == 0 else "NOT VERIFIED",
                "returncode": proc.returncode, "schemes": schemes,
                "stdout_tail": tail(proc.stdout, 3000),
                "stderr_tail": tail(proc.stderr, 2000)}
    if proc.returncode != 0:
        env.add_finding("SIG-001", "high", "APK signature does not verify",
                        "apksigner rejected the signature.",
                        evidence={"stderr": tail(proc.stderr, 800)},
                        remediation="Re-sign with a key you control; never trust a "
                                    "re-zipped APK.")
    env.limit("Signature validity confirms the integrity of the APK bytes, not the "
              "trustworthiness of the signer. Compare certificate fingerprints against a "
              "known-good value.")
    return env


def cmd_certificate(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("certificate", args)
    path = resolve_input(args.input)
    if tools.get("apksigner") and tools["apksigner"].path:
        proc = run_cmd([*apksigner_command(tools), "verify", "--print-certs", str(path)],
                       timeout=args.timeout)
        certificates: list[dict[str, Any]] = []
        for line in (proc.stdout or "").splitlines():
            digest = re.match(
                r"^Signer #(\d+) certificate (SHA-256|SHA-1|MD5) digest: (\S+)$", line)
            if digest:
                certificates.append({"signer": int(digest.group(1)),
                                     "digest": digest.group(2).upper(),
                                     "value": digest.group(3)})
            distinguished = re.match(r"^Signer #(\d+) certificate DN: (.+)$", line)
            if distinguished:
                certificates.append({"signer": int(distinguished.group(1)),
                                     "dn": distinguished.group(2)})
        env.data = {"input": str(path), "method": "apksigner",
                    "returncode": proc.returncode, "certificates": certificates,
                    "stderr_tail": tail(proc.stderr, 800)}
        env.tool_status = {"apksigner": tools["apksigner"].to_dict()}
        return env
    with open_zip(path) as zf:
        signature_files = [n for n in zf.namelist()
                           if re.match(r"META-INF/[^/]+\.(RSA|DSA|EC)$", n, re.I)]
    env.data = {"input": str(path), "method": "zip-inventory",
                "signature_block_files": signature_files,
                "note": "apksigner unavailable; only the presence of v1 signature files is "
                        "reported and digests are NOT verified."}
    env.limit(f"apksigner is {STATUS_MISSING}: certificate digests could not be extracted. "
              "Install Android build-tools to enable full certificate reporting.")
    if not signature_files:
        env.add_finding("SIG-002", "high", "No signature files found",
                        "The APK carries no META-INF signature files, and apksigner is "
                        "unavailable to inspect an APK Signature Scheme block.",
                        remediation="Install build-tools and run 'verify' before trusting "
                                    "or installing this artifact.")
    return env


# --------------------------------------------------------------------------
# commands: pipeline, preservation, reporting, batch, triage
# --------------------------------------------------------------------------


def cmd_rebuild(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    """preserve -> decode -> optional patch -> build -> align -> sign -> verify."""
    env = Envelope("rebuild", args)
    if not args.confirm:
        raise ApprovalRequired(
            "rebuild",
            "the pipeline decodes, patches and re-signs a package, producing a new "
            "artifact with a different signing identity")
    for name in ("apktool", "java", "apksigner", "zipalign"):
        require_tool(tools, name)
    path = resolve_input(args.input)
    workdir = Path(args.workdir or cfg["paths"].get("work_dir")
                   or Path(tempfile.gettempdir()) / "aar-work").expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir = (Path(args.outdir).expanduser() if args.outdir
              else workdir / f"{path.stem}-rebuild")
    outdir.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, Any]] = []

    def stage(name: str, ok: bool, **extra: Any) -> None:
        stages.append({"stage": name, "ok": ok, "at": utc_now(), **extra})

    originals = outdir / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    preserved = originals / path.name
    shutil.copy2(path, preserved)
    os.chmod(preserved, stat.S_IRUSR | stat.S_IRGRP)
    ledger: dict[str, Any] = {
        "tool": {"name": PROG, "version": TOOL_VERSION}, "created_at": utc_now(),
        "input": {"path": str(path.resolve()), "sha256": F.sha256_file(path)},
        "preserved_copy": {"path": str(preserved), "sha256": F.sha256_file(preserved),
                           "mode": "r--r-----"},
        "intent": args.intent or "not stated",
        "authorization_statement": args.authorization or "not stated",
        "stages": stages}
    stage("preserve", True, sha256=ledger["preserved_copy"]["sha256"])
    ledger_path = outdir / "rebuild-ledger.json"

    def finish(result: str) -> Envelope:
        ledger["result"] = result
        ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        env.data = {"stages": stages, "ledger": str(ledger_path),
                    "outputs": ledger.get("outputs", {}),
                    "verification": ledger.get("verification", {})}
        env.add_artifact(ledger_path, "rebuild-ledger")
        if preserved.is_file():
            env.add_artifact(preserved, "preserved-original")
        return env

    long_timeout = cfg["limits"]["subprocess_timeout_seconds"] * 4
    decoded = outdir / "decoded"
    decode = run_cmd([tools["java"].path, "-jar", tools["apktool"].path, "d", "-f",
                      str(path), "-o", str(decoded)], timeout=long_timeout)
    stage("decode", decode.returncode == 0, returncode=decode.returncode,
          stderr_tail=tail(decode.stderr, 800))
    if decode.returncode != 0:
        env.errors.append({"code": "decode-failed", "stderr": tail(decode.stderr, 1200)})
        return finish("failed at decode")

    patches: list[dict[str, Any]] = []
    if args.patch:
        patch_file = Path(args.patch).expanduser()
        if not patch_file.is_file():
            raise ToolkitError(f"patch file not found: {patch_file}")
        if not tools.get("patch") or not tools["patch"].path:
            raise ToolkitError(f"patch is {STATUS_MISSING}",
                               code=EXIT_MISSING_DEPENDENCY,
                               hint="install the GNU patch utility or apply the change "
                                    "manually")
        proc = run_cmd([tools["patch"].path, "-p1", "--batch", "--forward", "-i",
                        str(patch_file)], timeout=120, cwd=decoded)
        ok = proc.returncode == 0
        stage("patch", ok, returncode=proc.returncode, stdout_tail=tail(proc.stdout, 1200))
        patches.append({"file": str(patch_file), "ok": ok,
                        "sha256": F.sha256_file(patch_file)})
        if not ok:
            env.errors.append({"code": "patch-failed", "stderr": tail(proc.stderr, 1200)})
    ledger["patch"] = patches

    rebuilt = outdir / f"{path.stem}-unsigned.apk"
    build = run_cmd([tools["java"].path, "-jar", tools["apktool"].path, "b", str(decoded),
                     "-o", str(rebuilt)], timeout=long_timeout)
    stage("build", build.returncode == 0, returncode=build.returncode,
          stderr_tail=tail(build.stderr, 1500))
    if build.returncode != 0 or not rebuilt.is_file():
        env.add_finding("RB-001", "high", "Rebuild failed",
                        "apktool could not rebuild the decoded tree.",
                        remediation="Inspect stderr_tail, fix the resource or smali error, "
                                    "then retry.")
        ledger["outputs"] = {"unsigned": None, "aligned": None, "signed": None}
        return finish("failed at build")

    aligned = outdir / f"{path.stem}-aligned.apk"
    page = int(cfg["policy"]["page_size_bytes"])
    if zipalign_supports_page_flag(tools):
        align_cmd = [tools["zipalign"].path, "-P", str(page), "-f", "4", str(rebuilt),
                     str(aligned)]
        align_label = f"align(-P {page})"
    else:
        align_cmd = [tools["zipalign"].path, "-f", "4", str(rebuilt), str(aligned)]
        align_label = "align(-f 4 fallback; this zipalign has no -P flag)"
        env.warn(f"This zipalign release has no -P page-alignment flag (build-tools < 35). "
                 f"Native libraries were aligned to 4 bytes only, not {page} bytes; verify "
                 "with the 'page16k' command after signing.")
    align = run_cmd(align_cmd, timeout=300)
    stage(align_label, align.returncode == 0, returncode=align.returncode)
    if align.returncode != 0:
        fallback = run_cmd([tools["zipalign"].path, "-f", "4", str(rebuilt), str(aligned)],
                           timeout=300)
        stage("align-fallback-4", fallback.returncode == 0, returncode=fallback.returncode)

    keystore, password, alias = keystore_from_args(args, cfg)
    signed = (Path(args.output).expanduser() if args.output
              else outdir / f"{path.stem}-signed.apk")
    env_vars = dict(os.environ)
    env_vars["APKSIGNER_KS_PASS"] = password
    sign_cmd = [*apksigner_command(tools), "sign", "--ks", str(keystore),
                "--ks-pass", "env:APKSIGNER_KS_PASS", "--out", str(signed)]
    if alias:
        sign_cmd += ["--ks-key-alias", alias]
    sign_cmd.append(str(aligned if aligned.is_file() else rebuilt))
    sign = run_cmd(sign_cmd, timeout=300, env=env_vars)
    stage("sign", sign.returncode == 0, returncode=sign.returncode,
          stderr_tail=tail(sign.stderr, 800))
    verify = (run_cmd([*apksigner_command(tools), "verify", "--verbose", str(signed)],
                      timeout=180) if signed.is_file() else None)
    schemes = parse_apksigner_schemes((verify.stdout if verify else "") or "")
    verified = bool(verify and verify.returncode == 0)
    stage("verify", verified, returncode=verify.returncode if verify else None)
    ledger["outputs"] = {
        "unsigned": str(rebuilt) if rebuilt.is_file() else None,
        "aligned": str(aligned) if aligned.is_file() else None,
        "signed": str(signed) if signed.is_file() else None,
        "signed_sha256": F.sha256_file(signed) if signed.is_file() else None}
    ledger["verification"] = {"verified": verified,
                              "v1": schemes["v1"], "v2": schemes["v2"],
                              "v3": schemes["v3"]}
    ledger["tools"] = {name: tool.version for name, tool in tools.items()
                       if name in ("java", "apktool", "apksigner", "zipalign", "aapt",
                                   "aapt2")}
    if signed.is_file():
        env.add_artifact(signed, "signed-output")
    if not verified:
        env.add_finding("RB-002", "high", "Rebuilt APK did not verify",
                        "The output failed apksigner verification.",
                        remediation="Do not install or distribute the output; inspect the "
                                    "signing stage log.")
    env.limit("A rebuilt APK carries a development signing identity. It cannot update an "
              "installed copy signed by another key, and it does not imply runtime success "
              "without a device test.")
    return finish("success" if verified else "produced output that did not verify")


def cmd_preserve(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("preserve", args)
    if not args.confirm:
        raise ApprovalRequired("preserve",
                               "the command copies artifacts into a new evidence location "
                               "and writes a custody record")
    targets = [resolve_input(p) for p in args.inputs]
    if not targets:
        raise ToolkitError("no inputs supplied")
    dest = Path(args.outdir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"tool": {"name": PROG, "version": TOOL_VERSION},
                              "created_at": utc_now(), "operator_note": args.note or "",
                              "authorization": args.authorization or "not stated",
                              "items": []}
    for target in targets:
        if not target.is_file():
            env.warn(f"skipped non-file input {target}")
            continue
        source_digest = F.sha256_file(target)
        copy = dest / target.name
        if copy.exists() or copy.is_symlink():
            # Never reuse an existing path: a previous run left it read-only, and a
            # read-only file with identical content would make this a silent no-op.
            if copy.is_file() and not copy.is_symlink() and F.sha256_file(copy) == source_digest:
                copy = dest / f"{target.stem}-{source_digest[:8]}{target.suffix}"
            if copy.exists() or copy.is_symlink():
                if not args.force:
                    raise ToolkitError(
                        f"refusing to overwrite {copy}",
                        hint="pass --force to replace it, or use a fresh --outdir; "
                             "existing evidence copies are never rewritten silently",
                        details={"existing": str(copy)})
                copy.unlink()
        shutil.copy2(target, copy)
        os.chmod(copy, stat.S_IRUSR | stat.S_IRGRP)
        digest = F.sha256_file(copy)
        record["items"].append({"source": str(target.resolve()),
                                "copy": str(copy.resolve()),
                                "size_bytes": copy.stat().st_size, "sha256": digest,
                                "source_sha256": source_digest,
                                "read_only": True})
        env.add_artifact(copy, "preserved-original", sha256=digest)
    ledger = dest / "preservation-record.json"
    ledger.write_text(json.dumps(record, indent=2), encoding="utf-8")
    env.data = {"record": str(ledger), "items": record["items"]}
    env.add_artifact(ledger, "preservation-record")
    env.limit("Read-only mode bits deter accidental edits; they are not cryptographic proof "
              "of custody. Store the record with the artifacts.")
    return env


def render_markdown_report(env: Envelope, path: Path) -> str:
    data = env.data
    lines = [f"# Android artifact report: `{path.name}`", "",
             f"- Generated: {utc_now()}",
             f"- Tool: `{PROG}` {TOOL_VERSION}",
             f"- Source file: `{path}`",
             f"- Detected kind: `{data.get('kind')}`", ""]
    facts = data.get("facts") or {}
    if facts:
        lines += ["## Package identity", "", "| Field | Value |", "| --- | --- |",
                  f"| Package | `{facts.get('package')}` |",
                  f"| Version | `{facts.get('version_name')}` "
                  f"(`{facts.get('version_code')}`) |",
                  f"| minSdk | `{facts.get('min_sdk')}` |",
                  f"| targetSdk | `{facts.get('target_sdk')}` |",
                  f"| compileSdk | `{facts.get('compile_sdk')}` |",
                  f"| ABIs | `{', '.join(facts.get('abi_dirs') or []) or 'none'}` |",
                  f"| DEX files | `{len(facts.get('dex_files') or [])}` |",
                  f"| Components | `{len(facts.get('components') or [])}` |",
                  f"| Permissions | `{len(facts.get('permissions') or [])}` |", "",
                  "## Launchable activities", ""]
        launches = facts.get("launchable_activities") or []
        lines += [f"- `{a}`" for a in launches] or ["- none"]
        lines.append("")
    resources = data.get("resources") or {}
    if resources.get("present"):
        lines += ["## Resources", "",
                  f"- Entries: `{resources.get('entry_count')}` across "
                  f"`{resources.get('config_count')}` configs",
                  f"- Locales: `{', '.join(resources.get('locales') or []) or 'default only'}`",
                  f"- Densities: "
                  f"`{', '.join(resources.get('densities') or []) or 'default only'}`", ""]
    archive = data.get("zip") or {}
    if archive:
        lines += ["## Archive", "", f"- Entries: `{archive.get('entries')}`",
                  f"- APK signing block present: `{archive.get('signing_block')}`",
                  f"- Structural problems: `{archive.get('problems') or 'none'}`", ""]
    abi = data.get("abi") or {}
    if abi and "abis_present" in abi:
        lines += ["## Native coverage", "",
                  f"- ABIs present: `{', '.join(abi.get('abis_present') or []) or 'none'}`",
                  f"- 64-bit lane: `{abi.get('carries_64bit')}`",
                  f"- 32-bit lane: `{abi.get('carries_32bit')}`",
                  f"- {abi.get('device_capability_note')}", ""]
    lines += ["## Findings", ""]
    if env.findings:
        for finding in sorted(env.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9)):
            lines += [f"### [{finding.severity.upper()}] {finding.rule_id}: {finding.title}",
                      "", finding.detail]
            if finding.remediation:
                lines += ["", f"**Remediation:** {finding.remediation}"]
            lines.append("")
    else:
        lines += ["No findings were produced by the static rule set.", ""]
    lines += ["## Limits and untested claims", ""]
    limits = list(env.limitations) + [
        "No device or emulator was used, so installation and runtime behaviour are untested.",
        "Signature validity was not asserted unless the `verify` command was run on this "
        "host."]
    lines += [f"- {item}" for item in limits]
    lines.append("")
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("report", args)
    path = resolve_input(args.input)
    outdir = Path(args.outdir).expanduser() if args.outdir else Path.cwd() / "aar-report"
    outdir.mkdir(parents=True, exist_ok=True)
    kind = P.detect_artifact(path)["kind"]
    env.tool_status = {k: v.to_dict() for k, v in tools.items()}
    if kind == "apk":
        facts = apk_facts(path, cfg)
        layout = P.parse_zip_layout(path)
        env.data = {"kind": kind, "facts": facts,
                    "zip": {"entries": len(layout.entries),
                            "problems": layout.problems,
                            "signing_block": layout.sign_block_present},
                    "resources": arsc_summary(path), "abi": abi_matrix_data(path)}
        for sub in (cmd_exported_audit, cmd_compat_scan):
            try:
                env.findings.extend(sub(argparse.Namespace(**vars(args)), cfg, tools).findings)
            except ToolkitError as exc:
                env.warn(f"sub-analysis skipped: {exc.message}")
    elif kind == "aab":
        env.data = {"kind": kind, "aab": analyse_aab_path(path).to_dict()}
    else:
        env.data = {"kind": kind, "artifact": P.detect_artifact(path)}
    markdown_path = outdir / f"{path.stem}-report.md"
    json_path = outdir / f"{path.stem}-report.json"
    markdown_path.write_text(render_markdown_report(env, path), encoding="utf-8")
    json_path.write_text(json.dumps(env.to_dict(True, EXIT_OK), indent=2, default=str),
                         encoding="utf-8")
    env.data["report_markdown"] = str(markdown_path)
    env.data["report_json"] = str(json_path)
    env.add_artifact(markdown_path, "report-markdown")
    env.add_artifact(json_path, "report-json")
    env.limit("Reports state what was measured. Absent device tests are listed as untested, "
              "never as passing.")
    return env


def cmd_batch(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("batch", args)
    source = Path(args.input).expanduser()
    if not source.is_dir():
        raise ToolkitError(f"{source} is not a directory",
                           hint="point --input at a folder of artifacts")
    patterns = ([p.strip() for p in args.pattern.split(",")] if args.pattern
                else ["*.apk", "*.aab", "*.apks", "*.xapk"])
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(source.rglob(pattern)))
    cap = cfg["limits"]["max_artifacts_per_batch"]
    if len(files) > cap:
        env.limit(f"processing capped at {cap} artifacts (found {len(files)})")
        files = files[:cap]
    rows = []
    failures = 0
    skipped = 0
    for artifact in files:
        try:
            kind = P.detect_artifact(artifact)["kind"]
            analyzable = kind in ("apk", "aab")
            row: dict[str, Any] = {
                "file": artifact.name, "kind": kind,
                "size": artifact.stat().st_size,
                "status": "ok" if analyzable else "skipped",
                "package": "", "version": "", "min_sdk": "", "target_sdk": "",
                "error": "" if analyzable else f"not an Android package (detected: {kind})"}
            if not analyzable:
                skipped += 1
            if kind == "apk":
                facts = apk_facts(artifact, cfg)
                row.update({"package": facts["package"], "version": facts["version_name"],
                            "min_sdk": facts["min_sdk"], "target_sdk": facts["target_sdk"]})
            elif kind == "aab":
                structure = analyse_aab_path(artifact)
                row["package"] = next(
                    (v for v in structure.manifest_packages.values() if v), "")
            rows.append(row)
        except (ToolkitError, P.ParseError, OSError, ValueError) as exc:
            failures += 1
            rows.append({"file": artifact.name, "kind": "unknown", "status": "error",
                         "size": artifact.stat().st_size if artifact.exists() else 0,
                         "package": "", "version": "", "min_sdk": "", "target_sdk": "",
                         "error": str(exc)})
    outdir = Path(args.outdir).expanduser() if args.outdir else source / "aar-batch"
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "batch-index.csv"
    fields = ["file", "kind", "size", "status", "package", "version", "min_sdk",
              "target_sdk", "error"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    json_path = outdir / "batch-index.json"
    payload = {"source": str(source), "generated_at": utc_now(), "pattern": patterns,
               "total": len(rows), "analyzed": sum(1 for r in rows if r["status"] == "ok"),
               "skipped": skipped, "failures": failures, "items": rows}
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    env.data = dict(payload, csv=str(csv_path), json=str(json_path))
    env.add_artifact(csv_path, "batch-index-csv")
    env.add_artifact(json_path, "batch-index-json")
    if failures:
        env.warn(f"{failures} artifact(s) failed analysis and are marked status=error")
    if skipped:
        env.warn(f"{skipped} file(s) are not Android packages and are marked "
                 f"status=skipped, not analyzed")
    return env


TRIAGE_SIGNATURES = [
    (r"INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures do not match", "signing", "high",
     "Signature mismatch with an installed package.",
     "Uninstall the existing app (data loss) or sign with the original key."),
    (r"INSTALL_FAILED_INSUFFICIENT_STORAGE|NO_SPACE", "storage", "medium",
     "Device storage exhausted.",
     "Free space and retry; check the APK size against free storage."),
    (r"INSTALL_PARSE_FAILED_NO_CERTIFICATES|NOT_SIGNED", "signing", "high",
     "APK is unsigned or its signature block is missing.",
     "Sign with apksigner before installing."),
    (r"INSTALL_PARSE_FAILED_MANIFEST_MALFORMED|MANIFEST", "manifest", "high",
     "Manifest failed to parse on the device.",
     "Decode and inspect the manifest; confirm android:exported and namespace "
     "correctness."),
    (r"INSTALL_FAILED_NO_MATCHING_ABIS|NO_MATCHING_ABIS", "native", "high",
     "Device has no matching ABI for the packaged native code.",
     "Add the missing ABI (for example arm64-v8a) or target the device ABI."),
    (r"java\.lang\.UnsatisfiedLinkError", "native", "high",
     "JNI library could not be loaded.",
     "Confirm the .so is packaged under lib/<abi>/ and that DT_NEEDED dependencies "
     "exist."),
    (r"java\.lang\.NoClassDefFoundError|ClassNotFoundException", "dex", "high",
     "Class missing at runtime.",
     "Check multidex configuration and that the referenced DEX or module was packaged."),
    (r"android\.content\.ActivityNotFoundException", "manifest", "medium",
     "Target activity or intent not resolvable.",
     "Verify the component name and intent-filter/package-visibility rules."),
    (r"FileUriExposedException", "compat", "high",
     "file:// URI shared across apps.",
     "Use a FileProvider content:// URI with explicit grants."),
    (r"NetworkOnMainThreadException", "network", "medium",
     "Network call on the main thread.",
     "Move I/O off the main thread."),
    (r"javax\.net\.ssl\.SSLHandshakeException|CertPathValidatorException", "network", "high",
     "TLS trust failure.",
     "Check the trust store, certificate chain validity and network security config."),
    (r"Cleartext HTTP traffic.*not permitted", "network", "medium",
     "Cleartext traffic blocked by platform policy.",
     "Use HTTPS, or a scoped network security config you are authorized to change."),
    (r"OutOfMemoryError", "memory", "medium", "Heap exhaustion.",
     "Profile allocations; consider largeHeap only with justification."),
    (r"ANR in |Application Not Responding", "anr", "high", "Main thread blocked.",
     "Capture the ANR trace and identify the blocking call."),
    (r"FATAL EXCEPTION", "crash", "high", "Uncaught Java/Kotlin exception.",
     "Read the first 20 lines of the stack trace; retrace with the mapping file if "
     "obfuscated."),
    (r"SIGSEGV|SIGABRT|tombstone", "native-crash", "high", "Native crash.",
     "Inspect the tombstone backtrace and the library build id."),
    (r"INSTALL_FAILED_OLDER_SDK", "sdk", "medium",
     "Device API level is below minSdk.",
     "Lower minSdk or use a newer device lane."),
    (r"AndroidRuntime.*IllegalStateException", "runtime", "medium",
     "IllegalStateException during startup.",
     "Inspect lifecycle ordering in the stack trace."),
]


def cmd_triage(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("triage", args)
    if args.input:
        path = resolve_input(args.input)
        text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
        source = str(path)
    elif args.text:
        text, source = args.text, "<inline>"
    else:
        raise ToolkitError("triage needs --input <logfile> or --text '<message>'")
    matches = []
    for pattern, stage_name, severity, title, next_step in TRIAGE_SIGNATURES:
        for match in re.finditer(pattern, text, re.I):
            matches.append({"pattern": pattern, "stage": stage_name, "severity": severity,
                            "title": title,
                            "line": text.count("\n", 0, match.start()) + 1,
                            "excerpt": text[max(0, match.start() - 90):
                                            match.end() + 90].strip()[:300],
                            "next_step": next_step,
                            "workflow": f"{PROG} {stage_name}"})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in sorted(matches, key=lambda m: SEVERITY_ORDER.get(m["severity"], 9)):
        key = (match["stage"], match["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(match)
    env.data = {"source": source, "chars_scanned": len(text), "matches": unique,
                "earliest_stage": unique[0]["stage"] if unique else "unclassified",
                "verdict": "classified" if unique else "no known signature matched"}
    if not unique:
        env.warn("No known failure signature matched. Capture logcat with "
                 "`adb logcat -b crash` and retry, or read the earliest stack frame "
                 "manually.")
    for match in unique:
        env.add_finding(f"TRI-{match['stage'].upper()[:6]}", match["severity"],
                        match["title"], match["excerpt"], evidence=match)
    env.limit("Triage matches known signatures in the supplied text only; it does not read "
              "a device log or reproduce the failure.")
    return env


def cmd_strings_scan(args: argparse.Namespace, cfg: dict, tools: dict[str, Tool]) -> Envelope:
    env = Envelope("strings-scan", args)
    path = resolve_input(args.input)
    guard_size(path, cfg["limits"]["max_parse_bytes"])
    needles = [n.encode() for n in (args.needles.split(",") if args.needles
                                    else ["Android", "ndk", "clang", "apktool", "kotlin",
                                          "Unity"])]
    buf = path.read_bytes()
    hits = F.elf_strings_scan(buf, tuple(needles))
    env.data = {"source": str(path), "size": len(buf),
                "needles": [n.decode() for n in needles], "hits": hits,
                "hit_count": len(hits)}
    if not hits:
        env.warn("No breadcrumbs matched; try a different --needles list.")
    env.limit("Bounded substring scan; it is not a disassembler and does not prove "
              "provenance.")
    return env


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

COMMON_OPTIONS = (
    ("--config", dict(help="JSON config file (default: asset-default-config.json beside "
                           "the tool)")),
    ("--format", dict(choices=("json", "text"), default="json",
                      help="output format (default: json)")),
    ("--out", dict(help="write the result to this file instead of stdout")),
    ("--timeout", dict(type=float, default=120.0,
                       help="timeout in seconds for external commands (default: 120)")),
    ("--build-tools-dir", dict(help="directory containing aapt/aapt2/apksigner/zipalign")),
    ("--apktool-jar", dict(help="path to a versioned apktool JAR")),
)


def add_global_options(parser: argparse.ArgumentParser) -> None:
    """Options accepted both before and after the subcommand."""
    for name, kwargs in COMMON_OPTIONS:
        parser.add_argument(name, **kwargs)


def add_common(parser: argparse.ArgumentParser) -> None:
    """Subcommand copies that never clobber a global value unless supplied."""
    for name, kwargs in COMMON_OPTIONS:
        local = dict(kwargs)
        local["default"] = argparse.SUPPRESS
        parser.add_argument(name, **local)


class _ToolkitParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors with the documented exit code.

    argparse exits with 2 for usage errors, which this toolkit reserves for invalid
    *input*. Usage errors exit 1 so automation can tell "wrong command line" apart
    from "bad artifact".
    """

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        if not getattr(self, "_is_subparser", False):
            print(f"run '{self.prog} --help' for the command list", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _ToolkitParser(
        prog=PROG,
        description=("Lawful Android artifact recovery and compatibility toolkit. Core "
                     "analysis is pure Python; the apktool and signing lanes activate when "
                     "host tools are present."),
        epilog=("Examples:\n"
                "  tools-android-artifact-recovery.py capabilities\n"
                "  tools-android-artifact-recovery.py badging game.apk --format text\n"
                "  tools-android-artifact-recovery.py compat-scan game.apk --out findings.json\n"
                "  tools-android-artifact-recovery.py page16k game.apk\n"
                "  tools-android-artifact-recovery.py apktool decode game.apk --outdir work\n"
                "  tools-android-artifact-recovery.py --build-tools-dir \"$BT\" sign game.apk "
                "--output new.apk --keystore debug.p12 --confirm\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{PROG} {TOOL_VERSION}")
    add_global_options(parser)
    sub = parser.add_subparsers(dest="command", metavar="<command>",
                                 parser_class=_ToolkitParser)

    def add(name: str) -> argparse.ArgumentParser:
        parser_for = sub.add_parser(name, help=COMMAND_HELP[name],
                                    description=COMMAND_HELP[name])
        add_common(parser_for)
        return parser_for

    for name in ("capabilities", "doctor", "self-test"):
        add(name)

    sub_identify = add("identify")
    sub_identify.add_argument("inputs", nargs="+", help="artifact paths")

    for name in ("zip-health", "aab-inspect", "exported-audit", "compat-scan", "verify",
                 "certificate"):
        add(name).add_argument("input")

    sub_badging = add("badging")
    sub_badging.add_argument("input")
    sub_badging.add_argument("--aapt-crosscheck", action="store_true",
                             help="also run aapt dump badging when available")

    sub_manifest = add("manifest")
    sub_manifest.add_argument("input")
    sub_manifest.add_argument("--member", default="AndroidManifest.xml",
                              help="manifest member inside the archive")
    sub_manifest.add_argument("--tree", action="store_true",
                              help="include the full element tree")
    sub_manifest.add_argument("--xml-out", help="write a decoded XML rendering here")

    sub_arsc = add("arsc")
    sub_arsc.add_argument("input")
    sub_arsc.add_argument("--filter", help="case-insensitive filter on type/key or config")

    sub_dex = add("dex")
    sub_dex.add_argument("input")
    sub_dex.add_argument("--classes", action="store_true",
                         help="include a package histogram")

    sub_elf = add("elf")
    sub_elf.add_argument("input")
    sub_elf.add_argument("--strings", action="store_true",
                         help="scan for toolchain breadcrumbs")

    for name in ("abi-matrix", "page16k"):
        add(name).add_argument("input")

    sub_entry = add("entry")
    sub_entry.add_argument("input")
    sub_entry.add_argument("--filter", help="glob substring filter on entry names")
    sub_entry.add_argument("--extract", metavar="DIR",
                           help="extract matching entries into DIR")

    sub_apktool = add("apktool")
    sub_apktool.add_argument("operation", choices=("decode", "build"))
    sub_apktool.add_argument("input", nargs="?", help="APK to decode")
    sub_apktool.add_argument("--outdir", help="decode destination")
    sub_apktool.add_argument("--srcdir", help="decoded tree to build")
    sub_apktool.add_argument("--output", help="build output APK path")
    sub_apktool.add_argument("--workdir", help="workspace for state and logs")
    sub_apktool.add_argument("--no-res", action="store_true", help="decode: skip resources (-r)")
    sub_apktool.add_argument("--no-src", action="store_true", help="decode: skip smali (-s)")
    sub_apktool.add_argument("--use-aapt2", action="store_true", help="build: pass --use-aapt2")
    sub_apktool.add_argument("--force", action="store_true",
                             help="overwrite an existing destination")

    sub_sign = add("sign")
    sub_sign.add_argument("input")
    sub_sign.add_argument("--output", required=True)
    sub_sign.add_argument("--keystore", required=True)
    sub_sign.add_argument("--ks-pass",
                          help="keystore password (prefer the environment variable)")
    sub_sign.add_argument("--ks-alias")
    sub_sign.add_argument("--min-sdk", type=int)
    sub_sign.add_argument("--v1", action="store_true")
    sub_sign.add_argument("--v2", action="store_true")
    sub_sign.add_argument("--force", action="store_true")
    sub_sign.add_argument("--confirm", action="store_true", help="approve the signing action")

    sub_rebuild = add("rebuild")
    sub_rebuild.add_argument("input")
    sub_rebuild.add_argument("--patch", help="unified diff applied inside the decoded tree")
    sub_rebuild.add_argument("--intent", help="what the authorized change should achieve")
    sub_rebuild.add_argument("--authorization", help="who authorized the modification")
    sub_rebuild.add_argument("--keystore", required=True)
    sub_rebuild.add_argument("--ks-pass")
    sub_rebuild.add_argument("--ks-alias")
    sub_rebuild.add_argument("--outdir")
    sub_rebuild.add_argument("--output")
    sub_rebuild.add_argument("--workdir")
    sub_rebuild.add_argument("--confirm", action="store_true")

    sub_preserve = add("preserve")
    sub_preserve.add_argument("inputs", nargs="+")
    sub_preserve.add_argument("--outdir", required=True)
    sub_preserve.add_argument("--note")
    sub_preserve.add_argument("--authorization")
    sub_preserve.add_argument("--force", action="store_true")
    sub_preserve.add_argument("--confirm", action="store_true")

    sub_report = add("report")
    sub_report.add_argument("input")
    sub_report.add_argument("--outdir")

    sub_batch = add("batch")
    sub_batch.add_argument("input", help="directory containing artifacts")
    sub_batch.add_argument("--pattern", help="comma-separated globs")
    sub_batch.add_argument("--outdir")

    sub_triage = add("triage")
    sub_triage.add_argument("--input", help="log file to classify")
    sub_triage.add_argument("--text", help="inline failure text")

    sub_strings = add("strings-scan")
    sub_strings.add_argument("input")
    sub_strings.add_argument("--needles", help="comma-separated substrings")
    return parser


HANDLERS: dict[str, Callable[[argparse.Namespace, dict, dict[str, Tool]], Envelope]] = {
    "capabilities": cmd_capabilities, "doctor": cmd_doctor, "self-test": cmd_self_test,
    "identify": cmd_identify, "zip-health": cmd_zip_health, "badging": cmd_badging,
    "manifest": cmd_manifest, "arsc": cmd_arsc, "dex": cmd_dex, "elf": cmd_elf,
    "abi-matrix": cmd_abi_matrix, "page16k": cmd_page16k, "entry": cmd_entry,
    "aab-inspect": cmd_aab_inspect, "exported-audit": cmd_exported_audit,
    "compat-scan": cmd_compat_scan, "apktool": cmd_apktool, "sign": cmd_sign,
    "verify": cmd_verify, "certificate": cmd_certificate, "rebuild": cmd_rebuild,
    "preserve": cmd_preserve, "report": cmd_report, "batch": cmd_batch,
    "triage": cmd_triage, "strings-scan": cmd_strings_scan,
}


def error_envelope(command: str, exc: ToolkitError) -> dict[str, Any]:
    return {"ok": False, "command": command,
            "tool": {"name": PROG, "version": TOOL_VERSION}, "generated_at": utc_now(),
            "exit_code": exc.code, "data": {}, "findings": [], "artifacts": [],
            "warnings": [], "tool_status": [],
            "errors": [{"code": "error", "message": exc.message, "hint": exc.hint,
                        "details": exc.details}],
            "limitations": []}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        print(f"\n{PROG}: error: a command is required "
              f"(see 'capabilities' for the full list)", file=sys.stderr)
        return EXIT_USAGE
    fmt = getattr(args, "format", "json") or "json"
    try:
        cfg = load_config(getattr(args, "config", None))
    except ToolkitError as exc:
        if fmt == "json":
            print(json.dumps(error_envelope(args.command, exc), indent=2))
        else:
            print(f"error: {exc.message}\nhint: {exc.hint}", file=sys.stderr)
        return exc.code
    try:
        tools = resolve_tools(cfg, args)
        env = HANDLERS[args.command](args, cfg, tools)
        code = EXIT_OK
        if args.command == "self-test":
            code = env.data.pop("_exit", EXIT_OK)
        return emit(env, args, ok=(code == EXIT_OK), code=code)
    except ToolkitError as exc:
        payload = error_envelope(args.command, exc)
        if fmt == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(f"error: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
        if getattr(args, "out", None):
            out_path = Path(args.out).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return exc.code
    except P.ParseError as exc:
        err = ToolkitError(f"parse failure: {exc}", code=EXIT_INVALID_INPUT,
                           hint="the artifact may be corrupt or use an unsupported layout")
        payload = error_envelope(args.command, err)
        if fmt == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(f"error: {err.message}", file=sys.stderr)
        return err.code
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "command": args.command, "exit_code": 130,
                          "errors": [{"code": "cancelled", "message": "interrupted"}]},
                         indent=2))
        return 130
    except BrokenPipeError:  # pragma: no cover - shell piping
        return EXIT_PARTIAL
    except Exception as exc:  # noqa: BLE001 - final guard: never leak a traceback as output
        trace = traceback.format_exc(limit=6)
        err = ToolkitError(
            f"unexpected internal error: {type(exc).__name__}: {exc}", code=EXIT_PARTIAL,
            hint="This is a toolkit defect, not necessarily an artifact defect. Re-run with "
                 "--format text, and report the traceback tail from details.",
            details={"traceback_tail": "\n".join(trace.strip().splitlines()[-6:])})
        payload = error_envelope(args.command, err)
        if fmt == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(f"error: {err.message}", file=sys.stderr)
            print(err.hint, file=sys.stderr)
        return err.code


if __name__ == "__main__":
    sys.exit(main())
