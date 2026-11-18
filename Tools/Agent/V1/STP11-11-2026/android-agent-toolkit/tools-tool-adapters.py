#!/usr/bin/env python3
"""android-agent-toolkit :: external tool adapters (module 4 of 7).

Locates, version-checks and runs external Android/build/binary tools through the
whitelisted registry in asset-tool-registry.json. Rules enforced here:

  * argv lists only, never a shell; every call goes through core.run_command
  * binaries are resolved from PATH, SDK sub-paths (version-sorted, highest first),
    or jar paths from environment variables / configured search directories
  * a run is read-only only when its argument prefix matches read_only_args;
    every other invocation requires explicit approval (--yes)
  * when a tool declares allowed_first_args, any other first argument is refused
  * a missing tool is reported as MISSING with an install hint; it is never faked

Status vocabulary: FOUND, MISSING, WRONG VERSION, BROKEN (core.ST_*).
"""

import glob
import importlib.util
import os
import re
import shutil
import sys

import aatk_core as core

_REGISTRY_CACHE = {}


def registry():
    if "data" not in _REGISTRY_CACHE:
        data = core.load_json(core.resolve_asset("asset-tool-registry.json"), "tool registry")
        tools = data.get("tools") if isinstance(data, dict) else None
        if not isinstance(tools, list):
            raise core.ToolkitError("E_CONFIG", "asset-tool-registry.json has no 'tools' list")
        _REGISTRY_CACHE["data"] = {t["name"]: t for t in tools}
    return _REGISTRY_CACHE["data"]


def get_entry(name):
    reg = registry()
    if name not in reg:
        raise core.ToolkitError("E_NOT_FOUND", "Tool '%s' is not in the whitelist" % name,
                                hint="Run: tool list   (only registry tools may be executed)")
    return reg[name]


def sdk_roots(cfg):
    """Ordered, existing SDK roots from environment variables and configured fallbacks."""
    roots = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(var)
        if val:
            roots.append(os.path.expanduser(val))
    for spec in (cfg.get("sdk_root_fallbacks") or []):
        if spec.startswith("$"):
            val = os.environ.get(spec[1:])
            if val:
                roots.append(os.path.expanduser(val))
        else:
            roots.append(os.path.expanduser(spec))
    seen, out = set(), []
    for r in roots:
        real = os.path.abspath(r)
        if real not in seen and os.path.isdir(real):
            seen.add(real)
            out.append(real)
    return out


def search_dirs(cfg):
    base = [os.getcwd(), core.TOOLKIT_DIR]
    out = []
    for b in base:
        for d in (cfg.get("tool_search_dirs") or []):
            p = os.path.abspath(os.path.join(b, d))
            if os.path.isdir(p) and p not in out:
                out.append(p)
    return out


def _version_key(path):
    nums = re.findall(r"\d+", path)
    return tuple(int(n) for n in nums)


def locate(name, cfg):
    """Return (path, how) for a registry tool or (None, None)."""
    entry = get_entry(name)
    kind = entry.get("kind")
    if kind == "python-module":
        mod = (entry.get("exe_candidates") or [name])[0]
        spec = None
        try:
            spec = importlib.util.find_spec(mod)
        except (ImportError, ValueError):
            spec = None
        return (mod, "python-module") if spec else (None, None)
    if kind == "jar":
        env_name = entry.get("jar_env")
        if env_name and os.environ.get(env_name) and os.path.isfile(os.environ[env_name]):
            return os.path.abspath(os.environ[env_name]), "env:" + env_name
        for d in search_dirs(cfg):
            for pat in (entry.get("jar_globs") or []):
                hits = sorted(glob.glob(os.path.join(d, pat)), key=_version_key, reverse=True)
                hits = [h for h in hits if os.path.isfile(h)]
                if hits:
                    return os.path.abspath(hits[0]), "search-dir"
        return None, None
    for cand in (entry.get("exe_candidates") or [name]):
        found = shutil.which(cand)
        if found:
            return found, "path"
    for root in sdk_roots(cfg):
        for pat in (entry.get("sdk_globs") or []):
            hits = [h for h in glob.glob(os.path.join(root, pat)) if os.path.isfile(h)]
            hits.sort(key=_version_key, reverse=True)
            if hits:
                return hits[0], "sdk"
    return None, None


