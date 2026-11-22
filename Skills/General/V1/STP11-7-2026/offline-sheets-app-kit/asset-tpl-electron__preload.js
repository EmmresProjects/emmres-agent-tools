/* =====================================================================
   {{KIT_TITLE}} - preload bridge
   Only these four functions are exposed to the page. No Node API leaks.
   ===================================================================== */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('Desktop', {
  version: () => ipcRenderer.invoke('app:version'),
  /** Save a JSON backup into %APPDATA%\{{APP_NAME}}\Backups */
  saveBackup: (filename, content) => ipcRenderer.invoke('app:saveBackup', { filename, content }),
  openBackups: () => ipcRenderer.invoke('app:openBackups'),
  /** Print a receipt HTML string with a real print dialog */
  printHTML: (html) => ipcRenderer.invoke('app:print', html),
  platform: 'electron'
});
