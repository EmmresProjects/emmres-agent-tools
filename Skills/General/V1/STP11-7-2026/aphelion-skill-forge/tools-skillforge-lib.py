#!/usr/bin/env python3
"""tools-skillforge-lib.py - shared internals for the Aphelion Skill Forge.

This module is not meant to be executed directly. It is imported by
tools-skillforge.py (primary entry point), tools-skillforge-validate.py and
tools-skillforge-selftest.py. Imports are resolved by file path so the folder
name can change without breaking anything.

Responsibilities
    1. Input acquisition and text normalisation (Markdown, HTML, plain text).
    2. Extraction of categories, Skill Tool names and descriptions.
    3. Unification of related entries into one Skill Tool specification.
    4. Risk and approval policy evaluation.
    5. Validation of names, sizes, counts and file layout.

Standard library only.
"""

from __future__ import annotations

import datetime as _dt
import html
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

LIB_VERSION = "1.0.0"

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESOURCE_PREFIXES = ("tools-", "knowledge-", "asset-")
FORBIDDEN_NAMES = {"skill.md", "core.md", "manifest.json", "index.json"}
# Extension per generator target. Only extensions listed here are ever emitted.
GENERATOR_EXTENSIONS = {
    "python": ".py",
    "node": ".mjs",
    "shell": ".sh",
    "none": ".md",
}
RESOURCE_EXTENSIONS = {".py", ".mjs", ".js", ".sh", ".ps1", ".json", ".md", ".txt",
                       ".yaml", ".yml", ".toml", ".cfg", ".ini", ".csv", ".svg",
                       ".html", ".sql", ".ts"}


class ForgeError(Exception):
    """Structured, user-facing error. Always carries a machine code."""

    def __init__(self, code: str, message: str, hint: str = "", details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


# --------------------------------------------------------------------------
# Timeouts, retries and checkpoints (shared by every tool)
# --------------------------------------------------------------------------

@dataclass
class Budget:
    """Wall-clock budget plus retry counter for a single operation."""

    timeout_seconds: float = 120.0
    retries: int = 2
    backoff_seconds: float = 0.25
    started: float = field(default_factory=time.monotonic)
    attempts: dict = field(default_factory=dict)

    def remaining(self) -> float:
        return max(0.0, self.timeout_seconds - (time.monotonic() - self.started))

    def check(self) -> None:
        if self.remaining() <= 0:
            raise ForgeError(
                "E_TIMEOUT",
                "Operation exceeded the %.0f s budget." % self.timeout_seconds,
                "Raise --timeout, or split the input into smaller batches with --max-items.",
            )

    def run(self, label: str, fn, *args, **kwargs):
        """Run fn with bounded retries. Errors are classified, never hidden."""
        attempt = 0
        delay = self.backoff_seconds
        while True:
            self.check()
            attempt += 1
            self.attempts[label] = attempt
            try:
                return fn(*args, **kwargs)
            except ForgeError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                transient = isinstance(exc, (OSError, TimeoutError))
                if attempt > self.retries or not transient:
                    raise ForgeError(
                        "E_STEP_FAILED",
                        "%s failed on attempt %d: %s" % (label, attempt, exc),
                        "Re-run with --verbose to see the failing step, then retry.",
                        {"step": label, "attempt": attempt,
                         "transient": transient, "error": type(exc).__name__},
                    ) from exc
                time.sleep(min(delay, self.remaining()))
                delay *= 2


@dataclass
class Checkpoint:
    """Append-only JSONL progress log so long runs can resume."""

    path: Path | None = None
    enabled: bool = False

    def write(self, step: str, status: str, **extra) -> None:
        if not self.enabled or self.path is None:
            return
        record = {
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "step": step,
            "status": status,
        }
        record.update(extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def last_completed(self) -> str | None:
        if not self.enabled or self.path is None or not self.path.exists():
            return None
        last = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") == "ok":
                last = rec.get("step")
        return last


# --------------------------------------------------------------------------
# Input acquisition and normalisation
# --------------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Reduce HTML to line-oriented text, dropping tags, CSS and scripts."""

    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "br", "table", "ul", "ol", "dd", "dt", "td"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: D401 - parser hook
        if tag in {"script", "style", "noscript", "template"}:
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"} and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = html.unescape(raw)
        return raw


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|body|div|p|li|ul|ol|span|table|tr|td|td|h[1-6]|section|article)\b",
                          text, re.IGNORECASE))


