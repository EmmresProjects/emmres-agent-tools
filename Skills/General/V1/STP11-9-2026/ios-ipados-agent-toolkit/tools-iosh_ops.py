"""Operational logic for the iOS/iPadOS Agent Skill Tool.

This module holds everything that is a *policy-bearing operation* rather than a
pure parser: host inventory, execution planning, completion gating, workspaces,
pinning, caching, license and secret scanning, health self-tests, matrices,
Xcode project reading, privacy and signing readiness, crash triage, evidence
packaging and report validation. Parsers live in tools_iosh_apple.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import tools_iosh_apple as apple
import tools_iosh_core as core
from tools_iosh_core import (ACCOUNT_REQUIRED, BLOCKED, BROKEN, DEVICE_REQUIRED, FAIL, FOUND,
                             LICENSE_REQUIRED, MACOS_REQUIRED, MISSING, NETWORK_REQUIRED, OK,
                             SKIPPED, WARN, WARN as _WARN, InputError, LimitError, Limits,
                             PolicyError, UsageError, human_bytes, sha256_file, tool_version_line,
                             which_all)

# ==========================================================================
# host inventory
# ==========================================================================
TOOL_GROUPS: Dict[str, List[str]] = {
    "linux-host": ["git", "zip", "unzip", "python3", "node", "npm"],
    "device-linux": ["idevice_id", "ideviceinfo", "idevicepair", "ideviceinstaller",
                     "idevicesyslog", "idevicediagnostics", "idevicescreenshot", "idevicebackup2",
                     "iproxy", "ifuse", "plistutil", "pymobiledevice3", "go-ios", "ios-deploy"],
    "apple-macos": ["xcodebuild", "xcrun", "simctl", "codesign", "security", "swift", "swiftc",
                    "clang", "clang++", "lldb", "atos", "dsymutil", "otool", "plutil", "swift-demangle",
                    "actool", "ibtool", "metal", "metallib"],
    "dependency": ["pod", "carthage", "fastlane", "bundle", "tuist", "xcodegen", "bazel", "bazelisk"],
    "analysis": ["llvm-objdump", "llvm-readobj", "llvm-nm", "llvm-dwarfdump", "objdump", "readelf",
                 "strings", "file", "jtool2", "ipsw", "r2", "rabin2", "rizin", "ghidra", "sqlite3"],
    "media": ["ffmpeg", "ffprobe", "identify", "convert", "magick", "plutil"],
}


def _read_text(path: Path, limit: int = 4096) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def host_inventory(limits: Limits) -> Dict[str, Any]:
    import platform
    mem_total = 0
    try:
        for line in _read_text(Path("/proc/meminfo")).splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        mem_total = 0
    cpu_count = os.cpu_count() or 1
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    du = shutil.disk_usage(str(Path.cwd()))
    usb = _usb_probe()
    groups: Dict[str, Dict[str, Any]] = {}
    for group, names in TOOL_GROUPS.items():
        entry: Dict[str, Any] = {}
        for name in names:
            path = shutil.which(name)
            entry[name] = {"path": path, "required": group in ("linux-host",)}
        groups[group] = entry
    return {
        "host": {
            "system": platform.system(), "machine": platform.machine(),
            "is_x86_64": platform.machine().lower() in ("x86_64", "amd64"),
            "is_macos": platform.system() == "Darwin",
            "python": platform.python_version(), "kernel": platform.release(),
            "cpu_count": cpu_count, "load_1m": round(load[0], 2),
            "memory_total": mem_total, "memory_total_human": human_bytes(mem_total) if mem_total else "?",
            "uid": os.getuid() if hasattr(os, "getuid") else None,
        },
        "disk": {"total_bytes": du.total, "free_bytes": du.free, "used_ratio": round(du.used / du.total, 3)},
        "usb": usb,
        "groups": groups,
        "fuse": {"device": os.path.exists("/dev/fuse"), "fusermount": shutil.which("fusermount")
                 or shutil.which("fusermount3")},
        "network": _network_probe(),
    }


def _usb_probe() -> Dict[str, Any]:
    files = list(Path("/dev/bus/usb").glob("*/*")) if Path("/dev/bus/usb").exists() else []
    usb_devices = [p for p in Path("/sys/bus/usb/devices").glob("*") if (p / "idVendor").exists()] \
        if Path("/sys/bus/usb/devices").exists() else []
    apple = []
    for d in usb_devices:
        vid = _read_text(d / "idVendor").strip().lower()
        pid = _read_text(d / "idProduct").strip().lower()
        if vid == "05ac":  # Apple Inc.
            apple.append({"vendor": vid, "product": pid})
    return {"node_files": len(files), "sysfs_devices": len(usb_devices),
            "apple_devices": apple, "count": len(usb_devices),
            "note": "USB presence is not pairing or trust; run 'health' with a device attached"}


def _network_probe() -> Dict[str, Any]:
    import socket
    try:
        s = socket.socket()
        s.settimeout(0.2)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        loopback = True
    except OSError:
        loopback, port = False, None
    return {"loopback_bind": loopback, "ephemeral_port": port,
            "note": "outbound reachability of Apple services is not probed by default"}


# ==========================================================================
# planning
# ==========================================================================
def _gate_map(policy: Dict[str, Any]) -> Dict[str, str]:
    """Map approval gate ids to their human reason from the active policy."""
    return {rule["id"]: rule["reason"]
            for rule in policy["authorization"]["approval_required"]}


def _step_for(cap: Dict[str, Any], have_macos: bool, have_device: bool, have_account: bool,
              network: bool, reason_by_gate: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    req = cap.get("requires", [])
    impl = cap["implementation"]
    blockers: List[str] = []
    if "macos-required" in req and not have_macos:
        blockers.append("licence macOS/Xcode worker not confirmed (--have-macos)")
    if "physical-device-required" in req and not have_device:
        blockers.append("owned physical device not confirmed (--have-device)")
    if "apple-account-required" in req and not have_account:
        blockers.append("authorized Apple account/key not confirmed (--have-account)")
    if "network-required" in req and not network:
        blockers.append("network access not confirmed (--network)")
    if "license-required" in req:
        blockers.append("commercial licence required on the executing host")

    if impl == "native" and not blockers:
        kind, runner = "native", "tools-iosh.py " + cap["command"]
    elif impl == "native":
        kind, runner = "native-blocked", "tools-iosh.py " + cap["command"]
    elif impl == "external":
        kind, runner = "external-tool", cap["command"]
    elif impl == "plan-only":
        kind, runner = "routed", "execute on the macOS/device/account host: " + cap["command"]
    else:
        kind, runner = "reference", cap["command"]
    return {
        "step_id": cap["id"], "capability": cap["name"], "category": cap.get("category_number"),
        "kind": kind, "runner": runner, "requires": req,
        "approval": [{"gate": gate,
                      "reason": (reason_by_gate or {}).get(gate, gate)}
                     for gate in cap.get("approval_gates", [])],
        "blockers": blockers,
    }


def build_plan(catalog: Dict[str, Any], selected: Sequence[Dict[str, Any]], limits: Limits,
               policy: Dict[str, Any], *, target: str = "auto", have_macos: bool = False,
               have_device: bool = False, have_account: bool = False, network: bool = False,
               approvals: Sequence[str] = ()) -> Dict[str, Any]:
    reason_map = _gate_map(policy)
    steps = [_step_for(c, have_macos, have_device, have_account, network, reason_map)
             for c in selected]
    warnings: List[str] = []
    gates: Dict[str, Dict[str, str]] = {}
    for s in steps:
        for gate in s["approval"]:
            gates.setdefault(gate["gate"], {"id": gate["gate"], "reason": gate["reason"],
                                            "step": s["step_id"]})
    executed_here = [s for s in steps if s["kind"] == "native"]
    routed = [s for s in steps if s["kind"] in ("routed", "native-blocked", "external-tool")]
    if target == "linux":
        steps = [s for s in steps if s["kind"] != "routed"]
        warnings.append("target=linux: macOS-only steps were dropped from the plan")
    if any(s["kind"] == "routed" for s in steps):
        warnings.append("routed steps must be executed on a licensed macOS/device host; this toolkit "
                        "never emulates Xcode, Simulator, codesign or device trust")
    if any(s["kind"] == "native-blocked" for s in steps):
        warnings.append("some native steps are blocked by host/account/device prerequisites listed "
                        "in their 'blockers'")
    plan = {
        "schema_version": core.SCHEMA_VERSION,
        "plan_id": f"plan-{int(time.time())}-{len(steps)}",
        "generated_at": core.now_iso(),
        "catalog": {"title": catalog.get("catalog_title"),
                    "capability_count": catalog.get("capability_count")},
        "target": target,
        "host_flags": {"have_macos": have_macos, "have_device": have_device,
                       "have_account": have_account, "network": network},
        "limits": core.asdict(limits),
        "summary": {"steps": len(steps),
                    "native_steps": len(executed_here),
                    "routed_steps": len(routed),
                    "reference_steps": sum(1 for s in steps if s["kind"] == "reference"),
                    "approval_gates": len(gates)},
        "approval_required": [{"id": g["id"], "reason": g["reason"], "step": g["step"], **({"granted": True} if g["id"] in approvals else {})}
                              for g in gates.values()],
        "warnings": warnings,
        "steps": steps,
    }
    return plan


def _gate_id(reason: str) -> str:
    mapping = {
        "destructive or disruptive device action": "destructive-device",
        "signing with protected identities": "signing",
        "publishing or distributing an artifact": "publishing",
        "extracting user data": "data-extraction",
        "instrumenting a running application": "dynamic-analysis",
        "intercepting network traffic": "network-capture",
        "financial commitment": "spend",
    }
    return mapping.get(reason, re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-"))


SELF_SUFFICIENT_COMMANDS = {"probe", "health", "capabilities"}
EXECUTABLE_WITH_FLAGS = {"health": {"--no-fixtures"}}


def self_contained_only(command: str) -> bool:
    """True when the command is meaningful with no additional arguments."""
    return command in SELF_SUFFICIENT_COMMANDS


def execute_plan(plan: Dict[str, Any], limits: Limits, approvals: Sequence[str] = ()) -> Dict[str, Any]:
    """Execute only self-sufficient, non-destructive native steps.

    Steps that need artifact arguments are reported as SKIPPED with the exact
    command to run, because inventing paths would violate the tool's input contract.
    """
    results = []
    for step in plan["steps"]:
        if step["kind"] != "native":
            results.append({"step_id": step["step_id"], "capability": step["capability"],
                            "status": SKIPPED,
                            "detail": "routed step: execute on the macOS/device host"})
            continue
        cmd = step["runner"].split()
        if cmd and cmd[0].endswith(".py"):  # 'tools-iosh.py probe' -> ['probe']
            cmd = cmd[1:]
        if not cmd:
            results.append({"step_id": step["step_id"], "capability": step["capability"],
                            "status": SKIPPED, "detail": "command could not be derived"})
            continue
        # only bare, self-sufficient commands are safe to run without artifact arguments
        if not self_contained_only(cmd[0]) or len(cmd) > 1:
            results.append({"step_id": step["step_id"], "capability": step["capability"],
                            "status": SKIPPED,
                            "detail": f"needs artifact arguments: run \'{step['runner']}\' yourself"})
            continue
        argv = [sys.executable, str(Path(__file__).resolve().parent / "tools-iosh.py"), *cmd[1:]]
        proc = subprocess.run(argv, capture_output=True, timeout=limits.timeout_s, check=False)
        ok = proc.returncode == 0
        results.append({
            "step_id": step["step_id"], "capability": step["capability"],
            "status": OK if ok else ("BLOCKED" if proc.returncode == 5 else FAIL),
            "exit_code": proc.returncode,
            "detail": (proc.stdout or proc.stderr)[-600:].decode("utf-8", "replace").strip(),
        })
    return {"results": results,
            "executed": sum(1 for r in results if r["status"] != SKIPPED),
            "skipped": sum(1 for r in results if r["status"] == SKIPPED)}


import sys  # noqa: E402  (used by execute_plan)


# ==========================================================================
# completion gate
# ==========================================================================
PARSERS = {
    "ipa": lambda p, l: apple.inspect_ipa(p, l) if zipfile.is_zipfile(p) else (_ for _ in ()).throw(
        InputError("not a ZIP-based IPA", code="gate-ipa-invalid")),
    "zip": lambda p, l: apple.analyze_zip(p, l),
    "plist": lambda p, l: apple.load_plist_file(p, l),
    "profile": lambda p, l: apple.parse_mobileprovision(p, l),
    "crash": lambda p, l: apple.parse_crash_report(p, l),
    "macho": lambda p, l: apple.parse_macho(p.read_bytes(), path=p.as_posix()),
    "dsym": lambda p, l: {"uuids": apple.dsym_uuids(p, l)},
    "json": lambda p, l: json.loads(p.read_text(encoding="utf-8")),
    "report": lambda p, l: {"parsed": json.loads(p.read_text(encoding="utf-8")),
                           "schema": json.loads(p.read_text(encoding="utf-8")).get("schema_version")},
    "log": lambda p, l: {"lines": len(p.read_text(encoding="utf-8", errors="replace").splitlines())},
    "xcode-project": lambda p, l: _gate_project(p),
    "none": lambda p, l: {"ok": True},
}

SUFFIX_KIND = {".ipa": "ipa", ".zip": "zip", ".plist": "plist", ".mobileprovision": "profile",
               ".crash": "crash", ".ips": "crash", ".dylib": "macho", ".dSYM": "dsym",
               ".xcresult": "report", ".xcodeproj": "xcode-project", ".xcworkspace": "xcode-project"}


def _gate_project(p: Path) -> Dict[str, Any]:
    proj = p / "project.pbxproj"
    if not proj.exists():
        raise InputError("project.pbxproj missing", code="gate-project-invalid")
    return {"pbxproj_bytes": proj.stat().st_size}


def completion_gate(claim: str, evidence: Sequence[str], required: Sequence[str],
                    limits: Limits) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    lowered = claim.lower()
    # claims imply required evidence
    implied: List[str] = []
    for word, kind in (("install", "ipa"), ("build", "report"), ("sign", "profile"),
                       ("crash", "crash"), ("symbolicat", "dsym"),
                       ("tests passed", "report"), ("test suite", "report"),
                       ("archive", "report"), ("ship", "report"), ("distribut", "report"),
                       ("upload", "report")):
        if word in lowered and kind not in implied:
            implied.append(kind)
    needed = list(dict.fromkeys([*required, *implied]))
    if needed == ["none"]:
        needed = []
    checks.append({"name": "claim-stated", "ok": bool(claim.strip()),
                   "detail": claim.strip()[:200] or "no claim was provided"})
    for kind in needed:
        matches = [Path(e) for e in evidence if (SUFFIX_KIND.get(Path(e).suffix) == kind
                                                 or Path(e).suffix.lstrip(".") == kind
                                                 or kind in Path(e).name.lower())]
        if not matches:
            checks.append({"name": f"evidence:{kind}", "ok": False,
                           "detail": f"no {kind} evidence was supplied"})
            continue
        for m in matches:
            try:
                if not m.exists():
                    checks.append({"name": f"evidence:{kind}", "ok": False,
                                   "detail": f"{m.as_posix()} does not exist"})
                    continue
                parsed = PARSERS[kind](m, limits)
                checks.append({"name": f"opened:{m.name}", "ok": True,
                               "detail": f"{kind} parsed successfully "
                                         f"({len(json.dumps(parsed, default=str))} bytes of structured data)"})
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": f"opened:{m.name}", "ok": False,
                               "detail": f"{kind} could not be parsed: {getattr(exc, 'message', exc)}"})
    unparsed = [Path(e) for e in evidence if not any(c["name"].endswith(Path(e).name) for c in checks)]
    for u in unparsed:
        try:
            if u.exists():
                kind = apple.sniff_kind(u)
                parser_kind = {"ipa": "ipa", "zip": "zip", "plist": "plist",
                               "mobileprovision": "profile", "crash-report": "crash",
                               "macho": "macho", "macho-dylib": "macho", "macho-fat": "macho",
                               "dsym": "dsym", "xcresult": "report",
                               "xcworkspace": "xcode-project", "directory": "none"}.get(kind["kind"])
                if parser_kind and parser_kind != "none" and u.suffix in (".xcresult",):
                    parser_kind = None
                if parser_kind and parser_kind != "none":
                    try:
                        parsed = PARSERS[parser_kind](u, limits)
                        checks.append({"name": f"opened:{u.name}", "ok": True,
                                       "detail": f"sniffed {kind['kind']} and parsed it as "
                                                 f"{parser_kind} "
                                                 f"({len(json.dumps(parsed, default=str))} bytes)"})
                        continue
                    except Exception as exc:  # noqa: BLE001
                        checks.append({"name": f"opened:{u.name}", "ok": False,
                                       "detail": f"sniffed {kind['kind']} but could not parse: "
                                                 f"{getattr(exc, 'message', exc)}"})
                        continue
                with u.open("rb") as fh:
                    head = fh.read(8)
                checks.append({"name": f"opened:{u.name}", "ok": len(head) > 0,
                               "detail": f"read {len(head)} header bytes; kind {kind['kind']}"})
            else:
                checks.append({"name": f"opened:{u.name}", "ok": False, "detail": "missing"})
        except OSError as exc:
            checks.append({"name": f"opened:{u.name}", "ok": False, "detail": str(exc)})
    verdict = "PASS" if checks and all(c["ok"] for c in checks) else "BLOCKED"
    return {"claim": claim, "verdict": verdict, "checks": checks,
            "required_kinds": needed,
            "rule": "a capability is complete only when the artifact was opened, parsed and the "
                    "observed result matches the claim"}


# ==========================================================================
# workspace
# ==========================================================================
ROLE_BY_KIND = {"ipa": "artifact", "app-bundle": "artifact", "zip": "artifact",
                "plist": "metadata", "mobileprovision": "metadata", "macho": "binary",
                "crash-report": "log", "log": "log", "dSYM": "symbols", "dsym": "symbols",
                "xcodeproj": "project", "xcworkspace": "project", "directory": "input"}


def create_workspace(name: str, inputs: Sequence[str], limits: Limits, *, root: str = ".",
                     clean: bool = False) -> Dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", name):
        raise UsageError("workspace --name must be lowercase letters, digits, dots or hyphens "
                         "(max 64 chars)", code="bad-workspace-name")
    ws_root = Path(root).resolve() / f"ws-{name}"
    notes: List[str] = []
    if ws_root.exists():
        if clean:
            raise UsageError(f"workspace {ws_root.as_posix()} already exists and --clean was requested",
                             code="workspace-exists",
                             remediation="choose a new --name or remove the stale workspace yourself")
        notes.append(f"reusing existing workspace {ws_root.as_posix()}; originals are never modified")
    for sub in ("inputs", "work", "output", "logs"):
        (ws_root / sub).mkdir(parents=True, exist_ok=True)
    copied: List[Dict[str, Any]] = []
    total = 0
    for item in inputs:
        src = Path(item)
        if not src.exists():
            notes.append(f"input not found and was skipped: {item}")
            continue
        if src.stat().st_size > limits.max_file_bytes and src.is_file():
            raise LimitError(f"input {src.name} exceeds --max-file-bytes", code="file-too-large")
        kind = apple.sniff_kind(src)
        role = ROLE_BY_KIND.get(kind["kind"], "input")
        dest_dir = ws_root / "inputs" / role
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}.copy{src.suffix}" if src.is_file() else dest
        if src.is_dir():
            guard = sum(f.stat().st_size for f in src.rglob("*") if f.is_file()) or 0
            if guard > limits.max_total_bytes:
                raise LimitError(f"{src.name} is {human_bytes(guard)} which exceeds --max-total-bytes",
                                 code="file-too-large")
            shutil.copytree(src, dest, symlinks=False)
            digest = _tree_sha256(dest)
            size = guard
        else:
            shutil.copy2(src, dest)
            digest = sha256_file(dest, limits.max_file_bytes)
            size = dest.stat().st_size
        total += size
        copied.append({"role": role, "name": src.name, "kind": kind["kind"],
                       "source": src.as_posix(), "destination": dest.as_posix(),
                       "bytes": size, "sha256": digest,
                       "original_sha256": digest if not src.is_file() else sha256_file(src, limits.max_file_bytes)})
    manifest = ws_root / "work" / "workspace-manifest.json"
    core.write_json(manifest, {
        "schema_version": core.SCHEMA_VERSION, "workspace": ws_root.as_posix(),
        "created_at": core.now_iso(), "limits": core.asdict(limits),
        "inputs": copied, "notes": notes,
        "preservation_rule": "originals stay at their source paths; only copies are analysed",
    })
    return {"root": ws_root.as_posix(), "inputs": copied, "notes": notes,
            "originals_preserved": True, "manifest": manifest.as_posix(),
            "total_bytes": total}


def _tree_sha256(root: Path) -> str:
    h = __import__("hashlib").sha256()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


# ==========================================================================
# cross-host paths
# ==========================================================================
def normalise_paths(paths: Sequence[str], scheme: str) -> Dict[str, Any]:
    mapping: Dict[str, str] = {}
    conflicts: List[str] = []
    for raw in paths:
        p = raw.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", p) or p.startswith("//"):
            conflicts.append(f"{raw}: absolute drive/UNC path has no portable equivalent")
            p = p.lstrip("/")
            p = re.sub(r"^[A-Za-z]:", "", p)
        elif p.startswith("/"):
            marker = p.find("/repo/")
            if marker >= 0:
                p = p[marker + len("/repo/"):]
            else:
                p = p.lstrip("/")
                p = "/".join(p.split("/")[1:]) if p.count("/") > 1 else p
        p = re.sub(r"/+", "/", p).strip("/")
        if ".." in p.split("/"):
            conflicts.append(f"{raw}: contains parent-directory traversal")
        p = "/".join(seg for seg in p.split("/") if seg not in ("", ".", ".."))
        if not p:
            conflicts.append(f"{raw}: normalises to an empty path")
            continue
        if scheme == "macos-relative":
            mapping[raw] = f"./{p}"
        elif scheme == "windows":
            mapping[raw] = p.replace("/", "\\")
        else:
            mapping[raw] = f"{p}"
    return {"map": mapping, "target_scheme": scheme, "conflicts": conflicts,
            "rule": "all references stay relative to the future skill root; no absolute or traversal paths"}


# ==========================================================================
# pins
# ==========================================================================
def record_pins(tools: Sequence[str], limits: Limits, pins_file: Path) -> Dict[str, Any]:
    records = []
    for name in tools:
        status, version = tool_version_line(name, limits)
        path = shutil.which(name)
        record = {"name": name, "status": status, "version": version, "path": path,
                  "recorded_at": core.now_iso()}
        if path:
            try:
                st = Path(path).stat()
                record.update({"size": st.st_size, "mtime": int(st.st_mtime),
                               "sha256": sha256_file(Path(path), 64 << 20)
                               if st.st_size < 64 << 20 else None})
            except OSError as exc:
                record["error"] = str(exc)
        records.append(record)
    payload = {"schema_version": core.SCHEMA_VERSION, "generated_at": core.now_iso(),
               "host": {"system": os.uname().sysname if hasattr(os, "uname") else "?",
                        "machine": os.uname().machine if hasattr(os, "uname") else "?"},
               "tools": records, "file": pins_file.as_posix()}
    core.write_json(pins_file, payload)
    return payload


def compare_pins(pins_file: Path, limits: Limits) -> Dict[str, Any]:
    if not pins_file.exists():
        raise InputError(f"no pin baseline at {pins_file.as_posix()}", code="pins-missing",
                         remediation="run 'pins record --tool <name>' first")
    baseline = json.loads(pins_file.read_text(encoding="utf-8"))
    items = []
    drift = unchanged = missing = 0
    for rec in baseline["tools"]:
        status, version = tool_version_line(rec["name"], limits)
        current_path = shutil.which(rec["name"])
        if status == MISSING:
            verdict = "missing"
            missing += 1
        elif version != rec["version"]:
            verdict = "changed"
            drift += 1
        else:
            verdict = "unchanged"
            unchanged += 1
        items.append({"name": rec["name"], "pinned": rec["version"], "current": version,
                      "pinned_status": rec["status"], "current_status": status,
                      "path": current_path, "verdict": verdict})
    return {"baseline_file": pins_file.as_posix(), "baseline_generated": baseline["generated_at"],
            "items": items, "drift": drift, "unchanged": unchanged, "missing": missing}


# ==========================================================================
# offline cache
# ==========================================================================
APPLE_MARKERS = [
    (re.compile(rb"Xcode\.app/Contents", re.I), "path inside Xcode.app"),
    (re.compile(rb"iPhoneOS\.platform", re.I), "iOS platform SDK"),
    (re.compile(rb"iPhoneSimulator\.platform", re.I), "Simulator platform SDK"),
    (re.compile(rb"Apple Inc\. - Xcode license", re.I), "Xcode licence text"),
    (re.compile(rb"Contents/Developer/Platforms", re.I), "Xcode Developer platform tree"),
    (re.compile(rb"com\.apple\.dt\.SimulatorRuntime", re.I), "Simulator runtime bundle"),
]
COMMERCIAL_MARKERS = [
    "hopper disassembler", "ida professional", "jtool2", "pvrtextool", "macstadium",
    "browserstack", "saucelabs", "sauce labs", "unity technologies", "epic games",
]


def cache_manage(action: str, cache_dir: Optional[str], add: Sequence[str], name: Optional[str],
                 limits: Limits, check: Sequence[str], policy: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(cache_dir) if cache_dir else core.state_dir(None) / "cache"
    index_file = root / "index.json"
    root.mkdir(parents=True, exist_ok=True)
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else \
        {"schema_version": core.SCHEMA_VERSION, "entries": []}
    notes: List[str] = []
    clipped: List[str] = []
    if action in ("init",):
        core.write_json(index_file, index)
        notes.append(f"cache initialised at {root.as_posix()}")
    if action in ("add", "init") and add:
        for item in add:
            src = Path(item)
            if not src.exists():
                notes.append(f"not found, skipped: {item}")
                continue
            if src.is_file() and src.stat().st_size > limits.max_file_bytes:
                raise LimitError(f"{src.name} exceeds --max-file-bytes for caching", code="file-too-large")
            if src.is_dir():
                markers = _scan_markers(src, APPLE_MARKERS, limits.max_entries)
                if markers:
                    clipped.append(f"{src.as_posix()}: refused ({markers[0][1]}) -- Apple-licensed "
                                   "components must never be cached or redistributed")
                    continue
            elif src.is_file():
                with src.open("rb") as fh:
                    head = fh.read(4 << 20)
                for pat, label in APPLE_MARKERS:
                    if pat.search(head):
                        clipped.append(f"{src.as_posix()}: refused ({label})")
                        break
                else:
                    pass
                if clipped and clipped[-1].startswith(src.as_posix()):
                    continue
            if not name and len(add) > 1:
                raise UsageError("--name is required when caching more than one item at once")
            dest = root / (name or src.name)
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
                digest = _tree_sha256(dest)
                size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
            else:
                shutil.copy2(src, dest)
                digest = sha256_file(dest, limits.max_file_bytes)
                size = dest.stat().st_size
            entry = {"name": dest.name, "path": dest.as_posix(), "sha256": digest, "bytes": size,
                     "added_at": core.now_iso(), "source_name": src.name}
            index["entries"] = [e for e in index["entries"] if e["name"] != dest.name] + [entry]
            notes.append(f"cached {src.name} -> {dest.as_posix()} ({human_bytes(size)})")
    if action in ("list", "init"):
        for e in index["entries"]:
            notes.append(f"{e['name']}: {human_bytes(e['bytes'])} sha256={e['sha256'][:12]}")
    if action in ("verify", "check"):
        names = check or [e["name"] for e in index["entries"]]
        for e in index["entries"]:
            if e["name"] not in names:
                continue
            p = Path(e["path"])
            if not p.exists():
                notes.append(f"MISSING: {e['name']} at {e['path']}")
                continue
            current = sha256_file(p, limits.max_file_bytes) if p.is_file() else _tree_sha256(p)
            verdict = "verified" if current == e["sha256"] else "CHANGED"
            notes.append(f"{e['name']}: {verdict}")
        if action == "check":
            markers = _scan_markers(root, APPLE_MARKERS, limits.max_entries)
            for m in markers:
                clipped.append(f"{m[0]}: {m[1]}")
            if not markers:
                notes.append("cache contains no Apple-licensed payload markers")
    core.write_json(index_file, index)
    return {"cache_dir": root.as_posix(), "index_file": index_file.as_posix(),
            "entries": index["entries"], "notes": notes, "clipped": clipped,
            "summary": {"entries": len(index["entries"]),
                        "bytes": sum(e["bytes"] for e in index["entries"]),
                        "refused": len(clipped)}}


def _scan_markers(root: Path, patterns, limit: int) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    files = [root] if root.is_file() else [f for f in root.rglob("*") if f.is_file()]
    for f in files[:limit]:
        try:
            if f.is_file() and f.stat().st_size <= 8 << 20:
                blob = f.read_bytes()
            else:
                continue
        except OSError:
            continue
        for pat, label in patterns:
            if pat.search(blob):
                hits.append((f.as_posix(), label))
                break
    return hits


def license_scan(paths: Sequence[str], limits: Limits, policy: Dict[str, Any]) -> Dict[str, Any]:
    apple_hits: List[Dict[str, str]] = []
    commercial_hits: List[Dict[str, str]] = []
    files = 0
    for p in paths:
        root = Path(p)
        if not root.exists():
            raise InputError(f"path not found: {p}", code="input-missing")
        candidates = [root] if root.is_file() else [f for f in root.rglob("*") if f.is_file()]
        for f in candidates[: limits.max_entries]:
            files += 1
            try:
                if f.stat().st_size > 8 << 20:
                    continue
                blob = f.read_bytes()
            except OSError:
                continue
            low = blob[: 2 << 20].lower()
            for pat, label in APPLE_MARKERS:
                if pat.search(low):
                    apple_hits.append({"path": f.as_posix(), "marker": label})
                    break
            text = low.decode("utf-8", "replace")
            for marker in COMMERCIAL_MARKERS:
                if marker in text:
                    commercial_hits.append({"path": f.as_posix(), "marker": marker})
                    break
            if f.name in ("Xcode.app", "Xcode-beta.app") or (f.is_dir() and f.name.endswith(".platform")):
                apple_hits.append({"path": f.as_posix(), "marker": "Apple bundle name"})
    return {"files_scanned": files, "apple": apple_hits[:200], "commercial": commercial_hits[:200],
            "rule": policy["licenses"]["rule"]}


# ==========================================================================
# secret scan
# ==========================================================================
SECRET_PATTERNS: List[Tuple[str, re.Pattern, str, str]] = [
    ("apple-api-key", re.compile(r"-----BEGIN PRIVATE KEY-----"), "high",
     "remove the private key from the repository and rotate it in App Store Connect"),
    ("p8-authkey", re.compile(r"[A-Z0-9]{10}\.p8"), "high",
     "App Store Connect .p8 keys must be supplied through a secret store, never committed"),
    ("private-key-block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "high",
     "strip the key material and rotate the credential"),
    ("certificate-p12", re.compile(r"\.p12\b"), "medium",
     "signing identities must be imported from an approved keychain, not committed"),
    ("provisioning-udid-list", re.compile(r"ProvisionedDevices"), "low",
     "device lists are personal data; keep profiles out of shared trees"),
    ("generic-api-key", re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)"
                                    r"\b\s*[:=]\s*['\"][^'\"]{12,}"), "high",
     "move the secret to a secret store and rotate it"),
    ("generic-api-key-unquoted", re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|"
                                            r"password|client[_-]?secret)\b\s*[:=]\s*"
                                            r"(?=[A-Za-z0-9_\-]*[0-9])[A-Za-z0-9_\-]{18,}"),
     "high", "move the secret to a secret store and rotate it"),
    ("aws-credential", re.compile(r"AKIA[0-9A-Z]{16}"), "high", "rotate the AWS key immediately"),
    ("bearer-token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}"), "medium",
     "tokens in source must be short-lived and injected at runtime"),
    ("basic-auth-url", re.compile(r"://[^/\s:]+:[^/\s@]+@"), "medium",
     "credentials in URLs leak into logs"),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "low",
     "verify the token is not a long-lived production credential"),
]

SCAN_EXTENSIONS = {".swift", ".m", ".mm", ".h", ".c", ".cpp", ".plist", ".json", ".yml", ".yaml",
                   ".sh", ".py", ".rb", ".pbxproj", ".xcconfig", ".entitlements", ".md", ".txt",
                   ".env", ".xml", ".cfg", ".ini", ".gradle", ".properties", ".strings"}


def secrets_scan(paths: Sequence[str], limits: Limits) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    files = 0
    skipped_binary = 0
    for p in paths:
        root = Path(p)
        if not root.exists():
            raise InputError(f"path not found: {p}", code="input-missing")
        candidates = [root] if root.is_file() else [f for f in root.rglob("*") if f.is_file()]
        for f in candidates[: limits.max_entries]:
            if f.is_file() and f.suffix.lower() not in SCAN_EXTENSIONS and f.suffix:
                skipped_binary += 1
                continue
            try:
                if f.stat().st_size > limits.max_file_bytes:
                    continue
                text = f.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                skipped_binary += 1
                continue
            files += 1
            for lineno, line in enumerate(text.splitlines(), 1):
                for kind, pat, severity, fix in SECRET_PATTERNS:
                    m = pat.search(line)
                    if not m:
                        continue
                    preview = m.group(0)
                    preview = preview[:6] + "*" * max(0, len(preview) - 6)
                    findings.append({"kind": kind, "severity": severity, "path": f.as_posix(),
                                     "line": lineno, "preview": preview, "remediation": fix})
    return {"files_scanned": files, "skipped_binary": skipped_binary,
            "findings": findings[: limits.max_findings],
            "rule": "reports only shape-preserving previews; matched secrets are never echoed"}


# ==========================================================================
# health / self-tests
# ==========================================================================
def health_report(limits: Limits, policy: Dict[str, Any], *, macos_probe: Optional[str] = None,
                  tools: Sequence[str] = (), include_fixtures: bool = True,
                  fixtures_dir: Optional[Path] = None) -> Dict[str, Any]:
    required = list(dict.fromkeys(tools)) or policy["self_test"]["required_tools"]
    host_status = policy["self_test"]["host_status_when_absent"]
    rows: List[Dict[str, Any]] = []
    found = 0
    for name in required:
        status, version = tool_version_line(name, limits)
        if status == MISSING:
            status = host_status.get(name, MISSING)
            remediation = {
                DEVICE_REQUIRED: "install the pinned toolchain; a device is still required to prove it",
                MACOS_REQUIRED: "run this on a licensed macOS worker with Xcode selected",
                LICENSE_REQUIRED: "install the commercial tool and its licence on the executing host",
            }.get(status, "install the tool or add it to the pinned toolchain image")
        else:
            found += 1
            remediation = ""
        rows.append({"name": name, "status": status, "version": version,
                     "path": shutil.which(name), "remediation": remediation})
    fixtures: List[Dict[str, Any]] = []
    if include_fixtures and fixtures_dir:
        fixtures = _run_fixtures(fixtures_dir, limits)
    macos = json.loads(Path(macos_probe).read_text(encoding="utf-8")) if macos_probe else None
    if macos_probe and macos is not None:
        macos = _summarise_macos_probe(macos)
    return {
        "schema_version": core.SCHEMA_VERSION,
        "generated_at": core.now_iso(),
        "tools": rows,
        "fixtures": fixtures,
        "macos_probe": macos,
        "summary": {
            "checked": len(rows), "found": found,
            "required_elsewhere": sum(1 for r in rows if r["status"] in
                                      (DEVICE_REQUIRED, MACOS_REQUIRED, LICENSE_REQUIRED)),
            "broken": sum(1 for r in rows if r["status"] == BROKEN),
            "fixtures_run": len(fixtures),
            "fixtures_passed": sum(1 for f in fixtures if f["ok"]),
            "fixtures_failed": sum(1 for f in fixtures if not f["ok"]),
            "macos_worker": (macos or {}).get("verdict", "not supplied"),
        },
        "rule": policy["self_test"]["required_tools"] and
                "a command that exists is not a proven capability; fixtures prove the main function",
    }


def _summarise_macos_probe(probe: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either a raw worker capture or a report produced by 'health --format json'."""
    checks: Dict[str, Any] = {}
    body = probe.get("data") if isinstance(probe.get("data"), dict) else probe
    tools = {t.get("name"): t for t in (body.get("tools") or []) if isinstance(t, dict)}
    for key in ("xcodebuild", "xcrun", "codesign", "security", "swift"):
        t = tools.get(key)
        checks[key] = {"ok": bool(t and t.get("status") == FOUND),
                       "detail": (t or {}).get("version", "not reported")[:120]}
    xcode = body.get("xcode") or probe.get("xcode") or {}
    checks["xcode-select"] = {"ok": bool(xcode.get("developer_dir")),
                              "detail": xcode.get("developer_dir", "not reported")}
    sims = body.get("simulators") or probe.get("simulators") or {}
    checks["simctl-runtimes"] = {"ok": bool(sims.get("runtimes")),
                                 "detail": f"{len(sims.get('runtimes') or [])} runtime(s) listed"}
    verdict = "OK" if all(c["ok"] for c in checks.values()) else "INCOMPLETE"
    return {"verdict": verdict, "checks": checks,
            "raw_summary": body.get("summary") if isinstance(body.get("summary"), dict) else {}}


