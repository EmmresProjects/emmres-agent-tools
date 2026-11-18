"""Capability registry: catalog loading, resolution and parameter validation.

Loaded right after ``tools-core.py``. Every other module receives this module
through the loader, and the CLI uses it to resolve names and coerce parameters
before any handler runs.
"""

from __future__ import annotations

import difflib
import json

CATALOG_ASSET = "asset-capability-catalog.json"


class ParamError(Exception):
    def __init__(self, message, hint="", details=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}


def _coerce_bool(value, name):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    raise ParamError("parameter %r expects a boolean, got %r" % (name, value))


def _coerce_list(value, name):
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParamError("parameter %r looks like JSON but failed to parse: %s" % (name, exc))
        if not isinstance(parsed, list):
            raise ParamError("parameter %r expects a JSON array" % name)
        return [str(v).strip() for v in parsed]
    return [part.strip() for part in text.split(",") if part.strip()]


def coerce_param(name, spec, value):
    """Validate and normalize one parameter against its catalog spec."""
    ptype = spec.get("type", "string")
    if ptype == "string":
        if isinstance(value, (dict, list)):
            raise ParamError("parameter %r expects a string" % name)
        return str(value)
    if ptype == "integer":
        if isinstance(value, bool):
            raise ParamError("parameter %r expects an integer, got a boolean" % name)
        try:
            ivalue = int(str(value).strip())
        except (TypeError, ValueError):
            raise ParamError("parameter %r expects an integer, got %r" % (name, value))
        if "min" in spec and ivalue < spec["min"]:
            raise ParamError("parameter %r must be >= %s, got %s" % (name, spec["min"], ivalue))
        if "max" in spec and ivalue > spec["max"]:
            raise ParamError("parameter %r must be <= %s, got %s" % (name, spec["max"], ivalue))
        return ivalue
    if ptype == "number":
        try:
            fvalue = float(str(value).strip())
        except (TypeError, ValueError):
            raise ParamError("parameter %r expects a number, got %r" % (name, value))
        if "min" in spec and fvalue < spec["min"]:
            raise ParamError("parameter %r must be >= %s, got %s" % (name, spec["min"], fvalue))
        if "max" in spec and fvalue > spec["max"]:
            raise ParamError("parameter %r must be <= %s, got %s" % (name, spec["max"], fvalue))
        return fvalue
    if ptype == "boolean":
        return _coerce_bool(value, name)
    if ptype == "list":
        return _coerce_list(value, name)
    if ptype == "enum":
        values = spec.get("values", [])
        text = str(value).strip()
        if text in values:
            return text
        lowered = {v.lower(): v for v in values}
        if text.lower() in lowered:
            return lowered[text.lower()]
        raise ParamError(
            "parameter %r must be one of: %s (got %r)" % (name, ", ".join(values), value),
            hint="Values are case-insensitive and must match the catalog exactly.",
            details={"allowed": values},
        )
    raise ParamError("unknown parameter type %r for %r" % (ptype, name))


class Catalog:
    """Loaded capability catalog with resolution helpers."""

    def __init__(self, assets):
        self.assets = assets
        data = assets.json(CATALOG_ASSET)
        self.raw = data
        self.version = data.get("version", "0")
        self.categories = data.get("categories", [])
        self.by_id = {}
        self.by_name = {}
        self.by_slug = {}
        for cat in self.categories:
            for skill in cat["skills"]:
                self.by_id[skill["id"]] = skill
                self.by_name[skill["name"].lower()] = skill
                self.by_slug[skill["id"].split(".", 1)[1]] = skill
        self.skill_count = len(self.by_id)

    # -- queries ---------------------------------------------------------
    def get(self, cap_id):
        try:
            return self.by_id[cap_id]
        except KeyError:
            raise ParamError(
                "unknown capability %r" % cap_id,
                hint="Run 'list' to see capability ids, or 'search <term>'.",
            )

    def category(self, slug):
        for cat in self.categories:
            if cat["slug"] == slug or str(cat["number"]) == str(slug):
                return cat
        raise ParamError("unknown category %r" % slug,
                         hint="Run 'list --categories' to see the 20 categories.")

    def local(self):
        return [s for s in self.by_id.values() if s["engine"] == "local"]

    def agent(self):
        return [s for s in self.by_id.values() if s["engine"] == "agent"]

    def search(self, term, limit=20):
        term = (term or "").strip().lower()
        results = []
        for skill in self.by_id.values():
            haystack = " ".join([skill["id"], skill["name"], skill["description"],
                                 skill["category"]]).lower()
            score = 0
            if term and term in skill["id"].lower():
                score += 6
            if term and term in skill["name"].lower():
                score += 4
            for word in set(term.split()):
                if word in haystack:
                    score += 1
            if score:
                results.append((score, skill))
        results.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [skill for _, skill in results[:limit]]

    def resolve(self, token):
        """Resolve an id, a bare slug, an exact name, or a unique prefix."""
        if not token:
            raise ParamError("capability name is required")
        token = token.strip()
        if token in self.by_id:
            return self.by_id[token]
        key = token.lower()
        if key in self.by_name:
            return self.by_name[key]
        if key in self.by_slug:
            return self.by_slug[key]
        dashed = key.replace(" ", "-").replace("_", "-")
        if dashed in self.by_slug:
            return self.by_slug[dashed]
        prefixed = [cid for cid in self.by_id if cid.startswith(dashed)]
        if len(prefixed) == 1:
            return self.by_id[prefixed[0]]
        bare = [cid for cid in self.by_id if cid.split(".", 1)[1].startswith(dashed)]
        if len(bare) == 1:
            return self.by_id[bare[0]]
        guesses = difflib.get_close_matches(dashed, list(self.by_slug), n=5, cutoff=0.6)
        if len(guesses) == 1:
            return self.by_slug[guesses[0]]
        raise ParamError(
            "capability %r not found" % token,
            hint="Close matches: %s" % (", ".join(guesses) if guesses else "run 'list'"),
            details={"candidates": guesses},
        )

    # -- parameters ------------------------------------------------------
    def default_params(self, skill):
        return {name: spec.get("default") for name, spec in skill["params"].items()}

    def validate_params(self, skill, supplied):
        """Merge defaults with supplied values, rejecting unknown names."""
        params = self.default_params(skill)
        unknown = [k for k in supplied if k not in skill["params"]]
        if unknown:
            for name in unknown:
                close = difflib.get_close_matches(name, list(skill["params"]), n=2, cutoff=0.5)
                raise ParamError(
                    "capability %r does not accept parameter %r" % (skill["id"], name),
                    hint="Accepted parameters: %s" % (", ".join(sorted(skill["params"])) or "none")
                    + (("; did you mean %s?" % ", ".join(close)) if close else ""),
                    details={"unknown": unknown, "accepted": sorted(skill["params"])},
                )
        for name, value in supplied.items():
            params[name] = coerce_param(name, skill["params"][name], value)
        return params


def load_catalog(assets):
    return Catalog(assets)


EXPORTS = ["ParamError", "coerce_param", "Catalog", "load_catalog", "_coerce_bool", "_coerce_list"]