def normalise_text(text: str) -> str:
    """Strip tags, CSS, attributes, numbering decorations and extra spacing."""
    if "\x00" in text:
        raise ForgeError("E_BINARY_INPUT", "Input contains NUL bytes; it is not text.",
                         "Pass a plain .md, .txt, .html or .json file.")
    if looks_like_html(text):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:  # noqa: BLE001 - malformed markup is recoverable
            raise ForgeError("E_HTML_PARSE", "Could not parse HTML input: %s" % exc,
                             "Paste the readable text instead, or fix the markup.") from exc
        text = parser.text()

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\{\{.*?\}\}|<%%.*?%%>", " ", text)       # template residue
    text = re.sub(r"\{[^{}\n]{0,120}\}", " ", text)          # inline css/attr blocks
    text = re.sub(r"(?i)\b(class|data-[a-z-]+|style|id|aria-[a-z]+)\s*=\s*(\"[^\"]*\"|'[^']*')", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_input(source: str, max_bytes: int) -> tuple[str, str]:
    """Return (raw_text, origin_label). Reads a file or the '-' stdin marker."""
    if source == "-":
        data = sys.stdin.buffer.read(max_bytes + 1)
        origin = "<stdin>"
    else:
        path = Path(source)
        if not path.exists():
            raise ForgeError("E_INPUT_MISSING", "Input not found: %s" % source,
                             "Check the path, or pass --input - to read stdin.")
        if path.is_dir():
            raise ForgeError("E_INPUT_IS_DIR", "Input is a directory: %s" % source,
                             "Pass a single .md, .txt, .html or .json file.")
        size = path.stat().st_size
        if size > max_bytes:
            raise ForgeError("E_INPUT_TOO_LARGE",
                             "Input is %d bytes, over the %d byte limit." % (size, max_bytes),
                             "Raise max_input_bytes in the policy file, or split the input.")
        data = path.read_bytes()
        origin = str(path)
    if len(data) > max_bytes:
        raise ForgeError("E_INPUT_TOO_LARGE",
                         "Input exceeds the %d byte limit." % max_bytes,
                         "Raise max_input_bytes in the policy file, or split the input.")
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), origin
        except UnicodeDecodeError:
            continue
    raise ForgeError("E_ENCODING", "Input could not be decoded as text.",
                     "Save the input as UTF-8 and retry.")


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

CATEGORY_PATTERNS = [
    re.compile(r"^category\s*(?:\d+)?\s*[:.\-]\s*(?P<name>[^|]{2,120})$", re.IGNORECASE),
    re.compile(r"^#{1,6}\s*(?P<name>[^#|]{2,120})$"),
    re.compile(r"^(?:section|area|domain|group|theme)\s*[:.\-]\s*(?P<name>[^|]{2,120})$", re.IGNORECASE),
]
FOCUS_RE = re.compile(r"^\s*(?:focus|scope|about|purpose|summary)\s*[:.\-]\s*(?P<focus>.+)$", re.IGNORECASE)
SKILL_LINE_RE = re.compile(
    r"^(?P<num>\d{1,4})?\s*[).:\-\u2013]?\s*(?:skill\s*(?:tool)?\s*name\s*[:.\-\u2013]\s*)?(?P<name>[A-Za-z][^:|\n]{1,90})$",
    re.IGNORECASE)
NAME_LABEL_RE = re.compile(r"^(?:skill\s*(?:tool)?\s*name|tool\s*name|name|title)\s*[:.\-\u2013]\s*(?P<name>.+)$",
                           re.IGNORECASE)
DESC_RE = re.compile(r"^(?:description|desc|purpose|capability|summary|what it does)\s*[:.\-\u2013]\s*(?P<d>.*)$",
                     re.IGNORECASE)
STOP_HEADING_RE = re.compile(
    r"^(?:appendix|references?|index|catalog index|table of contents|contents|notes?|"
    r"how to use|source foundation|introduction|overview|preface|about this)\b", re.IGNORECASE)
