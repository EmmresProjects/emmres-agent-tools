"""Built-in self-tests and package checks for the Roblox production toolkit.

Entry points used by tools-roblox.py:
  run_all(root)      behavioural checks (routing, examples, CLI round trip, offline cloud dry runs)
  check_package(root) static checks (files present, Python parses, JSON parses, routing, defaults)

Nothing here opens a network connection or writes outside a temporary directory.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_FILES = [
    "tools-roblox.py", "tools-core.py", "tools-source.py", "tools-selftest.py",
    "tools-engine.py", "tools-structure.py", "tools-simulate.py", "tools-content.py",
    "tools-code.py", "tools-cloud.py",
    "asset-catalog.json", "asset-recipes.json", "asset-default-config.json",
    "asset-openapi-example.json", "asset-request-schema.json", "asset-avatar-rules.json",
    "asset-policy.json", "asset-place-templates.json", "asset-ui-tokens.json",
    "asset-luau-runtime.luau", "knowledge-readme.md",
]
FAMILY_FILES = [
    ("engine", "tools-engine.py", "rbx_engine"),
    ("structure", "tools-structure.py", "rbx_structure"),
    ("simulate", "tools-simulate.py", "rbx_simulate"),
    ("content", "tools-content.py", "rbx_content"),
    ("code", "tools-code.py", "rbx_code"),
    ("cloud", "tools-cloud.py", "rbx_cloud"),
]
ALLOWED_FINDINGS = {"pass", "fail", "review"}


def _check(name, ok, detail=""):
    return {"name": name, "status": "pass" if ok else "fail", "detail": detail}


def _load_file(root, filename, module_name):
    """Load a sibling module from `root`. Family modules import `rbx_core`, so load core first."""
    if "rbx_core" not in sys.modules:
        core_spec = importlib.util.spec_from_file_location("rbx_core", Path(root) / "tools-core.py")
        core_module = importlib.util.module_from_spec(core_spec)
        sys.modules["rbx_core"] = core_module
        core_spec.loader.exec_module(core_module)
    path = Path(root) / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _routes(root):
    """Map label -> (family, handler); report duplicates instead of silently overwriting."""
    routes, dupes, owners = {}, [], {}
    for family, filename, module_name in FAMILY_FILES:
        module = _load_file(root, filename, module_name)
        for label, fn in module.HANDLERS.items():
            if label in routes:
                dupes.append((label, owners[label], family))
            routes[label] = (family, fn)
            owners[label] = family
    return routes, dupes


def check_package(root):
    root = Path(root).resolve()
    checks = []
    missing = [f for f in REQUIRED_FILES if not (root / f).is_file()]
    checks.append(_check("files.present", not missing, "missing: " + ", ".join(missing) if missing else "all required files present"))

    bad_py = []
    for path in sorted(root.glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            bad_py.append(f"{path.name}:{exc.lineno}")
    checks.append(_check("python.parses", not bad_py, "syntax errors: " + ", ".join(bad_py) if bad_py else "all modules parse"))

    bad_json = []
    for path in sorted(root.glob("asset-*.json")):
        try:
            _read_json(path)
        except (ValueError, OSError):
            bad_json.append(path.name)
    checks.append(_check("json.parses", not bad_json, "invalid: " + ", ".join(bad_json) if bad_json else "all asset JSON parses"))

    try:
        catalog = _read_json(root / "asset-catalog.json")
        recipes = _read_json(root / "asset-recipes.json")["recipes"]
        cap_ids = [row[0] for row in catalog["capabilities"]]
        checks.append(_check("catalog.unique_ids", len(cap_ids) == len(set(cap_ids)), f"{len(cap_ids)} capability rows"))
        checks.append(_check("catalog.recipes_match", set(cap_ids) == set(recipes), "every catalog id has exactly one recipe"))
        routes, dupes = _routes(root)
        checks.append(_check("routes.no_duplicates", not dupes, "duplicates: " + repr(dupes) if dupes else f"{len(routes)} labels, each with one handler"))
        labels = {r["algorithm"] for r in recipes.values()}
        unrouted = sorted(labels - set(routes))
        unused = sorted(set(routes) - labels)
        checks.append(_check("routes.every_label_has_handler", not unrouted, "unrouted: " + ", ".join(unrouted) if unrouted else f"{len(labels)} labels used by {len(cap_ids)} capabilities"))
        checks.append(_check("routes.no_extra_handlers", not unused, "unused handlers: " + ", ".join(unused) if unused else "no unused handlers"))
    except (KeyError, OSError, ValueError, TypeError) as exc:
        checks.append(_check("catalog.readable", False, f"{type(exc).__name__}: {exc}"))

    try:
        cfg = _read_json(root / "asset-default-config.json")
        net = cfg["network"]
        checks.append(_check("config.network_off_by_default", net["enabled"] is False and net["allowed_operations"] == [] and net["allow_http"] is False,
                             "network disabled; no operations allowed; HTTP refused"))
        checks.append(_check("config.dry_run_default", cfg["policy"]["live_service_default"] == "dry-run", "live services default to dry-run"))
    except (KeyError, OSError, ValueError) as exc:
        checks.append(_check("config.readable", False, f"{type(exc).__name__}: {exc}"))

    failed = [c["name"] for c in checks if c["status"] != "pass"]
    return {"schema_version": 1, "status": "ok" if not failed else "fail", "root": str(root),
            "checks": checks, "failed": failed}


def _cli(root):
    return _load_file(root, "tools-roblox.py", "rbx_cli_selftest")


class _NoSockets:
    """Context manager that makes any socket connection attempt raise."""

    def __enter__(self):
        self._saved = (socket.socket.connect, socket.create_connection)

        def refuse(*_a, **_k):
            raise RuntimeError("network access attempted during a dry run")

        socket.socket.connect = refuse
        socket.create_connection = refuse
        return self

    def __exit__(self, *_exc):
        socket.socket.connect, socket.create_connection = self._saved
        return False


def run_all(root):
    root = Path(root).resolve()
    report = check_package(root)
    checks = list(report["checks"])
    cli = _cli(root)
    core = cli.core

    # 1. Every recipe example runs in memory through its handler without writing files.
    catalog, _ = cli._catalog()
    failures, statuses = [], {}
    cloud_ids = []
    for row in catalog["capabilities"]:
        cap = cli._capability(row[0])
        if cap["family"] == "cloud":
            cloud_ids.append(cap["id"])
        ctx = core.Context(cli._config(None), Path(tempfile.gettempdir()), timeout=60)
        try:
            result = cli._run_handler(cap, json.loads(json.dumps(cap["example"])), ctx)
            for f in result["findings"]:
                statuses[f["status"]] = statuses.get(f["status"], 0) + 1
        except Exception as exc:  # any error here is a failed example
            failures.append(f"{cap['id']}: {type(exc).__name__}: {str(exc)[:80]}")
    checks.append(_check("examples.run_in_memory", not failures,
                         f"{len(catalog['capabilities']) - len(failures)} of {len(catalog['capabilities'])} examples ran; findings {statuses}"
                         if not failures else "; ".join(failures[:5])))

    # 2. Cloud capabilities dry-run offline: no socket may be opened.
    offline_failures = []
    with _NoSockets():
        for cid in cloud_ids:
            cap = cli._capability(cid)
            ctx = core.Context(cli._config(None), Path(tempfile.gettempdir()), timeout=60)
            try:
                cli._run_handler(cap, json.loads(json.dumps(cap["example"])), ctx)
            except RuntimeError as exc:
                offline_failures.append(f"{cid}: {exc}")
            except Exception:
                pass  # input problems are covered by check 1
    checks.append(_check("cloud.dry_run_offline", not offline_failures,
                         f"{len(cloud_ids)} cloud examples ran without sockets" if not offline_failures else "; ".join(offline_failures[:3])))

    # 3. End-to-end CLI round trip into a temporary folder.
    with tempfile.TemporaryDirectory(prefix="rbx-selftest-") as tmp:
        out = Path(tmp) / "run"
        proc = subprocess.run([sys.executable, str(root / "tools-roblox.py"), "run", "--capability",
                               "reporting-system-builder", "--use-examples", "--output", str(out)],
                              capture_output=True, text=True, timeout=120)
        ok = proc.returncode == 0 and (out / "result.json").is_file() and (out / "report.md").is_file()
        if ok:
            payload = json.loads((out / "result.json").read_text(encoding="utf-8"))
            ok = payload.get("capability") == "reporting-system-builder"
        checks.append(_check("cli.run_round_trip", ok, f"exit {proc.returncode}; files written: {sorted(p.name for p in out.glob('*')) if out.exists() else []}"))

    # 4. Refusal paths: missing input and an output inside the toolkit folder.
    with tempfile.TemporaryDirectory(prefix="rbx-selftest-") as tmp:
        bad_input = subprocess.run([sys.executable, str(root / "tools-roblox.py"), "run", "--capability",
                                    "reporting-system-builder"], capture_output=True, text=True, timeout=60)
        inside = subprocess.run([sys.executable, str(root / "tools-roblox.py"), "run", "--capability",
                                 "reporting-system-builder", "--use-examples", "--output", str(root / "selftest-should-not-exist")],
                                capture_output=True, text=True, timeout=60)
    checks.append(_check("cli.refuses_missing_input", bad_input.returncode == 2, f"exit {bad_input.returncode}"))
    checks.append(_check("cli.refuses_output_inside_toolkit", inside.returncode == 2 and not (root / "selftest-should-not-exist").exists(),
                         f"exit {inside.returncode}"))

    failed = [c["name"] for c in checks if c["status"] != "pass"]
    return {"schema_version": 1, "status": "ok" if not failed else "fail", "root": str(root),
            "checks": checks, "failed": failed}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    mode = sys.argv[2] if len(sys.argv) > 2 else "all"
    result = check_package(target) if mode == "package" else run_all(target)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "ok" else 1)
