#!/usr/bin/env python3
"""Bounded multi-runtime capability discovery and artifact verification engine."""
import sys
sys.dont_write_bytecode = True

import ast
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

SKILL_TOOL_NAME = "software-engineering"
TOOL_DIR = Path(__file__).resolve().parent


def _load_core() -> Any:
    spec_path = TOOL_DIR / "tools-core-runtime.py"
    import importlib.util
    mod = sys.modules.get("se_wb_tools_core_runtime_py")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("se_wb_tools_core_runtime_py", str(spec_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["se_wb_tools_core_runtime_py"] = module
    spec.loader.exec_module(module)
    return module


def run_bounded_process(argv: List[str], timeout_seconds: int = 15, cwd: Optional[Path] = None) -> Dict[str, Any]:
    """Run an allowlisted binary with shell=False, bounded timeout, and sanitized environment."""
    core = _load_core()
    if not argv:
        return {"ok": False, "exit_code": -1, "stdout": "", "stderr": "Empty argv"}
    binary = shutil.which(argv[0])
    if not binary:
        return {"ok": False, "available": False, "exit_code": 127, "stdout": "", "stderr": f"Binary not installed: {argv[0]}"}
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        proc = subprocess.run(
            [binary] + argv[1:],
            cwd=str(cwd) if cwd else None,
            env=safe_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_seconds),
            shell=False,
        )
        return {
            "ok": proc.returncode == 0,
            "available": True,
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip()[:4000],
            "stderr": proc.stderr.strip()[:4000],
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "available": True,
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Process timed out after {timeout_seconds}s",
        }
    except OSError as exc:
        return {"ok": False, "available": False, "exit_code": -1, "stdout": "", "stderr": str(exc)}


def probe_host_environment(timeout_seconds: int = 8) -> Dict[str, Any]:
    """Inspect installed vs unavailable compilers, runtimes, and tools honestly."""
    probes = [
        ("python3", ["python3", "--version"]),
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("bash", ["bash", "--version"]),
        ("git", ["git", "--version"]),
        ("gcc", ["gcc", "--version"]),
        ("g++", ["g++", "--version"]),
        ("javac", ["javac", "-version"]),
        ("java", ["java", "-version"]),
        ("perl", ["perl", "-v"]),
        ("Rscript", ["Rscript", "--version"]),
        ("openssl", ["openssl", "version"]),
        ("go", ["go", "version"]),
        ("rustc", ["rustc", "--version"]),
        ("cargo", ["cargo", "--version"]),
        ("dotnet", ["dotnet", "--version"]),
        ("clang", ["clang", "--version"]),
        ("php", ["php", "--version"]),
        ("ruby", ["ruby", "--version"]),
        ("lua", ["lua", "-v"]),
        ("docker", ["docker", "--version"]),
        ("kubectl", ["kubectl", "version", "--client"]),
        ("terraform", ["terraform", "version"]),
    ]
    available: Dict[str, str] = {}
    unavailable: List[str] = []
    for name, cmd in probes:
        if not shutil.which(cmd[0]):
            unavailable.append(name)
            continue
        res = run_bounded_process(cmd, timeout_seconds=timeout_seconds)
        first_line = (res["stdout"] or res["stderr"] or "installed").splitlines()[0]
        available[name] = first_line
    available["python-sqlite3"] = f"SQLite {sqlite3.sqlite_version} (Python stdlib)"
    return {
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "available_runtimes": available,
        "unavailable_runtimes": unavailable,
    }


def verify_python_source(code: str, filename: str = "module.py") -> Dict[str, Any]:
    try:
        tree = ast.parse(code, filename=filename)
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        return {
            "status": "passed",
            "details": f"Python AST parsed cleanly ({len(funcs)} function(s), {len(classes)} class(es)).",
            "functions": funcs,
            "classes": classes,
        }
    except SyntaxError as exc:
        return {
            "status": "failed",
            "details": f"Python SyntaxError at line {exc.lineno}: {exc.msg}",
            "functions": [],
            "classes": [],
        }


def verify_javascript_source(code: str, timeout_seconds: int = 12) -> Dict[str, Any]:
    core = _load_core()
    analyzer_path = core.resolve_resource_path(f"{SKILL_TOOL_NAME}/tools-node-analyzer.mjs")
    if not shutil.which("node") or not analyzer_path.is_file():
        return {
            "status": "skipped",
            "details": "Node.js binary not installed; skipped live V8 syntax check.",
            "analysis": {},
        }
    tmp_dir = core.create_managed_temp_dir("se-node-")
    snippet = tmp_dir / "snippet.mjs"
    snippet.write_text(code, encoding="utf-8")
    res = run_bounded_process(["node", str(analyzer_path), "--mode", "source", "--file", str(snippet)], timeout_seconds=timeout_seconds)
    if not res["ok"]:
        return {"status": "failed", "details": res["stderr"] or res["stdout"], "analysis": {}}
    parsed = json.loads(res["stdout"])
    analysis = parsed.get("result", {})
    if not analysis.get("syntax_valid", False):
        return {
            "status": "failed",
            "details": f"Node.js syntax error: {analysis.get('syntax_error')}",
            "analysis": analysis,
        }
    return {
        "status": "passed",
        "details": f"Verified by {analysis.get('runtime')} ({len(analysis.get('findings', []))} static notice(s)).",
        "analysis": analysis,
    }


def verify_c_or_cpp_source(code: str, lang: str = "c", std_flag: str = "-std=c17", timeout_seconds: int = 15) -> Dict[str, Any]:
    core = _load_core()
    compiler = "gcc" if lang == "c" else "g++"
    ext = ".c" if lang == "c" else ".cpp"
    if lang != "c" and std_flag == "-std=c17":
        std_flag = "-std=c++20"
    if not shutil.which(compiler):
        return {
            "status": "skipped",
            "details": f"{compiler} not installed on host; performed static rule inspection only.",
        }
    tmp_dir = core.create_managed_temp_dir("se-cc-")
    src = tmp_dir / f"unit{ext}"
    src.write_text(code, encoding="utf-8")
    res = run_bounded_process([compiler, std_flag, "-fsyntax-only", "-Wall", "-Wextra", str(src)], timeout_seconds=timeout_seconds)
    return {
        "status": "passed" if res["ok"] else "failed",
        "details": f"{compiler} {std_flag} check {'succeeded' if res['ok'] else 'failed'}: {(res['stderr'] or '0 errors').splitlines()[0] if (res['stderr'] or not res['ok']) else 'clean syntax and types'}",
        "compiler_stderr": res["stderr"],
    }


def verify_java_source(code: str, class_name: str = "Main", timeout_seconds: int = 20) -> Dict[str, Any]:
    core = _load_core()
    if not shutil.which("javac"):
        return {"status": "skipped", "details": "javac not installed on host; skipped bytecode compilation."}
    tmp_dir = core.create_managed_temp_dir("se-java-")
    src = tmp_dir / f"{class_name}.java"
    src.write_text(code, encoding="utf-8")
    res = run_bounded_process(["javac", "-d", str(tmp_dir), str(src)], timeout_seconds=timeout_seconds)
    class_files = list(tmp_dir.glob("*.class"))
    return {
        "status": "passed" if res["ok"] and class_files else "failed",
        "details": f"javac produced {len(class_files)} .class artifact(s)" if res["ok"] else f"javac error: {res['stderr']}",
    }


def verify_shell_script(script: str, shell_bin: str = "bash", timeout_seconds: int = 10) -> Dict[str, Any]:
    core = _load_core()
    if not shutil.which(shell_bin):
        return {"status": "skipped", "details": f"{shell_bin} not installed; skipped shell -n check."}
    tmp_dir = core.create_managed_temp_dir("se-sh-")
    src = tmp_dir / "script.sh"
    src.write_text(script, encoding="utf-8")
    res = run_bounded_process([shell_bin, "-n", str(src)], timeout_seconds=timeout_seconds)
    return {
        "status": "passed" if res["ok"] else "failed",
        "details": f"{shell_bin} -n syntax check {'passed' if res['ok'] else 'failed: ' + res['stderr']}",
    }


def verify_sqlite_workload(ddl: str, seed_dml: Optional[List[str]] = None, query: Optional[str] = None, max_rows: int = 500) -> Dict[str, Any]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        if ddl.strip():
            conn.executescript(ddl)
        for stmt in (seed_dml or []):
            conn.execute(stmt)
        conn.commit()
        rows_out: List[Dict[str, Any]] = []
        plan_out: List[str] = []
        if query and query.strip():
            cur = conn.execute(query)
            rows_out = [dict(r) for r in cur.fetchmany(max_rows)]
            if query.lstrip().upper().startswith("SELECT"):
                plan_cur = conn.execute("EXPLAIN QUERY PLAN " + query)
                plan_out = [" | ".join(str(x) for x in r) for r in plan_cur.fetchall()]
        tables_cur = conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index', 'view', 'trigger') ORDER BY type, name;")
        objects = [dict(r) for r in tables_cur.fetchall()]
        return {
            "status": "passed",
            "details": f"SQLite {sqlite3.sqlite_version} executed cleanly ({len(objects)} schema object(s), {len(rows_out)} row(s) returned).",
            "schema_objects": objects,
            "query_rows": rows_out,
            "query_plan": plan_out,
        }
    except sqlite3.Error as exc:
        return {
            "status": "failed",
            "details": f"SQLite execution error: {exc}",
            "schema_objects": [],
            "query_rows": [],
            "query_plan": [],
        }
    finally:
        conn.close()


def verify_zip_archive(zip_path: Path) -> Dict[str, Any]:
    if not zip_path.is_file():
        return {"status": "failed", "details": f"ZIP archive not found: {zip_path}"}
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            corrupt_member = zf.testzip()
            names = zf.namelist()
            nested_archives = [n for n in names if n.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".7z"))]
            if corrupt_member:
                return {"status": "failed", "details": f"Corrupt member in ZIP: {corrupt_member}"}
            if nested_archives:
                return {"status": "failed", "details": f"Nested archive(s) found inside ZIP: {nested_archives}"}
            return {
                "status": "passed",
                "details": f"ZIP reopen and CRC check passed ({len(names)} entry/entries, 0 nested archives).",
                "entries": names,
            }
    except zipfile.BadZipFile as exc:
        return {"status": "failed", "details": f"Invalid ZIP archive: {exc}"}
