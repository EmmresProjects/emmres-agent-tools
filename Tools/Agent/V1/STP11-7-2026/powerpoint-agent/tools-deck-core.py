#!/usr/bin/env python3
"""Shared presentation core for the powerpoint-agent skill tool.

This module is not executed directly.  It is loaded by ``tools-pptx-agent.py``
(and by the sibling engine modules) through the ``load_sibling`` helper so the
whole skill tool keeps working as a flat folder of prefixed files.

Responsibilities
----------------
* dependency detection (python-pptx, Pillow) with actionable errors
* structured errors, JSON I/O, atomic writes, run logging, cooperative deadlines
* configuration loading and deep merging
* theme presets, colour resolution, WCAG contrast maths
* deck-spec validation
* deterministic deck construction for every supported slide layout
* deck inspection: stats, outline, text frames, font/size resolution, geometry
* PowerPoint section-list (p14:sectionLst) reading and writing
* speaker-note helpers and reading-order normalisation utilities
"""

from __future__ import annotations

import datetime as _dt
import errno
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

SKILL_TOOL_NAME = "powerpoint-agent"
TOOL_VERSION = "1.0.0"
SPEC_VERSION = "1.0"

# ---------------------------------------------------------------------------
# optional dependencies
# ---------------------------------------------------------------------------

PPTX_AVAILABLE = False
PPTX_IMPORT_ERROR = ""
try:  # pragma: no cover - environment dependent
    from pptx import Presentation  # type: ignore
    from pptx.util import Emu, Inches, Pt  # type: ignore
    from pptx.dml.color import RGBColor  # type: ignore
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN  # type: ignore
    from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE  # type: ignore
    from pptx.chart.data import CategoryChartData  # type: ignore
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION  # type: ignore
    from pptx.oxml.ns import qn  # type: ignore
    from lxml import etree  # type: ignore

    PPTX_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    PPTX_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

PIL_AVAILABLE = False
PIL_IMPORT_ERROR = ""
try:  # pragma: no cover
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    PIL_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    PIL_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

EMU_PER_INCH = 914400
EMU_PER_POINT = 12700


# ---------------------------------------------------------------------------
# errors and result envelopes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DEPENDENCY = 3
EXIT_VALIDATION = 4
EXIT_GATE_FAILED = 5
EXIT_SAFETY = 6
EXIT_RUNTIME = 7
EXIT_INTERRUPTED = 130


