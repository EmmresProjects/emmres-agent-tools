#!/usr/bin/env python3
"""tools-narrative-core.py - shared runtime for the story-narrative-studio Skill Tool.

This module is not run directly. It is loaded by the sibling ``tools-*.py``
modules with a filename-accurate ``importlib`` loader (see
``load_sibling_module``), because packaged filenames are required to keep the
``tools-`` prefix and therefore are not plain Python identifiers.

Responsibilities
----------------
* Locate and load the ``asset-*.json`` / ``asset-*.md`` resources.
* Validate and normalize user input before any work starts.
* Guarded, atomic, approval-aware file writes (no silent overwrite).
* A tiny project-document model so each operation can persist into one bundle.
* Structured result envelopes, exit codes, deadlines and size limits.
* A JSON-Schema subset validator used by the continuity checker.

Python 3.8+ standard library only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time

TOOL_NAME = "story-narrative-studio"
TOOL_VERSION = "1.0.0"
PRIMARY_ENTRY = "tools-story-narrative.py"

SKILL_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

# Exit codes used by every tool in this folder.
EXIT_OK = 0
EXIT_INPUT = 2
EXIT_APPROVAL = 3
EXIT_NOTFOUND = 4
EXIT_LIMIT = 5
EXIT_INTERNAL = 6

LIMITS = {
    "max_input_bytes": 8_000_000,      # 8 MB per text input
    "max_words": 400_000,              # manuscript word ceiling
    "max_items": 5_000,               # beats/pages/nodes/characters ceiling
    "max_pages": 200,
    "default_timeout": 60.0,           # seconds per operation
    "max_timeout": 600.0,
}

ASSET_FILES = {
    "catalog": "asset-capability-catalog.json",
    "structures": "asset-structure-presets.json",
    "genres": "asset-genre-presets.json",
    "rubric": "asset-rubric.json",
    "prompts": "asset-prompts.json",
    "continuity_schema": "asset-continuity-schema.json",
    "scene_card_template": "asset-scene-card-template.md",
    "brief_template": "asset-brief-template.md",
}


# --------------------------------------------------------------------------
# Errors and envelopes
# --------------------------------------------------------------------------
class StudioError(Exception):
    """A user-facing failure with a stable exit code and actionable hint."""

    def __init__(self, message, code=EXIT_INPUT, hint=None, field=None, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.field = field
        self.details = details or {}

    def envelope(self, op=None):
        return {
            "status": "error",
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "op": op,
            "code": self.code,
            "error": {
                "message": self.message,
                "field": self.field,
                "hint": self.hint,
                "details": self.details,
            },
        }


def ok_envelope(op, payload=None, warnings=None, artifacts=None):
    env = {
        "status": "ok",
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "op": op,
        "generated_at": now_iso(),
    }
    if payload is not None:
        env["result"] = payload
    if artifacts:
        env["artifacts"] = artifacts
    if warnings:
        env["warnings"] = warnings
    return env


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------
def slugify(text, fallback="untitled"):
    """ASCII, lowercase, hyphenated slug. Never returns an empty string."""
    text = (text or "").strip().lower()
    text = re.sub(r"['\u2019]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or fallback


def parse_list(value, field="list"):
    """Accept a comma/semicolon/pipe separated string or a JSON list string."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        raw = str(value).strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                raise StudioError(
                    "%s looks like a JSON list but could not be parsed." % field,
                    field=field,
                    hint='Use plain comma separated values, e.g. --themes "grief, loyalty".',
                )
            if not isinstance(parsed, list):
                raise StudioError("%s must be a list." % field, field=field)
            items = [str(v) for v in parsed]
        else:
            items = re.split(r"\s*[,;|]\s*", raw)
    out, seen = [], set()
    for item in items:
        clean = re.sub(r"\s+", " ", item).strip(" \t\r\n\"'")
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def clamp(value, low, high):
    return max(low, min(high, value))


def require_int(value, field, low, high, default=None):
    if value is None:
        if default is None:
            raise StudioError("%s is required." % field, field=field)
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise StudioError("%s must be an integer, got %r." % (field, value), field=field)
    if number < low or number > high:
        raise StudioError(
            "%s must be between %s and %s, got %s." % (field, low, high, number),
            field=field,
            hint="Adjust the value and rerun the command.",
        )
    return number


