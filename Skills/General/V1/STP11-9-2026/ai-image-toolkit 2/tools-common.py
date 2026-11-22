#!/usr/bin/env python3
"""Shared helpers for the ai-image-toolkit Skill Tool.

Imported by the other tools-* modules through load_sibling(). Holds the error model,
parameter validation, output-path safety (no silent overwrite), config loading,
colour parsing and small file utilities. Standard library only.
"""
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLKIT_VERSION = "1.0.0"

EXIT_CODES = {
    "ok": 0,
    "invalid_input": 2,
    "approval_required": 3,
    "dependency_missing": 4,
    "unavailable": 5,
    "runtime_error": 6,
    "not_found": 7,
    "cancelled": 8,
    "timeout": 9,
}


class ToolError(Exception):
    """Structured, user-facing error. `code` must be a key of EXIT_CODES."""

    def __init__(self, code, message, field=None, hint=None):
        super().__init__(message)
        self.code = code if code in EXIT_CODES else "runtime_error"
        self.message = message
        self.field = field
        self.hint = hint

    def as_dict(self):
        d = {"code": self.code, "message": self.message}
        if self.field:
            d["field"] = self.field
        if self.hint:
            d["hint"] = self.hint
        return d


# ----------------------------------------------------------------- assets/config

def read_text_file(path, field=None):
    """Read a UTF-8 text file; binary or undecodable input becomes invalid_input."""
    p = Path(path)
    if not p.is_file():
        raise ToolError("not_found", f"file not found: {path}", field=field)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError("invalid_input", f"{p.name} is not UTF-8 text (binary file?)", field=field,
                        hint="Pass a plain-text file, or use the image.* operations for images.")


