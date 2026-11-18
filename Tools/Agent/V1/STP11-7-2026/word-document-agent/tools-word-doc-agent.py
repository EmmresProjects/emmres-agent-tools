#!/usr/bin/env python3
"""word-document-agent: primary entry point.

A unified, offline Microsoft Word (.docx/.docm/.dotx/.dotm) skill tool that
implements the Microsoft Word Agent Skill Catalog (53 categories, 636
candidate skills) as 29 runnable operations: create and structure
documents, edit content, inspect and sanitise packages, review comments and
tracked changes, protect, mail-merge, export, back up and gate releases.

Everything is standard library only. Run ``--help`` for usage and
``capabilities`` for the operation list.

Examples
--------
    python3 tools-word-doc-agent.py capabilities
    python3 tools-word-doc-agent.py inspect --input report.docx --json
    python3 tools-word-doc-agent.py qa --input report.docx
    python3 tools-word-doc-agent.py create --output brief.docx ^
        --param markdown-file=brief.md --param title="Brief"
    python3 tools-word-doc-agent.py replace --input brief.docx ^
        --output brief-v2.docx --param find=Q3 --param replace=Q4
"""

from __future__ import annotations

import argparse
import importlib.util as _ilu
import json
import os
import sys
import time
import traceback

# Keep the packaged folder clean: the sibling modules are loaded by path, and
# we never want __pycache__ (or write failures on read-only media) inside a
# delivered skill folder.
sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))


def _bootstrap(module_name, filename):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = _ilu.spec_from_file_location(module_name, os.path.join(_HERE, filename))
    module = _ilu.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


TOOL_VERSION = "1.0.0"

try:
    C = _bootstrap("tools_docx_core", "tools-docx-core.py")
except Exception as exc:  # pragma: no cover - packaging failure path
    sys.stderr.write("fatal: cannot load tools-docx-core.py next to this entry point: %s\n" % exc)
    raise SystemExit(1)

EXIT_CODES = C.EXIT_CODES
ToolError = C.ToolError


def _lazy_modules():
    """Import the sibling tool modules only when an operation needs them."""
    return {
        "build": _bootstrap("tools_docx_build", "tools-docx-build.py"),
        "structure": _bootstrap("tools_docx_structure", "tools-docx-structure.py"),
        "edit": _bootstrap("tools_docx_edit", "tools-docx-edit.py"),
        "audit": _bootstrap("tools_docx_audit", "tools-docx-audit.py"),
        "export": _bootstrap("tools_docx_export", "tools-docx-export.py"),
    }


# --------------------------------------------------------------------------
# operation registry
# --------------------------------------------------------------------------

