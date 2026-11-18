"""Quantitative and scoring handlers.

Families: Q (quantitative: expressions, statistics, Bayes, Monte Carlo, sensitivity,
forecasting, calibration, dimensional checks, allocation, and more) and
P (scoring: weighted scores, matrices, risk registers, consensus, gates).
Expressions are parsed with the ast module and evaluated by a whitelist walker;
no eval() or exec() is used. Loaded by tools-engine.py; do not run directly.
"""
import ast
import datetime
import importlib.util
import math
import operator
import random
import re
import struct
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from itertools import product
from pathlib import Path
from statistics import NormalDist

TOOL_DIR = Path(__file__).resolve().parent
HANDLERS = {}


def _load_core():
    name = "tools_core_shared"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL_DIR / "tools-core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def handler(key):
    def register(fn):
        HANDLERS[key] = fn
        return fn
    return register


def num(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise core.InputError(f"{label} must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise core.InputError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise core.InputError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise core.InputError(f"{label} must be <= {maximum}")
    return value


def need_dict(data, label):
    if not isinstance(data, dict):
        raise core.InputError(f"{label} input must be a JSON object")
    return data


def number_list(raw, label):
    if not isinstance(raw, list) or not raw:
        raise core.InputError(f"{label} must be a non-empty list of numbers")
    return [num(v, f"{label}[{i}]") for i, v in enumerate(raw)]


def numbers_from_input(ctx):
    if ctx.input_path is not None and ctx.input_path.suffix.lower() == ".json":
        data = ctx.read_json()
        if isinstance(data, dict):
            data = data.get("values")
        return number_list(data, "values")
    values = []
    for line in ctx.read_text().splitlines():
        for part in re.split(r"[,\s]+", line.strip()):
            if part:
                try:
                    values.append(float(part))
                except ValueError as exc:
                    raise core.InputError(f"not a number in input: {part!r}") from exc
    if not values:
        raise core.InputError("input contains no numbers")
    return values


# ====================================================================== expressions
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
          "abs": abs, "min": min, "max": max, "round": round, "floor": math.floor,
          "ceil": math.ceil, "sin": math.sin, "cos": math.cos, "tan": math.tan, "pow": pow}
_CONSTS = {"pi": math.pi, "e": math.e}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod}
_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
                  ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd)


class Expr:
    """A validated arithmetic expression. Variables are looked up in a dict; functions are whitelisted."""

    def __init__(self, source, policy):
        if not isinstance(source, str) or not source.strip():
            raise core.InputError("expression must be a non-empty string")
        if len(source) > policy["limits"]["max_expression_chars"]:
            raise core.InputError("expression is too long")
        try:
            self.tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError as exc:
            raise core.InputError(f"expression syntax error in {source!r}: {exc.msg}") from exc
        self.source = source.strip()
        self.max_power = policy["limits"]["max_power_exponent"]
        self.names = set()
        self._validate(self.tree)

    def _validate(self, root):
        for node in ast.walk(root):
            if not isinstance(node, _ALLOWED_NODES):
                raise core.InputError(f"unsupported syntax in expression: {type(node).__name__}")
            if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float))):
                raise core.InputError("only numeric constants are allowed in expressions")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                    raise core.InputError("only the functions " + ", ".join(sorted(_FUNCS)) + " are allowed")
            if isinstance(node, ast.Name) and node.id not in _FUNCS and node.id not in _CONSTS:
                self.names.add(node.id)
        for node in ast.walk(root):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
                if isinstance(node.right, ast.Constant) and abs(node.right.value) > self.max_power:
                    raise core.InputError("exponent exceeds max_power_exponent")

    def evaluate(self, env):
        def ev(node):
            if isinstance(node, ast.Expression):
                return ev(node.body)
            if isinstance(node, ast.Constant):
                return float(node.value)
            if isinstance(node, ast.Name):
                if node.id in _CONSTS:
                    return _CONSTS[node.id]
                if node.id not in env:
                    raise core.InputError(f"variable {node.id!r} has no value")
                return num(env[node.id], f"variable {node.id}")
            if isinstance(node, ast.UnaryOp):
                val = ev(node.operand)
                return -val if isinstance(node.op, ast.USub) else val
            if isinstance(node, ast.BinOp):
                left, right = ev(node.left), ev(node.right)
                if isinstance(node.op, ast.Pow):
                    try:
                        result = left ** right
                    except (OverflowError, ZeroDivisionError) as exc:
                        raise core.InputError(f"power error in {self.source!r}: {exc}") from exc
                    if isinstance(result, complex):
                        raise core.InputError("expression produced a complex number (negative base with fractional power)")
                    return float(result)
                if isinstance(node.op, ast.Div) and right == 0:
                    raise core.InputError(f"division by zero in {self.source!r}")
                if isinstance(node.op, ast.Mod) and right == 0:
                    raise core.InputError(f"modulo by zero in {self.source!r}")
                return float(_BIN[type(node.op)](left, right))
            if isinstance(node, ast.Call):
                args = [ev(a) for a in node.args]
                try:
                    return float(_FUNCS[node.func.id](*args))
                except (ValueError, OverflowError, TypeError) as exc:
                    raise core.InputError(f"function {node.func.id} failed: {exc}") from exc
            raise core.InputError("unsupported expression node")

        value = ev(self.tree.body)
        if not math.isfinite(value):
            raise core.InputError(f"expression {self.source!r} produced a non-finite value")
        return value


def _env_from(data, key="variables"):
    env = data.get(key, {})
    if not isinstance(env, dict):
        raise core.InputError(f"{key} must be an object of name -> number")
    return {k: num(v, f"{key}.{k}") for k, v in env.items()}


# ---- dimensional analysis --------------------------------------------------
def parse_unit(spec, table):
    spec = spec.strip()
    if spec in table:
        return dict(table[spec])
    dims = defaultdict(float)
    tokens = re.split(r"([*/])", spec)
    next_sign = 1
    for tok in tokens:
        tok = tok.strip()
        if tok == "*":
            next_sign = 1
            continue
        if tok == "/":
            next_sign = -1
            continue
        if tok in ("", "1"):
            next_sign = 1
            continue
        m = re.fullmatch(r"([A-Za-z%]+)(?:\^(-?\d+))?", tok)
        if not m or m.group(1) not in table:
            raise core.InputError(f"unknown unit {tok!r}; allowed: " + ", ".join(sorted(table)))
        exponent = int(m.group(2)) if m.group(2) else 1
        for base, power in table[m.group(1)].items():
            dims[base] += next_sign * exponent * power
        next_sign = 1
    return {k: v for k, v in dims.items() if abs(v) > 1e-12}


def dims_text(dims, table):
    for name, vec in table.items():
        if {k: float(v) for k, v in vec.items()} == {k: float(v) for k, v in dims.items()}:
            return name
    if not dims:
        return "dimensionless"
    symbols = {"M": "kg", "L": "m", "T": "s", "Th": "K", "C": "currency"}
    parts = []
    for base in sorted(dims):
        exp = dims[base]
        parts.append(symbols.get(base, base) + ("" if exp == 1 else f"^{exp:g}"))
    return "*".join(parts)


