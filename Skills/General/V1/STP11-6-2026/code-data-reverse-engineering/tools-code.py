"""Source code, build/CI, regex, SQL, secrets and defensive triage analysis."""
from __future__ import annotations

import ast
import json
import re
import sqlite3
import time
from pathlib import Path

from tools_core import (RetoolError, EXIT_USAGE, load_json_asset, read_bytes, validate_input_file,
                        hash_bytes, redact, require_purpose, shannon_entropy)
from tools_identify import identify_bytes, extract_strings, detect_encoding
from tools_formats import detect_format, parse_elf, parse_pe, parse_macho, parse_pdf, parse_cfb, parse_zip

LANG_BY_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
               ".ts": "typescript", ".tsx": "typescript", ".c": "c", ".h": "c", ".cpp": "cpp",
               ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".m": "objc", ".mm": "objc",
               ".cs": "csharp", ".java": "java", ".kt": "kotlin", ".swift": "swift", ".rs": "rust",
               ".go": "go", ".rb": "ruby", ".php": "php", ".sh": "shell", ".bash": "shell",
               ".sql": "sql", ".cob": "cobol", ".cbl": "cobol", ".f": "fortran", ".f90": "fortran",
               ".pas": "pascal", ".pp": "pascal", ".vb": "vb", ".ada": "ada", ".adb": "ada",
               ".lisp": "lisp", ".lsp": "lisp", ".scm": "scheme", ".pl": "perl", ".pm": "perl",
               ".smali": "smali", ".ll": "llvm-ir", ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl",
               ".hlsl": "hlsl", ".sol": "solidity", ".vy": "vyper"}


def _rules():
    return load_json_asset("asset-rules.json")


def detect_lang(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    if ext in LANG_BY_EXT:
        return LANG_BY_EXT[ext]
    if text.startswith("#!") and "python" in text.splitlines()[0]:
        return "python"
    if text.startswith("<?php"):
        return "php"
    if re.search(r"\bpackage\s+main\b", text):
        return "go"
    return "unknown"


# ---------------------------------------------------------------------------
# Source outline / dependencies
# ---------------------------------------------------------------------------

def outline_python(text: str) -> dict:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return {"error": f"SyntaxError: {e.msg} at line {e.lineno}"}
    imports, functions, classes, calls = [], [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({"name": node.name, "line": node.lineno,
                              "args": [a.arg for a in node.args.args],
                              "decorators": [ast.unparse(d) for d in node.decorator_list]})
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno,
                            "bases": [ast.unparse(b) for b in node.bases],
                            "methods": [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]})
        elif isinstance(node, ast.Call):
            try:
                calls.append(ast.unparse(node.func))
            except Exception:
                pass
    return {"imports": sorted(set(imports)), "functions": functions, "classes": classes,
            "call_targets_sample": sorted(set(calls))[:100], "line_count": len(text.splitlines())}


