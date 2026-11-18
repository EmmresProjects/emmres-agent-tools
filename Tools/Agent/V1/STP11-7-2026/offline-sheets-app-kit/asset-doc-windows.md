# Windows build (EXE)

Electron wraps the same files into a desktop installer.

## What you need

* Node.js 18+ (`node -v`)
* Windows 10/11 for the installer itself (cross-building from macOS is unsupported
  without Wine)

## Build

```bash
cd electron
npm install
npm run build
```

Or the one-command wrapper, which also syncs `app/` first:

```bash
./build-and-windows.sh
```

Output in `dist/`:

```
dist/{{ARTIFACT_SLUG}}-Setup-{{APP_VERSION}}.exe        (installer)
dist/{{ARTIFACT_SLUG}}-Portable-{{APP_VERSION}}.exe     (no install, run from USB)
```

## What the desktop build adds

* A real window with its own menu: reload, full screen, print, About.
* A fixed data folder, so records survive updates.
* **File → Backup** writes a JSON snapshot next to the app data.

## Identity

| Field | Value |
| --- | --- |
| Product name | {{APP_NAME}} |
| App id | {{ANDROID_APP_ID}} |
| Version | {{APP_VERSION}} |
| Licence | `electron/build/LICENSE.txt` |

## Developer loop

```bash
cd electron
EMMRES_DEV=1 npm start     # loads app/index.html live instead of the packaged copy
```

(The variable is `{{BRAND_UPPER}}_DEV` in this build.)

## Common problems

| Symptom | Fix |
| --- | --- |
| `electron-builder` cannot download | Corporate proxy: set `ELECTRON_MIRROR` and `HTTP_PROXY`. |
| Installer is unsigned and SmartScreen warns | Expected. Sign it with a code-signing certificate before distribution, or distribute the portable EXE. |
| Blank window | Run `node scripts/sync-www.js` then rebuild; `www/` is generated. |