class AgentError(Exception):
    """Base class for every error the tool reports in a structured way."""

    code = "E_RUNTIME"
    exit_code = EXIT_RUNTIME

    def __init__(self, message: str, *, hint: str = "", details: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details


class UsageError(AgentError):
    code = "E_USAGE"
    exit_code = EXIT_USAGE


class DependencyError(AgentError):
    code = "E_DEPENDENCY"
    exit_code = EXIT_DEPENDENCY


class ValidationError(AgentError):
    code = "E_VALIDATION"
    exit_code = EXIT_VALIDATION


class SafetyError(AgentError):
    code = "E_SAFETY"
    exit_code = EXIT_SAFETY


class GateError(AgentError):
    code = "E_GATE"
    exit_code = EXIT_GATE_FAILED


class InterruptedError_(AgentError):
    code = "E_INTERRUPTED"
    exit_code = EXIT_INTERRUPTED


def require_pptx() -> None:
    """Raise a clear dependency error when python-pptx is unavailable."""

    if not PPTX_AVAILABLE:
        raise DependencyError(
            "python-pptx is required for this command but could not be imported.",
            hint="Install it with: python -m pip install -r tools-requirements.txt",
            details={"import_error": PPTX_IMPORT_ERROR},
        )


def require_pil() -> None:
    if not PIL_AVAILABLE:
        raise DependencyError(
            "Pillow is required for this command but could not be imported.",
            hint="Install it with: python -m pip install -r tools-requirements.txt",
            details={"import_error": PIL_IMPORT_ERROR},
        )


# ---------------------------------------------------------------------------
# module loading (flat skill-tool folder, prefixed filenames)
# ---------------------------------------------------------------------------

MODULE_CACHE: Dict[str, Any] = {}


def skill_root() -> str:
    """Absolute path of the skill-tool folder (directory holding this file)."""

    return os.path.dirname(os.path.abspath(__file__))


def load_sibling(module_name: str, filename: str, root: Optional[str] = None):
    """Load a sibling ``tools-*.py`` module by file path, cached by name."""

    cache_key = "%s@%s" % (module_name, root or skill_root())
    if cache_key in MODULE_CACHE:
        return MODULE_CACHE[cache_key]
    base = root or skill_root()
    path = os.path.join(base, filename)
    if not os.path.isfile(path):
        raise DependencyError(
            "Required skill-tool module is missing: %s" % filename,
            hint="Restore the file next to the entry point; all tool files live in one flat folder.",
            details={"expected_path": path},
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise DependencyError("Unable to create an import spec for %s" % filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    MODULE_CACHE[cache_key] = module
    return module


def asset_path(filename: str, root: Optional[str] = None) -> str:
    """Resolve an ``asset-*`` resource relative to the skill root."""

    return os.path.join(root or skill_root(), filename)


# ---------------------------------------------------------------------------
# small utilities: text, numbers, time
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 60) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = _SLUG_RE.sub("-", text).strip("-")
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "item"


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return "%.1f %s" % (value, unit) if unit != "B" else "%d B" % int(value)
        value /= 1024.0
    return "%.1f GB" % value


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def emu_to_inches(value: Optional[int]) -> Optional[float]:
    if value is None:
        return None
    return round(value / EMU_PER_INCH, 4)


def inches_to_emu(value: float) -> int:
    return int(round(value * EMU_PER_INCH))


def pt_to_emu(value: float) -> int:
    return int(round(value * EMU_PER_POINT))


def truncate(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# structured IO
# ---------------------------------------------------------------------------

MAX_JSON_BYTES = 32 * 1024 * 1024


def load_json(path: str, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    """Read a JSON document with explicit size and parse errors."""

    if not os.path.isfile(path):
        raise ValidationError("JSON file not found: %s" % path, details={"path": path})
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ValidationError(
            "JSON file is larger than the allowed limit.",
            details={"path": path, "bytes": size, "limit_bytes": max_bytes},
        )
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "Invalid JSON in %s at line %d column %d: %s" % (os.path.basename(path), exc.lineno, exc.colno, exc.msg),
            details={"path": path, "line": exc.lineno, "column": exc.colno},
        )
    except OSError as exc:
        raise ValidationError("Unable to read %s: %s" % (path, exc))


def dump_json(data: Any, path: str, *, indent: int = 2) -> str:
    """Atomically write a JSON document, creating parent folders as needed."""

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".pptx-agent-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def write_text(text: str, path: str) -> str:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pptx-agent-", suffix=".txt.tmp", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# logging and cooperative deadlines
# ---------------------------------------------------------------------------

class RunLog:
    """In-memory run log with optional JSONL mirror and event collection."""

    def __init__(self, path: Optional[str] = None, level: str = "info"):
        self.path = path
        self.level = level
        self.events: List[Dict[str, Any]] = []
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def log(self, level: str, message: str, **fields: Any) -> None:
        event = {"ts": utc_now_iso(), "level": level, "message": message}
        if fields:
            event["fields"] = fields
        self.events.append(event)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def info(self, message: str, **fields: Any) -> None:
        self.log("info", message, **fields)

    def warn(self, message: str, **fields: Any) -> None:
        self.log("warning", message, **fields)

    def step(self, message: str, **fields: Any) -> None:
        self.log("step", message, **fields)


class Deadline:
    """Cooperative timeout checked between units of work (slides, media, jobs)."""

    def __init__(self, seconds: Optional[float]):
        self.seconds = float(seconds) if seconds else 0.0
        self.started = time.monotonic()

    @property
    def enabled(self) -> bool:
        return self.seconds > 0

    def remaining(self) -> float:
        if not self.enabled:
            return float("inf")
        return self.seconds - (time.monotonic() - self.started)

    def check(self, context: str = "operation") -> None:
        if self.enabled and self.remaining() <= 0:
            raise AgentError(
                "Timed out after %.0fs while running %s." % (self.seconds, context),
                hint="Raise --timeout, reduce the batch, or split the deck into smaller files.",
                details={"timeout_seconds": self.seconds, "context": context},
            )


def install_sigint_guard() -> None:
    """Raise KeyboardInterrupt instead of dying silently mid-write."""

    def _handler(signum, frame):  # pragma: no cover - signal path
        raise KeyboardInterrupt()

    try:
        import signal

        signal.signal(signal.SIGINT, _handler)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# path validation
# ---------------------------------------------------------------------------

PPTX_EXTENSIONS = (".pptx", ".pptm", ".potx", ".ppsx")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg", ".emf", ".wmf")


def resolve_input(path: str, *, exts: Sequence[str] = PPTX_EXTENSIONS, label: str = "input file") -> str:
    """Validate that an input path exists, is a file, and has an allowed extension."""

    if not path:
        raise ValidationError("No %s path was provided." % label)
    absolute = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(absolute):
        raise ValidationError(
            "%s not found: %s" % (label.capitalize(), path),
            hint="Check the path, or run 'doctor' to confirm the working environment.",
            details={"path": absolute},
        )
    if not os.path.isfile(absolute):
        raise ValidationError("%s is not a file: %s" % (label.capitalize(), path))
    if exts and not absolute.lower().endswith(tuple(exts)):
        raise ValidationError(
            "Unsupported %s extension '%s'. Allowed: %s" % (label, os.path.splitext(absolute)[1], ", ".join(exts)),
        )
    return absolute


def resolve_output(
    path: str,
    *,
    overwrite: bool = False,
    exts: Optional[Sequence[str]] = None,
    create_parent: bool = True,
    label: str = "output",
) -> str:
    """Validate an output path and refuse silent overwrites by default."""

    if not path:
        raise ValidationError("No %s path was provided." % label)
    absolute = os.path.abspath(os.path.expanduser(path))
    if exts and not absolute.lower().endswith(tuple(exts)):
        raise ValidationError(
            "%s must use one of these extensions: %s" % (label.capitalize(), ", ".join(exts)),
            details={"path": absolute},
        )
    if os.path.isdir(absolute):
        raise ValidationError("%s path is an existing directory: %s" % (label.capitalize(), path))
    parent = os.path.dirname(absolute) or "."
    if create_parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(absolute) and not overwrite:
        raise ValidationError(
            "%s already exists and --overwrite was not supplied: %s" % (label.capitalize(), path),
            hint="Pass --overwrite to replace it, or choose a new output path.",
            details={"path": absolute},
        )
    return absolute


def resolve_output_dir(path: str, *, overwrite: bool = False) -> str:
    absolute = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(absolute) and not os.path.isdir(absolute):
        raise ValidationError("Output path must be a folder: %s" % path)
    if os.path.isdir(absolute) and os.listdir(absolute) and not overwrite:
        raise ValidationError(
            "Output folder is not empty and --overwrite was not supplied: %s" % path,
            hint="Choose an empty folder or pass --overwrite.",
            details={"path": absolute},
        )
    os.makedirs(absolute, exist_ok=True)
    return absolute


def atomic_copy(src: str, dst: str) -> str:
    directory = os.path.dirname(os.path.abspath(dst)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pptx-agent-", suffix=os.path.splitext(dst)[1] or ".tmp", dir=directory)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return dst


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": "1.0",
    "paths": {
        "default_out_dir": "pptx-agent-output",
        "temp_prefix": "pptx-agent-",
    },
    "limits": {
        "max_input_mb": 300,
        "max_slides": 400,
        "max_bullets_per_slide": 60,
        "max_table_rows": 200,
        "max_table_columns": 20,
        "max_chart_series": 12,
        "max_chart_categories": 60,
        "max_media_files": 500,
        "max_batch_jobs": 200,
        "max_text_chars": 4000,
        "max_notes_chars": 20000,
    },
    "timeouts": {"default_seconds": 300, "export_seconds": 240, "batch_seconds": 900},
    "audit": {
        "min_font_pt": 14,
        "min_title_pt": 24,
        "min_contrast_normal": 4.5,
        "min_contrast_large": 3.0,
        "large_text_pt": 18,
        "edge_margin_in": 0.3,
        "max_slide_budget": 40,
        "max_file_mb": 60,
        "max_image_pixels": 3840,
        "duplicate_media_min_kb": 64,
        "placeholder_patterns": [
            "click to add",
            "lorem ipsum",
            "todo",
            "tbd",
            "xxx",
            "insert text",
            "sample text",
        ],
        "fail_on": "error",
    },
    "format": {
        "min_font_pt": 14,
        "title_pt": 32,
        "body_pt": 16,
        "force_fonts": False,
        "normalize_geometry": True,
        "add_footer": True,
        "add_slide_numbers": True,
        "remove_empty_placeholders": True,
    },
    "repair": {
        "max_findings": 500,
        "auto_fix_rules": [],
        "dry_run": True,
    },
    "optimize": {
        "max_image_dimension_px": 1920,
        "jpeg_quality": 82,
        "drop_unused_media": True,
        "strip_thumbnails": False,
        "dry_run": True,
    },
    "render": {
        "backend": "auto",
        "preview_scale": 1.0,
        "max_preview_slides": 60,
        "width_px": 1280,
        "font_search_paths": [
            "/usr/share/fonts/truetype/dejavu",
            "/usr/share/fonts/truetype/liberation",
            "/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
            "C:/Windows/Fonts",
        ],
        "font_map": {
            "heading": ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf"],
            "body": ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "LiberationSans-Regular.ttf"],
            "mono": ["DejaVuSansMono.ttf", "CourierNew.ttf", "cour.ttf"],
        },
    },
    "export": {
        "libreoffice_binary": "",
        "soffice_candidates": ["soffice", "libreoffice", "/usr/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"],
        "pdf_filter": "impress_pdf_Export",
        "timeout_seconds": 240,
    },
    "batch": {"stop_on_error": False, "retries": 0, "timeout_seconds": 600, "stage_outputs": True},
    "safety": {
        "approval_required_commands": ["repair", "optimize", "replace"],
        "blocked_protocols": ["javascript:", "data:", "vbscript:", "file:"],
        "max_external_links": 200,
        "allow_in_place_with_approval": True,
    },
    "defaults": {"json": False, "overwrite": False, "theme": "corporate-blue"},
}


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overlay`` onto a copy of ``base``."""

    result = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(explicit_path: Optional[str] = None, *, root: Optional[str] = None) -> Dict[str, Any]:
    """Load defaults < asset-default-config.json < explicit --config file."""

    root = root or skill_root()
    config = dict(DEFAULT_CONFIG)
    default_asset = asset_path("asset-default-config.json", root)
    if os.path.isfile(default_asset):
        overlay = load_json(default_asset)
        if not isinstance(overlay, dict):
            raise ValidationError("asset-default-config.json must contain a JSON object.")
        config = deep_merge(config, overlay)
    env_path = os.environ.get("PPTX_AGENT_CONFIG", "").strip()
    candidate = explicit_path or (env_path if env_path else None)
    if candidate:
        overlay = load_json(resolve_input(candidate, exts=(".json",), label="config file"))
        if not isinstance(overlay, dict):
            raise ValidationError("Configuration file must contain a JSON object.")
        config = deep_merge(config, overlay)
        config["_config_source"] = os.path.abspath(candidate)
    else:
        config["_config_source"] = default_asset if os.path.isfile(default_asset) else "builtin-defaults"
    return config


def cfg(config: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """Read a dotted configuration key with a fallback."""

    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# colour utilities
# ---------------------------------------------------------------------------

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(value: Any, fallback: str = "000000") -> str:
    """Return an uppercase 6-digit hex string without '#', or a fallback."""

    if isinstance(value, str):
        match = HEX_RE.match(value.strip())
        if match:
            return match.group(1).upper()
    return fallback


def rgb_tuple(value: str) -> Tuple[int, int, int]:
    hex_value = parse_hex(value)
    return (int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16))


def relative_luminance(value: str) -> float:
    def channel(raw: int) -> float:
        srgb = raw / 255.0
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = rgb_tuple(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    l1 = relative_luminance(foreground)
    l2 = relative_luminance(background)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def blend(foreground: str, background: str, alpha: float) -> str:
    fr, fg, fb = rgb_tuple(foreground)
    br, bg, bb = rgb_tuple(background)
    alpha = clamp(alpha, 0.0, 1.0)
    mixed = (
        int(round(fr * alpha + br * (1 - alpha))),
        int(round(fg * alpha + bg * (1 - alpha))),
        int(round(fb * alpha + bb * (1 - alpha))),
    )
    return "%02X%02X%02X" % mixed


THEME_COLOR_MAP = {
    "DARK_1": "dk1",
    "LIGHT_1": "lt1",
    "DARK_2": "dk2",
    "LIGHT_2": "lt2",
    "ACCENT_1": "accent1",
    "ACCENT_2": "accent2",
    "ACCENT_3": "accent3",
    "ACCENT_4": "accent4",
    "ACCENT_5": "accent5",
    "ACCENT_6": "accent6",
    "HYPERLINK": "hlink",
    "FOLLOWED_HYPERLINK": "folHlink",
    "TEXT_1": "dk1",
    "BACKGROUND_1": "lt1",
    "TEXT_2": "dk2",
    "BACKGROUND_2": "lt2",
    "NOT_THEME_COLOR": "",
    "MIXED": "",
}


# ---------------------------------------------------------------------------
# theme
# ---------------------------------------------------------------------------

REQUIRED_THEME_KEYS = ("name", "fonts", "colors", "typography", "grid")

DEFAULT_THEME: Dict[str, Any] = {
    "name": "fallback-neutral",
    "description": "Built-in neutral theme used when no preset can be loaded.",
    "fonts": {"heading": "Arial", "body": "Arial", "mono": "Courier New"},
    "colors": {
        "background": "#FFFFFF",
        "surface": "#F1F4F8",
        "primary": "#1F2A37",
        "secondary": "#33465C",
        "accent": "#2F6FB0",
        "accent_soft": "#9BB6D2",
        "text": "#16202B",
        "text_inverse": "#FFFFFF",
        "muted": "#5B6B7B",
        "border": "#C7D1DC",
        "success": "#2E7D5B",
        "warning": "#B7791F",
        "danger": "#A62B2B",
    },
    "typography": {
        "title_pt": 32,
        "subtitle_pt": 18,
        "section_pt": 38,
        "heading_pt": 20,
        "body_pt": 16,
        "small_pt": 11,
        "kpi_pt": 38,
        "quote_pt": 26,
        "line_spacing": 1.15,
        "bullet_indent_in": 0.28,
    },
    "grid": {
        "margin_x_in": 0.62,
        "margin_y_in": 0.45,
        "title_top_in": 0.45,
        "content_top_in": 1.5,
        "footer_y_in": 6.95,
        "gap_in": 0.3,
    },
    "art": {"title_band": True, "accent_bar": True, "corner_radius_in": 0.1},
    "table": {
        "header_fill": "primary",
        "header_text": "text_inverse",
        "row_fill": "background",
        "alt_row_fill": "surface",
        "border": "border",
        "body_pt": 12,
        "header_pt": 12,
    },
    "chart": {
        "palette": ["accent", "secondary", "accent_soft", "success", "warning", "danger"],
        "gridline": "border",
        "font_pt": 11,
    },
    "footer": {"show_slide_number": True, "show_date": False, "show_footer": True},
}


class Theme:
    """Resolved theme preset with colour-name indirection and safe lookups."""

    def __init__(self, data: Dict[str, Any]):
        merged = deep_merge(DEFAULT_THEME, data or {})
        self.data = merged
        self.name = str(merged.get("name") or "custom")
        # raw values are kept so palette references such as "surface": "primary"
        # can be resolved lazily by :meth:`color`
        self.colors: Dict[str, str] = {
            str(key): str(value) for key, value in (merged.get("colors") or {}).items()
        }
        self.fonts: Dict[str, str] = dict(merged.get("fonts") or {})
        self.typography: Dict[str, Any] = dict(merged.get("typography") or {})
        self.grid: Dict[str, Any] = dict(merged.get("grid") or {})
        self.art: Dict[str, Any] = dict(merged.get("art") or {})
        self.table: Dict[str, Any] = dict(merged.get("table") or {})
        self.chart: Dict[str, Any] = dict(merged.get("chart") or {})
        self.footer: Dict[str, Any] = dict(merged.get("footer") or {})

    # -- colours ---------------------------------------------------------
    def color(self, name: str, default: str = "text") -> str:
        """Resolve a colour name to a 6-digit hex string (with reference following)."""

        if not name:
            return self.color(default, "000000")
        key = str(name).strip()
        if key.startswith("#"):
            return parse_hex(key, "000000")
        seen = set()
        while key not in seen:
            seen.add(key)
            raw = self.colors.get(key)
            if raw is None:
                if HEX_RE.match(key):
                    return parse_hex(key, "000000")
                return self.color(default, "000000") if default != key else "000000"
            if HEX_RE.match(raw):
                return parse_hex(raw, "000000")
            key = raw.strip()
        return "000000"

    def palette(self) -> List[str]:
        names = self.chart.get("palette") or ["accent", "secondary", "accent_soft"]
        return [self.color(str(name)) for name in names]

    # -- fonts -----------------------------------------------------------
    def font(self, role: str) -> str:
        return str(self.fonts.get(role) or self.fonts.get("body") or "Arial")

    def pt(self, key: str, fallback: float = 16.0) -> float:
        try:
            return float(self.typography.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    def grid_in(self, key: str, fallback: float) -> float:
        try:
            return float(self.grid.get(key, fallback))
        except (TypeError, ValueError):
            return fallback


def readable_on(theme: "Theme", background: str, *, prefer: str = "text") -> str:
    """Pick the theme ink (text / text_inverse) with the higher contrast."""

    candidates = [theme.color("text"), theme.color("text_inverse")]
    if prefer == "inverse":
        candidates.reverse()
    best = max(candidates, key=lambda color: contrast_ratio(color, background))
    return best


def is_dark(color: str) -> bool:
    return relative_luminance(color) < 0.45


def load_theme(name_or_path: Optional[str], root: Optional[str] = None) -> Theme:
    """Load a theme preset by bundled name, or from an explicit JSON path."""

    root = root or skill_root()
    candidate = (name_or_path or "corporate-blue").strip()
    presets = {
        "corporate-blue": "asset-brand-preset-corporate-blue.json",
        "editorial-warm": "asset-brand-preset-editorial-warm.json",
        "midnight-teal": "asset-brand-preset-midnight-teal.json",
    }
    if candidate.lower() in presets:
        path = asset_path(presets[candidate.lower()], root)
        if not os.path.isfile(path):
            raise ValidationError(
                "Bundled theme preset is missing: %s" % presets[candidate.lower()],
                hint="Restore the asset file or pass a --brand path to a custom preset JSON.",
            )
        return Theme(load_json(path))
    if os.path.isfile(candidate):
        data = load_json(candidate)
        if not isinstance(data, dict):
            raise ValidationError("Theme preset must be a JSON object: %s" % candidate)
        return Theme(data)
    raise ValidationError(
        "Unknown theme preset '%s'." % candidate,
        hint="Use one of: %s, or pass a path to a theme JSON file." % ", ".join(sorted(presets)),
        details={"presets": sorted(presets)},
    )


def validate_theme(data: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["theme preset must be a JSON object"]
    for key in REQUIRED_THEME_KEYS:
        if key not in data:
            problems.append("missing required key '%s'" % key)
    for key, value in (data.get("colors") or {}).items():
        if isinstance(value, str) and not value.startswith("#") and not HEX_RE.match(value):
            problems.append("color '%s' is neither a hex value nor a palette reference" % key)
        elif isinstance(value, str) and HEX_RE.match(value) is None:
            problems.append("color '%s' has an invalid value '%s'" % (key, value))
    for key in ("title_pt", "body_pt", "heading_pt"):
        if key in (data.get("typography") or {}):
            try:
                if float(data["typography"][key]) <= 0:
                    problems.append("typography.%s must be positive" % key)
            except (TypeError, ValueError):
                problems.append("typography.%s must be numeric" % key)
    return problems


def list_theme_presets(root: Optional[str] = None) -> List[Dict[str, Any]]:
    root = root or skill_root()
    found: List[Dict[str, Any]] = []
    for filename in sorted(os.listdir(root)):
        if not (filename.startswith("asset-brand-preset-") and filename.endswith(".json")):
            continue
        try:
            data = load_json(os.path.join(root, filename))
        except AgentError:
            continue
        found.append(
            {
                "file": filename,
                "name": data.get("name", os.path.splitext(filename)[0]),
                "description": data.get("description", ""),
                "problem_count": len(validate_theme(data)),
            }
        )
    return found


# ---------------------------------------------------------------------------
# text measurement (used for overflow estimation in audits and builders)
# ---------------------------------------------------------------------------

_FONT_FILE_CACHE: Dict[str, Optional[str]] = {}
_PIL_FONT_CACHE: Dict[Tuple[str, int], Any] = {}


def _candidate_font_files(config: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    config = config or {}
    fonts = cfg(config, "render.font_map", {}) or {}
    defaults = DEFAULT_CONFIG["render"]["font_map"]
    merged = {key: list(fonts.get(key) or defaults.get(key) or []) for key in ("heading", "body", "mono")}
    return merged


def find_font_file(role: str = "body", config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Locate a usable TrueType font file for measurement and preview rendering."""

    if not PIL_AVAILABLE:
        return None
    cache_key = "%s|%s" % (role, bool(config))
    if cache_key in _FONT_FILE_CACHE:
        return _FONT_FILE_CACHE[cache_key]
    candidates = _candidate_font_files(config).get(role, [])
    search_paths = list(cfg(config, "render.font_search_paths", DEFAULT_CONFIG["render"]["font_search_paths"]) or [])
    search_paths += [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        "C:/Windows/Fonts",
        "/System/Library/Fonts",
    ]
    found: Optional[str] = None
    for directory in search_paths:
        if not directory or not os.path.isdir(directory):
            continue
        for candidate in candidates:
            direct = os.path.join(directory, candidate)
            if os.path.isfile(direct):
                found = direct
                break
            for root, _dirs, files in os.walk(directory):
                match = next((name for name in files if name.lower() == candidate.lower()), None)
                if match:
                    found = os.path.join(root, match)
                    break
            if found:
                break
        if found:
            break
    _FONT_FILE_CACHE[cache_key] = found
    return found


def _pil_font(role: str, size_px: int, config: Optional[Dict[str, Any]] = None):
    if not PIL_AVAILABLE:
        return None
    path = find_font_file(role, config)
    key = (path or "default", int(size_px))
    if key in _PIL_FONT_CACHE:
        return _PIL_FONT_CACHE[key]
    font = None
    if path:
        try:
            font = ImageFont.truetype(path, int(size_px))
        except Exception:
            font = None
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    _PIL_FONT_CACHE[key] = font
    return font


def text_width_pt(text: str, font_pt: float, *, bold: bool = False, config: Optional[Dict[str, Any]] = None) -> float:
    """Approximate rendered width of a single line in points."""

    if not text:
        return 0.0
    font_pt = max(1.0, float(font_pt))
    font = _pil_font("heading" if bold else "body", max(6, int(round(font_pt * 4))), config)
    if font is not None:
        try:
            width_px = font.getlength(text)
            return round(width_px / 4.0, 2)
        except Exception:
            pass
    # conservative fallback: average glyph width of a humanist sans face
    return round(len(text) * font_pt * (0.54 if bold else 0.51), 2)


def wrap_line_count(text: str, width_in: float, font_pt: float, *, bold: bool = False, config: Optional[Dict[str, Any]] = None) -> int:
    """Estimate how many wrapped lines ``text`` needs inside ``width_in``."""

    width_pt = max(1.0, width_in * 72.0)
    words = str(text).split()
    if not words:
        return 0
    lines = 1
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if text_width_pt(trial, font_pt, bold=bold, config=config) <= width_pt or not current:
            current = trial
        else:
            lines += 1
            current = word
    return lines


def estimate_text_block_height_in(
    paragraphs: Sequence[Dict[str, Any]],
    width_in: float,
    default_pt: float,
    line_spacing: float = 1.15,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    """Estimate the rendered height of a paragraph list inside a text frame."""

    total_pt = 0.0
    for para in paragraphs:
        text = str(para.get("text", ""))
        size = float(para.get("size_pt") or default_pt)
        bold = bool(para.get("bold"))
        indent_in = float(para.get("indent_in") or 0.0)
        lines = max(1, wrap_line_count(text, max(0.4, width_in - indent_in), size, bold=bold, config=config))
        total_pt += lines * size * 1.22 * line_spacing
        total_pt += float(para.get("space_after_pt") or 4.0)
    return round(total_pt / 72.0, 3)


# ---------------------------------------------------------------------------
# deck spec: schema, validation, normalisation
# ---------------------------------------------------------------------------

LAYOUTS = {
    "title": {"required": ["title"], "optional": ["subtitle", "notes", "background", "eyebrow"]},
    "section": {"required": ["title"], "optional": ["subtitle", "notes", "background", "eyebrow"]},
    "closing": {"required": ["title"], "optional": ["subtitle", "notes", "background", "eyebrow"]},
    "bullets": {"required": ["title", "bullets"], "optional": ["subtitle", "notes", "aside", "background"]},
    "title-and-content": {"required": ["title", "bullets"], "optional": ["subtitle", "notes", "aside", "background"]},
    "two-column": {"required": ["title"], "optional": ["left", "right", "notes", "background"]},
    "kpi": {"required": ["title", "kpis"], "optional": ["subtitle", "notes", "background"]},
    "quote": {"required": ["quote"], "optional": ["attribution", "title", "notes", "background"]},
    "table": {"required": ["title", "table"], "optional": ["notes", "caption", "background"]},
    "chart": {"required": ["title", "chart"], "optional": ["notes", "caption", "background"]},
    "image-right": {"required": ["title", "image"], "optional": ["bullets", "notes", "caption", "background"]},
    "blank": {"required": [], "optional": ["title", "notes", "background"]},
}

LAYOUT_ALIASES = {
    "agenda": "bullets",
    "content": "bullets",
    "title-content": "bullets",
    "title_and_content": "bullets",
    "two-col": "two-column",
    "columns": "two-column",
    "kpis": "kpi",
    "metrics": "kpi",
    "data-table": "table",
    "graph": "chart",
    "image": "image-right",
    "picture": "image-right",
    "cover": "title",
    "opening": "title",
    "end": "closing",
    "thanks": "closing",
    "divider": "section",
}

CHART_TYPES = {
    "column": "COLUMN_CLUSTERED",
    "column-clustered": "COLUMN_CLUSTERED",
    "column-stacked": "COLUMN_STACKED",
    "bar": "BAR_CLUSTERED",
    "bar-clustered": "BAR_CLUSTERED",
    "line": "LINE_MARKERS",
    "line-smooth": "LINE_MARKERS",
    "pie": "PIE",
    "doughnut": "DOUGHNUT",
    "area": "AREA",
    "scatter": "XY_SCATTER",
}

BULLET_LEVEL_CHARS = ["•", "–", "·"]


def normalize_layout(name: str) -> str:
    key = slugify(name)
    key = LAYOUT_ALIASES.get(key, key)
    if key not in LAYOUTS:
        raise ValidationError(
            "Unknown slide layout '%s'." % name,
            hint="Supported layouts: %s" % ", ".join(sorted(LAYOUTS)),
            details={"layout": name},
        )
    return key


def _coerce_bullets(raw: Any, where: str, errors: List[str], limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    bullets: List[Dict[str, Any]] = []
    if raw is None:
        return bullets
    if not isinstance(raw, list):
        errors.append("%s must be a list of strings or bullet objects." % where)
        return bullets
    if len(raw) > int(limits.get("max_bullets_per_slide", 60)):
        errors.append("%s has %d bullets; limit is %d." % (where, len(raw), limits.get("max_bullets_per_slide", 60)))
    for position, item in enumerate(raw, start=1):
        if isinstance(item, str):
            text, level, bold = item, 0, False
        elif isinstance(item, dict):
            text = item.get("text")
            level = item.get("level", 0)
            bold = bool(item.get("bold", False))
        else:
            errors.append("%s[%d] must be a string or object with a 'text' key." % (where, position))
            continue
        if not isinstance(text, str) or not text.strip():
            errors.append("%s[%d] has empty or non-string text." % (where, position))
            continue
        if len(text) > int(limits.get("max_text_chars", 4000)):
            errors.append("%s[%d] exceeds the %d character limit." % (where, position, limits.get("max_text_chars", 4000)))
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 0
        if not 0 <= level <= 2:
            errors.append("%s[%d] level must be 0, 1, or 2." % (where, position))
            level = 0
        bullets.append({"text": text.strip(), "level": level, "bold": bold})
    return bullets


def _coerce_text(value: Any, where: str, errors: List[str], limits: Dict[str, Any], *, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            errors.append("%s is required." % where)
        return ""
    if not isinstance(value, str):
        errors.append("%s must be a string." % where)
        return ""
    text = value.strip()
    if len(text) > int(limits.get("max_text_chars", 4000)):
        errors.append("%s exceeds the %d character limit." % (where, limits.get("max_text_chars", 4000)))
    if required and not text:
        errors.append("%s is required." % where)
    return text


def validate_spec(
    spec: Any,
    *,
    config: Optional[Dict[str, Any]] = None,
    spec_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate and normalise a deck spec. Returns ``(normalized_spec, warnings)``.

    Raises :class:`ValidationError` when one or more blocking problems exist.
    """

    config = config or DEFAULT_CONFIG
    limits = cfg(config, "limits", {}) or {}
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(spec, dict):
        raise ValidationError("Deck spec must be a JSON object at the top level.")

    version = str(spec.get("spec_version", SPEC_VERSION))
    if version != SPEC_VERSION:
        warnings.append("spec_version '%s' differs from supported '%s'; continuing with best effort." % (version, SPEC_VERSION))

    base_dir = os.path.dirname(os.path.abspath(spec_path)) if spec_path else os.getcwd()

    meta_raw = spec.get("meta") or {}
    if not isinstance(meta_raw, dict):
        errors.append("meta must be an object.")
        meta_raw = {}
    meta = {
        "title": _coerce_text(meta_raw.get("title"), "meta.title", errors, limits),
        "author": _coerce_text(meta_raw.get("author"), "meta.author", errors, limits),
        "subject": _coerce_text(meta_raw.get("subject"), "meta.subject", errors, limits),
        "company": _coerce_text(meta_raw.get("company"), "meta.company", errors, limits),
        "language": _coerce_text(meta_raw.get("language"), "meta.language", errors, limits) or "en-US",
        "keywords": meta_raw.get("keywords") or [],
    }
    if not isinstance(meta["keywords"], list):
        errors.append("meta.keywords must be a list of strings.")
        meta["keywords"] = []

    theme_choice = spec.get("theme", cfg(config, "defaults.theme", "corporate-blue"))
    theme_overrides: Dict[str, Any] = {}
    if isinstance(theme_choice, dict):
        theme_overrides = theme_choice.get("overrides") or {}
        theme_choice = theme_choice.get("preset") or cfg(config, "defaults.theme", "corporate-blue")
        if not isinstance(theme_overrides, dict):
            errors.append("theme.overrides must be an object.")
            theme_overrides = {}
    if not isinstance(theme_choice, str):
        errors.append("theme must be a preset name or an object with a 'preset' key.")
        theme_choice = "corporate-blue"

    slide_size_raw = spec.get("slide_size") or {}
    if not isinstance(slide_size_raw, dict):
        errors.append("slide_size must be an object with width_in and height_in.")
        slide_size_raw = {}
    try:
        width_in = float(slide_size_raw.get("width_in", 13.333))
        height_in = float(slide_size_raw.get("height_in", 7.5))
    except (TypeError, ValueError):
        errors.append("slide_size width_in/height_in must be numeric.")
        width_in, height_in = 13.333, 7.5
    if not (5.0 <= width_in <= 60.0 and 3.0 <= height_in <= 40.0):
        errors.append("slide_size must be between 5x3 and 60x40 inches.")
    if abs((width_in / max(height_in, 0.1)) - (16 / 9)) > 0.35:
        warnings.append("slide_size %.2fx%.2f in is not close to 16:9; layouts assume a widescreen canvas." % (width_in, height_in))

    footer_raw = spec.get("footer") or {}
    if not isinstance(footer_raw, dict):
        errors.append("footer must be an object.")
        footer_raw = {}
    footer = {
        "text": _coerce_text(footer_raw.get("text"), "footer.text", errors, limits),
        "show_footer": bool(footer_raw.get("show_footer", bool(footer_raw.get("text")))),
        "show_slide_number": bool(footer_raw.get("show_slide_number", True)),
        "show_date": bool(footer_raw.get("show_date", False)),
    }

    raw_slides = spec.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise ValidationError(
            "Deck spec must contain a non-empty 'slides' array.",
            hint="Use asset-deck-spec-example.json as a starting point.",
        )
    max_slides = int(limits.get("max_slides", 400))
    if len(raw_slides) > max_slides:
        raise ValidationError("Deck spec has %d slides; the configured limit is %d." % (len(raw_slides), max_slides))

    slides: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    for position, raw in enumerate(raw_slides, start=1):
        if not isinstance(raw, dict):
            errors.append("slides[%d] must be an object." % position)
            continue
        layout = raw.get("layout", "bullets")
        try:
            layout = normalize_layout(str(layout))
        except ValidationError as exc:
            errors.append("slides[%d]: %s" % (position, exc.message))
            continue
        slide_id = slugify(str(raw.get("id") or "%s-%02d" % (layout, position)))
        if slide_id in seen_ids:
            errors.append("slides[%d] reuses slide id '%s' (already used by slide %d)." % (position, slide_id, seen_ids[slide_id]))
            slide_id = "%s-%02d" % (slide_id, position)
        seen_ids[slide_id] = position

        known = set(LAYOUTS[layout]["required"]) | set(LAYOUTS[layout]["optional"]) | {"id", "layout"}
        unknown = set(raw.keys()) - known
        if unknown:
            warnings.append("slides[%d] ignores unsupported keys: %s" % (position, ", ".join(sorted(unknown))))

        slide: Dict[str, Any] = {
            "id": slide_id,
            "layout": layout,
            "number": position,
            "title": _coerce_text(raw.get("title"), "slides[%d].title" % position, errors, limits, required="title" in LAYOUTS[layout]["required"]),
            "subtitle": _coerce_text(raw.get("subtitle"), "slides[%d].subtitle" % position, errors, limits),
            "eyebrow": _coerce_text(raw.get("eyebrow"), "slides[%d].eyebrow" % position, errors, limits),
            "notes": _coerce_text(raw.get("notes"), "slides[%d].notes" % position, errors, limits),
            "caption": _coerce_text(raw.get("caption"), "slides[%d].caption" % position, errors, limits),
            "background": _coerce_text(raw.get("background"), "slides[%d].background" % position, errors, limits),
            "quote": _coerce_text(raw.get("quote"), "slides[%d].quote" % position, errors, limits, required=layout == "quote"),
            "attribution": _coerce_text(raw.get("attribution"), "slides[%d].attribution" % position, errors, limits),
        }
        if slide["notes"] and len(slide["notes"]) > int(limits.get("max_notes_chars", 20000)):
            errors.append("slides[%d].notes exceeds the %d character limit." % (position, limits.get("max_notes_chars", 20000)))
        if slide["background"] and not (slide["background"].startswith("#") and len(slide["background"]) in (7,)):
            errors.append("slides[%d].background must be a #RRGGBB value." % position)
        if slide["background"] and len(slide["background"]) == 7:
            slide["background"] = "#" + parse_hex(slide["background"])

        if layout in ("bullets", "title-and-content", "image-right"):
            slide["bullets"] = _coerce_bullets(raw.get("bullets"), "slides[%d].bullets" % position, errors, limits)
            if layout in ("bullets", "title-and-content") and not slide["bullets"]:
                errors.append("slides[%d].bullets must contain at least one bullet." % position)
        slide["aside"] = _coerce_text(raw.get("aside"), "slides[%d].aside" % position, errors, limits)

        if layout == "two-column":
            for side in ("left", "right"):
                block = raw.get(side) or {}
                if not isinstance(block, dict):
                    errors.append("slides[%d].%s must be an object with a heading and bullets." % (position, side))
                    block = {}
                slide[side] = {
                    "heading": _coerce_text(block.get("heading"), "slides[%d].%s.heading" % (position, side), errors, limits),
                    "bullets": _coerce_bullets(block.get("bullets"), "slides[%d].%s.bullets" % (position, side), errors, limits),
                    "body": _coerce_text(block.get("body"), "slides[%d].%s.body" % (position, side), errors, limits),
                }
            if not any(slide[side]["bullets"] or slide[side]["body"] for side in ("left", "right")):
                errors.append("slides[%d] two-column layout needs bullets or body text in left or right." % position)

        if layout == "kpi":
            kpis_raw = raw.get("kpis")
            kpis: List[Dict[str, str]] = []
            if not isinstance(kpis_raw, list) or not kpis_raw:
                errors.append("slides[%d].kpis must be a non-empty list." % position)
            elif len(kpis_raw) > 4:
                errors.append("slides[%d].kpis supports at most 4 cards." % position)
            else:
                for index, item in enumerate(kpis_raw, start=1):
                    if not isinstance(item, dict) or not str(item.get("value", "")).strip():
                        errors.append("slides[%d].kpis[%d] needs a 'value'." % (position, index))
                        continue
                    kpis.append(
                        {
                            "value": _coerce_text(item.get("value"), "slides[%d].kpis[%d].value" % (position, index), errors, limits),
                            "label": _coerce_text(item.get("label"), "slides[%d].kpis[%d].label" % (position, index), errors, limits),
                            "detail": _coerce_text(item.get("detail"), "slides[%d].kpis[%d].detail" % (position, index), errors, limits),
                        }
                    )
            slide["kpis"] = kpis

        if layout == "table":
            table_raw = raw.get("table") or {}
            if not isinstance(table_raw, dict):
                errors.append("slides[%d].table must be an object." % position)
                table_raw = {}
            headers = table_raw.get("headers") or []
            rows = table_raw.get("rows") or []
            if not isinstance(headers, list) or not headers:
                errors.append("slides[%d].table.headers must be a non-empty list." % position)
                headers = []
            if not isinstance(rows, list) or not rows:
                errors.append("slides[%d].table.rows must be a non-empty list of rows." % position)
                rows = []
            if len(rows) > int(limits.get("max_table_rows", 200)):
                errors.append("slides[%d].table has %d rows; limit is %d." % (position, len(rows), limits.get("max_table_rows", 200)))
            column_count = len(headers)
            if column_count > int(limits.get("max_table_columns", 20)):
                errors.append("slides[%d].table has %d columns; limit is %d." % (position, column_count, limits.get("max_table_columns", 20)))
            normalized_rows: List[List[str]] = []
            for row_index, row in enumerate(rows, start=1):
                if not isinstance(row, list):
                    errors.append("slides[%d].table.rows[%d] must be a list." % (position, row_index))
                    continue
                if column_count and len(row) != column_count:
                    errors.append(
                        "slides[%d].table.rows[%d] has %d cells but %d headers." % (position, row_index, len(row), column_count)
                    )
                normalized_rows.append(["" if cell is None else str(cell) for cell in row])
            widths_raw = table_raw.get("column_widths_in") or []
            widths: List[float] = []
            if widths_raw and (not isinstance(widths_raw, list) or len(widths_raw) != column_count):
                errors.append("slides[%d].table.column_widths_in must list one width per column." % position)
            elif widths_raw:
                try:
                    widths = [float(value) for value in widths_raw]
                except (TypeError, ValueError):
                    errors.append("slides[%d].table.column_widths_in must be numeric." % position)
            slide["table"] = {
                "headers": [str(item) for item in headers],
                "rows": normalized_rows,
                "column_widths_in": widths,
                "first_row_header": bool(table_raw.get("first_row_header", True)),
            }

        if layout == "chart":
            chart_raw = raw.get("chart") or {}
            if not isinstance(chart_raw, dict):
                errors.append("slides[%d].chart must be an object." % position)
                chart_raw = {}
            chart_type = slugify(str(chart_raw.get("chart_type", "column")))
            if chart_type not in CHART_TYPES:
                errors.append(
                    "slides[%d].chart.chart_type '%s' is unsupported (allowed: %s)."
                    % (position, chart_type, ", ".join(sorted(CHART_TYPES)))
                )
                chart_type = "column"
            categories_raw = chart_raw.get("categories") or []
            if not isinstance(categories_raw, list) or not categories_raw:
                errors.append("slides[%d].chart.categories must be a non-empty list." % position)
                categories_raw = []
            if len(categories_raw) > int(limits.get("max_chart_categories", 60)):
                errors.append("slides[%d].chart has too many categories (limit %d)." % (position, limits.get("max_chart_categories", 60)))
            series_raw = chart_raw.get("series") or []
            series: List[Dict[str, Any]] = []
            if not isinstance(series_raw, list) or not series_raw:
                errors.append("slides[%d].chart.series must be a non-empty list." % position)
                series_raw = []
            if len(series_raw) > int(limits.get("max_chart_series", 12)):
                errors.append("slides[%d].chart has too many series (limit %d)." % (position, limits.get("max_chart_series", 12)))
            for series_index, item in enumerate(series_raw, start=1):
                if not isinstance(item, dict):
                    errors.append("slides[%d].chart.series[%d] must be an object." % (position, series_index))
                    continue
                values = item.get("values") or []
                if not isinstance(values, list) or len(values) != len(categories_raw):
                    errors.append(
                        "slides[%d].chart.series[%d].values must have %d numeric values."
                        % (position, series_index, len(categories_raw))
                    )
                    values = []
                numeric: List[float] = []
                for value in values:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        errors.append("slides[%d].chart.series[%d] contains a non-numeric value." % (position, series_index))
                        numeric = []
                        break
                    numeric.append(float(value))
                series.append({"name": _coerce_text(item.get("name"), "slides[%d].chart.series[%d].name" % (position, series_index), errors, limits) or "Series %d" % series_index, "values": numeric})
            slide["chart"] = {
                "chart_type": chart_type,
                "categories": [str(item) for item in categories_raw],
                "series": series,
                "value_suffix": _coerce_text(chart_raw.get("value_suffix"), "slides[%d].chart.value_suffix" % position, errors, limits),
                "show_legend": bool(chart_raw.get("show_legend", len(series) > 1)),
            }

        if layout == "image-right":
            image_raw = raw.get("image") or {}
            if isinstance(image_raw, str):
                image_raw = {"path": image_raw}
            if not isinstance(image_raw, dict):
                errors.append("slides[%d].image must be a string path or an object." % position)
                image_raw = {}
            rel = str(image_raw.get("path") or "").strip()
            resolved = ""
            if not rel:
                errors.append("slides[%d].image.path is required." % position)
            else:
                candidate = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base_dir, rel))
                if not os.path.isfile(candidate):
                    alt = os.path.normpath(os.path.join(skill_root(), rel))
                    candidate = alt if os.path.isfile(alt) else candidate
                if not os.path.isfile(candidate):
                    errors.append(
                        "slides[%d].image.path does not resolve: %s" % (position, rel),
                        )
                elif not candidate.lower().endswith(RASTER_IMAGE_EXTENSIONS):
                    errors.append(
                        "slides[%d].image.path extension '%s' cannot be embedded by python-pptx; "
                        "convert it to PNG or JPEG first (raster formats: %s)."
                        % (position, os.path.splitext(candidate)[1], ", ".join(RASTER_IMAGE_EXTENSIONS))
                    )
                else:
                    resolved = candidate
            slide["image"] = {
                "path": resolved,
                "alt": _coerce_text(image_raw.get("alt"), "slides[%d].image.alt" % (position), errors, limits),
                "fit": slugify(str(image_raw.get("fit", "contain"))),
            }
            if slide["image"]["path"] and not slide["image"]["alt"]:
                warnings.append("slides[%d].image.alt is empty; the deck will fail the alt-text accessibility rule." % position)
            if slide["image"]["fit"] not in ("contain", "cover"):
                errors.append("slides[%d].image.fit must be 'contain' or 'cover'." % position)

        slides.append(slide)

    sections_raw = spec.get("sections") or []
    sections: List[Dict[str, Any]] = []
    if sections_raw and not isinstance(sections_raw, list):
        errors.append("sections must be a list of {name, slide_ids} objects.")
    elif sections_raw:
        assigned: Dict[str, str] = {}
        for index, item in enumerate(sections_raw, start=1):
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                errors.append("sections[%d] needs a name." % index)
                continue
            ids = item.get("slide_ids") or []
            if not isinstance(ids, list) or not ids:
                errors.append("sections[%d].slide_ids must be a non-empty list." % index)
                continue
            unknown_ids = [str(x) for x in ids if slugify(str(x)) not in seen_ids]
            if unknown_ids:
                errors.append("sections[%d] references unknown slide ids: %s" % (index, ", ".join(unknown_ids)))
            for raw_id in ids:
                key = slugify(str(raw_id))
                if key in assigned:
                    errors.append("slide id '%s' appears in sections '%s' and '%s'." % (key, assigned[key], item["name"]))
                assigned[key] = str(item["name"])
            sections.append({"name": str(item["name"]).strip(), "slide_ids": [slugify(str(x)) for x in ids]})

    normalized = {
        "spec_version": SPEC_VERSION,
        "meta": meta,
        "theme": {"preset": theme_choice, "overrides": theme_overrides},
        "slide_size": {"width_in": width_in, "height_in": height_in},
        "footer": footer,
        "sections": sections,
        "slides": slides,
        "options": spec.get("options") or {},
        "source_path": os.path.abspath(spec_path) if spec_path else "",
    }

    if errors:
        raise ValidationError(
            "Deck spec validation failed with %d problem(s)." % len(errors),
            hint="Fix the listed items; see knowledge-readme.md for the full schema.",
            details={"errors": errors, "warnings": warnings, "spec_path": spec_path or ""},
        )
    return normalized, warnings


# ---------------------------------------------------------------------------
# presentation building primitives
# ---------------------------------------------------------------------------

RASTER_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")

try:  # python-pptx / lxml are optional at import time
    from lxml import etree as _etree  # type: ignore
except Exception:  # pragma: no cover
    _etree = None


def _sub(parent, tag: str):
    return _etree.SubElement(parent, qn(tag))


def _remove_shape(shape) -> None:
    element = shape._element
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _set_solid(shape, hex_color: str, *, alpha: Optional[float] = None) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(parse_hex(hex_color))
    if alpha is not None:
        solid_fill = shape.fill._xPr.find(qn("a:solidFill"))
        if solid_fill is not None:
            srgb = solid_fill.find(qn("a:srgbClr"))
            if srgb is not None:
                for existing in srgb.findall(qn("a:alpha")):
                    srgb.remove(existing)
                _sub(srgb, "a:alpha").set("val", str(int(clamp(alpha, 0, 1) * 100000)))


def _no_line(shape) -> None:
    shape.line.fill.background()


def _line(shape, hex_color: str, width_pt: float = 1.0) -> None:
    shape.line.color.rgb = RGBColor.from_string(parse_hex(hex_color))
    shape.line.width = Pt(width_pt)


def _add_rect(slide, x_in: float, y_in: float, w_in: float, h_in: float, fill: str, *, line: Optional[str] = None,
              rounded: bool = False, radius_in: float = 0.08, name: str = ""):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in))
    _set_solid(shape, fill)
    if line:
        _line(shape, line, 0.75)
    else:
        _no_line(shape)
    shape.shadow.inherit = False
    if rounded:
        try:
            shape.adjustments[0] = clamp(radius_in / max(min(w_in, h_in), 0.01), 0.0, 0.5)
        except Exception:
            pass
    if name:
        shape.name = name
    return shape


def _add_textbox(slide, x_in: float, y_in: float, w_in: float, h_in: float, *, name: str = "",
                 anchor: str = "top", wrap: bool = True):
    box = slide.shapes.add_textbox(Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in))
    frame = box.text_frame
    frame.word_wrap = wrap
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = Inches(0.02)
    frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = {
        "top": MSO_ANCHOR.TOP,
        "middle": MSO_ANCHOR.MIDDLE,
        "bottom": MSO_ANCHOR.BOTTOM,
    }.get(anchor, MSO_ANCHOR.TOP)
    if name:
        box.name = name
    return box


def _style_run(run, *, font: str, size_pt: float, color: str, bold: bool = False, italic: bool = False,
               link: str = "") -> None:
    run.font.name = font
    run.font.size = Pt(round(size_pt, 1))
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor.from_string(parse_hex(color))
    if link:
        run.hyperlink.address = link


def _set_bullet(paragraph, char: str, indent_in: float, *, level: int = 0) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    marl = inches_to_emu(indent_in * (level + 1))
    p_pr.set("marL", str(marl))
    # Hanging indent: paragraphs without an explicit indent set the first line
    # at 0 (overriding the bullet character) and text overlaps the bullet glyph.
    p_pr.set("indent", str(-inches_to_emu(indent_in * (level + 1))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buBlip"):
        for existing in p_pr.findall(qn(tag)):
            p_pr.remove(existing)
    bu_font = _sub(p_pr, "a:buFont")
    bu_font.set("typeface", "Arial")
    bu_char = _sub(p_pr, "a:buChar")
    bu_char.set("char", char)


def _clear_bullet(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buChar", "a:buAutoNum", "a:buBlip"):
        for existing in p_pr.findall(qn(tag)):
            p_pr.remove(existing)
    if p_pr.find(qn("a:buNone")) is None:
        _sub(p_pr, "a:buNone")


def _write_paragraph(paragraph, text: str, *, font: str, size_pt: float, color: str, bold: bool = False,
                     italic: bool = False, align=None, space_after_pt: float = 4.0, line_spacing: float = 1.15,
                     link: str = "", bullet: Optional[str] = None, indent_in: float = 0.28, level: int = 0) -> None:
    paragraph.text = ""
    run = paragraph.add_run()
    run.text = text
    _style_run(run, font=font, size_pt=size_pt, color=color, bold=bold, italic=italic, link=link)
    if align is not None:
        paragraph.alignment = align
    paragraph.line_spacing = line_spacing
    paragraph.space_after = Pt(space_after_pt)
    paragraph.space_before = Pt(0)
    if bullet:
        _set_bullet(paragraph, bullet, indent_in, level=level)
        paragraph.level = level
    else:
        _clear_bullet(paragraph)


def _fit_font_size(text: str, width_in: float, height_in: float, start_pt: float, min_pt: float,
                   *, bold: bool = False, line_spacing: float = 1.15, config: Optional[Dict[str, Any]] = None,
                   max_lines: int = 12) -> float:
    size = start_pt
    while size > min_pt:
        lines = wrap_line_count(text, width_in, size, bold=bold, config=config)
        needed = lines * size * 1.22 * line_spacing / 72.0
        if lines <= max_lines and needed <= height_in:
            return size
        size -= 1.0
    return max(min_pt, size)


def _paint_background(slide, hex_color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor.from_string(parse_hex(hex_color))


def _style_title_placeholder(prs_ctx, slide, text: str, *, x_in: float, y_in: float, w_in: float, h_in: float,
                             size_pt: float, color: str, font: str, bold: bool = True, align=None,
                             line_spacing: float = 1.0, anchor: str = "bottom") -> Any:
    title = slide.shapes.title
    if title is None:
        raise ValidationError("Slide layout is missing a title placeholder; cannot place a title.")
    title.left, title.top, title.width, title.height = Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in)
    frame = title.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = Inches(0.0)
    frame.margin_right = Inches(0.0)
    frame.margin_top = Inches(0.0)
    frame.margin_bottom = Inches(0.0)
    frame.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    fitted = _fit_font_size(text, w_in, h_in, size_pt, max(12.0, size_pt * 0.6), bold=bold,
                            line_spacing=line_spacing, config=prs_ctx.config, max_lines=3)
    frame.text = ""
    paragraph = frame.paragraphs[0]
    _write_paragraph(paragraph, text, font=font, size_pt=fitted, color=color, bold=bold, align=align,
                     space_after_pt=0, line_spacing=line_spacing)
    return title


def _style_body_placeholder(prs_ctx, shape, *, x_in: float, y_in: float, w_in: float, h_in: float) -> Any:
    shape.left, shape.top, shape.width, shape.height = Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in)
    frame = shape.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = Inches(0.05)
    frame.margin_right = Inches(0.05)
    frame.margin_top = Inches(0.03)
    frame.margin_bottom = Inches(0.03)
    return shape


def _fill_bullets(prs_ctx, frame, bullets: Sequence[Dict[str, Any]], *, size_pt: float, color: str, font: str,
                  width_in: float, height_in: float, line_spacing: float, indent_in: float,
                  align=None) -> float:
    """Write bullets into a frame, shrinking the font until the block fits."""

    size = size_pt
    while True:
        paragraphs = [
            {"text": bullet["text"], "size_pt": size, "bold": bullet.get("bold"),
             "indent_in": indent_in * (bullet.get("level", 0) + 1), "space_after_pt": 6}
            for bullet in bullets
        ]
        needed = estimate_text_block_height_in(paragraphs, width_in, size, line_spacing, prs_ctx.config)
        if needed <= height_in or size <= 10.0:
            break
        size -= 1.0
    frame.text = ""
    for index, bullet in enumerate(bullets):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        level = int(bullet.get("level", 0))
        _write_paragraph(
            paragraph,
            bullet["text"],
            font=font,
            size_pt=size,
            color=color,
            bold=bool(bullet.get("bold")),
            align=align,
            space_after_pt=max(2.0, size * 0.35),
            line_spacing=line_spacing,
            bullet=BULLET_LEVEL_CHARS[min(level, len(BULLET_LEVEL_CHARS) - 1)],
            indent_in=indent_in,
            level=level,
        )
    return size


def _slide_bg(ctx: "BuildContext", slide) -> str:
    try:
        return slide_background_hex(slide, ctx.prs, ctx.scheme or None)
    except Exception:
        return ctx.theme.color("background")


def _is_dark_slide(ctx: "BuildContext", slide) -> bool:
    return relative_luminance(_slide_bg(ctx, slide)) < 0.45


def _muted_on(ctx: "BuildContext", slide) -> str:
    """Muted text colour that stays readable on the slide's own background."""

    background = _slide_bg(ctx, slide)
    return blend(readable_on(ctx.theme, background), background, 0.78)


def _add_footer(ctx: "BuildContext", slide, number: int) -> None:
    footer_cfg = ctx.footer or {}
    show_footer = bool(footer_cfg.get("show_footer", bool(footer_cfg.get("text"))))
    show_number = bool(footer_cfg.get("show_slide_number", True))
    show_date = bool(footer_cfg.get("show_date", False))
    y = ctx.height_in - 0.42
    color = _muted_on(ctx, slide)
    if show_footer and footer_cfg.get("text"):
        box = _add_textbox(slide, ctx.margin_x, y, ctx.content_w * 0.7, 0.28, name="pptx-agent-footer")
        _write_paragraph(box.text_frame.paragraphs[0], str(footer_cfg["text"]), font=ctx.theme.font("body"),
                         size_pt=ctx.theme.pt("small_pt", 11), color=color, space_after_pt=0, line_spacing=1.0)
    if show_date:
        stamp = _dt.date.today().isoformat()
        box = _add_textbox(slide, ctx.margin_x + ctx.content_w * 0.7, y, ctx.content_w * 0.15, 0.28,
                           name="pptx-agent-footer-date")
        _write_paragraph(box.text_frame.paragraphs[0], stamp, font=ctx.theme.font("body"),
                         size_pt=ctx.theme.pt("small_pt", 11), color=color, align=PP_ALIGN.RIGHT,
                         space_after_pt=0, line_spacing=1.0)
    if show_number:
        box = _add_textbox(slide, ctx.margin_x + ctx.content_w - 0.7, y, 0.7, 0.28, name="pptx-agent-slidenumber")
        _write_paragraph(box.text_frame.paragraphs[0], str(number), font=ctx.theme.font("body"),
                         size_pt=ctx.theme.pt("small_pt", 11), color=color, align=PP_ALIGN.RIGHT,
                         space_after_pt=0, line_spacing=1.0)


def _accent_rule(ctx: "BuildContext", slide, y_in: float, *, width_in: Optional[float] = None,
                 color: Optional[str] = None, x_in: Optional[float] = None) -> None:
    _add_rect(
        slide,
        x_in if x_in is not None else ctx.margin_x,
        y_in,
        width_in if width_in is not None else 1.1,
        0.06,
        color or ctx.theme.color("accent"),
        name="pptx-agent-accent-rule",
    )


@dataclass
class BuildContext:
    prs: Any
    theme: Theme
    config: Dict[str, Any]
    log: RunLog
    width_in: float
    height_in: float
    footer: Dict[str, Any]
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    built_slides: List[Dict[str, Any]] = field(default_factory=list)
    scheme: Dict[str, str] = field(default_factory=dict)

    @property
    def margin_x(self) -> float:
        return self.theme.grid_in("margin_x_in", 0.62)

    @property
    def content_w(self) -> float:
        return round(self.width_in - 2 * self.margin_x, 3)

    @property
    def title_y(self) -> float:
        return self.theme.grid_in("title_top_in", 0.45)

    @property
    def content_top(self) -> float:
        return self.theme.grid_in("content_top_in", 1.5)


# ---------------------------------------------------------------------------
# layout builders
# ---------------------------------------------------------------------------

def _slide_shell(ctx: BuildContext, layout_index: int, slide_spec: Dict[str, Any]):
    slide = ctx.prs.slides.add_slide(ctx.prs.slide_layouts[layout_index])
    # Always paint the theme canvas: dark presets must not inherit the white
    # default master background, which would hide light text.
    background = slide_spec.get("background") or ctx.theme.color("background")
    _paint_background(slide, background)
    if slide_spec.get("notes"):
        slide.notes_slide.notes_text_frame.text = slide_spec["notes"]
    return slide


def _layout_title(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 0, slide_spec)
    bg = slide_spec.get("background") or theme.color("primary")
    _paint_background(slide, bg)
    dark_bg = is_dark(bg)
    text_color = readable_on(theme, bg, prefer="inverse")
    subtitle_color = blend(text_color, bg, 0.84)

    # NOTE: the slide background is painted through slide.background; a full-bleed
    # rectangle here would sit above the title placeholder in z-order and hide it.
    _add_rect(slide, ctx.width_in - 3.2, 0, 3.2, ctx.height_in, theme.color("secondary"), name="pptx-agent-band")
    _add_rect(slide, ctx.width_in - 3.28, 0, 0.08, ctx.height_in, theme.color("accent"), name="pptx-agent-band-rule")

    eyebrow = slide_spec.get("eyebrow") or ""
    accent_options = [theme.color("accent"), theme.color("accent_soft")]
    accent_text = max(accent_options, key=lambda color: contrast_ratio(color, bg))
    top = 2.05
    if eyebrow:
        box = _add_textbox(slide, ctx.margin_x, 1.58, ctx.content_w * 0.7, 0.34, name="pptx-agent-eyebrow")
        _write_paragraph(box.text_frame.paragraphs[0], eyebrow.upper(), font=theme.font("body"),
                         size_pt=theme.pt("small_pt", 11) + 1, color=accent_text, bold=True,
                         space_after_pt=0)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=top, w_in=ctx.content_w * 0.72, h_in=1.25,
        size_pt=theme.pt("title_pt", 32) + 8, color=text_color, font=theme.font("heading"),
        bold=True, line_spacing=1.0, anchor="top",
    )
    subtitle = slide_spec.get("subtitle")
    if subtitle:
        _accent_rule(ctx, slide, top + 1.38, width_in=1.3, color=accent_text)
        box = _add_textbox(slide, ctx.margin_x, top + 1.55, ctx.content_w * 0.66, 1.0, name="pptx-agent-subtitle")
        _write_paragraph(box.text_frame.paragraphs[0], subtitle, font=theme.font("body"),
                         size_pt=theme.pt("subtitle_pt", 18), color=subtitle_color, space_after_pt=0,
                         line_spacing=1.2)
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_section(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 2, slide_spec)
    bg = slide_spec.get("background") or theme.color("surface")
    _paint_background(slide, bg)
    dark_bg = is_dark(bg)
    text_color = readable_on(theme, bg, prefer="inverse" if dark_bg else "text")
    _add_rect(slide, 0, 0, ctx.width_in, 0.16, theme.color("accent"), name="pptx-agent-top-rule")
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=2.45, w_in=ctx.content_w * 0.86, h_in=1.2,
        size_pt=theme.pt("section_pt", 38), color=text_color, font=theme.font("heading"), bold=True,
    )
    _accent_rule(ctx, slide, 3.78, width_in=1.6)
    subtitle = slide_spec.get("subtitle")
    if subtitle:
        box = _add_textbox(slide, ctx.margin_x, 3.98, ctx.content_w * 0.8, 0.9,
                           name="pptx-agent-subtitle")
        _write_paragraph(box.text_frame.paragraphs[0], subtitle, font=theme.font("body"),
                         size_pt=theme.pt("subtitle_pt", 18), color=_muted_on(ctx, slide), space_after_pt=0,
                         line_spacing=1.2)
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_closing(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 0, slide_spec)
    bg = slide_spec.get("background") or theme.color("primary")
    _paint_background(slide, bg)
    for shape in list(slide.shapes):
        if shape.is_placeholder and shape.placeholder_format.idx == 1:
            _remove_shape(shape)
    closing_ink = readable_on(theme, bg, prefer="inverse")
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=2.7, w_in=ctx.content_w, h_in=1.3,
        size_pt=theme.pt("title_pt", 32) + 6, color=closing_ink, font=theme.font("heading"),
        align=PP_ALIGN.CENTER,
    )
    _accent_rule(ctx, slide, ctx.height_in / 2 + 0.85, width_in=1.4, x_in=(ctx.width_in - 1.4) / 2)
    subtitle = slide_spec.get("subtitle")
    if subtitle:
        box = _add_textbox(slide, ctx.margin_x, ctx.height_in / 2 + 1.1, ctx.content_w, 0.9,
                           name="pptx-agent-subtitle")
        _write_paragraph(box.text_frame.paragraphs[0], subtitle, font=theme.font("body"),
                         size_pt=theme.pt("subtitle_pt", 18), color=blend(closing_ink, bg, 0.84),
                         align=PP_ALIGN.CENTER, space_after_pt=0)
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


TITLE_PLACEHOLDER_TYPES = {"TITLE", "CENTER_TITLE", "VERTICAL_TITLE"}


def placeholder_type_name(shape) -> str:
    """Enum-style placeholder type name without the numeric suffix."""

    try:
        return str(shape.placeholder_format.type).split(" (")[0].strip().upper()
    except Exception:
        return ""


def is_title_placeholder(shape) -> bool:
    """True only for real title placeholders (never SUBTITLE)."""

    try:
        if not shape.is_placeholder:
            return False
        return placeholder_type_name(shape) in TITLE_PLACEHOLDER_TYPES
    except Exception:
        return False


def _remove_unused_placeholders(slide, *, keep_title: bool = True, keep_idx: Sequence[int] = ()) -> None:
    for shape in list(slide.shapes):
        if not shape.is_placeholder:
            continue
        idx = shape.placeholder_format.idx
        if keep_title and is_title_placeholder(shape):
            continue
        if idx in keep_idx:
            continue
        text = shape.text_frame.text.strip() if shape.has_text_frame else ""
        if not text:
            _remove_shape(shape)


def _layout_bullets(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 1, slide_spec)
    title_size = theme.pt("title_pt", 32)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
        size_pt=title_size, color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("heading"), bold=True,
    )
    _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    body = None
    for shape in slide.placeholders:
        if shape.placeholder_format.idx == 1:
            body = shape
            break
    if body is None:
        body = _add_textbox(slide, ctx.margin_x, ctx.content_top, ctx.content_w, ctx.height_in - ctx.content_top - 0.65,
                            name="pptx-agent-body")
    else:
        _style_body_placeholder(
            ctx, body,
            x_in=ctx.margin_x, y_in=ctx.content_top + 0.12,
            w_in=ctx.content_w * 0.86, h_in=ctx.height_in - ctx.content_top - 0.8,
        )
    used = _fill_bullets(
        ctx, body.text_frame, slide_spec["bullets"],
        size_pt=theme.pt("body_pt", 16), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("body"),
        width_in=ctx.content_w * 0.84, height_in=ctx.height_in - ctx.content_top - 0.8,
        line_spacing=float(theme.typography.get("line_spacing", 1.15)),
        indent_in=theme.grid_in("bullet_indent_in", 0.28),
    )
    if used < theme.pt("body_pt", 16) - 2:
        ctx.warnings.append({"code": "W_SHRINK", "slide": slide_spec["id"],
                             "message": "Bullets were auto-shrunk to %.0fpt to fit the content area." % used})
    aside = slide_spec.get("aside")
    if aside:
        box = _add_textbox(slide, ctx.margin_x + ctx.content_w * 0.88, ctx.content_top + 0.12,
                           ctx.content_w * 0.12, 1.4, name="pptx-agent-aside")
        _write_paragraph(box.text_frame.paragraphs[0], aside, font=theme.font("body"),
                         size_pt=theme.pt("small_pt", 11), color=theme.color("muted"), space_after_pt=0)
    _remove_unused_placeholders(slide, keep_title=True, keep_idx=(1,) if body is not None and body.is_placeholder else ())
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_two_column(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 3, slide_spec)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
        size_pt=theme.pt("title_pt", 32), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("heading"),
        bold=True,
    )
    _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    gap = theme.grid_in("gap_in", 0.3)
    col_w = (ctx.content_w - gap) / 2.0
    body_placeholders = [shape for shape in slide.placeholders if shape.placeholder_format.idx in (1, 2)]
    body_placeholders.sort(key=lambda shape: shape.left or 0)
    for position, side in enumerate(("left", "right")):
        block = slide_spec.get(side) or {}
        x = ctx.margin_x + position * (col_w + gap)
        y = ctx.content_top + 0.12
        height = ctx.height_in - y - 0.7
        heading = block.get("heading") or ""
        top_offset = 0.0
        if heading:
            heading_box = _add_textbox(slide, x, y, col_w, 0.45, name="pptx-agent-%s-heading" % side)
            _write_paragraph(heading_box.text_frame.paragraphs[0], heading, font=theme.font("heading"),
                             size_pt=theme.pt("heading_pt", 20), color=theme.color("accent"), bold=True,
                             space_after_pt=0)
            top_offset = 0.55
        target = None
        if position < len(body_placeholders):
            target = body_placeholders[position]
            _style_body_placeholder(ctx, target, x_in=x, y_in=y + top_offset, w_in=col_w, h_in=height - top_offset)
        else:
            target = _add_textbox(slide, x, y + top_offset, col_w, height - top_offset,
                                  name="pptx-agent-%s-body" % side)
        frame = target.text_frame
        bullets = block.get("bullets") or []
        if bullets:
            _fill_bullets(
                ctx, frame, bullets,
                size_pt=theme.pt("body_pt", 16) - 1, color=readable_on(theme, _slide_bg(ctx, slide)),
                font=theme.font("body"),
                width_in=col_w, height_in=height - top_offset,
                line_spacing=float(theme.typography.get("line_spacing", 1.15)),
                indent_in=theme.grid_in("bullet_indent_in", 0.28),
            )
        elif block.get("body"):
            frame.text = ""
            _write_paragraph(frame.paragraphs[0], block["body"], font=theme.font("body"),
                             size_pt=theme.pt("body_pt", 16) - 1, color=readable_on(theme, _slide_bg(ctx, slide)),
                             line_spacing=float(theme.typography.get("line_spacing", 1.15)))
    _remove_unused_placeholders(slide, keep_title=True, keep_idx=(1, 2))
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_kpi(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 5, slide_spec)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
        size_pt=theme.pt("title_pt", 32), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("heading"),
        bold=True,
    )
    _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    subtitle = slide_spec.get("subtitle")
    if subtitle:
        box = _add_textbox(slide, ctx.margin_x, ctx.title_y + 1.1, ctx.content_w * 0.9, 0.35,
                           name="pptx-agent-subtitle")
        _write_paragraph(box.text_frame.paragraphs[0], subtitle, font=theme.font("body"),
                         size_pt=theme.pt("subtitle_pt", 18) - 2, color=theme.color("muted"), space_after_pt=0)
    kpis = slide_spec["kpis"]
    gap = theme.grid_in("gap_in", 0.3)
    columns = len(kpis)
    card_w = (ctx.content_w - gap * (columns - 1)) / max(columns, 1)
    card_h = max(2.6, ctx.height_in * 0.42)
    y = ctx.content_top + 0.5
    for index, kpi in enumerate(kpis):
        x = ctx.margin_x + index * (card_w + gap)
        _add_rect(slide, x, y, card_w, card_h, theme.color("surface"), line=theme.color("border"),
                  rounded=True, radius_in=theme.art.get("corner_radius_in", 0.1), name="pptx-agent-kpi-card")
        value_size = _fit_font_size(kpi["value"], card_w - 0.5, 1.1, theme.pt("kpi_pt", 38), 18,
                                    bold=True, config=ctx.config, max_lines=1)
        value_box = _add_textbox(slide, x + 0.25, y + 0.35, card_w - 0.5, 1.1, name="pptx-agent-kpi-value")
        card_bg = theme.color("surface")
        accent_ink = max([theme.color("accent"), theme.color("accent_soft")],
                         key=lambda color: contrast_ratio(color, card_bg))
        _write_paragraph(value_box.text_frame.paragraphs[0], kpi["value"], font=theme.font("heading"),
                         size_pt=value_size, color=accent_ink, bold=True, space_after_pt=0,
                         line_spacing=1.0)
        if kpi.get("label"):
            label_box = _add_textbox(slide, x + 0.25, y + 1.55, card_w - 0.5, 0.75, name="pptx-agent-kpi-label")
            _write_paragraph(label_box.text_frame.paragraphs[0], kpi["label"], font=theme.font("body"),
                             size_pt=theme.pt("body_pt", 16), color=readable_on(theme, card_bg), bold=True,
                             space_after_pt=0, line_spacing=1.1)
        if kpi.get("detail"):
            detail_box = _add_textbox(slide, x + 0.25, y + card_h - 0.62, card_w - 0.5, 0.5,
                                      name="pptx-agent-kpi-detail")
            _write_paragraph(detail_box.text_frame.paragraphs[0], kpi["detail"], font=theme.font("body"),
                             size_pt=theme.pt("small_pt", 11) + 1, color=blend(readable_on(theme, card_bg), card_bg, 0.78),
                             space_after_pt=0,
                             line_spacing=1.1)
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_quote(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 5, slide_spec)
    bg = slide_spec.get("background") or theme.color("primary")
    _paint_background(slide, bg)
    dark_bg = is_dark(bg)
    text_color = readable_on(theme, bg, prefer="inverse")
    title = slide_spec.get("title")
    if title:
        box = _add_textbox(slide, ctx.margin_x, ctx.title_y, ctx.content_w, 0.4, name="pptx-agent-eyebrow")
        _write_paragraph(box.text_frame.paragraphs[0], title.upper(), font=theme.font("body"),
                         size_pt=theme.pt("small_pt", 11) + 1,
                         color=(theme.color("accent_soft") if dark_bg else theme.color("accent")), bold=True,
                         space_after_pt=0)
        _remove_unused_placeholders(slide, keep_title=False)
    quote = slide_spec["quote"]
    quote_size = _fit_font_size(quote, ctx.content_w * 0.82, 2.6, theme.pt("quote_pt", 26), 16,
                                bold=False, line_spacing=1.2, config=ctx.config, max_lines=6)
    mark = _add_textbox(slide, ctx.margin_x, 1.55, 1.0, 0.9, name="pptx-agent-quote-mark")
    _write_paragraph(mark.text_frame.paragraphs[0], "“", font=theme.font("heading"),
                     size_pt=72, color=(theme.color("accent_soft") if dark_bg else theme.color("accent")),
                     bold=True, space_after_pt=0, line_spacing=1.0)
    quote_box = _add_textbox(slide, ctx.margin_x + 0.1, 2.45, ctx.content_w * 0.82, 2.8, name="pptx-agent-quote")
    _write_paragraph(quote_box.text_frame.paragraphs[0], quote, font=theme.font("heading"),
                     size_pt=quote_size, color=text_color, italic=False, space_after_pt=0, line_spacing=1.18)
    attribution = slide_spec.get("attribution")
    if attribution:
        _add_rect(slide, ctx.margin_x + 0.1, 5.35, 0.9, 0.05, theme.color("accent"),
                  name="pptx-agent-quote-rule")
        box = _add_textbox(slide, ctx.margin_x + 0.1, 5.5, ctx.content_w * 0.7, 0.5,
                           name="pptx-agent-attribution")
        _write_paragraph(box.text_frame.paragraphs[0], attribution, font=theme.font("body"),
                         size_pt=theme.pt("subtitle_pt", 18) - 3, color=_muted_on(ctx, slide), bold=True,
                         space_after_pt=0)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _set_cell_border(cell, hex_color: str, width_pt: float = 0.75) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        for existing in tc_pr.findall(qn(tag)):
            tc_pr.remove(existing)
    for tag in ("a:lnB", "a:lnT", "a:lnR", "a:lnL"):
        ln = _etree.SubElement(tc_pr, qn(tag))
        ln.set("w", str(pt_to_emu(width_pt)))
        ln.set("cap", "flat")
        ln.set("cmpd", "sng")
        ln.set("algn", "ctr")
        solid = _sub(ln, "a:solidFill")
        _sub(solid, "a:srgbClr").set("val", parse_hex(hex_color))
        _sub(ln, "a:prstDash").set("val", "solid")
    # restore required element order: lnL, lnR, lnT, lnB before fills
    ordered = [tc_pr.find(qn(tag)) for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB")]
    for element in ordered:
        if element is None:
            continue
        tc_pr.remove(element)
    for offset, element in enumerate(ordered):
        if element is not None:
            tc_pr.insert(offset, element)


def _layout_table(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 5, slide_spec)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
        size_pt=theme.pt("title_pt", 32), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("heading"),
        bold=True,
    )
    _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    table_spec = slide_spec["table"]
    headers = table_spec["headers"]
    rows = table_spec["rows"]
    columns = len(headers)
    total_rows = len(rows) + 1
    available_w = ctx.content_w
    widths = table_spec.get("column_widths_in") or []
    if widths and len(widths) == columns:
        scale = available_w / sum(widths)
        col_widths = [max(0.6, value * scale) for value in widths]
    else:
        col_widths = [available_w / columns] * columns
    top = ctx.content_top + 0.2
    max_height = ctx.height_in - top - 0.85
    row_h = clamp(max_height / total_rows, 0.34, 0.62)
    table_height = row_h * total_rows
    shape = slide.shapes.add_table(total_rows, columns, Inches(ctx.margin_x), Inches(top),
                                   Inches(sum(col_widths)), Inches(table_height))
    table = shape.table
    try:
        shape._element.nvGraphicFramePr.cNvPr.set(
            "descr", "Data table: %s" % (slide_spec.get("title") or "table of values")
        )
    except Exception:
        pass
    for index, width in enumerate(col_widths):
        table.columns[index].width = Inches(width)
    for index in range(total_rows):
        table.rows[index].height = Inches(row_h)
    table.first_row = True
    table.horz_banding = False
    body_pt = theme.table.get("body_pt", 12)
    header_pt = theme.table.get("header_pt", 12)
    header_fill = theme.color(str(theme.table.get("header_fill", "primary")))
    header_text = theme.color(str(theme.table.get("header_text", "text_inverse")))
    row_fill = theme.color(str(theme.table.get("row_fill", "background")))
    alt_fill = theme.color(str(theme.table.get("alt_row_fill", "surface")))
    border = theme.color(str(theme.table.get("border", "border")))
    for column_index, text in enumerate(headers):
        cell = table.cell(0, column_index)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(parse_hex(header_fill))
        cell.margin_left = Inches(0.08)
        cell.margin_right = Inches(0.08)
        cell.margin_top = Inches(0.03)
        cell.margin_bottom = Inches(0.03)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        frame = cell.text_frame
        frame.word_wrap = True
        frame.text = ""
        _write_paragraph(frame.paragraphs[0], str(text), font=theme.font("body"), size_pt=float(header_pt),
                         color=header_text, bold=True, space_after_pt=0, line_spacing=1.0)
        _set_cell_border(cell, border, 0.75)
    for row_index, row in enumerate(rows, start=1):
        fill = alt_fill if row_index % 2 == 0 else row_fill
        for column_index in range(columns):
            cell = table.cell(row_index, column_index)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(parse_hex(fill))
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.08)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            frame = cell.text_frame
            frame.word_wrap = True
            frame.text = ""
            value = row[column_index] if column_index < len(row) else ""
            _write_paragraph(frame.paragraphs[0], str(value), font=theme.font("body"), size_pt=float(body_pt),
                             color=readable_on(theme, fill), bold=(column_index == 0 and row_index % 2 == 1),
                             space_after_pt=0, line_spacing=1.0)
            _set_cell_border(cell, border, 0.75)
    if slide_spec.get("caption"):
        box = _add_textbox(slide, ctx.margin_x, top + table_height + 0.12, ctx.content_w, 0.4,
                           name="pptx-agent-caption")
        _write_paragraph(box.text_frame.paragraphs[0], slide_spec["caption"], font=theme.font("body"),
                         size_pt=theme.pt("small_pt", 11) + 1, color=theme.color("muted"), space_after_pt=0)
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_chart(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 5, slide_spec)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
        size_pt=theme.pt("title_pt", 32), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("heading"),
        bold=True,
    )
    _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    chart_spec = slide_spec["chart"]
    chart_data = CategoryChartData()
    chart_data.categories = chart_spec["categories"]
    for series in chart_spec["series"]:
        chart_data.add_series(series["name"], series["values"])
    chart_type = getattr(XL_CHART_TYPE, CHART_TYPES[chart_spec["chart_type"]])
    top = ctx.content_top + 0.2
    height = ctx.height_in - top - 0.85
    if slide_spec.get("caption"):
        height -= 0.35
    frame = slide.shapes.add_chart(chart_type, Inches(ctx.margin_x), Inches(top), Inches(ctx.content_w),
                                   Inches(height), chart_data)
    chart = frame.chart
    chart.font.name = theme.font("body")
    chart.font.size = Pt(float(theme.chart.get("font_pt", 11)))
    chart.font.color.rgb = RGBColor.from_string(parse_hex(theme.color("text")))
    chart.has_title = False
    chart.has_legend = bool(chart_spec.get("show_legend"))
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(float(theme.chart.get("font_pt", 11)))
    plot = chart.plots[0]
    if chart_spec["chart_type"] in ("column", "column-clustered", "column-stacked", "bar", "bar-clustered"):
        plot.gap_width = 55
        try:
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.font.size = Pt(float(theme.chart.get("font_pt", 11)) - 1)
            labels.font.color.rgb = RGBColor.from_string(parse_hex(theme.color("text")))
            if chart_spec.get("value_suffix"):
                labels.number_format = "0%s" % chart_spec["value_suffix"]
                labels.number_format_is_linked = False
        except Exception:
            pass
    if chart_spec["chart_type"] in ("pie", "doughnut"):
        try:
            plot.has_data_labels = True
            plot.data_labels.show_percentage = True
            plot.data_labels.show_value = False
            plot.data_labels.font.size = Pt(float(theme.chart.get("font_pt", 11)))
            plot.data_labels.font.color.rgb = RGBColor.from_string(parse_hex(theme.color("text_inverse")))
        except Exception:
            pass
    palette = theme.palette()
    if chart_spec["chart_type"] in ("pie", "doughnut"):
        # Pie slices are coloured per data point, not per series.
        for series in plot.series:
            try:
                for point_index, point in enumerate(series.points):
                    colour = parse_hex(palette[point_index % len(palette)])
                    point.format.fill.solid()
                    point.format.fill.fore_color.rgb = RGBColor.from_string(colour)
                    point.format.line.color.rgb = RGBColor.from_string(theme.color("background"))
            except Exception:
                pass
    else:
        for index, series in enumerate(plot.series):
            try:
                series.format.fill.solid()
                series.format.fill.fore_color.rgb = RGBColor.from_string(parse_hex(palette[index % len(palette)]))
                series.format.line.color.rgb = RGBColor.from_string(parse_hex(palette[index % len(palette)]))
            except Exception:
                pass
    try:
        value_axis = chart.value_axis
        value_axis.has_major_gridlines = True
        value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string(parse_hex(theme.color("border")))
        value_axis.major_gridlines.format.line.width = Pt(0.5)
        value_axis.tick_labels.font.size = Pt(float(theme.chart.get("font_pt", 11)) - 1)
        value_axis.format.line.color.rgb = RGBColor.from_string(parse_hex(theme.color("border")))
        if chart_spec.get("value_suffix"):
            value_axis.tick_labels.number_format = "0%s" % chart_spec["value_suffix"]
            value_axis.tick_labels.number_format_is_linked = False
        category_axis = chart.category_axis
        category_axis.tick_labels.font.size = Pt(float(theme.chart.get("font_pt", 11)) - 1)
        category_axis.format.line.color.rgb = RGBColor.from_string(parse_hex(theme.color("border")))
    except Exception:
        pass
    frame.name = "pptx-agent-chart"
    try:
        frame._element.nvGraphicFramePr.cNvPr.set(
            "descr", "Chart: %s" % (slide_spec.get("title") or "data chart")
        )
    except Exception:
        pass
    if slide_spec.get("caption"):
        box = _add_textbox(slide, ctx.margin_x, ctx.height_in - 1.05, ctx.content_w, 0.4,
                           name="pptx-agent-caption")
        _write_paragraph(box.text_frame.paragraphs[0], slide_spec["caption"], font=theme.font("body"),
                         size_pt=theme.pt("small_pt", 11) + 1, color=theme.color("muted"), space_after_pt=0)
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _image_dimensions_px(path: str) -> Tuple[int, int]:
    if PIL_AVAILABLE:
        try:
            with Image.open(path) as image:
                return int(image.width), int(image.height)
        except Exception:
            pass
    return 0, 0


