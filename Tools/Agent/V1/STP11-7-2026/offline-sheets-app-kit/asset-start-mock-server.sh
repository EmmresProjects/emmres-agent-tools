#!/usr/bin/env bash
# Fake Google Apps Script backend for offline testing.
#   bash start-mock-server.sh          -> http://localhost:{{MOCK_PORT}}/exec
#   PORT=9000 TOKEN=abc bash start-mock-server.sh
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-{{MOCK_PORT}}}"
TOKEN="${TOKEN:-dev-token}"
echo "Starting the mock backend on http://localhost:${PORT}/exec  (token: ${TOKEN})"
echo "Stop it with Ctrl+C."
PORT="$PORT" TOKEN="$TOKEN" node tools/mock-server.js
