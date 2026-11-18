#!/bin/sh
# tools-verify.sh - portable verification launcher for the unified
# ultimate-game-maker Skill Tool. Run it from the future skill root with an
# explicit POSIX shell. It uses only relative paths and exits non-zero if any
# check fails.
#
#   sh ultimate-game-maker/tools-verify.sh             # doctor + full selftest
#   sh ultimate-game-maker/tools-verify.sh doctor     # environment and package health
#   sh ultimate-game-maker/tools-verify.sh selftest   # built-in test suite
#
# Requires: POSIX sh and python3 >= 3.9. No third-party packages, no network.

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || {
    printf 'verify: cannot resolve the script directory\n' >&2
    exit 1
}
cd "$SCRIPT_DIR" || exit 1

ENTRY="tools-game-maker.py"
MODE="${1:-all}"

if [ ! -f "$ENTRY" ]; then
    printf 'verify: %s not found in %s\n' "$ENTRY" "$SCRIPT_DIR" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    printf 'verify: python3 is required but was not found on PATH\n' >&2
    exit 1
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)' || {
    printf 'verify: python 3.9 or newer is required, found %s\n' "$(python3 -V 2>&1)" >&2
    exit 1
}

status=0

run_step() {
    label="$1"
    shift
    printf '\n== %s ==\n' "$label"
    "$@" || {
        code=$?
        printf 'verify: %s failed with exit code %s\n' "$label" "$code" >&2
        status=1
    }
}

case "$MODE" in
    doctor)
        run_step "doctor" python3 "$ENTRY" doctor --format json
        ;;
    selftest)
        run_step "selftest" python3 "$ENTRY" selftest --format json --verbose
        ;;
    all)
        run_step "verify" python3 "$ENTRY" verify --format json
        ;;
    *)
        printf 'verify: unknown mode "%s" (use: all, doctor, selftest)\n' "$MODE" >&2
        exit 2
        ;;
esac

printf '\n== summary ==\n'
if [ "$status" -eq 0 ]; then
    printf 'verify: all checks passed\n'
else
    printf 'verify: one or more checks failed\n' >&2
fi
exit "$status"
