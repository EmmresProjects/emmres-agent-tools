"""Module loader for the flat ``tools-*.py`` Skill Tool layout.

The Skill Tool keeps every file directly inside one folder with a ``tools-``
prefix, so module names contain hyphens and cannot be imported with a plain
``import`` statement. This loader registers each file under a stable import
alias, injects shared namespaces (``core``, ``H``, ``assets``) and collects the
``HANDLERS`` mapping that each capability module exports.

Load order matters only for the helper namespace (``H``); handlers are bound
after every module is loaded, so any module may call helpers from any other.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback

# Explicit, documented load order (see knowledge-readme.md -> "Runtime files").
MODULE_ORDER = [
    "tools-core.py",
    "tools-registry.py",
    "tools-text-ops.py",
    "tools-unicode-ops.py",
    "tools-structure-ops.py",
    "tools-art-ops.py",
    "tools-lang-ops.py",
    "tools-doc-ops.py",
    "tools-analysis-ops.py",
    "tools-safety-ops.py",
    "tools-media-ops.py",
    "tools-notation-ops.py",
    "tools-automation-ops.py",
    "tools-meta-ops.py",
    "tools-contract-ops.py",
    "tools-selftest.py",
]


class LoaderError(RuntimeError):
    pass


class ModuleSet:
    def __init__(self):
        self.modules = {}
        self.handlers = {}
        self.exports = {}
        self.core = None
        self.assets = None
        self.root = None
        self.load_errors = []

    def helper(self, name):
        if name not in self.exports:
            raise LoaderError("helper %r is not exported by any tools module" % name)
        return self.exports[name]


class _Helpers:
    """Attribute access onto the merged helper namespace."""

    def __init__(self, exports):
        self._exports = exports

    def __getattr__(self, name):
        try:
            return self._exports[name]
        except KeyError:
            raise AttributeError("helper %r is not available" % name)

    def __contains__(self, name):
        return name in self._exports


def _load_file(path, alias, registry):
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise LoaderError("cannot create import spec for %s" % os.path.basename(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - reported, not hidden
        raise LoaderError(
            "failed to load %s: %s\n%s"
            % (os.path.basename(path), exc, traceback.format_exc(limit=3))
        )
    registry.modules[alias] = module
    return module


def load_all(root):
    """Load every runtime module from ``root`` and bind handlers."""
    registry = ModuleSet()
    registry.root = os.path.abspath(root)
    missing = [n for n in MODULE_ORDER if not os.path.isfile(os.path.join(root, n))]
    if missing:
        raise LoaderError("missing runtime modules: %s" % ", ".join(missing))

    # Pass 1: load every module file with a stable alias.
    for filename in MODULE_ORDER:
        alias = "ult_" + filename[len("tools-") : -len(".py")].replace("-", "_")
        _load_file(os.path.join(root, filename), alias, registry)

    registry.core = registry.modules["ult_core"]

    # Pass 2: merge public helpers (modules that define EXPORTS).
    for alias, module in registry.modules.items():
        if alias == "ult_core":
            continue
        names = getattr(module, "EXPORTS", None)
        if not names:
            continue
        for name in names:
            fn = getattr(module, name, None)
            if fn is None:
                raise LoaderError("%s declares EXPORTS %r but does not define it" % (alias, name))
            if name in registry.exports:
                raise LoaderError(
                    "helper name collision for %r (%s)" % (name, alias)
                )
            registry.exports[name] = fn

    helpers = _Helpers(registry.exports)

    # Pass 3: inject shared namespaces and collect handlers.
    for alias, module in registry.modules.items():
        module.core = registry.core
        module.H = helpers
        handlers = getattr(module, "HANDLERS", None) or {}
        for cap_id, fn in handlers.items():
            if not callable(fn):
                raise LoaderError("%s handler %r is not callable" % (alias, cap_id))
            if cap_id in registry.handlers:
                raise LoaderError("duplicate handler for capability %r" % cap_id)
            registry.handlers[cap_id] = fn

    # Pass 4: expose the complete handler map so meta/automation capabilities can
    # call other capabilities in-process (documented in knowledge-readme.md).
    for alias, module in registry.modules.items():
        module.ALL_HANDLERS = registry.handlers
    return registry
