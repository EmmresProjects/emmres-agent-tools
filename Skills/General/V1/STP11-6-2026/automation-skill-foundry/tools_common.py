#!/usr/bin/env python3
"""tools_common.py - shared runtime for the Automation Skill Foundry Skill Tool.

Role: load and normalise the catalog asset, resolve resource paths, emit
structured JSON/text output, write atomic files, and enforce the configurable
policy limits that every other tool in this folder reuses.

Standard library only. Python 3.9+ (tested on 3.13).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import tempfile

TOOL_NAME = "automation-skill-foundry"
TOOL_VERSION = "1.0.0"
CATALOG_FILE = "asset-skill-catalog.json"

RULES_FILE = "asset-automation-rules.json"
GOVERNANCE_FILE = "asset-governance-checks.json"
RECIPE_FILE = "asset-recipe-patterns.json"
SKELETON_FILE = "asset-runner-skeleton.py"
SCHEDULE_FILE = "asset-scheduler-profiles.json"
SCAFFOLD_FILE = "asset-scaffold-blueprint.json"
TESTPLAN_FILE = "asset-test-plan-template.json"
GOAL_FILE = "asset-goal-fixtures.json"
CONFIG_FILE = "asset-default-config.json"

MANAGED_RESOURCES = (
    CATALOG_FILE, RULES_FILE, GOVERNANCE_FILE, RECIPE_FILE,
    SKELETON_FILE, SCHEDULE_FILE, SCAFFOLD_FILE, TESTPLAN_FILE, GOAL_FILE,
    CONFIG_FILE,
)


class FoundryError(Exception):
    """Raised for expected, user-facing failures (bad input, bad config)."""

    def __init__(self, message, code="error", detail=None, exit_code=2):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}
        self.exit_code = exit_code


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def tool_root():
    """Folder that holds this tool, resolved without relying on the CWD."""
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name, root=None):
    """Resolve a prefixed resource name relative to the Skill Tool root."""
    if not name:
        raise FoundryError("resource name is empty", code="bad_resource")
    if os.path.isabs(name) or name.startswith("~"):
        return os.path.abspath(os.path.expanduser(name))
    base = root or tool_root()
    return os.path.normpath(os.path.join(base, name))


def asset(name, root=None):
    """Resolve one of the packaged asset-* files and verify it exists."""
    path = resource_path(name, root)
    if not os.path.isfile(path):
        raise FoundryError(
            "required asset not found: %s (looked in %s)" % (name, path),
            code="missing_asset",
        )
    return path


def load_json(path, what="json file"):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise FoundryError("%s not found: %s" % (what, path), code="missing_file")
    except json.JSONDecodeError as exc:
        raise FoundryError(
            "%s is not valid JSON: %s" % (what, path), code="bad_json",
            detail={"line": exc.lineno, "column": exc.colno, "reason": exc.msg},
        )


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def stable_json(data):
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _inline(value, limit=72):
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def to_text(data, indent=0):
    """Render a dict/list as readable plain text (used by --format text)."""
    pad = "  " * indent
    lines = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                lines.append("%s%s:" % (pad, key))
                lines.append(to_text(value, indent + 1))
            else:
                lines.append("%s%s: %s" % (pad, key, _inline(value)))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)) and item:
                first = True
                for line in to_text(item, indent + 1).splitlines():
                    if first:
                        lines.append("%s- %s" % (pad, line.strip()))
                        first = False
                    else:
                        lines.append(line)
            else:
                lines.append("%s- %s" % (pad, _inline(item)))
    else:
        lines.append("%s%s" % (pad, data))
    return "\n".join(l for l in lines if l.strip())


def emit(data, fmt="json", out_path=None):
    """Print structured output and optionally write it to out_path."""
    text = stable_json(data) if fmt == "json" else to_text(data) + "\n"
    if out_path:
        write_text_atomic(out_path, text)
    sys.stdout.write(text)
    return text


def write_text_atomic(path, text):
    """Write text via a temp file + rename so partial files never appear."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        raise FoundryError("output directory does not exist: %s" % parent,
                           code="bad_output_dir")
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".foundry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def write_json_atomic(path, data):
    return write_text_atomic(path, stable_json(data))


