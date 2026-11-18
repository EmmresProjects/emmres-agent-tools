#!/usr/bin/env python3
"""tools-runtime-kit.py -- layer 1 of the offline-game-development Skill Tool.

Role in the Skill Tool
    The primitives every other layer reuses: typed errors with exit codes, the output
    envelope, a cooperative deadline and cancellation token, configuration layering,
    size-limited readers, approval-gated atomic writes, secret redaction, and the text,
    table, and CSV helpers that make markdown and CSV output deterministic.

    Nothing in this file knows anything about game development. If a helper here starts
    talking about capabilities, it belongs in layer 2 or 3.

Layer rules
    layer 1 (this file)   imports nothing from the Skill Tool
    layer 2 (engine)      imports this file
    layer 3 (generators)  imports this file and layer 2
    layer 4 (gates)       imports this file and layer 2
    CLI                  imports all of them
"""

from __future__ import annotations

import errno
import io
import json
import os
import re
import sys
import tempfile
import time

SKILL_TOOL_NAME = "offline-game-development"
TOOL_VERSION = "1.0.0"
PACKAGE = SKILL_TOOL_NAME

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_NOT_FOUND = 4
EXIT_GATE = 5
EXIT_APPROVAL = 6
EXIT_CANCELLED = 7
EXIT_INTERNAL = 8
EXIT_LIMIT = 9
EXIT_UNAVAILABLE = 10

TEMP_PREFIX = ".tmp-"
DEFAULT_MAX_INPUT_MB = 64
MIN_PYTHON = (3, 9)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """Any failure the tool reports instead of raising a traceback.

    Carries a stable machine code, an exit code for the shell, a one-line message, a
    remediation hint, the offending value in 'where', and any structured 'details'.
    """

    code = "E-INTERNAL"
    exit_code = EXIT_INTERNAL

    def __init__(self, message: str, hint: str = "", where: str = "", **details):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.where = where
        self.details = {k: v for k, v in details.items() if v not in (None, [], {}, "")}

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.where:
            out["where"] = self.where
        if self.details:
            out["details"] = self.details
        return out

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.code}: {self.message}"


class UsageError(ToolError):
    code, exit_code = "E-USAGE", EXIT_USAGE


class InputError(ToolError):
    code, exit_code = "E-INPUT", EXIT_INPUT


class EnvironmentFailure(ToolError):
    """A packaged file or interpreter is missing or unusable: the install, not the input."""
    code, exit_code = "E-ENVIRONMENT", EXIT_INPUT


class NotFoundError(ToolError):
    code, exit_code = "E-NOT-FOUND", EXIT_NOT_FOUND


class AmbiguousError(ToolError):
    code, exit_code = "E-AMBIGUOUS", EXIT_NOT_FOUND


class GateFailure(ToolError):
    code, exit_code = "E-GATE", EXIT_GATE


class ApprovalRequired(ToolError):
    code, exit_code = "E-APPROVAL", EXIT_APPROVAL

    def __init__(self, message, action="", required_token="", **kw):
        self.required_token = required_token
        kw.setdefault("hint", f"re-run with --approve {required_token}" if required_token
                      else "this action needs an explicit approval token")
        if required_token:
            kw["required_token"] = required_token
        super().__init__(message, **kw)
        self.action = action


class InternalError(ToolError):
    """A defect in the tool itself, never a bad input."""
    code, exit_code = "E-INTERNAL", EXIT_INTERNAL


class LimitExceeded(ToolError):
    code, exit_code = "E-LIMIT", EXIT_LIMIT


class TimeoutExceeded(ToolError):
    code, exit_code = "E-TIMEOUT", EXIT_LIMIT


class Cancelled(ToolError):
    code, exit_code = "E-CANCELLED", EXIT_CANCELLED


class IntegrationUnavailable(ToolError):
    code, exit_code = "E-UNAVAILABLE", EXIT_UNAVAILABLE


