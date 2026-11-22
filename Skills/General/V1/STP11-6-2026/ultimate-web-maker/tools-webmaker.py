#!/usr/bin/env python3
"""Primary command-line entry point for the Ultimate Web Maker Skill Tool."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    engine_path = Path(__file__).resolve().with_name("tools-engine.py")
    if not engine_path.is_file():
        print('{"ok":false,"error":{"code":"missing_tool_file","message":"tools-engine.py is missing beside the primary entry point."}}', file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("ultimate_web_maker_engine", engine_path)
    if spec is None or spec.loader is None:
        print('{"ok":false,"error":{"code":"tool_load_error","message":"Could not load tools-engine.py."}}', file=sys.stderr)
        return 2
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        print('{"ok":false,"error":{"code":"tool_load_error","message":"Could not initialize tools-engine.py","details":"' + type(exc).__name__ + '"}}', file=sys.stderr)
        return 2
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