def sha256_of(data):
    if not isinstance(data, str):
        data = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------

class Catalog:
    """Read-only index over asset-skill-catalog.json."""

    def __init__(self, path=None):
        self.path = path or asset(CATALOG_FILE)
        self._raw = load_json(self.path, "skill catalog")
        self.skills = self._raw.get("skills") or []
        self.categories = self._raw.get("categories") or []
        if not self.skills:
            raise FoundryError("catalog contains no skills: %s" % self.path,
                               code="empty_catalog")
        self.by_number = {int(s["number"]): s for s in self.skills}
        self.by_id = {s["id"]: s for s in self.skills}
        self.by_cat = {}
        for s in self.skills:
            self.by_cat.setdefault(int(s["category_number"]), []).append(s)
        for items in self.by_cat.values():
            items.sort(key=lambda s: int(s["number"]))

    # -- lookups ---------------------------------------------------------
    def category(self, number):
        for c in self.categories:
            if int(c["number"]) == int(number):
                return c
        raise FoundryError("unknown category number: %s" % number,
                           code="unknown_category",
                           detail={"category_total": len(self.categories)})

    def resolve(self, ref):
        """Resolve '#132', '132', 'invoice-processing-bot' or a skill name."""
        text = str(ref).strip()
        if not text:
            raise FoundryError("empty skill reference", code="bad_skill_ref")
        if text.startswith("#"):
            text = text[1:]
        if text.isdigit():
            num = int(text)
            if num in self.by_number:
                return self.by_number[num]
            raise FoundryError(
                "skill number %d does not exist (catalog numbers are %d-%d)"
                % (num, min(self.by_number), max(self.by_number)),
                code="unknown_skill",
            )
        key = _slug(text)
        if key in self.by_id:
            return self.by_id[key]
        for skill in self.skills:
            if _slug(skill["name"]) == key:
                return skill
        raise FoundryError(
            "unknown skill: %r - use `list` to see ids, or pass a number" % text,
            code="unknown_skill",
        )

    def search(self, query, category=None, limit=10):
        terms = [t for t in _slug(query or "").split("-") if t]
        if not terms:
            raise FoundryError("search query is empty", code="empty_query")
        scored = []
        for skill in self.skills:
            if category is not None and int(skill["category_number"]) != int(category):
                continue
            hay_name = _slug(skill["name"])
            hay_desc = _slug(skill["description"])
            hay_tags = " ".join(skill.get("tags") or [])
            score = 0
            for term in terms:
                if term in hay_name.split("-"):
                    score += 6
                elif term in hay_name:
                    score += 4
                if term in hay_tags.split():
                    score += 2
                if term in hay_desc:
                    score += 1
            if score > 0:
                scored.append((score, -int(skill["number"]), skill))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        limit = max(1, min(int(limit), 50))
        return [
            {
                "number": s["number"],
                "id": s["id"],
                "name": s["name"],
                "category": s["category"],
                "description": s["description"],
                "score": score,
            }
            for score, _, s in scored[:limit]
        ]

    def resolve_many(self, refs, max_items=12):
        if isinstance(refs, str):
            refs = [r for r in re.split(r"[,\n;]+", refs) if r.strip()]
        refs = list(refs or [])
        if not refs:
            raise FoundryError("no skills referenced", code="empty_selection")
        if len(refs) > max_items:
            raise FoundryError(
                "too many skills requested (%d > limit %d); split the bundle"
                % (len(refs), max_items),
                code="too_many_skills",
                detail={"limit": max_items},
            )
        return [self.resolve(r) for r in refs]

    # -- integrity -------------------------------------------------------
    def summary(self):
        return {
            "catalog_id": self._raw.get("catalog_id"),
            "title": self._raw.get("title"),
            "skill_total": len(self.skills),
            "category_total": len(self.categories),
            "number_range": [min(self.by_number), max(self.by_number)],
            "checksum_sha256": sha256_of(self._raw),
        }


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