def _layout_image_right(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 5, slide_spec)
    _style_title_placeholder(
        ctx, slide, slide_spec["title"],
        x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
        size_pt=theme.pt("title_pt", 32), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("heading"),
        bold=True,
    )
    _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    gap = theme.grid_in("gap_in", 0.3)
    image_w = ctx.content_w * 0.46
    text_w = ctx.content_w - image_w - gap
    top = ctx.content_top + 0.2
    height = ctx.height_in - top - 0.85
    image_spec = slide_spec["image"]
    img_w_px, img_h_px = _image_dimensions_px(image_spec["path"])
    aspect = (img_w_px / img_h_px) if img_w_px and img_h_px else 16 / 9
    box_ratio = image_w / height
    if aspect >= box_ratio:
        draw_w = image_w
        draw_h = image_w / aspect
    else:
        draw_h = height
        draw_w = height * aspect
    x = ctx.margin_x + ctx.content_w - draw_w
    y = top + (height - draw_h) / 2.0
    picture = slide.shapes.add_picture(image_spec["path"], Inches(x), Inches(y), Inches(draw_w), Inches(draw_h))
    picture.name = "pptx-agent-image"
    # Only an explicit alt text is written. Inventing one from the file name
    # would hide a real accessibility defect from the audit.
    alt = image_spec.get("alt") or ""
    try:
        if alt:
            picture._element.nvPicPr.cNvPr.set("descr", alt)
    except Exception:
        pass
    if slide_spec.get("caption"):
        box = _add_textbox(slide, x, y + draw_h + 0.08, draw_w, 0.34, name="pptx-agent-caption")
        _write_paragraph(box.text_frame.paragraphs[0], slide_spec["caption"], font=theme.font("body"),
                         size_pt=theme.pt("small_pt", 11) + 1, color=theme.color("muted"),
                         space_after_pt=0, align=PP_ALIGN.RIGHT)
    bullets = slide_spec.get("bullets") or []
    if bullets:
        box = _add_textbox(slide, ctx.margin_x, top, text_w, height, name="pptx-agent-body")
        _fill_bullets(
            ctx, box.text_frame, bullets,
            size_pt=theme.pt("body_pt", 16), color=readable_on(theme, _slide_bg(ctx, slide)), font=theme.font("body"),
            width_in=text_w, height_in=height,
            line_spacing=float(theme.typography.get("line_spacing", 1.15)),
            indent_in=theme.grid_in("bullet_indent_in", 0.28),
        )
    _remove_unused_placeholders(slide, keep_title=True)
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