def build_argv(entry, target, args):
    kind = entry.get("kind")
    if kind == "python-module":
        return [sys.executable, "-m", target] + list(args)
    if kind == "jar":
        return ["java", "-jar", target] + list(args)
    return [target] + list(args)


def _timeout(cfg):
    return core.limit(cfg, "subprocess_timeout_sec", 120)


def version_probe(name, cfg):
    """Run the registry version probe and classify the tool."""
    entry = get_entry(name)
    target, how = locate(name, cfg)
    base = {"tool": name, "purpose": entry.get("purpose"), "kind": entry.get("kind")}
    if not target:
        return dict(base, status=core.ST_MISSING, path=None, version=None,
                    hint=entry.get("install_hint") or "Install the tool and make it visible on PATH or in the SDK.",
                    device_hint=entry.get("requires_device_hint"))
    if entry.get("kind") == "python-module":
        return dict(base, status=core.ST_FOUND, path=target, version=None, source=how,
                    note="python module importable; version not probed")
    args = entry.get("version_args") or ["--version"]
    res = core.run_command(build_argv(entry, target, args), timeout=min(_timeout(cfg), 30),
                           max_output=65536, retries=core.limit(cfg, "retries_readonly", 1))
    text = (res.get("stdout") or "") + "\n" + (res.get("stderr") or "")
    accept = entry.get("accept_exit") or [0]
    version = None
    pat = entry.get("version_pattern")
    if pat:
        m = re.search(pat, text)
        if m:
            version = m.group(1) if m.groups() else m.group(0)
    out = dict(base, path=target, source=how, version=version)
    if res.get("timed_out"):
        return dict(out, status=core.ST_BROKEN, error="version probe timed out")
    if res.get("error"):
        return dict(out, status=core.ST_BROKEN, error=res.get("error"))
    if res.get("exit_code") not in accept and version is None:
        return dict(out, status=core.ST_BROKEN, error="exit %s and no version string" % res.get("exit_code"))
    minv = entry.get("min_version")
    if minv and version:
        if _version_key(str(version))[:len(_version_key(str(minv)))] < _version_key(str(minv)):
            return dict(out, status=core.ST_WRONG_VERSION, min_version=minv,
                        hint="Installed %s, need >= %s" % (version, minv))
    if minv and not version:
        return dict(out, status=core.ST_BROKEN, error="version not parseable", min_version=minv)
    return dict(out, status=core.ST_FOUND)


def is_read_only(entry, args):
    for prefix in (entry.get("read_only_args") or []):
        if prefix and list(args[:len(prefix)]) == list(prefix):
            return True
    return False


def _check_args(args):
    if not args:
        raise core.ToolkitError("E_USAGE", "No arguments given for the tool run",
                                hint="Usage: tool run <name> -- <args...>")
    for a in args:
        if "\x00" in a or "\n" in a or "\r" in a:
            raise core.ToolkitError("E_INPUT", "Argument contains a control character: %r" % a)


def run_tool(name, args, cfg, assume_yes=False):
    entry = get_entry(name)
    _check_args(args)
    read_only = is_read_only(entry, args)
    allowed = entry.get("allowed_first_args") or []
    if allowed and args[0] not in allowed:
        raise core.ToolkitError("E_INPUT", "'%s' is not a whitelisted first argument for %s" % (args[0], name),
                                hint="Allowed: " + ", ".join(allowed))
    target, how = locate(name, cfg)
    if not target:
        raise core.ToolkitError("E_TOOL_MISSING", "%s is not installed or not visible" % name,
                                status=core.ST_MISSING,
                                hint=entry.get("install_hint") or "Install the tool and retry.",
                                data={"tool": name})
    if not read_only:
        core.ensure_approval("run %s %s (non-read-only tool invocation)" % (name, " ".join(args[:4])),
                             assume_yes, cfg)
    retries = core.limit(cfg, "retries_readonly", 1) if read_only else 0
    res = core.run_command(build_argv(entry, target, args), timeout=_timeout(cfg),
                           max_output=core.limit(cfg, "max_output_capture_bytes", 2097152),
                           retries=retries)
    data = {"tool": name, "argv_tail": args, "path": target, "source": how,
            "read_only": read_only, "exit_code": res.get("exit_code"),
            "stdout": res.get("stdout", ""), "stderr": res.get("stderr", ""),
            "timed_out": res.get("timed_out", False), "truncated": res.get("truncated", False),
            "duration_ms": res.get("duration_ms")}
    if res.get("error"):
        raise core.ToolkitError("E_FAILED", "Could not start %s: %s" % (name, res["error"]),
                                status=core.ST_BROKEN, data=data)
    if res.get("timed_out"):
        raise core.ToolkitError("E_FAILED", "%s timed out after %ss" % (name, _timeout(cfg)),
                                status=core.ST_BROKEN, data=data)
    if res.get("exit_code") not in (entry.get("accept_exit") or [0]):
        raise core.ToolkitError("E_FAILED", "%s exited with %s" % (name, res.get("exit_code")), data=data)
    out_lines = (res.get("stdout") or "").splitlines()
    cap = 200
    data["_text"] = ["%s exit %s (%d ms)" % (name, res.get("exit_code"), res.get("duration_ms") or 0)] + \
        out_lines[:cap] + (["... %d more line(s) in stdout" % (len(out_lines) - cap)] if len(out_lines) > cap else [])
    return data