_REGEX_OUTLINES = {
    "javascript": [(r"(?:function\s+([A-Za-z_$][\w$]*)\s*\()", "function"),
                   (r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", "function"),
                   (r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", "method"),
                   (r"class\s+([A-Za-z_$][\w$]*)", "class")],
    "typescript": [(r"(?:function|export\s+function)\s+([A-Za-z_$][\w$]*)", "function"),
                   (r"class\s+([A-Za-z_$][\w$]*)", "class"), (r"interface\s+([A-Za-z_$][\w$]*)", "interface")],
    "c": [(r"^[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;]*?\)\s*\{", "function")],
    "cpp": [(r"^[A-Za-z_][\w:\s\*&<>]*?\b([A-Za-z_]\w*)\s*\([^;]*?\)\s*(?:const\s*)?\{", "function"),
            (r"class\s+([A-Za-z_]\w*)", "class"), (r"struct\s+([A-Za-z_]\w*)", "struct")],
    "java": [(r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\([^)]*\)\s*(?:throws [\w,\s]+)?\{", "method"),
             (r"class\s+(\w+)", "class"), (r"interface\s+(\w+)", "interface")],
    "csharp": [(r"(?:public|private|protected|internal|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\([^)]*\)\s*\{", "method"),
               (r"class\s+(\w+)", "class"), (r"interface\s+(\w+)", "interface")],
    "go": [(r"func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(", "function"), (r"type\s+(\w+)\s+struct", "struct")],
    "rust": [(r"fn\s+(\w+)\s*[<(]", "function"), (r"struct\s+(\w+)", "struct"),
             (r"enum\s+(\w+)", "enum"), (r"impl\s+(\w+)", "impl")],
    "kotlin": [(r"fun\s+(\w+)\s*\(", "function"), (r"class\s+(\w+)", "class")],
    "swift": [(r"func\s+(\w+)\s*[<(]", "function"), (r"class\s+(\w+)", "class"), (r"struct\s+(\w+)", "struct")],
    "ruby": [(r"def\s+([\w.]+)", "method"), (r"class\s+(\w+)", "class")],
    "php": [(r"function\s+(\w+)\s*\(", "function"), (r"class\s+(\w+)", "class")],
    "cobol": [(r"^\s*(\w[\w-]*)\s+SECTION", "section"), (r"^\s*(\w[\w-]*)\.\s*$", "paragraph"),
              (r"(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION", "division")],
    "fortran": [(r"(?i)^\s*(?:subroutine|function|program|module)\s+(\w+)", "program-unit")],
    "pascal": [(r"(?i)^\s*(?:procedure|function)\s+(\w+)", "routine"), (r"(?i)^\s*unit\s+(\w+)", "unit")],
    "vb": [(r"(?i)^\s*(?:Public\s+|Private\s+)?(?:Sub|Function)\s+(\w+)", "routine")],
    "ada": [(r"(?i)^\s*(?:procedure|function)\s+(\w+)", "routine"), (r"(?i)^\s*package\s+(?:body\s+)?(\w+)", "package")],
    "lisp": [(r"\(defun\s+([\w-]+)", "function"), (r"\(defmacro\s+([\w-]+)", "macro")],
    "scheme": [(r"\(define\s+\(?([\w-]+)", "definition")],
    "perl": [(r"sub\s+(\w+)", "subroutine"), (r"package\s+([\w:]+)", "package")],
    "smali": [(r"\.class\s+.*?(L[\w/$]+;)", "class"), (r"\.method\s+.*?(\w+)\(", "method"),
              (r"\.field\s+.*?(\w+):", "field")],
    "llvm-ir": [(r"define\s+[^@]*@([\w.]+)\s*\(", "function"), (r"declare\s+[^@]*@([\w.]+)\s*\(", "declare"),
                (r"@([\w.]+)\s*=\s*(?:global|constant)", "global")],
    "solidity": [(r"function\s+(\w+)\s*\(", "function"), (r"contract\s+(\w+)", "contract"),
                 (r"event\s+(\w+)\s*\(", "event"), (r"modifier\s+(\w+)\s*\(", "modifier")],
    "glsl": [(r"void\s+(\w+)\s*\(", "function"), (r"(?:uniform|varying|attribute|in|out)\s+\w+\s+(\w+)", "io-variable")],
    "hlsl": [(r"(?:float\d?|void|half\d?)\s+(\w+)\s*\(", "function")],
}

_IMPORT_PATTERNS = {
    "javascript": [r"import\s+.*?from\s+['\"]([^'\"]+)['\"]", r"require\(\s*['\"]([^'\"]+)['\"]"],
    "typescript": [r"import\s+.*?from\s+['\"]([^'\"]+)['\"]"],
    "c": [r"#include\s+[<\"]([^>\"]+)[>\"]"], "cpp": [r"#include\s+[<\"]([^>\"]+)[>\"]"],
    "java": [r"import\s+([\w.]+);"], "csharp": [r"using\s+([\w.]+);"],
    "go": [r"\"([\w./-]+)\""], "rust": [r"use\s+([\w:]+)", r"extern\s+crate\s+(\w+)"],
    "kotlin": [r"import\s+([\w.]+)"], "swift": [r"import\s+(\w+)"],
    "ruby": [r"require\s+['\"]([^'\"]+)['\"]"], "php": [r"(?:require|include)(?:_once)?\s+['\"]([^'\"]+)['\"]"],
    "perl": [r"use\s+([\w:]+)"], "python": [r"^\s*import\s+(\w+)", r"^\s*from\s+(\w+)"],
}


def outline_generic(text: str, lang: str) -> dict:
    items = []
    for pat, kind in _REGEX_OUTLINES.get(lang, []):
        for m in re.finditer(pat, text, re.M):
            line = text[:m.start()].count("\n") + 1
            items.append({"kind": kind, "name": m.group(1), "line": line})
    imports = []
    for pat in _IMPORT_PATTERNS.get(lang, []):
        imports.extend(m.group(1) for m in re.finditer(pat, text, re.M))
    return {"items": items[:500], "imports": sorted(set(imports))[:200], "line_count": len(text.splitlines()),
            "method": "regex-heuristic", "confidence": "medium",
            "note": "Structural outline via language-specific patterns; not a full parser. Verify with a language server for critical work."}


def security_scan(text: str, lang: str) -> list:
    rules = _rules()["code"]
    findings = []
    lines = text.splitlines()
    for rule in rules:
        langs = rule.get("lang", ["*"])
        if "*" not in langs and lang not in langs:
            continue
        try:
            pat = re.compile(rule["pattern"])
        except re.error:
            continue
        for i, line in enumerate(lines, 1):
            if rule.get("invert"):
                continue
            if pat.search(line):
                findings.append({"rule": rule["id"], "severity": rule["severity"], "line": i,
                                 "message": rule["message"], "evidence": line.strip()[:160]})
    # special invert rule: shell strict mode
    if lang == "shell" and lines and lines[0].startswith("#!"):
        if not any("set -e" in l or "set -euo" in l for l in lines[:20]):
            findings.append({"rule": "sh-no-strict", "severity": "info", "line": 1,
                             "message": "No 'set -e'/'set -euo pipefail' near the top; confirm error handling.",
                             "evidence": lines[0]})
    return findings


def algorithm_hints(text: str, lang: str) -> dict:
    lines = text.splitlines()
    formulas = []
    loop_depth = 0
    max_depth = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.search(r"\b(for|while)\b", stripped) or re.match(r"^\s*(for|while)\b", line):
            loop_depth += 1
            max_depth = max(max_depth, loop_depth)
        if re.search(r"\b(end|done)\b", stripped) and loop_depth:
            loop_depth = max(0, loop_depth - 1)
        m = re.search(r"([A-Za-z_]\w*)\s*=\s*([^=].*[+\-*/%].*)", stripped)
        if m and not stripped.startswith(("if", "for", "while", "return", "//", "#")):
            formulas.append({"line": i, "target": m.group(1), "expression": m.group(2)[:120]})
    return {"assignment_formulas_sample": formulas[:100], "max_loop_nesting_estimate": max_depth,
            "complexity_hint": ("O(n^%d) or worse (nested loops)" % max_depth) if max_depth >= 2 else ("linear or better (heuristic)" if max_depth == 1 else "no loops detected"),
            "note": "Heuristic extraction; confirm invariants and edge cases against tests."}


def cmd_code(args, env, cfg):
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    text = data.decode("utf-8", errors="replace")
    lang = args.lang if args.lang and args.lang != "auto" else detect_lang(path, text)
    mode = args.mode or "all"
    result = {"file": str(path), "language": lang, "size": len(data), "mode": mode}
    if lang == "python":
        result["outline"] = outline_python(text)
    else:
        result["outline"] = outline_generic(text, lang)
    if mode in ("all", "deps"):
        result["dependencies"] = result["outline"].get("imports", [])
    if mode in ("all", "security"):
        result["security_findings"] = security_scan(text, lang)
    if mode in ("all", "algorithm"):
        result["algorithm"] = algorithm_hints(text, lang)
    if mode in ("all", "encoding"):
        result["encoding"] = detect_encoding(data)
    env["result"] = result
    env["limitations"].append("Non-Python outlines use documented regex heuristics, not full parsers; treat names/lines as leads to verify.")
    return env


# ---------------------------------------------------------------------------
# Regex investigator
# ---------------------------------------------------------------------------

def cmd_regex(args, env, cfg):
    pattern = args.pattern
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise RetoolError("bad-regex", f"Invalid regular expression: {e}", exit_code=EXIT_USAGE)
    result = {"pattern": pattern, "valid": True, "flags": compiled.flags}
    # explanation via parser
    try:
        import re._parser as sre_parse
    except ImportError:
        import sre_parse
    try:
        parsed = sre_parse.parse(pattern)
        result["parse_tree"] = _explain_sre(parsed)
    except Exception as e:
        result["parse_tree_error"] = str(e)
    # ReDoS heuristics
    redos = []
    if re.search(r"(\([^)]*[+*][^)]*\))[+*]", pattern):
        redos.append("Nested quantifier on a group (e.g. (a+)+) — classic catastrophic backtracking shape.")
    if re.search(r"(\([^)]*\|[^)]*\))[+*]", pattern):
        redos.append("Quantified alternation with possible overlap — verify ambiguity.")
    if pattern.count(".*") >= 2:
        redos.append("Multiple .* sequences can cause quadratic backtracking on non-matching input.")
    result["redos_indicators"] = redos
    result["redos_risk"] = "high" if redos else "low-heuristic"
    if args.test is not None:
        tests = []
        for s in args.test[:10]:
            if len(s) > 10000:
                tests.append({"input_length": len(s), "skipped": "input too long for safe test"})
                continue
            t0 = time.perf_counter()
            m = compiled.search(s)
            dt = (time.perf_counter() - t0) * 1000
            tests.append({"input_preview": s[:80], "matched": bool(m),
                          "match": m.group(0)[:120] if m else None, "ms": round(dt, 3)})
        result["tests"] = tests
    env["result"] = result
    env["limitations"].append("ReDoS detection is heuristic (shape-based); confirm with timed tests on adversarial inputs in isolation.")
    return env


def _explain_sre(parsed, depth=0):
    out = []
    if depth > 6:
        return ["..."]
    for op, av in parsed:
        name = str(op)
        if name == "LITERAL":
            out.append(f"literal {chr(av)!r}")
        elif name == "NOT_LITERAL":
            out.append(f"not {chr(av)!r}")
        elif name == "ANY":
            out.append("any char (.)")
        elif name == "IN":
            out.append(f"charset [{_charset(av)}]")
        elif name == "MAX_REPEAT" or name == "MIN_REPEAT":
            lo, hi, sub = av
            hi_s = "inf" if hi == sre_max() else str(hi)
            out.append(f"repeat {lo}..{hi_s} {'(greedy)' if name == 'MAX_REPEAT' else '(lazy)'}: {_explain_sre(sub, depth + 1)}")
        elif name == "SUBPATTERN":
            out.append(f"group: {_explain_sre(av[3], depth + 1)}")
        elif name == "BRANCH":
            out.append("alternation: " + " | ".join(str(_explain_sre(b, depth + 1)) for b in av[1]))
        elif name == "AT":
            out.append(f"anchor {av}")
        elif name == "GROUPREF":
            out.append(f"backref \\{av}")
        elif name == "CATEGORY":
            out.append(f"category {av}")
        else:
            out.append(f"{name} {av if not hasattr(av, 'data') else ''}")
    return out


def sre_max():
    try:
        import re._parser as p
        return p.MAXREPEAT
    except ImportError:
        import sre_parse
        return sre_parse.MAXREPEAT


def _charset(items):
    parts = []
    for op, av in items:
        if str(op) == "LITERAL":
            parts.append(chr(av))
        elif str(op) == "RANGE":
            parts.append(f"{chr(av[0])}-{chr(av[1])}")
        elif str(op) == "CATEGORY":
            parts.append(str(av))
        else:
            parts.append(str(op))
    return "".join(parts)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def cmd_sql(args, env, cfg):
    if args.path:
        path = validate_input_file(args.path, cfg)
        text = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"])).decode("utf-8", errors="replace")
        source = str(path)
    elif args.statement:
        text = args.statement
        source = "<statement>"
    else:
        raise RetoolError("missing-input", "Provide a file path or --statement", exit_code=EXIT_USAGE)
    statements = _split_sql(text)
    results = []
    con = sqlite3.connect(":memory:")
    for stmt in statements[:200]:
        entry = {"statement": stmt[:200]}
        try:
            con.execute("EXPLAIN " + stmt)
            entry["sqlite_syntax_valid"] = True
        except sqlite3.Error as e:
            msg = str(e)
            if msg.startswith(("no such table", "no such column", "no such function")):
                entry["sqlite_syntax_valid"] = True
                entry["schema_note"] = msg + " (syntax OK; object unknown without the real schema)"
            else:
                entry["sqlite_syntax_valid"] = False
                entry["error"] = msg
        results.append(entry)
    con.close()
    review = []
    for i, stmt in enumerate(statements):
        up = stmt.upper()
        if "SELECT *" in up:
            review.append({"statement": i, "issue": "select-star", "severity": "low"})
        if re.search(r"\bDELETE\s+FROM\b", up) and "WHERE" not in up:
            review.append({"statement": i, "issue": "delete-without-where", "severity": "high"})
        if re.search(r"\bUPDATE\b", up) and "WHERE" not in up:
            review.append({"statement": i, "issue": "update-without-where", "severity": "high"})
        if re.search(r"(\"|')\s*\+\s*\w+|\|\|\s*\w+", stmt) and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", up):
            review.append({"statement": i, "issue": "possible-string-concatenation", "severity": "medium"})
        if "DROP TABLE" in up and "IF EXISTS" not in up:
            review.append({"statement": i, "issue": "drop-without-if-exists", "severity": "medium"})
    env["result"] = {"source": source, "statement_count": len(statements), "validation": results,
                     "review": review,
                     "note": "Syntax validated against SQLite's parser; PL/SQL, T-SQL and other dialects may differ. Review items are heuristic."}
    env["limitations"].append("SQL syntax check uses SQLite; dialect-specific constructs (PL/SQL, T-SQL) are not fully validated.")
    return env


def _split_sql(text: str) -> list:
    # strip line comments, then split incrementally using sqlite3.complete_statement
    lines = [line.split("--")[0] for line in text.splitlines()]
    cleaned = "\n".join(lines)
    stmts = []
    buf = ""
    for ch in cleaned:
        buf += ch
        if ch == ";" and sqlite3.complete_statement(buf):
            stmts.append(buf.strip())
            buf = ""
    if buf.strip():
        stmts.append(buf.strip())
    return [s for s in stmts if s]


# ---------------------------------------------------------------------------
# Build / CI / shell / IaC audits + error diagnosis + command translation
# ---------------------------------------------------------------------------

def cmd_build(args, env, cfg):
    mode = args.mode or "auto"
    if getattr(args, "text", None) is not None and mode in ("errors", "translate"):
        text = args.text
        name = "<text>"
        path = None
    else:
        path = validate_input_file(args.path, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        text = data.decode("utf-8", errors="replace")
        name = path.name.lower()
    if mode == "auto":
        if name == "makefile" or name.endswith(".mk"):
            mode = "make"
        elif name == "cmakelists.txt" or name.endswith(".cmake"):
            mode = "cmake"
        elif name == "dockerfile" or name.endswith(".dockerfile"):
            mode = "docker"
        elif "compose" in name and (name.endswith(".yml") or name.endswith(".yaml")):
            mode = "compose"
        elif name.endswith((".yml", ".yaml")):
            mode = "ci"
        elif name.endswith((".tf", ".tfvars")):
            mode = "terraform"
        elif name.endswith((".sh", ".bash")) or text.startswith("#!"):
            mode = "shell"
        elif name.endswith((".ps1",)):
            mode = "shell"
        else:
            mode = "shell"
    result = {"file": str(path) if path else "<text>", "mode": mode}
    rules = _rules()
    if mode == "make":
        targets = []
        for m in re.finditer(r"^([A-Za-z0-9_.\-/]+)\s*:\s*([^=\n]*)$", text, re.M):
            targets.append({"target": m.group(1), "deps": m.group(2).split()})
        variables = dict(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)$", text, re.M))
        result["targets"] = targets
        result["variables"] = variables
        result["phony"] = re.findall(r"\.PHONY\s*:\s*(.*)", text)
    elif mode == "cmake":
        result["project"] = re.findall(r"project\s*\(\s*(\w+)", text, re.I)
        result["executables"] = re.findall(r"add_executable\s*\(\s*(\w+)", text, re.I)
        result["libraries"] = re.findall(r"add_library\s*\(\s*(\w+)", text, re.I)
        result["link_libraries"] = re.findall(r"target_link_libraries\s*\(\s*(\w+)\s+([^)]*)\)", text, re.I)
        result["find_package"] = re.findall(r"find_package\s*\(\s*(\w+)", text, re.I)
    elif mode in ("docker", "compose"):
        findings = _apply_line_rules(text, rules["docker"])
        result["findings"] = findings
        result["from"] = re.findall(r"^\s*FROM\s+(.+)$", text, re.M | re.I)
        result["user"] = re.findall(r"^\s*USER\s+(.+)$", text, re.M | re.I)
        result["expose"] = re.findall(r"^\s*EXPOSE\s+(.+)$", text, re.M | re.I)
        if mode == "compose":
            result["services"] = re.findall(r"^\s{2}(\w[\w-]*):\s*$", text, re.M)
            result["privileged"] = bool(re.search(r"privileged:\s*true", text))
    elif mode == "ci":
        result["findings"] = _apply_line_rules(text, rules["ci"])
        result["platform"] = ("github-actions" if "runs-on" in text or "uses:" in text else
                              "gitlab-ci" if re.search(r"^\s*stages:|^\s*script:", text, re.M) else
                              "azure-pipelines" if "trigger:" in text or "pool:" in text else "generic-yaml")
        result["jobs"] = re.findall(r"^\s{2}([\w-]+):\s*$", text, re.M)[:50]
        result["uses"] = re.findall(r"uses:\s*(\S+)", text)
        result["permissions"] = re.findall(r"permissions:\s*(\S+)", text)
    elif mode in ("terraform", "ansible"):
        key = "iac"
        result["findings"] = _apply_line_rules(text, rules[key])
        if mode == "terraform":
            result["resources"] = re.findall(r'resource\s+"([^"]+)"\s+"([^"]+)"', text)
            result["providers"] = re.findall(r'provider\s+"([^"]+)"', text)
        else:
            result["tasks"] = re.findall(r"^\s*-\s*name:\s*(.+)$", text, re.M)
    elif mode == "shell":
        result["findings"] = _apply_line_rules(text, rules["shell"])
        result["shebang"] = text.splitlines()[0] if text.startswith("#!") else None
        result["strict_mode"] = any("set -e" in l or "set -euo" in l for l in text.splitlines()[:20])
        result["commands_sample"] = re.findall(r"^\s*([a-z_][\w-]*)\s", text, re.M)[:50]
    elif mode == "errors":
        result["diagnostics"] = diagnose_compiler_errors(text)
    elif mode == "translate":
        result["translation"] = translate_commands(text, args.to or "powershell")
    else:
        raise RetoolError("bad-mode", f"Unknown build mode: {mode}", exit_code=EXIT_USAGE)
    env["result"] = result
    return env


def _apply_line_rules(text: str, rules: list) -> list:
    findings = []
    lines = text.splitlines()
    for rule in rules:
        if rule.get("invert"):
            continue
        try:
            pat = re.compile(rule["pattern"])
        except re.error:
            continue
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                findings.append({"rule": rule["id"], "severity": rule["severity"], "line": i,
                                 "message": rule["message"], "evidence": line.strip()[:160]})
    return findings


COMPILER_ERROR_PATTERNS = [
    (r"undefined reference to [`']?([\w:]+)", "linker", "Undefined symbol at link time: the object/library defining it is missing or link order is wrong. Add the library after the objects that use it, or check name mangling (extern \"C\")."),
    (r"error:.*implicit declaration of function", "compile", "Function used without a declaration: include the proper header or add a prototype; implicit declarations are unsafe in C99+."),
    (r"error:.*expected.*before", "compile", "Syntax error: a token is missing or misplaced near the reported location (often a missing semicolon, brace, or paren)."),
    (r"No such file or directory", "compile", "Missing header/file: check include paths (-I), sysroot, and that generated headers were built first."),
    (r"error:.*incompatible pointer type", "compile", "Pointer type mismatch: verify const-qualification, struct types, and casts; may indicate an ABI bug."),
    (r"error:.*relocation.*truncated|relocation R_X86_64", "linker", "Relocation overflow: code model or PIC mismatch. Rebuild with -fPIC/-fPIE or adjust the code model (-mcmodel)."),
    (r"cannot find -l([\w.-]+)", "linker", "Library not found: install the dev package or add -L path; verify the library name and architecture."),
    (r"multiple definition of", "linker", "Symbol defined in multiple objects: avoid definitions in headers, use inline/static, or fix duplicate objects in the link."),
    (r"error MSB\d+|error LNK\d+|error C\d+", "msvc", "MSVC/MSBuild error: look up the specific code; LNK2019=unresolved external, LNK2005=already defined, C2065=undeclared identifier."),
    (r"error:.*no matching function|candidate", "compile", "Overload resolution failed: argument types/count do not match any candidate; check templates and conversions."),
]


def diagnose_compiler_errors(text: str) -> list:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, stage, explanation in COMPILER_ERROR_PATTERNS:
            if re.search(pat, line, re.I):
                out.append({"line": i, "stage": stage, "evidence": line.strip()[:200], "explanation": explanation})
                break
    return out


COMMAND_MAP = {
    "ls": {"powershell": "Get-ChildItem", "cmd": "dir"},
    "cp": {"powershell": "Copy-Item", "cmd": "copy"},
    "mv": {"powershell": "Move-Item", "cmd": "move"},
    "rm": {"powershell": "Remove-Item", "cmd": "del"},
    "rm -rf": {"powershell": "Remove-Item -Recurse -Force", "cmd": "rmdir /s /q"},
    "cat": {"powershell": "Get-Content", "cmd": "type"},
    "grep": {"powershell": "Select-String", "cmd": "findstr"},
    "mkdir": {"powershell": "New-Item -ItemType Directory", "cmd": "mkdir"},
    "pwd": {"powershell": "Get-Location", "cmd": "cd"},
    "echo": {"powershell": "Write-Output", "cmd": "echo"},
    "curl": {"powershell": "Invoke-WebRequest", "cmd": "curl"},
    "touch": {"powershell": "New-Item -ItemType File", "cmd": "type nul >"},
    "chmod": {"powershell": "# (ACL-based; use icacls)", "cmd": "icacls"},
    "export": {"powershell": "$env:NAME = 'value'", "cmd": "set NAME=value"},
    "which": {"powershell": "Get-Command", "cmd": "where"},
    "head": {"powershell": "Get-Content -TotalCount", "cmd": "# (no direct equivalent)"},
    "tail": {"powershell": "Get-Content -Tail", "cmd": "# (no direct equivalent)"},
    "wc -l": {"powershell": "(Get-Content).Count", "cmd": "find /c /v \"\""},
}


def translate_commands(text: str, to: str) -> list:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        translated = stripped
        matched = False
        for src, targets in sorted(COMMAND_MAP.items(), key=lambda x: -len(x[0])):
            if stripped == src or stripped.startswith(src + " "):
                rest = stripped[len(src):].strip()
                translated = f"{targets.get(to, targets['powershell'])} {rest}".strip()
                matched = True
                break
        out.append({"original": stripped, "translated": translated, "target": to,
                    "matched": matched, "note": "Verify arguments and quoting; complex pipelines need manual review."})
    return out


# ---------------------------------------------------------------------------
# Secrets scanning (redacted)
# ---------------------------------------------------------------------------

def cmd_secrets(args, env, cfg):
    require_purpose(args, env, "secrets")
    findings = []
    files_scanned = 0
    for p in args.paths:
        path = validate_input_file(p, cfg)
        data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
        text = data.decode("utf-8", errors="replace")
        files_scanned += 1
        for rule in _rules()["secrets"]:
            try:
                pat = re.compile(rule["pattern"])
            except re.error:
                continue
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                value = m.group(0)
                findings.append({"file": str(path), "rule": rule["id"], "severity": rule["severity"],
                                 "line": line, "offset": m.start(), "length": len(value),
                                 "fingerprint": redact(value), "message": rule["message"]})
                if len(findings) > 2000:
                    break
    env["result"] = {"files_scanned": files_scanned, "finding_count": len(findings),
                     "findings": findings[:1000], "redacted": True,
                     "note": "Values are redacted to SHA-256 fingerprints. Rotate any confirmed live credential and remove it from history."}
    env["inputs"]["paths"] = args.paths
    return env


# ---------------------------------------------------------------------------
# Defensive triage (gated)
# ---------------------------------------------------------------------------

def cmd_triage(args, env, cfg):
    require_purpose(args, env, "triage")
    path = validate_input_file(args.path, cfg)
    data = read_bytes(path, cfg, 0, min(path.stat().st_size, cfg["limits"]["max_read_bytes"]))
    ident = identify_bytes(data, path.name)
    rules = _rules()
    strings = extract_strings(data, 6, 3000, "all")
    text_blob = "\n".join(s["value"] for s in strings)
    indicators = {"packers": [], "anti_analysis": [], "persistence": [], "crypto_api": [],
                  "documents": [], "suspicious_imports": []}
    for category in ("packers", "anti_analysis", "persistence", "crypto_api", "documents"):
        for rule in rules[category]:
            try:
                pat = re.compile(rule["pattern"])
            except re.error:
                continue
            hits = pat.findall(text_blob)
            if hits:
                indicators[category].append({"rule": rule["id"], "severity": rule["severity"],
                                             "message": rule["message"], "hit_count": len(hits),
                                             "sample": [str(h)[:80] for h in hits[:3]]})
    # imports from binary
    fmt = detect_format(data, path.name)
    imports = []
    if fmt == "elf":
        try:
            imports = parse_elf(data)["symbols"]["imports"]
        except Exception:
            pass
    elif fmt == "pe":
        try:
            imports = [f["name"] for imp in parse_pe(data).get("imports", []) for f in imp.get("functions", []) if "name" in f]
        except Exception:
            pass
    elif fmt == "macho":
        try:
            imports = [s["name"] for s in parse_macho(data).get("symbols", [])]
        except Exception:
            pass
    for category, names in rules["suspicious_imports"].items():
        matched = [n for n in names if any(n.lower() in imp.lower() for imp in imports)]
        if matched:
            indicators["suspicious_imports"].append({"category": category, "matched": matched})
    # entropy
    ent = shannon_entropy(data[:65536])
    entropy_flag = ent >= 7.2
    # document-specific
    doc_flags = []
    if fmt == "pdf":
        doc_flags = parse_pdf(data).get("risk_flags", [])
    elif fmt == "cfb":
        doc_flags = parse_cfb(data).get("macro_indicators", [])
    elif fmt == "zip":
        try:
            z = parse_zip(data, cfg)
            names = [e["name"] for e in z["entries"]]
            if any("vbaProject.bin" in n for n in names):
                doc_flags.append("vbaProject.bin")
            if any("macrosheets" in n for n in names):
                doc_flags.append("xl/macrosheets")
        except Exception:
            pass
    risk_score = 0
    for cat, items in indicators.items():
        for it in items:
            sev = it.get("severity", "info")
            risk_score += {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(sev, 0)
    if entropy_flag:
        risk_score += 1
    risk_score += len(doc_flags) * 2
    verdict = "low" if risk_score < 3 else ("medium" if risk_score < 8 else "high")
    env["result"] = {
        "file": str(path), "identification": ident["best_match"], "format": fmt,
        "entropy": ent, "high_entropy": entropy_flag, "indicators": indicators,
        "document_flags": doc_flags, "import_count": len(imports),
        "risk_score": risk_score, "risk_verdict": verdict,
        "purpose": args.purpose,
        "note": "Defensive static triage only. Indicators are not proof of malice; dynamic behavior requires an authorized isolated sandbox (see plan --template dynamic-sandbox). No bypass or weaponization guidance is produced.",
    }
    env["limitations"].append("Static triage cannot observe runtime behavior; packed/encrypted payloads may hide indicators. Use the authorized dynamic plan for behavioral evidence.")
    return env