# --------------------------------------------------------------------------
# configuration and policy
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "format": "json",
    "out_dir": None,
    "catalog_file": CATALOG_FILE,
    "max_items": 12,
    "max_output_bytes": 4000000,
    "ask_skill_limit": 7,
    "secret_scan": True,
    "require_approval": True,
    "scheduler_name": "foundry-automation",
    "scheduler_owner": "automation-owner",
    "workspace_dir": "automation-workspace",
    "python_executable": "python3",
    "allowed_risk_levels": ["low", "medium", "high"],
    "deny_risk_levels": [],
}


def load_config(path=None, use_asset_defaults=True):
    """Merge built-in defaults, the packaged asset-default-config.json, and an
    optional --config file (which wins)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if use_asset_defaults:
        packaged = os.path.join(tool_root(), CONFIG_FILE)
        if os.path.isfile(packaged):
            try:
                data = load_json(packaged, "default config asset")
            except FoundryError:
                data = {}
            for key, value in (data.get("DEFAULTS") or {}).items():
                if value is not None:
                    cfg[key] = value
    if path:
        data = load_json(path, "config file")
        if not isinstance(data, dict):
            raise FoundryError("config file must contain a JSON object",
                               code="bad_config")
        # an edited copy of asset-default-config.json carries its settings under
        # DEFAULTS; those values override the packaged defaults and the built-ins
        for key, value in data.items():
            if key == "DEFAULTS":
                continue
            cfg[key] = value
        for key, value in (data.get("DEFAULTS") or {}).items():
            cfg[key] = value
    if cfg.get("format") not in ("json", "text"):
        raise FoundryError("config.format must be 'json' or 'text'",
                           code="bad_config", detail={"format": cfg.get("format")})
    try:
        cfg["max_items"] = int(cfg["max_items"])
        cfg["ask_skill_limit"] = int(cfg["ask_skill_limit"])
        cfg["max_output_bytes"] = int(cfg["max_output_bytes"])
    except (TypeError, ValueError):
        raise FoundryError("config numeric fields must be integers",
                           code="bad_config")
    if cfg["max_items"] < 1:
        raise FoundryError("config.max_items must be >= 1", code="bad_config")
    return cfg


def load_rules(path=None, root=None):
    path = path or asset(RULES_FILE, root)
    rules = load_json(path, "automation rules")
    if not isinstance(rules, dict) or "risk_dimensions" not in rules:
        raise FoundryError("automation rules are malformed (missing risk_dimensions)",
                           code="bad_rules")
    return rules


def scan_for_secrets(text):
    """Detect obvious embedded credentials in generated artifacts."""
    findings = []
    patterns = (
        ("private_key_block", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
        ("aws_secret_like", r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{20,}"),
        ("generic_bearer", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
        ("password_literal", r"(?i)\b(password|passwd|pwd|api[_-]?key|secret[_-]?key|"
                             r"access[_-]?token|client[_-]?secret)\b\s*[=:]\s*"
                             r"['\"][^'\"\s]{6,}['\"]"),
        ("url_with_credentials", r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s:/@]+@"),
        ("slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    )
    for name, pattern in patterns:
        for match in re.finditer(pattern, text):
            line = text[: match.start()].count("\n") + 1
            findings.append({"check": name, "line": line})
    return findings


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------

def add_common_arguments(parser, suppress=False):
    """Add --config/--format/--out/--catalog.

    When used as a parent parser for subcommands, defaults are SUPPRESS so an
    option given before the subcommand is not overwritten by a None default.
    """
    kw = {"default": argparse.SUPPRESS} if suppress else {}
    parser.add_argument("--config", metavar="PATH",
                        help="JSON config file overriding defaults", **kw)
    parser.add_argument("--format", choices=("json", "text"),
                        help="stdout format (overrides config.format)", **kw)
    parser.add_argument("--out", metavar="PATH",
                        help="also write the result to PATH", **kw)
    parser.add_argument("--catalog", metavar="PATH",
                        help="override the packaged catalog asset", **kw)
    return parser


def resolve_settings(args):
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "format", None):
        cfg["format"] = args.format
    if getattr(args, "catalog", None):
        cfg["catalog_file"] = args.catalog
    return cfg


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as structured JSON (exit 2)."""

    def error(self, message):
        payload = {"ok": False, "tool": self.prog,
                   "error": {"code": "bad_arguments", "message": message,
                             "detail": {"usage": self.format_usage().strip()}}}
        sys.stderr.write(stable_json(payload))
        raise SystemExit(2)


