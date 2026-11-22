#!/usr/bin/env python3
"""android-agent-toolkit :: core runtime (module 1 of 7, loaded first).

Shared infrastructure for every other tools-*.py module:
  * structured result envelope (JSON or human text) and the catalog status vocabulary
  * ToolkitError with stable error codes and process exit codes
  * configuration loading: asset-default-config.json deep-merged with an optional user config
  * asset resolution relative to the toolkit folder (never absolute, never CWD-dependent)
  * run_command(): the ONLY subprocess path - no shell, argv lists only, timeout,
    output capture caps, optional single retry for read-only commands
  * ensure_approval(): gate for destructive / privileged / signing / device-state actions
  * path validation and hashing helpers

Python 3.8+ standard library only.
"""

import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
import zlib

TOOLKIT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLKIT_NAME = "android-agent-toolkit"
TOOLKIT_VERSION = "1.0.0"
PYTHON_MIN = (3, 8)

# Catalog status vocabulary (Appendix C / D of the Android Agent Skill Catalog)
ST_FOUND = "FOUND"
ST_MISSING = "MISSING"
ST_WRONG_VERSION = "WRONG VERSION"
ST_BROKEN = "BROKEN"
ST_LICENSE_REQUIRED = "LICENSE REQUIRED"
ST_DEVICE_REQUIRED = "DEVICE REQUIRED"
ST_NETWORK_REQUIRED = "NETWORK REQUIRED"
ST_HOST_FEATURE_REQUIRED = "HOST FEATURE REQUIRED"
STATUSES = [ST_FOUND, ST_MISSING, ST_WRONG_VERSION, ST_BROKEN, ST_LICENSE_REQUIRED,
            ST_DEVICE_REQUIRED, ST_NETWORK_REQUIRED, ST_HOST_FEATURE_REQUIRED]

# Error codes -> process exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_APPROVAL = 3
EXIT_MISSING = 4

ERROR_EXIT = {
    "E_USAGE": EXIT_USAGE,
    "E_INPUT": EXIT_USAGE,
    "E_CONFIG": EXIT_USAGE,
    "E_NOT_FOUND": EXIT_ERROR,
    "E_PARSE": EXIT_ERROR,
    "E_LIMIT": EXIT_ERROR,
    "E_APPROVAL": EXIT_APPROVAL,
    "E_TOOL_MISSING": EXIT_MISSING,
    "E_DEVICE_REQUIRED": EXIT_MISSING,
    "E_NETWORK_REQUIRED": EXIT_MISSING,
    "E_LICENSE_REQUIRED": EXIT_MISSING,
    "E_HOST_REQUIRED": EXIT_MISSING,
    "E_FAILED": EXIT_ERROR,
    "E_INTERNAL": EXIT_ERROR,
}


