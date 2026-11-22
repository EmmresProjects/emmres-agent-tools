#!/usr/bin/env python3
"""Shared runtime utilities for the voice-capability-toolkit Skill Tool.

This module is not a user-facing entry point. It is loaded by every other
``tools-*`` module in this folder and provides:

* a consistent structured-result envelope (JSON on stdout or to a file)
* structured errors with stable exit codes
* resource resolution for the ``asset-*`` files beside this module
* approval gating for destructive / irreversible / rights-sensitive actions
* small filesystem, hashing and deadline helpers

Loading pattern used by the sibling modules (keeps ``tools-*`` filenames usable
even though they are not valid Python module names)::

    import importlib.util, os, sys
    _HERE = os.path.dirname(os.path.abspath(__file__))

    def _load(filename, modname):
        if modname in sys.modules:
            return sys.modules[modname]
        spec = importlib.util.spec_from_file_location(
            modname, os.path.join(_HERE, filename))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
        return mod

Running this file directly prints a short description of the shared contract.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys

sys.dont_write_bytecode = True

import tempfile
import time
import traceback

TOOLKIT_NAME = "voice-capability-toolkit"
TOOLKIT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

# Stable process exit codes. Agents and pipelines can branch on these.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INVALID_INPUT = 3
EXIT_NOT_FOUND = 4
EXIT_BLOCKED = 5          # policy / approval / consent gate
EXIT_UNAVAILABLE = 6      # optional dependency or runtime missing
EXIT_RUNTIME = 7          # unexpected failure while doing the work
EXIT_CANCELLED = 130

MAX_TEXT_BYTES = 4 * 1024 * 1024          # 4 MiB of script/catalog input
MAX_AUDIO_SECONDS = 60 * 60               # 1 hour per file, keeps DSP bounded
MAX_JSON_BYTES = 16 * 1024 * 1024


class ToolError(Exception):
    """Structured, user-facing error carrying a stable exit code."""

    def __init__(self, message, code=EXIT_INVALID_INPUT, *, hint=None,
                 details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.details = details or {}

    def to_dict(self):
        out = {"status": "error", "code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class Deadline:
    """Cooperative deadline used to bound long DSP / batch loops."""

    def __init__(self, seconds):
        self.seconds = float(seconds) if seconds is not None else None
        self.started = time.monotonic()

    def elapsed(self):
        return time.monotonic() - self.started

    def expired(self):
        return self.seconds is not None and self.elapsed() > self.seconds

    def check(self, stage="operation"):
        if self.expired():
            raise ToolError(
                f"Operation cancelled: '{stage}' exceeded the {self.seconds:g}s "
                "timeout.",
                code=EXIT_CANCELLED,
                hint="Raise --timeout, shorten the input, or reduce --workers.",
                details={"stage": stage, "timeout_seconds": self.seconds},
            )


# ---------------------------------------------------------------------------
# module loading
# ---------------------------------------------------------------------------

def module_dir():
    """Directory that contains this tool file (the Skill Tool folder)."""
    return os.path.dirname(os.path.abspath(__file__))


def load_sibling(filename, modname):
    """Import a sibling ``tools-*`` file once and cache it in ``sys.modules``."""
    if modname in sys.modules:
        return sys.modules[modname]
    import importlib.util

    path = os.path.join(module_dir(), filename)
    if not os.path.isfile(path):
        raise ToolError(
            f"Required companion tool '{filename}' was not found beside "
            f"'{os.path.basename(__file__)}'.",
            code=EXIT_NOT_FOUND,
            hint="Keep every tools-* file of the Skill Tool in the same folder.",
            details={"expected_path": filename},
        )
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def require_numpy():
    """Import numpy or fail with a precise, actionable message."""
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ToolError(
            "NumPy is required for audio and numeric operations but is not "
            "installed.",
            code=EXIT_UNAVAILABLE,
            hint="Run: python3 -m pip install 'numpy>=1.24'",
            details={"import_error": str(exc)},
        )
    return np


# ---------------------------------------------------------------------------
# resources / configuration
# ---------------------------------------------------------------------------

def resource_path(name, assets_dir=None):
    """Resolve an ``asset-*`` file with an optional directory override.

    Resolution order: explicit ``assets_dir`` argument, ``VOICE_TOOLKIT_ASSET_DIR``
    environment variable, then the folder holding the tools.
    """
    if not re.fullmatch(r"asset-[a-z0-9.\-]+", name):
        raise ToolError(
            f"Resource name '{name}' must use the asset- prefix.",
            code=EXIT_INVALID_INPUT,
        )
    for base in (assets_dir, os.environ.get("VOICE_TOOLKIT_ASSET_DIR"), module_dir()):
        if not base:
            continue
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    raise ToolError(
        f"Required asset '{name}' was not found.",
        code=EXIT_NOT_FOUND,
        hint="Pass --assets-dir pointing at the Skill Tool folder that holds "
             "the asset-* files.",
    )


def load_asset_json(name, assets_dir=None):
    path = resource_path(name, assets_dir)
    return read_json_file(path), path


def read_json_file(path):
    """Read JSON, supporting ``file.json#key`` to select a named sub-document.

    The ``#key`` form lets examples in ``asset-fixtures.json`` be passed
    directly, e.g. ``--input asset-fixtures.json#consent_example``.
    """
    pointer = None
    if "#" in path:
        path, _, pointer = path.partition("#")
    if not os.path.isfile(path):
        raise ToolError(f"Input file not found: {path}", code=EXIT_NOT_FOUND)
    size = os.path.getsize(path)
    if size > MAX_JSON_BYTES:
        raise ToolError(
            f"JSON input '{path}' is {size} bytes, above the "
            f"{MAX_JSON_BYTES} byte limit.",
            code=EXIT_INVALID_INPUT,
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"'{path}' is not valid JSON: {exc.msg} (line {exc.lineno}, "
            f"column {exc.colno}).",
            code=EXIT_INVALID_INPUT,
            hint="Validate the file with: python3 -m json.tool <file>",
        )
    if pointer:
        node = document
        for part in pointer.strip("/").split("/"):
            if not isinstance(node, dict) or part not in node:
                raise ToolError(
                    f"'{pointer}' was not found in '{path}'.",
                    code=EXIT_NOT_FOUND,
                    details={"available": sorted(node) if isinstance(node, dict) else []},
                )
            node = node[part]
        return node
    return document


def read_text_input(args, *, stdin_label="stdin"):
    """Read text from ``--input``, ``--text`` or stdin (in that order)."""
    value = getattr(args, "text", None)
    path = getattr(args, "input", None)
    if path:
        if not os.path.isfile(path):
            raise ToolError(f"Input file not found: {path}", code=EXIT_NOT_FOUND)
        if os.path.getsize(path) > MAX_TEXT_BYTES:
            raise ToolError(
                f"Input '{path}' exceeds the {MAX_TEXT_BYTES} byte text limit.",
                code=EXIT_INVALID_INPUT,
            )
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), path
    if value:
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ToolError(
                f"--text exceeds the {MAX_TEXT_BYTES} byte text limit.",
                code=EXIT_INVALID_INPUT,
            )
        return value, "<--text>"
    if sys.stdin is not None and not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data, stdin_label
    raise ToolError(
        "No input supplied.",
        code=EXIT_INVALID_INPUT,
        hint="Pass --input <file>, --text '<content>', or pipe text on stdin.",
    )


def dumps(obj, *, indent=2):
    return json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=False)


def write_json_file(path, obj, *, overwrite=True):
    if not overwrite and os.path.exists(path):
        raise ToolError(
            f"Refusing to overwrite existing file: {path}",
            code=EXIT_INVALID_INPUT,
            hint="Pass --force to overwrite.",
        )
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    atomic_write_text(path, dumps(obj) + "\n")
    return path


def atomic_write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tmp-voice-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path, *, limit=None):
    if not os.path.isfile(path):
        raise ToolError(f"File not found: {path}", code=EXIT_NOT_FOUND)
    digest = hashlib.sha256()
    remaining = limit
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining <= 0:
                    break
    return digest.hexdigest()


def canonical_json(obj):
    """Deterministic JSON used for fingerprints and provenance chains."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def stable_fingerprint(obj, length=16):
    return sha256_text(canonical_json(obj))[:length]