def require_text(value, field, min_len=1, max_len=4000, default=None, allow_empty=False):
    if value is None:
        if default is None:
            if allow_empty:
                return ""
            raise StudioError("%s is required." % field, field=field)
        value = default
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not allow_empty and len(text) < min_len:
        raise StudioError("%s must be at least %s characters." % (field, min_len), field=field)
    if len(text) > max_len:
        raise StudioError(
            "%s exceeds the %s character limit." % (field, max_len), field=field
        )
    return text


def deep_get(data, *path, default=None):
    cur = data
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def sha_seed(*parts):
    """Deterministic integer seed from arbitrary parts."""
    joined = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16], 16)


def seeded_rng(*parts):
    return random.Random(sha_seed(*parts))


class Deadline:
    """Cooperative soft timeout checked inside generation loops."""

    def __init__(self, seconds):
        seconds = float(seconds or LIMITS["default_timeout"])
        if seconds <= 0:
            raise StudioError(
                "--timeout must be greater than 0.", field="timeout",
                hint="Use --timeout 60 for a one minute budget.",
            )
        if seconds > LIMITS["max_timeout"]:
            raise StudioError(
                "--timeout above %ss is not allowed." % int(LIMITS["max_timeout"]),
                field="timeout",
                hint="Split the work into smaller runs instead of raising the timeout.",
            )
        self.seconds = seconds
        self.started = time.monotonic()

    def remaining(self):
        return self.seconds - (time.monotonic() - self.started)

    def check(self, stage="work"):
        if self.remaining() <= 0:
            raise StudioError(
                "Operation exceeded its %ss time budget during %s." % (int(self.seconds), stage),
                code=EXIT_LIMIT,
                hint="Lower the requested size (beats, pages, cast) or raise --timeout.",
            )

    def elapsed(self):
        return round(time.monotonic() - self.started, 4)


# --------------------------------------------------------------------------
# Asset loading
# --------------------------------------------------------------------------
def asset_path(name):
    """Resolve a packed asset by key (see ASSET_FILES) or by exact filename."""
    filename = ASSET_FILES.get(name, name)
    if not isinstance(filename, str):
        raise StudioError("Unknown asset key: %r" % (name,), code=EXIT_INTERNAL, field="asset")
    if os.path.sep in filename or "/" in filename or "\\" in filename:
        raise StudioError(
            "Asset names must be plain filenames, got %r." % filename,
            code=EXIT_INTERNAL,
            field="asset",
        )
    if not filename.startswith("asset-"):
        raise StudioError(
            "Packed resources must use the asset- prefix, got %r." % filename,
            code=EXIT_INTERNAL,
            field="asset",
        )
    path = os.path.join(SKILL_TOOL_DIR, filename)
    if not os.path.isfile(path):
        raise StudioError(
            "Required resource %s is missing beside %s." % (filename, PRIMARY_ENTRY),
            code=EXIT_NOTFOUND,
            field="asset",
            hint="Restore the file so it sits directly inside the skill tool folder.",
        )
    return path


_ASSET_CACHE = {}


def load_asset(name, cache=True):
    """Load a JSON asset (dict) or a text asset (str)."""
    path = asset_path(name)
    key = os.path.basename(path)
    if cache and key in _ASSET_CACHE:
        return _ASSET_CACHE[key]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if path.endswith(".json"):
                data = json.load(handle)
            else:
                data = handle.read()
    except ValueError as exc:
        raise StudioError(
            "Resource %s is not valid JSON: %s" % (key, exc), code=EXIT_INTERNAL, field="asset"
        )
    if cache:
        _ASSET_CACHE[key] = data
    return data