def dimension_of(node, units, issues, warnings):
    if isinstance(node, ast.Expression):
        return dimension_of(node.body, units, issues, warnings)
    if isinstance(node, ast.Constant):
        return {}
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return {}
        if node.id not in units:
            warnings.append(f"variable {node.id} has no unit; treated as dimensionless")
            return {}
        return dict(units[node.id])
    if isinstance(node, ast.UnaryOp):
        return dimension_of(node.operand, units, issues, warnings)
    if isinstance(node, ast.BinOp):
        left = dimension_of(node.left, units, issues, warnings)
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
            right = dimension_of(node.right, units, issues, warnings)
            if {k: v for k, v in left.items() if v} != {k: v for k, v in right.items() if v}:
                issues.append("addition, subtraction, or modulo mixes different dimensions")
            return left
        if isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant):
                return {k: v * node.right.value for k, v in left.items()}
            issues.append("non-constant exponent makes the dimension undefined")
            return {}
        right = dimension_of(node.right, units, issues, warnings)
        out = defaultdict(float)
        sign = 1 if isinstance(node.op, ast.Mult) else -1
        for k, v in left.items():
            out[k] += v
        for k, v in right.items():
            out[k] += sign * v
        return dict(out)
    if isinstance(node, ast.Call):
        name = node.func.id
        args = [dimension_of(a, units, issues, warnings) for a in node.args]
        if name in ("sqrt",):
            return {k: v / 2 for k, v in args[0].items()}
        if name in ("abs", "round", "floor", "ceil"):
            return args[0]
        if name in ("min", "max"):
            if any(a != args[0] for a in args):
                issues.append(f"{name}() compares different dimensions")
            return args[0]
        if name == "pow":
            return {k: v * (node.args[1].value if isinstance(node.args[1], ast.Constant) else 0) for k, v in args[0].items()}
        if any(v for a in args for v in a.values()):
            issues.append(f"{name}() needs a dimensionless argument")
        return {}
    return {}


# ====================================================================== P: scoring
def _dimensions(data):
    dims = data.get("dimensions") if isinstance(data, dict) else None
    if not isinstance(dims, list) or not dims:
        raise core.InputError("input needs a non-empty 'dimensions' list")
    out = []
    names = set()
    for n, raw in enumerate(dims, start=1):
        if not isinstance(raw, dict) or "name" not in raw or "score" not in raw:
            raise core.InputError(f"dimension {n} needs 'name' and 'score'")
        name = str(raw["name"])
        if name in names:
            raise core.InputError(f"duplicate dimension {name!r}")
        names.add(name)
        lo = num(raw.get("min", 0), f"min of {name}")
        hi = num(raw.get("max", 5), f"max of {name}")
        if hi <= lo:
            raise core.InputError(f"max must be greater than min for {name}")
        score = num(raw["score"], f"score of {name}")
        norm = min(1.0, max(0.0, (score - lo) / (hi - lo)))
        out.append({"name": name, "score": score, "norm": norm, "weight": num(raw.get("weight", 1), f"weight of {name}", 0),
                    "critical": bool(raw.get("critical", False)), "min": lo, "max": hi})
    if sum(d["weight"] for d in out) <= 0:
        raise core.InputError("at least one dimension needs a positive weight")
    return out


@handler("P:score")
def p_score(ctx):
    data = need_dict(ctx.read_json(), "score")
    dims = _dimensions(data)
    d = ctx.policy["defaults"]
    crit_below = ctx.opt_float("critical_below", d["critical_below"], 0, 1)
    pass_thr = ctx.opt_float("pass_threshold", d["pass_threshold"], 0, 1)
    weak_thr = d["weak_threshold"]
    total_w = sum(x["weight"] for x in dims)
    avg = sum(x["weight"] * x["norm"] for x in dims) / total_w
    critical_fail = [x["name"] for x in dims if x["critical"] and x["norm"] < crit_below]
    weakest = min(dims, key=lambda x: x["norm"])
    if critical_fail:
        verdict = "BLOCK"
    elif avg >= pass_thr and weakest["norm"] >= weak_thr:
        verdict = "PASS"
    else:
        verdict = "REVISE"
    override = bool(critical_fail) and avg >= pass_thr
    items = [{"dimension": x["name"], "score": x["score"], "normalized": round(x["norm"], 3),
              "weight": x["weight"], "critical": "yes" if x["critical"] else "",
              "status": "critical failure" if x["name"] in critical_fail else ("weak" if x["norm"] < weak_thr else "ok")}
             for x in dims]
    summary = {"weighted_average": round(avg, 3), "weakest": weakest["name"], "critical_failures": len(critical_fail),
               "verdict": verdict, "pass_threshold": pass_thr}
    result = {"summary": summary, "items": items}
    if override:
        result["note"] = "The average would pass, but a critical dimension failed. Critical failures override the average."
    return result


def _matrix(data, label):
    criteria = data.get("criteria")
    alts = data.get("alternatives")
    if not isinstance(criteria, dict) or not criteria or not isinstance(alts, dict) or not alts:
        raise core.InputError(f"{label} needs 'criteria' (name -> weight) and 'alternatives' (name -> {{criterion: score}})")
    weights = {k: num(v, f"weight {k}", 0) for k, v in criteria.items()}
    wsum = sum(weights.values())
    if wsum <= 0:
        raise core.InputError("criteria weights must sum to a positive number")
    scale = num(data.get("scale", 1), "scale", 0.000001)
    results = {}
    missing_report = {}
    for alt, scores in alts.items():
        if not isinstance(scores, dict):
            raise core.InputError(f"scores for {alt!r} must be an object")
        total, missing = 0.0, []
        for crit, w in weights.items():
            if crit not in scores:
                missing.append(crit)
                continue
            total += (w / wsum) * (num(scores[crit], f"score {alt}/{crit}") / scale)
        results[alt] = total
        missing_report[alt] = missing
    return weights, wsum, results, missing_report


@handler("P:matrix")
def p_matrix(ctx):
    data = need_dict(ctx.read_json(), "matrix")
    _, _, results, missing = _matrix(data, "matrix")
    ranked = sorted(results.items(), key=lambda kv: (-kv[1], kv[0]))
    items = [{"rank": i, "alternative": a, "weighted_score": round(s, 4),
              "missing_criteria": ", ".join(missing[a])} for i, (a, s) in enumerate(ranked, start=1)]
    warnings = [f"{a} is missing {len(m)} criteria (counted as 0)" for a, m in missing.items() if m]
    ctx.warnings.extend(warnings)
    return {"summary": {"alternatives": len(items), "top": ranked[0][0] if ranked else "",
                        "scale": data.get("scale", 1)}, "items": items}