# ---------------------------------------------------------------------------
# module loading (the filenames are hyphenated, so import * cannot work)
# ---------------------------------------------------------------------------
def load_sibling(filename: str, here: str | None = None):
    """Import a sibling tools-*.py of this package, cached under an importable alias."""
    here = here or os.path.dirname(os.path.abspath(__file__))
    stem = os.path.splitext(os.path.basename(filename))[0]
    key = "ogd_" + stem
    if key in sys.modules:
        return sys.modules[key]
    path = filename if os.path.isabs(filename) else os.path.join(here, filename)
    if not os.path.isfile(path):
        raise EnvironmentFailure(f"missing Skill Tool module: {PACKAGE}/{os.path.basename(path)}",
                                 hint=f"every file of the {PACKAGE} folder must sit beside the entry "
                                      "point; re-extract the package or run 'doctor'",
                                 where=path)
    import importlib.util

    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # keep the failure structured
        sys.modules.pop(key, None)
        raise EnvironmentFailure(f"{os.path.basename(path)} failed to load: {type(exc).__name__}: {exc}",
                                 hint="check that this file is complete and unmodified", where=path) from exc
    return module


# ---------------------------------------------------------------------------
# result envelope
# ---------------------------------------------------------------------------
RESULT_ERROR_ORDER = (ApprovalRequired, Cancelled, GateFailure, TimeoutExceeded, LimitExceeded,
                      IntegrationUnavailable, NotFoundError, AmbiguousError, EnvironmentFailure,
                      InputError, UsageError, InternalError)


def exit_code_for(errors) -> int:
    """The one exit code a list of serialized errors implies.

    Order matters when a run collected several kinds: the decision a human has to make first wins
    (an approval question, then a gate result, then the environment, then the input). Every error
    class is in RESULT_ERROR_ORDER, and 'tools-selftest.py' fails the package if one is left out,
    so a new failure kind cannot quietly report the wrong number.
    """
    codes = {item.get("code") for item in errors or ()}
    if not codes:
        return EXIT_OK
    for cls in RESULT_ERROR_ORDER:
        if cls.code in codes:
            return cls.exit_code
    return EXIT_INTERNAL


class Result:
    """Accumulates data, warnings, and notes for one command run."""

    def __init__(self, command: str, fmt: str = "json"):
        self.command = command
        self.format = fmt
        self.data = None
        self.warnings: list = []
        self.errors: list = []
        self.meta: dict = {}
        self.started = time.monotonic()

    # -- accumulation ----------------------------------------------------
    def set_data(self, data) -> "Result":
        self.data = data
        return self

    def update_data(self, mapping: dict) -> "Result":
        if not isinstance(self.data, dict):
            self.data = {}
        self.data.update(mapping)
        return self

    def warn(self, message: str, code: str = "W-NOTE", **details) -> "Result":
        item = {"code": code, "message": message}
        if details:
            item["details"] = details
        self.warnings.append(item)
        return self

    def note(self, key: str, value) -> "Result":
        self.meta[key] = value
        return self

    def error(self, err: ToolError) -> "Result":
        self.errors.append(err.to_dict())
        return self

    # -- output ----------------------------------------------------------
    @property
    def exit_code(self) -> int:
        return exit_code_for(self.errors)

    def envelope(self, include_timestamp: bool = False) -> dict:
        out = {"ok": not self.errors, "tool": SKILL_TOOL_NAME, "version": TOOL_VERSION,
               "command": self.command, "data": self.data, "warnings": self.warnings,
               "errors": self.errors, "meta": dict(self.meta, exit_code=self.exit_code)}
        if include_timestamp:
            out["meta"]["duration_ms"] = round((time.monotonic() - self.started) * 1000, 1)
            out["generated_at"] = utc_stamp()
        return out

    def to_json(self, indent: int = 1, include_timestamp: bool = False) -> str:
        return json.dumps(self.envelope(include_timestamp), ensure_ascii=False, indent=indent,
                          separators=(",", ": "), sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# cancellation and deadlines
# ---------------------------------------------------------------------------
class CancelToken:
    """Set by SIGINT/SIGTERM or by an embedding host; long loops check it."""

    def __init__(self):
        self.requested = False
        self.reason = ""
        self._previous: dict = {}

    def request(self, reason: str = "cancelled") -> None:
        self.requested = True
        self.reason = reason

    def _handle(self, signum, frame):  # pragma: no cover - signal path
        self.request(f"received signal {signum}")
        raise Cancelled("interrupted by signal", hint="partial output was discarded; no file was replaced")

    def install(self, enabled: bool = True) -> None:
        if not enabled:
            return
        import signal

        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                self._previous[name] = signal.getsignal(sig)
                signal.signal(sig, self._handle)
            except (ValueError, OSError):  # not the main thread
                self._previous.pop(name, None)

    def restore(self) -> None:
        if not self._previous:
            return
        import signal

        for name, handler in self._previous.items():
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):  # pragma: no cover
                    pass
        self._previous = {}

    def check(self, doing: str = "") -> None:
        if self.requested:
            raise Cancelled(f"cancelled while {doing}" if doing else "cancelled",
                            reason=self.reason,
                            hint="partial output was discarded and no file was replaced")