def _layout_blank(ctx: BuildContext, slide_spec: Dict[str, Any]):
    theme = ctx.theme
    slide = _slide_shell(ctx, 5, slide_spec)
    title = slide_spec.get("title")
    if title:
        _style_title_placeholder(
            ctx, slide, title,
            x_in=ctx.margin_x, y_in=ctx.title_y, w_in=ctx.content_w * 0.92, h_in=0.95,
            size_pt=theme.pt("title_pt", 32), color=readable_on(theme, _slide_bg(ctx, slide)),
            font=theme.font("heading"), bold=True,
        )
        _accent_rule(ctx, slide, ctx.title_y + 1.02, width_in=1.0)
    _remove_unused_placeholders(slide, keep_title=bool(title))
    _add_footer(ctx, slide, len(ctx.prs.slides))
    return slide


LAYOUT_BUILDERS: Dict[str, Callable[[BuildContext, Dict[str, Any]], Any]] = {
    "title": _layout_title,
    "section": _layout_section,
    "closing": _layout_closing,
    "bullets": _layout_bullets,
    "title-and-content": _layout_bullets,
    "two-column": _layout_two_column,
    "kpi": _layout_kpi,
    "quote": _layout_quote,
    "table": _layout_table,
    "chart": _layout_chart,
    "image-right": _layout_image_right,
    "blank": _layout_blank,
}