TOC_LINE_RE = re.compile(r"(?:\(\s*\d+\s+skills?\s*\)|\bfocus\s*:|\bpage\s+\d+\b)", re.IGNORECASE)


@dataclass
class RawItem:
    name: str
    description: str
    category: str | None = None
    number: int | None = None


@dataclass
class Category:
    name: str
    focus: str = ""
    items: list = field(default_factory=list)


@dataclass
class ParseResult:
    items: list
    categories: list
    mode: str
    warnings: list
    source_origin: str = ""
    source_bytes: int = 0

    def category_names(self) -> list:
        return [c.name for c in self.categories]


def _clean_name(name: str) -> str:
    name = re.sub(r"[*_`#>]+", "", name)
    name = re.sub(r"^\s*\d{1,4}\s*[).:\-\u2013]?\s*", "", name)
    name = re.sub(r"\s*[-\u2013|]\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip(" .:;-|\u2013")
    return name


def _is_likely_description(text: str) -> bool:
    words = text.split()
    return len(words) >= 6 or text.rstrip().endswith(".")


def _strip_markers(line: str) -> tuple[str, int]:
    """Remove leading markdown markers; return (text, heading_level)."""
    level = 0
    m = re.match(r"^(#{1,6})\s+", line)
    if m:
        level = len(m.group(1))
        line = line[m.end():]
    else:
        line = re.sub(r"^(?:[-*+>\u2022]|\d{1,3}[).])\s+", "", line)
    return line.strip(), level


def _number_prefix(line: str):
    """Return (number, remainder) when the line starts with a list number."""
    m = re.match(r"^\s*(\d{1,4})\s*[).:\-\u2013]\s+(.*)$", line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, line


def _entry_start(lines: list) -> int:
    """Index of the first numbered category marker, so cover pages are ignored.

    Only an explicit 'Category N:' marker triggers skipping. Documents without
    one are parsed from the first line, and headings are resolved in order.
    """
    for idx, line in enumerate(lines):
        text, _ = _strip_markers(line)
        if re.match(r"^(?:category|section)\s*\d+\s*[:.\-]", text, re.IGNORECASE):
            return idx
    return 0


def parse_catalog(text: str, origin: str = "") -> ParseResult:
    """Extract categories and skill items from Markdown, HTML or plain text."""
    raw = text
    text = normalise_text(text)
    if not text:
        raise ForgeError("E_EMPTY_INPUT", "No usable text found in the input.",
                         "Provide category names with descriptions.")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln and not re.fullmatch(r"[-=_*~]{3,}", ln)]
    lines = lines[_entry_start(lines):]

    items: list[RawItem] = []
    categories: list[Category] = []
    warnings: list[str] = []
    current_cat: Category | None = None
    pending: dict | None = None

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        name = _clean_name(pending["name"])
        desc = re.sub(r"\s+", " ", " ".join(pending["desc"])).strip()
        if name and len(name) >= 3 and not re.fullmatch(r"[\d\s.\-]+", name):
            item = RawItem(name=name, description=desc,
                           category=pending["category"], number=pending["number"])
            items.append(item)
            if current_cat is not None and pending.get("cat_ref") is current_cat:
                current_cat.items.append(item)
        pending = None

    def pending_cat_probe(cat, pend) -> bool:
        return pend is not None

    for idx_first_heading, raw_line in enumerate(lines):
        text_line, heading = _strip_markers(raw_line.strip())
        line = text_line.strip()
        if not line:
            continue

        # Explicit category marker always wins.
        m_cat = re.match(r"^(?:category|section|area|domain|group|theme)\s*(?:\d+)?\s*[:.\-]\s*(.+)$",
                         line, re.IGNORECASE)
        if m_cat:
            candidate = _clean_name(m_cat.group(1))
            if candidate and len(candidate) <= 120:
                flush()
                current_cat = Category(name=candidate)
                categories.append(current_cat)
                continue

        # Heading categories: a heading is promoted to a category when a focus
        # line follows it, or when a numbered capability block follows it.
        if heading and not pending and not NAME_LABEL_RE.match(line) \
                and not DESC_RE.match(line) and len(line.split()) <= 14 \
                and not line.rstrip().endswith("."):
            lookahead = [_strip_markers(l2)[0] for l2 in lines[idx_first_heading + 1:
                                                               idx_first_heading + 5]]
            focus_ahead = any(FOCUS_RE.match(l2) for l2 in lookahead)
            entry_ahead = any(re.match(r"^\d{1,4}\s*[).:\-\u2013]\s+", l2)
                              or NAME_LABEL_RE.match(l2) for l2 in lookahead)
            if focus_ahead or entry_ahead:
                candidate = _clean_name(line)
                if candidate and len(candidate) >= 3:
                    flush()
                    current_cat = Category(name=candidate)
                    categories.append(current_cat)
                    continue

        # A heading that announces a section we should not mine.
        if (heading or len(line.split()) <= 8) and STOP_HEADING_RE.match(line) \
                and not DESC_RE.match(line) and not _is_likely_description(line):
            flush()
            continue

        if TOC_LINE_RE.search(line) and (heading or re.match(r"^\d", line)):
            continue

        m = FOCUS_RE.match(line)
        if m and current_cat is not None:
            current_cat.focus = re.sub(r"\s+", " ", m.group("focus")).strip()
            continue

        number, remainder = _number_prefix(line)

        m = NAME_LABEL_RE.match(remainder)
        if m:
            flush()
            pending = {"name": m.group("name"), "desc": [],
                       "category": current_cat.name if current_cat else None,
                       "number": number, "cat_ref": current_cat}
            continue

        m = DESC_RE.match(line)
        if m and pending is not None:
            pending["desc"].append(m.group("d"))
            continue

        if heading and current_cat is not None and not pending:
            # Additional named heading inside a category: start of a new entry.
            candidate = _clean_name(line)
            if candidate and len(candidate.split()) <= 12:
                flush()
                pending = {"name": candidate, "desc": [],
                           "category": current_cat.name, "number": None, "cat_ref": current_cat}
                continue

        if number is not None:
            candidate = _clean_name(remainder)
            if candidate and len(candidate.split()) <= 14 and not candidate.endswith((".", ":")):
                flush()
                pending = {"name": candidate, "desc": [],
                           "category": current_cat.name if current_cat else None,
                           "number": number, "cat_ref": current_cat}
                continue

        if pending is not None:
            pending["desc"].append(line)
            if current_cat is not None and current_cat.focus \
                    and not re.search(r"[.!?]$", current_cat.focus) and len(current_cat.focus) < 300:
                current_cat.focus = (current_cat.focus + " " + line).strip()
            continue
        if current_cat is not None and not current_cat.focus and _is_likely_description(line):
            current_cat.focus = line

    flush()

    missing = [i for i in items if len((i.description or "").split()) < 4]
    if missing and not categories:
        items = [i for i in items if i not in missing]

    mode = "catalog"
    if not items:
        mode = _fallback_mode(text, items, categories, warnings)
    if not items:
        raise ForgeError("E_NO_CAPABILITIES",
                         "No Skill Tool names or descriptions could be extracted.",
                         "Use lines like '1. Skill Name: X' followed by 'Description: ...', "
                         "or plain sentences that describe the capability.")

    if len(items) > 400:
        warnings.append("Extracted %d items; the policy max_items default is 400." % len(items))
    unnamed = [i for i in items if len(i.name.split()) > 12]
    if unnamed:
        warnings.append("%d extracted names are longer than 12 words; check the input formatting."
                        % len(unnamed))

    return ParseResult(items=items, categories=categories, mode=mode, warnings=warnings,
                       source_origin=origin, source_bytes=len(raw.encode("utf-8")))


def _fallback_mode(text: str, items: list, categories: list, warnings: list) -> str:
    """Handle loosely formatted input: one capability per sentence or bullet."""
    warnings.append("Structured markers not found; used sentence/bullet fallback.")
    chunks: list[str] = []
    for line in text.split("\n"):
        line = line.strip(" -*\u2022\t")
        if not line:
            continue
        if re.match(r"^\d{1,3}[).]\s+", line):
            chunks.append(re.sub(r"^\d{1,3}[).]\s+", "", line))
        elif len(line.split()) >= 4:
            chunks.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
    seen = set()
    for chunk in chunks:
        name = _clean_name(chunk)
        words = name.split()
        if len(words) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        title = " ".join(w.capitalize() if w.islower() else w for w in words[:4])
        items.append(RawItem(name=title, description=chunk))
    return "fallback"


# --------------------------------------------------------------------------
# Unification
# --------------------------------------------------------------------------

KEY_STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "to", "with", "on", "in", "by",
                 "tool", "skill", "builder", "manager", "checker", "helper", "assistant",
                 "based", "aware", "driven"}