def load_sibling_module(filename, alias=None):
    """Import a sibling tools-*.py file by its exact packaged filename."""
    sys.dont_write_bytecode = True
    if not filename.startswith("tools-") or not filename.endswith(".py"):
        raise StudioError(
            "Sibling modules must be tools-*.py files, got %r." % filename,
            code=EXIT_INTERNAL,
        )
    path = os.path.join(SKILL_TOOL_DIR, filename)
    if not os.path.isfile(path):
        raise StudioError(
            "Sibling module %s is missing." % filename, code=EXIT_NOTFOUND,
            hint="Restore it beside the primary entry point.",
        )
    alias = alias or re.sub(r"[^A-Za-z0-9_]", "_", filename[:-3])
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise StudioError("Could not create an import spec for %s." % filename, code=EXIT_INTERNAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[alias] = module
    return module


# --------------------------------------------------------------------------
# Input reading
# --------------------------------------------------------------------------
def read_text_file(path, field="input", max_bytes=None):
    """Read UTF-8 text with a hard byte ceiling and clear errors."""
    if not path:
        raise StudioError("%s path is required." % field, field=field)
    max_bytes = max_bytes or LIMITS["max_input_bytes"]
    if not os.path.exists(path):
        raise StudioError(
            "%s file not found: %s" % (field, path), code=EXIT_NOTFOUND, field=field,
            hint="Check the path, or pass the text inline with the matching --text option.",
        )
    if os.path.isdir(path):
        raise StudioError("%s must be a file, not a directory: %s" % (field, path), field=field)
    size = os.path.getsize(path)
    if size > max_bytes:
        raise StudioError(
            "%s file is %.1f MB, above the %.1f MB limit." % (field, size / 1e6, max_bytes / 1e6),
            code=EXIT_LIMIT,
            field=field,
            hint="Split the manuscript into parts and audit them separately.",
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except UnicodeDecodeError:
        raise StudioError(
            "%s is not valid UTF-8 text: %s" % (field, path), field=field,
            hint="Convert the file to UTF-8 and rerun.",
        )


def read_json_file(path, field="input"):
    text = read_text_file(path, field=field)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise StudioError(
            "%s is not valid JSON: %s" % (field, exc), field=field,
            hint="Check for a trailing comma or a missing bracket.",
        )


# --------------------------------------------------------------------------
# Guarded output
# --------------------------------------------------------------------------
def resolve_out_path(base_dir, name, field="output"):
    """Build an output path inside base_dir; reject traversal and absolutes."""
    if not name:
        raise StudioError("%s name is required." % field, field=field)
    name = str(name)
    if os.path.isabs(name) or re.match(r"^[A-Za-z]:[\\/]", name):
        raise StudioError(
            "%s must be a relative filename, got an absolute path." % field,
            field=field,
            hint="Pass a name such as 'brief.md' and use --out to choose the directory.",
        )
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise StudioError(
            "%s must not contain parent-directory traversal." % field, field=field
        )
    if not parts:
        raise StudioError("%s name resolves to nothing." % field, field=field)
    base = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(base, *parts))
    if target != base and not target.startswith(base + os.sep):
        raise StudioError(
            "%s escapes the output directory." % field, field=field,
            hint="Use a plain filename or a subfolder under --out.",
        )
    return target


def _atomic_write(path, text, approve, kind):
    if os.path.exists(path) and not approve:
        raise StudioError(
            "Refusing to overwrite existing %s: %s" % (kind, path),
            code=EXIT_APPROVAL,
            field="output",
            hint="Rerun with --approve-overwrite after checking the existing file.",
        )
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    return path


def write_text_guarded(path, text, approve=False, kind="file"):
    return _atomic_write(path, text, approve, kind)


def write_json_guarded(path, data, approve=False, kind="JSON file"):
    return _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", approve, kind)


def dumps_json(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def render_template(template, values):
    """Very small {{key}} renderer with conditional-free, literal substitution."""
    def replace(match):
        key = match.group(1).strip()
        value = values.get(key, "")
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return "" if value is None else str(value)

    return re.sub(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", replace, template)


# --------------------------------------------------------------------------
# Project model
# --------------------------------------------------------------------------
PROJECT_SECTIONS = (
    "brief", "premise", "outline", "characters", "scenes", "comic", "game", "bible", "audits",
)


def project_new(title=None):
    return {
        "schema": "story-narrative-studio/project",
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "title": title or "Untitled narrative project",
        "meta": {},
        "brief": None,
        "premise": None,
        "outline": None,
        "characters": None,
        "scenes": None,
        "comic": None,
        "game": None,
        "bible": None,
        "audits": [],
    }


def project_load(path):
    if not path:
        raise StudioError("--project path is required for this option.", field="project")
    if not os.path.exists(path):
        raise StudioError(
            "Project file not found: %s" % path, code=EXIT_NOTFOUND, field="project",
            hint="Run the command once with --project <new-file> to create it.",
        )
    data = read_json_file(path, field="project")
    if not isinstance(data, dict) or data.get("schema") != "story-narrative-studio/project":
        raise StudioError(
            "Project file has an unknown schema.", field="project",
            hint="Projects must be written by this Skill Tool; regenerate or fix the schema key.",
        )
    for key in PROJECT_SECTIONS:
        data.setdefault(key, [] if key == "audits" else None)
    data.setdefault("meta", {})
    return data


def project_set(project, section, value):
    if section not in PROJECT_SECTIONS:
        raise StudioError("Unknown project section %r." % section, code=EXIT_INTERNAL)
    project[section] = value
    project["updated_at"] = now_iso()
    return project


def project_add_audit(project, report, keep=20):
    audits = project.setdefault("audits", [])
    audits.append(report)
    if len(audits) > keep:
        del audits[:-keep]
    project["updated_at"] = now_iso()
    return project


def save_project(path, project, approve=True):
    if approve is False and os.path.exists(path):
        raise StudioError(
            "Refusing to overwrite existing project: %s" % path, code=EXIT_APPROVAL, field="project",
            hint="Rerun with --approve-overwrite.",
        )
    return write_json_guarded(path, project, approve=True, kind="project file")


# --------------------------------------------------------------------------
# JSON Schema subset validator (used for the continuity bible)
# --------------------------------------------------------------------------
def validate_schema(data, schema, path="$"):
    """Validate against the JSON Schema subset supported in knowledge-readme.md.

    Returns a list of {path, message} problems; empty means valid.
    """
    problems = []
    if not isinstance(schema, dict):
        return problems

    expected = schema.get("type")
    type_map = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool, "null": type(None),
    }
    if expected in type_map:
        ok = isinstance(data, type_map[expected])
        if expected in ("integer", "number") and isinstance(data, bool):
            ok = False
        if not ok:
            problems.append({
                "path": path,
                "message": "expected type %s, got %s" % (expected, type(data).__name__),
            })
            return problems

    if "enum" in schema and data not in schema["enum"]:
        problems.append({
            "path": path,
            "message": "value %r is not one of %s" % (data, schema["enum"]),
        })

    if isinstance(data, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, data):
            problems.append({"path": path, "message": "does not match pattern %r" % pattern})
        if "minLength" in schema and len(data) < schema["minLength"]:
            problems.append({"path": path, "message": "shorter than %s" % schema["minLength"]})
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            problems.append({"path": path, "message": "longer than %s" % schema["maxLength"]})

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            problems.append({"path": path, "message": "below minimum %s" % schema["minimum"]})
        if "maximum" in schema and data > schema["maximum"]:
            problems.append({"path": path, "message": "above maximum %s" % schema["maximum"]})

    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            problems.append({"path": path, "message": "fewer than %s items" % schema["minItems"]})
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            problems.append({"path": path, "message": "more than %s items" % schema["maxItems"]})
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(data):
                problems.extend(validate_schema(item, item_schema, "%s[%s]" % (path, index)))

    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                problems.append({"path": path, "message": "missing required key %r" % key})
        properties = schema.get("properties", {})
        for key, value in data.items():
            if key in properties:
                problems.extend(validate_schema(value, properties[key], "%s.%s" % (path, key)))
            elif schema.get("additionalProperties") is False:
                problems.append({"path": "%s.%s" % (path, key), "message": "unexpected key"})

    return problems


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------
def temp_dir(prefix="sns-"):
    return tempfile.mkdtemp(prefix=prefix)


def cleanup(path):
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif path and os.path.isfile(path):
        try:
            os.unlink(path)
        except OSError:
            pass


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9'\u2019-]+", text or ""))


def require_choice(value, field, choices, default=None, allow_custom=False):
    if value is None:
        if default is not None:
            return default
        raise StudioError(
            "%s is required. Choose one of: %s" % (field, ", ".join(sorted(choices))),
            field=field,
        )
    value = str(value).strip()
    if value in choices:
        return value
    lowered = {str(c).lower(): c for c in choices}
    if value.lower() in lowered:
        return lowered[value.lower()]
    if allow_custom:
        return value
    raise StudioError(
        "%s=%r is not supported. Choose one of: %s" % (field, value, ", ".join(sorted(choices))),
        field=field,
        hint="Run `capabilities --op %s` for the accepted values." % field,
    )