NS_FALLBACK = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
}

SECTION_EXT_URI = "{521415D9-36F7-43E2-AB2F-B90AF26B5E84}"


def _qn(tag: str) -> str:
    try:
        return qn(tag)
    except Exception:
        prefix, local = tag.split(":", 1)
        return "{%s}%s" % (NS_FALLBACK[prefix], local)


# ---------------------------------------------------------------------------
# deck assembly
# ---------------------------------------------------------------------------

def build_deck(
    spec: Dict[str, Any],
    *,
    config: Optional[Dict[str, Any]] = None,
    log: Optional[RunLog] = None,
    into: Optional[str] = None,
    deadline: Optional[Deadline] = None,
) -> Tuple[Any, List[Dict[str, Any]], Dict[str, Any]]:
    """Build (or extend) a presentation from a validated deck spec."""

    require_pptx()
    config = config or DEFAULT_CONFIG
    log = log or RunLog()

    base_theme = load_theme(spec["theme"]["preset"])
    overrides = spec["theme"].get("overrides") or {}
    theme = Theme(deep_merge(base_theme.data, overrides)) if overrides else base_theme

    if into:
        prs = Presentation(resolve_input(into, label="base presentation"))
    else:
        prs = Presentation()
    prs.slide_width = Inches(spec["slide_size"]["width_in"])
    prs.slide_height = Inches(spec["slide_size"]["height_in"])

    meta = spec.get("meta") or {}
    props = prs.core_properties
    if meta.get("title"):
        props.title = meta["title"]
    if meta.get("author"):
        props.author = meta["author"]
        props.last_modified_by = meta["author"]
    if meta.get("subject"):
        props.subject = meta["subject"]
    if meta.get("keywords"):
        props.keywords = ", ".join(str(item) for item in meta["keywords"])
    if meta.get("company"):
        props.category = meta["company"]
    props.language = meta.get("language") or "en-US"
    props.comments = "Generated by powerpoint-agent %s" % TOOL_VERSION
    props.revision = 1

    ctx = BuildContext(
        prs=prs,
        theme=theme,
        config=config,
        log=log,
        width_in=spec["slide_size"]["width_in"],
        height_in=spec["slide_size"]["height_in"],
        footer=spec.get("footer") or {},
        scheme=theme_color_scheme(prs),
    )

    built: List[Dict[str, Any]] = []
    for index, slide_spec in enumerate(spec["slides"]):
        if deadline:
            deadline.check("building slide %d" % (index + 1))
        builder = LAYOUT_BUILDERS[slide_spec["layout"]]
        slide = builder(ctx, slide_spec)
        built.append(
            {
                "spec_id": slide_spec["id"],
                "layout": slide_spec["layout"],
                "slide_index": len(prs.slides),
                "title": slide_title_text(slide) or slide_spec.get("title") or "",
                "notes": bool(slide_spec.get("notes")),
            }
        )
        log.step("built slide", spec_id=slide_spec["id"], layout=slide_spec["layout"], number=len(prs.slides))

    if spec.get("sections"):
        write_sections(prs, spec["sections"], built)

    return prs, ctx.warnings, {"built": built, "theme": theme.name, "slide_count": len(prs.slides)}


