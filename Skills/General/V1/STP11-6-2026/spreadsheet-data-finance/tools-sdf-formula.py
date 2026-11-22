"""Formula engine: tokenizer, parser, evaluator, dependency graph, explainer.

Supports the practical subset of Excel formula syntax needed for authoring,
debugging, tracing precedent/dependent cells, and explaining logic in plain
language. Evaluation is deliberately sandboxed: no workbook execution, no macros,
no external calls.

Loaded by tools-sdf.py as the formula subsystem.
"""

from __future__ import annotations

import importlib.util
import math
import re
import sys
from datetime import date
from pathlib import Path


def _sdf_import(alias, filename):
    if alias in sys.modules:
        return sys.modules[alias]
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


C = _sdf_import("sdf_common", "tools-sdf-common.py")
SkillToolError = C.SkillToolError

TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)
  | (?P<name>(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!)?[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<op><>|<=|>=|[-+*/^&=<>(),%:])
    """,
    re.VERBOSE,
)

FUNCTIONS = {
    "SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "COUNTIF", "SUMIF", "PRODUCT",
    "IF", "IFERROR", "AND", "OR", "NOT", "ABS", "ROUND", "ROUNDUP", "ROUNDDOWN",
    "SQRT", "POWER", "MOD", "INT", "TRUNC", "EXP", "LN", "LOG", "SIGN",
    "CONCAT", "CONCATENATE", "LEN", "LEFT", "RIGHT", "MID", "UPPER", "LOWER", "TRIM", "VALUE", "TEXT",
    "YEAR", "MONTH", "DAY", "DATE", "TODAY",
    "NPV", "IRR", "PMT", "FV", "PV", "RATE", "SLN", "SUMPRODUCT", "MEDIAN", "STDEV", "VAR",
    "LARGE", "SMALL", "RANK", "INDEX", "MATCH", "VLOOKUP",
}

ERROR_VALUES = {"#REF!", "#VALUE!", "#N/A", "#DIV/0!", "#NAME?", "#NUM!", "#SPILL!", "#NULL!", "#CALC!"}


# --------------------------------------------------------------------------
# tokenizer / parser
# --------------------------------------------------------------------------


def tokenize(formula: str):
    src = formula.strip()
    if src.startswith("="):
        src = src[1:]
    tokens = []
    pos = 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise SkillToolError("FORMULA_SYNTAX", f"Unexpected character {src[pos]!r} at position {pos}.",
                                 "Check quotes, commas and parentheses.")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        text = m.group()
        tokens.append((kind, text))
    tokens.append(("eof", ""))
    return tokens


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, text):
        kind, val = self.next()
        if val != text:
            raise SkillToolError("FORMULA_SYNTAX", f"Expected {text!r} but found {val!r}.",
                                 "Check parentheses and argument separators.")
        return val

    def parse(self):
        node = self.expr()
        if self.peek()[0] != "eof":
            raise SkillToolError("FORMULA_SYNTAX", f"Unexpected token {self.peek()[1]!r} after expression.")
        return node

    def expr(self):
        node = self.comparison()
        while self.peek()[1] in {"&"}:
            self.next()
            node = ("bin", "&", node, self.comparison())
        return node

    def comparison(self):
        node = self.additive()
        while self.peek()[1] in {"=", "<>", "<", ">", "<=", ">="}:
            op = self.next()[1]
            node = ("bin", op, node, self.additive())
        return node

    def additive(self):
        node = self.term()
        while self.peek()[1] in {"+", "-"}:
            op = self.next()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.power()
        while self.peek()[1] in {"*", "/"}:
            op = self.next()[1]
            node = ("bin", op, node, self.power())
        return node

    def power(self):
        node = self.unary()
        while self.peek()[1] in {"^"}:
            self.next()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        if self.peek()[1] in {"-", "+"}:
            op = self.next()[1]
            return ("unary", op, self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek()[1] == "%":
            self.next()
            node = ("bin", "/", node, ("num", 100.0))
        return node

    def primary(self):
        kind, val = self.next()
        if kind == "number":
            return ("num", float(val))
        if kind == "string":
            return ("str", val[1:-1].replace('""', '"'))
        if kind == "ref":
            return ("ref", val)
        if kind == "name":
            if self.peek()[1] == "(":
                self.next()
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek()[1] == ",":
                        self.next()
                        if self.peek()[1] == ")":
                            break
                        args.append(self.expr())
                self.expect(")")
                return ("call", val.upper(), args)
            return ("name", val)
        if val == "(":
            node = self.expr()
            self.expect(")")
            return node
        raise SkillToolError("FORMULA_SYNTAX", f"Unexpected token {val!r}.")


def parse_formula(formula: str):
    if not isinstance(formula, str) or not formula.strip():
        raise SkillToolError("EMPTY_FORMULA", "Formula text is empty.", "Pass --formula '=SUM(A1:A10)'.")
    return Parser(tokenize(formula)).parse()


# --------------------------------------------------------------------------
# dependency extraction
# --------------------------------------------------------------------------


def _split_ref(ref: str):
    sheet = None
    if "!" in ref:
        sheet, ref = ref.rsplit("!", 1)
        sheet = sheet.strip("'")
    ref = ref.replace("$", "")
    return sheet, ref


def _col_to_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n


def _index_to_col(n: int) -> str:
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def expand_range(ref: str):
    """Expand 'A1:B3' -> list of cell addresses (bounded)."""
    if ":" not in ref:
        return [ref]
    start, end = ref.split(":", 1)
    m1 = re.match(r"([A-Za-z]+)(\d+)", start)
    m2 = re.match(r"([A-Za-z]+)(\d+)", end)
    if not m1 or not m2:
        return [ref]
    c1, r1 = _col_to_index(m1.group(1)), int(m1.group(2))
    c2, r2 = _col_to_index(m2.group(1)), int(m2.group(2))
    c1, c2 = min(c1, c2), max(c1, c2)
    r1, r2 = min(r1, r2), max(r1, r2)
    cells = []
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            cells.append(f"{_index_to_col(c)}{r}")
    return cells


def dependencies(formula: str, names=None) -> dict:
    """Return precedent cells, ranges, defined names and called functions."""
    ast = parse_formula(formula)
    refs, funcs, name_refs = [], [], []

    def walk(node):
        tag = node[0]
        if tag == "ref":
            refs.append(node[1])
        elif tag == "name":
            name_refs.append(node[1])
        elif tag == "call":
            funcs.append(node[1])
            for a in node[2]:
                walk(a)
        elif tag == "bin":
            walk(node[2])
            walk(node[3])
        elif tag == "unary":
            walk(node[2])

    walk(ast)
    cells = []
    for r in refs:
        sheet, bare = _split_ref(r)
        if ":" in bare:
            for cell in expand_range(bare):
                cells.append(f"{sheet}!{cell}" if sheet else cell)
        else:
            cells.append(f"{sheet}!{bare}" if sheet else bare)
    unknown = [f for f in funcs if f not in FUNCTIONS]
    return {
        "cells": sorted(set(cells)),
        "ranges": [r for r in refs if ":" in r],
        "names_used": sorted(set(name_refs)),
        "functions": sorted(set(funcs)),
        "volatile_functions": sorted({f for f in funcs if f in {"TODAY", "RAND", "NOW", "OFFSET", "INDIRECT"}}),
        "unknown_functions": unknown,
        "known_names": [n for n in name_refs if names and n in names],
        "undefined_names": [n for n in name_refs if (names is not None) and n not in names and n not in FUNCTIONS],
        "ast": ast,
    }


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------


class Env:
    """Cell environment: literal values plus lazily evaluated formula cells."""

    def __init__(self, cells=None, names=None, max_depth=64):
        self.raw = dict(cells or {})
        self.names = dict(names or {})
        self.cache = {}
        self.stack = []
        self.max_depth = max_depth
        self.errors = []

    def _key(self, ref: str) -> str:
        return ref.replace("$", "").upper()

    def get_cell(self, ref: str):
        key = self._key(ref)
        if key in self.cache:
            return self.cache[key]
        if key in self.stack:
            raise SkillToolError("CIRCULAR_REFERENCE",
                                 f"Circular reference detected: {' -> '.join(self.stack + [key])}.",
                                 "Break the loop by replacing one formula with a value or a different input.")
        if key not in self.raw:
            return None
        val = self.raw[key]
        if isinstance(val, str) and val.startswith("="):
            self.stack.append(key)
            try:
                ast = parse_formula(val)
                val = evaluate(ast, self)
            finally:
                self.stack.pop()
        self.cache[key] = val
        return val

    def get_range(self, ref: str):
        cells = expand_range(self._key(ref))
        return [self.get_cell(c) for c in cells]

    def resolve_name(self, name: str):
        if name in self.names:
            target = self.names[name]
            if isinstance(target, str) and re.fullmatch(r"\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?", target):
                if ":" in target:
                    return self.get_range(target)
                return self.get_cell(target)
            return target
        raise SkillToolError("UNKNOWN_NAME", f"Defined name {name!r} is not in the environment.",
                             "Pass --names '{\"Rate\":0.05}' or use a literal.")


REF_WITH_SHEET = re.compile(r"^(?:(?P<sheet>'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!)?(?P<range>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)$")


def _flatten(args):
    out = []
    for a in args:
        if isinstance(a, list):
            out.extend(_flatten(a))
        elif a is None:
            continue
        else:
            out.append(a)
    return out


def _numeric(args):
    vals = []
    for a in _flatten(args):
        if isinstance(a, bool):
            continue
        if isinstance(a, (int, float)):
            vals.append(float(a))
        else:
            n = C.to_number(a)
            if n is not None:
                vals.append(float(n))
    return vals


def _f_sum(*args):
    return sum(_numeric(args))


def _f_average(*args):
    vals = _numeric(args)
    if not vals:
        return "#DIV/0!"
    return sum(vals) / len(vals)


def _f_countif(rng, crit):
    values = _flatten([rng])
    return sum(1 for v in values if _match_criteria(v, crit))


def _match_criteria(value, crit):
    if isinstance(crit, str) and crit[:2] in {">=", "<=", "<>"}:
        op, rhs = crit[:2], crit[2:]
        n_rhs = C.to_number(rhs)
        n_val = C.to_number(value)
        if n_rhs is None or n_val is None:
            return False
        return {">=": n_val >= n_rhs, "<=": n_val <= n_rhs, "<>": n_val != n_rhs}[op]
    if isinstance(crit, str) and crit[:1] in {">", "<"}:
        op, rhs = crit[0], crit[1:]
        n_rhs, n_val = C.to_number(rhs), C.to_number(value)
        if n_rhs is None or n_val is None:
            return False
        return n_val > n_rhs if op == ">" else n_val < n_rhs
    if isinstance(crit, (int, float)) and isinstance(value, (int, float)):
        return float(value) == float(crit)
    return str(value).strip().lower() == str(crit).strip().lower()


def _f_sumif(rng, crit, sum_rng=None):
    values = _flatten([rng])
    targets = _flatten([sum_rng]) if sum_rng is not None else values
    total = 0.0
    for v, t in zip(values, targets):
        if _match_criteria(v, crit):
            n = C.to_number(t)
            if n is not None:
                total += float(n)
    return total


def _f_if(cond, a, b=False):
    truthy = cond
    if isinstance(cond, str):
        truthy = cond.strip().upper() in {"TRUE", "1"}
    return a if truthy else b


def _f_and(*args):
    return all(bool(_flatten([a])[0] if isinstance(a, list) else a) for a in args)


def _f_or(*args):
    return any(bool(_flatten([a])[0] if isinstance(a, list) else a) for a in args)


def _round_like(x, digits, mode):
    f = 10 ** int(digits)
    if mode == "half_up":
        return math.floor(abs(x) * f + 0.5) / f * (1 if x >= 0 else -1)
    if mode == "up":
        return math.ceil(abs(x) * f) / f * (1 if x >= 0 else -1)
    return math.floor(abs(x) * f) / f * (1 if x >= 0 else -1)


def _f_npv(rate, *values):
    vals = _numeric(values)
    return sum(v / (1 + rate) ** (i + 1) for i, v in enumerate(vals))


def _f_irr(values, guess=0.1):
    vals = _numeric([values])
    if not vals or all(v >= 0 for v in vals) or all(v <= 0 for v in vals):
        return "#NUM!"

    def npv(r):
        return sum(v / (1 + r) ** i for i, v in enumerate(vals))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return "#NUM!"
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _f_pmt(rate, nper, pv, fv=0.0, ftype=0):
    if rate == 0:
        return -(pv + fv) / nper
    p = (1 + rate) ** nper
    pmt = -(rate * (pv * p + fv)) / ((p - 1) * (1 + rate * ftype))
    return pmt


def _f_fv(rate, nper, pmt, pv=0.0, ftype=0):
    if rate == 0:
        return -(pv + pmt * nper)
    p = (1 + rate) ** nper
    return -(pv * p + pmt * (1 + rate * ftype) * (p - 1) / rate)


def _f_pv(rate, nper, pmt, fv=0.0, ftype=0):
    if rate == 0:
        return -(fv + pmt * nper)
    p = (1 + rate) ** nper
    return -(fv + pmt * (1 + rate * ftype) * (p - 1) / rate) / p


def _f_lookup_index(args_grid, row, col):
    grid = args_grid if isinstance(args_grid, list) else [args_grid]
    if not isinstance(grid[0], list):
        grid = [grid]
    r = int(row) - 1
    c = int(col) - 1
    if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
        return grid[r][c]
    return "#REF!"


def _text(x):
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def evaluate(node, env: Env):
    tag = node[0]
    if tag == "num":
        return node[1]
    if tag == "str":
        return node[1]
    if tag == "ref":
        sheet, bare = _split_ref(node[1])
        if ":" in bare:
            vals = env.get_range(bare)
            return vals
        return env.get_cell(bare)
    if tag == "name":
        name = node[1]
        if name.upper() in {"TRUE", "FALSE"}:
            return name.upper() == "TRUE"
        return env.resolve_name(name)
    if tag == "unary":
        v = C.to_number(evaluate(node[2], env), 0.0)
        return -float(v) if node[1] == "-" else float(v)
    if tag == "bin":
        op = node[1]
        a = evaluate(node[2], env)
        b = evaluate(node[3], env)
        if op == "&":
            return _text(a) + _text(b)
        if op in {"=", "<>", "<", ">", "<=", ">="}:
            na, nb = C.to_number(a), C.to_number(b)
            if na is not None and nb is not None:
                a_cmp, b_cmp = float(na), float(nb)
            else:
                a_cmp, b_cmp = str(a), str(b)
            return {"=": a_cmp == b_cmp, "<>": a_cmp != b_cmp, "<": a_cmp < b_cmp,
                    ">": a_cmp > b_cmp, "<=": a_cmp <= b_cmp, ">=": a_cmp >= b_cmp}[op]
        x = C.to_number(a, 0.0)
        y = C.to_number(b, 0.0)
        try:
            if op == "+":
                return float(x) + float(y)
            if op == "-":
                return float(x) - float(y)
            if op == "*":
                return float(x) * float(y)
            if op == "/":
                return "#DIV/0!" if float(y) == 0 else float(x) / float(y)
            if op == "^":
                return float(x) ** float(y)
        except (TypeError, ValueError, OverflowError):
            return "#VALUE!"
        return "#VALUE!"
    if tag == "call":
        return _call(node[1], node[2], env)
    return "#VALUE!"


def _call(name, arg_nodes, env):
    if name not in FUNCTIONS:
        env.errors.append(f"Unknown function {name}")
        return "#NAME?"
    args = [evaluate(a, env) for a in arg_nodes]
    try:
        if name == "IF":
            return _f_if(*args)
        if name == "IFERROR":
            v = args[0]
            if isinstance(v, str) and v in ERROR_VALUES:
                return args[1] if len(args) > 1 else ""
            return v
        if name == "AND":
            return _f_and(*args)
        if name == "OR":
            return _f_or(*args)
        if name == "NOT":
            return not bool(args[0])
        if name == "SUM":
            return _f_sum(*args)
        if name == "PRODUCT":
            vals = _numeric(args)
            out = 1.0
            for v in vals:
                out *= v
            return out
        if name == "SUMPRODUCT":
            lists = [_numeric([a]) if isinstance(a, list) else [C.to_number(a, 0.0)] for a in args]
            n = min(len(l) for l in lists)
            return sum(math.prod(l[i] for l in lists) for i in range(n))
        if name == "AVERAGE":
            return _f_average(*args)
        if name == "MEDIAN":
            vals = _numeric(args)
            return C.median(vals) if vals else "#NUM!"
        if name == "STDEV":
            vals = _numeric(args)
            return C.stdev(vals) if len(vals) > 1 else "#DIV/0!"
        if name == "VAR":
            vals = _numeric(args)
            return C.variance(vals) if len(vals) > 1 else "#DIV/0!"
        if name == "MIN":
            vals = _numeric(args)
            return min(vals) if vals else 0.0
        if name == "MAX":
            vals = _numeric(args)
            return max(vals) if vals else 0.0
        if name == "COUNT":
            return float(len(_numeric(args)))
        if name == "COUNTA":
            return float(len(_flatten(args)))
        if name == "COUNTIF":
            return float(_f_countif(args[0], args[1]))
        if name == "SUMIF":
            return _f_sumif(*args)
        if name == "LARGE":
            vals = sorted(_numeric([args[0]]), reverse=True)
            k = int(C.to_number(args[1], 1))
            return vals[k - 1] if 0 < k <= len(vals) else "#NUM!"
        if name == "SMALL":
            vals = sorted(_numeric([args[0]]))
            k = int(C.to_number(args[1], 1))
            return vals[k - 1] if 0 < k <= len(vals) else "#NUM!"
        if name == "RANK":
            vals = _numeric([args[0]])
            return float(sum(1 for v in vals if v > vals[0]) + 1) if vals else "#VALUE!"
        if name == "INDEX":
            return _f_lookup_index(*args)
        if name == "MATCH":
            target = args[0]
            hay = _flatten([args[1]])
            for i, v in enumerate(hay, start=1):
                if _match_criteria(v, target):
                    return float(i)
            return "#N/A"
        if name == "VLOOKUP":
            target, grid, col = args[0], args[1], int(C.to_number(args[2], 1))
            rows = grid if isinstance(grid[0], list) else [grid]
            for row in rows:
                if _match_criteria(row[0], target):
                    return row[col - 1] if col - 1 < len(row) else "#REF!"
            return "#N/A"
        if name == "ABS":
            return abs(_numeric([args[0]])[0])
        if name == "SIGN":
            v = _numeric([args[0]])[0]
            return 0.0 if v == 0 else (1.0 if v > 0 else -1.0)
        if name == "ROUND":
            return _round_like(_numeric([args[0]])[0], C.to_number(args[1], 0), "half_up")
        if name == "ROUNDUP":
            return _round_like(_numeric([args[0]])[0], C.to_number(args[1], 0), "up")
        if name == "ROUNDDOWN":
            return _round_like(_numeric([args[0]])[0], C.to_number(args[1], 0), "down")
        if name == "SQRT":
            v = _numeric([args[0]])[0]
            return math.sqrt(v) if v >= 0 else "#NUM!"
        if name == "POWER":
            return _numeric([args[0]])[0] ** _numeric([args[1]])[0]
        if name == "MOD":
            a, b = _numeric([args[0]])[0], _numeric([args[1]])[0]
            return "#DIV/0!" if b == 0 else math.fmod(a, b)
        if name == "INT":
            return float(math.floor(_numeric([args[0]])[0]))
        if name == "TRUNC":
            v = _numeric([args[0]])[0]
            d = int(C.to_number(args[1], 0)) if len(args) > 1 else 0
            return float(int(v * 10 ** d) / 10 ** d)
        if name == "EXP":
            return math.exp(_numeric([args[0]])[0])
        if name == "LN":
            v = _numeric([args[0]])[0]
            return math.log(v) if v > 0 else "#NUM!"
        if name == "LOG":
            v = _numeric([args[0]])[0]
            b = _numeric([args[1]])[0] if len(args) > 1 else 10.0
            return math.log(v, b) if v > 0 else "#NUM!"
        if name in {"CONCAT", "CONCATENATE"}:
            return "".join(_text(a) for a in _flatten(args))
        if name == "LEN":
            return float(len(_text(args[0])))
        if name == "LEFT":
            s = _text(args[0])
            n = int(C.to_number(args[1], 1)) if len(args) > 1 else 1
            return s[:n]
        if name == "RIGHT":
            s = _text(args[0])
            n = int(C.to_number(args[1], 1)) if len(args) > 1 else 1
            return s[-n:] if n else ""
        if name == "MID":
            s = _text(args[0])
            start = int(C.to_number(args[1], 1))
            n = int(C.to_number(args[2], 1))
            return s[start - 1: start - 1 + n]
        if name == "UPPER":
            return _text(args[0]).upper()
        if name == "LOWER":
            return _text(args[0]).lower()
        if name == "TRIM":
            return re.sub(r"\s+", " ", _text(args[0])).strip()
        if name == "VALUE":
            n = C.to_number(args[0])
            return n if n is not None else "#VALUE!"
        if name == "TEXT":
            v = args[0]
            if isinstance(v, (int, float)):
                if len(args) > 1 and "0.00%" in str(args[1]):
                    return f"{v * 100:.2f}%"
                if len(args) > 1 and re.fullmatch(r"[#,0]+(\.0+)?", str(args[1])):
                    decimals = len(str(args[1]).split(".")[1]) if "." in str(args[1]) else 0
                    return f"{v:,.{decimals}f}"
            return _text(v)
        if name == "TODAY":
            return date.today().isoformat()
        if name == "DATE":
            y, m, d = (int(C.to_number(a, 1)) for a in args[:3])
            return date(y, m, d).isoformat()
        if name == "YEAR":
            return float(str(args[0])[:4])
        if name == "MONTH":
            parsed = C.parse_date(args[0])
            return float(parsed.month) if parsed else "#VALUE!"
        if name == "DAY":
            parsed = C.parse_date(args[0])
            return float(parsed.day) if parsed else "#VALUE!"
        if name == "NPV":
            return _f_npv(_numeric([args[0]])[0], *args[1:])
        if name == "IRR":
            return _f_irr(args[0], *args[1:])
        if name == "PMT":
            vals = [_numeric([a])[0] for a in args]
            return _f_pmt(*vals)
        if name == "FV":
            vals = [_numeric([a])[0] for a in args]
            return _f_fv(*vals)
        if name == "PV":
            vals = [_numeric([a])[0] for a in args]
            return _f_pv(*vals)
        if name == "SLN":
            cost, salvage, life = (_numeric([a])[0] for a in args[:3])
            return (cost - salvage) / life if life else "#DIV/0!"
        if name == "RATE":
            return "#N/A"
    except SkillToolError:
        raise
    except ZeroDivisionError:
        return "#DIV/0!"
    except (TypeError, ValueError, IndexError, OverflowError):
        return "#VALUE!"
    return "#NAME?"


def evaluate_formula(formula: str, cells=None, names=None):
    ast = parse_formula(formula)
    env = Env(cells=cells, names=names)
    value = evaluate(ast, env)
    if isinstance(value, list):
        value = _flatten([value])
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return {"value": value, "warnings": env.errors}


# --------------------------------------------------------------------------
# explanation
# --------------------------------------------------------------------------


def explain_formula(formula: str, names=None) -> dict:
    deps = dependencies(formula, names=names)
    ast = deps["ast"]
    steps = []

    def describe(node, depth=0):
        tag = node[0]
        pad = "  " * depth
        if tag == "num":
            steps.append(f"{pad}constant {node[1]:g}")
        elif tag == "str":
            steps.append(f'{pad}text "{node[1]}"')
        elif tag == "ref":
            steps.append(f"{pad}read cell/range {node[1]}")
        elif tag == "name":
            steps.append(f"{pad}read defined name {node[1]}")
        elif tag == "unary":
            steps.append(f"{pad}negate/apply sign")
            describe(node[2], depth + 1)
        elif tag == "bin":
            opname = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide", "^": "raise to power",
                      "&": "join text", "=": "compare equality", "<>": "compare inequality",
                      "<": "compare less-than", ">": "compare greater-than",
                      "<=": "compare less-or-equal", ">=": "compare greater-or-equal"}.get(node[1], node[1])
            steps.append(f"{pad}{opname}")
            describe(node[2], depth + 1)
            describe(node[3], depth + 1)
        elif tag == "call":
            steps.append(f"{pad}call {node[1]}() with {len(node[2])} argument(s)")
            for a in node[2]:
                describe(a, depth + 1)

    describe(ast)
    risks = []
    if deps["volatile_functions"]:
        risks.append("Volatile function(s) " + ", ".join(deps["volatile_functions"]) + ": results change on recalculation or open.")
    if deps["unknown_functions"]:
        risks.append("Unknown function(s) for this engine: " + ", ".join(deps["unknown_functions"]) + ".")
    if any(f in {"VLOOKUP", "MATCH", "INDEX"} for f in deps["functions"]):
        risks.append("Lookup functions are position-sensitive: inserting columns/rows can silently change results.")
    if any(op in formula for op in ["/"]):
        risks.append("Division present: confirm denominators cannot be zero (guard with IFERROR or IF).")
    if re.search(r"#[A-Z/0!?]+", formula):
        risks.append("Formula contains a literal error value.")
    plain = formula.strip()
    if plain.startswith("="):
        plain = plain[1:]
    top = ast if isinstance(ast, tuple) else None
    if top and top[0] == "call":
        summary = f"Computes {top[1]}(...) over {len(deps['cells'])} referenced cell(s)."
    elif top and top[1] == "/" and top[0] == "bin":
        summary = "Divides one quantity by another; the result is a ratio."
    elif top and top[1] in {"+", "-"} and top[0] == "bin":
        summary = "Adds or subtracts quantities to produce a total or difference."
    else:
        summary = "Evaluates the expression to a single value."
    return {
        "formula": formula,
        "summary": summary,
        "steps": steps,
        "precedent_cells": deps["cells"],
        "precedent_ranges": deps["ranges"],
        "functions": deps["functions"],
        "names_used": deps["names_used"],
        "risk_notes": risks,
        "safe_patterns": [
            "Wrap divisions: =IFERROR(numerator/denominator, 0)",
            "Prefer structured table references for lookups that must survive column inserts",
            "Replace volatile TODAY() with an explicit as-of input cell for auditable models",
        ],
    }


# --------------------------------------------------------------------------
# workbook scanning (errors, hardcodes, name integrity)
# --------------------------------------------------------------------------


def scan_workbook(path, max_findings=500):
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), data_only=False)
    error_strings = []
    broken_names = []
    circular = []
    volatile = []
    formulas = 0
    unknown_funcs = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                if v.startswith("="):
                    formulas += 1
                    found = re.findall(r"#(REF!|VALUE!|N/A|DIV/0!|NAME\?|NUM!|SPILL!|NULL!|CALC!)", v)
                    if found:
                        error_strings.append({"sheet": ws.title, "cell": cell.coordinate, "error": found[0], "formula": v[:200]})
                    if re.search(r"\[\d+\]", v):
                        broken_names.append({"sheet": ws.title, "cell": cell.coordinate, "detail": "external workbook index reference", "formula": v[:200]})
                    try:
                        deps = dependencies(v)
                        for f in deps["unknown_functions"]:
                            unknown_funcs.setdefault(f, []).append(f"{ws.title}!{cell.coordinate}")
                        for f in deps["volatile_functions"]:
                            volatile.append({"sheet": ws.title, "cell": cell.coordinate, "function": f})
                        graph_key = f"{ws.title}!{cell.coordinate}"
                        if graph_key in deps["cells"]:
                            circular.append({"sheet": ws.title, "cell": cell.coordinate, "detail": "self-reference"})
                    except SkillToolError as exc:
                        unknown_funcs.setdefault("__parse_error__", []).append(f"{ws.title}!{cell.coordinate}: {exc.message}")
                elif v in ERROR_VALUES:
                    error_strings.append({"sheet": ws.title, "cell": cell.coordinate, "error": v, "formula": None})
    names = []
    for name, dn in getattr(wb, "defined_names", {}).items():
        names.append({"name": name, "value": str(dn.value)})
    wb.close()
    return {
        "formula_cells": formulas,
        "error_cells": error_strings[:max_findings],
        "error_count": len(error_strings),
        "external_reference_cells": broken_names[:max_findings],
        "self_reference_cells": circular[:max_findings],
        "volatile_cells": volatile[:max_findings],
        "unknown_functions": {k: v[:20] for k, v in unknown_funcs.items()},
        "defined_names": names,
        "verdict": "clean" if not (error_strings or broken_names or circular) else "issues_found",
    }


def hardcode_scan(path, max_findings=300):
    """Find constants sitting inside otherwise-formula rows/columns (override risk)."""
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), data_only=False)
    findings = []
    for ws in wb.worksheets:
        col_formula_count = {}
        cells = {}
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    col_formula_count[cell.column_letter] = col_formula_count.get(cell.column_letter, 0) + 1
                    cells[cell.coordinate] = cell
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    cells[cell.coordinate] = cell
        for coord, cell in cells.items():
            v = cell.value
            if isinstance(v, str) and v.startswith("="):
                continue
            if col_formula_count.get(cell.column_letter, 0) >= 3:
                findings.append({
                    "sheet": ws.title,
                    "cell": coord,
                    "value": v,
                    "reason": "numeric constant in a column where most cells are formulas",
                    "column_formula_cells": col_formula_count[cell.column_letter],
                })
    wb.close()
    return {"findings": findings[:max_findings], "count": len(findings)}


def diff_formula_patterns(path):
    """Detect inconsistent formula patterns within a column (classic copy/paste error)."""
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), data_only=False)
    issues = []
    for ws in wb.worksheets:
        by_col = {}
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    pattern = re.sub(r"\$?[A-Z]{1,3}\$?\d+", "<R>", v)
                    pattern = re.sub(r"-?\d+(?:\.\d+)?", "<N>", pattern)
                    by_col.setdefault(cell.column_letter, {}).setdefault(pattern, []).append(cell.coordinate)
        for col, pats in by_col.items():
            if len(pats) > 1:
                biggest = max(pats.items(), key=lambda kv: len(kv[1]))
                outliers = [c for p, coords in pats.items() if p != biggest[0] for c in coords]
                if outliers:
                    issues.append({"sheet": ws.title, "column": col,
                                   "dominant_pattern": biggest[0],
                                   "outlier_cells": outliers[:20],
                                   "note": "Formula shape differs from the dominant pattern in this column."})
    wb.close()
    return {"issues": issues, "count": len(issues)}