def slugify(value: str, max_len: int = 60) -> str:
    value = value.replace("&", " and ")
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    value = re.sub(r"-{2,}", "-", value)
    return value[:max_len].strip("-") or "skill-tool"


def keywords(text: str) -> set:
    words = re.findall(r"[a-z0-9]{3,}", text.lower())
    return {w for w in words if w not in KEY_STOPWORDS}


def _load_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ForgeError("E_ASSET_MISSING", "%s not found: %s" % (label, path.name),
                         "Restore the asset file next to the tools-* files.") from exc
    except json.JSONDecodeError as exc:
        raise ForgeError("E_ASSET_INVALID", "%s is not valid JSON: %s" % (label, exc),
                         "Fix the JSON syntax in %s." % path.name) from exc


def load_policy(path: Path | None, here: Path) -> dict:
    default_path = here / "asset-default-policy.json"
    policy = _load_json(default_path, "Default policy")
    if path is not None:
        override = _load_json(Path(path), "Policy override")
        if not isinstance(override, dict):
            raise ForgeError("E_POLICY_INVALID", "Policy override must be a JSON object.",
                             "See knowledge-readme.md for the expected shape.")
        for key in ("defaults", "risk_levels"):
            if key in override and isinstance(override[key], dict):
                policy[key].update(override[key])
        for key in ("rules", "profiles"):
            if key in override:
                if not isinstance(override[key], list):
                    raise ForgeError("E_POLICY_INVALID", "Policy key '%s' must be a list." % key)
                policy[key] = override[key]
    return policy