def save_presentation(prs, path: str) -> str:
    """Atomically save a presentation to ``path``."""

    require_pptx()
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    suffix = os.path.splitext(path)[1] or ".pptx"
    fd, tmp = tempfile.mkstemp(prefix=".pptx-agent-", suffix=suffix, dir=directory)
    os.close(fd)
    try:
        prs.save(tmp)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def open_presentation(path: str):
    require_pptx()
    try:
        return Presentation(resolve_input(path))
    except AgentError:
        raise
    except Exception as exc:
        raise ValidationError(
            "Unable to open presentation: %s" % os.path.basename(path),
            hint="Confirm the file is a valid .pptx/.pptm package and is not password protected.",
            details={"path": path, "error": "%s: %s" % (type(exc).__name__, exc)},
        )


# ---------------------------------------------------------------------------
# sections (p14:sectionLst)
# ---------------------------------------------------------------------------

def _sld_id_list(prs) -> List[Any]:
    lst = prs.slides._sldIdLst
    return list(lst)


def write_sections(prs, sections: Sequence[Dict[str, Any]], built: Sequence[Dict[str, Any]]) -> None:
    """Write a PowerPoint section list that matches ``sections`` in the spec.

    The section list lives in ``p:extLst`` on the presentation part and uses the
    ``p14`` namespace.  The XML is assembled as a string so the conventional
    ``p14`` prefix (not an auto-generated ``ns0``) is written, which keeps the
    file byte-shape close to what PowerPoint itself produces.
    """

    from xml.sax.saxutils import escape

    slide_ids = _sld_id_list(prs)
    position_by_spec_id = {item["spec_id"]: item["slide_index"] for item in built}
    if not slide_ids or not sections:
        return
    presentation = prs.part._element
    ext_lst = presentation.find(_qn("p:extLst"))
    if ext_lst is None:
        ext_lst = _etree.SubElement(presentation, _qn("p:extLst"))
    for ext in list(ext_lst):
        if ext.get("uri") == SECTION_EXT_URI:
            ext_lst.remove(ext)

    used: set = set()
    chunks: List[str] = []
    for section in sections:
        id_parts: List[str] = []
        for spec_id in section["slide_ids"]:
            position = position_by_spec_id.get(spec_id)
            if position is None or position - 1 >= len(slide_ids):
                continue
            value = slide_ids[position - 1].get("id")
            if value in used:
                continue
            used.add(value)
            id_parts.append('<p14:sldId id="%s"/>' % escape(str(value), {'"': "&quot;"}))
        if not id_parts:
            continue
        chunks.append(
            '<p14:section name="%s" id="{%s}"><p14:sldIdLst>%s</p14:sldIdLst></p14:section>'
            % (
                escape(str(section["name"])[:255], {'"': "&quot;"}),
                str(uuid.uuid4()).upper(),
                "".join(id_parts),
            )
        )
    if not chunks:
        return
    xml = (
        '<p:ext xmlns:p="%s" uri="%s"><p14:sectionLst xmlns:p14="%s">%s</p14:sectionLst></p:ext>'
        % (NS_FALLBACK["p"], SECTION_EXT_URI, NS_FALLBACK["p14"], "".join(chunks))
    )
    try:
        from pptx.oxml import parse_xml  # type: ignore

        ext_lst.append(parse_xml(xml))
    except Exception:
        ext_lst.append(_etree.fromstring(xml.encode("utf-8")))