def list_tools(cfg):
    rows = []
    for name in sorted(registry()):
        e = registry()[name]
        target, how = locate(name, cfg)
        rows.append({"name": name, "kind": e.get("kind"), "priority": e.get("priority"),
                     "installed": bool(target), "path": target, "purpose": e.get("purpose")})
    found = sum(1 for r in rows if r["installed"])
    return {"tools": rows, "count": len(rows), "installed": found,
            "_text": ["%d registry tools, %d installed on this host" % (len(rows), found)] +
                     ["  [%s] %-14s %s" % ("x" if r["installed"] else " ", r["name"], r["purpose"])
                      for r in rows]}


def adb_devices(cfg):
    """Device listing through the whitelisted adb entry (read-only)."""
    target, _ = locate("adb", cfg)
    if not target:
        return {"status": core.ST_MISSING, "devices": [],
                "hint": "adb is not installed; install platform-tools and retry."}
    res = core.run_command([target, "devices", "-l"], timeout=20, max_output=65536, retries=1)
    if res.get("error") or res.get("timed_out"):
        return {"status": core.ST_BROKEN, "devices": [], "error": res.get("error") or "timeout"}
    devices = []
    for line in (res.get("stdout") or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1],
                            "details": " ".join(parts[2:])})
    ready = [d for d in devices if d["state"] == "device"]
    status = core.ST_FOUND if ready else core.ST_DEVICE_REQUIRED
    return {"status": status, "devices": devices, "ready_count": len(ready),
            "hint": None if ready else "Connect a device with USB debugging, or start an emulator; "
                                       "no physical device is attached to this host."}


def selftest_entry(name, cfg):
    """Adapter view used by the selftest runner."""
    return version_probe(name, cfg)


# ---------------------------------------------------------------------------
# command handlers (dispatched by tools-android-agent-toolkit.py)
# ---------------------------------------------------------------------------

def cmd_tool_list(args, cfg):
    return core.ok("tool list", list_tools(cfg))


def cmd_tool_locate(args, cfg):
    probe = version_probe(args.name, cfg)
    return core.ok("tool locate", probe, status=probe["status"])


def cmd_tool_run(args, cfg):
    raw = list(args.tool_args or [])
    if raw[:1] == ["--"]:
        raw = raw[1:]
    data = run_tool(args.name, raw, cfg, assume_yes=args.yes)
    return core.ok("tool run", data)


def cmd_device_list(args, cfg):
    data = adb_devices(cfg)
    status = data.get("status", core.ST_FOUND)
    if status == core.ST_DEVICE_REQUIRED:
        raise core.ToolkitError("E_DEVICE_REQUIRED", "No attached device or emulator in 'device' state",
                                hint=data.get("hint") or "Attach a device with USB debugging or start an emulator.",
                                status=status, data=data)
    if status == core.ST_MISSING:
        raise core.ToolkitError("E_TOOL_MISSING", "adb is not installed or not visible",
                                hint=data.get("hint"), status=status, data=data)
    return core.ok("device list", data, status=status)


COMMANDS = {
    ("tool", "list"): cmd_tool_list,
    ("tool", "locate"): cmd_tool_locate,
    ("tool", "run"): cmd_tool_run,
    ("device", "list"): cmd_device_list,
}
