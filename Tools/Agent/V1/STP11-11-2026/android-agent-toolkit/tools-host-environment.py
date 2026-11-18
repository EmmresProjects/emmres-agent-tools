#!/usr/bin/env python3
"""android-agent-toolkit :: host, SDK, build, integrity, self-test and workspace (module 2 of 7).

Command groups implemented here:
  host        profile | env | limits          host capability, environment, configured limits
  sdk         inventory | licenses | footprint | avds | jdk-route | matrix
  build       inspect <dir> | compat          read-only build-system detection (nothing is executed)
  integrity   verify | pins-init | pins-add | pins-verify   SHA-256 pins for tool/input files
  selftest    run [--only id] | status        startup self-test driven by asset-selftest-suite.json
  workspace   clean --target <dir>            dry-run by default; deletes only with --yes

Pure Python standard library. External binaries are reached only through
tools-tool-adapters.py (whitelist + core.run_command).
"""

import base64
import datetime
import json
import os
import platform
import re
import shutil
import sys
import tempfile

import aatk_core as core

ADAPTERS = core.get_sibling("tools-tool-adapters.py")
ARTIFACTS = core.get_sibling("tools-artifact-analysis.py")

HOST_RUNTIME_TOOLS = ["java", "javac", "node", "npm", "git", "zip", "unzip", "file", "strings",
                      "readelf", "objdump", "python3", "openssl", "sqlite3"]
SELFTEST_FILE = "selftest-status.json"
PIN_KIND = "toolkit-pins"


def _mtime_iso(p):
    return datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# host
# ---------------------------------------------------------------------------

def _cpu_flags():
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("flags"):
                    flags = set(line.split(":", 1)[1].split())
                    return {"vmx": "vmx" in flags, "svm": "svm" in flags}
    except OSError:
        pass
    return {"vmx": None, "svm": None}


def _meminfo():
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1048576.0, 2)
    except (OSError, ValueError, IndexError):
        pass
    return None


def kvm_status():
    path = "/dev/kvm"
    if not os.path.exists(path):
        return {"status": core.ST_HOST_FEATURE_REQUIRED, "present": False,
                "hint": "/dev/kvm is absent. Emulator acceleration needs KVM; use a device or a CI host with KVM."}
    ok = os.access(path, os.R_OK | os.W_OK)
    return {"status": core.ST_FOUND if ok else core.ST_HOST_FEATURE_REQUIRED, "present": True,
            "readable_writable": ok,
            "hint": None if ok else "/dev/kvm exists but is not readable and writable by this user."}


def cmd_host_profile(args, cfg):
    cpu = _cpu_flags()
    disk = shutil.disk_usage(os.getcwd())
    tools = {}
    for name in HOST_RUNTIME_TOOLS:
        try:
            r = ADAPTERS.version_probe(name, cfg)
        except core.ToolkitError:
            continue
        tools[name] = {"status": r["status"], "version": r.get("version"), "path": r.get("path")}
    uname = os.uname()
    data = {
        "platform": {"system": uname.sysname, "machine": uname.machine, "kernel": uname.release,
                     "libc": " ".join(x for x in platform.libc_ver() if x),
                     "python": platform.python_version()},
        "cpu": {"logical_cpus": os.cpu_count(), "virtualization_flags": cpu},
        "memory_gib": _meminfo(),
        "disk_free_gib_at_cwd": round(disk.free / 1073741824.0, 2),
        "kvm": kvm_status(),
        "runtime_tools": tools,
        "baseline": {"java": "OpenJDK 17", "required_commands": ["java", "git", "zip", "unzip", "file",
                                                                 "strings", "readelf", "objdump"]},
    }
    missing = [n for n in data["baseline"]["required_commands"]
               if tools.get(n, {}).get("status") != core.ST_FOUND]
    warnings = []
    if tools.get("java", {}).get("status") == core.ST_WRONG_VERSION:
        warnings.append("java is %s; baseline is OpenJDK 17. Run: sdk jdk-route" % tools["java"].get("version"))
    if missing:
        warnings.append("baseline commands not ready: " + ", ".join(missing))
    lines = ["platform: %s %s, kernel %s, %s" % (uname.sysname, uname.machine, uname.release,
                                                  data["platform"]["libc"]),
             "cpus: %s, memory: %s GiB, disk free at cwd: %s GiB" % (
                 data["cpu"]["logical_cpus"], data["memory_gib"], data["disk_free_gib_at_cwd"]),
             "kvm: %s" % data["kvm"]["status"]]
    for name, t in tools.items():
        lines.append("tool %-12s %-16s %s" % (name, t["status"], t.get("version") or ""))
    data["_text"] = lines
    return core.ok("host profile", data, warnings)