def read_sections(prs) -> List[Dict[str, Any]]:
    """Read the PowerPoint section list, if present, as slide indexes."""

    presentation = prs.part._element
    ext_lst = presentation.find(_qn("p:extLst"))
    if ext_lst is None:
        return []
    target = None
    for ext in list(ext_lst):
        if ext.get("uri") == SECTION_EXT_URI:
            target = ext
            break
    if target is None:
        return []
    section_lst = target.find(_qn("p14:sectionLst"))
    if section_lst is None:
        return []
    ids = [str(item.get("id")) for item in _sld_id_list(prs)]
    result: List[Dict[str, Any]] = []
    for section in list(section_lst):
        name = section.get("name") or "Section"
        slide_indexes: List[int] = []
        ids_el = section.find(_qn("p14:sldIdLst"))
        if ids_el is not None:
            for child in list(ids_el):
                value = str(child.get("id"))
                if value in ids:
                    slide_indexes.append(ids.index(value) + 1)
        result.append({"name": name, "slide_indexes": sorted(slide_indexes)})
    return result


# ---------------------------------------------------------------------------
# inspection helpers
# ---------------------------------------------------------------------------

def iter_shapes(shapes) -> Iterator[Any]:
    """Yield every shape, descending into groups."""

    for shape in shapes:
        yield shape
        if getattr(shape, "shape_type", None) is not None and str(shape.shape_type) == "GROUP (6)":
            try:
                for child in iter_shapes(shape.shapes):
                    yield child
            except Exception:
                continue
        elif hasattr(shape, "shapes"):
            try:
                for child in iter_shapes(shape.shapes):
                    yield child
            except Exception:
                continue


def shape_text(shape) -> str:
    """Best-effort text extraction for text frames, tables, and groups."""

    parts: List[str] = []
    if getattr(shape, "has_text_frame", False):
        try:
            parts.append(shape.text_frame.text)
        except Exception:
            pass
    if getattr(shape, "has_table", False):
        try:
            for row in shape.table.rows:
                for cell in row.cells:
                    parts.append(cell.text)
        except Exception:
            pass
    if not parts and getattr(shape, "has_chart", False):
        try:
            chart = shape.chart
            if chart.has_title:
                parts.append(chart.chart_title.text_frame.text)
        except Exception:
            pass
    return "\n".join(part for part in parts if part)


def slide_title_text(slide) -> str:
    """Return the slide title using placeholders first, then heuristics."""

    try:
        title_placeholder = slide.shapes.title
    except Exception:
        title_placeholder = None
    if title_placeholder is not None and title_placeholder.has_text_frame:
        text = title_placeholder.text_frame.text.strip()
        if text:
            return text
    best = ""
    best_size = 0.0
    for shape in iter_shapes(slide.shapes):
        if not getattr(shape, "has_text_frame", False):
            continue
        for paragraph in shape.text_frame.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            size = 0.0
            for run in paragraph.runs:
                if run.font.size is not None:
                    size = max(size, run.font.size.pt)
            if size > best_size:
                best, best_size = text, size
    return best


def shape_bounds_in(shape) -> Dict[str, Optional[float]]:
    return {
        "left": emu_to_inches(shape.left),
        "top": emu_to_inches(shape.top),
        "width": emu_to_inches(shape.width),
        "height": emu_to_inches(shape.height),
    }


def theme_color_scheme(prs) -> Dict[str, str]:
    """Read the theme colour scheme (dk1/lt1/accent1..) as hex strings."""

    scheme: Dict[str, str] = {}
    try:
        package = prs.part.package
        theme_part = None
        for part in package.iter_parts():
            if str(part.partname) == "/ppt/theme/theme1.xml":
                theme_part = part
                break
        if theme_part is None:
            return scheme
        root = _etree.fromstring(theme_part.blob)
        clr_scheme = root.find(".//" + _qn("a:clrScheme"))
        if clr_scheme is None:
            return scheme
        for child in list(clr_scheme):
            name = _etree.QName(child).localname
            srgb = child.find(_qn("a:srgbClr"))
            sys_color = child.find(_qn("a:sysClr"))
            if srgb is not None and srgb.get("val"):
                scheme[name] = str(srgb.get("val")).upper()
            elif sys_color is not None and sys_color.get("lastClr"):
                scheme[name] = str(sys_color.get("lastClr")).upper()
    except Exception:
        return scheme
    return scheme


def resolve_theme_color(value: Any, scheme: Dict[str, str], fallback: str = "000000") -> str:
    key = str(value)
    for candidate in (key, key.split(".")[-1], key.upper()):
        if candidate in THEME_COLOR_MAP:
            mapped = THEME_COLOR_MAP[candidate]
            if mapped and mapped in scheme:
                return scheme[mapped]
    return fallback


def font_color_hex(font, scheme: Dict[str, str]) -> Optional[str]:
    """Resolve an explicit run colour, or ``None`` when the colour is inherited."""

    try:
        color = font.color
        if color is None:
            return None
        color_type = color.type
    except Exception:
        return None
    try:
        from pptx.enum.dml import MSO_COLOR_TYPE  # type: ignore

        if color_type == MSO_COLOR_TYPE.RGB:
            rgb = color.rgb
            return "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])
        if color_type == MSO_COLOR_TYPE.SCHEME:
            return resolve_theme_color(color.theme_color, scheme, "000000")
    except Exception:
        return None
    return None


def slide_background_hex(slide, prs, scheme: Optional[Dict[str, str]] = None) -> str:
    """Resolve the effective slide background (slide -> layout -> master -> white)."""

    scheme = scheme if scheme is not None else theme_color_scheme(prs)
    for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
        try:
            element = owner._element
        except Exception:
            continue
        try:
            c_sld = element.find(_qn("p:cSld"))
            if c_sld is None:
                continue
            bg = c_sld.find(_qn("p:bg"))
            if bg is None:
                continue
            bg_pr = bg.find(_qn("p:bgPr"))
            if bg_pr is None:
                continue
            solid = bg_pr.find(_qn("a:solidFill"))
            if solid is None:
                continue
            srgb = solid.find(_qn("a:srgbClr"))
            if srgb is not None and srgb.get("val"):
                return str(srgb.get("val")).upper()
            scheme_clr = solid.find(_qn("a:schemeClr"))
            if scheme_clr is not None:
                return resolve_theme_color(scheme_clr.get("val"), scheme, "FFFFFF")
        except Exception:
            continue
    try:
        fill = slide.background.fill
        if fill.type is not None:
            rgb = fill.fore_color.rgb
            return "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])
    except Exception:
        pass
    return "FFFFFF"


def slide_solid_background(slide) -> Optional[str]:
    """Return the slide-level solid background hex, or None when not a plain fill."""

    try:
        element = slide._element
        c_sld = element.find(_qn("p:cSld"))
        if c_sld is None:
            return None
        bg = c_sld.find(_qn("p:bg"))
        if bg is None:
            return None
        bg_pr = bg.find(_qn("p:bgPr"))
        if bg_pr is None:
            return None
        solid = bg_pr.find(_qn("a:solidFill"))
        if solid is None:
            return None
        srgb = solid.find(_qn("a:srgbClr"))
        if srgb is not None and srgb.get("val"):
            return str(srgb.get("val")).upper()
    except Exception:
        return None
    return None


def shape_fill_hex(shape, scheme: Dict[str, str]) -> Optional[str]:
    """Solid fill colour of a shape, or ``None`` when it is transparent."""

    try:
        fill = shape.fill
        if fill.type is None:
            return None
        from pptx.enum.dml import MSO_FILL  # type: ignore

        if fill.type != MSO_FILL.SOLID:
            return None
        fore = fill.fore_color
        try:
            from pptx.enum.dml import MSO_COLOR_TYPE  # type: ignore

            if fore.type == MSO_COLOR_TYPE.RGB:
                rgb = fore.rgb
                return "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])
            if fore.type == MSO_COLOR_TYPE.SCHEME:
                return resolve_theme_color(fore.theme_color, scheme, "FFFFFF")
        except Exception:
            return None
    except Exception:
        return None
    return None


def width_before_panel(slide, shape, scheme: Dict[str, str], limit_in: float) -> float:
    """Cap a text box's width so it never runs beneath an opaque panel.

    Title placeholders on designed layouts often sit left of a full-height
    accent block; stretching the placeholder to the content width would slide
    the text under that block. Returns the usable width in inches.
    """

    try:
        left = emu_to_inches(shape.left) or 0.0
        top = emu_to_inches(shape.top) or 0.0
        height = emu_to_inches(shape.height) or 0.0
        shape_id = shape.shape_id
    except Exception:
        return limit_in

    limit = limit_in
    band_top, band_bottom = top, top + max(height, 0.35)
    for candidate in iter_shapes(slide.shapes):
        try:
            if candidate.shape_id == shape_id:
                continue
            other_left = emu_to_inches(candidate.left) or 0.0
            other_top = emu_to_inches(candidate.top) or 0.0
            other_height = emu_to_inches(candidate.height) or 0.0
            other_width = emu_to_inches(candidate.width) or 0.0
        except Exception:
            continue
        if other_left <= left + 0.05 or other_width < 0.5 or other_height < 0.5:
            continue
        if other_top > band_bottom or other_top + other_height < band_top:
            continue
        if not shape_fill_hex(candidate, scheme):
            continue
        # Only panels that dominate the vertical band count as obstacles.
        if other_height < max(height, 0.35) * 0.8:
            continue
        limit = min(limit, max(0.0, other_left - left - 0.15))
    return limit


