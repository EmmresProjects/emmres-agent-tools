#!/usr/bin/env python3
"""Presentation Production Suite -- primary entry point.

A production-grade, dependency-light command line tool for inspecting,
auditing, repairing, authoring, governing and validating PowerPoint decks. It is
the reusable Skill Tool component of a larger Copilot skill: it contains no
SKILL.md and no wrapper package.

Quick start
-----------
    python tools-presentation-suite.py help
    python tools-presentation-suite.py capabilities
    python tools-presentation-suite.py audit  --deck deck.pptx --out out/
    python tools-presentation-suite.py render --deck deck.pptx --out out/render --contact-sheet
    python tools-presentation-suite.py repair --deck deck.pptx --out out --scope bounds,type-min --apply

Exit codes
----------
    0  success, no blocking findings
    1  completed, but blocking findings were reported (gate/audit --fail-on)
    2  usage error (bad arguments, unknown engine, invalid configuration)
    3  runtime error (unreadable deck, renderer missing, unsafe operation refused)

All paths in this file are resolved relative to the suite folder, so the folder
can be copied beside a parent SKILL.md without modification.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.dont_write_bytecode = True  # keep the Skill Tool folder free of __pycache__

SUITE_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_NAME = "presentation-production-suite"
TOOL_VERSION = "1.0.0"

ASSET_RULES = "asset-default-rules.json"
ASSET_REGISTRY = "asset-capability-registry.json"
ASSET_CONFIG_SCHEMA = "asset-config.schema.json"
ASSET_DECK_SPEC_SCHEMA = "asset-deck-spec.schema.json"
ASSET_REPORT_TEMPLATE = "asset-report-template.html"

REQUIRED_ASSETS = [ASSET_RULES, ASSET_REGISTRY, ASSET_CONFIG_SCHEMA, ASSET_DECK_SPEC_SCHEMA, ASSET_REPORT_TEMPLATE]


# --------------------------------------------------------------------- loading
def _asset_path(name: str) -> str:
    return os.path.join(SUITE_DIR, name)


def _load_module(filename: str, module_name: str):
    path = _asset_path(filename)
    if not os.path.isfile(path):
        raise SystemExit(f"FATAL: required module missing: {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_suite_modules() -> Dict[str, Any]:
    """Load the supporting modules in dependency order."""
    mods: Dict[str, Any] = {}
    mods["dm"] = _load_module("tools-deckmodel.py", "deckmodel")
    mods["an"] = _load_module("tools-analysis.py", "deckanalysis")
    mods["au"] = _load_module("tools-authoring.py", "decksuthoring")
    mods["rd"] = _load_module("tools-render.py", "deckrender")
    return mods


# ---------------------------------------------------------------------- config
def load_config(path: Optional[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Load the rules config: asset defaults merged with an optional user file."""
    notes: List[str] = []
    with open(_asset_path(ASSET_RULES), "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not path:
        return cfg, notes
    if not os.path.isfile(path):
        raise UsageError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            user = json.load(fh)
        except json.JSONDecodeError as exc:
            raise UsageError(f"config file is not valid JSON: {exc}") from exc
    if not isinstance(user, dict):
        raise UsageError("config file must contain a JSON object")
    known = set(cfg)
    unknown = sorted(set(user) - known - {"overrides", "brand"})
    for key in unknown:
        notes.append(f"unknown config key ignored: {key}")
    for key, value in user.items():
        if key in ("profiles", "sensitive_patterns", "font_fallback_map", "limits", "risk_weights",
                   "score_weights", "dependency_checks") and isinstance(value, dict):
            merged = dict(cfg.get(key, {}))
            merged.update(value)
            cfg[key] = merged
        else:
            cfg[key] = value
    return cfg, notes


def validate_config(cfg: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    required = schema.get("required", [])
    for key in required:
        if key not in cfg:
            errors.append(f"missing required key: {key}")
    for key, spec in (schema.get("properties") or {}).items():
        if key not in cfg:
            continue
        value = cfg[key]
        expect = spec.get("type")
        types = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int,
                 "boolean": bool}
        if expect in types and not isinstance(value, types[expect]):
            errors.append(f"'{key}' must be {expect}, got {type(value).__name__}")
        if expect == "object" and isinstance(value, dict):
            sub_required = spec.get("required", [])
            for r in sub_required:
                if r not in value:
                    errors.append(f"'{key}.{r}' is required")
            for sub_key, sub_spec in (spec.get("properties") or {}).items():
                if sub_key not in value:
                    continue
                want = sub_spec.get("type")
                got = value[sub_key]
                if want == "number" and not isinstance(got, (int, float)):
                    errors.append(f"'{key}.{sub_key}' must be a number")
                elif want == "integer" and (not isinstance(got, int) or isinstance(got, bool)):
                    errors.append(f"'{key}.{sub_key}' must be an integer")
                elif want == "boolean" and not isinstance(got, bool):
                    errors.append(f"'{key}.{sub_key}' must be a boolean")
    profile = cfg.get("profile")
    if profile and profile not in (cfg.get("profiles") or {}):
        errors.append(f"profile '{profile}' is not defined in profiles")
    if not (cfg.get("profiles") or {}):
        errors.append("at least one profile must be defined")
    return errors


# ------------------------------------------------------------------- utilities
class UsageError(Exception):
    pass


class RunError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: str, data: Any) -> str:
    ensure_dir(os.path.dirname(os.path.abspath(path)) or ".")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path: str) -> Any:
    if not os.path.isfile(path):
        raise UsageError(f"file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{path} is not valid JSON: {exc}") from exc


def human_bytes(n: Optional[int]) -> str:
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


class timeout_guard:
    """POSIX wall-clock guard around a long engine run (no-op on Windows)."""

    def __init__(self, seconds: int, label: str = "operation"):
        self.seconds = max(1, int(seconds))
        self.label = label
        self.active = False

    def __enter__(self):
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
            self.active = True
        return self

    def _fire(self, signum, frame):
        raise RunError(f"{self.label} exceeded the {self.seconds}s timeout (config limits.timeout_seconds)")

    def __exit__(self, exc_type, exc, tb):
        if self.active:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return False


# ------------------------------------------------------------------ reporting
def fmt_issue_line(i: Dict[str, Any]) -> str:
    loc = f"slide {i['slide']}" if i.get("slide") else "deck"
    obj = f" [{i['object']}]" if i.get("object") else ""
    return f"  {i['severity'].upper():7} {i['code']:34} {loc}{obj}: {i['message']}"


