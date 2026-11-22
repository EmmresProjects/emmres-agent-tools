#!/usr/bin/env python3
"""tools-oaa-guard.py -- answer integrity, grounding and content safety operations.

Loaded by `tools-oaa.py`. Registers:

  guard.check      grounding / hallucination check of an answer against local evidence
  guard.cite       citation-to-source verification
  guard.schema     structured output validation (JSON Schema subset, local)
  guard.injection  prompt-injection and instruction-hierarchy detection
  guard.redact     sensitive-value redaction for logs, exports and prompts
  guard.calc       bounded arithmetic recheck with an independent exact recomputation

Everything runs locally with the standard library. No model is required; the checks are
deterministic so their results are reproducible and testable.
"""
from __future__ import annotations

import ast
import json
import os
import re
from fractions import Fraction

import importlib.util as _ilu
import sys as _sys

# Load the shared kernel exactly once per process so every module sees the same
# exception types, configuration objects and audit chain instance.
_KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools-oaa-kernel.py")
if "oaa_kernel" in _sys.modules:
    K = _sys.modules["oaa_kernel"]
else:
    _spec = _ilu.spec_from_file_location("oaa_kernel", _KERNEL_PATH)
    K = _ilu.module_from_spec(_spec)
    _sys.modules["oaa_kernel"] = K
    _spec.loader.exec_module(K)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")

DEFAULT_SIGNALS = {
    "instruction_override": {
        "severity": "high",
        "patterns": [
            r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|directives)",
            r"disregard (the |all )?(previous|prior|system) (instructions|prompt)",
            r"forget (everything|all) (above|before)",
            r"new instructions? (take|have) precedence",
            r"override (the )?(system|developer) (prompt|instructions)",
        ],
    },
    "role_hijack": {
        "severity": "high",
        "patterns": [
            r"you are (now|no longer) (a|an|the) (system|developer|administrator|root|unrestricted)",
            r"act as (the )?(system|developer|administrator|root)",
            r"developer mode (enabled|on)",
            r"jailbreak",
        ],
    },
    "secret_exfiltration": {
        "severity": "high",
        "patterns": [
            r"send (all |the )?(secrets?|credentials?|api keys?|tokens?|passwords?)\b.*\b(to|into)",
            r"exfiltrate",
            r"upload (the )?(file|document|data) to",
            r"print (the )?(environment|env) (variables|vars)",
            r"reveal (the )?(secret|token|password|credential)",
        ],
    },
    "tool_coercion": {
        "severity": "medium",
        "patterns": [
            r"run (the )?(following )?(command|script|shell)",
            r"execute (the )?command",
            r"call (the )?tool [a-z_\-]+ without (approval|confirmation)",
            r"\b(curl|wget|rm -rf|chmod \+x)\b",
        ],
    },
    "delimiter_spoof": {
        "severity": "medium",
        "patterns": [
            r"```\s*system",
            r"<\|?im_start\|?>",
            r"</?system>",
            r"\[INST\]",
            r"###\s*(system|instruction)s?\s*:",
        ],
    },
    "policy_override": {
        "severity": "high",
        "patterns": [
            r"approval (is|was) (not required|granted|waived)",
            r"skip (the )?approval",
            r"bypass (the )?(policy|guard|filter|review)",
            r"do not (log|audit|record) this",
        ],
    },
}

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


def _terms(text):
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_\-']+", str(text).lower())
            if len(t) > 2 and t not in K._STOP}


def split_claims(text):
    parts = [p.strip() for p in SENTENCE_SPLIT.split(str(text or "")) if p.strip()]
    return [p for p in parts if len(p) > 3]


def support_score(claim, evidence_text):
    claim_terms = _terms(claim)
    if not claim_terms:
        return 0.0
    ev_terms = _terms(evidence_text)
    if not ev_terms:
        return 0.0
    return len(claim_terms & ev_terms) / len(claim_terms)