def _run_fixtures(fixtures_dir: Path, limits: Limits) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not fixtures_dir.exists():
        return [{"name": "fixtures-dir", "ok": False, "detail": f"{fixtures_dir} missing"}]

    plist_path = fixtures_dir / "asset-fixture-info.plist"
    if plist_path.exists():
        try:
            xml = apple.load_plist_file(plist_path, limits)
            binary = apple.convert_plist(plist_path.read_bytes(), "binary")
            back = apple.parse_plist_bytes(binary)["value"]
            ok = back == apple.parse_plist_bytes(plist_path.read_bytes())["value"]
            results.append({"name": "plist-roundtrip", "ok": ok,
                            "detail": f"xml->binary->dict preserved all {len(back)} keys"
                                      if ok else "round-trip changed values"})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": "plist-roundtrip", "ok": False, "detail": str(exc)})

    ipa_path = fixtures_dir / "asset-fixture-app.ipa"
    if ipa_path.exists():
        try:
            info = apple.inspect_ipa(ipa_path, limits)
            ok = (info["bundle"]["bundle_id"] == "com.example.fixture"
                  and "arm64" in info["architectures"]
                  and info["container"]["zip"]["healthy"])
            results.append({"name": "ipa-inspect", "ok": ok,
                            "detail": f"bundle={info['bundle']['bundle_id']} "
                                      f"arch={info['architectures']} "
                                      f"zip_healthy={info['container']['zip']['healthy']}"})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": "ipa-inspect", "ok": False, "detail": str(exc)})

    macho_path = fixtures_dir / "asset-fixture-arm64.dylib"
    if macho_path.exists():
        try:
            info = apple.parse_macho(macho_path.read_bytes(), path=macho_path.as_posix())
            sl = info["slices"][0]
            ok = (sl["arch"] == "arm64" and str(sl["min_os"]).startswith("14.0")
                  and sl["uuid"] is not None and sl["platform"] == "ios")
            results.append({"name": "macho-parse", "ok": ok,
                            "detail": f"arch={sl['arch']} min_os={sl['min_os']} uuid={sl['uuid']}"})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": "macho-parse", "ok": False, "detail": str(exc)})

    profile_path = fixtures_dir / "asset-fixture.mobileprovision"
    if profile_path.exists():
        try:
            prof = apple.parse_mobileprovision(profile_path, limits)
            ok = prof["summary"]["uuid"] == "11111111-2222-3333-4444-555555555555"
            results.append({"name": "profile-decode", "ok": ok,
                            "detail": f"uuid={prof['summary']['uuid']} "
                                      f"method={prof['summary']['distribution_method']}"})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": "profile-decode", "ok": False, "detail": str(exc)})

    crash_path = fixtures_dir / "asset-fixture-crash.ips"
    if crash_path.exists():
        try:
            crash = apple.parse_crash_report(crash_path, limits)
            triage = triage_crash(crash)
            ok = crash["format"] == "ips" and len(crash["frames"]) > 0 and triage["class"] != "unknown"
            results.append({"name": "crash-triage", "ok": ok,
                            "detail": f"format={crash['format']} class={triage['class']} "
                                      f"frames={len(crash['frames'])}"})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": "crash-triage", "ok": False, "detail": str(exc)})

    proj_path = fixtures_dir / "asset-fixture-project.pbxproj"
    if proj_path.exists():
        try:
            info = inspect_project(proj_path, limits)
            ok = info["summary"]["targets"] == 1
            results.append({"name": "project-parse", "ok": ok,
                            "detail": f"targets={info['summary']['targets']} "
                                      f"sources={info['summary']['source_files']}"})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": "project-parse", "ok": False, "detail": str(exc)})

    if not results:
        return [{"name": "fixtures-dir", "ok": False,
                 "detail": f"no asset-fixture-* files found in {fixtures_dir}"}]
    for name in ("sqlite3", "plistutil", "ffmpeg"):
        present = shutil.which(name) is not None
        results.append({"name": f"external-{name}", "ok": True,
                        "detail": (f"{name} present" if present else
                                   f"{name} absent: capabilities that shell out to it report "
                                   f"EXTERNAL TOOL MISSING and stay configurable")})
    return results