def effective_backdrop(slide, shape, scheme: Dict[str, str], canvas: str) -> str:
    """Solid colour actually visible behind ``shape``.

    Text is often a transparent text box stacked on top of a card shape, so the
    shape's own fill says nothing about legibility. Walk the shapes beneath it
    in z-order and take the last opaque one whose bounds contain the text
    box's centre; fall back to the slide canvas.
    """

    try:
        centre_x = (shape.left or 0) + (shape.width or 0) / 2
        centre_y = (shape.top or 0) + (shape.height or 0) / 2
        shape_id = shape.shape_id
    except Exception:
        return canvas

    backdrop = canvas
    for candidate in iter_shapes(slide.shapes):
        try:
            if candidate.shape_id == shape_id:
                break
        except Exception:
            continue
        fill = shape_fill_hex(candidate, scheme)
        if not fill:
            continue
        try:
            left, top = candidate.left or 0, candidate.top or 0
            width, height = candidate.width or 0, candidate.height or 0
        except Exception:
            continue
        if left <= centre_x <= left + width and top <= centre_y <= top + height:
            backdrop = fill
    return backdrop


def _placeholder_style_size(shape, level: int) -> Optional[float]:
    """Read a placeholder's inherited font size from layout/master txStyles."""

    try:
        if not shape.is_placeholder:
            return None
        ph_type = str(shape.placeholder_format.type)
        is_title = ph_type.split(" (")[0].strip().upper() in TITLE_PLACEHOLDER_TYPES
        owners = [shape.slide_layout, shape.slide_layout.slide_master]
        for owner in owners:
            try:
                element = owner._element
            except Exception:
                continue
            tx_styles = element.find(_qn("p:txStyles"))
            if tx_styles is None:
                continue
            style_name = "p:titleStyle" if is_title else "p:bodyStyle"
            style = tx_styles.find(_qn(style_name))
            if style is None:
                continue
            lvl = style.find(_qn("a:lvl%dpPr" % (min(level, 8) + 1)))
            if lvl is None:
                lvl = style.find(_qn("a:lvl1pPr"))
            if lvl is None:
                continue
            def_rpr = lvl.find(_qn("a:defRPr"))
            if def_rpr is not None and def_rpr.get("sz"):
                try:
                    return float(def_rpr.get("sz")) / 100.0
                except (TypeError, ValueError):
                    continue
    except Exception:
        return None
    return None


def iter_text_runs(slide) -> Iterator[Dict[str, Any]]:
    """Yield every run with resolved styling metadata."""

    for shape in iter_shapes(slide.shapes):
        if not getattr(shape, "has_text_frame", False):
            continue
        for paragraph_index, paragraph in enumerate(shape.text_frame.paragraphs):
            level = int(getattr(paragraph, "level", 0) or 0)
            for run_index, run in enumerate(paragraph.runs):
                if not run.text.strip():
                    continue
                yield {
                    "shape": shape,
                    "paragraph": paragraph,
                    "paragraph_index": paragraph_index,
                    "run": run,
                    "run_index": run_index,
                    "text": run.text,
                    "level": level,
                }


def resolve_run_style(prs, slide, shape, paragraph, run, scheme: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Resolve effective size, font, colour, and weight for one text run."""

    scheme = scheme if scheme is not None else theme_color_scheme(prs)
    size = None
    source = "explicit"
    try:
        if run.font.size is not None:
            size = float(run.font.size.pt)
    except Exception:
        pass
    if size is None:
        try:
            if paragraph.font.size is not None:
                size = float(paragraph.font.size.pt)
                source = "paragraph"
        except Exception:
            pass
    if size is None:
        inherited = _placeholder_style_size(shape, int(getattr(paragraph, "level", 0) or 0))
        if inherited is not None:
            size, source = inherited, "placeholder-style"
    if size is None:
        is_title = is_title_placeholder(shape)
        size, source = (40.0 if is_title else 18.0), "default-estimate"

    font_name = None
    try:
        if run.font.name:
            font_name = run.font.name
    except Exception:
        pass
    if not font_name:
        try:
            if paragraph.font.name:
                font_name = paragraph.font.name
        except Exception:
            pass

    bold = None
    try:
        bold = run.font.bold
    except Exception:
        bold = None
    if bold is None:
        try:
            bold = paragraph.font.bold
        except Exception:
            bold = None

    color = font_color_hex(run.font, scheme)
    color_source = "explicit" if color else "inherited"

    return {
        "size_pt": round(float(size), 2),
        "size_source": source,
        "font_name": font_name or "",
        "bold": bool(bold) if bold is not None else False,
        "bold_source": "explicit" if bold is not None else "inherited",
        "color_hex": color or "",
        "color_source": color_source,
    }


def deck_stats(prs, path: Optional[str] = None) -> Dict[str, Any]:
    """Collect structural and content statistics for a presentation."""

    scheme = theme_color_scheme(prs)
    slides: List[Dict[str, Any]] = []
    fonts: Dict[str, int] = {}
    colors: Dict[str, int] = {}
    total_chars = 0
    total_words = 0
    pictures = 0
    charts = 0
    tables = 0
    groups = 0
    notes_present = 0
    media: List[Dict[str, Any]] = []

    for index, slide in enumerate(prs.slides, start=1):
        slide_chars = 0
        slide_words = 0
        shape_count = 0
        for shape in iter_shapes(slide.shapes):
            shape_count += 1
            shape_type = str(getattr(shape, "shape_type", ""))
            if shape_type.startswith("PICTURE"):
                pictures += 1
            elif shape_type.startswith("CHART"):
                charts += 1
            elif shape_type.startswith("TABLE"):
                tables += 1
            elif shape_type.startswith("GROUP"):
                groups += 1
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text
                slide_chars += len(text)
                slide_words += len(text.split())
            for run_info in iter_text_runs(slide):
                style = resolve_run_style(prs, slide, run_info["shape"], run_info["paragraph"], run_info["run"], scheme)
                if style["font_name"]:
                    fonts[style["font_name"]] = fonts.get(style["font_name"], 0) + 1
                if style["color_hex"]:
                    colors[style["color_hex"]] = colors.get(style["color_hex"], 0) + 1
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text
        except Exception:
            notes = ""
        if notes.strip():
            notes_present += 1
        total_chars += slide_chars
        total_words += slide_words
        slides.append(
            {
                "index": index,
                "title": slide_title_text(slide),
                "layout": getattr(slide.slide_layout, "name", ""),
                "shape_count": shape_count,
                "characters": slide_chars,
                "words": slide_words,
                "has_notes": bool(notes.strip()),
                "notes_words": len(notes.split()),
                "hidden": bool(getattr(slide, "_element", None) is not None and slide._element.get("show") == "0"),
            }
        )

    try:
        for part in prs.part.package.iter_parts():
            name = str(part.partname)
            if name.startswith("/ppt/media/"):
                media.append({"part": name, "bytes": len(part.blob)})
    except Exception:
        pass

    sizes = {"width_in": emu_to_inches(prs.slide_width), "height_in": emu_to_inches(prs.slide_height)}
    return {
        "file": os.path.basename(path) if path else "",
        "bytes": os.path.getsize(path) if path and os.path.isfile(path) else None,
        "slide_count": len(prs.slides),
        "slide_size": sizes,
        "aspect_ratio": round((sizes["width_in"] or 1) / (sizes["height_in"] or 1), 3),
        "total_characters": total_chars,
        "total_words": total_words,
        "pictures": pictures,
        "charts": charts,
        "tables": tables,
        "groups": groups,
        "slides_with_notes": notes_present,
        "fonts": dict(sorted(fonts.items(), key=lambda item: -item[1])),
        "colors": dict(sorted(colors.items(), key=lambda item: -item[1])),
        "media": sorted(media, key=lambda item: -item["bytes"]),
        "media_bytes": sum(item["bytes"] for item in media),
        "sections": read_sections(prs),
        "slides": slides,
    }


def extract_outline(prs) -> Dict[str, Any]:
    """Return a structured outline: sections, slides, titles, bullets, notes flag."""

    sections = read_sections(prs)
    section_for_slide: Dict[int, str] = {}
    for section in sections:
        for index in section["slide_indexes"]:
            section_for_slide[index] = section["name"]
    slides: List[Dict[str, Any]] = []
    for index, slide in enumerate(prs.slides, start=1):
        bullets: List[str] = []
        for shape in iter_shapes(slide.shapes):
            if not getattr(shape, "has_text_frame", False):
                continue
            if is_title_placeholder(shape):
                continue
            name = str(getattr(shape, "name", "")).lower()
            if name.startswith(("pptx-agent-footer", "pptx-agent-slidenumber", "pptx-agent-footer-date")):
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text.strip()
                if text:
                    bullets.append(text)
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            notes = ""
        slides.append(
            {
                "index": index,
                "title": slide_title_text(slide),
                "section": section_for_slide.get(index, ""),
                "bullets": bullets[:40],
                "has_notes": bool(notes),
                "notes": notes[:2000],
                "layout": getattr(slide.slide_layout, "name", ""),
            }
        )
    return {"slide_count": len(prs.slides), "sections": sections, "slides": slides}


def outline_to_markdown(outline: Dict[str, Any], title: str = "Deck outline") -> str:
    lines = ["# %s" % title, ""]
    current_section = None
    for slide in outline["slides"]:
        section = slide.get("section") or ""
        if section and section != current_section:
            lines.append("## %s" % section)
            lines.append("")
            current_section = section
        heading = slide["title"] or "(untitled slide %d)" % slide["index"]
        lines.append("### %d. %s" % (slide["index"], heading))
        if slide["bullets"]:
            for bullet in slide["bullets"]:
                lines.append("- %s" % bullet)
        if slide.get("has_notes"):
            lines.append("")
            lines.append("> Notes: %s" % truncate(slide.get("notes", ""), 240))
        lines.append("")
    if not outline["slides"]:
        lines.append("_No slides found._")
    return "\n".join(lines).rstrip() + "\n"


def outline_to_text(outline: Dict[str, Any]) -> str:
    lines: List[str] = []
    for slide in outline["slides"]:
        prefix = ("[%s] " % slide["section"]) if slide.get("section") else ""
        lines.append("%s%d. %s" % (prefix, slide["index"], slide["title"] or "(untitled)"))
        for bullet in slide["bullets"]:
            lines.append("    - %s" % bullet)
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# package integrity validation (no external tools required)
# ---------------------------------------------------------------------------

REQUIRED_PACKAGE_PARTS = ("[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml")


def validate_package(path: str) -> Dict[str, Any]:
    """Validate OOXML package integrity: zip, XML, relationships, content types.

    This is a structural check, not a rendering check.  It catches the failure
    modes that make PowerPoint report "needs repair": truncated packages,
    malformed XML parts, relationships that point at missing parts, parts with
    no declared content type, and unreferenced parts.
    """

    import zipfile
    from xml.etree import ElementTree as ET

    resolved = resolve_input(path)
    issues: List[Dict[str, Any]] = []
    checks = 0

    def issue(kind: str, severity: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        issues.append({"kind": kind, "severity": severity, "message": message, "detail": detail or {}})

    try:
        archive = zipfile.ZipFile(resolved)
    except zipfile.BadZipFile as exc:
        raise ValidationError(
            "File is not a valid ZIP/OOXML package: %s" % exc,
            hint="The file may be corrupt or not a .pptx at all. Try recovering it in PowerPoint first.",
            details={"path": resolved},
        )

    with archive:
        bad_member = archive.testzip()
        checks += 1
        if bad_member:
            issue("zip", "error", "Corrupt zip member: %s" % bad_member, {"member": bad_member})
        names = set(archive.namelist())
        for required in REQUIRED_PACKAGE_PARTS:
            checks += 1
            if required not in names:
                issue("structure", "error", "Required package part is missing: %s" % required, {"part": required})

        content_types: Dict[str, str] = {}
        defaults: Dict[str, str] = {}
        if "[Content_Types].xml" in names:
            checks += 1
            try:
                root = ET.fromstring(archive.read("[Content_Types].xml"))
                for child in root:
                    tag = child.tag.split("}")[-1]
                    if tag == "Default":
                        defaults[(child.get("Extension") or "").lower()] = child.get("ContentType") or ""
                    elif tag == "Override":
                        content_types[(child.get("PartName") or "").lstrip("/")] = child.get("ContentType") or ""
            except ET.ParseError as exc:
                issue("xml", "error", "[Content_Types].xml is not well-formed: %s" % exc)

        xml_parts = [name for name in names if name.lower().endswith((".xml", ".rels"))]
        malformed = 0
        for name in xml_parts:
            checks += 1
            try:
                ET.fromstring(archive.read(name))
            except ET.ParseError as exc:
                malformed += 1
                issue("xml", "error", "Malformed XML in %s: %s" % (name, exc), {"part": name})
        if malformed:
            issue("xml", "error", "%d XML part(s) failed to parse." % malformed)

        missing_targets = 0
        external = 0
        for name in [item for item in names if item.endswith(".rels")]:
            checks += 1
            base = os.path.dirname(os.path.dirname(name)).replace("\\", "/")
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                continue
            for relationship in root:
                target = relationship.get("Target") or ""
                mode = relationship.get("TargetMode") or ""
                if mode == "External":
                    external += 1
                    continue
                if not target:
                    continue
                if target.startswith("/"):
                    resolved_target = target.lstrip("/")
                else:
                    resolved_target = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
                if resolved_target not in names:
                    missing_targets += 1
                    issue(
                        "relationship",
                        "error",
                        "Relationship in %s points at a missing part: %s" % (name, resolved_target),
                        {"rels": name, "target": resolved_target},
                    )
        if missing_targets:
            issue("relationship", "error", "%d relationship target(s) are missing." % missing_targets)

        undeclared = 0
        for name in names:
            if name.endswith("/"):
                continue
            checks += 1
            if name in content_types:
                continue
            base_name = name.rsplit("/", 1)[-1]
            extension = base_name.rsplit(".", 1)[1].lower() if "." in base_name else ""
            if extension not in defaults:
                undeclared += 1
                issue(
                    "content-type",
                    "error",
                    "Part has no declared content type: %s" % name,
                    {"part": name, "extension": extension},
                )
        if undeclared:
            issue("content-type", "error", "%d part(s) lack a content type." % undeclared)

        media_parts = [name for name in names if name.startswith("ppt/media/")]
        try:
            prs = Presentation(resolved)
            slide_count = len(prs.slides)
        except Exception as exc:
            slide_count = 0
            issue("open", "error", "python-pptx could not open the package: %s" % exc)

        try:
            prs = Presentation(resolved)
            referenced: set = set()
            for part in prs.part.package.iter_parts():
                try:
                    for rel in part.rels.values():
                        try:
                            referenced.add(str(rel.target_part.partname).lstrip("/"))
                        except Exception:
                            continue
                except Exception:
                    continue
            orphans = [name for name in names if name.startswith("ppt/") and name not in referenced
                       and not name.endswith((".rels", "[Content_Types].xml")) and name != "ppt/presentation.xml"]
            if orphans:
                issue(
                    "orphan",
                    "warning",
                    "%d part(s) are not referenced by any relationship." % len(orphans),
                    {"parts": sorted(orphans)[:40]},
                )
        except Exception:
            referenced = set()

    errors = len([item for item in issues if item["severity"] == "error"])
    warnings = len([item for item in issues if item["severity"] == "warning"])
    return {
        "file": os.path.basename(resolved),
        "path": resolved,
        "bytes": os.path.getsize(resolved),
        "checks": checks,
        "slide_count": slide_count,
        "xml_parts": len(xml_parts),
        "media_parts": len(media_parts),
        "external_relationships": external,
        "error_count": errors,
        "warning_count": warnings,
        "passed": errors == 0,
        "issues": issues,
    }