@handler("P:weight-sensitivity")
def p_weight_sensitivity(ctx):
    data = need_dict(ctx.read_json(), "weight-sensitivity")
    weights, _, base, _ = _matrix(data, "weight-sensitivity")
    delta = ctx.opt_float("delta", ctx.policy["defaults"]["fragility_delta"], 0.0001, 1)
    base_top = sorted(base.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    flips, items = [], []
    for crit in weights:
        for sign, label in ((-1, "-"), (1, "+")):
            ctx.tick()
            changed = dict(data)
            changed["criteria"] = dict(weights)
            changed["criteria"][crit] = weights[crit] * (1 + sign * delta)
            _, _, res, _ = _matrix(changed, "weight-sensitivity")
            top = sorted(res.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            flipped = top != base_top
            items.append({"criterion": crit, "change": f"{label}{int(delta * 100)}%", "top": top,
                          "flipped": "yes" if flipped else ""})
            if flipped:
                flips.append(crit)
    return {"summary": {"base_top": base_top, "delta": delta, "flips": len(flips),
                        "verdict": "fragile: top choice changes under small weight changes" if flips else "robust to this weight change"},
            "items": items}


@handler("P:pareto")
def p_pareto(ctx):
    data = need_dict(ctx.read_json(), "pareto")
    alts = data.get("alternatives")
    if not isinstance(alts, dict) or not alts:
        raise core.InputError("pareto needs 'alternatives' (name -> {criterion: score}); higher is better")
    items = []
    for a, sa in alts.items():
        dominators = []
        for b, sb in alts.items():
            if a == b:
                continue
            shared = [c for c in sa if c in sb]
            if not shared:
                continue
            ge = all(num(sb[c], f"{b}/{c}") >= num(sa[c], f"{a}/{c}") for c in shared)
            gt = any(num(sb[c], f"{b}/{c}") > num(sa[c], f"{a}/{c}") for c in shared)
            if ge and gt:
                dominators.append(b)
        items.append({"alternative": a, "status": "dominated by " + ", ".join(dominators) if dominators else "non-dominated",
                      "frontier": "" if dominators else "yes"})
    front = sum(1 for i in items if i["frontier"])
    return {"summary": {"alternatives": len(items), "non_dominated": front}, "items": items}


@handler("P:risk")
def p_risk(ctx):
    data = ctx.read_json()
    if isinstance(data, dict):
        data = data.get("risks")
    if not isinstance(data, list) or not data:
        raise core.InputError("risk input must be a non-empty list or an object with 'risks'")
    high = ctx.policy["defaults"]["risk_high_score"]
    medium = ctx.policy["defaults"]["risk_medium_score"]
    items = []
    for n, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            raise core.InputError(f"risk {n} must be an object")
        if "severity" in raw:
            factors = [num(raw["severity"], "severity", 1, 10), num(raw.get("occurrence", 1), "occurrence", 1, 10),
                       num(raw.get("detection", 1), "detection", 1, 10)]
            method = "severity x occurrence x detection"
        else:
            factors = [num(raw.get("likelihood", 1), "likelihood", 1, 10), num(raw.get("impact", 1), "impact", 1, 10),
                       num(raw.get("detectability", 1), "detectability", 1, 10)]
            method = "likelihood x impact x detectability"
        score = factors[0] * factors[1] * factors[2]
        level = "high" if score >= high else ("medium" if score >= medium else "low")
        items.append({"id": str(raw.get("id", n)), "score": score, "level": level,
                      "owner": raw.get("owner", ""), "mitigation": raw.get("mitigation", ""),
                      "trigger": raw.get("trigger", ""), "method": method})
    items.sort(key=lambda x: (-x["score"], x["id"]))
    counts = Counter(i["level"] for i in items)
    return {"summary": {"risks": len(items), "high": counts.get("high", 0), "medium": counts.get("medium", 0),
                        "low": counts.get("low", 0)}, "items": items}


@handler("P:consensus")
def p_consensus(ctx):
    data = need_dict(ctx.read_json(), "consensus")
    votes_all = data.get("items")
    if not isinstance(votes_all, dict) or not votes_all:
        raise core.InputError("consensus needs 'items': {item: {reviewer: agree|disagree|abstain}}")
    threshold = ctx.policy["defaults"]["consensus_min"]
    items = []
    for item, votes in votes_all.items():
        if not isinstance(votes, dict):
            raise core.InputError(f"votes for {item!r} must be an object")
        for reviewer, v in votes.items():
            if v not in ("agree", "disagree", "abstain"):
                raise core.InputError(f"vote of {reviewer} on {item} must be agree, disagree, or abstain")
        agree = [r for r, v in votes.items() if v == "agree"]
        disagree = [r for r, v in votes.items() if v == "disagree"]
        decided = len(agree) + len(disagree)
        share = len(agree) / decided if decided else None
        if share is None:
            status = "no decisive votes"
        elif share >= threshold:
            status = "consensus"
        elif share > 0.5:
            status = "majority; record dissent"
        else:
            status = "contested"
        items.append({"item": item, "agreement": "" if share is None else round(share, 2), "status": status,
                      "dissent": ", ".join(disagree)})
    counts = Counter(i["status"] for i in items)
    return {"summary": {"items": len(items), "consensus": counts.get("consensus", 0),
                        "contested": counts.get("contested", 0)}, "items": items}


@handler("P:portfolio")
def p_portfolio(ctx):
    data = need_dict(ctx.read_json(), "portfolio")
    alloc = data.get("allocations")
    if not isinstance(alloc, dict) or not alloc:
        raise core.InputError("portfolio needs 'allocations' (name -> percent)")
    values = {k: num(v, f"allocation {k}", 0, 100) for k, v in alloc.items()}
    total = sum(values.values())
    cap = ctx.opt_float("max_share", ctx.policy["defaults"]["portfolio_max_share"], 0, 100)
    items = []
    for k, v in sorted(values.items(), key=lambda kv: -kv[1]):
        flag = []
        if v > cap:
            flag.append(f"over {cap:g}%")
        if v == 0:
            flag.append("unfunded")
        items.append({"item": k, "percent": v, "flag": "; ".join(flag)})
    if abs(total - 100) > 0.5:
        ctx.warnings.append(f"allocations sum to {total:g}%, not 100%")
    return {"summary": {"total_percent": total, "largest": max(values, key=values.get),
                        "flags": sum(1 for i in items if i["flag"])}, "items": items}


@handler("P:weakest")
def p_weakest(ctx):
    dims = _dimensions(need_dict(ctx.read_json(), "weakest"))
    pass_thr = ctx.policy["defaults"]["pass_threshold"]
    ordered = sorted(dims, key=lambda x: x["norm"])[:3]
    items = []
    for x in ordered:
        target = x["min"] + pass_thr * (x["max"] - x["min"])
        gap = max(0.0, target - x["score"])
        items.append({"dimension": x["name"], "normalized": round(x["norm"], 3),
                      "repair": f"raise from {x['score']:g} toward {target:g} (gap {gap:g})" if gap > 0 else "already at target"})
    return {"summary": {"weakest": ordered[0]["name"], "target_threshold": pass_thr}, "items": items,
            "note": "Work on the first item; re-score after each repair."}


@handler("P:gate")
def p_gate(ctx):
    data = need_dict(ctx.read_json(), "gate")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise core.InputError("gate needs a non-empty 'checks' list of {name, result, critical}")
    items = []
    for n, c in enumerate(checks, start=1):
        if not isinstance(c, dict) or "result" not in c:
            raise core.InputError(f"check {n} needs 'result' (pass, warn, or fail)")
        if c["result"] not in ("pass", "warn", "fail"):
            raise core.InputError(f"check {n} result must be pass, warn, or fail")
        items.append({"check": str(c.get("name", n)), "result": c["result"],
                      "critical": "yes" if c.get("critical") else ""})
    blocking = [i for i in items if i["result"] == "fail" and i["critical"]]
    if blocking:
        verdict = "BLOCK"
    elif any(i["result"] in ("fail", "warn") for i in items):
        verdict = "REVISE"
    else:
        verdict = "PASS"
    return {"summary": {"verdict": verdict, "failed": sum(1 for i in items if i["result"] == "fail"),
                        "warned": sum(1 for i in items if i["result"] == "warn"),
                        "critical_failed": len(blocking)}, "items": items}


@handler("P:compare")
def p_compare(ctx):
    base = _dimensions(need_dict(ctx.read_json(), "compare"))
    other_path = ctx.opt("other")
    if not other_path:
        raise core.InputError("option other=<path to the newer score JSON> is required")
    newer = _dimensions(core.read_json_file(Path(other_path), ctx.policy))
    new_map = {x["name"]: x for x in newer}
    items, regressed = [], 0
    for x in base:
        if x["name"] not in new_map:
            items.append({"dimension": x["name"], "change": "missing in newer scores", "delta": ""})
            continue
        delta = new_map[x["name"]]["norm"] - x["norm"]
        if delta < -1e-9:
            regressed += 1
        items.append({"dimension": x["name"], "before": round(x["norm"], 3), "after": round(new_map[x["name"]]["norm"], 3),
                      "delta": round(delta, 3), "change": "regressed" if delta < -1e-9 else ("improved" if delta > 1e-9 else "unchanged")})
    return {"summary": {"dimensions": len(items), "regressed": regressed,
                        "verdict": "regression detected" if regressed else "no regression"}, "items": items}


# ====================================================================== Q: quantitative
@handler("Q:evaluate")
def q_evaluate(ctx):
    data = need_dict(ctx.read_json(), "evaluate")
    expr = Expr(data.get("expression"), ctx.policy)
    env = _env_from(data)
    missing = sorted(expr.names - set(env))
    if missing:
        raise core.InputError(f"missing variables: {', '.join(missing)}")
    value = expr.evaluate(env)
    return {"summary": {"expression": expr.source, "value": value}, "items": []}


_T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
         2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


@handler("Q:stats")
def q_stats(ctx):
    values = numbers_from_input(ctx)
    n = len(values)
    mean = sum(values) / n
    s = sorted(values)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    if n > 1:
        t = _T975[n - 2] if n - 1 <= len(_T975) else ctx.policy["defaults"]["confidence_z"]
        half = t * sd / math.sqrt(n)
        ci = [round(mean - half, 6), round(mean + half, 6)]
    else:
        ci = None
    cv = (sd / mean) if mean else None
    summary = {"n": n, "mean": round(mean, 6), "median": round(median, 6), "sd_sample": round(sd, 6),
               "min": s[0], "max": s[-1], "ci95_mean": ci,
               "coefficient_of_variation": None if cv is None else round(cv, 4)}
    note = "Small samples give wide intervals; check the data design before drawing conclusions." if n < 30 else ""
    return {"summary": summary, "items": [], "note": note}


@handler("Q:bayes")
def q_bayes(ctx):
    data = need_dict(ctx.read_json(), "bayes")
    prior = num(data.get("prior"), "prior", 0, 1)
    if prior in (0.0, 1.0):
        raise core.InputError("prior must be strictly between 0 and 1")
    if "likelihood_ratios" in data:
        lrs = [num(v, "likelihood ratio", 0.000001) for v in data["likelihood_ratios"]]
    else:
        sens = num(data.get("sensitivity"), "sensitivity", 0, 1)
        fp = num(data.get("false_positive"), "false_positive", 0.000001, 1)
        lrs = [sens / fp]
    odds = prior / (1 - prior)
    items = [{"step": 0, "label": "prior", "probability": round(prior, 6)}]
    for i, lr in enumerate(lrs, start=1):
        odds *= lr
        items.append({"step": i, "label": f"evidence {i} (LR={lr:.4g})", "probability": round(odds / (1 + odds), 6)})
    posterior = odds / (1 + odds)
    warn = "base rate is low; a positive result alone may not be conclusive" if prior < 0.05 else ""
    return {"summary": {"prior": prior, "posterior": round(posterior, 6), "posterior_pct": round(posterior * 100, 2),
                        "base_rate_warning": warn}, "items": items}


def _oom(x):
    return None if x == 0 else math.floor(math.log10(abs(x)))


@handler("Q:bounds")
def q_bounds(ctx):
    data = need_dict(ctx.read_json(), "bounds")
    if "expression" in data:
        expr = Expr(data["expression"], ctx.policy)
        ranges = data.get("ranges", {})
        if not isinstance(ranges, dict) or not ranges:
            raise core.InputError("bounds with an expression needs 'ranges' (name -> [low, high])")
        names = sorted(ranges)
        if len(names) > 12:
            raise core.InputError("at most 12 ranged variables (2^12 corners) are supported")
        results = []
        for corner in product(*[(num(ranges[k][0], f"low {k}"), num(ranges[k][1], f"high {k}")) for k in names]):
            ctx.tick()
            env = dict(zip(names, corner))
            results.append((expr.evaluate(env), env))
        lo = min(results, key=lambda r: r[0])
        hi = max(results, key=lambda r: r[0])
        summary = {"expression": expr.source, "min": lo[0], "max": hi[0], "min_at": lo[1], "max_at": hi[1],
                   "order_of_magnitude_min": _oom(lo[0]), "order_of_magnitude_max": _oom(hi[0]),
                   "corners_evaluated": len(results)}
        return {"summary": summary, "items": [], "note": "Extremes are taken at the corners of the ranges; use montecarlo for distributions."}
    low = num(data.get("low"), "low", 0.000001)
    high = num(data.get("high"), "high")
    if high < low:
        raise core.InputError("high must be >= low")
    ratio = high / low
    return {"summary": {"low": low, "high": high, "spread_ratio": round(ratio, 3),
                        "geometric_midpoint": round(math.sqrt(low * high), 6),
                        "order_of_magnitude_low": _oom(low), "order_of_magnitude_high": _oom(high),
                        "precision_note": "a spread above 10x means the estimate is order-of-magnitude only" if ratio > 10 else "the range is narrow enough for a point estimate with caveats"}, "items": []}


@handler("Q:pert")
def q_pert(ctx):
    data = need_dict(ctx.read_json(), "pert")
    tasks = data.get("items")
    if not isinstance(tasks, list) or not tasks:
        raise core.InputError("pert needs 'items': [{name, optimistic, likely, pessimistic}]")
    z80 = NormalDist().inv_cdf(0.8)
    items, means, variances = [], 0.0, 0.0
    for t in tasks:
        o, m, p = (num(t.get(k), f"{k} of {t.get('name')}", 0) for k in ("optimistic", "likely", "pessimistic"))
        if not (o <= m <= p):
            raise core.InputError(f"{t.get('name')}: need optimistic <= likely <= pessimistic")
        mean = (o + 4 * m + p) / 6.0
        sd = (p - o) / 6.0
        means += mean
        variances += sd * sd
        items.append({"name": t.get("name", ""), "mean": round(mean, 3), "sd": round(sd, 3),
                      "p80": round(mean + z80 * sd, 3)})
    total_sd = math.sqrt(variances)
    return {"summary": {"items": len(items), "total_mean": round(means, 3), "total_sd": round(total_sd, 3),
                        "total_p80": round(means + z80 * total_sd, 3)}, "items": items,
            "note": "PERT assumes independent items; the total spread is root-sum-of-squares."}


def _sample(rng, spec, name):
    kind = spec.get("type")
    if kind == "fixed":
        return num(spec["value"], f"{name}.value")
    if kind == "uniform":
        lo, hi = num(spec["low"], f"{name}.low"), num(spec["high"], f"{name}.high")
        if hi < lo:
            raise core.InputError(f"{name}: high must be >= low")
        return rng.uniform(lo, hi)
    if kind == "normal":
        return rng.gauss(num(spec["mean"], f"{name}.mean"), num(spec["sd"], f"{name}.sd", 0))
    if kind == "triangular":
        lo, mode, hi = (num(spec[k], f"{name}.{k}") for k in ("low", "mode", "high"))
        if not lo <= mode <= hi:
            raise core.InputError(f"{name}: need low <= mode <= high")
        return rng.triangular(lo, hi, mode)
    if kind == "lognormal":
        return rng.lognormvariate(num(spec["mu"], f"{name}.mu"), num(spec["sigma"], f"{name}.sigma", 0))
    raise core.InputError(f"{name}: unknown distribution {kind!r}; use fixed, uniform, normal, triangular, lognormal")


@handler("Q:montecarlo")
def q_montecarlo(ctx):
    data = need_dict(ctx.read_json(), "montecarlo")
    expr = Expr(data.get("expression"), ctx.policy)
    dists = data.get("distributions", {})
    if not isinstance(dists, dict) or not dists:
        raise core.InputError("montecarlo needs 'distributions' (name -> {type, ...})")
    missing = sorted(expr.names - set(dists))
    if missing:
        raise core.InputError(f"expression variables without distributions: {', '.join(missing)}")
    n = int(num(data.get("n", 10000), "n", 1, ctx.policy["limits"]["max_montecarlo_samples"]))
    seed = int(num(data.get("seed", 1), "seed"))
    pcts = data.get("percentiles", [5, 50, 95])
    rng = random.Random(seed)
    results = []
    for i in range(n):
        if i % 2000 == 0:
            ctx.tick()
        env = {k: _sample(rng, spec, k) for k, spec in dists.items()}
        results.append(expr.evaluate(env))
    results.sort()
    mean = sum(results) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in results) / (n - 1)) if n > 1 else 0.0

    def pct(p):
        idx = min(n - 1, max(0, int(math.ceil(p / 100.0 * n)) - 1))
        return results[idx]

    summary = {"n": n, "seed": seed, "mean": round(mean, 6), "sd": round(sd, 6)}
    items = []
    for p in pcts:
        p = num(p, "percentile", 0, 100)
        items.append({"percentile": p, "value": round(pct(p), 6)})
    if data.get("threshold") is not None:
        thr = num(data["threshold"], "threshold")
        summary["probability_above_threshold"] = round(sum(1 for r in results if r > thr) / n, 4)
        summary["threshold"] = thr
    summary["reproducibility"] = "same seed and inputs reproduce the same numbers"
    return {"summary": summary, "items": items}


