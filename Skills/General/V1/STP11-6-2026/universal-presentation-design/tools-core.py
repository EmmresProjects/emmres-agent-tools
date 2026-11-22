"""Shared validation, parsing, themes, accessibility, and safe I/O.

Primary CLI: universal-presentation-design/tools-presentation-design.py
All packaged resource references are relative to the future skill root.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

TOOL_DIR = Path(__file__).resolve().parent
TOOL_NAME = TOOL_DIR.name
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_VALIDATION = 2
EXIT_APPROVAL = 3
EXIT_NOT_FOUND = 4
EXIT_DEPENDENCY = 5
EXIT_TIMEOUT = 6
EXIT_CANCELLED = 7


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: Any = None, exit_code: int = EXIT_VALIDATION):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class Deadline:
    def __init__(self, seconds: float | None = None):
        self.seconds = seconds
        self.start = time.monotonic()
        self.cancelled = False
        self._old_int = None
        self._old_term = None

    def __enter__(self):
        def handler(signum, frame):
            self.cancelled = True
            raise ToolError("CANCELLED", "Operation cancelled by signal.", {"signal": signum}, EXIT_CANCELLED)
        try:
            self._old_int = signal.signal(signal.SIGINT, handler)
            self._old_term = signal.signal(signal.SIGTERM, handler)
        except (ValueError, OSError, AttributeError):
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._old_int is not None:
                signal.signal(signal.SIGINT, self._old_int)
            if self._old_term is not None:
                signal.signal(signal.SIGTERM, self._old_term)
        except (ValueError, OSError, AttributeError):
            pass
        return False

    def check(self, stage: str = "work") -> None:
        if self.cancelled:
            raise ToolError("CANCELLED", "Operation cancelled.", {"stage": stage}, EXIT_CANCELLED)
        if self.seconds is not None and time.monotonic() - self.start > self.seconds:
            raise ToolError("TIMEOUT", "Operation exceeded its time budget.", {"stage": stage, "timeout_seconds": self.seconds}, EXIT_TIMEOUT)

    @property
    def elapsed(self) -> float:
        return round(time.monotonic() - self.start, 4)


def resolve_resource(reference: str | Path, base: Path | None = None) -> Path:
    """Resolve a packaged relative reference from cwd/skill root or the tool folder."""
    p = Path(reference)
    if p.is_absolute() or ".." in p.parts:
        raise ToolError("UNSAFE_PATH", "Resource paths must be relative and stay inside the skill root.", {"path": str(reference)})
    candidates = []
    if base:
        candidates.append(base / p)
    candidates.append(Path.cwd() / p)
    candidates.append(TOOL_DIR.parent / p)
    candidates.append(TOOL_DIR / p.name)
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise ToolError("RESOURCE_NOT_FOUND", "Referenced resource was not found.", {"path": str(reference), "tried": [str(c) for c in candidates]}, EXIT_NOT_FOUND)


def safe_output_path(path: str | Path, *, must_exist: bool = False) -> Path:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or any(part.startswith("~") for part in p.parts):
        raise ToolError("UNSAFE_PATH", "Use a relative input/output path without parent traversal.", {"path": str(path)})
    resolved = (Path.cwd() / p).resolve()
    root = Path.cwd().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolError("UNSAFE_PATH", "Path resolves outside the current working root.", {"path": str(path)})
    if must_exist and not resolved.exists():
        raise ToolError("INPUT_NOT_FOUND", "Input path does not exist.", {"path": str(path)}, EXIT_NOT_FOUND)
    return resolved


def load_json(path: Path, max_bytes: int) -> Any:
    size = path.stat().st_size
    if size > max_bytes:
        raise ToolError("INPUT_TOO_LARGE", "JSON input exceeds the configured byte limit.", {"path": str(path), "bytes": size, "limit": max_bytes})
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ToolError("INVALID_JSON", "Input is not valid JSON.", {"path": str(path), "line": exc.lineno, "column": exc.colno, "error": exc.msg})
    except UnicodeDecodeError as exc:
        raise ToolError("INVALID_ENCODING", "Input must be UTF-8 text.", {"path": str(path), "error": str(exc)})


def atomic_write(path: Path, data: str | bytes, *, overwrite: bool = False, approved: bool = False, retries: int = 2, backoff_ms: int = 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not overwrite:
            raise ToolError("OUTPUT_EXISTS", "Output already exists. Use a new path or approve an overwrite.", {"path": str(path)}, EXIT_APPROVAL)
        if not approved:
            raise ToolError("APPROVAL_REQUIRED", "Overwriting requires --overwrite and --approve.", {"path": str(path)}, EXIT_APPROVAL)
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            backup.unlink()
        path.replace(backup)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": ""}
    last = None
    for attempt in range(max(1, retries)):
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
            with os.fdopen(fd, mode, **kwargs) as fh:
                fh.write(data)
            os.replace(tmp, path)
            return path
        except OSError as exc:
            last = exc
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if attempt + 1 < retries:
                time.sleep(backoff_ms / 1000.0 * (attempt + 1))
    raise ToolError("WRITE_FAILED", "Could not write output after retries.", {"path": str(path), "error": str(last)}, EXIT_INTERNAL)


def prepare_output_dir(path: Path, *, overwrite: bool = False, approved: bool = False) -> Path:
    if path.exists():
        if not path.is_dir():
            raise ToolError("OUTPUT_NOT_DIRECTORY", "Output path exists and is not a directory.", {"path": str(path)})
        if any(path.iterdir()):
            if not overwrite:
                raise ToolError("OUTPUT_EXISTS", "Output directory is not empty. Use a new directory or approve an overwrite.", {"path": str(path)}, EXIT_APPROVAL)
            if not approved:
                raise ToolError("APPROVAL_REQUIRED", "Overwriting a non-empty directory requires --overwrite and --approve.", {"path": str(path)}, EXIT_APPROVAL)
            backup = path.with_name(path.name + ".bak")
            if backup.exists():
                raise ToolError("BACKUP_EXISTS", "A backup directory already exists; choose a new output path.", {"path": str(backup)}, EXIT_APPROVAL)
            path.replace(backup)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(path: str | None = None) -> dict:
    default = TOOL_DIR / "asset-config.json"
    cfg = load_json(resolve_resource(path) if path else default, 1024 * 1024)
    if not isinstance(cfg, dict):
        raise ToolError("INVALID_CONFIG", "Configuration must be a JSON object.")
    return cfg


def load_themes(config: dict, path: str | None = None) -> dict:
    ref = path or config.get("paths", {}).get("themes", f"{TOOL_NAME}/asset-themes.json")
    themes = load_json(resolve_resource(ref), 1024 * 1024)
    if not isinstance(themes, dict) or not themes:
        raise ToolError("INVALID_THEMES", "Theme file must be a non-empty object.")
    return themes


def load_schema(config: dict) -> dict:
    ref = config.get("paths", {}).get("deck_schema", f"{TOOL_NAME}/asset-deck.schema.json")
    return load_json(resolve_resource(ref), 2 * 1024 * 1024)


def validate_deck(deck: Any, config: dict, schema: dict | None = None) -> list[str]:
    try:
        import jsonschema
    except ImportError as exc:
        raise ToolError("DEPENDENCY_MISSING", "jsonschema is required for deck validation.", {"install": "python3 -m pip install jsonschema==4.25.1"}, EXIT_DEPENDENCY) from exc
    schema = schema or load_schema(config)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(deck), key=lambda e: list(e.path))
    if errors:
        raise ToolError("SCHEMA_VALIDATION_FAILED", "Deck does not match the deck schema.", {"errors": [{"path": "/".join(map(str, e.path)) or "$", "message": e.message} for e in errors[:25]]})
    warnings: list[str] = []
    ids = [s["id"] for s in deck["slides"]]
    if len(ids) != len(set(ids)):
        raise ToolError("DUPLICATE_SLIDE_ID", "Slide ids must be unique.", {"ids": ids})
    if len(deck["slides"]) > config["limits"]["max_slides"]:
        raise ToolError("TOO_MANY_SLIDES", "Deck exceeds the slide limit.", {"limit": config["limits"]["max_slides"]})
    sections = {s["id"] for s in deck.get("sections", [])}
    idset = set(ids)
    for slide in deck["slides"]:
        if slide.get("section") and slide["section"] not in sections:
            warnings.append(f"Slide {slide['id']} references unknown section {slide['section']}.")
        link = slide.get("link") or {}
        if link.get("target_slide") and link["target_slide"] not in idset:
            warnings.append(f"Slide {slide['id']} links to unknown slide {link['target_slide']}.")
        for opt in (slide.get("quiz") or {}).get("options", []):
            if opt.get("next") and opt["next"] not in idset:
                warnings.append(f"Quiz option on {slide['id']} links to unknown slide {opt['next']}.")
        objs = slide.get("objects") or []
        if len(objs) > config["limits"]["max_objects_per_slide"]:
            raise ToolError("TOO_MANY_OBJECTS", "Slide exceeds the object limit.", {"slide": slide["id"], "limit": config["limits"]["max_objects_per_slide"]})
    return warnings


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.href = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "noscript"}:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head", "noscript"} and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and self.href:
            self.parts.append(f" ({self.href})")
            self.href = None

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(source: str) -> str:
    parser = _HTMLText()
    parser.feed(source)
    parser.close()
    return parser.text()


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return re.sub(r"-{2,}", "-", value) or "item"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    for line in text.splitlines():
        if ":" in line and not line.startswith(("#", "-", "*", " ")):
            k, v = line.split(":", 1)
            if k.strip().lower() in {"audience", "outcome", "purpose", "title", "language", "theme"}:
                meta[k.strip().lower()] = v.strip()
    return meta, text


def parse_markdown(text: str, config: dict) -> dict:
    if len(text) > config["limits"]["max_text_chars"]:
        raise ToolError("INPUT_TOO_LARGE", "Text input exceeds the character limit.", {"limit": config["limits"]["max_text_chars"]})
    meta, text = _split_frontmatter(text)
    deck = {"schema_version": "1.0", "title": meta.get("title", "Untitled presentation"), "audience": meta.get("audience", ""), "purpose": meta.get("purpose", meta.get("outcome", "")), "language": meta.get("language", "en"), "direction": "ltr", "theme": meta.get("theme", config["defaults"]["theme"]), "canvas": copy.deepcopy(config["defaults"]["canvas"]), "sections": [], "slides": [], "metadata": {"source": "markdown"}}
    current_section = None
    current_slide = None
    bullets: list[str] = []
    paragraph: list[str] = []
    fenced = None
    fence_lines: list[str] = []

    def flush_slide():
        nonlocal current_slide, bullets, paragraph
        if current_slide is None:
            return
        if paragraph:
            current_slide["body"] = " ".join(paragraph).strip()
        if bullets:
            current_slide["bullets"] = bullets
        if not current_slide.get("body") and not current_slide.get("bullets"):
            current_slide["body"] = current_slide.get("subtitle", "")
        deck["slides"].append(current_slide)
        current_slide, bullets, paragraph = None, [], []

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if fenced is None:
                fenced = line[3:].strip().lower() or "text"
                fence_lines = []
            else:
                block = "\n".join(fence_lines)
                if fenced == "json":
                    try:
                        data = json.loads(block)
                        if isinstance(data, dict) and "slides" in data:
                            flush_slide()
                            return normalize_deck(data, config)
                        if current_slide is not None and isinstance(data, dict):
                            for key in ("chart", "table", "diagram", "timeline", "quiz", "kpis", "image"):
                                if key in data:
                                    current_slide[key] = data[key]
                                    current_slide["layout"] = key if key != "kpis" else "kpi"
                    except json.JSONDecodeError:
                        paragraph.append(block)
                else:
                    paragraph.append(block)
                fenced = None
            continue
        if fenced is not None:
            fence_lines.append(raw)
            continue
        if not line.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                flush_slide()
                deck["title"] = title
                current_slide = {"id": slugify(title), "layout": "title", "title": title, "duration_seconds": 30}
            elif level == 2:
                flush_slide()
                current_section = slugify(title)
                if not any(s["id"] == current_section for s in deck["sections"]):
                    deck["sections"].append({"id": current_section, "title": title})
                current_slide = {"id": f"{current_section}-section", "section": current_section, "layout": "section", "title": title, "duration_seconds": 15}
            else:
                flush_slide()
                current_slide = {"id": slugify(title), "section": current_section, "layout": "bullets", "title": title, "duration_seconds": 45}
            continue
        if re.match(r"^[-*+]\s+", line) or re.match(r"^\d+[.)]\s+", line):
            item = re.sub(r"^([-*+]|\d+[.)])\s+", "", line).strip()
            if current_slide is None:
                current_slide = {"id": f"slide-{len(deck['slides'])+1}", "layout": "bullets", "title": "Key points", "duration_seconds": 45}
            bullets.append(item)
            continue
        if current_slide is None:
            current_slide = {"id": f"slide-{len(deck['slides'])+1}", "layout": "bullets", "title": "Overview", "duration_seconds": 45}
        paragraph.append(line.strip())
    flush_slide()
    if not deck["slides"]:
        deck["slides"] = [{"id": "overview", "layout": "bullets", "title": deck["title"], "body": text.strip()[:2000], "duration_seconds": 45}]
    return normalize_deck(deck, config)


def parse_csv(text: str, config: dict, *, delimiter: str = ",") -> dict:
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows or not rows[0]:
        raise ToolError("INVALID_CSV", "CSV input must contain a header row.")
    if len(rows) - 1 > config["limits"]["max_csv_rows"]:
        raise ToolError("TOO_MANY_ROWS", "CSV exceeds the row limit.", {"limit": config["limits"]["max_csv_rows"]})
    headers = [h.strip() or f"column_{i+1}" for i, h in enumerate(rows[0])]
    data = [r + [""] * (len(headers) - len(r)) for r in rows[1:] if any(c.strip() for c in r)]
    numeric_cols = []
    for i in range(len(headers)):
        vals = []
        ok = True
        for r in data:
            try:
                vals.append(float(r[i]))
            except (ValueError, IndexError):
                ok = False
                break
        if ok and vals:
            numeric_cols.append(i)
    deck = {"schema_version": "1.0", "title": "Data presentation", "audience": "", "purpose": "Review the supplied data", "language": "en", "direction": "ltr", "theme": config["defaults"]["theme"], "canvas": copy.deepcopy(config["defaults"]["canvas"]), "sections": [], "metadata": {"source": "csv"}, "slides": []}
    deck["slides"].append({"id": "title", "layout": "title", "title": "Data presentation", "subtitle": f"{len(data)} rows, {len(headers)} columns", "duration_seconds": 20})
    if numeric_cols:
        cat_col = next((i for i in range(len(headers)) if i not in numeric_cols), None)
        categories = [r[cat_col] for r in data] if cat_col is not None else [str(i + 1) for i in range(len(data))]
        series = [{"name": headers[i], "values": [float(r[i]) for r in data]} for i in numeric_cols[:4]]
        deck["slides"].append({"id": "chart", "layout": "chart", "title": "Numeric overview", "chart": {"type": "column", "categories": categories, "series": series, "source": "Supplied CSV", "zero_baseline": True}, "duration_seconds": 45})
    deck["slides"].append({"id": "table", "layout": "table", "title": "Source data", "table": {"headers": headers, "rows": data[:200], "caption": f"Showing {min(len(data),200)} of {len(data)} rows."}, "duration_seconds": 45})
    return normalize_deck(deck, config)


def normalize_deck(raw: Any, config: dict) -> dict:
    if not isinstance(raw, dict):
        raise ToolError("INVALID_DECK", "Deck input must be a JSON object.")
    deck = copy.deepcopy(raw)
    deck.setdefault("schema_version", "1.0")
    deck.setdefault("title", "Untitled presentation")
    deck.setdefault("language", config["defaults"]["language"])
    deck.setdefault("direction", config["defaults"]["direction"])
    deck.setdefault("theme", config["defaults"]["theme"])
    deck.setdefault("canvas", copy.deepcopy(config["defaults"]["canvas"]))
    deck.setdefault("sections", [])
    deck.setdefault("metadata", {})
    deck.setdefault("assets", [])
    if "slides" not in deck or not isinstance(deck["slides"], list) or not deck["slides"]:
        raise ToolError("INVALID_DECK", "Deck must contain a non-empty slides array.")
    seen = set()
    for i, slide in enumerate(deck["slides"]):
        if not isinstance(slide, dict):
            raise ToolError("INVALID_SLIDE", "Each slide must be an object.", {"index": i})
        slide.setdefault("id", slugify(slide.get("title", f"slide-{i+1}")))
        base = slide["id"]
        n = 2
        while slide["id"] in seen:
            slide["id"] = f"{base}-{n}"
            n += 1
        seen.add(slide["id"])
        slide.setdefault("layout", infer_layout(slide))
        slide.setdefault("title", f"Slide {i+1}")
        slide.setdefault("duration_seconds", 45)
        if "body" in slide and isinstance(slide["body"], list):
            slide.setdefault("bullets", slide["body"])
            slide["body"] = " ".join(str(x) for x in slide["body"])
    return deck


def infer_layout(slide: dict) -> str:
    for key, layout in (("chart", "chart"), ("table", "table"), ("diagram", "diagram"), ("timeline", "timeline"), ("image", "image"), ("quiz", "quiz"), ("kpis", "kpi")):
        if slide.get(key):
            return layout
    return "bullets"


def read_text_input(path: Path, config: dict) -> str:
    if path.stat().st_size > config["limits"]["max_input_bytes"]:
        raise ToolError("INPUT_TOO_LARGE", "Input exceeds the byte limit.", {"path": str(path), "limit": config["limits"]["max_input_bytes"]})
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ToolError("INVALID_ENCODING", "Input must be UTF-8 text.", {"path": str(path), "error": str(exc)})


def read_deck_input(path: str | None, config: dict, *, kind: str = "auto", text: str | None = None) -> tuple[dict, str]:
    if text is not None:
        return parse_markdown(text, config), "text"
    if not path:
        raise ToolError("INPUT_REQUIRED", "Provide --input or --text.")
    p = safe_output_path(path, must_exist=True)
    suffix = p.suffix.lower()
    if kind == "auto":
        kind = {".json": "deck", ".md": "markdown", ".markdown": "markdown", ".txt": "markdown", ".html": "html", ".htm": "html", ".csv": "csv", ".tsv": "tsv", ".pptx": "pptx"}.get(suffix, "markdown")
    if kind == "deck":
        return normalize_deck(load_json(p, config["limits"]["max_input_bytes"]), config), kind
    if kind in {"markdown", "text"}:
        return parse_markdown(read_text_input(p, config), config), kind
    if kind == "html":
        return parse_markdown(html_to_text(read_text_input(p, config)), config), kind
    if kind in {"csv", "tsv"}:
        return parse_csv(read_text_input(p, config), config, delimiter="\t" if kind == "tsv" else ","), kind
    if kind == "pptx":
        from importlib import import_module
        mod = sys.modules.get("tools_pptx")
        if mod is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("tools_pptx", TOOL_DIR / "tools-pptx.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["tools_pptx"] = mod
            spec.loader.exec_module(mod)
        return mod.read_pptx(p, config), kind
    raise ToolError("UNSUPPORTED_INPUT", "Unsupported input kind.", {"kind": kind})


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ToolError("INVALID_COLOR", "Colors must be hex RGB values.", {"color": value})
    try:
        return tuple(int(v[i:i+2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ToolError("INVALID_COLOR", "Colors must be hex RGB values.", {"color": value})


def rgb_to_hex(rgb: Iterable[int]) -> str:
    return "#" + "".join(f"{max(0,min(255,int(round(c)))):02X}" for c in rgb)


def relative_luminance(color: str) -> float:
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 3)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def resolve_theme(deck: dict, themes: dict, override: str | None = None) -> dict:
    name = override or (deck.get("theme") if isinstance(deck.get("theme"), str) else "neutral")
    if isinstance(deck.get("theme"), dict):
        base = copy.deepcopy(themes.get("neutral", next(iter(themes.values()))))
        base.update(deck["theme"])
        return base
    if name not in themes:
        raise ToolError("UNKNOWN_THEME", "Theme is not defined in the theme file.", {"theme": name, "available": sorted(themes)})
    return copy.deepcopy(themes[name])


def apply_theme(deck: dict, theme: dict, name: str | None = None) -> dict:
    out = copy.deepcopy(deck)
    if name is not None:
        out["theme"] = name
    out["resolved_theme"] = theme
    return out


def slide_text(slide: dict) -> str:
    parts = [slide.get("title", ""), slide.get("subtitle", ""), slide.get("body", "")]
    parts.extend(slide.get("bullets") or [])
    if slide.get("table"):
        parts.extend(slide["table"].get("headers") or [])
        for row in slide["table"].get("rows") or []:
            parts.extend(str(c) for c in row)
    if slide.get("chart"):
        parts.extend(slide["chart"].get("categories") or [])
        parts.extend(s.get("name", "") for s in slide["chart"].get("series") or [])
    if slide.get("diagram"):
        parts.extend(n.get("label", "") for n in slide["diagram"].get("nodes") or [])
    if slide.get("timeline"):
        parts.extend(e.get("label", "") for e in slide["timeline"].get("events") or [])
    if slide.get("quiz"):
        parts.append(slide["quiz"].get("question", ""))
        parts.extend(o.get("text", "") for o in slide["quiz"].get("options") or [])
    parts.extend(str(k.get("label", "")) + " " + str(k.get("value", "")) for k in slide.get("kpis") or [])
    return " ".join(str(p) for p in parts if p)


def audit_deck(deck: dict, config: dict, themes: dict, *, checks: list[str] | None = None) -> dict:
    checks = checks or ["accessibility", "brand", "content", "links", "charts", "privacy", "motion", "geometry", "media"]
    theme = resolve_theme(deck, themes)
    colors = theme["colors"]
    acc = config["accessibility"]
    findings: list[dict] = []

    def add(check, severity, slide_id, message, remedy, evidence=None):
        findings.append({"check": check, "severity": severity, "slide_id": slide_id, "message": message, "remedy": remedy, "evidence": evidence or {}})

    ratios = {k: contrast_ratio(v, colors["background"]) for k, v in colors.items() if isinstance(v, str) and v.startswith("#")}
    if "accessibility" in checks or "brand" in checks:
        for role in ("text", "muted", "accent", "accent2"):
            if ratios.get(role, 0) < acc["min_contrast"]:
                add("accessibility", "major", None, f"Theme color {role} has insufficient contrast on background.", "Use the high-contrast theme or darken/lighten the color.", {"role": role, "ratio": ratios.get(role), "required": acc["min_contrast"]})
    for idx, slide in enumerate(deck["slides"], 1):
        sid = slide["id"]
        text = slide_text(slide)
        words = len(text.split())
        if "accessibility" in checks or "content" in checks:
            if not slide.get("alt_text") and slide.get("layout") in {"image", "chart", "diagram", "timeline", "table"}:
                add("accessibility", "major", sid, "Visual slide lacks alt_text or an equivalent description.", "Add a concise alt_text describing the visual's meaning.")
            if words > acc["max_words_per_slide"]:
                add("content", "minor", sid, "Slide exceeds the recommended word budget.", "Split the slide or move detail to speaker notes.", {"words": words, "limit": acc["max_words_per_slide"]})
            if len(slide.get("bullets") or []) > acc["max_bullets"]:
                add("content", "minor", sid, "Too many bullets for comfortable reading.", "Group or split the bullets.", {"bullets": len(slide["bullets"]), "limit": acc["max_bullets"]})
            if slide.get("layout") == "table" and not (slide.get("table") or {}).get("headers"):
                add("accessibility", "major", sid, "Table lacks header cells.", "Add table.headers so screen readers can announce columns.")
        if "charts" in checks and slide.get("chart"):
            chart = slide["chart"]
            vals = [v for s in chart.get("series", []) for v in s.get("values", []) if isinstance(v, (int, float))]
            if chart.get("type") in {"bar", "column", "area"} and vals and chart.get("zero_baseline") is False:
                add("charts", "major", sid, "Bar/column/area chart does not use a zero baseline.", "Set zero_baseline=true unless the deviation is explicitly annotated.", {"min": min(vals)})
            if not chart.get("source"):
                add("charts", "minor", sid, "Chart lacks a source note.", "Add chart.source or mark the data as illustrative.")
            if any(v < 0 for v in vals) and chart.get("type") in {"funnel", "pie"}:
                add("charts", "major", sid, "Negative values are not valid for this chart type.", "Use a bar or waterfall chart.")
        if "links" in checks:
            link = slide.get("link") or {}
            if link.get("url") and not re.match(r"^https?://", link["url"]):
                add("links", "major", sid, "External link is not http(s).", "Use an https URL or an internal slide link.", {"url": link["url"]})
        if "motion" in checks:
            tr = slide.get("transition") or {}
            if tr.get("duration_ms", 0) > acc["max_motion_ms"]:
                add("motion", "major", sid, "Transition exceeds the motion duration budget.", "Shorten the transition or disable motion.", {"duration_ms": tr.get("duration_ms")})
        if "privacy" in checks:
            for m in re.finditer(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text + " " + slide.get("notes", "")):
                add("privacy", "minor", sid, "Possible personal email address found in slide or notes.", "Review and redact before external delivery.", {"match": m.group(0)[:3] + "***"})
            if re.search(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S+", text + " " + slide.get("notes", "")):
                add("privacy", "critical", sid, "Possible credential-like text found.", "Remove secrets before sharing; use a credential manager.", {})
        if "geometry" in checks:
            canvas = deck.get("canvas", config["defaults"]["canvas"])
            for obj in slide.get("objects") or []:
                x, y, w, h = obj.get("x", 0), obj.get("y", 0), obj.get("w", 0), obj.get("h", 0)
                if x < 0 or y < 0 or x + w > canvas["width"] or y + h > canvas["height"]:
                    add("geometry", "major", sid, "Object extends outside the canvas.", "Reposition or resize the object.", {"object": obj.get("id"), "box": [x, y, w, h]})
        if "media" in checks:
            img = slide.get("image")
            if img and img.get("path"):
                try:
                    resolve_resource(img["path"])
                except ToolError:
                    add("media", "major", sid, "Image asset cannot be resolved from the skill root.", "Fix the relative path or include the asset.", {"path": img["path"]})
    for asset in deck.get("assets", []):
        if "media" in checks:
            try:
                resolve_resource(asset["path"])
            except ToolError:
                add("media", "major", None, "Declared asset cannot be resolved.", "Fix the relative path or include the asset.", {"path": asset["path"]})
        if "brand" in checks and not asset.get("license"):
            add("brand", "minor", None, "Asset lacks license metadata.", "Record the license or usage rights.", {"asset": asset.get("id")})
    score = max(0, 100 - sum({"critical": 25, "major": 10, "minor": 3}.get(f["severity"], 1) for f in findings))
    return {"schema_version": "1.0", "deck_title": deck.get("title"), "slide_count": len(deck["slides"]), "checks": checks, "theme_contrast": ratios, "findings": findings, "score": score, "passed": not any(f["severity"] in {"critical", "major"} for f in findings)}


def repair_deck(deck: dict, config: dict, themes: dict, audit: dict | None = None) -> tuple[dict, list[str]]:
    out = copy.deepcopy(deck)
    audit = audit or audit_deck(out, config, themes)
    changes: list[str] = []
    theme = resolve_theme(out, themes)
    if any(f["check"] == "accessibility" and f["slide_id"] is None for f in audit["findings"]):
        theme = copy.deepcopy(themes.get("high-contrast", theme))
        out["theme"] = "high-contrast"
        out["resolved_theme"] = theme
        changes.append("Switched to high-contrast theme.")
    for slide in out["slides"]:
        if not slide.get("alt_text") and slide.get("layout") in {"image", "chart", "diagram", "timeline", "table"}:
            slide["alt_text"] = f"{slide.get('title','Visual')}: {describe_visual(slide)}"
            changes.append(f"Added alt_text to {slide['id']}.")
        if slide.get("chart") and slide["chart"].get("type") in {"bar", "column", "area"} and slide["chart"].get("zero_baseline") is False:
            slide["chart"]["zero_baseline"] = True
            changes.append(f"Restored zero baseline on {slide['id']}.")
        if slide.get("layout") == "table" and not (slide.get("table") or {}).get("headers"):
            rows = slide["table"].get("rows") or []
            slide["table"]["headers"] = [f"Column {i+1}" for i in range(len(rows[0]) if rows else 0)]
            changes.append(f"Added generic table headers to {slide['id']}; review them.")
        if (slide.get("transition") or {}).get("duration_ms", 0) > config["accessibility"]["max_motion_ms"]:
            slide["transition"]["duration_ms"] = config["accessibility"]["max_motion_ms"]
            changes.append(f"Shortened transition on {slide['id']}.")
    out.setdefault("review", {}).setdefault("changes", []).append({"type": "accessibility-repair", "changes": changes})
    return out, changes


def describe_visual(slide: dict) -> str:
    if slide.get("chart"):
        c = slide["chart"]
        names = ", ".join(s.get("name", f"series {i+1}") for i, s in enumerate(c.get("series", [])))
        return f"{c.get('type','chart')} showing {names or 'values'}."
    if slide.get("table"):
        t = slide["table"]
        return f"table with {len(t.get('rows', []))} rows and columns {', '.join(t.get('headers', []))}."
    if slide.get("diagram"):
        d = slide["diagram"]
        return f"{d.get('type','diagram')} with {len(d.get('nodes', []))} connected steps."
    if slide.get("timeline"):
        return f"timeline with {len(slide['timeline'].get('events', []))} events."
    if slide.get("image"):
        return slide["image"].get("alt") or slide.get("title", "image")
    return slide.get("title", "visual")


def parse_request(source: str) -> dict:
    text = html_to_text(source) if re.search(r"<[a-zA-Z][^>]*>", source) else source
    text = text.replace("\u2010", "-").replace("\u2011", "-")
    categories: list[dict] = []
    current = None
    pending_name = None
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        m = re.match(r"^(?:Category\s+)?(\d+)[.)]?\s*(?:Category\s+\d+:)?\s*(.+?)(?:\s*\(\d+\s+skills\))?$", line, re.I)
        if m and ("category" in line.lower() or re.search(r"\(\d+\s+skills\)", line) or re.match(r"^\d+[.)]\s+[A-Z]", line)) and "skill name" not in line.lower():
            name = re.sub(r"^Category\s+\d+:\s*", "", m.group(2), flags=re.I).strip()
            current = {"number": int(m.group(1)), "name": name, "skills": []}
            categories.append(current)
            pending_name = None
            continue
        m = re.search(r"Skill Name:\s*(.+?)(?=\s+Description:|$)", line, re.I)
        if m:
            pending_name = m.group(1).strip()
            if current is None:
                current = {"number": len(categories) + 1, "name": "Requested capabilities", "skills": []}
                categories.append(current)
            current["skills"].append({"name": pending_name, "description": ""})
            d = re.search(r"Description:\s*(.+)$", line, re.I)
            if d:
                current["skills"][-1]["description"] = d.group(1).strip()
                pending_name = None
            continue
        d = re.search(r"Description:\s*(.+)$", line, re.I)
        if d and current and current["skills"]:
            current["skills"][-1]["description"] += (" " if current["skills"][-1]["description"] else "") + d.group(1).strip()
            pending_name = None
    if not categories:
        lines = [l.strip(" -\t") for l in text.splitlines() if l.strip()]
        if lines:
            categories = [{"number": 1, "name": lines[0], "skills": [{"name": x, "description": ""} for x in lines[1:]] or [{"name": lines[0], "description": ""}]}]
    total = sum(len(c["skills"]) for c in categories)
    umbrella = slugify(categories[0]["name"]) if len(categories) == 1 else "universal-presentation-design"
    return {"schema_version": "1.0", "umbrella_name": umbrella, "category_count": len(categories), "skill_count": total, "categories": categories}


def fingerprint(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def diff_decks(before: dict, after: dict) -> dict:
    b = {s["id"]: s for s in before.get("slides", [])}
    a = {s["id"]: s for s in after.get("slides", [])}
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed = [sid for sid in sorted(set(a) & set(b)) if fingerprint(a[sid]) != fingerprint(b[sid])]
    return {"added": added, "removed": removed, "changed": changed, "unchanged": sorted(set(a) & set(b) - set(changed)), "before_fingerprint": fingerprint(before), "after_fingerprint": fingerprint(after)}


def format_number(value: float, locale: str, config: dict) -> str:
    loc = config.get("locales", {}).get(locale, config.get("locales", {}).get("en", {"decimal": ".", "thousands": ","}))
    s = f"{value:,.2f}".rstrip("0").rstrip(".")
    s = s.replace(",", "\x00").replace(".", loc.get("decimal", ".")).replace("\x00", loc.get("thousands", ","))
    return s


def plain_language(text: str) -> tuple[str, list[str]]:
    replacements = {"utilize": "use", "in order to": "to", "commence": "start", "terminate": "end", "facilitate": "help", "leverage": "use", "aforementioned": "this", "subsequent": "next"}
    notes = []
    out = text
    for a, b in replacements.items():
        new = re.sub(rf"\b{re.escape(a)}\b", b, out, flags=re.I)
        if new != out:
            notes.append(f"Replaced '{a}' with '{b}'.")
            out = new
    sentences = re.split(r"(?<=[.!?])\s+", out)
    long = [s for s in sentences if len(s.split()) > 30]
    if long:
        notes.append(f"{len(long)} sentence(s) exceed 30 words; consider splitting.")
    return out, notes