OPERATIONS = {
    # name: (needs_input, needs_output, destructive, writes, summary)
    "create": (False, True, False, True,
               "create a new Word document from Markdown/JSON blocks, optionally from a template"),
    "copy": (True, True, False, True,
             "save-a-copy branch with revision bump and title suffix"),
    "convert": (True, True, False, True,
                "convert between docx, docm, dotx and dotm package types"),
    "inspect": (True, False, False, False,
                "full structured profile: properties, structure, hidden data, risk, a11y, proofing"),
    "metadata": (True, True, False, True,
                 "read or set core/app/custom properties, document language and personal data"),
    "replace": (True, True, False, True,
                "find and replace (literal or regex) across body, headers and footers"),
    "insert": (True, True, False, True,
               "insert page/column breaks, cover pages, bookmarks, captions, controls, lists or text"),
    "format": (True, True, False, True,
               "set page geometry, sections, columns, paragraph spacing and character defaults"),
    "styles": (True, True, False, True,
               "list, apply or normalise named styles"),
    "list": (True, True, False, True,
             "inspect, create, restyle or restart bullet and numbered lists"),
    "fields": (True, True, False, True,
               "list, insert, mark-for-update, lock or unlock field codes"),
    "toc": (True, True, False, True,
            "build, inspect or remove a table-of-contents field from heading structure"),
    "table": (True, True, False, True,
              "create tables from CSV/JSON, manage structure, sort, design and total rows"),
    "image": (True, True, False, True,
              "embed a PNG/JPEG/GIF/BMP image with alt text and controlled width"),
    "headerfooter": (True, True, False, True,
                     "write header/footer content with page numbering fields"),
    "watermark": (True, True, True, True,
                  "add or remove a WordArt text watermark in the section header"),
    "form": (True, True, False, True,
             "build fillable content controls from a JSON form spec"),
    "comments": (True, True, False, True,
                 "list, add or remove comments and their ranges"),
    "revisions": (True, True, True, True,
                  "report, accept or reject tracked changes"),
    "sanitize": (True, True, True, True,
                 "remove hidden data, comments, revisions and personal metadata per policy"),
    "protect": (True, True, True, True,
                "apply read-only/comments/forms/tracked-changes protection"),
    "unprotect": (True, True, True, True,
                  "remove document protection"),
    "merge": (True, True, False, True,
              "mail-merge a placeholder document from a JSON/CSV record set"),
    "export": (True, True, False, True,
               "export to txt, md, html, json or pdf (external adapter)"),
    "a11y": (True, False, False, False,
             "accessibility audit with rule ids, severities and remediation"),
    "proof": (True, False, False, False,
              "proofing checks: placeholders, repeated words, spacing and quote defects"),
    "qa": (True, False, False, False,
           "release gate combining integrity, structure, accessibility, proofing and review state"),
    "backup": (True, False, False, False,
               "create or list last-known-good backups in the state directory"),
    "restore": (True, True, True, True,
                "restore a verified backup to an output path"),
    "route": (False, False, False, False,
              "map a free-text request to catalog skills and implementing operations"),
    "plan": (False, False, False, False,
             "produce a verified procedure for a catalog skill, with automation and rollback"),
    "score": (True, False, False, False,
              "score catalog coverage and document evidence across all 53 categories"),
    "capabilities": (False, False, False, False,
                     "list operations, catalog categories and skill counts"),
}

ALIASES = {
    "help": "capabilities",
    "accept-revisions": "revisions",
    "reject-revisions": "revisions",
    "pdf": "export",
    "check": "qa",
    "audit": "inspect",
    "profile": "inspect",
    "mailmerge": "merge",
    "versions": "backup",
}

APPROVAL_REASONS = {
    "sanitize": "removes content and metadata irreversibly",
    "protect": "changes who can edit or open the document",
    "unprotect": "removes a protection control set by someone else",
    "revisions": "rewrites the tracked-change history",
    "watermark": "removes a visual marking from every page",
    "restore": "overwrites the target document with an older copy",
    "in-place": "overwrites the input document",
}


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError("USAGE", message, {"usage": self.format_usage().strip()})


def coerce(value: str):
    text = value.strip()
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none"):
        return None
    try:
        if text.isdigit():
            return int(text)
        if text.replace(".", "", 1).lstrip("-").isdigit():
            return float(text)
    except ValueError:
        pass
    return text


def parse_params(items) -> dict:
    params = {}
    for item in items or []:
        key, sep, value = str(item).partition("=")
        if not sep:
            raise ToolError("USAGE", "--param must be key=value (got %r)" % item)
        key = key.strip().replace("-", "_")
        if not key:
            raise ToolError("USAGE", "--param needs a key")
        params[key] = coerce(value)
    return params


def build_parser() -> _Parser:
    parser = _Parser(
        prog="tools-word-doc-agent.py",
        description="Unified Microsoft Word document skill tool "
                    "(create, edit, inspect, review, export and gate .docx packages).",
        epilog="Run 'capabilities' for the operation list, "
               "'capabilities --all' for the 636-skill catalog map, "
               "'plan --param skill=NAME' for a specific capability.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", nargs="?",
                        help="operation name (see 'capabilities'); default: capabilities")
    parser.add_argument("--input", "-i", help="input .docx/.docm/.dotx/.dotm path")
    parser.add_argument("--output", "-o", help="output path (file, or directory for merge)")
    parser.add_argument("--param", "-p", action="append", metavar="KEY=VALUE",
                        help="operation parameter; repeatable")
    parser.add_argument("--config", help="path to a JSON config (see asset-default-config.json)")
    parser.add_argument("--json", action="store_true", help="emit one JSON envelope on stdout")
    parser.add_argument("--quiet", "-q", action="store_true", help="suppress the text summary")
    parser.add_argument("--approve", action="store_true",
                        help="approve a destructive, publishing or in-place action")
    parser.add_argument("--in-place", action="store_true",
                        help="write back onto --input (requires --approve)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing output")
    parser.add_argument("--report-file", help="also write the JSON result to this path")
    parser.add_argument("--timeout", type=float,
                        help="time budget in seconds (default: config limits.max_seconds)")
    parser.add_argument("--version", action="version", version="word-document-agent %s" % TOOL_VERSION)
    return parser


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