class Deadline:
    """Wall-clock bound for a run: a SIGALRM hard stop where available, plus cooperative ticks.

    'seconds' of 0 or less means unlimited. ticks also honour the cancellation token, so one
    object is the only thing a loop needs to check.
    """

    def __init__(self, seconds: float = 0.0, cancel: CancelToken | None = None):
        self.seconds = float(seconds or 0)
        self.cancel = cancel if cancel is not None else CancelToken()
        self._start = time.monotonic()
        self._armed = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    @property
    def remaining(self) -> float:
        if self.seconds <= 0:
            return float("inf")
        return max(0.0, self.seconds - self.elapsed)

    def arm(self) -> None:
        if self.seconds <= 0 or self._armed:
            return
        import signal

        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            return
        try:
            self._previous_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, max(0.001, self.seconds))
            self._armed = True
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            self._armed = False

    def _fire(self, signum, frame):  # pragma: no cover - timing dependent
        raise TimeoutExceeded(f"operation exceeded the {self.seconds:g}s limit",
                              hint="raise --timeout, narrow the selection with --limit/--category, "
                                   "or run fewer targets")

    def disarm(self) -> None:
        if not self._armed:
            return
        import signal

        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, getattr(self, "_previous_handler", signal.SIG_DFL))
        except (ValueError, OSError):  # pragma: no cover
            pass
        self._armed = False

    close = disarm

    def tick(self, doing: str = "") -> None:
        self.cancel.check(doing)
        if self.seconds > 0 and self.elapsed > self.seconds:
            raise TimeoutExceeded(f"operation exceeded the {self.seconds:g}s limit while {doing}"
                                  if doing else f"operation exceeded the {self.seconds:g}s limit",
                                  hint="raise --timeout, narrow the selection with --limit/--category, "
                                       "or run fewer targets")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
BUILTIN_CONFIG = {
    "assets_dir": "auto",
    "dataset": "asset-catalog.json",
    "archetypes": "asset-archetypes.json",
    "license_policy": "asset-license-policy.json",
    "budget_presets": "asset-budget-presets.json",
    "output_schema": "asset-output-schema.json",
    "spec_template": "asset-spec-template.md",
    "evidence_fixture": "asset-evidence-fixture.json",
    "engines": {"enabled": ["godot", "unity", "unreal", "roblox"], "default": "godot"},
    "target": {"platform": "desktop", "device_profile": "desktop-mid", "region": "worldwide"},
    "provider": {"kind": "local", "model_id": "", "endpoint": "", "allow_network": False},
    "limits": {"max_input_mb": DEFAULT_MAX_INPUT_MB, "max_targets": 400, "timeout_seconds": 60,
               "retries": 3, "max_list_rows": 500, "walk_depth": 6},
    "output": {"format": "json", "dir": "", "timestamp": False, "redact_secrets": True,
               "deterministic": True},
    "approvals": {"require_for": ["overwrite-file", "delete-file", "replace-dataset", "rights-waiver",
                                  "publish", "signing", "monetization-change", "credential-access",
                                  "device-flash"]},
    "offline": {"allow_network": False, "forbid_remote_urls_in_specs": True},
}


def deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(config_path: str = "", assets_dir: str = "") -> dict:
    """built-ins -> asset-config-default.json -> --config -> $OGD_CONFIG."""
    cfg = json.loads(json.dumps(BUILTIN_CONFIG))
    if not config_path and assets_dir:
        candidate = os.path.join(assets_dir, "asset-config-default.json")
        if os.path.isfile(candidate):
            config_path = candidate
    if config_path:
        path = os.path.abspath(os.path.expanduser(config_path))
        if not os.path.isfile(path):
            raise InputError(f"config file not found: {path}",
                             hint=f"pass a readable path to a JSON object, or drop --config to use "
                                  f"{PACKAGE}/asset-config-default.json", where=config_path)
        data = load_json(path, 8)
        if not isinstance(data, dict):
            raise InputError("a config file must contain a JSON object", where=path)
        cfg = deep_merge(cfg, {k: v for k, v in data.items() if not k.startswith("_")})
    env_blob = (os.environ.get("OGD_CONFIG") or "").strip()
    if env_blob:
        if os.path.isfile(env_blob):
            cfg = deep_merge(cfg, load_json(env_blob, 8))
        else:
            try:
                parsed = json.loads(env_blob)
            except ValueError as exc:
                raise InputError(f"OGD_CONFIG is not valid JSON: {exc}",
                                 hint="put a JSON object in the variable, or a path to a config file",
                                 where="OGD_CONFIG") from exc
            if isinstance(parsed, dict):
                cfg = deep_merge(cfg, parsed)
    return cfg


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def read_bytes(path: str, max_mb: float = DEFAULT_MAX_INPUT_MB, deadline=None) -> bytes:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise InputError(f"input file not found: {path}",
                         hint="pass a path you can read, or omit --input to use the bundled dataset",
                         where=path)
    if not os.access(path, os.R_OK):
        raise InputError(f"input file is not readable: {path}", where=path)
    size = os.path.getsize(path)
    if max_mb and size > int(float(max_mb) * 1024 * 1024):
        raise LimitExceeded(f"{path} is {size / 1048576:.1f} MB, above the {max_mb:g} MB input limit",
                             hint=f"raise --max-input-mb, or split the file", where=path)
    if deadline:
        deadline.tick("reading " + os.path.basename(path))
    with open(path, "rb") as fh:
        return fh.read()


def read_text(path: str, max_mb: float = DEFAULT_MAX_INPUT_MB, deadline=None) -> str:
    raw = read_bytes(path, max_mb, deadline)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    for encoding in ("utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(path: str, max_mb: float = 16, deadline=None):
    text = read_text(path, max_mb, deadline)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InputError(f"invalid JSON in {os.path.basename(path)}: {exc}",
                         hint="check for a trailing comma or an unquoted key", where=path) from exc


def dump_json(obj, indent: int = 1) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, separators=(",", ": "),
                      sort_keys=False) + "\n"


def resolve_asset(assets_dir: str, name: str, required: bool = True):
    """Locate a packaged asset by name; the name must look like an asset-* file."""
    safe = check_asset_name(name)
    path = os.path.join(assets_dir, safe)
    if not os.path.isfile(path):
        if required:
            raise NotFoundError(f"missing packaged asset: {PACKAGE}/{safe}",
                                hint=f"the {PACKAGE} folder must contain every file listed by 'doctor'; "
                                     "re-extract the package", where=path)
        return ""
    return path


def check_asset_name(name: str) -> str:
    """Only a bare 'asset-*' style filename may be resolved inside the package."""
    text = str(name or "").strip()
    base = os.path.basename(text)
    if not text or "/" in text or "\\" in text or ".." in text or text != base:
        raise InputError(f"not a usable packaged file name: {name!r}",
                         hint="give a bare file name inside the skill tool folder, for example "
                              "asset-catalog.json, or a path outside it", where=str(name))
    if not re.match(r"^(asset|knowledge|tools)-[A-Za-z0-9._-]+$", base):
        raise InputError(f"unexpected packaged file name: {base}",
                         hint=f"files in {PACKAGE}/ are named tools-*, knowledge-*, or asset-*",
                         where=base)
    return base


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def sanitize_out_path(path: str) -> str:
    if not path or not str(path).strip():
        raise UsageError("an output path must not be empty", hint="pass --out PATH", where="--out")
    path = str(path)
    expanded = os.path.abspath(os.path.expanduser(path))
    here = os.path.abspath(os.getcwd())
    inside = os.path.commonpath([expanded, here]) == here
    if not inside and os.path.isabs(path) and not path.startswith("/"):
        raise InputError(f"output path is outside the current tree: {path}",
                         hint="choose a path under the directory you run the tool from", where=path)
    return expanded


def _approval_action_for(path: str) -> str:
    return "overwrite-file"