def _load_signals(params):
    fixtures_path = params.get("fixtures")
    if fixtures_path and os.path.isfile(fixtures_path):
        data = K.read_json(fixtures_path)
        return data.get("signals", DEFAULT_SIGNALS)
    return DEFAULT_SIGNALS


def grounding_report(cfg, answer, evidence, params=None):
    """Deterministic grounding report for an answer against local evidence."""
    params = params or {}
    if not isinstance(evidence, list) or not evidence:
        raise K.InputError("a non-empty evidence list is required")
    for i, item in enumerate(evidence):
        if not isinstance(item, dict) or "text" not in item:
            raise K.InputError("each evidence item must be an object with text",
                               index=i)
    if isinstance(answer, str):
        answer = {"text": answer}
    if not isinstance(answer, dict) or "text" not in answer:
        raise K.InputError("answer must be a string or an object with text")
    guard_cfg = cfg.get("guard", {}) if isinstance(cfg, dict) else {}
    threshold = float(params.get("support_threshold",
                                 guard_cfg.get("support_threshold", 0.6)))
    max_unsupported = float(params.get("max_unsupported_ratio",
                                       guard_cfg.get("max_unsupported_ratio", 0.34)))
    require_citations = bool(params.get("require_citations",
                                        guard_cfg.get("require_citations", True)))
    evidence_text = " ".join(str(e.get("text", "")) for e in evidence)
    claims = []
    for claim in split_claims(answer.get("text", "")):
        best, best_src = 0.0, None
        for item in evidence:
            score = support_score(claim, str(item.get("text", "")))
            if score > best:
                best, best_src = score, item.get("source")
        claims.append({"claim": claim, "support": round(best, 4),
                       "supported": best >= threshold, "best_source": best_src,
                       "coverage_vs_all_evidence": round(
                           support_score(claim, evidence_text), 4)})
    supported = [c for c in claims if c["supported"]]
    ratio = (len(supported) / len(claims)) if claims else 0.0
    unsupported_ratio = 1.0 - ratio
    citations = answer.get("citations") or []
    citation_report = {"provided": len(citations), "verified": 0, "problems": []}
    if isinstance(citations, list):
        sources = {str(e.get("source")) for e in evidence}
        for citation in citations:
            source = str((citation or {}).get("source", ""))
            if source in sources:
                citation_report["verified"] += 1
            else:
                citation_report["problems"].append(
                    {"issue": "citation_not_in_evidence", "source": source})
    if require_citations and not citations:
        citation_report["problems"].append({"issue": "citations_required_but_missing"})
    ok = ratio >= (1.0 - max_unsupported) and bool(claims)
    if citation_report["problems"]:
        ok = False
    return {
        "grounded": ok,
        "supported_ratio": round(ratio, 4),
        "unsupported_ratio": round(unsupported_ratio, 4),
        "thresholds": {"support_threshold": threshold,
                       "max_unsupported_ratio": max_unsupported},
        "claims": claims,
        "unsupported_claims": [c for c in claims if not c["supported"]],
        "citations": citation_report,
        "abstain_recommended": (not claims) or ratio < max(0.5, 1.0 - max_unsupported),
        "evidence_sources": sorted({str(e.get("source")) for e in evidence}),
    }


def detect_injection(text, signals=None, min_severity="low"):
    """Pattern-based injection detection returning detections and sanitized text."""
    signals = signals or DEFAULT_SIGNALS
    detections = []
    for name, spec in signals.items():
        for pattern in spec.get("patterns", []):
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                detections.append({
                    "signal": name,
                    "severity": spec.get("severity", "medium"),
                    "match": match.group(0)[:120],
                    "span": [match.start(), match.end()],
                })
    floor = SEVERITY_ORDER.get(str(min_severity), 1)
    detections = [d for d in detections
                  if SEVERITY_ORDER.get(d["severity"], 0) >= floor]
    top = max((SEVERITY_ORDER.get(d["severity"], 0) for d in detections), default=0)
    sanitized = text
    for det in sorted(detections, key=lambda d: -d["span"][0]):
        start, end = det["span"]
        sanitized = sanitized[:start] + "[[UNTRUSTED-CONTENT-BLOCKED]]" + sanitized[end:]
    return {
        "detected": bool(detections),
        "severity": {0: "none", 1: "low", 2: "medium", 3: "high"}.get(top, "none"),
        "detections": detections,
        "signal_count": len({d["signal"] for d in detections}),
        "sanitized_text": sanitized,
        "instruction_hierarchy": {
            "trusted_instruction_source": "system/operator prompt",
            "untrusted_source": "retrieved content",
            "action": "block" if top >= 3 else ("review" if top == 2 else "allow"),
        },
    }


