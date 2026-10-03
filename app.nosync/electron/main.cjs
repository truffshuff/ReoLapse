// Electron shell: starts the reolapse Python server (unless one is already running) and opens its UI.
const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');

const PORT = Number(process.env.REOLAPSE_PORT || 8765);
const API = `http://127.0.0.1:${PORT}`;
const UI = process.env.REOLAPSE_UI_URL || API; // dev: the Vite server; otherwise the Python server hosts dist/
const REPO = path.resolve(__dirname, '..', '..');

let server = null;

async function healthy() {
  try {
    const res = await fetch(`${API}/api/health`, { signal: AbortSignal.timeout(1000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function ensureServer() {
  if (await healthy()) return; // reuse a server started from the terminal
  const uv = process.env.REOLAPSE_UV || '/opt/homebrew/bin/uv';
  server = spawn(uv, ['run', '--no-sync', 'reolapse', 'serve', '--port', String(PORT)], {
    cwd: REPO, stdio: ['ignore', 'inherit', 'inherit'],
  });
  server.on('exit', (code) => { server = null; if (code) console.error(`reolapse server exited with ${code}`); });
  for (let i = 0; i < 120; i++) {
    if (await healthy()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('The reolapse server did not start within 60 seconds.');
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1100, minHeight: 700,
    backgroundColor: '#0E1014', titleBarStyle: 'hiddenInset', title: 'Reolapse',
    webPreferences: { contextIsolation: true, sandbox: true },
  });
  // Start from the current build: an older cached UI must never outlive an update.
  await win.webContents.session.clearCache();
  // Links to other sites open in the browser, not inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  await win.loadURL(UI);
}

app.whenReady().then(async () => {
  try {
    await ensureServer();
    await createWindow();
  } catch (e) {
    dialog.showErrorBox('Reolapse', String(e.message || e));
    app.quit();
  }
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { if (server) server.kill(); });
