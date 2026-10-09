// The few desktop-only abilities the UI may use, exposed without giving the page Node access.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('reolapse', {
  /** Render a report page (an app URL hash, e.g. "#/report?day=…") to PDF and let the user save it. */
  savePdf: (hash, filename) => ipcRenderer.invoke('report:pdf', { hash, filename }),
});
