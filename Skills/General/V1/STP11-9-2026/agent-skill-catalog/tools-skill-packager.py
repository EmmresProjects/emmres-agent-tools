#!/usr/bin/env python3
"""Package structure checker and scaffold generator.

Two responsibilities:

``validate_package`` / ``validate_zip``
    Enforce the Skill Tool packaging contract: no ``SKILL.md`` inside the tool
    folder, no internal subfolders, every filename prefixed with ``tools-``,
    ``knowledge-`` or ``asset-``, exactly one ``knowledge-readme.md``, at least
    one runnable ``tools-`` entry point, depth and file-count ceilings, no nested
    archives, and resolvable internal references.

``scaffold``
    Generate a *working* starter Skill Tool folder for one catalog capability:
    a runnable ``tools-<name>.py`` CLI with help, a capability list, config
    resolution and a self-check, a populated ``knowledge-readme.md`` and a real
    ``asset-config.json``. Optionally zip the folder with the folder itself at
    the archive root.

Loaded by ``agent-skill-catalog/tools-skillcatalog.py`` via ``importlib``.
Standard library only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import zipfile

try:  # loaded through the primary entry point
    from tools_catalog_parser import normalize_ws, slugify
except ImportError:  # direct execution / standalone loading
    _here = os.path.dirname(os.path.abspath(__file__))
    _spec = importlib.util.spec_from_file_location(
        "tools_catalog_parser", os.path.join(_here, "tools-catalog-parser.py"))
    _parser = importlib.util.module_from_spec(_spec)
    sys.modules["tools_catalog_parser"] = _parser
    _spec.loader.exec_module(_parser)
    from tools_catalog_parser import normalize_ws, slugify

__all__ = [
    "MAX_FILES",
    "MAX_DEPTH",
    "NAME_RE",
    "PREFIXES",
    "archive_folder",
    "scaffold",
    "validate_package",
    "validate_zip",
]

MAX_FILES = 500
MAX_DEPTH = 3
PREFIXES = ("tools-", "knowledge-", "asset-")
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".7z", ".rar", ".bz2", ".xz")
EXECUTABLE_SUFFIXES = (".py", ".js", ".mjs", ".ts", ".sh", ".bash", ".rb", ".ps1", ".lua")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
PLACEHOLDER_RE = re.compile(r"(\{\{[a-z_]+\}\}|\bTODO\b|\bFIXME\b|\bTBD\b|<insert|placeholder text)", re.I)


def _finding(bucket: list, level: str, code: str, message: str, path: str = "") -> None:
    bucket.append({"level": level, "code": code, "message": message, "path": path})


def validate_package(path: str, max_files: int = MAX_FILES, max_depth: int = MAX_DEPTH,
                     check_references: bool = True) -> dict:
    """Validate an extracted Skill Tool folder."""
    errors: list[dict] = []
    warnings: list[dict] = []
    if not os.path.isdir(path):
        raise FileNotFoundError("package folder not found: %s" % path)

    root_name = os.path.basename(os.path.abspath(path))
    if not NAME_RE.match(root_name):
        _finding(warnings, "warning", "FOLDER_NAME_STYLE",
                 "folder name %r is not lowercase-hyphenated" % root_name, root_name)

    files: list[str] = []
    dirs: list[str] = []
    for current, subdirs, names in os.walk(path):
        relative_current = os.path.relpath(current, path)
        for sub in subdirs:
            relative_dir = sub if relative_current == "." else os.path.join(relative_current, sub)
            dirs.append(relative_dir)
            _finding(errors, "error", "SUBFOLDER_FORBIDDEN",
                     "internal subfolder not allowed: %s" % relative_dir)
        for name in names:
            relative = name if relative_current == "." else os.path.join(relative_current, name)
            depth = relative.replace(os.sep, "/").count("/") + 1
            if depth > max_depth:
                _finding(errors, "error", "DEPTH_EXCEEDED",
                         "path is %d levels deep, limit is %d: %s" % (depth, max_depth, relative))
            files.append(relative)

    if len(files) > max_files:
        _finding(errors, "error", "TOO_MANY_FILES",
                 "package has %d files, limit is %d" % (len(files), max_files))
    if not files:
        _finding(errors, "error", "EMPTY_PACKAGE", "package contains no files")

    knowledge_files: list[str] = []
    tool_files: list[str] = []
    asset_files: list[str] = []
    executable = 0

    for name in sorted(files):
        base = os.path.basename(name)
        lowered = base.lower()
        if lowered == "skill.md":
            _finding(errors, "error", "SKILL_MD_PRESENT",
                     "Skill Tool folders must not contain SKILL.md (the parent skill owns it)", name)
        if "/" in name:
            _finding(errors, "error", "NESTED_PATH", "file sits in a subfolder: %s" % name, name)
            continue
        if base.endswith(ARCHIVE_SUFFIXES):
            _finding(errors, "error", "NESTED_ARCHIVE", "archives are not allowed inside the package: %s" % base, name)
        if base.startswith("tools-"):
            tool_files.append(base)
        elif base.startswith("knowledge-"):
            knowledge_files.append(base)
        elif base.startswith("asset-"):
            asset_files.append(base)
        else:
            _finding(errors, "error", "FILENAME_PREFIX",
                     "filename must start with tools-, knowledge- or asset-: %s" % base, name)
        if not FILE_RE.match(os.path.splitext(base)[0]):
            _finding(warnings, "warning", "FILENAME_STYLE",
                     "filename should use lowercase letters, digits, dots and single hyphens: %s" % base, name)
        if " " in base:
            _finding(errors, "error", "FILENAME_SPACE", "filename contains a space: %s" % base, name)
        if base.lower().endswith(EXECUTABLE_SUFFIXES):
            executable += 1
        full = os.path.join(path, name)
        try:
            if os.path.getsize(full) == 0:
                _finding(warnings, "warning", "EMPTY_FILE", "file is empty: %s" % base, name)
        except OSError:
            _finding(errors, "error", "UNREADABLE_FILE", "file cannot be stat'ed: %s" % base, name)

    if len(knowledge_files) != 1 or knowledge_files != ["knowledge-readme.md"]:
        _finding(errors, "error", "KNOWLEDGE_FILE",
                 "folder must contain exactly one knowledge-readme.md, found: %s" % (knowledge_files or "none"))
    if not tool_files:
        _finding(errors, "error", "NO_TOOLS", "folder must contain at least one tools- entry point")
    if executable == 0:
        _finding(errors, "error", "NO_RUNNABLE", "no runnable tools- file (expected one of %s)" % (EXECUTABLE_SUFFIXES,))

    if check_references:
        known = set(os.path.basename(name) for name in files)
        # Only folder-qualified references are contract references, e.g.
        # agent-skill-catalog/asset-catalog-240-skills.md
        reference_re = re.compile(
            r"\b[a-z0-9][a-z0-9-]*/((?:tools|knowledge|asset)-[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]{1,6})\b")
        seen_references: set[tuple[str, str]] = set()
        for name in files:
            if not name.lower().endswith((".md", ".txt", ".html")):
                continue
            full = os.path.join(path, name)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read(1_000_000)
            except OSError:
                continue
            for match in reference_re.finditer(text):
                referenced = match.group(1)
                if referenced in known:
                    continue
                if any(marker in referenced for marker in ("<", ">", "{{", "}}")):
                    continue
                if referenced.split("-", 1)[0] not in ("tools", "knowledge", "asset"):
                    continue
                key = (name, referenced)
                if key in seen_references:
                    continue
                seen_references.add(key)
                _finding(warnings, "warning", "REFERENCE_UNRESOLVED",
                         "reference to %r does not exist in the folder" % referenced, name)
            if "template" in os.path.basename(name).lower():
                continue  # templates legitimately carry placeholders
            for hit in PLACEHOLDER_RE.finditer(text):
                _finding(warnings, "warning", "PLACEHOLDER_TEXT",
                         "file contains a placeholder marker %r" % hit.group(1), name)
                break

    return {
        "ok": not errors,
        "path": os.path.abspath(path),
        "folder": root_name,
        "counts": {"files": len(files), "tools": len(tool_files), "knowledge": len(knowledge_files),
                   "assets": len(asset_files), "directories": len(dirs)},
        "files": sorted(files),
        "errors": errors,
        "warnings": warnings,
    }


def validate_zip(path: str, max_files: int = MAX_FILES) -> dict:
    """Validate a delivery ZIP without extracting it."""
    if not os.path.isfile(path):
        raise FileNotFoundError("zip file not found: %s" % path)
    errors: list[dict] = []
    warnings: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        bad = archive.testzip()
        if bad:
            _finding(errors, "error", "ZIP_CORRUPT", "corrupt member: %s" % bad, bad)
    if not names:
        _finding(errors, "error", "EMPTY_ZIP", "archive contains no files")
    roots = set()
    for name in names:
        parts = [part for part in name.split("/") if part]
        if not parts:
            continue
        roots.add(parts[0])
        if len(parts) > 1 and parts[0].endswith("/"):
            pass
        if name.lower().endswith(ARCHIVE_SUFFIXES):
            _finding(errors, "error", "NESTED_ARCHIVE", "nested archive inside delivery: %s" % name, name)
        if parts[-1].lower() == "skill.md" and len(parts) >= 2:
            _finding(warnings, "warning", "SKILL_MD_IN_MEMBER",
                     "archive member contains SKILL.md inside a folder: %s" % name, name)
        if os.path.basename(name).lower() == "skill.md" and len(parts) == 1:
            _finding(warnings, "warning", "ROOT_SKILL_MD",
                     "archive root contains SKILL.md; the delivery should hold Skill Tool folders only", name)
    if len(roots) != 1:
        _finding(errors, "error", "ZIP_ROOT",
                 "archive must contain exactly one top-level Skill Tool folder, found: %s" % sorted(roots))
    else:
        root = roots.pop()
        if not NAME_RE.match(root):
            _finding(warnings, "warning", "ZIP_ROOT_STYLE",
                     "top-level folder %r is not lowercase-hyphenated" % root)
        members = [name for name in names if name.startswith(root + "/")]
        direct = [name for name in members if name.count("/") == 1]
        if not direct:
            _finding(errors, "error", "ZIP_WRAPPER",
                     "top-level folder %r holds no files directly; a wrapper folder is present" % root)
        if not any(os.path.basename(name).startswith("tools-") for name in members):
            _finding(errors, "error", "ZIP_NO_TOOLS", "archive has no tools- entry point")
    if len(names) > max_files:
        _finding(errors, "error", "TOO_MANY_FILES", "archive holds %d files, limit is %d" % (len(names), max_files))
    return {"ok": not errors, "zip": os.path.abspath(path), "members": len(names),
            "roots": sorted(set(name.split("/")[0] for name in names)),
            "errors": errors, "warnings": warnings, "files": sorted(names)}


# --------------------------------------------------------------------------- #
# Scaffold
# --------------------------------------------------------------------------- #
_GENERATED_TOOL = '''#!/usr/bin/env python3
"""{name} — generated Skill Tool entry point.