class Context:
    def __init__(self, args):
        self.args = args
        self.cfg = C.load_config(getattr(args, "config", None))
        self.params = parse_params(getattr(args, "param", None))
        self.operation = normalize_operation(args.operation or "capabilities")
        self.input = getattr(args, "input", None)
        self.output = getattr(args, "output", None)
        self.json = bool(getattr(args, "json", False))
        self.quiet = bool(getattr(args, "quiet", False))
        self.approve = bool(getattr(args, "approve", False))
        self.in_place = bool(getattr(args, "in_place", False))
        self.dry_run = bool(getattr(args, "dry_run", False))
        self.report_file = getattr(args, "report_file", None)
        self.timeout = getattr(args, "timeout", None) or self.cfg["limits"]["max_seconds"]
        self.deadline = C.set_deadline(C.Deadline(self.timeout))
        self._doc = None
        self._modules = None
        self._policy = None

    @property
    def modules(self):
        if self._modules is None:
            self._modules = _lazy_modules()
        return self._modules

    @property
    def policy(self):
        if self._policy is None:
            self._policy = self.modules["audit"].load_policy(self.cfg)
        return self._policy

    def doc(self):
        if self._doc is None:
            path = C.validate_input_path(self.input, self.cfg)
            self._doc = C.Docx(path, self.cfg)
        return self._doc

    def output_path(self, extension: str | None = None) -> str:
        return C.validate_output_path(self.output, self.input, self.cfg,
                                      in_place=self.in_place)

    def export_output_path(self) -> str:
        if not self.output:
            raise ToolError("USAGE", "export needs --output FILE.ext")
        full = os.path.abspath(os.path.expanduser(self.output))
        if self.input and os.path.abspath(os.path.expanduser(self.input)) == full:
            raise ToolError("USAGE", "export output must differ from the input document")
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        return full

    def require_approval(self, reason: str):
        if self.dry_run:
            return
        if not self.approve:
            raise ToolError(
                "APPROVAL_REQUIRED",
                "%s requires approval: %s" % (self.operation, reason),
                {"approve_with": "--approve", "dry_run_with": "--dry-run",
                 "reason": reason})


def normalize_operation(name: str) -> str:
    name = (name or "capabilities").strip().lower()
    name = ALIASES.get(name, name)
    if name not in OPERATIONS:
        close = sorted(OPERATIONS)[:5]
        raise ToolError("USAGE", "unknown operation: %r" % name,
                        {"available": sorted(OPERATIONS), "examples": close,
                         "help": "run: tools-word-doc-agent.py capabilities"})
    return name


# --------------------------------------------------------------------------
# operation implementations
# --------------------------------------------------------------------------

def op_capabilities(ctx: Context) -> dict:
    modules = ctx.modules
    map_data = modules["audit"].load_capability_map(ctx.cfg)
    ops = modules["edit"].describe_capabilities(map_data)
    inventory = []
    for name, (needs_input, needs_output, destructive, writes, summary) in OPERATIONS.items():
        meta = ops.get(name, {})
        inventory.append({
            "operation": name,
            "summary": summary,
            "needs_input": needs_input,
            "needs_output": needs_output,
            "writes_output": writes,
            "destructive": destructive,
            "requires_approval": destructive,
            "implemented_in": _file_for_operation(name),
            "catalog_skills": meta.get("catalog_skills", 0),
            "catalog_categories": meta.get("categories", []),
        })
    result = {
        "tool": "word-document-agent",
        "version": TOOL_VERSION,
        "python": sys.version.split()[0],
        "catalog": {"categories": len(map_data["categories"]),
                    "skills": sum(len(c["skills"]) for c in map_data["categories"])},
        "operations": inventory,
        "operation_count": len(inventory),
        "approval_required_for": ctx.cfg["editing"]["require_approval_for"],
        "supported_inputs": ctx.cfg["editing"]["allowed_input_extensions"],
        "config_file": C.DEFAULT_CONFIG_FILE if os.path.isfile(C.DEFAULT_CONFIG_FILE) else None,
        "usage": "python3 tools-word-doc-agent.py <operation> --input FILE "
                 "[--output FILE] [--param key=value] [--json]",
    }
    if ctx.params.get("all"):
        result["catalog"] = {
            "categories": [
                {"id": c["id"], "name": c["name"], "purpose": c["purpose"],
                 "skills": len(c["skills"]),
                 "operations": sorted({s["op"] for s in c["skills"]})}
                for c in map_data["categories"]
            ],
            "skills": [{ "category_id": c["id"], "category": c["name"], **s}
                       for c in map_data["categories"] for s in c["skills"]],
        }
    return result


