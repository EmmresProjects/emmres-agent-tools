"""Shared core for the code-data-reverse-engineering skill tool.

Provides: configuration and policy loading, structured result envelopes,
safe input/output handling, limits, hashing, entropy, evidence/case ledgers,
approval gates, and small binary/text utilities used by every module.
Standard library only. Python 3.10+.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

TOOL_NAME = "code-data-reverse-engineering"
TOOL_VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_APPROVAL = 3
EXIT_LIMIT = 4


class RetoolError(Exception):
    def __init__(self, code: str, message: str, details=None, exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code


def load_sibling(stem: str):
    """Load a sibling tools-*.py module by its hyphenated file stem."""
    modname = stem.replace("-", "_")
    if modname in sys.modules:
        return sys.modules[modname]
    path = HERE / f"{stem}.py"
    if not path.exists():
        raise RetoolError("missing-module", f"Required module file not found: {path.name}")
    spec = importlib.util.spec_from_file_location(modname, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_asset(name: str):
    path = HERE / name
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise RetoolError("missing-asset", f"Required asset not found: {TOOL_NAME}/{name}")
    except json.JSONDecodeError as e:
        raise RetoolError("bad-asset", f"Asset is not valid JSON: {TOOL_NAME}/{name}: {e}")


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(config_path: str | None) -> dict:
    cfg = load_json_asset("asset-config.json")
    if config_path:
        p = Path(config_path)
        if not p.is_file():
            raise RetoolError("bad-config", f"Config file not found: {config_path}", exit_code=EXIT_USAGE)
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RetoolError("bad-config", f"Config is not valid JSON: {e}", exit_code=EXIT_USAGE)
        if not isinstance(user, dict):
            raise RetoolError("bad-config", "Config root must be an object", exit_code=EXIT_USAGE)
        cfg = deep_merge(cfg, user)
    lim = cfg.get("limits", {})
    for k, v in lim.items():
        if not isinstance(v, (int, float)) or v <= 0:
            raise RetoolError("bad-config", f"limits.{k} must be a positive number", exit_code=EXIT_USAGE)
    return cfg


def policy() -> dict:
    return load_json_asset("asset-policy.json")


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

def envelope(command: str, cfg: dict) -> dict:
    return {
        "ok": True,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "command": command,
        "started_at": utcnow(),
        "inputs": {},
        "result": {},
        "warnings": [],
        "errors": [],
        "limitations": [],
        "limits_applied": {
            "max_file_bytes": cfg["limits"]["max_file_bytes"],
            "max_read_bytes": cfg["limits"]["max_read_bytes"],
        },
    }


def finish(env: dict, started: float, output: str | None, fmt: str) -> int:
    env["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    env["ok"] = not env["errors"]
    text = render(env, fmt)
    if output:
        p = Path(output)
        if p.exists():
            raise RetoolError("output-exists", f"Output path already exists: {output}. Choose a new path (inputs and existing outputs are never overwritten).", exit_code=EXIT_USAGE)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
        env["output_file"] = str(p)
    print(text)
    return EXIT_OK if env["ok"] else EXIT_ERROR


def render(env: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(env, indent=2, ensure_ascii=False, default=str)
    if fmt == "md":
        return render_md(env)
    return render_text(env)


def render_text(env: dict) -> str:
    lines = [f"[{'OK' if env['ok'] else 'ERROR'}] {env['command']} ({env.get('duration_ms', '?')} ms)"]
    res = env.get("result", {})
    lines.append(json.dumps(res, indent=2, ensure_ascii=False, default=str)[:20000])
    for w in env.get("warnings", []):
        lines.append(f"WARNING: {w}")
    for e in env.get("errors", []):
        lines.append(f"ERROR: {e}")
    for l in env.get("limitations", []):
        lines.append(f"LIMIT: {l}")
    return "\n".join(lines)


def render_md(env: dict) -> str:
    lines = [f"# {env['command']} result", "", f"- Tool: {env['tool']} v{env['tool_version']}",
             f"- Started: {env['started_at']}", f"- Status: {'ok' if env['ok'] else 'error'}", ""]
    lines.append("```json")
    lines.append(json.dumps(env.get("result", {}), indent=2, ensure_ascii=False, default=str))
    lines.append("```")
    if env.get("warnings"):
        lines += ["", "## Warnings"] + [f"- {w}" for w in env["warnings"]]
    if env.get("limitations"):
        lines += ["", "## Limitations"] + [f"- {l}" for l in env["limitations"]]
    return "\n".join(lines)


def error_envelope(command: str, exc: Exception) -> tuple[dict, int]:
    if isinstance(exc, RetoolError):
        env = {"ok": False, "tool": TOOL_NAME, "tool_version": TOOL_VERSION, "command": command,
               "started_at": utcnow(), "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        return env, exc.exit_code
    env = {"ok": False, "tool": TOOL_NAME, "tool_version": TOOL_VERSION, "command": command,
           "started_at": utcnow(), "error": {"code": "internal-error", "message": str(exc),
                                             "details": {"type": type(exc).__name__}}}
    return env, EXIT_ERROR


# ---------------------------------------------------------------------------
# Input validation and safe IO
# ---------------------------------------------------------------------------

def validate_input_file(path: str, cfg: dict, must_exist: bool = True) -> Path:
    p = Path(path)
    if must_exist and not p.exists():
        raise RetoolError("input-missing", f"Input path does not exist: {path}", exit_code=EXIT_USAGE)
    if must_exist and not p.is_file():
        raise RetoolError("input-not-file", f"Input is not a regular file: {path}", exit_code=EXIT_USAGE)
    if must_exist:
        size = p.stat().st_size
        if size > cfg["limits"]["max_file_bytes"]:
            raise RetoolError("file-too-large", f"File exceeds max_file_bytes ({size} > {cfg['limits']['max_file_bytes']})",
                              {"size": size, "limit": cfg["limits"]["max_file_bytes"]}, EXIT_LIMIT)
    return p


def read_bytes(path: Path, cfg: dict, offset: int = 0, length: int | None = None) -> bytes:
    limit = cfg["limits"]["max_read_bytes"]
    if length is None:
        length = min(path.stat().st_size - offset, limit)
    if length < 0:
        raise RetoolError("bad-range", "Negative read length", exit_code=EXIT_USAGE)
    if length > limit:
        raise RetoolError("read-limit", f"Requested read exceeds max_read_bytes ({length} > {limit})", exit_code=EXIT_LIMIT)
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(length)
    return data


def ensure_new_output(path: str, approve: bool = False) -> Path:
    p = Path(path)
    if p.exists():
        if not approve:
            raise RetoolError("approval-required", f"Output path exists: {path}. Writing over existing files requires --approve.", exit_code=EXIT_APPROVAL)
    return p


def write_new_bytes(path: Path, data: bytes, approve: bool = False):
    if path.exists() and not approve:
        raise RetoolError("approval-required", f"Output exists: {path}; pass --approve to overwrite.", exit_code=EXIT_APPROVAL)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and approve:
        bak = path.with_suffix(path.suffix + ".bak")
        i = 1
        while bak.exists():
            bak = path.with_suffix(path.suffix + f".bak{i}")
            i += 1
        bak.write_bytes(path.read_bytes())
    with open(path, "wb") as f:
        f.write(data)


def require_purpose(args, env: dict, operation: str):
    pol = policy()
    purposes = pol["gated_operations"].get(operation, {})
    purpose = getattr(args, "purpose", None)
    allowed = load_json_asset("asset-config.json")["safety"]["allowed_purposes"]
    if purpose not in allowed:
        raise RetoolError(
            "purpose-required",
            pol["redaction"]["purpose_required_message"],
            {"operation": operation, "allowed_purposes": allowed, "gate": purposes},
            EXIT_APPROVAL,
        )
    env["inputs"]["purpose"] = purpose
    env["warnings"].append(f"Dual-use operation '{operation}' authorized by operator flag --purpose {purpose}. Confirm artifact authorization before proceeding.")


def require_approval(args, env: dict, operation: str):
    if not getattr(args, "approve", False):
        pol = policy()
        gate = pol["gated_operations"].get(operation, {"reason": "requires explicit approval"})
        raise RetoolError("approval-required", f"{gate['reason']} Re-run with --approve after reviewing the dry-run plan.",
                          {"operation": operation}, EXIT_APPROVAL)
    env["inputs"]["approved"] = True


# ---------------------------------------------------------------------------
# Hashing / entropy / hex
# ---------------------------------------------------------------------------

def hash_file(path: Path, algorithms=("sha256", "sha1", "md5"), chunk: int = 1024 * 1024) -> dict:
    hs = {a: hashlib.new(a) for a in algorithms}
    size = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            size += len(b)
            for h in hs.values():
                h.update(b)
    out = {"size": size}
    out.update({a: h.hexdigest() for a, h in hs.items()})
    return out


def hash_bytes(data: bytes, algorithms=("sha256",)) -> dict:
    return {a: hashlib.new(a, data).hexdigest() for a in algorithms}


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return round(ent, 4)


def entropy_windows(data: bytes, window: int = 1024, stride: int | None = None, max_windows: int = 512) -> list:
    stride = stride or window
    out = []
    for off in range(0, len(data), stride):
        chunk = data[off:off + window]
        if not chunk:
            break
        out.append({"offset": off, "length": len(chunk), "entropy": shannon_entropy(chunk)})
        if len(out) >= max_windows:
            break
    return out


def hexdump(data: bytes, base_offset: int = 0, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base_offset + i:08x}  {hexpart:<{width * 3 - 1}}  |{asciipart}|")
    return "\n".join(lines)


def find_all(data: bytes, needle: bytes, start: int = 0, limit: int = 1000):
    out = []
    pos = start
    while len(out) < limit:
        idx = data.find(needle, pos)
        if idx < 0:
            break
        out.append(idx)
        pos = idx + 1
    return out


def safe_decode(data: bytes, encoding: str = "utf-8") -> str:
    return data.decode(encoding, errors="replace")


def cstring(data: bytes, offset: int, max_len: int = 4096) -> str:
    end = data.find(b"\x00", offset, offset + max_len)
    if end < 0:
        end = min(len(data), offset + max_len)
    return data[offset:end].decode("utf-8", errors="replace")


def _eb(endian: str) -> str:
    return "little" if endian in ("le", "little", "<") else "big"


def u16(data: bytes, off: int, endian: str = "le") -> int:
    return int.from_bytes(data[off:off + 2], _eb(endian))


def u32(data: bytes, off: int, endian: str = "le") -> int:
    return int.from_bytes(data[off:off + 4], _eb(endian))


def u64(data: bytes, off: int, endian: str = "le") -> int:
    return int.from_bytes(data[off:off + 8], _eb(endian))


def i32(data: bytes, off: int, endian: str = "le") -> int:
    return int.from_bytes(data[off:off + 4], _eb(endian), signed=True)


def align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


def human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


# ---------------------------------------------------------------------------
# Case management: intake, custody, ledger, verification
# ---------------------------------------------------------------------------

def case_paths(case_dir: Path) -> dict:
    return {
        "root": case_dir,
        "artifacts": case_dir / "artifacts",
        "case": case_dir / "case.json",
        "custody": case_dir / "custody.jsonl",
        "ledger": case_dir / "ledger.jsonl",
        "results": case_dir / "results",
    }


def load_case(case_dir: str) -> dict:
    p = Path(case_dir)
    cp = case_paths(p)
    if not cp["case"].is_file():
        raise RetoolError("case-missing", f"No case.json in {case_dir}. Run intake first or pass a valid --case.", exit_code=EXIT_USAGE)
    try:
        return json.loads(cp["case"].read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RetoolError("case-corrupt", f"case.json is not valid JSON: {e}")


def save_case(case_dir: Path, case: dict):
    cp = case_paths(case_dir)
    cp["root"].mkdir(parents=True, exist_ok=True)
    tmp = cp["case"].with_suffix(".json.tmp")
    tmp.write_text(json.dumps(case, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cp["case"])


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_raw": line, "_error": "invalid JSON line"})
    return out


def custody_event(case_dir: Path, event: str, details: dict, actor: str = "operator"):
    cp = case_paths(case_dir)
    prev = read_jsonl(cp["custody"])
    prev_hash = prev[-1]["event_hash"] if prev else "0" * 64
    rec = {"seq": len(prev) + 1, "at": utcnow(), "actor": actor, "event": event, "details": details,
           "prev_hash": prev_hash}
    rec["event_hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
    append_jsonl(cp["custody"], rec)
    return rec


def verify_custody_chain(case_dir: Path) -> dict:
    events = read_jsonl(case_paths(case_dir)["custody"])
    prev = "0" * 64
    bad = []
    for ev in events:
        claimed = ev.get("event_hash")
        body = {k: v for k, v in ev.items() if k != "event_hash"}
        actual = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if actual != claimed or ev.get("prev_hash") != prev:
            bad.append(ev.get("seq"))
        prev = claimed or prev
    return {"events": len(events), "valid": not bad, "invalid_sequences": bad}


def redact(value: str, keep: int = 6) -> str:
    h = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:keep]
    return f"<redacted:{h}>"


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name)


def parse_int(s: str) -> int:
    s = s.strip()
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        if s.lower().startswith("0o"):
            return int(s, 8)
        if s.lower().startswith("0b"):
            return int(s, 2)
        return int(s, 10)
    except ValueError:
        raise RetoolError("bad-integer", f"Not an integer: {s}", exit_code=EXIT_USAGE)


def parse_offset_length(args, cfg: dict, file_size: int) -> tuple[int, int]:
    off = parse_int(getattr(args, "offset", "0") or "0")
    if off < 0 or off > file_size:
        raise RetoolError("bad-range", f"Offset out of range: {off}", exit_code=EXIT_USAGE)
    if getattr(args, "length", None):
        ln = parse_int(args.length)
        if ln < 0 or off + ln > file_size:
            raise RetoolError("bad-range", f"Length out of range: {ln}", exit_code=EXIT_USAGE)
    else:
        ln = min(file_size - off, cfg["limits"]["max_read_bytes"])
    return off, ln
