"""Shared runtime helpers for the spreadsheet-data-finance Skill Tool.

Pure standard library. Provides:
  * structured result envelopes and typed errors
  * path / IO validation with atomic writes
  * table container helpers
  * locale-aware number and date parsing
  * statistics primitives (normal/t/chi-square distributions, OLS, percentiles)
  * approval gating and an append-only audit trail
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TOOL_NAME = "spreadsheet-data-finance"
VERSION = "1.0.0"

DEFAULT_MAX_ROWS = 200_000
DEFAULT_MAX_FILE_MB = 200
DEFAULT_TIMEOUT_S = 120
AUDIT_ENV = "SDF_AUDIT_LOG"

# --------------------------------------------------------------------------
# errors and result envelopes
# --------------------------------------------------------------------------


class SkillToolError(Exception):
    """Typed, user-facing error with a stable machine code."""

    def __init__(self, code: str, message: str, hint: str = "", details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


def ok(operation: str, data=None, warnings=None, meta=None) -> dict:
    return {
        "status": "ok",
        "operation": operation,
        "tool": TOOL_NAME,
        "version": VERSION,
        "data": data if data is not None else {},
        "warnings": list(warnings or []),
        "errors": [],
        "meta": dict(meta or {}),
    }


def fail(operation: str, error: SkillToolError, warnings=None) -> dict:
    return {
        "status": "error",
        "operation": operation,
        "tool": TOOL_NAME,
        "version": VERSION,
        "data": {},
        "warnings": list(warnings or []),
        "errors": [error.as_dict()],
        "meta": {},
    }


# --------------------------------------------------------------------------
# limits
# --------------------------------------------------------------------------


def limits() -> dict:
    def _int(name, default):
        raw = os.environ.get(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            raise SkillToolError(
                "INVALID_LIMIT",
                f"Environment value {name}={raw!r} is not an integer.",
                f"Set {name} to a positive integer or unset it.",
            )

    return {
        "max_rows": _int("SDF_MAX_ROWS", DEFAULT_MAX_ROWS),
        "max_file_mb": _int("SDF_MAX_FILE_MB", DEFAULT_MAX_FILE_MB),
        "timeout_s": _int("SDF_TIMEOUT_S", DEFAULT_TIMEOUT_S),
    }


# --------------------------------------------------------------------------
# path and IO validation
# --------------------------------------------------------------------------


def input_path(raw, must_exist: bool = True) -> Path:
    if raw in (None, ""):
        raise SkillToolError("MISSING_INPUT", "No input path supplied.", "Pass --input <path>.")
    p = Path(str(raw)).expanduser()
    if must_exist:
        if not p.exists():
            raise SkillToolError("INPUT_NOT_FOUND", f"Input path does not exist: {p}", "Check the path and retry.")
        if p.is_dir():
            raise SkillToolError("INPUT_IS_DIRECTORY", f"Input path is a directory: {p}", "Pass a file path.")
        size_mb = p.stat().st_size / (1024 * 1024)
        if size_mb > limits()["max_file_mb"]:
            raise SkillToolError(
                "INPUT_TOO_LARGE",
                f"Input is {size_mb:.1f} MB which exceeds the configured limit.",
                "Raise SDF_MAX_FILE_MB or split the file.",
            )
    return p


def output_path(raw, default: str, overwrite: bool) -> Path:
    p = Path(str(raw or default)).expanduser()
    if p.exists() and not overwrite:
        raise SkillToolError(
            "OUTPUT_EXISTS",
            f"Refusing to overwrite existing output: {p}",
            "Pass --overwrite to replace it, or choose another --output.",
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".sdf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, obj) -> Path:
    return atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def load_json_arg(value):
    """Accept a JSON file path, raw JSON text, or a plain value string."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("{") or s.startswith("["):
        try:
            return json.loads(s)
        except json.JSONDecodeError as exc:
            raise SkillToolError("BAD_JSON", f"Inline JSON could not be parsed: {exc}", "Check quoting or pass a file path.")
    p = Path(s).expanduser()
    if p.exists() and p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SkillToolError("BAD_JSON", f"JSON file {p} could not be parsed: {exc}", "Fix the JSON syntax.")
    raise SkillToolError(
        "BAD_JSON",
        "Value is neither parseable JSON nor an existing file path.",
        "Pass inline JSON (starting with '{') or an existing .json file path.",
    )


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------
# audit trail and approval gate
# --------------------------------------------------------------------------

APPROVAL_REQUIRED = {
    "protect",
    "export-gate",
    "redact",
    "scrub",
    "batch",
    "convert",
    "set-cells",
    "bulk",
    "sort",
    "build",
    "dashboard",
    "assist-build",
}


def audit_log_path() -> Path:
    raw = os.environ.get(AUDIT_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "sdf-audit.jsonl"


def record_audit(operation: str, summary: dict, approved: bool) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": TOOL_NAME,
        "version": VERSION,
        "operation": operation,
        "approved": bool(approved),
        "summary": summary,
    }
    try:
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        # Auditing must never break the operation itself; the caller reports it.
        pass


def require_approval(operation: str, approve: bool, what: str) -> None:
    """Block mutating / irreversible / security-sensitive actions without --approve."""
    if operation in APPROVAL_REQUIRED and not approve:
        raise SkillToolError(
            "APPROVAL_REQUIRED",
            f"Operation '{operation}' can modify or release data ({what}) and needs explicit approval.",
            f"Re-run with --approve after reviewing the dry-run preview.",
        )


# --------------------------------------------------------------------------
# table container
# --------------------------------------------------------------------------


def make_table(columns, rows, meta=None) -> dict:
    cols = [str(c) for c in columns]
    cleaned = []
    for r in rows:
        row = list(r)
        if len(row) < len(cols):
            row += [None] * (len(cols) - len(row))
        elif len(row) > len(cols):
            row = row[: len(cols)]
        cleaned.append(row)
    return {"columns": cols, "rows": cleaned, "meta": dict(meta or {})}


def table_shape(table: dict) -> tuple:
    return len(table.get("rows", [])), len(table.get("columns", []))


def column_index(table: dict, name: str) -> int:
    cols = table["columns"]
    if name in cols:
        return cols.index(name)
    low = [c.lower() for c in cols]
    if str(name).lower() in low:
        return low.index(str(name).lower())
    raise SkillToolError(
        "UNKNOWN_COLUMN",
        f"Column {name!r} not found.",
        "Available columns: " + ", ".join(cols),
        {"available": cols},
    )


def sort_table(table: dict, keys, descending=False) -> dict:
    idx = [(column_index(table, k), k) for k in keys]
    rows = list(table["rows"])

    def sort_key(row):
        out = []
        for i, _ in idx:
            v = row[i]
            if v is None:
                out.append((1, ""))
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((0, float(v)))
            else:
                out.append((0, str(v)))
        return out

    rows.sort(key=sort_key, reverse=bool(descending))
    return {**table, "rows": rows}


def is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def to_number(v, default=None):
    """Lenient numeric coercion used by analysis commands."""
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return default
        return v
    s = str(v).strip()
    if not s:
        return default
    s = s.replace("\u00a0", " ")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    # Strip a leading/trailing currency code but never guess past real letters:
    # identifiers such as "INV-2001" must not silently become numbers.
    s = re.sub(r"^([A-Za-z]{2,3})\s+", "", s)
    s = re.sub(r"\s+([A-Za-z]{2,3})$", "", s)
    if re.search(r"[A-Za-z]", s) and not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+", s):
        return default
    s = re.sub(r"[^0-9eE+\-.,]", "", s)
    if not s:
        return default
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", "") if len(parts[-1]) == 3 else s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return default
    if neg:
        val = -val
    return int(val) if val.is_integer() and "e" not in s.lower() else val


def to_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"true", "t", "yes", "y", "1", "on"}:
        return True
    if s in {"false", "f", "no", "n", "0", "off"}:
        return False
    return default


# --------------------------------------------------------------------------
# numbers / dates / locale
# --------------------------------------------------------------------------

TRUE_WORDS = {"true", "t", "yes", "y", "1", "on"}


def parse_number_locale(raw, decimal: str = ".", thousands: str = ","):
    """Parse a locale-formatted number string. Returns (value, ok)."""
    if raw is None:
        return None, False
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw, True
    s = str(raw).strip().replace("\u00a0", "").replace(" ", "")
    if s == "":
        return None, False
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.startswith("-"):
        neg, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    cur = re.match(r"^[^\d+\-.,]*", s)
    if cur and cur.group(0):
        s = s[cur.group(0).__len__():]
    s = re.sub(r"[^\d.,eE+\-]", "", s)
    if not s:
        return None, False
    if thousands and thousands in s:
        s = s.replace(thousands, "")
    elif "," in s and decimal != ",":
        # tolerate bare group separators when the group is exactly 3 digits
        parts = s.split(",")
        if all(len(p) == 3 for p in parts[1:]) and len(parts) > 1:
            s = "".join(parts)
    if decimal and decimal != ".":
        s = s.replace(decimal, ".")
    if "," in s:
        s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None, False
    if pct:
        val = val / 100.0
    return (-val if neg else val), True


DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%d.%m.%Y", "%Y%m%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%y", "%m/%d/%y",
]