def summarize_issues(issues: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        out[i["severity"]] = out.get(i["severity"], 0) + 1
    return out


def render_markdown_report(meta: Dict[str, Any], results: Sequence[Dict[str, Any]],
                           issues: Sequence[Dict[str, Any]], scores: Optional[Dict[str, Any]] = None,
                           queue: Optional[Dict[str, Any]] = None,
                           plan: Optional[Dict[str, Any]] = None) -> str:
    L: List[str] = []
    counts = summarize_issues(issues)
    L.append(f"# Presentation audit report: {meta.get('deck_name', 'deck')}")
    L.append("")
    L.append(f"- Generated: {meta.get('generated', '')}")
    L.append(f"- Tool: {SUITE_NAME} {TOOL_VERSION} ({meta.get('command', 'audit')})")
    L.append(f"- Deck: `{meta.get('deck_path', '')}`")
    L.append(f"- Canvas: {meta.get('width_pt', 0):.0f}x{meta.get('height_pt', 0):.0f}pt ({meta.get('aspect', '')})")
    L.append(f"- Slides: {meta.get('slide_count', 0)}   Objects: {meta.get('object_count', 0)}   "
             f"Words: {meta.get('word_count', 0)}")
    L.append(f"- Profile: `{meta.get('profile', '')}`   SHA-256: `{meta.get('sha256', '')[:16]}`")
    L.append("")
    L.append("## Verdict")
    L.append("")
    L.append(f"| Severity | Count |\n|---|---:|\n| error | {counts['error']} |\n| warning | {counts['warning']} |"
             f"\n| info | {counts['info']} |")
    L.append("")
    if scores:
        L.append("## Scorecards")
        L.append("")
        L.append("| Dimension | Score | Findings | Errors |\n|---|---:|---:|---:|")
        for key in ("readability", "consistency", "accessibility", "structure", "evidence", "security"):
            if key in scores:
                L.append(f"| {key} | {scores[key]['score']} | {scores[key]['issue_count']} | {scores[key]['errors']} |")
        L.append(f"| **overall** | **{scores.get('overall')}** | | |")
        L.append("")
    L.append("## Engine results")
    L.append("")
    L.append("| Engine | Result | Findings |\n|---|---|---:|")
    for r in results:
        L.append(f"| {r['engine']} | {r['summary']} | {len(r['issues'])} |")
    L.append("")
    if queue and queue.get("queue"):
        L.append("## Repair queue (priority order)")
        L.append("")
        L.append("| Priority | Code | Severity | Count | Slides | Suggested repair |")
        L.append("|---:|---|---|---:|---|---|")
        for q in queue["queue"][:25]:
            slides = ", ".join(str(s) for s in q["slides"][:8]) or "-"
            L.append(f"| {q['priority']} | {q['code']} | {q['severity']} | {q['count']} | {slides} | {q['repair'] or '-'} |")
        L.append("")
    if plan and plan.get("plan"):
        L.append("## Suggested execution plan")
        L.append("")
        for step in plan["plan"]:
            L.append(f"{step['order']}. **{step['step']}** — {step['reason']}")
            L.append("")
            L.append(f"   `python tools-presentation-suite.py {step['command']}`")
            L.append("")
    by_engine: Dict[str, List[Dict[str, Any]]] = {}
    for i in issues:
        by_engine.setdefault(i["code"], []).append(i)
    if by_engine:
        L.append("## Findings detail")
        L.append("")
        for code in sorted(by_engine, key=lambda c: (-len(by_engine[c]), c)):
            group = by_engine[code]
            L.append(f"### {code} ({len(group)}×, {group[0]['severity']})")
            L.append("")
            L.append(f"{group[0]['message']}")
            if group[0].get("repair"):
                L.append("")
                L.append(f"_Repair:_ {group[0]['repair']}")
            L.append("")
            for i in group[:12]:
                loc = f"slide {i['slide']}" if i.get("slide") else "deck"
                obj = f", {i['object']}" if i.get("object") else ""
                L.append(f"- {loc}{obj}: {i['message']}")
            if len(group) > 12:
                L.append(f"- ... {len(group) - 12} more (see audit.json)")
            L.append("")
    limits = sorted({lim for r in results for lim in r.get("limits", [])})
    if limits:
        L.append("## Measurement limits for this run")
        L.append("")
        for lim in limits:
            L.append(f"- {lim}")
        L.append("")
    L.append("## How to reproduce")
    L.append("")
    L.append("```sh")
    L.append(f"python tools-presentation-suite.py audit --deck \"{meta.get('deck_path', '')}\" "
             f"--out out --profile {meta.get('profile', 'conference-room')}")
    L.append("```")
    L.append("")
    return "\n".join(L)


def render_html_report(markdown_text: str, meta: Dict[str, Any], template: str,
                       contact_sheet: Optional[str] = None) -> str:
    """Render the HTML report from the shipped template with a small, dependency-free
    markdown subset (headings, lists, tables, code fences, paragraphs). Table rows
    are wrapped in <table>/<tbody> and the first row becomes a <thead>."""
    import html as htmllib

    def esc(text: str) -> str:
        out = htmllib.escape(text)
        # inline code spans and bold
        out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
        out = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", out)
        out = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", lambda m: f"<em>{m.group(1)}</em>", out)
        return out

    lines = markdown_text.splitlines()
    body: List[str] = []
    in_code = False
    in_list = False
    table_rows: List[List[str]] = []

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        head, rest = table_rows[0], table_rows[1:]
        body.append("<table><thead><tr>" + "".join(f"<th>{esc(c)}</th>" for c in head) + "</tr></thead><tbody>")
        for row in rest:
            body.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>")
        body.append("</tbody></table>")
        table_rows = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_table(); close_list()
            body.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            body.append(htmllib.escape(line))
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(c and set(c) <= set("-: ") for c in cells):
                continue  # separator row
            table_rows.append(cells)
            continue
        flush_table()
        if not stripped:
            close_list()
            continue
        if stripped.startswith("### "):
            close_list(); body.append(f"<h3>{esc(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            close_list(); body.append(f"<h2>{esc(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            close_list(); body.append(f"<h1>{esc(stripped[2:])}</h1>")
        elif re.match(r"^[-*] ", stripped):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{esc(stripped[2:])}</li>")
        elif re.match(r"^\d+\. ", stripped):
            close_list()
            body.append(f"<p class='step'>{esc(stripped)}</p>")
        else:
            close_list()
            body.append(f"<p>{esc(stripped)}</p>")
    flush_table()
    close_list()
    if in_code:
        body.append("</pre>")

    sheet = ""
    if contact_sheet and os.path.isfile(contact_sheet):
        try:
            import base64
            with open(contact_sheet, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            sheet = f'<h2>Contact sheet</h2><img alt="Contact sheet of all slides" src="data:image/png;base64,{b64}" />'
        except Exception:
            sheet = ""
    out = template
    out = out.replace("{{TITLE}}", htmllib.escape(f"Presentation audit: {meta.get('deck_name', 'deck')}"))
    out = out.replace("{{SUBTITLE}}", htmllib.escape(
        f"{meta.get('slide_count', 0)} slides · {meta.get('aspect', '')} · profile {meta.get('profile', '')} · "
        f"generated {meta.get('generated', '')}"))
    out = out.replace("{{SUMMARY}}", htmllib.escape(meta.get("verdict", "")))
    out = out.replace("{{CONTACT_SHEET}}", sheet)
    out = out.replace("{{BODY}}", "\n".join(body))
    out = out.replace("{{TOOL}}", f"{SUITE_NAME} {TOOL_VERSION}")
    return out


# ------------------------------------------------------------- engine runners
def run_engines(mods: Dict[str, Any], deck, cfg: Dict[str, Any], names: Sequence[str],
                baseline: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    an = mods["an"]
    results: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    timeout = int((cfg.get("limits") or {}).get("timeout_seconds", 120))
    for name in names:
        if name in ("regression",):
            with timeout_guard(timeout, f"engine {name}"):
                res = an.engine_regression(deck, cfg, before=baseline)
        elif name == "plan":
            continue  # computed after all issues are known
        else:
            fn = an.ENGINES.get(name)
            if fn is None:
                raise UsageError(f"unknown engine '{name}' (run: capabilities)")
            with timeout_guard(timeout, f"engine {name}"):
                res = fn(deck, cfg)
        results.append(res)
        issues.extend(res["issues"])
    return results, issues


AUDIT_BUNDLE = [
    "package", "census", "layouts", "editable", "hidden", "metadata", "assets", "dependencies", "intent",
    "canvas", "zones", "safe-margin", "bounds", "rotation", "whitespace", "overlap",
    "alignment", "distribution", "spacing-tokens", "title-zone", "footer-zone",
    "fonts", "type-inheritance", "min-type", "type-scale", "line-length", "overflow", "emphasis", "rhythm",
    "palette", "contrast", "color-drift", "transparency", "monochrome", "color-semantics",
    "shapes", "connectors", "tables", "charts", "images", "av", "animation",
    "reading-order", "alt-text", "color-independence", "links", "language",
    "families", "bullets", "titles", "terminology", "redundancy", "abbreviations", "sequence",
    "citations", "freshness", "brand",
    "sensitive", "metadata-privacy", "macros",
]


def deck_meta(deck, cfg: Dict[str, Any], command: str, path: str) -> Dict[str, Any]:
    return {
        "generated": now_iso(),
        "tool": SUITE_NAME,
        "version": TOOL_VERSION,
        "command": command,
        "deck_path": path,
        "deck_name": os.path.basename(path),
        "sha256": sha256_file(path) if os.path.isfile(path) else "",
        "profile": cfg.get("profile"),
        "slide_count": len(deck.slides),
        "object_count": sum(len(s.objects) for s in deck.slides),
        "word_count": sum(s.words for s in deck.slides),
        "width_pt": round(deck.width_pt, 2),
        "height_pt": round(deck.height_pt, 2),
        "aspect": deck.aspect,
    }


def load_deck_checked(mods, path: str, cfg: Dict[str, Any]):
    dm = mods["dm"]
    limits = cfg.get("limits") or {}
    if not os.path.isfile(path):
        raise UsageError(f"deck not found: {path}")
    size = os.path.getsize(path)
    if size > int(limits.get("max_file_bytes", 524288000)):
        raise RunError(f"deck is {human_bytes(size)}, above the configured limit "
                       f"({human_bytes(int(limits.get('max_file_bytes', 524288000)))})")
    deck = dm.load_deck(path, cfg)
    max_slides = int(limits.get("max_slides", 2000))
    if len(deck.slides) > max_slides:
        raise RunError(f"deck has {len(deck.slides)} slides, above the configured limit ({max_slides})")
    return deck


# ------------------------------------------------------------------- commands
def cmd_help(args, mods, ctx) -> int:
    if args.topic:
        topic = args.topic
        for name, spec in COMMANDS.items():
            if name == topic:
                print(f"{name} -- {spec['summary']}\n")
                print("Usage:")
                print(f"  {spec['usage']}\n")
                print(spec["details"])
                return 0
        raise UsageError(f"no help topic '{topic}'")
    print(__doc__.strip())
    print("\nCommands (use: help <command>):")
    for name, spec in COMMANDS.items():
        print(f"  {name:16} {spec['summary']}")
    print("\nRun 'capabilities' for the machine-readable command + engine list.")
    return 0


def cmd_capabilities(args, mods, ctx) -> int:
    an = mods["an"]
    registry_path = _asset_path(ASSET_REGISTRY)
    registry = read_json(registry_path) if os.path.isfile(registry_path) else {"entries": []}
    entries = registry.get("entries", [])
    by_status: Dict[str, int] = {}
    for e in entries:
        by_status[e.get("status", "unknown")] = by_status.get(e.get("status", "unknown"), 0) + 1
    data = {
        "tool": SUITE_NAME,
        "version": TOOL_VERSION,
        "python": sys.version.split()[0],
        "commands": [{"name": k, "summary": v["summary"], "usage": v["usage"], "approval": v.get("approval", False)}
                     for k, v in COMMANDS.items()],
        "engines": [{"name": k, "summary": an.ENGINE_DESCRIPTIONS.get(k, ""),
                     "group": next((g for g, names in an.ENGINE_GROUPS.items() if k in names), "other")}
                    for k in an.ENGINES],
        "engine_count": len(an.ENGINES),
        "catalog": {
            "categories": registry.get("catalog_categories"),
            "capabilities_total": len(entries),
            "by_status": by_status,
            "registry_version": registry.get("version"),
        },
        "assets": [a for a in REQUIRED_ASSETS if os.path.isfile(_asset_path(a))],
        "dependencies": {
            "required": ["python>=3.9", "python-pptx>=0.6.21"],
            "optional": ["Pillow>=9 (rendering)", "jsonschema (schema validation)"],
        },
    }
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"{SUITE_NAME} {TOOL_VERSION} on Python {data['python']}\n")
    print("COMMANDS")
    for c in data["commands"]:
        flag = " [approval]" if c["approval"] else ""
        print(f"  {c['name']:16} {c['summary']}{flag}")
    print(f"\nENGINES ({len(an.ENGINES)})")
    for group, names in an.ENGINE_GROUPS.items():
        print(f"  {group}:")
        for n in names:
            if n in an.ENGINES:
                print(f"    {n:20} {an.ENGINE_DESCRIPTIONS.get(n, '')}")
    cat = data["catalog"]
    print(f"\nCATALOG COVERAGE: {cat['capabilities_total']} catalogued capabilities across "
          f"{cat['categories']} categories -> {cat['by_status']}")
    print("\nDEPENDENCIES")
    print(f"  required: {', '.join(data['dependencies']['required'])}")
    print(f"  optional: {', '.join(data['dependencies']['optional'])}")
    return 0


def cmd_inspect(args, mods, ctx) -> int:
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    names = ["package", "census", "layouts", "editable", "hidden", "metadata", "assets", "dependencies",
             "canvas", "coordinates", "zones", "families", "palette", "fonts"]
    results, issues = run_engines(mods, deck, ctx["cfg"], names)
    meta = deck_meta(deck, ctx["cfg"], "inspect", args.deck)
    payload = {"meta": meta, "model": deck.to_dict(), "engines": results, "issues": issues,
               "issue_counts": summarize_issues(issues)}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "inspect.json"), payload)
        print(f"wrote {os.path.join(args.out, 'inspect.json')}")
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k not in ("model",)}, indent=2, default=str))
    elif not ctx["quiet"]:
        print(f"Inspected {meta['deck_name']}: {meta['slide_count']} slides, {meta['object_count']} objects, "
              f"{meta['width_pt']:.0f}x{meta['height_pt']:.0f}pt ({meta['aspect']})")
        for r in results:
            print(f"  {r['engine']:22} {r['summary']}")
        counts = payload["issue_counts"]
        print(f"\nFindings: {counts['error']} error, {counts['warning']} warning, {counts['info']} info")
        for i in [x for x in issues if x["severity"] == "error"][:12]:
            print(fmt_issue_line(i))
    return 0