def _file_for_operation(name: str) -> str:
    mapping = {
        "create": "tools-docx-structure.py", "insert": "tools-docx-structure.py",
        "format": "tools-docx-structure.py", "table": "tools-docx-structure.py",
        "image": "tools-docx-structure.py", "headerfooter": "tools-docx-structure.py",
        "watermark": "tools-docx-structure.py", "form": "tools-docx-structure.py",
        "toc": "tools-docx-structure.py", "list": "tools-docx-structure.py",
        "metadata": "tools-docx-edit.py", "replace": "tools-docx-edit.py",
        "styles": "tools-docx-edit.py", "fields": "tools-docx-edit.py",
        "comments": "tools-docx-edit.py", "revisions": "tools-docx-edit.py",
        "sanitize": "tools-docx-edit.py", "protect": "tools-docx-edit.py",
        "unprotect": "tools-docx-edit.py", "merge": "tools-docx-edit.py",
        "copy": "tools-docx-edit.py", "convert": "tools-docx-edit.py",
        "backup": "tools-docx-edit.py", "restore": "tools-docx-edit.py",
        "plan": "tools-docx-edit.py", "route": "tools-docx-edit.py",
        "inspect": "tools-docx-audit.py", "a11y": "tools-docx-audit.py",
        "proof": "tools-docx-audit.py", "qa": "tools-docx-audit.py",
        "score": "tools-docx-audit.py",
        "export": "tools-docx-export.py",
    }
    return mapping.get(name, "tools-word-doc-agent.py")


def op_create(ctx: Context) -> dict:
    output = ctx.output_path()
    structure = ctx.modules["structure"]
    params = dict(ctx.params)
    if "markdown_file" in params:
        path = os.path.abspath(os.path.expanduser(str(params.pop("markdown_file"))))
        if not os.path.isfile(path):
            raise ToolError("NOT_FOUND", "markdown file not found: %s" % path)
        params["markdown"] = open(path, "r", encoding="utf-8-sig").read()
    elif "content_file" in params:
        params["content_file"] = params["content_file"]
    doc, meta = structure.create(ctx.cfg, params)
    doc.save(output)
    integrity = C.integrity_report(output, ctx.cfg)
    integrity = {"valid": integrity["valid"], "parts": integrity["parts"],
                 "issues": integrity["issues"],
                 "warning_count": len(integrity["warnings"])}
    return {"operation": "create", "written": output, "bytes": os.path.getsize(output),
            "format": meta.get("format"), "title": meta.get("title"),
            "blocks": meta.get("blocks"), "template": meta.get("template"),
            "header_footer": meta.get("header_footer"),
            "geometry": meta.get("geometry"),
            "headings": len([b for b in meta.get("blocks") or []
                             if b.get("type") in ("heading",)]),
            "self_check": integrity}


def op_copy(ctx: Context) -> dict:
    output = ctx.output_path()
    return ctx.modules["edit"].save_a_copy(ctx.doc(), output, ctx.params)


def op_convert(ctx: Context) -> dict:
    output = ctx.output_path()
    doc = ctx.doc()
    result = ctx.modules["edit"].convert(doc, ctx.params)
    expected = result["expected_extension"]
    if not output.lower().endswith(expected):
        raise ToolError("USAGE", "output extension must be %s for a %s package"
                        % (expected, result["to"]), {"output": output})
    doc.save(output)
    result["written"] = output
    result["bytes"] = os.path.getsize(output)
    return result


def op_inspect(ctx: Context) -> dict:
    return ctx.modules["audit"].inspect(ctx.doc(), ctx.params, ctx.cfg, ctx.policy)