def parse_date(raw, dayfirst=True):
    """Return datetime.date or None. Excel serial numbers are supported."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if 20000 <= float(raw) <= 80000:  # plausible Excel serial range
            return (datetime(1899, 12, 30) + timedelta(days=float(raw))).date()
        return None
    s = str(raw).strip()
    for fmt in DATE_FORMATS:
        for cand in ([s] if not dayfirst else [s]):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    # ambiguous numeric dates: swap day/month if dayfirst
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        d, mo = (a, b) if dayfirst else (b, a)
        try:
            return date(y, mo, d)
        except ValueError:
            try:
                return date(y, d, mo)
            except ValueError:
                return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


ID_HEADER_HINTS = {"id", "code", "number", "no", "ref", "sku", "iban", "tin", "ssn", "phone",
                   "postal", "zip", "account", "invoice", "batch", "lot", "serial", "gl", "barcode"}


def looks_like_identifier(name, values) -> bool:
    """True when a column is an identifier and must stay text (never numeric-coerced).

    Free text columns such as 'customer' or 'region' are plain text, not identifiers;
    only columns that mix letters and digits (or carry code-like digits) qualify.
    """
    low = str(name).strip().lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", low) if t}
    if tokens & ID_HEADER_HINTS:
        return True
    samples = [str(v).strip().replace(" ", "") for v in values if v is not None and str(v).strip()]
    if not samples:
        return False
    for s in samples:
        bare = s.lstrip("+-")
        if bare.isdigit() and bare.startswith("0") and len(bare) > 1:
            return True
        if bare.isdigit() and len(bare) >= 11:
            return True
    with_digit = sum(1 for s in samples if any(ch.isdigit() for ch in s)) / len(samples)
    alpha_numeric = sum(1 for s in samples
                        if any(ch.isalpha() for ch in s) and any(ch.isdigit() for ch in s)) / len(samples)
    return alpha_numeric >= 0.5 and with_digit >= 0.8


def normalize_header(name: str) -> str:
    s = re.sub(r"[\s_\-]+", " ", str(name).strip())
    return s


# --------------------------------------------------------------------------
# statistics primitives (pure python, deterministic)
# --------------------------------------------------------------------------


def nums(values):
    out = []
    for v in values:
        n = to_number(v)
        if n is not None:
            out.append(float(n))
    return out


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def variance(xs, sample=True):
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    denom = (n - 1) if sample else n
    return sum((x - m) ** 2 for x in xs) / denom


def stdev(xs, sample=True):
    v = variance(xs, sample)
    return math.sqrt(v) if v is not None else None


def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def percentile(xs, q, method="linear"):
    """Percentile with linear interpolation (Excel PERCENTILE.INC compatible)."""
    if not xs:
        return None
    if not 0 <= q <= 1:
        raise SkillToolError("BAD_ARGUMENT", f"Percentile q must be within 0..1, got {q}.")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    if method == "exclusive":
        pos = q * (len(s) + 1) - 1
    else:
        pos = q * (len(s) - 1)
    pos = max(0.0, min(pos, len(s) - 1.0))
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def skew(xs):
    n = len(xs)
    if n < 3:
        return None
    m, sd = mean(xs), stdev(xs)
    if not sd:
        return None
    return (n / ((n - 1) * (n - 2))) * sum(((x - m) / sd) ** 3 for x in xs)


def kurtosis(xs):
    n = len(xs)
    if n < 4:
        return None
    m, sd = mean(xs), stdev(xs)
    if not sd:
        return None
    s4 = sum(((x - m) / sd) ** 4 for x in xs)
    return (n * (n + 1) * s4 - 3 * (n - 1) ** 3) / ((n - 1) * (n - 2) * (n - 3))


def _gammaln(x: float) -> float:
    return math.lgamma(x)


def _betacf(a, b, x, itmax=200, eps=3e-12):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a, b, x) -> float:
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = _gammaln(a + b) - _gammaln(a) - _gammaln(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """Upper-tail probability P(T > t) for Student t."""
    if df <= 0:
        raise SkillToolError("BAD_ARGUMENT", "Degrees of freedom must be positive.")
    x = df / (df + t * t)
    p = 0.5 * betainc(df / 2.0, 0.5, x)
    return p if t > 0 else 1.0 - p


def t_cdf(t: float, df: float) -> float:
    return 1.0 - t_sf(t, df)


def t_critical(alpha: float, df: float) -> float:
    """Two-sided critical t value via bisection."""
    target = alpha / 2.0
    lo, hi = 0.0, 200.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def norm_sf(z: float) -> float:
    return 1.0 - norm_cdf(z)


def norm_ppf(p: float) -> float:
    if not 0 < p < 1:
        raise SkillToolError("BAD_ARGUMENT", "p must be within (0,1) for the normal quantile.")
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _gammainc_upper_reg(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a,x)."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        # series for P(a,x)
        term = 1.0 / a
        total = term
        n = a
        for _ in range(1000):
            n += 1.0
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        p = total * math.exp(-x + a * math.log(x) - _gammaln(a))
        return max(0.0, 1.0 - p)
    # continued fraction for Q(a,x)
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - _gammaln(a)) * h


def chi2_sf(stat: float, df: float) -> float:
    return _gammainc_upper_reg(df / 2.0, stat / 2.0)


def chi2_critical(alpha: float, df: float) -> float:
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if chi2_sf(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def f_sf(stat: float, df1: float, df2: float) -> float:
    if stat <= 0:
        return 1.0
    x = df2 / (df2 + df1 * stat)
    return betainc(df2 / 2.0, df1 / 2.0, x)


def pearson(xs, ys):
    pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    mx = mean([p[0] for p in pairs])
    my = mean([p[1] for p in pairs])
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    return sxy / math.sqrt(sxx * syy), n


def rankdata(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
    if len(pairs) < 3:
        return None, len(pairs)
    rx = rankdata([p[0] for p in pairs])
    ry = rankdata([p[1] for p in pairs])
    r, _ = pearson(rx, ry)
    return r, len(pairs)


def ols(X, y):
    """Ordinary least squares with intercept already included in X.

    Returns dict with coefficients, se, t, p, r2, adj_r2, rmse, n, k.
    Uses normal equations with Gauss-Jordan inversion (fine for small k).
    """
    n = len(y)
    k = len(X[0]) if X else 0
    if n <= k:
        raise SkillToolError("INSUFFICIENT_DATA", f"Need more rows ({n}) than parameters ({k}).")
    # XtX and Xty
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    inv = _invert(XtX)
    beta = [sum(inv[a][b] * Xty[b] for b in range(k)) for a in range(k)]
    fitted = [sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]
    resid = [y[i] - fitted[i] for i in range(n)]
    sse = sum(r * r for r in resid)
    ybar = mean(y)
    sst = sum((v - ybar) ** 2 for v in y)
    df_resid = n - k
    mse = sse / df_resid if df_resid > 0 else None
    se = []
    tvals = []
    pvals = []
    for a in range(k):
        var = mse * inv[a][a] if mse is not None else None
        s = math.sqrt(var) if var is not None and var >= 0 else None
        se.append(s)
        if s and s != 0:
            tv = beta[a] / s
            tvals.append(tv)
            pvals.append(2.0 * t_sf(abs(tv), df_resid))
        else:
            tvals.append(None)
            pvals.append(None)
    r2 = 1.0 - sse / sst if sst > 0 else None
    adj = 1.0 - (1.0 - r2) * (n - 1) / df_resid if (r2 is not None and df_resid > 0) else None
    return {
        "n": n,
        "k": k,
        "df_resid": df_resid,
        "coefficients": beta,
        "std_errors": se,
        "t_stats": tvals,
        "p_values": pvals,
        "r2": r2,
        "adj_r2": adj,
        "rmse": math.sqrt(mse) if mse is not None else None,
        "sse": sse,
        "sst": sst,
        "residuals": resid,
        "fitted": fitted,
        "inv_xtx": inv,
    }


def _invert(m):
    n = len(m)
    a = [list(m[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise SkillToolError(
                "SINGULAR_MATRIX",
                "Design matrix is singular (collinear or constant predictors).",
                "Drop a column or remove constant/duplicated predictors.",
            )
        a[col], a[piv] = a[piv], a[col]
        pv = a[col][col]
        a[col] = [v / pv for v in a[col]]
        for r in range(n):
            if r != col and a[r][col] != 0.0:
                factor = a[r][col]
                a[r] = [v - factor * av for v, av in zip(a[r], a[col])]
    return [row[n:] for row in a]


def safe_div(a, b, default=None):
    try:
        if b in (0, None) or a is None:
            return default
        return a / b
    except ZeroDivisionError:
        return default


def round_half_up(value, places=2):
    if value is None:
        return None
    from decimal import Decimal, ROUND_HALF_UP

    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def npr(values, r):
    out = 1
    for i in range(r):
        out *= values - i
    return out


def combinations(n, r):
    return math.comb(int(n), int(r))


def money_pattern(currency="USD", decimals=2, negative="parens", zero="dash"):
    """Build an Excel number-format pattern for a currency."""
    symbols = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5", "PHP": "\u20b1", "INR": "\u20b9", "CNY": "\u00a5", "AUD": "A$", "CAD": "C$", "SGD": "S$"}
    sym = symbols.get(currency.upper(), currency.upper() + " ")
    dec = "" if decimals == 0 else "." + "0" * decimals
    pos = f'"{sym}"#,##0{dec};'
    negf = f'"{sym}"(#,##0{dec})' if negative == "parens" else f'"{sym}"-#,##0{dec}'
    return pos + negf + ";" + ('"-"' if zero == "dash" else f'"{sym}"0{dec}')


def pmt(rate, nper, pv, fv=0.0, ftype=0):
    """Excel-compatible PMT (returns a negative cash flow for a positive pv)."""
    if nper == 0:
        raise SkillToolError("BAD_ARGUMENT", "nper must be non-zero.")
    if rate == 0:
        return -(pv + fv) / nper
    p = (1 + rate) ** nper
    return -(rate * (pv * p + fv)) / ((p - 1) * (1 + rate * ftype))


def fv(rate, nper, pmt_value, pv=0.0, ftype=0):
    if rate == 0:
        return -(pv + pmt_value * nper)
    p = (1 + rate) ** nper
    return -(pv * p + pmt_value * (1 + rate * ftype) * (p - 1) / rate)


def pv(rate, nper, pmt_value, fv_value=0.0, ftype=0):
    if rate == 0:
        return -(fv_value + pmt_value * nper)
    p = (1 + rate) ** nper
    return -(fv_value + pmt_value * (1 + rate * ftype) * (p - 1) / rate) / p


def safe_filename(name: str) -> str:
    s = re.sub(r"[^\w.\- ]+", "", str(name)).strip().replace(" ", "-")
    s = re.sub(r"-{2,}", "-", s)
    return s or "output"