def _hardened(parser):
    """Apply the JSON error behaviour to a parser and all of its subparsers."""
    parser.__class__ = _Parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _hardened(child)
    return parser


def run_cli(prog, build_parser, handler, argv=None):
    """Shared entry wrapper: parse, run handler(args, cfg), map errors to exit codes."""
    parser = _hardened(build_parser())
    args = parser.parse_args(argv)
    try:
        if not hasattr(args, "func"):
            parser.print_help(sys.stderr)
            return 2
        cfg = resolve_settings(args)
        data = handler(args, cfg)
        if data is None:
            return 0
        exit_code = 0
        if isinstance(data, dict):
            exit_code = int(data.pop("_exit_code", 0) or 0)
        emit(data, cfg["format"], getattr(args, "out", None))
        return exit_code
    except FoundryError as exc:
        payload = {
            "ok": False,
            "tool": prog,
            "error": {"code": exc.code, "message": exc.message, "detail": exc.detail},
        }
        if getattr(args, "format", None) == "text":
            sys.stderr.write("error [%s]: %s\n" % (exc.code, exc.message))
        else:
            sys.stdout.write(stable_json(payload))
            sys.stdout.flush()
        return exc.exit_code
    except BrokenPipeError:
        return 3
    except KeyboardInterrupt:
        sys.stderr.write("cancelled by user\n")
        return 130


def guard_output_size(payload, cfg):
    size = len(stable_json(payload).encode("utf-8"))
    limit = int(cfg.get("max_output_bytes") or 0)
    if limit and size > limit:
        raise FoundryError(
            "result exceeds max_output_bytes (%d > %d); reduce max_items"
            % (size, limit),
            code="output_too_large",
            detail={"actual_bytes": size, "limit_bytes": limit},
        )
    return payload


def clamp(value, low, high):
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# risk scoring (shared by catalog, plan and governance tools)
# --------------------------------------------------------------------------

