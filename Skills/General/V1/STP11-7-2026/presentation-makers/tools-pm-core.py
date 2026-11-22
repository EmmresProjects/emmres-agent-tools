#!/usr/bin/env python3
"""tools-pm-core.py - shared library for the presentation-makers Skill Tool.

Load order: this module loads first. Every other tools-pm-*.py module and the
primary entry point tools-presentation-makers.py import it through
``import pm_core`` after registering it in ``sys.modules`` under that name.

Responsibilities: configuration, path resolution, structured diagnostics,
JSON schema validation (stdlib subset), colour/contrast maths, text metrics,
safe file IO, and the shared error/exit-code contract.

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

TOOL_NAME = "presentation-makers"
TOOL_VERSION = "1.0.0"
SPEC_VERSION = "1.0"

# Exit codes are part of the public contract (documented in knowledge-readme.md).
EXIT_OK = 0
EXIT_USAGE = 2          # invalid or missing input
EXIT_GATE = 3           # quality gate / audit failure
EXIT_EXTERNAL = 4       # required external tool or integration unavailable
EXIT_APPROVAL = 5       # approval required for a sensitive action
EXIT_INTERNAL = 6       # unexpected internal failure

LEVELS = ("error", "warning", "info")

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class PMError(Exception):
    """Structured, user-facing failure.

    exit_code maps to the documented exit contract; hint is printed with the
    error so the caller can recover without reading the source.
    """

    def __init__(self, message, code="error", exit_code=EXIT_USAGE, hint=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.hint = hint

    def as_dict(self):
        return {
            "status": "error",
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "exit_code": self.exit_code,
        }


def diag(level, code, message, where=None, hint=None):
    """One structured diagnostic record."""
    if level not in LEVELS:
        level = "info"
    rec = {"level": level, "code": code, "message": message}
    if where:
        rec["where"] = where
    if hint:
        rec["hint"] = hint
    return rec


def count_diagnostics(records):
    counts = {lvl: 0 for lvl in LEVELS}
    for rec in records:
        counts[rec.get("level", "info")] = counts.get(rec.get("level", "info"), 0) + 1
    return counts


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Paths and IO
# --------------------------------------------------------------------------
def asset_path(filename):
    """Resolve an asset-* file that ships beside the tool modules.

    Callers always pass the bare prefixed filename (e.g. 'asset-theme-presets.json')
    so the reference stays relative to the Skill Tool folder, which will sit
    under the future skill root.
    """
    candidate = os.path.join(MODULE_DIR, filename)
    if not os.path.isfile(candidate):
        raise PMError(
            "Shipped asset not found: %s" % filename,
            code="asset-missing",
            hint="Restore the asset-* file into the presentation-makers/ folder; "
                 "it must sit next to the tools-* modules.",
        )
    return candidate


def load_asset_json(filename):
    return read_json(asset_path(filename))


def resolve_path(path, base_dir=None):
    """Resolve a user supplied path.

    Absolute paths are honoured. Relative paths resolve against base_dir when
    given (usually the CLI working directory) so behaviour does not depend on
    where the package was extracted.
    """
    if not path:
        raise PMError("A path value is required", code="path-empty")
    path = os.path.expanduser(str(path))
    if os.path.isabs(path):
        return os.path.normpath(path)
    base = base_dir or os.getcwd()
    return os.path.normpath(os.path.join(base, path))


def read_text(path):
    resolved = resolve_path(path)
    if not os.path.isfile(resolved):
        raise PMError(
            "Input file not found: %s" % path,
            code="input-missing",
            hint="Check the path, or run the command with --help for the expected arguments.",
        )
    try:
        with open(resolved, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except OSError as exc:  # pragma: no cover - filesystem dependent
        raise PMError("Cannot read %s: %s" % (path, exc), code="input-unreadable")


def read_json(path):
    raw = read_text(path)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PMError(
            "Invalid JSON in %s: %s (line %d, column %d)" % (path, exc.msg, exc.lineno, exc.colno),
            code="json-invalid",
            hint="Fix the JSON syntax; run 'validate' to see schema problems separately.",
        )


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def write_text(path, text, force=True):
    """Atomic text write (temp file in the same directory, then replace)."""
    resolved = resolve_path(path)
    parent = os.path.dirname(resolved) or "."
    ensure_dir(parent)
    if os.path.exists(resolved) and not force:
        raise PMError(
            "Refusing to overwrite existing file: %s" % path,
            code="output-exists",
            exit_code=EXIT_APPROVAL,
            hint="Pass --force to overwrite an existing output.",
        )
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".pm-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, resolved)
    except OSError as exc:  # pragma: no cover - filesystem dependent
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise PMError("Cannot write %s: %s" % (path, exc), code="output-unwritable")
    return resolved


def write_json(path, data, force=True, indent=2):
    return write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n", force=force)


def relpath_for_display(path, base=None):
    try:
        return os.path.relpath(path, base or os.getcwd())
    except ValueError:  # pragma: no cover - different drives on Windows
        return path


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "schema_version": "1.0",
    "paths": {
        "output_dir": "presentation-output",
        "catalog": "asset-skill-catalog.json",
        "brief_schema": "asset-brief-schema.json",
        "deck_spec_schema": "asset-deck-spec-schema.json",
        "narrative_frameworks": "asset-narrative-frameworks.json",
        "slide_roles": "asset-slide-roles.json",
        "platform_profiles": "asset-platform-profiles.json",
        "theme_presets": "asset-theme-presets.json",
        "quality_gates": "asset-quality-gates.json",
        "coverage_map": "asset-coverage-map.json",
    },
    "limits": {
        "max_slides": 120,
        "max_sections": 24,
        "max_bullets_per_slide": 7,
        "max_chars_per_bullet": 180,
        "max_words_per_minute": 180,
        "max_file_bytes": 26214400,
        "max_input_bytes": 2097152,
        "max_media_per_deck": 40,
        "external_tool_timeout_seconds": 180,
        "max_retries": 2,
    },
    "policy": {
        "require_overwrite_approval": True,
        "require_external_tool_approval": True,
        "allow_network": False,
        "allow_arbitrary_code": False,
        "fail_on_warning_in_strict_mode": True,
    },
    "audit": {
        "contrast_minimum_normal": 4.5,
        "contrast_minimum_large": 3.0,
        "large_text_pt": 18.0,
        "min_body_font_pt": 14.0,
        "max_flashes_per_second": 3,
        "max_animation_objects_per_slide": 8,
    },
    "render": {
        "default_theme": "aurora",
        "slide_width_in": 13.333,
        "slide_height_in": 7.5,
        "pptx_creator": "presentation-makers Skill Tool",
        "include_notes": True,
        "include_pdf_handout": True,
    },
    "ai": {
        "provider": "local-deterministic",
        "network": False,
        "note": "Only the offline deterministic drafting engine is implemented; "
                "external model providers are deliberately not contacted.",
    },
    "logging": {"level": "info"},
}


def deep_merge(base, override):
    """Recursive merge where override wins; lists are replaced wholesale."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path=None):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        data = read_json(path)
        if not isinstance(data, dict):
            raise PMError("Config root must be a JSON object", code="config-invalid")
        config = deep_merge(config, data)
    validate_config(config)
    return config


