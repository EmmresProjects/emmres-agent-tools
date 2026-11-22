"""Shared runtime for the iOS/iPadOS Agent Skill Tool.

Standard library only. Every other tools-* module imports from here so that
limits, status codes, policy gates, hashing, report assembly and the command
runner behave identically across capabilities.

Design rules enforced in this module
------------------------------------
* originals are never mutated; writes go to new paths or explicit --out paths
* every report carries schema_version, limits actually applied, and toolchain info
* planning happens before execution; destructive/publishing actions need approval
* no arbitrary shell execution: bounded, allow-listed, timed out, output-capped
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0.0"
TOOL_NAME = "ios-ipados-agent-toolkit"
TOOL_VERSION = "1.0.0"
POLICY_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# status vocabulary (mirrors the catalog's status aggregation vocabulary)
# --------------------------------------------------------------------------
OK = "OK"
FOUND = "FOUND"
MISSING = "MISSING"
WRONG_VERSION = "WRONG VERSION"
BROKEN = "BROKEN"
LICENSE_REQUIRED = "LICENSE REQUIRED"
MACOS_REQUIRED = "MACOS HOST REQUIRED"
ACCOUNT_REQUIRED = "APPLE DEVELOPER ACCOUNT REQUIRED"
DEVICE_REQUIRED = "PHYSICAL DEVICE REQUIRED"
NETWORK_REQUIRED = "NETWORK REQUIRED"
SKIPPED = "SKIPPED"
BLOCKED = "BLOCKED"
FAIL = "FAIL"
WARN = "WARN"

LEVELS = ("info", "warn", "error", "critical")

_REQ_TO_STATUS = {
    "macos-required": MACOS_REQUIRED,
    "apple-account-required": ACCOUNT_REQUIRED,
    "physical-device-required": DEVICE_REQUIRED,
    "network-required": NETWORK_REQUIRED,
    "license-required": LICENSE_REQUIRED,
}


class ToolError(Exception):
    """Base class for expected, user-facing failures."""

    exit_code = 1
    code = "tool-error"

    def __init__(self, message: str, *, code: Optional[str] = None, remediation: str = "",
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.remediation = remediation
        self.details = details or {}


class UsageError(ToolError):
    exit_code = 2
    code = "usage-error"


class InputError(ToolError):
    exit_code = 3
    code = "invalid-input"


class LimitError(ToolError):
    exit_code = 4
    code = "limit-exceeded"


class PolicyError(ToolError):
    exit_code = 5
    code = "policy-blocked"


class ApprovalRequired(ToolError):
    exit_code = 5
    code = "approval-required"


class CommandFailure(ToolError):
    def __init__(self, message: str, *, result: "CommandResult", remediation: str = "") -> None:
        super().__init__(message, code="command-failed", remediation=remediation)
        self.result = result


# --------------------------------------------------------------------------
# limits
# --------------------------------------------------------------------------
@dataclass
class Limits:
    """Every bounded resource is declared here and echoed in every report."""

    max_entries: int = 200_000          # archive / directory entries walked
    max_file_bytes: int = 512 << 20     # single file read/parse ceiling
    max_total_bytes: int = 2 << 30      # aggregate extraction ceiling
    max_depth: int = 24                 # recursion depth (zip payload walk)
    timeout_s: int = 120                # per external command
    retries: int = 0                    # bounded retries for idempotent probes
    retry_backoff_s: float = 1.0
    max_workers: int = 4                # concurrency ceiling
    max_findings: int = 5_000           # report finding ceiling
    max_output_bytes: int = 1 << 20     # captured stdout/stderr per command
    max_disk_ratio: float = 0.9         # refuse to write past this filesystem usage

    def merge(self, **overrides: Any) -> "Limits":
        data = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue
            if key not in data:
                raise UsageError(f"unknown limit '{key}'")
            if not isinstance(value, (int, float)) or value < 0:
                raise UsageError(f"limit '{key}' must be a non-negative number")
            data[key] = value
        if data["max_entries"] < 1:
            raise UsageError("--max-entries must be >= 1")
        if data["timeout_s"] < 1:
            raise UsageError("--timeout must be >= 1 second")
        if data["max_depth"] < 1:
            raise UsageError("--max-depth must be >= 1")
        if data["max_workers"] < 1 or data["max_workers"] > 32:
            raise UsageError("--max-workers must be between 1 and 32")
        if data["retries"] > 10:
            raise UsageError("--retries must be <= 10 (retries must stay bounded)")
        return Limits(**data)


def default_limits(**overrides: Any) -> Limits:
    return Limits().merge(**overrides)


# --------------------------------------------------------------------------
# findings + report
# --------------------------------------------------------------------------
@dataclass
class Finding:
    code: str
    level: str
    message: str
    capability: str = ""
    path: str = ""
    remediation: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", {}, None)}


class Report:
    """Versioned, deterministic container for command output."""

    def __init__(self, command: str, limits: Limits, inputs: Optional[Dict[str, Any]] = None) -> None:
        self.command = command
        self.limits = limits
        self.inputs = inputs or {}
        self.started = time.time()
        self.findings: List[Finding] = []
        self.data: Dict[str, Any] = {}
        self.errors: List[Dict[str, Any]] = []
        self.next_actions: List[str] = []
        self.truncated = False
        self.run_id = uuid.uuid4().hex[:12]

    # -- findings ---------------------------------------------------------
    def add(self, code: str, level: str, message: str, **kw: Any) -> None:
        if len(self.findings) >= self.limits.max_findings:
            if not self.truncated:
                self.truncated = True
                self.findings.append(Finding("report/findings-truncated", "warn",
                                             f"finding ceiling {self.limits.max_findings} reached; "
                                             "later findings were dropped",
                                             remediation="raise --max-findings or narrow the input"))
            return
        if level not in LEVELS:
            raise UsageError(f"invalid finding level '{level}'")
        self.findings.append(Finding(code=code, level=level, message=message, **kw))

    def error(self, exc: BaseException) -> None:
        if isinstance(exc, ToolError):
            entry = {"code": exc.code, "message": exc.message, "remediation": exc.remediation}
            if exc.details:
                entry["details"] = exc.details
        else:
            entry = {"code": "unexpected-error", "message": f"{type(exc).__name__}: {exc}"}
        self.errors.append(entry)

    # -- derived state ----------------------------------------------------
    @property
    def status(self) -> str:
        levels = {f.level for f in self.findings}
        if "critical" in levels or self.errors:
            return FAIL
        if "error" in levels:
            return FAIL
        if "warn" in levels:
            return WARN
        return OK

    def as_dict(self) -> Dict[str, Any]:
        counts = {lvl: sum(1 for f in self.findings if f.level == lvl) for lvl in LEVELS}
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION, "policy_version": POLICY_VERSION},
            "run_id": self.run_id,
            "command": self.command,
            "generated_at": now_iso(),
            "status": self.status,
            "ok": self.status == OK,
            "host": host_summary(),
            "inputs": self.inputs,
            "limits": asdict(self.limits),
            "counts": counts,
            "findings": [f.as_dict() for f in self.findings],
            "data": self.data,
            "errors": self.errors,
            "next_actions": self.next_actions,
            "truncated": self.truncated,
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def host_summary() -> Dict[str, Any]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "is_macos": platform.system() == "Darwin",
        "is_linux": platform.system() == "Linux",
        "cwd_relative_root": ".",
    }


def sha256_file(path: Path, limit: Optional[int] = None) -> str:
    h = hashlib.sha256()
    read = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            read += len(chunk)
            if limit is not None and read > limit:
                raise LimitError(f"{path} exceeds hashing ceiling", code="file-too-large",
                                 remediation="raise --max-file-bytes or hash a smaller artifact")
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def ensure_readable_file(path: str, limits: Limits, *, what: str = "input") -> Path:
    p = Path(path)
    if not p.exists():
        raise InputError(f"{what} not found: {path}", code="input-missing",
                         remediation="check the path or run 'probe' to see what is available")
    if not p.is_file():
        raise InputError(f"{what} is not a regular file: {path}", code="input-not-file")
    size = p.stat().st_size
    if size > limits.max_file_bytes:
        raise LimitError(f"{what} is {human_bytes(size)} which exceeds --max-file-bytes "
                         f"({human_bytes(limits.max_file_bytes)})", code="file-too-large",
                         remediation="raise --max-file-bytes or work on an extracted slice")
    if size == 0:
        raise InputError(f"{what} is empty: {path}", code="input-empty")
    return p


def ensure_readable_dir(path: str, *, what: str = "directory") -> Path:
    p = Path(path)
    if not p.exists():
        raise InputError(f"{what} not found: {path}", code="input-missing")
    if not p.is_dir():
        raise InputError(f"{what} is not a directory: {path}", code="input-not-directory")
    return p


def ensure_output_path(path: Optional[str], *, default: Optional[Path] = None) -> Optional[Path]:
    """Resolve an output path; refuse to overwrite an existing file unless allowed."""
    if path is None:
        return default
    p = Path(path)
    if p.exists() and p.is_dir():
        raise InputError(f"output path is a directory: {path}", code="output-is-directory")
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def guard_free_space(target: Path, needed: int, limits: Limits) -> None:
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError:
        return
    if usage.used / usage.total > limits.max_disk_ratio:
        raise LimitError(f"filesystem holding {probe} is {usage.used / usage.total:.0%} full, "
                         f"above the --max-disk-ratio ceiling", code="disk-pressure",
                         remediation="free space or move outputs to another volume")
    if needed > usage.free:
        raise LimitError(f"need {human_bytes(needed)} but only {human_bytes(usage.free)} free",
                         code="disk-pressure", remediation="reduce the extraction scope")


def write_json(path: Optional[Path], payload: Dict[str, Any]) -> Optional[Path]:
    if path is None:
        return None
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def safe_relpath(name: str) -> Optional[str]:
    """Return a normalised relative path, or None when the entry is unsafe."""
    if not name or name in (".", ".."):
        return None
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts: List[str] = []
    for part in n.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) if parts else None


def state_dir(root: Optional[str], env_var: str = "IOSH_STATE_DIR") -> Path:
    """Resolve the writable state directory (pins, cache index, dSYM index, workspaces)."""
    candidate = root or os.environ.get(env_var) or os.path.join(tempfile.gettempdir(),
                                                               "iosh-state")
    p = Path(candidate)
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------
DEFAULT_POLICY: Dict[str, Any] = {
    "schema_version": POLICY_VERSION,
    "authorization": {
        "required_attestation": "I own or am authorized to inspect, build, test, sign or distribute "
                                "these assets",
        "denied_requests": [
            {"id": "bypass-activation-lock", "patterns": ["activation lock", "icloud lock", "find my iphone off"],
             "reason": "Activation Lock and ownership controls must never be bypassed"},
            {"id": "bypass-passcode", "patterns": ["bypass passcode", "crack passcode", "brute force passcode",
                                                   "unlock without password"],
             "reason": "Passcode, biometric and Secure Enclave protections must never be bypassed"},
            {"id": "bypass-drm", "patterns": ["remove drm", "bypass drm", "crack fairplay", "decrypt ipa",
                                              "dump decrypted", "circumvent receipt"],
             "reason": "DRM, purchase and receipt protections must never be bypassed"},
            {"id": "bypass-store", "patterns": ["bypass in-app purchase", "free purchases", "fake receipt",
                                                "jailbreak detection bypass to install", "sideload someone else"],
             "reason": "App Store purchase and delivery controls must never be bypassed"},
            {"id": "credential-theft", "patterns": ["steal certificate", "extract private key from", "harvest apple id",
                                                    "phish apple id", "keychain dump of another"],
             "reason": "Credential and certificate theft is refused"},
            {"id": "identity-spoof", "patterns": ["spoof another developer", "impersonate team", "forged certificate"],
             "reason": "Identity and team impersonation is refused"},
        ],
        "approval_required": [
            {"id": "destructive-device", "patterns": ["erase", "delete device", "uninstall", "restart", "shutdown",
                                                      "reset", "wipe", "remove profile"],
             "reason": "destructive or disruptive device action"},
            {"id": "signing", "patterns": ["codesign", "sign ", "keychain", "provisioning profile install"],
             "reason": "signing with protected identities"},
            {"id": "publishing", "patterns": ["upload", "testflight", "app store submit", "transporter", "distribute",
                                              "release to", "phased release"],
             "reason": "publishing or distributing an artifact"},
            {"id": "data-extraction", "patterns": ["backup", "extract user data", "dump container", "photos", "contacts"],
             "reason": "extracting user data"},
            {"id": "dynamic-analysis", "patterns": ["frida", "objection", "inject", "attach to running",
                                                    "instrument"],
             "reason": "instrumenting a running application"},
            {"id": "network-capture", "patterns": ["mitmproxy", "charles", "proxyman", "wireshark", "tshark",
                                                   "certificate pinning disable"],
             "reason": "intercepting network traffic"},
            {"id": "spend", "patterns": ["billing", "paid lane", "device farm purchase", "increase quota"],
             "reason": "financial commitment"},
        ],
    },
    "licenses": {
        "commercial_tools": ["hopper", "ida", "jtool2", "pvrtool", "charltes", "charles", "proxyman",
                             "macstadium", "browserstack", "sauce labs", "aws device farm", "unity",
                             "unreal", "cocos2d", "mobsf cloud"],
        "apple_components": ["xcode", "iphoneos sdk", "iphonesimulator sdk", "simulator runtime",
                             "apple sdk", "dyld shared cache"],
        "rule": "Apple-licensed components must never be copied into a Linux image or redistributed",
    },
    "routing": {
        "macos_required_keywords": ["xcodebuild", "xcrun", "simctl", "codesign", "xcarchive", "archive",
                                    "export ipa", "dtrace", "instruments", "xcodebuild test", "ios simulator",
                                    "simulator", "actool", "ibtool", "metal", "metallib", "atos", "dsymutil",
                                    "lldb attach", "security find-identity", "otool", "plutil"],
        "device_required_keywords": ["device", "udid", "usb", "pair", "idevice", "iproxy", "ifuse", "syslog",
                                     "physical iphone", "physical ipad", "developer mode"],
        "account_required_keywords": ["app store connect", "testflight", "team id", "issuer id", "api key",
                                      "certificate", "provisioning profile", "app store submission"],
        "network_required_keywords": ["upload", "download", "api", "http", "https", "push", "apns", "icloud",
                                      "cloudkit", "storekit sandbox", "device farm"],
        "license_required_keywords": ["hopper", "ida pro", "jtool2", "pvrtool", "charles", "proxyman",
                                      "macstadium", "browserstack", "sauce labs", "device farm"],
        "linux_capable": ["inspect", "parse", "report", "hash", "zip", "plist", "macho", "xmach", "ipa",
                          "provisioning", "crash report", "dwarf", "artifact", "privacy manifest",
                          "secrets", "string", "metadata", "diff", "audit", "plan"],
    },
    "self_test": {
        "required_tools": ["idevice_id", "ideviceinfo", "ideviceinstaller", "idevicesyslog", "iproxy",
                           "ifuse", "plistutil", "sqlite3", "ffmpeg", "xcodebuild", "xcrun", "swift",
                           "clang", "codesign", "lldb", "pod", "carthage", "fastlane", "ios-deploy",
                           "git", "zip", "python3"],
        "host_status_when_absent": {
            "idevice_id": DEVICE_REQUIRED, "ideviceinfo": DEVICE_REQUIRED,
            "ideviceinstaller": DEVICE_REQUIRED, "idevicesyslog": DEVICE_REQUIRED,
            "iproxy": DEVICE_REQUIRED, "ifuse": DEVICE_REQUIRED, "ios-deploy": DEVICE_REQUIRED,
            "xcodebuild": MACOS_REQUIRED, "xcrun": MACOS_REQUIRED, "codesign": MACOS_REQUIRED,
            "lldb": MACOS_REQUIRED, "pod": MACOS_REQUIRED, "carthage": MACOS_REQUIRED,
            "fastlane": MACOS_REQUIRED, "swift": MACOS_REQUIRED, "clang": MACOS_REQUIRED,
        },
    },
    "exit_codes": {"OK": 0, WARN: 0, FAIL: 1, "usage": 2, "invalid-input": 3, "limit": 4,
                   "policy": 5, "approval": 5, "internal": 70},
}


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY))  # deep copy
    if path:
        p = ensure_readable_file(path, default_limits(), what="policy file")
        try:
            override = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError(f"policy file is not valid JSON: {exc}", code="policy-invalid",
                             remediation="compare against asset-policy.json") from exc
        policy = _deep_merge(policy, override)
    return policy


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


@dataclass
class RouteDecision:
    accepted: bool
    classification: str                 # linux-capable | macos-required | ...
    status: str
    requirement: str
    reasons: List[str]
    approvals: List[Dict[str, str]]
    denied: List[Dict[str, str]]
    keywords: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_request(request: str, policy: Dict[str, Any], *,
                     have_macos: bool = False, have_device: bool = False,
                     have_account: bool = False, network: bool = False) -> RouteDecision:
    """Decide where a described action may run, and whether it needs approval."""
    text = " " + re.sub(r"\s+", " ", request.lower()) + " "
    if not request.strip():
        raise UsageError("request text is empty")

    denied: List[Dict[str, str]] = []
    for rule in policy["authorization"]["denied_requests"]:
        hits = [p for p in rule["patterns"] if p in text]
        if hits:
            denied.append({"id": rule["id"], "reason": rule["reason"], "matched": ", ".join(hits)})

    approvals: List[Dict[str, str]] = []
    for rule in policy["authorization"]["approval_required"]:
        hits = [p for p in rule["patterns"] if p in text]
        if hits:
            approvals.append({"id": rule["id"], "reason": rule["reason"], "matched": ", ".join(hits)})

    routing = policy["routing"]
    found: List[str] = []
    macos_hits = [k for k in routing["macos_required_keywords"] if k in text]
    device_hits = [k for k in routing["device_required_keywords"] if k in text]
    account_hits = [k for k in routing["account_required_keywords"] if k in text]
    network_hits = [k for k in routing["network_required_keywords"] if k in text]
    license_hits = [k for k in routing["license_required_keywords"] if k in text]
    linux_hits = [k for k in routing["linux_capable"] if k in text]

    # precedence: hard platform boundaries before convenience
    if license_hits:
        classification, requirement = "license-required", "license-required"
    elif macos_hits:
        classification, requirement = "macos-required", "macos-required"
    elif device_hits:
        classification, requirement = "physical-device-required", "physical-device-required"
    elif account_hits:
        classification, requirement = "apple-account-required", "apple-account-required"
    elif network_hits:
        classification, requirement = "network-required", "network-required"
    else:
        classification, requirement = "linux-capable", "linux-native"

    satisfied = {
        "macos-required": have_macos,
        "physical-device-required": have_device,
        "apple-account-required": have_account,
        "network-required": network,
        "license-required": False,
        "linux-capable": True,
    }[classification]
    status = OK if satisfied else _REQ_TO_STATUS.get(requirement, MISSING)

    for label, hits in (("macOS", macos_hits), ("device", device_hits),
                        ("Apple account", account_hits), ("network", network_hits),
                        ("licensed tool", license_hits), ("linux-capable", linux_hits)):
        if hits:
            found.append(f"{label}: {', '.join(sorted(set(hits)))[:180]}")

    accepted = not denied
    if denied:
        status = BLOCKED

    return RouteDecision(
        accepted=accepted,
        classification=classification,
        status=status,
        requirement=requirement,
        reasons=found,
        approvals=approvals,
        denied=denied,
        keywords=sorted({*macos_hits, *device_hits, *account_hits, *network_hits,
                         *license_hits, *linux_hits}),
    )


def require_approval(decision: RouteDecision, approved: Sequence[str]) -> None:
    """Raise ApprovalRequired listing exactly which gates are unmet."""
    unmet = [a for a in decision.approvals if a["id"] not in set(approved)]
    if unmet:
        raise ApprovalRequired(
            "approval required before this action: " + ", ".join(a["id"] for a in unmet),
            details={"unmet": unmet, "granted": list(approved)},
            remediation="re-run with --approve " + " --approve ".join(a["id"] for a in unmet)
                        + " after reviewing the described side effects",
        )


def require_not_denied(decision: RouteDecision) -> None:
    if decision.denied:
        raise PolicyError(
            "refused by policy: " + "; ".join(d["reason"] for d in decision.denied),
            details={"denied": decision.denied},
            remediation="this toolkit never implements bypasses of ownership, passcode, DRM, purchase "
                        "or credential controls; describe an authorized alternative",
        )


# --------------------------------------------------------------------------
# command runner (bounded, dry-runnable, allow-listed)
# --------------------------------------------------------------------------
COMMAND_ALLOWLIST = {  # binaries this tool may invoke on the host
    "idevice_id", "ideviceinfo", "ideviceinstaller", "idevicesyslog", "iproxy", "ifuse",
    "plistutil", "sqlite3", "ffmpeg", "ffprobe", "identify", "convert", "magick",
    "xcodebuild", "xcrun", "codesign", "security", "swift", "swiftc", "clang", "lldb",
    "pod", "carthage", "fastlane", "bundle", "tuist", "xcodegen", "bazel",
    "git", "zip", "unzip", "python3", "ios-deploy", "atos", "dsymutil", "otool", "plutil",
    "swift-demangle", "jtool2", "ipsw", "log", "xcrun simctl",
}


@dataclass
class CommandResult:
    argv: List[str]
    status: str
    exit_code: Optional[int]
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    attempts: int = 1
    skipped_reason: str = ""
    timed_out: bool = False
    truncated: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_command(argv: Sequence[str], limits: Limits, *, dry_run: bool = False,
                cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None,
                allowlist: Optional[Iterable[str]] = None,
                stdin_data: Optional[bytes] = None) -> CommandResult:
    """Run one bounded, allow-listed command. Never uses shell=True."""
    if not argv:
        raise UsageError("empty command")
    allowed = set(allowlist) if allowlist is not None else COMMAND_ALLOWLIST
    head = argv[0]
    if head not in allowed:
        raise PolicyError(f"command '{head}' is not on the allow-list", code="command-not-allowed",
                          remediation="extend the allow-list in your policy file only for reviewed tools")
    if any(a in ("|", ">", "<", ";", "&&", "||", "$(", "`") for a in argv):
        raise PolicyError("shell metacharacters are not permitted in commands", code="shell-metacharacter")

    if dry_run:
        return CommandResult(argv=list(argv), status=SKIPPED, exit_code=None, duration_ms=0,
                             skipped_reason="dry-run")

    exe = shutil.which(argv[0])
    if exe is None:
        return CommandResult(argv=list(argv), status=MISSING, exit_code=127, duration_ms=0,
                             skipped_reason=f"{argv[0]} is not installed on this host")

    attempts = 0
    last: Optional[CommandResult] = None
    while attempts <= limits.retries:
        attempts += 1
        started = time.time()
        try:
            proc = subprocess.run(
                [exe, *argv[1:]], cwd=str(cwd) if cwd else None,
                input=stdin_data, capture_output=True, timeout=limits.timeout_s,
                env={**os.environ, **(env or {})}, check=False,
            )
            dur = int((time.time() - started) * 1000)
            out, err = proc.stdout or b"", proc.stderr or b""
            truncated = len(out) > limits.max_output_bytes or len(err) > limits.max_output_bytes
            res = CommandResult(
                argv=list(argv), status=OK if proc.returncode == 0 else FAIL,
                exit_code=proc.returncode, duration_ms=dur,
                stdout=out[: limits.max_output_bytes].decode("utf-8", "replace"),
                stderr=err[: limits.max_output_bytes].decode("utf-8", "replace"),
                attempts=attempts, truncated=truncated,
            )
        except subprocess.TimeoutExpired:
            dur = int((time.time() - started) * 1000)
            res = CommandResult(argv=list(argv), status=FAIL, exit_code=None, duration_ms=dur,
                                stderr=f"timed out after {limits.timeout_s}s", attempts=attempts,
                                timed_out=True)
        except OSError as exc:  # pragma: no cover - host dependent
            dur = int((time.time() - started) * 1000)
            res = CommandResult(argv=list(argv), status=BROKEN, exit_code=None, duration_ms=dur,
                                stderr=str(exc), attempts=attempts)
        last = res
        if res.status == OK or not res.timed_out and res.status != BROKEN:
            break
        if attempts <= limits.retries:
            time.sleep(min(limits.retry_backoff_s * attempts, 5.0))
    assert last is not None
    return last


def which_all(names: Iterable[str]) -> Dict[str, Optional[str]]:
    return {n: shutil.which(n) for n in names}


# --------------------------------------------------------------------------
# output rendering
# --------------------------------------------------------------------------
def render_human(report: Dict[str, Any], *, verbose: bool = False, max_findings: int = 40,
                 max_next_actions: int = 6) -> str:
    lines: List[str] = []
    head = f"[{report['status']}] {report['command']}  (run {report['run_id']})"
    lines.append(head)
    lines.append("-" * min(len(head), 78))
    host = report["host"]
    lines.append(f"host      : {host['system']} {host['machine']} | python {host['python']}"
                 f" | macos={host['is_macos']}")
    lines.append(f"generated : {report['generated_at']}")
    lim = report["limits"]
    lines.append(f"limits    : timeout={lim['timeout_s']}s entries={lim['max_entries']} "
                 f"file={human_bytes(lim['max_file_bytes'])} retries={lim['retries']} "
                 f"workers={lim['max_workers']}")

    data = report.get("data") or {}
    summary = data.get("summary")
    if summary:
        lines.append("")
        lines.append("summary")
        for key, value in summary.items():
            lines.append(f"  {key:<28} {value}")

    if verbose and data:
        lines.append("")
        lines.append("data")
        lines.append(json.dumps(data, indent=2)[:20000])

    findings = report["findings"]
    if findings:
        lines.append("")
        lines.append("findings")
        shown = findings if verbose else findings[:max_findings]
        for f in shown:
            loc = f" [{f['path']}]" if f.get("path") else ""
            lines.append(f"  {f['level'].upper():<8} {f['code']}{loc}: {f['message']}")
            if verbose and f.get("remediation"):
                lines.append(f"           fix: {f['remediation']}")
        if len(findings) > len(shown):
            lines.append(f"  ... {len(findings) - len(shown)} more finding(s); see --json-out, "
                         f"--verbose or filter by level")

    if report["errors"]:
        lines.append("")
        lines.append("errors")
        for e in report["errors"]:
            lines.append(f"  {e['code']}: {e['message']}")
            if e.get("remediation"):
                lines.append(f"           fix: {e['remediation']}")

    if report["next_actions"]:
        lines.append("")
        lines.append("next actions")
        for a in report["next_actions"][:max_next_actions]:
            lines.append(f"  - {a}")

    counts = report["counts"]
    lines.append("")
    lines.append(f"counts: info={counts['info']} warn={counts['warn']} "
                 f"error={counts['error']} critical={counts['critical']}"
                 + ("  (truncated)" if report.get("truncated") else ""))
    return "\n".join(lines)


def emit(report: Report, *, fmt: str = "human", verbose: bool = False,
         json_path: Optional[Path] = None, human_path: Optional[Path] = None) -> int:
    payload = report.as_dict()
    if json_path:
        write_json(json_path, payload)
    text = render_human(payload, verbose=verbose,
                        max_findings=0 if False else 40)
    if human_path:
        human_path.write_text(text + "\n", encoding="utf-8")
    if fmt == "json" or fmt == "both" and json_path is None:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    elif fmt == "jsonl":
        for f in payload["findings"]:
            sys.stdout.write(json.dumps({"run_id": payload["run_id"], "status": payload["status"], **f}) + "\n")
    elif fmt in ("human", "both"):
        sys.stdout.write(text + "\n")
        if fmt == "both":
            sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        raise UsageError(f"unknown output format '{fmt}' (use human|json|jsonl|both)")

    status = payload["status"]
    if payload["errors"]:
        first = payload["errors"][0]["code"]
        return {"usage-error": 2, "invalid-input": 3, "limit-exceeded": 4, "policy-blocked": 5,
                "approval-required": 5, "command-not-allowed": 5}.get(first, 1)
    return 0 if status in (OK, WARN, WARN) else 1


# --------------------------------------------------------------------------
# artifact type sniffing (shared by detector + inspectors)
# --------------------------------------------------------------------------
MAGICS: List[Tuple[bytes, str, int]] = [
    (b"\xcf\xfa\xed\xfe", "macho-64le", 0),
    (b"\xce\xfa\xed\xfe", "macho-32le", 0),
    (b"\xfe\xed\xfa\xcf", "macho-64be", 0),
    (b"\xfe\xed\xfa\xce", "macho-32be", 0),
    (b"\xca\xfe\xba\xbe", "macho-fat-or-java", 0),
    (b"\xbe\xba\xfe\xca", "macho-fat-be", 0),
    (b"bplist0", "bplist", 0),
    (b"PK\x03\x04", "zip", 0),
    (b"SQLite format 3\x00", "sqlite", 0),
    (b"<?xml", "xml", 0),
    (b"## ", "bplist-ascii-keyed", 0),   # ASCII plist fragment, low confidence
    (b"\x30\x82", "cms-der", 0),         # CMS/DER envelope (mobileprovision, p12, certificates)
]

# A hint is only trusted when the bytes corroborate it; otherwise the extension lies.
STRICT_HINTS = {"ipa", "zip", "app-bundle", "xcarchive", "xcframework", "framework", "dsym",
                "macho", "macho-dylib", "macho-fat", "sqlite", "mobileprovision", "metallib"}
CORROBORATION = {
    "ipa": {"zip"}, "zip": {"zip"}, "app-bundle": {"zip", "directory"},
    "xcarchive": {"zip", "directory"}, "xcframework": {"zip", "directory"},
    "framework": {"directory"}, "dsym": {"directory", "macho", "macho-dylib"},
    "macho": {"macho-64le", "macho-32le", "macho-64be", "macho-32be", "macho-fat-or-java",
              "macho-fat-be"},
    "macho-dylib": {"macho-64le", "macho-32le", "macho-64be", "macho-32be"},
    "macho-fat": {"macho-fat-or-java", "macho-fat-be"},
    "sqlite": {"sqlite"}, "mobileprovision": {"cms-der", "xml"},
    "metallib": {"macho-64le", "macho-32le"},
    "plist": {"bplist", "xml", "bplist-ascii-keyed"}, "privacy-manifest": {"bplist", "xml"},
}

EXT_HINTS = {
    ".ipa": "ipa", ".app": "app-bundle", ".xcarchive": "xcarchive", ".xcframework": "xcframework",
    ".framework": "framework", ".dSYM": "dsym", ".dSYM/": "dsym", ".xcresult": "xcresult",
    ".plist": "plist", ".mobileprovision": "mobileprovision", ".xcodeproj": "xcodeproj",
    ".xcworkspace": "xcworkspace", ".xcconfig": "xcconfig", ".mobileconfig": "mobileconfig",
    ".crash": "crash-report", ".ips": "crash-report", ".dylib": "macho-dylib", ".xcdatamodel": "coredata-model",
    ".xcdatamodeld": "coredata-model", ".swiftinterface": "swift-interface", ".metallib": "metallib",
    ".xcprivacy": "privacy-manifest", ".ktx": "ktx-texture", ".ktx2": "ktx2-texture", ".usdz": "usdz",
    ".sqlite": "sqlite", ".db": "sqlite", ".log": "log", ".ips.ca": "crash-report",
}


def sniff_kind(path: Path) -> Dict[str, Any]:
    head = b""
    if path.is_file():
        with path.open("rb") as fh:
            head = fh.read(16)
    magic_kind = None
    for magic, kind, off in MAGICS:
        if head[off:off + len(magic)] == magic:
            magic_kind = kind
            break
    ext = path.suffix
    hint = EXT_HINTS.get(ext)
    if path.is_dir():
        hint = hint or ("xcarchive" if (path / "Info.plist").exists() and (path / "Products").exists()
                        else "directory")
        return {"kind": hint, "by": "directory", "magic": None}
    if ext in (".plist",) and magic_kind is None and head.startswith(b"bplist"):
        magic_kind = "bplist"
    # mislabelled ZIP-based artifacts: inspect the first central member name
    zip_members_probe = None
    if magic_kind == "zip":
        zip_members_probe = _peek_zip_names(path)
        if any(n.startswith("Payload/") for n in zip_members_probe):
            hint = "ipa"
        elif any("/Products/" in n and n.endswith(".app/Info.plist") for n in zip_members_probe):
            hint = "xcarchive"
        elif ext == ".ipa":
            pass
    magic_kind_to_kind = {"zip": "zip", "bplist": "plist", "macho-64le": "macho",
                          "macho-32le": "macho", "macho-64be": "macho", "macho-32be": "macho",
                          "macho-fat-or-java": "macho-fat", "macho-fat-be": "macho-fat",
                          "xml": "xml", "sqlite": "sqlite", "bplist-ascii-keyed": "plist",
                          "cms-der": "cms-envelope"}
    content_kind = magic_kind_to_kind.get(magic_kind or "", "unknown")
    if hint and hint in STRICT_HINTS:
        allowed = CORROBORATION.get(hint, set())
        if magic_kind in allowed or (hint in ("app-bundle", "framework", "xcframework")
                                     and path.is_dir()):
            kind, by = hint, "extension+content"
        else:
            kind = "unknown" if content_kind == "unknown" else content_kind
            return {"kind": kind, "by": "content", "magic": magic_kind, "extension": ext,
                    "extension_hint": hint,
                    "note": f"extension '{ext}' claims {hint} but the content is "
                            f"{content_kind}; the extension is misleading",
                    "zip_members": zip_members_probe[:8] if zip_members_probe else None}
    elif hint:
        kind, by = (hint if content_kind in ("unknown",) or hint in CORROBORATION.get(hint, {hint})
                    else content_kind), ("extension+content" if content_kind != "unknown" else "extension")
    else:
        kind, by = content_kind, "content"
    return {"kind": kind, "by": by, "magic": magic_kind, "extension": ext,
            "zip_members": zip_members_probe[:8] if zip_members_probe else None}


def _peek_zip_names(path: Path, limit: int = 40) -> List[str]:
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            return [i.filename for i in zf.infolist()[:limit]]
    except Exception:
        return []


def tool_version_line(binary: str, limits: Limits) -> Tuple[str, str]:
    """Return (status, version text) using approved version flags only."""
    flag = {"carthage": "version", "idevicesyslog": "--help", "iproxy": "--help",
            "xcodebuild": "-version", "swift": "--version", "clang": "--version",
            "codesign": "--version", "lldb": "--version", "pod": "--version",
            "ffmpeg": "-version", "git": "--version", "sqlite3": "--version"}.get(binary, "--version")
    res = run_command([binary, flag], limits)
    if res.status == MISSING:
        return MISSING, ""
    text = (res.stdout or res.stderr).strip().splitlines()
    line = text[0].strip() if text else ""
    if res.status == OK:
        return FOUND, line
    # some Apple tools exit non-zero for --version but still print
    if line and not res.timed_out:
        return FOUND, line
    if res.timed_out:
        return BROKEN, f"timeout after {limits.timeout_s}s"
    return WRONG_VERSION if line else BROKEN, line