@handler("Q:sensitivity")
def q_sensitivity(ctx):
    data = need_dict(ctx.read_json(), "sensitivity")
    expr = Expr(data.get("expression"), ctx.policy)
    base = _env_from(data, "base")
    ranges = data.get("ranges", {})
    delta = num(data.get("delta_pct", 10), "delta_pct", 0, 1000) / 100.0
    missing = sorted(expr.names - set(base))
    if missing:
        raise core.InputError(f"base values missing for: {', '.join(missing)}")
    base_val = expr.evaluate(base)
    items = []
    for var in sorted(base):
        if var not in expr.names:
            continue
        ctx.tick()
        if var in ranges:
            lo_v, hi_v = num(ranges[var][0], f"range {var}"), num(ranges[var][1], f"range {var}")
        else:
            lo_v, hi_v = base[var] * (1 - delta), base[var] * (1 + delta)
        lo_r = expr.evaluate({**base, var: lo_v})
        hi_r = expr.evaluate({**base, var: hi_v})
        items.append({"variable": var, "low_input": lo_v, "high_input": hi_v, "result_low": round(lo_r, 6),
                      "result_high": round(hi_r, 6), "swing": round(abs(hi_r - lo_r), 6)})
    items.sort(key=lambda x: (-x["swing"], x["variable"]))
    for i, it in enumerate(items, start=1):
        it["rank"] = i
    driver = items[0]["variable"] if items else ""
    return {"summary": {"expression": expr.source, "base_result": round(base_val, 6), "top_driver": driver},
            "items": items}


