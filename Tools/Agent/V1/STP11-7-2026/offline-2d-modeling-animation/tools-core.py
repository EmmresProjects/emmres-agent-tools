#!/usr/bin/env python3
"""tools-core.py — shared foundation for the offline-2d-modeling-animation Skill Tool.

Provides: structured errors, JSON/CSV/text IO with atomic writes, checksums,
deterministic RNG, bounded subprocess runner, executable discovery, naming
rules, path safety, config handling, limits, retries, temp dirs, and small
validation helpers. Loaded first; every other tools- module builds on it.
"""
import csv
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TOOL_NAME = "offline-2d-modeling-animation"
TOOL_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent

IMAGE_EXTS = (".png", ".bmp", ".tga", ".ppm", ".pgm", ".gif", ".webp", ".jpg", ".jpeg", ".tif", ".tiff")

# ---------------------------------------------------------------- errors ----

class StudioError(Exception):
    """Structured, recoverable error carrying a machine-readable code."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ApprovalRequired(StudioError):
    def __init__(self, action, details=None):
        super().__init__("approval_required",
                         "action '%s' is destructive or privileged; re-run with --yes after review" % action,
                         dict(details or {}, action=action))


class GateBlocked(StudioError):
    def __init__(self, message, blockers):
        super().__init__("gate_blocked", message, {"blockers": blockers})


def err(code, message, **details):
    return StudioError(code, message, details)


# ------------------------------------------------------------------ paths ----

def is_safe_relative(p):
    """True when p is a relative path without drive letters, ~, or traversal."""
    if p is None:
        return False
    s = str(p)
    if not s:
        return False
    if s.startswith("~") or os.path.isabs(s) or re.match(r"^[A-Za-z]:[\\/]", s):
        return False
    if s.lower().startswith("file:") or "://" in s:
        return False
    parts = normalize_path(s).split("/")
    return ".." not in parts


def normalize_path(p):
    return str(p).replace("\\", "/")


def to_rel(p, root):
    try:
        return normalize_path(os.path.relpath(str(p), str(root)))
    except ValueError:
        return normalize_path(p)


def ensure_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return Path(path)


def check_under(path, root, what="path"):
    rp = Path(path).resolve()
    rr = Path(root).resolve()
    if rr != rp and rr not in rp.parents:
        raise err("path_outside_scope", "%s '%s' is outside '%s'" % (what, path, root))
    return rp


# -------------------------------------------------------------------- io ----

def _atomic_write(path, data, mode="wb"):
    path = Path(path)
    ensure_parent(path)
    tmp = path.with_name(path.name + ".tmp-write")
    with open(tmp, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_text(path, text):
    _atomic_write(path, text.encode("utf-8"))
    return str(path)


def load_text(path, required=True):
    p = Path(path)
    if not p.is_file():
        if required:
            raise err("file_not_found", "file not found: %s" % path, path=str(path))
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def save_json(path, data):
    out = json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)
    _atomic_write(path, out.encode("utf-8"))
    return str(path)


def load_json(path, required=True):
    p = Path(path)
    if not p.is_file():
        if required:
            raise err("file_not_found", "JSON file not found: %s" % path, path=str(path))
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise err("invalid_json", "invalid JSON in %s: %s" % (path, e), path=str(path))


def load_data_file(path):
    """Load .json, .jsonl or .csv into python data (list of dicts for row formats)."""
    p = Path(path)
    if not p.is_file():
        raise err("file_not_found", "data file not found: %s" % path, path=str(path))
    suf = p.suffix.lower()
    if suf == ".json":
        return load_json(path)
    if suf == ".jsonl":
        rows = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                raise err("invalid_json", "bad JSONL row %d in %s" % (i, path))
        return rows
    if suf == ".csv":
        with open(p, newline="", encoding="utf-8-sig") as f:
            return [dict(r) for r in csv.DictReader(f)]
    raise err("unsupported_format", "unsupported data format '%s' (use .json/.jsonl/.csv)" % suf, path=str(path))


def save_rows(path, rows, fields=None):
    """Save list-of-dicts as .json, .jsonl or .csv depending on extension."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".csv":
        fields = fields or (list(rows[0].keys()) if rows else [])
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
        return save_text(path, buf.getvalue())
    if suf == ".jsonl":
        return save_text(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return save_json(path, rows)


def read_bytes(path):
    p = Path(path)
    if not p.is_file():
        raise err("file_not_found", "file not found: %s" % path, path=str(path))
    return p.read_bytes()


def write_bytes(path, data):
    _atomic_write(path, data)
    return str(path)


# ------------------------------------------------------------- validation ----

def require(cond, code, message, **details):
    if not cond:
        raise err(code, message, **details)


def require_keys(d, keys, what="input"):
    if not isinstance(d, dict):
        raise err("invalid_input", "%s must be an object" % what)
    missing = [k for k in keys if k not in d]
    if missing:
        raise err("missing_field", "%s missing required field(s): %s" % (what, ", ".join(missing)),
                  missing=missing)


def req_str(d, key, what="input", default=None, choices=None):
    v = d.get(key, default)
    if v is None:
        raise err("missing_field", "%s missing required field '%s'" % (what, key), field=key)
    if not isinstance(v, str) or not v.strip():
        raise err("invalid_input", "%s field '%s' must be a non-empty string" % (what, key), field=key)
    if choices and v not in choices:
        raise err("invalid_input", "%s field '%s' must be one of: %s" % (what, key, ", ".join(choices)), field=key)
    return v


def req_num(d, key, what="input", default=None, lo=None, hi=None, integer=False):
    v = d.get(key, default)
    if v is None:
        raise err("missing_field", "%s missing required field '%s'" % (what, key), field=key)
    try:
        n = int(v) if integer else float(v)
    except (TypeError, ValueError):
        raise err("invalid_input", "%s field '%s' must be a number" % (what, key), field=key)
    if lo is not None and n < lo:
        raise err("out_of_range", "%s field '%s' must be >= %s" % (what, key, lo), field=key)
    if hi is not None and n > hi:
        raise err("out_of_range", "%s field '%s' must be <= %s" % (what, key, hi), field=key)
    return n


def req_bool(d, key, what="input", default=False):
    v = d.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false", "1", "0", "yes", "no"):
        return v.lower() in ("true", "1", "yes")
    raise err("invalid_input", "%s field '%s' must be a boolean" % (what, key), field=key)


def parse_int(value, name, lo=None, hi=None):
    try:
        n = int(str(value), 0)
    except (TypeError, ValueError):
        raise err("invalid_input", "%s must be an integer (got %r)" % (name, value))
    if lo is not None and n < lo:
        raise err("out_of_range", "%s must be >= %s" % (name, lo))
    if hi is not None and n > hi:
        raise err("out_of_range", "%s must be <= %s" % (name, hi))
    return n


# ---------------------------------------------------------------- limits ----

DEFAULT_CONFIG = {
    "_comment": [
        "offline-2d-modeling-animation configuration",
        "All paths are relative to where you run the command unless absolute.",
        "External applications are optional; every command degrades gracefully.",
        "Remove keys you do not need; missing keys fall back to these defaults."
    ],
    "limits": {
        "max_pixels": 40000000,
        "max_frames": 20000,
        "max_rows": 200000,
        "subprocess_timeout": 600,
        "subprocess_retries": 0,
        "sheet_max_tiles": 20000
    },
    "apps": {
        "krita": None, "inkscape": None, "aseprite": None, "blender": None,
        "opentoonz": None, "synfig": None, "ffmpeg": None, "cwebp": None, "magick": None
    },
    "export": {
        "h264_crf": "18",
        "h264_preset": "medium",
        "master_codec": "ffv1",
        "master_container": "mkv",
        "png_compression": 6
    },
    "neural": {
        "runtimes": {},
        "default_timeout": 900
    },
    "review": {"contact_columns": 6, "burnin_color": [255, 255, 0]},
    "disk": {"safety_margin": 0.25, "png_compression_factor": 0.45},
    "packages": {"exclude_cache_dirs": ["cache", "caches", ".cache", "thumbs", "proxies"]}
}


def deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    cfg = dict(DEFAULT_CONFIG)
    source = None
    if path:
        p = Path(path)
        if not p.is_file():
            raise err("file_not_found", "config file not found: %s" % path, path=str(path))
        source = str(p)
    else:
        for cand in ("studio-config.json", "studio.json"):
            if Path(cand).is_file():
                source = cand
                break
    if source:
        user = load_json(source)
        require(isinstance(user, dict), "invalid_config", "config root must be a JSON object")
        cfg = deep_merge(cfg, user)
        cfg["_source"] = source
    return cfg


def cfg_get(cfg, dotted, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def asset(name):
    """Resolve an asset-<name> file shipped inside this Skill Tool folder."""
    return ROOT / ("asset-" + name)


# ------------------------------------------------------------------ misc ----

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_slug():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def md5_str(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def list_files(path, exts=None, recursive=False):
    p = Path(path)
    if p.is_file():
        return [p]
    require(p.is_dir(), "file_not_found", "directory not found: %s" % path, path=str(path))
    it = p.rglob("*") if recursive else p.glob("*")
    out = [f for f in sorted(it) if f.is_file()]
    if exts:
        out = [f for f in out if f.suffix.lower() in exts]
    return out


def list_images(path, recursive=False):
    return list_files(path, IMAGE_EXTS, recursive)


def slugify(name, default="item"):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or default


def with_retries(fn, attempts=1, backoff=0.5, description="operation"):
    attempts = max(1, int(attempts))
    last = None
    for i in range(attempts):
        try:
            return fn()
        except StudioError as e:
            last = e
            if i + 1 < attempts:
                time.sleep(backoff * (i + 1))
    raise last


@contextmanager
def temp_dir(prefix="studio-"):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------- rng ----

class RNG(object):
    """Deterministic splitmix64-based generator (stable across machines)."""

    def __init__(self, seed):
        if isinstance(seed, str):
            seed = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
        self.state = (int(seed) ^ 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF

    def _next(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def random(self):
        return (self._next() >> 11) / float(1 << 53)

    def int(self, a, b):
        if b <= a:
            return a
        return a + int(self.random() * (b - a + 1))

    def float(self, a, b):
        return a + self.random() * (b - a)

    def choice(self, seq):
        return seq[self.int(0, len(seq) - 1)]

    def gauss(self, mu=0.0, sigma=1.0):
        u1 = max(self.random(), 1e-12)
        u2 = self.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)

    def chance(self, p):
        return self.random() < p

    def shuffle(self, lst):
        for i in range(len(lst) - 1, 0, -1):
            j = self.int(0, i)
            lst[i], lst[j] = lst[j], lst[i]
        return lst


# ------------------------------------------------------------- subprocess ----

def find_executable(name, configured=None):
    """Resolve an external tool: explicit config path wins, then PATH lookup."""
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
        found = shutil.which(str(configured))
        if found:
            return found
        raise err("app_not_found", "configured path for '%s' does not exist: %s" % (name, configured))
    return shutil.which(name)


def detect_apps(cfg):
    """Return {app: {path, version}} for optional external tools. Never executes anything."""
    out = {}
    for app, conf in (cfg.get("apps") or {}).items():
        try:
            path = find_executable(app, conf)
        except StudioError:
            out[app] = {"path": None, "version": None, "error": "configured path missing"}
            continue
        out[app] = {"path": path, "version": None}
    return out


def run_command(argv, timeout=None, cwd=None, cfg=None, label=None):
    """Bounded, shell-free subprocess runner. Returns structured result."""
    require(isinstance(argv, (list, tuple)) and argv, "invalid_input",
            "command must be a non-empty argument list")
    require(all(isinstance(a, str) for a in argv), "invalid_input",
            "command arguments must be strings (no shell features)")
    cfg = cfg or {}
    timeout = timeout if timeout is not None else cfg_get(cfg, "limits.subprocess_timeout", 600)
    t0 = time.time()
    try:
        proc = subprocess.run(list(argv), cwd=str(cwd) if cwd else None,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=float(timeout))
        return {
            "argv": list(argv), "returncode": proc.returncode, "ok": proc.returncode == 0,
            "stdout_tail": proc.stdout.decode("utf-8", "replace")[-4000:],
            "stderr_tail": proc.stderr.decode("utf-8", "replace")[-4000:],
            "duration_s": round(time.time() - t0, 3), "timed_out": False,
            "label": label or argv[0],
        }
    except subprocess.TimeoutExpired:
        return {
            "argv": list(argv), "returncode": None, "ok": False, "stdout_tail": "",
            "stderr_tail": "timeout after %ss" % timeout, "duration_s": round(time.time() - t0, 3),
            "timed_out": True, "label": label or argv[0],
        }
    except FileNotFoundError:
        return {
            "argv": list(argv), "returncode": None, "ok": False, "stdout_tail": "",
            "stderr_tail": "executable not found: %s" % argv[0], "duration_s": 0.0,
            "timed_out": False, "label": label or argv[0],
        }
    except OSError as e:
        return {
            "argv": list(argv), "returncode": None, "ok": False, "stdout_tail": "",
            "stderr_tail": "OS error: %s" % e, "duration_s": 0.0, "timed_out": False,
            "label": label or argv[0],
        }


def probe_version(argv, timeout=15):
    r = run_command(list(argv) + ["-version"] if argv and argv[0].endswith("ffmpeg")
                    else list(argv) + ["--version"], timeout=timeout)
    first = (r["stdout_tail"] or r["stderr_tail"]).strip().splitlines()
    return (first[0][:160] if first else None), r["ok"]


# ---------------------------------------------------------------- naming ----

NAMING_RULES = {
    "pattern_default": "{project}-{seq}-{shot}-{task}-v{version:03d}",
    "frame_pattern_default": "{project}-{seq}-{shot}-{task}-v{version:03d}.{frame:04d}.{ext}",
    "fields": ["project", "seq", "shot", "task", "version", "frame", "res", "variant", "ext"],
    "case": "lower",
    "separator": "-",
    "forbidden": r"[^A-Za-z0-9._-]+",
}


def render_name(pattern, fields):
    class Safedict(dict):
        def __missing__(self, k):
            return "-"
    data = {}
    for k, v in (fields or {}).items():
        if v is None:
            continue
        data[k] = v if isinstance(v, (int, float)) else str(v)
    out = pattern.format_map(Safedict(data))
    out = re.sub(NAMING_RULES["forbidden"], "-", out)
    out = re.sub(r"-{2,}", "-", out).strip("-.")
    return out


def check_name(name, rules=None):
    rules = rules or NAMING_RULES
    problems = []
    if re.search(rules["forbidden"], name):
        problems.append("contains characters outside [A-Za-z0-9._-]")
    if name != name.lower() and rules.get("case") == "lower":
        problems.append("uppercase letters not allowed by convention")
    if name.startswith(("-", ".")) or name.endswith(("-", ".")):
        problems.append("leading/trailing separator")
    return problems


# --------------------------------------------------------------- machine ----

def machine_report():
    try:
        import resource
        ram_kb = resource.getrlimit(resource.RLIMIT_AS)[1]
    except Exception:
        ram_kb = None
    total, used, free = shutil.disk_usage(os.getcwd())
    return {
        "os": platform.system(), "os_release": platform.release(),
        "machine": platform.machine(), "processor": platform.processor() or None,
        "python": platform.python_version(), "python_impl": platform.python_implementation(),
        "cpu_count": os.cpu_count(),
        "ram_limit_hint_kb": ram_kb,
        "cwd_free_bytes": free, "cwd_total_bytes": total,
        "hostname_stub": "workstation",
        "checked_at": utc_now(),
    }


def check_limits(cfg, pixels=None, frames=None, rows=None):
    lim = cfg.get("limits", {}) if cfg else {}
    if pixels is not None and pixels > lim.get("max_pixels", 40000000):
        raise err("limit_exceeded",
                  "image is %d pixels; limit is %d (raise limits.max_pixels if intended)"
                  % (pixels, lim.get("max_pixels", 40000000)))
    if frames is not None and frames > lim.get("max_frames", 20000):
        raise err("limit_exceeded",
                  "sequence has %d frames; limit is %d (raise limits.max_frames if intended)"
                  % (frames, lim.get("max_frames", 20000)))
    if rows is not None and rows > lim.get("max_rows", 200000):
        raise err("limit_exceeded", "table has %d rows; limit is %d" % (rows, lim.get("max_rows", 200000)))


def result(**kw):
    return kw