def atomic_write(path: str, text: str, allow_overwrite: bool = False, approval_token: str = "",
                 backup: bool = False, deadline=None, retries: int = 3,
                 sensitive_names: dict | None = None) -> dict:
    """Write via a temp file plus os.replace. Overwriting is a destructive action.

    'sensitive_names' maps an absolute path to the approval token that protects it, so
    replacing a shipped asset needs a different, stronger token than a normal file.
    """
    target = sanitize_out_path(path)
    if os.path.isdir(target):
        raise InputError(f"the output path is a directory: {target}",
                         hint="add a file name, for example --out reports/spec.md", where=path)
    names = sensitive_names or {}
    sensitive = names.get(target)
    if sensitive is None and names:
        resolved = os.path.normpath(os.path.abspath(target))
        sensitive = names.get(resolved)
    replacing = os.path.isfile(target)
    if sensitive is not None:
        replacing = True
    required_token = sensitive or "OVERWRITE"
    given = str(approval_token or "").strip()
    if replacing and not (allow_overwrite and given.upper() == required_token):
        if given and given.upper() != required_token:
            raise ApprovalRequired(
                f"the approval given for {os.path.basename(target)} is not the one this action "
                f"requires: --approve {required_token}, not --approve {given}",
                action="replace " + os.path.basename(target), required_token=required_token,
                where=target,
                hint=f"re-run with --allow-overwrite --approve {required_token}; the token states that "
                     "this replacement is intended, so a near miss never writes the file")
        raise ApprovalRequired(
            f"refusing to replace an existing file: {target}",
            action="replace " + os.path.basename(target), required_token=required_token,
            where=target,
            hint=f"re-run with --allow-overwrite --approve {required_token} to replace it, or choose "
                 "a new --out path")
    parent = os.path.dirname(target) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise InputError(f"cannot create {parent}: {exc.strerror or exc}", where=parent)

    attempts = max(1, int(retries or 1))
    backup_path = ""
    temp = None
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if deadline:
            deadline.tick(f"writing {os.path.basename(target)}")
        try:
            fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".part", dir=parent, text=True)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            if backup and replacing:
                backup_path = _backup_path(target)
                try:
                    with open(target, "rb") as src, open(backup_path, "wb") as dst:
                        dst.write(src.read())
                except OSError as exc:
                    os.unlink(temp)
                    temp = None
                    raise InputError(f"cannot write the backup for {target}: {exc.strerror or exc}",
                                     where=target)
            os.replace(temp, target)
            temp = None
            break
        except (OSError, ToolError) as exc:
            last = exc
            if temp and os.path.exists(temp):
                try:
                    os.unlink(temp)
                except OSError:
                    pass
                temp = None
            if isinstance(exc, ToolError) and exc.code in ("E-INPUT", "E-APPROVAL"):
                raise
            if getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM, errno.ENOSPC, errno.EISDIR):
                raise InputError(f"cannot write {target}: {getattr(exc, 'strerror', None) or exc}",
                                 hint="check the directory permissions and free space, then retry",
                                 where=target) from exc
            if attempt >= attempts:
                raise InputError(f"write of {target} failed after {attempts} attempts: {exc}",
                                 hint="the filesystem may be busy; --retries bounds this loop",
                                 where=target) from exc
            time.sleep(0.05 * attempt)
    else:  # pragma: no cover - defensive
        raise InputError(f"write of {target} did not complete", where=target)
    return {"path": target, "bytes": len(text.encode("utf-8")), "created": not replacing,
            "attempts": attempts, "backup": backup_path, "kind": "artifact"}


def _backup_path(target: str) -> str:
    candidate = target + ".bak"
    n = 1
    while os.path.exists(candidate) and n < 1000:
        candidate = f"{target}.bak{n}"
        n += 1
    return candidate


