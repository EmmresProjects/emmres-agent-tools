#!/usr/bin/env bash
# Portable POSIX/Bash launcher for the software-engineering Skill Tool.
set -euo pipefail
IFS=$'\n\t'

export PYTHONDONTWRITEBYTECODE=1

if ! command -v python3 >/dev/null 2>&1; then
  printf '{"status":"failed","errors":[{"code":"MISSING_PYTHON3","message":"python3 (>= 3.10) is required to run software-engineering/tools-workbench.py"}]}\n' >&2
  exit 127
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/tools-workbench.py" "$@"