def clamp(value, low, high):
    return max(low, min(high, value))


def slugify(text, *, max_length=48):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_length].rstrip("-") or "item")


def parse_duration(text, *, default_unit="s"):
    """Parse '1.5', '900ms', '00:01:30.250', '2m' into seconds."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip().lower().replace(",", "")
    if not raw:
        return None
    match = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", raw)
    if match:
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    match = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", raw)
    if match:
        minutes, seconds = match.groups()
        return int(minutes) * 60 + float(seconds)
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(ms|s|m|h)?", raw)
    if not match:
        raise ToolError(
            f"Cannot parse duration '{text}'.",
            code=EXIT_INVALID_INPUT,
            hint="Use plain seconds ('4.2'), milliseconds ('420ms'), "
                 "or timecode ('00:00:04.200').",
        )
    value = float(match.group(1))
    unit = match.group(2) or default_unit
    return value * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]


def format_timecode(seconds, *, millis=True):
    seconds = max(0.0, float(seconds))
    if millis:
        total_ms = int(round(seconds * 1000))
        hours, rem = divmod(total_ms, 3600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, ms = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_srt_time(seconds):
    return format_timecode(seconds, millis=True).replace(".", ",")


# ---------------------------------------------------------------------------
# structured output
# ---------------------------------------------------------------------------

def emit(result, *, output=None, quiet=False, overwrite=False):
    """Print the result envelope and optionally write it to ``output``."""
    if not isinstance(result, dict):
        result = {"status": "ok", "result": result}
    result.setdefault("toolkit", TOOLKIT_NAME)
    result.setdefault("version", TOOLKIT_VERSION)
    text = dumps(result) + "\n"
    if output:
        parent = os.path.dirname(os.path.abspath(output))
        os.makedirs(parent, exist_ok=True)
        atomic_write_text(output, text)
        if not quiet:
            sys.stdout.write(text)
            sys.stderr.write(f"[{TOOLKIT_NAME}] wrote {output}\n")
    elif not quiet:
        sys.stdout.write(text)
    return result


def log(message, *, quiet=False):
    if not quiet:
        sys.stderr.write(f"[{TOOLKIT_NAME}] {message}\n")


# ---------------------------------------------------------------------------
# approval gate
# ---------------------------------------------------------------------------

SENSITIVE_ACTIONS = {
    "delete": "permanently deletes files",
    "overwrite": "replaces an existing deliverable",
    "revoke": "revokes a voice profile and flags dependent assets",
    "publish": "marks an asset as released to downstream users",
    "release": "releases a deliverable past the quality/rights gate",
    "credential": "handles credential or secret material",
}


def require_approval(args, action, *, detail="", force_ok=True):
    """Block destructive or rights-sensitive actions unless approved.

    Approval is granted with ``--approve`` on the command line (or by setting
    ``VOICE_TOOLKIT_APPROVAL_TOKEN`` to any non-empty value in automated
    pipelines). The check is explicit and logged so an agent cannot silently
    perform an irreversible step.
    """
    approved = bool(getattr(args, "approve", False)) or bool(
        os.environ.get("VOICE_TOOLKIT_APPROVAL_TOKEN", "").strip())
    if approved:
        return True
    env_note = "" if force_ok else " (this action cannot be undone)"
    reason = SENSITIVE_ACTIONS.get(action, action)
    raise ToolError(
        f"Approval required: this operation {reason}{env_note}."
        + (f" Detail: {detail}" if detail else ""),
        code=EXIT_BLOCKED,
        hint="Re-run with --approve after a human reviews the change, or set "
             "VOICE_TOOLKIT_APPROVAL_TOKEN for a pre-approved pipeline.",
        details={"action": action, "detail": detail},
    )


# ---------------------------------------------------------------------------
# argparse helpers
# ---------------------------------------------------------------------------

def add_output_args(parser):
    parser.add_argument("-o", "--output", help="Write the JSON result to this path.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the result envelope on stdout.")


def add_input_args(parser, *, text_help="Inline text input."):
    parser.add_argument("-i", "--input",
                        help="Read input from this file (Markdown, HTML, JSON, "
                             "text, or audio depending on the command).")
    parser.add_argument("--text", help=text_help)


def add_common_args(parser, *, timeout=120.0):
    parser.add_argument("--assets-dir",
                        help="Folder holding the asset-* files (defaults to the "
                             "Skill Tool folder).")
    parser.add_argument("--timeout", type=float, default=timeout,
                        help=f"Cooperative timeout in seconds (default {timeout:g}).")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing outputs.")
    parser.add_argument("--approve", action="store_true",
                        help="Explicit human approval for destructive or "
                             "rights-sensitive actions.")
    parser.add_argument("--pretty", action="store_true",
                        help="Indent the summary text output.")


def build_parser(prog, description, *, epilog=None):
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"{prog} {TOOLKIT_VERSION}")
    return parser


def guard(main_func):
    """Run a CLI body with structured error handling and stable exit codes."""

    def wrapper(argv=None):
        started = time.monotonic()
        try:
            code = main_func(argv)
            return EXIT_OK if code is None else int(code)
        except ToolError as exc:
            payload = exc.to_dict()
            payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
            sys.stdout.write(dumps(payload) + "\n")
            if exc.hint:
                sys.stderr.write(f"hint: {exc.hint}\n")
            return exc.code
        except KeyboardInterrupt:
            sys.stdout.write(dumps({
                "status": "cancelled",
                "message": "Interrupted by user.",
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }) + "\n")
            return EXIT_CANCELLED
        except BrokenPipeError:  # pragma: no cover
            return EXIT_OK
        except Exception as exc:  # pragma: no cover - defensive
            payload = {
                "status": "error",
                "code": EXIT_RUNTIME,
                "message": f"Unexpected failure: {type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=6).splitlines()[-6:],
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            sys.stdout.write(dumps(payload) + "\n")
            return EXIT_RUNTIME

    return wrapper


_SCHEMA_DOCUMENTS = {}


def validate_schema(instance, schema, path="$"):
    """Validate ``instance`` against the small JSON-Schema subset we ship.

    Supported keywords: type, required, properties, additionalProperties,
    items, enum, minimum, maximum, minLength, maxLength, minItems, maxItems,
    pattern, oneOf, anyOf, const. This keeps the shipped ``asset-schemas.json``
    authoritative without adding a third-party dependency.
    """
    errors = []

    def fail(message, sub="$"):
        errors.append(f"{path}{sub[1:]}: {message}" if sub != "$" else
                      f"{path}: {message}")

    def resolve_ref(pointer):
        """Resolve a local ``$ref`` such as ``#/$defs/voice_profile``."""
        if not pointer.startswith("#/"):
            raise ToolError(
                f"Unsupported $ref '{pointer}': only local references are allowed.",
                code=EXIT_INVALID_INPUT)
        node = None
        for document in _SCHEMA_DOCUMENTS.values():
            node = document
            for part in pointer[2:].split("/"):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    node = None
                    break
            if node is not None:
                return node
        raise ToolError(f"Unresolvable $ref '{pointer}' in schema.",
                        code=EXIT_INVALID_INPUT)

    def check(value, node, sub):
        if node is True or node is None:
            return
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            target = resolve_ref(node["$ref"])
            check(value, target, sub)
            node = {k: v for k, v in node.items() if k != "$ref"}
            if not node:
                return
        if "const" in node and value != node["const"]:
            fail(f"expected constant {node['const']!r}", sub)
        if "enum" in node and value not in node["enum"]:
            fail(f"expected one of {node['enum']}", sub)
        if "anyOf" in node:
            if not any(not validate_schema(value, option, "$")
                       for option in node["anyOf"]):
                fail("value does not match any allowed variant", sub)
        if "oneOf" in node:
            matches = sum(1 for option in node["oneOf"]
                          if not validate_schema(value, option, "$"))
            if matches != 1:
                fail(f"value must match exactly one of {len(node['oneOf'])} "
                     f"variants (matched {matches})", sub)
        node_type = node.get("type")
        if node_type:
            types = node_type if isinstance(node_type, list) else [node_type]
            if not _type_matches(value, types):
                fail(f"expected type {node_type}, got {type(value).__name__}", sub)
                return
        if isinstance(value, str):
            if "minLength" in node and len(value) < node["minLength"]:
                fail(f"shorter than minLength {node['minLength']}", sub)
            if "maxLength" in node and len(value) > node["maxLength"]:
                fail(f"longer than maxLength {node['maxLength']}", sub)
            if "pattern" in node and not re.search(node["pattern"], value):
                fail(f"does not match pattern {node['pattern']!r}", sub)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in node and value < node["minimum"]:
                fail(f"below minimum {node['minimum']}", sub)
            if "maximum" in node and value > node["maximum"]:
                fail(f"above maximum {node['maximum']}", sub)
        if isinstance(value, dict):
            for key in node.get("required", []):
                if key not in value:
                    fail(f"missing required property '{key}'", sub)
            props = node.get("properties", {})
            extra = node.get("additionalProperties", True)
            for key, item in value.items():
                if key in props:
                    check(item, props[key], f"{sub}.{key}")
                elif extra is False:
                    fail(f"unexpected property '{key}'", sub)
                elif isinstance(extra, dict):
                    check(item, extra, f"{sub}.{key}")
        if isinstance(value, list):
            if "minItems" in node and len(value) < node["minItems"]:
                fail(f"fewer than minItems {node['minItems']}", sub)
            if "maxItems" in node and len(value) > node["maxItems"]:
                fail(f"more than maxItems {node['maxItems']}", sub)
            if "items" in node:
                for index, item in enumerate(value):
                    check(item, node["items"], f"{sub}[{index}]")

    check(instance, schema, "$")
    return errors


