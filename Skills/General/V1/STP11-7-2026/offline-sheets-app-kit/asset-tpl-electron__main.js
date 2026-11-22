/* =====================================================================
   {{KIT_TITLE}} - Electron main process (Windows desktop build)
   ---------------------------------------------------------------------
   Security posture:
     - contextIsolation ON, nodeIntegration OFF
     - preload exposes only a tiny, explicit bridge
     - navigation to anything but the local app is blocked
     - external links open in the real browser
   Offline behaviour is identical to the Android build: IndexedDB lives in
   the app's user-data folder and syncs to Google Sheets when online.
   ===================================================================== */
'use strict';

const { app, BrowserWindow, Menu, shell, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

const APP_ID = '{{ANDROID_APP_ID}}';
const isDev = !!process.env.{{BRAND_UPPER}}_DEV;

app.setAppUserModelId(APP_ID);

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 900,
    minHeight: 620,
    show: false,
    backgroundColor: '{{THEME_COLOR}}',
    icon: path.join(__dirname, 'build', 'icon.png'),
    title: "{{APP_NAME}}",
    autoHideMenuBar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
      partition: 'persist:{{BRAND_SLUG}}'
    }
  });

  mainWindow.loadFile(path.join(__dirname, 'www', 'index.html'));

  mainWindow.once('ready-to-show', function () {
    mainWindow.show();
    if (isDev) mainWindow.webContents.openDevTools({ mode: 'detach' });
  });

  /* ---------------- navigation hardening ---------------- */
  mainWindow.webContents.setWindowOpenHandler(function (details) {
    // Receipts print from a popup; everything else goes to the real browser.
    if (details.url === 'about:blank' || details.url.indexOf('file://') === 0) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          width: 420, height: 640, autoHideMenuBar: true,
          webPreferences: { contextIsolation: true, nodeIntegration: false }
        }
      };
    }
    shell.openExternal(details.url);
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', function (event, url) {
    if (url.indexOf('file://') === 0) return;
    event.preventDefault();
    shell.openExternal(url);
  });

  mainWindow.on('closed', function () { mainWindow = null; });

  mainWindow.webContents.on('did-fail-load', function (e, code, desc) {
    dialog.showErrorBox('Could not open the app',
      'The app files could not be loaded (' + code + ' ' + desc + ').\n\nReinstall {{APP_NAME}}.');
  });
}

/* ------------------------------------------------------------------ */
/*  Menu                                                              */
/* ------------------------------------------------------------------ */
function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: function () { if (mainWindow) mainWindow.reload(); } },
        { label: 'Print current screen', accelerator: 'CmdOrCtrl+P', click: function () { if (mainWindow) mainWindow.webContents.print(); } },
        { type: 'separator' },
        { label: 'Open backup folder', click: openBackupFolder },
        { type: 'separator' },
        { role: 'quit', label: 'Exit' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { role: 'zoomIn', label: 'Zoom in' },
        { role: 'zoomOut', label: 'Zoom out' },
        { role: 'resetZoom', label: 'Actual size' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: 'Full screen (kiosk)' },
        { role: 'toggleDevTools', label: 'Developer tools' }
      ]
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'About {{APP_NAME}}',
          click: function () {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: 'About',
              message: "{{APP_TITLE}}",
              detail: 'Version ' + app.getVersion() + '\n' +
                '{{TAGLINE}}.\n\n' +
                'Built by {{AUTHOR}}.\n' +
                'Data is stored on this PC first, then sent to Google Sheets.'
            });
          }
        },
        { label: 'Where is my data?', click: openBackupFolder }
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function openBackupFolder() {
  const p = path.join(app.getPath('userData'), 'Backups');
  fs.mkdirSync(p, { recursive: true });
  shell.openPath(p);
}

/* ------------------------------------------------------------------ */
/*  IPC bridge used by preload.js                                     */
/* ------------------------------------------------------------------ */
ipcMain.handle('app:version', function () { return app.getVersion(); });

ipcMain.handle('app:saveBackup', function (event, payload) {
  const dir = path.join(app.getPath('userData'), 'Backups');
  fs.mkdirSync(dir, { recursive: true });
  const name = (payload && payload.filename) || ('backup-' + new Date().toISOString().slice(0, 10) + '.json');
  const file = path.join(dir, name.replace(/[^\w.\-]/g, '_'));
  fs.writeFileSync(file, (payload && payload.content) || '{}', 'utf8');
  return file;
});

ipcMain.handle('app:openBackups', function () { openBackupFolder(); return true; });

ipcMain.handle('app:print', function (event, html) {
  return new Promise(function (resolve) {
    const w = new BrowserWindow({
      show: false, width: 420, height: 640,
      webPreferences: { contextIsolation: true, nodeIntegration: false }
    });
    w.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html || ''));
    w.webContents.once('did-finish-load', function () {
      w.webContents.print({ silent: false, printBackground: true }, function (ok, reason) {
        w.close();
        resolve({ ok: ok, reason: reason || '' });
      });
    });
  });
});

/* ------------------------------------------------------------------ */
/*  App lifecycle                                                     */
/* ------------------------------------------------------------------ */
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', function () {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(function () {
    createWindow();
    buildMenu();
  });

  app.on('window-all-closed', function () {
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}