# ==========================================================================
# test matrix
# ==========================================================================
def build_matrix(limits: Limits, *, template: Path, retries: int = 0, clean_state: bool = True,
                 runtimes: Optional[str] = None, include_device: bool = False) -> Dict[str, Any]:
    data = json.loads(template.read_text(encoding="utf-8"))
    if runtimes:
        parsed = [r.strip() for r in runtimes.split(",") if r.strip()]
        if not parsed:
            raise UsageError("--runtimes must contain at least one runtime hint", code="bad-runtimes")
        for lane, hint in zip(data["lanes"], parsed + [parsed[-1]] * len(data["lanes"])):
            lane["runtime_hint"] = hint
    lanes = []
    for lane in data["lanes"]:
        if lane["environment"] == "device" and not include_device:
            continue
        lanes.append({**lane, "id": f"lane-{len(lanes)+1:02d}",
                      "clean_state": clean_state,
                      "status_hint": MACOS_REQUIRED if lane["environment"] == "simulator"
                      else DEVICE_REQUIRED})
    return {"schema_version": core.SCHEMA_VERSION, "generated_at": core.now_iso(),
            "source_template": template.name, "limits": core.asdict(limits),
            "policy": {"retries": retries, "clean_state": clean_state,
                       "first_failure_evidence": "retained even when a retry passes",
                       "bounded": True},
            "lanes": lanes,
            "execution": {"simulator_lanes": "xcrun simctl on a licensed macOS worker",
                          "device_lanes": "owned, paired and provisioned physical devices",
                          "note": "this toolkit schedules lanes; it never boots a Simulator or a device"}}