def _audit_payload(mods, deck, cfg, baseline, command, deck_path) -> Dict[str, Any]:
    an = mods["an"]
    names = list(AUDIT_BUNDLE)
    if baseline:
        names.append("regression")
    results, issues = run_engines(mods, deck, cfg, names, baseline=baseline)
    scores = an.engine_scores(deck, cfg, issues)["data"]
    queue = an.engine_defect_queue(deck, cfg, issues)["data"]
    plan = an.engine_work_plan(deck, cfg, issues)["data"]
    gate = an.engine_completion_gate(deck, cfg, issues)["data"]
    meta = deck_meta(deck, cfg, command, deck_path)
    meta["verdict"] = ("BLOCKED: {e} error(s) must be fixed".format(e=summarize_issues(issues)["error"])
                       if not gate["passed"] else "PASS: no blocking findings")
    return {"meta": meta, "engines": results, "issues": issues, "scores": scores,
            "defect_queue": queue, "plan": plan, "gate": gate,
            "issue_counts": summarize_issues(issues)}


def cmd_audit(args, mods, ctx) -> int:
    cfg = ctx["cfg"]
    if args.profile:
        if args.profile not in (cfg.get("profiles") or {}):
            raise UsageError(f"unknown profile '{args.profile}'; available: {', '.join(cfg.get('profiles', {}))}")
        cfg = dict(cfg)
        cfg["profile"] = args.profile
    deck = load_deck_checked(mods, args.deck, cfg)
    baseline = read_json(args.baseline) if args.baseline else None
    if baseline and "model" not in baseline and "slides" not in baseline:
        raise UsageError("baseline must be an inspect.json (with a 'model' key) or a neutral model JSON")
    model_baseline = baseline.get("model") if baseline and "model" in baseline else baseline
    payload = _audit_payload(mods, deck, cfg, model_baseline, "audit", args.deck)
    counts = payload["issue_counts"]
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "audit.json"), payload)
        md = render_markdown_report(payload["meta"], payload["engines"], payload["issues"],
                                    payload["scores"], payload["defect_queue"], payload["plan"])
        with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        with open(_asset_path(ASSET_REPORT_TEMPLATE), "r", encoding="utf-8") as fh:
            tpl = fh.read()
        html_doc = render_html_report(md, payload["meta"], tpl)
        with open(os.path.join(args.out, "report.html"), "w", encoding="utf-8") as fh:
            fh.write(html_doc)
        print(f"wrote {os.path.join(args.out, 'audit.json')}, report.md, report.html")
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not ctx["quiet"]:
        meta = payload["meta"]
        print(f"Audit: {meta['deck_name']} — {meta['slide_count']} slides — profile {meta['profile']}")
        print(f"Verdict: {meta['verdict']}")
        print(f"Overall score: {payload['scores']['overall']}/100 "
              f"(readability {payload['scores']['readability']['score']}, "
              f"consistency {payload['scores']['consistency']['score']}, "
              f"accessibility {payload['scores']['accessibility']['score']}, "
              f"structure {payload['scores']['structure']['score']}, "
              f"evidence {payload['scores']['evidence']['score']})")
        print(f"Findings: {counts['error']} error, {counts['warning']} warning, {counts['info']} info")
        print("\nTop defect classes:")
        for q in payload["defect_queue"]["queue"][:10]:
            print(f"  {q['priority']:5.1f} {q['code']:34} {q['count']:4}x {q['message'][:74]}")
    fail_on = args.fail_on
    if fail_on == "none":
        return 0
    if fail_on == "error" and counts["error"]:
        return 1
    if fail_on == "warning" and (counts["error"] or counts["warning"]):
        return 1
    return 0


def cmd_analyze(args, mods, ctx) -> int:
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    names = [n.strip() for n in args.engine.split(",") if n.strip()]
    results, issues = run_engines(mods, deck, ctx["cfg"], names)
    payload = {"meta": deck_meta(deck, ctx["cfg"], "analyze", args.deck), "engines": results, "issues": issues}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "analysis.json"), payload)
        print(f"wrote {os.path.join(args.out, 'analysis.json')}")
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not ctx["quiet"]:
        for r in results:
            print(f"{r['engine']}: {r['summary']}")
            for i in r["issues"][:args.limit]:
                print(fmt_issue_line(i))
            if len(r["issues"]) > args.limit:
                print(f"  ... {len(r['issues']) - args.limit} more")
    return 0


def cmd_report(args, mods, ctx) -> int:
    payload = read_json(args.audit)
    for key in ("meta", "engines", "issues"):
        if key not in payload:
            raise UsageError(f"audit file is missing '{key}'; regenerate it with the audit command")
    md = render_markdown_report(payload["meta"], payload["engines"], payload["issues"],
                                payload.get("scores"), payload.get("defect_queue"), payload.get("plan"))
    out_dir = args.out or "."
    ensure_dir(out_dir)
    md_path = os.path.join(out_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    written = [md_path]
    if not args.markdown_only:
        with open(_asset_path(ASSET_REPORT_TEMPLATE), "r", encoding="utf-8") as fh:
            tpl = fh.read()
        html_path = os.path.join(out_dir, "report.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(render_html_report(md, payload["meta"], tpl, args.contact_sheet))
        written.append(html_path)
    print("wrote " + ", ".join(written))
    return 0


def cmd_render(args, mods, ctx) -> int:
    rd = mods["rd"]
    if not rd.PIL_AVAILABLE:
        raise RunError("Pillow is not installed; run: pip install Pillow")
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    out_dir = args.out or "render"
    slides = [int(x) for x in args.slides.split(",")] if args.slides else None
    th = mods["an"].thresholds(ctx["cfg"])
    manifest = rd.render_deck(deck, out_dir, width_px=args.width, show_text=not args.no_text,
                              show_labels=args.labels,
                              safe_area_pt=float(th.get("safe_margin_pt", 28)) if args.safe_area else None,
                              fmt=args.format, slides=slides)
    payload = {"meta": deck_meta(deck, ctx["cfg"], "render", args.deck), "render": manifest}
    if args.contact_sheet:
        sheet = os.path.join(out_dir, "contact-sheet.png")
        payload["contact_sheet"] = rd.make_contact_sheet(deck, sheet, thumb_width=args.thumb_width)
        if not ctx["quiet"]:
            print(f"contact sheet: {sheet}")
    write_json(os.path.join(out_dir, "render.json"), payload)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not ctx["quiet"]:
        print(f"Rendered {manifest['count']} slide(s) at {args.width}px into {out_dir}")
        print("NOTE: geometry-model rendering; text metrics are approximate (see knowledge-readme.md).")
    return 0


def cmd_contact_sheet(args, mods, ctx) -> int:
    rd = mods["rd"]
    if not rd.PIL_AVAILABLE:
        raise RunError("Pillow is not installed; run: pip install Pillow")
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    out_path = args.out or "contact-sheet.png"
    info = rd.make_contact_sheet(deck, out_path, thumb_width=args.width, cols=args.cols)
    print(f"wrote {info['path']} ({info['slides']} slides, {info['width']}x{info['height']}px)")
    return 0


def cmd_snapshot(args, mods, ctx) -> int:
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    out_dir = ensure_dir(args.out or "snapshot")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model = deck.to_dict()
    write_json(os.path.join(out_dir, "model.json"), model)
    results, issues = run_engines(mods, deck, ctx["cfg"], ["package", "census", "bounds", "hidden", "a11y"])
    payload = {
        "meta": deck_meta(deck, ctx["cfg"], "snapshot", args.deck),
        "label": args.label or stamp,
        "model": model,
        "model_sha256": hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest(),
        "issues": issues,
        "issue_counts": summarize_issues(issues),
        "deck_copy": None,
    }
    if args.copy and os.path.isfile(args.deck):
        dest = os.path.join(out_dir, f"last-known-good{os.path.splitext(args.deck)[1]}")
        shutil.copy2(args.deck, dest)
        payload["deck_copy"] = dest
        payload["deck_sha256"] = sha256_file(dest)
    write_json(os.path.join(out_dir, "snapshot.json"), payload)
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "model"}, indent=2, default=str))
    else:
        print(f"snapshot written to {out_dir} (label: {payload['label']})")
        if payload["deck_copy"]:
            print(f"last-known-good copy: {payload['deck_copy']}")
    return 0


def _open_for_edit(deck_path: str) -> Any:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RunError("python-pptx is required to edit decks (pip install python-pptx)") from exc
    if not deck_path.lower().endswith((".pptx", ".pptm")):
        raise RunError("editing requires a .pptx/.pptm package (JSON models are read-only)")
    return Presentation(deck_path)