def validate_config(config):
    problems = []
    limits = config.get("limits", {})
    for key in ("max_slides", "max_sections", "max_bullets_per_slide", "max_chars_per_bullet"):
        value = limits.get(key)
        if not isinstance(value, int) or value <= 0:
            problems.append("limits.%s must be a positive integer" % key)
    timeout = limits.get("external_tool_timeout_seconds")
    if not isinstance(timeout, int) or not (1 <= timeout <= 3600):
        problems.append("limits.external_tool_timeout_seconds must be an integer between 1 and 3600")
    audit = config.get("audit", {})
    if not isinstance(audit.get("contrast_minimum_normal"), (int, float)):
        problems.append("audit.contrast_minimum_normal must be numeric")
    if problems:
        raise PMError("Invalid configuration: " + "; ".join(problems), code="config-invalid")
    return True


def config_asset(config, key):
    """Load a shipped asset named by a config path entry."""
    name = config.get("paths", {}).get(key)
    if not name:
        raise PMError("Configuration is missing paths.%s" % key, code="config-invalid")
    return load_asset_json(os.path.basename(name))


# --------------------------------------------------------------------------
# JSON schema validation (subset: the keywords the shipped schemas use)
# --------------------------------------------------------------------------
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, type_name):
    if type_name == "integer" and isinstance(value, bool):
        return False
    if type_name == "number" and isinstance(value, bool):
        return False
    expected = _TYPES.get(type_name)
    if expected is None:
        return True
    if type_name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def validate_schema(instance, schema, path="$"):
    """Return a list of diagnostics for a JSON Schema subset."""
    errors = []

    def fail(msg, where):
        errors.append(diag("error", "schema", msg, where=where))

    def walk(value, node, where):
        if node is True or node is None:
            return
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            fail("unsupported $ref in schema", where)
            return
        declared = node.get("type")
        if declared is not None:
            types = declared if isinstance(declared, list) else [declared]
            if not any(_type_ok(value, t) for t in types):
                fail("expected %s" % " or ".join(types), where)
                return
        if "enum" in node and value not in node["enum"]:
            fail("must be one of %s" % ", ".join(repr(v) for v in node["enum"]), where)
        if isinstance(value, str):
            if "minLength" in node and len(value) < node["minLength"]:
                fail("shorter than minLength %d" % node["minLength"], where)
            if "maxLength" in node and len(value) > node["maxLength"]:
                fail("longer than maxLength %d" % node["maxLength"], where)
            if "pattern" in node:
                try:
                    if re.search(node["pattern"], value) is None:
                        fail("does not match pattern %s" % node["pattern"], where)
                except re.error:  # pragma: no cover - schema authoring error
                    fail("invalid pattern in schema", where)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in node and value < node["minimum"]:
                fail("below minimum %s" % node["minimum"], where)
            if "maximum" in node and value > node["maximum"]:
                fail("above maximum %s" % node["maximum"], where)
        if isinstance(value, list):
            if "minItems" in node and len(value) < node["minItems"]:
                fail("needs at least %d item(s)" % node["minItems"], where)
            if "maxItems" in node and len(value) > node["maxItems"]:
                fail("allows at most %d item(s)" % node["maxItems"], where)
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    walk(item, item_schema, "%s[%d]" % (where, idx))
            elif isinstance(item_schema, list):
                for idx, item in enumerate(value[: len(item_schema)]):
                    walk(item, item_schema[idx], "%s[%d]" % (where, idx))
        if isinstance(value, dict):
            props = node.get("properties", {})
            for key in node.get("required", []):
                if key not in value:
                    fail("missing required property '%s'" % key, where)
            for key, sub in props.items():
                if key in value:
                    walk(value[key], sub, "%s.%s" % (where, key))
            additional = node.get("additionalProperties", True)
            if additional is False:
                for key in value:
                    if key not in props:
                        fail("unexpected property '%s'" % key, where)
            elif isinstance(additional, dict):
                for key, sub_value in value.items():
                    if key not in props:
                        walk(sub_value, additional, "%s.%s" % (where, key))
        for sub in node.get("allOf", []):
            walk(value, sub, where)
        for idx, sub in enumerate(node.get("anyOf", [])):
            probe = validate_schema(value, sub, where)
            if probe:
                if idx == len(node["anyOf"]) - 1:
                    fail("does not satisfy any allowed variant", where)
                continue
            break
        if "oneOf" in node:
            matches = [s for s in node["oneOf"] if not validate_schema(value, s, where)]
            if len(matches) != 1:
                fail("must satisfy exactly one variant (matched %d)" % len(matches), where)

    walk(instance, schema, path)
    return errors