def stale_temp_files(directory: str, older_than_minutes: float = 1.0, max_depth: int = 8,
                     deadline=None) -> list:
    """The .tmp-* leftovers of writes that were interrupted, oldest first."""
    root = os.path.abspath(os.path.expanduser(directory or "."))
    found: list = []
    cutoff = time.time() - max(0.0, float(older_than_minutes)) * 60
    walk_root = root
    for base, dirs, files in os.walk(walk_root):
        if deadline:
            deadline.tick("scanning for temp files")
        depth = base[len(root):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
        for name in files:
            if not name.startswith(TEMP_PREFIX):
                continue
            path = os.path.join(base, name)
            try:
                if deadline:
                    deadline.tick("stat " + name)
                if os.path.getmtime(path) <= cutoff:
                    found.append({"path": path, "bytes": os.path.getsize(path),
                                  "age_minutes": round((time.time() - os.path.getmtime(path)) / 60, 1)})
            except OSError:
                continue
    found.sort(key=lambda item: -item["age_minutes"])
    return found


# ---------------------------------------------------------------------------
# hygiene
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b")),
    ("bearer-token", re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+[0-9A-Za-z._~+/=-]{8,}")),
    # 'token' alone is a common variable name in code, so only credential-shaped keys with a
    # credential-shaped value count here; a line may also carry an 'audit: allow' marker.
    ("generic-secret", re.compile(
        r"(?i)\b(api[_ -]?key|apikey|secret[_ -]?key|client[_ -]?secret|access[_ -]?token|auth[_ -]?token|"
        r"password|passwd|passphrase|private[_ -]?key)\b\s*[:=]\s*"
        r"[\"']?(?!\*{3,}|<|\{\{|fill|\bos\.|\bgetenv\b)[A-Za-z0-9+/=_.\-]{10,}")),
    ("connection-string", re.compile(
        r"(?i)\b(?:postgres|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s\"'<>]{4,}")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z]{20,}\b")),
]


def scan_secrets(text: str) -> list:
    """Where credential-looking material appears, without returning the value itself."""
    hits = []
    for lineno, line in enumerate((text or "").split("\n"), start=1):
        if "audit: allow" in line:
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                hits.append({"line": lineno, "pattern": label,
                             "column": match.start() + 1, "length": len(match.group(0))})
    return hits


def redact_str(text: str) -> str:
    """Replace credential-looking runs with a marker, so reports are safe to commit."""
    out = text or ""
    for _label, pattern in SECRET_PATTERNS:
        out = pattern.sub(lambda m: "[redacted:" + _label_for(m, pattern) + "]", out)
    return out


def _label_for(match, pattern) -> str:
    for label, candidate in SECRET_PATTERNS:
        if candidate is pattern:
            return label
    return "secret"


def redact_hits(text: str) -> tuple:
    """Return (redacted_text, hits) in one pass."""
    return redact_str(text), scan_secrets(text)


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    raw = (text or "").strip().lower()
    raw = raw.replace("c#", "c-sharp").replace("++", "-plus-plus").replace("&", " and ")
    raw = raw.replace("'", "").replace("\u2019", "")
    return _SLUG_STRIP.sub("-", raw).strip("-")


SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to",
               "with", "via", "per", "so", "yet"}
KEEP_UPPER = {"2d", "3d", "ai", "ar", "vr", "xr", "mr", "ui", "ux", "qa", "sdk", "sdks", "api", "apis",
              "gpu", "cpu", "ios", "macos", "linux", "windows", "pc", "hud", "fps", "pbr", "uv", "uvs",
              "vr90", "godot", "unity", "unreal", "roblox", "json", "yaml", "toml", "csv", "html",
              "ci", "cd", "ot", "mr", "vr", "ar"}


def title_case(text: str) -> str:
    words = (text or "").split()
    out = []
    for i, word in enumerate(words):
        low = word.lower()
        core = low.strip(".,:;()")
        if core in KEEP_UPPER:
            out.append(word.replace(core, core.upper()))
        elif i and core in SMALL_WORDS:
            out.append(low)
        else:
            out.append(low[:1].upper() + low[1:])
    return " ".join(out)


_TAG = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>|<!--.*?-->|<!\[CDATA\[.*?\]\]>")
_BLOCK_TAG = re.compile(r"(?i)</?(p|div|li|ul|ol|tr|table|thead|tbody|tfoot|h[1-6]|br|hr|section|"
                        r"article|pre|blockquote|figure|figcaption)\b[^>]*>")
_CELL_TAG = re.compile(r"(?i)</?(td|th)\b[^>]*>")
_ANY_TAG = re.compile(r"(?s)<[^>]+>")
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " ",
             "&mdash;": "\u2014", "&ndash;": "\u2013", "&hellip;": "\u2026", "&rsquo;": "'",
             "&lsquo;": "'", "&ldquo;": '"', "&rdquo;": '"', "&middot;": "\u00b7", "&bull;": "\u2022"}


