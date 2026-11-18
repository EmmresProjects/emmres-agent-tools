#!/bin/sh
# Cross-platform launcher for the pc-capability-governance Skill Tool.
# Finds a Python 3.9+ interpreter, then forwards every argument unchanged.
# Usage:  sh tools-run.sh self-test --evidence evidence
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY_SCRIPT="$HERE/tools-capability_governance.py"

for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      exec "$candidate" "$PY_SCRIPT" "$@"
    fi
  fi
done

printf '%s\n' '{"error":true,"status":"RUNTIME MISSING","message":"Python 3.9+ not found on PATH","exit_code":2}'
exit 2