# ==========================================================================
# Info.plist audit
# ==========================================================================
USAGE_KEYS = {
    "NSCameraUsageDescription": ["camera", "avcapture", "uiimagepicker"],
    "NSMicrophoneUsageDescription": ["microphone", "avaudiorecorder", "avaudiosession"],
    "NSPhotoLibraryUsageDescription": ["photolibrary", "uiimagepicker", "phpicker"],
    "NSPhotoLibraryAddUsageDescription": ["photolibrary", "saveimage", "uiimagewritetosavedphotosalbum"],
    "NSLocationWhenInUseUsageDescription": ["corelocation", "cllocation"],
    "NSLocationAlwaysAndWhenInUseUsageDescription": ["corelocation", "cllocation"],
    "NSContactsUsageDescription": ["contacts", "cncontact"],
    "NSCalendarsUsageDescription": ["eventkit", "ekevent"],
    "NSRemindersUsageDescription": ["eventkit", "ekreminder"],
    "NSBluetoothAlwaysUsageDescription": ["corebluetooth", "cbcentral"],
    "NSLocalNetworkUsageDescription": ["nwbrowser", "bonjour", "multipeer"],
    "NSFaceIDUsageDescription": ["localauthentication", "lacontext"],
    "NSTrackingUsageDescription": ["apptrackingtransparency", "attrackingmanager"],
    "NSMotionUsageDescription": ["coremotion", "cmmotion"],
    "NSSpeechRecognitionUsageDescription": ["speech", "sfspeech"],
    "NSHealthShareUsageDescription": ["healthkit"],
    "NSUserTrackingUsageDescription": ["apptrackingtransparency"],
}
REQUIRED_INFO_KEYS = ["CFBundleIdentifier", "CFBundleExecutable", "CFBundlePackageType",
                      "CFBundleShortVersionString", "CFBundleVersion"]


def audit_info_plist(info: Dict[str, Any], *, path: str = "", privacy: bool = False) -> Dict[str, Any]:
    issues: List[Dict[str, str]] = []
    if not isinstance(info, dict) or not info:
        return {"issues": [{"code": "plist/empty", "level": "error",
                            "message": "property list has no keys"}], "privacy_strings": []}
    is_info = "CFBundleIdentifier" in info or "CFBundleExecutable" in info
    if is_info:
        for key in REQUIRED_INFO_KEYS:
            if key not in info:
                issues.append({"code": "plist/missing-key", "level": "error",
                               "message": f"required Info.plist key missing: {key}",
                               "remediation": "add the key with a valid value before building"})
        bundle_id = str(info.get("CFBundleIdentifier", ""))
        if bundle_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]*(\$\{[A-Za-z_]+\}[A-Za-z0-9.\-]*)?", bundle_id):
            issues.append({"code": "plist/bad-bundle-id", "level": "error",
                           "message": f"CFBundleIdentifier '{bundle_id}' is not a valid reverse-DNS id"})
        if any(x in bundle_id for x in ("${", "$(", "{PRODUCT")):
            issues.append({"code": "plist/unresolved-variable", "level": "warn",
                           "message": "bundle identifier contains an unexpanded build variable"})
        if info.get("CFBundlePackageType") not in (None, "APPL", "FMWK", "XPC!", "BNDL"):
            issues.append({"code": "plist/package-type", "level": "warn",
                           "message": f"unusual CFBundlePackageType {info.get('CFBundlePackageType')}"})
        families = info.get("UIDeviceFamily")
        if families is not None:
            if not isinstance(families, list) or not all(f in (1, 2) for f in families):
                issues.append({"code": "plist/device-family", "level": "error",
                               "message": "UIDeviceFamily must list 1 (iPhone) and/or 2 (iPad)"})
            elif families == [1]:
                issues.append({"code": "plist/iphone-only", "level": "warn",
                               "message": "UIDeviceFamily is iPhone-only; the app will run scaled on iPad"})
        scene = info.get("UIApplicationSceneManifest")
        if scene is not None and not isinstance(scene, dict):
            issues.append({"code": "plist/scene-manifest", "level": "error",
                           "message": "UIApplicationSceneManifest must be a dictionary"})
        for key in ("UIBackgroundModes", "CFBundleURLTypes", "UIRequiredDeviceCapabilities"):
            if key in info and not isinstance(info[key], list):
                issues.append({"code": "plist/type", "level": "error",
                               "message": f"{key} must be an array"})
        if "UILaunchStoryboardName" not in info and "UILaunchScreen" not in info:
            issues.append({"code": "plist/launch-screen", "level": "warn",
                           "message": "neither UILaunchStoryboardName nor UILaunchScreen is present; "
                                      "App Store validation requires a launch experience"})
        if not info.get("ITSAppUsesNonExemptEncryption") in (True, False):
            issues.append({"code": "plist/export-compliance", "level": "warn",
                           "message": "ITSAppUsesNonExemptEncryption is undeclared; every upload will "
                                      "prompt for export compliance"})
        caps = info.get("UIRequiredDeviceCapabilities") or []
        stale = [c for c in caps if c in ("armv7", "armv7s")]
        if stale:
            issues.append({"code": "plist/stale-capability", "level": "error",
                           "message": f"UIRequiredDeviceCapabilities lists retired architecture(s): "
                                      f"{', '.join(stale)}"})
    privacy_strings: List[Dict[str, str]] = []
    if privacy:
        present = [k for k in info if k.endswith("UsageDescription")]
        for key in present:
            value = str(info.get(key) or "")
            if not value.strip():
                privacy_strings.append({"key": key, "verdict": "empty",
                                        "note": "empty purpose string fails review"})
            elif len(value.strip()) < 20:
                privacy_strings.append({"key": key, "verdict": "vague",
                                        "note": f"{len(value.strip())} characters is too short to "
                                                "explain the user benefit"})
            elif re.search(r"(?i)\b(lorem|tbd|todo|placeholder|example)\b", value):
                privacy_strings.append({"key": key, "verdict": "placeholder", "note": value[:60]})
            else:
                privacy_strings.append({"key": key, "verdict": "present", "note": value[:80]})
        for key, apis in USAGE_KEYS.items():
            if key not in info and key != "NSTrackingUsageDescription":
                privacy_strings.append({"key": key, "verdict": "omit-unless-used",
                                        "note": "required only when the app links " + ", ".join(apis[:2])})
        if "NSUserTrackingUsageDescription" in info and "NSTrackingUsageDescription" not in info:
            privacy_strings.append({"key": "NSTrackingUsageDescription", "verdict": "not-applicable",
                                    "note": "the AppTrackingTransparency purpose key is "
                                            "NSUserTrackingUsageDescription"})
    return {"issues": issues, "privacy_strings": privacy_strings, "is_info_plist": is_info}


# ==========================================================================
# Xcode project reading (pbxproj parser, no Xcode required)
# ==========================================================================
def _pbx_tokens(text: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            if j < 0:
                break
            i = j + 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i)
            i = (j + 2) if j >= 0 else n
            continue
        if c in "{}();,=":
            tokens.append((c, c))
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            tokens.append(("string", "".join(buf)))
            i = j + 1
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n{}();,="':
            j += 1
        word = text[i:j]
        tokens.append(("word", word))
        i = j
    return tokens


