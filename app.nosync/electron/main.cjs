// Electron shell: makes sure the reolapse Python server is running, then opens its UI.
//
// Development (npm run app): the server runs from the source checkout with `uv run`.
// Packaged app: the installer carries the Python source, `uv` and ffmpeg. The first launch
// (and the first launch after an update) builds the Python environment in the app's data
// folder with a setup window; after that the server starts straight from it.
const { app, BrowserWindow, shell, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const PORT = Number(process.env.REOLAPSE_PORT || 8765);
const API = `http://127.0.0.1:${PORT}`;
const REPO = path.resolve(__dirname, '..', '..');
const PACKAGED = app.isPackaged;
const RES = PACKAGED ? process.resourcesPath : null;
const DATA = app.getPath('userData'); // ~/Library/Application Support/Reolapse
const ENGINE = path.join(DATA, 'engine'); // the Python environment
const STAMP = path.join(ENGINE, '.reolapse-version');
const PY = path.join(ENGINE, 'bin', 'python');

let server = null;
let mainWindow = null;
let quitting = false;

async function get(pathname) {
  try {
    const res = await fetch(`${API}${pathname}`, { signal: AbortSignal.timeout(1500) });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

function toolEnv() {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  env.REOLAPSE_APP_VERSION = app.getVersion(); // for "Check for updates"
  if (PACKAGED) {
    env.PATH = `${path.join(RES, 'bin')}:${env.PATH || '/usr/bin:/bin'}`; // bundled ffmpeg/ffprobe/uv
    env.REOLAPSE_UI_DIR = path.join(RES, 'ui');
    env.REOLAPSE_MODELS = path.join(DATA, 'models');
    env.UV_PYTHON_INSTALL_DIR = path.join(DATA, 'python');
    env.UV_PROJECT_ENVIRONMENT = ENGINE;
    env.UV_PYTHON_PREFERENCE = 'only-managed'; // our own Python, whatever is installed on the Mac
  }
  return env;
}

// ---------------------------------------------------------------- first-run setup (packaged)

function engineReady() {
  try {
    return fs.existsSync(PY) && fs.readFileSync(STAMP, 'utf8').trim() === app.getVersion();
  } catch {
    return false;
  }
}

function setupEngine() {
  return new Promise((resolve, reject) => {
    const win = new BrowserWindow({
      width: 720, height: 460, resizable: false, title: 'Setting up Reolapse', backgroundColor: '#0E1014',
      webPreferences: { contextIsolation: true, sandbox: true },
    });
    win.loadFile(path.join(__dirname, 'setup.html'));
    const say = (line) => {
      if (!win.isDestroyed()) win.webContents.executeJavaScript(`window.addLine && addLine(${JSON.stringify(line)})`).catch(() => {});
    };
    win.webContents.once('did-finish-load', () => {
      say(`Building the analysis engine in ${ENGINE}`);
      const uv = spawn(path.join(RES, 'bin', 'uv'), [
        'sync', '--frozen', '--no-dev', '--no-editable', '--project', path.join(RES, 'backend'),
      ], { env: toolEnv() });
      const onData = (buf) => buf.toString().split(/[\r\n]+/).filter(Boolean).forEach(say);
      uv.stdout.on('data', onData);
      uv.stderr.on('data', onData);
      uv.on('error', (e) => { win.destroy(); reject(e); });
      uv.on('exit', (code) => {
        if (code === 0) {
          fs.writeFileSync(STAMP, app.getVersion());
          say('Done.');
          setTimeout(() => { win.destroy(); resolve(); }, 600);
        } else {
          say(`Setup failed (exit code ${code}). Check the internet connection and open Reolapse again.`);
          reject(new Error('Setting up the analysis engine failed; see the setup window for details.'));
        }
      });
    });
  });
}

// ---------------------------------------------------------------- server

async function ensureServer() {
  if (await get('/api/health')) return; // already running (another window, or started from a terminal)
  // The server stops itself once this process is gone (crash, force quit, WindowServer restart).
  const env = { ...toolEnv(), REOLAPSE_PARENT_PID: String(process.pid) };
  if (PACKAGED) {
    if (!engineReady()) await setupEngine();
    fs.mkdirSync(path.join(DATA, 'logs'), { recursive: true });
    const log = fs.openSync(path.join(DATA, 'logs', 'server.log'), 'a');
    server = spawn(PY, ['-m', 'reolapse', 'serve', '--port', String(PORT)], {
      cwd: DATA, env, stdio: ['ignore', log, log],
    });
  } else {
    const uv = process.env.REOLAPSE_UV || '/opt/homebrew/bin/uv';
    server = spawn(uv, ['run', '--no-sync', 'reolapse', 'serve', '--port', String(PORT)], {
      cwd: REPO, env, stdio: ['ignore', 'inherit', 'inherit'],
    });
  }
  server.on('exit', (code) => { server = null; if (code && !quitting) console.error(`reolapse server exited with ${code}`); });
  for (let i = 0; i < 240; i++) {
    if (await get('/api/health')) return;
    if (!server) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(PACKAGED
    ? `The Reolapse server didn't start. Details: ${path.join(DATA, 'logs', 'server.log')}`
    : 'The reolapse server did not start within 2 minutes.');
}

// ---------------------------------------------------------------- PDF reports

// The report page loads in a hidden window at print size; it sets window.__reportReady once its data
// and every picture have loaded, then Chromium renders it to PDF.
ipcMain.handle('report:pdf', async (_e, { hash, filename }) => {
  if (!/^#\/report\?/.test(hash || '')) throw new Error('not a report');
  const base = new URL(mainWindow?.webContents.getURL() || API).origin;
  const win = new BrowserWindow({ show: false, width: 1100, height: 1400,
    webPreferences: { contextIsolation: true, sandbox: true } });
  try {
    await win.loadURL(`${base}/${hash}${hash.includes('?') ? '&' : '?'}print=1`);
    const deadline = Date.now() + 10 * 60 * 1000; // hundreds of object pictures can take a while
    for (;;) {
      const state = await win.webContents.executeJavaScript('window.__reportReady || null');
      if (state === true) break;
      if (typeof state === 'string') throw new Error(state); // the page reports its own error
      if (Date.now() > deadline) throw new Error('the report took too long to load');
      await new Promise((r) => setTimeout(r, 500));
    }
    const pdf = await win.webContents.printToPDF({
      pageSize: 'Letter', printBackground: true, preferCSSPageSize: true,
      displayHeaderFooter: true, headerTemplate: '<span></span>',
      footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#777">'
        + '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    });
    const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
      title: 'Save report', defaultPath: path.join(app.getPath('downloads'), filename || 'Reolapse report.pdf'),
      filters: [{ name: 'PDF', extensions: ['pdf'] }],
    });
    if (canceled || !filePath) return { saved: false };
    await fs.promises.writeFile(filePath, pdf);
    shell.openPath(filePath);
    return { saved: true, path: filePath };
  } finally {
    win.destroy();
  }
});

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1100, minHeight: 700,
    backgroundColor: '#0E1014', titleBarStyle: 'hiddenInset', title: 'Reolapse',
    webPreferences: { contextIsolation: true, sandbox: true, preload: path.join(__dirname, 'preload.cjs') },
  });
  // Start from the current build: an older cached UI must never outlive an update.
  await mainWindow.webContents.session.clearCache();
  // Links to other sites open in the browser, not inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  mainWindow.on('closed', () => { mainWindow = null; });
  await mainWindow.loadURL(process.env.REOLAPSE_UI_URL || API);
}

// ---------------------------------------------------------------- lifecycle

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } else createWindow(); });

  app.whenReady().then(async () => {
    try {
      await ensureServer();
      await createWindow();
    } catch (e) {
      dialog.showErrorBox('Reolapse', String(e.message || e));
      quitting = true;
      if (server) server.kill('SIGTERM');
      app.exit(1);
    }
    app.on('activate', () => { if (!mainWindow) createWindow(); });
  });

  // Closing the window keeps Reolapse (and its jobs) running, like other Mac apps; ⌘Q quits.
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

  app.on('before-quit', async (event) => {
    if (quitting || !server) return; // nothing of ours to stop (or we already asked)
    event.preventDefault();
    const jobs = (await get('/api/jobs')) || [];
    const running = jobs.filter((j) => j.status === 'running').length;
    if (running) {
      const { response } = await dialog.showMessageBox({
        type: 'question', buttons: ['Quit', 'Cancel'], defaultId: 1, cancelId: 1,
        message: `${running} job${running === 1 ? ' is' : 's are'} running`,
        detail: 'Quitting stops them; they continue from their last checkpoint the next time you open Reolapse.',
      });
      if (response !== 0) return;
    }
    quitting = true;
    server.kill('SIGTERM'); // the server saves the queue and stops its jobs
    const stopped = new Promise((r) => { if (!server) r(); else server.once('exit', r); });
    await Promise.race([stopped, new Promise((r) => setTimeout(r, 8000))]);
    app.exit(0); // (app.quit() from inside before-quit is ignored)
  });
}