def _target_paths(args) -> Tuple[str, str]:
    """Resolve (work_copy, final_out). work_copy is where edits are written."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.splitext(os.path.basename(args.deck))[0]
    if args.out:
        if args.out.lower().endswith((".pptx", ".pptm")):
            return args.out, args.out
        ensure_dir(args.out)
        return os.path.join(args.out, f"{base}-{args.command}-{stamp}.pptx"), args.out
    return os.path.join(SUITE_DIR, f"{base}-{args.command}.pptx"), SUITE_DIR


def _approval_needed(scopes: Sequence[str], mods) -> List[str]:
    au = mods["au"]
    table = dict(au.OPERATIONS)
    table.update(dict(au.NORMALIZERS))
    return [s for s in scopes if table.get(s, (None, False, ""))[1]]


def _run_edits(args, mods, ctx, scopes: Sequence[str], kind: str) -> int:
    au = mods["au"]
    an = mods["an"]
    dm = mods["dm"]
    cfg = ctx["cfg"]
    th = an.thresholds(cfg)
    table = dict(au.OPERATIONS) if kind == "repair" else dict(au.NORMALIZERS)
    unknown = [s for s in scopes if s not in table]
    if unknown:
        raise UsageError(f"unknown {kind} scope(s): {', '.join(unknown)}; available: {', '.join(sorted(table))}")
    needs_approval = _approval_needed(scopes, mods)
    if needs_approval and not args.approve:
        raise UsageError(
            "approval required for: " + ", ".join(needs_approval) +
            " (these change appearance/appearance-derived content irreversibly). "
            "Re-run with --approve after reviewing the dry-run output.")
    work_path, out_dir = _target_paths(args)
    dry = not args.apply
    deck = load_deck_checked(mods, args.deck, cfg)
    prs = _open_for_edit(args.deck)
    changes: List[Dict[str, Any]] = []
    applied: List[Dict[str, Any]] = []
    for scope in scopes:
        fn = table[scope][0]
        with timeout_guard(int((cfg.get("limits") or {}).get("timeout_seconds", 120)), f"{kind} {scope}"):
            try:
                n = fn(prs, deck, cfg, changes, th)
            except au.AuthoringError as exc:
                raise RunError(f"{kind} scope '{scope}' refused: {exc}") from exc
            except Exception as exc:
                raise RunError(f"{kind} scope '{scope}' failed: {exc}") from exc
        applied.append({"scope": scope, "changes": n})
    payload = {
        "meta": deck_meta(deck, cfg, f"{kind}", args.deck),
        "kind": kind,
        "scopes": applied,
        "dry_run": dry,
        "change_count": len(changes),
        "changes": changes[:800],
        "truncated": len(changes) > 800,
        "output": None,
        "verification": None,
    }
    if dry:
        if args.out:
            ensure_dir(args.out)
            write_json(os.path.join(args.out, f"{kind}-preview.json"), payload)
            print(f"dry run: wrote {os.path.join(args.out, kind + '-preview.json')} ({len(changes)} planned change(s))")
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        elif not ctx["quiet"]:
            print(f"DRY RUN ({kind}) — {len(changes)} planned change(s); nothing written.")
            for a in applied:
                print(f"  {a['scope']:16} {a['changes']} change(s)")
            for c in changes[:15]:
                print(f"  - {json.dumps(c, default=str)[:150]}")
            print("\nRe-run with --apply to write the result.")
        return 0

    # real write path: snapshot first, then save, then re-verify
    snap_dir = os.path.join(out_dir, "last-known-good")
    ensure_dir(snap_dir)
    backup = os.path.join(snap_dir, os.path.basename(args.deck))
    if os.path.abspath(backup) != os.path.abspath(args.deck):
        shutil.copy2(args.deck, backup)
    prs.save(work_path)
    new_deck = dm.load_deck(work_path, cfg)
    after_issues = []
    for name in ("package", "bounds", "hidden", "min-type", "overflow", "a11y", "contrast"):
        res = an.ENGINES[name](new_deck, cfg)
        after_issues.extend(res["issues"])
    before_issues = []
    for name in ("package", "bounds", "hidden", "min-type", "overflow", "a11y", "contrast"):
        before_issues.extend(an.ENGINES[name](deck, cfg)["issues"])
    verify = {
        "before": summarize_issues(before_issues),
        "after": summarize_issues(after_issues),
        "package_health_ok": bool(new_deck.health.get("ok")),
        "introduced_errors": sorted(
            {i["code"] for i in after_issues if i["severity"] == "error"} -
            {i["code"] for i in before_issues if i["severity"] == "error"}),
    }
    payload["output"] = work_path
    payload["backup"] = backup
    payload["verification"] = verify
    write_json(os.path.join(out_dir, f"{kind}-changes.json"), payload)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not ctx["quiet"]:
        print(f"applied {len(changes)} change(s) -> {work_path}")
        print(f"backup: {backup}")
        print(f"verification: {verify['before']} -> {verify['after']}; package healthy={verify['package_health_ok']}")
        if verify["introduced_errors"]:
            print(f"WARNING new error classes introduced: {verify['introduced_errors']}")
            return 3
    return 0


def cmd_repair(args, mods, ctx) -> int:
    scopes = [s.strip() for s in args.scope.split(",") if s.strip()]
    return _run_edits(args, mods, ctx, scopes, "repair")


def cmd_normalize(args, mods, ctx) -> int:
    scopes = [s.strip() for s in args.scope.split(",") if s.strip()]
    return _run_edits(args, mods, ctx, scopes, "normalize")


def _run_governance_edits(args, mods, ctx, actions: Sequence[str]) -> int:
    """Apply governance actions with the same dry-run/approval/verify contract as repair."""
    au = mods["au"]
    an = mods["an"]
    dm = mods["dm"]
    cfg = ctx["cfg"]
    th = an.thresholds(cfg)
    table = {"scrub-metadata": (au.scrub_metadata, False, "clear personal document properties"),
             "strip-notes": (au.strip_notes, True, "delete all speaker notes"),
             "redact": (au.redact_matches, True, "replace sensitive matches with [REDACTED]")}
    unknown = [a for a in actions if a not in table]
    if unknown:
        raise UsageError(f"unknown governance action(s): {', '.join(unknown)}; available: {', '.join(sorted(table))}")
    needs = [a for a in actions if table[a][1]]
    if needs and not args.approve:
        raise UsageError("approval required for: " + ", ".join(needs) +
                         " (destructive or irreversible content changes). Re-run with --approve.")
    work_path, out_dir = _target_paths(args)
    deck = load_deck_checked(mods, args.deck, cfg)
    prs = _open_for_edit(args.deck)
    changes: List[Dict[str, Any]] = []
    applied = []
    for action in actions:
        fn = table[action][0]
        n = fn(prs, deck, cfg, changes, th)
        applied.append({"action": action, "changes": n})
    payload = {"meta": deck_meta(deck, cfg, "governance", args.deck), "actions": applied,
               "dry_run": not args.apply, "change_count": len(changes),
               "changes": changes[:500], "output": None, "verification": None}
    if not args.apply:
        if args.out:
            ensure_dir(args.out)
            write_json(os.path.join(args.out, "governance-preview.json"), payload)
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        elif not ctx["quiet"]:
            print(f"DRY RUN (governance) — {len(changes)} planned change(s); nothing written.")
            for a in applied:
                print(f"  {a['action']:16} {a['changes']} change(s)")
        return 0
    snap_dir = os.path.join(out_dir, "last-known-good")
    ensure_dir(snap_dir)
    shutil.copy2(args.deck, os.path.join(snap_dir, os.path.basename(args.deck)))
    prs.save(work_path)
    new_deck = dm.load_deck(work_path, cfg)
    before = []
    after = []
    for name in ("sensitive", "metadata", "metadata-privacy", "macros"):
        before.extend(an.ENGINES[name](deck, cfg)["issues"])
        after.extend(an.ENGINES[name](new_deck, cfg)["issues"])
    verify = {"before": summarize_issues(before), "after": summarize_issues(after),
              "package_health_ok": bool(new_deck.health.get("ok"))}
    payload["output"] = work_path
    payload["verification"] = verify
    write_json(os.path.join(out_dir, "governance-changes.json"), payload)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not ctx["quiet"]:
        print(f"applied {len(changes)} governance change(s) -> {work_path}")
        print(f"verification: {verify['before']} -> {verify['after']}")
    return 0


def cmd_governance(args, mods, ctx) -> int:
    au = mods["au"]
    actions = [a.strip() for a in args.action.split(",") if a.strip()]
    table = {"scrub-metadata": au.scrub_metadata, "strip-notes": au.strip_notes, "redact": au.redact_matches}
    unknown = [a for a in actions if a not in table]
    if unknown:
        raise UsageError(f"unknown governance action(s): {', '.join(unknown)}; available: {', '.join(table)}")
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    if args.hide or args.show:
        hide = [int(x) for x in args.hide.split(",")] if args.hide else []
        show = [int(x) for x in args.show.split(",")] if args.show else []
        for idx in hide + show:
            if not any(s.index == idx for s in deck.slides):
                raise UsageError(f"slide {idx} does not exist (deck has {len(deck.slides)} slides)")
        if not args.apply:
            print(f"DRY RUN: would set visibility for hide={hide} show={show}")
            return 0
        prs = _open_for_edit(args.deck)
        changes: List[Dict[str, Any]] = []
        n = au.set_hidden_slides(prs, deck, ctx["cfg"], changes, mods["an"].thresholds(ctx["cfg"]),
                                 show=show, hide=hide)
        out = args.out if (args.out or "").lower().endswith(".pptx") else os.path.join(
            args.out or ".", os.path.splitext(os.path.basename(args.deck))[0] + "-visibility.pptx")
        ensure_dir(os.path.dirname(os.path.abspath(out)))
        prs.save(out)
        write_json(os.path.join(os.path.dirname(os.path.abspath(out)), "governance-changes.json"),
                   {"actions": ["visibility"], "changes": changes, "output": out})
        print(f"updated slide visibility ({n} change(s)) -> {out}")
        return 0
    if not actions:
        raise UsageError("specify --action and/or --hide/--show")
    return _run_governance_edits(args, mods, ctx, actions)


def cmd_author(args, mods, ctx) -> int:
    au = mods["au"]
    an = mods["an"]
    cfg = ctx["cfg"]
    th = an.thresholds(cfg)
    if args.spec:
        spec = read_json(args.spec)
        _validate_deck_spec(spec, read_json(_asset_path(ASSET_DECK_SPEC_SCHEMA)))
        slides = spec.get("slides", [])
        brand = (spec.get("theme") or {}).get("accent1")
    elif args.type:
        slides = [{"type": args.type, "title": args.title or "Untitled",
                   "bullets": [b.strip() for b in (args.bullets or "").split("|") if b.strip()],
                   "claim": args.claim or "", "footnote": args.footnote or "",
                   "cards": json.loads(args.cards) if args.cards else [],
                   "steps": [s.strip() for s in (args.steps or "").split("|") if s.strip()],
                   "subtitle": args.subtitle or "", "presenter": args.presenter or ""}]
        brand = None
    else:
        raise UsageError("author needs either --spec <deck-spec.json> or --type <title|content|kpi|process>")
    out = args.out or "authored.pptx"
    if not out.lower().endswith(".pptx"):
        raise UsageError("--out for author must be a .pptx path")
    if os.path.exists(out) and not args.force:
        raise UsageError(f"{out} already exists; pass --force to overwrite")
    if not au.PPTX_AVAILABLE:
        raise RunError("python-pptx is required to author presentations "
                       "(python -m pip install \"python-pptx>=0.6.21\")")
    from pptx import Presentation
    from pptx.util import Emu
    if args.base:
        if not os.path.isfile(args.base):
            raise UsageError(f"base deck not found: {args.base}")
        prs = Presentation(args.base)
        base_deck = mods["dm"].load_deck(args.base, cfg)
    else:
        prs = Presentation()
        prs.slide_width = Emu(int(13.333 * 914400))
        prs.slide_height = Emu(int(7.5 * 914400))
        base_deck = mods["dm"].deck_from_dict({"width_pt": 960, "height_pt": 540, "slides": [],
                                               "theme_colors": {"accent1": "1F5C9E", "lt1": "FFFFFF"}})
    if not prs.slides:
        # an empty base still needs a layout for python-pptx; drop the auto-slide if created
        pass
    changes: List[Dict[str, Any]] = []
    ensure_dir(os.path.dirname(os.path.abspath(out)))
    built = 0
    for spec_slide in slides:
        kind = spec_slide.get("type", "content")
        title = spec_slide.get("title", "")
        if kind == "title":
            au.build_title_slide(prs, base_deck, changes, th, title=title,
                                 subtitle=spec_slide.get("subtitle", ""),
                                 presenter=spec_slide.get("presenter", ""),
                                 date_text=spec_slide.get("date", ""), brand_hex=brand)
        elif kind == "kpi":
            au.build_kpi_slide(prs, base_deck, changes, th, title=title,
                               cards=spec_slide.get("cards", []),
                               footnote=spec_slide.get("footnote", ""))
        elif kind == "process":
            au.build_process_slide(prs, base_deck, changes, th, title=title,
                                   steps=spec_slide.get("steps", []),
                                   footnote=spec_slide.get("footnote", ""))
        elif kind == "content":
            au.build_content_slide(prs, base_deck, changes, th, title=title,
                                   bullets=spec_slide.get("bullets", []),
                                   claim=spec_slide.get("claim", ""),
                                   footnote=spec_slide.get("footnote", ""))
        else:
            raise UsageError(f"unsupported slide type '{kind}'; use title|content|kpi|process")
        built += 1
    # authored slides must also satisfy the reading-order contract
    au.repair_reading_order(prs, base_deck, cfg, changes, th)
    prs.save(out)
    new_deck = mods["dm"].load_deck(out, cfg)
    check_issues = []
    seen_keys = set()
    for name in ("package", "bounds", "min-type", "overflow", "a11y", "overlap"):
        for i in an.ENGINES[name](new_deck, cfg)["issues"]:
            key = (i["code"], i["slide"], i["object"], i["message"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            check_issues.append(i)
    payload = {"meta": deck_meta(new_deck, cfg, "author", out), "slides_built": built,
               "changes": changes, "verification": summarize_issues(check_issues),
               "validation_errors": [i for i in check_issues if i["severity"] == "error"][:20]}
    write_json(os.path.splitext(out)[0] + "-authoring.json", payload)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"authored {built} slide(s) -> {out}")
        print(f"validation: {payload['verification']}")
        for i in payload["validation_errors"]:
            print(fmt_issue_line(i))
    return 0 if not payload["validation_errors"] else 1


def _validate_deck_spec(spec: Dict[str, Any], schema: Dict[str, Any]) -> None:
    errors = []
    if not isinstance(spec, dict):
        raise UsageError("deck spec must be a JSON object")
    for key in schema.get("required", []):
        if key not in spec:
            errors.append(f"missing required key '{key}'")
    allowed_types = set((schema.get("properties", {}).get("slides", {}).get("items", {})
                         .get("properties", {}).get("type", {}).get("enum", ["title", "content", "kpi", "process"])))
    for n, s in enumerate(spec.get("slides", []) or [], start=1):
        if not isinstance(s, dict):
            errors.append(f"slide {n} must be an object")
            continue
        kind = s.get("type", "content")
        if kind not in allowed_types:
            errors.append(f"slide {n} type '{kind}' not in {sorted(allowed_types)}")
        if not str(s.get("title", "")).strip():
            errors.append(f"slide {n} needs a non-empty title")
        if kind == "content":
            bullets = s.get("bullets") or []
            if not isinstance(bullets, list) or not bullets:
                errors.append(f"slide {n} (content) needs a non-empty bullets array")
            if len(bullets) > (schema.get("properties", {}).get("max_bullets", {}).get("maximum", 8)):
                errors.append(f"slide {n} has {len(bullets)} bullets, above the maximum")
        if kind == "kpi":
            cards = s.get("cards") or []
            if not isinstance(cards, list) or not cards:
                errors.append(f"slide {n} (kpi) needs a non-empty cards array")
            for c in cards:
                if not str(c.get("label", "")).strip() or not str(c.get("value", "")).strip():
                    errors.append(f"slide {n} kpi card needs both 'label' and 'value'")
        if kind == "process":
            steps = s.get("steps") or []
            if not isinstance(steps, list) or len(steps) < 2:
                errors.append(f"slide {n} (process) needs at least two steps")
            if len(steps) > 6:
                errors.append(f"slide {n} has {len(steps)} steps; a single row fits 6")
    if not spec.get("slides"):
        errors.append("spec must contain at least one slide")
    if errors:
        raise UsageError("invalid deck spec:\n  - " + "\n  - ".join(errors))


def cmd_rescale(args, mods, ctx) -> int:
    au = mods["au"]
    an = mods["an"]
    cfg = ctx["cfg"]
    presets = {"16:9": (960.0, 540.0), "4:3": (720.0, 540.0), "16:10": (960.0, 600.0),
               "1:1": (720.0, 720.0), "portrait": (540.0, 960.0), "a4-landscape": (842.0, 595.0),
               "a4-portrait": (595.0, 842.0)}
    if args.preset:
        if args.preset not in presets:
            raise UsageError(f"unknown preset '{args.preset}'; available: {', '.join(presets)}")
        w_pt, h_pt = presets[args.preset]
    elif args.width_pt and args.height_pt:
        w_pt, h_pt = args.width_pt, args.height_pt
    else:
        raise UsageError("rescale needs --preset or both --width-pt and --height-pt")
    if not (200 <= w_pt <= 4000 and 200 <= h_pt <= 4000):
        raise UsageError("canvas dimensions must be between 200 and 4000 points")
    deck = load_deck_checked(mods, args.deck, cfg)
    out = args.out or os.path.join(".", os.path.splitext(os.path.basename(args.deck))[0] + f"-{args.preset or 'custom'}.pptx")
    if not args.apply:
        print(f"DRY RUN: would rescale {len(deck.slides)} slide(s) from "
              f"{deck.width_pt:.0f}x{deck.height_pt:.0f}pt to {w_pt:.0f}x{h_pt:.0f}pt")
        print("Objects are scaled proportionally; aspect changes cause distortion. "
              "Re-run with --apply to write.")
        return 0
    if not args.approve:
        raise UsageError("rescale reflows every slide and is treated as appearance-changing; pass --approve with --apply")
    prs = _open_for_edit(args.deck)
    changes: List[Dict[str, Any]] = []
    n = au.rescale_canvas(prs, deck, cfg, changes, an.thresholds(cfg), w_pt, h_pt)
    ensure_dir(os.path.dirname(os.path.abspath(out)) or ".")
    prs.save(out)
    new_deck = mods["dm"].load_deck(out, cfg)
    issues = []
    for name in ("package", "bounds", "min-type", "overflow"):
        issues.extend(an.ENGINES[name](new_deck, cfg)["issues"])
    write_json(os.path.splitext(out)[0] + "-rescale.json",
               {"from_pt": [round(deck.width_pt, 2), round(deck.height_pt, 2)],
                "to_pt": [w_pt, h_pt], "slides": n, "output": out,
                "verification": summarize_issues(issues)})
    print(f"rescaled {n} slide(s) -> {out} ({w_pt:.0f}x{h_pt:.0f}pt); verification: {summarize_issues(issues)}")
    return 0


def cmd_export_check(args, mods, ctx) -> int:
    dm = mods["dm"]
    an = mods["an"]
    cfg = ctx["cfg"]
    deck = load_deck_checked(mods, args.deck, cfg)
    artifacts = [a.strip() for a in (args.artifacts or "").split(",") if a.strip()]
    checks: List[Dict[str, Any]] = []
    health = deck.health or {}
    checks.append({"check": "package_integrity", "passed": bool(health.get("ok")),
                   "detail": health.get("notes", []) or ["package opens and parses"]})
    missing = [a for a in artifacts if not os.path.isfile(a)]
    checks.append({"check": "artifacts_present", "passed": not missing,
                   "detail": missing or [f"{len(artifacts)} artifact(s) present"]})
    for a in artifacts:
        if os.path.isfile(a) and a.lower().endswith((".pptx", ".pptm")):
            h = dm.package_health(a)
            checks.append({"check": f"reopen:{os.path.basename(a)}", "passed": bool(h.get("ok")),
                           "detail": h.get("notes") or ["opens cleanly"]})
    exts = sorted({os.path.splitext(a)[1].lower() for a in artifacts})
    font_issues = [i for i in an.engine_font_inventory(deck, cfg)["issues"] if i["code"] == "TYPE_FONT_NO_FALLBACK"]
    checks.append({"check": "font_fallback_coverage", "passed": not font_issues,
                   "detail": [i["message"] for i in font_issues] or ["all fonts have mapped fallbacks"]})
    av = [m for m in deck.media if m.get("ext") in (".mp4", ".mp3", ".wav", ".mov", ".m4v", ".avi", ".wmv")]
    checks.append({"check": "media_present", "passed": True,
                   "detail": [f"{len(av)} A/V part(s): verify playback on the target machine"] if av else ["no A/V parts"]})
    rel = (health.get("missing_rels_targets") or [])
    checks.append({"check": "external_references", "passed": not rel,
                   "detail": rel or ["no dangling package relationships"]})
    passed = all(c["passed"] for c in checks)
    payload = {"meta": deck_meta(deck, cfg, "export-check", args.deck), "passed": passed,
               "checks": checks, "artifact_extensions": exts}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "export-check.json"), payload)
        print(f"wrote {os.path.join(args.out, 'export-check.json')}")
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"export check: {'PASS' if passed else 'FAIL'}")
        for c in checks:
            print(f"  [{'ok' if c['passed'] else '!!'}] {c['check']}: {'; '.join(map(str, c['detail']))[:110]}")
        print("\nLimitation: this validates package structure and artifact presence. PDF/image parity and "
              "font substitution can only be confirmed by exporting with PowerPoint or LibreOffice.")
    return 0 if passed else 1


def cmd_compare(args, mods, ctx) -> int:
    dm = mods["dm"]
    an = mods["an"]
    rd = mods["rd"]
    cfg = ctx["cfg"]
    before = load_deck_checked(mods, args.before, cfg)
    after = load_deck_checked(mods, args.after, cfg)
    diff = dm.compare_models(before.to_dict(), after.to_dict())
    payload: Dict[str, Any] = {"before": deck_meta(before, cfg, "compare", args.before),
                               "after": deck_meta(after, cfg, "compare", args.after),
                               "structural_diff": diff}
    if args.visual and rd.PIL_AVAILABLE:
        tmp = tempfile.mkdtemp(prefix="ppsuite-diff-")
        try:
            pairs = []
            for s_old in before.slides:
                s_new = next((x for x in after.slides if x.index == s_old.index), None)
                if s_new is None:
                    continue
                a = os.path.join(tmp, f"a-{s_old.index}.png")
                b = os.path.join(tmp, f"b-{s_old.index}.png")
                rd.render_slide(before, s_old, width_px=args.width).save(a)
                rd.render_slide(after, s_new, width_px=args.width).save(b)
                overlay = os.path.join(tmp, f"diff-{s_old.index}.png") if args.out else None
                d = rd.diff_images(a, b, overlay)
                d["slide"] = s_old.index
                pairs.append(d)
            payload["visual_diff"] = {"slides": pairs,
                                      "changed_slides": [p["slide"] for p in pairs if not p["identical"]]}
            if args.out:
                ensure_dir(args.out)
                for p in pairs:
                    if p.get("overlay_path") and os.path.isfile(p["overlay_path"]):
                        shutil.copy2(p["overlay_path"], os.path.join(args.out, f"diff-{p['slide']:03d}.png"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    elif args.visual:
        payload["visual_diff"] = {"error": "Pillow unavailable"}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "compare.json"), payload)
        print(f"wrote {os.path.join(args.out, 'compare.json')}")
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"structural changes: {diff['change_count']} "
              f"({diff['slide_count_before']} -> {diff['slide_count_after']} slides)")
        for ch in diff["changes"][:20]:
            print("  " + json.dumps(ch, default=str)[:160])
        if "visual_diff" in payload and "changed_slides" in payload["visual_diff"]:
            print(f"slides with visual geometry changes: {payload['visual_diff']['changed_slides'][:30]}")
    return 0


def cmd_score(args, mods, ctx) -> int:
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    baseline = read_json(args.baseline) if args.baseline else None
    model_baseline = baseline.get("model") if baseline and "model" in baseline else baseline
    payload = _audit_payload(mods, deck, ctx["cfg"], model_baseline, "score", args.deck)
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "score.json"),
                   {"meta": payload["meta"], "scores": payload["scores"],
                    "issue_counts": payload["issue_counts"], "defect_queue": payload["defect_queue"]})
        print(f"wrote {os.path.join(args.out, 'score.json')}")
    if args.json:
        print(json.dumps({"scores": payload["scores"], "issue_counts": payload["issue_counts"]}, indent=2))
    else:
        print(f"Overall: {payload['scores']['overall']}/100")
        for key in ("readability", "consistency", "accessibility", "structure", "evidence", "security"):
            v = payload["scores"][key]
            print(f"  {key:14} {v['score']:5.1f}  ({v['issue_count']} findings, {v['errors']} errors)")
    if baseline and args.compare_to_baseline:
        before_scores = (baseline.get("scores") or {})
        if before_scores:
            delta = payload["scores"]["overall"] - before_scores.get("overall", 0)
            print(f"delta vs baseline: {delta:+.1f}")
    return 0


def cmd_gate(args, mods, ctx) -> int:
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    baseline = read_json(args.baseline) if args.baseline else None
    model_baseline = baseline.get("model") if baseline and "model" in baseline else baseline
    payload = _audit_payload(mods, deck, ctx["cfg"], model_baseline, "gate", args.deck)
    artifacts = [a.strip() for a in (args.artifacts or "").split(",") if a.strip()]
    gate = mods["an"].engine_completion_gate(deck, ctx["cfg"], payload["issues"], artifacts)["data"]
    result_payload = {"meta": payload["meta"], "gate": gate, "issue_counts": payload["issue_counts"],
                      "scores": payload["scores"]}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "gate.json"), result_payload)
        print(f"wrote {os.path.join(args.out, 'gate.json')}")
    if args.json:
        print(json.dumps(result_payload, indent=2, default=str))
    else:
        print(f"GATE: {'PASS' if gate['passed'] else 'BLOCKED'}")
        if not gate["passed"]:
            print(f"  blocking error classes: {', '.join(gate['blocking_codes']) or '-'}")
            print(f"  missing artifacts: {gate['missing_artifacts'] or '-'}")
        print(f"  errors={payload['issue_counts']['error']} warnings={payload['issue_counts']['warning']} "
              f"overall score={payload['scores']['overall']}")
    if gate["passed"]:
        return 0
    return 1 if not args.allow_fail else 0


def cmd_plan(args, mods, ctx) -> int:
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    payload = _audit_payload(mods, deck, ctx["cfg"], None, "plan", args.deck)
    result_payload = {"meta": payload["meta"], "plan": payload["plan"],
                      "defect_queue": payload["defect_queue"], "issue_counts": payload["issue_counts"]}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "plan.json"), result_payload)
    if args.json:
        print(json.dumps(result_payload, indent=2, default=str))
    else:
        print("Dependency-aware remediation plan:")
        for step in payload["plan"]["plan"]:
            print(f"  {step['order']}. {step['step']}: {step['reason']}")
            print(f"     -> python tools-presentation-suite.py {step['command']}")
    return 0


def cmd_validate_config(args, mods, ctx) -> int:
    cfg, notes = load_config(args.config)
    schema = read_json(_asset_path(ASSET_CONFIG_SCHEMA))
    errors = validate_config(cfg, schema)
    payload = {"config_path": args.config or _asset_path(ASSET_RULES), "valid": not errors,
               "errors": errors, "notes": notes, "profile": cfg.get("profile"),
               "profiles": sorted((cfg.get("profiles") or {}).keys())}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"config {'VALID' if not errors else 'INVALID'}: {payload['config_path']}")
        print(f"  active profile: {payload['profile']} (available: {', '.join(payload['profiles'])})")
        for e in errors:
            print(f"  ERROR: {e}")
        for n in notes:
            print(f"  NOTE: {n}")
    return 0 if not errors else 2


def cmd_catalog(args, mods, ctx) -> int:
    an = mods["an"]
    registry = read_json(_asset_path(ASSET_REGISTRY))
    engine_names = set(an.ENGINES) | {"regression"}
    referenced = set()
    for e in registry.get("entries", []):
        for eng in e.get("engines", []) or []:
            referenced.add(eng)
    unknown = sorted(referenced - engine_names)
    unfilled = [e for e in registry.get("entries", []) if not e.get("status")]
    payload = {
        "registry_version": registry.get("version"),
        "catalog_categories": registry.get("catalog_categories"),
        "total_entries": len(registry.get("entries", [])),
        "status_counts": {st: sum(1 for e in registry.get("entries", []) if e.get("status") == st)
                          for st in sorted({e.get("status") for e in registry.get("entries", [])})},
        "engines_referenced": sorted(referenced),
        "unknown_engine_references": unknown,
        "entries_without_status": len(unfilled),
        "categories": registry.get("categories", []),
    }
    if args.missing:
        payload["delegated"] = [{"name": e["name"], "category": e.get("category"),
                                 "requires": e.get("requires")}
                                for e in registry.get("entries", []) if e.get("status") == "delegated"]
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Capability registry v{payload['registry_version']}: {payload['total_entries']} entries "
              f"across {payload['catalog_categories']} catalog categories")
        for st, n in payload["status_counts"].items():
            print(f"  {st:12} {n}")
        print(f"  engines referenced: {len(payload['engines_referenced'])}")
        if unknown:
            print(f"  UNKNOWN engine references: {unknown}")
        if args.missing:
            for d in payload["delegated"]:
                print(f"  delegated: {d['name']} ({d['category']}) -> {d['requires']}")
    return 0 if not unknown else 1


def cmd_selfcheck(args, mods, ctx) -> int:
    """Fast environment + asset readiness check (no deck required)."""
    checks = []
    checks.append({"check": "python", "passed": sys.version_info >= (3, 9),
                   "detail": sys.version.split()[0]})
    try:
        import pptx  # noqa: F401
        checks.append({"check": "python-pptx", "passed": True, "detail": pptx.__version__})
    except ImportError:
        checks.append({"check": "python-pptx", "passed": False,
                       "detail": "not installed - required for .pptx support (pip install python-pptx)"})
    try:
        import PIL
        checks.append({"check": "Pillow", "passed": True, "detail": PIL.__version__})
    except ImportError:
        checks.append({"check": "Pillow", "passed": False,
                       "detail": "not installed - rendering and visual diff unavailable (pip install Pillow)"})
    try:
        import jsonschema  # noqa: F401
        checks.append({"check": "jsonschema", "passed": True, "detail": "available (optional)"})
    except ImportError:
        checks.append({"check": "jsonschema", "passed": True,
                       "detail": "not installed (optional; built-in validator used)"})
    for asset in REQUIRED_ASSETS:
        path = _asset_path(asset)
        ok = os.path.isfile(path) and os.path.getsize(path) > 0
        checks.append({"check": f"asset:{asset}", "passed": ok,
                       "detail": f"{os.path.getsize(path)} bytes" if ok else "missing"})
    schema = read_json(_asset_path(ASSET_CONFIG_SCHEMA))
    cfg, _notes = load_config(None)
    cfg_errors = validate_config(cfg, schema)
    checks.append({"check": "config:defaults", "passed": not cfg_errors, "detail": cfg_errors or ["valid"]})
    registry = read_json(_asset_path(ASSET_REGISTRY))
    checks.append({"check": "registry:entries", "passed": len(registry.get("entries", [])) > 0,
                   "detail": f"{len(registry.get('entries', []))} entries"})
    ignored = {"__pycache__", ".DS_Store"}
    structural = sorted(f for f in os.listdir(SUITE_DIR) if f not in ignored)
    bad = [f for f in structural if os.path.isdir(os.path.join(SUITE_DIR, f)) or f == "SKILL.md"]
    checks.append({"check": "structure:flat", "passed": not bad,
                   "detail": bad or ["no subfolders, no SKILL.md"]})
    required_ok = all(c["passed"] for c in checks)
    payload = {"suite": SUITE_NAME, "version": TOOL_VERSION, "cwd": os.getcwd(),
               "checks": checks, "ready": required_ok}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"{SUITE_NAME} {TOOL_VERSION} readiness: {'READY' if required_ok else 'NOT READY'}")
        for c in checks:
            print(f"  [{'ok' if c['passed'] else '!!'}] {c['check']}: {c['detail']}")
    return 0 if required_ok else 3




def cmd_extract_text(args, mods, ctx) -> int:
    """Extract text with slide/object context for translation or review."""
    deck = load_deck_checked(mods, args.deck, ctx["cfg"])
    rows = []
    for s in deck.slides:
        if args.slides:
            wanted = {int(x) for x in args.slides.split(",")}
            if s.index not in wanted:
                continue
        for o in sorted(s.objects, key=lambda o: (round(o.y_pt), o.x_pt)):
            text = (o.text or "").strip()
            if not text and not args.include_empty:
                continue
            rows.append({"slide": s.index, "object": o.name, "id": o.id, "kind": o.kind,
                         "role": "title" if o.y == min((x.y for x in s.objects), default=o.y) else "body",
                         "x_pt": round(o.x_pt, 1), "y_pt": round(o.y_pt, 1),
                         "text": text, "notes": s.notes if args.with_notes and o.id == (s.objects[0].id if s.objects else -1) else ""})
    payload = {"meta": deck_meta(deck, ctx["cfg"], "extract-text", args.deck), "count": len(rows), "items": rows}
    if args.out:
        ensure_dir(args.out)
        write_json(os.path.join(args.out, "text-extract.json"), payload)
        md = ["# Text extract: " + payload["meta"]["deck_name"], "",
              f"{len(rows)} text object(s), {sum(len(r['text']) for r in rows)} characters", ""]
        for r in rows:
            md.append(f"## Slide {r['slide']} · {r['object']} · slide:{r['slide']} object:{r['id']}")
            md.append("")
            md.append(r["text"] or "_(empty)_")
            if r["notes"]:
                md.append("")
                md.append(f"**Notes:** {r['notes']}")
            md.append("")
        with open(os.path.join(args.out, "text-extract.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(md))
        print(f"wrote {os.path.join(args.out, 'text-extract.json')} and text-extract.md")
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not ctx["quiet"]:
        for r in rows:
            print(f"[slide {r['slide']:>3} | {r['object'][:24]:24}] {r['text'][:110]}")
    return 0


def cmd_batch(args, mods, ctx) -> int:
    """Run an operation across many decks with per-deck isolation."""
    paths: List[str] = []
    for pattern in args.deck:
        if any(ch in pattern for ch in "*?["):
            import glob
            paths.extend(sorted(glob.glob(pattern)))
        else:
            paths.append(pattern)
    if not paths:
        raise UsageError("no decks matched --deck patterns")
    out_dir = ensure_dir(args.out or "batch-out")
    max_files = int((ctx["cfg"].get("limits") or {}).get("max_slides", 2000))
    results = []
    for path in paths:
        entry: Dict[str, Any] = {"deck": path}
        try:
            deck = load_deck_checked(mods, path, ctx["cfg"])
            if args.engine:
                names = [n.strip() for n in args.engine.split(",") if n.strip()]
                res, issues = run_engines(mods, deck, ctx["cfg"], names)
                entry.update({"ok": True, "engines": [r["engine"] for r in res],
                              "issue_counts": summarize_issues(issues),
                              "top_codes": [c for c, _ in sorted(
                                  ((c, sum(1 for i in issues if i["code"] == c)) for c in {i["code"] for i in issues}),
                                  key=lambda kv: -kv[1])[:6]]})
            elif args.audit:
                payload = _audit_payload(mods, deck, ctx["cfg"], None, "batch-audit", path)
                stem = os.path.splitext(os.path.basename(path))[0]
                write_json(os.path.join(out_dir, f"{stem}.audit.json"),
                           {k: v for k, v in payload.items() if k != "engines"})
                entry.update({"ok": True, "issue_counts": payload["issue_counts"],
                              "overall_score": payload["scores"]["overall"],
                              "gate_passed": payload["gate"]["passed"]})
            elif args.repair_scopes and args.apply:
                sub = argparse.Namespace(
                    deck=path, out=out_dir, scope=args.repair_scopes, apply=True,
                    approve=args.approve, command="repair", json=False, cfg=None)
                rc = cmd_repair(sub, mods, ctx)
                entry.update({"ok": rc in (0, 1), "exit_code": rc})
            else:
                entry.update({"ok": True, "slides": len(deck.slides), "aspect": deck.aspect,
                              "package_ok": bool(deck.health.get("ok"))})
        except (UsageError, RunError) as exc:
            entry.update({"ok": False, "error": str(exc)})
        except Exception as exc:
            entry.update({"ok": False, "error": f"unexpected: {exc}"})
        results.append(entry)
    payload = {"meta": {"generated": now_iso(), "command": "batch", "deck_count": len(paths)},
               "results": results, "ok": sum(1 for r in results if r.get("ok")),
               "failed": [r for r in results if not r.get("ok")]}
    write_json(os.path.join(out_dir, "batch.json"), payload)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"batch: {payload['ok']}/{len(paths)} deck(s) processed -> {os.path.join(out_dir, 'batch.json')}")
        for r in results:
            state = "ok" if r.get("ok") else "FAIL"
            extra = json.dumps({k: v for k, v in r.items() if k not in ("deck", "ok")}, default=str)[:110]
            print(f"  [{state:4}] {os.path.basename(r['deck'])}: {extra}")
    return 0 if not payload["failed"] else 1


def cmd_convert_units(args, mods, ctx) -> int:
    dm = mods["dm"]
    values = [float(v) for v in args.values.split(",") if v.strip()]
    if not values:
        raise UsageError("--values needs at least one number")
    rows = [{args.to: round(dm.convert(v, args.from_unit, args.to), 4),
             "emu": int(round(dm.convert(v, args.from_unit, "emu"))),
             "pt": round(dm.convert(v, args.from_unit, "pt"), 3),
             "in": round(dm.convert(v, args.from_unit, "in"), 5)} for v in values]
    payload = {"from": args.from_unit, "to": args.to, "values": values, "results": rows,
               "note": "px conversions assume 96 dpi"}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for v, r in zip(values, rows):
            print(f"{v:g} {args.from_unit} = {r[args.to]:g} {args.to}  ({r['emu']} EMU, {r['pt']:g} pt, {r['in']:g} in)")
    return 0


# ------------------------------------------------------------------ CLI wiring
COMMANDS: Dict[str, Dict[str, Any]] = {
    "help": {"summary": "show help for the tool or a command", "func": cmd_help,
             "usage": "tools-presentation-suite.py help [command]",
             "details": "With no topic, prints the overview and command list."},
    "capabilities": {"summary": "list commands, engines, catalog coverage and dependencies", "func": cmd_capabilities,
                     "usage": "tools-presentation-suite.py capabilities [--json]",
                     "details": "Machine-readable inventory of everything this Skill Tool implements."},
    "selfcheck": {"summary": "verify runtime, assets and folder structure", "func": cmd_selfcheck,
                  "usage": "tools-presentation-suite.py selfcheck [--json]",
                  "details": "Run this first after copying the folder. Exits 3 when something required is missing."},
    "validate-config": {"summary": "validate a rules/config JSON against the shipped schema", "func": cmd_validate_config,
                        "usage": "tools-presentation-suite.py validate-config [--config cfg.json]",
                        "details": "Merges your file over asset-default-rules.json and reports errors and unknown keys."},
    "inspect": {"summary": "structural inspection + neutral model dump", "func": cmd_inspect,
                "usage": "tools-presentation-suite.py inspect --deck deck.pptx --out out/ [--json]",
                "details": "Writes inspect.json containing the full neutral model and 14 structural engines. "
                           "Use the result as the --baseline for later audits."},
    "audit": {"summary": "run the full audit bundle, write audit.json/report.md/report.html", "func": cmd_audit,
              "usage": "tools-presentation-suite.py audit --deck deck.pptx --out out/ [--profile P] "
                       "[--baseline inspect.json] [--fail-on error|warning|none] [--json]",
              "details": "Runs 60+ engines, computes scorecards, a ranked defect queue and a remediation plan."},
    "analyze": {"summary": "run selected engines and print their structured results", "func": cmd_analyze,
                "usage": "tools-presentation-suite.py analyze --deck deck.pptx --engine contrast,overflow "
                         "[--out out/] [--json] [--limit N]",
                "details": "Use 'capabilities' for engine names. Comma-separated, order preserved."},
    "report": {"summary": "regenerate report.md/report.html from an audit.json", "func": cmd_report,
               "usage": "tools-presentation-suite.py report --audit out/audit.json [--out dir] [--contact-sheet sheet.png]",
               "details": "Reports are rebuilt from data, never hand-edited."},
    "render": {"summary": "render slides to PNG/JPEG from the geometry model", "func": cmd_render,
               "usage": "tools-presentation-suite.py render --deck deck.pptx --out render/ [--width 1280] "
                        "[--labels] [--safe-area] [--contact-sheet] [--format png|jpg] [--slides 1,3]",
               "details": "Deterministic, dependency-light rendering for review and regression; not pixel-parity."},
    "contact-sheet": {"summary": "one thumbnail grid image for the whole deck", "func": cmd_contact_sheet,
                      "usage": "tools-presentation-suite.py contact-sheet --deck deck.pptx --out sheet.png [--width 320] [--cols 4]",
                      "details": "Fastest way to review deck rhythm."},
    "snapshot": {"summary": "record a last-known-good model + optional deck copy", "func": cmd_snapshot,
                 "usage": "tools-presentation-suite.py snapshot --deck deck.pptx --out snapshot/ [--label before-edit] [--copy]",
                 "details": "Always snapshot before repair/normalize/author runs."},
    "repair": {"summary": "repair structural/a11y/typography defects (dry-run by default)", "func": cmd_repair,
               "approval": True,
               "usage": "tools-presentation-suite.py repair --deck deck.pptx --out out/ "
                        "--scope bounds,type-min,overflow,alt-text,reading-order [--apply] [--approve]",
               "details": "Scopes: bounds, safe-margin, connectors, type-min, overflow, contrast, alt-text, "
                          "reading-order, effects, scrub-metadata, strip-notes, redact. Without --apply it reports "
                          "planned changes only. Scopes that change appearance require --approve."},
    "normalize": {"summary": "align/spacing/colour normalization (dry-run by default)", "func": cmd_normalize,
                  "approval": True,
                  "usage": "tools-presentation-suite.py normalize --deck deck.pptx --out out/ "
                           "--scope align,spacing[,colors] [--apply] [--approve]",
                  "details": "Idempotent: a second run on the output reports zero changes."},
    "governance": {"summary": "metadata scrubbing, note stripping, redaction, slide visibility", "func": cmd_governance,
                   "approval": True,
                   "usage": "tools-presentation-suite.py governance --deck deck.pptx --out out/ "
                            "--action scrub-metadata,strip-notes,redact [--apply] [--approve]\n"
                            "       tools-presentation-suite.py governance --deck d.pptx --out out/ --hide 4,5 [--show 1] [--apply]",
                   "details": "Run before external release. Redaction replaces matches with [REDACTED]."},
    "author": {"summary": "build slides from a declared spec", "func": cmd_author,
               "usage": "tools-presentation-suite.py author --spec deck-spec.json --out new.pptx [--base template.pptx] [--force]\n"
                        "       tools-presentation-suite.py author --type content --title \"T\" --bullets \"a|b|c\" --out new.pptx",
               "details": "Slide types: title, content, kpi, process. Validated against asset-deck-spec.schema.json "
                          "before anything is written."},
    "rescale": {"summary": "reflow the deck to another canvas size", "func": cmd_rescale,
                "approval": True,
                "usage": "tools-presentation-suite.py rescale --deck deck.pptx --out out.pptx "
                         "--preset 16:9|4:3|1:1|portrait|a4-landscape|a4-portrait [--apply --approve]",
                "details": "Scales object geometry proportionally; check overflow findings afterwards."},
    "export-check": {"summary": "validate deliverable artifacts and package integrity", "func": cmd_export_check,
                     "usage": "tools-presentation-suite.py export-check --deck deck.pptx [--artifacts a.pdf,b.png] [--out out/]",
                     "details": "Checks package health, artifact presence, reopening, font fallback coverage and media."},
    "compare": {"summary": "structural + optional visual diff between two decks", "func": cmd_compare,
                "usage": "tools-presentation-suite.py compare --before a.pptx --after b.pptx [--visual] [--width 960] [--out out/]",
                "details": "Uses object matching by name+text so small edits are reported precisely."},
    "score": {"summary": "print scorecards, optionally against a baseline", "func": cmd_score,
              "usage": "tools-presentation-suite.py score --deck deck.pptx [--baseline inspect.json] [--compare-to-baseline] [--out out/]",
              "details": "Scores: readability, consistency, accessibility, structure, evidence, security."},
    "gate": {"summary": "release gate over error findings and required artifacts", "func": cmd_gate,
             "usage": "tools-presentation-suite.py gate --deck deck.pptx [--artifacts deck.pptx,deck.pdf] "
                      "[--baseline inspect.json] [--out out/]",
             "details": "Exit 0 only when no error-severity finding remains. Use --allow-fail to force exit 0."},
    "plan": {"summary": "dependency-aware remediation plan for this deck", "func": cmd_plan,
             "usage": "tools-presentation-suite.py plan --deck deck.pptx [--out out/] [--json]",
             "details": "Orders inspection, repair, normalization, validation and gating."},
    "extract-text": {"summary": "extract slide text with object context for translation/review",
                     "func": cmd_extract_text,
                     "usage": "tools-presentation-suite.py extract-text --deck deck.pptx --out out/ [--slides 1,2] [--with-notes] [--json]",
                     "details": "Writes text-extract.json (round-trippable structure) and text-extract.md."},
    "batch": {"summary": "process many decks with per-deck isolation", "func": cmd_batch,
              "usage": "tools-presentation-suite.py batch --deck \"decks/*.pptx\" --out batch-out/ "
                       "[--audit | --engine contrast,overflow | --repair-scopes bounds --apply] [--json]",
              "details": "A failing deck never aborts the batch; every result is recorded in batch.json."},
    "convert-units": {"summary": "convert EMU/pt/px/in/cm/mm values", "func": cmd_convert_units,
                      "usage": "tools-presentation-suite.py convert-units --values 72,540 --from pt --to in [--json]",
                      "details": "px assumes 96 dpi. Useful when translating layout specs between tools."},
    "catalog": {"summary": "audit the shipped capability registry", "func": cmd_catalog,
                "usage": "tools-presentation-suite.py catalog [--missing] [--json]",
                "details": "Maps every catalogued capability to its implementation status and engine."},
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools-presentation-suite.py",
        description="Presentation Production Suite - inspect, audit, repair, author and govern PowerPoint decks.",
        epilog="Run 'tools-presentation-suite.py capabilities' for the full command and engine list.",
    )
    p.add_argument("--config", help="user rules JSON merged over asset-default-rules.json")
    p.add_argument("--quiet", action="store_true", help="suppress human-readable progress output")
    p.add_argument("--version", action="version", version=f"{SUITE_NAME} {TOOL_VERSION}")
    sub = p.add_subparsers(dest="command", required=False)

    def add(name, *, deck=False, out=False, jsonflag=True):
        sp = sub.add_parser(name, help=COMMANDS[name]["summary"])
        if deck:
            sp.add_argument("--deck", required=True, help="path to the .pptx/.pptm/.json deck")
        if out:
            sp.add_argument("--out", help="output directory (or .pptx file for edit commands)")
        if jsonflag:
            sp.add_argument("--json", action="store_true", help="print machine-readable JSON to stdout")
        return sp

    sp = add("help", jsonflag=False)
    sp.add_argument("topic", nargs="?", help="command name")
    add("capabilities")
    add("selfcheck")
    sp = add("validate-config", jsonflag=True)
    sp.add_argument("--config", dest="config", help="config file to validate")

    add("inspect", deck=True, out=True)
    sp = add("audit", deck=True, out=True)
    sp.add_argument("--profile", help="viewing profile from the config")
    sp.add_argument("--baseline", help="inspect.json or neutral model for regression comparison")
    sp.add_argument("--fail-on", default="error", choices=["error", "warning", "none"],
                    help="exit 1 when findings reach this severity (default: error)")

    sp = add("analyze", deck=True, out=True)
    sp.add_argument("--engine", required=True, help="comma-separated engine names")
    sp.add_argument("--limit", type=int, default=10, help="max findings printed per engine")

    sp = add("report")
    sp.add_argument("--audit", required=True, help="audit.json path")
    sp.add_argument("--out", help="output directory")
    sp.add_argument("--contact-sheet", dest="contact_sheet", help="PNG contact sheet to embed in the HTML report")
    sp.add_argument("--markdown-only", action="store_true")

    sp = add("render", deck=True, out=True)
    sp.add_argument("--width", type=int, default=1280, help="render width in pixels")
    sp.add_argument("--no-text", action="store_true", help="draw boxes only")
    sp.add_argument("--labels", action="store_true", help="overlay object names")
    sp.add_argument("--safe-area", action="store_true", help="draw the safe-area guide")
    sp.add_argument("--contact-sheet", action="store_true")
    sp.add_argument("--thumb-width", type=int, default=320)
    sp.add_argument("--format", default="png", choices=["png", "jpg"])
    sp.add_argument("--slides", help="comma-separated slide indexes")

    sp = add("contact-sheet", deck=True, out=True, jsonflag=False)
    sp.add_argument("--width", type=int, default=320)
    sp.add_argument("--cols", type=int, default=4)

    sp = add("snapshot", deck=True, out=True, jsonflag=True)
    sp.add_argument("--label")
    sp.add_argument("--copy", action="store_true", help="also copy the deck as last-known-good")

    sp = add("repair", deck=True, out=True)
    sp.add_argument("--scope", required=True, help="comma-separated repair scopes")
    sp.add_argument("--apply", action="store_true", help="write the repaired deck")
    sp.add_argument("--approve", action="store_true", help="approve appearance-changing scopes")

    sp = add("normalize", deck=True, out=True)
    sp.add_argument("--scope", required=True, help="comma-separated normalizers: align,spacing,colors")
    sp.add_argument("--apply", action="store_true", help="write the normalized deck")
    sp.add_argument("--approve", action="store_true", help="approve appearance-changing normalizers")

    sp = add("governance", deck=True, out=True)
    sp.add_argument("--action", default="", help="comma-separated: scrub-metadata,strip-notes,redact")
    sp.add_argument("--hide", help="comma-separated slide indexes to hide")
    sp.add_argument("--show", help="comma-separated slide indexes to show")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--approve", action="store_true")

    sp = add("author", out=True)
    sp.add_argument("--spec", help="deck spec JSON")
    sp.add_argument("--base", help="existing .pptx to extend")
    sp.add_argument("--type", choices=["title", "content", "kpi", "process"])
    sp.add_argument("--title")
    sp.add_argument("--subtitle")
    sp.add_argument("--claim")
    sp.add_argument("--bullets", help="pipe-separated bullets")
    sp.add_argument("--steps", help="pipe-separated process steps")
    sp.add_argument("--cards", help="JSON array of KPI cards")
    sp.add_argument("--footnote")
    sp.add_argument("--presenter")
    sp.add_argument("--force", action="store_true", help="overwrite an existing output file")

    sp = add("rescale", deck=True, out=True, jsonflag=False)
    sp.add_argument("--preset")
    sp.add_argument("--width-pt", dest="width_pt", type=float)
    sp.add_argument("--height-pt", dest="height_pt", type=float)
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--approve", action="store_true")

    sp = add("export-check", deck=True, out=True)
    sp.add_argument("--artifacts", help="comma-separated deliverable paths to verify")

    sp = add("compare", jsonflag=True)
    sp.add_argument("--before", required=True)
    sp.add_argument("--after", required=True)
    sp.add_argument("--visual", action="store_true", help="also render and diff each slide")
    sp.add_argument("--width", type=int, default=960)
    sp.add_argument("--out")

    sp = add("score", deck=True, out=True)
    sp.add_argument("--baseline")
    sp.add_argument("--compare-to-baseline", action="store_true")

    sp = add("gate", deck=True, out=True)
    sp.add_argument("--artifacts")
    sp.add_argument("--baseline")
    sp.add_argument("--allow-fail", action="store_true")

    add("plan", deck=True, out=True)

    sp = add("extract-text", deck=True, out=True)
    sp.add_argument("--slides", help="comma-separated slide indexes")
    sp.add_argument("--with-notes", action="store_true")
    sp.add_argument("--include-empty", action="store_true")

    sp = add("batch")
    sp.add_argument("--deck", required=True, nargs="+", help="deck paths or glob patterns")
    sp.add_argument("--out", help="output directory")
    sp.add_argument("--engine", help="comma-separated engines to run per deck")
    sp.add_argument("--audit", action="store_true", help="run the full audit bundle per deck")
    sp.add_argument("--repair-scopes", dest="repair_scopes", help="comma-separated repair scopes to apply per deck")
    sp.add_argument("--apply", action="store_true", help="apply repairs per deck (writes new files)")
    sp.add_argument("--approve", action="store_true")

    sp = add("convert-units", jsonflag=True)
    sp.add_argument("--values", required=True, help="comma-separated numbers")
    sp.add_argument("--from", dest="from_unit", default="pt", choices=["emu", "pt", "px", "in", "cm", "mm"])
    sp.add_argument("--to", default="in", choices=["emu", "pt", "px", "in", "cm", "mm"])

    sp = add("catalog")
    sp.add_argument("--missing", action="store_true", help="list delegated capabilities and their requirements")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        argv = ["help"]
    args = parser.parse_args(argv)
    # --config may appear before or after the subcommand
    if getattr(args, "config", None) is None:
        for i, token in enumerate(argv):
            if token == "--config" and i + 1 < len(argv):
                args.config = argv[i + 1]
    try:
        cfg, notes = load_config(getattr(args, "config", None))
        mods = load_suite_modules()
        ctx = {"cfg": cfg, "quiet": getattr(args, "quiet", False), "notes": notes}
        # domain errors from the supporting modules are expected conditions, not bugs
        known_errors = tuple(
            getattr(mod, name) for mod in mods.values()
            for name in ("ModelError", "AuthoringError", "RenderError")
            if hasattr(mod, name)
        )
        if notes and not ctx["quiet"]:
            for n in notes:
                print(f"NOTE: {n}", file=sys.stderr)
        spec = COMMANDS.get(args.command)
        if spec is None:
            raise UsageError(f"unknown command '{args.command}'")
        return spec["func"](args, mods, ctx)
    except UsageError as exc:
        print(f"ERROR (usage): {exc}", file=sys.stderr)
        return 2
    except known_errors as exc:  # type: ignore[misc]
        print(f"ERROR (runtime): {exc}", file=sys.stderr)
        return 3
    except RunError as exc:
        print(f"ERROR (runtime): {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 3
    except Exception as exc:  # unexpected
        print(f"ERROR (unexpected): {exc}", file=sys.stderr)
        if os.environ.get("PPTSUITE_DEBUG"):
            traceback.print_exc()
        else:
            print("set PPTSUITE_DEBUG=1 for a traceback", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
