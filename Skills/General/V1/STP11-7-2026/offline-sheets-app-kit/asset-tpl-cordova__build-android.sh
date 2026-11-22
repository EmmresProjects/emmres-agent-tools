#!/usr/bin/env bash
# =====================================================================
#  {{APP_TITLE}} - one-command Android build
#  Usage:  ./build-android.sh            (debug APK)
#          ./build-android.sh release    (signed release APK + AAB)
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-debug}"

echo "==> 1/6 Checking tools"
command -v node >/dev/null || { echo "Node.js is required: https://nodejs.org"; exit 1; }
command -v java >/dev/null || { echo "Java JDK 17 is required for cordova-android 13."; exit 1; }
echo "    node $(node -v)   java $(java -version 2>&1 | head -1)"

if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]; then
  echo "    ! ANDROID_HOME is not set. Install Android Studio, then:"
  echo "      export ANDROID_HOME=\$HOME/Android/Sdk"
  echo "      export PATH=\$PATH:\$ANDROID_HOME/platform-tools:\$ANDROID_HOME/cmdline-tools/latest/bin"
fi
echo "    ANDROID_HOME=${ANDROID_HOME:-<not set>}"

echo "==> 2/6 Installing npm dependencies"
if [ ! -d node_modules ]; then npm install; else echo "    already installed"; fi

echo "==> 3/6 Copying app/ into www/"
node scripts/sync-www.js

echo "==> 4/6 Adding the Android platform (first build only)"
if [ ! -d platforms/android ]; then
  npx cordova platform add android
else
  echo "    platform already present"
fi

echo "==> 5/6 Syncing plugins"
npx cordova prepare android

echo "==> 6/6 Building ($MODE)"
if [ "$MODE" = "release" ]; then
  if [ ! -f ../keystore/{{KEYSTORE_NAME}}-release.keystore ]; then
    echo ""
    echo "  No release keystore found. Create one first:"
    echo "    keytool -genkey -v -keystore ../keystore/{{KEYSTORE_NAME}}-release.keystore \\"
    echo "      -alias {{KEYSTORE_ALIAS}} -keyalg RSA -keysize 2048 -validity 10000"
    echo ""
    exit 1
  fi
  npx cordova build android --release -- \
    --keystore=../keystore/{{KEYSTORE_NAME}}-release.keystore \
    --storePassword="${KS_PASS:?Set KS_PASS}" \
    --alias={{KEYSTORE_ALIAS}} \
    --password="${KS_PASS}"
  OUT="platforms/android/app/build/outputs/apk/release/app-release.apk"
else
  npx cordova build android --debug
  OUT="platforms/android/app/build/outputs/apk/debug/app-debug.apk"
fi

if [ -f "$OUT" ]; then
  mkdir -p ../dist
  cp "$OUT" "../dist/{{ARTIFACT_SLUG}}-${MODE}.apk"
  echo ""
  echo "  APK ready: dist/{{ARTIFACT_SLUG}}-${MODE}.apk"
  echo "  Install on a tablet: adb install -r \"$OUT\""
else
  echo "  Build finished but the APK was not found at $OUT"
fi