def load_catalog_dataset(path: Path | None, here: Path) -> dict | None:
    target = Path(path) if path else here / "asset-aphelion-catalog.json"
    if not target.exists():
        return None
    data = _load_json(target, "Catalog dataset")
    if not isinstance(data, dict) or "categories" not in data:
        raise ForgeError("E_ASSET_INVALID", "Catalog dataset is missing 'categories'.",
                         "Regenerate or restore asset-aphelion-catalog.json.")
    return data


def evaluate_risk(text_parts, policy: dict) -> tuple[str, list, list]:
    """Return (risk_level, triggers, explicit_approval_reasons)."""
    blob = " ".join(p for p in text_parts if p).lower()
    triggers = []
    for rule in policy.get("rules", []):
        pattern = rule.get("patterns")
        if isinstance(pattern, list):
            pattern = "|".join(pattern)
        try:
            if pattern and re.search(pattern, blob, re.IGNORECASE):
                triggers.append({"id": rule.get("id", "rule"), "label": rule.get("label", ""),
                                 "risk": rule.get("risk", "elevated")})
        except re.error as exc:
            raise ForgeError("E_POLICY_REGEX", "Invalid regex in policy rule %r: %s"
                             % (rule.get("id"), exc), "Fix the pattern in asset-default-policy.json.") from exc
    levels = policy.get("risk_levels", {})
    rank = {name: meta.get("rank", 0) for name, meta in levels.items()}

    def bump(current: str, new: str) -> str:
        return new if rank.get(new, 0) > rank.get(current, 0) else current

    explicit = set()
    risk = "low"
    if triggers:
        for t in triggers:
            risk = bump(risk, t["risk"])
    needs = policy.get("defaults", {}).get("require_approval_for_risk", ["elevated", "high"])
    approvals = sorted({t["label"] for t in triggers if t["risk"] in needs})
    for t in triggers:
        if t["risk"] in needs:
            explicit.add(t["id"])
    return risk, triggers, approvals