@handler("Q:crossvalidate")
def q_crossvalidate(ctx):
    data = need_dict(ctx.read_json(), "crossvalidate")
    tol = num(data.get("tolerance", 1e-6), "tolerance", 0)
    if "results" in data:
        results = data["results"]
        if not isinstance(results, list) or len(results) < 2:
            raise core.InputError("results must be a list of at least two {tool, value} objects")
        vals = [(str(r.get("tool", i)), num(r.get("value"), f"value of {r.get('tool', i)}")) for i, r in enumerate(results)]
        items = []
        for (na, va), (nb, vb) in [(a, b) for i, a in enumerate(vals) for b in vals[i + 1:]]:
            denom = max(abs(va), abs(vb), 1e-300)
            rel = abs(va - vb) / denom
            items.append({"pair": f"{na} vs {nb}", "relative_difference": f"{rel:.3g}",
                          "agree": "yes" if rel <= tol else "no - explain the disagreement"})
        disagree = sum(1 for i in items if i["agree"] != "yes")
        return {"summary": {"pairs": len(items), "disagreements": disagree}, "items": items,
                "note": "Disagreements are reported, not resolved silently."}
    ea = Expr(data.get("expression_a"), ctx.policy)
    eb = Expr(data.get("expression_b"), ctx.policy)
    env = _env_from(data)
    va, vb = ea.evaluate(env), eb.evaluate(env)
    rel = abs(va - vb) / max(abs(va), abs(vb), 1e-300)
    agree = rel <= tol
    return {"summary": {"value_a": va, "value_b": vb, "relative_difference": rel, "agree": agree}, "items": []}


def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


@handler("Q:stability")
def q_stability(ctx):
    data = need_dict(ctx.read_json(), "stability")
    expr = Expr(data.get("expression"), ctx.policy)
    env = _env_from(data)
    base = expr.evaluate(env)
    f32_env = {k: _f32(v) for k, v in env.items()}
    try:
        f32_val = expr.evaluate(f32_env)
    except core.InputError as exc:
        f32_val = float("nan")
        ctx.warnings.append(f"float32 evaluation failed: {exc}")
    rel32 = abs(f32_val - base) / max(abs(base), 1e-300) if f32_val == f32_val else float("nan")
    items = []
    worst = 0.0
    for var, val in sorted(env.items()):
        if var not in expr.names:
            continue
        ctx.tick()
        h = abs(val) * 1e-6 if val else 1e-6
        up = expr.evaluate({**env, var: val + h})
        down = expr.evaluate({**env, var: val - h})
        rel_change = abs(up - down) / (2 * h) * abs(val) / max(abs(base), 1e-300)
        worst = max(worst, rel_change)
        items.append({"variable": var, "relative_output_change_per_relative_input_change": f"{rel_change:.3g}"})
    flags = []
    if rel32 == rel32 and rel32 > 1e-4:
        flags.append("precision-sensitive: float32 changes the result by more than 0.01%")
    if worst > 1000:
        flags.append("ill-conditioned: small input changes produce large output changes")
    return {"summary": {"value_float64": base, "value_float32": f32_val, "relative_difference_f32": rel32,
                        "condition_estimate": round(worst, 6), "flags": "; ".join(flags) or "none"},
            "items": items}


@handler("Q:dimension")
def q_dimension(ctx):
    data = need_dict(ctx.read_json(), "dimension")
    table = ctx.policy["unit_dimensions"]
    units_raw = data.get("units", {})
    if not isinstance(units_raw, dict):
        raise core.InputError("units must map variable names to unit strings")
    units = {k: parse_unit(str(v), table) for k, v in units_raw.items()}
    expr = Expr(data.get("expression"), ctx.policy)
    issues, warnings = [], []
    dims = dimension_of(expr.tree, units, issues, warnings)
    dims = {k: v for k, v in dims.items() if abs(v) > 1e-12}
    result_text = dims_text(dims, table)
    expect = data.get("expect")
    expect_ok = None
    if expect is not None:
        expect_dims = parse_unit(str(expect), table)
        expect_ok = {k: round(v, 9) for k, v in expect_dims.items()} == {k: round(v, 9) for k, v in dims.items()}
        if not expect_ok:
            issues.append(f"result is {result_text}, expected {expect}")
    ctx.warnings.extend(warnings)
    items = [{"issue": i} for i in issues]
    return {"summary": {"expression": expr.source, "result_dimension": result_text,
                        "consistent": not issues, "expected_match": expect_ok},
            "items": items}


@handler("Q:ev")
def q_ev(ctx):
    data = need_dict(ctx.read_json(), "ev")
    outcomes = data.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        raise core.InputError("ev needs 'outcomes': [{name, probability, value}]")
    total_p = 0.0
    ev_total, downside = 0.0, 0.0
    items = []
    for o in outcomes:
        p = num(o.get("probability"), f"probability of {o.get('name')}", 0, 1)
        v = num(o.get("value"), f"value of {o.get('name')}")
        total_p += p
        ev_total += p * v
        downside += p * max(0.0, -v)
        items.append({"outcome": o.get("name", ""), "probability": p, "value": v, "contribution": round(p * v, 6)})
    if abs(total_p - 1.0) > 1e-6:
        raise core.InputError(f"probabilities must sum to 1; they sum to {total_p:g}")
    lam = ctx.opt_float("risk_aversion", ctx.policy["defaults"]["risk_aversion"], 0, 10)
    cost = num(data.get("cost", 0), "cost", 0)
    net = ev_total - cost
    return {"summary": {"expected_value": round(ev_total, 6), "expected_downside": round(downside, 6),
                        "risk_adjusted_value": round(ev_total - lam * downside, 6), "risk_aversion": lam,
                        "cost": cost, "net_after_cost": round(net, 6)}, "items": items,
            "note": "Speculative probabilities or values should be marked as assumptions in the decision record."}


