# Android build (APK)

The Android app is the same files wrapped by Cordova. No Android code to write.

## What you need

| Tool | Version | Notes |
| --- | --- | --- |
| Node.js | 18 or newer | `node -v` |
| Java JDK | 17 | `java -version` |
| Android SDK | API 34 | Install **Android Studio**, then its SDK Manager |
| Gradle | comes with Cordova | nothing to install |

Set the SDK path once:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
```

## Build

```bash
cd cordova
npm install
./build-android.sh
```

The script copies `app/` into `cordova/www/`, adds the Android platform if it is
missing, and builds. The APK lands in `dist/`:

```
dist/{{ARTIFACT_SLUG}}-debug.apk
```

### Signed release build

```bash
# 1. create a keystore ONCE and keep it forever (losing it means you cannot update the app)
keytool -genkey -v -keystore ../keystore/{{KEYSTORE_NAME}}-release.keystore \
  -alias {{KEYSTORE_ALIAS}} -keyalg RSA -keysize 2048 -validity 10000

# 2. build a signed release
KS_PASS='your-keystore-password' ./build-android.sh release
```

```
dist/{{ARTIFACT_SLUG}}-release.apk
```

Never commit the keystore or its password. `.gitignore` already excludes it.

## Install on the phone

```bash
adb install -r dist/{{ARTIFACT_SLUG}}-debug.apk
```

Or copy the APK to the phone and open it (enable *Install unknown apps*).

## App identity

These come from your specification and cannot be changed after publishing:

| Field | Value in this build |
| --- | --- |
| App name | {{APP_NAME}} |
| Package id | {{ANDROID_APP_ID}} |
| Version | {{APP_VERSION}} |
| Orientation | {{ORIENTATION}} |

Changing them means a new package — Android treats it as a different app.

## Icons and splash

Already generated in `cordova/res/`. To re-generate after changing colours:

```bash
python tools/make_icons.py --spec spec.json --out . --only cordova
```

## Common problems

| Symptom | Fix |
| --- | --- |
| `SDK location not found` | Set `ANDROID_HOME`; create `cordova/local.properties` with `sdk.dir=/path/to/Sdk`. |
| `Unsupported class file major version` | You are on JDK 21+. Use JDK 17. |
| White screen on launch | Rebuild after any `app/` change — `www/` is generated, not edited. |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | Uninstall the old app first; the signing key changed. |
| Play Store rejects the APK | It needs an **AAB**: `cd cordova && npx cordova build android --release -- --bundle`. |