def _type_matches(value, types):
    mapping = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }
    for name in types:
        if name == "integer":
            if isinstance(value, int) and not isinstance(value, bool):
                return True
        elif name == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return True
        elif name in mapping and isinstance(value, mapping[name]):
            return True
    return False


def schema_for(schema_doc, name):
    """Fetch ``$defs/<name>`` and register the document for ``$ref`` resolution."""
    defs = (schema_doc or {}).get("$defs", {})
    if schema_doc:
        _SCHEMA_DOCUMENTS[str(schema_doc.get("$id", "asset-schemas.json"))] = schema_doc
    if name not in defs:
        raise ToolError(
            f"Schema definition '{name}' is missing from asset-schemas.json.",
            code=EXIT_NOT_FOUND,
            details={"available": sorted(defs)},
        )
    return defs[name]


def assert_valid(instance, schema, *, label, code=EXIT_INVALID_INPUT):
    problems = validate_schema(instance, schema)
    if problems:
        raise ToolError(
            f"{label} failed validation ({len(problems)} problem(s)).",
            code=code,
            hint="Fix the reported fields; run the tool's 'validate' command "
                 "for a full report.",
            details={"problems": problems[:25]},
        )


def capabilities_report(module, entries, *, notes=None):
    """Uniform payload for each tool's ``capabilities`` subcommand."""
    return {
        "status": "ok",
        "tool": module,
        "toolkit": TOOLKIT_NAME,
        "version": TOOLKIT_VERSION,
        "capabilities": entries,
        **({"notes": notes} if notes else {}),
    }


def _self_description():
    return {
        "status": "ok",
        "module": "tools-runtime-common.py",
        "role": "Shared runtime helpers: structured results, errors, resource "
                "resolution, approval gate, hashing, deadlines.",
        "entry_point": False,
        "documented_in": "knowledge-readme.md",
    }


if __name__ == "__main__":
    print(dumps(_self_description()))