def match_profiles(items: list, policy: dict, dataset: dict | None, category_name: str | None) -> list:
    """Choose operating profiles for a capability set.

    An explicit catalog category is authoritative: when the dataset has a match,
    that profile is used alone. Otherwise keywords are scored with two guards
    against generic over-matching: a profile must match at least
    `profile_min_keywords` distinct keywords, and hits inside capability names
    are weighted higher than hits in prose.
    """
    defaults = policy.get("defaults", {})
    min_keywords = int(defaults.get("profile_min_keywords", 2))
    max_profiles = int(defaults.get("max_profiles", 2))

    if dataset and category_name:
        for cat in dataset.get("categories", []):
            if cat.get("name", "").lower() == category_name.lower():
                return [(10 ** 6, cat.get("slug", slugify(category_name)), {
                    "domain": cat.get("domain", "general"),
                    "artifacts": cat.get("artifacts", []),
                    "checks": cat.get("checks", []),
                    "risk": cat.get("risk", "standard"),
                })]

    names_blob = " ".join((i.name or "") for i in items).lower()
    prose_blob = " ".join((i.description or "") for i in items).lower()

    scored = []
    for slug, prof in policy.get("profiles", {}).items():
        distinct = 0
        score = 0.0
        for keyword in prof.get("keywords", []):
            key = keyword.lower()
            in_name = names_blob.count(key)
            in_prose = prose_blob.count(key)
            if not (in_name or in_prose):
                continue
            distinct += 1
            weight = 1.0 + min(len(key), 14) / 10.0
            score += weight * (min(in_name, 2) * 3 + min(in_prose, 3) * 0.5)
        if distinct:
            scored.append({"slug": slug, "profile": prof, "distinct": distinct, "score": score})

    scored.sort(key=lambda row: (-row["distinct"], -row["score"], row["slug"]))
    chosen = [row for row in scored if row["distinct"] >= min_keywords][:max_profiles]
    if not chosen:
        best = [row for row in scored if row["distinct"] >= 1][:1]
        chosen = best
    if not chosen:
        chosen = [{"slug": "general", "distinct": 0, "score": 0.0, "profile": {
            "domain": "general",
            "artifacts": ["output.md"],
            "checks": ["inputs validated", "outputs inspected", "recovery documented"],
            "risk": "standard"}}]
    return [(row["score"], row["slug"], row["profile"]) for row in chosen]


@dataclass
class Spec:
    name: str
    title: str
    category: str | None
    mode: str
    domains: list
    risk: str
    approval_required: bool
    approval_reasons: list
    risk_triggers: list
    capabilities: list
    artifacts: list
    checks: list
    generator: str
    inputs: list
    outputs: list
    warnings: list
    source: dict
    validation: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "category": self.category,
            "mode": self.mode,
            "domains": self.domains,
            "risk": self.risk,
            "approval_required": self.approval_required,
            "approval_reasons": self.approval_reasons,
            "risk_triggers": self.risk_triggers,
            "artifacts": self.artifacts,
            "checks": self.checks,
            "capabilities": self.capabilities,
            "generator": self.generator,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "warnings": self.warnings,
            "source": self.source,
            "validation": self.validation,
        }