class ToolkitError(Exception):
    """Structured, machine-readable error. `status` uses the catalog vocabulary
    when the failure is a missing requirement (tool/device/network/license/host)."""

    def __init__(self, code, message, hint=None, status=None, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.status = status
        self.data = data or {}

    @property
    def exit_code(self):
        return ERROR_EXIT.get(self.code, EXIT_ERROR)

    def to_dict(self):
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.status:
            d["status"] = self.status
        if self.data:
            d["data"] = self.data
        return d


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

def ok(command, data=None, warnings=None, status="OK"):
    return {"ok": True, "command": command, "status": status,
            "data": data if data is not None else {},
            "warnings": list(warnings or [])}


def envelope_error(command, err):
    return {"ok": False, "command": command,
            "status": err.status or "ERROR", "error": err.to_dict()}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _deep_merge(base, overlay):
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def resolve_asset(name):
    """Absolute path of a packaged asset, resolved next to this module."""
    p = os.path.join(TOOLKIT_DIR, name)
    if not os.path.isfile(p):
        raise ToolkitError("E_CONFIG", "Packaged asset is missing: %s/%s" % (TOOLKIT_NAME, name),
                           hint="Re-extract the toolkit folder; every asset ships inside it.")
    return p


def load_json(path, what="JSON file"):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ToolkitError("E_NOT_FOUND", "%s not found: %s" % (what, path))
    except json.JSONDecodeError as exc:
        raise ToolkitError("E_PARSE", "%s is not valid JSON: %s (%s)" % (what, path, exc),
                           hint="Fix the JSON syntax; the toolkit never edits assets in place silently.")
    except OSError as exc:
        raise ToolkitError("E_INPUT", "Cannot read %s: %s" % (path, exc))


def load_config(user_path=None):
    cfg = load_json(resolve_asset("asset-default-config.json"), "default config")
    if user_path:
        user = load_json(user_path, "user config")
        if not isinstance(user, dict):
            raise ToolkitError("E_CONFIG", "User config must be a JSON object: %s" % user_path)
        cfg = _deep_merge(cfg, user)
    return cfg


def limit(cfg, key, default=None):
    val = (cfg.get("limits") or {}).get(key, default)
    return default if val is None else val


def expand_path(spec):
    """Expand $VAR and ~ in configured path candidates."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(spec)))


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def check_input_path(path, kind="file", max_bytes=None, cfg=None, label=None):
    """Validate an input path before any work begins. kind: file|dir|any."""
    label = label or "input"
    if not path:
        raise ToolkitError("E_INPUT", "%s path is empty" % label)
    if "\x00" in path:
        raise ToolkitError("E_INPUT", "%s path contains a NUL byte" % label)
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(p):
        raise ToolkitError("E_NOT_FOUND", "%s does not exist: %s" % (label, p),
                           hint="Check the path; relative paths resolve against the current directory.")
    if kind == "file" and not os.path.isfile(p):
        raise ToolkitError("E_INPUT", "%s is not a regular file: %s" % (label, p))
    if kind == "dir" and not os.path.isdir(p):
        raise ToolkitError("E_INPUT", "%s is not a directory: %s" % (label, p))
    if kind == "file" or (kind == "any" and os.path.isfile(p)):
        size = os.path.getsize(p)
        cap = max_bytes if max_bytes is not None else limit(cfg or {}, "max_input_bytes", 1 << 31)
        if size > cap:
            raise ToolkitError("E_LIMIT", "%s exceeds the %d byte input limit (%d bytes): %s"
                               % (label, cap, size, p),
                               hint="Raise limits.max_input_bytes in your config if this file is authorized for analysis.")
    return p


def check_output_path(path, kind="file", overwrite=False, must_not_exist=False, label=None):
    """Validate an output path. Refuses to silently overwrite unless allowed."""
    label = label or "output"
    if not path:
        raise ToolkitError("E_INPUT", "%s path is empty" % label)
    p = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(p)  # the leaf (file or directory) is created by the caller
    if parent and not os.path.isdir(parent):
        raise ToolkitError("E_NOT_FOUND", "%s parent directory does not exist: %s" % (label, parent),
                           hint="Create the directory first; the toolkit only creates the final leaf.")
    if os.path.exists(p):
        if must_not_exist:
            raise ToolkitError("E_INPUT", "%s already exists: %s" % (label, p))
        if kind == "dir" and os.path.isdir(p) and os.listdir(p) and not overwrite:
            raise ToolkitError("E_INPUT", "%s directory is not empty: %s" % (label, p),
                               hint="Choose an empty directory or re-run with --yes to approve overwriting contents.")
        if kind == "file" and os.path.isdir(p):
            raise ToolkitError("E_INPUT", "%s path is a directory: %s" % (label, p))
        if kind == "file" and not overwrite:
            raise ToolkitError("E_INPUT", "%s file exists and overwrite was not approved: %s" % (label, p),
                               hint="Re-run with --yes to overwrite, or pick another path.")
    return p


def is_inside(child, parent):
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------

def ensure_approval(action_desc, assume_yes, cfg, force_error=True):
    """Approval gate for destructive/irreversible/privileged/publishing/financial/
    credential/security-sensitive actions. Returns the approval mode string.
    Non-interactive sessions must pass --yes explicitly (configurable)."""
    if assume_yes:
        return "approved-by-flag"
    if sys.stdin.isatty() and sys.stderr.isatty():
        sys.stderr.write("APPROVAL REQUIRED: %s\nProceed? Type 'yes' to continue: " % action_desc)
        sys.stderr.flush()
        try:
            answer = sys.stdin.readline().strip().lower()
        except (KeyboardInterrupt, EOFError):
            answer = ""
        if answer in ("yes", "y"):
            return "approved-interactively"
    if force_error:
        gate = (cfg.get("approval") or {}).get("gate_message",
                                               "Re-run with --yes to approve this action.")
        raise ToolkitError("E_APPROVAL",
                           "Action not approved: %s" % action_desc,
                           hint=gate + " Destructive, signing, credential and device-state actions are never auto-approved.")
    return "denied"


# ---------------------------------------------------------------------------
# Hardened subprocess execution (the ONLY external execution path)
# ---------------------------------------------------------------------------

def run_command(argv, timeout=120, max_output=2 * 1024 * 1024, cwd=None, env=None,
                retries=0, stdin_data=None):
    """Run an argv list without a shell. Enforces timeout, output caps, and
    optional retries for read-only commands. Returns a structured result and
    never raises for non-zero exit codes (callers classify)."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ToolkitError("E_INTERNAL", "run_command requires a non-empty argv list")
    argv = [str(a) for a in argv]
    timeout = max(1, int(timeout))
    max_output = max(4096, int(max_output))
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        result = {"argv": argv, "exit_code": None, "stdout": "", "stderr": "",
                  "timed_out": False, "truncated": False, "duration_ms": 0}
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=cwd, env=env, shell=False)
        except FileNotFoundError:
            result["error"] = "executable-not-found"
            return result
        except PermissionError:
            result["error"] = "permission-denied"
            return result
        except OSError as exc:
            result["error"] = "os-error: %s" % exc
            return result

        outs = {}

        def _reader(stream, key):
            buf = bytearray()
            truncated = False
            try:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    if len(buf) + len(chunk) > max_output:
                        buf.extend(chunk[:max(0, max_output - len(buf))])
                        truncated = True
                        # keep draining briefly so the child does not block, then stop
                        try:
                            while stream.read(1024 * 1024):
                                pass
                        except (OSError, ValueError):
                            pass
                        break
                    buf.extend(chunk)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            outs[key] = (bytes(buf), truncated)

        t_out = threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True)
        t_out.start()
        t_err.start()
        try:
            if stdin_data is not None:
                proc.stdin.write(stdin_data)
            proc.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass

        fired = {"timeout": False}

        def _on_timeout(p=proc, flag=fired):
            flag["timeout"] = True
            _kill(p)

        timer = threading.Timer(timeout, _on_timeout)
        timer.daemon = True
        timer.start()
        try:
            proc.wait()
            timer.cancel()
        except Exception:
            timer.cancel()
            _kill(proc)
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        result["timed_out"] = fired["timeout"]
        if result["timed_out"]:
            _kill(proc)
            proc.wait()
        so, tr1 = outs.get("stdout", (b"", False))
        se, tr2 = outs.get("stderr", (b"", False))
        result["stdout"] = so.decode("utf-8", "replace")
        result["stderr"] = se.decode("utf-8", "replace")
        result["truncated"] = tr1 or tr2
        result["exit_code"] = proc.returncode
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        if retries > 0 and attempt <= retries and (result["timed_out"] or result.get("error")):
            continue
        return result