@handler("Q:scenarios")
def q_scenarios(ctx):
    data = need_dict(ctx.read_json(), "scenarios")
    scen = data.get("scenarios")
    if not isinstance(scen, list) or not scen:
        raise core.InputError("scenarios needs a list of {name, probability, outcomes: {option: value}}")
    total_p = sum(num(s.get("probability"), f"probability of {s.get('name')}", 0, 1) for s in scen)
    if abs(total_p - 1.0) > 1e-6:
        raise core.InputError(f"scenario probabilities must sum to 1; they sum to {total_p:g}")
    options = sorted({o for s in scen for o in s.get("outcomes", {})})
    if not options:
        raise core.InputError("each scenario needs outcomes for at least one option")
    expected = {o: 0.0 for o in options}
    worst = {o: float("inf") for o in options}
    best_per = {}
    for s in scen:
        p = float(s["probability"])
        for o in options:
            v = num(s["outcomes"].get(o), f"outcome {o} in {s.get('name')}")
            expected[o] += p * v
            worst[o] = min(worst[o], v)
        best_per[s.get("name", "")] = max(num(v, "outcome") for v in s["outcomes"].values())
    regret = {o: 0.0 for o in options}
    for s in scen:
        best = max(num(s["outcomes"][o], "outcome") for o in options if o in s["outcomes"])
        for o in options:
            regret[o] = max(regret[o], best - num(s["outcomes"][o], "outcome"))
    ev_pick = max(options, key=lambda o: (expected[o], o))
    maximin_pick = max(options, key=lambda o: (worst[o], o))
    minimax_pick = min(options, key=lambda o: (regret[o], o))
    items = [{"option": o, "expected": round(expected[o], 4), "worst_case": worst[o], "max_regret": round(regret[o], 4)}
             for o in options]
    agree = len({ev_pick, maximin_pick, minimax_pick}) == 1
    return {"summary": {"best_expected": ev_pick, "most_robust_maximin": maximin_pick,
                        "least_regret": minimax_pick, "criteria_agree": agree}, "items": items,
            "note": "Choose by the criterion the decision owner cares about; disagreement is a risk signal."}


@handler("Q:optimize")
def q_optimize(ctx):
    data = need_dict(ctx.read_json(), "optimize")
    obj = Expr(data.get("objective"), ctx.policy)
    sense = data.get("sense", "min")
    if sense not in ("min", "max"):
        raise core.InputError("sense must be min or max")
    vars_spec = data.get("search")
    if not isinstance(vars_spec, dict) or not vars_spec:
        raise core.InputError("optimize needs 'search' (name -> {min, max, steps})")
    grids = {}
    total = 1
    for name, spec in vars_spec.items():
        lo, hi = num(spec.get("min"), f"min of {name}"), num(spec.get("max"), f"max of {name}")
        steps = int(num(spec.get("steps", 11), f"steps of {name}", 2, 10000))
        if hi < lo:
            raise core.InputError(f"max must be >= min for {name}")
        grids[name] = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]
        total *= steps
    if total > ctx.policy["limits"]["max_grid_points"]:
        raise core.InputError(f"grid has {total} points; the limit is {ctx.policy['limits']['max_grid_points']}")
    constraints = []
    for c in data.get("constraints", []):
        m = re.fullmatch(r"\s*(.+?)\s*(<=|>=)\s*(.+?)\s*", c)
        if m:
            left, op, right = m.groups()
            text = f"({left})-({right})" if op == "<=" else f"({right})-({left})"
        else:
            text = c
        constraints.append((c, Expr(text, ctx.policy)))
    names = sorted(grids)
    best, feasible = None, 0
    for combo in product(*[grids[n] for n in names]):
        ctx.tick()
        env = dict(zip(names, combo))
        if all(e.evaluate(env) <= 0 for _, e in constraints):
            feasible += 1
            val = obj.evaluate(env)
            if best is None or (val < best[0] if sense == "min" else val > best[0]):
                best = (val, env)
    if best is None:
        return {"summary": {"status": "no feasible point", "grid_points": total}, "items": []}
    return {"summary": {"objective": obj.source, "sense": sense, "best_value": best[0], "feasible_points": feasible,
                        "grid_points": total}, "items": [{"variable": k, "value": v} for k, v in best[1].items()],
            "note": "Grid search finds the best grid point, not a guaranteed global optimum."}


@handler("Q:sample-size")
def q_sample_size(ctx):
    data = need_dict(ctx.read_json(), "sample-size")
    alpha = num(data.get("alpha", 0.05), "alpha", 0.000001, 0.5)
    power = num(data.get("power", 0.8), "power", 0.5, 0.999)
    nd = NormalDist()
    za, zb = nd.inv_cdf(1 - alpha / 2), nd.inv_cdf(power)
    kind = data.get("type", "means")
    if kind == "means":
        sd = num(data.get("sd"), "sd", 0.000001)
        delta = num(data.get("difference"), "difference", 0.000001)
        n = 2 * ((za + zb) * sd / delta) ** 2
    elif kind == "proportions":
        p1, p2 = num(data.get("p1"), "p1", 0, 1), num(data.get("p2"), "p2", 0, 1)
        if p1 == p2:
            raise core.InputError("p1 and p2 must differ")
        pbar = (p1 + p2) / 2
        n = ((za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) / (p1 - p2)) ** 2
    else:
        raise core.InputError("type must be means or proportions")
    per_group = int(math.ceil(n))
    dropout = num(data.get("dropout_pct", 0), "dropout_pct", 0, 90) / 100.0
    inflated = int(math.ceil(per_group / (1 - dropout)))
    return {"summary": {"type": kind, "alpha": alpha, "power": power, "per_group": per_group,
                        "per_group_with_dropout": inflated, "total_with_dropout": 2 * inflated},
            "items": [], "note": "Normal approximation; for small samples confirm with an exact or t-based calculation."}


@handler("Q:faulttree")
def q_faulttree(ctx):
    data = need_dict(ctx.read_json(), "faulttree")
    root = data.get("tree")
    if root is None:
        raise core.InputError("faulttree needs a 'tree' object")
    items = []

    def prob(node, depth, path):
        if depth > 20:
            raise core.InputError("fault tree is deeper than 20 levels")
        ctx.tick()
        kind = node.get("type")
        if kind == "basic":
            p = num(node.get("p"), f"p of {node.get('name')}", 0, 1)
            items.append({"event": node.get("name", ""), "probability": p, "path": path})
            return p
        children = node.get("children", [])
        if not children:
            raise core.InputError("and/or gates need children")
        ps = [prob(c, depth + 1, path + "/" + str(node.get("name", kind))) for c in children]
        if kind == "and":
            out = 1.0
            for x in ps:
                out *= x
            return out
        if kind == "or":
            out = 1.0
            for x in ps:
                out *= (1 - x)
            return 1 - out
        raise core.InputError(f"unknown node type {kind!r}; use basic, and, or")

    top = prob(root, 0, "")
    return {"summary": {"top_event_probability": round(top, 8), "basic_events": len(items)}, "items": items,
            "assumption": "basic events are independent; common-cause failures are not modeled"}


@handler("Q:forecast")
def q_forecast(ctx):
    series = numbers_from_input(ctx)
    if len(series) < 3:
        raise core.InputError("forecasting needs at least 3 observations")
    horizon = ctx.opt_int("horizon", 3, 1, 120)

    def fc_naive(hist, h):
        return [hist[-1]] * h

    def fc_mean(hist, h):
        return [sum(hist) / len(hist)] * h

    def fc_drift(hist, h):
        slope = (hist[-1] - hist[0]) / (len(hist) - 1)
        return [hist[-1] + slope * (k + 1) for k in range(h)]

    def fc_ma(hist, h):
        w = min(3, len(hist))
        window = list(hist[-w:])
        out = []
        for _ in range(h):
            val = sum(window) / w
            out.append(val)
            window = window[1:] + [val]
        return out

    methods = {"naive": fc_naive, "mean": fc_mean, "drift": fc_drift, "moving_average_3": fc_ma}
    items = []
    for name, fn in methods.items():
        errs = []
        for t in range(2, len(series)):
            errs.append(abs(series[t] - fn(series[:t], 1)[0]))
        mae = sum(errs) / len(errs)
        items.append({"method": name, "one_step_mae": round(mae, 4),
                      "forecast": ", ".join(f"{v:.4g}" for v in fn(series, horizon))})
    best = min(items, key=lambda i: (i["one_step_mae"], i["method"]))
    for it in items:
        it["selected"] = "yes" if it is best else ""
    return {"summary": {"observations": len(series), "horizon": horizon, "recommended": best["method"],
                        "recommended_forecast": best["forecast"]}, "items": items,
            "note": "Selection uses in-sample one-step errors; short series give unreliable selection."}


