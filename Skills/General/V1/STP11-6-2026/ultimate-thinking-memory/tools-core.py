"""Shared runtime for the Ultimate Thinking Memory Skill Tool.

Standard library only. This module holds the pieces every capability family uses:
configuration and limits, validated file input, atomic writes with retry,
hash-chained ledgers with file locking, approval gating, cooperative timeouts,
cancellation, temporary-file cleanup, and the Context object handed to handlers.
"""
import atexit
import datetime as _dt
import hashlib
import json
import math
import os
import re
import signal
import tempfile
import time
from pathlib import Path

try:  # POSIX only; the tool degrades to no file locking elsewhere.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

TOOL_DIR = Path(__file__).resolve().parent
TOOL_NAME = "ultimate-thinking-memory"
TOOL_VERSION = "1.0.0"
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".py", ".html", ".yaml", ".yml", ".log", ".rst"}
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv"}
_TEMP_FILES = []


# ---------------------------------------------------------------- exceptions
class ToolError(Exception):
    exit_code = 1
    code = "error"


class UsageError(ToolError):
    exit_code = 2
    code = "usage"


class InputError(ToolError):
    exit_code = 1
    code = "invalid-input"


class ApprovalRequired(ToolError):
    exit_code = 3
    code = "approval-required"


class ToolTimeout(ToolError):
    exit_code = 4
    code = "timeout"


class Cancelled(ToolError):
    exit_code = 130
    code = "cancelled"


# ---------------------------------------------------------------- config
def load_policy():
    return json.loads((TOOL_DIR / "asset-policy.json").read_text(encoding="utf-8"))