def evaluate_calc(expression, expected_value=None, tolerance=1e-9):
    """Exact-vs-float arithmetic recheck."""
    if not isinstance(expression, str) or not expression.strip():
        raise K.InputError("an arithmetic expression is required")
    if len(expression) > 400:
        raise K.InputError("expression is too long", max_chars=400)
    value_float = _eval_float(expression)
    value_exact = _eval_exact(expression)
    exact_float = float(value_exact)
    drift = abs(exact_float - value_float)
    result = {
        "expression": expression,
        "value": value_float,
        "exact_value": value_exact.numerator if value_exact.denominator == 1
        else str(value_exact),
        "exact_as_float": exact_float,
        "drift": round(drift, 12),
        "consistent": drift <= max(float(tolerance), abs(exact_float) * 1e-12),
    }
    if expected_value is not None:
        try:
            expected_f = float(expected_value)
        except (TypeError, ValueError):
            raise K.InputError("expected_value must be numeric", value=expected_value)
        diff = abs(expected_f - exact_float)
        tol = float(tolerance)
        result.update({"expected": expected_f, "difference": round(diff, 12),
                       "matches_expected": diff <= max(tol, abs(expected_f) * 1e-9)})
        result["consistent"] = bool(result["consistent"]
                                    and result["matches_expected"])
    return result