@handler("Q:calibrate")
def q_calibrate(ctx):
    data = need_dict(ctx.read_json(), "calibrate")
    fcs = data.get("forecasts")
    if not isinstance(fcs, list) or not fcs:
        raise core.InputError("calibrate needs 'forecasts': [{point, low, high, actual}]")
    nominal = ctx.opt_float("nominal", ctx.policy["defaults"]["nominal_coverage"], 0.01, 0.999)
    items, errs, hits = [], [], 0
    for n, f in enumerate(fcs, start=1):
        point = num(f.get("point"), f"point {n}")
        lo, hi = num(f.get("low", point), f"low {n}"), num(f.get("high", point), f"high {n}")
        actual = num(f.get("actual"), f"actual {n}")
        hit = lo <= actual <= hi
        hits += 1 if hit else 0
        errs.append(actual - point)
        items.append({"id": f.get("id", n), "error": round(actual - point, 4), "in_interval": "yes" if hit else "no"})
    mae = sum(abs(e) for e in errs) / len(errs)
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    bias = sum(errs) / len(errs)
    coverage = hits / float(len(fcs))
    if coverage < nominal - 0.1:
        verdict = "overconfident: intervals are too narrow"
    elif coverage > nominal + 0.1:
        verdict = "underconfident: intervals are too wide"
    else:
        verdict = "calibrated within 10 points of nominal coverage"
    return {"summary": {"n": len(fcs), "mae": round(mae, 4), "rmse": round(rmse, 4), "bias": round(bias, 4),
                        "coverage": round(coverage, 3), "nominal": nominal, "verdict": verdict}, "items": items}


@handler("Q:error")
def q_error(ctx):
    data = need_dict(ctx.read_json(), "error")
    if "pairs" in data:
        pairs = [(num(p.get("expected"), "expected"), num(p.get("actual"), "actual")) for p in data["pairs"]]
    else:
        exp_v = number_list(data.get("expected"), "expected")
        act_v = number_list(data.get("actual"), "actual")
        if len(exp_v) != len(act_v):
            raise core.InputError("expected and actual must have the same length")
        pairs = list(zip(exp_v, act_v))
    if not pairs:
        raise core.InputError("no pairs to compare")
    errs = [a - e for e, a in pairs]
    mae = sum(abs(x) for x in errs) / len(errs)
    rmse = math.sqrt(sum(x * x for x in errs) / len(errs))
    mape_vals = [abs(a - e) / abs(a) for e, a in pairs if a != 0]
    mape = (100 * sum(mape_vals) / len(mape_vals)) if mape_vals else None
    bias = sum(errs) / len(errs)
    if abs(bias) < 1e-12:
        note = "no systematic bias"
    elif bias > 0:
        note = "actuals were above expectations on average (expectations were too low)"
    else:
        note = "actuals were below expectations on average (expectations were too high)"
    items = [{"n": i, "expected": e, "actual": a, "error": round(a - e, 4)} for i, (e, a) in enumerate(pairs, start=1)]
    return {"summary": {"n": len(pairs), "mae": round(mae, 4), "rmse": round(rmse, 4),
                        "mape_pct": None if mape is None else round(mape, 2), "bias": round(bias, 4),
                        "interpretation": note}, "items": items}


@handler("Q:brier")
def q_brier(ctx):
    data = need_dict(ctx.read_json(), "brier")
    fcs = data.get("forecasts")
    if not isinstance(fcs, list) or not fcs:
        raise core.InputError("brier needs 'forecasts': [{p, outcome}] with outcome 0 or 1")
    pairs = []
    for n, f in enumerate(fcs, start=1):
        p = num(f.get("p"), f"p {n}", 0, 1)
        o = f.get("outcome")
        if o not in (0, 1, True, False):
            raise core.InputError(f"outcome {n} must be 0 or 1")
        pairs.append((p, float(bool(o))))
    score = sum((p - o) ** 2 for p, o in pairs) / len(pairs)
    bins = defaultdict(list)
    for p, o in pairs:
        bins[min(4, int(p * 5))].append((p, o))
    items = []
    for b in sorted(bins):
        members = bins[b]
        mean_p = sum(p for p, _ in members) / len(members)
        freq = sum(o for _, o in members) / len(members)
        items.append({"bin": f"{b / 5:.1f}-{(b + 1) / 5:.1f}", "n": len(members), "mean_forecast": round(mean_p, 3),
                      "observed_frequency": round(freq, 3), "gap": round(mean_p - freq, 3)})
    worst_gap = max((abs(i["gap"]) for i in items), default=0)
    verdict = "well calibrated" if worst_gap <= 0.1 else "miscalibrated: bins differ from observed frequency by more than 0.10"
    return {"summary": {"n": len(pairs), "brier_score": round(score, 4), "calibration": verdict}, "items": items,
            "note": "Lower Brier is better; 0.25 is the score of always answering 0.5."}


@handler("Q:integrity")
def q_integrity(ctx):
    data = need_dict(ctx.read_json(), "integrity")
    groups = data.get("groups", {})
    items = []
    rates = {}
    for name, g in groups.items():
        enrolled = num(g.get("enrolled"), f"enrolled {name}", 1)
        completed = num(g.get("completed"), f"completed {name}", 0)
        if completed > enrolled:
            raise core.InputError(f"completed exceeds enrolled in {name}")
        rate = 1 - completed / enrolled
        rates[name] = rate
        items.append({"item": f"attrition {name}", "value": f"{rate * 100:.1f}%", "flag": ""})
    diff = (max(rates.values()) - min(rates.values())) * 100 if len(rates) > 1 else 0.0
    if diff > 10:
        items.append({"item": "differential attrition", "value": f"{diff:.1f} points", "flag": "check for selection bias"})
    pvals = data.get("pvalues", [])
    alpha = num(data.get("alpha", 0.05), "alpha", 0, 1)
    m = len(pvals)
    if pvals:
        indexed = sorted(enumerate(num(p, "p-value", 0, 1) for p in pvals), key=lambda x: x[1])
        adjusted = [0.0] * m
        running = 0.0
        for rank, (idx, p) in enumerate(indexed):
            running = max(running, min(1.0, (m - rank) * p))
            adjusted[idx] = running
        for idx, p in enumerate(pvals):
            items.append({"item": f"test {idx + 1}", "value": f"p={p:g}, Holm={adjusted[idx]:.4g}",
                          "flag": "significant after Holm" if adjusted[idx] < alpha else ""})
        if m > 1:
            items.append({"item": "multiplicity", "value": f"{m} tests", "flag": "adjust or pre-register"})
    return {"summary": {"groups": len(groups), "attrition_difference_points": round(diff, 2),
                        "tests": m, "holm_significant": sum(1 for it in items if "Holm" in it["value"] and it["flag"])},
            "items": items}