def load_catalog():
    return json.loads((TOOL_DIR / "asset-capability-catalog.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- basics
def now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def now_iso():
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(value, label="date"):
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise InputError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or "item"


def split_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def content_words(text, stopwords=()):
    stop = set(stopwords)
    return {w for w in words(text) if len(w) >= 4 and w not in stop}


def sentences_with_lines(text):
    """Yield (line_number, sentence) pairs; sentences split on ., ?, ! and line breaks."""
    out = []
    for lineno, line in enumerate(split_lines(text), start=1):
        for part in re.split(r"(?<=[.!?])\s+", line.strip()):
            part = part.strip()
            if part:
                out.append((lineno, part))
    return out


def estimate_tokens(text, factor):
    return int(math.ceil(len(text.split()) * factor))


def excerpt(text, limit=160):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / float(len(a | b))


# ---------------------------------------------------------------- file writes
def _cleanup_temp_files():
    for path in list(_TEMP_FILES):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    del _TEMP_FILES[:]


atexit.register(_cleanup_temp_files)


def atomic_write_bytes(path, data, policy):
    """Write bytes to a temp file in the same folder, then os.replace. Retries transient OSErrors."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    retries = policy["limits"]["write_retries"]
    backoff = policy["limits"]["retry_backoff_seconds"]
    last_error = None
    for attempt in range(1, retries + 1):
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
            _TEMP_FILES.append(tmp_name)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, str(path))
            _TEMP_FILES.remove(tmp_name)
            return path
        except OSError as exc:
            last_error = exc
            if tmp_name and os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass
                if tmp_name in _TEMP_FILES:
                    _TEMP_FILES.remove(tmp_name)
            time.sleep(backoff * attempt)
    raise ToolError(f"could not write {path} after {retries} attempts: {last_error}")


def atomic_write_text(path, text, policy):
    return atomic_write_bytes(path, text.encode("utf-8"), policy)


def atomic_write_json(path, obj, policy):
    return atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", policy)


class FileLock:
    """Advisory exclusive lock on a sidecar .lock file (no-op where fcntl is unavailable)."""

    def __init__(self, target):
        self.lock_path = Path(str(target) + ".lock")
        self.fh = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.lock_path, "a+")
        if fcntl is not None:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl is not None:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        self.fh.close()
        return False


def locked_json_update(path, default, mutate, policy):
    """Read JSON (or default), apply mutate(obj) -> result, write atomically under a lock."""
    path = Path(path)
    with FileLock(path):
        if path.exists():
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise InputError(f"state file {path} is unreadable ({exc}); restore it from a checkpoint or move it aside") from exc
        else:
            obj = default
        result = mutate(obj)
        atomic_write_json(path, obj, policy)
    return result


# ---------------------------------------------------------------- ledgers
def ledger_read(path, policy):
    """Return (records, problems). Each problem is a dict describing a damaged line."""
    records, problems = [], []
    if not Path(path).exists():
        return records, problems
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            problems.append({"line": lineno, "problem": "not valid JSON (possibly a partial write after an interruption)"})
        if len(records) > policy["limits"]["max_ledger_records"]:
            raise InputError("ledger exceeds max_ledger_records; archive the ledger file first")
    return records, problems


def ledger_verify(records):
    """Recompute the hash chain. Returns (ok, first_bad_seq, detail)."""
    prev = "GENESIS"
    for rec in records:
        body = {k: rec[k] for k in ("seq", "ts", "capability", "data")}
        expected = sha256_bytes((prev + canonical(body)).encode("utf-8"))
        if rec.get("prev") != prev or rec.get("hash") != expected:
            return False, rec.get("seq"), "hash chain broken at this record"
        prev = rec["hash"]
    return True, None, "chain intact"


def ledger_append(path, capability_slug, data, policy):
    path = Path(path)
    if not isinstance(data, dict):
        raise InputError("ledger records must be JSON objects")
    line_bytes = canonical(data).encode("utf-8")
    if len(line_bytes) > policy["limits"]["max_record_bytes"]:
        raise InputError("ledger record exceeds max_record_bytes")
    with FileLock(path):
        records, problems = ledger_read(path, policy)
        if problems:
            raise InputError(f"ledger {path.name} has damaged lines {problems[:3]}; run the L:verify operation before appending")
        prev = records[-1]["hash"] if records else "GENESIS"
        seq = (records[-1]["seq"] + 1) if records else 1
        body = {"seq": seq, "ts": now_iso(), "capability": capability_slug, "data": data}
        rec = dict(body)
        rec["prev"] = prev
        rec["hash"] = sha256_bytes((prev + canonical(body)).encode("utf-8"))
        path.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        for attempt in range(1, policy["limits"]["write_retries"] + 1):
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                return rec
            except OSError as exc:
                last_error = exc
                time.sleep(policy["limits"]["retry_backoff_seconds"] * attempt)
        raise ToolError(f"could not append to ledger {path}: {last_error}")


# ---------------------------------------------------------------- input files
def read_text_file(path, policy, max_bytes=None):
    path = Path(path)
    if not path.exists():
        raise InputError(f"input file not found: {path}")
    if not path.is_file():
        raise InputError(f"input is not a regular file: {path}")
    limit = max_bytes or policy["limits"]["max_input_bytes"]
    size = path.stat().st_size
    if size > limit:
        raise InputError(f"input is {size} bytes; the limit is {limit} bytes")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"input must be UTF-8 text: {path} ({exc})") from exc


def read_json_file(path, policy):
    text = read_text_file(path, policy)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InputError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def walk_files(root, policy, extensions=None, skip_dirs=()):
    """Deterministic, bounded directory walk. Skips hidden folders, state/output folders, and caches."""
    root = Path(root)
    found = []
    limit = policy["limits"]["max_scan_files"]
    skip = set(SKIP_DIR_NAMES) | set(skip_dirs)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in skip)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = Path(dirpath) / name
            if extensions and full.suffix.lower() not in extensions:
                continue
            found.append(full)
            if len(found) >= limit:
                return found
    return found


# ---------------------------------------------------------------- timeouts
class TimeBudget:
    def __init__(self, seconds):
        self.seconds = seconds
        self.start = time.monotonic()

    def check(self):
        if time.monotonic() - self.start > self.seconds:
            raise ToolTimeout(f"operation exceeded the {self.seconds:g} second timeout")

    def elapsed_ms(self):
        return int((time.monotonic() - self.start) * 1000)


def install_signal_handlers(budget):
    def _timeout(signum, frame):
        raise ToolTimeout(f"operation exceeded the {budget.seconds:g} second timeout")

    def _cancel(signum, frame):
        raise Cancelled("operation cancelled by signal")

    handlers = {}
    if hasattr(signal, "SIGALRM"):
        handlers[signal.SIGALRM] = signal.signal(signal.SIGALRM, _timeout)
        signal.setitimer(signal.ITIMER_REAL, budget.seconds)
    handlers[signal.SIGINT] = signal.signal(signal.SIGINT, _cancel)
    if hasattr(signal, "SIGTERM"):
        handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, _cancel)
    return handlers


def restore_signal_handlers(handlers):
    if hasattr(signal, "SIGALRM"):
        signal.setitimer(signal.ITIMER_REAL, 0)
    for signum, old in handlers.items():
        try:
            signal.signal(signum, old)
        except (ValueError, TypeError):
            pass


# ---------------------------------------------------------------- context
class Context:
    """Passed to every handler. Gives validated access to options, inputs, and state."""

    def __init__(self, *, capability, handler_key, options, input_path, output_dir, state_dir,
                 approve, policy, budget):
        self.capability = capability
        self.handler_key = handler_key
        self.op = handler_key.split(":", 1)[1]
        self.options = options
        self.input_path = Path(input_path).resolve() if input_path else None
        self.output_dir = Path(output_dir)
        self.state_dir = Path(state_dir)
        self.approve = approve
        self.policy = policy
        self.budget = budget
        self.slug = capability["slug"]
        self.warnings = []
        self.outputs = []

    # options ----------------------------------------------------------
    def opt(self, key, default=None):
        return self.options.get(key, default)

    def opt_int(self, key, default, minimum=None, maximum=None):
        raw = self.options.get(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise InputError(f"option {key} must be an integer, got {raw!r}") from exc
        if minimum is not None and value < minimum:
            raise InputError(f"option {key} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise InputError(f"option {key} must be <= {maximum}")
        return value

    def opt_float(self, key, default, minimum=None, maximum=None):
        raw = self.options.get(key)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError as exc:
            raise InputError(f"option {key} must be a number, got {raw!r}") from exc
        if not math.isfinite(value):
            raise InputError(f"option {key} must be finite")
        if minimum is not None and value < minimum:
            raise InputError(f"option {key} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise InputError(f"option {key} must be <= {maximum}")
        return value

    def opt_bool(self, key, default=False):
        raw = self.options.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    def tick(self):
        self.budget.check()

    # inputs -----------------------------------------------------------
    def require_input(self):
        if self.input_path is None:
            raise InputError(f"this operation needs --input (path to a file or folder); see: describe {self.capability['id']}")
        if not self.input_path.exists():
            raise InputError(f"input path not found: {self.input_path}")
        return self.input_path

    def read_text(self, path=None):
        target = Path(path) if path else self.require_input()
        return read_text_file(target, self.policy)

    def read_json(self, path=None):
        target = Path(path) if path else self.require_input()
        return read_json_file(target, self.policy)

    def optional_text(self, key):
        """Text from a path given in an option, or None."""
        value = self.opt(key)
        if not value:
            return None
        return read_text_file(Path(value), self.policy)

    def base_dir(self, key="base"):
        value = self.opt(key)
        if value:
            return Path(value).resolve()
        if self.input_path is not None and self.input_path.is_dir():
            return self.input_path
        if self.input_path is not None:
            return self.input_path.parent
        return Path.cwd()

    # state & outputs ----------------------------------------------------
    def state_path(self, *parts):
        path = self.state_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def as_of(self):
        raw = self.opt("as_of")
        if raw:
            return parse_date(raw, "as_of")
        return now_utc().date()

    def write_artifact(self, name, content):
        if isinstance(content, str):
            path = atomic_write_text(self.output_dir / name, content, self.policy)
        else:
            path = atomic_write_bytes(self.output_dir / name, content, self.policy)
        self.outputs.append(str(path))
        return str(path)

    def require_approval(self, reason, details=None):
        """Raise ApprovalRequired unless the human approved this run with --approve."""
        if not self.approve:
            raise _ApprovalWithDetails(reason, details)


class _ApprovalWithDetails(ApprovalRequired):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.details = details or {}


def render_report(envelope):
    """Human-readable markdown report built from the run envelope."""
    lines = []
    cap = envelope.get("capability", {})
    lines.append(f"# {cap.get('name', 'Run')} - {envelope.get('op', '')}")
    lines.append("")
    lines.append(f"- Capability: {cap.get('id', '')} ({cap.get('category', '')})")
    lines.append(f"- Support level: {cap.get('support_level', '')}")
    lines.append(f"- Status: {envelope.get('status', '')}")
    lines.append(f"- Elapsed: {envelope.get('elapsed_ms', 0)} ms")
    lines.append(f"- Generated (UTC): {now_iso()}")
    lines.append("")
    result = envelope.get("result") or {}
    summary = result.get("summary")
    if summary:
        lines.append("## Summary")
        lines.append("")
        lines.append("| Measure | Value |")
        lines.append("| --- | --- |")
        for key, value in summary.items():
            lines.append(f"| {key} | {_cell(value)} |")
        lines.append("")
    items = result.get("items")
    if isinstance(items, list) and items and all(isinstance(i, dict) for i in items):
        keys = []
        for item in items:
            for k in item:
                if k not in keys:
                    keys.append(k)
        shown = items[: 200]
        lines.append(f"## Items ({len(items)})")
        lines.append("")
        lines.append("| " + " | ".join(keys) + " |")
        lines.append("| " + " | ".join("---" for _ in keys) + " |")
        for item in shown:
            lines.append("| " + " | ".join(_cell(item.get(k, "")) for k in keys) + " |")
        if len(items) > len(shown):
            lines.append(f"\n_Showing first {len(shown)} of {len(items)} items. The JSON result has all of them._")
        lines.append("")
    extra = {k: v for k, v in result.items() if k not in ("summary", "items")}
    if extra:
        lines.append("## Details")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(extra, indent=2, ensure_ascii=False, default=str)[:20000])
        lines.append("```")
        lines.append("")
    if envelope.get("warnings"):
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in envelope["warnings"])
        lines.append("")
    if envelope.get("errors"):
        lines.append("## Errors")
        lines.extend(f"- {e}" for e in envelope["errors"])
        lines.append("")
    lines.append("_Support note: this report is generated by deterministic code. Assisted and worksheet results need human or agent review as described in knowledge-readme.md._")
    return "\n".join(lines) + "\n"


def _cell(value):
    if isinstance(value, (list, tuple, dict)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")[:300]