def _register(registry):
    reg = registry

    # ---------------------------------------------------------------- guard.check
    @reg.op("guard.check",
            "Check whether an answer is supported by approved local evidence.",
            params=["answer", "evidence", "support_threshold",
                    "max_unsupported_ratio"],
            group="integrity",
            examples=['{"answer": {"text": "..."}, '
                      '"evidence": [{"source": "a.md", "text": "..."}]}'])
    def guard_check(ctx, params):
        answer = params.get("answer")
        evidence = params.get("evidence")
        if answer is None:
            raise K.InputError("guard.check requires params.answer")
        result = grounding_report(ctx.cfg, answer, evidence, params)
        ctx.emit("guard.check", action="guard.check",
                 grounded=result["grounded"], ratio=result["supported_ratio"])
        return result

    # ---------------------------------------------------------------- guard.cite
    @reg.op("guard.cite",
            "Verify that cited passages exist and support the cited claim.",
            params=["citations", "evidence", "quoted_min_chars"],
            group="integrity",
            examples=['{"citations": [{"source": "a.md", "claim": "..."}], '
                      '"evidence": [{"source": "a.md", "text": "..."}]}'])
    def guard_cite(ctx, params):
        citations = params.get("citations")
        evidence = params.get("evidence") or []
        if not isinstance(citations, list) or not citations:
            raise K.InputError("guard.cite requires a non-empty params.citations list")
        if not isinstance(evidence, list) or not evidence:
            raise K.InputError("guard.cite requires params.evidence")
        by_source = {}
        for item in evidence:
            by_source.setdefault(str(item.get("source")), []).append(
                str(item.get("text", "")))
        min_chars = int(params.get("quoted_min_chars", 20))
        checks = []
        for citation in citations:
            if not isinstance(citation, dict):
                raise K.InputError("each citation must be an object")
            source = str(citation.get("source", ""))
            claim = citation.get("claim") or citation.get("text") or ""
            quote = citation.get("quote")
            entry = {"source": source, "claim": claim, "verified": False,
                     "problems": []}
            if source not in by_source:
                entry["problems"].append("source_not_in_approved_evidence")
            else:
                joined = " ".join(by_source[source])
                if quote:
                    if len(str(quote)) < min_chars:
                        entry["problems"].append("quote_too_short_to_verify")
                    elif str(quote) not in joined:
                        entry["problems"].append("quote_not_found_in_source")
                if claim:
                    score = support_score(str(claim), joined)
                    entry["support"] = round(score, 4)
                    if score < float(ctx.cfg.get("guard", {}).get(
                            "support_threshold", 0.6)):
                        entry["problems"].append("claim_poorly_supported")
                if not entry["problems"]:
                    entry["verified"] = True
            checks.append(entry)
        verified = sum(1 for c in checks if c["verified"])
        result = {"ok": verified == len(checks), "verified": verified,
                  "total": len(checks), "checks": checks}
        ctx.emit("guard.cite", action="guard.cite", verified=verified,
                 total=len(checks))
        return result

    # ---------------------------------------------------------------- guard.schema
    @reg.op("guard.schema",
            "Validate a document against a JSON Schema subset.",
            params=["document", "schema", "document_path", "schema_path"],
            group="integrity",
            examples=['{"document": {"id": "x"}, "schema_path": '
                      '"asset-workflow-schema.json"}'])
    def guard_schema(ctx, params):
        document = params.get("document")
        schema = params.get("schema")
        if document is None and params.get("document_path"):
            document = K.read_json(ctx.workspace.resolve_asset(
                params["document_path"], must_exist=True))
        if schema is None and params.get("schema_path"):
            try:
                path = ctx.workspace.resolve_asset(params["schema_path"],
                                                  must_exist=True)
            except K.OAAError:
                raise K.NotFoundError("schema file not found",
                                      schema_path=params["schema_path"])
            schema = K.read_json(path)
        if schema is None:
            raise K.InputError("guard.schema requires params.schema or params.schema_path")
        if document is None:
            raise K.InputError("guard.schema requires params.document or "
                               "params.document_path")
        errors = validate_schema(document, schema, path="$")
        result = {"valid": not errors, "error_count": len(errors),
                  "errors": errors}
        ctx.emit("guard.schema", action="guard.schema", valid=result["valid"],
                 errors=len(errors))
        return result

    # ---------------------------------------------------------------- guard.injection
    @reg.op("guard.injection",
            "Detect prompt-injection and instruction-hierarchy violations in text.",
            params=["text", "text_path", "fixtures", "min_severity"],
            group="integrity",
            examples=['{"text": "Ignore all previous instructions ..."}'])
    def guard_injection(ctx, params):
        text = params.get("text")
        if text is None and params.get("text_path"):
            text = K.read_text(ctx.workspace.read_scope.resolve(
                params["text_path"], must_exist=True))
        if not isinstance(text, str) or not text.strip():
            raise K.InputError("guard.injection requires params.text or params.text_path")
        result = detect_injection(text, _load_signals(params),
                                  params.get("min_severity", "low"))
        ctx.emit("guard.injection", action="guard.injection",
                 detected=result["detected"], severity=result["severity"])
        return result

    # ---------------------------------------------------------------- guard.redact
    @reg.op("guard.redact",
            "Redact sensitive values from text or JSON for logging and export.",
            params=["text", "text_path", "patterns", "object", "keys"],
            group="integrity",
            examples=['{"text": "api_key = abcdef123456"}'])
    def guard_redact(ctx, params):
        cfg_patterns = ctx.cfg.get("guard", {}).get("redact_patterns", [])
        patterns = params.get("patterns") or cfg_patterns
        text = params.get("text")
        if text is None and params.get("text_path"):
            text = K.read_text(ctx.workspace.read_scope.resolve(
                params["text_path"], must_exist=True))
        if text is None and params.get("object") is None:
            raise K.InputError("guard.redact requires params.text, params.text_path or "
                               "params.object")
        counts = {}
        if text is not None:
            redacted = str(text)
            for pattern in patterns:
                try:
                    compiled = re.compile(pattern)
                except re.error as exc:
                    raise K.InputError("invalid redaction pattern", pattern=pattern,
                                       error=str(exc))
                def _sub(match):
                    counts[pattern] = counts.get(pattern, 0) + 1
                    if match.groups() and match.lastindex and match.lastindex >= 2:
                        return "%s[REDACTED]" % match.group(1)
                    return "[REDACTED]"
                redacted = compiled.sub(_sub, redacted)
            result = {"redacted_text": redacted, "redactions": sum(counts.values()),
                      "by_pattern": counts}
        else:
            obj, hits = _redact_object(params["object"],
                                       params.get("keys") or ctx.cfg.get(
                                           "retention", {}).get("redact_keys", []))
            result = {"redacted_object": obj, "redactions": hits}
        ctx.emit("guard.redact", action="guard.redact",
                 redactions=result["redactions"])
        return result

    # ---------------------------------------------------------------- guard.calc
    @reg.op("guard.calc",
            "Recompute an arithmetic expression exactly and compare with a claimed value.",
            params=["expression", "expected_value", "tolerance"],
            group="integrity",
            examples=['{"expression": "1240 * 3", "expected_value": 3720}'])
    def guard_calc(ctx, params):
        result = evaluate_calc(params.get("expression"), params.get("expected_value"),
                               params.get("tolerance", 1e-9))
        ctx.emit("guard.calc", action="guard.calc", expression=result["expression"],
                 consistent=result["consistent"])
        return result