def validate_against_asset(instance, config, key):
    schema = config_asset(config, key)
    return validate_schema(instance, schema)


# --------------------------------------------------------------------------
# Colour, contrast, text metrics
# --------------------------------------------------------------------------
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(value):
    if not isinstance(value, str):
        raise PMError("Colour must be a hex string like #1B2A41", code="color-invalid")
    match = HEX_RE.match(value.strip())
    if not match:
        raise PMError("Colour must be a 6-digit hex string like #1B2A41 (got %r)" % value,
                      code="color-invalid")
    digits = match.group(1)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _srgb_channel(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def contrast_ratio(fg_hex, bg_hex):
    l1 = relative_luminance(parse_hex(fg_hex))
    l2 = relative_luminance(parse_hex(bg_hex))
    lighter, darker = max(l1, l2), min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def wcag_result(ratio, large_text=False, config=None):
    config = config or {}
    audit = config.get("audit", DEFAULT_CONFIG["audit"])
    need = audit.get("contrast_minimum_large" if large_text else "contrast_minimum_normal", 4.5)
    level = "AAA" if ratio >= 7.0 else "AA" if ratio >= need else "fail"
    return {"ratio": ratio, "required": need, "level": level, "passes": ratio >= need}


def mix(fg_hex, bg_hex, amount):
    """Blend fg toward bg by amount (0..1) - used for tinted panels."""
    fg, bg = parse_hex(fg_hex), parse_hex(bg_hex)
    return to_hex(tuple(fg[i] + (bg[i] - fg[i]) * amount for i in range(3)))


CHAR_WIDTH_FACTOR = 0.52  # average glyph advance relative to font size, sans-serif


def estimate_lines(text, width_in, font_pt, char_factor=CHAR_WIDTH_FACTOR):
    """Estimate wrapped line count for a text block inside a given width."""
    if not text:
        return 0
    if width_in <= 0 or font_pt <= 0:
        return 999
    chars_per_line = max(1, int((width_in * 72.0) / (font_pt * char_factor)))
    total = 0
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            total += 1
            continue
        line_len = 0
        lines = 1
        for word in words:
            add = len(word) + (1 if line_len else 0)
            if line_len + add > chars_per_line and line_len:
                lines += 1
                line_len = len(word)
            else:
                line_len += add
        total += lines
    return total


def text_height_in(text, width_in, font_pt, line_spacing=1.18):
    return estimate_lines(text, width_in, font_pt) * (font_pt * line_spacing) / 72.0


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9'’\-]+", text or ""))


def speaking_seconds(text, words_per_minute=140):
    words = word_count(text)
    if words_per_minute <= 0:
        return 0
    return int(round(words / float(words_per_minute) * 60))


def truncate(text, limit):
    text = " ".join(str(text or "").split())
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",;:") + "…"