Capability: {capability}

Commands:
  --help        full usage
  capabilities  machine-readable capability list
  config        resolved configuration (asset-config.json)
  verify        re-check this folder against the Skill Tool packaging contract

Generated by agent-skill-catalog/tools-skillcatalog.py scaffold.
Replace the capability body with your real implementation, keep the checks.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

VERSION = "0.1.0"
TOOL_NAME = "{name}"
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "asset-config.json")
PREFIXES = ("tools-", "knowledge-", "asset-")

CAPABILITIES = [
    {{"id": "config", "summary": "Resolve and print asset-config.json with defaults applied"}},
    {{"id": "verify", "summary": "Check folder structure, prefixes, knowledge file and file count"}},
    {{"id": "run", "summary": "Execute the capability for a supplied input path (fill in)"}},
]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    config.setdefault("limits", {{}})
    config["limits"].setdefault("timeout_seconds", 30)
    config["limits"].setdefault("max_input_bytes", 8388608)
    config.setdefault("approval", {{"required_for": []}})
    return config


def check_structure(root=ROOT):
    errors, warnings, files = [], [], []
    for current, subdirs, names in os.walk(root):
        if os.path.abspath(current) != os.path.abspath(root):
            errors.append("internal subfolder not allowed: " + os.path.relpath(current, root))
        for name in names:
            relative = os.path.relpath(os.path.join(current, name), root)
            files.append(relative)
            if os.sep in relative:
                errors.append("nested file not allowed: " + relative)
            if os.path.basename(relative).lower() == "skill.md":
                errors.append("SKILL.md is not allowed inside a Skill Tool folder")
            if not os.path.basename(relative).startswith(PREFIXES):
                errors.append("filename lacks tools-/knowledge-/asset- prefix: " + relative)
    knowledge = [name for name in files if name == "knowledge-readme.md"]
    if len(knowledge) != 1:
        errors.append("expected exactly one knowledge-readme.md, found %d" % len(knowledge))
    if not any(name.startswith("tools-") for name in files):
        errors.append("no tools- entry point present")
    if len(files) > 500:
        errors.append("file count %d exceeds 500" % len(files))
    return {{"ok": not errors, "files": sorted(files), "errors": errors, "warnings": warnings}}