def _redact_object(obj, keys):
    hits = 0
    lowered = {k.lower() for k in keys}

    def walk(node):
        nonlocal hits
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if str(key).lower() in lowered:
                    out[key] = "[REDACTED]"
                    hits += 1
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(obj), hits


# --------------------------------------------------------------------------------------
# JSON Schema subset validator
# --------------------------------------------------------------------------------------

TYPE_MAP = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "number": (int, float), "integer": int, "null": type(None),
}


def validate_schema(value, schema, path="$"):
    errors = []

    def err(message, **extra):
        entry = {"path": path, "message": message}
        entry.update(extra)
        errors.append(entry)

    if not isinstance(schema, dict):
        err("schema must be an object")
        return errors
    expected_type = schema.get("type")
    if expected_type:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        matched = False
        for name in types:
            py = TYPE_MAP.get(name)
            if py is None:
                continue
            if name in ("number",) and isinstance(value, bool):
                continue
            if name == "integer" and isinstance(value, bool):
                continue
            if isinstance(value, py):
                matched = True
                break
        if not matched:
            err("expected type %s" % expected_type, actual=type(value).__name__)
            return errors
    if "const" in schema and value != schema["const"]:
        err("value must equal const", expected=schema["const"], actual=value)
    if "enum" in schema and value not in schema["enum"]:
        err("value must be one of enum", allowed=schema["enum"], actual=value)
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            err("string shorter than minLength", min= schema["minLength"], length=len(value))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            err("string longer than maxLength", max=schema["maxLength"], length=len(value))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            err("string does not match pattern", pattern=schema["pattern"])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            err("number below minimum", minimum=schema["minimum"], actual=value)
        if "maximum" in schema and value > schema["maximum"]:
            err("number above maximum", maximum=schema["maximum"], actual=value)
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                err("missing required property", property=required)
        properties = schema.get("properties", {})
        for key, sub in properties.items():
            if key in value:
                errors.extend(validate_schema(value[key], sub,
                                              path="%s.%s" % (path, key)))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    err("additional property not allowed", property=key)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            err("array shorter than minItems", minimum=schema["minItems"],
                length=len(value))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            err("array longer than maxItems", maximum=schema["maxItems"],
                length=len(value))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema,
                                              path="%s[%d]" % (path, index)))
    if "anyOf" in schema:
        if not any(not validate_schema(value, sub, path) for sub in schema["anyOf"]):
            err("value does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(1 for sub in schema["oneOf"]
                      if not validate_schema(value, sub, path))
        if matches != 1:
            err("value does not satisfy exactly one oneOf branch", matches=matches)
    return errors


