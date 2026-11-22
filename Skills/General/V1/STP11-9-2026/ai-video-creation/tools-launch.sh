#!/bin/sh
# ai-video-creation / tools-launch.sh
# POSIX launcher for the primary entry point. Run from the future skill root:
#     sh ai-video-creation/tools-launch.sh help
#     sh ai-video-creation/tools-launch.sh capabilities --limit 5
# Resolves every path relative to this launcher; no installation or environment setup.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$here/tools-ai-video.py" "$@"