def slugify(text, fallback="item"):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or fallback


def sentence_case(text):
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    return text[0].upper() + text[1:]


def strip_html(fragment):
    """Remove tags, scripts, styles and collapse whitespace (HTML intake)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return text


# --------------------------------------------------------------------------
# Envelope helpers
# --------------------------------------------------------------------------
def new_result(command, status="ok"):
    return {
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "command": command,
        "status": status,
        "generated_at": now_iso(),
        "diagnostics": [],
        "outputs": [],
    }


def finish(result, strict=False):
    counts = count_diagnostics(result.get("diagnostics", []))
    result["counts"] = counts
    if counts.get("error"):
        result["status"] = "error"
    elif counts.get("warning") and strict:
        result["status"] = "error"
    elif counts.get("warning"):
        result["status"] = "warning"
    else:
        result["status"] = "ok"
    return result


def clamp(value, low, high):
    return max(low, min(high, value))


def safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":  # pragma: no cover - library module
    print("%s core %s - library module, use tools-presentation-makers.py" % (TOOL_NAME, TOOL_VERSION))
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        assert contrast_ratio("#000000", "#FFFFFF") == 21.0
        assert wcag_result(4.6)["passes"] is True
        assert slugify("AI-Assisted Deck!") == "ai-assisted-deck"
        print("core selftest ok")