# --------------------------------------------------------------------------------------
# Bounded arithmetic evaluation
# --------------------------------------------------------------------------------------

ALLOWED_FUNCS = {
    "sqrt": lambda x: x ** 0.5,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "floor": lambda x: float(int(x // 1)),
    "ceil": lambda x: float(-int(-x // 1)),
}
MAX_NODES = 120
MAX_EXPONENT = 64


def _parse(expression):
    if sum(expression.count(ch) for ch in "()") > 64:
        raise K.InputError("expression has excessive nesting")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise K.InputError("expression is not valid arithmetic", error=str(exc))
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES:
            raise K.InputError("expression is too complex", max_nodes=MAX_NODES)
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.Await,
                             ast.Import, ast.Call) ):
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
                    raise K.InputError("only whitelisted functions are allowed",
                                       allowed=sorted(ALLOWED_FUNCS))
                continue
            raise K.InputError("unsupported expression element",
                               element=type(node).__name__)
        if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                                 ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult,
                                 ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub,
                                 ast.UAdd, ast.arguments, ast.arg)):
            raise K.InputError("unsupported expression element",
                               element=type(node).__name__)
    return tree


def _eval_float(expression):
    tree = _parse(expression)
    env = dict(ALLOWED_FUNCS)
    env.update({"pi": 3.141592653589793, "e": 2.718281828459045})
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) \
                and isinstance(node.right, ast.Constant):
            value = node.right.value
            if isinstance(value, (int, float)) and abs(value) > MAX_EXPONENT:
                raise K.InputError("exponent is too large",
                                   max_exponent=MAX_EXPONENT)
    try:
        return float(eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, env))
    except ZeroDivisionError:
        raise K.InputError("division by zero")
    except OverflowError:
        raise K.InputError("arithmetic overflow")


def _eval_exact(expression):
    """Independent recomputation using rational arithmetic (no shared float path)."""
    tree = _parse(expression)

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise K.InputError("only numeric literals are allowed")
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp):
            val = walk(node.operand)
            return -val if isinstance(node.op, ast.USub) else +val
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise K.InputError("division by zero")
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                if right == 0:
                    raise K.InputError("division by zero")
                return Fraction(left // right)
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise K.InputError("division by zero")
                return left % right
            if isinstance(node.op, ast.Pow):
                if right.denominator != 1:
                    return Fraction(float(left) ** float(right))
                exponent = int(right)
                if abs(exponent) > MAX_EXPONENT:
                    raise K.InputError("exponent is too large",
                                       max_exponent=MAX_EXPONENT)
                return left ** exponent
            raise K.InputError("unsupported operator")
        if isinstance(node, ast.Call):
            name = node.func.id
            args = [walk(arg) for arg in node.args]
            if name == "sqrt":
                if len(args) != 1 or args[0] < 0:
                    raise K.InputError("sqrt requires one non-negative argument")
                return Fraction(float(args[0]) ** 0.5)
            if name == "abs":
                return abs(args[0])
            if name == "round":
                return Fraction(round(float(args[0]), int(args[1]) if len(args) > 1 else 0))
            if name == "min":
                return min(args)
            if name == "max":
                return max(args)
            if name == "floor":
                return Fraction(int(args[0] // 1))
            if name == "ceil":
                return Fraction(-int(-args[0] // 1))
            raise K.InputError("unsupported function", name=name)
        raise K.InputError("unsupported expression element",
                           element=type(node).__name__)

    return walk(tree)


def register(registry):
    _register(registry)
    return registry