def build_spec(parsed: ParseResult, policy: dict, dataset: dict | None,
               generator: str = "python", name_override: str | None = None,
               title_override: str | None = None) -> Spec:
    if name_override is not None:
        # Validate the operator's name as given: do not silently "repair" it.
        problems = validate_name(name_override)
        if problems:
            raise ForgeError("E_BAD_NAME", "; ".join(problems),
                             "Pass --name as a lowercase hyphenated slug, e.g. my-skill-tool.")
    if generator not in GENERATOR_EXTENSIONS:
        raise ForgeError("E_GENERATOR", "Unknown generator %r." % generator,
                         "Choose one of: %s." % ", ".join(sorted(GENERATOR_EXTENSIONS)))
    items = parsed.items
    max_items = int(policy.get("defaults", {}).get("max_items", 400))
    if len(items) > max_items:
        raise ForgeError("E_TOO_MANY_ITEMS",
                         "%d capabilities exceed the max_items limit of %d." % (len(items), max_items),
                         "Split the source, or raise max_items in the policy file.")

    # One category names the umbrella; several categories mean the caller must
    # name the umbrella, so fall back to a neutral, still-valid name.
    multi = len(parsed.categories) > 1
    category_name = parsed.categories[0].name if parsed.categories else None
    if multi and not (name_override or title_override):
        title = "Unified Capability Tool"
        name = "unified-capability-tool"
    else:
        title = title_override or category_name or "Unified Capability Tool"
        name = slugify(name_override or category_name or title)
    if not SLUG_RE.match(name):
        raise ForgeError("E_BAD_NAME", "Skill Tool name %r is not a valid slug." % name,
                         "Use lowercase letters, digits and single hyphens.")

    profiles = match_profiles(items, policy, dataset, None if multi else category_name)
    domains = [row[1] for row in profiles]
    artifacts: list[str] = []
    checks: list[str] = []
    risk_from_profile = "low"
    rank = {k: v.get("rank", 0) for k, v in policy.get("risk_levels", {}).items()}
    for _, _, prof in profiles:
        for art in prof.get("artifacts", []):
            if art not in artifacts:
                artifacts.append(art)
        for chk in prof.get("checks", []):
            if chk not in checks:
                checks.append(chk)
        prof_risk = prof.get("risk", "standard")
        if rank.get(prof_risk, 0) > rank.get(risk_from_profile, 0):
            risk_from_profile = prof_risk

    capabilities = [{
        "id": slugify(i.name),
        "name": i.name,
        "summary": (i.description or "").strip(),
        "category": i.category,
        "risk": _item_risk(i, policy)[0],
    } for i in items]
    seen = {}
    for cap in capabilities:
        if cap["id"] in seen:
            seen[cap["id"]] += 1
            cap["id"] = "%s-%d" % (cap["id"], seen[cap["id"]])
        else:
            seen[cap["id"]] = 1

    risk, triggers, approvals = evaluate_risk(
        [i.name + ". " + (i.description or "") for i in items] + [category_name or ""], policy)
    if rank.get(risk_from_profile, 0) > rank.get(risk, 0):
        risk = risk_from_profile
    approval_required = risk in policy.get("defaults", {}).get("require_approval_for_risk", ["elevated", "high"])

    warnings = list(parsed.warnings)
    if multi:
        warnings.append(
            "%d categories were combined into one Skill Tool; pass --name to give the "
            "umbrella a descriptive slug (used: %s)." % (len(parsed.categories), name))
    if approvals:
        warnings.append("Approval triggers present: %s" % "; ".join(approvals))
    if generator == "none":
        warnings.append("Generator 'none' emits specification files only; the folder is not runnable.")

    return Spec(
        name=name,
        title=title,
        category=None if multi else category_name,
        mode=parsed.mode,
        domains=domains,
        risk=risk,
        approval_required=approval_required,
        approval_reasons=approvals,
        risk_triggers=triggers,
        capabilities=capabilities,
        artifacts=artifacts or ["output.md"],
        checks=checks or ["inputs validated", "outputs inspected", "recovery documented"],
        generator=generator,
        inputs=["an input document or task brief", "a policy file (optional)", "output directory (optional)"],
        outputs=artifacts or ["output.md"],
        warnings=warnings,
        source={
            "origin": Path(parsed.source_origin).name if parsed.source_origin else "<memory>",
            "bytes": parsed.source_bytes,
            "items": len(items),
            "categories": parsed.category_names(),
            "mode": parsed.mode,
        },
    )


def _item_risk(item: RawItem, policy: dict) -> tuple[str, list]:
    risk, triggers, approvals = evaluate_risk([item.name, item.description or ""], policy)
    return risk, triggers


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

def validate_name(name: str) -> list:
    problems = []
    if not SLUG_RE.match(name):
        problems.append("Skill Tool folder name %r must match ^[a-z0-9]+(?:-[a-z0-9]+)*$" % name)
    if name.lower() in FORBIDDEN_NAMES:
        problems.append("Name %r is reserved." % name)
    if len(name) > 64:
        problems.append("Name is longer than 64 characters.")
    return problems


def validate_resource_filename(filename: str) -> list:
    problems = []
    if not filename.startswith(RESOURCE_PREFIXES):
        problems.append("%s does not start with tools-, knowledge- or asset-" % filename)
    if filename.lower() in FORBIDDEN_NAMES:
        problems.append("%s is a forbidden file name (no SKILL.md inside a Skill Tool)." % filename)
    if " " in filename or "_" in filename:
        problems.append("%s contains a space or underscore." % filename)
    if filename != filename.lower():
        problems.append("%s is not lowercase." % filename)
    ext = Path(filename).suffix.lower()
    if ext and ext not in RESOURCE_EXTENSIONS:
        problems.append("%s has an unexpected extension %s." % (filename, ext))
    if not re.fullmatch(r"[a-z0-9.\-]+", filename):
        problems.append("%s contains characters outside lowercase ASCII, digits, dots and hyphens." % filename)
    return problems