def cmd_run(args):
    config = load_config()
    path = args.input
    if not path:
        print(json.dumps({{"ok": False, "error": "missing --input"}}, indent=2))
        return 2
    if not os.path.isfile(path):
        print(json.dumps({{"ok": False, "error": "input not found: " + path}}, indent=2))
        return 2
    size = os.path.getsize(path)
    ceiling = config["limits"].get("max_input_bytes", 8388608)
    if size > ceiling:
        print(json.dumps({{"ok": False, "error": "input exceeds %d bytes" % ceiling}}, indent=2))
        return 2
    print(json.dumps({{"ok": False,
                     "error": "capability body not implemented yet",
                     "hint": "edit tools-{name}.py: cmd_run, then extend knowledge-readme.md",
                     "input": path, "bytes": size,
                     "capability": config.get("capability", "")}}, indent=2))
    return 3


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tools-{name}.py",
        description="{name}: {capability}",
        epilog="Run 'capabilities' for the machine-readable capability list.")
    parser.add_argument("--version", action="version", version=TOOL_NAME + " " + VERSION)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("capabilities", help="list capabilities as JSON")
    sub.add_parser("config", help="print the resolved configuration")

    verify = sub.add_parser("verify", help="re-check the folder contract")
    verify.add_argument("--json", action="store_true", help="emit JSON")

    run = sub.add_parser("run", help="execute the capability against an input file")
    run.add_argument("--input", help="path to the input file")
    run.add_argument("--out", help="path for the output artifact")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "capabilities":
        print(json.dumps({{"tool": TOOL_NAME, "version": VERSION,
                          "capability": "{capability}",
                          "capabilities": CAPABILITIES}}, indent=2))
        return 0
    if args.command == "config":
        print(json.dumps(load_config(), indent=2))
        return 0
    if args.command == "verify":
        result = check_structure()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(("OK" if result["ok"] else "FAILED") + ": %d file(s)" % len(result["files"]))
            for error in result["errors"]:
                print("  error: " + error)
        return 0 if result["ok"] else 1
    if args.command == "run":
        return cmd_run(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''

_GENERATED_SELFTEST = '''#!/usr/bin/env python3
"""{name} — self-test for the generated Skill Tool folder.

Runs the tools-{name}.py CLI through a subprocess and asserts behaviour.
Exit code 0 means every check passed.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(ROOT, "tools-{name}.py")
CHECKS = []


def check(label, condition, detail=""):
    CHECKS.append((label, bool(condition), detail))


def run(*args):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, timeout=120)


helped = run("--help")
check("help exits 0", helped.returncode == 0, helped.stderr[:200])
caps = run("capabilities")
try:
    payload = json.loads(caps.stdout)
    check("capabilities lists entries", len(payload.get("capabilities", [])) >= 2)
except json.JSONDecodeError:
    check("capabilities is JSON", False, caps.stdout[:200])
check("config resolves", run("config").returncode == 0)
check("verify passes", run("verify").returncode == 0)
missing = run("run")
check("missing input rejected", missing.returncode == 2)

failed = [item for item in CHECKS if not item[1]]
for label, ok, detail in CHECKS:
    print(("PASS " if ok else "FAIL ") + label + ((" :: " + detail) if detail and not ok else ""))
print("\\n%d/%d checks passed" % (len(CHECKS) - len(failed), len(CHECKS)))
sys.exit(0 if not failed else 1)
'''


def _knowledge_from_brief(name: str, capability: str, brief: str, catalog_ref: str) -> str:
    """Render the generated knowledge file for a scaffolded Skill Tool folder."""
    commands = "\n".join([
        "python3 %(n)s/tools-%(n)s.py --help" % {"n": name},
        "python3 %(n)s/tools-%(n)s.py capabilities" % {"n": name},
        "python3 %(n)s/tools-%(n)s.py config" % {"n": name},
        "python3 %(n)s/tools-%(n)s.py verify" % {"n": name},
        "python3 %(n)s/tools-%(n)s.py run --input <file> --out <file>" % {"n": name},
    ])
    parts = [
        "# %(n)s" % {"n": name},
        "",
        "Skill Tool component for the future Copilot skill. This folder is copied beside the parent "
        "package's root SKILL.md; it contains no SKILL.md of its own.",
        "",
        "## Capability",
        "",
        capability,
        "",
        "## Files",
        "",
        "- `tools-%(n)s.py` — primary entry point (help, `capabilities`, `config`, `verify`, `run`)." % {"n": name},
        "- `asset-config.json` — configuration: capability, catalog reference (see `%s`), limits, approval policy." % catalog_ref,
        "- `knowledge-readme.md` — this document.",
        "- `tools-selftest.py` — subprocess checks for the generated CLI.",
        "",
        "## Commands",
        "",
        "```bash",
        commands,
        "```",
        "",
        "## Status",
        "",
        "Generated starter. `capabilities`, `config` and `verify` are implemented and covered by "
        "`tools-selftest.py`. `run` validates its input and exits 3 until the capability body is written. "
        "Do not describe `run` as complete before it is implemented and re-tested.",
        "",
        "## Implementation brief",
        "",
        brief,
        "",
        "## Maintenance",
        "",
        "1. Implement the capability body in `tools-%(n)s.py` (`cmd_run`)." % {"n": name},
        "2. Add boundary, failure and recovery cases to `tools-selftest.py`.",
        "3. Keep every filename prefixed with `tools-`, `knowledge-` or `asset-`; no subfolders.",
        "4. Re-run `python3 %(n)s/tools-%(n)s.py verify` after any structural change." % {"n": name},
        "5. Keep `asset-config.json` free of secrets and environment-specific absolute paths.",
        "",
    ]
    return "\n".join(parts)


def scaffold(name: str, out_dir: str, capability: str, brief: str = "",
             catalog_ref: str = "agent-skill-catalog/asset-catalog-240-skills.md",
             with_tests: bool = True, force: bool = False, approve: bool = False,
             created: str | None = None) -> dict:
    """Create a working starter Skill Tool folder (and optionally a ZIP)."""
    slug = slugify(name)
    if not NAME_RE.match(slug):
        raise ValueError("skill tool name must match ^[a-z0-9]+(?:-[a-z0-9]+)*$: %r" % name)
    folder = os.path.join(out_dir, slug)
    if os.path.isdir(folder) and os.listdir(folder) and not (force or approve):
        raise PermissionError("target folder %s already exists and is not empty; pass --approve --force to replace it" % folder)
    os.makedirs(folder, exist_ok=True)

    capability = normalize_ws(capability) or "%s capability from the agent skill catalog" % slug.replace("-", " ")
    if not brief:
        brief = "Brief not supplied. Run `tools-skillcatalog.py plan --skill <name>` to generate one."

    tool_path = os.path.join(folder, "tools-%s.py" % slug)
    with open(tool_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_GENERATED_TOOL.format(name=slug, capability=capability.replace('"', "'")))
    os.chmod(tool_path, 0o755)

    config = {
        "name": slug,
        "version": "0.1.0",
        "capability": capability,
        "catalog": catalog_ref,
        "created": created or "not recorded",
        "limits": {"timeout_seconds": 30, "max_input_bytes": 8388608, "max_retries": 1,
                   "network": "disabled by default; enable explicitly and bound the request count"},
        "approval": {"required_for": ["destructive", "publishing", "financial",
                                      "credential-or-security", "personal-data", "high-stakes"],
                     "mechanism": "explicit human --approve flag plus a written rollback plan"},
        "outputs": {"format": "json+markdown", "path": "caller supplied"},
    }
    with open(os.path.join(folder, "asset-config.json"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(config, indent=2) + "\n")

    with open(os.path.join(folder, "knowledge-readme.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_knowledge_from_brief(slug, capability, brief, catalog_ref))

    written = ["tools-%s.py" % slug, "asset-config.json", "knowledge-readme.md"]
    if with_tests:
        test_path = os.path.join(folder, "tools-selftest.py")
        with open(test_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_GENERATED_SELFTEST.format(name=slug))
        os.chmod(test_path, 0o755)
        written.append("tools-selftest.py")

    result = {"ok": True, "folder": folder, "name": slug, "files": sorted(written),
              "structure": validate_package(folder)}
    return result


def archive_folder(folder: str, zip_path: str, approve: bool = False) -> dict:
    """Zip a Skill Tool folder with the folder itself at the archive root."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise FileNotFoundError("folder not found: %s" % folder)
    if os.path.exists(zip_path) and not approve:
        raise PermissionError("archive %s already exists; pass --approve to replace it" % zip_path)
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)) or ".", exist_ok=True)
    root = os.path.basename(folder)
    tmp = zip_path + ".tmp-partial"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for current, _subdirs, names in os.walk(folder):
            for name in sorted(names):
                full = os.path.join(current, name)
                relative = os.path.relpath(full, folder)
                archive.write(full, arcname="%s/%s" % (root, relative.replace(os.sep, "/")))
    os.replace(tmp, zip_path)
    return validate_zip(zip_path)
