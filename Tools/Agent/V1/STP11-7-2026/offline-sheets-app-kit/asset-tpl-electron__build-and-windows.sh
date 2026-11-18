#!/usr/bin/env bash
# =====================================================================
#  {{APP_TITLE}} - one-command Windows build (Electron)
#  Output: dist/{{ARTIFACT_SLUG}}-Setup-{{APP_VERSION}}.exe  and  dist/{{ARTIFACT_SLUG}}-Portable-{{APP_VERSION}}.exe
#  Run on Windows for the .exe (electron-builder cross-builds poorly).
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/4 Checking tools"
command -v node >/dev/null || { echo "Node.js is required: https://nodejs.org"; exit 1; }
echo "    node $(node -v)   npm $(npm -v)"

echo "==> 2/4 Installing dependencies (first run downloads Electron ~100MB)"
if [ ! -d node_modules ]; then npm install; else echo "    already installed"; fi

echo "==> 3/4 Copying app/ into www/"
npm run sync

echo "==> 4/4 Building the Windows installer and portable EXE"
npm run build:all

echo ""
echo "  Output in ../dist :"
echo "    {{ARTIFACT_SLUG}}-Setup-{{APP_VERSION}}.exe       (installer, Start Menu + Desktop shortcut)"
echo "    {{ARTIFACT_SLUG}}-Portable-{{APP_VERSION}}.exe    (single file, no install)"
echo ""
echo "  First launch: Settings > Google Sheets connection > paste the /exec URL and token."