def parse_pbxproj(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    tokens = _pbx_tokens(text)
    pos = [0]

    def parse_value() -> Any:
        if pos[0] >= len(tokens):
            return None
        kind, value = tokens[pos[0]]
        if kind == "{":
            pos[0] += 1
            d: Dict[str, Any] = {}
            while pos[0] < len(tokens) and tokens[pos[0]][0] != "}":
                if tokens[pos[0]][0] not in ("string", "word"):
                    pos[0] += 1
                    continue
                key = tokens[pos[0]][1]
                pos[0] += 1
                if pos[0] < len(tokens) and tokens[pos[0]][0] == "=":
                    pos[0] += 1
                    d[key] = parse_value()
                    if pos[0] < len(tokens) and tokens[pos[0]][0] == ";":
                        pos[0] += 1
                else:
                    d[key] = None
            pos[0] += 1
            return d
        if kind == "(":
            pos[0] += 1
            arr: List[Any] = []
            while pos[0] < len(tokens) and tokens[pos[0]][0] != ")":
                arr.append(parse_value())
                if pos[0] < len(tokens) and tokens[pos[0]][0] == ",":
                    pos[0] += 1
            pos[0] += 1
            return arr
        pos[0] += 1
        return value

    root = parse_value()
    if not isinstance(root, dict):
        raise InputError("project.pbxproj did not parse into an object tree", code="pbxproj-invalid")
    objects = root.get("objects") if isinstance(root.get("objects"), dict) else {}
    return {"root": root, "objects": objects, "is_ascii_plist": True}


def inspect_project(project: str, limits: Limits, *, workspace: bool = False,
                    diff_configs: bool = False, xcconfig: bool = False,
                    destinations: bool = False) -> Dict[str, Any]:
    p = Path(project)
    if not p.exists():
        raise InputError(f"project not found: {project}", code="input-missing")
    issues: List[Dict[str, str]] = []
    result: Dict[str, Any] = {"project": p.as_posix(), "targets": [], "issues": issues}

    pbx = None
    flat_project = p.is_file() and p.suffix == ".pbxproj"
    if flat_project:
        pbx = parse_pbxproj(p)
        project_dir = p.parent
        objects = pbx["objects"]
        return _project_summary(pbx, project_dir, p, limits, issues,
                                diff_configs=diff_configs, xcconfig=xcconfig,
                                destinations=destinations, flat=True)
    if p.name.endswith(".xcworkspace") or workspace:
        ws = p if p.suffix == ".xcworkspace" else p.parent / (p.stem + ".xcworkspace")
        members: List[str] = []
        if (ws / "contents.xcworkspacedata").exists():
            xml = (ws / "contents.xcworkspacedata").read_text(encoding="utf-8", errors="replace")
            members = re.findall(r'location\s*=\s*"group:([^"]+)"', xml)
        result["workspace"] = {"path": ws.as_posix(), "members": members,
                               "has_pods": any("Pods" in m for m in members),
                               "has_packages": any(m.endswith(".xcodeproj") for m in members)}
        proj_candidates = [ws / m for m in members if m.endswith(".xcodeproj")]
        if not proj_candidates:
            issues.append({"code": "project/workspace-empty", "level": "error",
                           "message": "workspace references no .xcodeproj member"})
        p = proj_candidates[0] if proj_candidates else p

    pbx_path = p / "project.pbxproj"
    if not pbx_path.exists():
        raise InputError(f"{p.as_posix()} has no project.pbxproj", code="pbxproj-missing")
    pbx = parse_pbxproj(pbx_path)
    return _project_summary(pbx, p, p, limits, issues, diff_configs=diff_configs,
                            xcconfig=xcconfig, destinations=destinations, flat=False)


def _project_summary(pbx: Dict[str, Any], project_dir: Path, project_obj: Path, limits: Limits,
                     issues: List[Dict[str, str]], *, diff_configs: bool, xcconfig: bool,
                     destinations: bool, flat: bool) -> Dict[str, Any]:
    """Shared analysis for folder-shaped .xcodeproj and flat .pbxproj fixtures."""
    objects = pbx["objects"]
    p = Path(project_dir).resolve()
    result: Dict[str, Any] = {"project": project_obj.as_posix(), "targets": [], "issues": issues,
                              "flat_layout": flat}
    if flat:
        result.setdefault("schemes", {"shared": [], "user": [], "missing_targets": []})

    def o(oid: str) -> Dict[str, Any]:
        v = objects.get(oid)
        return v if isinstance(v, dict) else {}

    files_by_id = {k: v for k, v in objects.items() if isinstance(v, dict)
                   and v.get("isa") in ("PBXFileReference", "PBXGroup", "PBXVariantGroup")}
    targets = []
    missing_refs = 0
    for oid, obj in objects.items():
        if not isinstance(obj, dict) or obj.get("isa") != "PBXNativeTarget":
            continue
        phases = obj.get("buildPhases") or []
        sources = resources = 0
        scripts = []
        for ph in phases:
            pho = o(ph)
            if pho.get("isa") == "PBXSourcesBuildPhase":
                sources = len(pho.get("files") or [])
            elif pho.get("isa") == "PBXResourcesBuildPhase":
                resources = len(pho.get("files") or [])
            elif pho.get("isa") == "PBXShellScriptBuildPhase":
                scripts.append(str(pho.get("shellScript", ""))[:200])
        config_list = o(obj.get("buildConfigurationList", ""))
        configs = {}
        for cfg_id in config_list.get("buildConfigurations") or []:
            cfg = o(cfg_id)
            configs[cfg.get("name", "?")] = cfg.get("buildSettings", {}) or {}
        for f in ((obj.get("buildPhases") or [])):
            pass
        deps = [o(d).get("target") for d in obj.get("dependencies") or []]
        targets.append({"name": obj.get("name", "?"), "product_type": obj.get("productType", "?"),
                        "sources": sources, "resources": resources, "configurations": configs,
                        "shell_scripts": scripts, "dependencies": [d for d in deps if d],
                        "product_name": obj.get("productName", "")})
    for oid, obj in files_by_id.items():
        if obj.get("isa") == "PBXFileReference" and obj.get("sourceTree") == "<group>" \
                and obj.get("path"):
            candidate = p.parent / str(obj["path"])
            if not candidate.exists() and not str(obj["path"]).startswith("../"):
                missing_refs += 1
    if missing_refs:
        issues.append({"code": "project/missing-file-reference", "level": "warn",
                       "message": f"{missing_refs} file reference(s) point at paths that do not exist "
                                  f"beside the project; builds will fail with 'file not found'",
                       "remediation": "re-add the files or fix their paths in the project navigator"})
    if not targets:
        issues.append({"code": "project/no-targets", "level": "error",
                       "message": "project defines no PBXNativeTarget"})
    for t in targets:
        if t["sources"] == 0:
            issues.append({"code": "project/empty-sources", "level": "warn",
                           "message": f"target {t['name']} compiles zero source files"})
        if not t["configurations"]:
            issues.append({"code": "project/no-configurations", "level": "error",
                           "message": f"target {t['name']} has no build configurations"})
        for name, settings in t["configurations"].items():
            if settings.get("SWIFT_VERSION") is None and any(
                    str(v).endswith(".swift") for v in [t.get("product_name", "")]):
                pass
    # schemes
    schemes = result.setdefault("schemes", {"shared": [], "user": [], "missing_targets": []})
    scheme_dirs = [p / "xcshareddata" / "xcschemes", p / "xcuserdata"]
    if flat:
        scheme_files = sorted({*p.glob("asset-fixture-scheme.xcscheme"), *p.glob("*.xcscheme")})
        for f in scheme_files:
            if f.stem not in schemes["shared"]:
                schemes["shared"].append(f.stem)
            txt = f.read_text(encoding="utf-8", errors="replace")
            names = {t["name"] for t in targets}
            for ref in set(re.findall(r'BlueprintName\s*=\s*"([^"]+)"', txt)) - names:
                schemes["missing_targets"].append({"scheme": f.stem, "target": ref})
    elif scheme_dirs[0].exists():
        for f in sorted(scheme_dirs[0].glob("*.xcscheme")):
            schemes["shared"].append(f.stem)
            txt = f.read_text(encoding="utf-8", errors="replace")
            refs = set(re.findall(r'BlueprintName\s*=\s*"([^"]+)"', txt))
            names = {t["name"] for t in targets}
            for r in refs - names:
                schemes["missing_targets"].append({"scheme": f.stem, "target": r})
    if scheme_dirs[1].exists():
        for f in scheme_dirs[1].rglob("*.xcscheme"):
            schemes["user"].append(f.stem)
    if not schemes["shared"]:
        issues.append({"code": "project/no-shared-scheme", "level": "warn",
                       "message": "no shared schemes: CI cannot select a scheme reproducibly",
                       "remediation": "mark the scheme as shared in Xcode and commit it"})
    for m in schemes["missing_targets"]:
        issues.append({"code": "project/scheme-missing-target", "level": "error",
                       "message": f"scheme {m['scheme']} references missing target {m['target']}"})
    result["schemes"] = schemes

    if diff_configs and targets:
        names = sorted({n for t in targets for n in t["configurations"]})
        diffs: Dict[str, Any] = {}
        for key in sorted({k for t in targets for cfg in t["configurations"].values() for k in cfg}):
            values = {cfg_name: t["configurations"].get(cfg_name, {}).get(key)
                      for t in targets[:1] for cfg_name in names}
            present = {k: v for k, v in values.items() if v is not None}
            if len(present) > 1 and len(set(map(str, present.values()))) > 1:
                diffs[key] = present
        result["configuration_diff"] = diffs
        for key, values in list(diffs.items())[:30]:
            issues.append({"code": "project/config-diff", "level": "info",
                           "message": f"{key} differs between configurations: {values}"})

    if xcconfig:
        resolved, cfg_issues = _resolve_xcconfigs(p, targets, limits)
        result["xcconfig"] = {"resolved": resolved, "issues": cfg_issues}
        for i in cfg_issues:
            issues.append({"code": "project/xcconfig", "level": "warn", "message": i})
        for item in resolved[:40]:
            issues.append({"code": "project/xcconfig-resolved", "level": "info",
                           "message": f"{item['key']} = {item['value']} ({item['origin']})"})

    if destinations:
        dests = [{"kind": "generic-device", "detail": "generic/platform=iOS (needs a signing configuration)"},
                 {"kind": "generic-simulator", "detail": "generic/platform=iOS Simulator (no signing)"},
                 {"kind": "simulator-named", "detail": "platform=iOS Simulator,name=<device>,OS=<version>"},
                 {"kind": "attached-device", "detail": "platform=iOS,id=<UDID> (paired, trusted device)"},
                 {"kind": "mac-catalyst", "detail": "platform=macOS,variant=Mac Catalyst (if SUPPORTS_MACCATALYST)"}]
        cfg = targets[0]["configurations"].get("Debug", {}) if targets else {}
        if str(cfg.get("SUPPORTS_MACCATALYST", "")).upper() == "YES":
            dests.append({"kind": "mac-catalyst-enabled", "detail": "target declares Mac Catalyst support"})
        result["destinations"] = dests

    result["summary"] = {
        "project": project_obj.name or p.name, "targets": len(targets),
        "source_files": sum(t["sources"] for t in targets),
        "resource_phases": sum(t["resources"] for t in targets),
        "configurations": len({n for t in targets for n in t["configurations"]}),
        "shared_schemes": len(schemes["shared"]),
        "shell_script_phases": sum(len(t["shell_scripts"]) for t in targets),
        "total_objects": len(objects),
    }
    for t in targets:
        for script in t["shell_scripts"]:
            if re.search(r"(?i)curl|wget|http", script):
                issues.append({"code": "project/network-script-phase", "level": "warn",
                               "message": f"target {t['name']} runs a shell phase that touches the "
                                          f"network: {script[:120]}",
                               "remediation": "pin and verify any downloaded tooling, or vendor it"})
    return result


def _resolve_xcconfigs(project: Path, targets: Sequence[Dict[str, Any]],
                       limits: Limits) -> Tuple[List[Dict[str, str]], List[str]]:
    resolved: List[Dict[str, str]] = []
    issues: List[str] = []
    files = list(project.parent.rglob("*.xcconfig"))[:20]
    if not files:
        return [], ["no .xcconfig files found beside the project"]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            issues.append(f"{f.name}: {exc}")
            continue
        local: Dict[str, str] = {}
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            m = re.match(r"^#include\??\s+\"?([^\"]+)\"?$", line)
            if m:
                inc = (f.parent / m.group(1)).resolve()
                if not inc.exists():
                    issues.append(f"{f.name}:{lineno} includes missing file {m.group(1)}")
                else:
                    local.setdefault("__includes__", "")
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\s*=\s*(.*)$", line)
            if m:
                key, cond, value = m.group(1), m.group(2) or "", m.group(3).strip()
                if "$(" in value or "${" in value:
                    for var in re.findall(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]", value):
                        if var in local:
                            value = re.sub(r"\$[({]" + var + r"[)}]", local[var], value)
                local[key] = value
                resolved.append({"key": key + cond, "value": value, "origin": f.as_posix()})
                continue
            if "=" in line:
                issues.append(f"{f.name}:{lineno} unparsed assignment: {line[:80]}")
        for key, value in local.items():
            if key != "__includes__" and re.search(r"\$[({]", value):
                issues.append(f"{f.name}: {key} still contains an unresolved variable reference")
    return resolved, issues


def diff_entitlements(keys: Sequence[str], first: Dict[str, Any],
                      second: Dict[str, Any]) -> Dict[str, Any]:
    a = set(keys)
    b = set(second["summary"]["entitlement_keys"])
    return {"first": first["summary"].get("name"), "second": second["summary"].get("name"),
            "missing_in_second": {k: k for k in sorted(a - b)},
            "extra_in_second": {k: k for k in sorted(b - a)},
            "shared": sorted(a & b),
            "earliest_mismatch_rule": "check signed entitlements first, then profile-allowed, then "
                                      "distribution; the first disagreement is the root cause"}


# ==========================================================================
# Mach-O audit / closure
# ==========================================================================
def audit_macho(entry: Dict[str, Any], *, base_dir: Path) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for s in entry["slices"]:
        name = f"{Path(entry['path']).name}[{s['arch']}]"
        if s["min_os"] and s["platform"] in ("ios", "ios-simulator"):
            major = int(str(s["min_os"]).split(".")[0] or 0)
            if s["arch"] == "arm64" and major >= 11:
                issues.append({"code": "macho/min-os", "level": "info",
                               "message": f"{name}: minimum OS {s['min_os']} is compatible with "
                                          f"{s['platform']}", "remediation": ""})
        if s["encrypted"]:
            issues.append({"code": "macho/encrypted", "level": "warn",
                           "message": f"{name}: cryptid={s['cryptid']} -- FairPlay-encrypted code cannot "
                                      "be rebuilt or re-signed; authorized unencrypted sources are "
                                      "required", "remediation": "use a development/enterprise build "
                                                                "or the original source"})
        if not s["has_code_signature"]:
            issues.append({"code": "macho/unsigned", "level": "warn",
                           "message": f"{name}: no LC_CODE_SIGNATURE; the image is unsigned and cannot "
                                      "install on a device without signing"})
        if s["filetype"] == "execute" and s["entry_point"] is None:
            issues.append({"code": "macho/no-entry", "level": "info",
                           "message": f"{name}: LC_MAIN absent (entry may use the legacy unix thread)"})
        for dylib in s["dylibs"]:
            if dylib.startswith("@rpath/"):
                found = _resolve_rpath(dylib, s["rpaths"], base_dir)
                if not found:
                    issues.append({"code": "macho/unresolved-rpath", "level": "warn",
                                   "message": f"{name}: {dylib} not found via rpaths "
                                              f"{s['rpaths'][:3]}; dyld will fail at launch",
                                   "remediation": "embed the framework under Frameworks/ and add the "
                                                 "matching @executable_path rpath"})
            elif dylib.startswith("/usr/lib/") or dylib.startswith("/System/Library/"):
                continue
            elif dylib.startswith("@executable_path/") or dylib.startswith("@loader_path/"):
                rel = dylib.split("/", 1)[1]
                if not (base_dir / rel).exists():
                    issues.append({"code": "macho/missing-embedded", "level": "warn",
                                   "message": f"{name}: {dylib} is not present beside the binary"})
    return issues


def _resolve_rpath(dylib: str, rpaths: Sequence[str], base_dir: Path) -> Optional[Path]:
    tail = dylib.split("@rpath/", 1)[1]
    for rp in rpaths:
        resolved = rp.replace("@executable_path", str(base_dir)).replace("@loader_path", str(base_dir))
        candidate = Path(resolved) / tail
        if candidate.exists():
            return candidate
    candidate = base_dir / "Frameworks" / tail
    return candidate if candidate.exists() else None


def dylib_closure(info: Dict[str, Any], *, base_dir: Path) -> Dict[str, Any]:
    required = set()
    present = set()
    weak = set()
    for s in info["slices"]:
        required.update(d for d in s["dylibs"] if not d.startswith(("/usr/lib/", "/System/Library/")))
        weak.update(s.get("weak_dylibs", []))
        if (base_dir / Path(info["path"]).name).name:
            pass
    for f in base_dir.rglob("*"):
        if f.suffix in (".dylib",) or (f.is_dir() and f.suffix == ".framework"):
            present.add(f.name)
    missing = sorted(d for d in required if not _resolve_rpath(d, info["slices"][0]["rpaths"], base_dir)
                     and Path(d).name not in present)
    return {"required": sorted(required)[:60], "weak": sorted(weak)[:20],
            "present_nearby": sorted(present)[:60], "unresolved": missing[:40],
            "rule": "resolve @rpath/@loader_path/@executable_path before claiming a launchable binary"}


def objc_metadata(data: bytes) -> Dict[str, Any]:
    """Heuristic Objective-C metadata harvest from __objc_* sections and class names."""
    names = set()
    for m in re.finditer(rb"__objc_(classlist|catlist|protolist|const|methname|methtype)", data):
        names.add(m.group(0).decode())
    selectors = set(re.findall(rb"\b[a-zA-Z_][a-zA-Z0-9_]{2,40}:", data[: 2 << 20]))
    classes = set(re.findall(rb"\b(?:NS|UI|CA|CL|MK|AV|SK|WK|PH|CN|EK|HK|GK|CK|MT|SC|AR|RP)[A-Z][A-Za-z0-9]{3,40}\b", data[: 4 << 20]))
    return {"sections": sorted(names), "selector_samples": sorted(s.decode("utf-8", "replace")
                                                                  for s in list(selectors)[:40]),
            "framework_class_samples": sorted(c.decode() for c in list(classes)[:60]),
            "note": "heuristic string-level harvest; full interfaces need authorized tooling on the "
                    "matching binary"}


def swift_metadata(data: bytes) -> Dict[str, Any]:
    mangled = sorted({"$s" + m.decode("ascii", "replace") for m in
                      re.findall(rb"\$s[A-Za-z0-9_]{6,120}", data[: 8 << 20])})[:40]
    modules = sorted({m.decode() for m in re.findall(rb"\b(?:Swift|_Concurrency|Foundation|SwiftUI|Combine)\b", data[: 4 << 20])})
    return {"mangled_symbol_samples": mangled, "swift_modules_referenced": modules,
            "has_swift_sections": b"__swift5_" in data,
            "note": "use swift-demangle on the macOS worker for readable signatures"}


# ==========================================================================
# privacy
# ==========================================================================
REQUIRED_REASON_APIS = {
    "NSPrivacyAccessedAPICategoryFileTimestamp": ["creationDate", "modificationDate", "fileModificationDate",
                                                  "contentModificationDate", "attributesOfItem"],
    "NSPrivacyAccessedAPICategorySystemBootTime": ["systemUptime", "mach_absolute_time"],
    "NSPrivacyAccessedAPICategoryDiskSpace": ["volumeAvailableCapacity", "systemFreeSize",
                                              "attributesOfFileSystem"],
    "NSPrivacyAccessedAPICategoryActiveKeyboards": ["activeInputModes"],
    "NSPrivacyAccessedAPICategoryUserDefaults": ["UserDefaults", "NSUserDefaults"],
}
VALID_REASON_CODES = {
    "NSPrivacyAccessedAPICategoryFileTimestamp": {"C617.1", "3B52.1", "0A2A.1"},
    "NSPrivacyAccessedAPICategorySystemBootTime": {"35F9.1", "8FFB.1", "3D61.1"},
    "NSPrivacyAccessedAPICategoryDiskSpace": {"E174.1", "85F4.1", "7D9E.1"},
    "NSPrivacyAccessedAPICategoryActiveKeyboards": {"3EC4.1", "54BD.1"},
    "NSPrivacyAccessedAPICategoryUserDefaults": {"CA92.1", "1C8F.1", "C56D.1", "AC92.1"},
}


def privacy_audit(target: str, limits: Limits, *, required_reasons: Optional[str] = None) -> Dict[str, Any]:
    root = Path(target)
    if not root.exists():
        raise InputError(f"path not found: {target}", code="input-missing")
    manifests: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    files = 0
    search_in_zip = zipfile.is_zipfile(root) if root.is_file() else False
    texts: List[Tuple[str, str]] = []

    if search_in_zip:
        with zipfile.ZipFile(root) as zf:
            for n in zf.namelist():
                if n.endswith(apple.PRIVACY_MANIFEST):
                    try:
                        data = zf.read(n)
                        parsed = apple.parse_plist_bytes(data)["value"]
                    except Exception as exc:  # noqa: BLE001
                        issues.append({"code": "privacy/unparsable", "level": "error",
                                       "message": f"{n}: {exc}", "path": root.as_posix()})
                        continue
                    manifests.append(_manifest_summary(parsed, n))
    else:
        for f in ([root] if root.is_file() else root.rglob("*")):
            if not f.is_file():
                continue
            files += 1
            if f.name == apple.PRIVACY_MANIFEST or f.suffix == ".xcprivacy":
                try:
                    parsed = apple.parse_plist_bytes(f.read_bytes())["value"]
                    manifests.append(_manifest_summary(parsed, f.as_posix()))
                except Exception as exc:  # noqa: BLE001
                    issues.append({"code": "privacy/unparsable", "level": "error",
                                   "message": f"{f.as_posix()}: {exc}", "path": f.as_posix()})
            elif f.suffix in (".swift", ".m", ".mm", ".h", ".plist"):
                if f.stat().st_size < limits.max_file_bytes:
                    try:
                        texts.append((f.as_posix(), f.read_text(encoding="utf-8", errors="replace")))
                    except OSError:
                        pass

    tracked = set()
    declared = {}
    for m in manifests:
        declared.update(m["reasons"])
        tracked.update(m.get("tracking_domains", []))

    api_hits: Dict[str, List[str]] = {}
    for path, text in texts:
        for category, symbols in REQUIRED_REASON_APIS.items():
            for sym in symbols:
                if re.search(rf"\b{re.escape(sym)}\b", text):
                    api_hits.setdefault(category, []).append(f"{Path(path).name}:{sym}")
    for category, hits in api_hits.items():
        if category not in declared:
            issues.append({"code": "privacy/undeclared-required-reason", "level": "error",
                           "message": f"{category} is used ({', '.join(sorted(set(hits))[:4])}) but not "
                                      f"declared in any privacy manifest",
                           "remediation": "add NSPrivacyAccessedAPITypes with a valid reason code"})
        else:
            for code in declared[category]:
                if code not in VALID_REASON_CODES.get(category, set()):
                    issues.append({"code": "privacy/invalid-reason-code", "level": "error",
                                   "message": f"{category} declares unknown reason code {code}",
                                   "remediation": "use a documented reason code from the Apple list"})
    for m in manifests:
        if m["tracking"] and not m.get("tracking_domains"):
            issues.append({"code": "privacy/tracking-without-domains", "level": "warn",
                           "message": f"{m['path']}: NSPrivacyTracking is true but no "
                                      f"NSPrivacyTrackingDomains are listed"})
        if m["collected_types"] and not m.get("purposes"):
            issues.append({"code": "privacy/collected-without-purpose", "level": "warn",
                           "message": f"{m['path']}: collected data types have no acknowledged purposes"})
        if not m["accessed_api_types"]:
            issues.append({"code": "privacy/no-accessed-apis", "level": "info",
                           "message": f"{m['path']}: no required-reason API categories declared"})
    manifest_names = {Path(m["path"]).parent.as_posix() for m in manifests}
    if root.is_dir() and not manifests:
        issues.append({"code": "privacy/manifest-absent", "level": "warn",
                       "message": "no PrivacyInfo.xcprivacy found under the target",
                       "remediation": "bundle a privacy manifest with the app and each SDK"})
    return {
        "target": root.as_posix(), "manifests": manifests, "issues": issues,
        "files_scanned": files,
        "summary": {"manifests": len(manifests),
                    "api_categories": len({k for m in manifests for k in m["reasons"]}),
                    "declared_reasons": sum(len(m["reasons"]) for m in manifests),
                    "tracking_usage": sum(1 for m in manifests if m["tracking"]),
                    "files_scanned": files},
        "rule": "absence of a manifest is a compliance finding, never a build failure this tool invents",
    }


def _manifest_summary(value: Dict[str, Any], path: str) -> Dict[str, Any]:
    reasons: Dict[str, List[str]] = {}
    accessed = value.get("NSPrivacyAccessedAPITypes") or []
    for entry in accessed:
        if isinstance(entry, dict):
            cat = entry.get("NSPrivacyAccessedAPIType", "?")
            reasons[cat] = list(entry.get("NSPrivacyAccessedAPITypeReasons") or [])
    collected = value.get("NSPrivacyCollectedDataTypes") or []
    purposes: List[str] = []
    for c in collected:
        if isinstance(c, dict):
            # Apple names this key NSPrivacyCollectedDataTypePurposes; accept the short form too
            purposes.extend(c.get("NSPrivacyCollectedDataTypePurposes")
                            or c.get("NSPrivacyPurpose") or [])
    return {"path": path, "keys": len(value), "reasons": reasons,
            "accessed_api_types": sorted(reasons.keys()),
            "tracking": bool(value.get("NSPrivacyTracking")),
            "tracking_domains": list(value.get("NSPrivacyTrackingDomains") or []),
            "collected_types": len(collected), "purposes": sorted(set(purposes))}


# ==========================================================================
# signing readiness
# ==========================================================================
def signing_readiness(ipa: Optional[str], profile: Optional[str], identity: Optional[str],
                      limits: Limits, *, team_id: Optional[str] = None,
                      bundle_id: Optional[str] = None, purpose: str = "development",
                      approve: Sequence[str] = ()) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {"verdict": "BLOCKED", "checks": checks}

    prof: Dict[str, Any] = {}
    if profile:
        pp = core.ensure_readable_file(profile, limits, what="provisioning profile")
        prof = apple.parse_mobileprovision(pp, limits)
    else:
        checks.append({"name": "profile-supplied", "ok": False,
                       "detail": "no .mobileprovision supplied; profile-derived checks are unproven"})
    result["profile"] = prof.get("summary", {}) if prof else {}

    ident = {"subject": identity or "", "kind": "declared" if identity else "absent",
             "importable": bool(identity)}
    if identity:
        if "PRIVATE KEY" in identity.upper() or "BEGIN" in identity.upper():
            raise PolicyError("pass the identity name only; private key material is never accepted "
                              "on the command line", code="identity-material-rejected",
                              remediation="import the identity into a keychain and reference its "
                                          "common name or SHA-1 hash")
        checks.append({"name": "identity-declared", "ok": True, "detail": identity[:120]})
        if prof and prof["summary"].get("certificate_count", 0) == 0:
            checks.append({"name": "identity-in-profile", "ok": False,
                           "detail": "profile embeds no developer certificate"})
        else:
            checks.append({"name": "identity-in-profile", "ok": bool(prof),
                           "detail": "profile embeds at least one certificate; exact hash match must be "
                                     "confirmed on the macOS worker with security find-identity"})
    result["identity"] = ident

    if prof:
        days = prof["summary"].get("days_until_expiry")
        expired = isinstance(days, int) and days < 0
        checks.append({"name": "profile-valid", "ok": not expired,
                       "detail": f"expires {prof['summary']['expiration_date']} ({days} days)"})
        if team_id:
            ok = team_id in (prof["summary"].get("team_identifiers") or [])
            checks.append({"name": "team-matches-profile", "ok": ok,
                           "detail": f"team {team_id} vs profile teams "
                                     f"{prof['summary'].get('team_identifiers')}"})
        if bundle_id:
            pattern = prof["summary"].get("bundle_pattern", "")
            ok = bool(pattern) and (pattern == "*" or bundle_id == pattern
                                    or (pattern.endswith("*") and bundle_id.startswith(pattern[:-1])))
            checks.append({"name": "bundle-id-matches-profile", "ok": ok,
                           "detail": f"{bundle_id} vs pattern {pattern}"})
        if purpose == "distribution":
            method = prof["summary"].get("distribution_method", "")
            ok = method in ("ad-hoc", "enterprise (in-house)", "app-store") and \
                not prof["summary"].get("get_task_allow")
            checks.append({"name": "distribution-entitlement", "ok": ok,
                           "detail": f"purpose=distribution, profile method={method}, "
                                     f"get-task-allow={prof['summary'].get('get_task_allow')}"})
        else:
            checks.append({"name": "development-purpose", "ok": bool(prof["summary"].get("get_task_allow")),
                           "detail": "purpose=development and the profile allows development installs"
                                     if prof["summary"].get("get_task_allow") else
                                     "purpose=development but the profile has get-task-allow=false; "
                                     "use a distribution purpose instead"})

    nested = {"count": 0, "components": []}
    signed_ents: Dict[str, Any] = {}
    if ipa:
        ip = core.ensure_readable_file(ipa, limits, what="IPA")
        info = apple.inspect_ipa(ip, limits)
        result["ipa"] = {"bundle_id": info.get("bundle", {}).get("bundle_id"),
                         "architectures": info.get("architectures"),
                         "encrypted": info.get("encrypted")}
        nested = {"count": len(info.get("frameworks", [])) + len(info.get("extensions", [])),
                  "components": (info.get("frameworks", []) + info.get("extensions", []))[:40]}
        checks.append({"name": "ipa-unencrypted", "ok": not info.get("encrypted"),
                       "detail": "FairPlay-encrypted binary cannot be re-signed"
                                 if info.get("encrypted") else "binary is not FairPlay encrypted"})
        if bundle_id and info.get("bundle", {}).get("bundle_id"):
            checks.append({"name": "ipa-bundle-matches", "ok": bundle_id == info["bundle"]["bundle_id"],
                           "detail": f"{bundle_id} vs {info['bundle']['bundle_id']}"})
        with zipfile.ZipFile(ip) as zf:
            prof_names = [n for n in zf.namelist() if n.endswith("embedded.mobileprovision")]
            if prof_names:
                embedded = apple.parse_mobileprovision_bytes(zf.read(prof_names[0]), limits) \
                    if hasattr(apple, "parse_mobileprovision_bytes") else None
                if embedded:
                    result["embedded_profile"] = embedded["summary"]
                    if prof:
                        same = embedded["summary"]["uuid"] == prof["summary"]["uuid"]
                        checks.append({"name": "embedded-profile-matches", "ok": same,
                                       "detail": f"embedded uuid {embedded['summary']['uuid']}"})
                    signed_ents = embedded.get("profile", {}).get("Entitlements", {}) or {}
    result["nested"] = nested

    conflicts: List[str] = []
    if prof and signed_ents:
        allowed = set(prof["summary"]["entitlement_keys"])
        for key, value in signed_ents.items():
            if key not in allowed and not key.startswith(("com.apple.developer.associated-domains",)):
                conflicts.append(f"{key}={value} is signed but not allowed by the supplied profile")
    result["entitlement_conflicts"] = conflicts
    checks.append({"name": "entitlements-consistent", "ok": not conflicts,
                   "detail": "no signed-but-not-allowed entitlements" if not conflicts
                             else f"{len(conflicts)} conflict(s)"})

    gates = [c for c in checks if not c["ok"]]
    result["purpose"] = purpose
    result["verdict"] = "READY" if not gates else "BLOCKED"
    result["gate_note"] = ("READY means every supplied input is internally consistent. It is NOT proof of "
                          "installability: device trust, provisioning eligibility and an observed install "
                          "are still required.")
    return result


# ==========================================================================
# crash triage
# ==========================================================================
TRIAGE_RULES: List[Tuple[str, str, re.Pattern, List[str]]] = [
    ("signing", "signature or provisioning failure",
     re.compile(r"(?i)(invalid signature|code signature|0x800b0101|application-identifier entitlement|"
                r"provisioning profile|errSec|CSSMERR|no valid .* certificate)"),
     ["re-verify with signing-readiness", "compare signed entitlements with the profile"]),
    ("dyld", "dynamic loader failure",
     re.compile(r"(?i)(dyld|Library not loaded|image not found|Symbol not found|no suitable image)"),
     ["run inspect-binary --closure to resolve @rpath references",
      "confirm deployment target of every embedded framework"]),
    ("architecture", "architecture or OS mismatch",
     re.compile(r"(?i)(bad CPU type|no matching architecture|minimum os|requires iOS|"
                r"not supported on this device)"),
     ["run inspect-ipa for architecture and deployment target", "check device OS version"]),
    ("watchdog", "watchdog termination",
     re.compile(r"(?i)(watchdog|0x8badf00d|scene-update|failed to terminate|"
                r"exited abnormally: .*cpu)"),
     ["sample the main thread during launch", "move blocking work off the launch path"]),
    ("memory", "memory pressure or jetsam",
     re.compile(r"(?i)(jetsam|memorystatus|out of memory|vm allocation|per-process-limit)"),
     ["profile with Instruments Allocations on a representative device"]),
    ("swift-runtime", "Swift runtime trap",
     re.compile(r"(?i)(Swift runtime failure|Fatal error:|force.?unwrap|unexpectedly found nil|"
                r"exclusivity|_dispatch_assert)"),
     ["symbolicate with the matching dSYM", "add guards for the failing optional or index"]),
    ("objc-exception", "Objective-C exception",
     re.compile(r"(?i)(NSInvalidArgumentException|NSRangeException|NSUnknownKeyException|"
                r"unrecognized selector|EXC_CRASH \(SIGABRT\))"),
     ["inspect the first app frame and the exception reason line"]),
    ("native-signal", "native signal",
     re.compile(r"(?i)(EXC_BAD_ACCESS|SIGSEGV|SIGBUS|EXC_BAD_INSTRUCTION)"),
     ["enable zombie objects and address sanitizer in a debug build"]),
    ("privacy", "privacy or purpose-string rejection",
     re.compile(r"(?i)(NS.*UsageDescription|privacy manifest|ITMS-91053|ITMS-90683)"),
     ["run inspect-plist --privacy and privacy on the bundle"]),
    ("ats", "App Transport Security rejection",
     re.compile(r"(?i)(App Transport Security|NSURLErrorSecureConnectionFailed|cleartext|ATS)"),
     ["inspect NSAppTransportSecurity exceptions rather than disabling ATS"]),
    ("retired-service", "retired platform service",
     re.compile(r"(?i)(deprecated.*StoreKit|Game Center.*retired|UIWebView|"
                r"we're no longer accepting|ITMS-90809)"),
     ["plan a source migration to the current service"]),
    ("test-failure", "test assertion or UI automation failure",
     re.compile(r"(?i)(XCTAssert|failed - |Assertion Failure|Timed out waiting for)"),
     ["extract first-failure evidence from the xcresult bundle"]),
    ("build-error", "compilation or linker failure",
     re.compile(r"(?i)(error: |undefined symbol|duplicate symbol|clang: error|ld: )"),
     ["run diagnose --kind build on the full log", "check header/module maps and linker flags"]),
    ("device-trust", "pairing or trust failure",
     re.compile(r"(?i)(could not connect|device is locked|trust this computer|pairing failed|"
                r"Developer Mode|AMDeviceSecureStartSession)"),
     ["unlock the device, tap Trust, and confirm Developer Mode where required"]),
]

DTYPE_NAMES = {1: "EXC_BAD_ACCESS", 2: "EXC_BAD_INSTRUCTION", 3: "EXC_ARITHMETIC",
               4: "EXC_EMULATION", 5: "EXC_SOFTWARE", 6: "EXC_BREAKPOINT", 10: "EXC_CRASH",
               11: "EXC_GUARD", 12: "EXC_RESOURCE", 13: "EXC_GUARD"}


def triage_crash(crash: Dict[str, Any]) -> Dict[str, Any]:
    blob = json.dumps(crash, default=str)
    frames = " ".join((f.get("symbol", "") or "") + " " + (f.get("source_file", "") or "")
                      for f in crash.get("frames", [])[:40])
    haystack = f"{blob} {frames}"
    suspects = [img.get("name") for img in crash.get("binary_images", [])
                if img.get("uuid") and img.get("name")]
    for match_class, reason, pattern, hints in TRIAGE_RULES:
        if pattern.search(haystack):
            classification = match_class
            return {"class": classification, "reason": reason, "confidence": "high",
                    "hints": hints, "suspects": suspects[:8],
                    "dtype": DTYPE_NAMES.get(_dtype(crash), "")}
    return {"class": "unknown", "reason": "no catalogued signature matched; inspect the triggered "
                                          "thread and symbolicate before claiming a cause",
            "confidence": "low", "hints": ["run with --dsym-dir to symbolicate app frames"],
            "suspects": suspects[:8], "dtype": DTYPE_NAMES.get(_dtype(crash), "")}


def _dtype(crash: Dict[str, Any]) -> int:
    exc = crash.get("exception", {}) or {}
    try:
        return int(exc.get("type", 0))
    except (TypeError, ValueError):
        return 0


def match_dsyms(crash: Dict[str, Any], dsym_dir: str, limits: Limits) -> Dict[str, Any]:
    root = core.ensure_readable_dir(dsym_dir, what="dSYM directory")
    found: List[Dict[str, Any]] = []
    seen: set = set()
    for p in sorted(root.rglob("*"))[: limits.max_entries]:
        candidates: List[Path] = []
        if p.is_dir() and p.suffix in (".dSYM", ".framework"):
            candidates = [p]
        elif p.is_file():
            try:
                if p.stat().st_size < 4096 or p.suffix in (".plist", ".json", ".txt", ".md", ".log",
                                                           ".ipa", ".zip", ".crash", ".ips",
                                                           ".mobileprovision", ".xcconfig"):
                    continue
                with p.open("rb") as fh:
                    magic = fh.read(4)
                if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                    candidates = [p]
            except OSError:
                continue
        for candidate in candidates:
            for rec in apple.dsym_uuids(candidate, limits):
                key = (rec["uuid"], rec["arch"], rec["file"])
                if key in seen:
                    continue
                seen.add(key)
                found.append(rec)
    by_uuid = {rec["uuid"].replace("-", "").upper(): rec for rec in found}
    matched, unmatched = {}, {}
    for img in crash.get("binary_images", []):
        uuid = (img.get("uuid") or "").replace("-", "").upper()
        if not uuid:
            continue
        if uuid in by_uuid:
            matched[img.get("name", uuid)] = {"uuid": img["uuid"], "file": by_uuid[uuid]["file"],
                                             "arch": by_uuid[uuid]["arch"]}
        else:
            unmatched[img.get("name", uuid)] = img["uuid"]
    return {"dsym_dir": root.as_posix(), "indexed": len(found), "matched": matched,
            "unmatched": unmatched,
            "note": "UUID matches are exact; symbolication itself runs on macOS with atos or "
                    "symbolicatecrash"}


# ==========================================================================
# dSYM index
# ==========================================================================
def index_dsyms(paths: Sequence[str], state: Path, limits: Limits, *, tag: str = "") -> Dict[str, Any]:
    index_file = state / "dsym-index.json"
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else \
        {"schema_version": core.SCHEMA_VERSION, "records": []}
    keyed = {rec["uuid"]: rec for rec in index["records"]}
    indexed = updated = failed = 0
    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    for p in paths:
        root = Path(p)
        if not root.exists():
            errors.append(f"not found: {p}")
            failed += 1
            continue
        for rec in apple.dsym_uuids(root, limits):
            rec = {**rec, "tag": tag, "indexed_at": core.now_iso()}
            records.append(rec)
            if rec["uuid"] in keyed:
                changed = keyed[rec["uuid"]].get("sha256_of_path") != rec["sha256_of_path"]
                keyed[rec["uuid"]] = rec
                updated += 1 if changed else 0
            else:
                keyed[rec["uuid"]] = rec
                indexed += 1
    index["records"] = list(keyed.values())
    core.write_json(index_file, index)
    return {"index_file": index_file.as_posix(), "records": records, "indexed": indexed,
            "updated": updated, "failed": failed, "errors": errors,
            "total_indexed": len(index["records"])}


# ==========================================================================
# log diagnosis
# ==========================================================================
def diagnose_text(path: Path, limits: Limits, *, kind: str = "auto",
                  patterns: Sequence[str] = ()) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > limits.max_entries:
        lines = lines[: limits.max_entries]
    extra = []
    for raw in patterns:
        try:
            extra.append((f"custom/{raw[:40]}", "custom pattern", re.compile(raw),
                          ["inspect the matching log context"]))
        except re.error as exc:
            raise UsageError(f"invalid --pattern regex '{raw}': {exc}", code="bad-regex")
    matches: List[Dict[str, Any]] = []
    classes: List[str] = []
    for i, line in enumerate(lines, 1):
        for match_class, reason, pattern, hints in [*TRIAGE_RULES, *extra]:
            if pattern.search(line):
                if match_class not in classes:
                    classes.append(match_class)
                matches.append({"class": match_class, "reason": reason, "line": i,
                                "text": line.strip()[:400],
                                "remediation": hints[0] if hints else ""})
                break
    if kind != "auto":
        matches = [m for m in matches if kind in m["class"] or kind == m["class"]]
        classes = sorted({m["class"] for m in matches})
    return {"path": path.as_posix(), "kind": kind, "lines": len(lines),
            "matches": matches[: limits.max_findings], "classes": classes}


# ==========================================================================
# migration scan
# ==========================================================================
DEPRECATIONS = [
    ("UIWebView", "use WKWebView", "error"),
    ("UIApplication.shared.keyWindow", "use the scene's key window", "warn"),
    ("UIActivityIndicatorView.Style.gray", "use .medium", "warn"),
    ("SKPaymentQueue", "StoreKit 1 is deprecated for new work; plan StoreKit 2", "info"),
    ("SKProduct", "StoreKit 1 type; migrate to Product", "info"),
    ("UIApplicationDelegate.applicationDidBecomeActive", "keep but pair with scene lifecycle", "info"),
    ("NSCalendarIdentifierGregorian", "fine; check locale handling", "info"),
    ("NSKeyedArchiver.archiveRootObject", "use secure coding APIs", "warn"),
    ("try! ", "replace force-try with handled errors", "warn"),
    ("as! ", "replace force-cast with conditional cast", "warn"),
    ("FatalError", "avoid fatalError in shipping paths", "warn"),
    ("UIScreen.main.bounds", "use the window scene's coordinate space on iPad/multi-window", "warn"),
    ("UIScreen.main.scale", "scale is not a layout unit; use points", "info"),
    ("NotificationCenter.default.addObserver(forName: nil", "nil-name observers are unordered", "info"),
    ("retain]", "manual retain/release: ARC migration required", "error"),
    ("release]", "manual retain/release: ARC migration required", "error"),
    ("autorelease]", "manual retain/release: ARC migration required", "error"),
]
ARCH_REFS = re.compile(r"\b(armv7|armv7s|i386|ARCHS\s*=\s*[^;\n]*armv7|VALID_ARCHS)\b")
DEPLOY_REFS = re.compile(r"(IPHONEOS_DEPLOYMENT_TARGET\s*=\s*)([0-9.]+)")


def migration_scan(root: Path, limits: Limits, *, deprecations: bool = False) -> Dict[str, Any]:
    files = 0
    blockers: List[Dict[str, str]] = []
    found_deprecations: List[Dict[str, Any]] = []
    arch_refs: List[Dict[str, Any]] = []
    deploy_targets: Dict[str, int] = {}
    swift_versions: Dict[str, int] = {}
    candidates = [root] if root.is_file() else [f for f in root.rglob("*") if f.is_file()]
    for f in candidates[: limits.max_entries]:
        if f.suffix.lower() not in {".swift", ".m", ".mm", ".h", ".pbxproj", ".xcconfig", ".plist",
                                    ".podfile", ".gradle", ".json", ".xcodeproj"}:
            continue
        try:
            if f.stat().st_size > limits.max_file_bytes:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files += 1
        for m in DEPLOY_REFS.finditer(text):
            deploy_targets[m.group(2)] = deploy_targets.get(m.group(2), 0) + 1
        for m in re.finditer(r"SWIFT_VERSION\s*=\s*([0-9.]+)", text):
            swift_versions[m.group(1)] = swift_versions.get(m.group(1), 0) + 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if ARCH_REFS.search(line):
                if "armv7" in line or "i386" in line or "VALID_ARCHS" in line:
                    arch_refs.append({"path": f.as_posix(), "line": lineno, "text": line.strip()[:200]})
            if deprecations:
                for symbol, fix, severity in DEPRECATIONS:
                    if symbol in line:
                        found_deprecations.append({"symbol": symbol.strip(), "path": f.as_posix(),
                                                   "line": lineno, "severity": severity,
                                                   "remediation": fix})
    for target, count in deploy_targets.items():
        try:
            major = int(target.split(".")[0])
        except ValueError:
            continue
        if major < 11:
            blockers.append({"message": f"IPHONEOS_DEPLOYMENT_TARGET {target} is below iOS 11, where "
                                        f"32-bit-only apps stopped being supported",
                             "path": "project settings",
                             "remediation": "raise the deployment target and provide 64-bit slices"})
        elif major < 12:
            blockers.append({"message": f"IPHONEOS_DEPLOYMENT_TARGET {target} is below iOS 12; verify "
                                        f"store submission policy for the minimum SDK",
                             "path": "project settings",
                             "remediation": "test the oldest supported lane before raising it"})
    if any(r["line"] for r in arch_refs):
        blockers.append({"message": f"{len(arch_refs)} obsolete architecture reference(s) "
                                    f"(armv7/i386/VALID_ARCHS): a 64-bit rebuild needs source",
                         "path": "project settings",
                         "remediation": "replace binary-only 32-bit dependencies with XCFramework slices"})
    verdict = "BLOCKED" if any("below iOS 11" in b["message"] for b in blockers) else \
              ("REVIEW" if blockers or found_deprecations else "READY")
    return {"root": root.as_posix(), "files": files, "blockers": blockers,
            "deprecations": found_deprecations[: limits.max_findings],
            "arch_references": arch_refs[:200],
            "deployment_targets": deploy_targets, "swift_versions": swift_versions,
            "verdict": verdict}


# ==========================================================================
# export options
# ==========================================================================
METHOD_MAP = {
    "development": "development", "debugging": "development",
    "ad-hoc": "ad-hoc", "release-testing": "release-testing",
    "enterprise": "enterprise", "app-store": "app-store-connect",
    "app-store-connect": "app-store-connect", "validation": "validation",
}


def export_options(method: str, *, team_id: str, bundle_id: Optional[str], signing_style: str,
                   profile: Optional[str], identity: Optional[str], template: Optional[Path],
                   upload_symbols: bool = False, manage_app_version: bool = False) -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Z0-9]{10}", team_id or ""):
        raise InputError("--team-id must be a 10-character Apple team identifier",
                         code="bad-team-id", remediation="copy the team ID from the developer portal")
    notes: List[str] = []
    plist: Dict[str, Any] = {"method": METHOD_MAP[method],
                             "signingStyle": signing_style,
                             "teamID": team_id,
                             "destination": "export",
                             "uploadSymbols": bool(upload_symbols) or method.startswith("app-store"),
                             "stripSwiftSymbols": True,
                             "compileBitcode": False}
    if manage_app_version:
        plist["manageAppVersionAndBuildNumber"] = True
    if signing_style == "manual":
        if not profile or not identity:
            raise InputError("manual signing requires --profile-name and --identity",
                             code="manual-signing-incomplete",
                             remediation="supply the exact profile name and certificate common name")
        mapping: Dict[str, Any] = {identity: {"provisioningProfiles": {}}}
        if bundle_id:
            mapping[identity]["provisioningProfiles"][bundle_id] = profile
        plist["provisioningProfiles"] = {bundle_id: profile} if bundle_id else {}
        plist["signingCertificate"] = identity
        if identity.startswith("Apple Distribution"):
            notes.append("Apple Distribution identities cannot export development builds")
        if method in ("app-store", "app-store-connect") and "Developer" in identity:
            notes.append("a Development identity is invalid for App Store export; use Apple Distribution")
    else:
        notes.append("automatic signing delegates identity selection to Xcode; capture the decision "
                     "from the build log afterwards")
    if method in ("app-store", "app-store-connect"):
        plist["method"] = "app-store-connect"
        notes.append("app-store-connect export produces an upload-ready package; uploading requires "
                     "explicit approval (gate: publishing)")
        notes.append("ensure ITSAppUsesNonExemptEncryption and the privacy manifest are already correct")
    if method == "enterprise":
        notes.append("enterprise export requires an in-house profile with ProvisionsAllDevices; it "
                     "cannot be uploaded to the App Store")
    if method == "ad-hoc":
        notes.append("ad-hoc export only installs on devices listed in the profile")
    if template and template.exists():
        try:
            tpl = plistlib.loads(template.read_bytes())
            for key, value in tpl.items():
                plist.setdefault(key, value)
            notes.append(f"defaults merged from {template.name}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"template not merged: {exc}")
    return {"plist": plist, "notes": notes, "method": method,
            "validation": {"team_id_format": bool(re.fullmatch(r"[A-Z0-9]{10}", team_id)),
                           "method_supported": method in METHOD_MAP,
                           "distribution_method": plist["method"]}}


# ==========================================================================
# test result merge
# ==========================================================================
def merge_test_results(files: Sequence[str], limits: Limits) -> Dict[str, Any]:
    shards = []
    by_id: Dict[str, Dict[str, Any]] = {}
    totals = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "retried": 0}
    failures: List[Dict[str, Any]] = []
    conflicts: List[str] = []
    for f in files:
        p = Path(f)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError(f"{p.name} is not valid JSON: {exc}", code="result-invalid",
                             remediation="merge expects JSON shard summaries with a 'tests' list") from exc
        tests = data.get("tests") if isinstance(data, dict) else data
        if not isinstance(tests, list):
            raise InputError(f"{p.name} has no 'tests' array", code="result-invalid")
        shard = {"file": p.name, "destination": data.get("destination", p.stem),
                 "tests": len(tests), "retries": data.get("retries", 0)}
        shards.append(shard)
        totals["retried"] += int(data.get("retries", 0) or 0)
        for t in tests:
            tid = str(t.get("identifier") or t.get("name") or t.get("test") or "")
            if not tid:
                conflicts.append(f"{p.name}: a test entry has no identifier")
                continue
            status = str(t.get("status", "")).lower()
            entry = {"identifier": tid, "status": status,
                     "destination": shard["destination"],
                     "duration": t.get("duration"),
                     "message": str(t.get("message", ""))[:400],
                     "attempt": int(t.get("attempt", 1) or 1),
                     "attachments": t.get("attachments", [])}
            key = f"{tid}@{shard['destination']}"
            totals["tests"] += 1
            if status in ("passed", "pass", "success"):
                totals["passed"] += 1
            elif status in ("skipped", "skip", "pending"):
                totals["skipped"] += 1
            else:
                totals["failed"] += 1
            if key in by_id:
                prev = by_id[key]
                if prev["status"] != entry["status"] and entry["attempt"] <= prev["attempt"]:
                    conflicts.append(f"{key}: conflicting status {prev['status']} vs {entry['status']} "
                                     f"at the same attempt; first failure retained")
                    continue
                if entry["attempt"] > prev["attempt"]:
                    by_id[key] = {**entry,
                                  "first_failure": prev if prev["status"] not in ("passed", "pass", "success")
                                  else prev.get("first_failure")}
            else:
                by_id[key] = entry
    merged_tests = sorted(by_id.values(), key=lambda t: (t["identifier"], t["destination"]))
    final_failures = [t for t in merged_tests if t["status"] not in ("passed", "pass", "success", "skipped",
                                                                     "skip", "pending")]
    retried_but_passed = [t for t in merged_tests if t.get("first_failure")]
    totals["failed"] = len(final_failures)
    totals["retried"] = len(retried_but_passed)
    return {"schema_version": core.SCHEMA_VERSION, "generated_at": core.now_iso(),
            "shards": len(shards), "shard_detail": shards, "totals": totals,
            "tests": merged_tests[: limits.max_entries],
            "failures": final_failures[: limits.max_findings],
            "first_failure_evidence": retried_but_passed[: limits.max_findings],
            "conflicts": conflicts[:100],
            "rule": "final failures decide the result; first-failure evidence for retried tests is "
                    "retained separately and never erases a regression"}