def cmd_host_env(args, cfg):
    env = {}
    for var in cfg.get("env_vars") or []:
        val = os.environ.get(var)
        # only configured, non-secret toolchain locations are echoed
        env[var] = {"set": val is not None, "value": val}
    sdk = ADAPTERS.sdk_roots(cfg)
    data = {"env": env, "sdk_roots_found": sdk, "search_dirs": ADAPTERS.search_dirs(cfg),
            "toolkit_dir": core.TOOLKIT_DIR, "cwd": os.getcwd(), "python": sys.version.split()[0],
            "_text": ["env %-20s %s" % (k, ("set: " + str(v["value"])) if v["set"] else "unset") for k, v in env.items()]
                     + ["sdk roots: %s" % (", ".join(sdk) or "none found")]}
    return core.ok("host env", data, [] if sdk else ["No Android SDK root found; SDK-dependent tools will report MISSING."])


def cmd_host_limits(args, cfg):
    limits = cfg.get("limits") or {}
    data = {"limits": limits, "approval": cfg.get("approval"),
            "_text": ["%s = %s" % (k, v) for k, v in sorted(limits.items())]}
    return core.ok("host limits", data)


# ---------------------------------------------------------------------------
# sdk
# ---------------------------------------------------------------------------

def _sdk_root_or_error(cfg):
    roots = ADAPTERS.sdk_roots(cfg)
    if not roots:
        raise core.ToolkitError("E_TOOL_MISSING", "No Android SDK root found",
                                status=core.ST_MISSING,
                                hint="Set ANDROID_HOME or ANDROID_SDK_ROOT to an SDK directory.")
    return roots[0]


def _bounded_size(path, max_entries):
    total, count, truncated = 0, 0, False
    for root, dirs, files in os.walk(path):
        for f in files:
            count += 1
            if count > max_entries:
                return total, count, True
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total, count, truncated


def _list_dirs(base, pattern_depth=1):
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if os.path.isdir(p):
            out.append(name)
    return out


def cmd_sdk_inventory(args, cfg):
    root = _sdk_root_or_error(cfg)
    inv = {
        "root": root,
        "platforms": [n for n in _list_dirs(os.path.join(root, "platforms")) if n.startswith("android-")],
        "build_tools": _list_dirs(os.path.join(root, "build-tools")),
        "ndk": _list_dirs(os.path.join(root, "ndk")),
        "cmake": _list_dirs(os.path.join(root, "cmake")),
        "system_images": [],
        "platform_tools": os.path.isdir(os.path.join(root, "platform-tools")),
        "emulator": os.path.isdir(os.path.join(root, "emulator")),
        "cmdline_tools": _list_dirs(os.path.join(root, "cmdline-tools")),
    }
    si = os.path.join(root, "system-images")
    for api in _list_dirs(si):
        for tag in _list_dirs(os.path.join(si, api)):
            for abi in _list_dirs(os.path.join(si, api, tag)):
                inv["system_images"].append("%s/%s/%s" % (api, tag, abi))
    data = {"inventory": inv}
    if args.health:
        checks = {
            "adb": os.path.isfile(os.path.join(root, "platform-tools", "adb")),
            "sdkmanager": bool(ADAPTERS.locate("sdkmanager", cfg)[0]),
            "build-tools aapt2/apksigner/zipalign": any(
                os.path.isfile(os.path.join(root, "build-tools", v, t)) for v in inv["build_tools"]
                for t in ("aapt2", "apksigner", "zipalign")),
            "platforms present": bool(inv["platforms"]),
            "licenses directory": os.path.isdir(os.path.join(root, "licenses")),
        }
        data["health"] = {k: (core.ST_FOUND if v else core.ST_MISSING) for k, v in checks.items()}
    if args.caches:
        gh = os.environ.get("GRADLE_USER_HOME") or os.path.join(os.path.expanduser("~"), ".gradle")
        cache = os.path.join(gh, "caches")
        size, count, trunc = _bounded_size(cache, core.limit(cfg, "clean_max_files", 100000)) \
            if os.path.isdir(cache) else (0, 0, False)
        data["caches"] = {"gradle_caches": cache if os.path.isdir(cache) else None,
                          "bytes": size, "files_counted": count, "truncated": trunc}
    data["_text"] = ["%s: %s" % (k, v) for k, v in inv.items()]
    return core.ok("sdk inventory", data)


