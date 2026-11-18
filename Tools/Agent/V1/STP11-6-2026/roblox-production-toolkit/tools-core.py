"""Shared validation, portable paths, bounded work and transactional output.
Loaded as rbx_core by tools-roblox.py; no third-party dependencies.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
ROOT_NAME = ROOT.name
MAX_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 40
MAX_NODES = 100000
CANCEL = __import__("threading").Event()


class ToolError(Exception):
    def __init__(self, code, message, details=None, status=2):
        super().__init__(message)
        self.code, self.message, self.details, self.status = code, message, details or {}, status

    def dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def fail(code, message, **details):
    raise ToolError(code, message, details)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def check_json(value, depth=0):
    if depth > MAX_DEPTH:
        fail("INPUT_LIMIT", "JSON nesting exceeds 40 levels.")
    if isinstance(value, float) and not math.isfinite(value):
        fail("INPUT_INVALID", "NaN and infinity are not valid JSON numbers.")
    if isinstance(value, dict):
        if len(value) > 10000:
            fail("INPUT_LIMIT", "A JSON object exceeds 10,000 keys.")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 512:
                fail("INPUT_INVALID", "JSON keys must be strings of at most 512 characters.")
            check_json(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 20000:
            fail("INPUT_LIMIT", "A JSON array exceeds 20,000 items.")
        for child in value:
            check_json(child, depth + 1)
    elif value is not None and not isinstance(value, (str, int, bool, float)):
        fail("INPUT_INVALID", f"Unsupported input value type: {type(value).__name__}.")
    elif isinstance(value, str) and len(value) > MAX_BYTES:
        fail("INPUT_LIMIT", "An input string exceeds the byte limit.")


def read_json(path, max_bytes=MAX_BYTES):
    p = Path(path)
    try:
        if p.is_symlink() or not p.is_file():
            fail("INPUT_MISSING", "Input must be a regular, non-symlink file.", path=str(path))
        if p.stat().st_size > max_bytes:
            fail("INPUT_LIMIT", "Input file is too large.", path=str(path), limit=max_bytes)
        value = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        fail("INPUT_INVALID", "Input is not valid UTF-8 JSON.", reason=str(exc))
    except OSError as exc:
        fail("INPUT_MISSING", "Cannot read input.", reason=str(exc))
    check_json(value)
    return value


def read_text(path, max_bytes=MAX_BYTES):
    p = Path(path)
    try:
        if p.is_symlink() or not p.is_file() or p.stat().st_size > max_bytes:
            fail("INPUT_INVALID", "Source must be a regular non-symlink file within the size limit.", limit=max_bytes)
        return p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        fail("INPUT_MISSING", "Cannot read UTF-8 source.", reason=str(exc))


def resource(name):
    if not re.fullmatch(r"(?:tools|asset|knowledge)-[a-z0-9.-]+", name):
        fail("RESOURCE_INVALID", "Invalid component resource name.", name=name)
    p = ROOT / name
    if not p.is_file() or p.is_symlink():
        fail("RESOURCE_MISSING", "Required component resource is missing.", resource=f"{ROOT_NAME}/{name}")
    return p


def relative_resource(name):
    return f"{ROOT_NAME}/{name}"


def relpath(path, base=None):
    return Path(os.path.relpath(Path(path).resolve(), Path(base or Path.cwd()).resolve())).as_posix()


def safe_name(value, fallback="object"):
    if not isinstance(value, str):
        fail("INPUT_INVALID", "Names must be strings.")
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
    return result or fallback


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", value):
        fail("INPUT_INVALID", "Expected an ASCII identifier.", value=value)
    reserved = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while", "continue", "type", "export"}
    if value in reserved:
        fail("INPUT_INVALID", "Identifier is a Luau keyword.", value=value)
    return value


def number(data, key, default=None, minimum=None, maximum=None, integer=False):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail("INPUT_INVALID", f"{key} must be a finite number.")
    if integer and not isinstance(value, int):
        fail("INPUT_INVALID", f"{key} must be an integer.")
    if minimum is not None and value < minimum:
        fail("INPUT_INVALID", f"{key} must be >= {minimum}.", value=value)
    if maximum is not None and value > maximum:
        fail("INPUT_INVALID", f"{key} must be <= {maximum}.", value=value)
    return value


def integer(data, key, default=None, minimum=0, maximum=10000):
    return number(data, key, default, minimum, maximum, True)


def text(data, key, default=None, maximum=4000, required=True):
    value = data.get(key, default)
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        fail("INPUT_INVALID", f"{key} must be a string of 1–{maximum} characters.")
    return value


def boolean(data, key, default=False):
    value = data.get(key, default)
    if not isinstance(value, bool):
        fail("INPUT_INVALID", f"{key} must be true or false.")
    return value


def array(data, key, default=None, maximum=1000, kind=None, required=False):
    value = data.get(key, default if default is not None else [])
    if not isinstance(value, list) or len(value) > maximum:
        fail("INPUT_INVALID", f"{key} must be an array with at most {maximum} items.")
    if required and not value:
        fail("INPUT_INVALID", f"{key} must not be empty.")
    if kind and any(not isinstance(x, kind) for x in value):
        fail("INPUT_INVALID", f"{key} contains an invalid item type.")
    if any(isinstance(x, dict) for x in value):
        if any(not isinstance(x, dict) for x in value):
            fail("INPUT_INVALID", f"{key} must contain only objects when using object records.")
    return value


def obj(data, key, default=None, required=False):
    value = data.get(key, default if default is not None else {})
    if not isinstance(value, dict) or (required and not value):
        fail("INPUT_INVALID", f"{key} must be an object.")
    return value


def vector(value, size=3, default=None):
    value = value if value is not None else default
    if not isinstance(value, list) or len(value) != size or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 1e9 for x in value):
        fail("INPUT_INVALID", f"Expected a finite {size}-component numeric vector.")
    return [float(x) for x in value]


def get_path(data, path, default=None):
    current = data
    try:
        for key in path.split("."):
            current = current[key]
        return current
    except (KeyError, TypeError, IndexError):
        return default


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_schema(value, schema, path="$", depth=0):
    """Small, explicit JSON Schema subset; no remote refs or regular-expression eval."""
    if depth > MAX_DEPTH:
        fail("SCHEMA_INVALID", "Schema nesting limit exceeded.", path=path)
    if "$ref" in schema:
        fail("SCHEMA_INVALID", "Only the internal envelope schema is supported; external schema refs are disabled.", path=path)
    typ = schema.get("type")
    types = typ if isinstance(typ, list) else [typ] if typ else []
    checks = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list), "string": lambda x: isinstance(x, str), "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x), "integer": lambda x: isinstance(x, int) and not isinstance(x, bool), "boolean": lambda x: isinstance(x, bool), "null": lambda x: x is None}
    if types and not any(checks[t](value) for t in types if t in checks):
        fail("INPUT_INVALID", "Value does not match the configured schema.", path=path, expected=typ, actual=type(value).__name__)
    if "enum" in schema and value not in schema["enum"]:
        fail("INPUT_INVALID", "Value is not an allowed choice.", path=path, allowed=schema["enum"])
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", MAX_BYTES):
            fail("INPUT_INVALID", "String length is outside the schema limit.", path=path)
        if "pattern" in schema:
            pattern = schema["pattern"]
            if len(pattern) > 256 or not re.fullmatch(pattern, value):
                fail("INPUT_INVALID", "String does not match the schema pattern.", path=path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            fail("INPUT_INVALID", "Number is outside the schema range.", path=path)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                fail("INPUT_INVALID", "Required input field is missing.", path=f"{path}.{key}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in props:
                validate_schema(child, props[key], f"{path}.{key}", depth + 1)
            elif extra is False:
                fail("INPUT_INVALID", "Unknown field; consult describe for this capability.", path=f"{path}.{key}")
            elif isinstance(extra, dict):
                validate_schema(child, extra, f"{path}.{key}", depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 20000):
            fail("INPUT_INVALID", "Array length is outside the schema limit.", path=path)
        for index, child in enumerate(value):
            if "items" in schema:
                validate_schema(child, schema["items"], f"{path}[{index}]", depth + 1)
    return True


class Context:
    def __init__(self, config, output, timeout=30, approved=False, approve_overwrite=False):
        self.config, self.output = config, Path(output)
        self.approved, self.approve_overwrite = approved, approve_overwrite
        self.started, self.timeout = time.monotonic(), timeout
        self.warnings = []
        self.resources = {}

    def check(self):
        if CANCEL.is_set():
            fail("CANCELLED", "Work was cancelled by the operator; staged output was cleaned.", status=130)
        if time.monotonic() - self.started > self.timeout:
            fail("TIMEOUT", "Work exceeded its deadline; staged output was cleaned and committed files were preserved.", limit_seconds=self.timeout, status=124)

    def remaining(self):
        self.check()
        return max(0.01, self.timeout - (time.monotonic() - self.started))

    def approve(self, action, details=None):
        if not self.approved:
            raise ToolError("APPROVAL_REQUIRED", "Explicit --approve is required for this action.", {"action": action, **(details or {})}, status=77)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)


class Writer:
    """Bounded, no-follow file writer. Commits only after all artifacts validate.
    Existing files are backed up and restored on a failed commit; --approve AND
    --overwrite are both required. Unrelated files are never deleted.
    """
    def __init__(self, output, ctx, max_files=400, max_bytes=32 * 1024 * 1024):
        self.output, self.ctx = Path(output), ctx
        self.max_files, self.max_bytes = max_files, max_bytes
        self.files, self.total, self.stage = {}, 0, None
        self.backups, self.committed, self.recovered = {}, [], []

    def add(self, name, content, kind="text"):
        self.ctx.check()
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or len(p.parts) > 8 or len(name) > 220:
            fail("OUTPUT_PATH", "Artifact paths must be relative, bounded and contain no traversal.", name=name)
        if any(part.startswith(".") for part in p.parts):
            fail("OUTPUT_PATH", "Hidden artifact paths are not permitted.", name=name)
        if name in self.files or len(self.files) >= self.max_files:
            fail("OUTPUT_LIMIT", "Duplicate artifact or output file limit reached.", name=name, limit=self.max_files)
        if kind == "json":
            check_json(content)
            content = json.dumps(content, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes):
            fail("OUTPUT_INVALID", "Artifact content must be text, JSON, or bytes.")
        self.total += len(content)
        if self.total > self.max_bytes:
            fail("OUTPUT_LIMIT", "Output exceeds the configured byte budget.", limit=self.max_bytes)
        self.files[name] = content
        return name

    def commit(self):
        self.ctx.check()
        out = self.output
        if out.is_symlink():
            fail("OUTPUT_PATH", "Output directory must not be a symlink.")
        for parent in out.parents:
            if parent.is_symlink():
                fail("OUTPUT_PATH", "Output ancestors must not be symlinks.")
        out.mkdir(parents=True, exist_ok=True)
        self.recovered = clean_stale_staging(out)
        for name in self.files:
            target = out / name
            for parent in list(target.parents):
                if parent == out:
                    break
                if parent.is_symlink():
                    fail("OUTPUT_PATH", "Artifact parent must not be a symlink.", name=name)
            if target.exists() or target.is_symlink():
                if not (self.ctx.approve_overwrite and self.ctx.approved):
                    fail("OUTPUT_EXISTS", "Output exists. Choose a new directory or explicitly approve --overwrite.", path=relpath(target), status=73)
                if target.is_symlink() or not target.is_file():
                    fail("OUTPUT_PATH", "Only regular files can be replaced; directories are never removed.", name=name)
        self.stage = Path(tempfile.mkdtemp(prefix=".rbx-stage-", dir=out))
        try:
            for name, content in self.files.items():
                self.ctx.check()
                staged = self.stage / name
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(content)
                # Inspect each serialized artifact, not just in-memory values.
                if name.endswith(".json"):
                    json.loads(staged.read_text(encoding="utf-8"))
            for name in self.files:
                target = out / name
                if target.exists():
                    backup = self.stage / "backups" / name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    self.backups[name] = backup
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(self.stage / name, target)
                self.committed.append(name)
            result = [relpath(out / name) for name in self.files]
            self.cleanup()
            return result
        except BaseException:
            for name in reversed(self.committed):
                target = out / name
                if name in self.backups:
                    os.replace(self.backups[name], target)
                elif target.is_file():
                    target.unlink()
            self.cleanup()
            raise

    def cleanup(self):
        if self.stage and self.stage.exists():
            shutil.rmtree(self.stage, ignore_errors=True)
        self.stage = None


def walk_tree(tree, limit=10000):
    count = 0
    def walk(node, path):
        nonlocal count
        count += 1
        if count > limit or len(path.split("/")) > 30:
            fail("INPUT_LIMIT", "Instance tree exceeds the traversal limit.")
        if not isinstance(node, dict):
            fail("INPUT_INVALID", "Tree nodes must be objects.", path=path)
        name = text(node, "name", path.rsplit("/", 1)[-1], maximum=120)
        cls = text(node, "class", "Folder", maximum=80)
        current = {"name": name, "class": cls, "path": path, "node": node}
        yield current
        children = array(node, "children", maximum=2000, kind=dict)
        names = []
        for child in children:
            child_name = text(child, "name", maximum=120)
            if child_name in names:
                fail("INPUT_INVALID", "Sibling names must be unique for stable tree references.", path=path, name=child_name)
            names.append(child_name)
            yield from walk(child, path + "/" + child_name)
    root_name = text(tree, "name", "game", maximum=120)
    yield from walk(tree, root_name)


def tree_index(tree):
    return {entry["path"]: entry for entry in walk_tree(tree)}


def graph_order(nodes, edges):
    names = set(nodes)
    if len(names) != len(nodes):
        fail("INPUT_INVALID", "Graph node IDs must be unique.")
    incoming = {name: 0 for name in names}
    outgoing = {name: [] for name in names}
    for edge in edges:
        a, b = (edge["from"], edge["to"]) if isinstance(edge, dict) else edge
        if a not in names or b not in names:
            fail("INPUT_INVALID", "Graph edge references a missing node.", edge=[a, b])
        outgoing[a].append(b)
        incoming[b] += 1
    ready = sorted(n for n in names if incoming[n] == 0)
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for target in outgoing[n]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort()
    if len(order) != len(names):
        fail("GRAPH_CYCLE", "Graph contains a cycle or unresolved dependency.", unresolved=sorted(n for n in names if incoming[n]))
    return order


def closure(edges, start):
    adjacency = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
    seen, pending = set(), [start]
    while pending:
        item = pending.pop()
        if item not in seen:
            seen.add(item)
            pending.extend(adjacency.get(item, []))
    return sorted(seen - {start})


def lua(value, indent=0):
    """Serialize data, never executable source, to Luau literal syntax."""
    pad, childpad = "\t" * indent, "\t" * (indent + 1)
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            fail("OUTPUT_INVALID", "Cannot serialize a non-finite number to Luau.")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        if not value:
            return "{}"
        return "{\n" + ",\n".join(childpad + lua(v, indent + 1) for v in value) + "\n" + pad + "}"
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for key, val in value.items():
            keyout = "[" + lua(str(key)) + "]" if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)) else str(key)
            lines.append(childpad + keyout + " = " + lua(val, indent + 1))
        return "{\n" + ",\n".join(lines) + "\n" + pad + "}"
    fail("OUTPUT_INVALID", "Unsupported Luau data type.")


def md(value):
    if isinstance(value, (dict, list)):
        return "```json\n" + json.dumps(value, indent=2, ensure_ascii=True) + "\n```"
    return str(value).replace("\r", "").strip()


def report(title, result, checks=None, notes=None):
    lines = ["# " + title, "", "## Result", ""]
    if isinstance(result, dict):
        for key, value in result.items():
            lines.extend(["### " + key.replace("_", " ").title(), "", md(value), ""])
    else:
        lines.extend([md(result), ""])
    if checks:
        lines.extend(["## Validation", ""])
        lines.extend([f"- **{c['status'].upper()}** `{c['id']}`: {c['message']}" for c in checks])
        lines.append("")
    if notes:
        lines.extend(["## Boundaries and next actions", ""])
        lines.extend(["- " + str(n) for n in notes])
    return "\n".join(lines).rstrip() + "\n"


def finding(id, passed, message, severity="warning", evidence=None):
    return {"id": id, "status": "pass" if passed is True else "review" if passed is None else "fail", "message": message, "severity": "info" if passed is True else severity, "evidence": evidence}


def summarize(checks):
    return {"passed": sum(c["status"] == "pass" for c in checks), "failed": sum(c["status"] == "fail" for c in checks), "review": sum(c["status"] == "review" for c in checks), "total": len(checks)}


def new_id(prefix="id"):
    return prefix + "-" + uuid.uuid4().hex[:16]


def chk(id, passed, message, severity="warning", evidence=None):
    """Finding helper: passed True=pass, False=fail, None=review (operator evidence needed)."""
    return finding(id, passed, message, severity, evidence)


def percentile(values, pct):
    data = sorted(float(v) for v in values)
    if not data:
        return None
    rank = (len(data) - 1) * pct / 100.0
    low, high = math.floor(rank), math.ceil(rank)
    return data[low] + (data[high] - data[low]) * (rank - low)


def resolve_ref(ref):
    """Resolve a skill-root-relative reference such as roblox-production-toolkit/asset-x.json."""
    if not isinstance(ref, str) or ref.startswith(("/", "\\")) or ":" in ref or ".." in Path(ref).parts:
        fail("RESOURCE_INVALID", "References must be relative to the skill root without traversal.", ref=str(ref))
    parts = Path(ref).parts
    if len(parts) != 2 or parts[0] != ROOT_NAME:
        fail("RESOURCE_INVALID", "References must name a file inside the component folder.", ref=ref)
    p = ROOT.parent / ref
    if not p.is_file() or p.is_symlink():
        fail("RESOURCE_MISSING", "Referenced component resource is missing.", ref=ref)
    return p


def load_json_resource(ref):
    try:
        return json.loads(resolve_ref(ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        fail("RESOURCE_INVALID", "Component JSON resource cannot be parsed.", ref=ref, reason=str(exc))


def clean_stale_staging(out):
    """Recovery: remove only interrupted staging folders created by Writer."""
    removed = []
    if out.is_dir() and not out.is_symlink():
        for child in out.iterdir():
            if child.name.startswith(".rbx-stage-") and child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
    return removed


def load_component(filename, module_name):
    """Load a sibling tools-*.py module by its prefixed filename (no package layout assumed)."""
    import importlib.util
    import sys
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = ROOT / filename
    if not path.is_file() or path.is_symlink() or not re.fullmatch(r"tools-[a-z0-9-]+\.py", filename):
        fail("RESOURCE_MISSING", "Required tool module is missing.", module=f"{ROOT_NAME}/{filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def hex_to_rgb(value):
    """Parse #rgb or #rrggbb into a tuple of 0-255 ints."""
    if not isinstance(value, str) or not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value):
        fail("INPUT_INVALID", "Colors must be #rgb or #rrggbb hex strings.", value=str(value)[:20])
    h = value[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def relative_luminance(rgb):
    """WCAG 2.x relative luminance."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """WCAG contrast ratio between two hex colors (1.0 to 21.0)."""
    a, b = relative_luminance(hex_to_rgb(fg)), relative_luminance(hex_to_rgb(bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def res_json(name):
    """Load a component JSON resource by its file name (for example asset-ui-tokens.json)."""
    return load_json_resource(f"{ROOT_NAME}/{name}")