def read_json(path):
    text = read_text_file(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ToolError("invalid_input", f"invalid JSON in {path}: {e.msg} (line {e.lineno})")


def asset_path(name):
    p = HERE / name
    if not p.is_file():
        raise ToolError("not_found", f"asset file missing: {name}",
                        hint="Restore the asset-* file beside the tools-* files.")
    return p


def load_asset(name):
    return read_json(asset_path(name))


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(override=None):
    cfg = load_asset("asset-config-default.json")
    if override:
        cfg = deep_merge(cfg, read_json(override))
    return cfg


_MODULES = {}


def load_sibling(filename):
    """Import a tools-* sibling by filename (hyphens are allowed in filenames)."""
    if filename not in _MODULES:
        mod_name = "aiit_" + re.sub(r"[^a-z0-9]", "_", filename[:-3].lower())
        spec = importlib.util.spec_from_file_location(mod_name, HERE / filename)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        _MODULES[filename] = mod
    return _MODULES[filename]


# ----------------------------------------------------------------- operation specs

def P(name, type_, required=False, default=None, help="", choices=None, min=None, max=None):
    """Declare one parameter of an operation.

    Types: str, int, float, bool, choice, file, files, dir, outfile, json, list, intlist, floatlist.
    """
    d = {"name": name, "type": type_, "required": required, "help": help}
    if default is not None:
        d["default"] = default
    if choices:
        d["choices"] = list(choices)
    if min is not None:
        d["min"] = min
    if max is not None:
        d["max"] = max
    return d


def op(fn, params, summary, needs=None):
    return {"fn": fn, "params": params, "summary": summary, "needs": needs}


def _to_bool(v, field):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    raise ToolError("invalid_input", f"'{field}' must be true or false", field=field)


def parse_json_value(v, field):
    if isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if s.startswith("@"):
        path = Path(s[1:])
        if not path.is_file():
            raise ToolError("not_found", f"JSON file for '{field}' not found: {path}", field=field)
        s = path.read_text(encoding="utf-8")
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ToolError("invalid_input", f"'{field}' is not valid JSON ({e.msg})", field=field,
                        hint="Pass inline JSON or @path/to/file.json")


def _split_list(v, field):
    if isinstance(v, list):
        return v
    s = str(v).strip()
    if s.startswith("["):
        return parse_json_value(s, field)
    return [x.strip() for x in s.split(",") if x.strip()]


def _existing_file(v, field):
    p = Path(str(v))
    if not p.is_file():
        raise ToolError("not_found", f"'{field}' file not found: {v}", field=field)
    return str(p)


def coerce_value(spec, v):
    name, t = spec["name"], spec["type"]
    try:
        if t == "str" or t == "outfile":
            v = str(v)
        elif t == "int":
            if isinstance(v, bool):
                raise ValueError
            f = float(v)
            if f != int(f):
                raise ValueError
            v = int(f)
        elif t == "float":
            v = float(v)
        elif t == "bool":
            v = _to_bool(v, name)
        elif t == "choice":
            v = str(v)
            if v not in spec.get("choices", []):
                raise ToolError("invalid_input", f"'{name}' must be one of {', '.join(spec['choices'])}",
                                field=name)
        elif t == "file":
            v = _existing_file(v, name)
        elif t == "files":
            items = _split_list(v, name)
            if not items:
                raise ToolError("invalid_input", f"'{name}' needs at least one file", field=name)
            v = [_existing_file(x, name) for x in items]
        elif t == "dir":
            p = Path(str(v))
            if not p.is_dir():
                raise ToolError("not_found", f"'{name}' directory not found: {v}", field=name)
            v = str(p)
        elif t == "json":
            v = parse_json_value(v, name)
        elif t == "list":
            v = [str(x) for x in _split_list(v, name)]
        elif t == "intlist":
            v = [int(float(x)) for x in _split_list(v, name)]
        elif t == "floatlist":
            v = [float(x) for x in _split_list(v, name)]
        else:
            raise ToolError("runtime_error", f"unknown parameter type {t} for {name}")
    except ToolError:
        raise
    except (ValueError, TypeError):
        raise ToolError("invalid_input", f"'{name}' must be of type {t}", field=name)
    if "min" in spec and isinstance(v, (int, float)) and v < spec["min"]:
        raise ToolError("invalid_input", f"'{name}' must be >= {spec['min']}", field=name)
    if "max" in spec and isinstance(v, (int, float)) and v > spec["max"]:
        raise ToolError("invalid_input", f"'{name}' must be <= {spec['max']}", field=name)
    return v


def validate_params(op_def, raw, defaults):
    """Merge defaults (catalog default_params) with user input and validate against the spec."""
    specs = {s["name"]: s for s in op_def["params"]}
    unknown = [k for k in raw if k not in specs]
    if unknown:
        raise ToolError("invalid_input", f"unknown parameter(s): {', '.join(sorted(unknown))}",
                        field=unknown[0], hint="Run 'describe <capability>' to see accepted parameters.")
    merged = dict(defaults or {})
    merged.update(raw)
    out = {}
    for name, spec in specs.items():
        if name in merged:
            out[name] = coerce_value(spec, merged[name])
        elif spec.get("required"):
            raise ToolError("invalid_input", f"missing required parameter '{name}'", field=name,
                            hint=spec.get("help", ""))
        elif "default" in spec:
            out[name] = coerce_value(spec, spec["default"])
        else:
            out[name] = None
    return out


def parse_kv_pairs(pairs):
    """Turn ['key=value', ...] into a dict. Values stay strings; validation coerces them."""
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise ToolError("invalid_input", f"--param expects key=value, got '{item}'")
        k, v = item.split("=", 1)
        out[k.strip()] = v
    return out


# ----------------------------------------------------------------- run context

class Context:
    """Per-run state: config, approval flags, output directory and collected artifacts."""

    def __init__(self, cfg, cap, output_dir, approve=False, overwrite=False):
        self.cfg = cfg
        self.cap = cap
        self.output_dir = Path(output_dir)
        self.approve = approve
        self.overwrite = overwrite
        self.cap_dir = self.output_dir / cap["id"]
        self.warnings = []
        self.artifacts = []
        self.protected = set()  # input files that no output may ever replace

    @property
    def limits(self):
        return self.cfg["limits"]

    def warn(self, msg):
        self.warnings.append(msg)

    def out_path(self, value, default_name, inputs=(), allowed_ext=None):
        """Return a safe output path.

        - Default paths live in the tool-managed output directory and may be replaced.
        - Explicit user paths that already exist need --overwrite AND --approve.
        - An output may never be the same file as an input.
        """
        explicit = bool(value)
        p = Path(value) if explicit else self.cap_dir / default_name
        if allowed_ext and p.suffix.lower() not in allowed_ext:
            raise ToolError("invalid_input", f"output extension must be one of {sorted(allowed_ext)}",
                            field="output")
        resolved = p.resolve()
        for i in list(inputs) + sorted(self.protected):
            if i and Path(i).resolve() == resolved:
                raise ToolError("invalid_input", "output path would overwrite an input file; choose another path",
                                field="output")
        if p.exists():
            if explicit:
                if not self.overwrite:
                    raise ToolError("invalid_input", f"output already exists: {p}", field="output",
                                    hint="Choose a new --output path, or pass --overwrite --approve.")
                if not self.approve:
                    raise ToolError("approval_required", f"replacing {p} needs explicit approval",
                                    hint="Re-run with --overwrite --approve after reviewing the target.")
            else:
                self.warn(f"replaced previous tool-managed output: {p.name}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def record(self, path):
        self.artifacts.append(str(path))
        return str(path)

    def protect_inputs(self, params, specs):
        """Protect every existing file named by a non-output parameter, including files inside JSON values."""
        out_types = {"outfile", "dir"}
        names = {s["name"]: s["type"] for s in specs}

        def walk(v):
            if isinstance(v, str) and Path(v).is_file():
                self.protected.add(str(Path(v).resolve()))
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)

        for k, v in params.items():
            if names.get(k) not in out_types:
                walk(v)


# ----------------------------------------------------------------- small utilities

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def slugify(text, maxlen=60):
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:maxlen].strip("-")) or "item"


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(value, field="color"):
    m = _HEX.match(str(value).strip())
    if not m:
        raise ToolError("invalid_input", f"'{field}' must be a hex colour like #1e88e5", field=field)
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def require_pillow():
    try:
        import PIL  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        raise ToolError("dependency_missing", "Pillow is not installed",
                        hint="Run: python3 -m pip install -r tools-requirements.txt")
    import PIL
    return PIL


def check_image_file(path, cfg):
    """Basic bounds check before any decode: size on disk and pixel count."""
    p = Path(path)
    size = p.stat().st_size
    if size > cfg["limits"]["max_file_bytes"]:
        raise ToolError("invalid_input", f"file too large ({size} bytes): {p.name}",
                        hint="Raise limits.max_file_bytes in the config if this is intended.")
    return p


def fmt_num(x, nd=2):
    return f"{x:.{nd}f}"