def strip_html(text: str) -> str:
    """Remove tags, attributes, comments, CSS/JS bodies, and entities; keep the words."""
    out = text or ""
    out = _TAG.sub(" ", out)
    out = _CELL_TAG.sub("\n", out)
    out = _BLOCK_TAG.sub("\n", out)
    out = _ANY_TAG.sub(" ", out)
    for entity, char in _ENTITIES.items():
        out = out.replace(entity, char)
        out = out.replace(entity.upper(), char)
    out = re.sub(r"&#[xX]?[0-9A-Fa-f]+;", " ", out)
    out = out.replace("\u00a0", " ").replace("\u200b", "")
    lines = []
    for line in out.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", re_strip_marks(line))
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def re_strip_marks(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^(?:[-*+\u2022\uf0b7][ \t]+|\d{1,3}[.)][ \t]+|[A-Za-z][.)][ \t]+)", "", text)
    return text.strip()


_MD_EMPHASIS = [
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"(?<![\w*])[*_](?!\s)([^*_\n]+?)(?<!\s)[*_](?![\w*])"), r"\1"),
]


def strip_markdown(text: str) -> str:
    """Inline emphasis, links, and images only; list and heading marks are structure, not noise."""
    out = text or ""
    for pattern, repl in _MD_EMPHASIS:
        out = pattern.sub(repl, out)
    return out


def normalize_text(text: str) -> str:
    out = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", strip_markdown((text or "").replace("\n", " "))).strip()


def wrap(text: str, width: int = 96, indent: str = "") -> str:
    import textwrap as _tw

    return _tw.fill(re.sub(r"\s+", " ", text or "").strip(), width=max(24, int(width)),
                    initial_indent=indent, subsequent_indent=indent)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={_cell(v)}" for k, v in value.items())
    return re.sub(r"\s*\n\s*", " ", str(value))


def ascii_table(columns, rows, indent: str = "") -> str:
    cols = [str(c) for c in columns]
    grid = [cols] + [[_cell(r.get(c) if isinstance(r, dict) else r[i] if i < len(r) else "")
                      for i, c in enumerate(cols)] if isinstance(r, (dict, list, tuple)) else
                     [_cell(x) for x in (r if isinstance(r, (list, tuple)) else [r])]
                     for r in rows]
    widths = [max(len(row[i]) if i < len(row) else 0 for row in grid) for i in range(len(cols))]
    out = []
    for n, row in enumerate(grid):
        cells = [(row[i] if i < len(row) else "").ljust(widths[i]) for i in range(len(cols))]
        out.append(indent + "  ".join(cells).rstrip())
        if n == 0:
            out.append(indent + "  ".join("-" * w for w in widths))
    return "\n".join(out)


def csv_render(columns, rows) -> str:
    import csv as _csv

    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow([str(c) for c in columns])
    for row in rows:
        writer.writerow([_csv_cell(row.get(c) if isinstance(row, dict) else v)
                         for c, v in zip(columns, row if isinstance(row, (list, tuple))
                                         else [row.get(c) for c in columns])])
    return buf.getvalue()


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_csv_cell(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={_csv_cell(v)}" for k, v in value.items())
    return str(value)


def csv_parse(text: str, restkey: str = "_extra") -> list:
    import csv as _csv

    reader = _csv.DictReader(io.StringIO(text or ""), restkey=restkey, restval="")
    rows = []
    for raw in reader:
        row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k is not None}
        extra = raw.get(restkey)
        if extra:
            row[restkey] = [e for e in (extra if isinstance(extra, list) else [extra])]
        if any(v not in (None, "") for v in row.values()):
            rows.append(row)
    return rows


def fingerprint(obj) -> str:
    """Short deterministic id of a dataset, so drift between runs is visible."""
    import hashlib

    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def confirm(action: str, approval_token: str, required_token: str, assume_yes: bool = False) -> None:
    """The approval gate. Non-interactive runs must pass the exact token; nothing is guessed."""
    if str(approval_token or "").strip().upper() == str(required_token).strip().upper():
        return
    if assume_yes and is_tty():
        return
    raise ApprovalRequired(f"action needs explicit approval: {action}", action=action,
                           required_token=required_token,
                           hint=f"re-run with --approve {required_token}"
                                + (" (this run has no interactive terminal, so prompting is unavailable)"
                                   if not is_tty() else ""))


def is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (ValueError, OSError):
        return False


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [n for n in dir() if not n.startswith("_")]