def _kill(proc):
    try:
        proc.kill()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Hashing and IO helpers
# ---------------------------------------------------------------------------

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def adler32_file(path, chunk=1 << 20):
    a = 1
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            a = zlib.adler32(b, a)
    return a & 0xFFFFFFFF


def read_bytes(path, max_bytes=None, offset=0, length=None):
    """Read a file, enforcing max_bytes against the real size (no oversize pre-allocation)."""
    size = os.path.getsize(path)
    if max_bytes is not None and length is None and size - offset > max_bytes:
        raise ToolkitError("E_LIMIT", "Read exceeded %d bytes: %s" % (max_bytes, path))
    with open(path, "rb") as fh:
        if offset:
            fh.seek(offset)
        if length is not None:
            return fh.read(length)
        return fh.read(max(0, size - offset))


def write_bytes(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def json_dumps(obj, indent=2):
    return json.dumps(obj, indent=indent, ensure_ascii=False, default=str)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def open_zip(path, mode="r"):
    try:
        return zipfile.ZipFile(path, mode)
    except zipfile.BadZipFile as exc:
        raise ToolkitError("E_PARSE", "Not a readable ZIP container: %s (%s)" % (path, exc))
    except OSError as exc:
        raise ToolkitError("E_INPUT", "Cannot open ZIP: %s (%s)" % (path, exc))


def glob_first(dirs, pattern):
    """First existing match of `pattern` across `dirs`, version-sorted descending."""
    hits = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for m in fnmatch.filter(sorted(os.listdir(d), reverse=True), pattern):
            hits.append(os.path.join(d, m))
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Text rendering of the result envelope (used when --json is not requested)
# ---------------------------------------------------------------------------

def render_text(env, verbose=False, max_items=40):
    lines = []
    cmd = env.get("command", "?")
    if env.get("ok"):
        status = env.get("status", "OK")
        lines.append("command: %s" % cmd)
        lines.append("status: %s" % status)
        data = env.get("data") or {}
        text = data.get("_text") if isinstance(data, dict) else None
        if text:
            # drop body lines that merely repeat the header status (avoid "status: X" twice)
            lines.extend(t for t in text if t.strip() != "status: %s" % status)
        else:
            if isinstance(data, dict) and data.get("status") == status:
                data = {k: v for k, v in data.items() if k != "status"}  # already shown in header
            lines.extend(_render_value(data, indent=1, verbose=verbose, max_items=max_items))
        for w in env.get("warnings") or []:
            lines.append("warning: %s" % w)
    else:
        err = env.get("error") or {}
        lines.append("command: %s" % cmd)
        lines.append("status: %s" % env.get("status", "ERROR"))
        lines.append("error: [%s] %s" % (err.get("code"), err.get("message")))
        if err.get("hint"):
            lines.append("hint: %s" % err["hint"])
        edata = err.get("data") if isinstance(err.get("data"), dict) else {}
        if edata.get("_text"):
            lines.extend(edata["_text"])  # report body carried by the error (e.g. self-test table)
        elif verbose and err.get("data"):
            lines.extend(_render_value(err["data"], indent=1, verbose=True, max_items=max_items))
    return "\n".join(lines)


def _render_value(v, indent=0, verbose=False, max_items=40):
    pad = "  " * indent
    out = []
    if isinstance(v, dict):
        for k, val in v.items():
            if isinstance(val, (dict, list)):
                out.append("%s%s:" % (pad, k))
                out.extend(_render_value(val, indent + 1, verbose, max_items))
            else:
                out.append("%s%s: %s" % (pad, k, _scalar(val)))
    elif isinstance(v, list):
        shown = v if verbose else v[:max_items]
        for item in shown:
            if isinstance(item, (dict, list)):
                out.extend(_render_value(item, indent, verbose, max_items))
                out.append("%s-" % pad)
            else:
                out.append("%s- %s" % (pad, _scalar(item)))
        if not verbose and len(v) > max_items:
            out.append("%s... %d more items (use --json or --verbose)" % (pad, len(v) - max_items))
    else:
        out.append("%s%s" % (pad, _scalar(v)))
    while out and out[-1].strip() in ("-", ""):
        out.pop()
    return out


def _scalar(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if x is None:
        return "-"
    return str(x)


# ---------------------------------------------------------------------------
# Module loading helper (siblings are named tools-*.py, so they are loaded by
# path with importlib; the entry point loads them in documented order).
# ---------------------------------------------------------------------------

def load_sibling(filename, module_name):
    path = os.path.join(TOOLKIT_DIR, filename)
    if not os.path.isfile(path):
        raise ToolkitError("E_CONFIG", "Toolkit module missing: %s/%s" % (TOOLKIT_NAME, filename),
                           hint="Re-extract the toolkit folder; do not move individual files.")
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def sibling_module_name(filename):
    return "aatk_" + re.sub(r"[^a-z0-9]+", "_", filename.lower()).strip("_")


def get_sibling(filename):
    """Return an already-loaded sibling module, loading it once if needed."""
    name = sibling_module_name(filename)
    mod = sys.modules.get(name)
    if mod is None:
        mod = load_sibling(filename, name)
    return mod