_LEVEL_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _stem(word):
    for suffix, repl in (("ies", "y"), ("ses", "s"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + repl
    return word


def _terms(text):
    return [_stem(t) for t in _slug(text).split("-") if t]


def score_risk(text, rules, extra_flags=None):
    """Deterministic keyword risk scoring over asset-automation-rules.json data.

    Returns a dict with level/score/drivers. It is a triage heuristic, never a
    substitute for human review of the real system.
    """
    hay = " %s " % " ".join(_terms(text or ""))
    drivers = []
    total = 0
    level_cap = 0
    for dim in rules["risk_dimensions"]:
        hits = []
        for keyword in dim.get("keywords", []):
            probe = " %s " % " ".join(_terms(keyword))
            if probe.strip() and probe in hay:
                hits.append(keyword)
        if not hits:
            continue
        weight = int(dim["weight"])
        total += weight
        level_cap = max(level_cap, _LEVEL_ORDER.get(dim["level"], 1))
        drivers.append({"dimension": dim["id"], "label": dim["label"],
                        "weight": weight, "matched": hits[:5]})
    for flag in extra_flags or []:
        drivers.append({"dimension": "operator_flag", "label": flag, "weight": 0,
                        "matched": [flag]})
    if not drivers:
        level = "low"
    elif total >= 18 or level_cap >= 4:
        level = "critical" if level_cap >= 4 else "high"
    elif total >= 9 or level_cap >= 3:
        level = "high"
    elif total >= 4:
        level = "medium"
    else:
        level = "low"
    return {
        "level": level,
        "score": total,
        "drivers": drivers,
        "notes": "Keyword heuristic from asset-automation-rules.json; human review of the "
                 "real system is still required.",
    }


# --------------------------------------------------------------------------
# bundle files (output of tools_catalog.py route/parse)
# --------------------------------------------------------------------------

def load_bundle(path, catalog=None, allow_goal=True, require_skills=True):
    """Validate and normalise a bundle JSON file produced by this Skill Tool."""
    data = load_json(path, "bundle file")
    if not isinstance(data, dict):
        raise FoundryError("bundle file must contain a JSON object",
                           code="bad_bundle")
    goal = data.get("goal") or data.get("title") or data.get("name") or ""
    umbrella = data.get("umbrella") or {}
    if isinstance(umbrella, dict) and umbrella.get("name"):
        goal = goal or umbrella["name"]
    skills = data.get("skills") or []
    if not isinstance(skills, list):
        raise FoundryError("bundle.skills must be a list", code="bad_bundle")
    if require_skills and not skills:
        raise FoundryError(
            "bundle contains no skills; run tools_catalog.py route/parse first",
            code="empty_bundle")
    if len(skills) > 200:
        raise FoundryError("bundle has too many skills (%d > 200)" % len(skills),
                           code="bad_bundle")
    normalised = []
    for item in skills:
        if not isinstance(item, dict):
            raise FoundryError("each bundle skill must be an object", code="bad_bundle")
        name = str(item.get("name") or "").strip()
        if not name:
            raise FoundryError("bundle skill is missing a name", code="bad_bundle")
        entry = dict(item)
        entry["name"] = name
        entry["id"] = entry.get("id") or _slug(name)
        entry["number"] = int(entry["number"]) if str(entry.get("number", "")).isdigit() else None
        entry["description"] = str(entry.get("description") or "").strip()
        entry["stage"] = entry.get("stage") or "build"
        normalised.append(entry)
    data["skills"] = normalised
    data["goal"] = (goal or "unnamed automation").strip()
    if allow_goal is False and not goal:
        raise FoundryError("bundle is missing a goal/title", code="bad_bundle")
    return data


def bundle_text(bundle):
    """Flatten a bundle into text for risk scoring and keyword matching."""
    parts = [bundle.get("goal") or ""]
    for skill in bundle.get("skills", []):
        parts.append(skill.get("name", ""))
        parts.append(skill.get("description", ""))
        parts.append(skill.get("category", "") or "")
    return " ".join(p for p in parts if p)


def stage_of(skill, rules):
    """Assign a skill to the earliest lifecycle stage whose vocabulary it serves."""
    hay = _slug(skill.get("name", "") + " " + skill.get("description", ""))
    candidates = []
    for stage in rules["bundle_stages"]:
        hits = sum(1 for phrase in stage.get("match", []) if _slug(phrase) in hay)
        if hits:
            candidates.append((-hits, stage.get("priority", 99), stage["id"]))
    if not candidates:
        return "build"
    candidates.sort()
    return candidates[0][2]


def stages_in_order(rules):
    """Lifecycle order (plan -> ... -> handoff) from the rules asset."""
    stages = rules["bundle_stages"]
    return [st["id"] for st in sorted(
        stages, key=lambda st: (st.get("order", 99), st.get("priority", 99)))]


def stage_checks(rules, stage_id):
    return list((rules.get("stage_checks") or {}).get(stage_id, []))