def cmd_sdk_licenses(args, cfg):
    root = _sdk_root_or_error(cfg)
    ldir = os.path.join(root, "licenses")
    files = sorted(os.listdir(ldir)) if os.path.isdir(ldir) else []
    accepted = [f for f in files if f.endswith("-license")]
    data = {"licenses_dir": ldir, "accepted_license_files": accepted,
            "accepted_count": len(accepted),
            "policy": "Read-only audit. This toolkit never accepts SDK licenses on your behalf; "
                      "run sdkmanager --licenses yourself after reviewing the terms."}
    status = core.ST_FOUND if accepted else core.ST_LICENSE_REQUIRED
    data["status"] = status
    data["_text"] = ["status: %s" % status, "accepted license files: %d" % len(accepted)] + \
                    ["  " + f for f in accepted[:40]]
    return core.ok("sdk licenses", data, [] if accepted else ["No accepted SDK license files found."],
                   status=status)


def cmd_sdk_footprint(args, cfg):
    root = _sdk_root_or_error(cfg)
    cap = core.limit(cfg, "clean_max_files", 100000)
    rows = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            size, count, trunc = _bounded_size(p, cap)
            rows.append({"dir": name, "bytes": size, "files": count, "truncated": trunc})
    rows.sort(key=lambda r: r["bytes"], reverse=True)
    total = sum(r["bytes"] for r in rows)
    data = {"root": root, "total_bytes": total, "dirs": rows,
            "_text": ["total: %.2f GiB" % (total / 1073741824.0)] +
                     ["  %-18s %10.1f MiB  %d files" % (r["dir"], r["bytes"] / 1048576.0, r["files"]) for r in rows]}
    return core.ok("sdk footprint", data)