@handler("Q:allocate")
def q_allocate(ctx):
    data = need_dict(ctx.read_json(), "allocate")
    total = num(data.get("total"), "total", 0)
    raw = data.get("items")
    if not isinstance(raw, list) or not raw:
        raise core.InputError("allocate needs 'items': [{name, weight, min, max}]")
    rows = []
    for it in raw:
        rows.append({"name": str(it.get("name")), "w": num(it.get("weight", 1), "weight", 0),
                     "lo": num(it.get("min", 0), "min", 0), "hi": num(it.get("max", total), "max", 0)})
    if sum(r["lo"] for r in rows) > total + 1e-9:
        raise core.InputError("sum of minimums exceeds the total")
    if sum(r["hi"] for r in rows) < total - 1e-9:
        raise core.InputError("sum of maximums is below the total; the total cannot be allocated")
    alloc = {r["name"]: r["lo"] for r in rows}
    for _ in range(100):
        remaining = total - sum(alloc.values())
        if remaining <= 1e-9:
            break
        free = [r for r in rows if alloc[r["name"]] < r["hi"] - 1e-12]
        if not free:
            break
        wsum = sum(r["w"] for r in free)
        for r in free:
            share = remaining * (r["w"] / wsum) if wsum > 0 else remaining / len(free)
            alloc[r["name"]] = min(r["hi"], alloc[r["name"]] + share)
    items = [{"item": r["name"], "allocated": round(alloc[r["name"]], 3), "min": r["lo"], "max": r["hi"]} for r in rows]
    return {"summary": {"total": total, "allocated": round(sum(alloc.values()), 3),
                        "unallocated": round(total - sum(alloc.values()), 3)}, "items": items}


@handler("Q:diminish")
def q_diminish(ctx):
    data = ctx.read_json()
    gains = number_list(data if isinstance(data, list) else (data or {}).get("gains"), "gains")
    threshold = ctx.opt_float("threshold", 0.05, 0, 1)
    first = abs(gains[0]) or 1e-300
    stop_at = None
    items = []
    for i, g in enumerate(gains, start=1):
        ratio = abs(g) / first
        if i > 1 and stop_at is None and ratio < threshold:
            stop_at = i
        items.append({"step": i, "gain": g, "ratio_to_first": round(ratio, 4),
                      "decision": "stop" if stop_at == i else ("continue" if stop_at is None or i < stop_at else "after stop")})
    return {"summary": {"steps": len(gains), "threshold": threshold,
                        "recommendation": f"stop at step {stop_at}" if stop_at else "keep going; no step fell below threshold"},
            "items": items}


@handler("Q:progress")
def q_progress(ctx):
    data = need_dict(ctx.read_json(), "progress")
    done = num(data.get("done"), "done", 0)
    total = num(data.get("total"), "total", 1)
    if done > total:
        raise core.InputError("done cannot exceed total")
    start = core.parse_date(data.get("start"), "start")
    as_of = ctx.as_of()
    elapsed = (as_of - start).days
    if elapsed <= 0 or done <= 0:
        return {"summary": {"status": "insufficient data", "reason": "need done > 0 and at least one elapsed day"}, "items": []}
    rate = done / elapsed
    remaining = total - done
    eta_days = remaining / rate
    eta = as_of + datetime.timedelta(days=int(math.ceil(eta_days)))
    blocked = num(data.get("blocked", 0), "blocked", 0)
    conf = "low" if elapsed < 3 or done < 3 else "moderate"
    return {"summary": {"elapsed_days": elapsed, "rate_per_day": round(rate, 4), "remaining": remaining,
                        "eta_days": round(eta_days, 1), "eta_date": str(eta), "blocked": blocked,
                        "confidence": conf},
            "items": [], "note": "Linear forecast from the observed rate; blockers and changed scope move the date."}


@handler("Q:stress")
def q_stress(ctx):
    data = need_dict(ctx.read_json(), "stress")
    expr = Expr(data.get("expression"), ctx.policy)
    base = _env_from(data, "base")
    shocks = data.get("shocks", {})
    if not isinstance(shocks, dict) or not shocks:
        raise core.InputError("stress needs 'shocks' (name -> multiplicative factor)")
    base_val = expr.evaluate(base)
    items = []
    combined = dict(base)
    for k, f in shocks.items():
        factor = num(f, f"shock {k}", 0)
        if k not in base:
            raise core.InputError(f"shock variable {k!r} is not in base")
        val = expr.evaluate({**base, k: base[k] * factor})
        combined[k] = base[k] * factor
        items.append({"shock": k, "factor": factor, "result": round(val, 6),
                      "change_pct": round(100 * (val - base_val) / abs(base_val), 2) if base_val else None})
    comb = expr.evaluate(combined)
    items.append({"shock": "all combined", "factor": "", "result": round(comb, 6),
                  "change_pct": round(100 * (comb - base_val) / abs(base_val), 2) if base_val else None})
    return {"summary": {"base_result": round(base_val, 6), "combined_result": round(comb, 6)}, "items": items}


@handler("Q:margin")
def q_margin(ctx):
    data = need_dict(ctx.read_json(), "margin")
    est = data.get("estimates")
    if not isinstance(est, dict) or not est:
        raise core.InputError("margin needs 'estimates' (name -> value)")
    default = num(data.get("default_margin_pct", 20), "default_margin_pct", 0, 1000)
    margins = data.get("margins", {})
    items, total = [], 0.0
    for k, v in est.items():
        pct_m = num(margins.get(k, default), f"margin {k}", 0, 1000)
        buffered = num(v, f"estimate {k}", 0) * (1 + pct_m / 100.0)
        total += buffered
        items.append({"item": k, "estimate": v, "margin_pct": pct_m, "buffered": round(buffered, 4)})
    cap = data.get("capacity")
    util = None
    if cap is not None:
        capv = num(cap, "capacity", 0.000001)
        util = round(100 * total / capv, 2)
    return {"summary": {"buffered_total": round(total, 4), "capacity": cap,
                        "utilization_pct": util,
                        "flag": "" if util is None or util <= 85 else "over 85% utilization; add buffer or reduce load"},
            "items": items}


@handler("Q:project")
def q_project(ctx):
    data = need_dict(ctx.read_json(), "project")
    base = num(data.get("base_cost", 0), "base_cost", 0)
    growth = num(data.get("annual_growth_pct", 0), "annual_growth_pct", -100, 1000) / 100.0
    years = int(num(data.get("years", 3), "years", 1, 50))
    hours = num(data.get("maintenance_hours_per_year", 0), "maintenance_hours_per_year", 0)
    rate = num(data.get("hourly_rate", 0), "hourly_rate", 0)
    items, total = [], 0.0
    for y in range(1, years + 1):
        cost = base * (1 + growth) ** (y - 1) + hours * rate
        total += cost
        items.append({"year": y, "cost": round(cost, 2)})
    return {"summary": {"years": years, "total_cost": round(total, 2)}, "items": items}


@handler("Q:visual")
def q_visual(ctx):
    data = need_dict(ctx.read_json(), "visual")
    f = {k: bool(data.get(k, False)) for k in ("hierarchy", "network", "steps", "time_series", "parts_of_whole",
                                              "comparison", "uncertainty", "exact_values_needed", "relationship")}
    categories = int(num(data.get("categories", 0), "categories", 0))
    points = int(num(data.get("points", 0), "points", 0))
    recs = []
    if f["hierarchy"]:
        recs.append(("tree diagram or nested list", "parent-child structure"))
    if f["network"] or f["relationship"]:
        recs.append(("node-link diagram", "many-to-many relations"))
    if f["steps"]:
        recs.append(("numbered list or flow diagram", "ordered steps"))
    if f["time_series"]:
        recs.append(("line chart", "change over time"))
    if f["parts_of_whole"] and 0 < categories <= 6:
        recs.append(("stacked bar (or pie only for 2-3 parts)", "composition with few parts"))
    elif f["parts_of_whole"]:
        recs.append(("sorted horizontal bar", "composition with many parts"))
    if f["comparison"] and categories >= 2:
        recs.append(("bar chart sorted by value", "comparison across categories"))
    if f["uncertainty"]:
        recs.append(("ranges or error bars; never a single value alone", "uncertainty must be visible"))
    if f["exact_values_needed"] or (0 < points <= 12 and not recs):
        recs.append(("table", "readers need exact values"))
    if not recs:
        recs.append(("short prose with one summary table", "no structural signal in the data"))
    items = [{"recommendation": r, "reason": why} for r, why in recs]
    return {"summary": {"primary": recs[0][0], "options": len(recs)}, "items": items,
            "note": "Rules are deterministic; confirm the audience can read the chosen form."}
