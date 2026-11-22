#!/usr/bin/env python3
"""Primary runnable entry point for the code-data-reverse-engineering Skill Tool."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# A reusable Skill Tool must remain a flat component folder after it is run.
# Keep Python's import cache out of the packaged directory.
sys.dont_write_bytecode = True


def load_core():
    core_path = Path(__file__).resolve().with_name("tools-artifact-core.py")
    spec = importlib.util.spec_from_file_location("artifact_inspector_core", core_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load required core module: {core_path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(load_core().main())