# ==========================================================================
# evidence pack
# ==========================================================================
def build_evidence_pack(evidence: Sequence[str], claim: str, out_dir: str, limits: Limits,
                        *, tool_version: str) -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    entries: List[Dict[str, Any]] = []
    missing: List[str] = []
    warnings: List[str] = []
    total = 0
    for item in evidence:
        src = Path(item)
        if not src.exists():
            missing.append(item)
            continue
        if src.is_dir():
            files = [f for f in src.rglob("*") if f.is_file()]
        else:
            files = [src]
        for f in files[: limits.max_entries]:
            size = f.stat().st_size
            if total + size > limits.max_total_bytes:
                warnings.append(f"evidence ceiling reached at {human_bytes(total)}; remaining files "
                                f"were not indexed")
                break
            total += size
            kind = apple.sniff_kind(f)
            entry = {"source": f.as_posix(), "kind": kind["kind"], "bytes": size,
                     "sha256": sha256_file(f, limits.max_file_bytes) if size <= limits.max_file_bytes
                     else None,
                     "opened": False, "parsed": False, "note": ""}
            try:
                with f.open("rb") as fh:
                    entry["opened"] = len(fh.read(64)) > 0
            except OSError as exc:
                entry["note"] = str(exc)
            if entry["kind"] == "plist":
                try:
                    apple.load_plist_file(f, limits)
                    entry["parsed"] = True
                except Exception as exc:  # noqa: BLE001
                    entry["note"] = f"plist not parsed: {getattr(exc, 'message', exc)}"
            elif entry["kind"] == "ipa":
                try:
                    apple.inspect_ipa(f, limits)
                    entry["parsed"] = True
                except Exception as exc:  # noqa: BLE001
                    entry["note"] = f"ipa not parsed: {getattr(exc, 'message', exc)}"
            elif entry["kind"] in ("json", "log", "crash-report"):
                try:
                    f.read_text(encoding="utf-8", errors="replace")[: 1 << 16]
                    entry["parsed"] = True
                except OSError as exc:
                    entry["note"] = str(exc)
            elif entry["kind"] in ("macho", "macho-fat", "macho-dylib"):
                try:
                    apple.parse_macho(f.read_bytes()[: limits.max_file_bytes], path=f.as_posix())
                    entry["parsed"] = True
                except Exception:  # noqa: BLE001
                    entry["parsed"] = False
            entries.append(entry)
    manifest_path = out / "evidence-manifest.json"
    payload = {"schema_version": core.SCHEMA_VERSION, "generated_at": core.now_iso(),
               "tool_version": tool_version, "claim": claim, "evidence": entries,
               "missing": missing, "warnings": warnings, "total_bytes": total,
               "file_count": len(entries),
               "manifest_path": manifest_path.as_posix(),
               "preservation_rule": "originals are read in place; the manifest records hashes"}
    core.write_json(manifest_path, payload)
    return payload