def cmd_sdk_avds(args, cfg):
    home = os.environ.get("ANDROID_AVD_HOME") or os.path.join(os.path.expanduser("~"), ".android", "avd")
    avds = []
    if os.path.isdir(home):
        for f in sorted(os.listdir(home)):
            if f.endswith(".ini"):
                info = {"name": f[:-4]}
                try:
                    with open(os.path.join(home, f), "r", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if "=" in line:
                                k, v = line.strip().split("=", 1)
                                if k == "path":
                                    info["path"] = v
                except OSError:
                    info["error"] = "unreadable"
                avds.append(info)
    status = core.ST_FOUND if avds else core.ST_DEVICE_REQUIRED
    data = {"avd_home": home, "avds": avds, "status": status,
            "hint": None if avds else "No AVDs found. Create one with avdmanager (approval required)."}
    if not avds:
        raise core.ToolkitError("E_DEVICE_REQUIRED", "No Android Virtual Devices found in %s" % home,
                                hint=data["hint"], status=status, data=data)
    return core.ok("sdk avds", data, status=status)


def _java_major(java_bin):
    res = core.run_command([java_bin, "-version"], timeout=20, max_output=65536, retries=1)
    text = (res.get("stderr") or "") + (res.get("stdout") or "")
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if not m:
        return None, text.strip()[:120]
    major = int(m.group(1))
    if major == 1 and m.group(2):
        major = int(m.group(2))
    return major, text.strip().splitlines()[0] if text.strip() else ""


def _license_state(cfg):
    roots = ADAPTERS.sdk_roots(cfg)
    if not roots:
        return core.ST_MISSING
    ldir = os.path.join(roots[0], "licenses")
    if os.path.isdir(ldir) and any(f.endswith("-license") for f in os.listdir(ldir)):
        return core.ST_FOUND
    return core.ST_LICENSE_REQUIRED


def cmd_sdk_jdk_route(args, cfg):
    candidates = {}
    for spec in cfg.get("jdk_scan_dirs") or []:
        base = core.expand_path(spec) if not spec.startswith("$") else os.environ.get(spec[1:], "")
        if not base or not os.path.isdir(base):
            continue
        candidates_dirs = [base] + [os.path.join(base, d) for d in _list_dirs(base)]
        for d in candidates_dirs:
            jb = os.path.join(d, "bin", "java")
            if os.path.isfile(jb) and os.access(jb, os.X_OK):
                candidates[os.path.realpath(jb)] = d
    path_java = shutil.which("java")
    if path_java:
        candidates.setdefault(os.path.realpath(path_java), "PATH")
    installs = []
    for jb, home in sorted(candidates.items()):
        major, desc = _java_major(jb)
        installs.append({"java": jb, "home": home, "major": major, "banner": desc})
    chosen = next((i for i in installs if i["major"] == 17), None)
    if chosen:
        status, hint = core.ST_FOUND, "Use JDK 17: export JAVA_HOME=%s" % os.path.dirname(os.path.dirname(chosen["java"]))
    elif installs:
        status = core.ST_WRONG_VERSION
        hint = ("No OpenJDK 17 found; detected majors: %s. Install openjdk-17 or set JAVA_HOME to a 17 JDK. "
                "Gradle 8.9+/AGP 8.7 accept 17 or 21." % sorted({str(i["major"]) for i in installs}))
    else:
        status, hint = core.ST_MISSING, "No Java installed. Install OpenJDK 17."
    data = {"status": status, "baseline": "OpenJDK 17", "installs": installs, "hint": hint,
            "JAVA_HOME": os.environ.get("JAVA_HOME")}
    data["_text"] = ["hint: %s" % hint] + \
                    ["  java %s (major %s) at %s" % (i["java"], i["major"], i["home"]) for i in installs]
    return core.ok("sdk jdk-route", data, status=status)


def cmd_sdk_matrix(args, cfg):
    matrix = core.load_json(core.resolve_asset("asset-compat-matrix.json"), "compat matrix")
    kinds = matrix.get("sdk_matrix_kinds") or {}
    kind = (args.kind or "platforms").replace("-", "_")
    if kind not in kinds:
        raise core.ToolkitError("E_USAGE", "Unknown SDK matrix kind: %s" % args.kind,
                                hint="Choose one of: %s" % ", ".join(k.replace("_", "-") for k in kinds))
    data = {"kind": args.kind, "matrix": kinds[kind]}
    return core.ok("sdk matrix", data)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

BUILD_MARKERS = {
    "gradle": ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew"],
    "apktool": ["apktool.yml"],
    "cmake": ["CMakeLists.txt"],
    "ndk-build": ["Android.mk", "Application.mk"],
    "maven": ["pom.xml"],
    "bazel": ["WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel"],
}


def cmd_build_inspect(args, cfg):
    d = core.check_input_path(args.path, kind="dir", label="project-dir")
    found = {}
    for sys_name, names in BUILD_MARKERS.items():
        hits = [n for n in names if os.path.exists(os.path.join(d, n))]
        if hits:
            found[sys_name] = hits
    notes = []
    gwp = os.path.join(d, "gradle", "wrapper", "gradle-wrapper.properties")
    wrapper = None
    if os.path.isfile(gwp):
        with open(gwp, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("distributionUrl"):
                    wrapper = line.split("=", 1)[1].strip().replace("\\:", ":")
    agp_hint = None
    for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle/libs.versions.toml"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.path.getsize(p) <= 512 * 1024:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            m = re.search(r"com\.android\.tools\.build:gradle:([0-9][^'\"\s]*)|agp\s*=\s*\"([0-9][^\"]*)\"", txt)
            if m:
                agp_hint = m.group(1) or m.group(2)
                break
    if "gradle" in found and not any(n.endswith("gradlew") for n in found["gradle"]):
        notes.append("No gradlew wrapper present; use a pinned Gradle distribution.")
    if "apktool" in found:
        notes.append("apktool project: rebuild with `tool run apktool -- b <dir>` (approval required).")
    data = {"path": d, "build_systems": found or {"none": []}, "gradle_wrapper_distribution": wrapper,
            "android_gradle_plugin_hint": agp_hint, "notes": notes,
            "execution": "none - inspection only; no build is started by this command."}
    data["_text"] = ["build systems: %s" % ", ".join(sorted(found)) if found else "no known build system markers"]
    return core.ok("build inspect", data, notes)


def cmd_build_compat(args, cfg):
    matrix = core.load_json(core.resolve_asset("asset-compat-matrix.json"), "compat matrix")
    data = {"agp_gradle_jdk": matrix.get("agp_gradle_jdk"), "gradle_jdk": matrix.get("gradle_jdk")}
    return core.ok("build compat", data)


# ---------------------------------------------------------------------------
# integrity
# ---------------------------------------------------------------------------

def _pin_store_path(args, cfg):
    name = (cfg.get("paths") or {}).get("pin_store", "toolkit-pins.json")
    return os.path.join(args.out_dir, name)


def _hash_inputs(files, cfg):
    rows = []
    for f in files:
        p = core.check_input_path(f, kind="file", label="file")
        rows.append({"path": f, "sha256": core.sha256_file(p), "size_bytes": os.path.getsize(p)})
    return rows


def cmd_integrity_verify(args, cfg):
    if not args.files:
        raise core.ToolkitError("E_USAGE", "integrity verify needs --files <path...>")
    rows = _hash_inputs(args.files, cfg)
    store = _pin_store_path(args, cfg)
    pins = {}
    if os.path.isfile(store):
        pins = core.load_json(store, "pin store").get("pins", {})
    for r in rows:
        pinned = pins.get(r["path"])
        if pinned is None:
            r["pin"] = "NOT PINNED"
        else:
            r["pin"] = "MATCH" if pinned.get("sha256") == r["sha256"] else "CHANGED"
    data = {"files": rows, "pin_store": store if pins else None,
            "_text": ["%s  %s  %s" % (r["sha256"], r["pin"], r["path"]) for r in rows]}
    return core.ok("integrity verify", data)


def cmd_integrity_pins_init(args, cfg):
    store = _pin_store_path(args, cfg)
    if os.path.exists(store):
        core.ensure_approval("overwrite existing pin store %s" % store, args.yes, cfg)
    core.check_output_path(store, kind="file", overwrite=True, label="pin-store")
    doc = {"kind": PIN_KIND, "created": core.now_iso(), "pins": {}}
    core.write_text(store, core.json_dumps(doc))
    return core.ok("integrity pins-init", {"pin_store": store, "pins": 0})


def cmd_integrity_pins_add(args, cfg):
    if not args.files:
        raise core.ToolkitError("E_USAGE", "integrity pins-add needs --files <path...>")
    store = _pin_store_path(args, cfg)
    if not os.path.isfile(store):
        raise core.ToolkitError("E_NOT_FOUND", "No pin store at %s" % store,
                                hint="Run: integrity pins-init first")
    doc = core.load_json(store, "pin store")
    rows = _hash_inputs(args.files, cfg)
    for r in rows:
        doc.setdefault("pins", {})[r["path"]] = {"sha256": r["sha256"], "size_bytes": r["size_bytes"],
                                                  "pinned_at": core.now_iso()}
    core.write_text(store, core.json_dumps(doc))
    return core.ok("integrity pins-add", {"pin_store": store, "added": [r["path"] for r in rows],
                                          "total_pins": len(doc["pins"])})


def cmd_integrity_pins_verify(args, cfg):
    store = _pin_store_path(args, cfg)
    if not os.path.isfile(store):
        raise core.ToolkitError("E_NOT_FOUND", "No pin store at %s" % store,
                                hint="Run: integrity pins-init, then pins-add --files <path...>")
    doc = core.load_json(store, "pin store")
    rows = []
    for path, pin in sorted((doc.get("pins") or {}).items()):
        if not os.path.isfile(path):
            rows.append({"path": path, "result": "MISSING"})
            continue
        cur = core.sha256_file(path)
        rows.append({"path": path, "result": "OK" if cur == pin.get("sha256") else "CHANGED",
                     "pinned": pin.get("sha256"), "current": cur})
    bad = [r for r in rows if r["result"] != "OK"]
    data = {"pin_store": store, "results": rows, "failing": len(bad),
            "_text": ["%-8s %s" % (r["result"], r["path"]) for r in rows] or ["no pins recorded"]}
    if bad:
        raise core.ToolkitError("E_FAILED", "%d pinned file(s) do not verify" % len(bad), data=data)
    return core.ok("integrity pins-verify", data)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _suite():
    return core.load_json(core.resolve_asset("asset-selftest-suite.json"), "self-test suite")


def _status_result(entry_id, kind, status, critical, detail=None, extra=None):
    row = {"id": entry_id, "kind": kind, "status": status, "critical": bool(critical)}
    if detail:
        row["detail"] = detail
    if extra:
        row.update(extra)
    return row


def _builtin_check(entry, cfg, out_dir):
    chk = entry["check"]
    blob = base64.b64decode(entry["blob_b64"]) if entry.get("blob_b64") else b""
    if chk == "axml-package":
        doc = ARTIFACTS.parse_axml(blob)
        ms = ARTIFACTS.manifest_summary(doc)
        ok = ms.get("package") == entry["expect"]["package"]
        return ok, {"package": ms.get("package")}
    if chk in ("dex-header", "elf-machine", "zip-fixture"):
        fd, tmp = tempfile.mkstemp(prefix="selftest-", dir=out_dir)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            if chk == "dex-header":
                hdr, _ = ARTIFACTS.dex_verify(tmp, cfg)
                ok = (hdr["adler32_ok"] and hdr["sha1_ok"] and
                      hdr["class_defs_size"] == entry["expect"]["class_defs_size"])
                return ok, {"adler32_ok": hdr["adler32_ok"], "sha1_ok": hdr["sha1_ok"],
                            "class_defs_size": hdr["class_defs_size"]}
            if chk == "elf-machine":
                info = ARTIFACTS.elf_summary(tmp, cfg)
                return info.get("machine") == entry["expect"]["machine"], {"machine": info.get("machine"),
                                                                            "class": info.get("elf_class")}
            zh = ARTIFACTS.zip_health(tmp, cfg)
            codes = sorted({i["code"] for i in zh.get("issues", [])})
            want = set(entry["expect"]["issue_codes"])
            return want.issubset(set(codes)), {"issue_codes": codes}
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if chk == "triage-sample":
        TRIAGE = core.get_sibling("tools-triage-reporting.py")
        path = core.resolve_asset(entry["file"])
        res = TRIAGE.analyze_files([path], cfg)
        kinds = sorted({k for b in res["blocks"] for k in b["kinds"]})
        ok = len(res["blocks"]) >= entry["expect"]["min_blocks"] and set(entry["expect"]["kinds"]).issubset(set(kinds))
        return ok, {"blocks": len(res["blocks"]), "kinds": kinds}
    raise core.ToolkitError("E_INTERNAL", "Unknown builtin check: %s" % chk)


def run_selftest(only, cfg, out_dir):
    suite = _suite()
    entries = suite.get("entries") or []
    if only:
        entries = [e for e in entries if e["id"] == only]
        if not entries:
            raise core.ToolkitError("E_USAGE", "No self-test entry with id '%s'" % only,
                                    hint="Run: selftest status   or   capabilities --category 24")
    results = []
    for e in entries:
        kind, crit, eid = e["kind"], bool(e.get("critical")), e["id"]
        try:
            if kind == "tool":
                r = ADAPTERS.selftest_entry(e["tool"], cfg)
                results.append(_status_result(eid, kind, r["status"], crit,
                                              detail=r.get("version") or r.get("error") or r.get("hint"),
                                              extra={"tool": e["tool"]}))
            elif kind == "host":
                r = kvm_status()
                results.append(_status_result(eid, kind, r["status"], crit, detail=r.get("hint")))
            elif kind == "license":
                results.append(_status_result(eid, kind, _license_state(cfg), crit))
            elif kind == "device":
                r = ADAPTERS.adb_devices(cfg)
                results.append(_status_result(eid, kind, r["status"], crit, detail=r.get("hint")))
            elif kind == "builtin":
                ok, detail = _builtin_check(e, cfg, out_dir)
                results.append(_status_result(eid, kind, "PASS" if ok else "FAIL", crit, detail=detail))
            else:
                results.append(_status_result(eid, kind, "FAIL", crit, detail="unknown kind"))
        except core.ToolkitError as exc:
            results.append(_status_result(eid, kind, exc.status or "FAIL", crit, detail=exc.message))
        except Exception as exc:  # noqa: BLE001 - a self-test row must never abort the run
            results.append(_status_result(eid, kind, "FAIL", crit, detail="%s: %s" % (type(exc).__name__, exc)))
    summary = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    critical_bad = [r for r in results if r["critical"] and r["status"] not in (core.ST_FOUND, "PASS")]
    doc = {"generated": core.now_iso(), "suite": suite.get("suite_id"), "toolkit_version": core.TOOLKIT_VERSION,
           "summary": summary, "results": results, "critical_failures": [r["id"] for r in critical_bad]}
    return doc, critical_bad


def cmd_selftest_run(args, cfg):
    out_dir = args.out_dir
    doc, critical_bad = run_selftest(args.only, cfg, out_dir)
    status_path = os.path.join(out_dir, SELFTEST_FILE)
    core.write_text(status_path, core.json_dumps(doc))
    lines = ["self-test: %s" % ", ".join("%s=%d" % kv for kv in sorted(doc["summary"].items()))]
    for r in doc["results"]:
        det = r.get("detail")
        if det is None or det == "":
            det = r.get("hint") or "-"
        elif isinstance(det, dict):
            det = json.dumps(det)
        lines.append("  %-28s %-20s %s" % (r["id"], r["status"], det))
    doc["_text"] = lines
    doc["status_file"] = status_path
    data = doc
    if critical_bad:
        raise core.ToolkitError("E_FAILED", "Critical self-test entries did not pass: %s" %
                                ", ".join(doc["critical_failures"]), data=data,
                                hint="Fix the failing host requirement; see knowledge-readme.md (Troubleshooting).")
    return core.ok("selftest run", data)


def cmd_selftest_status(args, cfg):
    path = os.path.join(args.out_dir, SELFTEST_FILE)
    if not os.path.isfile(path):
        return core.ok("selftest status", {"status_file": path, "present": False,
                                           "hint": "No stored result. Run: selftest run"},
                       status="NOT RUN")
    doc = core.load_json(path, "self-test status")
    doc["present"] = True
    doc["status_file"] = path
    doc["_text"] = ["last run: %s" % doc.get("generated"),
                    "summary: %s" % doc.get("summary"),
                    "critical failures: %s" % (doc.get("critical_failures") or "none")]
    return core.ok("selftest status", doc)


# ---------------------------------------------------------------------------
# workspace clean (dry run unless --yes)
# ---------------------------------------------------------------------------

def _glob_match(name, pattern):
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def cmd_workspace_clean(args, cfg):
    target = core.check_input_path(args.target, kind="dir", label="clean-target")
    home = os.path.realpath(os.path.expanduser("~"))
    if target in (os.path.sep, home):
        raise core.ToolkitError("E_INPUT", "Refusing to clean filesystem root or home directory: %s" % target)
    wc = cfg.get("workspace_clean") or {}
    gdirs, gfiles = set(wc.get("generated_dir_names") or []), wc.get("generated_file_globs") or []
    protected = wc.get("protected_globs") or []
    cap = core.limit(cfg, "clean_max_files", 100000)
    candidates, kept, count = [], [], 0
    for root, dirs, files in os.walk(target):
        rel_root = os.path.relpath(root, target)
        for d in list(dirs):
            full = os.path.join(root, d)
            rel = os.path.normpath(os.path.join(rel_root, d))
            if d in gdirs:
                size, n, _ = _bounded_size(full, cap)
                candidates.append({"path": rel, "type": "dir", "bytes": size, "files": n})
                dirs.remove(d)
                continue
        for f in files:
            count += 1
            if count > cap:
                raise core.ToolkitError("E_LIMIT", "More than %d files in target; narrow the target." % cap)
            rel = os.path.normpath(os.path.join(rel_root, f))
            if any(_glob_match(f, p) or _glob_match(rel, p) for p in protected):
                kept.append(rel)
                continue
            if any(_glob_match(f, g) for g in gfiles):
                candidates.append({"path": rel, "type": "file", "bytes": os.path.getsize(os.path.join(root, f))})
    total = sum(c["bytes"] for c in candidates)
    data = {"target": target, "dry_run": not args.yes, "candidates": candidates,
            "candidate_count": len(candidates), "bytes": total, "protected_kept": kept[:200],
            "_text": ["%s %s (%d bytes)" % (c["type"], c["path"], c["bytes"]) for c in candidates]
                     or ["nothing to clean"]}
    if not args.yes:
        data["hint"] = "Dry run. Re-run with --yes to delete the listed generated items."
        return core.ok("workspace clean", data, status="DRY RUN")
    core.ensure_approval("delete %d generated item(s) under %s" % (len(candidates), target), True, cfg)
    removed = []
    for c in candidates:
        p = os.path.join(target, c["path"])
        try:
            if c["type"] == "dir":
                shutil.rmtree(p)
            else:
                os.remove(p)
            removed.append(c["path"])
        except OSError as exc:
            data.setdefault("errors", []).append({"path": c["path"], "error": str(exc)})
    data["removed"] = removed
    return core.ok("workspace clean", data, status="CLEANED")


# ---------------------------------------------------------------------------
# dispatch table
# ---------------------------------------------------------------------------

COMMANDS = {
    ("host", "profile"): cmd_host_profile,
    ("host", "env"): cmd_host_env,
    ("host", "limits"): cmd_host_limits,
    ("sdk", "inventory"): cmd_sdk_inventory,
    ("sdk", "licenses"): cmd_sdk_licenses,
    ("sdk", "footprint"): cmd_sdk_footprint,
    ("sdk", "avds"): cmd_sdk_avds,
    ("sdk", "jdk-route"): cmd_sdk_jdk_route,
    ("sdk", "matrix"): cmd_sdk_matrix,
    ("build", "inspect"): cmd_build_inspect,
    ("build", "compat"): cmd_build_compat,
    ("integrity", "verify"): cmd_integrity_verify,
    ("integrity", "pins-init"): cmd_integrity_pins_init,
    ("integrity", "pins-add"): cmd_integrity_pins_add,
    ("integrity", "pins-verify"): cmd_integrity_pins_verify,
    ("selftest", "run"): cmd_selftest_run,
    ("selftest", "status"): cmd_selftest_status,
    ("workspace", "clean"): cmd_workspace_clean,
}