def validate_folder(folder: Path) -> dict:
    """Verify structure rules that the future combined skill package depends on."""
    errors: list[str] = []
    warnings: list[str] = []
    files: list[str] = []
    if not folder.exists() or not folder.is_dir():
        return {"ok": False, "errors": ["Folder not found: %s" % folder.name],
                "warnings": [], "files": [], "count": 0}
    if not SLUG_RE.match(folder.name):
        errors.append("Folder name %r is not a lowercase hyphenated slug." % folder.name)
    IGNORED = {"__pycache__", ".git", ".arena", ".venv", "node_modules", ".pytest_cache"}
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            if entry.name in IGNORED:
                warnings.append("Ignored local cache directory: %s/ (excluded from delivery)"
                                % entry.name)
                continue
            errors.append("Nested directory not allowed inside a delivered Skill Tool: %s/ "
                          "(write run outputs outside the folder, e.g. --out ../out)"
                          % entry.name)
            continue
        files.append(entry.name)
        errors.extend(validate_resource_filename(entry.name))
        if entry.stat().st_size == 0 and entry.suffix != ".json":
            warnings.append("%s is empty." % entry.name)
    knowledge = [f for f in files if f.startswith("knowledge-")]
    if knowledge != ["knowledge-readme.md"]:
        errors.append("Expected exactly one knowledge-readme.md, found: %s"
                      % (", ".join(knowledge) if knowledge else "none"))
    tools = [f for f in files if f.startswith("tools-")]
    if not tools:
        errors.append("No tools-* file found.")
    has_runnable = any(Path(f).suffix in {".py", ".mjs", ".js", ".sh"} for f in tools)
    if not has_runnable:
        warnings.append("No runnable tools- entry point (only data or config tools-* files).")
    if "skill.md" in (f.lower() for f in files):
        errors.append("SKILL.md must not be present inside the Skill Tool folder.")
    if len(files) > 500:
        errors.append("File count %d exceeds the 500 file package limit." % len(files))
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "files": files, "count": len(files),
            "knowledge": knowledge, "tools": tools,
            "assets": [f for f in files if f.startswith("asset-")]}


def load_asset_json(here: Path, filename: str) -> dict:
    return _load_json(here / filename, filename)


def emit(result: dict, fmt: str = "json", stream=None) -> None:
    """Structured output. json is the default so callers can machine-read it."""
    stream = stream or sys.stdout
    if fmt == "json":
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    elif fmt == "text":
        for key, value in result.items():
            if isinstance(value, (list, dict)):
                stream.write("%s:\n" % key)
                if isinstance(value, list):
                    for row in value:
                        stream.write("  - %s\n" % (json.dumps(row, ensure_ascii=False)
                                                   if isinstance(row, (dict, list)) else row))
                else:
                    for k2, v2 in value.items():
                        stream.write("  %s: %s\n" % (k2, v2))
            else:
                stream.write("%s: %s\n" % (key, value))
    else:
        raise ForgeError("E_FORMAT", "Unknown output format %r." % fmt, "Use json or text.")


def fail(error: ForgeError, fmt: str = "json", exit_code: int = 2) -> int:
    payload = {"ok": False, "error": error.to_dict()}
    emit(payload, fmt)
    return exit_code


def module_name_for(path: Path) -> str:
    return "forge_" + re.sub(r"[^a-z0-9]", "_", path.stem.lower())


def import_sibling(here: Path, filename: str):
    """Import a sibling tools-* file by path, independent of sys.path."""
    target = here / filename
    if not target.exists():
        raise ForgeError("E_MODULE_MISSING", "Required module missing: %s" % filename,
                         "Keep all tools-* files in the same folder.")
    name = module_name_for(target)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.fspath(target))
    if spec is None or spec.loader is None:
        raise ForgeError("E_MODULE_MISSING", "Could not load module %s." % filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