# ==========================================================================
# report validation
# ==========================================================================
REQUIRED_REPORT_FIELDS = {
    "schema_version": str, "tool": dict, "run_id": str, "command": str, "generated_at": str,
    "status": str, "ok": bool, "limits": dict, "counts": dict, "findings": list, "errors": list,
}


def validate_report(path: Path, schema_path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"report is not valid JSON: {exc}", code="report-invalid") from exc
    errors: List[str] = []
    for field_name, typ in REQUIRED_REPORT_FIELDS.items():
        if field_name not in data:
            errors.append(f"missing required field '{field_name}'")
        elif not isinstance(data[field_name], typ):
            errors.append(f"field '{field_name}' must be {typ.__name__}, "
                          f"found {type(data[field_name]).__name__}")
    if isinstance(data.get("tool"), dict):
        for k in ("name", "version"):
            if k not in data["tool"]:
                errors.append(f"tool.{k} is required")
    for f in data.get("findings", []) if isinstance(data.get("findings"), list) else []:
        for k in ("code", "level", "message"):
            if k not in f:
                errors.append(f"finding missing '{k}'")
                break
        if f.get("level") not in core.LEVELS:
            errors.append(f"finding level '{f.get('level')}' is not one of {core.LEVELS}")
    if data.get("schema_version") != core.SCHEMA_VERSION:
        errors.append(f"schema_version {data.get('schema_version')!r} != current "
                      f"{core.SCHEMA_VERSION!r} (backward compatibility requires an upgrade path)")
    status = data.get("status")
    if status not in (core.OK, core.WARN, core.FAIL):
        errors.append(f"status {status!r} is not one of {core.OK}/{core.WARN}/{core.FAIL}")
    return {"schema_version": data.get("schema_version", "unknown"), "verdict": "PASS" if not errors
            else "FAIL", "errors": errors, "findings_checked": len(data.get("findings", []))}

