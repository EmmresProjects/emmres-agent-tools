"""Shared runtime for the Ultimate Text Skill Tool.

Responsibilities
----------------
* deterministic error model (SkillError / ApprovalRequired / TimeoutExceeded)
* asset loading from the Skill Tool folder (case-sensitive, relative lookups)
* bounded, validated text input/output (size, encoding, atomic writes)
* per-capability timeouts, retries for idempotent read-only work, cleanup
* approval gate for destructive / irreversible / publishing actions
* one structured result envelope shared by every capability

This module is loaded by ``tools-loader.py``; it is never executed directly.
Python 3.9+ standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import tempfile
import threading
import time
import unicodedata

VERSION = "1.0.0"
TOOL_NAME = "ultimate-text"

# --------------------------------------------------------------------------
# Error model
# --------------------------------------------------------------------------


class SkillError(Exception):
    """A validated, user-facing failure. Carries a stable machine code."""

    code = "SKILL_ERROR"
    exit_code = 4

    def __init__(self, message, hint="", details=None, code=None, exit_code=None):
        super().__init__(message)
        self.message = str(message)
        self.hint = hint or ""
        self.details = details if details is not None else {}
        if code:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code

    def to_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }


class InvalidInput(SkillError):
    code = "INVALID_INPUT"
    exit_code = 2


class InvalidParam(SkillError):
    code = "INVALID_PARAM"
    exit_code = 2


class Unsupported(SkillError):
    code = "UNSUPPORTED"
    exit_code = 2


class NotFound(SkillError):
    code = "NOT_FOUND"
    exit_code = 5


class ApprovalRequired(SkillError):
    code = "APPROVAL_REQUIRED"
    exit_code = 3


class TimeoutExceeded(SkillError):
    code = "TIMEOUT"
    exit_code = 6


class LimitExceeded(SkillError):
    code = "LIMIT_EXCEEDED"
    exit_code = 7


# --------------------------------------------------------------------------
# Limits / configuration
# --------------------------------------------------------------------------

HARD_LIMITS = {
    "max_input_bytes": 4 * 1024 * 1024,
    "max_output_bytes": 8 * 1024 * 1024,
    "max_input_lines": 200000,
    "max_regex_pattern": 2000,
    "max_batch_files": 500,
    "default_timeout_s": 20,
    "max_timeout_s": 300,
    "max_variants": 50,
}

DEFAULT_CONFIG = {
    "workspace_root": ".",
    "default_encoding": "utf-8",
    "encoding_errors": "strict",
    "default_output_format": "text",
    "style_profile": "neutral-us",
    "locale": "en-US",
    "default_timeout_s": 20,
    "retries": 0,
    "allow_overwrite": False,
    "approval_required_for_write": False,
    "max_input_bytes": HARD_LIMITS["max_input_bytes"],
    "paths": {"input_dir": ".", "output_dir": "."},
    "max_pipeline_depth": 4,
    "slow_capability_ms": 2000,
    "notes": "Runtime defaults. Override with --config path/to/config.json.",
}


def clamp_int(value, low, high, name="value"):
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise InvalidParam("%s must be an integer, got %r" % (name, value))
    if ivalue < low or ivalue > high:
        raise InvalidParam(
            "%s must be between %s and %s, got %s" % (name, low, high, ivalue),
            hint="Use --help to see the accepted range.",
        )
    return ivalue


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def text_stats(text):
    return {
        "chars": len(text),
        "bytes_utf8": len(text.encode("utf-8", "surrogatepass")),
        "lines": text.count("\n") + 1 if text else 0,
        "words": len(re.findall(r"[^\s]+", text)),
        "sha256": sha256_text(text),
    }


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------


class Assets:
    """Read-only accessor for ``asset-*.json`` files beside the tools files."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self._cache = {}

    def path(self, name):
        p = os.path.join(self.root, name)
        if not os.path.isfile(p):
            raise NotFound(
                "asset file %r is missing from the Skill Tool folder" % name,
                hint="Every reference is relative to the future skill root.",
                details={"expected": "%s/%s" % (TOOL_NAME, name)},
            )
        return p

    def json(self, name):
        if name in self._cache:
            return self._cache[name]
        try:
            with open(self.path(name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise SkillError(
                "asset file %r is not valid JSON: %s" % (name, exc),
                code="ASSET_CORRUPT",
            )
        self._cache[name] = data
        return data

    def names(self):
        try:
            return sorted(n for n in os.listdir(self.root) if n.startswith("asset-"))
        except OSError:
            return []


# --------------------------------------------------------------------------
# Paths and I/O
# --------------------------------------------------------------------------


def resolve_path(path, base, must_exist=False, kind="file"):
    """Resolve a user path against ``base``. Rejects nothing silently."""
    if not isinstance(path, str) or not path.strip():
        raise InvalidInput("empty path", hint="Pass a non-empty relative path.")
    expanded = os.path.abspath(os.path.join(base, os.path.expanduser(path)))
    if must_exist:
        ok = os.path.isfile(expanded) if kind == "file" else os.path.isdir(expanded)
        if not ok:
            raise NotFound(
                "%s not found: %s" % (kind, path),
                hint="Paths are relative to --cwd (default: current directory).",
                details={"resolved": expanded},
            )
    return expanded


def read_text_file(path, encoding="utf-8", errors="strict", max_bytes=None):
    max_bytes = max_bytes or HARD_LIMITS["max_input_bytes"]
    if not os.path.isfile(path):
        raise NotFound("input file not found", details={"resolved": path})
    size = os.path.getsize(path)
    if size > max_bytes:
        raise LimitExceeded(
            "input file is %d bytes, limit is %d bytes" % (size, max_bytes),
            hint="Split the file or raise max_input_bytes in the config.",
            details={"bytes": size, "limit": max_bytes},
        )
    raw = open(path, "rb").read()
    if b"\x00" in raw and encoding == "utf-8":
        raise InvalidInput(
            "input contains NUL bytes and is not plain UTF-8 text",
            hint="This runtime processes text only. Detected NULs at byte offset %d."
            % raw.index(b"\x00"),
        )
    try:
        return raw.decode(encoding, errors)
    except UnicodeDecodeError as exc:
        raise InvalidInput(
            "input is not valid %s: %s" % (encoding, exc.reason),
            hint="Pass --encoding to select another encoding, or run the "
            "integrity.encoding-forensics capability first.",
            details={"byte_offset": exc.start},
        )


def canonical_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def enforce_limits(text, cfg, label="output"):
    limit = int(cfg.get("max_output_bytes", HARD_LIMITS["max_output_bytes"]))
    n = len(text.encode("utf-8", "surrogatepass"))
    if n > limit:
        raise LimitExceeded(
            "%s is %d bytes, limit is %d bytes" % (label, n, limit),
            hint="Raise max_output_bytes in the config or request a smaller result.",
        )
    return text


def write_text_atomic(path, text, allow_overwrite=False, encoding="utf-8"):
    """Write via a temp file in the same directory, then atomically replace."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(directory):
        raise InvalidInput(
            "output directory does not exist: %s" % directory,
            hint="Create the directory first; the runtime does not create trees.",
        )
    if os.path.exists(path) and not allow_overwrite:
        raise ApprovalRequired(
            "refusing to overwrite existing file: %s" % os.path.basename(path),
            hint="Pass --overwrite (and --approve when the capability requires it).",
            details={"path": path},
        )
    fd, tmp = tempfile.mkstemp(prefix=".ult-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# --------------------------------------------------------------------------
# Timeouts
# --------------------------------------------------------------------------


class _TimeoutGuard:
    """POSIX interval timer based timeout for the calling thread."""

    def __init__(self, seconds):
        self.seconds = max(1, int(seconds))
        self.enabled = hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
        self._prev = None

    def __enter__(self):
        if self.enabled:
            def _fire(signum, frame):
                raise TimeoutExceeded(
                    "capability exceeded its %ds time budget" % self.seconds,
                    hint="Reduce input size or raise --timeout (max %ds)."
                    % HARD_LIMITS["max_timeout_s"],
                )

            self._prev = signal.signal(signal.SIGALRM, _fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if self._prev is not None:
                signal.signal(signal.SIGALRM, self._prev)
        return False


def run_timed(fn, seconds, capability_id="capability", retries=0):
    """Run ``fn`` under a time budget. Only idempotent work may set retries."""
    attempts = max(0, int(retries)) + 1
    last = None
    for attempt in range(attempts):
        try:
            with _TimeoutGuard(seconds):
                return fn()
        except TimeoutExceeded:
            raise
        except SkillError:
            raise
        except (OSError, RuntimeError) as exc:  # transient, retryable
            last = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(0.05 * (attempt + 1))
    raise SkillError(
        "%s failed after %d attempt(s): %s" % (capability_id, attempts, last),
        code="RUNTIME_ERROR",
        hint="Retry with a smaller input; see knowledge-readme.md troubleshooting.",
    )


# --------------------------------------------------------------------------
# Approval gate
# --------------------------------------------------------------------------


def gate(ctx, action, details=None):
    """Require an explicit --approve flag for destructive / publishing work."""
    if getattr(ctx, "approve", False):
        return True
    raise ApprovalRequired(
        "capability %r needs explicit approval for: %s" % (ctx.capability_id, action),
        hint="Re-run with --approve after reviewing the preview in --dry-run mode.",
        details=details or {},
    )


# --------------------------------------------------------------------------
# Small shared helpers used by many capabilities
# --------------------------------------------------------------------------

WORD_RE = re.compile(r"[A-Za-z0-9']+")


def sentence_split(text):
    """Language-agnostic-ish sentence splitter that keeps terminators."""
    text = canonical_newlines(text)
    parts, buf = [], []
    i = 0
    closers = "\"')]\u201d\u2019"
    abbrev = {
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "no", "vs", "etc",
        "e.g", "i.e", "fig", "al", "inc", "ltd", "co", "cf", "approx", "dept",
        "u.s", "u.k", "ph.d", "a.m", "p.m",
    }
    while i < len(text):
        ch = text[i]
        buf.append(ch)
        if ch in ".!?":
            j = i + 1
            while j < len(text) and text[j] in closers:
                buf.append(text[j])
                j += 1
            spaced = j >= len(text) or text[j] in " \t\n"
            head = "".join(buf).strip()
            last_word = (re.findall(r"([A-Za-z.]+)\.$", head) or [""])[0].lower()
            if ch == "." and (last_word in abbrev or (len(last_word) == 1 and last_word.isalpha())):
                # keep the following space inside the current sentence buffer
                i += 1
                continue
            if spaced:
                k = j
                while k < len(text) and text[k] in " \t\n":
                    k += 1
                if k >= len(text) or text[k].isupper() or text[k].isdigit() or text[k] in "\"'(\u201c#*_-":
                    parts.append("".join(buf).strip())
                    buf = []
                    i = k
                    continue
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def word_tokens(text):
    return WORD_RE.findall(text)


def lower_words(text):
    return [w.lower() for w in WORD_RE.findall(text)]


def strip_accents(text):
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def display_width(text):
    """Monospace display width (East Asian wide/fullwidth count as 2)."""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            total += 2
        else:
            total += 1
    return total


def truncate(text, limit, marker="\u2026"):
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(marker):
        return text[:limit]
    return text[: limit - len(marker)] + marker


def fold_ws(text):
    return re.sub(r"[ \t\u00a0]+", " ", text)


def dedupe_preserve(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def table(rows, headers=None, align=None):
    """Render a plain-text table with display-width aware padding."""
    rows = [[("" if c is None else str(c)) for c in row] for row in rows]
    if headers:
        rows = [[str(h) for h in headers]] + rows
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for row in rows:
        for idx in range(ncols):
            cell = row[idx] if idx < len(row) else ""
            for line in cell.split("\n"):
                widths[idx] = max(widths[idx], display_width(line))
    align = align or ["l"] * ncols

    def fmt(row):
        cells = []
        for idx in range(ncols):
            cell = row[idx] if idx < len(row) else ""
            pad = widths[idx] - display_width(cell)
            if align[idx % len(align)] == "r":
                cells.append(" " * pad + cell)
            elif align[idx % len(align)] == "c":
                left = pad // 2
                cells.append(" " * left + cell + " " * (pad - left))
            else:
                cells.append(cell + " " * pad)
        return "| " + " | ".join(cells) + " |"

    out = [fmt(rows[0])]
    if headers:
        out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows[1:]:
        out.append(fmt(row))
    return "\n".join(out)


def bullets(items, marker="- ", indent="  "):
    lines = []
    for item in items:
        text = str(item).replace("\n", "\n" + indent)
        lines.append(marker + text)
    return "\n".join(lines)


def seeded_rng(seed):
    """Small deterministic PRNG so repeat runs are byte-identical."""
    state = (hashlib.sha256(str(seed).encode("utf-8")).hexdigest()[:16],)

    class Rng:
        def __init__(self):
            self.s = int(state[0], 16) or 1

        def next_int(self, n):
            self.s = (self.s * 6364136223846793005 + 1442695040888963407) % (2 ** 64)
            return (self.s >> 33) % max(1, n)

        def choice(self, seq):
            return seq[self.next_int(len(seq))]

        def shuffle(self, seq):
            items = list(seq)
            for i in range(len(items) - 1, 0, -1):
                j = self.next_int(i + 1)
                items[i], items[j] = items[j], items[i]
            return items

    return Rng()


def json_dumps(obj, indent=2):
    return json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=False)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Configuration and execution context
# --------------------------------------------------------------------------


def load_config(path=None, assets=None, overrides=None):
    """Merge defaults <- asset-config-defaults.json <- user JSON <- overrides."""
    cfg = dict(DEFAULT_CONFIG)
    if assets is not None:
        try:
            cfg.update({k: v for k, v in assets.json("asset-config-defaults.json").items()
                        if not k.startswith("_")})
        except NotFound:
            pass
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user = json.load(fh)
        except FileNotFoundError:
            raise NotFound("config file not found: %s" % path)
        except json.JSONDecodeError as exc:
            raise InvalidInput("config file is not valid JSON: %s" % exc,
                               details={"path": path})
        if not isinstance(user, dict):
            raise InvalidInput("config file must contain a JSON object")
        unknown = [k for k in user if k not in cfg]
        if unknown:
            raise InvalidParam(
                "unknown config keys: %s" % ", ".join(sorted(unknown)),
                hint="See 'config' command output for the accepted keys.",
                details={"unknown": unknown, "accepted": sorted(cfg)},
            )
        cfg.update(user)
    for key, value in (overrides or {}).items():
        cfg[key] = value
    return cfg


class Context:
    """Everything a capability handler may read or write."""

    __slots__ = ("core", "assets", "catalog", "capability", "capability_id", "text",
                 "text2", "text3", "texts", "params", "cfg", "cwd", "approve",
                 "dry_run", "verbose", "timeout", "encoding", "out_dir", "warnings",
                 "scratch")

    def __init__(self, core_mod, assets, catalog, capability, params, cfg,
                 texts, cwd, approve=False, dry_run=False, verbose=False,
                 timeout=20, encoding="utf-8"):
        self.core = core_mod
        self.assets = assets
        self.catalog = catalog
        self.capability = capability
        self.capability_id = capability["id"]
        self.params = params
        self.cfg = cfg
        self.texts = texts
        self.text = texts[0] if texts else ""
        self.text2 = texts[1] if len(texts) > 1 else ""
        self.text3 = texts[2] if len(texts) > 2 else ""
        self.cwd = cwd
        self.approve = approve
        self.dry_run = dry_run
        self.verbose = verbose
        self.timeout = timeout
        self.encoding = encoding
        self.out_dir = cwd
        self.warnings = []
        self.scratch = {}

    # convenience -------------------------------------------------------
    def param(self, name, default=None):
        value = self.params.get(name, default)
        return default if value is None else value

    def require_text(self, minimum=1, allow_empty=False):
        if allow_empty:
            return self.text
        if not self.text.strip():
            raise InvalidInput(
                "capability %r needs input text" % self.capability_id,
                hint="Pass --text '...' or --text-file path/to/file.txt "
                     "(use --text2/--text3 for the second and third input).",
            )
        if len(self.text.strip()) < minimum:
            raise InvalidInput(
                "input text is too short (%d visible characters, minimum %d)"
                % (len(self.text.strip()), minimum),
                hint="Provide at least %d characters of input." % minimum,
            )
        return self.text

    def require_second_text(self):
        if not self.text2.strip():
            raise InvalidInput(
                "capability %r needs a second text" % self.capability_id,
                hint="Pass --text2 '...' or --text2-file path.",
            )
        return self.text2

    def resolve(self, path, must_exist=False, kind="file"):
        return resolve_path(path, self.cwd, must_exist=must_exist, kind=kind)

    def warn(self, message):
        self.warnings.append(str(message))

    def gate(self, action, details=None):
        return gate(self, action, details)

    def write_output(self, path, text):
        """Write capability output, honouring dry-run, approval and overwrite rules."""
        target = self.resolve(path)
        if self.dry_run:
            return {"path": target, "written": False, "reason": "dry-run"}
        if self.capability.get("requires_approval") and not self.approve:
            raise ApprovalRequired(
                "capability %r writes files and requires approval" % self.capability_id,
                hint="Re-run with --approve once the dry-run output looks right.",
                details={"path": target},
            )
        write_text_atomic(target, text, allow_overwrite=bool(self.cfg.get("allow_overwrite")) or self.approve,
                          encoding=self.encoding)
        return {"path": target, "written": True}