def op_metadata(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["edit"].metadata(doc, ctx.params)
    if result["changed"]:
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_replace(ctx: Context) -> dict:
    doc = ctx.doc()
    params = dict(ctx.params)
    if ctx.dry_run:
        params["dry_run"] = True
    if "find" not in params:
        raise ToolError("USAGE", "replace needs --param find=TEXT")
    result = ctx.modules["edit"].replace_document(doc, params["find"],
                                                 params.get("replace", ""), params)
    if not result["dry_run"]:
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_insert(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].insert(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_format(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].format_document(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result.update({"operation": "format", "written": output})
    return result


def op_styles(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["edit"].styles(doc, ctx.params)
    if result.get("mode") != "list":
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_fields(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["edit"].fields(doc, ctx.params)
    if result.get("mode") != "list":
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_list(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].lists(doc, ctx.params)
    if result["mode"] != "report":
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_toc(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].toc(doc, ctx.params)
    if result["mode"] in ("insert", "remove"):
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_table(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].table(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_image(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].image(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_headerfooter(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].set_header_footer(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_watermark(ctx: Context) -> dict:
    ctx.require_approval(APPROVAL_REASONS["watermark"])
    doc = ctx.doc()
    remove = str(ctx.params.get("mode", "add")).lower() in ("remove", "delete", "off")
    if ctx.dry_run:
        return {"operation": "watermark", "dry_run": True,
                "would": "remove" if remove else "add",
                "text": ctx.params.get("text")}
    result = ctx.modules["structure"].watermark(doc, ctx.params, remove=remove)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_form(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["structure"].form(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_comments(ctx: Context) -> dict:
    doc = ctx.doc()
    result = ctx.modules["edit"].comments(doc, ctx.params)
    if result.get("mode") != "list":
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_revisions(ctx: Context) -> dict:
    mode = str(ctx.params.get("mode", "report")).lower()
    if mode in ("accept-all", "reject-all"):
        ctx.require_approval(APPROVAL_REASONS["revisions"])
    if ctx.dry_run:
        doc = ctx.doc()
        return {"operation": "revisions", "dry_run": True, "mode": mode,
                **ctx.modules["audit"].revision_inventory(doc)}
    doc = ctx.doc()
    result = ctx.modules["edit"].revisions(doc, {"mode": mode})
    if mode in ("accept-all", "reject-all"):
        output = ctx.output_path()
        doc.save(output)
        result["written"] = output
    return result


def op_sanitize(ctx: Context) -> dict:
    ctx.require_approval(APPROVAL_REASONS["sanitize"])
    doc = ctx.doc()
    if ctx.dry_run:
        return ctx.modules["edit"].sanitize(doc, ctx.params, ctx.policy, apply_changes=False)
    result = ctx.modules["edit"].sanitize(doc, ctx.params, ctx.policy, apply_changes=True)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    result["self_check"] = ctx.modules["audit"].qa_gate(
        C.Docx(output, ctx.cfg), {"report_only": "true"}, ctx.cfg, ctx.policy)["verdict"]
    return result


def op_protect(ctx: Context) -> dict:
    ctx.require_approval(APPROVAL_REASONS["protect"])
    doc = ctx.doc()
    if ctx.dry_run:
        return {"operation": "protect", "dry_run": True, "mode": ctx.params.get("mode")}
    result = ctx.modules["edit"].protect(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_unprotect(ctx: Context) -> dict:
    ctx.require_approval(APPROVAL_REASONS["unprotect"])
    doc = ctx.doc()
    if ctx.dry_run:
        return {"operation": "unprotect", "dry_run": True,
                "protected": ctx.modules["audit"].protection_state(doc)}
    result = ctx.modules["edit"].remove_protection(doc, ctx.params)
    output = ctx.output_path()
    doc.save(output)
    result["written"] = output
    return result


def op_merge(ctx: Context) -> dict:
    doc = ctx.doc()
    output = ctx.output or "."
    directory = os.path.abspath(os.path.expanduser(output))
    os.makedirs(directory, exist_ok=True)
    if ctx.dry_run:
        records = ctx.modules["edit"].load_records(
            ctx.params.get("data_file") or "", ctx.cfg)
        return {"operation": "merge", "dry_run": True, "records": len(records),
                "output_dir": directory}
    return ctx.modules["edit"].merge(doc, ctx.params, output_dir=directory)


def op_export(ctx: Context) -> dict:
    output = ctx.export_output_path()
    fmt = str(ctx.params.get("format", "")).lower()
    ext = os.path.splitext(output)[1].lstrip(".").lower()
    if ext in ctx.cfg["editing"]["allowed_input_extensions"]:
        raise ToolError("USAGE", "export writes non-Word formats; use create/convert for Word "
                                 "packages", {"output": output})
    return ctx.modules["export"].export(ctx.doc(), ctx.params, output, ctx.cfg)


def op_a11y(ctx: Context) -> dict:
    audit = ctx.modules["audit"]
    doc = ctx.doc()
    return {"operation": "a11y", **audit.a11y_audit(doc, ctx.policy)}


def op_proof(ctx: Context) -> dict:
    audit = ctx.modules["audit"]
    doc = ctx.doc()
    result = {"operation": "proof", **audit.proofing(doc, ctx.policy)}
    fail_on = str(ctx.params.get("fail_on", "")).lower()
    if fail_on:
        severities = {s.strip() for s in fail_on.split(",")}
        blocking = [f for f in result["findings"] if f["severity"] in severities]
        result["fail_on"] = sorted(severities)
        result["blocking_findings"] = len(blocking)
        if blocking:
            raise ToolError("POLICY", "proofing found %d blocking finding(s)" % len(blocking),
                            {"result": result})
    return result


def op_qa(ctx: Context) -> dict:
    audit = ctx.modules["audit"]
    doc = ctx.doc()
    result = audit.qa_gate(doc, ctx.params, ctx.cfg, ctx.policy)
    if result["verdict"] == "fail" and not str(result.get("report_only")).endswith("fail"):
        raise ToolError("POLICY", "release gate failed: %s"
                        % ", ".join(result["blocking_checks"] or ["warnings"]),
                        {"result": result})
    return {"operation": "qa", **result}


def op_backup(ctx: Context) -> dict:
    edit = ctx.modules["edit"]
    if str(ctx.params.get("mode", "")).lower() in ("list", "ls"):
        return edit.list_backups(ctx.input, ctx.cfg)
    return edit.backup(ctx.input, ctx.cfg, ctx.params)


def op_restore(ctx: Context) -> dict:
    ctx.require_approval(APPROVAL_REASONS["restore"])
    output = ctx.output_path()
    if ctx.dry_run:
        return {"operation": "restore", "dry_run": True,
                "backups": ctx.modules["edit"].list_backups(ctx.input, ctx.cfg)["backups"][:5]}
    return ctx.modules["edit"].restore(ctx.input, output, ctx.cfg, ctx.params)


def op_route(ctx: Context) -> dict:
    edit = ctx.modules["edit"]
    audit = ctx.modules["audit"]
    query = ctx.params.get("query") or ctx.params.get("q") or ctx.params.get("skill")
    if not query:
        raise ToolError("USAGE", "route needs --param query=\"pause page numbering\"")
    map_data = audit.load_capability_map(ctx.cfg)
    result = edit.route_request(str(query), map_data,
                                limit=int(ctx.params.get("limit", 5)))
    result["operation"] = "route"
    if not result["candidates"]:
        result["hint"] = ("no catalog skill matched; try inspect/inspect --deep for evidence "
                          "or plan --param skill=... with a broader name")
    return result


def op_plan(ctx: Context) -> dict:
    edit = ctx.modules["edit"]
    audit = ctx.modules["audit"]
    skill = ctx.params.get("skill") or ctx.params.get("name") or ctx.input
    if not skill:
        raise ToolError("USAGE", "plan needs --param skill=\"Skill Name\" "
                                 "(use route to find the name)")
    map_data = audit.load_capability_map(ctx.cfg)
    result = edit.build_plan(str(skill), map_data, ctx.params.get("category_id"))
    result["operation"] = "plan"
    if ctx.input:
        doc = ctx.doc()
        result["current_state"] = audit.inspect_summary(
            audit.inspect(doc, {"deep": False}, ctx.cfg, ctx.policy))
    return result


def op_score(ctx: Context) -> dict:
    audit = ctx.modules["audit"]
    map_data = audit.load_capability_map(ctx.cfg)
    return {"operation": "score", **audit.score_document(ctx.doc(), ctx.cfg, map_data)}


HANDLERS = {
    "capabilities": op_capabilities,
    "create": op_create,
    "copy": op_copy,
    "convert": op_convert,
    "inspect": op_inspect,
    "metadata": op_metadata,
    "replace": op_replace,
    "insert": op_insert,
    "format": op_format,
    "styles": op_styles,
    "fields": op_fields,
    "toc": op_toc,
    "list": op_list,
    "table": op_table,
    "image": op_image,
    "headerfooter": op_headerfooter,
    "watermark": op_watermark,
    "form": op_form,
    "comments": op_comments,
    "revisions": op_revisions,
    "sanitize": op_sanitize,
    "protect": op_protect,
    "unprotect": op_unprotect,
    "merge": op_merge,
    "export": op_export,
    "a11y": op_a11y,
    "proof": op_proof,
    "qa": op_qa,
    "backup": op_backup,
    "restore": op_restore,
    "route": op_route,
    "plan": op_plan,
    "score": op_score,
}


# --------------------------------------------------------------------------
# human-readable summaries
# --------------------------------------------------------------------------

def summarize(ctx: Context, result: dict) -> str:
    op = ctx.operation
    lines = []
    if op == "capabilities":
        catalog = result.get("catalog") or {}
        total_categories = catalog.get("categories") if isinstance(catalog.get("categories"), int) else "-"
        total_skills = catalog.get("skills") if isinstance(catalog.get("skills"), int) else "-"
        lines.append("word-document-agent %s  |  %d operations  |  catalog: %s categories, "
                     "%s skills" % (result["version"], result["operation_count"],
                                    total_categories, total_skills))
        lines.append("")
        lines.append("%-14s %-58s %s" % ("OPERATION", "PURPOSE", "SKILLS"))
        for item in result["operations"]:
            lines.append("%-14s %-58s %5d" % (item["operation"], item["summary"],
                                              item["catalog_skills"]))
        lines.append("")
        lines.append("approval required for: %s" % ", ".join(result["approval_required_for"]))
        return "\n".join(lines)
    if op == "inspect":
        audit = ctx.modules["audit"]
        return audit.inspect_summary(result)
    if op == "qa":
        lines.append("verdict: %s" % result["verdict"])
        for check in result["checks"]:
            lines.append("  [%s] %-18s %s" % ("PASS" if check["passed"] else "FAIL",
                                              check["id"], _short(check["detail"])))
        if result["next_actions"]:
            lines.append("next actions:")
            lines.extend("  - " + action for action in result["next_actions"])
        return "\n".join(lines)
    if op in ("a11y", "proof"):
        findings = result["findings"]
        head = ("accessibility: %d finding(s), score %d" % (len(findings), result.get("score", 0))
                if op == "a11y" else "proofing: %d finding(s)" % len(findings))
        lines.append(head)
        for finding in findings[:25]:
            label = finding.get("rule") or finding.get("check")
            lines.append("  [%s] %-22s %s" % (finding["severity"], label,
                                              finding.get("message")
                                              or "%r near %r" % (finding.get("match"),
                                                                 finding.get("context", "")[:60])))
        if len(findings) > 25:
            lines.append("  ... %d more (use --json)" % (len(findings) - 25))
        return "\n".join(lines)
    if op == "route":
        lines.append("query: %s" % result["query"])
        for idx, candidate in enumerate(result["candidates"], start=1):
            lines.append("  %d. %s  [%s] -> %s (%s)" % (idx, candidate["skill"],
                                                        candidate["category"],
                                                        candidate["op"], candidate["engine"]))
        if not result["candidates"]:
            lines.append("  (no match)")
        return "\n".join(lines)
    if op == "plan" and result.get("kind") == "category":
        lines.append("category: %s (%d skills)" % (result["category"], len(result["skills"])))
        lines.append("purpose: %s" % result["purpose"])
        for entry in result["skills"]:
            lines.append("  - %-52s -> %s (%s)" % (entry["skill"], entry["op"],
                                                   entry["engine"]))
        lines.append("automation example: %s" % result["automation"]["command"])
        return "\n".join(lines)
    if op == "plan":
        lines.append("skill: %s  (%s)" % (result["skill"], result["category"]))
        lines.append("engine: %s   implemented by: %s"
                     % (result["engine"], result.get("implementing_operation")))
        lines.append("steps:")
        lines.extend("  %d. %s" % (i, s) for i, s in enumerate(result["steps"], start=1))
        lines.append("automate: %s" % result["automation"]["command"])
        lines.append("verify: %s" % result["verification"][0])
        if result.get("current_state"):
            lines.append("current document:")
            lines.extend("  " + line for line in result["current_state"].split("\n"))
        return "\n".join(lines)
    if op == "score":
        lines.append("catalog coverage: %d categories, %d skills, %d native operations"
                     % (result["categories"], result["skills"],
                        len(result["native_operations"])))
        lines.append("advisory-only skills: %d" % result["advisory_skills"])
        evidenced = [c for c in result["detail"] if c["document_evidence"]]
        lines.append("categories with document evidence: %d (%s)"
                     % (len(evidenced), ", ".join(str(c["id"]) for c in evidenced) or "none"))
        lines.append("document: %s, %d words, a11y score %d"
                     % (result["document"]["format"], result["document"]["words"],
                        result["document"]["a11y_score"]))
        return "\n".join(lines)
    # generic: key/value summary of the result
    for key, value in result.items():
        if key in ("operation",):
            continue
        if isinstance(value, (list, dict)) and value:
            size = len(value)
            lines.append("%s: %d entr%s" % (key, size, "y" if size == 1 else "ies")
                         if isinstance(value, dict) else "%s: %d item(s)" % (key, size))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            lines.append("%s: %s" % (key, value))
    return "\n".join(lines) if lines else "ok"


def _short(detail) -> str:
    text = json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail
    return text if len(text) <= 110 else text[:107] + "..."


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def run(argv) -> int:
    started = time.monotonic()
    json_mode = "--json" in argv
    ctx = None
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        ctx = Context(args)
        json_mode = ctx.json
        if ctx.in_place and not ctx.approve and not ctx.dry_run:
            raise ToolError("APPROVAL_REQUIRED",
                            "writing back onto the input document requires approval",
                            {"approve_with": "--approve", "reason": APPROVAL_REASONS["in-place"]})
        handler = HANDLERS[ctx.operation]
        result = handler(ctx)
        elapsed = int((time.monotonic() - started) * 1000)
        envelope = {"ok": True, "operation": ctx.operation,
                    "elapsed_ms": elapsed, "result": result}
        if ctx.report_file:
            C.atomic_write_text(os.path.abspath(os.path.expanduser(ctx.report_file)),
                                json.dumps(envelope, indent=1, ensure_ascii=False))
            envelope["report_file"] = ctx.report_file
        if json_mode:
            print(json.dumps(envelope, indent=1, ensure_ascii=False, default=str))
        elif not ctx.quiet:
            print(summarize(ctx, result))
        return 0
    except ToolError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        payload = {"ok": False, "operation": getattr(ctx, "operation", None),
                   "elapsed_ms": elapsed, "error": {"code": exc.code,
                                                    "message": exc.message,
                                                    "details": exc.details}}
        if json_mode:
            print(json.dumps(payload, indent=1, ensure_ascii=False, default=str))
        else:
            sys.stderr.write("error [%s]: %s\n" % (exc.code, exc.message))
            if exc.details:
                hint = exc.details.get("hint") or exc.details.get("enable")
                if hint:
                    sys.stderr.write("hint: %s\n" % hint)
        return exc.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("cancelled\n")
        return EXIT_CODES["ERROR"]
    except Exception as exc:  # pragma: no cover - unexpected path
        elapsed = int((time.monotonic() - started) * 1000)
        payload = {"ok": False, "operation": getattr(ctx, "operation", None),
                   "elapsed_ms": elapsed,
                   "error": {"code": "ERROR", "message": "%s: %s" % (type(exc).__name__, exc),
                             "details": {"traceback": traceback.format_exc().splitlines()[-6:]}}}
        if json_mode:
            print(json.dumps(payload, indent=1, ensure_ascii=False, default=str))
        else:
            sys.stderr.write("internal error: %s\n" % exc)
            sys.stderr.write("set --json for a structured report\n")
        return EXIT_CODES["ERROR"]


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