# ==========================================================================
# artifact snapshots (reproducible build audit + idempotency evidence)
# ==========================================================================
def snapshot_artifacts(root: Path, limits: Limits, *, workspace: Optional[Path] = None,
                       previous: Optional[Path] = None) -> Dict[str, Any]:
    """Hash a build tree and compare it with a previous snapshot."""
    files: List[Dict[str, Any]] = []
    total = 0
    duplicates: Dict[str, List[str]] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        if len(files) >= limits.max_entries:
            break
        size = f.stat().st_size
        if size > limits.max_file_bytes:
            files.append({"path": f.relative_to(root).as_posix(), "bytes": size,
                          "sha256": None, "note": "exceeds --max-file-bytes; not hashed"})
            continue
        digest = sha256_file(f, limits.max_file_bytes)
        rel = f.relative_to(root).as_posix()
        if workspace:
            rel = Path(workspace).name + "/" + rel
        files.append({"path": rel, "bytes": size, "sha256": digest})
        total += size
        duplicates.setdefault(digest, []).append(rel)
    dupe_groups = {d: paths for d, paths in duplicates.items() if len(paths) > 1}
    result: Dict[str, Any] = {
        "root": root.as_posix(), "files": files, "generated_at": core.now_iso(),
        "total_bytes": total, "duplicate_content_groups": dupe_groups,
        "timestamps_included": False,
        "note": "content hashes only: timestamps are excluded so snapshots are comparable across hosts",
    }
    if previous and Path(previous).exists():
        before = json.loads(Path(previous).read_text(encoding="utf-8"))
        before_map = {f["path"]: f["sha256"] for f in before.get("files", [])}
        after_map = {f["path"]: f["sha256"] for f in files}
        added = sorted(set(after_map) - set(before_map))
        removed = sorted(set(before_map) - set(after_map))
        changed = sorted(p for p in set(before_map) & set(after_map) if before_map[p] != after_map[p])
        unhashed = sorted(p for p, h in {**before_map, **after_map}.items() if h is None)
        result["diff"] = {
            "previous": Path(previous).as_posix(), "added": added[:200],
            "removed": removed[:200], "changed": changed[:200],
            "identical": not (added or removed or changed),
            "not_comparable": unhashed[:200],
            "caveat": "Reproducible-build claims also require identical toolchain, SDK, settings, "
                      "dependency locks and environment; identical hashes alone prove only file equality",
        }
    return result


# ==========================================================================
# binary diff between two authorized builds
# ==========================================================================
def binary_diff(first: Path, second: Path, limits: Limits) -> Dict[str, Any]:
    a = _binary_facts(first, limits)
    b = _binary_facts(second, limits)
    def cmp(key: str) -> Dict[str, Any]:
        return {"first": a.get(key), "second": b.get(key), "same": a.get(key) == b.get(key)}
    arch_added = sorted(set(b["architectures"]) - set(a["architectures"]))
    arch_removed = sorted(set(a["architectures"]) - set(b["architectures"]))
    dylib_added = sorted(b["dylibs"] - a["dylibs"])
    dylib_removed = sorted(a["dylibs"] - b["dylibs"])
    return {
        "first": {"path": first.as_posix(), **{k: a[k] for k in ("size", "sha256", "architectures",
                                                                 "uuid", "min_os", "platform")}},
        "second": {"path": second.as_posix(), **{k: b[k] for k in ("size", "sha256", "architectures",
                                                                  "uuid", "min_os", "platform")}},
        "size": cmp("size"),
        "uuid": cmp("uuid"),
        "min_os": cmp("min_os"),
        "platform": cmp("platform"),
        "architectures_added": arch_added,
        "architectures_removed": arch_removed,
        "dylibs_added": dylib_added[:40],
        "dylibs_removed": dylib_removed[:40],
        "identical_bytes": a["sha256"] == b["sha256"],
        "note": "compares load-command level facts and content hashes; disassembly-level diffs need "
                "authorized tooling on the matching binaries",
    }


def _binary_facts(path: Path, limits: Limits) -> Dict[str, Any]:
    data = path.read_bytes()
    if len(data) > limits.max_file_bytes:
        raise LimitError(f"{path.name} exceeds --max-file-bytes for diffing", code="file-too-large")
    info = apple.parse_macho(data, path=path.as_posix())
    first = info["slices"][0]
    return {
        "size": len(data), "sha256": core.sha256_bytes(data),
        "architectures": info["architectures"],
        "uuid": first.get("uuid"), "min_os": first.get("min_os"), "platform": first.get("platform"),
        "dylibs": {d for s in info["slices"] for d in s.get("dylibs", [])},
        "slices": [s["arch"] for s in info["slices"]],
    }
